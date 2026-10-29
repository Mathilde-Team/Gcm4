# SPDX-License-Identifier: GPL-3.0-or-later
"""Sélection multiple et édition en lot dans l'arbre, sous Xvfb (#100)."""

from __future__ import annotations

from gi.repository import Gtk

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import load_config
from gcm4.core.model import Host
from tests.gtk4.test_menu_arbre import _Geste, _libelles


def _relire(dossier):
    return load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))


def _arbre(app_fixture):
    app, dossier = app_fixture
    return app.get_active_window().arbre, dossier


def _clic_droit(arbre, chemin, pomper):
    """Clic droit au milieu de la ligne visible ``chemin``."""
    enfant = arbre.liste.get_first_child()
    while not (
        isinstance(enfant.get_first_child(), Gtk.TreeExpander)
        and enfant.get_first_child().get_list_row() is arbre.ligne(chemin)
    ):
        enfant = enfant.get_next_sibling()
    ok, bornes = enfant.compute_bounds(arbre.liste)
    assert ok
    arbre.menu._on_clic_droit(
        _Geste(), 1, bornes.get_x() + 30, bornes.get_y() + bornes.get_height() / 2
    )
    pomper()


def _ouvrir_dialogue(arbre, chemins, pomper):
    assert arbre.selectionner_plusieurs(chemins) == len(chemins)
    arbre.menu._menu_clavier()
    pomper()
    arbre.menu.popover.popdown()
    arbre.menu.executer("edit-selection")
    pomper()
    return arbre.menu.dialogue, arbre.menu.edition_lot_widgets


def test_selection_multiple_et_menu_de_la_selection(app_fixture, pomper):
    """Deux hôtes sélectionnés : menu de lot ; pas de cible unique pour Suppr ou F2."""
    arbre, _dossier = _arbre(app_fixture)
    assert isinstance(arbre.selection, Gtk.MultiSelection)
    assert arbre.selectionner_plusieurs(["LAB/web", "LAB/bases/db"]) == 2
    assert arbre.chemin_selectionne() is None
    # Ordre de l'arbre : sous-groupes avant les hôtes.
    assert [n.chemin for n in arbre.noeuds_selectionnes()] == ["LAB/bases/db", "LAB/web"]
    arbre.menu._menu_clavier()
    pomper()
    assert _libelles(arbre.menu) == ["arbre.edit-selection", "arbre.expand", "arbre.collapse"]
    arbre.menu.popover.popdown()
    arbre.menu.dialogue = None
    arbre.menu._clavier("delete")
    arbre.menu._clavier("rename-group", "edit")
    assert arbre.menu.dialogue is None and arbre.menu.dialogue_hote is None


def test_clic_droit_garde_ou_remplace_la_selection(app_fixture, pomper):
    """Clic droit dans la sélection : gardée ; hors de la sélection : remplacée."""
    arbre, _dossier = _arbre(app_fixture)
    arbre.selectionner_plusieurs(["LAB/web", "LAB/bases/db"])
    _clic_droit(arbre, "LAB/web", pomper)
    assert [n.chemin for n in arbre.menu.lot] == ["LAB/bases/db", "LAB/web"]
    assert _libelles(arbre.menu)[0] == "arbre.edit-selection"
    arbre.menu.popover.popdown()
    _clic_droit(arbre, "LAB", pomper)
    assert arbre.chemin_selectionne() == "LAB" and arbre.menu.lot == []
    arbre.menu.popover.popdown()


def test_lot_de_groupes_sans_edition(app_fixture, pomper):
    """Un groupe et un hôte : pas d'édition en lot (il faut deux hôtes)."""
    arbre, _dossier = _arbre(app_fixture)
    arbre.selectionner_plusieurs(["LAB", "LAB/web"])
    arbre.menu._menu_clavier()
    pomper()
    assert _libelles(arbre.menu) == ["arbre.expand", "arbre.collapse"]
    arbre.menu.popover.popdown()
    arbre.menu.dialogue = None
    arbre.menu.executer("edit-selection")
    assert arbre.menu.dialogue is None


def test_changer_port_et_groupe(app_fixture, pomper):
    """Port 2200 et groupe PROD pour web et db : écrits, sélection gardée, message."""
    arbre, dossier = _arbre(app_fixture)
    messages = []
    arbre.notifier = messages.append
    dialogue, (case_port, port, case_groupe, choix, erreur) = _ouvrir_dialogue(
        arbre, ["LAB/web", "LAB/bases/db"], pomper
    )
    assert "2" in dialogue.get_heading()
    assert not dialogue.get_response_enabled("appliquer")  # rien de coché
    assert not port.get_sensitive() and not choix.get_sensitive()
    case_port.set_active(True)
    assert port.get_sensitive() and dialogue.get_response_enabled("appliquer")  # port par défaut
    port.set_text("22a")
    assert not dialogue.get_response_enabled("appliquer") and erreur.get_visible()
    port.set_text("2200")
    assert dialogue.get_response_enabled("appliquer") and not erreur.get_visible()
    case_groupe.set_active(True)
    noms = [choix.get_model().get_string(i) for i in range(choix.get_model().get_n_items())]
    assert noms[0] == "/"
    choix.set_selected(noms.index("PROD"))
    dialogue.emit("response", "appliquer")
    pomper()
    relu = _relire(dossier).store
    assert relu.find("PROD/web").port == 2200 and relu.find("PROD/db").port == 2200
    assert relu.find("LAB/web") is None
    assert [n.chemin for n in arbre.noeuds_selectionnes()] == ["PROD/db", "PROD/web"]
    assert messages == ["Hosts updated: 2"]


def test_conflit_de_noms_refuse(app_fixture, pomper):
    """PROD/web existe : déplacer LAB/web vers PROD est refusé avant toute écriture."""
    arbre, dossier = _arbre(app_fixture)
    arbre.store.add(Host("web", group="PROD", host="h", user="u", protocol="ssh"))
    arbre.recharger()
    avant = (dossier / "gcm4.conf").read_bytes()
    dialogue, (_case_port, _port, case_groupe, choix, erreur) = _ouvrir_dialogue(
        arbre, ["LAB/web", "LAB/bases/db"], pomper
    )
    case_groupe.set_active(True)
    noms = [choix.get_model().get_string(i) for i in range(choix.get_model().get_n_items())]
    choix.set_selected(noms.index("PROD"))
    assert erreur.get_visible() and "PROD/web" in erreur.get_label()
    assert not dialogue.get_response_enabled("appliquer")
    dialogue.emit("response", "appliquer")
    pomper()
    assert (dossier / "gcm4.conf").read_bytes() == avant
    assert arbre.store.find("LAB/web") is not None


def test_annuler_ne_change_rien(app_fixture, pomper):
    """Annuler : fichier inchangé."""
    arbre, dossier = _arbre(app_fixture)
    avant = (dossier / "gcm4.conf").read_bytes()
    dialogue, (case_port, port, *_reste) = _ouvrir_dialogue(
        arbre, ["LAB/web", "LAB/bases/db"], pomper
    )
    case_port.set_active(True)
    port.set_text("2200")
    dialogue.emit("response", "annuler")
    pomper()
    assert (dossier / "gcm4.conf").read_bytes() == avant
