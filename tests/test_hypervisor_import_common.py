"""Helpers d'import hyperviseur de ``hypervisor_import_common.py`` (#293, palier 2).

``tests/test_gcm.py`` couvre déjà ``vm_name_split``, une partie de
``dep_install_hint`` et ``check_port_open``. Ici : collecte des clés SSH,
``paramiko_connect`` (paramiko remplacé par une doublure : clés illisibles,
authentification refusée, erreur réseau, repli sur l'agent), scan nmap des
sous-réseaux, lecture dconf, et repli quand paramiko est absent. Aucun
réseau, aucun processus externe.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hypervisor_import_common as hic  # noqa: E402

CLE_PRIVEE = b"-----BEGIN OPENSSH PRIVATE KEY-----\n"


@pytest.fixture
def ssh_dir(tmp_path, monkeypatch):
    """HOME temporaire avec un dossier ~/.ssh vide."""
    monkeypatch.setenv("HOME", str(tmp_path))
    dossier = tmp_path / ".ssh"
    dossier.mkdir()
    return dossier


class TestDepInstallHint:
    """Commande d'installation selon /etc/os-release."""

    @pytest.mark.parametrize(
        ("os_release", "attendu"),
        [
            ("ID=opensuse-leap", "sudo zypper install deb"),
            ("ID=manjaro", "sudo pacman -S arch"),
            (
                "ID=plan9",
                "Debian/Ubuntu : sudo apt install deb\n  Fedora/RHEL   : sudo dnf install rpm",
            ),
        ],
    )
    def test_distributions(self, monkeypatch, tmp_path, os_release, attendu) -> None:
        """Cas openSUSE, Arch et distribution inconnue."""
        fichier = tmp_path / "os-release"
        fichier.write_text(os_release)
        vrai_open = open
        monkeypatch.setattr(
            "builtins.open",
            lambda p, *a, **k: vrai_open(fichier if p == "/etc/os-release" else p, *a, **k),
        )
        assert hic.dep_install_hint("deb", "rpm", "arch") == attendu


class TestCollecteDesCles:
    """Clés privées de ~/.ssh, Ed25519 d'abord."""

    def test_sans_dossier_ssh(self, tmp_path, monkeypatch) -> None:
        """Sans ~/.ssh, aucune clé."""
        monkeypatch.setenv("HOME", str(tmp_path))
        assert hic.collect_ssh_keys() == []

    def test_tri_et_filtrage(self, ssh_dir, monkeypatch) -> None:
        """Publiques, known_hosts, non-clés, dossiers et fichiers illisibles écartés ; tri Ed25519 > RSA > autres."""
        (ssh_dir / "id_rsa").write_bytes(CLE_PRIVEE)
        (ssh_dir / "id_ed25519").write_bytes(CLE_PRIVEE)
        (ssh_dir / "perso").write_bytes(b"-----BEGIN RSA PRIVATE KEY-----\n")
        (ssh_dir / "autre").write_bytes(CLE_PRIVEE)
        (ssh_dir / "id_rsa.pub").write_bytes(b"ssh-rsa AAAA")
        (ssh_dir / "known_hosts").write_bytes(b"h ssh-ed25519 AAAA")
        (ssh_dir / "notes.txt").write_bytes(b"pas une cle")
        (ssh_dir / "sous-dossier").mkdir()
        (ssh_dir / "illisible").write_bytes(CLE_PRIVEE)
        vrai_open = open

        def ouvrir(chemin, *args, **kwargs):
            if str(chemin).endswith("illisible"):
                raise PermissionError("accès refusé")
            return vrai_open(chemin, *args, **kwargs)

        monkeypatch.setattr("builtins.open", ouvrir)
        noms = [Path(c).name for c in hic.collect_ssh_keys()]
        assert noms[0] == "id_ed25519"
        assert sorted(noms[1:3]) == ["id_rsa", "perso"]
        assert noms[3:] == ["autre"]


class _ErreurAuth(Exception):
    pass


class FauxParamiko:
    """Doublure de paramiko : chaque clé dit si elle charge, chaque hôte comment il répond."""

    AuthenticationException = _ErreurAuth

    def __init__(
        self, cles_valides: set[str], refus: dict[str, Exception], agent: Exception | None = None
    ):
        """Clés chargeables, refus par clé et comportement de l'agent."""
        self.cles_valides = cles_valides
        self.refus = refus
        self.agent = agent
        self.connexions: list[dict] = []
        doublure = self

        class Cle:
            @staticmethod
            def from_private_key_file(chemin):
                if Path(chemin).name not in doublure.cles_valides:
                    raise ValueError("format de clé inconnu")
                return SimpleNamespace(chemin=chemin)

        class Client:
            def set_missing_host_key_policy(self, politique):
                self.politique = politique

            def connect(self, **kwargs):
                doublure.connexions.append(kwargs)
                pkey = kwargs.get("pkey")
                if pkey is None:
                    if doublure.agent is not None:
                        raise doublure.agent
                    return
                erreur = doublure.refus.get(Path(pkey.chemin).name)
                if erreur is not None:
                    raise erreur

        self.Ed25519Key = Cle
        self.RSAKey = Cle
        self.SSHClient = Client
        self.AutoAddPolicy = lambda: "auto"


