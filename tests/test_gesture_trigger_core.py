"""Tests pour gesture_trigger_core (déclenchement Gtk.GestureClick, session-39).

Couvre la logique pure (zéro Gtk/Gdk) de classification d'un clic sur
treeServers, extraite de `Wmain.on_tvServers_button_press_event` lors de son
portage vers `Gtk.GestureMultiPress` — voir `docs/gtk4-migration.md`
§3.6-octies pour le contexte du portage et `CONSIGNES-AGENTS-IA.md` section 5
pour la règle qui motive cette extraction.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gesture_trigger_core import (
    TAB_LABEL_CLICK_OUTCOMES,
    TERMINAL_CLICK_OUTCOMES,
    TREE_SERVERS_PRESS_OUTCOMES,
    classify_tab_label_click,
    classify_terminal_click,
    classify_tree_servers_press,
    masque_actif_dans_etat,
)


class TestClassifyTreeServersPress(unittest.TestCase):
    """Comportement de classify_tree_servers_press() pour (bouton, n_press)."""

    def test_single_right_click_opens_menu(self):
        """Clic droit simple (bouton 3, n_press=1) : ouvrir popupMenuFolder."""
        self.assertEqual(classify_tree_servers_press(button=3, n_press=1), "open-menu")

    def test_single_left_click_propagates(self):
        """Clic gauche simple (bouton 1, n_press=1) : laisser le traitement par defaut."""
        self.assertEqual(classify_tree_servers_press(button=1, n_press=1), "propagate")

    def test_single_middle_click_propagates(self):
        """Clic milieu simple (bouton 2, n_press=1) : laisser le traitement par defaut.

        Non geree explicitement par l'ancien gestionnaire (aucune branche
        dediee au bouton 2) : tombe deja dans le meme cas que le clic gauche.
        """
        self.assertEqual(classify_tree_servers_press(button=2, n_press=1), "propagate")

    def test_double_right_click_swallows_second_press(self):
        """Deuxieme pression d'un double clic droit (bouton 3, n_press=2) : avaler."""
        self.assertEqual(classify_tree_servers_press(button=3, n_press=2), "swallow")

    def test_double_left_click_swallows_second_press(self):
        """Deuxieme pression d'un double clic gauche (bouton 1, n_press=2) : avaler."""
        self.assertEqual(classify_tree_servers_press(button=1, n_press=2), "swallow")

    def test_triple_click_swallows_regardless_of_button(self):
        """Troisieme pression (n_press=3), tout bouton confondu : avaler."""
        self.assertEqual(classify_tree_servers_press(button=1, n_press=3), "swallow")
        self.assertEqual(classify_tree_servers_press(button=3, n_press=3), "swallow")

    def test_all_outcomes_reachable_and_documented(self):
        """Les trois issues possibles sont bien celles annoncees par le module (garde-fou)."""
        reached = {
            classify_tree_servers_press(button=3, n_press=1),
            classify_tree_servers_press(button=1, n_press=1),
            classify_tree_servers_press(button=1, n_press=2),
        }
        self.assertEqual(reached, set(TREE_SERVERS_PRESS_OUTCOMES))


