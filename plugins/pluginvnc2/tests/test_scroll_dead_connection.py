# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncDisplay._on_scroll face à une connexion déjà morte.

Bug corrigé le 2026-09-12 : dernier des trois chemins souris à haute
fréquence (avec `_on_motion`, corrigé le 2026-09-11, et
`_on_button_pressed`/`_on_button_released`, corrigés le 2026-09-08/09) à
recevoir ce garde. `_on_scroll` appelait `self.client.mouse.scroll_down()`/
`scroll_up()` sans aucun `try/except`, alors que `self.client` n'est jamais
remis à `None` après une erreur de lecture (`_read_loop` se contente de
`_set_status(ERROR, ...)`) -- le garde `if not self.client:` en tête de la
méthode ne protège donc pas le cas où la connexion tombe pendant un scroll.
Un `scroll_down()`/`scroll_up()` sur un socket déjà mort pouvait alors lever
hors du gestionnaire de signal GTK.

Ces tests appellent la VRAIE méthode de `VncDisplay` (pas une
réimplémentation de la logique) via `conftest.make_test_display`, en
substituant juste `client.mouse.scroll_down`/`scroll_up` par des fonctions
qui lèvent -- même approche que `tests/test_motion_dead_connection.py`.

Session 24 a ajouté `VncConnectionInfo.invert_scroll` (« défilement
naturel » façon trackpad macOS) et sa résolution pure
`_resolve_effective_scroll_delta()`, testée ci-dessous en plus des
chemins de garde déjà en place.
"""

from __future__ import annotations

from conftest import make_test_display, requires_gtk4


class FakeMouse:
    def __init__(self):
        self.scroll_down_calls = 0
        self.scroll_up_calls = 0

    def scroll_down(self):
        self.scroll_down_calls += 1

    def scroll_up(self):
        self.scroll_up_calls += 1


class FakeScrollController:
    """Remplace Gtk.EventControllerScroll : _on_scroll n'utilise que les
    arguments (ctrl, dx, dy) qui lui sont passés directement par GTK."""


def _make_display_with_fake_mouse():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display.client.mouse = FakeMouse()
    return display


@requires_gtk4
def test_scroll_down_calls_mouse_scroll_down():
    display = _make_display_with_fake_mouse()
    ctrl = FakeScrollController()

    display._on_scroll(ctrl, 0, 1)

    assert display.client.mouse.scroll_down_calls == 1
    assert display.client.mouse.scroll_up_calls == 0


@requires_gtk4
def test_scroll_up_calls_mouse_scroll_up():
    display = _make_display_with_fake_mouse()
    ctrl = FakeScrollController()

    display._on_scroll(ctrl, 0, -1)

    assert display.client.mouse.scroll_up_calls == 1
    assert display.client.mouse.scroll_down_calls == 0


@requires_gtk4
def test_scroll_swallows_exception_from_dead_connection_scroll_down():
    display = _make_display_with_fake_mouse()
    ctrl = FakeScrollController()

    def _boom():
        raise OSError("connexion déjà fermée (simulé)")

    display.client.mouse.scroll_down = _boom

    display._on_scroll(ctrl, 0, 1)  # ne doit pas lever


@requires_gtk4
def test_scroll_swallows_exception_from_dead_connection_scroll_up():
    display = _make_display_with_fake_mouse()
    ctrl = FakeScrollController()

    def _boom():
        raise OSError("connexion déjà fermée (simulé)")

    display.client.mouse.scroll_up = _boom

    display._on_scroll(ctrl, 0, -1)  # ne doit pas lever


@requires_gtk4
def test_scroll_works_normally_again_after_a_failed_scroll():
    # Contre-épreuve fonctionnelle : après un scroll raté (avalé), un
    # nouveau scroll doit se comporter normalement -- pas d'état corrompu
    # durable côté client.
    display = _make_display_with_fake_mouse()
    ctrl = FakeScrollController()
    calls = {"n": 0}

    def _scroll_down_once_then_boom():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulé")
        display.client.mouse.scroll_down_calls += 1

    display.client.mouse.scroll_down = _scroll_down_once_then_boom

    display._on_scroll(ctrl, 0, 1)  # échoue, avalé
    assert display.client.mouse.scroll_down_calls == 0

    display._on_scroll(ctrl, 0, 1)  # doit réussir normalement
    assert display.client.mouse.scroll_down_calls == 1


@requires_gtk4
def test_scroll_does_nothing_without_a_client():
    display = _make_display_with_fake_mouse()
    display.client = None
    ctrl = FakeScrollController()

    display._on_scroll(ctrl, 0, 1)  # ne doit pas lever


# ---- VncConnectionInfo.invert_scroll ----


@requires_gtk4
def test_resolve_effective_scroll_delta_unchanged_when_not_inverted():
    display = _make_display_with_fake_mouse()
    assert display._resolve_effective_scroll_delta(1, invert=False) == 1
    assert display._resolve_effective_scroll_delta(-1, invert=False) == -1


@requires_gtk4
def test_resolve_effective_scroll_delta_flips_sign_when_inverted():
    display = _make_display_with_fake_mouse()
    assert display._resolve_effective_scroll_delta(1, invert=True) == -1
    assert display._resolve_effective_scroll_delta(-1, invert=True) == 1


@requires_gtk4
def test_scroll_calls_scroll_up_instead_of_down_when_inverted():
    # dy=1 signifie normalement "molette vers le bas" (scroll_down) -- avec
    # invert_scroll=True, ce même mouvement physique doit produire l'effet
    # inverse (scroll_up), façon défilement naturel.
    display = _make_display_with_fake_mouse()
    display.info.invert_scroll = True
    ctrl = FakeScrollController()

    display._on_scroll(ctrl, 0, 1)

    assert display.client.mouse.scroll_up_calls == 1
    assert display.client.mouse.scroll_down_calls == 0


@requires_gtk4
def test_scroll_calls_scroll_down_instead_of_up_when_inverted():
    display = _make_display_with_fake_mouse()
    display.info.invert_scroll = True
    ctrl = FakeScrollController()

    display._on_scroll(ctrl, 0, -1)

    assert display.client.mouse.scroll_down_calls == 1
    assert display.client.mouse.scroll_up_calls == 0
