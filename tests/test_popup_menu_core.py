# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour popup_menu_core (menu contextuel du terminal, session-33).

Couvre la logique pure (zero Gtk/Gio) du menu contextuel ``popupMenu``,
extraite de ``widgets.PopupMenu`` pour rester testable sans stub GTK — voir
``docs/gtk4-migration.md`` section 3.6-sexies pour le contexte du portage et
``CONSIGNES-AGENTS-IA.md`` section 5 pour la regle qui motive cette
extraction.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from popup_menu_core import (
    POPUP_MENU_ACTIONS,
    POPUP_MENU_SECTIONS,
    VARIABLE_SENSITIVITY_ACTIONS,
    compute_enabled_actions,
    flatten_action_names,
    format_command_label,
    select_command_shortcuts,
)


class TestPopupMenuSections(unittest.TestCase):
    """Structure constante de POPUP_MENU_SECTIONS (pas de disposition conditionnelle)."""

    def test_two_static_sections(self):
        """Le menu modelise 2 sections statiques (la 3e, dynamique, est a part)."""
        self.assertEqual(len(POPUP_MENU_SECTIONS), 2)

    def test_no_duplicate_action_names(self):
        """Chaque nom d'action n'apparait qu'une seule fois."""
        names = flatten_action_names(POPUP_MENU_SECTIONS)
        self.assertEqual(len(names), len(set(names)))

    def test_all_popup_menu_actions_present(self):
        """Chaque action de POPUP_MENU_ACTIONS apparait dans les sections."""
        names = set(flatten_action_names(POPUP_MENU_SECTIONS))
        expected = {name for name, _code in POPUP_MENU_ACTIONS}
        self.assertTrue(expected.issubset(names))

    def test_log_present_though_not_in_popup_menu_actions(self):
        """Le nom log figure dans les sections mais pas dans POPUP_MENU_ACTIONS (action a etat)."""
        names = flatten_action_names(POPUP_MENU_SECTIONS)
        self.assertIn("log", names)
        self.assertNotIn("log", dict(POPUP_MENU_ACTIONS))

    def test_variable_sensitivity_actions_all_known(self):
        """Les 4 actions a sensibilite variable existent bien dans les sections."""
        names = set(flatten_action_names(POPUP_MENU_SECTIONS))
        self.assertTrue(set(VARIABLE_SENSITIVITY_ACTIONS).issubset(names))


class TestPopupMenuActionCodes(unittest.TestCase):
    """Verrouille les codes d'action historiques attendus par Wmain.on_popupmenu."""

    def test_action_codes_match_legacy_menu(self):
        """Les codes doivent rester ceux de l'ancien Gtk.Menu (voir historique #74-#116)."""
        expected = {
            "copy": "C",
            "paste": "V",
            "copy-paste": "CV",
            "select-all": "A",
            "copy-all": "CA",
            "save-buffer": "S",
            "split-h": "SPH",
            "split-v": "SPV",
            "unsplit": "USP",
            "reset": "RS2",
            "clear": "RC2",
            "clone": "CC2",
            "close": "X",
        }
        self.assertEqual(dict(POPUP_MENU_ACTIONS), expected)

    def test_action_names_unique(self):
        """Aucun nom d'action duplique dans POPUP_MENU_ACTIONS."""
        names = [name for name, _code in POPUP_MENU_ACTIONS]
        self.assertEqual(len(names), len(set(names)))

    def test_action_codes_unique(self):
        """Aucun code duplique (chaque item doit rester distinguable par on_popupmenu)."""
        codes = [code for _name, code in POPUP_MENU_ACTIONS]
        self.assertEqual(len(codes), len(set(codes)))

    def test_suffix_2_codes_distinguish_from_tab_context_menu(self):
        """Les codes RS2/RC2/CC2 restent distincts des codes RS/RC/CC de popupMenuTab."""
        codes = dict(POPUP_MENU_ACTIONS)
        self.assertEqual(codes["reset"], "RS2")
        self.assertEqual(codes["clear"], "RC2")
        self.assertEqual(codes["clone"], "CC2")


