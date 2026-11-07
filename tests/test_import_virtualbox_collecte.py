# SPDX-License-Identifier: GPL-3.0-or-later
"""Collecte des VMs VirtualBox de ``plugins/import_virtualbox/core.py`` (#439).

``collecter_hotes`` interroge les hôtes VirtualBox par SSH (``VBoxManage
list/showvminfo/guestproperty``, ARP, nmap) et fabrique les hôtes GCM
(SSH direct ou via l'hôte, RDP VRDE ou invité, VNC invité). Les accès
SSH du socle partagé ``gcm4_hv`` sont remplacés par la doublure
``tests/doublures_ssh.py`` : aucun réseau, aucun processus externe,
aucun GTK.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doublures_ssh import Hyperviseur  # noqa: E402

from plugins.import_virtualbox import core as vb  # noqa: E402


def collecter(uris, proto_filter=None, ssh_user="admin"):
    """Lance la collecte VirtualBox et renvoie hôtes, journal, progression."""
    journal: list[str] = []
    progression: list[tuple] = []
    hotes = vb.collecter_hotes(
        uris, ssh_user, journal.append, lambda f, t: progression.append((f, t)), proto_filter
    )
    return hotes, journal, progression


MACHINEREADABLE = """name="vm-web"
macaddress1="080027AABBCC"
vrde="off"
vrdeport="0"
ostype="Linux_64"
"""


def test_vboxmanage_absent_hote_ignore(monkeypatch):
    """Sans VBoxManage sur l'hôte : message, hôte sauté, rien d'importé."""
    Hyperviseur({"": ""}).installer(monkeypatch)
    hotes, journal, _ = collecter(["vbox+ssh://root@vbox.example"])
    assert hotes == []
    assert any("VBoxManage introuvable" in ligne for ligne in journal)


def test_connexion_impossible(monkeypatch):
    """Connexion SSH refusée : aucun hôte, aucune erreur non piégée."""
    Hyperviseur({}, connexion=False).installer(monkeypatch)
    hotes, _, _ = collecter(["vbox+ssh://root@vbox.example"])
    assert hotes == []


def test_rdp_vrde_et_ssh_via_ip_connue(monkeypatch):
    """VM en marche : IP par guestproperty → SSH direct ; VRDE off, 3389
    fermé → aucun RDP.
    """
    Hyperviseur(
        {
            "which VBoxManage": "/usr/bin/VBoxManage",
            "list vms": '"vm-web" {abc}\n',
            "list runningvms": '"vm-web" {abc}\n',
            "showvminfo": MACHINEREADABLE,
            "guestproperty get": "Value: 10.0.0.8",
            "ip neigh show": "",
        },
        ports_ouverts=set(),
    ).installer(monkeypatch)
    hotes, journal, _ = collecter(["vbox+ssh://root@vbox.example"])

    assert [(h["protocol"], h["host"]) for h in hotes] == [("ssh", "10.0.0.8")]
    assert hotes[0]["extra_params"] == "-J root@vbox.example"
    assert any("port 3389 fermé sur 10.0.0.8" in ligne for ligne in journal)


def test_rdp_invite_3389_ouvert(monkeypatch):
    """VRDE désactivé mais port 3389 ouvert dans l'invité : RDP invité."""
    Hyperviseur(
        {
            "which VBoxManage": "/usr/bin/VBoxManage",
            "list vms": '"vm-web" {abc}\n',
            "list runningvms": '"vm-web" {abc}\n',
            "showvminfo": MACHINEREADABLE,
            "guestproperty get": "Value: 10.0.0.8",
            "ip neigh show": "",
        },
        ports_ouverts={("10.0.0.8", 3389)},
    ).installer(monkeypatch)
    hotes, _, _ = collecter(["vbox+ssh://root@vbox.example"], proto_filter={"rdp"})
    assert [(h["protocol"], h["host"], h["port"]) for h in hotes] == [("rdp", "10.0.0.8", 3389)]


def test_rdp_vrde_actif(monkeypatch):
    """VRDE actif : RDP sur l'hôte VirtualBox, aucune IP requise."""
    Hyperviseur(
        {
            "which VBoxManage": "/usr/bin/VBoxManage",
            "list vms": '"vm-web" {abc}\n',
            "list runningvms": "",
            "showvminfo": 'name="vm-web"\nvrde="on"\nvrdeport="33891"\nostype="Linux_64"\n',
            "ip neigh show": "",
        }
    ).installer(monkeypatch)
    hotes, _, _ = collecter(["vbox+ssh://root@vbox.example"], proto_filter={"rdp"})
    assert [(h["protocol"], h["host"], h["port"]) for h in hotes] == [
        ("rdp", "vbox.example", 33891)
    ]


