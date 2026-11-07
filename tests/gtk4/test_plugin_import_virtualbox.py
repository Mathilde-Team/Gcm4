# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin outil « Import from VirtualBox » GTK4 (#439, portage #179).

L'onglet est piloté comme l'utilisateur : activation du menu, scan à
travers la vraie logique ``core.collecter_hotes`` (client SSH factice
répondant aux commandes ``VBoxManage``), prévisualisation, import via
le contexte du plugin. Le décodage de ``--machinereadable``
(:func:`parser_machinereadable`) et la priorité VRDE sont testés à part.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import registre
from gcm4.ui.main_window import MainWindow

RACINE = Path(__file__).resolve().parents[2]

#: Réponses du client SSH factice, par commande exacte.
REPONSES = {
    "which VBoxManage 2>/dev/null || which vboxmanage 2>/dev/null": "/usr/bin/VBoxManage",
    "/usr/bin/VBoxManage list vms": '"vm-prod-a" {aaaa}\n"vm-win-b" {bbbb}',
    "/usr/bin/VBoxManage list runningvms": '"vm-prod-a" {aaaa}',
    '/usr/bin/VBoxManage showvminfo "vm-prod-a" --machinereadable': (
        'name="vm-prod-a"\nmacaddress1="080027ABCDEF"\nvrde="off"\nvrdeport="0"\n'
        'ostype="Linux_64"\n'
    ),
    '/usr/bin/VBoxManage showvminfo "vm-win-b" --machinereadable': (
        'name="vm-win-b"\nmacaddress1="080027123456"\nvrde="on"\nvrdeport="33891"\n'
        'ostype="Windows10"\n'
    ),
    '/usr/bin/VBoxManage guestproperty get "vm-prod-a" "/VirtualBox/GuestInfo/Net/0/V4/IP"': "Value: 192.168.1.30",
    "ip neigh show 2>/dev/null || arp -n 2>/dev/null": "",
    "ip -o -f inet addr show": "",
    "which nmap 2>/dev/null": "",
}


class FauxSortie:
    """Sortie de commande SSH factice (mêmes méthodes que celle de paramiko)."""

    def __init__(self, texte: str) -> None:
        """Mémorise la sortie brute de la commande."""
        self._octets = texte.encode()

    def read(self) -> bytes:
        """Rend la sortie de la commande."""
        return self._octets


class FauxClient:
    """Client SSH factice : chaque commande a sa réponse dans ``REPONSES``."""

    def __init__(self, reponses: dict[str, str]) -> None:
        """Mémorise les réponses par commande exacte."""
        self.reponses = reponses
        self.ferme = False

    def exec_command(self, cmd: str, timeout: int = 30):
        """Rend ``(None, sortie, None)`` comme paramiko."""
        return None, FauxSortie(self.reponses.get(cmd, "")), None

    def close(self) -> None:
        """Marque le client fermé."""
        self.ferme = True


@pytest.fixture
def app_virtualbox(tmp_path, monkeypatch, pomper):
    """Application sur la fixture aes avec le seul plugin import_virtualbox."""
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
        c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "import_virtualbox"
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
    """Charge le plugin (paquet ``gcm4_plugin_import_virtualbox``) et rend sa classe."""
    from gcm4.core import registre

    return registre._importer_fichier(
        "import_virtualbox", RACINE / "plugins" / "import_virtualbox" / "gtk4.py"
    )


def _fenetre(app):
    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def _onglet(fenetre):
    """Onglet du plugin (dernier onglet ouvert)."""
    import gcm4_plugin_import_virtualbox.gtk4 as plugin  # noqa: PLC0415

    onglet = fenetre.onglets.get_nth_page(fenetre.onglets.get_n_pages() - 1)
    assert isinstance(onglet, plugin.OngletImportVirtualBox)
    return onglet


def test_parser_machinereadable():
    """Décodage clé=valeur, guillemets retirés, clés absentes ignorées."""
    _plugin()
    import gcm4_plugin_import_virtualbox.core as core  # noqa: PLC0415

    infos = core.parser_machinereadable('name="vm"\nmacaddress1="08:00:27:aa:bb:cc"\nvrde="on"')
    assert infos == {
        "name": "vm",
        "macaddress1": "08:00:27:aa:bb:cc",
        "vrde": "on",
    }
    assert core.parser_machinereadable("") == {}


