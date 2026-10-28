# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncDisplay._send_enable_continuous_updates face à une
connexion déjà morte.

Bug identifié en session 14 (audit systématique de tous les accès à
`self.client` dans `vnc_tab.py`, cf. docs/sessions/session-14.md) et corrigé
en session 15 (cf. docs/sessions/session-15.md) : huitième et dernier point
de la famille « écriture sur socket déjà mort », même famille
fire-and-forget que `_send_cad` (`tests/test_send_cad_dead_connection.py`),
mais fenêtre plus étroite en pratique : `btn_continuous` est déjà désactivé
hors de l'état CONNECTED, contrairement au bouton Ctrl+Alt+Suppr. La
coroutine renvoyée par `client.send_enable_continuous_updates(...)` était
jusqu'ici passée DIRECTEMENT à `asyncio.ensure_future()`, sans aucun
wrapper à envelopper dans un `try/except` -- `_send_enable_continuous_updates`
ajoute ce point d'ancrage.

Ces tests appellent directement la coroutine `_send_enable_continuous_updates()`
(pas `set_continuous_updates()`, qui ne fait que planifier via
`asyncio.ensure_future()` -- appeler la coroutine elle-même via
`asyncio.run()` teste la même logique sans dépendre d'une boucle
d'événements GLib), en substituant `client.send_enable_continuous_updates`
par un faux -- même approche que les autres tests de cette famille.
"""

from __future__ import annotations

import asyncio

from conftest import make_test_display, requires_gtk4


class FakeClientContinuousUpdates:
    """Trace les appels réussis à send_enable_continuous_updates."""

    def __init__(self):
        self.calls = []

    async def send_enable_continuous_updates(self, enable, x, y, w, h):
        self.calls.append((enable, x, y, w, h))


def _make_display_with_fake_client():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    fake = FakeClientContinuousUpdates()
    # video.width/height doivent rester lisibles (cf. make_test_display) :
    # on ne remplace que la méthode dont ce test a besoin, pas tout `client`.
    display.client.send_enable_continuous_updates = fake.send_enable_continuous_updates
    display.client.video.width = 1024
    display.client.video.height = 768
    return display, fake


@requires_gtk4
def test_send_enable_continuous_updates_succeeds_normally():
    display, fake = _make_display_with_fake_client()

    asyncio.run(display._send_enable_continuous_updates(True))

    assert fake.calls == [(True, 0, 0, 1024, 768)]


@requires_gtk4
def test_send_enable_continuous_updates_swallows_exception_from_dead_connection():
    display, _fake = _make_display_with_fake_client()

    async def _boom(*_args):
        raise OSError("connexion déjà fermée (simulé)")

    display.client.send_enable_continuous_updates = _boom

    asyncio.run(display._send_enable_continuous_updates(True))  # ne doit pas lever


@requires_gtk4
def test_send_enable_continuous_updates_works_normally_again_after_a_failed_attempt():
    # Contre-épreuve fonctionnelle : après un envoi raté (exception avalée),
    # un nouvel appel doit se comporter normalement -- pas d'état corrompu
    # durable côté client.
    display, fake = _make_display_with_fake_client()
    calls = {"n": 0}
    real_send = fake.send_enable_continuous_updates

    async def _fail_once_then_send(enable, x, y, w, h):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulé")
        await real_send(enable, x, y, w, h)

    display.client.send_enable_continuous_updates = _fail_once_then_send

    asyncio.run(display._send_enable_continuous_updates(True))  # échoue, avalé
    assert fake.calls == []

    asyncio.run(display._send_enable_continuous_updates(False))  # doit réussir normalement
    assert fake.calls == [(False, 0, 0, 1024, 768)]


@requires_gtk4
def test_set_continuous_updates_does_nothing_without_a_client():
    display, _fake = _make_display_with_fake_client()
    display.client = None

    display.set_continuous_updates(True)  # ne doit pas lever, ne planifie aucune tâche
