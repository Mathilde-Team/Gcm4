# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour folder_context_menu_core (menu contextuel du panneau de serveurs, session-32).

Couvre la logique pure (zero Gtk/Gio) de disposition du menu contextuel
``popupMenuFolder``, extraite de ``widgets.FolderContextMenu`` pour rester
testable sans stub GTK — voir ``docs/gtk4-migration.md`` section
3.6-quinquies pour le contexte du portage et ``CONSIGNES-AGENTS-IA.md``
section 5 pour la regle qui motive cette extraction.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from folder_context_menu_core import (
    CLICK_TARGETS,
    build_folder_context_menu_layout,
    flatten_action_names,
    should_show_protocol_items,
)


class TestBuildFolderContextMenuLayout(unittest.TestCase):
    """Comportement de build_folder_context_menu_layout() pour les 3 cibles de clic."""

    def test_empty_click_hides_node_specific_items(self):
        """Clic dans le vide : ni edit/delete/duplicate/rename-group, ni copy-address."""
        names = flatten_action_names(build_folder_context_menu_layout(click_target="empty"))
        for absent in ("copy-address", "edit", "delete", "duplicate", "rename-group"):
            self.assertNotIn(absent, names)

    def test_empty_click_keeps_always_visible_items(self):
        """Clic dans le vide : les actions globales restent presentes."""
        names = flatten_action_names(build_folder_context_menu_layout(click_target="empty"))
        for present in (
            "connect",
            "add",
            "new-group",
            "group-color",
            "group-color-reset",
            "expand",
            "collapse",
        ):
            self.assertIn(present, names)

    def test_folder_click_shows_rename_group_hides_host_only_items(self):
        """Noeud dossier/groupe : rename-group et delete presents, edit/copy-address/duplicate absents."""
        names = flatten_action_names(build_folder_context_menu_layout(click_target="folder"))
        self.assertIn("rename-group", names)
        self.assertIn("delete", names)
        for absent in ("edit", "copy-address", "duplicate"):
            self.assertNotIn(absent, names)

    def test_host_click_shows_host_only_items_hides_rename_group(self):
        """Noeud hote : edit/copy-address/duplicate/delete presents, rename-group absent."""
        names = flatten_action_names(build_folder_context_menu_layout(click_target="host"))
        for present in ("edit", "copy-address", "duplicate", "delete"):
            self.assertIn(present, names)
        self.assertNotIn("rename-group", names)

    def test_invalid_click_target_raises(self):
        """Une cible inconnue leve ValueError plutot que de degrader silencieusement."""
        with self.assertRaises(ValueError):
            build_folder_context_menu_layout(click_target="bogus")

    def test_all_click_targets_covered(self):
        """CLICK_TARGETS couvre bien les trois cas testes ci-dessus (garde-fou)."""
        self.assertEqual(set(CLICK_TARGETS), {"empty", "folder", "host"})


class TestShouldShowProtocolItems(unittest.TestCase):
    """Comportement de should_show_protocol_items() (section injectee par plugin)."""

    def test_shown_only_for_host(self):
        """Les items protocole ne sont visibles que pour un noeud hote."""
        self.assertTrue(should_show_protocol_items(click_target="host"))
        self.assertFalse(should_show_protocol_items(click_target="folder"))
        self.assertFalse(should_show_protocol_items(click_target="empty"))

    def test_invalid_click_target_raises(self):
        """Une cible inconnue leve ValueError."""
        with self.assertRaises(ValueError):
            should_show_protocol_items(click_target="bogus")


if __name__ == "__main__":
    unittest.main()
