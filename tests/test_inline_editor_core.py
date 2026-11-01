# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour inline_editor_core (éditeur inline `CellTextView`, sessions 43-44).

Couvre la logique pure (zéro Gtk/Gdk) de classification d'un appui de
touche (`classify_editor_key_press`, session-43) et, depuis la session-44,
d'un clic souris (`classify_editor_button_press`) dans l'éditeur inline de
`treeServers`. La première est extraite de
`MultilineCellRenderer._on_editor_key_press_event` lors de son portage vers
`Gtk.EventControllerKey`/`key-pressed` ; la seconde de
`MultilineCellRenderer._on_editor_pressed` lors de son portage vers
`Gtk.GestureClick`/`Gtk.GestureMultiPress` — voir `docs/gtk4-migration.md`
§3.6-undecies pour le contexte (audit session-42), §3.6-duodecies pour le
premier portage (session-43) et §3.6-terdecies pour le second
(session-44). `CONSIGNES-AGENTS-IA.md` section 5 impose cette extraction
pour rester testable sans stub GTK.

Les valeurs Gdk réelles sont codées en dur ici (comme dans
`test_gesture_trigger_core.py` pour les numéros de bouton) plutôt
qu'importées de `gi.repository.Gdk`, absent de cet environnement de
travail : ``Gdk.ModifierType.SHIFT_MASK`` = 1, ``Gdk.ModifierType.
CONTROL_MASK`` = 4, ``Gdk.KEY_Return`` = 0xFF0D, ``Gdk.KEY_KP_Enter`` =
0xFF8D, ``Gdk.KEY_Escape`` = 0xFF1B (valeurs stables du protocole X11
keysyms, reprises telles quelles par GDK).
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from inline_editor_core import (
    EDITOR_BUTTON_PRESS_OUTCOMES,
    EDITOR_KEY_PRESS_OUTCOMES,
    classify_editor_button_press,
    classify_editor_key_press,
)

SHIFT_MASK = 1
CONTROL_MASK = 4
KEY_RETURN = 0xFF0D
KEY_KP_ENTER = 0xFF8D
KEY_ESCAPE = 0xFF1B
KEY_A = 0x061  # touche "a", exemple de texte normal


def _classify(keyval, state=0):
    """Appelle classify_editor_key_press() avec les constantes Gdk fixées ci-dessus.

    Args:
        keyval (int): Code de la touche a classifier.
        state (int): Masque des modificateurs actifs (defaut : aucun).

    Returns:
        str: Une des valeurs de EDITOR_KEY_PRESS_OUTCOMES.
    """
    return classify_editor_key_press(
        keyval=keyval,
        state=state,
        shift_mask=SHIFT_MASK,
        control_mask=CONTROL_MASK,
        return_keyval=KEY_RETURN,
        kp_enter_keyval=KEY_KP_ENTER,
        escape_keyval=KEY_ESCAPE,
    )


class TestClassifyEditorKeyPress(unittest.TestCase):
    """Comportement de classify_editor_key_press() pour (keyval, state)."""

    def test_return_commits(self):
        """Entree, sans modificateur : valider l'edition."""
        self.assertEqual(_classify(KEY_RETURN), "commit")

    def test_kp_enter_commits(self):
        """Entree du pave numerique, sans modificateur : valider l'edition."""
        self.assertEqual(_classify(KEY_KP_ENTER), "commit")

    def test_escape_cancels(self):
        """Echap, sans modificateur : annuler l'edition."""
        self.assertEqual(_classify(KEY_ESCAPE), "cancel")

    def test_plain_letter_is_ignored(self):
        """Touche de texte normal, sans modificateur : ignoree (laissee a l'editeur)."""
        self.assertEqual(_classify(KEY_A), "ignore")

    def test_shift_return_is_ignored(self):
        """Entree avec Shift enfonce : ignoree, contrairement a l'Entree seule.

        Reproduit la priorite de l'ancien code : le test du modificateur
        est fait avant celui de ``keyval``, donc Shift+Entree ne valide pas.
        """
        self.assertEqual(_classify(KEY_RETURN, state=SHIFT_MASK), "ignore")

    def test_control_escape_is_ignored(self):
        """Echap avec Ctrl enfonce : ignoree, contrairement a l'Echap seule."""
        self.assertEqual(_classify(KEY_ESCAPE, state=CONTROL_MASK), "ignore")

    def test_shift_and_control_together_is_ignored(self):
        """Les deux modificateurs a la fois bloquent aussi la validation."""
        self.assertEqual(_classify(KEY_RETURN, state=SHIFT_MASK | CONTROL_MASK), "ignore")

    def test_unrelated_modifier_does_not_block(self):
        """Un bit de modificateur hors Shift/Ctrl (ex. Mod1/Alt) ne bloque pas.

        L'ancien code ne testait que ``SHIFT_MASK | CONTROL_MASK`` : un autre
        bit du masque (ici 0x8, Mod1/Alt) ne doit pas empecher la validation.
        """
        self.assertEqual(_classify(KEY_RETURN, state=0x8), "commit")

    def test_all_outcomes_are_declared(self):
        """Toute valeur retournee appartient a EDITOR_KEY_PRESS_OUTCOMES."""
        for keyval, state in (
            (KEY_RETURN, 0),
            (KEY_KP_ENTER, 0),
            (KEY_ESCAPE, 0),
            (KEY_A, 0),
            (KEY_RETURN, SHIFT_MASK),
        ):
            with self.subTest(keyval=keyval, state=state):
                self.assertIn(_classify(keyval, state), EDITOR_KEY_PRESS_OUTCOMES)


