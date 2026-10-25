"""Import et export des hôtes en CSV et JSON, sans GTK (#152).

Portage de ``Wmain._csv_fields``, ``_host_to_dict``, ``_coerce_field_value``,
``_dict_to_host`` et ``_import_hosts_from_list`` (application GTK3) sur le
modèle de la base GTK4 (:class:`~gcm4.core.model.Host`). Schéma documenté
dans ``docs/echange-hotes.md``.

Garanties :

- **mots de passe jamais exportés par défaut** : il faut demander
  explicitement ``mots_de_passe=True`` (colonne ``password``, en clair) ;
- **fichier mal formé → aucune modification** : le fichier est lu et validé
  en entier avant de toucher au magasin ; la moindre erreur lève
  :class:`ExchangeError` (message traduit) ;
- **aller-retour sans perte** : ``lire_json(ecrire_json(h))`` redonne les
  mêmes hôtes, options de tous les protocoles comprises. En CSV, une cellule
  d'option vide vaut « option absente » (toutes les lignes partagent les
  mêmes colonnes) ;
- **exports GTK3 acceptés** : CSV et JSON (liste à plat) de l'application
  GTK3 s'importent ; les colonnes propres à un protocole y portent le nom de
  l'attribut Python (``keep_alive``), converti vers la clé de ``gcm.conf``
  (``keepalive``) pour que :func:`gcm4.core.config.save_config` les réécrive
  à l'identique.

Neutralité de protocole : aucune option n'est interprétée ici. Les colonnes
hors champs communs sont rangées telles quelles dans
``host.protocol_options[host.protocol]``.
"""

from __future__ import annotations

import copy
import csv
import enum
import io
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from gcm4.core.config import _booleen, _entier, _texte
from gcm4.core.i18n import _
from gcm4.core.model import Host, HostStore, normaliser_chemin

#: Identifiant et version du format JSON (``docs/echange-hotes.md``).
FORMAT_JSON = "gcm4-hosts"
VERSION_JSON = 1

#: Colonnes des champs communs, dans l'ordre d'export (noms d'attributs de Host).
CHAMPS_COMMUNS: tuple[str, ...] = (
    "group",
    "name",
    "description",
    "host",
    "user",
    "port",
    "protocol",
    "font_color",
    "back_color",
    "extra_params",
    "auto_close_tab",
    "workspace",
    "pre_connect_command",
    "post_connect_command",
)
#: Colonne du mot de passe (exportée seulement sur demande, en clair).
CHAMP_MOT_DE_PASSE = "password"
#: Options des protocoles autres que ``protocol`` (objet JSON ; texte JSON en CSV).
CHAMP_AUTRES_PROTOCOLES = "other_protocol_options"

#: Format GTK3 : nom de colonne d'export (attribut de ``models.Host``) → clé de
#: ``gcm.conf`` (``models.save_host_to_ini``), quand les deux diffèrent. Les
#: autres colonnes (champs des plugins compris) portent le nom de la clé.
_CLES_GTK3: dict[str, str] = {
    "keep_alive": "keepalive",
    "compressionLevel": "compression-level",
    "backspace_key": "backspace-key",
    "delete_key": "delete-key",
}
_COLONNES_GTK3: dict[str, str] = {cle: col for col, cle in _CLES_GTK3.items()}
#: Colonne dont les retours à la ligne sont écrits ``\\n`` dans ``gcm.conf``.
_COLONNE_ECHAPPEE = "commands"


class ExchangeError(Exception):
    """Fichier d'échange illisible, mal formé ou impossible à écrire (message traduit)."""


class Conflit(str, enum.Enum):
    """Que faire d'un hôte importé dont le nom existe déjà dans son groupe."""

    IGNORER = "ignorer"
    RENOMMER = "renommer"
    REMPLACER = "remplacer"


@dataclass
class Rapport:
    """Bilan d'un import : chemins ``groupe/nom`` concernés, groupes créés."""

    ajoutes: list[str] = field(default_factory=list)
    remplaces: list[str] = field(default_factory=list)
    renommes: list[tuple[str, str]] = field(default_factory=list)
    ignores: list[str] = field(default_factory=list)
    groupes_crees: list[str] = field(default_factory=list)


