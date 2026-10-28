# SPDX-License-Identifier: GPL-3.0-or-later
"""Verrou textuel sur les signaux GTK3-only de l'éditeur inline `CellTextView` (audit session-42).

Contexte : dernier point resté ouvert de l'audit historique des menus
contextuels (session-29, `docs/gtk4-migration.md` §3.6-ter, item 5 du
tableau), désigné jusqu'ici dans toute la documentation comme « le cas
`Gtk.Entry`/`populate-popup` ». Avant de le porter, vérification du code
réel (même discipline que les audits sessions-28/38) : le widget concerné,
`widgets.py::MultilineCellRenderer`/`CellTextView`, n'est PAS un `Gtk.Entry`
mais une sous-classe de `Gtk.TextView` (+ `Gtk.CellEditable`) — l'éditeur
inline utilisé pour renommer un hôte/dossier par double-clic dans
`treeServers`. `Gtk.Entry`/`GtkText` n'apparaît nulle part dans ce chemin de
code ; l'appellation du tableau §3.6-ter était donc inexacte depuis la
session-29 — corrigée dans `docs/gtk4-migration.md` §3.6-undecies.

Périmètre réel également plus large que documenté : `CellTextView.
do_start_editing()` connecte quatre signaux GTK3-only consécutifs
(`widgets.py:1819-1822`), pas un seul :

1. `focus-out-event` → `Gtk.EventControllerFocus` (signal `leave`)
2. `key-press-event` → `Gtk.EventControllerKey` (signal `key-pressed`)
3. `populate-popup` → **aucun équivalent GTK4 direct** pour l'usage fait ici
   (observer l'ouverture/fermeture du menu contextuel natif, sans y ajouter
   d'item) ; `Gtk.TextView.set_extra_menu(Gio.MenuModel)` existe bien en
   GTK4 mais répond à un besoin différent (ajouter des items personnalisés)
   — voir `docs/gtk4-migration.md` §3.6-undecies pour le détail et la
   question non tranchée (le `Gtk.Popover` interne du menu natif GTK4
   vole-t-il le focus du widget porteur comme le faisait l'ancien
   `Gtk.Menu` top-level ? invérifiable empiriquement dans cet environnement
   de travail, GTK4 absent)
4. `button-press-event` → `Gtk.GestureClick` (site déjà catalogué comme
   « décoy » du point 3 dans `test_gtk4_context_menus_baseline.py`
   vis-à-vis du *déclenchement de menu contextuel*, mais qui reste, comme
   les trois autres ci-dessus, un signal GTK3-only à porter pour ce widget)

Ce fichier ne teste PAS un comportement runtime (même contrainte que
`test_gtk4_context_menus_baseline.py` : `widgets.py` importe `gi`/`Gtk`,
indisponible dans cet environnement de développement). Il verrouille,
sur le même principe, le *nombre exact* de ces connexions — non pas pour
les compter (elles sont uniques par construction) mais pour détecter toute
variation de leur forme textuelle exacte (site déplacé, renommé, ou signal
supprimé sans mise à jour de cette documentation), et servir de check-list
vivante pour une future session de portage : à mesure qu'un signal est
migré, la ligne correspondante ci-dessous doit être ajustée en même temps,
pas oubliée — même logique que `EXPECTED_GESTURECLICK_TRIGGER_SITES`.

Mise à jour session-43 (`docs/gtk4-migration.md` §3.6-duodecies) : premier
des quatre signaux, `key-press-event`, effectivement porté vers
`Gtk.EventControllerKey`/`key-pressed` (logique extraite dans
`inline_editor_core.classify_editor_key_press()`, testée dans
`tests/test_inline_editor_core.py`) — retiré de `EXPECTED_INLINE_EDITOR_SIGNALS`
ci-dessous et remplacé par un verrou positif sur la nouvelle connexion
(`TestInlineEditorSignalBaseline.test_key_pressed_controller_is_wired`).

Mise à jour session-44 (`docs/gtk4-migration.md` §3.6-terdecies) : deuxième
des quatre signaux, `button-press-event`, effectivement porté vers
`Gtk.GestureMultiPress`/`pressed` (logique extraite dans
`inline_editor_core.classify_editor_button_press()`, testée dans
`tests/test_inline_editor_core.py`) — retiré à son tour de
`EXPECTED_INLINE_EDITOR_SIGNALS` ci-dessous et remplacé par un verrou
positif (`test_button_gesture_is_wired`) et un verrou négatif
(`test_button_press_event_signal_is_gone`), même principe que pour
`key-press-event` en session-43. Les deux signaux restants
(`focus-out-event`, `populate-popup`) sont inchangés et toujours
verrouillés ci-dessous.

Si un test échoue ici après un changement délibéré (portage effectif d'un
de ces signaux), c'est le signe attendu qu'il faut ajuster la baseline et
`docs/gtk4-migration.md` §3.6-undecies/§3.6-duodecies — pas une régression
à l'aveugle.
"""

