# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation et application des glisser-déposer, sans GTK (#141)."""

from __future__ import annotations

import random

import pytest

from gcm4.core.deplacements import Deplacement, Refus, appliquer, cle, groupe_cible, valider
from gcm4.core.model import Host, HostStore


@pytest.fixture
def store():
    """LAB/{web, bases/db}, PROD/eu/api, local (racine), ARCHIVE vide."""
    s = HostStore()
    for nom, groupe in (("web", "LAB"), ("db", "LAB/bases"), ("api", "PROD/eu"), ("local", "")):
        s.add(Host(nom, group=groupe, host="h", user="u", protocol="p"))
    s.ensure_group("ARCHIVE")
    return s


def H(c):
    """Clé d'hôte."""
    return cle(c, False)


def G(c):
    """Clé de groupe."""
    return cle(c, True)


def test_groupe_cible(store):
    """Groupe → lui-même ; hôte → son groupe ; zone vide → racine ; disparu → None."""
    assert groupe_cible(store, G("LAB/bases")) == "LAB/bases"
    assert groupe_cible(store, H("LAB/web")) == "LAB"
    assert groupe_cible(store, H("local")) == ""
    assert groupe_cible(store, None) == ""
    assert groupe_cible(store, G("absent")) is None and groupe_cible(store, H("absent")) is None


def test_hote_vers_groupe_ou_hote(store):
    """Sur un groupe ou sur un hôte de ce groupe : même résultat."""
    assert valider(store, H("LAB/web"), G("ARCHIVE")) == Deplacement(H("LAB/web"), "ARCHIVE")
    assert valider(store, H("LAB/web"), H("PROD/eu/api")) == Deplacement(H("LAB/web"), "PROD/eu")
    assert valider(store, H("LAB/web"), None).nouveau_chemin == "web"


def test_refus_hote(store):
    """Même groupe, nom pris à l'arrivée, source disparue."""
    assert valider(store, H("LAB/web"), G("LAB")) is Refus.MEME_GROUPE
    assert valider(store, H("LAB/web"), H("LAB/web")) is Refus.MEME_GROUPE
    store.add(Host("web", group="ARCHIVE", host="h", user="u", protocol="p"))
    assert valider(store, H("LAB/web"), G("ARCHIVE")) is Refus.NOM_PRIS
    assert valider(store, H("absent"), G("LAB")) is Refus.INTROUVABLE
    assert valider(store, "x:LAB", G("LAB")) is Refus.INTROUVABLE


def test_groupe_vers_groupe_et_racine(store):
    """Groupe dans un autre ; remonté à la racine par un hôte racine ou la zone vide."""
    assert valider(store, G("LAB/bases"), G("PROD")).nouveau_chemin == "PROD/bases"
    assert valider(store, G("LAB/bases"), H("local")).nouveau_chemin == "bases"
    assert valider(store, G("LAB/bases"), None).nouveau_chemin == "bases"


def test_refus_groupe(store):
    """Dans lui-même, dans un descendant, même parent, chemin pris, disparu."""
    assert valider(store, G("LAB"), G("LAB")) is Refus.DANS_LUI_MEME
    assert valider(store, G("LAB"), G("LAB/bases")) is Refus.DANS_LUI_MEME
    assert valider(store, G("LAB"), H("LAB/bases/db")) is Refus.DANS_LUI_MEME
    assert valider(store, G("LAB/bases"), G("LAB")) is Refus.MEME_GROUPE
    assert valider(store, G("LAB"), None) is Refus.MEME_GROUPE
    store.ensure_group("PROD/bases")
    assert valider(store, G("LAB/bases"), G("PROD")) is Refus.NOM_PRIS
    assert valider(store, G("absent"), G("PROD")) is Refus.INTROUVABLE


def test_appliquer_puis_annuler(store):
    """Groupe avec contenu et couleur déplacé ; l'inverse rétablit tout."""
    store.groups["LAB/bases"].color = "#123456"
    store.groups["LAB/bases"].collapsed = True
    avant = sorted(h.path for h in store.hosts), sorted(store.groups)
    inverse = appliquer(store, valider(store, G("LAB/bases"), G("PROD/eu")))
    assert store.find("PROD/eu/bases/db") is not None
    assert (
        store.groups["PROD/eu/bases"].color == "#123456"
        and store.groups["PROD/eu/bases"].collapsed
    )
    appliquer(store, inverse)
    assert (sorted(h.path for h in store.hosts), sorted(store.groups)) == avant
    inverse = appliquer(store, valider(store, H("local"), G("ARCHIVE")))
    assert store.find("ARCHIVE/local") is not None
    appliquer(store, inverse)
    assert store.find("local") is not None


def test_appliquer_store_modifie(store):
    """Hôte disparu entre la validation et l'application : KeyError."""
    d = valider(store, H("local"), G("ARCHIVE"))
    store.remove(store.find("local"))
    with pytest.raises(KeyError):
        appliquer(store, d)


def test_serie_aleatoire_ni_perte_ni_doublon():
    """2000 dépôts aléatoires : mêmes hôtes (identités), chemins uniques, groupes cohérents."""
    alea = random.Random(141)
    s = HostStore()
    for i in range(30):
        groupe = "/".join(f"g{alea.randrange(4)}" for _ in range(alea.randrange(4)))
        s.add(Host(f"h{i}", group=groupe, host="h", user="u", protocol="p"))
    identites = {id(h) for h in s.hosts}
    appliques = 0
    for _ in range(2000):
        elements = [G(g) for g in s.groups] + [H(h.path) for h in s.hosts]
        source = alea.choice(elements)
        cible = alea.choice([*elements, None])
        d = valider(s, source, cible)
        if isinstance(d, Deplacement):
            appliquer(s, d)
            appliques += 1
        assert {id(h) for h in s.hosts} == identites
        chemins = [h.path for h in s.hosts]
        assert len(chemins) == len(set(chemins))
        assert all(not h.group or h.group in s.groups for h in s.hosts)
        assert all(g.path == p for p, g in s.groups.items())
    assert appliques > 300
