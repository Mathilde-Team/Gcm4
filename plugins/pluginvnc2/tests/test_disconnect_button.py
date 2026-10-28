# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncTab._resolve_disconnect_button_sensitivity (bouton
"Déconnecter", barre d'outils).

Feature ajoutée en session 21, en repartant d'un besoin utilisateur réel
(cf. docs/sessions/session-21.md) plutôt que d'un nouveau point de l'audit
« accès à self.client » (clos depuis la session 15) : un moyen de fermer
la connexion SANS fermer l'onglet -- libérer une session exclusive côté
serveur, ou annuler une tentative de connexion/reconnexion en cours --
distinct du bouton "Reconnecter" déjà existant (qui referme puis rouvre
tout de suite) et de la fermeture complète de l'onglet.

Contrairement à btn_screenshot/btn_copy_screenshot/btn_send_text (cliquables
seulement une fois pleinement CONNECTED, cf. tests/test_screenshot.py
et consorts), "Déconnecter" a un sens dans CONNECTING et ERROR aussi bien
que CONNECTED -- seul DISCONNECTED (rien à arrêter) le désactive. Même
style de test que tests/test_fullscreen_toggle.py : méthode statique pure,
appelée directement sur la classe, sans instancier de vrai VncTab.
"""

from __future__ import annotations

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncStatus, VncTab


@requires_gtk4
def test_sensitive_while_connecting():
    # Permet d'annuler une tentative de connexion en cours (VncDisplay.stop()
    # annule proprement _connect_task, cf. docs/sessions/session-21.md).
    assert VncTab._resolve_disconnect_button_sensitivity(VncStatus.CONNECTING.value) is True


@requires_gtk4
def test_sensitive_while_connected():
    # Cas d'usage principal : libérer une session active.
    assert VncTab._resolve_disconnect_button_sensitivity(VncStatus.CONNECTED.value) is True


@requires_gtk4
def test_sensitive_on_error():
    # Permet d'interrompre une boucle de reconnexion automatique en cours de
    # backoff (cf. VncDisplay._should_schedule_auto_reconnect) sans attendre
    # la prochaine tentative ni fermer l'onglet.
    assert VncTab._resolve_disconnect_button_sensitivity(VncStatus.ERROR.value) is True


@requires_gtk4
def test_not_sensitive_when_already_disconnected():
    # Rien à arrêter -- même état que juste après construction (self.status
    # = VncStatus.DISCONNECTED posé par VncDisplay.__init__).
    assert VncTab._resolve_disconnect_button_sensitivity(VncStatus.DISCONNECTED.value) is False