import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIDGETS_FILE = os.path.join(REPO_ROOT, "widgets.py")


def _read(path: str) -> str:
    """Lit un fichier texte du dépôt en UTF-8.

    Args:
        path (str): Chemin absolu du fichier à lire.

    Returns:
        str: Contenu intégral du fichier.
    """
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# Les deux connexions GTK3-only de CellTextView.do_start_editing()
# restant à porter à l'issue de la session-44 (`key-press-event` porté en
# session-43, `button-press-event` porté cette session, tous deux retirés
# de cette table — voir test_key_pressed_controller_is_wired et
# test_button_gesture_is_wired ci-dessous).
EXPECTED_INLINE_EDITOR_SIGNALS = {
    'editor.connect("focus-out-event", self._on_editor_focus_out_event)': 1,
    'editor.connect("populate-popup", self._on_editor_populate_popup)': 1,
}


class TestInlineEditorSignalBaseline(unittest.TestCase):
    """Nombre d'occurrences des quatre `connect()` GTK3-only de `CellTextView`."""

    def test_signal_connections_match_baseline(self):
        """Chaque connexion doit apparaître exactement le nombre attendu de fois.

        Toute variation doit être accompagnée d'une mise à jour consciente
        de `EXPECTED_INLINE_EDITOR_SIGNALS` et de `docs/gtk4-migration.md`
        §3.6-undecies — jamais d'un ajustement muet.
        """
        source = _read(WIDGETS_FILE)
        for needle, expected in EXPECTED_INLINE_EDITOR_SIGNALS.items():
            with self.subTest(needle=needle):
                actual = source.count(needle)
                self.assertEqual(
                    actual,
                    expected,
                    f"{os.path.relpath(WIDGETS_FILE, REPO_ROOT)} : {actual} occurrence(s) "
                    f"de {needle!r} trouvée(s), {expected} attendue(s) (baseline "
                    "session-42). Voir docs/gtk4-migration.md §3.6-undecies avant "
                    "d'ajuster ce test.",
                )

    def test_key_press_event_signal_is_gone(self):
        """L'ancien signal `key-press-event` ne doit plus être connecté.

        Verrouille le portage session-43 : si cette chaîne réapparaît, le
        signal GTK3-only a été réintroduit sans passer par
        `Gtk.EventControllerKey`/`key-pressed`.
        """
        source = _read(WIDGETS_FILE)
        self.assertNotIn(
            'editor.connect("key-press-event",',
            source,
            "key-press-event doit rester porté vers Gtk.EventControllerKey/"
            "key-pressed (session-43, docs/gtk4-migration.md §3.6-duodecies).",
        )

    def test_key_pressed_controller_is_wired(self):
        """Le remplacement `Gtk.EventControllerKey`/`key-pressed` doit être présent.

        Verrouille la construction du contrôleur, la connexion du signal,
        et la référence gardée sur `editor` pour éviter une libération
        prématurée du wrapper Python (même précaution que pour les
        `Gtk.Gesture*` portés en session-39/40/41).
        """
        source = _read(WIDGETS_FILE)
        for needle in (
            "editor._key_controller = Gtk.EventControllerKey.new(editor)",
            'editor._key_controller.connect("key-pressed", self._on_editor_key_pressed)',
            "def _on_editor_key_pressed(self, controller, keyval, keycode, state):",
        ):
            with self.subTest(needle=needle):
                self.assertIn(
                    needle,
                    source,
                    f"{needle!r} attendu dans widgets.py (portage session-43 de "
                    "key-press-event, docs/gtk4-migration.md §3.6-duodecies).",
                )

    def test_button_press_event_signal_is_gone(self):
        """L'ancien signal `button-press-event` ne doit plus être connecté sur l'éditeur.

        Verrouille le portage session-44 : si cette chaîne réapparaît, le
        signal GTK3-only a été réintroduit sans passer par
        `Gtk.GestureMultiPress`/`pressed`.
        """
        source = _read(WIDGETS_FILE)
        self.assertNotIn(
            'editor.connect("button-press-event",',
            source,
            "button-press-event doit rester porté vers Gtk.GestureMultiPress/"
            "pressed (session-44, docs/gtk4-migration.md §3.6-terdecies).",
        )

    def test_button_gesture_is_wired(self):
        """Le remplacement `Gtk.GestureMultiPress`/`pressed` doit être présent.

        Verrouille la construction du geste, `set_button(0)` (tous
        boutons, comme l'ancien signal ne distinguait pas non plus le
        bouton), la connexion du signal, et la référence gardée sur
        `editor` pour éviter une libération prématurée du wrapper Python
        (même précaution que pour `_key_controller` en session-43 et les
        `Gtk.Gesture*` des sessions 39-41).
        """
        source = _read(WIDGETS_FILE)
        for needle in (
            "editor._button_gesture = Gtk.GestureMultiPress.new(editor)",
            "editor._button_gesture.set_button(0)",
            'editor._button_gesture.connect("pressed", self._on_editor_button_pressed)',
            "def _on_editor_button_pressed(self, gesture, n_press, x, y):",
        ):
            with self.subTest(needle=needle):
                self.assertIn(
                    needle,
                    source,
                    f"{needle!r} attendu dans widgets.py (portage session-44 de "
                    "button-press-event, docs/gtk4-migration.md §3.6-terdecies).",
                )

    def test_editor_widget_is_not_a_gtk_entry(self):
        """`CellTextView` est une sous-classe de `Gtk.TextView`, pas de `Gtk.Entry`.

        Verrouille la correction de terminologie de la session-42 : la
        documentation antérieure (`docs/gtk4-migration.md` §3.6-ter,
        `docs/features-backlog.md`, `CLAUDE.md`) désignait ce widget comme
        « `Gtk.Entry` » — inexact, vérifié par lecture du code source.
        """
        source = _read(WIDGETS_FILE)
        self.assertIn(
            "class CellTextView(Gtk.TextView, Gtk.CellEditable):",
            source,
            "CellTextView doit rester une sous-classe de Gtk.TextView — si ce "
            "widget a changé de base, docs/gtk4-migration.md §3.6-undecies et "
            "ce test doivent être revus ensemble.",
        )
        cell_text_view_body = source.split("class CellTextView")[-1].split(
            "class MultilineCellRenderer"
        )[0]
        self.assertNotIn(
            "Gtk.Entry(",
            cell_text_view_body,
            "Aucun Gtk.Entry( attendu dans le corps de CellTextView — ce widget "
            "édite via Gtk.TextView, pas Gtk.Entry (voir "
            "docs/gtk4-migration.md §3.6-undecies).",
        )


if __name__ == "__main__":
    unittest.main()