# --- export ----------------------------------------------------------------


def _vers_colonne(cle: str) -> str:
    """Clé de ``gcm.conf`` → nom de colonne GTK3 (``keepalive`` → ``keep_alive``).

    Args:
        cle (str): Clé INI d'une option de protocole.

    Returns:
        str: Nom de colonne d'export.
    """
    logger.trace("_vers_colonne: {!r}", cle)
    resultat = _COLONNES_GTK3.get(cle, cle)
    logger.trace("_vers_colonne: -> {!r}", resultat)
    return resultat


def _vers_cle(colonne: str) -> str:
    """Nom de colonne GTK3 → clé de ``gcm.conf`` (``keep_alive`` → ``keepalive``).

    Args:
        colonne (str): Nom de colonne d'un fichier importé.

    Returns:
        str: Clé INI de l'option.
    """
    logger.trace("_vers_cle: {!r}", colonne)
    resultat = _CLES_GTK3.get(colonne, colonne)
    logger.trace("_vers_cle: -> {!r}", resultat)
    return resultat


def hote_vers_dict(host: Host, mots_de_passe: bool = False) -> dict[str, Any]:
    """Host → dictionnaire d'export (valeurs texte, comme l'export GTK3).

    Champs communs, puis options du protocole de l'hôte sous leur nom de
    colonne GTK3, puis (s'il y en a) les options des autres protocoles sous
    :data:`CHAMP_AUTRES_PROTOCOLES`. Les options priment sur un champ commun
    de même nom, comme dans ``config._ecrire_hote`` (port ou ``workspace``
    invalide conservé brut à la lecture de ``gcm.conf``).

    Args:
        host (Host): Hôte à exporter.
        mots_de_passe (bool): Inclure le mot de passe en clair (colonne
            :data:`CHAMP_MOT_DE_PASSE`). Faux par défaut.

    Returns:
        dict[str, Any]: Colonne → texte ; :data:`CHAMP_AUTRES_PROTOCOLES`
        → ``{protocole: {clé: valeur}}``.
    """
    logger.debug("hote_vers_dict: {!r} mots_de_passe={}", host.path, mots_de_passe)
    ligne: dict[str, Any] = {c: _texte(getattr(host, c)) for c in CHAMPS_COMMUNS}
    if mots_de_passe:
        ligne[CHAMP_MOT_DE_PASSE] = host.password
    for cle, brut in host.protocol_options.get(host.protocol, {}).items():
        colonne = _vers_colonne(cle)
        ligne[colonne] = brut.replace("\\n", "\n") if colonne == _COLONNE_ECHAPPEE else brut
    autres = {
        p: dict(opts) for p, opts in host.protocol_options.items() if p != host.protocol and opts
    }
    if autres:
        ligne[CHAMP_AUTRES_PROTOCOLES] = autres
    logger.debug("hote_vers_dict: {} colonne(s)", len(ligne))
    return ligne


def ecrire_json(hosts: Iterable[Host], mots_de_passe: bool = False) -> str:
    """Hôtes → texte JSON au format :data:`FORMAT_JSON` version :data:`VERSION_JSON`.

    Args:
        hosts (Iterable[Host]): Hôtes à exporter.
        mots_de_passe (bool): Inclure les mots de passe en clair.

    Returns:
        str: Document JSON (UTF-8, indenté, terminé par un saut de ligne).
    """
    logger.debug("ecrire_json: mots_de_passe={}", mots_de_passe)
    lignes = [hote_vers_dict(h, mots_de_passe) for h in hosts]
    doc = {"format": FORMAT_JSON, "version": VERSION_JSON, "hosts": lignes}
    resultat = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    logger.debug("ecrire_json: {} hôte(s)", len(lignes))
    return resultat


