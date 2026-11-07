# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin outil « Import from Proxmox » GTK4 (#439, portage #179).

L'onglet est piloté comme l'utilisateur : activation du menu, scan à
travers la vraie logique ``core.collecter_hotes`` (client SSH factice
répondant aux commandes ``qm``), prévisualisation, import via le
contexte du plugin. La résolution IP par l'agent QGA
(:func:`extraire_premiere_ipv4`) est testée à part (formes JSON,
repli regex, loopback exclu).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import registre
from gcm4.ui.main_window import MainWindow

RACINE = Path(__file__).resolve().parents[2]

#: Sortie QGA factice : eth0 en 192.168.1.20, lo en 127.0.0.1 (à ignorer).
_QGA = [
    {"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1"}]},
    {"name": "eth0", "ip-addresses": [{"ip-address": "192.168.1.20"}]},
]

#: Réponses du client SSH factice, par commande exacte.
REPONSES = {
    "qm list": "VMID NAME STATUS MEM(MB) ...\n101 vm-prod-a running 2048\n102 vm-win-b stopped 4096",
    "qm guest cmd 101 network-get-interfaces 2>/dev/null": json.dumps(_QGA),
    "qm config 101 | grep '^ipconfig'": "",
    "qm config 102 | grep '^ipconfig'": "",
    "qm config 101 | grep -oP '(?<=virtio=|e1000=|rtl8139=)[0-9A-Fa-f:]+' | head -1": "",
    "qm config 102 | grep -oP '(?<=virtio=|e1000=|rtl8139=)[0-9A-Fa-f:]+' | head -1": "",
    "ip neigh show 2>/dev/null || arp -n 2>/dev/null": "",
    "ip -o -f inet addr show": "",
    "qm config 101 | grep '^vga:'": "vga: qxl",
    "qm config 102 | grep '^vga:'": "vga: std",
    "hostname": "pve1",
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
def app_proxmox(tmp_path, monkeypatch, pomper):
    """Application sur la fixture aes avec le seul plugin import_proxmox."""
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
        c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "import_proxmox"
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
    """Charge le plugin (paquet ``gcm4_plugin_import_proxmox``) et rend sa classe."""
    from gcm4.core import registre

    return registre._importer_fichier(
        "import_proxmox", RACINE / "plugins" / "import_proxmox" / "gtk4.py"
    )


def _fenetre(app):
    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def _onglet(fenetre):
    """Onglet du plugin (dernier onglet ouvert)."""
    import gcm4_plugin_import_proxmox.gtk4 as plugin  # noqa: PLC0415

    onglet = fenetre.onglets.get_nth_page(fenetre.onglets.get_n_pages() - 1)
    assert isinstance(onglet, plugin.OngletImportProxmox)
    return onglet


def test_extraire_premiere_ipv4():
    """QGA : formes liste et dict, repli regex, loopback jamais retourné."""
    _plugin()
    import gcm4_plugin_import_proxmox.core as core  # noqa: PLC0415

    assert core.extraire_premiere_ipv4(json.dumps(_QGA)) == "192.168.1.20"
    assert core.extraire_premiere_ipv4(json.dumps({"result": _QGA})) == "192.168.1.20"
    assert core.extraire_premiere_ipv4('pas du tout du json {"ip-address": "127.0.0.1"}') == ""
    assert (
        core.extraire_premiere_ipv4('x "ip-address": "10.0.0.9" "ip-address": "127.0.0.1"')
        == "10.0.0.9"
    )


def test_normaliser_cible():
    """Saisie libre → URI qemu+ssh (port non standard compris)."""
    _plugin()
    import gcm4_plugin_import_proxmox.core as core  # noqa: PLC0415

    assert core.normaliser_cible("root@h.example") == "qemu+ssh://root@h.example/system"
    assert core.normaliser_cible("h.example:2222") == "qemu+ssh://root@h.example:2222/system"
    assert core.normaliser_cible("qemu+ssh://root@h/system") == "qemu+ssh://root@h/system"
    assert core.normaliser_cible("   ") == ""


def test_entree_de_menu_et_onglet(app_proxmox, pomper):
    """L'outil est dans le menu Fichier (section import) et ouvre son onglet."""
    app, _dossier = app_proxmox
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-proxmox", None)
    pomper()
    onglet = _onglet(fenetre)
    assert fenetre.titres_onglets[onglet] == "Import from Proxmox"
    assert onglet._stack.get_visible_child_name() == "config"


def test_scan_collecte_et_import(app_proxmox, pomper, monkeypatch):
    """Scan via le vrai ``collecter_hotes`` (client SSH factice) puis import."""
    app, dossier = app_proxmox
    import gcm4_plugin_import_proxmox.core as plugin_core  # noqa: PLC0415

    client = FauxClient(REPONSES)

    def _connecter(hostname, port, username, log_fn=None):
        return client

    monkeypatch.setattr(plugin_core.hv_common, "paramiko_connect", _connecter)
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-proxmox", None)
    pomper()
    onglet = _onglet(fenetre)
    onglet.zone_cibles.definir(["qemu+ssh://root@pve.example/system"])
    onglet._on_scan(None)
    for _ in range(300):
        pomper()
        if onglet._stack.get_visible_child_name() == "results":
            break
    assert onglet._stack.get_visible_child_name() == "results"
    assert client.ferme

    # 101 (running, IP par QGA, vga qxl) : SSH + SPICE ;
    # 102 (stopped, sans IP) : SSH via l'hyperviseur.
    decouvertes = onglet.decouvertes
    assert len(decouvertes) == 3
    ssh_prod = next(d for d in decouvertes if d["protocol"] == "ssh" and d["name"] == "prod-a")
    assert ssh_prod["host"] == "192.168.1.20"
    assert ssh_prod["extra_params"] == "-J root@pve.example"
    spice = next(d for d in decouvertes if d["protocol"] == "spice")
    assert "--proxmox pve1 101" in spice["extra_params"]
    ssh_win = next(d for d in decouvertes if d["protocol"] == "ssh" and d["name"] == "win-b")
    assert ssh_win["host"] == "pve.example"

    onglet._on_importer(None)
    pomper()
    from gcm4.core.chiffrement import ChiffrementGcm
    from gcm4.core.config import load_config

    relu = load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))
    assert {"prod-a", "win-b"} <= {h.name for h in relu.store.hosts}


def test_scan_sans_cible_ni_protocole(app_proxmox, pomper):
    """Sans cible cochée ni protocole choisi : messages, pas de scan."""
    app, _dossier = app_proxmox
    fenetre = _fenetre(app)
    fenetre.activate_action("win.outil-import-proxmox", None)
    pomper()
    onglet = _onglet(fenetre)

    def _journal():
        return onglet._journal_tampon.get_text(
            onglet._journal_tampon.get_start_iter(), onglet._journal_tampon.get_end_iter(), False
        )

    onglet.zone_cibles.definir([])
    onglet._on_scan(None)
    pomper()
    assert "No Proxmox target selected." in _journal()
    assert onglet._stack.get_visible_child_name() == "config"

    onglet.zone_cibles.definir(["qemu+ssh://root@pve.example/system"])
    for case in onglet.cases_protocole.values():
        case.set_active(False)
    onglet._on_scan(None)
    pomper()
    assert "No connection type selected." in _journal()
    assert onglet._stack.get_visible_child_name() == "config"
