# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests unitaires pour plugins/plugin_base.py — verrou GTK3 levé (session-25).

Couvre le comportement NOUVEAU de cette session : ``plugin_base.py`` ne
verrouille plus ``gi.require_version("Gtk", "3.0")`` ni n'importe plus
``Gtk`` au niveau module (voir ``proposition-architecture-plugins-gtk4.md``
§4). L'unique usage de ``Gtk`` restant dans le fichier est en annotation de
type, déjà paresseuse grâce à ``from __future__ import annotations`` — donc
protégée par un bloc ``if TYPE_CHECKING:``.

Contrairement à ``tests/test_gcm.py`` (qui doit stubber ``gi``/``Gtk``/``Vte``
pour importer ``gnome_connection_manager.py``), ce fichier vérifie
justement l'ABSENCE de tout besoin de stub GTK pour importer
``plugin_base`` — c'est le point précis de la régression à verrouiller : si
quelqu'un réintroduit un `import gi`/`from gi.repository import Gtk` réel
en tête de fichier, ce test échoue immédiatement, y compris dans un
environnement où PyGObject n'est pas installé du tout (cas de cet
environnement de développement).
"""

import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
)


class TestPluginBaseGtkAgnostic(unittest.TestCase):
    """Vérifie que plugin_base.py s'importe sans jamais charger gi/Gtk."""

    def setUp(self):
        """Retire gi/plugin_base de sys.modules pour un import propre à chaque test."""
        for mod in ("gi", "gi.repository", "plugin_base"):
            sys.modules.pop(mod, None)

    def test_import_does_not_require_gi_module(self):
        """plugin_base doit s'importer même si `gi` n'est pas dans sys.modules avant coup."""
        self.assertNotIn("gi", sys.modules)
        module = importlib.import_module("plugin_base")
        self.assertIsNotNone(module)

    def test_import_does_not_load_gi_as_a_side_effect(self):
        """Importer plugin_base ne doit pas, lui-même, déclencher `import gi`."""
        self.assertNotIn("gi", sys.modules)
        importlib.import_module("plugin_base")
        self.assertNotIn(
            "gi",
            sys.modules,
            "plugin_base.py a réintroduit un import gi réel au niveau module "
            "— cassera le chargement de tout plugin GTK4 dans le même process "
            "(voir proposition-architecture-plugins-gtk4.md §4).",
        )

    def test_no_require_version_call_in_source(self):
        """Verrou textuel : aucun gi.require_version("Gtk", ...) dans le fichier."""
        module = importlib.import_module("plugin_base")
        source = open(module.__file__, encoding="utf-8").read()
        self.assertNotIn('require_version("Gtk"', source)

    def test_public_contract_unchanged(self):
        """Le contrat public (__all__, classes) ne doit pas bouger avec ce refactor."""
        module = importlib.import_module("plugin_base")
        self.assertEqual(
            set(module.__all__),
            {"ConnectionPlugin", "PluginRegistry", "BatchPlugin", "BatchPluginRegistry"},
        )
        self.assertTrue(hasattr(module, "ConnectionPlugin"))
        self.assertTrue(hasattr(module, "PluginRegistry"))
        self.assertTrue(hasattr(module, "BatchPlugin"))
        self.assertTrue(hasattr(module, "BatchPluginRegistry"))


if __name__ == "__main__":
    unittest.main()
