# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour remote_desktop_menu_core (menu contextuel bureau distant, session-53).

Couvre la logique pure (zéro Gtk/Gio) de disposition et de sensibilité du
menu contextuel bureau distant, extraite de
``widgets.RemoteDesktopContextMenu`` pour rester testable sans stub GTK —
voir ``docs/gtk4-migration.md`` §3.8 pour le contexte du portage et
``CONSIGNES-AGENTS-IA.md`` section 5 pour la règle qui motive cette
extraction. Même principe que ``tests/test_tab_context_menu_core.py``
(session-31), ``tests/test_folder_context_menu_core.py`` (session-32) et
``tests/test_popup_menu_core.py`` (session-33).
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from remote_desktop_menu_core import (
    REMOTE_DESKTOP_SPECIAL_KEY_COMBOS,
    build_remote_desktop_menu_layout,
    compute_remote_desktop_enabled_actions,
)


class TestBuildRemoteDesktopMenuLayout(unittest.TestCase):
    """Comportement de build_remote_desktop_menu_layout() selon extra_items."""

    def test_no_extra_items(self):
        """Sans entrée supplémentaire : section principale réduite à connect/disconnect."""
        main_actions, special_key_actions = build_remote_desktop_menu_layout()
        self.assertEqual(main_actions, ("connect", "disconnect"))
        self.assertEqual(len(special_key_actions), len(REMOTE_DESKTOP_SPECIAL_KEY_COMBOS))

    def test_extra_items_generate_sequential_action_names(self):
        """Chaque entrée supplémentaire obtient un nom d'action extra0, extra1, ..."""
        extras = [("USB", lambda _w: None), ("Autre", lambda _w: None)]
        main_actions, _ = build_remote_desktop_menu_layout(extras)
        self.assertEqual(main_actions, ("connect", "disconnect", "extra0", "extra1"))

    def test_empty_extra_items_list_same_as_none(self):
        """Liste vide et None produisent la même disposition (len() == 0)."""
        self.assertEqual(
            build_remote_desktop_menu_layout([]), build_remote_desktop_menu_layout(None)
        )

    def test_special_key_actions_zero_based_and_exhaustive(self):
        """Le sous-menu énumère special-key-0..N-1, un par combinaison historique."""
        _, special_key_actions = build_remote_desktop_menu_layout()
        expected = tuple(f"special-key-{i}" for i in range(len(REMOTE_DESKTOP_SPECIAL_KEY_COMBOS)))
        self.assertEqual(special_key_actions, expected)

    def test_no_duplicate_action_names(self):
        """Aucun nom d'action dupliqué, quelle que soit la combinaison."""
        for extras in (
            None,
            [],
            [("a", lambda _w: None)],
            [("a", lambda _w: None), ("b", lambda _w: None)],
        ):
            with self.subTest(extras=extras):
                main_actions, special_key_actions = build_remote_desktop_menu_layout(extras)
                all_names = tuple(main_actions) + tuple(special_key_actions)
                self.assertEqual(len(all_names), len(set(all_names)))


class TestComputeRemoteDesktopEnabledActions(unittest.TestCase):
    """Comportement de compute_remote_desktop_enabled_actions()."""

    def test_all_four_combinations(self):
        """La sensibilité de chaque action reflète exactement celle du bouton correspondant."""
        for connect_sensitive in (True, False):
            for disconnect_sensitive in (True, False):
                with self.subTest(connect=connect_sensitive, disconnect=disconnect_sensitive):
                    enabled = compute_remote_desktop_enabled_actions(
                        connect_sensitive, disconnect_sensitive
                    )
                    self.assertEqual(
                        enabled,
                        {"connect": connect_sensitive, "disconnect": disconnect_sensitive},
                    )

    def test_non_boolean_inputs_coerced(self):
        """Les valeurs non booléennes (ex. retours get_sensitive() truthy) sont normalisées."""
        enabled = compute_remote_desktop_enabled_actions(1, 0)
        self.assertEqual(enabled, {"connect": True, "disconnect": False})


class TestRemoteDesktopSpecialKeyCombos(unittest.TestCase):
    """Verrouille les combinaisons de touches spéciales historiques (sessions 28-46)."""

    def test_historical_combos_unchanged(self):
        """Les 5 combinaisons du Gtk.Menu historique sont conservées à l'identique."""
        self.assertEqual(
            REMOTE_DESKTOP_SPECIAL_KEY_COMBOS,
            (
                ("Ctrl+Alt+Suppr", ("Control_L", "Alt_L", "Delete")),
                ("Ctrl+Échap", ("Control_L", "Escape")),
                ("Alt+Tab", ("Alt_L", "Tab")),
                ("Alt+F4", ("Alt_L", "F4")),
                ("Impr. écran", ("Print",)),
            ),
        )

    def test_every_combo_has_label_and_keys(self):
        """Chaque entrée : libellé non vide + combinaison non vide (noms de touches Gdk)."""
        for label, keys in REMOTE_DESKTOP_SPECIAL_KEY_COMBOS:
            with self.subTest(label=label):
                self.assertTrue(label)
                self.assertTrue(all(keys))
                self.assertIsInstance(keys, tuple)


if __name__ == "__main__":
    unittest.main()
