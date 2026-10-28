# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour les raccourcis clavier globaux de VncTab (capture d'écran,
copie presse-papiers, envoi de texte, plein écran, Ctrl+Alt+Suppr) -- Gtk.ShortcutController
en phase CAPTURE, cf. docstring de VncTab._setup_global_shortcuts dans
vnc_tab.py pour la raison exacte (VncDisplay._on_key_pressed() retourne
toujours True et avalerait ces raccourcis sans la phase CAPTURE sur un
ancêtre).

`Gtk.ShortcutController`/`Gtk.Shortcut`/`Gtk.CallbackAction` ne sont pas
vérifiables sous le stub GTK4 de test (tests/gtk_stub/ renvoie un _NoOp()
pour tout, sans vraie sémantique de déclenchement d'accélérateur) : ce
fichier teste donc la table `_GLOBAL_SHORTCUTS` elle-même (regression-proof
contre une faute de frappe dans un nom de méthode) et les quatre méthodes
d'action `_shortcut_action_*`, qui ne font que déléguer aux méthodes déjà
couvertes ailleurs (bouton capture d'écran, bouton copie presse-papiers,
bouton plein écran, bouton Ctrl+Alt+Suppr) -- pas de nouvelle logique
métier propre à tester en plus de cette délégation.
"""

from __future__ import annotations

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncTab


def _bare_tab():
    """Instance de VncTab SANS passer par __init__ (pas de vrai widget GTK
    construit) -- même technique que VncDisplay.__new__(VncDisplay) déjà
    utilisée dans tests/test_key_and_mouse_mapping.py."""
    return VncTab.__new__(VncTab)


# ---- Table _GLOBAL_SHORTCUTS ----


@requires_gtk4
def test_global_shortcuts_table_is_not_empty():
    assert len(VncTab._GLOBAL_SHORTCUTS) == 5


@requires_gtk4
def test_global_shortcuts_accelerators_are_unique():
    accelerators = [accelerator for accelerator, _ in VncTab._GLOBAL_SHORTCUTS]
    assert len(accelerators) == len(set(accelerators))


@requires_gtk4
def test_global_shortcuts_reference_existing_callable_handlers():
    # Filet anti-faute-de-frappe : si un nom de méthode dans la table ne
    # correspond à rien (ou n'est pas appelable), getattr lève ou renvoie
    # un objet non appelable -- ce test l'aurait détecté avant un plantage
    # au premier appui de touche.
    for _accelerator, handler_name in VncTab._GLOBAL_SHORTCUTS:
        handler = getattr(VncTab, handler_name)
        assert callable(handler)


@requires_gtk4
def test_global_shortcuts_cover_screenshot_fullscreen_and_ctrl_alt_del():
    handler_names = {handler_name for _accelerator, handler_name in VncTab._GLOBAL_SHORTCUTS}
    assert handler_names == {
        "_shortcut_action_screenshot",
        "_shortcut_action_copy_screenshot",
        "_shortcut_action_toggle_fullscreen",
        "_shortcut_action_send_ctrl_alt_del",
        "_shortcut_action_send_text",
    }


@requires_gtk4
def test_send_text_shortcut_is_alt_f12():
    # Gcm4 #92 : même famille F12 que la capture (Maj) et la copie (Ctrl).
    assert ("<Alt>F12", "_shortcut_action_send_text") in VncTab._GLOBAL_SHORTCUTS


# ---- Méthodes d'action (délégation pure) ----


@requires_gtk4
def test_shortcut_action_screenshot_delegates_to_on_screenshot_clicked_and_returns_true():
    tab = _bare_tab()
    calls = []
    tab._on_screenshot_clicked = lambda: calls.append("screenshot")

    result = tab._shortcut_action_screenshot()

    assert calls == ["screenshot"]
    assert result is True


@requires_gtk4
def test_shortcut_action_copy_screenshot_delegates_to_on_copy_screenshot_clicked_and_returns_true():
    tab = _bare_tab()
    calls = []
    tab._on_copy_screenshot_clicked = lambda: calls.append("copy_screenshot")

    result = tab._shortcut_action_copy_screenshot()

    assert calls == ["copy_screenshot"]
    assert result is True


@requires_gtk4
def test_shortcut_action_toggle_fullscreen_delegates_and_returns_true():
    tab = _bare_tab()
    calls = []
    tab._toggle_fullscreen = lambda: calls.append("fullscreen")

    result = tab._shortcut_action_toggle_fullscreen()

    assert calls == ["fullscreen"]
    assert result is True


@requires_gtk4
def test_shortcut_action_send_ctrl_alt_del_delegates_to_display_and_returns_true():
    tab = _bare_tab()

    class FakeDisplay:
        def __init__(self):
            self.cad_sent = 0

        def send_ctrl_alt_del(self):
            self.cad_sent += 1

    tab.display = FakeDisplay()

    result = tab._shortcut_action_send_ctrl_alt_del()

    assert tab.display.cad_sent == 1
    assert result is True


class _FakeButton:
    def __init__(self, sensitive):
        self.sensitive = sensitive

    def get_sensitive(self):
        return self.sensitive


@requires_gtk4
def test_shortcut_action_send_text_opens_dialog_when_connected_and_returns_true():
    tab = _bare_tab()
    tab.btn_send_text = _FakeButton(True)
    calls = []
    tab._on_send_text_clicked = lambda: calls.append("send_text")

    result = tab._shortcut_action_send_text()

    assert calls == ["send_text"]
    assert result is True


@requires_gtk4
def test_shortcut_action_send_text_does_nothing_when_not_connected_and_returns_false():
    # Même garde que le bouton (désactivé sans connexion) : pas de
    # dialogue pour un envoi qui ne partirait nulle part, et False pour ne
    # pas avaler l'événement sans effet visible.
    tab = _bare_tab()
    tab.btn_send_text = _FakeButton(False)
    calls = []
    tab._on_send_text_clicked = lambda: calls.append("send_text")

    result = tab._shortcut_action_send_text()

    assert calls == []
    assert result is False
