# SPDX-License-Identifier: GPL-3.0-or-later
"""Cœur de l'outil « Import from oVirt » : API, sondes et routes (#439).

``plugins/import_ovirt/core.py`` est testé sans interface : l'API REST
est doublée (``urlopen``), les sondes TCP et SSH aussi. Aucun réseau,
aucun GTK.
"""

from __future__ import annotations

import os
import sys
from urllib.error import HTTPError, URLError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins.import_ovirt import core as ov  # noqa: E402


def _log_vide(message: str) -> None:
    """Log factice sans effet."""


# ── api_get ──────────────────────────────────────────────────────────────────


class ReponseFactice:
    """Réponse HTTP factice : contexte de lecture d'un corps JSON."""

    def __init__(self, corps: bytes):
        """Mémorise le corps binaire de la réponse."""
        self._corps = corps

    def read(self) -> bytes:
        """Rend le corps de la réponse."""
        return self._corps

    def __enter__(self):
        """Entrée du contexte ``with``."""
        return self

    def __exit__(self, *_args):
        """Sortie du contexte ``with``."""
        return False


def test_api_get_succes(monkeypatch):
    """GET authentifié : JSON décodé, Basic auth construite."""
    vues: list = []

    def _urlopen(req, timeout=20, context=None):
        vues.append((req.full_url, req.headers, timeout, context))
        return ReponseFactice(b'{"vm": []}')

    monkeypatch.setattr(ov, "urlopen", _urlopen)
    data = ov.api_get("https://ovirt.example/api", "admin", "secret", True, "/vms")
    assert data == {"vm": []}
    assert vues[0][0] == "https://ovirt.example/api/vms"
    assert vues[0][1]["Authorization"].startswith("Basic ")


def test_api_get_tls_non_verifie(monkeypatch):
    """verify_tls False : un contexte TLS permissif est passé à urlopen."""
    contextes: list = []

    def _urlopen(req, timeout=20, context=None):
        contextes.append(context)
        return ReponseFactice(b"{}")

    monkeypatch.setattr(ov, "urlopen", _urlopen)
    ov.api_get("https://ovirt.example/api", "a", "b", False, "/vms")
    assert contextes[0] is not None
    ov.api_get("https://ovirt.example/api", "a", "b", True, "/vms")
    assert contextes[1] is None


