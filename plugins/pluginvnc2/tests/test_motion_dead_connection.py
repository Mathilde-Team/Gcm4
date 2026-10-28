# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncDisplay._on_motion face à une connexion déjà morte.

Bug corrigé le 2026-09-10 : contrairement à `_on_button_pressed` et
`_on_button_released` (gardés depuis le 2026-09-08/09 contre une exception
de `mouse.move()`/`hold_cm.__enter__()`/`__exit__()`), `_on_motion` n'avait
aucun garde équivalent alors qu'il appelle lui aussi `self.client.mouse.move()`.
`self.client` n'est jamais remis à `None` après une erreur de lecture
(`_read_loop` se contente de `_set_status(ERROR, ...)`), donc le garde
`if not self.client:` en tête de la méthode ne protège pas le cas où la
connexion tombe pendant un déplacement de souris -- un `mouse.move()` sur un
socket déjà mort pouvait alors lever hors du gestionnaire de signal GTK.
`_on_motion` est appelé à haute fréquence (chaque déplacement), donc ce
chemin est en pratique le plus facilement atteignable des trois.

Ces tests appellent la VRAIE méthode de `VncDisplay` (pas une
réimplémentation de la logique) via `conftest.make_test_display`, en
substituant juste `client.mouse.move` par une fonction qui lève.
"""

from __future__ import annotations

from conftest import make_test_display, requires_gtk4


class FakeMouse:
    def __init__(self):
        self.moved_to = None

    def move(self, x, y):
        self.moved_to = (x, y)


class FakeMotionController:
    """Remplace Gtk.EventControllerMotion : _on_motion n'utilise que les
    arguments (ctrl, x, y) qui lui sont passés directement par GTK."""


def _make_display_with_fake_mouse():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display.client.mouse = FakeMouse()
    return display


@requires_gtk4
def test_motion_moves_mouse_normally():
    display = _make_display_with_fake_mouse()
    ctrl = FakeMotionController()

    display._on_motion(ctrl, 10, 20)

    assert display.client.mouse.moved_to is not None


@requires_gtk4
def test_motion_swallows_exception_from_dead_connection_move():
    display = _make_display_with_fake_mouse()
    ctrl = FakeMotionController()

    def _boom(*_args):
        raise OSError("connexion déjà fermée (simulé)")

    display.client.mouse.move = _boom

    display._on_motion(ctrl, 10, 20)  # ne doit pas lever


@requires_gtk4
def test_motion_works_normally_again_after_a_failed_move():
    # Contre-épreuve fonctionnelle : après un déplacement raté (move() a
    # levé et a été avalé), un nouveau déplacement doit se comporter
    # normalement -- pas d'état corrompu durable côté client.
    display = _make_display_with_fake_mouse()
    ctrl = FakeMotionController()
    real_move = display.client.mouse.move
    calls = {"n": 0}

    def _move_once_then_boom(x, y):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulé")
        real_move(x, y)

    display.client.mouse.move = _move_once_then_boom

    display._on_motion(ctrl, 10, 20)  # échoue, avalé
    assert display.client.mouse.moved_to is None

    display._on_motion(ctrl, 30, 40)  # doit réussir normalement
    assert display.client.mouse.moved_to is not None


@requires_gtk4
def test_motion_does_nothing_without_a_client():
    display = _make_display_with_fake_mouse()
    display.client = None
    ctrl = FakeMotionController()

    display._on_motion(ctrl, 10, 20)  # ne doit pas lever
