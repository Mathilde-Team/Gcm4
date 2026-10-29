# SPDX-License-Identifier: GPL-3.0-or-later
"""Découverte des plugins et isolation de leurs erreurs (#150), sans GTK.

Sources, dans cet ordre (``docs/plugins.md``, « Installation et découverte ») :

1. points d'entrée ``importlib.metadata`` du groupe :data:`GROUPE`, triés
   par nom ;
2. en mode source seulement, ``plugins/<nom>/gtk4.py`` du dépôt (variable
   ``PLUGIN``), triés par nom de dossier ; le dossier est importé comme le
   paquet ``gcm4_plugin_<nom>``, ``gtk4.py`` peut donc importer ``core.py``.

Chaque candidat devient un :class:`EtatPlugin`. Un plugin qui lève à l'import
ou à l'initialisation, incompatible (:func:`gcm4.core.plugins.incompatibilite`),
en double (même ``name``) ou qui réclame un protocole déjà pris est marqué
**en erreur** avec sa raison traduite et un ``warning`` au journal. Les autres
continuent, et le démarrage aussi. Les plugins chargés sont rangés par
``name`` : l'ordre ne dépend pas du système de fichiers.
"""

from __future__ import annotations

import functools
import importlib.machinery
import importlib.util
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from loguru import logger

from gcm4.core.i18n import _
from gcm4.core.plugins import ConnectionPlugin, PluginContext, incompatibilite

GROUPE = "gcm4.plugins"
#: Fichier d'un plugin en mode source, dans ``plugins/<nom>/``.
FICHIER_SOURCE = "gtk4.py"

#: ``fabrique(nom)`` : contexte injecté dans le plugin ``nom``.
FabriqueDeContexte = Callable[[str], PluginContext]


@dataclass(frozen=True)
class EtatPlugin:
    """Résultat du chargement d'un candidat (affiché dans Préférences > Extensions)."""

    name: str
    source: str
    version: str = ""
    protocols: tuple[str, ...] = ()
    plugin: ConnectionPlugin | None = None
    erreur: str | None = None

    @property
    def charge(self) -> bool:
        """Vrai si le plugin est utilisable."""
        return self.plugin is not None


@dataclass(frozen=True)
class Candidat:
    """Classe de plugin à charger, ou la fonction qui l'importe (peut lever)."""

    nom: str
    source: str
    charger: Callable[[], Any]


def dossier_source() -> Path | None:
    """``plugins/`` du dépôt quand gcm4 tourne depuis ses sources, sinon ``None``."""
    logger.trace("dossier_source")
    racine = Path(__file__).resolve().parents[3]
    dossier = racine / "plugins"
    resultat = (
        dossier
        if (racine / "pyproject.toml").is_file()
        and (racine / "src" / "gcm4").is_dir()
        and dossier.is_dir()
        else None
    )
    logger.trace("dossier_source: {}", resultat)
    return resultat


def _points_du_groupe() -> list[metadata.EntryPoint]:
    """Points d'entrée du groupe :data:`GROUPE`, de Python 3.9 à 3.14.

    ``entry_points(group=...)`` date de Python 3.10. En 3.9 (Rocky 9, #223),
    ``entry_points()`` rend un ``dict`` groupe → points ; en 3.10 et 3.11, un
    objet qui accepte aussi ``select(group=...)``.
    """
    logger.debug("_points_du_groupe: entrée")
    tous: Any = metadata.entry_points()
    if hasattr(tous, "select"):
        points = list(tous.select(group=GROUPE))
    else:
        logger.trace("_points_du_groupe: API Python 3.9 (dict)")
        points = list(tous.get(GROUPE, ()))
    logger.debug("_points_du_groupe: {} point(s)", len(points))
    return points


def candidats_points_d_entree(
    points: Iterable[metadata.EntryPoint] | None = None,
) -> list[Candidat]:
    """Candidats du groupe :data:`GROUPE`, triés par nom."""
    logger.debug("candidats_points_d_entree: entrée")
    if points is None:
        points = _points_du_groupe()
    resultat = [
        Candidat(nom=p.name, source=f"{p.value}", charger=p.load)
        for p in sorted(points, key=lambda p: p.name)
    ]
    logger.debug("candidats_points_d_entree: {}", [c.nom for c in resultat])
    return resultat


