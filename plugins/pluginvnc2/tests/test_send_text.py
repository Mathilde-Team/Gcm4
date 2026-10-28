# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncDisplay.send_text() -- envoyer du texte au serveur distant
comme une suite de frappes clavier, en contournant le presse-papiers.

Feature ajoutée en session 20, en repartant d'un besoin utilisateur réel
(cf. docs/sessions/session-20.md) plutôt que d'un nouveau point de l'audit
« accès à self.client » (clos depuis la session 15) : utile quand la
synchronisation presse-papiers est désactivée par profil
(VncConnectionInfo.sync_clipboard), ou pour un caractère que le clavier
physique local ne produit pas directement.

Deux familles de tests, comme pour le reste du fichier :

1. _resolve_vnc_key_name_for_char() -- logique de résolution PURE (pas de
   self.client, pas de connexion). Ne dépend que de
   Gdk.unicode_to_keyval()/Gdk.keyval_name()/Gdk.keyval_to_unicode() :
   monkeypatchés directement sur le module Gdk importé par vnc_tab.py,
   même approche que tests/test_key_and_mouse_mapping.py (dont ce fichier
   réutilise le raisonnement plutôt que de le retester : les quatre
   branches de _resolve_vnc_key_name() elle-même sont déjà couvertes
   là-bas -- ici on vérifie seulement le NOUVEAU point d'entrée
   Unicode -> keyval qui s'ajoute devant).

2. send_text()/_send_text() -- mécanique de la boucle d'envoi (ordre,
   caractères sans équivalent ignorés, un caractère qui échoue
   n'interrompt pas les suivants). Même approche que
   tests/test_send_cad_dead_connection.py : FakeKeyboard substitué à
   client.keyboard, coroutine _send_text() appelée directement via
   asyncio.run() (pas send_text(), qui ne fait que planifier via
   asyncio.ensure_future()). _resolve_vnc_key_name_for_char() est
   monkeypatchée sur l'INSTANCE pour ces tests-là : la résolution
   Unicode -> nom de touche est déjà testée séparément dans la famille 1
   ci-dessus, la mélanger ici n'apporterait rien et compliquerait la
   lecture des assertions.
"""

from __future__ import annotations

import asyncio

from conftest import GTK4_AVAILABLE, make_test_display, requires_gtk4

if GTK4_AVAILABLE:
    import vnc_tab as _vnc_tab_module


# ---------------------------------------------------------------------------
# _resolve_vnc_key_name_for_char()
# ---------------------------------------------------------------------------


@requires_gtk4
def test_resolve_vnc_key_name_for_char_passes_codepoint_to_unicode_to_keyval(monkeypatch):
    # Vérifie le câblage du nouveau point d'entrée : le point de code
    # Unicode du caractère (pas le caractère lui-même, pas un keyval
    # construit autrement) doit être passé tel quel à Gdk.unicode_to_keyval().
    seen_codepoints = []
    monkeypatch.setattr(
        _vnc_tab_module.Gdk,
        "unicode_to_keyval",
        lambda wc: seen_codepoints.append(wc) or object(),
    )
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_name", lambda keyval: None)
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_to_unicode", lambda keyval: 0)

    display = _vnc_tab_module.VncDisplay.__new__(_vnc_tab_module.VncDisplay)
    display._resolve_vnc_key_name_for_char("é")

    assert seen_codepoints == [ord("é")]


@requires_gtk4
def test_resolve_vnc_key_name_for_char_uses_named_keysym_when_available(monkeypatch):
    # Cas majoritaire : Gdk.unicode_to_keyval() renvoie un keysym X11
    # nommé (lettres, chiffres, ponctuation, l'essentiel de l'AZERTY --
    # même mécanisme que la frappe physique réelle), transmis tel quel par
    # _resolve_vnc_key_name() -- cf. test_resolve_vnc_key_name_passes_
    # through_gdk_keysym_name dans test_key_and_mouse_mapping.py.
    monkeypatch.setattr(_vnc_tab_module.Gdk, "unicode_to_keyval", lambda wc: object())
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_name", lambda keyval: "eacute")
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_to_unicode", lambda keyval: 0)

    display = _vnc_tab_module.VncDisplay.__new__(_vnc_tab_module.VncDisplay)
    assert display._resolve_vnc_key_name_for_char("é") == "eacute"


@requires_gtk4
def test_resolve_vnc_key_name_for_char_falls_back_to_raw_character_without_named_keysym(monkeypatch):
    # Caractère sans keysym X11 classique : Gdk.unicode_to_keyval() renvoie
    # alors un keysym Unicode synthétique (point_de_code | 0x01000000,
    # convention X11/GDK -- cf. docs.gtk.org/gdk4/func.unicode_to_keyval.html,
    # vérifié le 2026-09-11) que Gdk.keyval_name() ne sait pas nommer.
    # _resolve_vnc_key_name() retombe alors sur Gdk.keyval_to_unicode(),
    # qui sait retrouver le point de code d'origine dans cette même plage
    # -- le caractère est transmis tel quel, comme le filet de secours déjà
    # en place pour une touche physique sans nom keysym classique (cf.
    # test_resolve_vnc_key_name_falls_back_to_unicode_when_no_keysym_name
    # dans test_key_and_mouse_mapping.py).
    codepoint = ord("★")
    synthetic_keysym = codepoint | 0x01000000
    monkeypatch.setattr(_vnc_tab_module.Gdk, "unicode_to_keyval", lambda wc: synthetic_keysym)
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_name", lambda keyval: None)
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_to_unicode", lambda keyval: codepoint)

    display = _vnc_tab_module.VncDisplay.__new__(_vnc_tab_module.VncDisplay)
    assert display._resolve_vnc_key_name_for_char("★") == "★"


# ---------------------------------------------------------------------------
# send_text() / _send_text()
# ---------------------------------------------------------------------------


class FakeKeyboard:
    def __init__(self):
        self.pressed = []

    def press(self, name):
        self.pressed.append(name)


def _make_display_with_fake_keyboard():
    display = make_test_display(remote_w=1024, remote_h=768, alloc_w=1024, alloc_h=768)
    display.client.keyboard = FakeKeyboard()
    return display


@requires_gtk4
def test_send_text_presses_resolved_name_for_each_character_in_order(monkeypatch):
    display = _make_display_with_fake_keyboard()
    monkeypatch.setattr(display, "_resolve_vnc_key_name_for_char", lambda char: char.upper())

    asyncio.run(display._send_text("ab"))

    assert display.client.keyboard.pressed == ["A", "B"]


@requires_gtk4
def test_send_text_skips_characters_with_no_resolved_key_name(monkeypatch):
    # Un caractère sans équivalent clavier connu (retour None de
    # _resolve_vnc_key_name_for_char, ex. un caractère purement décoratif
    # sans keysym ET sans point de code exploitable) est ignoré proprement
    # -- rien n'est envoyé pour lui, mais les autres caractères du même
    # texte le sont normalement.
    display = _make_display_with_fake_keyboard()
    monkeypatch.setattr(display, "_resolve_vnc_key_name_for_char", lambda char: None if char == "?" else char)

    asyncio.run(display._send_text("a?b"))

    assert display.client.keyboard.pressed == ["a", "b"]


@requires_gtk4
def test_send_text_continues_after_a_single_character_press_failure(monkeypatch):
    # Point central de cette feature : contrairement à _send_cad (une
    # seule action, garde autour de l'action entière), _send_text() envoie
    # PLUSIEURS caractères -- un seul qui échoue (touche non reconnue par
    # asyncvnc2, ou connexion tombée en cours d'envoi, cf. le commentaire
    # dans vnc_tab.py) ne doit pas empêcher l'envoi des caractères suivants
    # du même texte.
    display = _make_display_with_fake_keyboard()
    monkeypatch.setattr(display, "_resolve_vnc_key_name_for_char", lambda char: char)

    def _press(name):
        if name == "b":
            raise OSError("connexion déjà fermée (simulé)")
        display.client.keyboard.pressed.append(name)

    display.client.keyboard.press = _press

    asyncio.run(display._send_text("abc"))  # ne doit pas lever

    assert display.client.keyboard.pressed == ["a", "c"]


@requires_gtk4
def test_send_text_works_normally_again_after_a_failed_attempt(monkeypatch):
    # Contre-épreuve fonctionnelle (même esprit que
    # test_send_cad_works_normally_again_after_a_failed_attempt) : après un
    # caractère raté au milieu d'un envoi (exception avalée), un appel
    # suivant et complet doit se comporter normalement -- pas d'état
    # corrompu durable côté client.
    display = _make_display_with_fake_keyboard()
    monkeypatch.setattr(display, "_resolve_vnc_key_name_for_char", lambda char: char)

    def _fail_on_x(name):
        if name == "x":
            raise OSError("simulé")
        display.client.keyboard.pressed.append(name)

    display.client.keyboard.press = _fail_on_x

    asyncio.run(display._send_text("axb"))  # "x" échoue, avalé
    assert display.client.keyboard.pressed == ["a", "b"]

    display.client.keyboard.press = lambda name: display.client.keyboard.pressed.append(name)
    asyncio.run(display._send_text("ok"))  # doit réussir normalement
    assert display.client.keyboard.pressed == ["a", "b", "o", "k"]


@requires_gtk4
def test_send_text_does_nothing_without_a_client():
    display = _make_display_with_fake_keyboard()
    display.client = None

    display.send_text("bonjour")  # ne doit pas lever, ne planifie aucune tâche


@requires_gtk4
def test_send_text_does_nothing_with_empty_text():
    display = _make_display_with_fake_keyboard()

    display.send_text("")  # ne doit pas lever, ne planifie aucune tâche