def test_normaliser_cible():
    """Saisie libre → URI vbox+ssh ; utilisateur défaut « root » conservé."""
    _plugin()
    import gcm4_plugin_import_virtualbox.core as core  # noqa: PLC0415

    assert core.normaliser_cible("h.example") == "vbox+ssh://root@h.example/"
    assert core.normaliser_cible("admin@h.example") == "vbox+ssh://admin@h.example/"
    assert core.normaliser_cible("vbox+ssh://admin@h") == "vbox+ssh://admin@h"
    assert core.normaliser_cible("h.example:2222") == "vbox+ssh://root@h.example:2222/"


def test_entree_de_menu_et_onglet(app_virtualbox, pomper):
    """L'outil est dans le menu Fichier (section import) et ouvre son onglet."""
    app, _dossier = app_virtualbox
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-virtualbox", None)
    pomper()
    onglet = _onglet(fenetre)
    assert fenetre.titres_onglets[onglet] == "Import from VirtualBox"
    assert onglet._stack.get_visible_child_name() == "config"


def test_scan_collecte_et_import(app_virtualbox, pomper, monkeypatch):
    """Scan via le vrai ``collecter_hotes`` (client SSH factice) puis import.

    vm-prod-a (en marche, IP par guestproperty, VRDE off) : SSH direct ;
    vm-win-b (arrêtée, Windows, VRDE on 33891) : SSH via l'hyperviseur +
    RDP VRDE sur l'hyperviseur.
    """
    app, dossier = app_virtualbox
    import gcm4_plugin_import_virtualbox.core as plugin_core  # noqa: PLC0415

    client = FauxClient(REPONSES)

    def _connecter(hostname, port, username, log_fn=None):
        return client

    monkeypatch.setattr(plugin_core.hv_common, "paramiko_connect", _connecter)
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-virtualbox", None)
    pomper()
    onglet = _onglet(fenetre)
    onglet.zone_cibles.definir(["vbox+ssh://root@vbox.example"])
    onglet._on_scan(None)
    for _ in range(300):
        pomper()
        if onglet._stack.get_visible_child_name() == "results":
            break
    assert onglet._stack.get_visible_child_name() == "results"
    assert client.ferme

    decouvertes = onglet.decouvertes
    assert len(decouvertes) == 3
    ssh_prod = next(d for d in decouvertes if d["protocol"] == "ssh" and d["name"] == "prod-a")
    assert ssh_prod["host"] == "192.168.1.30"
    assert ssh_prod["extra_params"] == "-J root@vbox.example"
    ssh_win = next(d for d in decouvertes if d["protocol"] == "ssh" and d["name"] == "win-b")
    assert ssh_win["host"] == "vbox.example"
    rdp = next(d for d in decouvertes if d["protocol"] == "rdp")
    assert rdp["host"] == "vbox.example" and rdp["port"] == 33891
    assert rdp["user"] == "Administrator"

    onglet._on_importer(None)
    pomper()
    from gcm4.core.chiffrement import ChiffrementGcm
    from gcm4.core.config import load_config

    relu = load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))
    assert {"prod-a", "win-b"} <= {h.name for h in relu.store.hosts}


def test_scan_sans_cible_ni_protocole(app_virtualbox, pomper):
    """Sans hôte saisi ni protocole choisi : messages, pas de scan."""
    app, _dossier = app_virtualbox
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-virtualbox", None)
    pomper()
    onglet = _onglet(fenetre)

    def _journal():
        return onglet._journal_tampon.get_text(
            onglet._journal_tampon.get_start_iter(), onglet._journal_tampon.get_end_iter(), False
        )

    onglet.zone_cibles.definir([])
    onglet._on_scan(None)
    pomper()
    assert "No host selected." in _journal()
    assert onglet._stack.get_visible_child_name() == "config"

    onglet.zone_cibles.definir(["vbox+ssh://root@vbox.example"])
    for case in onglet.cases_protocole.values():
        case.set_active(False)
    onglet._on_scan(None)
    pomper()
    assert "No connection type selected." in _journal()
    assert onglet._stack.get_visible_child_name() == "config"
