# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin outil « Import from oVirt » GTK4 (#439, portage #179).

L'onglet est piloté comme l'utilisateur : activation du menu, scan à
travers la vraie logique ``core.collecter_hotes`` (API factice), import
via le contexte du plugin. La résolution de route
(:func:`resoudre_route`, :func:`interpreter_route_manuelle`) et le sondage
RDP sont testés à part, sans interface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import registre
from gcm4.ui.main_window import MainWindow

RACINE = Path(__file__).resolve().parents[2]

#: VMs factices : prod-a (IP connue), win-b (Windows sans IP → ignorée),
#: win-c (Windows avec IP), down (hors service → ignorée).
_VMS = {
    "vm": [
        {
            "name": "vm-prod-a",
            "status": "up",
            "host": {"id": "h1"},
            "os": {"type": "rhel_8"},
            "guest_info": {"ips": {"ip": [{"address": "192.168.1.40"}]}},
        },
        {
            "name": "vm-win-b",
            "status": "up",
            "host": {"id": "h1"},
            "os": {"type": "windows_2022"},
            "guest_info": {"ips": []},
        },
        {
            "name": "vm-win-c",
            "status": "up",
            "host": {"id": "h1"},
            "os": {"type": "windows_2022"},
            "guest_info": {"ips": {"ip": [{"address": "192.168.1.50"}]}},
        },
        {"name": "vm-down", "status": "down", "host": {"id": "h1"}},
    ]
}


def _api_factice(
    url_moteur, utilisateur, mot_de_passe, verifier_tls, chemin, log_fn=None, timeout=20
):
    """API oVirt factice : la liste des VMs de :data:`_VMS`."""
    return _VMS


def _log_vide(message: str) -> None:
    """Log factice sans effet."""


def _progression_vide(frac: float, texte: str) -> None:
    """Progression factice sans effet."""


@pytest.fixture
def app_ovirt(tmp_path, monkeypatch, pomper):
    """Application sur la fixture aes avec le seul plugin import_ovirt."""
    import shutil

    from gi.repository import Gio

    from gcm4.core import chemins
    from gcm4.core import config as config_mod
    from gcm4.ui.application import Gcm4Application

    monkeypatch.setenv("USER", "utilisateur")
    monkeypatch.setattr(config_mod, "_sauvegardes", set())
    dossier = tmp_path / "gcm"
    shutil.copytree(RACINE / "tests" / "data" / "config" / "aes", dossier)
    chemins.preparer(str(dossier))
    candidats = [
        c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "import_ovirt"
    ]
    app = Gcm4Application(
        _args(tmp_path),
        flags=Gio.ApplicationFlags.NON_UNIQUE,
        candidats=candidats,
    )
    app.register(None)
    app.activate()
    pomper()
    yield app, dossier
    for fenetre in list(app.get_windows()):
        fenetre.destroy()
    pomper()


def _args(tmp_path):
    import argparse

    return argparse.Namespace(config_dir=str(tmp_path / "gcm"), debug=False, hotes=[])


def _plugin():
    """Charge le plugin (paquet ``gcm4_plugin_import_ovirt``) et rend sa classe."""
    from gcm4.core import registre

    return registre._importer_fichier(
        "import_ovirt", RACINE / "plugins" / "import_ovirt" / "gtk4.py"
    )


def _fenetre(app):
    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def _onglet(fenetre):
    """Onglet du plugin (dernier onglet ouvert)."""
    import gcm4_plugin_import_ovirt.gtk4 as plugin  # noqa: PLC0415

    onglet = fenetre.onglets.get_nth_page(fenetre.onglets.get_n_pages() - 1)
    assert isinstance(onglet, plugin.OngletImportOvirt)
    return onglet


def test_interpreter_route_manuelle():
    """Routes manuelles : saut, direct, cible implicite, saisie vide refusée."""
    _plugin()
    import gcm4_plugin_import_ovirt.core as core  # noqa: PLC0415

    assert core.interpreter_route_manuelle("-J root@jump 10.0.0.1", "10.9.9.9") == {
        "mode": "jump",
        "host": "10.0.0.1",
        "extra_params": "-J root@jump 10.0.0.1",
    }
    assert core.interpreter_route_manuelle("10.0.0.1", "10.9.9.9") == {
        "mode": "direct",
        "host": "10.0.0.1",
        "extra_params": "",
    }
    # Saut sans cible explicite : la cible implicite est l'IP de repli
    assert core.interpreter_route_manuelle("-J root@jump", "10.9.9.9") == {
        "mode": "jump",
        "host": "10.9.9.9",
        "extra_params": "-J",
    }
    assert core.interpreter_route_manuelle("", "10.9.9.9") is None


