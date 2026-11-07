# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin outil démo des tests (#73) : entrée de menu, onglet ``Gtk.Label``.

Chargé comme un plugin en mode source (``plugins/<nom>/gtk4.py``) :
``registre.candidats_dossier(tests/fixtures)``. Il suit ``docs/plugins.md``
et compte ses activations pour les tests.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import N_  # noqa: E402
from gcm4.core.plugins import ToolPlugin  # noqa: E402


class OutilDemo(ToolPlugin):
    """Outil minimal : ouvre un onglet « outil:demo », échoue si demandé."""

    name = "outil-demo"
    version = "1.0"
    menu_label = N_("Demo Tool…")
    menu_section = "outils"

    def __init__(self, context) -> None:
        """Compte les activations et les onglets ouverts."""
        logger.debug("OutilDemo.__init__: {!r}", self.name)
        super().__init__(context)
        self.activations = 0
        self.onglets: list[str] = []
        logger.debug("OutilDemo.__init__: fait")

    def activate(self) -> None:
        """Ouvre un onglet factice ; lève si le message vaut « echec »."""
        logger.debug("OutilDemo.activate: entrée")
        self.activations += 1
        self.context.open_tab("outil:demo", Gtk.Label(label="outil:demo"))
        self.onglets.append("demo")
        logger.debug("OutilDemo.activate: fait")


PLUGIN = OutilDemo