def ecrire_csv(hosts: Iterable[Host], mots_de_passe: bool = False) -> str:
    """Hôtes → texte CSV (en-tête, puis une ligne par hôte).

    Colonnes : champs communs, ``password`` si demandé, options rencontrées
    (ordre d'apparition), puis :data:`CHAMP_AUTRES_PROTOCOLES` (texte JSON)
    si un hôte en a. Une cellule vide vaut « option absente ».

    Args:
        hosts (Iterable[Host]): Hôtes à exporter.
        mots_de_passe (bool): Inclure les mots de passe en clair.

    Returns:
        str: Document CSV.
    """
    logger.debug("ecrire_csv: mots_de_passe={}", mots_de_passe)
    lignes = [hote_vers_dict(h, mots_de_passe) for h in hosts]
    colonnes: dict[str, None] = dict.fromkeys(CHAMPS_COMMUNS)
    if mots_de_passe:
        colonnes[CHAMP_MOT_DE_PASSE] = None
    for ligne in lignes:
        colonnes.update(dict.fromkeys(c for c in ligne if c != CHAMP_AUTRES_PROTOCOLES))
    if any(CHAMP_AUTRES_PROTOCOLES in ligne for ligne in lignes):
        colonnes[CHAMP_AUTRES_PROTOCOLES] = None
    sortie = io.StringIO()
    writer = csv.DictWriter(sortie, fieldnames=list(colonnes), lineterminator="\n")
    writer.writeheader()
    for ligne in lignes:
        if CHAMP_AUTRES_PROTOCOLES in ligne:
            ligne = {**ligne, CHAMP_AUTRES_PROTOCOLES: json.dumps(ligne[CHAMP_AUTRES_PROTOCOLES])}
        writer.writerow(ligne)
    logger.debug("ecrire_csv: {} hôte(s), {} colonne(s)", len(lignes), len(colonnes))
    return sortie.getvalue()


# --- import ----------------------------------------------------------------


def _en_texte(valeur: object, ou: str, colonne: str) -> str:
    """Valeur JSON scalaire → texte (``None`` → vide, booléen → ``True``/``False``).

    Args:
        valeur (object): Valeur lue.
        ou (str): Position traduite (« ligne 3 », « entrée 2 ») pour l'erreur.
        colonne (str): Nom de la colonne, pour l'erreur.

    Returns:
        str: Valeur en texte.

    Raises:
        ExchangeError: liste, objet ou autre type non scalaire.
    """
    logger.trace("_en_texte: {} {!r}", ou, colonne)
    if valeur is None:
        logger.trace("_en_texte: vide")
        return ""
    if isinstance(valeur, (str, bool, int, float)):
        resultat = str(valeur)
        logger.trace("_en_texte: scalaire")
        return resultat
    logger.debug("_en_texte: valeur non scalaire pour {!r}", colonne)
    raise ExchangeError(_("%s: unexpected value for %s") % (ou, colonne))


def _autres_protocoles(valeur: object, ou: str) -> dict[str, dict[str, str]]:
    """Valeur de :data:`CHAMP_AUTRES_PROTOCOLES` (objet, ou texte JSON en CSV) → options.

    Args:
        valeur (object): Objet ``{protocole: {clé: valeur}}``, texte JSON de cet
            objet, ou vide.
        ou (str): Position traduite, pour l'erreur.

    Returns:
        dict[str, dict[str, str]]: Options par protocole (valeurs en texte).

    Raises:
        ExchangeError: structure invalide.
    """
    logger.debug("_autres_protocoles: {}", ou)
    if valeur in (None, ""):
        logger.debug("_autres_protocoles: aucune")
        return {}
    if isinstance(valeur, str):
        try:
            valeur = json.loads(valeur)
        except ValueError as exc:
            logger.debug("_autres_protocoles: JSON invalide ({})", exc)
            raise ExchangeError(
                _("%s: unexpected value for %s") % (ou, CHAMP_AUTRES_PROTOCOLES)
            ) from exc
    if not isinstance(valeur, dict) or not all(
        isinstance(p, str) and isinstance(o, dict) for p, o in valeur.items()
    ):
        logger.debug("_autres_protocoles: structure invalide")
        raise ExchangeError(_("%s: unexpected value for %s") % (ou, CHAMP_AUTRES_PROTOCOLES))
    resultat = {
        p: {str(c): _en_texte(v, ou, CHAMP_AUTRES_PROTOCOLES) for c, v in o.items()}
        for p, o in valeur.items()
    }
    logger.debug("_autres_protocoles: {} protocole(s)", len(resultat))
    return resultat