class TestParamikoConnect:
    """Connexion SSH par clé puis par agent."""

    def test_paramiko_absent(self, monkeypatch) -> None:
        """Sans paramiko : None et conseil d'installation."""
        monkeypatch.setattr(hic, "PARAMIKO_OK", False)
        messages = []
        assert hic.paramiko_connect("h", 22, "u", messages.append) is None
        assert messages[0].startswith("ERREUR : paramiko non installé")
        assert hic.paramiko_connect("h", 22, "u") is None

    def test_cle_refusee_puis_cle_acceptee(self, ssh_dir, monkeypatch) -> None:
        """Authentification refusée sur une clé, acceptée sur la suivante."""
        for nom in ("id_ed25519", "id_rsa", "autre"):
            (ssh_dir / nom).write_bytes(CLE_PRIVEE)
        faux = FauxParamiko({"id_ed25519", "id_rsa"}, {"id_ed25519": _ErreurAuth("refus")})
        monkeypatch.setattr(hic, "PARAMIKO_OK", True)
        monkeypatch.setattr(hic, "paramiko", faux, raising=False)
        messages = []
        client = hic.paramiko_connect("h", 2222, "u", messages.append)
        assert client is not None
        assert [Path(c["pkey"].chemin).name for c in faux.connexions] == ["id_ed25519", "id_rsa"]
        assert messages == ["  SSH OK → u@h:2222 [id_rsa]"]

    def test_cle_acceptee_sans_journal(self, ssh_dir, monkeypatch) -> None:
        """Première clé acceptée, sans fonction de journal."""
        (ssh_dir / "id_rsa").write_bytes(CLE_PRIVEE)
        monkeypatch.setattr(hic, "PARAMIKO_OK", True)
        monkeypatch.setattr(hic, "paramiko", FauxParamiko({"id_rsa"}, {}), raising=False)
        assert hic.paramiko_connect("h", 22, "u") is not None

    @pytest.mark.parametrize("avec_journal", [True, False])
    def test_erreur_reseau_puis_agent(self, ssh_dir, monkeypatch, avec_journal) -> None:
        """Erreur réseau sur la clé, repli réussi sur l'agent."""
        (ssh_dir / "id_rsa").write_bytes(CLE_PRIVEE)
        faux = FauxParamiko({"id_rsa"}, {"id_rsa": OSError("hôte injoignable")})
        monkeypatch.setattr(hic, "PARAMIKO_OK", True)
        monkeypatch.setattr(hic, "paramiko", faux, raising=False)
        messages = []
        client = hic.paramiko_connect("h", 22, "u", messages.append if avec_journal else None)
        assert client is not None
        assert faux.connexions[-1]["allow_agent"] is True
        if avec_journal:
            assert messages == [
                "  Erreur clé id_rsa : hôte injoignable",
                "  SSH OK → u@h:22 [agent]",
            ]

    @pytest.mark.parametrize("avec_journal", [True, False])
    def test_tout_echoue(self, ssh_dir, monkeypatch, avec_journal) -> None:
        """Aucune clé ni agent : None."""
        faux = FauxParamiko(set(), {}, agent=OSError("agent absent"))
        monkeypatch.setattr(hic, "PARAMIKO_OK", True)
        monkeypatch.setattr(hic, "paramiko", faux, raising=False)
        messages = []
        assert (
            hic.paramiko_connect("h", 22, "u", messages.append if avec_journal else None) is None
        )
        assert messages == (
            ["  ERREUR SSH (toutes clés échouées) : agent absent"] if avec_journal else []
        )

    def test_cle_illisible_ignoree(self, ssh_dir, monkeypatch) -> None:
        """Clé qu'aucune classe ne charge : passage direct à l'agent."""
        (ssh_dir / "id_rsa").write_bytes(CLE_PRIVEE)
        faux = FauxParamiko(set(), {})
        monkeypatch.setattr(hic, "PARAMIKO_OK", True)
        monkeypatch.setattr(hic, "paramiko", faux, raising=False)
        assert hic.paramiko_connect("h", 22, "u") is not None
        assert [c.get("pkey") for c in faux.connexions] == [None]


