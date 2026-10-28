# SPDX-License-Identifier: GPL-3.0-or-later
"""Décompte seconde par seconde avant la reconnexion automatique (Gcm4 #94).

Jusqu'ici le message d'erreur affichait le délai une seule fois
(« … nouvelle tentative automatique dans 4s ») puis restait figé. Un
minuteur d'une seconde (``GLib.timeout_add``, distinct du minuteur de
reconnexion ``GLib.timeout_add_seconds``) émet ``vnc-reconnect-countdown``
avec le message mis à jour ; il est annulé avec le minuteur de reconnexion
(stop, close, reconnexion manuelle) et à la tentative elle-même.
"""

from __future__ import annotations

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4, requires_real_gtk

if GTK4_AVAILABLE:
    import vnc_tab
    from vnc_tab import VncDisplay, VncStatus


def _display():
    return make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)


class FakeTimers:
    def __init__(self, monkeypatch):
        self.ajouts = []
        self.retraits = []
        self._suivant = 100
        monkeypatch.setattr(vnc_tab.GLib, "timeout_add", self._ajouter)
        monkeypatch.setattr(vnc_tab.GLib, "timeout_add_seconds", self._ajouter)
        monkeypatch.setattr(vnc_tab.GLib, "source_remove", self.retraits.append)

    def _ajouter(self, intervalle, rappel):
        self._suivant += 1
        self.ajouts.append((intervalle, rappel, self._suivant))
        return self._suivant


def _messages(display):
    """Intercepte emit() : fonctionne avec le vrai GObject comme avec le stub
    (dont les signaux ne sont pas distribués)."""
    recus = []
    emit = display.emit

    def _emit(nom, *args):
        if nom == "vnc-reconnect-countdown":
            recus.append(args[0])
            return None
        return emit(nom, *args)

    display.emit = _emit
    return recus


@requires_gtk4
def test_tick_decision():
    assert VncDisplay._resolve_countdown_tick(4) == (3, True)
    assert VncDisplay._resolve_countdown_tick(2) == (1, True)
    assert VncDisplay._resolve_countdown_tick(1) == (0, False)
    assert VncDisplay._resolve_countdown_tick(0) == (0, False)


@requires_gtk4
def test_error_after_connection_starts_both_timers(monkeypatch):
    minuteurs = FakeTimers(monkeypatch)
    display = _display()
    display._had_connected_before = True
    display._set_status(VncStatus.ERROR, "Connexion interrompue : x")
    intervalles = sorted(i for i, _r, _id in minuteurs.ajouts)
    assert intervalles == [display._resolve_reconnect_delay_seconds(0), 1000]
    assert display._countdown_base_message == "Connexion interrompue : x"


@requires_gtk4
def test_countdown_emits_each_second_then_stops(monkeypatch):
    minuteurs = FakeTimers(monkeypatch)
    display = _display()
    recus = _messages(display)
    display._countdown_base_message = "Connexion interrompue : x"
    display._start_reconnect_countdown(3)
    (_i, tick, _id) = minuteurs.ajouts[-1]
    assert tick() is True and tick() is True and tick() is False
    assert recus == [
        "Connexion interrompue : x — nouvelle tentative automatique dans 2s",
        "Connexion interrompue : x — nouvelle tentative automatique dans 1s",
    ]
    assert display._countdown_timeout_id is None


@requires_gtk4
def test_cancel_pending_reconnect_stops_the_countdown(monkeypatch):
    minuteurs = FakeTimers(monkeypatch)
    display = _display()
    display._had_connected_before = True
    display._set_status(VncStatus.ERROR, "Connexion interrompue : x")
    ids = {i: id_ for i, _r, id_ in minuteurs.ajouts}
    display._cancel_pending_auto_reconnect()
    assert sorted(minuteurs.retraits) == sorted(ids.values())
    assert display._countdown_timeout_id is None and display._reconnect_timeout_id is None


@requires_gtk4
def test_restart_replaces_the_previous_countdown(monkeypatch):
    minuteurs = FakeTimers(monkeypatch)
    display = _display()
    display._start_reconnect_countdown(4)
    premier = minuteurs.ajouts[-1][2]
    display._start_reconnect_countdown(8)
    assert minuteurs.retraits == [premier] and display._countdown_remaining == 8


@requires_gtk4
def test_attempt_stops_the_countdown(monkeypatch):
    minuteurs = FakeTimers(monkeypatch)
    display = _display()
    display._start_reconnect_countdown(2)
    id_decompte = minuteurs.ajouts[-1][2]
    monkeypatch.setattr(display, "reconnect", lambda: None)
    display._on_auto_reconnect_timeout()
    assert minuteurs.retraits == [id_decompte] and display._countdown_timeout_id is None


@requires_gtk4
@requires_real_gtk
def test_real_glib_loop_counts_down():
    """Avec la vraie boucle GLib : délai de 2 s → un message « dans 1s »,
    puis la tentative (reconnect simulé), et plus aucun minuteur actif."""
    from gi.repository import GLib

    display = _display()
    recus = _messages(display)
    boucle = GLib.MainLoop()
    tentatives = []

    def faux_reconnect():
        tentatives.append(True)
        GLib.timeout_add(300, boucle.quit)

    display.reconnect = faux_reconnect
    display._had_connected_before = True
    display._set_status(VncStatus.ERROR, "Connexion interrompue : x")
    garde = GLib.timeout_add(8000, boucle.quit)
    boucle.run()
    GLib.source_remove(garde)
    assert tentatives == [True]
    assert recus == ["Connexion interrompue : x — nouvelle tentative automatique dans 1s"]
    assert display._countdown_timeout_id is None and display._reconnect_timeout_id is None


class FakeLabel:
    def __init__(self):
        self.label = None

    def set_label(self, value):
        self.label = value


@requires_gtk4
def test_tab_updates_its_labels():
    import asyncio

    from vnc_tab import VncConnectionInfo, VncTab

    asyncio.set_event_loop(asyncio.new_event_loop())
    tab = VncTab(VncConnectionInfo(name="srv", host="192.0.2.1"))
    tab.status_label = FakeLabel()
    tab.placeholder_label = FakeLabel()
    tab._on_reconnect_countdown(tab.display, "Connexion interrompue : x — nouvelle tentative automatique dans 3s")
    assert tab.status_label.label == "srv — Connexion interrompue : x — nouvelle tentative automatique dans 3s"
    assert tab.placeholder_label.label == "Connexion interrompue : x — nouvelle tentative automatique dans 3s"
