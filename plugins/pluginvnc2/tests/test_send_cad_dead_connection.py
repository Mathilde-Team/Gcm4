# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncDisplay._send_cad face à une connexion déjà morte.

Bug identifié en session 14 (audit systématique de tous les accès à
`self.client` dans `vnc_tab.py`, cf. docs/sessions/session-14.md) et corrigé
en session 15 (cf. docs/sessions/session-15.md) : septième point de la
famille « écriture sur socket déjà mort », mais premier des deux points
FIRE-AND-FORGET (`asyncio.ensure_future`) plutôt que callback GTK/GLib
synchrone. `client.keyboard.hold("Control_L", "Alt_L")` / `.press("Delete")`
n'avaient AUCUN garde -- ni `except` dédié, ni le garde générique
`if not self.client:` (qui de toute façon ne protège pas ce cas précis,
cf. docs/pieges.md : `self.client` n'est jamais remis à `None` après une
erreur de lecture). Sans garde, une exception ici devenait un « Task
exception was never retrieved » silencieux (avalé par le handler par
défaut d'asyncio, PAS une remontée hors d'un callback GTK) -- différence
importante avec les six points précédents, mais un trou réel : le bouton
"Ctrl+Alt+Suppr" de la barre d'outils reste cliquable dans tous les états
(CONNECTING/ERROR/DISCONNECTED compris), contrairement à `btn_continuous`
pour `set_continuous_updates`.

Ces tests appellent directement la coroutine `_send_cad()` (pas
`send_ctrl_alt_del()`, qui ne fait que planifier via
`asyncio.ensure_future()` -- appeler la coroutine elle-même via
`asyncio.run()` teste la même logique sans dépendre d'une boucle
d'événements GLib) sur un `VncDisplay` construit par `conftest.make_test_display`,
en substituant `client.keyboard` par un faux clavier -- même approche que
`tests/test_key_dead_connection.py`.
"""

from __future__ import annotations

import asyncio

from conftest import make_test_display, requires_gtk4


class FakeHold:
    """Faux gestionnaire de contexte renvoyé par keyboard.hold(*names) --
    trace juste les noms maintenus et s'il a été entré."""

    def __init__(self, names):
        self.names = names
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc_info):
        return False


class FakeKeyboard:
    def __init__(self):
        self.held = []
        self.pressed = []

    def hold(self, *names):
        hold_cm = FakeHold(names)
        self.held.append(hold_cm)
        return hold_cm

    def press(self, name):
        self.pressed.append(name)


def _make_display_with_fake_keyboard():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display.client.keyboard = FakeKeyboard()
    return display


@requires_gtk4
def test_send_cad_holds_and_presses_normally():
    display = _make_display_with_fake_keyboard()

    asyncio.run(display._send_cad())

    assert display.client.keyboard.held[0].names == ("Control_L", "Alt_L")
    assert display.client.keyboard.held[0].entered
    assert display.client.keyboard.pressed == ["Delete"]


@requires_gtk4
def test_send_cad_swallows_exception_from_dead_connection_hold():
    display = _make_display_with_fake_keyboard()

    def _boom(*_names):
        raise OSError("connexion déjà fermée (simulé)")

    display.client.keyboard.hold = _boom

    asyncio.run(display._send_cad())  # ne doit pas lever

    assert display.client.keyboard.pressed == []


@requires_gtk4
def test_send_cad_swallows_exception_from_dead_connection_press():
    # Cas distinct du précédent : hold() réussit (le context manager
    # s'ouvre normalement), mais c'est press("Delete") -- qui écrit
    # réellement sur le socket -- qui lève.
    display = _make_display_with_fake_keyboard()

    def _boom(_name):
        raise OSError("connexion déjà fermée (simulé)")

    display.client.keyboard.press = _boom

    asyncio.run(display._send_cad())  # ne doit pas lever

    assert display.client.keyboard.held[0].entered


@requires_gtk4
def test_send_cad_works_normally_again_after_a_failed_attempt():
    # Contre-épreuve fonctionnelle : après un Ctrl+Alt+Suppr raté
    # (exception avalée), un nouvel appel doit se comporter normalement --
    # pas d'état corrompu durable côté client.
    display = _make_display_with_fake_keyboard()
    calls = {"n": 0}
    real_press = display.client.keyboard.press

    def _fail_once_then_press(name):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulé")
        real_press(name)

    display.client.keyboard.press = _fail_once_then_press

    asyncio.run(display._send_cad())  # échoue, avalé
    assert display.client.keyboard.pressed == []

    asyncio.run(display._send_cad())  # doit réussir normalement
    assert display.client.keyboard.pressed == ["Delete"]


@requires_gtk4
def test_send_ctrl_alt_del_does_nothing_without_a_client():
    display = _make_display_with_fake_keyboard()
    display.client = None

    display.send_ctrl_alt_del()  # ne doit pas lever, ne planifie aucune tâche
