# SPDX-License-Identifier: GPL-3.0-or-later
"""Menu contextuel de l'arbre sans GTK (#142)."""

from __future__ import annotations

import pytest

import folder_context_menu_core
from gcm4.core import menu_arbre
from gcm4.core.host_tree import construire
from gcm4.core.model import Host, HostStore


@pytest.fixture
def store():
    """LAB/{web, bases/db}, local (racine), ARCHIVE vide."""
    s = HostStore()
    for nom, groupe in (("web", "LAB"), ("db", "LAB/bases"), ("local", "")):
        s.add(Host(nom, group=groupe, host="10.0.0.1", user="u", protocol="p"))
    s.ensure_group("ARCHIVE")
    return s


def _noeud(store, chemin):
    pile = list(construire(store))
    while pile:
        n = pile.pop()
        if n.chemin == chemin:
            return n
        pile.extend(n.enfants)
    raise KeyError(chemin)


@pytest.mark.parametrize("cible", folder_context_menu_core.CLICK_TARGETS)
def test_parite_avec_gtk3(cible):
    """Disposition portée identique à folder_context_menu_core (tant qu'il existe, #182)."""
    attendu = folder_context_menu_core.build_folder_context_menu_layout(click_target=cible)
    assert menu_arbre.disposition_gtk3(cible) == attendu
    with pytest.raises(ValueError):
        menu_arbre.disposition_gtk3("autre")


def test_sections_selon_la_cible(store):
    """Sans plugin : pas de connect ; racine sans couleur."""
    assert menu_arbre.sections(None) == [["add", "new-group"], ["expand", "collapse"]]
    assert menu_arbre.sections(_noeud(store, "LAB")) == [
        ["add", "new-group", "rename-group", "group-color", "group-color-reset"],
        ["delete"],
        ["expand", "collapse"],
    ]
    assert menu_arbre.sections(_noeud(store, "LAB/web")) == [
        ["copy-address", "add", "new-group", "group-color", "group-color-reset"],
        ["edit", "duplicate", "delete"],
        ["expand", "collapse"],
    ]
    assert menu_arbre.sections(_noeud(store, "local"))[0] == ["copy-address", "add", "new-group"]


def test_connect_seulement_si_un_plugin_gere_le_protocole(store):
    """Connect apparaît pour un hôte quand le registre le permet, jamais pour un groupe."""
    assert (
        menu_arbre.sections(_noeud(store, "LAB/web"), connexion_possible=True)[0][0] == "connect"
    )
    assert "connect" not in menu_arbre.sections(_noeud(store, "LAB"), connexion_possible=True)[0]
    assert all(a in menu_arbre.LIBELLES for s in menu_arbre.disposition_gtk3("host") for a in s)


def test_sections_lot():
    """Plusieurs hôtes sélectionnés : édition en lot ; moins de deux : affichage seul (#100)."""
    assert menu_arbre.sections_lot(3) == [["edit-selection"], ["expand", "collapse"]]
    assert menu_arbre.sections_lot(1) == [["expand", "collapse"]]
    assert all(a in menu_arbre.LIBELLES for s in menu_arbre.sections_lot(2) for a in s)


def test_groupe_de_travail(store):
    """Groupe cliqué, groupe de l'hôte, racine."""
    assert menu_arbre.groupe_de_travail(_noeud(store, "LAB/bases")) == "LAB/bases"
    assert menu_arbre.groupe_de_travail(_noeud(store, "LAB/bases/db")) == "LAB/bases"
    assert menu_arbre.groupe_de_travail(_noeud(store, "local")) == ""
    assert menu_arbre.groupe_de_travail(None) == ""


def test_decompte(store):
    """Hôtes récursifs et sous-groupes ; groupe vide."""
    assert menu_arbre.decompte(store, "LAB") == menu_arbre.Decompte(hotes=2, sous_groupes=1)
    assert menu_arbre.decompte(store, "ARCHIVE").vide
    assert not menu_arbre.decompte(store, "LAB").vide


def test_chemin_de_groupe(store):
    """Nom nettoyé ; vide, « / » ou pris refusés ; renommer en soi-même accepté."""
    assert menu_arbre.chemin_de_groupe(store, "LAB", "  neuf ") == "LAB/neuf"
    assert menu_arbre.chemin_de_groupe(store, "", "PROD") == "PROD"
    for nom in ("", "   ", "a/b"):
        assert menu_arbre.chemin_de_groupe(store, "LAB", nom) is None
    assert menu_arbre.chemin_de_groupe(store, "LAB", "bases") is None
    assert menu_arbre.chemin_de_groupe(store, "LAB", "bases", actuel="LAB/bases") == "LAB/bases"
