# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour la copie de la capture d'écran dans le presse-papiers (bouton
"edit-copy-symbolic", barre d'outils) : VncDisplay.copy_screenshot_to_clipboard(),
et la méthode statique pure de VncTab qui l'entoure (message de retour affiché
dans status_label).

Feature soeur de save_screenshot() (docs/sessions/session-16.md) : même
source de données (VncDisplay._last_frame_texture, dernier frame poussé par
_push_frame -- déjà recadré sur l'écran actif le cas échéant), même contrat
défensif -- lecture locale pure, ne touche JAMAIS self.client ni le socket,
donc pas concernée par la famille de garde "connexion déjà morte" décrite
dans docs/pieges.md. Seule différence : Gdk.Clipboard.set_texture() au lieu
de Gdk.Texture.save_to_png(path) -- pas de chemin de fichier, pas de
Gtk.FileDialog, la copie est immédiate au clic.
"""

from __future__ import annotations

import numpy as np

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncTab


class FakeTexture:
    """Remplace la Gdk.Texture réelle stockée dans _last_frame_texture --
    copy_screenshot_to_clipboard() ne fait que la transmettre telle quelle
    à Gdk.Clipboard.set_texture(), sans y toucher."""


class FakeClipboard:
    """Remplace le Gdk.Clipboard réel renvoyé par Gtk.Widget.get_clipboard()
    -- copy_screenshot_to_clipboard() n'appelle que set_texture(texture)
    dessus."""

    def __init__(self, fail_with: Exception | None = None):
        self.fail_with = fail_with
        self.texture_set = None

    def set_texture(self, texture):
        if self.fail_with is not None:
            raise self.fail_with
        self.texture_set = texture


def _rgba_frame(width=4, height=3):
    return np.zeros((height, width, 4), dtype=np.uint8)


# ---- VncDisplay.copy_screenshot_to_clipboard() ----


@requires_gtk4
def test_copy_screenshot_returns_false_before_first_frame():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)

    assert display._last_frame_texture is None
    assert display.copy_screenshot_to_clipboard() is False


@requires_gtk4
def test_copy_screenshot_sets_the_last_pushed_frame_on_the_clipboard():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display._push_frame(_rgba_frame())
    fake_texture = FakeTexture()
    display._last_frame_texture = fake_texture
    fake_clipboard = FakeClipboard()
    display.get_clipboard = lambda: fake_clipboard

    result = display.copy_screenshot_to_clipboard()

    assert result is True
    assert fake_clipboard.texture_set is fake_texture


@requires_gtk4
def test_copy_screenshot_returns_false_and_swallows_clipboard_errors():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display._last_frame_texture = FakeTexture()
    display.get_clipboard = lambda: FakeClipboard(fail_with=RuntimeError("presse-papiers indisponible (simulé)"))

    result = display.copy_screenshot_to_clipboard()  # ne doit pas lever

    assert result is False


@requires_gtk4
def test_copy_screenshot_works_normally_again_after_a_failed_attempt():
    # Contre-épreuve : un échec de copie n'empêche pas une tentative
    # suivante de fonctionner -- pas d'état corrompu durable (même esprit
    # que test_save_screenshot_works_normally_again_after_a_failed_attempt
    # dans test_screenshot.py).
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display._last_frame_texture = FakeTexture()
    display.get_clipboard = lambda: FakeClipboard(fail_with=RuntimeError("simulé"))
    assert display.copy_screenshot_to_clipboard() is False

    good_clipboard = FakeClipboard()
    display.get_clipboard = lambda: good_clipboard
    result = display.copy_screenshot_to_clipboard()

    assert result is True
    assert good_clipboard.texture_set is display._last_frame_texture


# ---- VncTab._resolve_copy_screenshot_feedback_message() ----


@requires_gtk4
def test_copy_feedback_message_on_success():
    message = VncTab._resolve_copy_screenshot_feedback_message(True)

    assert "presse-papiers" in message
    assert "Échec" not in message


@requires_gtk4
def test_copy_feedback_message_on_failure():
    message = VncTab._resolve_copy_screenshot_feedback_message(False)

    assert "Échec" in message
