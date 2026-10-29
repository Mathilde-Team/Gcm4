# SPDX-License-Identifier: GPL-3.0-or-later
"""Glisser-déposer dans l'arbre sous Xvfb (#141)."""

from __future__ import annotations

import configparser

from gi.repository import Gdk, GObject, Gtk

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import ConfigError, load_config, update_keys
from gcm4.core.deplacements import cle
from gcm4.core.model import Host


class _Item:
    """ListItem minimal : ``get_item`` renvoie la ligne visée."""

    def __init__(self, ligne):
        self._ligne = ligne

    def get_item(self):
        return self._ligne


class _Cible:
    """DropTarget minimal : ``get_value`` renvoie la clé glissée (préchargement)."""

    def __init__(self, valeur):
        self._valeur = valeur

    def get_value(self):
        return self._valeur


def _relire(dossier):
    return load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))


def _ini(dossier):
    cp = configparser.RawConfigParser()
    cp.read(dossier / "gcm4.conf", encoding="utf-8")
    return cp


def test_controleurs_sur_chaque_ligne_et_zone_vide(app_fixture):
    """Chaque ligne construite porte DragSource + DropTarget ; la liste a sa cible (racine)."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre

    def controleurs(widget):
        return [
            widget.observe_controllers().get_item(i)
            for i in range(widget.observe_controllers().get_n_items())
        ]

    assert any(isinstance(c, Gtk.DropTarget) for c in controleurs(arbre.liste))
    fleches = []
    enfant = arbre.liste.get_first_child()
    while enfant is not None:
        if isinstance(enfant.get_first_child(), Gtk.TreeExpander):
            fleches.append(enfant.get_first_child())
        enfant = enfant.get_next_sibling()
    assert len(fleches) == len(arbre.lignes_visibles())
    for fleche in fleches:
        types = {type(c) for c in controleurs(fleche)}
        assert {Gtk.DragSource, Gtk.DropTarget} <= types


def test_survol_valide_ou_refuse(app_fixture):
    """Motion : MOVE pour un dépôt valide, aucune action pour un groupe dans son descendant."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    sur_prod = _Item(arbre.ligne("PROD"))
    assert (
        arbre._on_drop_motion(_Cible(cle("LAB/web", False)), 0, 0, sur_prod) == Gdk.DragAction.MOVE
    )
    sur_bases = _Item(arbre.ligne("LAB/bases"))
    assert arbre._on_drop_motion(_Cible(cle("LAB", True)), 0, 0, sur_bases) == Gdk.DragAction(0)
    assert arbre._on_drop_motion(_Cible(None), 0, 0, sur_prod) == Gdk.DragAction(0)
    contenu = arbre._on_drag_prepare(None, 0, 0, _Item(arbre.ligne("LAB/web")))
    assert isinstance(contenu, Gdk.ContentProvider)
    # Contenu de type chaîne : le seul format accepté par les DropTarget de l'arbre.
    assert contenu.ref_formats().contain_gtype(GObject.TYPE_STRING)


def test_hote_depose_enregistre_et_visible(app_fixture, pomper):
    """Web → PROD (replié) : écrit tout de suite, PROD déplié, web sélectionné ; thème de session gardé."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    update_keys(str(dossier / "gcm4.conf"), "window", {"theme-mode": "dark"})
    assert arbre._on_drop(None, cle("LAB/web", False), 0, 0, _Item(arbre.ligne("PROD")))
    pomper()
    assert (1, "PROD/web") in arbre.lignes_visibles()
    assert arbre.chemin_selectionne() == "PROD/web"
    relu = _relire(dossier)
    assert relu.store.find("PROD/web") is not None and relu.store.find("LAB/web") is None
    assert relu.store.find("PROD/web").password == arbre.store.find("PROD/web").password
    assert _ini(dossier).get("window", "theme-mode") == "dark"
    assert _ini(dossier).get("plugin inconnu", "cle") == "valeur"


def test_groupe_vers_racine_par_zone_vide(app_fixture, pomper):
    """LAB/bases déposé sur la zone vide : groupe racine « bases » avec son hôte."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    assert arbre._on_drop(None, cle("LAB/bases", True), 0, 0, None)
    pomper()
    assert (0, "bases") in arbre.lignes_visibles()
    assert _relire(dossier).store.find("bases/db") is not None


def test_refus_sans_ecriture(app_fixture, pomper):
    """Groupe dans son descendant ou même groupe : refusé, fichier inchangé."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    avant = (dossier / "gcm4.conf").read_bytes()
    assert not arbre.deposer(cle("LAB", True), cle("LAB/bases", True))
    assert not arbre.deposer(cle("LAB/web", False), cle("LAB", True))
    pomper()
    assert (dossier / "gcm4.conf").read_bytes() == avant


def test_nom_pris_notifie(app_fixture, pomper):
    """Hôte de même nom à l'arrivée : refus et message."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    messages = []
    arbre.notifier = messages.append
    arbre.store.add(Host("web", group="PROD", host="h", user="u", protocol="p"))
    assert not arbre.deposer(cle("LAB/web", False), cle("PROD", True))
    assert messages and "'PROD'" in messages[0]


def test_echec_d_ecriture_annule(app_fixture, pomper):
    """Enregistrer lève ConfigError : déplacement annulé, arbre et fichier inchangés, toast."""
    app, dossier = app_fixture
    fenetre = app.get_active_window()
    arbre = fenetre.arbre
    avant = (dossier / "gcm4.conf").read_bytes()
    lignes = arbre.lignes_visibles()
    messages = []
    arbre.notifier = messages.append

    def echoue():
        raise ConfigError("disque plein")

    arbre.enregistrer = echoue
    assert not arbre.deposer(cle("LAB/bases", True), cle("PROD", True))
    pomper()
    assert arbre.store.find("LAB/bases/db") is not None and "PROD/bases" not in arbre.store.groups
    assert arbre.lignes_visibles() == lignes
    assert (dossier / "gcm4.conf").read_bytes() == avant
    assert messages and "disque plein" in messages[0]


def test_depot_pendant_une_recherche(app_fixture, pomper):
    """Recherche « db » : db déplacé vers ARCHIVE reste affiché sous son nouveau chemin."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    arbre.recherche.set_text("db")
    arbre.chercher("db")
    pomper()
    assert arbre.deposer(cle("LAB/bases/db", False), cle("ARCHIVE", True))
    pomper()
    assert arbre.lignes_visibles() == [(0, "ARCHIVE"), (1, "ARCHIVE/db")]


def test_toast_de_la_fenetre(app_fixture, pomper):
    """Notifier de la fenêtre : un Adw.Toast dans l'overlay, sans erreur."""
    app, _ = app_fixture
    fenetre = app.get_active_window()
    fenetre.notifier("message")
    pomper()
    assert fenetre.get_content() is fenetre.toasts
