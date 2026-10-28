# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncTab._resolve_resize_target_size (bouton "ajuster la
fenêtre à la taille distante actuelle", barre d'outils).

Bug corrigé le 2026-09-02 : ce bouton visait toujours `_last_remote_size`
(taille du framebuffer COMPLET, mise à jour par le signal
vnc-size-changed), même quand un écran spécifique était sélectionné dans
le dropdown multi-écran (cf. set_active_screen / _find_active_screen dans
vnc_tab.py). Résultat : la fenêtre grossissait à la taille du bureau
entier alors que seul un écran recadré était affiché. La logique de
résolution de la taille cible est extraite en méthode statique (même
style que `VncDisplay._gdk_button_to_vnc`) pour rester testable sans
instancier `VncTab` au complet.
"""

from __future__ import annotations

from collections import namedtuple

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncTab

Screen = namedtuple("Screen", "id x y width height")


@requires_gtk4
def test_no_active_screen_uses_full_remote_size():
    screens = [Screen(id=1, x=0, y=0, width=1920, height=1080)]

    result = VncTab._resolve_resize_target_size(screens, None, (3840, 1080))

    assert result == (3840, 1080)


@requires_gtk4
def test_active_screen_uses_that_screen_size_not_full_desktop():
    # Bureau complet 3840x1080 (deux écrans 1920x1080 côte à côte), mais
    # l'écran 2 est sélectionné -> la cible doit être 1920x1080, pas 3840x1080.
    screens = [
        Screen(id=1, x=0, y=0, width=1920, height=1080),
        Screen(id=2, x=1920, y=0, width=1920, height=1080),
    ]

    result = VncTab._resolve_resize_target_size(screens, 2, (3840, 1080))

    assert result == (1920, 1080)


@requires_gtk4
def test_vanished_active_screen_falls_back_to_full_remote_size():
    # L'écran sélectionné a disparu de la liste courante (moniteur
    # déconnecté côté serveur) -- même repli que _find_active_screen,
    # pas de crash sur un déballage impossible.
    screens = [Screen(id=1, x=0, y=0, width=1920, height=1080)]

    result = VncTab._resolve_resize_target_size(screens, 99, (3840, 1080))

    assert result == (3840, 1080)


@requires_gtk4
def test_returns_none_when_nothing_known_yet():
    # Avant le tout premier vnc-size-changed : rien à viser, l'appelant
    # doit pouvoir distinguer "pas encore de taille" de "0x0".
    result = VncTab._resolve_resize_target_size([], None, None)

    assert result is None


@requires_gtk4
def test_active_screen_with_no_screens_known_falls_back_to_none():
    result = VncTab._resolve_resize_target_size([], 2, None)

    assert result is None
