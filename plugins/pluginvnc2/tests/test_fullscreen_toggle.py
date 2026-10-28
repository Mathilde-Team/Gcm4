# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncTab._resolve_fullscreen_action (bouton plein écran, barre
d'outils).

Corrigé le 2026-09-05 : le bouton plein écran appelait
`self.activate_action("win.toggle-fullscreen", None)` sur la seule foi d'une
hypothèse jamais vérifiée (cf. CLAUDE.md § "Pièges"/"Hypothèses"). Vérifié
contre le vrai dépôt gcm4 fourni : aucune action "win.toggle-fullscreen"
n'existe -- le plein écran y est géré par un raccourci clavier (F11) câblé
uniquement sur les widgets VTE, jamais sur les onglets non-terminal. Cet
onglet bascule donc désormais lui-même le plein écran sur sa propre fenêtre
racine (`get_root()`), en suivant un état local. La décision "quel est le
prochain état, quelle méthode Gtk.Window appeler" est extraite en méthode
statique pure (même style que `_resolve_resize_target_size` /
`_resolve_screen_dropdown_index`) pour rester testable sans instancier
`VncTab` ni un vrai `Gtk.Window`.
"""

from __future__ import annotations

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncTab


@requires_gtk4
def test_starts_not_fullscreen_switches_to_fullscreen():
    next_state, method_name = VncTab._resolve_fullscreen_action(False)

    assert next_state is True
    assert method_name == "fullscreen"


@requires_gtk4
def test_currently_fullscreen_switches_back_to_windowed():
    next_state, method_name = VncTab._resolve_fullscreen_action(True)

    assert next_state is False
    assert method_name == "unfullscreen"


@requires_gtk4
def test_toggling_twice_returns_to_the_original_state():
    state = False

    state, method_name = VncTab._resolve_fullscreen_action(state)
    assert (state, method_name) == (True, "fullscreen")

    state, method_name = VncTab._resolve_fullscreen_action(state)
    assert (state, method_name) == (False, "unfullscreen")
