# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncConnectionInfo (dataclass de profil de connexion) et
VncStatus."""

from __future__ import annotations

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncConnectionInfo, VncStatus


@requires_gtk4
def test_minimal_construction_only_needs_name_and_host():
    info = VncConnectionInfo(name="Serveur test", host="192.0.2.1")

    assert info.name == "Serveur test"
    assert info.host == "192.0.2.1"


@requires_gtk4
def test_default_port_is_5900():
    info = VncConnectionInfo(name="x", host="192.0.2.1")

    assert info.port == 5900


@requires_gtk4
def test_defaults_match_documented_safe_choices():
    info = VncConnectionInfo(name="x", host="192.0.2.1")

    # cf. les docstrings des champs dans vnc_tab.py : ces valeurs par
    # défaut sont un choix delibéré (pas juste "ce que dataclass a mis").
    assert info.username is None
    assert info.password is None
    assert info.jpeg_quality is None
    assert info.compression_level is None
    assert info.allow_indexed_colour is False
    assert info.shared is True
    assert info.encodings is None
    assert info.pin_host_key is True
    assert info.sync_clipboard is True


@requires_gtk4
def test_all_fields_are_overridable():
    info = VncConnectionInfo(
        name="Prod ARD",
        host="192.0.2.1",
        port=5901,
        username="admin",
        password="s3cret",
        jpeg_quality=6,
        compression_level=9,
        allow_indexed_colour=True,
        shared=False,
        encodings=[1, 2],
        pin_host_key=False,
        sync_clipboard=False,
    )

    assert info.port == 5901
    assert info.username == "admin"
    assert info.jpeg_quality == 6
    assert info.compression_level == 9
    assert info.allow_indexed_colour is True
    assert info.shared is False
    assert info.encodings == [1, 2]
    assert info.pin_host_key is False
    assert info.sync_clipboard is False


@requires_gtk4
def test_vnc_status_values_are_stable_strings():
    # Ces valeurs sont émises sur le signal "tab-status-changed" -- un
    # code appelant pourrait comparer sur la chaîne, donc elles font
    # partie de l'API publique implicite, pas un détail interne.
    assert VncStatus.DISCONNECTED.value == "disconnected"
    assert VncStatus.CONNECTING.value == "connecting"
    assert VncStatus.CONNECTED.value == "connected"
    assert VncStatus.ERROR.value == "error"
