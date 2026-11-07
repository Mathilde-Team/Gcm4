# SPDX-License-Identifier: GPL-3.0-or-later
"""Collecte des VMs Proxmox de ``plugins/import_proxmox/core.py`` (#293, #439).

``collecter_hotes`` interroge les hyperviseurs par SSH (``qm list``,
agent invité, ``qm config``, ARP, nmap) et fabrique les hôtes GCM (SSH
direct ou via l'hyperviseur, SPICE, RDP, VNC). Ici, les accès SSH du
socle partagé ``gcm4_hv`` sont remplacés par une doublure qui rejoue des
sorties de commandes : aucun réseau, aucun processus externe, aucun GTK.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
)

from doublures_ssh import Hyperviseur  # noqa: E402

from plugins.import_proxmox import core as pxm  # noqa: E402

hv = pxm.hv_common


def collecter(uris, proto_filter=None, ssh_user="admin"):
    """Lance la collecte Proxmox et renvoie hôtes, journal, progression."""
    journal: list[str] = []
    progression: list[tuple] = []
    hotes = pxm.collecter_hotes(
        uris, ssh_user, journal.append, lambda f, t: progression.append((f, t)), proto_filter
    )
    return hotes, journal, progression


QM_LIST = """      VMID NAME                 STATUS     MEM(MB)    BOOTDISK(GB) PID
       100 webfront             running    2048              32.00 1234
       101 win2019ad            running    4096              64.00 1235
       102 arret                stopped    1024              16.00 0

  ligne parasite
"""

QGA_JSON = (
    '[{"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1"}]},'
    ' {"name": "eth0", "ip-addresses": [{"ip-address": "fe80::1"},'
    ' {"ip-address": "10.0.0.5"}]}, "pas un dict"]'
)


def test_paramiko_absent(monkeypatch):
    """Sans paramiko : message d'installation, aucun hôte."""
    monkeypatch.setattr(hv, "PARAMIKO_OK", False)
    monkeypatch.setattr(hv, "dep_install_hint", lambda *a: "installer paramiko")
    hotes, journal, _ = collecter(["qemu+ssh://root@pve/system"])
    assert hotes == []
    assert "paramiko non installé" in journal[0]


def test_connexion_impossible(monkeypatch):
    """Connexion refusée : hyperviseur sauté."""
    Hyperviseur({}, connexion=False).installer(monkeypatch)
    hotes, _, progression = collecter(["qemu+ssh://pve/system"])
    assert hotes == []
    assert progression == [(0.0, "Connexion à qemu+ssh://pve/system…")]


def test_filtre_par_defaut_ssh_spice_rdp(monkeypatch):
    """IP via l'agent invité ; Windows → Administrator ; SPICE si vga qxl."""
    hyperviseur = Hyperviseur(
        {
            "qm list": QM_LIST,
            "qm guest cmd 100": QGA_JSON,
            "qm guest cmd 101": '{"result": [{"ip-addresses": [{"ip-address": "10.0.0.6"}]}]}',
            "qm config 100 | grep '^vga:'": "vga: qxl",
            "qm config 101 | grep '^vga:'": "vga: std",
            "qm config 102 | grep '^vga:'": "",
            "qm config 102 | grep '^ipconfig'": "ipconfig0: ip=192.168.1.20/24,gw=192.168.1.1",
            "hostname": "noeud1\n",
        },
        ports_ouverts={("10.0.0.6", 3389)},
    )
    hyperviseur.installer(monkeypatch)

    hotes, journal, progression = collecter(["qemu+ssh://root@pve:2222/system"])

    par_cle = {(h["name"], h["protocol"]): h for h in hotes}
    assert set(par_cle) == {
        ("webfront", "ssh"),
        ("webfront", "spice"),
        ("win2019ad", "ssh"),
        ("win2019ad", "rdp"),
        ("arret", "ssh"),
    }
    web = par_cle[("webfront", "ssh")]
    assert (web["host"], web["user"], web["extra_params"]) == (
        "10.0.0.5",
        "admin",
        "-J root@pve:2222",
    )
    assert par_cle[("webfront", "spice")]["extra_params"] == "--proxmox noeud1 100"
    assert par_cle[("win2019ad", "ssh")]["user"] == "Administrator"
    assert par_cle[("win2019ad", "rdp")]["port"] == 3389
    assert par_cle[("arret", "ssh")]["host"] == "192.168.1.20"  # repli ipconfig0
    assert "port 3389 fermé sur 10.0.0.5 — RDP ignoré" in "\n".join(journal)
    assert progression[-1] == (1.0, "1/1 hyperviseurs traités")
    assert hyperviseur.clients[0].ferme
    assert hyperviseur.connexions == [("pve", 2222, "root")]
    # Aucune requête invité pour une VM arrêtée
    assert not any("qm guest cmd 102" in c for c in hyperviseur.commandes)


def test_ip_inconnue_ssh_via_hyperviseur(monkeypatch):
    """Ni agent, ni ipconfig, ni ARP, ni nmap : SSH rebondi par l'hyperviseur."""
    Hyperviseur({"qm list": "100 webfront stopped\n"}).installer(monkeypatch)

    hotes, journal, _ = collecter(["qemu+ssh://pve/system"], proto_filter={"ssh", "rdp", "vnc"})

    assert len(hotes) == 1
    assert hotes[0]["host"] == "pve"
    assert hotes[0]["user"] == "root"
    assert hotes[0]["port"] == 22
    assert hotes[0]["extra_params"] == "-t ssh admin@webfront"
    assert any("inconnue (QGA/ARP/nmap ont échoué)" in ligne for ligne in journal)


