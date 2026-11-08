# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique sans GTK du plugin spice (``plugins/spice/core.py``, #179)."""

from __future__ import annotations

import json
import os
import stat

import pytest

import plugins.spice.core as core
from gcm4.core.model import Host


def _hote(**champs):
    """Hôte ``spice`` de test."""
    base = {"name": "vm1", "host": "kvm.example.org", "protocol": "spice"}
    base.update(champs)
    return Host(**base)


def test_options_defaut_comme_gtk3():
    """Mode URI, presse-papier partagé, le reste non partagé, booléens ``True``/``False``."""
    options = core.options_defaut()
    assert options[core.OPT_MODE] == "uri"
    assert options[core.OPT_PRESSE_PAPIER] == "True"
    assert {options[c] for c in (core.OPT_USB, core.OPT_AUDIO, core.OPT_DOSSIER)} == {"False"}
    assert set(options) == {
        "spice_mode",
        "spice_tls_port",
        "spice_ca_cert",
        "spice_libvirt_uri",
        "spice_vm_name",
        "spice_px_node",
        "spice_px_vmid",
        "spice_share_clipboard",
        "spice_share_usb",
        "spice_share_audio",
        "spice_share_folder",
        "spice_shared_folder_path",
    }


@pytest.mark.parametrize(
    "brut, defaut, attendu",
    [("True", False, True), ("no", True, False), ("", True, True), ("?", False, False)],
)
def test_lire_booleen(brut, defaut, attendu):
    """Relu comme ``configparser`` ; absent ou illisible : défaut."""
    assert core.lire_booleen({"k": brut}, "k", defaut) is attendu


@pytest.mark.parametrize(
    "brut, attendu",
    [("", "uri"), ("URI", "uri"), ("libvirt", "libvirt"), (" proxmox ", "proxmox"), ("x", "uri")],
)
def test_mode(brut, attendu):
    """Mode inconnu ou vide : ``uri`` comme GTK3."""
    assert core.mode({core.OPT_MODE: brut}) == attendu


def test_valider_uri():
    """Port TLS facultatif mais numérique de 1 à 65535."""
    assert core.valider({}) == []
    assert core.valider({core.OPT_PORT_TLS: "5931"}) == []
    for faux in ("0", "70000", "abc"):
        assert [e.champ for e in core.valider({core.OPT_PORT_TLS: faux})] == [core.OPT_PORT_TLS]


def test_valider_libvirt():
    """URI libvirt obligatoire, nom de VM facultatif."""
    options = {core.OPT_MODE: "libvirt"}
    assert [e.champ for e in core.valider(options)] == [core.OPT_URI_LIBVIRT]
    options[core.OPT_URI_LIBVIRT] = "qemu+ssh://root@kvm/system"
    assert core.valider(options) == []


def test_valider_proxmox():
    """Nœud (nom simple, sans caractère de shell) et VMID numérique obligatoires."""
    options = {core.OPT_MODE: "proxmox"}
    assert [e.champ for e in core.valider(options)] == [core.OPT_NOEUD, core.OPT_VMID]
    options.update({core.OPT_NOEUD: "pve;rm", core.OPT_VMID: "10a"})
    assert [e.champ for e in core.valider(options)] == [core.OPT_NOEUD, core.OPT_VMID]
    options.update({core.OPT_NOEUD: "pve-1.lan", core.OPT_VMID: "100"})
    assert core.valider(options) == []


def test_valider_dossier_partage():
    """Partage de dossier coché : chemin obligatoire."""
    options = {core.OPT_DOSSIER: "True"}
    assert [e.champ for e in core.valider(options)] == [core.OPT_CHEMIN_DOSSIER]
    options[core.OPT_CHEMIN_DOSSIER] = "~/Public"
    assert core.valider(options) == []


def test_trouver_visionneuse():
    """``remote-viewer`` pour URI et Proxmox, ``virt-viewer`` pour libvirt."""
    vus = []

    def which(nom):
        vus.append(nom)
        return None if nom == "virt-viewer" else f"/usr/bin/{nom}"

    assert core.trouver_visionneuse("uri", which) == "/usr/bin/remote-viewer"
    assert core.trouver_visionneuse("proxmox", which) == "/usr/bin/remote-viewer"
    assert core.trouver_visionneuse("libvirt", which) is None
    assert vus == ["remote-viewer", "remote-viewer", "virt-viewer"]


def test_options_partage():
    """USB et audio coupés par défaut ; dossier partagé si coché et rempli, ``~`` développé."""
    assert core.options_partage({}) == ["--spice-disable-usbredir", "--spice-disable-audio"]
    options = {
        core.OPT_USB: "True",
        core.OPT_AUDIO: "True",
        core.OPT_DOSSIER: "True",
        core.OPT_CHEMIN_DOSSIER: "~/Public",
    }
    assert core.options_partage(options) == [
        "--spice-shared-dir=" + os.path.expanduser("~/Public")
    ]
    options[core.OPT_DOSSIER] = "False"
    assert core.options_partage(options) == []


def test_parametres_supplementaires():
    """Découpés comme un shell ; guillemet non fermé : ``ValueError`` traduite."""
    assert core.parametres_supplementaires(_hote(extra_params="-f --zoom 90")) == [
        "-f",
        "--zoom",
        "90",
    ]
    assert core.parametres_supplementaires(_hote()) == []
    with pytest.raises(ValueError, match="extra parameters"):
        core.parametres_supplementaires(_hote(extra_params='-t "x'))


def test_titre():
    """Nom de l'hôte, ou son adresse sans nom."""
    assert core.titre(_hote()) == "vm1"
    assert core.titre(_hote(name="")) == "kvm.example.org"


