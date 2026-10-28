# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncTab._resolve_screen_dropdown_index (dropdown de sélection
d'écran, barre d'outils, multi-écran).

Bug corrigé le 2026-09-03 : `_on_screens_changed` reconstruit le modèle du
dropdown à CHAQUE `vnc-screens-changed` (le serveur peut la ré-annoncer
pour bien d'autres raisons qu'un vrai changement de moniteur -- ex.
simple rafraîchissement d'EDID) et remettait systématiquement l'index
sélectionné à 0 ("Bureau complet"), même quand l'écran actif
(`VncDisplay._active_screen_id`) était toujours présent dans la nouvelle
liste. Comme `_updating_screen_dropdown` bloque justement
`_on_screen_selected` pendant cette reconstruction, `_active_screen_id`
ne changeait pas -- l'UI affichait "Bureau complet" alors que le crop
réellement montré restait celui de l'écran précédemment choisi. Logique
de résolution d'index extraite en méthode statique testable, même style
que `_gdk_button_to_vnc` / `_resolve_resize_target_size`.

Bug complémentaire corrigé le 2026-09-05 : le correctif du 2026-09-03
n'alignait `_active_screen_id` que dans la branche >= 2 écrans de
`_on_screens_changed` -- repasser sous 2 écrans (retour à un seul écran,
reconnexion vers un serveur sans info multi-écran) sortait de la fonction
par un `return` anticipé, AVANT tout appel à cette méthode, laissant
`_active_screen_id` périmé. `_on_screens_changed` calcule désormais
`selected_index` (et fait l'alignement qui en découle) dans tous les cas,
que le dropdown finisse visible ou non -- cf. les tests avec 0 ou 1 écran
ci-dessous, qui couvrent déjà ce chemin côté logique pure.
"""

from __future__ import annotations

from collections import namedtuple

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncTab

Screen = namedtuple("Screen", "id x y width height")


@requires_gtk4
def test_no_active_screen_selects_full_desktop():
    screens = [
        Screen(id=1, x=0, y=0, width=1920, height=1080),
        Screen(id=2, x=1920, y=0, width=1920, height=1080),
    ]

    assert VncTab._resolve_screen_dropdown_index(screens, None) == 0


@requires_gtk4
def test_active_screen_still_present_keeps_its_selection():
    # Écran 2 toujours actif et toujours présent -> le dropdown doit
    # rester sur son entrée (index 1-based), pas revenir à 0.
    screens = [
        Screen(id=1, x=0, y=0, width=1920, height=1080),
        Screen(id=2, x=1920, y=0, width=1920, height=1080),
    ]

    assert VncTab._resolve_screen_dropdown_index(screens, 2) == 2


@requires_gtk4
def test_active_screen_selection_survives_reordering():
    # Même écran actif, mais la nouvelle annonce liste les écrans dans un
    # ordre différent -- l'index doit suivre la position réelle de
    # l'écran dans la NOUVELLE liste, pas rester figé sur l'ancien index.
    screens = [
        Screen(id=2, x=1920, y=0, width=1920, height=1080),
        Screen(id=1, x=0, y=0, width=1920, height=1080),
    ]

    assert VncTab._resolve_screen_dropdown_index(screens, 2) == 1


@requires_gtk4
def test_vanished_active_screen_falls_back_to_full_desktop():
    # L'écran actif a disparu de cette nouvelle annonce (moniteur
    # déconnecté côté serveur) -- repli sur "Bureau complet", même
    # comportement que _find_active_screen ailleurs dans le fichier.
    screens = [Screen(id=1, x=0, y=0, width=1920, height=1080)]

    assert VncTab._resolve_screen_dropdown_index(screens, 99) == 0


@requires_gtk4
def test_no_screens_at_all_falls_back_to_full_desktop_even_with_stale_active_id():
    # Aucune info ExtendedDesktopSize (0 écran) -- c'est le chemin
    # emprunté par `_on_screens_changed` quand on repasse sous 2 écrans
    # (retour à un seul écran, ou reconnexion vers un serveur qui n'en
    # annonce aucun). Avant le 2026-09-05, `_on_screens_changed` sortait
    # de la fonction sans jamais appeler cette méthode dans ce cas
    # précis, laissant `_active_screen_id` périmé plutôt que de le
    # réaligner sur "Bureau complet" comme le fait cette fonction pure.
    assert VncTab._resolve_screen_dropdown_index([], 7) == 0