def dict_vers_hote(ligne: dict[str, object], ou: str, vides_absents: bool = False) -> Host:
    """Dictionnaire importé (export gcm4 ou GTK3) → Host.

    Divergences avec ``Wmain._dict_to_host`` (GTK3), voulues :

    - groupe absent ou vide → racine (la base GTK4 accepte des hôtes à la
      racine), et non plus ``IMPORT`` ;
    - protocole absent : ``type``, sinon vide (aucun protocole par défaut
      écrit en dur dans le cœur), et non plus ``ssh`` ;
    - les options de protocole restent du texte, conservées telles quelles
      (plus de conversion par type de valeur par défaut d'un plugin :
      c'est le plugin qui les interprète) ; le mot de passe est repris s'il
      est dans le fichier.

    Args:
        ligne (dict[str, object]): Colonne → valeur.
        ou (str): Position traduite (« ligne 3 »), pour les erreurs.
        vides_absents (bool): Ignorer les options vides (CSV, où toutes les
            lignes partagent les mêmes colonnes).

    Returns:
        Host: Hôte construit (non ajouté au magasin).

    Raises:
        ExchangeError: nom absent, valeur non scalaire, options d'autres
            protocoles mal formées.
    """
    logger.debug("dict_vers_hote: {}", ou)
    textes = {
        str(c): _en_texte(v, ou, str(c)) for c, v in ligne.items() if c != CHAMP_AUTRES_PROTOCOLES
    }
    nom = textes.get("name", "")
    if not nom.strip():
        logger.debug("dict_vers_hote: nom absent")
        raise ExchangeError(_("%s: the host name is missing") % ou)
    protocole = textes.get("protocol") or textes.get("type", "")
    host = Host(name=nom, group=textes.get("group", ""), protocol=protocole)
    options: dict[str, str] = {}
    for colonne, texte in textes.items():
        if colonne in ("name", "group", "protocol"):
            continue
        if colonne == CHAMP_MOT_DE_PASSE:
            host.password = texte
        elif colonne == "port":
            host.port = _entier(texte) if texte.strip() else None
            if host.port is None and texte.strip():
                options["port"] = texte  # invalide : conservé brut, comme config.py
        elif colonne == "workspace":
            booleen = _booleen(texte) if texte.strip() else False
            host.workspace = bool(booleen)
            if booleen is None:
                options["workspace"] = texte
        elif colonne in CHAMPS_COMMUNS:
            setattr(host, colonne, texte)
        elif texte or not vides_absents:
            valeur = texte.replace("\n", "\\n") if colonne == _COLONNE_ECHAPPEE else texte
            options[_vers_cle(colonne)] = valeur
    if options:
        host.protocol_options[protocole] = options
    autres = _autres_protocoles(ligne.get(CHAMP_AUTRES_PROTOCOLES), ou)
    for p, opts in autres.items():
        if p != protocole:
            host.protocol_options[p] = opts
    logger.debug("dict_vers_hote: {!r}, {} option(s)", host.path, len(options))
    return host


