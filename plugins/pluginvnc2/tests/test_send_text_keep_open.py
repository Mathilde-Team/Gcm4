"""Tests pour VncTab._resolve_send_text_action -- dialogue "Envoyer du
texte" restant ouvert pour un envoi répété (issue #93).

Une case « Garder la fenêtre ouverte après l'envoi » (décochée par défaut,
mémorisée pour la durée de vie de l'onglet) choisit entre l'ancien
comportement (fermeture après l'envoi) et l'envoi répété (champ vidé,
dialogue gardé ouvert). Comme pour tests/test_send_text_paste.py, seule la
décision pure est testée ici, pas la construction du dialogue GTK.
"""

from __future__ import annotations

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncTab


@requires_gtk4
def test_closes_after_sending_by_default():
    assert VncTab._resolve_send_text_action("ls -l", keep_open=False) == "close"


@requires_gtk4
def test_keeps_the_dialog_open_when_asked():
    assert VncTab._resolve_send_text_action("ls -l", keep_open=True) == "keep"


@requires_gtk4
def test_does_nothing_for_an_empty_text():
    # Entrée dans un champ vide : rien à envoyer, ni fermeture ni vidage.
    assert VncTab._resolve_send_text_action("", keep_open=False) is None
    assert VncTab._resolve_send_text_action("", keep_open=True) is None
