# SPDX-License-Identifier: GPL-3.0-or-later
"""Recherche dans l'arbre sous Xvfb (#140)."""

from __future__ import annotations

import configparser

from gi.repository import Gtk

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)


def test_filtre_deplie_tout_puis_restaure(app_fixture, pomper):
    """« api » : PROD/eu/api seul, groupes dépliés ; effacé : PROD de nouveau replié, LAB déplié."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    avant = arbre.lignes_visibles()
    arbre.recherche.set_text("API")
    arbre.chercher(arbre.recherche.get_text())
    pomper()
    assert arbre.lignes_visibles() == [(0, "PROD"), (1, "PROD/eu"), (2, "PROD/eu/api")]
    arbre.chercher("10.0.0.2")  # adresse de db
    pomper()
    assert arbre.lignes_visibles() == [(0, "LAB"), (1, "LAB/bases"), (2, "LAB/bases/db")]
    arbre.chercher("")
    pomper()
    assert arbre.lignes_visibles() == avant
    assert arbre.store.groups["PROD"].collapsed and not arbre.store.groups["LAB"].collapsed


def test_recherche_ne_modifie_pas_l_etat_enregistre(app_fixture, pomper):
    """Chercher, replier pendant la recherche, effacer, quitter : collapsed-folders inchangé (« 2 »)."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    arbre.chercher("e")
    pomper()
    arbre.ligne("LAB").set_expanded(False)
    pomper()
    arbre.chercher("")
    pomper()
    app.activate_action("quit", None)
    pomper()
    cp = configparser.RawConfigParser()
    cp.read(dossier / "gcm4.conf", encoding="utf-8")
    assert cp.get("window", "collapsed-folders") == "2"


def test_aucun_resultat_et_selection_conservee(app_fixture, pomper):
    """Sans résultat : message affiché ; sélection gardée à l'effacement si visible."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    assert arbre.selectionner("LAB/web")
    arbre.chercher("web")
    pomper()
    arbre.chercher("introuvable")
    pomper()
    assert arbre.aucun_resultat.get_visible() and not arbre.defilement.get_visible()
    arbre.chercher("")
    pomper()
    assert not arbre.aucun_resultat.get_visible()
    assert arbre.chemin_selectionne() == "LAB/web"


def test_ctrl_f_et_echap(app_fixture, pomper):
    """win.search montre le panneau et donne le focus ; stop-search (Échap) efface."""
    app, _ = app_fixture
    fenetre = app.get_active_window()
    fenetre.activate_action("win.toggle-panel", None)
    pomper()
    assert not fenetre.panneau.get_visible()
    assert app.get_accels_for_action("win.search") == ["<Control>f"]
    fenetre.activate_action("win.search", None)
    pomper()
    assert fenetre.panneau.get_visible()
    assert (
        fenetre.arbre.recherche.has_focus()
        or fenetre.arbre.recherche.get_focus_child() is not None
    )
    fenetre.arbre.recherche.set_text("api")
    fenetre.arbre.chercher("api")
    fenetre.arbre.recherche.emit("stop-search")
    pomper()
    assert fenetre.arbre.recherche.get_text() == "" and fenetre.arbre.garder is None
    assert (0, "LAB") in fenetre.arbre.lignes_visibles()


def test_saisie_dans_la_liste_capturee(app_fixture):
    """La frappe dans la liste part dans la recherche (set_key_capture_widget)."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    assert arbre.recherche.get_key_capture_widget() is arbre.liste
    assert isinstance(arbre.recherche, Gtk.SearchEntry)
