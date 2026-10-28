# SPDX-License-Identifier: GPL-3.0-or-later
"""Presse-papiers étendu (Extended Clipboard) vers le serveur (Gcm4 #104, tâche 1).

Quand le serveur a confirmé Extended Clipboard
(``client.clipboard_ext_supported``), le texte copié localement part par
``send_clipboard_update()`` (Notify puis Provide, UTF-8) au lieu du
ClientCutText historique limité au Latin-1. Sans ce support, le chemin
historique reste inchangé (cf. ``tests/test_clipboard_dead_connection.py``),
et un texte hors Latin-1 n'est plus perdu en silence : il est journalisé.
"""

from __future__ import annotations

import asyncio

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4

if GTK4_AVAILABLE:
    import vnc_tab
    from vnc_tab import VncDisplay


class FakeServerClipboard:
    def __init__(self):
        self.written = []

    def write(self, text):
        text.encode("latin-1")  # même contrainte que asyncvnc2.Clipboard.write
        self.written.append(text)


class FakeSystemClipboard:
    def __init__(self, text):
        self.text = text

    def read_text_finish(self, _result):
        return self.text


def _display(ext_supported, fail=False):
    display = make_test_display(remote_w=100, remote_h=100, alloc_w=100, alloc_h=100)
    display.client.clipboard = FakeServerClipboard()
    display.client.clipboard_ext_supported = ext_supported
    envoyes = []

    async def send_clipboard_update(text):
        if fail:
            raise PermissionError("support retiré (simulé)")
        envoyes.append(text)

    display.client.send_clipboard_update = send_clipboard_update
    return display, envoyes


def _coller(display, text):
    async def scenario():
        display._on_local_clipboard_read(FakeSystemClipboard(text), None)
        await asyncio.sleep(0)

    asyncio.run(scenario())


@requires_gtk4
def test_mode_decision():
    assert VncDisplay._resolve_clipboard_send_mode(True, "€ ✓") == "extended"
    assert VncDisplay._resolve_clipboard_send_mode(True, "abc") == "extended"
    assert VncDisplay._resolve_clipboard_send_mode(False, "café") == "legacy"
    assert VncDisplay._resolve_clipboard_send_mode(False, "€") == "unsupported"


@requires_gtk4
def test_extended_server_receives_utf8_via_update():
    display, envoyes = _display(ext_supported=True)
    _coller(display, "prix : 12 € ✓")
    assert envoyes == ["prix : 12 € ✓"]
    assert display.client.clipboard.written == []


@requires_gtk4
def test_legacy_server_keeps_client_cut_text():
    display, envoyes = _display(ext_supported=False)
    _coller(display, "café")
    assert display.client.clipboard.written == ["café"] and envoyes == []


@requires_gtk4
def test_client_without_the_attribute_is_legacy():
    display, _envoyes = _display(ext_supported=False)
    del display.client.clipboard_ext_supported
    _coller(display, "abc")
    assert display.client.clipboard.written == ["abc"]


@requires_gtk4
def test_non_latin1_without_extension_is_logged(monkeypatch):
    display, envoyes = _display(ext_supported=False)
    avertissements = []
    monkeypatch.setattr(vnc_tab.logger, "warning", lambda msg, **kw: avertissements.append(msg.format(**kw)))
    _coller(display, "12 €")
    assert envoyes == [] and display.client.clipboard.written == []
    assert any("hors Latin-1" in m for m in avertissements)
    assert display._last_clipboard_text == "12 €"  # pas de nouvelle tentative pour le même texte


@requires_gtk4
def test_extended_failure_is_logged_not_raised(monkeypatch):
    display, _envoyes = _display(ext_supported=True, fail=True)
    avertissements = []
    monkeypatch.setattr(vnc_tab.logger, "warning", lambda msg, **kw: avertissements.append(msg.format(**kw)))
    _coller(display, "texte")
    assert any("_send_clipboard_extended | échec" in m for m in avertissements)


@requires_gtk4
def test_unchanged_text_is_not_resent():
    display, envoyes = _display(ext_supported=True)
    _coller(display, "même")
    _coller(display, "même")
    assert envoyes == ["même"]
