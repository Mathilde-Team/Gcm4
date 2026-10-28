# SPDX-License-Identifier: GPL-3.0-or-later
"""Modèle sans GTK de la base GTK4 (#136)."""

from __future__ import annotations

import random

import pytest

from gcm4.core.model import ConflitDeNom, Group, Host, HostStore, normaliser_chemin, parent


@pytest.fixture
def store():
    """Magasin : LAB/web, LAB/db, PROD/eu/api, racine/local."""
    s = HostStore()
    for nom, groupe, adresse, desc in (
        ("web", "LAB", "10.0.0.1", "Serveur Web"),
        ("db", "LAB", "10.0.0.2", "Base PostgreSQL"),
        ("api", "PROD/eu", "api.example.org", "Réseau européen"),
        ("local", "", "", ""),
    ):
        s.add(Host(nom, group=groupe, host=adresse, description=desc))
    return s


def test_chemins():
    """Normalisation des chemins de groupe et parent."""
    assert normaliser_chemin(" a//b/ ") == "a/b"
    assert normaliser_chemin(None) == ""
    assert parent("a/b/c") == "a/b" and parent("a") == ""
    assert Host("x", group="/A//B/").group == "A/B"
    assert Group("A/B").name == "B"


def test_aucun_protocole_par_defaut():
    """Neutralité : le protocole par défaut est vide, c'est le plugin qui décide."""
    h = Host("x")
    assert h.protocol == "" and h.port is None and h.protocol_options == {}


def test_options_de_protocole_conservees_et_clonees():
    """Options inconnues gardées telles quelles ; le clone est indépendant."""
    h = Host("x", protocol="proto-a", protocol_options={"proto-a": {"x11": "True"}})
    h.options("proto-b")["cle"] = "valeur"
    copie = h.clone()
    copie.options("proto-a")["x11"] = "False"
    assert h.protocol_options == {"proto-a": {"x11": "True"}, "proto-b": {"cle": "valeur"}}


def test_ajout_cree_les_groupes_parents(store):
    """Ajouter PROD/eu/api crée PROD et PROD/eu."""
    assert {"LAB", "PROD", "PROD/eu"} <= set(store.groups)
    assert [g.path for g in store.subgroups()] == ["LAB", "PROD"]
    assert [g.path for g in store.subgroups("PROD")] == ["PROD/eu"]


def test_nom_deja_pris_ou_vide(store):
    """Même nom dans le même groupe refusé ; même nom ailleurs accepté ; nom vide refusé."""
    with pytest.raises(ConflitDeNom):
        store.add(Host("web", group="LAB"))
    store.add(Host("web", group="PROD"))
    with pytest.raises(ValueError):
        store.add(Host("  "))


def test_find_et_path(store):
    """find('groupe/nom') ; path d'un hôte de la racine sans préfixe."""
    assert store.find("PROD/eu/api").host == "api.example.org"
    assert store.find("local").path == "local"
    assert store.find("LAB/absent") is None


def test_renommer_deplacer_supprimer(store):
    """Renommage avec conflit, déplacement vers un groupe créé, suppression."""
    web = store.find("LAB/web")
    with pytest.raises(ConflitDeNom):
        store.rename(web, "db")
    store.rename(web, "www")
    store.move(web, "NOUVEAU/sous")
    assert store.find("NOUVEAU/sous/www") is web and "NOUVEAU" in store.groups
    store.remove(web)
    assert store.find("NOUVEAU/sous/www") is None and "NOUVEAU/sous" in store.groups


def test_dupliquer(store):
    """Duplication : « nom (2) », puis « nom (3) », copie indépendante."""
    web = store.find("LAB/web")
    d2, d3 = store.duplicate(web), store.duplicate(web)
    assert (d2.name, d3.name) == ("web (2)", "web (3)")
    d2.host = "autre"
    assert web.host == "10.0.0.1"


def test_hosts_in(store):
    """Contenu direct ou récursif, trié."""
    assert [h.name for h in store.hosts_in("LAB")] == ["db", "web"]
    assert [h.path for h in store.hosts_in("PROD")] == []
    assert [h.path for h in store.hosts_in("PROD", recursive=True)] == ["PROD/eu/api"]
    assert len(store.hosts_in("", recursive=True)) == 4


def test_recherche_casse_et_accents(store):
    """« reseau » trouve « Réseau européen » ; « WEB » trouve web ; adresse ; vide = tout."""
    assert [h.name for h in store.search("reseau")] == ["api"]
    assert [h.name for h in store.search("WEB")] == ["web"]
    assert [h.name for h in store.search("10.0.0.2")] == ["db"]
    assert len(store.search("  ")) == 4