def test_champs_vv_direct():
    """Adresse, port (5930 par défaut), port TLS et mot de passe s'ils existent."""
    champs = core.champs_vv_direct(_hote(), {})
    assert champs == {
        "type": "spice",
        "host": "kvm.example.org",
        "port": "5930",
        "title": "vm1",
        "delete-this-file": "1",
    }
    champs = core.champs_vv_direct(
        _hote(port=5901, password="s3cret"), {core.OPT_PORT_TLS: "5902"}
    )
    assert (champs["port"], champs["tls-port"], champs["password"]) == ("5901", "5902", "s3cret")


def test_contenu_vv_echappements_gkeyfile():
    r"""Section ``[virt-viewer]`` ; ``\``, retours à la ligne et espace de tête échappés."""
    contenu = core.contenu_vv({"type": "spice", "password": " a\\b", "ca": "L1\r\nL2"})
    assert contenu == "[virt-viewer]\ntype=spice\npassword=\\sa\\\\b\nca=L1\\nL2\n"


def test_ecrire_vv_prive(tmp_path):
    """Fichier ``gcm4-spice-*.vv`` en ``0600`` dans le dossier demandé."""
    chemin = core.ecrire_vv({"type": "spice", "password": "x"}, str(tmp_path))
    assert chemin.parent == tmp_path and chemin.name.startswith("gcm4-spice-")
    assert chemin.suffix == ".vv"
    assert stat.S_IMODE(chemin.stat().st_mode) == 0o600
    assert chemin.read_text(encoding="utf-8") == "[virt-viewer]\ntype=spice\npassword=x\n"


def test_ecrire_vv_dossier_par_defaut(tmp_path, monkeypatch):
    """Défaut : ``$XDG_RUNTIME_DIR`` s'il existe, sinon le dossier temporaire."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    chemin = core.ecrire_vv({"type": "spice"})
    assert chemin.parent == tmp_path
    chemin.unlink()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(core.tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    chemin = core.ecrire_vv({"type": "spice"})
    assert chemin.parent == tmp_path / "tmp"


def test_commande_vv(tmp_path):
    """Partages, certificat, paramètres supplémentaires puis le ``.vv`` en dernier."""
    vv = tmp_path / "s.vv"
    options = {core.OPT_CA: "~/ca.pem", core.OPT_USB: "True", core.OPT_AUDIO: "True"}
    hote = _hote(extra_params="-f")
    assert core.commande_vv("/bin/rv", vv, hote, options) == [
        "/bin/rv",
        "--spice-ca-file=" + os.path.expanduser("~/ca.pem"),
        "-f",
        str(vv),
    ]
    assert core.commande_vv("/bin/rv", vv, hote, options, ca=False) == ["/bin/rv", "-f", str(vv)]


def test_commande_libvirt():
    """``--connect <URI>``, titre, partages, paramètres, puis la VM si elle est nommée."""
    options = {core.OPT_URI_LIBVIRT: "qemu:///system", core.OPT_VM: "web"}
    assert core.commande_libvirt("/bin/vv", _hote(extra_params="-a"), options) == [
        "/bin/vv",
        "--connect",
        "qemu:///system",
        "--title",
        "vm1",
        "--spice-disable-usbredir",
        "--spice-disable-audio",
        "-a",
        "web",
    ]
    del options[core.OPT_VM]
    assert core.commande_libvirt("/bin/vv", _hote(), options)[-1] == "--spice-disable-audio"


@pytest.mark.parametrize("port, attendu", [(None, 22), (5930, 22), (5900, 22), (2222, 2222)])
def test_port_ssh(port, attendu):
    """Port ssh du nœud : celui de l'hôte sauf vide ou port d'affichage (GTK3)."""
    assert core.port_ssh(_hote(port=port)) == attendu


def test_commande_ticket():
    """Commande ssh non interactive vers le nœud ; ``pvesh`` cité, ``--proxy`` sur l'adresse de l'hôte."""
    options = {core.OPT_NOEUD: "pve", core.OPT_VMID: "100"}
    argv = core.commande_ticket(_hote(port=2222, user="admin"), options)
    assert argv == [
        "ssh",
        "-p",
        "2222",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-l",
        "admin",
        "kvm.example.org",
        "pvesh create /nodes/pve/qemu/100/spiceproxy --proxy kvm.example.org --output-format json",
    ]
    assert core.commande_ticket(_hote(host="a b"), options)[8:10] == ["root", "a b"]
    assert "--proxy 'a b'" in core.commande_ticket(_hote(host="a b"), options)[-1]


def test_lire_ticket():
    """Champs du ticket repris, ``delete-this-file`` forcé, ``type`` ajouté s'il manque."""
    ticket = {
        "host": "pvespiceproxy:abc",
        "password": "t1",
        "proxy": "http://pve:3128",
        "tls-port": 61000,
        "ca": "-----BEGIN-----\nX\n-----END-----",
        "delete-this-file": 0,
        "title": None,
    }
    champs = core.lire_ticket(json.dumps(ticket))
    assert champs["type"] == "spice"
    assert champs["delete-this-file"] == "1"
    assert champs["tls-port"] == "61000"
    assert "title" not in champs
    assert champs["ca"].count("\n") == 2


@pytest.mark.parametrize(
    "sortie, motif",
    [
        ("pas du json", "pas du json"),
        ("[1, 2]", "incomplete"),
        (json.dumps({"host": "h"}), "incomplete"),
        (json.dumps({"password": "secret-a-ne-pas-montrer"}), "incomplete"),
    ],
)
def test_lire_ticket_refuse(sortie, motif):
    """Sortie illisible ou ticket incomplet : ``ErreurTicket`` sans le mot de passe."""
    with pytest.raises(core.ErreurTicket, match=motif) as erreur:
        core.lire_ticket(sortie)
    assert "secret" not in str(erreur.value)
