# SPDX-License-Identifier: GPL-3.0-or-later
"""Collecte des VMs libvirt de ``plugin_import_libvirt`` (#293, palier 7).

``_libvirt_fetch_hosts`` interroge les hyperviseurs par SSH (``virsh list``,
ARP, baux DHCP, nmap, ``dumpxml``, ``domifaddr``) et fabrique les hôtes GCM.
Doublure SSH partagée (``tests/doublures_ssh.py``) :
aucun réseau, aucun processus externe, aucun GTK.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
)

import test_gcm  # noqa: E402, F401  (pose les doublures gi/Gtk avant l'import du plugin)

# isort: split
import plugin_import_libvirt as lv  # noqa: E402
from doublures_ssh import Hyperviseur  # noqa: E402


def collecter(uris, proto_filter=None, ssh_user="admin"):
    """Lance la collecte libvirt et renvoie hôtes, journal, progression."""
    journal: list[str] = []
    progression: list[tuple] = []
    hotes = lv._libvirt_fetch_hosts(
        uris, ssh_user, journal.append, lambda f, t: progression.append((f, t)), proto_filter
    )
    return hotes, journal, progression


def xml(mac, spice_port=None):
    """XML de domaine minimal : interface sans MAC, interface avec MAC, SPICE optionnel."""
    graphique = f"<graphics type='spice' port='{spice_port}' autoport='no'/>" if spice_port else ""
    return (
        f"<domain>{graphique}<devices>"
        "<interface type='network'><source network='default'/></interface>"
        f"<interface type='network'><mac address='{mac}'/></interface>"
        "</devices></domain>"
    )


def test_paramiko_absent(monkeypatch):
    """Sans paramiko : message d'installation, aucun hôte."""
    monkeypatch.setattr(lv.hv_common, "PARAMIKO_OK", False)
    monkeypatch.setattr(lv.hv_common, "dep_install_hint", lambda *a: "installer paramiko")
    hotes, journal, _ = collecter(["qemu+ssh://kvm/system"])
    assert hotes == [] and "paramiko non installé" in journal[0]


def test_uri_locale_et_connexion_impossible(monkeypatch):
    """URI locale ignorée, connexion refusée sautée."""
    hyperviseur = Hyperviseur({}, connexion=False)
    hyperviseur.installer(monkeypatch)
    hotes, journal, _ = collecter(["qemu:///system", "qemu+ssh://kvm/system"])
    assert hotes == []
    assert "  → URI locale ignorée (qemu:///system)." in journal
    assert hyperviseur.connexions == [("kvm", 22, "root")]