def lire_json(texte: str) -> list[Host]:
    """Texte JSON → hôtes. Accepte le format gcm4 (objet versionné) et l'export GTK3 (liste).

    Args:
        texte (str): Contenu du fichier.

    Returns:
        list[Host]: Hôtes lus, dans l'ordre du fichier.

    Raises:
        ExchangeError: JSON invalide, format ou version inconnus, entrée
            invalide.
    """
    logger.debug("lire_json: {} caractère(s)", len(texte))
    try:
        doc = json.loads(texte)
    except ValueError as exc:
        logger.debug("lire_json: JSON invalide ({})", exc)
        raise ExchangeError(str(exc)) from exc
    if isinstance(doc, dict):
        if doc.get("format") != FORMAT_JSON or doc.get("version") != VERSION_JSON:
            logger.debug(
                "lire_json: format {!r} version {!r}", doc.get("format"), doc.get("version")
            )
            raise ExchangeError(_("unsupported format or version"))
        doc = doc.get("hosts")
    if not isinstance(doc, list):
        logger.debug("lire_json: pas de liste d'hôtes")
        raise ExchangeError(_("unsupported format or version"))
    hosts = []
    for i, entree in enumerate(doc, start=1):
        ou = _("entry %d") % i
        if not isinstance(entree, dict):
            logger.debug("lire_json: entrée {} non objet", i)
            raise ExchangeError(_("%s: unexpected value for %s") % (ou, "hosts"))
        hosts.append(dict_vers_hote(entree, ou))
    logger.debug("lire_json: {} hôte(s)", len(hosts))
    return hosts


def lire_csv(texte: str) -> list[Host]:
    """Texte CSV (en-tête puis une ligne par hôte) → hôtes ; exports gcm4 et GTK3.

    Args:
        texte (str): Contenu du fichier.

    Returns:
        list[Host]: Hôtes lus, dans l'ordre du fichier.

    Raises:
        ExchangeError: colonne ``name`` absente, CSV invalide (lecture stricte :
            guillemet non fermé…), ligne invalide ou trop longue.
    """
    logger.debug("lire_csv: {} caractère(s)", len(texte))
    reader = csv.DictReader(io.StringIO(texte, newline=""), strict=True)
    hosts = []
    try:
        if "name" not in (reader.fieldnames or []):
            logger.debug("lire_csv: colonne name absente")
            raise ExchangeError(_("the column %s is missing") % "name")
        for ligne in reader:
            ou = _("line %d") % reader.line_num
            if None in ligne:
                logger.debug("lire_csv: ligne {} trop longue", reader.line_num)
                raise ExchangeError(_("%s: too many cells") % ou)
            hosts.append(dict_vers_hote(dict(ligne), ou, vides_absents=True))
    except csv.Error as exc:
        logger.debug("lire_csv: CSV invalide ({})", exc)
        raise ExchangeError(str(exc)) from exc
    logger.debug("lire_csv: {} hôte(s)", len(hosts))
    return hosts


def importer(
    store: HostStore, hosts: Iterable[Host], conflit: Conflit = Conflit.IGNORER
) -> Rapport:
    """Ajoute ``hosts`` à ``store`` selon la politique ``conflit`` ; renvoie le bilan.

    Un doublon à l'intérieur du fichier est traité comme un conflit avec
    l'hôte déjà importé. Le travail se fait sur une copie du magasin,
    recopiée à la fin : une erreur inattendue ne laisse pas d'import à moitié
    fait. Remplace ``Wmain._import_hosts_from_list`` (qui ignorait toujours les
    doublons, sans bilan).

    Args:
        store (HostStore): Magasin à compléter.
        hosts (Iterable[Host]): Hôtes à importer (copiés, jamais partagés).
        conflit (Conflit): Ignorer, renommer (``nom (2)``) ou remplacer.

    Returns:
        Rapport: Hôtes ajoutés, remplacés, renommés, ignorés ; groupes créés.
    """
    logger.debug("importer: conflit={}", conflit.value)
    travail = copy.deepcopy(store)
    avant = set(travail.groups)
    rapport = Rapport()
    for original in hosts:
        host = original.clone()
        host.group = normaliser_chemin(host.group)
        existant = travail.find(host.path)
        if existant is None:
            travail.add(host)
            rapport.ajoutes.append(host.path)
        elif conflit is Conflit.REMPLACER:
            travail.replace(existant, host)
            rapport.remplaces.append(host.path)
        elif conflit is Conflit.RENOMMER:
            ancien = host.path
            host.name = travail.nom_de_copie(host)
            travail.add(host)
            rapport.renommes.append((ancien, host.path))
        else:
            rapport.ignores.append(host.path)
    rapport.groupes_crees = sorted(set(travail.groups) - avant)
    store.hosts, store.groups = travail.hosts, travail.groups
    logger.debug(
        "importer: {} ajouté(s), {} remplacé(s), {} renommé(s), {} ignoré(s), {} groupe(s)",
        len(rapport.ajoutes),
        len(rapport.remplaces),
        len(rapport.renommes),
        len(rapport.ignores),
        len(rapport.groupes_crees),
    )
    return rapport