def test_renommer_un_groupe_deplace_tout(store):
    """PROD → ARCHIVE/PROD : sous-groupes et hôtes suivent, parent créé."""
    store.rename_group("PROD", "ARCHIVE/PROD")
    assert store.find("ARCHIVE/PROD/eu/api") is not None
    assert {"ARCHIVE", "ARCHIVE/PROD", "ARCHIVE/PROD/eu"} <= set(store.groups)
    assert "PROD" not in store.groups and "PROD/eu" not in store.groups


def test_renommer_un_groupe_refus(store):
    """Groupe absent, cible existante, cible sous lui-même ou racine : refusés."""
    with pytest.raises(KeyError):
        store.rename_group("ABSENT", "X")
    with pytest.raises(ConflitDeNom):
        store.rename_group("PROD", "LAB")
    for cible in ("PROD/eu/x", "PROD", ""):
        with pytest.raises(ValueError):
            store.rename_group("PROD", cible)


def test_supprimer_un_groupe(store):
    """Non vide refusé ; avec_contenu retire hôtes et sous-groupes ; vide accepté."""
    with pytest.raises(ValueError):
        store.remove_group("PROD")
    retires = store.remove_group("PROD", avec_contenu=True)
    assert [h.name for h in retires] == ["api"]
    assert "PROD/eu" not in store.groups and store.find("PROD/eu/api") is None
    store.ensure_group("VIDE")
    assert store.remove_group("VIDE") == []
    with pytest.raises(KeyError):
        store.remove_group("VIDE")


def test_aucun_hote_perdu_sur_deplacements_de_groupes():
    """Propriété : 300 déplacements aléatoires de groupes et d'hôtes ne perdent ni ne dupliquent aucun hôte."""
    alea = random.Random(20261012)
    s = HostStore()
    for i in range(40):
        s.add(Host(f"h{i}", group="/".join(alea.choice("ABC") for _ in range(alea.randint(0, 3)))))
    identites = {id(h) for h in s.hosts}
    for _ in range(300):
        if alea.random() < 0.5 and s.groups:
            source = alea.choice(sorted(s.groups))
            cible = "/".join(alea.choice("ABCDE") for _ in range(alea.randint(1, 3)))
            try:
                s.rename_group(source, cible)
            except (ValueError, KeyError):
                pass
        else:
            h = alea.choice(s.hosts)
            try:
                s.move(h, "/".join(alea.choice("ABCDE") for _ in range(alea.randint(0, 3))))
            except ConflitDeNom:
                pass
        assert {id(h) for h in s.hosts} == identites
        assert len({h.path for h in s.hosts}) == len(s.hosts)
        assert all(not h.group or h.group in s.groups for h in s.hosts)


def test_nom_de_copie(store):
    """Premier nom libre ; « web (2) » pris → « web (3) » ; rien n'est ajouté."""
    web = store.find("LAB/web")
    assert store.nom_de_copie(web) == "web (2)"
    store.add(Host("web (2)", group="LAB"))
    assert store.nom_de_copie(web) == "web (3)"
    assert len(store.hosts) == 5


def test_replace_garde_le_rang(store):
    """Remplacement en place, même rang ; changement de groupe créé au besoin."""
    web = store.find("LAB/web")
    rang = store.hosts.index(web)
    nouveau = web.clone()
    nouveau.name, nouveau.group = "front", " NEUF/x "
    assert store.replace(web, nouveau) is nouveau
    assert store.hosts[rang] is nouveau and nouveau.group == "NEUF/x" and "NEUF/x" in store.groups
    assert store.find("LAB/web") is None


def test_replace_refus(store):
    """Nom pris par un autre hôte → ConflitDeNom ; original absent → ValueError ; rien ne change."""
    web, db = store.find("LAB/web"), store.find("LAB/db")
    avant = list(store.hosts)
    copie = web.clone()
    copie.name = "db"
    with pytest.raises(ConflitDeNom):
        store.replace(web, copie)
    with pytest.raises(ValueError):
        store.replace(Host("fantome"), copie)
    assert store.hosts == avant and store.find("LAB/db") is db
    meme = web.clone()
    meme.description = "modifié"
    store.replace(web, meme)  # même nom, même groupe : accepté
    assert store.find("LAB/web").description == "modifié"