class TestMasqueActifDansEtat(unittest.TestCase):
    """Comportement de masque_actif_dans_etat() selon le format de get_state().

    Voir `docs/gtk4-migration.md` §3.6-quaterdecies (issue #72) : PyGObject
    récent (vérifié 3.56) renvoie un tuple résultat ``(succès, état)`` où un
    ``&`` direct lève TypeError — la fonction doit normaliser les deux
    formats (flags seuls et tuple) sans dépendre de Gtk/Gdk.
    """

    def test_flags_seuls_masque_actif(self):
        """Flags seuls (PyGObject ancien), masque présent : True."""
        self.assertTrue(masque_actif_dans_etat(0b100, 0b100))

    def test_flags_seuls_masque_absent(self):
        """Flags seuls, masque absent : False."""
        self.assertFalse(masque_actif_dans_etat(0b011, 0b100))

    def test_tuple_resultat_masque_actif(self):
        """Tuple (succès, état) de PyGObject récent, masque présent : True.

        C'est le format _ResultTuple observé empiriquement sous PyGObject
        3.56 (Ubuntu 26.04) — l'ancien « & » direct plantait ici.
        """
        self.assertTrue(masque_actif_dans_etat((True, 0b100), 0b100))

    def test_tuple_resultat_masque_absent(self):
        """Tuple (succès, état), masque absent : False."""
        self.assertFalse(masque_actif_dans_etat((True, 0b011), 0b100))

    def test_tuple_resultat_succes_faux(self):
        """Tuple avec succès=False : l'état n'est pas garanti, donc non testé.

        Sémantique du tuple résultat GIR : l'état n'est valide que si la
        lecture réussit ; on ne se fie pas à sa valeur sinon.
        """
        self.assertFalse(masque_actif_dans_etat((False, 0b100), 0b100))

    def test_tuple_vide(self):
        """Tuple vide : aucun état exploitable, masque réputé inactif."""
        self.assertFalse(masque_actif_dans_etat((), 0b100))

    def test_aucun_etat_renvoie_faux(self):
        """get_state() à None (aucun état) : masque réputé inactif."""
        self.assertFalse(masque_actif_dans_etat(None, 0b100))

    def test_tuple_un_seul_element(self):
        """Tuple réduit à l'état seul : le dernier élément reste l'état."""
        self.assertTrue(masque_actif_dans_etat((0b100,), 0b100))


class TestClassifyTerminalClick(unittest.TestCase):
    """Comportement de classify_terminal_click() pour (bouton, n_press, ctrl, paste_on_right_click).

    Voir `docs/gtk4-migration.md` §3.6-nonies (session-40) pour le contexte
    du portage et la correspondance avec l'ancien `if`/`elif`.
    """

    def test_single_right_click_opens_menu_by_default(self):
        """Clic droit simple, PASTE_ON_RIGHT_CLICK desactive : ouvrir popupMenu."""
        self.assertEqual(
            classify_terminal_click(
                button=3, n_press=1, ctrl_pressed=False, paste_on_right_click=False
            ),
            "open-menu",
        )

    def test_single_right_click_pastes_when_preference_enabled(self):
        """Clic droit simple, PASTE_ON_RIGHT_CLICK active : coller le presse-papier."""
        self.assertEqual(
            classify_terminal_click(
                button=3, n_press=1, ctrl_pressed=False, paste_on_right_click=True
            ),
            "paste",
        )

    def test_ctrl_left_click_checks_url(self):
        """Ctrl+clic gauche simple : detecter un lien sous le curseur."""
        self.assertEqual(
            classify_terminal_click(
                button=1, n_press=1, ctrl_pressed=True, paste_on_right_click=False
            ),
            "check-url",
        )

    def test_plain_left_click_propagates(self):
        """Clic gauche simple sans Ctrl : laisser le traitement par defaut de VTE."""
        self.assertEqual(
            classify_terminal_click(
                button=1, n_press=1, ctrl_pressed=False, paste_on_right_click=False
            ),
            "propagate",
        )

    def test_ctrl_right_click_still_opens_menu(self):
        """Ctrl+clic droit : le clic droit reste prioritaire (comme l'ancien if/elif)."""
        self.assertEqual(
            classify_terminal_click(
                button=3, n_press=1, ctrl_pressed=True, paste_on_right_click=False
            ),
            "open-menu",
        )

    def test_double_and_triple_click_never_swallowed(self):
        """Double/triple clic, tout bouton confondu : jamais avale (selection native VTE).

        Contrairement a classify_tree_servers_press(), aucune issue
        "swallow" n'existe ici : l'ancien code ne testait que
        event.type == Gdk.EventType.BUTTON_PRESS, jamais les types
        multi-clic, laissant VTE gerer nativement la selection d'un mot ou
        d'une ligne.
        """
        for button in (1, 2, 3):
            for n_press in (2, 3):
                with self.subTest(button=button, n_press=n_press):
                    self.assertEqual(
                        classify_terminal_click(
                            button=button,
                            n_press=n_press,
                            ctrl_pressed=False,
                            paste_on_right_click=False,
                        ),
                        "propagate",
                    )

    def test_middle_click_propagates(self):
        """Clic milieu simple : non gere explicitement par l'ancien code, propage."""
        self.assertEqual(
            classify_terminal_click(
                button=2, n_press=1, ctrl_pressed=False, paste_on_right_click=False
            ),
            "propagate",
        )

    def test_all_outcomes_reachable_and_documented(self):
        """Les quatre issues possibles sont bien celles annoncees par le module (garde-fou)."""
        reached = {
            classify_terminal_click(
                button=3, n_press=1, ctrl_pressed=False, paste_on_right_click=True
            ),
            classify_terminal_click(
                button=3, n_press=1, ctrl_pressed=False, paste_on_right_click=False
            ),
            classify_terminal_click(
                button=1, n_press=1, ctrl_pressed=True, paste_on_right_click=False
            ),
            classify_terminal_click(
                button=1, n_press=1, ctrl_pressed=False, paste_on_right_click=False
            ),
        }
        self.assertEqual(reached, set(TERMINAL_CLICK_OUTCOMES))


