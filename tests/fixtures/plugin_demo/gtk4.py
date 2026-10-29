# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin démo des tests (#151) : protocole ``demo``, onglet ``Gtk.Label``.

Chargé comme un plugin en mode source (``plugins/<nom>/gtk4.py``) :
``registre.candidats_dossier(tests/fixtures)``. Il suit ``docs/plugins.md``
et compte ses ouvertures et fermetures pour les tests.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.plugins import ConnectionPlugin  # noqa: E402


class DemoSession:
    """Onglet : un libellé ; ``close()`` compte les fermetures."""

    def __init__(self, plugin: DemoPlugin, adresse: str) -> None:
        """Libellé « demo:<adresse> »."""
        logger.debug("DemoSession.__init__: {!r}", adresse)
        self.plugin = plugin
        self.widget = Gtk.Label(label=f"demo:{adresse}")
        logger.debug("DemoSession.__init__: prête")

    def close(self) -> None:
        """Compte la fermeture."""
        logger.debug("DemoSession.close: entrée")
        self.plugin.fermetures += 1
        logger.debug("DemoSession.close: fait")


class DemoPlugin(ConnectionPlugin):
    """Plugin minimal : port 4242, option ``mode``, échec si l'adresse vaut ``echec``."""

    name = "demo"
    version = "1.0"
    protocols = ("demo",)

    def __init__(self, context):
        """Compteurs à zéro."""
        logger.debug("DemoPlugin.__init__: entrée")
        super().__init__(context)
        self.ouvertures = 0
        self.fermetures = 0
        logger.debug("DemoPlugin.__init__: prêt")

    def default_port(self, protocol):
        """Port proposé."""
        logger.debug("default_port: {!r}", protocol)
        logger.debug("default_port: 4242")
        return 4242

    def build_host_options_widget(self, host, options, changed):
        """Option ``mode`` : une ligne de saisie (exemple de ``docs/plugins.md``)."""
        logger.debug("build_host_options_widget: entrée")
        ligne = Gtk.Entry()
        ligne.set_placeholder_text("mode")
        ligne.set_text(options.get("mode", ""))

        def saisie(*_args):
            options["mode"] = ligne.get_text()
            changed()

        ligne.connect("changed", saisie)
        logger.debug("build_host_options_widget: fait")
        return ligne

    def open(self, host):
        """Ouvre une session, ou lève si l'adresse vaut ``echec``."""
        logger.debug("open: {!r}", host.path)
        if host.host == "echec":
            logger.debug("open: échec demandé")
            raise ConnectionError("refusée")
        self.ouvertures += 1
        session = DemoSession(self, host.host)
        logger.debug("open: session {}", self.ouvertures)
        return session


PLUGIN = DemoPlugin
