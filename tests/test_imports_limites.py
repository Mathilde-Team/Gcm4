# SPDX-License-Identifier: GPL-3.0-or-later
"""Routes de secours et inventaires incomplets des imports hyperviseurs (#293)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from doublures_ssh import Hyperviseur

from plugins.import_ovirt import core as ov
from plugins.import_virtualbox import core as vb


@pytest.mark.parametrize(
    "erreur",
    [
        HTTPError("https://exemple.invalid", 503, "indisponible", {}, None),
        URLError("refus"),
        ValueError("JSON invalide"),
    ],
)
def test_ovirt_api_erreur_sans_journal_optionnel(monkeypatch, erreur):
    """Une panne reste tracée par loguru même sans callback de progression."""

    def refuser(*_args, **_kwargs):
        """Simule une erreur de transport ou de décodage, sans requête réelle."""
        raise erreur

    monkeypatch.setattr(ov, "urlopen", refuser)
    assert ov.api_get("https://exemple.invalid", "u", "", True, "/vms") is None


@pytest.mark.parametrize("module", [ov, vb])
def test_chargeur_hyperviseur_a_froid(monkeypatch, module):
    """Le chargement initial du paquet partagé crée puis réutilise son identité."""
    with monkeypatch.context() as contexte:
        contexte.delitem(sys.modules, "gcm4_hv", raising=False)
        charge = module.partage()
        assert charge is sys.modules["gcm4_hv"]
        assert module.partage() is charge


def test_route_manuelle_sans_cible():
    """Un rebond sans IP de repli ne fabrique pas une destination vide."""
    assert ov.interpreter_route_manuelle("-J utilisateur@rebond", "") is None


@pytest.mark.parametrize("moteur", ["", "https://moteur.invalid/api"])
@pytest.mark.parametrize("connexion", [False, True])
@pytest.mark.parametrize("manuelle", [None, "", ":"])
def test_routes_ovirt_echouees_ferment_les_clients(monkeypatch, moteur, connexion, manuelle):
    """Les rebonds qui ne voient pas la VM sont fermés avant le secours manuel."""
    clients = []
    journal = []

    def connecter(*_args):
        """Un vrai cycle de vie de doublure par tentative de rebond."""
        if not connexion:
            return None
        client = SimpleNamespace(ferme=False)
        client.close = lambda: setattr(client, "ferme", True)
        clients.append(client)
        return client

    monkeypatch.setattr(ov, "sonder_tcp_direct", lambda *_a, **_k: False)
    monkeypatch.setattr(ov, "adresse_hote", lambda *_a, **_k: "hv.invalid")
    monkeypatch.setattr(ov.hv_common, "paramiko_connect", connecter)
    monkeypatch.setattr(ov.hv_common, "check_port_open", lambda *_a, **_k: False)
    resultat = ov.resoudre_route(
        "vm",
        "192.0.2.1",
        moteur,
        "u",
        "",
        True,
        "u",
        "h1",
        journal.append,
        lambda _nom: manuelle,
    )
    assert resultat is None
    assert all(client.ferme for client in clients)
    assert len(clients) == (int(bool(moteur)) + 1 if connexion else 0)
    assert any("hyperviseur" in message for message in journal)


@pytest.mark.parametrize(
    "inventaire",
    [
        {},
        {
            "vm": {
                "name": "linux",
                "status": {"#text": "up"},
                "os": "linux",
                "guest_info": "absent",
                "host": "absent",
            }
        },
        {"vm": {"name": "linux", "guest_info": {"ips": "absentes"}}},
        {"vm": {"name": "linux", "guest_info": {"ips": {"ip": {"address": "192.0.2.1"}}}}},
        {
            "vm": {
                "name": "linux",
                "guest_info": {
                    "ips": {"ip": ["invalide", {"address": "127.0.0.1"}, {"address": "::1"}]}
                },
            }
        },
    ],
)
def test_inventaire_ovirt_degrade(monkeypatch, inventaire):
    """Métadonnées atypiques, VM unique et absence d'IPv4 n'inventent aucun hôte."""
    monkeypatch.setattr(ov, "api_get", lambda *_a, **_k: inventaire)
    monkeypatch.setattr(
        ov,
        "resoudre_route",
        lambda *_a, **_k: {"mode": "direct", "host": "192.0.2.1", "extra_params": ""},
    )
    journal, progression = [], []
    assert (
        ov.collecter_hotes(
            "https://moteur.invalid/api",
            "u",
            "",
            True,
            "u",
            journal.append,
            lambda *etape: progression.append(etape),
            set(),
        )
        == []
    )
    assert progression


@pytest.mark.parametrize("cible", ["@", "u@", "@:2222"])
def test_virtualbox_cible_sans_hote(cible):
    """Un utilisateur ou port sans hôte n'est pas transformé en URI utilisable."""
    assert vb.normaliser_cible(cible, "u") == ""


def test_virtualbox_ignore_lignes_sans_paire():
    """Le parseur conserve les valeurs contenant '=' et ignore le bruit."""
    assert vb.parser_machinereadable('bruit\n\nname="a=b"\n') == {"name": "a=b"}


def test_virtualbox_paramiko_absent(monkeypatch):
    """L'absence du transport produit une explication sans tentative de collecte."""
    monkeypatch.setattr(vb.hv_common, "PARAMIKO_OK", False)
    journal = []
    assert vb.collecter_hotes([], "u", journal.append, lambda *_a: None) == []
    assert any("paramiko" in message for message in journal)


@pytest.mark.parametrize("ip", ["Value: 192.0.2.1", "No value set!"])
def test_virtualbox_ports_fermes_et_arp_bruyant(monkeypatch, ip):
    """Un port fermé ou une IP inconnue ne génère pas de faux accès RDP/VNC."""
    double = Hyperviseur(
        {
            "which VBoxManage": "/usr/bin/VBoxManage",
            "list vms": '"vm-linux" {abc}\n',
            "list runningvms": '"vm-linux" {abc}\n',
            "showvminfo": 'name="vm-linux"\nvrde="off"\nostype="Linux_64"\n',
            "guestproperty get": ip,
            "ip neigh show": "sortie non exploitable\n",
        },
        ports_ouverts=set(),
    )
    double.installer(monkeypatch)
    journal = []
    assert (
        vb.collecter_hotes(
            ["vbox+ssh://u@h"], "u", journal.append, lambda *_a: None, {"rdp", "vnc"}
        )
        == []
    )
    assert any("aucune connexion" in message for message in journal)


@pytest.mark.parametrize("sortie,attendu", [("OPEN", True), ("CLOSED", False)])
def test_sonde_hyperviseur_netcat(sortie, attendu):
    """Le résultat explicite de netcat évite un second sondage nmap."""
    appels = []

    def executer(_client, commande, **_kwargs):
        """Capture la seule commande envoyée à la doublure SSH."""
        appels.append(commande)
        return sortie

    assert ov.hv_common.check_port_open(None, executer, "192.0.2.1", 22) is attendu
    assert len(appels) == 1 and appels[0].startswith("nc ")
    assert ov.hv_common.check_port_open(None, executer, "", 22) is False
    assert len(appels) == 1
