# SPDX-License-Identifier: GPL-3.0-or-later
"""Adaptateur v1 du composant monofichier vnc_tab, laissé autonome (#179).

Le composant est chargé à la demande sous un nom stable : redécouvrir les
plugins ne réenregistre pas ses types GObject. L'absence d'asyncvnc2 ne
bloque ni le registre ni les autres protocoles.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402
from gcm4.core.model import Host  # noqa: E402
from gcm4.core.plugins import ConnectionPlugin  # noqa: E402
from gcm4.ui import boucle  # noqa: E402

from . import core  # noqa: E402

MODULE_WIDGET = "gcm4_widget_vnc"


def charger_widget() -> ModuleType:
    """Charge une fois le voisin autonome, sans ajouter de dossier au sys.path.

    Raises:
        FileNotFoundError: Le composant ou sa dépendance asyncvnc2 manque.

    Returns:
        Le module contenant VncConnectionInfo et VncTab.
    """
    logger.debug("charger_widget: entrée")
    if MODULE_WIDGET in sys.modules:
        logger.debug("charger_widget: sortie, module déjà chargé")
        return sys.modules[MODULE_WIDGET]
    try:
        chemin = Path(__file__).with_name("vnc_tab.py")
        spec = importlib.util.spec_from_file_location(MODULE_WIDGET, chemin)
        if spec is None or spec.loader is None:
            logger.debug("charger_widget: chargeur absent")
            raise ImportError("vnc_tab.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE_WIDGET] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.debug("charger_widget: échec, module partiel retiré ({})", type(exc).__name__)
        sys.modules.pop(MODULE_WIDGET, None)
        if isinstance(exc, ImportError):
            logger.debug("charger_widget: sortie, dépendance absente")
            raise FileNotFoundError(_("{program} not found").format(program="AsyncVnc4 (asyncvnc2)")) from exc
        raise
    logger.debug("charger_widget: sortie, module chargé")
    return module


def zone_options(options: dict[str, str], changed: Callable[[], None]) -> Gtk.Box:
    """Édite la copie d'options ; la base ne la persiste qu'à validation du dialogue.

    Args:
        options: Copie mutable d'options, pas le magasin.
        changed: Notification de changement pour revalider le dialogue.

    Returns:
        La boîte des contrôles ; aucun import du client ni accès réseau.
    """
    logger.debug("zone_options: entrée")
    zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    zone.controles = {}
    for cle, libelle, defaut in (
        (core.OPT_PARTAGE, _("Shared session (VNC)"), True),
        (core.OPT_LECTURE_SEULE, _("View only"), False),
        (core.OPT_PLEIN_ECRAN, _("Fullscreen"), False),
        (core.OPT_PRESSE_PAPIER, _("Share clipboard"), True),
    ):
        bouton = Gtk.CheckButton(label=libelle, active=core.booleen(options, cle, defaut))

        def basculer(controle: Gtk.CheckButton, option: str = cle) -> None:
            """Reporte la case changée et demande la revalidation du formulaire."""
            logger.debug("zone_options.basculer: entrée, clé={}", option)
            options[option] = "True" if controle.get_active() else "False"
            changed()
            logger.debug("zone_options.basculer: sortie")

        bouton.connect("toggled", basculer)
        zone.controles[cle] = bouton
        zone.append(bouton)
    ligne = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    ligne.append(Gtk.Label(label=_("Display"), xalign=0))
    entree = Gtk.Entry(text=options.get(core.OPT_AFFICHAGE, ""), hexpand=True)

    def afficher(controle: Gtk.Entry) -> None:
        """Reporte le numéro d'affichage sans transformer une saisie invalide."""
        logger.debug("zone_options.afficher: entrée")
        options[core.OPT_AFFICHAGE] = controle.get_text()
        changed()
        logger.debug("zone_options.afficher: sortie")

    entree.connect("changed", afficher)
    zone.controles[core.OPT_AFFICHAGE] = entree
    ligne.append(entree)
    zone.append(ligne)
    logger.debug("zone_options: sortie")
    return zone


