# SPDX-License-Identifier: GPL-3.0-or-later
"""Groupes repliés au format GTK3 de collapsed-folders (#139)."""

from __future__ import annotations

from gcm4.core.replies import ecrire_replies, gtk3_tree_paths, lire_replies

GROUPES = ["LAB", "LAB/bases", "LAB/vide", "ARCHIVE", "PROD", "PROD/eu", "PROD/eu/fr"]


def test_ordre_de_l_arbre_gtk3():
    """Index Gtk.TreePath tels que Wmain.updateTree les produit (dossiers triés, imbriqués)."""
    assert gtk3_tree_paths(GROUPES) == {
        "ARCHIVE": "0",
        "LAB": "1",
        "LAB/bases": "1:0",
        "LAB/vide": "1:1",
        "PROD": "2",
        "PROD/eu": "2:0",
        "PROD/eu/fr": "2:0:0",
    }


def test_ordre_des_points_de_code_et_dossiers_intermediaires():
    """Ordre de sorted() (majuscules avant minuscules, « - » avant « / ») ; parents implicites créés."""
    assert gtk3_tree_paths(["a", "B", "A-b", "A/c", "X/y/z"]) == {
        "A-b": "0",
        "A": "1",
        "A/c": "1:0",
        "B": "2",
        "X": "3",
        "X/y": "3:0",
        "X/y/z": "3:0:0",
        "a": "4",
    }


def test_racine_ignoree():
    """Le groupe racine « » n'a pas de ligne (updateTree le supprime)."""
    assert gtk3_tree_paths(["", "A"]) == {"A": "0"}


def test_lecture_gtk3():
    """« 1,2:0 » → LAB et PROD/eu ; index inconnu ou vide ignoré."""
    assert lire_replies("1,2:0, 9 ,,", GROUPES) == {"LAB", "PROD/eu"}
    assert lire_replies("", GROUPES) == set()


def test_lecture_noms_de_groupe_rc1():
    """Valeur écrite par gcm4 1.3.4-rc1 (noms de groupe) : encore comprise ; nom inconnu ignoré."""
    assert lire_replies("PROD,LAB/vide,INCONNU", GROUPES) == {"PROD", "LAB/vide"}


def test_ecriture_ordonnee():
    """Écriture au format GTK3, dans l'ordre de l'arbre ; groupe inconnu ignoré."""
    assert ecrire_replies(["PROD/eu/fr", "LAB", "PROD", "INCONNU"], GROUPES) == "1,2,2:0:0"
    assert ecrire_replies([], GROUPES) == ""


def test_aller_retour():
    """Pour tout sous-ensemble, lire(écrire(x)) == x."""
    import itertools

    for n in range(len(GROUPES) + 1):
        for sous in itertools.combinations(GROUPES, n):
            assert lire_replies(ecrire_replies(sous, GROUPES), GROUPES) == set(sous)
