# SPDX-License-Identifier: GPL-3.0-or-later
"""Construction de l'arbre des hôtes sans GTK (#139)."""

from __future__ import annotations

import locale

import pytest

from gcm4.core import host_tree
from gcm4.core.host_tree import ancetres, cle_de_tri, construire, couleur_effective, infobulle
from gcm4.core.model import Host, HostStore


@pytest.fixture
def store():
    """Groupes imbriqués et colorés, hôtes en désordre, un hôte sans groupe."""
    s = HostStore()
    for nom, groupe in (
        ("web", "LAB"),
        ("Alpha", "LAB"),
        ("charlie", "LAB"),
        ("db", "LAB/bases"),
        ("api", "PROD/eu"),
        ("local", ""),
    ):
        s.add(Host(nom, group=groupe, host="h", user="u", protocol="proto-a"))
    s.ensure_group("ARCHIVE")
    s.groups["LAB"].color = "#ff8800"
    s.groups["PROD/eu"].color = "#00AAFF"
    return s


def _arbre(noeuds, niveau=0):
    lignes = []
    for n in noeuds:
        lignes.append("  " * niveau + ("[" + n.nom + "]" if n.est_groupe else n.nom))
        lignes += _arbre(n.enfants, niveau + 1)
    return lignes


def test_groupes_puis_hotes_tries(store):
    """Groupes d'abord, hôtes ensuite, ordre alphabétique sans casse ; hôte sans groupe à la racine."""
    assert _arbre(construire(store)) == [
        "[ARCHIVE]",
        "[LAB]",
        "  [bases]",
        "    db",
        "  Alpha",
        "  charlie",
        "  web",
        "[PROD]",
        "  [eu]",
        "    api",
        "local",
    ]


def test_couleurs_heritees(store):
    """Couleur héritée de l'ancêtre coloré le plus proche ; hôte = couleur de son groupe."""
    noeuds = {n.chemin: n for n in _tous(construire(store))}
    assert noeuds["LAB"].classe_css == "gcm-couleur-ff8800"
    assert noeuds["LAB/bases"].couleur == "#ff8800"
    assert noeuds["LAB/bases/db"].classe_css == "gcm-couleur-ff8800"
    assert noeuds["PROD"].classe_css is None
    assert noeuds["PROD/eu/api"].classe_css == "gcm-couleur-00aaff"
    assert noeuds["local"].classe_css is None and noeuds["ARCHIVE"].couleur == ""
    assert couleur_effective("", {"A": "#fff"}) == ""


def _tous(noeuds):
    for n in noeuds:
        yield n
        yield from _tous(n.enfants)


def test_couleur_invalide_sans_classe():
    """Une couleur invalide dans gcm.conf ne produit aucune classe CSS."""
    s = HostStore()
    s.add(Host("x", group="G"))
    s.groups["G"].color = "rouge"
    assert construire(s)[0].classe_css is None


def test_infobulle_echappee():
    """Nom en gras, protocole:utilisateur@hôte, description en petit, balisage échappé."""
    h = Host("srv <1>", host="10.0.0.1", user="root", protocol="proto-a", description="a & b")
    assert infobulle(h) == "<b>srv &lt;1&gt;</b>\nproto-a:root@10.0.0.1\n<small>a &amp; b</small>"
    assert infobulle(Host("y", host="h", user="u")) == "<b>y</b>\nu@h"


def test_ancetres():
    """Parents du plus haut au plus proche."""
    assert ancetres("a/b/c") == ["a", "a/b"]
    assert ancetres("a") == []


def test_tri_selon_la_locale(monkeypatch):
    """Strxfrm est utilisé ; en cas d'échec, repli sur l'ordre des points de code sans casse."""
    assert cle_de_tri("Abc") == locale.strxfrm("abc")

    def echec(_texte):
        raise ValueError("caractère nul")

    monkeypatch.setattr(host_tree.locale, "strxfrm", echec)
    assert cle_de_tri("Abc") == "abc"


def test_correspondances_hotes_et_ancetres(store):
    """Recherche : hôtes trouvés + tous leurs groupes ; groupes sans résultat exclus ; vide : None."""
    assert host_tree.correspondances(store, "  ") is None
    assert host_tree.correspondances(store, "db") == {"LAB", "LAB/bases", "LAB/bases/db"}
    assert host_tree.correspondances(store, "local") == {"local"}
    assert host_tree.correspondances(store, "introuvable") == set()


def test_correspondances_casse_accents_adresse_description():
    """Casse et accents ignorés ; adresse et description cherchées comme le nom."""
    s = HostStore()
    s.add(
        Host(
            "Éclair",
            group="A/B",
            host="srv.exemple",
            user="u",
            protocol="p",
            description="Base Clients",
        )
    )
    attendu = {"A", "A/B", "A/B/Éclair"}
    for texte in ("eclair", "ÉCLAIR", "exemple", "base cli"):
        assert host_tree.correspondances(s, texte) == attendu, texte
