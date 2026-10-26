"""Transport WebSocket de l'onglet VNC (Gcm4 #104, tâche 3).

asyncvnc2 accepte ``websocket=True`` / ``ws_path=`` sur ``connect()``.
Côté plugin : deux champs de ``VncConnectionInfo``, une décision pure
(``VncDisplay._resolve_transport_kwargs``) et la transmission à
``asyncvnc2.connect()`` dans ``_connect_and_run``. La démo autonome
accepte ``--websocket[=CHEMIN]`` (``parse_demo_args``).
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4

if GTK4_AVAILABLE:
    import vnc_tab
    from vnc_tab import VncConnectionInfo, VncDisplay, VncStatus, parse_demo_args


@requires_gtk4
def test_defaults_keep_raw_tcp():
    info = VncConnectionInfo(name="n", host="h")
    assert info.websocket is False and info.ws_path == "/"
    assert VncDisplay._resolve_transport_kwargs(info.websocket, info.ws_path) == {}


@requires_gtk4
@pytest.mark.parametrize(
    ("ws_path", "attendu"),
    [("/", "/"), ("", "/"), (None, "/"), ("  ", "/"), ("websockify", "/websockify"), (" /ws ", "/ws")],
)
def test_path_is_normalised(ws_path, attendu):
    assert VncDisplay._resolve_transport_kwargs(True, ws_path) == {"websocket": True, "ws_path": attendu}


@requires_gtk4
@pytest.mark.parametrize("ws_path", ["/a b", "/x\r\nHost: evil", "/\x00", "/a\tb", "/\x7f"])
def test_header_injection_is_refused(ws_path):
    with pytest.raises(ValueError):
        VncDisplay._resolve_transport_kwargs(True, ws_path)


@requires_gtk4
def test_path_ignored_without_websocket():
    assert VncDisplay._resolve_transport_kwargs(False, "/a b") == {}


class _Stop(Exception):
    """Interrompt la connexion simulée juste après l'appel à connect()."""


def _run_connect(monkeypatch, **info_kwargs):
    appels = []

    def faux_connect(host, port, **kwargs):
        appels.append((host, port, kwargs))
        raise ConnectionRefusedError("simulé")

    monkeypatch.setattr(vnc_tab.asyncvnc2, "connect", faux_connect)
    display = make_test_display(100, 100, 100, 100)
    for cle, valeur in info_kwargs.items():
        setattr(display.info, cle, valeur)
    display.client = None
    asyncio.run(display._connect_and_run())
    return display, appels


@requires_gtk4
def test_connect_receives_websocket_kwargs(monkeypatch):
    _display, appels = _run_connect(monkeypatch, websocket=True, ws_path="websockify")
    assert appels and appels[0][2]["websocket"] is True and appels[0][2]["ws_path"] == "/websockify"


@requires_gtk4
def test_connect_without_websocket_has_no_transport_kwargs(monkeypatch):
    _display, appels = _run_connect(monkeypatch)
    assert appels and "websocket" not in appels[0][2] and "ws_path" not in appels[0][2]


@requires_gtk4
def test_invalid_path_stops_before_connect(monkeypatch):
    display, appels = _run_connect(monkeypatch, websocket=True, ws_path="/a b")
    assert appels == []
    assert display.status == VncStatus.ERROR


@requires_gtk4
def test_demo_args():
    assert parse_demo_args([]) is None
    assert parse_demo_args(["--websocket"]) is None
    info = parse_demo_args(["h", "5901", "u", "p"])
    assert (info.host, info.port, info.username, info.password, info.websocket) == ("h", 5901, "u", "p", False)
    info = parse_demo_args(["--websocket", "proxy", "6080"])
    assert (info.host, info.port, info.websocket, info.ws_path) == ("proxy", 6080, True, "/")
    info = parse_demo_args(["proxy", "--websocket=/websockify"])
    assert (info.websocket, info.ws_path) == (True, "/websockify")