# --- fichiers --------------------------------------------------------------


def _est_json(chemin: str | Path) -> bool:
    """Vrai si l'extension du fichier est ``.json`` (sinon : CSV).

    Args:
        chemin (str | Path): Fichier.

    Returns:
        bool: Format JSON.
    """
    logger.trace("_est_json: {}", chemin)
    resultat = Path(chemin).suffix.lower() == ".json"
    logger.trace("_est_json: -> {}", resultat)
    return resultat


def lire_fichier(chemin: str | Path) -> list[Host]:
    """Lit et valide un fichier d'échange (``.json`` : JSON, sinon CSV).

    Args:
        chemin (str | Path): Fichier à lire (UTF-8, BOM accepté).

    Returns:
        list[Host]: Hôtes lus.

    Raises:
        ExchangeError: fichier illisible ou mal formé (message traduit).
    """
    logger.debug("lire_fichier: {}", chemin)
    try:
        texte = Path(chemin).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("lire_fichier: illisible ({})", exc)
        raise ExchangeError(_("Unable to read the file %s: %s") % (chemin, exc)) from exc
    try:
        hosts = lire_json(texte) if _est_json(chemin) else lire_csv(texte)
    except ExchangeError as exc:
        logger.debug("lire_fichier: mal formé ({})", exc)
        raise ExchangeError(
            _("The file %s is not a valid host export: %s") % (chemin, exc)
        ) from exc
    logger.debug("lire_fichier: {} hôte(s)", len(hosts))
    return hosts


def importer_fichier(
    store: HostStore, chemin: str | Path, conflit: Conflit = Conflit.IGNORER
) -> Rapport:
    """Lit ``chemin`` en entier puis l'importe ; fichier invalide → ``store`` inchangé.

    Args:
        store (HostStore): Magasin à compléter.
        chemin (str | Path): Fichier CSV ou JSON.
        conflit (Conflit): Politique en cas de nom déjà pris.

    Returns:
        Rapport: Bilan de l'import.

    Raises:
        ExchangeError: fichier illisible ou mal formé (rien n'est importé).
    """
    logger.debug("importer_fichier: {} conflit={}", chemin, conflit.value)
    rapport = importer(store, lire_fichier(chemin), conflit)
    logger.debug("importer_fichier: {} ajouté(s)", len(rapport.ajoutes))
    return rapport


def exporter_fichier(
    chemin: str | Path, hosts: Iterable[Host], mots_de_passe: bool = False
) -> int:
    """Écrit ``hosts`` dans ``chemin`` (``.json`` : JSON, sinon CSV), permissions 0600.

    Args:
        chemin (str | Path): Fichier à créer ou remplacer.
        hosts (Iterable[Host]): Hôtes à exporter.
        mots_de_passe (bool): Inclure les mots de passe en clair.

    Returns:
        int: Nombre d'hôtes exportés.

    Raises:
        ExchangeError: écriture impossible (message traduit).
    """
    logger.debug("exporter_fichier: {} mots_de_passe={}", chemin, mots_de_passe)
    liste = list(hosts)
    texte = (
        ecrire_json(liste, mots_de_passe)
        if _est_json(chemin)
        else ecrire_csv(liste, mots_de_passe)
    )
    try:
        cible = Path(chemin)
        cible.touch(mode=0o600, exist_ok=True)
        cible.chmod(0o600)
        cible.write_text(texte, encoding="utf-8")
    except OSError as exc:
        logger.debug("exporter_fichier: écriture impossible ({})", exc)
        raise ExchangeError(_("Unable to write the file %s: %s") % (chemin, exc)) from exc
    logger.debug("exporter_fichier: {} hôte(s)", len(liste))
    return len(liste)
