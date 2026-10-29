# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour VncTab._resolve_forget_key_confirmation (confirmation par
retype avant « Oublier la clé enregistrée et réessayer »).

Avant ce correctif, `_on_forget_key_clicked` appelait directement
`forget_host_key()` + `reconnect()` sur un simple clic -- oublier une clé
pinnée par erreur (mauvais onglet, double-clic accidentel) rouvre une
fenêtre d'exposition à un MITM le temps de la reconnexion suivante, sans
aucun filet. Trouvé le 2026-09-05 en comparant ce plugin à `PATTERNS.md`,
qui impose une confirmation par retype avant toute action destructrice
(cf. `docs/sessions/session-05.md`). La décision "le texte retapé autorise-t-il l'action ?"
est extraite en méthode statique pure (même style que
`_resolve_resize_target_size` / `_resolve_screen_dropdown_index` /
`_resolve_fullscreen_action`) pour rester testable sans instancier
`VncTab`, `Gtk.Window` ni `Gtk.Entry`.
"""

from __future__ import annotations

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import VncTab


@requires_gtk4
def test_exact_match_confirms():
    assert VncTab._resolve_forget_key_confirmation("192.0.2.1", "192.0.2.1") is True


@requires_gtk4
def test_empty_entry_does_not_confirm():
    assert VncTab._resolve_forget_key_confirmation("", "192.0.2.1") is False


@requires_gtk4
def test_partial_match_does_not_confirm():
    assert VncTab._resolve_forget_key_confirmation("192.0.2", "192.0.2.1") is False


@requires_gtk4
def test_whitespace_padded_match_does_not_confirm():
    # Comparaison stricte voulue : pas de .strip() -- un espace en trop
    # doit bloquer la confirmation plutôt que la tolérer silencieusement.
    assert VncTab._resolve_forget_key_confirmation(" 192.0.2.1 ", "192.0.2.1") is False


@requires_gtk4
def test_case_mismatch_does_not_confirm():
    # Comparaison stricte voulue : les noms d'hôte DNS sont insensibles à
    # la casse côté résolution, mais on veut vérifier que l'utilisateur a
    # bien relu et retapé EXACTEMENT ce qui est affiché, pas une variante.
    assert VncTab._resolve_forget_key_confirmation("EXAMPLE.COM", "example.com") is False


@requires_gtk4
def test_empty_expected_host_never_confirms_even_with_empty_entry():
    # Cas limite qui ne devrait pas arriver en pratique (host toujours
    # renseigné dans VncConnectionInfo), mais on ne veut surtout pas qu'une
    # entrée vide "matche" un hôte vide par accident.
    assert VncTab._resolve_forget_key_confirmation("", "") is False