class VncSession:
    """Session v1 : vrai VncTab, fermeture idempotente et plein écran historique."""

    def __init__(self, host: Host, reglages: core.Reglages, module: ModuleType) -> None:
        """Construit le widget et lance son cycle de connexion asynchrone existant.

        Args:
            host: Hôte et informations d'authentification, jamais journalisées.
            reglages: Réglages validés par le cœur du plugin.
            module: Composant autonome déjà importé.
        """
        logger.debug("VncSession.__init__: entrée")
        self.ferme = False
        self.plein_ecran = reglages.plein_ecran
        info = module.VncConnectionInfo(
            name=host.name,
            host=host.host,
            port=reglages.port,
            username=host.user or None,
            password=host.password or None,
            shared=reglages.partage,
            sync_clipboard=reglages.presse_papier,
            view_only=reglages.lecture_seule,
        )
        self.widget = module.VncTab(info)
        if reglages.lecture_seule:
            # Le display n'installe aucun contrôleur d'entrée ; les actions
            # publiques sont aussi gardées dans le composant. La barre est
            # inerte mais l'image reste normalement sensible et lisible.
            self.widget.toolbar.set_sensitive(False)
        self.widget.connect("map", self._afficher)
        logger.debug("VncSession.__init__: sortie")

    def _afficher(self, _widget: Gtk.Widget) -> None:
        """Applique le plein écran demandé une seule fois, lorsque la racine existe.

        Args:
            _widget: Source du signal map.
        """
        logger.debug("VncSession._afficher: entrée")
        if self.plein_ecran and not self.ferme:
            racine = self.widget.get_root()
            if isinstance(racine, Gtk.Window):
                racine.fullscreen()
                self.plein_ecran = False
        logger.debug("VncSession._afficher: sortie")

    def close(self) -> None:
        """Annule connexion/reconnexion et ferme le client via l'API du composant."""
        logger.debug("VncSession.close: entrée")
        if not self.ferme:
            self.ferme = True
            self.widget.close()
        logger.debug("VncSession.close: sortie")


class VncPlugin(ConnectionPlugin):
    """Connexion VNC embarquée : pas de GtkVnc GTK3, de XEmbed ou de viewer externe."""

    name = "vnc"
    version = "1.0"
    protocols = ("vnc",)

    def default_port(self, protocol: str) -> int:
        """Port RFB TCP 5900, sauf affichage ou port propre à l'hôte.

        Args:
            protocol: Protocole demandé au contrat v1.

        Returns:
            Le port RFB TCP par défaut.
        """
        logger.debug("VncPlugin.default_port: entrée")
        logger.debug("VncPlugin.default_port: sortie {}", core.PORT_DEFAUT)
        return core.PORT_DEFAUT

    def default_options(self, protocol: str) -> dict[str, str]:
        """Valeurs historiques et partage actif par défaut (#82).

        Args:
            protocol: Protocole demandé au contrat v1.

        Returns:
            Une nouvelle copie des options INI.
        """
        logger.debug("VncPlugin.default_options: entrée")
        resultat = core.options_defaut()
        logger.debug("VncPlugin.default_options: sortie")
        return resultat

    def validate_options(self, protocol: str, options: Mapping[str, str]) -> list:
        """Valide le numéro d'affichage avant enregistrement.

        Args:
            protocol: Protocole demandé au contrat v1.
            options: Copie des options du formulaire.

        Returns:
            Les erreurs de champ, ou une liste vide.
        """
        logger.debug("VncPlugin.validate_options: entrée")
        resultat = core.valider(options)
        logger.debug("VncPlugin.validate_options: sortie")
        return resultat

    def build_host_options_widget(self, host: Host, options: dict[str, str], changed: Callable[[], None]) -> Gtk.Widget:
        """Cases de partage, lecture seule, plein écran et presse-papier, puis affichage.

        Args:
            host: Hôte en cours d'édition ; non modifié par ce formulaire.
            options: Copie mutable des options.
            changed: Notification du dialogue parent.

        Returns:
            Le formulaire GTK4 du protocole.
        """
        logger.debug("VncPlugin.build_host_options_widget: entrée")
        resultat = zone_options(options, changed)
        logger.debug("VncPlugin.build_host_options_widget: sortie")
        return resultat

    def open(self, host: Host) -> VncSession:
        """Valide puis lance le widget dans la boucle asyncio/GLib de l'application.

        Args:
            host: Hôte à ouvrir ; le mot de passe reste dans le client en mémoire.

        Raises:
            RuntimeError: PyGObject ne fournit pas la boucle asyncio/GLib.
            FileNotFoundError: AsyncVnc4 n'est pas installé.
            ValueError: Options ou port invalides.

        Returns:
            La session de contrat v1 propriétaire du widget et de sa fermeture.
        """
        logger.debug("VncPlugin.open: entrée")
        reglages = core.reglages(host)
        if not boucle.asyncio_disponible():
            logger.debug("VncPlugin.open: sortie, asyncio absent")
            raise RuntimeError(_("VNC needs asyncio in the GTK main loop (PyGObject 3.50 or later)"))
        session = VncSession(host, reglages, charger_widget())
        logger.debug("VncPlugin.open: sortie, session créée")
        return session


PLUGIN = VncPlugin