class TestClassifyTabLabelClick(unittest.TestCase):
    """Comportement de classify_tab_label_click() pour (bouton, n_press).

    Voir `docs/gtk4-migration.md` §3.6-decies (session-41) pour le contexte
    du portage et la correspondance avec l'ancien `if`/`elif` de
    `widgets.NotebookTabLabel.popupmenu`.
    """

    def test_single_right_click_opens_menu(self):
        """Clic droit simple (bouton 3, n_press=1) : ouvrir le menu de l'onglet."""
        self.assertEqual(classify_tab_label_click(button=3, n_press=1), "open-menu")

    def test_single_middle_click_closes_tab(self):
        """Clic milieu simple (bouton 2, n_press=1) : fermer l'onglet."""
        self.assertEqual(classify_tab_label_click(button=2, n_press=1), "close-tab")

    def test_single_left_click_propagates(self):
        """Clic gauche simple (bouton 1, n_press=1) : non gere, laisser le traitement par defaut.

        Non gere explicitement par l'ancien gestionnaire (aucune branche
        dediee au bouton 1) : le changement d'onglet est gere ailleurs
        (mecanisme propre de Gtk.Notebook), pas par ce gestionnaire.
        """
        self.assertEqual(classify_tab_label_click(button=1, n_press=1), "propagate")

    def test_unhandled_button_propagates(self):
        """Bouton au-dela de 3 (peripherique inhabituel) : laisser le traitement par defaut."""
        self.assertEqual(classify_tab_label_click(button=8, n_press=1), "propagate")

    def test_double_and_triple_click_never_swallowed(self):
        """Double/triple clic, tout bouton confondu : jamais avale (comme classify_terminal_click).

        Contrairement a classify_tree_servers_press(), aucune issue
        "swallow" n'existe ici : l'ancien code ne testait que
        event.type == Gdk.EventType.BUTTON_PRESS, jamais les types
        multi-clic — la deuxieme pression d'un double clic droit ne
        rouvre donc pas une seconde fois le menu via cette classification
        (contrairement a ce qu'aurait litteralement fait l'ancien evenement
        BUTTON_PRESS supplementaire emis par GDK pour cette meme pression).
        """
        for button in (1, 2, 3):
            for n_press in (2, 3):
                with self.subTest(button=button, n_press=n_press):
                    self.assertEqual(
                        classify_tab_label_click(button=button, n_press=n_press),
                        "propagate",
                    )

    def test_all_outcomes_reachable_and_documented(self):
        """Les trois issues possibles sont bien celles annoncees par le module (garde-fou)."""
        reached = {
            classify_tab_label_click(button=3, n_press=1),
            classify_tab_label_click(button=2, n_press=1),
            classify_tab_label_click(button=1, n_press=1),
        }
        self.assertEqual(reached, set(TAB_LABEL_CLICK_OUTCOMES))


if __name__ == "__main__":
    unittest.main()
