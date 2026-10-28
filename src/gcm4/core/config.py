# SPDX-License-Identifier: GPL-3.0-or-later
"""Lecture et écriture de ``~/.config/gcm4/gcm4.conf`` sans GTK (#137).

Format repris de ``Wmain.loadConfig`` / ``Wmain.writeConfig`` (application
GTK3), inventorié dans ``docs/configuration.md``.

Garanties :

- **aucune perte** : ``save_config(path, load_config(path))`` conserve
  chaque section et chaque clé. Les options globales connues sont typées
  dans :class:`AppConfig` ; les autres (terminal, plugins, raccourcis…)
  restent brutes dans ``Config.brut``. Dans une section ``host N``, les
  clés hors champs communs vont dans ``host.protocol_options[protocole]``
  et sont réécrites telles quelles, même sans le plugin du protocole ;
- **écriture atomique** : fichier temporaire du même dossier, ``fsync``,
  ``os.replace``, permissions ``0600`` ; copie ``gcm4.conf.bak`` avant la
  première écriture d'une session ; en cas d'erreur (disque plein…),
  l'ancien fichier reste intact et :class:`ConfigError` est levée ;
- **jamais d'écrasement silencieux** : fichier illisible ou INI corrompu
  → :class:`ConfigError`, jamais une configuration vide enregistrée
  par-dessus.

Mots de passe : le champ ``pass`` passe par un objet :class:`Chiffrement`.
Par défaut (:class:`SansChiffrement`), la valeur stockée est conservée
telle quelle. ``gcm4.core.chiffrement.ChiffrementGcm`` (#138) lit et
écrit le format de l'application (``.gcm4.key``) ; ses deux points
d'accroche facultatifs sont ``preparer(brut)``, appelé avant la lecture
des hôtes, et ``version_format``, écrit dans ``[options] version``.
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Protocol

from loguru import logger

from gcm4.core.i18n import _
from gcm4.core.model import Group, Host, HostStore, normaliser_chemin
from gcm4.core.replies import ecrire_replies, lire_replies

SECTION_OPTIONS = "options"
SECTION_WINDOW = "window"
SECTION_GROUPES_VIDES = "empty_groups"
#: Raccourcis clavier (#247) : écrits en cours de session par ``update_keys``,
#: le fichier fait foi pour toute la section (voir ``_reprendre_cles_hors_modele``).
SECTION_SHORTCUTS = "shortcuts"
_SECTION_HOTE = re.compile(r"^host (\d+)$")

#: Champs communs d'une section ``host N`` : (attribut de Host, clé INI).
#: Toute autre clé est une option de protocole (``protocol_options``).
CHAMPS_HOTE: tuple[tuple[str, str], ...] = (
    ("group", "group"),
    ("name", "name"),
    ("description", "description"),
    ("host", "host"),
    ("user", "user"),
    ("password", "pass"),
    ("port", "port"),
    ("protocol", "protocol"),
    ("font_color", "font-color"),
    ("back_color", "back-color"),
    ("extra_params", "extra_params"),
    ("auto_close_tab", "auto-close-tab"),
    ("workspace", "workspace"),
    ("pre_connect_command", "pre-connect-command"),
    ("post_connect_command", "post-connect-command"),
)


class ConfigError(Exception):
    """Configuration illisible, corrompue ou impossible à écrire (message traduit)."""


class Chiffrement(Protocol):
    """Transformation du champ ``pass`` entre le fichier et ``Host.password``."""

    def dechiffrer(self, stocke: str) -> str:
        """Valeur du fichier → mot de passe."""
        ...

    def chiffrer(self, clair: str) -> str:
        """Mot de passe → valeur à écrire."""
        ...


class SansChiffrement:
    """Conserve le champ ``pass`` tel quel (avant le lot 2c, et pour les tests)."""

    def dechiffrer(self, stocke: str) -> str:
        """Renvoie ``stocke``."""
        logger.trace("SansChiffrement.dechiffrer")
        return stocke

    def chiffrer(self, clair: str) -> str:
        """Renvoie ``clair``."""
        logger.trace("SansChiffrement.chiffrer")
        return clair


@dataclass
class AppConfig:
    """Options globales génériques de ``[options]`` (valeurs par défaut de ``utils.conf``).

    Les options propres au terminal, aux protocoles ou aux plugins ne sont
    pas typées ici : elles restent brutes dans ``Config.brut``.
    """

    confirm_exit: bool = True
    confirm_close_tab: bool = True
    confirm_close_tab_middle: bool = True
    auto_close_tab: int = 0
    cycle_tabs: bool = True
    disable_shortcuts: bool = False
    disable_notifications: bool = False
    restore_open_tabs: bool = False
    check_updates: bool = True
    disable_hosts_stripes: bool = False
    update_title: bool = False
    app_title: str = ""
    font: str = ""
    font_color: str = ""
    back_color: str = ""
    transparency: int = 0


def _cle_option(attribut: str) -> str:
    """``confirm_close_tab`` → ``confirm-close-tab`` ; ``confirm_exit`` → ``confirm-exit``."""
    logger.trace("_cle_option: {}", attribut)
    resultat = attribut.replace("_", "-")
    logger.trace("_cle_option: -> {}", resultat)
    return resultat


@dataclass
class Config:
    """Configuration complète : options typées, hôtes et groupes, reste brut."""

    options: AppConfig = field(default_factory=AppConfig)
    store: HostStore = field(default_factory=HostStore)
    brut: configparser.RawConfigParser = field(default_factory=lambda: _parseur())


def _parseur() -> configparser.RawConfigParser:
    """Parseur sensible à la casse des clés (``Wmain`` n'en change pas la casse)."""
    logger.trace("_parseur")
    cp = configparser.RawConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    logger.trace("_parseur: prêt")
    return cp


def _booleen(texte: str) -> bool | None:
    """Booléen INI de configparser, ``None`` si invalide."""
    logger.trace("_booleen: {!r}", texte)
    resultat = configparser.RawConfigParser.BOOLEAN_STATES.get(texte.strip().lower())
    logger.trace("_booleen: -> {}", resultat)
    return resultat


def _entier(texte: str) -> int | None:
    """Entier, ``None`` si invalide."""
    logger.trace("_entier: {!r}", texte)
    try:
        resultat: int | None = int(texte.strip())
    except ValueError:
        logger.warning("entier invalide dans gcm4.conf : {!r}", texte)
        resultat = None
    logger.trace("_entier: -> {}", resultat)
    return resultat


def _convertir(brut: str, defaut: object) -> object:
    """Texte INI → type de ``defaut`` (bool, int ou str) ; ``None`` si invalide."""
    logger.trace("_convertir: {!r}", brut)
    resultat: object
    if isinstance(defaut, bool):
        resultat = _booleen(brut)
    elif isinstance(defaut, int):
        resultat = _entier(brut)
    else:
        resultat = brut
    logger.trace("_convertir: -> {!r}", resultat)
    return resultat


def _texte_option(cfg: Config, attribut: str) -> str:
    """Texte à écrire pour une option : l'original s'il est inchangé (« 1 » reste « 1 », valeur invalide conservée)."""
    logger.trace("_texte_option: {}", attribut)
    valeur = getattr(cfg.options, attribut)
    brut = cfg.brut.get(SECTION_OPTIONS, _cle_option(attribut), fallback=None)
    if brut is not None:
        lu = _convertir(brut, getattr(AppConfig(), attribut))
        if lu == valeur or (lu is None and valeur == getattr(AppConfig(), attribut)):
            logger.trace("_texte_option: original conservé")
            return brut
    resultat = _texte(valeur)
    logger.trace("_texte_option: -> {!r}", resultat)
    return resultat


def _lire_options(cp: configparser.RawConfigParser) -> AppConfig:
    """Options connues de ``[options]`` ; valeur invalide → défaut (la valeur brute reste dans ``brut``)."""
    logger.debug("_lire_options")
    cfg = AppConfig()
    if not cp.has_section(SECTION_OPTIONS):
        logger.debug("_lire_options: section absente")
        return cfg
    for f in fields(AppConfig):
        cle = _cle_option(f.name)
        if not cp.has_option(SECTION_OPTIONS, cle):
            continue
        valeur = _convertir(cp.get(SECTION_OPTIONS, cle), getattr(cfg, f.name))
        if valeur is not None:
            setattr(cfg, f.name, valeur)
    logger.debug("_lire_options: fait")
    return cfg


def _lire_hote(cp: configparser.RawConfigParser, section: str, chiffrement: Chiffrement) -> Host:
    """Section ``host N`` → Host ; clés hors champs communs → ``protocol_options[protocole]``."""
    logger.debug("_lire_hote: {}", section)
    valeurs = dict(cp.items(section))
    host = Host(name=valeurs.get("name", ""))
    autres: dict[str, str] = {}
    communs = dict(CHAMPS_HOTE)
    for attribut, cle in CHAMPS_HOTE:
        if cle not in valeurs or attribut == "name":
            continue
        brut = valeurs[cle]
        if attribut == "password":
            host.password = chiffrement.dechiffrer(brut)
        elif attribut == "port":
            port = _entier(brut)
            if port is None:
                autres[cle] = brut  # vide ou invalide : conservé tel quel
            host.port = port
        elif attribut == "workspace":
            booleen = _booleen(brut)
            if booleen is None:
                autres[cle] = brut
            else:
                host.workspace = booleen
        else:
            setattr(host, attribut, brut)
    host.group = normaliser_chemin(host.group)
    for cle, brut in valeurs.items():
        if cle not in communs.values():
            autres[cle] = brut
    if autres:
        host.protocol_options[host.protocol] = autres
    logger.debug("_lire_hote: {!r}, {} option(s) de protocole", host.path, len(autres))
    return host


def _liste(texte: str) -> list[str]:
    """Liste séparée par des virgules, sans entrées vides."""
    logger.trace("_liste: {!r}", texte)
    resultat = [normaliser_chemin(m) for m in texte.split(",") if normaliser_chemin(m)]
    logger.trace("_liste: {}", len(resultat))
    return resultat


def _lire_groupes(cp: configparser.RawConfigParser, store: HostStore) -> None:
    """Groupes vides, couleurs et groupes repliés (``[empty_groups]``, ``[window]``)."""
    logger.debug("_lire_groupes")
    for chemin in _liste(cp.get(SECTION_GROUPES_VIDES, "names", fallback="")):
        store.ensure_group(chemin)
    for entree in cp.get(SECTION_WINDOW, "group-colors", fallback="").split(","):
        chemin, egal, couleur = entree.partition("=")
        groupe = store.ensure_group(chemin) if egal and couleur.strip() else None
        if groupe is not None:
            groupe.color = couleur.strip()
    # Format GTK3 : chemins Gtk.TreePath (« 0:1 »), cf. gcm4.core.replies.
    for chemin in lire_replies(
        cp.get(SECTION_WINDOW, "collapsed-folders", fallback=""), store.groups
    ):
        store.groups[chemin].collapsed = True
    logger.debug("_lire_groupes: {} groupe(s)", len(store.groups))


def _lire_brut(path: str) -> configparser.RawConfigParser | None:
    """INI brut de ``path`` ; ``None`` si absent.

    Raises:
        ConfigError: fichier illisible (droits, encodage) ou INI corrompu.
    """
    logger.debug("_lire_brut: {}", path)
    cp = _parseur()
    try:
        with open(path, encoding="utf-8") as fichier:
            cp.read_file(fichier)
    except FileNotFoundError:
        logger.debug("_lire_brut: absent")
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("_lire_brut: illisible : {}", exc)
        raise ConfigError(_("Unable to read the configuration file %s: %s") % (path, exc)) from exc
    except configparser.Error as exc:
        logger.warning("_lire_brut: INI corrompu : {}", exc)
        raise ConfigError(_("The configuration file %s is corrupted: %s") % (path, exc)) from exc
    logger.debug("_lire_brut: {} section(s)", len(cp.sections()))
    return cp


def load_config(path: str, chiffrement: Chiffrement | None = None) -> Config:
    """Lit ``path`` ; fichier absent → configuration par défaut.

    Raises:
        ConfigError: fichier illisible (droits, encodage) ou INI corrompu.
    """
    logger.debug("load_config: {}", path)
    chiffrement = chiffrement or SansChiffrement()
    lu = _lire_brut(path)
    if lu is None:
        logger.debug("load_config: absent, valeurs par défaut")
        return Config(brut=_parseur())
    cp = lu
    preparer = getattr(chiffrement, "preparer", None)
    if callable(preparer):
        preparer(cp)  # format et clé, avant de déchiffrer le premier hôte (lot 2c)
    cfg = Config(options=_lire_options(cp), brut=cp)
    sections = sorted(
        (int(m.group(1)), s) for s in cp.sections() if (m := _SECTION_HOTE.match(s)) is not None
    )
    for _numero, section in sections:
        host = _lire_hote(cp, section, chiffrement)
        cfg.store.ensure_group(host.group)
        cfg.store.hosts.append(host)  # sans contrôle de doublon : rien n'est écarté à la lecture
        cp.remove_section(section)
    _lire_groupes(cp, cfg.store)
    logger.debug(
        "load_config: {} hôte(s), {} groupe(s)", len(cfg.store.hosts), len(cfg.store.groups)
    )
    return cfg


def _texte(valeur: object) -> str:
    """Valeur → texte INI comme ``Wmain.writeConfig`` (``str()``, ``None`` → vide)."""
    logger.trace("_texte: {!r}", type(valeur))
    resultat = "" if valeur is None else str(valeur)
    logger.trace("_texte: fait")
    return resultat


def _ecrire_hote(
    cp: configparser.RawConfigParser, section: str, host: Host, chiffrement: Chiffrement
) -> None:
    """Host → section ``host N`` : champs communs puis options de protocole telles quelles."""
    logger.debug("_ecrire_hote: {} {!r}", section, host.path)
    cp.add_section(section)
    for attribut, cle in CHAMPS_HOTE:
        valeur = getattr(host, attribut)
        if attribut == "password":
            cp.set(section, cle, chiffrement.chiffrer(host.password))
        elif attribut == "port" and valeur is None:
            continue  # absent, ou valeur brute conservée dans protocol_options
        else:
            cp.set(section, cle, _texte(valeur))
    # Protocole courant en dernier : ses valeurs priment en cas de clé commune.
    ordre = sorted(host.protocol_options, key=lambda p: p == host.protocol)
    for protocole in ordre:
        for cle, brut in host.protocol_options[protocole].items():
            cp.set(section, cle, brut)
    logger.debug("_ecrire_hote: fait")


def _construire(cfg: Config, chiffrement: Chiffrement) -> configparser.RawConfigParser:
    """Parseur complet à écrire : sections brutes, options, groupes, hôtes renumérotés."""
    logger.debug("_construire")
    cp = _parseur()
    for section in cfg.brut.sections():
        if _SECTION_HOTE.match(section):
            continue
        cp.add_section(section)
        for cle, brut in cfg.brut.items(section):
            cp.set(section, cle, brut)
    for section in (SECTION_OPTIONS, SECTION_WINDOW, SECTION_GROUPES_VIDES):
        if not cp.has_section(section):
            cp.add_section(section)
    for f in fields(AppConfig):
        cp.set(SECTION_OPTIONS, _cle_option(f.name), _texte_option(cfg, f.name))
    version_format = getattr(chiffrement, "version_format", None)
    if version_format is not None:
        cp.set(SECTION_OPTIONS, "version", version_format)  # format des mots de passe écrits
    store = cfg.store
    groupes: list[Group] = sorted(store.groups.values(), key=lambda g: g.path)
    cp.set(
        SECTION_WINDOW, "group-colors", ",".join(f"{g.path}={g.color}" for g in groupes if g.color)
    )
    cp.set(
        SECTION_WINDOW,
        "collapsed-folders",
        ecrire_replies((g.path for g in groupes if g.collapsed), store.groups),
    )
    vides = [g.path for g in groupes if not store.hosts_in(g.path, recursive=True)]
    cp.set(SECTION_GROUPES_VIDES, "names", ",".join(vides))
    for numero, host in enumerate(store.hosts_in("", recursive=True), start=1):
        _ecrire_hote(cp, f"host {numero}", host, chiffrement)
    logger.debug("_construire: {} section(s)", len(cp.sections()))
    return cp


#: Fichiers déjà copiés en ``.bak`` pendant cette session.
_sauvegardes: set[str] = set()


def _copie_bak(path: str) -> None:
    """Copie ``path`` en ``path.bak`` (0600) à la première écriture de la session."""
    logger.debug("_copie_bak: {}", path)
    cle = os.path.abspath(path)
    if cle in _sauvegardes or not os.path.exists(path):
        logger.debug("_copie_bak: inutile")
        return
    shutil.copyfile(path, path + ".bak")
    os.chmod(path + ".bak", 0o600)
    _sauvegardes.add(cle)
    logger.debug("_copie_bak: fait")


def _ecrire_atomique(path: str, cp: configparser.RawConfigParser) -> None:
    """Écrit ``cp`` dans ``path`` : temporaire 0600, ``fsync``, ``os.replace``, ``.bak``.

    Raises:
        ConfigError: écriture impossible ; l'ancien fichier est alors intact.
    """
    logger.debug("_ecrire_atomique: {}", path)
    dossier = os.path.dirname(os.path.abspath(path))
    temporaire = ""
    try:
        os.makedirs(dossier, exist_ok=True)
        _copie_bak(path)
        descripteur, temporaire = tempfile.mkstemp(
            prefix=".gcm4.conf.", suffix=".tmp", dir=dossier
        )
        os.fchmod(descripteur, 0o600)
        with os.fdopen(descripteur, "w", encoding="utf-8") as fichier:
            cp.write(fichier)
            fichier.flush()
            os.fsync(fichier.fileno())
        os.replace(temporaire, path)
        temporaire = ""
    except OSError as exc:
        logger.warning("_ecrire_atomique: écriture impossible, ancien fichier intact : {}", exc)
        raise ConfigError(
            _("Unable to write the configuration file %s: %s") % (path, exc)
        ) from exc
    finally:
        if temporaire and os.path.exists(temporaire):
            os.unlink(temporaire)
    logger.debug("_ecrire_atomique: fait")


def update_keys(path: str, section: str, valeurs: Mapping[str, str | None]) -> None:
    """Change quelques clés d'une section sans toucher au reste du fichier.

    Pour l'état de fenêtre et le thème, enregistrés hors de ``save_config`` :
    mêmes garanties (lecture stricte, écriture atomique 0600, ``.bak``).
    Une valeur ``None`` retire la clé (absente : rien à faire).

    Raises:
        ConfigError: fichier illisible ou corrompu (il n'est alors **pas**
            réécrit), ou écriture impossible.
    """
    logger.debug("update_keys: {} [{}] {}", path, section, sorted(valeurs))
    cp = _lire_brut(path) or _parseur()
    if not cp.has_section(section):
        cp.add_section(section)
    for cle, valeur in valeurs.items():
        if valeur is None:
            cp.remove_option(section, cle)
        else:
            cp.set(section, cle, valeur)
    _ecrire_atomique(path, cp)
    logger.debug("update_keys: fait")


def save_config(path: str, cfg: Config, chiffrement: Chiffrement | None = None) -> None:
    """Écrit ``cfg`` dans ``path`` de façon atomique (0600, ``.bak``, ``fsync``).

    Raises:
        ConfigError: écriture impossible ; l'ancien fichier est alors intact.
    """
    logger.debug("save_config: {}", path)
    chiffrement = chiffrement or SansChiffrement()
    _reprendre_cles_hors_modele(path, cfg)
    _ecrire_atomique(path, _construire(cfg, chiffrement))
    logger.debug("save_config: fait")


#: Clés de ``[window]`` écrites depuis le modèle par ``_construire`` ; les
#: autres (géométrie, thème…) sont écrites en cours de session par
#: ``update_keys`` et le fichier fait foi pour elles.
_CLES_WINDOW_DU_MODELE = frozenset({"group-colors", "collapsed-folders"})


def _cles_du_modele() -> dict[str, frozenset[str]]:
    """Par section reprise : clés écrites depuis le modèle par ``_construire``."""
    logger.trace("_cles_du_modele")
    resultat = {
        SECTION_WINDOW: _CLES_WINDOW_DU_MODELE,
        SECTION_OPTIONS: frozenset(_cle_option(f.name) for f in fields(AppConfig)),
    }
    logger.trace("_cles_du_modele: {} section(s)", len(resultat))
    return resultat


def _reprendre_cles_hors_modele(path: str, cfg: Config) -> None:
    """Recopie dans ``cfg.brut`` les clés hors modèle de ``[window]`` et ``[options]``, et ``[shortcuts]``.

    Sans cela, un enregistrement du modèle en cours de session (glisser-déposer,
    #141) remettrait le thème ou la géométrie lus au démarrage, ou effacerait
    une préférence sans champ ``AppConfig`` écrite depuis (``log-level``, #148).

    Raises:
        ConfigError: fichier devenu illisible ou corrompu (pas d'écrasement).
    """
    logger.debug("_reprendre_cles_hors_modele: {}", path)
    disque = _lire_brut(path)
    if disque is None:
        logger.debug("_reprendre_cles_hors_modele: rien à reprendre")
        return
    reprises = 0
    for section, du_modele in _cles_du_modele().items():
        if not disque.has_section(section):
            continue
        if not cfg.brut.has_section(section):
            cfg.brut.add_section(section)
        for cle, valeur in disque.items(section):
            if cle not in du_modele:
                cfg.brut.set(section, cle, valeur)
                reprises += 1
    # Section entièrement écrite hors modèle : le fichier fait foi, y compris
    # pour une clé retirée depuis le chargement (raccourci réinitialisé, #247).
    if cfg.brut.has_section(SECTION_SHORTCUTS):
        cfg.brut.remove_section(SECTION_SHORTCUTS)
    if disque.has_section(SECTION_SHORTCUTS):
        cfg.brut.add_section(SECTION_SHORTCUTS)
        for cle, valeur in disque.items(SECTION_SHORTCUTS):
            cfg.brut.set(SECTION_SHORTCUTS, cle, valeur)
            reprises += 1
    logger.debug("_reprendre_cles_hors_modele: {} clé(s)", reprises)