def test_resoudre_route_directe_puis_manuelle(monkeypatch):
    """Direct si le port répond ; sinon question ; sans question → None."""
    _plugin()
    import gcm4_plugin_import_ovirt.core as core  # noqa: PLC0415

    monkeypatch.setattr(core, "sonder_tcp_direct", lambda hote, port, timeout=4: True)
    route = core.resoudre_route(
        "vm",
        "10.0.0.1",
        "https://ovirt.example/api",
        "admin",
        "secret",
        True,
        "root",
        "h1",
        _log_vide,
    )
    assert route == {"mode": "direct", "host": "10.0.0.1", "extra_params": ""}

    # Ni direct, ni jump (SSH impossible, hyperviseur inconnu) : question posée
    monkeypatch.setattr(core, "sonder_tcp_direct", lambda hote, port, timeout=4: False)
    monkeypatch.setattr(core.hv_common, "paramiko_connect", lambda *a, **k: None)
    monkeypatch.setattr(core, "adresse_hote", lambda *a, **k: "")
    route = core.resoudre_route(
        "vm",
        "10.0.0.1",
        "https://ovirt.example/api",
        "admin",
        "secret",
        True,
        "root",
        "h1",
        _log_vide,
        lambda nom: "10.5.5.5",
    )
    assert route == {"mode": "direct", "host": "10.5.5.5", "extra_params": ""}
    assert (
        core.resoudre_route(
            "vm",
            "10.0.0.1",
            "https://ovirt.example/api",
            "admin",
            "secret",
            True,
            "root",
            "h1",
            _log_vide,
        )
        is None
    )


def test_collecte_par_api(monkeypatch):
    """SSH direct quand le port répond ; VM sans IP ignorée."""
    _plugin()
    import gcm4_plugin_import_ovirt.core as core  # noqa: PLC0415

    monkeypatch.setattr(core, "api_get", _api_factice)
    monkeypatch.setattr(core, "sonder_tcp_direct", lambda hote, port, timeout=4: port == 22)
    hotes = core.collecter_hotes(
        url_moteur="https://ovirt.example/ovirt-engine/api",
        utilisateur="admin@internal",
        mot_de_passe="secret",
        verifier_tls=True,
        utilisateur_ssh="root",
        log_fn=_log_vide,
        progression_fn=_progression_vide,
    )
    # prod-a et win-c : SSH direct ; win-b (sans IP) et down : ignorées
    assert [(h["name"], h["protocol"]) for h in hotes] == [
        ("prod-a", "ssh"),
        ("win-c", "ssh"),
    ]
    assert hotes[0]["host"] == "192.168.1.40"
    assert hotes[1]["host"] == "192.168.1.50"


def test_collecte_rdp_via_route(monkeypatch):
    """VM Windows avec RDP ouvert : entrée RDP en plus du SSH."""
    _plugin()
    import gcm4_plugin_import_ovirt.core as core  # noqa: PLC0415

    monkeypatch.setattr(core, "api_get", _api_factice)
    monkeypatch.setattr(core, "sonder_tcp_direct", lambda hote, port, timeout=4: True)
    hotes = core.collecter_hotes(
        url_moteur="https://ovirt.example/ovirt-engine/api",
        utilisateur="admin@internal",
        mot_de_passe="secret",
        verifier_tls=True,
        utilisateur_ssh="root",
        log_fn=_log_vide,
        progression_fn=_progression_vide,
    )
    rdp = next(h for h in hotes if h["protocol"] == "rdp")
    assert rdp["name"] == "win-c" and rdp["host"] == "192.168.1.50"
    assert rdp["user"] == "Administrator" and rdp["port"] == 3389


