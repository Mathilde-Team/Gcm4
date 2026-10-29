# SPDX-License-Identifier: GPL-3.0-or-later
"""Reconnexion inversée (``asyncvnc2.listen``) de l'onglet VNC (Gcm4 #104, tâche 4).

``VncConnectionInfo.reverse`` fait écouter l'onglet sur ``host:port`` au
lieu de composer vers lui. ``VncDisplay._resolve_connection_mode`` choisit
la fonction d'asyncvnc2, le texte d'attente et coupe le pinning de clé
hôte (l'adresse d'écoute n'identifie pas le serveur qui se présente).
"""

from __future__ import annotations

import asyncio

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4, requires_real_asyncvnc2

if GTK4_AVAILABLE:
    import vnc_tab
    from vnc_tab import VncConnectionInfo, VncDisplay, VncStatus


@requires_gtk4
def test_default_is_outgoing():
    assert VncConnectionInfo(name="n", host="h").reverse is False
    mode = VncDisplay._resolve_connection_mode(False, "srv", 5900, True)
    assert mode == {"opener": "connect", "status": "Connexion à srv…", "pin": True}
    assert VncDisplay._resolve_connection_mode(False, "srv", 5900, False)["pin"] is False


@requires_gtk4
def test_reverse_listens_without_pinning():
    mode = VncDisplay._resolve_connection_mode(True, "0.0.0.0", 5500, True)
    assert mode["opener"] == "listen" and mode["pin"] is False
    assert mode["status"] == "En attente d'une connexion entrante sur 0.0.0.0:5500…"


class FakeHostKeyStore:
    def __init__(self):
        self.lus = []

    def get(self, host, port):
        self.lus.append((host, port))
        return "clé épinglée"

    def set(self, *args):
        raise AssertionError("aucune clé ne doit être enregistrée")


def _run(monkeypatch, reverse):
    appels = []

    def faux(nom):
        def _ouvrir(host, port, **kwargs):
            appels.append((nom, host, port, kwargs))
            raise ConnectionRefusedError("simulé")

        return _ouvrir

    monkeypatch.setattr(vnc_tab.asyncvnc2, "connect", faux("connect"))
    monkeypatch.setattr(vnc_tab.asyncvnc2, "listen", faux("listen"))
    display = make_test_display(100, 100, 100, 100)
    display.info.reverse = reverse
    display.info.host = "127.0.0.1"
    display.info.port = 5500
    display.host_key_store = FakeHostKeyStore()
    statuts = []
    monkeypatch.setattr(display, "_set_status", lambda s, m="": statuts.append((s, m)))
    asyncio.run(display._connect_and_run())
    return display, appels, statuts


@requires_gtk4
def test_reverse_calls_listen_without_host_key(monkeypatch):
    display, appels, statuts = _run(monkeypatch, reverse=True)
    assert [(a[0], a[1], a[2]) for a in appels] == [("listen", "127.0.0.1", 5500)]
    assert appels[0][3]["host_key"] is None and appels[0][3]["shared"] is True
    assert display.host_key_store.lus == []
    assert statuts[0] == (VncStatus.CONNECTING, "En attente d'une connexion entrante sur 127.0.0.1:5500…")
    assert statuts[-1][0] == VncStatus.ERROR


@requires_gtk4
def test_outgoing_still_calls_connect_with_pinning(monkeypatch):
    display, appels, statuts = _run(monkeypatch, reverse=False)
    assert [a[0] for a in appels] == ["connect"]
    assert appels[0][3]["host_key"] == "clé épinglée"
    assert display.host_key_store.lus == [("127.0.0.1", 5500)]
    assert statuts[0] == (VncStatus.CONNECTING, "Connexion à 127.0.0.1…")


@requires_gtk4
@requires_real_asyncvnc2
def test_real_listen_accepts_an_incoming_server():
    """Bout en bout avec le vrai ``asyncvnc2.listen`` : un faux serveur VNC
    (RFB 3.8, sécurité None) se connecte à l'adresse d'écoute, le ``Client``
    obtenu lit le nom du bureau."""
    import socket

    import asyncvnc2

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    async def serveur_distant():
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.02)
        writer.write(b"RFB 003.008\n")
        await reader.readexactly(12)
        writer.write(b"\x01\x01")  # un type de sécurité : None
        await reader.readexactly(1)
        writer.write(b"\x00\x00\x00\x00")  # SecurityResult OK
        await reader.readexactly(1)  # ClientInit
        nom = b"bureau-inverse"
        pixel = bytes([32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0])
        writer.write((64).to_bytes(2, "big") + (48).to_bytes(2, "big") + pixel + len(nom).to_bytes(4, "big") + nom)
        await writer.drain()
        await asyncio.sleep(0.2)
        writer.close()

    async def scenario():
        tache = asyncio.ensure_future(serveur_distant())
        mode = VncDisplay._resolve_connection_mode(True, "127.0.0.1", port, True)
        async with getattr(asyncvnc2, mode["opener"])("127.0.0.1", port) as client:
            nom = client.video.name
        await tache
        return nom

    assert asyncio.run(asyncio.wait_for(scenario(), 10)) == "bureau-inverse"
