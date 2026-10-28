# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncDisplay._on_local_clipboard_read face à une connexion déjà
morte.

Bug trouvé en auditant tous les accès à `self.client` dans `vnc_tab.py` (cf.
docs/sessions/session-14.md) : sixième point de la même famille que les cinq
chemins d'entrée haute fréquence déjà protégés (`_on_motion`,
`_on_button_pressed`, `_on_button_released`, `_on_scroll`, `_on_key_pressed`
-- sessions 09 à 13). `self.client.clipboard.write(text)` écrit réellement
sur le socket pour relayer le presse-papiers local vers le serveur, et
n'avait AUCUN garde -- ni `except` dédié, ni le garde générique `if not
self.client:` déjà présent (qui ne protège de toute façon pas ce cas
précis, cf. docs/pieges.md : `self.client` n'est jamais remis à `None`
après une erreur de lecture, seule une nouvelle connexion réussie le
réaffecte). Ce callback est invoqué directement par GLib
(`clipboard.read_text_async`), donc sans garde une exception y remontait
telle quelle hors du callback, exactement comme pour les cinq chemins déjà
corrigés.

Ces tests appellent la VRAIE méthode de `VncDisplay` (pas une
réimplémentation de la logique) via `conftest.make_test_display`, en
substituant `client.clipboard` (presse-papiers CÔTÉ SERVEUR, cible de
`write()`) par un faux qui trace les écritures ou lève sur demande -- même
approche que `tests/test_key_dead_connection.py`. Le premier argument reçu
par `_on_local_clipboard_read` (presse-papiers SYSTÈME local) est lui aussi
remplacé par un faux minimal exposant juste `read_text_finish()`, seule
méthode appelée dessus.
"""

from __future__ import annotations

from conftest import make_test_display, requires_gtk4


class FakeServerClipboard:
    """Remplace client.clipboard (presse-papiers côté serveur VNC) : trace
    les écritures réussies."""

    def __init__(self):
        self.written = []

    def write(self, text):
        self.written.append(text)


class FakeSystemClipboard:
    """Remplace le Gdk.Clipboard local passé en premier argument --
    _on_local_clipboard_read n'appelle que read_text_finish(result) dessus."""

    def __init__(self, text):
        self.text = text

    def read_text_finish(self, result):
        return self.text


def _make_display_with_fake_clipboard():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display.client.clipboard = FakeServerClipboard()
    return display


@requires_gtk4
def test_clipboard_write_succeeds_normally():
    display = _make_display_with_fake_clipboard()

    display._on_local_clipboard_read(FakeSystemClipboard("bonjour"), None)

    assert display.client.clipboard.written == ["bonjour"]
    assert display._last_clipboard_text == "bonjour"


@requires_gtk4
def test_clipboard_read_ignores_unchanged_text():
    display = _make_display_with_fake_clipboard()
    display._last_clipboard_text = "déjà envoyé"

    display._on_local_clipboard_read(FakeSystemClipboard("déjà envoyé"), None)

    assert display.client.clipboard.written == []  # rien à renvoyer, texte inchangé


@requires_gtk4
def test_clipboard_write_swallows_exception_from_dead_connection():
    display = _make_display_with_fake_clipboard()

    def _boom(_text):
        raise OSError("connexion déjà fermée (simulé)")

    display.client.clipboard.write = _boom

    display._on_local_clipboard_read(FakeSystemClipboard("bonjour"), None)  # ne doit pas lever

    # L'écriture a bien été tentée (et a échoué) -- seule l'exception est
    # avalée, _last_clipboard_text reflète le texte qu'on a essayé d'envoyer.
    assert display._last_clipboard_text == "bonjour"


@requires_gtk4
def test_clipboard_write_works_normally_again_after_a_failed_write():
    # Contre-épreuve fonctionnelle : après une écriture ratée (exception
    # avalée), un changement de presse-papiers DIFFÉRENT doit se comporter
    # normalement -- pas d'état corrompu durable côté client.
    display = _make_display_with_fake_clipboard()
    calls = []

    def _fail_once_then_record(text):
        calls.append(text)
        if len(calls) == 1:
            raise OSError("simulé")

    display.client.clipboard.write = _fail_once_then_record

    display._on_local_clipboard_read(FakeSystemClipboard("premier essai"), None)  # échoue, avalé
    assert display._last_clipboard_text == "premier essai"

    display._on_local_clipboard_read(FakeSystemClipboard("second essai"), None)  # doit réussir

    assert calls == ["premier essai", "second essai"]
    assert display._last_clipboard_text == "second essai"


@requires_gtk4
def test_clipboard_read_does_nothing_without_a_client():
    display = _make_display_with_fake_clipboard()
    display.client = None

    display._on_local_clipboard_read(FakeSystemClipboard("bonjour"), None)  # ne doit pas lever
