# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests pour le mapping clavier/souris GDK -> asyncvnc2.

Rappel du choix d'architecture (cf. le commentaire au-dessus de
GDK_TO_VNC_KEY dans vnc_tab.py) : GDK_TO_VNC_KEY est une table de
DÉROGATIONS ponctuelles, pas la table principale -- la plupart des
touches (y compris tout l'AZERTY) passent par le nom de keysym X11 que
GDK renvoie directement. Le test ci-dessous vérifie que cette table reste
volontairement petite -- si elle regonfle, c'est probablement le signe
qu'un futur changement a réintroduit des entrées redondantes plutôt que
de laisser passer le nom GDK tel quel.
"""

from __future__ import annotations

from conftest import GTK4_AVAILABLE, requires_gtk4

if GTK4_AVAILABLE:
    from vnc_tab import GDK_TO_VNC_KEY, VncDisplay


@requires_gtk4
def test_gdk_to_vnc_key_only_contains_deliberate_overrides():
    # KP_Enter -> Return est la seule dérogation voulue au moment de
    # l'écriture. Si ce test casse parce que la table a grossi, vérifie
    # que chaque nouvelle entrée est bien une VRAIE dérogation (le nom
    # GDK ne doit pas déjà exister tel quel côté asyncvnc2) et pas une
    # réintroduction accidentelle d'entrées redondantes.
    assert GDK_TO_VNC_KEY == {"KP_Enter": "Return"}


@requires_gtk4
def test_gdk_button_to_vnc_maps_left_middle_right():
    # GDK : 1=gauche, 2=milieu, 3=droit -> asyncvnc2 : 0=gauche, 1=milieu, 2=droit
    assert VncDisplay._gdk_button_to_vnc(1) == 0
    assert VncDisplay._gdk_button_to_vnc(2) == 1
    assert VncDisplay._gdk_button_to_vnc(3) == 2


@requires_gtk4
def test_gdk_button_to_vnc_unknown_button_defaults_to_left():
    # Souris avec des boutons additionnels (ex. boutons latéraux) : pas
    # de crash, repli raisonnable sur le bouton gauche plutôt que de
    # planter la session pour un bouton qu'on ne sait pas mapper.
    assert VncDisplay._gdk_button_to_vnc(8) == 0
    assert VncDisplay._gdk_button_to_vnc(9) == 0


# ---------------------------------------------------------------------------
# VncDisplay._resolve_vnc_key_name -- jusqu'ici seule GDK_TO_VNC_KEY (la
# table de dérogations) et _gdk_button_to_vnc étaient testées dans ce
# fichier ; la chaîne de repli complète (dérogation -> nom de keysym GDK ->
# caractère Unicode -> None) n'avait encore aucun test dédié alors qu'elle
# contient trois branches de décision distinctes. Ajouté le 2026-09-05.
#
# _resolve_vnc_key_name() ne dépend que de `Gdk.keyval_name`/
# `Gdk.keyval_to_unicode` (pas de `self`, pas de connexion) : on les
# monkeypatch directement sur le module `Gdk` importé par vnc_tab.py plutôt
# que d'construire un vrai keyval GDK, ce qui suffit à isoler les trois
# branches sans dépendre de constantes X11 réelles.
# ---------------------------------------------------------------------------

if GTK4_AVAILABLE:
    import vnc_tab as _vnc_tab_module


@requires_gtk4
def test_resolve_vnc_key_name_applies_deliberate_override(monkeypatch):
    # Dérogation explicite (GDK_TO_VNC_KEY) : prioritaire même si le nom
    # keysym existe par ailleurs -- KP_Enter doit être traité comme Return,
    # pas transmis tel quel.
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_name", lambda keyval: "KP_Enter")
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_to_unicode", lambda keyval: 0)

    display = VncDisplay.__new__(VncDisplay)
    assert display._resolve_vnc_key_name(object()) == "Return"


@requires_gtk4
def test_resolve_vnc_key_name_passes_through_gdk_keysym_name(monkeypatch):
    # Cas immensément majoritaire (cf. commentaire au-dessus de
    # GDK_TO_VNC_KEY) : aucune dérogation nécessaire, le nom keysym X11
    # renvoyé par GDK est transmis tel quel -- y compris pour l'AZERTY
    # (lettres accentuées, touches mortes).
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_name", lambda keyval: "eacute")
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_to_unicode", lambda keyval: 0)

    display = VncDisplay.__new__(VncDisplay)
    assert display._resolve_vnc_key_name(object()) == "eacute"


@requires_gtk4
def test_resolve_vnc_key_name_falls_back_to_unicode_when_no_keysym_name(monkeypatch):
    # Filet de secours : certaines touches (claviers étendus) n'ont pas de
    # nom keysym X11 classique côté GDK -- on retombe alors sur le
    # caractère Unicode produit, que key_codes (asyncvnc2) reconnaît aussi.
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_name", lambda keyval: None)
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_to_unicode", lambda keyval: ord("é"))

    display = VncDisplay.__new__(VncDisplay)
    assert display._resolve_vnc_key_name(object()) == "é"


@requires_gtk4
def test_resolve_vnc_key_name_returns_none_when_nothing_matches(monkeypatch):
    # Ni nom keysym, ni codepoint Unicode (ex. touche purement modificatrice
    # sans keyval exploitable) : on ignore proprement plutôt que d'envoyer
    # n'importe quoi côté serveur -- cf. le `return True` sans action dans
    # `_on_key_pressed`.
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_name", lambda keyval: None)
    monkeypatch.setattr(_vnc_tab_module.Gdk, "keyval_to_unicode", lambda keyval: 0)

    display = VncDisplay.__new__(VncDisplay)
    assert display._resolve_vnc_key_name(0) is None
