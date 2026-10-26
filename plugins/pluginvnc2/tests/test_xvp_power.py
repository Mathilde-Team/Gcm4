"""Tests du menu « Alimentation » (xvp) de l'onglet VNC (Gcm4 #104, tâche 5).

asyncvnc2 expose ``send_xvp_shutdown()`` / ``send_xvp_reboot()`` /
``send_xvp_reset()`` (coroutines, ``confirm=True`` obligatoire) et
``Client.xvp_supported`` (vrai une fois le message XVP_INIT du serveur
reçu). Côté onglet :

- ``VncTab._resolve_power_menu_state`` décide si les entrées sont actives ;
- ``VncTab._resolve_power_action_texts`` donne les libellés du menu et du
  dialogue de confirmation ;
- ``VncDisplay.send_power_action`` / ``_send_power_action`` envoient la
  commande avec ``confirm=True`` et journalisent un échec au lieu de le
  laisser remonter d'une tâche asyncio orpheline (même famille que
  ``tests/test_send_cad_dead_connection.py``).
"""

from __future__ import annotations

import asyncio
import types

import pytest

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncDisplay, VncTab


class FakeXvpClient:
    """Faux client asyncvnc2 : trace les commandes xvp reçues."""

    def __init__(self, supported=True, fail=False):
        self.xvp_supported = supported
        self.fail = fail
        self.calls = []

    def _make(name):
        async def _send(self, confirm=False):
            if self.fail:
                raise ConnectionResetError("socket mort")
            self.calls.append((name, confirm))

        return _send

    send_xvp_shutdown = _make("shutdown")
    send_xvp_reboot = _make("reboot")
    send_xvp_reset = _make("reset")


@requires_gtk4
def test_menu_inactive_until_connected():
    actif, message = VncTab._resolve_power_menu_state(connected=False, xvp_supported=True)
    assert actif is False and "connexion" in message


@requires_gtk4
def test_menu_inactive_without_xvp():
    actif, message = VncTab._resolve_power_menu_state(connected=True, xvp_supported=False)
    assert actif is False and "xvp" in message


@requires_gtk4
def test_menu_active_with_xvp():
    assert VncTab._resolve_power_menu_state(connected=True, xvp_supported=True) == (True, None)


@requires_gtk4
@pytest.mark.parametrize("action", ["shutdown", "reboot", "reset"])
def test_texts_name_the_host(action):
    textes = VncTab._resolve_power_action_texts(action, "srv.example")
    assert set(textes) == {"menu", "title", "body", "confirm"}
    assert "srv.example" in textes["body"] and textes["menu"].endswith("…")


@requires_gtk4
def test_reset_warns_about_data_loss():
    assert "perdues" in VncTab._resolve_power_action_texts("reset", "h")["body"]


@requires_gtk4
def test_unknown_action_texts():
    with pytest.raises(KeyError):
        VncTab._resolve_power_action_texts("hibernate", "h")


@requires_gtk4
def test_xvp_supported_follows_the_client():
    display = make_test_display(100, 100, 100, 100)
    display.client = FakeXvpClient(supported=True)
    assert display.xvp_supported() is True
    display.client = FakeXvpClient(supported=False)
    assert display.xvp_supported() is False
    display.client = types.SimpleNamespace()  # client sans attribut xvp_supported
    assert display.xvp_supported() is False
    display.client = None
    assert display.xvp_supported() is False


@requires_gtk4
@pytest.mark.parametrize("action", ["shutdown", "reboot", "reset"])
def test_send_power_action_passes_confirm(action):
    display = make_test_display(100, 100, 100, 100)
    client = FakeXvpClient()
    display.client = client
    asyncio.run(display._send_power_action(action))
    assert client.calls == [(action, True)]


@requires_gtk4
def test_send_power_action_schedules_the_coroutine():
    display = make_test_display(100, 100, 100, 100)
    client = FakeXvpClient()
    display.client = client

    async def scenario():
        assert display.send_power_action("reboot") is True
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert client.calls == [("reboot", True)]


@requires_gtk4
def test_send_power_action_without_connection():
    display = make_test_display(100, 100, 100, 100)
    display.client = None
    assert display.send_power_action("shutdown") is False


@requires_gtk4
def test_send_power_action_rejects_unknown_action():
    display = make_test_display(100, 100, 100, 100)
    display.client = FakeXvpClient()
    with pytest.raises(ValueError):
        display.send_power_action("hibernate")
    assert display.POWER_ACTIONS == VncDisplay.POWER_ACTIONS == ("shutdown", "reboot", "reset")


@requires_gtk4
def test_dead_connection_is_logged_not_raised(monkeypatch):
    import vnc_tab

    display = make_test_display(100, 100, 100, 100)
    display.client = FakeXvpClient(fail=True)
    messages = []
    # Pas de puits loguru ajouté ici (règle tests/test_loguru_only.py) :
    # on intercepte l'appel au logger du module.
    monkeypatch.setattr(vnc_tab.logger, "warning", lambda msg, **kw: messages.append(msg.format(**kw)))
    asyncio.run(display._send_power_action("reset"))
    assert any("échec action=reset" in m for m in messages)


class FakeWidget:
    """Remplace un widget pour lire l'état posé, en GTK réel comme sous le
    stub (dont les getters ne renvoient rien d'exploitable)."""

    def __init__(self):
        self.sensitive = None
        self.label = None
        self.visible = None

    def set_sensitive(self, value):
        self.sensitive = value

    def set_label(self, value):
        self.label = value

    def set_visible(self, value):
        self.visible = value


@requires_gtk4
def test_toolbar_menu_follows_connection_and_xvp():
    """Bout en bout côté onglet : le menu relit l'état à chaque ouverture."""
    from vnc_tab import VncConnectionInfo, VncStatus

    asyncio.set_event_loop(asyncio.new_event_loop())
    tab = VncTab(VncConnectionInfo(name="t", host="192.0.2.1"))
    assert set(tab.power_buttons) == set(VncDisplay.POWER_ACTIONS)
    tab.power_buttons = {a: FakeWidget() for a in VncDisplay.POWER_ACTIONS}
    tab.power_info_label = FakeWidget()

    tab._refresh_power_popover()
    assert [b.sensitive for b in tab.power_buttons.values()] == [False] * 3
    assert tab.power_info_label.visible is True and "connexion" in tab.power_info_label.label

    tab.display.client = FakeXvpClient(supported=True)
    tab.display.status = VncStatus.CONNECTED
    tab._refresh_power_popover()
    assert [b.sensitive for b in tab.power_buttons.values()] == [True] * 3
    assert tab.power_info_label.visible is False

    tab._show_power_confirmation_dialog("reset")
    assert tab.power_dialog is not None
    tab.power_dialog.close()