def test_import_sans_paramiko(monkeypatch) -> None:
    """Le module se charge sans paramiko et le signale par PARAMIKO_OK."""
    monkeypatch.setitem(sys.modules, "paramiko", None)
    spec = importlib.util.spec_from_file_location("hic_sans_paramiko", hic.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PARAMIKO_OK is False


class TestDconf:
    """URIs libvirt de virt-manager lues dans dconf."""

    @pytest.mark.parametrize(
        ("sortie", "attendu"),
        [
            (
                "['qemu:///system', 'qemu+ssh://root@h/system']\n",
                ["qemu:///system", "qemu+ssh://root@h/system"],
            ),
            ("@as []\n", []),
            ("", []),
        ],
    )
    def test_lecture(self, monkeypatch, sortie, attendu) -> None:
        """Liste, liste vide GVariant et sortie vide."""
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=sortie))
        assert hic.libvirt_get_uris_from_dconf() == attendu

    def test_dconf_absent(self, monkeypatch) -> None:
        """Sans dconf : liste vide."""

        def absent(*args, **kwargs):
            raise FileNotFoundError("dconf")

        monkeypatch.setattr(subprocess, "run", absent)
        assert hic.libvirt_get_uris_from_dconf() == []


def test_ssh_run_decode_la_sortie() -> None:
    """Sortie décodée avec remplacement et espaces retirés."""
    sortie = SimpleNamespace(read=lambda: b"  ok\xff \n")
    client = SimpleNamespace(exec_command=lambda cmd, timeout: (None, sortie, None))
    assert hic.libvirt_ssh_run(client, "uptime") == "ok\ufffd"


XML_NMAP = """<nmaprun>
<host><address addr="10.0.0.5" addrtype="ipv4"/><address addr="AA:BB:CC:00:11:22" addrtype="mac"/></host>
<host><address addr="10.0.0.1" addrtype="ipv4"/></host>
</nmaprun>"""


class TestScanNmap:
    """Scan nmap des sous-réseaux de l'hyperviseur."""

    @staticmethod
    def _hyperviseur(reponses: dict[str, str]):
        commandes = []

        def run(client, cmd, timeout=30):
            commandes.append(cmd)
            return next((v for k, v in reponses.items() if cmd.startswith(k)), "")

        return run, commandes

    ADRESSES = "\n".join(
        [
            "1: lo    inet 127.0.0.1/8 scope host lo",
            "2: eth0  inet 10.0.0.2/24 brd 10.0.0.255",
            "3: br0   inet 10.0.0.3/24 brd 10.0.0.255",
            "4: eth1  inet 169.254.1.1/16",
            "5: wg0   inet 192.168.7.9/30",
            "6: eth2  inet6 fe80::1/64",
        ]
    )

    def test_scan_complet(self) -> None:
        """Sous-réseaux dédoublonnés, loopback et lien local écartés, MAC associées aux IP."""
        run, commandes = self._hyperviseur(
            {
                "ip -o": self.ADRESSES,
                "which nmap": "/usr/bin/nmap",
                "nmap --version": "Nmap version 7.94 ( https://nmap.org )",
                "nmap -sn 10.0.0.0/24": XML_NMAP,
            }
        )
        messages = []
        assert hic.libvirt_nmap_scan(object(), run, messages.append) == {
            "aa:bb:cc:00:11:22": "10.0.0.5"
        }
        assert [c for c in commandes if c.startswith("nmap -sn")] == [
            "nmap -sn 10.0.0.0/24 -oX - --host-timeout 5s 2>/dev/null",
            "nmap -sn 192.168.7.8/30 -oX - --host-timeout 5s 2>/dev/null",
        ]
        assert messages == [
            "  nmap -sn 10.0.0.0/24…",
            "    → 1 hôte(s) avec MAC sur 10.0.0.0/24",
            "  nmap -sn 192.168.7.8/30…",
        ]

    def test_ancien_nmap(self) -> None:
        """Avec nmap < 6 : option -sP ; sortie XML vide ignorée."""
        run, commandes = self._hyperviseur(
            {
                "ip -o": "2: eth0 inet 10.0.0.2/24",
                "which nmap": "/usr/bin/nmap",
                "nmap --version": "Nmap 5.0",
            }
        )
        assert hic.libvirt_nmap_scan(object(), run, lambda m: None) == {}
        assert commandes[-1].startswith("nmap -sP 10.0.0.0/24")

    def test_sans_nmap(self) -> None:
        """Sans nmap : scan ignoré avec message."""
        run, _ = self._hyperviseur({"ip -o": "2: eth0 inet 10.0.0.2/24"})
        messages = []
        assert hic.libvirt_nmap_scan(object(), run, messages.append) == {}
        assert messages == ["  nmap non disponible sur l'hyperviseur — scan réseau ignoré"]

    def test_sans_sous_reseau(self) -> None:
        """Aucun sous-réseau utile : nmap n'est pas cherché."""
        run, commandes = self._hyperviseur({"ip -o": "1: lo inet 127.0.0.1/8"})
        assert hic.libvirt_nmap_scan(object(), run, lambda m: None) == {}
        assert commandes == ["ip -o -f inet addr show"]
