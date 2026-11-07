# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique sans GTK de l'éditeur de ``~/.ssh/config`` (``plugins/ssh/core.py``, #97)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def core():
    """``plugins/ssh/core.py`` chargé hors registre."""
    spec = importlib.util.spec_from_file_location(
        "ssh_core_editeur_test", RACINE / "plugins" / "ssh" / "core.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


def _hote(core, texte: str):
    """Premier hôte du texte ``ssh_config``."""
    return core.config_depuis_texte(texte).hosts[0]


def test_redirections_lues_dans_l_ordre(core):
    """DynamicForward, puis LocalForward, puis RemoteForward ; valeurs atypiques gardées."""
    hote = _hote(
        core,
        "Host a\n  LocalForward 8080 web:80\n  DynamicForward 1080\n"
        "  RemoteForward 9000 localhost:22\n  LocalForward bizarre\n",
    )
    lues = core.redirections_de(hote)
    assert [(r.type, r.local, r.hote, r.distant) for r in lues] == [
        ("dynamic", "1080", "", ""),
        ("local", "8080", "web", "80"),
        ("local", "bizarre", "", ""),
        ("remote", "9000", "localhost", "22"),
    ]


def test_redirection_sans_deux_points(core):
    """Cible sans port (socket) : hôte seul, valeur brute intacte."""
    hote = _hote(core, "Host a\n  LocalForward 8080 socket\n")
    (redirection,) = core.redirections_de(hote)
    assert (redirection.hote, redirection.distant, redirection.brut) == (
        "socket",
        "",
        "8080 socket",
    )


def test_appliquer_redirections(core):
    """Les trois directives sont réécrites depuis la liste."""
    hote = _hote(core, "Host a\n  HostName h\n  LocalForward 1 x:1\n")
    core.appliquer_redirections(
        hote,
        [
            core.nouvelle_redirection("dynamic", 1080),
            core.nouvelle_redirection("remote", 9000, " localhost ", 22),
        ],
    )
    assert hote.get_options("LocalForward") == []
    assert hote.get_options("DynamicForward") == ["1080"]
    assert hote.get_options("RemoteForward") == ["9000 localhost:22"]


def test_nouvelle_redirection_sans_hote(core):
    """Hôte cible vide hors SOCKS : refus."""
    with pytest.raises(ValueError):
        core.nouvelle_redirection("local", 8080, "  ", 80)


def test_valeurs_formulaire(core):
    """Un champ par directive ; IdentityFile multiple joint par des virgules."""
    hote = _hote(core, "Host a b\n  HostName h\n  IdentityFile ~/k1\n  IdentityFile ~/k2\n")
    valeurs = core.valeurs_formulaire(hote)
    assert set(valeurs) == set(core.CHAMPS_EDITEUR)
    assert valeurs["Host"] == "a b"
    assert valeurs["IdentityFile"] == "~/k1, ~/k2"
    assert valeurs["Port"] == ""


@pytest.mark.parametrize(
    ("valeurs", "attendu"),
    [
        ({"Host": "a", "Port": "22"}, {}),
        ({"Host": "a", "Port": ""}, {}),
        ({"Host": "  ", "Port": "0"}, {"Host": "alias", "Port": "port"}),
        ({"Host": "a", "Port": "65536"}, {"Port": "port"}),
        ({"Host": "a", "Port": "abc"}, {"Port": "port"}),
        ({}, {"Host": "alias"}),
    ],
)
def test_erreurs_formulaire(core, valeurs, attendu):
    """Alias obligatoire, port entier de 1 à 65535 ou vide."""
    assert core.erreurs_formulaire(valeurs) == attendu


def test_appliquer_formulaire(core):
    """Alias, champ vidé retiré, port, IdentityFile multiple sur plusieurs lignes."""
    hote = _hote(core, "Host a\n  HostName h\n  User u\n  Port 22\n")
    valeurs = core.valeurs_formulaire(hote)
    valeurs.update(Host="b c", User="", IdentityFile="~/k1, , ~/k2", Port="2222")
    assert core.appliquer_formulaire(hote, valeurs) == {}
    assert hote.patterns == ["b", "c"]
    assert hote.get_option("User") is None
    assert hote.get_option("Port") == "2222"
    assert hote.get_options("IdentityFile") == ["~/k1", "~/k2"]


def test_appliquer_formulaire_garde_les_champs_en_erreur(core):
    """Alias et port invalides gardent leur ancienne valeur ; le reste est écrit."""
    hote = _hote(core, "Host a\n  Port 22\n")
    valeurs = core.valeurs_formulaire(hote)
    valeurs.update(Host="", Port="99999", HostName="h")
    assert core.appliquer_formulaire(hote, valeurs) == {"Host": "alias", "Port": "port"}
    assert hote.patterns == ["a"]
    assert hote.get_option("Port") == "22"
    assert hote.get_option("HostName") == "h"


@pytest.mark.parametrize(
    ("texte", "commande"),
    [
        ("Host a\n  HostName h\n  User u\n  Port 2222\n", "ssh -p 2222 u@h"),
        ("Host a\n", "ssh -p 22 a"),
    ],
)
def test_commande_ssh(core, texte, commande):
    """Commande équivalente au bloc, port 22 et alias par défaut."""
    assert core.commande_ssh(_hote(core, texte)) == commande


def test_commande_ssh_sans_alias(core):
    """Bloc sans alias : cible vide, pas d'exception."""
    assert core.commande_ssh(core.SSHHost()) == "ssh -p 22 "


def test_commande_test(core):
    """Test non interactif, 5 s, verbeux."""
    assert core.commande_test("a") == [
        "ssh",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "BatchMode=yes",
        "-v",
        "a",
    ]


def test_lignes_diff(core):
    """Diff vide sans changement ; en-têtes et lignes +/- sinon."""
    assert core.lignes_diff(["Host a"], "Host a\n") == []
    diff = core.lignes_diff(["Host a"], "Host b\n")
    assert diff[0].startswith("--- original") and "-Host a\n" in diff and "+Host b\n" in diff


def test_config_depuis_texte(core):
    """Le texte est analysé comme un fichier, lignes d'origine comprises."""
    config = core.config_depuis_texte("Compression yes\nHost a\n  HostName h\n")
    assert [h.patterns for h in config.hosts] == [["a"]]
    assert config.original_lines == ["Compression yes", "Host a", "  HostName h"]