def test_collecte_complete(monkeypatch):
    """Domifaddr (agent), baux DHCP, ARP ; SSH, SPICE, RDP, VNC."""
    hyperviseur = Hyperviseur(
        {
            "virsh list --all --name": "webfront\nwin2019ad\narret\n\n",
            "ip neigh show": (
                "10.0.0.30 dev virbr0 lladdr 52:54:00:00:00:03 STALE\nligne sans mac\n"
            ),
            "virsh net-list --name": "default\n\n",
            "virsh net-dhcp-leases default": (
                " Expiry Time   MAC address         Protocol  IP address\n"
                " 2026-11-03    52:54:00:00:00:02   ipv4      10.0.0.20/24  win\n"
                " 2026-11-03    52:54:00:00:00:09   ipv4      pas-une-ip    x\n"
            ),
            "virsh domstate webfront": "running\n",
            "virsh domstate win2019ad": "running\n",
            "virsh domstate arret": "shut off\n",
            "virsh dumpxml webfront": xml("52:54:00:00:00:01", spice_port=5901),
            "virsh dumpxml win2019ad": xml("52:54:00:00:00:02", spice_port=-1),
            "virsh dumpxml arret": xml("52:54:00:00:00:03"),
            "domifaddr webfront --source agent": (
                " vnet0  52:54:00:00:00:01  ipv4  10.0.0.10/24\n"
            ),
        },
        ports_ouverts={("10.0.0.20", 3389), ("10.0.0.10", 5900)},
    )
    hyperviseur.installer(monkeypatch)

    hotes, journal, progression = collecter(
        ["qemu+ssh://root@kvm:542/system"], proto_filter={"ssh", "spice", "rdp", "vnc"}
    )

    par_cle = {(h["name"], h["protocol"]): h for h in hotes}
    assert set(par_cle) == {
        ("webfront", "ssh"),
        ("webfront", "spice"),
        ("webfront", "vnc"),
        ("win2019ad", "ssh"),
        ("win2019ad", "rdp"),
        ("arret", "ssh"),
    }
    assert par_cle[("webfront", "ssh")]["host"] == "10.0.0.10"  # agent invité
    assert par_cle[("webfront", "ssh")]["extra_params"] == "-J root@kvm:542"
    assert par_cle[("webfront", "spice")]["port"] == 5901
    assert par_cle[("webfront", "spice")]["extra_params"] == (
        "--connect qemu+ssh://root@kvm:542/system webfront"
    )
    assert par_cle[("win2019ad", "ssh")]["host"] == "10.0.0.20"  # bail DHCP
    assert par_cle[("win2019ad", "ssh")]["user"] == "Administrator"
    assert par_cle[("arret", "ssh")]["host"] == "10.0.0.30"  # ARP noyau, VM arrêtée
    assert "  Table MAC→IP : 2 entrées" in journal
    assert any("port 3389 fermé sur 10.0.0.10" in ligne for ligne in journal)
    assert any("port 5900 fermé sur 10.0.0.20" in ligne for ligne in journal)
    assert progression[-1] == (1.0, "1/1 hyperviseurs traités")
    assert hyperviseur.clients[0].ferme
    # Aucune requête domifaddr pour une VM arrêtée
    assert not any("domifaddr arret" in c for c in hyperviseur.commandes)


def test_ip_inconnue_et_port_par_defaut(monkeypatch):
    """Agent puis ARP vides : SSH rebondi ; SPICE avec l'URI sans port."""
    Hyperviseur(
        {
            "virsh list --all --name": "webfront\n",
            "virsh domstate": "running",
            "virsh dumpxml": xml("52:54:00:00:00:01", spice_port=5902),
            "--source agent": " vnet0  52:54:00:00:00:01  ipv4  pas d'adresse\n",
        },
        nmap={},
    ).installer(monkeypatch)

    hotes, _, _ = collecter(["qemu+ssh://kvm/system"])  # filtre par défaut

    assert [(h["protocol"], h["host"]) for h in hotes] == [("ssh", "kvm"), ("spice", "kvm")]
    assert hotes[0]["extra_params"] == "-t ssh admin@webfront"
    assert hotes[1]["extra_params"] == "--connect qemu+ssh://root@kvm/system webfront"


@pytest.mark.parametrize("source", ["arp", "nmap"])
def test_resolution_par_domifaddr_arp_ou_nmap(monkeypatch, source):
    """IP par domifaddr --source arp, ou par la table nmap."""
    sorties = {
        "virsh list --all --name": "webfront\n",
        "virsh domstate": "running",
        "virsh dumpxml": xml("52:54:00:00:00:01"),
    }
    nmap = {}
    if source == "arp":
        sorties["--source arp"] = " vnet0  52:54:00:00:00:01  ipv4  10.0.0.11/24\n"
    else:
        nmap = {"52:54:00:00:00:01": "10.0.0.12"}
    Hyperviseur(sorties, nmap=nmap).installer(monkeypatch)

    hotes, _, _ = collecter(["qemu+ssh://kvm/system"], proto_filter={"ssh"})

    assert hotes[0]["host"] == ("10.0.0.11" if source == "arp" else "10.0.0.12")
    assert hotes[0]["extra_params"] == "-J root@kvm"


def test_aucune_connexion_generee(monkeypatch):
    """VM arrêtée sans SPICE ni IP : rien n'est généré."""
    Hyperviseur(
        {
            "virsh list --all --name": "webfront\n",
            "virsh domstate": "shut off",
            "virsh dumpxml": "<domain/>",
        }
    ).installer(monkeypatch)
    hotes, journal, _ = collecter(["qemu+ssh://kvm/system"], proto_filter={"rdp", "spice"})
    assert hotes == []
    assert "  - webfront : aucune connexion générée" in journal
