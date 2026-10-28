# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour la capture d'écran (bouton "camera-photo-symbolic", barre
d'outils) : VncDisplay.save_screenshot(), et les deux méthodes statiques
pures de VncTab qui l'entourent (nom de fichier par défaut, message de
retour affiché dans status_label).

Fonctionnalité purement GTK4/locale : save_screenshot() ne touche JAMAIS
self.client ni le socket (lecture de la dernière texture déjà poussée à
l'écran par _push_frame, écriture sur disque uniquement) -- elle n'entre
donc PAS dans la famille de garde "connexion déjà morte" décrite dans
docs/pieges.md, contrairement aux six callbacks GTK/GLib et deux actions
fire-and-forget déjà protégés (sessions 09 à 15). Elle reste toutefois
protégée par son propre try/except : à l'appelant (bouton de la barre
d'outils, dialogue de sauvegarde) de prévenir l'utilisateur plutôt que de
laisser une exception d'écriture disque (permissions, disque plein, chemin
invalide) remonter hors d'un callback GTK.

`_last_frame_texture` est un attribut d'instance normal (pas récupéré via
self.get_paintable()) précisément pour rester testable avec le stub GTK4
de tests/gtk_stub/ : ce stub renvoie un `_NoOp()` -- toujours "vrai" --
pour tout attribut/méthode non explicitement défini, y compris
get_paintable() avant le tout premier rendu, ce qui rendrait "pas encore
d'image reçue" indétectable si on s'appuyait dessus.
"""

from __future__ import annotations

import numpy as np

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncTab


class FakeTexture:
    """Remplace la Gdk.Texture réelle stockée dans _last_frame_texture --
    save_screenshot() n'appelle que save_to_png(path) dessus."""

    def __init__(self, fail_with: Exception | None = None):
        self.fail_with = fail_with
        self.saved_to = None

    def save_to_png(self, path):
        if self.fail_with is not None:
            raise self.fail_with
        self.saved_to = path


def _rgba_frame(width=4, height=3):
    return np.zeros((height, width, 4), dtype=np.uint8)


# ---- VncDisplay.save_screenshot() ----


@requires_gtk4
def test_save_screenshot_returns_false_before_first_frame():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)

    assert display._last_frame_texture is None
    assert display.save_screenshot("/tmp/should-not-be-created.png") is False


@requires_gtk4
def test_push_frame_records_the_texture_for_later_screenshot():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)

    display._push_frame(_rgba_frame())

    assert display._last_frame_texture is not None


@requires_gtk4
def test_save_screenshot_writes_the_last_pushed_frame():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display._push_frame(_rgba_frame())
    fake_texture = FakeTexture()
    display._last_frame_texture = fake_texture

    result = display.save_screenshot("/tmp/capture.png")

    assert result is True
    assert fake_texture.saved_to == "/tmp/capture.png"


@requires_gtk4
def test_save_screenshot_returns_false_and_swallows_disk_write_errors():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display._last_frame_texture = FakeTexture(fail_with=OSError("disque plein (simulé)"))

    result = display.save_screenshot("/tmp/capture.png")  # ne doit pas lever

    assert result is False


@requires_gtk4
def test_save_screenshot_works_normally_again_after_a_failed_attempt():
    # Contre-épreuve : un échec d'écriture n'empêche pas une capture
    # suivante de fonctionner -- pas d'état corrompu durable.
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display._last_frame_texture = FakeTexture(fail_with=OSError("simulé"))
    assert display.save_screenshot("/tmp/premier.png") is False

    good_texture = FakeTexture()
    display._last_frame_texture = good_texture
    result = display.save_screenshot("/tmp/second.png")

    assert result is True
    assert good_texture.saved_to == "/tmp/second.png"


# ---- VncTab._default_screenshot_filename() ----


@requires_gtk4
def test_default_screenshot_filename_format():
    from datetime import datetime

    when = datetime(2026, 9, 10, 21, 31, 47)

    name = VncTab._default_screenshot_filename("192.0.2.1", when)

    assert name == "vnc-192.0.2.1-20260910-213147.png"


@requires_gtk4
def test_default_screenshot_filename_sanitizes_ipv6_colons():
    from datetime import datetime

    when = datetime(2026, 9, 10, 21, 31, 47)

    name = VncTab._default_screenshot_filename("fe80::1", when)

    assert ":" not in name
    assert name == "vnc-fe80--1-20260910-213147.png"


# ---- VncTab._resolve_screenshot_feedback_message() ----


@requires_gtk4
def test_feedback_message_on_success_mentions_the_path():
    message = VncTab._resolve_screenshot_feedback_message(True, "/home/user/Images/capture.png")

    assert "/home/user/Images/capture.png" in message


@requires_gtk4
def test_feedback_message_on_failure_does_not_mention_a_path():
    message = VncTab._resolve_screenshot_feedback_message(False, "/home/user/Images/capture.png")

    assert "/home/user/Images/capture.png" not in message
    assert "Échec" in message