class TestClassifyEditorButtonPress(unittest.TestCase):
    """Comportement de classify_editor_button_press() pour n_press."""

    def test_single_click_claims(self):
        """Clic simple : revendiquer la sequence (contournement du bug GTK3)."""
        self.assertEqual(classify_editor_button_press(n_press=1), "claim")

    def test_double_click_claims(self):
        """Double clic : meme issue qu'un clic simple, aucun branchement sur n_press."""
        self.assertEqual(classify_editor_button_press(n_press=2), "claim")

    def test_triple_click_claims(self):
        """Triple clic : meme issue, confirme l'absence totale de branchement."""
        self.assertEqual(classify_editor_button_press(n_press=3), "claim")

    def test_all_outcomes_are_declared(self):
        """Toute valeur retournee appartient a EDITOR_BUTTON_PRESS_OUTCOMES."""
        for n_press in (1, 2, 3):
            with self.subTest(n_press=n_press):
                self.assertIn(
                    classify_editor_button_press(n_press=n_press),
                    EDITOR_BUTTON_PRESS_OUTCOMES,
                )


if __name__ == "__main__":
    unittest.main()


class TestFocusLeave(unittest.TestCase):
    """#96 : la perte de focus valide l'édition, sauf popover ouvert ou édition terminée."""

    def test_commit_par_defaut(self):
        """Hors popover, la perte de focus valide."""

        from inline_editor_core import classify_editor_focus_leave

        self.assertEqual(classify_editor_focus_leave(menu_open=False, done=False), "commit")

    def test_ignore_pendant_le_popover(self):
        """Popover ouvert : pas de validation."""

        from inline_editor_core import classify_editor_focus_leave

        self.assertEqual(classify_editor_focus_leave(menu_open=True, done=False), "ignore")

    def test_ignore_apres_entree_ou_echap(self):
        """Édition déjà terminée : rien à refaire."""

        from inline_editor_core import classify_editor_focus_leave

        self.assertEqual(classify_editor_focus_leave(menu_open=False, done=True), "ignore")

    def test_issues_dans_la_liste(self):
        """Liste des issues possibles."""

        from inline_editor_core import EDITOR_FOCUS_LEAVE_OUTCOMES

        self.assertEqual(EDITOR_FOCUS_LEAVE_OUTCOMES, ("commit", "ignore"))


class TestEditorMenu(unittest.TestCase):
    """#96 : popover maîtrisé, quatre actions standard, ouvert au clic droit."""

    def test_quatre_actions_standard(self):
        """Couper, copier, coller, tout sélectionner."""

        from inline_editor_core import EDITOR_MENU_ACTIONS

        self.assertEqual(
            [a[0] for a in EDITOR_MENU_ACTIONS], ["cut", "copy", "paste", "select-all"]
        )
        self.assertEqual(
            [a[2] for a in EDITOR_MENU_ACTIONS],
            ["cut-clipboard", "copy-clipboard", "paste-clipboard", "select-all"],
        )

    def test_clic_droit_seulement(self):
        """Seul le bouton 3 ouvre le popover."""

        from inline_editor_core import is_editor_menu_button

        self.assertTrue(is_editor_menu_button(3))
        for b in (0, 1, 2, 8):
            with self.subTest(button=b):
                self.assertFalse(is_editor_menu_button(b))