def test_replis_ipconfig_lien_local_puis_arp(monkeypatch):
    """ipconfig0 en 169.254 ignoré ; ARP filtré par MAC ; groupe tiré du nom."""
    Hyperviseur(
        {
            "qm list": "100 prod_web stopped\n",
            "grep '^ipconfig'": "ipconfig0: ip=169.254.0.9/16",
            "grep -oP '(?<=virtio=": "AA:BB:CC:DD:EE:FF\n",
            "ip neigh show": (
                "10.0.0.1 dev vmbr0 lladdr 11:22:33:44:55:66 REACHABLE\n"
                "127.0.0.9 dev lo lladdr aa:bb:cc:dd:ee:ff\n"
                "10.0.0.7 dev vmbr0 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"
            ),
        },
        ports_ouverts={("10.0.0.7", 5900)},
    ).installer(monkeypatch)

    hotes, _, _ = collecter(["qemu+ssh://pve/system"], proto_filter={"ssh", "vnc"})

    groupe_attendu = hv.vm_name_split("prod_web")[0]
    assert [(h["protocol"], h["host"]) for h in hotes] == [
        ("ssh", "10.0.0.7"),
        ("vnc", "10.0.0.7"),
    ]
    attendu_ssh = f"{groupe_attendu}/ssh" if groupe_attendu else "ssh"
    assert hotes[0]["group"] == attendu_ssh
    assert hotes[0]["extra_params"] == "-J root@pve"


@pytest.mark.parametrize(
    "mac, nmap, ip_attendue, message",
    [
        ("aa:bb:cc:dd:ee:ff", {"aa:bb:cc:dd:ee:ff": "10.0.0.8"}, "10.0.0.8", None),
        ("", {"00:00:00:00:00:01": "10.0.0.9"}, None, "MAC VM introuvable"),
    ],
)
def test_repli_nmap(monkeypatch, mac, nmap, ip_attendue, message):
    """Agent invalide : IP par la table nmap, ou IP non résolue."""
    Hyperviseur(
        {
            "qm list": "100 web running\n",
            "qm guest cmd": "pas du json",
            "[0-9A-Fa-f]{2}": mac,
        },
        nmap=nmap,
    ).installer(monkeypatch)

    hotes, journal, _ = collecter(["qemu+ssh://pve/system"], proto_filter={"ssh"})

    if ip_attendue:
        assert hotes[0]["host"] == ip_attendue
    else:
        assert hotes[0]["host"] == "pve"
        assert any(message in ligne for ligne in journal)


@pytest.mark.parametrize(
    "sortie_qga, attendu",
    [
        ('{"result": [{"ip-addresses": [{"ip-address": "127.0.0.1"}]}]}', ""),  # loopback seul
        ('"ip-address": "127.0.0.1", "ip-address": "10.1.1.1" tronqué', "10.1.1.1"),  # regex
        ("42", ""),  # JSON valide mais ni liste ni dict
    ],
)
def test_extraction_ipv4_agent(monkeypatch, sortie_qga, attendu):
    """Sortie de l'agent invité : loopback ignoré, repli regex, JSON inattendu."""
    Hyperviseur({"qm list": "100 web running\n", "qm guest cmd": sortie_qga}).installer(
        monkeypatch
    )
    hotes, _, _ = collecter(["qemu+ssh://pve/system"], proto_filter={"ssh"})
    assert hotes[0]["host"] == (attendu or "pve")


def test_spice_seul_sans_qxl_aucune_connexion(monkeypatch):
    """SPICE seul sans vga qxl : rien n'est généré."""
    Hyperviseur(
        {"qm list": "100 web running\n", "qm guest cmd": QGA_JSON, "vga:": "vga: std"}
    ).installer(monkeypatch)
    hotes, journal, _ = collecter(["qemu+ssh://pve/system"], proto_filter={"spice"})
    assert hotes == []
    assert any("aucune connexion générée" in ligne for ligne in journal)
    assert any("pas de vga qxl" in ligne for ligne in journal)


def test_vnc_ferme_et_plusieurs_hyperviseurs(monkeypatch):
    """Port VNC fermé sur deux hyperviseurs ; clients fermés."""
    hyperviseur = Hyperviseur({"qm list": "100 web running\n", "qm guest cmd": QGA_JSON})
    hyperviseur.installer(monkeypatch)
    hotes, journal, progression = collecter(
        ["qemu+ssh://pve1/system", "qemu+ssh://pve2/system"], proto_filter={"vnc"}
    )
    assert hotes == []
    assert sum("port 5900 fermé" in ligne for ligne in journal) == 2
    assert progression[-1] == (1.0, "2/2 hyperviseurs traités")
    assert all(c.ferme for c in hyperviseur.clients)


def test_client_ferme_meme_en_cas_d_erreur(monkeypatch):
    """Erreur SSH : l'exception remonte, le client est fermé."""
    hyperviseur = Hyperviseur({})
    hyperviseur.installer(monkeypatch)

    def panne(client, commande, timeout=30):
        raise RuntimeError("canal SSH coupé")

    monkeypatch.setattr(hv, "libvirt_ssh_run", panne)
    with pytest.raises(RuntimeError):
        collecter(["qemu+ssh://pve/system"])
    assert hyperviseur.clients[0].ferme
