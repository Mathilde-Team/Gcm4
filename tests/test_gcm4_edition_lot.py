"""Édition de plusieurs hôtes en une fois, sans GTK (#100)."""

from __future__ import annotations

import pytest

from gcm4.core.edition_lot import EditionLot, LotInvalide, appliquer, conflits, hotes_de
from gcm4.core.model import Host, HostStore


@pytest.fixture
def store():
    """LAB/{web, db}, PROD/{web, api}, local (racine)."""
    s = HostStore()
    for nom, groupe, port in (
        ("web", "LAB", 22),
        ("db", "LAB", 2222),
        ("web", "PROD", None),
        ("api", "PROD", 22),
        ("local", "", None),
    ):
        s.add(Host(nom, group=groupe, host="h", user="u", protocol="ssh", port=port))
    return s


def test_lot_vide():
    """Aucune case cochée : le lot ne change rien."""
    assert EditionLot().vide
    assert not EditionLot(changer_port=True).vide
    assert not EditionLot(groupe="").vide


def test_hotes_de_ignore_groupes_inconnus_et_doublons(store):
    """Ordre conservé ; groupes, chemins absents et doublons ignorés."""
    hotes = hotes_de(store, ["PROD/api", "LAB", "absent", "LAB/web", "PROD/api"])
    assert [h.path for h in hotes] == ["PROD/api", "LAB/web"]


def test_changer_le_port(store):
    """Le port est appliqué à chaque hôte ; seuls les hôtes changés sont comptés."""
    hotes = hotes_de(store, ["LAB/web", "LAB/db", "PROD/api"])
    assert appliquer(store, hotes, EditionLot(changer_port=True, port=22)) == 1
    assert [h.port for h in hotes] == [22, 22, 22]


def test_port_par_defaut(store):
    """``port=None`` : port par défaut du protocole."""
    hotes = hotes_de(store, ["LAB/web", "LAB/db"])
    assert appliquer(store, hotes, EditionLot(changer_port=True, port=None)) == 2
    assert [h.port for h in hotes] == [None, None]


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_port_hors_bornes(store, port):
    """Port hors 1–65535 : lot refusé, rien n'est modifié."""
    hotes = hotes_de(store, ["LAB/web"])
    with pytest.raises(LotInvalide):
        appliquer(store, hotes, EditionLot(changer_port=True, port=port))
    assert hotes[0].port == 22


def test_port_inchange_si_case_decochee(store):
    """``changer_port`` faux : le port saisi n'est pas appliqué."""
    hotes = hotes_de(store, ["LAB/db"])
    assert appliquer(store, hotes, EditionLot(port=80)) == 0
    assert hotes[0].port == 2222


def test_deplacer_vers_un_groupe(store):
    """Déplacement vers un groupe existant ou nouveau (créé au besoin)."""
    hotes = hotes_de(store, ["LAB/db", "PROD/api"])
    assert appliquer(store, hotes, EditionLot(groupe="ARCHIVE/2026")) == 2
    assert [h.path for h in hotes] == ["ARCHIVE/2026/db", "ARCHIVE/2026/api"]
    assert "ARCHIVE/2026" in store.groups


def test_deplacer_vers_la_racine(store):
    """``groupe=""`` : racine."""
    hotes = hotes_de(store, ["LAB/db"])
    appliquer(store, hotes, EditionLot(groupe=""))
    assert hotes[0].path == "db"


def test_hote_deja_dans_le_groupe_cible(store):
    """Un hôte déjà dans le groupe cible n'est ni un conflit ni un hôte modifié."""
    hotes = hotes_de(store, ["PROD/api", "LAB/db"])
    assert conflits(store, hotes, EditionLot(groupe="PROD")) == []
    assert appliquer(store, hotes, EditionLot(groupe="PROD")) == 1


def test_conflit_avec_un_hote_hors_selection(store):
    """LAB/web vers PROD : PROD/web existe déjà, tout le lot est refusé."""
    hotes = hotes_de(store, ["LAB/web", "LAB/db"])
    edition = EditionLot(changer_port=True, port=80, groupe="PROD")
    assert conflits(store, hotes, edition) == ["PROD/web"]
    with pytest.raises(LotInvalide, match="PROD/web"):
        appliquer(store, hotes, edition)
    assert [h.path for h in hotes] == ["LAB/web", "LAB/db"]
    assert [h.port for h in hotes] == [22, 2222]


def test_conflit_entre_hotes_selectionnes(store):
    """LAB/web et PROD/web vers la racine : deux « web », refusé."""
    hotes = hotes_de(store, ["LAB/web", "PROD/web"])
    assert conflits(store, hotes, EditionLot(groupe="/")) == ["web"]


def test_sans_deplacement_aucun_conflit(store):
    """Sans groupe demandé, deux hôtes de même nom peuvent changer de port ensemble."""
    hotes = hotes_de(store, ["LAB/web", "PROD/web"])
    assert conflits(store, hotes, EditionLot(changer_port=True, port=2200)) == []
    assert appliquer(store, hotes, EditionLot(changer_port=True, port=2200)) == 2
