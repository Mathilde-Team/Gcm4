# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin ipmisol de gcm4 (contrat v1, ``docs/plugins.md``) : SOL dans un terminal VTE GTK4.

Portage de ``plugins/plugin_ipmisol.py`` (GTK3, #179, étape 2 de
``docs/gtk4-plugins-portage.md``) ; la logique sans GTK est dans
:mod:`core` (arguments ipmitool, ``IPMI_PASSWORD``, ``sol deactivate``),
la session VTE et la zone d'options communes aux plugins terminal dans
:mod:`gcm4.ui.terminal`. Chargé en mode source comme
``gcm4_plugin_ipmisol.gtk4`` (#288).

Écarts avec GTK3, voulus ou en attente de la base :

- la session démarre à l'ouverture de l'hôte (comme local et telnet) : les
  boutons Connecter/Déconnecter et la barre d'état du dialogue GTK3 ne
  sont pas repris — se déconnecter revient à fermer l'onglet, qui
  déclenche ``sol deactivate`` ;
- la page d'options du dialogue GTK3 était vide ; la zone commune des
  plugins terminal (commandes, effacement, ``TERM``) est proposée, lue
  par la session commune ;
- police et couleurs par défaut des préférences globales GTK3
  (``[options]``) ne sont pas lues : le contrat v1 ne donne au plugin que
  sa section ``[plugin ipmisol]``. Les couleurs de l'hôte sont appliquées.
"""

from __future__ import annotations

import os
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


class IpmiSolSession(SessionTerminal):
    """Onglet SOL : session terminal commune, commandes envoyées au bout de 700 ms."""

    def __init__(self, host: Host, lancement: core.Lancement, log, notifier=None) -> None:
        """Construit le terminal et lance ipmitool (asynchrone, rien ne bloque).

        Args:
            host: hôte ouvert (couleurs, options ``ipmi``).
            lancement: commande à lancer (:func:`core.lancement`).
            log: journal du plugin.
            notifier: toast d'échec de connexion (#80, ``context.notify``).
        """
        logger.debug("IpmiSolSession.__init__: {!r}", host.path)
        super().__init__(host, "ipmi", lancement, log, core.DELAI_COMMANDES_MS, notifier)
        self._chemin_ipmitool, self._hote_ipmi = lancement.argv[0], host
        logger.debug("IpmiSolSession.__init__: fait")

    def close(self) -> None:
        """Termine la session puis déverrouille le BMC (``sol deactivate``).

        Comme le dialogue GTK3 : une session SOL fermée sans ``deactivate``
        reste verrouillée côté BMC jusqu'à expiration d'un timeout. Bloquant
        jusqu'à 10 s, l'onglet étant déjà fermé à l'appel.
        """
        logger.debug("IpmiSolSession.close: entrée")
        super().close()
        if core.desactivation(self._chemin_ipmitool, self._hote_ipmi):
            logger.debug("IpmiSolSession.close: sol deactivate envoyé")
        else:
            logger.debug("IpmiSolSession.close: sol deactivate sans effet")
        logger.debug("IpmiSolSession.close: fait")


class IpmiSolPlugin(ConnectionPlugin):
    """Protocole ``ipmi`` : SOL (console série des cartes de gestion)."""

    name = "ipmisol"
    version = "1.0"
    protocols = ("ipmi",)  # protocol_id GTK3 : les configurations existantes

    def default_port(self, protocol: str) -> int | None:
        """Port UDP 623."""
        logger.debug("IpmiSolPlugin.default_port: {!r}", protocol)
        logger.debug("IpmiSolPlugin.default_port: {}", core.PORT_DEFAUT)
        return core.PORT_DEFAUT

    def build_host_options_widget(
        self, host: Host, options: dict[str, str], changed: Callable[[], None]
    ) -> Gtk.Widget:
        """Commandes après connexion, touches d'effacement et ``TERM`` (zone commune)."""
        logger.debug("IpmiSolPlugin.build_host_options_widget: {!r}", host.path)
        zone = zone_options(options, changed)
        logger.debug("IpmiSolPlugin.build_host_options_widget: fait")
        return zone

    def open(self, host: Host) -> IpmiSolSession:
        """Lance ``ipmitool sol activate`` dans un terminal.

        Raises:
            FileNotFoundError: ``ipmitool`` est introuvable dans ``PATH``.
            ValueError: paramètres supplémentaires mal formés.
        """
        logger.debug("IpmiSolPlugin.open: {!r}", host.path)
        ipmitool = core.chemin(os.environ)
        if ipmitool is None:
            logger.debug("IpmiSolPlugin.open: ipmitool absent")
            raise FileNotFoundError(_("{program} not found").format(program=core.IPMITOOL))
        lancement = core.lancement(ipmitool, host)
        session = IpmiSolSession(host, lancement, self.context.log, notifier=self.context.notify)
        logger.debug("IpmiSolPlugin.open: session ouverte")
        return session


PLUGIN = IpmiSolPlugin
