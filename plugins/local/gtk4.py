"""Plugin local de gcm4 (contrat v1, ``docs/plugins.md``) : shell local dans un terminal VTE GTK4.

Portage de ``plugins/plugin_local.py`` (GTK3, #179, #295) ; la logique sans
GTK est dans :mod:`core` (shell, délai des commandes), la session VTE et la
zone d'options communes aux plugins terminal dans :mod:`gcm4.ui.terminal`.
Chargé en mode source comme ``gcm4_plugin_local.gtk4`` (#288).

Écarts avec GTK3, voulus ou en attente de la base :

- un shell introuvable est signalé à l'ouverture (``FileNotFoundError``),
  au lieu d'un onglet vide ;
- police, couleurs par défaut, lignes d'historique et séparateurs de mots
  des préférences globales de GTK3 (``[options]``) ne sont pas lus : le
  contrat v1 ne donne au plugin que sa section ``[plugin local]``. Les
  couleurs de l'hôte (``font-color``, ``back-color``) sont appliquées.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)
gi.require_version("Vte", "3.91")

from gi.repository import Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402
from gcm4.core.model import Host  # noqa: E402
from gcm4.core.plugins import ConnectionPlugin  # noqa: E402
from gcm4.ui.terminal import SessionTerminal, zone_options  # noqa: E402

from . import core  # noqa: E402


class LocalSession(SessionTerminal):
    """Onglet local : session terminal commune, commandes envoyées au bout de 700 ms."""

    def __init__(self, host: Host, lancement: core.Lancement, log) -> None:
        """Construit le terminal et lance le shell (asynchrone, rien ne bloque).

        Args:
            host: hôte ouvert (couleurs, options ``local``).
            lancement: commande à lancer (:func:`core.lancement`).
            log: journal du plugin.
        """
        logger.debug("LocalSession.__init__: {!r}", host.path)
        super().__init__(host, "local", lancement, log, core.DELAI_COMMANDES_MS)
        logger.debug("LocalSession.__init__: fait")


class LocalPlugin(ConnectionPlugin):
    """Protocole ``local`` : shell de l'utilisateur, sans hôte distant."""

    name = "local"
    version = "1.0"
    protocols = ("local",)

    def default_port(self, protocol: str) -> int | None:
        """Aucun port : rien n'est contacté sur le réseau."""
        logger.debug("LocalPlugin.default_port: {!r}", protocol)
        logger.debug("LocalPlugin.default_port: aucun")
        return None

    def requires_address(self, protocol: str) -> bool:
        """Pas d'adresse : le shell local ne contacte aucun hôte (#297)."""
        logger.debug("LocalPlugin.requires_address: {!r}", protocol)
        logger.debug("LocalPlugin.requires_address: False")
        return False

    def build_host_options_widget(
        self, host: Host, options: dict[str, str], changed: Callable[[], None]
    ) -> Gtk.Widget:
        """Commandes après ouverture, touches d'effacement et ``TERM`` (comme telnet)."""
        logger.debug("LocalPlugin.build_host_options_widget: {!r}", host.path)
        zone = zone_options(options, changed)
        logger.debug("LocalPlugin.build_host_options_widget: fait")
        return zone

    def open(self, host: Host) -> LocalSession:
        """Lance le shell de l'utilisateur dans un terminal.

        Raises:
            FileNotFoundError: le shell (``SHELL`` ou ``/bin/sh``) est
                introuvable ou n'est pas exécutable.
        """
        logger.debug("LocalPlugin.open: {!r}", host.path)
        programme = core.shell(os.environ)
        chemin = shutil.which(programme)
        if chemin is None:
            logger.debug("LocalPlugin.open: {} absent", programme)
            raise FileNotFoundError(_("{program} not found").format(program=programme))
        session = LocalSession(host, core.lancement(chemin), self.context.log)
        logger.debug("LocalPlugin.open: session ouverte")
        return session


PLUGIN = LocalPlugin
