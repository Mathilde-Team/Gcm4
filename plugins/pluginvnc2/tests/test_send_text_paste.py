# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncTab._resolve_pasted_clipboard_text -- bouton "Coller"
(icône secondaire) sur le champ de saisie du dialogue "Envoyer du texte"
(cf. docs/sessions/session-22.md).

Feature ajoutée en session 22, en repartant d'un besoin utilisateur réel
plutôt que d'un nouveau point de l'audit « accès à self.client » (clos
depuis la session 15) ou de l'une des deux pistes différées en session 20
: coller le contenu du presse-papiers LOCAL directement dans le champ,
sans avoir à le retaper à la main -- utile dans le même contexte que
send_text() lui-même (synchronisation presse-papiers désactivée par
profil), pour éviter la ressaisie manuelle d'un texte déjà copié
localement (ex. un mot de passe depuis un gestionnaire).

Comme pour tests/test_disconnect_button.py (session 21), le nouveau code
de cette session est essentiellement de la coordination GTK (icône sur
Gtk.Entry, lecture asynchrone du presse-papiers) qui n'est pas testée
directement dans ce dépôt (aucun dialogue n'est testé à ce niveau, cf.
tests/test_forget_key_confirmation.py qui teste sa règle de décision, pas
la construction du dialogue lui-même) -- seule la décision pure extraite,
_resolve_pasted_clipboard_text(), est testée ici.
"""

from __future__ import annotations

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncTab


@requires_gtk4
def test_returns_the_text_when_the_clipboard_has_content():
    assert VncTab._resolve_pasted_clipboard_text("hunter2") == "hunter2"


@requires_gtk4
def test_returns_none_for_an_empty_clipboard():
    # Un presse-papiers vide ne doit pas écraser un champ déjà rempli.
    assert VncTab._resolve_pasted_clipboard_text("") is None


@requires_gtk4
def test_returns_none_when_the_clipboard_could_not_be_read():
    # Cas du contenu non textuel (ex. une image copiée) : clipboard.read_text_finish()
    # lève GLib.Error, capturé dans _show_send_text_dialog AVANT d'appeler
    # cette méthode -- None est ce qu'elle reçoit alors en entrée.
    assert VncTab._resolve_pasted_clipboard_text(None) is None