def _oublier_paquet(paquet: str) -> None:
    """Retire ``paquet`` et ses sous-modules de ``sys.modules`` (import manqué)."""
    logger.debug("_oublier_paquet: {}", paquet)
    for nom in [n for n in sys.modules if n == paquet or n.startswith(paquet + ".")]:
        del sys.modules[nom]
    logger.debug("_oublier_paquet: fait")


def _importer_fichier(nom: str, chemin: Path) -> Any:
    """Importe ``chemin`` comme ``gcm4_plugin_<nom>.gtk4`` et rend sa variable ``PLUGIN``.

    Le dossier du plugin devient le paquet ``gcm4_plugin_<nom>`` (son
    ``__init__.py`` est exécuté s'il existe) : ``gtk4.py`` peut ainsi importer
    ses voisins, par exemple ``from . import core`` (découpage
    ``core.py``/``gtk4.py``, #73). Si l'import échoue, le paquet et tout ce
    qu'il a déjà chargé sont retirés de ``sys.modules``.
    """
    logger.debug("_importer_fichier: {}", chemin)
    paquet = f"gcm4_plugin_{nom}"
    dossier = chemin.parent
    init = dossier / "__init__.py"
    if init.is_file():
        spec_paquet = importlib.util.spec_from_file_location(
            paquet, init, submodule_search_locations=[str(dossier)]
        )
        if spec_paquet is None or spec_paquet.loader is None:
            raise ImportError(str(init))
    else:
        spec_paquet = importlib.machinery.ModuleSpec(paquet, None, is_package=True)
        spec_paquet.submodule_search_locations = [str(dossier)]
    # Lu avant module_from_spec, qui pose un chargeur d'espace de noms
    # (sans effet) sur un paquet sans __init__.py.
    chargeur = spec_paquet.loader
    module_paquet = importlib.util.module_from_spec(spec_paquet)
    sys.modules[paquet] = module_paquet
    try:
        if chargeur is not None:
            chargeur.exec_module(module_paquet)
        spec = importlib.util.spec_from_file_location(f"{paquet}.{chemin.stem}", chemin)
        if spec is None or spec.loader is None:
            raise ImportError(str(chemin))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except BaseException:
        logger.debug("_importer_fichier: échec, paquet retiré")
        _oublier_paquet(paquet)
        raise
    if not hasattr(module, "PLUGIN"):
        raise AttributeError(_("{file} does not define PLUGIN.").format(file=chemin.name))
    logger.debug("_importer_fichier: fait")
    return module.PLUGIN


def candidats_dossier(dossier: Path | None) -> list[Candidat]:
    """Candidats ``dossier/<nom>/gtk4.py``, triés par nom de dossier."""
    logger.debug("candidats_dossier: {}", dossier)
    if dossier is None or not dossier.is_dir():
        logger.debug("candidats_dossier: aucun dossier")
        return []
    resultat = [
        Candidat(
            nom=d.name,
            source=str(d / FICHIER_SOURCE),
            charger=functools.partial(_importer_fichier, d.name, d / FICHIER_SOURCE),
        )
        for d in sorted(dossier.iterdir(), key=lambda p: p.name)
        if (d / FICHIER_SOURCE).is_file()
    ]
    logger.debug("candidats_dossier: {}", [c.nom for c in resultat])
    return resultat


def _en_erreur(nom: str, source: str, raison: str, classe: Any = None) -> EtatPlugin:
    """État en erreur, journalisé en ``warning``."""
    logger.debug("_en_erreur: {!r}", nom)
    logger.warning("plugin {!r} ({}) ignoré : {}", nom, source, raison)
    return EtatPlugin(
        name=nom,
        source=source,
        version=str(getattr(classe, "version", "") or ""),
        protocols=tuple(getattr(classe, "protocols", ()) or ()),
        erreur=raison,
    )


def _charger(candidat: Candidat, contexte: FabriqueDeContexte) -> EtatPlugin:
    """Import, vérification du contrat, construction ; toute erreur reste dans l'état rendu."""
    logger.debug("_charger: {!r}", candidat.nom)
    try:
        classe = candidat.charger()
    except (Exception, SystemExit) as exc:  # un plugin ne doit pas arrêter gcm4
        logger.debug("_charger: import impossible")
        return _en_erreur(
            candidat.nom, candidat.source, _("Import failed: {error}").format(error=exc)
        )
    raison = incompatibilite(classe)
    if raison is not None:
        logger.debug("_charger: incompatible")
        return _en_erreur(
            getattr(classe, "name", "") or candidat.nom, candidat.source, raison, classe
        )
    try:
        plugin = classe(contexte(classe.name))
    except (Exception, SystemExit) as exc:
        logger.debug("_charger: initialisation impossible")
        return _en_erreur(
            classe.name,
            candidat.source,
            _("Initialization failed: {error}").format(error=exc),
            classe,
        )
    etat = EtatPlugin(
        name=classe.name,
        source=candidat.source,
        version=classe.version,
        protocols=tuple(classe.protocols),
        plugin=plugin,
    )
    logger.debug("_charger: {!r} chargé ({})", etat.name, etat.protocols)
    return etat


