# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour la reconnexion automatique après une coupure inattendue
(VncStatus.ERROR alors que la connexion avait déjà été établie au moins
une fois) -- PAS après un stop()/close() volontaire (VncStatus.DISCONNECTED)
ni après un échec de connexion initiale (auth refusée, hôte injoignable
avant tout premier CONNECTED), cf. docstring de
VncDisplay._should_schedule_auto_reconnect dans vnc_tab.py.

Fonctionnalité ajoutée à la demande explicite de l'utilisateur (session
17), en complément des raccourcis clavier globaux (tests/test_global_shortcuts.py) --
toutes deux repérées via Context7 (doc PyGObject/GTK4 à jour) puis
confirmées par l'utilisateur plutôt que supposées "manquantes" d'office,
cf. docs/sessions/session-17.md.

Session 23 a complété le message de statut affiché pendant l'attente
(`_append_reconnect_delay_to_message`) : jusque-là l'interface montrait
le message d'erreur brut sans indiquer qu'une reconnexion automatique
était planifiée ni dans combien de temps -- cette info n'existait que
dans les logs. Repartie d'un besoin utilisateur réel, pas d'un nouveau
point de l'audit `self.client`, cf. docs/sessions/session-23.md.
"""

from __future__ import annotations

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncDisplay, VncStatus


def _display():
    return make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)


# ---- VncDisplay._should_schedule_auto_reconnect() ----


@requires_gtk4
def test_schedules_on_error_after_a_previous_connection():
    assert VncDisplay._should_schedule_auto_reconnect(VncStatus.ERROR.value, True) is True


@requires_gtk4
def test_does_not_schedule_on_initial_connection_failure():
    # Jamais connecté auparavant : auth refusée / hôte injoignable dès le
    # départ ne doit PAS déclencher de boucle de reconnexion automatique
    # (même mot de passe, même adresse -- ça ne se réparera pas tout seul,
    # et ça pourrait verrouiller un compte Apple ARD).
    assert VncDisplay._should_schedule_auto_reconnect(VncStatus.ERROR.value, False) is False


@requires_gtk4
def test_does_not_schedule_on_voluntary_disconnect():
    # stop()/close() posent DISCONNECTED, jamais ERROR -- même après une
    # session pleinement établie, une déconnexion volontaire ne doit
    # jamais se reprogrammer elle-même.
    assert VncDisplay._should_schedule_auto_reconnect(VncStatus.DISCONNECTED.value, True) is False


@requires_gtk4
def test_does_not_schedule_on_connecting_or_connected():
    assert VncDisplay._should_schedule_auto_reconnect(VncStatus.CONNECTING.value, True) is False
    assert VncDisplay._should_schedule_auto_reconnect(VncStatus.CONNECTED.value, True) is False


# ---- VncDisplay._resolve_reconnect_delay_seconds() ----


@requires_gtk4
def test_reconnect_delay_grows_exponentially_then_caps_at_30s():
    assert VncDisplay._resolve_reconnect_delay_seconds(0) == 2
    assert VncDisplay._resolve_reconnect_delay_seconds(1) == 4
    assert VncDisplay._resolve_reconnect_delay_seconds(2) == 8
    assert VncDisplay._resolve_reconnect_delay_seconds(3) == 16
    assert VncDisplay._resolve_reconnect_delay_seconds(4) == 30  # 2*2**4 = 32, plafonné
    assert VncDisplay._resolve_reconnect_delay_seconds(5) == 30
    assert VncDisplay._resolve_reconnect_delay_seconds(50) == 30  # ne remonte jamais au-delà du plafond


# ---- VncDisplay._append_reconnect_delay_to_message() ----


@requires_gtk4
def test_append_reconnect_delay_appends_to_a_non_empty_message():
    result = VncDisplay._append_reconnect_delay_to_message("Connexion interrompue : boom", 8)
    assert result == "Connexion interrompue : boom — nouvelle tentative automatique dans 8s"


@requires_gtk4
def test_append_reconnect_delay_returns_just_the_suffix_for_an_empty_message():
    # N'arrive pas en pratique (tous les appels _set_status(ERROR, ...) du
    # fichier passent un message non vide) mais une fonction pure doit se
    # comporter correctement pour toute entrée -- pas de tiret orphelin
    # devant un message vide.
    result = VncDisplay._append_reconnect_delay_to_message("", 30)
    assert result == "nouvelle tentative automatique dans 30s"


# ---- VncDisplay._set_status() : câblage bout en bout ----


@requires_gtk4
def test_set_status_connected_marks_had_connected_before_and_resets_attempt_counter():
    display = _display()
    display._reconnect_attempt = 3  # simule une série de tentatives échouées

    display._set_status(VncStatus.CONNECTED, "")

    assert display._had_connected_before is True
    assert display._reconnect_attempt == 0


@requires_gtk4
def test_set_status_error_schedules_reconnect_only_after_a_prior_connection():
    display = _display()
    calls = []
    display._schedule_auto_reconnect = lambda: calls.append(1)

    display._set_status(VncStatus.ERROR, "connexion interrompue")  # jamais connecté avant

    assert calls == []


@requires_gtk4
def test_set_status_error_schedules_reconnect_after_a_prior_connection():
    display = _display()
    calls = []
    display._had_connected_before = True
    display._schedule_auto_reconnect = lambda: calls.append(1)

    display._set_status(VncStatus.ERROR, "connexion interrompue")

    assert calls == [1]


@requires_gtk4
def test_set_status_disconnected_never_schedules_a_reconnect():
    display = _display()
    calls = []
    display._had_connected_before = True
    display._schedule_auto_reconnect = lambda: calls.append(1)

    display._set_status(VncStatus.DISCONNECTED, "Déconnecté")

    assert calls == []


@requires_gtk4
def test_set_status_error_emits_the_message_with_the_delay_appended_when_reconnect_is_scheduled():
    # Câblage complet ajouté en session 23 : jusqu'ici l'interface
    # affichait le message d'erreur brut, sans indiquer qu'une
    # reconnexion automatique était planifiée -- cf.
    # docs/sessions/session-23.md.
    display = _display()
    display._had_connected_before = True
    display._schedule_auto_reconnect = lambda: None  # pas testé ici, cf. section dédiée plus bas
    emitted = {}
    display.emit = lambda signal_name, *args: emitted.setdefault(signal_name, args)

    display._set_status(VncStatus.ERROR, "Connexion interrompue : boom")

    assert emitted["vnc-status-changed"] == (
        "error",
        "Connexion interrompue : boom — nouvelle tentative automatique dans 2s",
    )


@requires_gtk4
def test_set_status_error_emits_the_plain_message_when_no_reconnect_is_scheduled():
    # Contraste avec le test ci-dessus : sans connexion préalable établie
    # (_should_schedule_auto_reconnect renvoie False, cf. plus haut), le
    # message ne doit PAS être augmenté -- rien n'est réellement planifié.
    display = _display()  # _had_connected_before reste False
    emitted = {}
    display.emit = lambda signal_name, *args: emitted.setdefault(signal_name, args)

    display._set_status(VncStatus.ERROR, "Authentification refusée")

    assert emitted["vnc-status-changed"] == ("error", "Authentification refusée")


# ---- VncDisplay._schedule_auto_reconnect() / _on_auto_reconnect_timeout() ----


@requires_gtk4
def test_schedule_auto_reconnect_uses_the_resolved_delay_and_increments_attempt(monkeypatch):
    display = _display()
    captured = {}

    def fake_timeout_add_seconds(delay, callback):
        captured["delay"] = delay
        captured["callback"] = callback
        return "fake-source-id"

    import vnc_tab

    monkeypatch.setattr(vnc_tab.GLib, "timeout_add_seconds", fake_timeout_add_seconds)

    display._schedule_auto_reconnect()

    assert captured["delay"] == 2  # _resolve_reconnect_delay_seconds(0)
    assert captured["callback"] == display._on_auto_reconnect_timeout
    assert display._reconnect_attempt == 1
    assert display._reconnect_timeout_id == "fake-source-id"


@requires_gtk4
def test_auto_reconnect_timeout_calls_reconnect_and_clears_the_timeout_id():
    display = _display()
    display._reconnect_timeout_id = "fake-source-id"
    calls = []
    display.reconnect = lambda: calls.append(1)

    result = display._on_auto_reconnect_timeout()

    assert calls == [1]
    assert display._reconnect_timeout_id is None
    assert result is False  # source à usage unique, ne pas répéter automatiquement


# ---- VncDisplay._cancel_pending_auto_reconnect() / stop() ----


@requires_gtk4
def test_cancel_pending_auto_reconnect_clears_a_pending_timeout(monkeypatch):
    display = _display()
    display._reconnect_timeout_id = "fake-source-id"
    removed = []

    import vnc_tab

    monkeypatch.setattr(vnc_tab.GLib, "source_remove", lambda source_id: removed.append(source_id))

    display._cancel_pending_auto_reconnect()

    assert removed == ["fake-source-id"]
    assert display._reconnect_timeout_id is None


@requires_gtk4
def test_cancel_pending_auto_reconnect_is_a_no_op_without_a_pending_timeout(monkeypatch):
    display = _display()
    assert display._reconnect_timeout_id is None
    removed = []

    import vnc_tab

    monkeypatch.setattr(vnc_tab.GLib, "source_remove", lambda source_id: removed.append(source_id))

    display._cancel_pending_auto_reconnect()  # ne doit pas lever, ne doit rien annuler

    assert removed == []


@requires_gtk4
def test_stop_cancels_a_pending_auto_reconnect_timeout(monkeypatch):
    display = _display()
    display._reconnect_timeout_id = "fake-source-id"
    removed = []

    import vnc_tab

    monkeypatch.setattr(vnc_tab.GLib, "source_remove", lambda source_id: removed.append(source_id))

    display.stop()

    assert removed == ["fake-source-id"]
    assert display._reconnect_timeout_id is None
