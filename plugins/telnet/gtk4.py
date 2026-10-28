# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin telnet de gcm4 (contrat v1, ``docs/plugins.md``) : terminal VTE GTK4.

Portage de ``plugins/plugin_telnet.py`` (GTK3, #179) ; la logique sans GTK
est dans :mod:`core` (commande, délai des commandes), la session VTE et la
zone d'options communes aux plugins terminal dans :mod:`gcm4.ui.terminal`
(#295). Chargé en mode source comme ``gcm4_plugin_telnet.gtk4`` (#288).

Écarts avec GTK3, voulus ou en attente de la base :

- le mot de passe passe par un tube (descripteur 3 du script expect), et
  non plus par une frappe simulée 2 s après le lancement ;
- la sortie du serveur reste visible pendant la saisie automatique ;
- police, couleurs par défaut, lignes d'historique et séparateurs de mots
  des préférences globales de GTK3 (``[options]``) ne sont pas lus : le
  contrat v1 ne donne au plugin que sa section ``[plugin telnet]``. Les
  couleurs de l'hôte (``font-color``, ``back-color``) sont appliquées.
"""

from __future__ import annotations

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
from gcm4.ui.terminal import LIAISONS, PALETTE, SessionTerminal, zone_options  # noqa: E402
from gcm4.ui.terminal import rgba as _rgba  # noqa: E402

from . import core  # noqa: E402

__all__ = ["LIAISONS", "PALETTE", "PLUGIN", "TelnetPlugin", "TelnetSession", "_rgba"]


class TelnetSession(SessionTerminal):
    """Onglet telnet : session terminal commune, commandes envoyées au bout de 3 s."""

    def __init__(self, host: Host, lancement: core.Lancement, log) -> None:
        """Construit le terminal et lance ``lancement`` (asynchrone, rien ne bloque).

        Args:
            host: hôte ouvert (couleurs, options ``telnet``).
            lancement: commande à lancer (:func:`core.lancement`).
            log: journal du plugin.
        """
        logger.debug("TelnetSession.__init__: {!r}", host.path)
        super().__init__(host, "telnet", lancement, log, core.DELAI_COMMANDES_MS)
        logger.debug("TelnetSession.__init__: fait")


class TelnetPlugin(ConnectionPlugin):
    """Protocole ``telnet`` : terminal VTE, saisie automatique par expect."""

    name = "telnet"
    version = "1.0"
    protocols = ("telnet",)

    def default_port(self, protocol: str) -> int | None:
        """Port 23."""
        logger.debug("TelnetPlugin.default_port: {!r}", protocol)
        logger.debug("TelnetPlugin.default_port: {}", core.PORT_DEFAUT)
        return core.PORT_DEFAUT

    def build_host_options_widget(
        self, host: Host, options: dict[str, str], changed: Callable[[], None]
    ) -> Gtk.Widget:
        """Commandes après connexion, touches d'effacement et ``TERM`` (page « Avancé » de GTK3)."""
        logger.debug("TelnetPlugin.build_host_options_widget: {!r}", host.path)
        zone = zone_options(options, changed)
        logger.debug("TelnetPlugin.build_host_options_widget: fait")
        return zone

    def open(self, host: Host) -> TelnetSession:
        """Lance telnet (ou expect) dans un terminal.

        Raises:
            FileNotFoundError: ``telnet``, ou ``expect`` pour la saisie
                automatique, est introuvable dans ``PATH``.
            ValueError: paramètres supplémentaires mal formés.
        """
        logger.debug("TelnetPlugin.open: {!r}", host.path)
        telnet = shutil.which("telnet")
        if telnet is None:
            logger.debug("TelnetPlugin.open: telnet absent")
            raise FileNotFoundError(_("{program} not found").format(program="telnet"))
        try:
            lancement = core.lancement(
                host.host,
                host.port,
                host.user,
                host.password,
                host.extra_params,
                telnet,
                shutil.which("expect"),
            )
        except FileNotFoundError as exc:
            logger.debug("TelnetPlugin.open: {} absent", exc.args[0])
            raise FileNotFoundError(_("{program} not found").format(program=exc.args[0])) from None
        session = TelnetSession(host, lancement, self.context.log)
        logger.debug("TelnetPlugin.open: session ouverte")
        return session


PLUGIN = TelnetPlugin
