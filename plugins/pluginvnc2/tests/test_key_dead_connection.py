# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncDisplay._on_key_pressed face à une connexion déjà morte.

Bug corrigé le 2026-09-13 : dernier chemin haut-niveau d'entrée à recevoir ce
garde, après les quatre chemins souris (`_on_motion`, `_on_button_pressed`,
`_on_button_released`, `_on_scroll`, corrigés le 2026-09-08 au 2026-09-12).
`_on_key_pressed` n'avait qu'un `except KeyError` -- couvrant le cas normal
d'une touche sans équivalent connu côté `asyncvnc2` (`keyboard.hold()` lève
`KeyError` sur un keysym inconnu) -- mais aucun garde contre une AUTRE
exception que `hold_cm.__enter__()` peut lever en écrivant sur un socket
déjà mort. `self.client` n'est jamais remis à `None` après une erreur de
lecture (`_read_loop` se contente de `_set_status(ERROR, ...)`), donc le
garde `if not self.client:` en tête de la méthode ne protège pas ce cas,
exactement comme pour les quatre chemins souris déjà corrigés.

Ces tests appellent la VRAIE méthode de `VncDisplay` (pas une
réimplémentation de la logique) via `conftest.make_test_display`, en
substituant `client.keyboard` par un faux clavier qui trace les holds créés
ou lève sur demande -- même approche que `tests/test_mouse_button_holds.py`
et `tests/test_motion_dead_connection.py`.
"""

from __future__ import annotations

from conftest import make_test_display, requires_gtk4


class FakeHold:
    """Faux gestionnaire de contexte renvoyé par keyboard.hold(name) --
    trace juste s'il a été entré, pour vérifier qu'un hold "perdu" (créé
    puis jamais suivi dans _active_key_holds) est détectable dans les
    tests."""

    def __init__(self, name):
        self.name = name
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc_info):
        return False


class FakeKeyboard:
    def __init__(self):
        self.held = []

    def hold(self, name):
        hold_cm = FakeHold(name)
        self.held.append(hold_cm)
        return hold_cm


class FakeKeyController:
    """Remplace Gtk.EventControllerKey : _on_key_pressed n'utilise que les
    arguments (ctrl, keyval, keycode, state) qui lui sont passés
    directement par GTK."""


def _make_display_with_fake_keyboard():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display.client.keyboard = FakeKeyboard()
    return display


@requires_gtk4
def test_key_pressed_holds_normally():
    display = _make_display_with_fake_keyboard()
    ctrl = FakeKeyController()

    # keyval=0 : Gdk.keyval_name(0) renvoie None, donc _resolve_vnc_key_name
    # retombe sur Gdk.keyval_to_unicode -- utiliser une touche imprimable
    # réelle serait plus représentatif, mais ce test ne vérifie que le
    # chemin heureux du hold, pas la résolution du nom (déjà couverte par
    # tests/test_key_and_mouse_mapping.py).
    from gi.repository import Gdk

    display._on_key_pressed(ctrl, Gdk.KEY_a, 38, 0)

    assert len(display.client.keyboard.held) == 1
    assert display.client.keyboard.held[0].entered
    assert 38 in display._active_key_holds


@requires_gtk4
def test_key_pressed_swallows_keyerror_for_unknown_keysym():
    display = _make_display_with_fake_keyboard()
    ctrl = FakeKeyController()

    def _unknown(_name):
        raise KeyError("keysym inconnu (simulé)")

    display.client.keyboard.hold = _unknown

    from gi.repository import Gdk

    display._on_key_pressed(ctrl, Gdk.KEY_a, 38, 0)  # ne doit pas lever
    assert 38 not in display._active_key_holds


@requires_gtk4
def test_key_pressed_swallows_exception_from_dead_connection_hold():
    display = _make_display_with_fake_keyboard()
    ctrl = FakeKeyController()

    def _boom(_name):
        raise OSError("connexion déjà fermée (simulé)")

    display.client.keyboard.hold = _boom

    from gi.repository import Gdk

    display._on_key_pressed(ctrl, Gdk.KEY_a, 38, 0)  # ne doit pas lever
    assert 38 not in display._active_key_holds


@requires_gtk4
def test_key_pressed_swallows_exception_from_dead_connection_enter():
    # Cas distinct du précédent : hold() réussit (renvoie un context
    # manager), mais c'est __enter__() -- qui écrit réellement sur le
    # socket -- qui lève.
    display = _make_display_with_fake_keyboard()
    ctrl = FakeKeyController()

    class _BoomingHold:
        def __enter__(self):
            raise OSError("connexion déjà fermée (simulé)")

        def __exit__(self, *exc_info):
            return False

    display.client.keyboard.hold = lambda _name: _BoomingHold()

    from gi.repository import Gdk

    display._on_key_pressed(ctrl, Gdk.KEY_a, 38, 0)  # ne doit pas lever
    assert 38 not in display._active_key_holds


@requires_gtk4
def test_key_pressed_works_normally_again_after_a_failed_hold():
    # Contre-épreuve fonctionnelle : après un appui raté (exception avalée),
    # un nouvel appui sur une AUTRE touche doit se comporter normalement --
    # pas d'état corrompu durable côté client.
    display = _make_display_with_fake_keyboard()
    ctrl = FakeKeyController()
    real_hold = display.client.keyboard.hold
    calls = {"n": 0}

    def _hold_once_then_boom(name):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulé")
        return real_hold(name)

    display.client.keyboard.hold = _hold_once_then_boom

    from gi.repository import Gdk

    display._on_key_pressed(ctrl, Gdk.KEY_a, 38, 0)  # échoue, avalé
    assert 38 not in display._active_key_holds

    display._on_key_pressed(ctrl, Gdk.KEY_b, 56, 0)  # doit réussir normalement
    assert 56 in display._active_key_holds


@requires_gtk4
def test_key_pressed_does_nothing_without_a_client():
    display = _make_display_with_fake_keyboard()
    display.client = None
    ctrl = FakeKeyController()

    from gi.repository import Gdk

    display._on_key_pressed(ctrl, Gdk.KEY_a, 38, 0)  # ne doit pas lever
