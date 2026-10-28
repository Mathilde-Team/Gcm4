# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour tab_context_menu_core (menu contextuel des onglets, session-31).

Couvre la logique pure (zero Gtk/Gio) de disposition du menu contextuel des
onglets, extraite de ``widgets.TabContextMenu`` pour rester testable sans
stub GTK — voir ``docs/gtk4-migration.md`` section 3.6-ter pour le contexte
du portage et ``CONSIGNES-AGENTS-IA.md`` section 5 pour la regle qui motive
cette extraction.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tab_context_menu_core import (
    TAB_CONTEXT_MENU_ACTIONS,
    build_tab_context_menu_layout,
    flatten_action_names,
)


class TestBuildTabContextMenuLayout(unittest.TestCase):
    """Comportement de build_tab_context_menu_layout() pour les 4 combinaisons d'etat."""

    def test_active_single_pane(self):
        """Onglet actif, notebook a un seul volet : ni reopen, ni split."""
        sections = build_tab_context_menu_layout(is_active=True, multi_pane=False)
        names = flatten_action_names(sections)
        self.assertNotIn("reopen", names)
        self.assertNotIn("split-h", names)
        self.assertNotIn("split-v", names)
        self.assertIn("unsplit", names)

    def test_inactive_single_pane(self):
        """Onglet inactif : reopen present (ancien mnuReopen.show())."""
        sections = build_tab_context_menu_layout(is_active=False, multi_pane=False)
        names = flatten_action_names(sections)
        self.assertIn("reopen", names)
        self.assertNotIn("split-h", names)

    def test_active_multi_pane(self):
        """Plusieurs onglets ouverts : split-h/split-v presents (ancien show())."""
        sections = build_tab_context_menu_layout(is_active=True, multi_pane=True)
        names = flatten_action_names(sections)
        self.assertNotIn("reopen", names)
        self.assertIn("split-h", names)
        self.assertIn("split-v", names)

    def test_inactive_multi_pane_has_everything_conditional(self):
        """Onglet inactif + plusieurs volets : reopen ET split-h/split-v presents."""
        sections = build_tab_context_menu_layout(is_active=False, multi_pane=True)
        names = flatten_action_names(sections)
        self.assertIn("reopen", names)
        self.assertIn("split-h", names)
        self.assertIn("split-v", names)

    def test_unconditional_items_always_present(self):
        """rename/reset/clear/clone/log/unsplit ne dependent d'aucun etat."""
        always_present = {"rename", "reset", "clear", "clone", "log", "unsplit"}
        for is_active in (True, False):
            for multi_pane in (True, False):
                with self.subTest(is_active=is_active, multi_pane=multi_pane):
                    names = set(
                        flatten_action_names(
                            build_tab_context_menu_layout(
                                is_active=is_active, multi_pane=multi_pane
                            )
                        )
                    )
                    self.assertTrue(always_present.issubset(names))

    def test_reopen_ordered_before_clone_when_present(self):
        """L'action reopen reste dans la 1ere section, avant clone (ordre historique)."""
        sections = build_tab_context_menu_layout(is_active=False, multi_pane=False)
        first_section = sections[0]
        self.assertLess(first_section.index("reopen"), first_section.index("clone"))

    def test_no_duplicate_action_names_in_any_combination(self):
        """Chaque action n'apparait qu'une seule fois, quelle que soit la combinaison."""
        for is_active in (True, False):
            for multi_pane in (True, False):
                with self.subTest(is_active=is_active, multi_pane=multi_pane):
                    names = flatten_action_names(
                        build_tab_context_menu_layout(is_active=is_active, multi_pane=multi_pane)
                    )
                    self.assertEqual(len(names), len(set(names)))

    def test_all_layout_names_are_known_actions_or_log(self):
        """Chaque nom retourne correspond a une action de TAB_CONTEXT_MENU_ACTIONS ou "log"."""
        known = {name for name, _code in TAB_CONTEXT_MENU_ACTIONS} | {"log"}
        for is_active in (True, False):
            for multi_pane in (True, False):
                names = flatten_action_names(
                    build_tab_context_menu_layout(is_active=is_active, multi_pane=multi_pane)
                )
                self.assertTrue(set(names).issubset(known))


class TestTabContextMenuActionCodes(unittest.TestCase):
    """Verrouille les codes d'action historiques attendus par Wmain.on_popupmenu."""

    def test_action_codes_match_legacy_menu(self):
        """Les codes doivent rester ceux de l'ancien Gtk.Menu (voir historique #74-#116)."""
        expected = {
            "rename": "R",
            "reset": "RS",
            "clear": "RC",
            "reopen": "RO",
            "clone": "CC",
            "split-h": "SPH",
            "split-v": "SPV",
            "unsplit": "USP",
        }
        self.assertEqual(dict(TAB_CONTEXT_MENU_ACTIONS), expected)

    def test_action_names_unique(self):
        """Aucun nom d'action duplique dans TAB_CONTEXT_MENU_ACTIONS."""
        names = [name for name, _code in TAB_CONTEXT_MENU_ACTIONS]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