class PluginRegistry:
    """Plugins chargés (par protocole) et en erreur ; vide = mode zéro plugin."""

    def __init__(self, etats: Iterable[EtatPlugin] = ()) -> None:
        """Range ``etats`` par nom et indexe les protocoles des plugins chargés."""
        logger.debug("PluginRegistry.__init__: entrée")
        self.etats: list[EtatPlugin] = sorted(etats, key=lambda e: (e.name, e.source))
        self._par_protocole: dict[str, ConnectionPlugin] = {}
        for etat in self.etats:
            if etat.plugin is not None:
                for protocole in etat.protocols:
                    self._par_protocole.setdefault(protocole, etat.plugin)
        logger.debug(
            "PluginRegistry.__init__: {} chargé(s), {} en erreur",
            len(self.plugins()),
            len(self.erreurs()),
        )

    def plugins(self) -> list[ConnectionPlugin]:
        """Plugins utilisables, par nom."""
        logger.trace("plugins")
        resultat = [e.plugin for e in self.etats if e.plugin is not None]
        logger.trace("plugins: {}", len(resultat))
        return resultat

    def erreurs(self) -> list[EtatPlugin]:
        """Candidats refusés, avec leur raison."""
        logger.trace("erreurs")
        resultat = [e for e in self.etats if e.erreur is not None]
        logger.trace("erreurs: {}", len(resultat))
        return resultat

    def get(self, protocole: str) -> ConnectionPlugin | None:
        """Plugin du protocole, ou ``None`` (aucune extension ne le gère)."""
        logger.trace("get: {!r}", protocole)
        resultat = self._par_protocole.get(protocole)
        logger.trace("get: {}", resultat.name if resultat else None)
        return resultat

    def protocols(self) -> list[str]:
        """Protocoles gérés, triés (liste du dialogue d'hôte)."""
        logger.trace("protocols")
        resultat = sorted(self._par_protocole)
        logger.trace("protocols: {}", resultat)
        return resultat

    def __contains__(self, protocole: object) -> bool:
        """``"ssh" in registre``."""
        logger.trace("__contains__: {!r}", protocole)
        resultat = protocole in self._par_protocole
        logger.trace("__contains__: {}", resultat)
        return resultat


def decouvrir(
    contexte: FabriqueDeContexte,
    candidats: Iterable[Candidat] | None = None,
) -> PluginRegistry:
    """Charge tous les candidats ; aucune exception d'un plugin ne remonte.

    Args:
        contexte: fabrique du contexte de chaque plugin.
        candidats: par défaut, points d'entrée puis dossier source.

    Doublons : le premier candidat d'un ``name`` gagne ; un protocole déjà
    pris par un plugin chargé fait refuser le suivant.
    """
    logger.debug("decouvrir: entrée")
    if candidats is None:
        candidats = [*candidats_points_d_entree(), *candidats_dossier(dossier_source())]
    etats: list[EtatPlugin] = []
    noms: dict[str, str] = {}
    pris: dict[str, str] = {}
    for candidat in candidats:
        etat = _charger(candidat, contexte)
        if etat.charge and etat.name in noms:
            etat = _en_erreur(
                etat.name,
                etat.source,
                _("A plugin with the same name is already loaded from {source}.").format(
                    source=noms[etat.name]
                ),
            )
        elif etat.charge:
            conflit = next((p for p in etat.protocols if p in pris), None)
            if conflit is not None:
                etat = _en_erreur(
                    etat.name,
                    etat.source,
                    _("Protocol «{protocol}» is already handled by «{other}».").format(
                        protocol=conflit, other=pris[conflit]
                    ),
                )
            else:
                noms[etat.name] = etat.source
                pris.update(dict.fromkeys(etat.protocols, etat.name))
        etats.append(etat)
    registre = PluginRegistry(etats)
    logger.debug("decouvrir: {}", [(e.name, e.charge) for e in registre.etats])
    return registre