def test_vnc_invite_5900_ouvert(monkeypatch):
    """Filtre vnc : entrée VNC quand le port 5900 de l'invité répond."""
    Hyperviseur(
        {
            "which VBoxManage": "/usr/bin/VBoxManage",
            "list vms": '"vm-web" {abc}\n',
            "list runningvms": '"vm-web" {abc}\n',
            "showvminfo": MACHINEREADABLE,
            "guestproperty get": "Value: 10.0.0.8",
            "ip neigh show": "",
        },
        ports_ouverts={("10.0.0.8", 5900)},
    ).installer(monkeypatch)
    hotes, _, _ = collecter(["vbox+ssh://root@vbox.example"], proto_filter={"vnc"})
    assert [(h["protocol"], h["host"], h["port"]) for h in hotes] == [("vnc", "10.0.0.8", 5900)]


def test_ip_par_arp_mac(monkeypatch):
    """Guestproperty muette : l'IP vient de la table ARP du noyau (MAC)."""
    Hyperviseur(
        {
            "which VBoxManage": "/usr/bin/VBoxManage",
            "list vms": '"vm-web" {abc}\n',
            "list runningvms": '"vm-web" {abc}\n',
            "showvminfo": MACHINEREADABLE,
            "guestproperty get": "",
            "ip neigh show": "10.0.0.9 dev vboxnet0 lladdr 08:00:27:aa:bb:cc REACHABLE\n",
        }
    ).installer(monkeypatch)
    hotes, _, _ = collecter(["vbox+ssh://root@vbox.example"], proto_filter={"ssh"})
    assert [(h["protocol"], h["host"]) for h in hotes] == [("ssh", "10.0.0.9")]


def test_ip_inconnue_ssh_via_hote(monkeypatch):
    """VM arrêtée sans IP : SSH rebondi via l'hôte, ``-t ssh admin@vm``."""
    Hyperviseur(
        {
            "which VBoxManage": "/usr/bin/VBoxManage",
            "list vms": '"vm-web" {abc}\n',
            "list runningvms": "",
            "showvminfo": 'name="vm-web"\nvrde="off"\nvrdeport="0"\nostype="Linux_64"\n',
            "ip neigh show": "",
        }
    ).installer(monkeypatch)
    hotes, _, _ = collecter(["vbox+ssh://root@vbox.example"], proto_filter={"ssh"})
    assert [(h["protocol"], h["host"], h["user"]) for h in hotes] == [
        ("ssh", "vbox.example", "root")
    ]
    assert hotes[0]["extra_params"] == "-t ssh admin@vm-web"


def test_port_ssh_non_standard(monkeypatch):
    """URI avec port : le ProxyJump et le SSH rebondi portent le port."""
    Hyperviseur(
        {
            "which VBoxManage": "/usr/bin/VBoxManage",
            "list vms": '"vm-web" {abc}\n',
            "list runningvms": '"vm-web" {abc}\n',
            "showvminfo": MACHINEREADABLE,
            "guestproperty get": "Value: 10.0.0.8",
            "ip neigh show": "",
        }
    ).installer(monkeypatch)
    hotes, _, _ = collecter(["vbox+ssh://root@vbox.example:2222"], proto_filter={"ssh"})
    assert hotes[0]["extra_params"] == "-J root@vbox.example:2222"


def test_normaliser_cible():
    """Saisie libre → URI vbox+ssh : URI complète, utilisateur vide, port."""
    assert vb.normaliser_cible("") == ""
    assert vb.normaliser_cible("vbox+ssh://admin@h") == "vbox+ssh://admin@h"
    assert vb.normaliser_cible("@h.example") == "vbox+ssh://root@h.example/"
    assert vb.normaliser_cible("h.example:22") == "vbox+ssh://root@h.example/"
    assert vb.normaliser_cible("h.example:abc") == "vbox+ssh://root@h.example:abc/"
    assert vb.normaliser_cible("h.example:2222") == "vbox+ssh://root@h.example:2222/"
    assert vb.normaliser_cible("h.example", utilisateur_defaut="admin") == (
        "vbox+ssh://admin@h.example/"
    )