def test_api_get_erreurs(monkeypatch):
    """HTTPError et URLError : None rendu, message passé au journal."""
    journal: list[str] = []

    def _http(req, timeout=20, context=None):
        raise HTTPError(req.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(ov, "urlopen", _http)
    assert ov.api_get("https://ovirt.example/api", "a", "b", True, "/vms", journal.append) is None
    assert any("404" in ligne for ligne in journal)

    def _url(req, timeout=20, context=None):
        raise URLError("refus")

    monkeypatch.setattr(ov, "urlopen", _url)
    journal.clear()
    assert ov.api_get("https://ovirt.example/api", "a", "b", True, "/vms", journal.append) is None
    assert any("connexion" in ligne for ligne in journal)


# ── sonder_tcp_direct / adresse_hote ────────────────────────────────────────


def test_sonder_tcp_direct(monkeypatch):
    """Sonde directe : ouvert, fermé (OSError), hôte vide."""
    monkeypatch.setattr(
        ov.socket, "create_connection", lambda cible, timeout=4: ReponseFactice(b"")
    )
    assert ov.sonder_tcp_direct("10.0.0.1", 22) is True

    def _refuse(cible, timeout=4):
        raise OSError("refusé")

    monkeypatch.setattr(ov.socket, "create_connection", _refuse)
    assert ov.sonder_tcp_direct("10.0.0.1", 22) is False
    assert ov.sonder_tcp_direct("", 22) is False


def test_adresse_hote(monkeypatch):
    """Adresse de l'hyperviseur : address, puis name, sinon vide."""
    monkeypatch.setattr(ov, "api_get", lambda *a, **k: {"address": "10.1.1.1", "name": "hv1"})
    assert ov.adresse_hote("https://e/api", "a", "b", True, "h1", _log_vide) == "10.1.1.1"
    monkeypatch.setattr(ov, "api_get", lambda *a, **k: {"name": "hv1"})
    assert ov.adresse_hote("https://e/api", "a", "b", True, "h1", _log_vide) == "hv1"
    monkeypatch.setattr(ov, "api_get", lambda *a, **k: None)
    assert ov.adresse_hote("https://e/api", "a", "b", True, "h1", _log_vide) == ""
    assert ov.adresse_hote("https://e/api", "a", "b", True, "", _log_vide) == ""


# ── resoudre_route / sonder_port_via_route ──────────────────────────────────


class ClientFaux:
    """Client SSH factice : seul ``close`` compte."""

    def __init__(self):
        """Client ouvert, jamais réellement connecté."""
        self.ferme = False

    def close(self):
        """Ferme le client."""
        self.ferme = True


def test_resoudre_route_jump_moteur(monkeypatch):
    """Direct impossible : rebond via la machine du moteur oVirt."""
    client = ClientFaux()

    def _connecter(hote, port, utilisateur, log_fn=None):
        return client

    monkeypatch.setattr(ov, "sonder_tcp_direct", lambda hote, port, timeout=4: False)
    monkeypatch.setattr(ov.hv_common, "paramiko_connect", _connecter)
    monkeypatch.setattr(ov.hv_common, "check_port_open", lambda c, run, ip, port: True)
    route = ov.resoudre_route(
        "vm", "10.0.0.1", "https://moteur.example/api", "a", "b", True, "root", "h1", _log_vide
    )
    assert route == {
        "mode": "jump",
        "host": "10.0.0.1",
        "extra_params": "-J root@moteur.example",
    }


def test_resoudre_route_jump_hyperviseur(monkeypatch):
    """Moteur injoignable en SSH : rebond via l'hyperviseur hébergeant la VM."""
    client = ClientFaux()

    def _connecter(hote, port, utilisateur, log_fn=None):
        return client if hote == "hv1.example" else None

    monkeypatch.setattr(ov, "sonder_tcp_direct", lambda hote, port, timeout=4: False)
    monkeypatch.setattr(ov.hv_common, "paramiko_connect", _connecter)
    monkeypatch.setattr(ov.hv_common, "check_port_open", lambda c, run, ip, port: True)
    monkeypatch.setattr(ov, "adresse_hote", lambda *a, **k: "hv1.example")
    route = ov.resoudre_route(
        "vm", "10.0.0.1", "https://moteur.example/api", "a", "b", True, "root", "h1", _log_vide
    )
    assert route == {"mode": "jump", "host": "10.0.0.1", "extra_params": "-J root@hv1.example"}


def test_sonder_port_via_route(monkeypatch):
    """Sondage sur route directe (TCP) et sur rebond (SSH + check)."""
    monkeypatch.setattr(ov, "sonder_tcp_direct", lambda hote, port, timeout=4: port == 3389)
    assert ov.sonder_port_via_route(
        {"mode": "direct", "host": "h", "extra_params": ""}, 3389, "root", _log_vide
    )
    assert not ov.sonder_port_via_route(
        {"mode": "direct", "host": "h", "extra_params": ""}, 22, "root", _log_vide
    )

    client = ClientFaux()
    monkeypatch.setattr(ov.hv_common, "paramiko_connect", lambda *a, **k: client)
    monkeypatch.setattr(ov.hv_common, "check_port_open", lambda c, run, ip, port: True)
    assert ov.sonder_port_via_route(
        {"mode": "jump", "host": "10.0.0.1", "extra_params": "-J root@jump"},
        3389,
        "root",
        _log_vide,
    )
    # extra_params sans rebond lisible : sondage impossible
    assert not ov.sonder_port_via_route(
        {"mode": "jump", "host": "10.0.0.1", "extra_params": ""}, 3389, "root", _log_vide
    )
    # Rebond injoignable : False
    monkeypatch.setattr(ov.hv_common, "paramiko_connect", lambda *a, **k: None)
    assert not ov.sonder_port_via_route(
        {"mode": "jump", "host": "10.0.0.1", "extra_params": "-J root@jump"},
        3389,
        "root",
        _log_vide,
    )


def test_api_get_erreur_inattendue(monkeypatch):
    """Erreur non HTTP : None rendu, message passé au journal."""
    journal: list[str] = []

    def _paisible(req, timeout=20, context=None):
        raise ValueError("cassé")

    monkeypatch.setattr(ov, "urlopen", _paisible)
    assert ov.api_get("https://e/api", "a", "b", True, "/vms", journal.append) is None
    assert any("inattendue" in ligne for ligne in journal)


def test_interpreter_route_manuelle_avec_port():
    """Route directe « ip:port » : seul l'hôte est retenu."""
    assert ov.interpreter_route_manuelle("10.0.0.1:2222", "10.9.9.9") == {
        "mode": "direct",
        "host": "10.0.0.1",
        "extra_params": "",
    }


def test_sonder_port_via_route_rebond_avec_port(monkeypatch):
    """Rebond « -J user@jump:2222 » : le port du rebond est respecté."""
    vu: list = []
    client = ClientFaux()

    def _connecter(hote, port, utilisateur, log_fn=None):
        vu.append((hote, port))
        return client

    monkeypatch.setattr(ov.hv_common, "paramiko_connect", _connecter)
    monkeypatch.setattr(ov.hv_common, "check_port_open", lambda c, run, ip, port: True)
    assert ov.sonder_port_via_route(
        {"mode": "jump", "host": "10.0.0.1", "extra_params": "-J root@jump:2222"},
        3389,
        "root",
        _log_vide,
    )
    assert vu == [("jump", 2222)]


def test_collecte_rdp_via_rebond(monkeypatch):
    """VM Windows injoignable en direct : SSH/RDP via le rebond moteur."""
    client = ClientFaux()

    def _connecter(hote, port, utilisateur, log_fn=None):
        return client

    vms = {
        "vm": [
            {
                "name": "vm-win-c",
                "status": "up",
                "host": {"id": "h1"},
                "os": {"type": "windows_2022"},
                "guest_info": {"ips": {"ip": [{"address": "192.168.1.50"}]}},
            }
        ]
    }

    def _api(url_moteur, utilisateur, mot_de_passe, verifier_tls, chemin, log_fn=None, timeout=20):
        return vms

    monkeypatch.setattr(ov, "api_get", _api)
    # Direct refusé, rebond moteur joignable : route par le moteur
    monkeypatch.setattr(ov, "sonder_tcp_direct", lambda hote, port, timeout=4: False)
    monkeypatch.setattr(ov.hv_common, "paramiko_connect", _connecter)
    monkeypatch.setattr(ov.hv_common, "check_port_open", lambda c, run, ip, port: True)
    hotes = ov.collecter_hotes(
        url_moteur="https://moteur.example/api",
        utilisateur="admin@internal",
        mot_de_passe="secret",
        verifier_tls=True,
        utilisateur_ssh="root",
        log_fn=_log_vide,
        progression_fn=lambda f, t: None,
    )
    par_protocole = {h["protocol"]: h for h in hotes}
    assert set(par_protocole) == {"ssh", "rdp"}
    assert par_protocole["ssh"]["extra_params"] == "-J root@moteur.example"
    assert par_protocole["rdp"]["port"] == 3389