def test_entree_de_menu_et_onglet(app_ovirt, pomper):
    """L'outil est dans le menu Fichier (section import) et ouvre son onglet."""
    app, _dossier = app_ovirt
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-ovirt", None)
    pomper()
    onglet = _onglet(fenetre)
    assert fenetre.titres_onglets[onglet] == "Import from oVirt"
    assert onglet._stack.get_visible_child_name() == "config"


def test_scan_collecte_et_import(app_ovirt, pomper, monkeypatch):
    """Scan via le vrai ``collecter_hotes`` (API factice) puis import."""
    app, dossier = app_ovirt
    import gcm4_plugin_import_ovirt.core as plugin_core  # noqa: PLC0415

    monkeypatch.setattr(plugin_core, "api_get", _api_factice)
    monkeypatch.setattr(plugin_core, "sonder_tcp_direct", lambda hote, port, timeout=4: port == 22)
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-ovirt", None)
    pomper()
    onglet = _onglet(fenetre)
    onglet.saisie_url.set_text("https://ovirt.example/ovirt-engine/api")
    onglet.saisie_utilisateur.set_text("admin@internal")
    onglet.saisie_mot_de_passe.set_text("secret")
    onglet.saisie_utilisateur_ssh.set_text("root")
    onglet._on_scan(None)
    for _ in range(300):
        pomper()
        if onglet._stack.get_visible_child_name() == "results":
            break
    assert onglet._stack.get_visible_child_name() == "results"

    decouvertes = onglet.decouvertes
    assert [(d["name"], d["protocol"]) for d in decouvertes] == [
        ("prod-a", "ssh"),
        ("win-c", "ssh"),
    ]

    onglet._on_importer(None)
    pomper()
    from gcm4.core.chiffrement import ChiffrementGcm
    from gcm4.core.config import load_config

    relu = load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))
    assert {"prod-a", "win-c"} <= {h.name for h in relu.store.hosts}


def test_scan_sans_url_journalise(app_ovirt, pomper):
    """Sans URL d'engine : message dans le journal, pas de changement de page."""
    app, _dossier = app_ovirt
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-ovirt", None)
    pomper()
    onglet = _onglet(fenetre)
    onglet._on_scan(None)
    pomper()
    journal = onglet._journal_tampon.get_text(
        onglet._journal_tampon.get_start_iter(), onglet._journal_tampon.get_end_iter(), False
    )
    assert "Engine URL is required." in journal
    assert onglet._stack.get_visible_child_name() == "config"


def test_scan_sans_protocole(app_ovirt, pomper):
    """URL saisie mais aucun protocole : message, pas de scan."""
    app, _dossier = app_ovirt
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-ovirt", None)
    pomper()
    onglet = _onglet(fenetre)
    onglet.saisie_url.set_text("https://ovirt.example/ovirt-engine/api")
    for case in onglet.cases_protocole.values():
        case.set_active(False)
    onglet._on_scan(None)
    pomper()
    journal = onglet._journal_tampon.get_text(
        onglet._journal_tampon.get_start_iter(), onglet._journal_tampon.get_end_iter(), False
    )
    assert "No connection type selected." in journal
    assert onglet._stack.get_visible_child_name() == "config"


def test_demander_route_dialogue(app_ovirt, pomper, monkeypatch):
    """Route manuelle : le dialogue rend la saisie validée (ou None)."""
    import gcm4_plugin_import_ovirt.gtk4 as plugin_gtk4  # noqa: PLC0415

    app, _dossier = app_ovirt
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-ovirt", None)
    pomper()
    onglet = _onglet(fenetre)

    # GLib.idle_add synchrone : le dialogue s'affiche avant le wait()
    monkeypatch.setattr(plugin_gtk4.GLib, "idle_add", lambda f: (f(), False)[1])

    def _creer(parent, titre, corps, boutons, defaut, refus, repondu, extra=None):
        extra.set_text("10.9.9.9")
        repondu("valider")

    monkeypatch.setattr(plugin_gtk4.dialogues, "creer", _creer)
    assert onglet._demander_route("vm-x") == "10.9.9.9"

    def _creer_ignorer(parent, titre, corps, boutons, defaut, refus, repondu, extra=None):
        extra.set_text("10.9.9.9")
        repondu("ignorer")

    monkeypatch.setattr(plugin_gtk4.dialogues, "creer", _creer_ignorer)
    assert onglet._demander_route("vm-x") is None