class TestComputeEnabledActions(unittest.TestCase):
    """Comportement de compute_enabled_actions() pour les combinaisons d'etat."""

    def test_all_true(self):
        """Selection presente, notebook multi-volets, notebook scinde : tout actif."""
        result = compute_enabled_actions(
            has_selection=True, multi_pane=True, has_split_notebook=True
        )
        self.assertEqual(result, {"copy": True, "split-h": True, "split-v": True, "unsplit": True})

    def test_all_false(self):
        """Aucune selection, notebook mono-volet, pas de notebook scinde : tout inactif."""
        result = compute_enabled_actions(
            has_selection=False, multi_pane=False, has_split_notebook=False
        )
        self.assertEqual(
            result, {"copy": False, "split-h": False, "split-v": False, "unsplit": False}
        )

    def test_flags_are_independent(self):
        """copy/unsplit ne suivent pas multi_pane ; split-h/split-v ne suivent pas has_selection."""
        result = compute_enabled_actions(
            has_selection=True, multi_pane=False, has_split_notebook=True
        )
        self.assertTrue(result["copy"])
        self.assertFalse(result["split-h"])
        self.assertFalse(result["split-v"])
        self.assertTrue(result["unsplit"])

    def test_split_h_and_split_v_always_match_multi_pane(self):
        """split-h et split-v suivent toujours exactement multi_pane."""
        for multi_pane in (True, False):
            with self.subTest(multi_pane=multi_pane):
                result = compute_enabled_actions(
                    has_selection=False, multi_pane=multi_pane, has_split_notebook=False
                )
                self.assertEqual(result["split-h"], multi_pane)
                self.assertEqual(result["split-v"], multi_pane)

    def test_result_keys_match_variable_sensitivity_actions(self):
        """Les cles du resultat correspondent exactement a VARIABLE_SENSITIVITY_ACTIONS."""
        result = compute_enabled_actions(
            has_selection=False, multi_pane=False, has_split_notebook=False
        )
        self.assertEqual(set(result.keys()), set(VARIABLE_SENSITIVITY_ACTIONS))


class TestSelectCommandShortcuts(unittest.TestCase):
    """Comportement de select_command_shortcuts() (filtre du sous-menu dynamique)."""

    def test_excludes_list_values(self):
        """Les entrees a valeur liste (sequences de commandes) sont exclues."""
        shortcuts = {"a": "echo hello", "b": ["grouped", "commands"]}
        result = select_command_shortcuts(shortcuts)
        self.assertEqual(result, [("a", "echo hello")])

    def test_empty_shortcuts(self):
        """Un dict vide donne une liste vide."""
        self.assertEqual(select_command_shortcuts({}), [])

    def test_preserves_insertion_order(self):
        """L'ordre d'insertion de shortcuts est preserve (pas de tri alphabetique)."""
        shortcuts = {"z": "cmd-z", "a": "cmd-a", "m": "cmd-m"}
        result = select_command_shortcuts(shortcuts)
        self.assertEqual([key for key, _cmd in result], ["z", "a", "m"])

    def test_all_simple_values_kept(self):
        """Toutes les entrees a valeur simple sont conservees."""
        shortcuts = {"one": "cmd1", "two": "cmd2"}
        result = select_command_shortcuts(shortcuts)
        self.assertEqual(result, [("one", "cmd1"), ("two", "cmd2")])


class TestFormatCommandLabel(unittest.TestCase):
    """Comportement de format_command_label() (libelle du sous-menu dynamique)."""

    def test_short_command_not_truncated(self):
        """Une commande courte n'est pas tronquee."""
        self.assertEqual(format_command_label("F1", "ls -la"), "[F1] ls -la")

    def test_long_command_truncated_to_30_chars_by_default(self):
        """Une commande longue est tronquee a 30 caracteres par defaut."""
        command = "a" * 50
        label = format_command_label("F2", command)
        self.assertEqual(label, f"[F2] {'a' * 30}")

    def test_custom_max_length(self):
        """max_command_length est respecte quand fourni explicitement."""
        self.assertEqual(format_command_label("F3", "abcdefgh", max_command_length=3), "[F3] abc")

    def test_no_pango_markup_in_output(self):
        """Le libelle ne doit contenir aucun balisage Pango (Gio.MenuItem : texte brut)."""
        label = format_command_label("F4", "echo hi")
        self.assertNotIn("<span", label)


if __name__ == "__main__":
    unittest.main()
