# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique du gestionnaire de clés SSH (#293, palier 10).

``ssh_key_manager_dialog.py`` (GTK3, 0 % de couverture avant ce palier)
mêle widgets et logique métier. Seule la logique est testée ici, sans
GTK ni outil système :

- fonctions de module : commentaire par défaut, presse-papiers (GTK puis
  repli wl-copy/xclip/xsel), date de création, certificats OpenSSH,
  empreintes, analyse de ``known_hosts``, ``authorized_keys`` et des
  ``Host`` de ``~/.ssh/config`` ;
- ``SSHTargetSpec`` et les adaptateurs ``_LocalFS`` / ``_RemoteFS``
  (lecture, écriture atomique base64, test de connexion) ;
- méthodes de ``SSHKeyManagerDialog`` qui ne construisent pas d'interface :
  CA désignée, scan de ``~/.ssh/``, permissions, agent, hôtes candidats,
  rechargement et édition de ``known_hosts`` / ``authorized_keys``,
  garde-fou root, bascule local ↔ distant.

``subprocess.run`` est remplacé par un faux qui répond selon la commande
(``ssh-keygen``, ``stat``, ``ssh``, ``ssh-add``…) ; le dialogue est créé
sans ``__init__`` (``object.__new__``), ses magasins de lignes et ses
notifications sont des doublures. Les doublures ``gi`` de
``tests/test_gcm.py`` servent à l'import du module.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
)

import test_gcm  # noqa: E402, F401  (pose les doublures gi/Gtk avant l'import)

import ssh_key_manager_dialog as skm  # noqa: E402

CLE_ED = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ0 alice@poste"
CLE_RSA = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC bob@poste"


def _res(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    """Résultat de ``subprocess.run`` minimal."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FauxRun:
    """Remplace ``subprocess.run`` : réponses par nom de commande, appels gardés."""

    def __init__(self, reponses: dict[str, Any] | None = None) -> None:
        """Initialise la doublure.

        Args:
            reponses: commande (premier argument, ou ``"ssh-add -l"``…) →
                résultat, exception à lever, ou fonction ``(cmd, kwargs)``.
        """
        self.reponses = reponses or {}
        self.appels: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        """Rend la réponse prévue pour ``cmd``."""
        self.appels.append(list(cmd))
        for cle in (" ".join(cmd[:2]), cmd[0]):
            if cle in self.reponses:
                reponse = self.reponses[cle]
                if isinstance(reponse, BaseException):
                    raise reponse
                if callable(reponse):
                    return reponse(cmd, kwargs)
                return reponse
        return _res(1, "", "commande inconnue")


@pytest.fixture
def faux_run(monkeypatch: pytest.MonkeyPatch) -> FauxRun:
    """Installe un ``FauxRun`` vide dans le module."""
    faux = FauxRun()
    monkeypatch.setattr(skm.subprocess, "run", faux)
    return faux


# ---------------------------------------------------------------------------
# Fonctions de module
# ---------------------------------------------------------------------------


class TestCommentaireParDefaut:
    """Commentaire par défaut ``user@host`` des nouvelles clés."""

    def test_utilisateur_et_hote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Utilisateur et hôte."""
        monkeypatch.setattr(skm.getpass, "getuser", lambda: "alice")
        monkeypatch.setattr(skm.socket, "gethostname", lambda: "poste")
        assert skm.get_default_ssh_key_comment() == "alice@poste"

    def test_replis_quand_tout_echoue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replis quand tout échoue."""

        def echec() -> str:
            """Échec."""
            raise OSError("pas d'utilisateur")

        monkeypatch.setattr(skm.getpass, "getuser", echec)
        monkeypatch.setattr(skm.socket, "gethostname", echec)
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.setenv("USERNAME", "bob")
        assert skm.get_default_ssh_key_comment() == "bob@localhost"

    def test_repli_ultime_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Repli ultime user."""

        def echec() -> str:
            """Échec."""
            raise KeyError("x")

        monkeypatch.setattr(skm.getpass, "getuser", echec)
        monkeypatch.delenv("USER", raising=False)
        monkeypatch.delenv("USERNAME", raising=False)
        assert skm.get_default_ssh_key_comment().startswith("user@")


class TestPressePapiers:
    """Copie dans le presse-papiers (GTK puis outils système)."""

    def test_gtk_d_abord(self, monkeypatch: pytest.MonkeyPatch, faux_run: FauxRun) -> None:
        """GTK d'abord."""
        copies: list[str] = []
        clip = SimpleNamespace(set_text=lambda texte, _n: copies.append(texte))
        monkeypatch.setattr(
            skm, "Gtk", SimpleNamespace(Clipboard=SimpleNamespace(get=lambda _atome: clip))
        )
        monkeypatch.setattr(
            skm, "Gdk", SimpleNamespace(Atom=SimpleNamespace(intern=lambda *_a: "CLIPBOARD"))
        )
        assert skm._copy_text_to_clipboard("SHA256:abc") is True
        assert copies == ["SHA256:abc"]
        assert faux_run.appels == []

    def _gtk_en_echec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GTK en échec."""

        def echec(_atome: object) -> None:
            """Échec."""
            raise RuntimeError("pas d'affichage")

        monkeypatch.setattr(skm, "Gtk", SimpleNamespace(Clipboard=SimpleNamespace(get=echec)))
        monkeypatch.setattr(
            skm, "Gdk", SimpleNamespace(Atom=SimpleNamespace(intern=lambda *_a: "CLIPBOARD"))
        )

    def test_repli_sur_les_outils_dans_l_ordre(
        self, monkeypatch: pytest.MonkeyPatch, faux_run: FauxRun
    ) -> None:
        """Repli sur les outils dans l'ordre."""
        self._gtk_en_echec(monkeypatch)
        faux_run.reponses = {"wl-copy": FileNotFoundError("wl-copy"), "xclip": _res(0)}
        assert skm._copy_text_to_clipboard("texte") is True
        assert [a[0] for a in faux_run.appels] == ["wl-copy", "xclip"]

    def test_aucun_outil_disponible(
        self, monkeypatch: pytest.MonkeyPatch, faux_run: FauxRun
    ) -> None:
        """Aucun outil disponible."""
        self._gtk_en_echec(monkeypatch)
        faux_run.reponses = {"wl-copy": _res(1), "xclip": _res(1), "xsel": OSError("absent")}
        assert skm._copy_text_to_clipboard("texte") is False
        assert [a[0] for a in faux_run.appels] == ["wl-copy", "xclip", "xsel"]


class TestDateDeCreation:
    """Date de création d'un fichier de clé."""

    def test_birth_time_de_stat(self, tmp_path: Path, faux_run: FauxRun) -> None:
        """Birth time de stat."""
        fichier = tmp_path / "id_ed25519"
        fichier.write_text("x")
        faux_run.reponses = {"stat": _res(0, "1700000000\n")}
        assert skm._get_creation_datetime(fichier) == datetime.fromtimestamp(1700000000)

    @pytest.mark.parametrize("sortie", ["0\n", "-\n", "?\n"])
    def test_repli_sur_ctime_si_birth_time_inconnu(
        self, tmp_path: Path, faux_run: FauxRun, sortie: str
    ) -> None:
        """Repli sur ctime si birth time inconnu."""
        fichier = tmp_path / "id"
        fichier.write_text("x")
        faux_run.reponses = {"stat": _res(0, sortie)}
        assert skm._get_creation_datetime(fichier) == datetime.fromtimestamp(
            fichier.stat().st_ctime
        )

    def test_repli_sur_ctime_si_stat_absent(self, tmp_path: Path, faux_run: FauxRun) -> None:
        """Repli sur ctime si stat absent."""
        fichier = tmp_path / "id"
        fichier.write_text("x")
        faux_run.reponses = {"stat": FileNotFoundError("stat")}
        assert skm._get_creation_datetime(fichier) is not None

    def test_fichier_absent(self, tmp_path: Path, faux_run: FauxRun) -> None:
        """Fichier absent."""
        faux_run.reponses = {"stat": _res(1)}
        assert skm._get_creation_datetime(tmp_path / "absent") is None


class TestCertificat:
    """Lecture d'un certificat OpenSSH (``ssh-keygen -L``)."""

    SORTIE = (
        "id_ed25519-cert.pub:\n"
        "        Type: ssh-ed25519-cert-v01@openssh.com user certificate\n"
        "        Signing CA: ED25519 SHA256:abcdefghijklmnopqrstuvwxyz0123456789 (using ssh-ed25519)\n"
        "        Valid: {valid}\n"
    )

    def test_valide_jusqu_a_une_date_future(self, faux_run: FauxRun) -> None:
        """Valide jusqu'à une date future."""
        fin = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
        faux_run.reponses = {
            "ssh-keygen": _res(0, self.SORTIE.format(valid=f"from 2026-01-01T00:00:00 to {fin}"))
        }
        info = skm._parse_cert_info(Path("c-cert.pub"))
        assert info["expired"] is False
        assert fin in str(info["valid_str"])
        assert info["signed_by"] == "ED25519 SHA256:abcdefghijklmnopq"

    def test_expire(self, faux_run: FauxRun) -> None:
        """Expirée."""
        faux_run.reponses = {
            "ssh-keygen": _res(
                0, self.SORTIE.format(valid="from 2020-01-01T00:00:00 to 2021-01-01T00:00:00")
            )
        }
        assert skm._parse_cert_info(Path("c")).get("expired") is True

    def test_date_illisible_non_expire(self, faux_run: FauxRun) -> None:
        """Date illisible non expirée."""
        faux_run.reponses = {
            "ssh-keygen": _res(0, self.SORTIE.format(valid="from 2020-01-01 to demain"))
        }
        info = skm._parse_cert_info(Path("c"))
        assert info["expired"] is False
        assert "demain" in str(info["valid_str"])

    def test_pour_toujours(self, faux_run: FauxRun) -> None:
        """Pour toujours."""
        faux_run.reponses = {"ssh-keygen": _res(0, self.SORTIE.format(valid="forever"))}
        info = skm._parse_cert_info(Path("c"))
        assert info["expired"] is False
        assert info["valid_str"] == "forever"

    def test_sans_ligne_valid_ni_ca(self, faux_run: FauxRun) -> None:
        """Sans ligne valid ni ca."""
        faux_run.reponses = {"ssh-keygen": _res(0, "Type: inconnu\n")}
        assert skm._parse_cert_info(Path("c")) == {
            "valid_str": "?",
            "expired": False,
            "signed_by": "unknown CA",
        }

    @pytest.mark.parametrize("reponse", [_res(255, "", "erreur"), FileNotFoundError("ssh-keygen")])
    def test_ssh_keygen_en_echec(self, faux_run: FauxRun, reponse: Any) -> None:
        """Ssh keygen en échec."""
        faux_run.reponses = {"ssh-keygen": reponse}
        assert skm._parse_cert_info(Path("c")) == {
            "valid_str": "?",
            "expired": False,
            "signed_by": "?",
        }


class TestEmpreinte:
    """Empreinte d'une ligne de clé publique."""

    def test_empreinte_et_fichier_temporaire_supprime(self, faux_run: FauxRun) -> None:
        """Empreinte et fichier temporaire supprimé."""
        vus: list[str] = []

        def keygen(cmd: list[str], _kwargs: dict[str, Any]) -> SimpleNamespace:
            """Simule ssh-keygen et lit le fichier temporaire."""
            chemin = cmd[-1]
            vus.append(Path(chemin).read_text())
            return _res(0, "256 SHA256:" + "x" * 60 + " alice@poste (ED25519)\n")

        faux_run.reponses = {"ssh-keygen": keygen}
        empreinte = skm._fingerprint_from_line("  ssh-ed25519 AAAA  ")
        assert empreinte == ("SHA256:" + "x" * 60)[:48]
        assert vus == ["ssh-ed25519 AAAA\n"]
        assert not Path(faux_run.appels[0][-1]).exists()

    def test_sortie_trop_courte(self, faux_run: FauxRun) -> None:
        """Sortie trop courte."""
        faux_run.reponses = {"ssh-keygen": _res(0, "256\n")}
        assert skm._fingerprint_from_line("ssh-ed25519 AAAA") == ""

    def test_cle_refusee(self, faux_run: FauxRun) -> None:
        """Clé refusée."""
        faux_run.reponses = {"ssh-keygen": _res(255, "", "not a public key")}
        assert skm._fingerprint_from_line("n'importe quoi") == ""

    def test_exception_et_suppression_impossible(
        self, faux_run: FauxRun, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exception et suppression impossible."""
        faux_run.reponses = {"ssh-keygen": subprocess.TimeoutExpired("ssh-keygen", 3)}
        supprimes: list[str] = []

        def unlink(chemin: str) -> None:
            """Suppression qui échoue."""
            supprimes.append(chemin)
            raise OSError("occupé")

        vrai_unlink = os.unlink
        monkeypatch.setattr(skm.os, "unlink", unlink)
        assert skm._fingerprint_from_line("ssh-ed25519 AAAA") == ""
        assert len(supprimes) == 1
        vrai_unlink(supprimes[0])

    def test_echec_avant_creation_du_fichier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Échec avant création du fichier."""

        def refus(*_a: object, **_k: object) -> None:
            """Lève une erreur."""
            raise OSError("disque plein")

        monkeypatch.setattr(skm.tempfile, "NamedTemporaryFile", refus)
        assert skm._fingerprint_from_line("ssh-ed25519 AAAA") == ""


@pytest.fixture
def empreintes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Remplace ``_fingerprint_from_line`` : empreinte factice, lignes vues gardées."""
    vues: list[str] = []

    def empreinte(ligne: str) -> str:
        """Empreinte."""
        vues.append(ligne)
        return f"SHA256:{len(vues)}"

    monkeypatch.setattr(skm, "_fingerprint_from_line", empreinte)
    return vues


class TestKnownHosts:
    """Analyse de ``known_hosts``."""

    def test_analyse(self, empreintes: list[str]) -> None:
        """Analyse."""
        contenu = "\n".join(
            [
                "# commentaire",
                "",
                "srv1,10.0.0.1 ssh-ed25519 AAAAED commentaire libre",
                "|1|aGFzaA==|c2Fs ecdsa-sha2-nistp256 AAAAEC",
                "@cert-authority *.exemple.fr ssh-rsa AAAARSA ca",
                "@revoked",
                "ligne-incomplete ssh-rsa",
            ]
        )
        entrees = skm._parse_known_hosts(contenu)
        assert [e["lineno"] for e in entrees] == [3, 4, 5]
        premier, hache, ca = entrees
        assert premier["host_display"] == "srv1,10.0.0.1"
        assert premier["comment"] == "commentaire libre"
        assert premier["hashed"] is False
        assert hache["hashed"] is True
        assert hache["host_display"] == "(hashed)"
        assert hache["comment"] == ""
        assert ca["marker"] == "@cert-authority"
        assert ca["host_display"] == "*.exemple.fr"
        assert empreintes == [
            "ssh-ed25519 AAAAED",
            "ecdsa-sha2-nistp256 AAAAEC",
            "ssh-rsa AAAARSA",
        ]

    def test_vide(self, empreintes: list[str]) -> None:
        """Vide."""
        assert skm._parse_known_hosts("") == []
        assert empreintes == []


class TestAuthorizedKeys:
    """Analyse de ``authorized_keys``."""

    def test_analyse(self, empreintes: list[str]) -> None:
        """Analyse."""
        contenu = "\n".join(
            [
                CLE_ED,
                "# " + CLE_RSA,
                'from="10.0.0.0/8",no-pty ssh-ed25519 AAAAOPT avec options',
                "#",
                "# simple commentaire",
                "",
                "pas-une-cle AAAA",
                "ssh-dss",
            ]
        )
        entrees = skm._parse_authorized_keys(contenu)
        assert [e["lineno"] for e in entrees] == [1, 2, 3, 8]
        active, desactivee, options, sans_cle = entrees
        assert active["enabled"] is True
        assert active["comment"] == "alice@poste"
        assert active["options"] == ""
        assert desactivee["enabled"] is False
        assert desactivee["keytype"] == "ssh-rsa"
        assert options["options"] == 'from="10.0.0.0/8",no-pty'
        assert options["comment"] == "avec options"
        assert sans_cle["fingerprint"] == ""
        assert len(empreintes) == 3


class TestHotesSshConfig:
    """Alias ``Host`` concrets de ``~/.ssh/config``."""

    def test_alias_concrets_sans_doublon(self, tmp_path: Path) -> None:
        """Alias concrets sans doublon."""
        config = tmp_path / "config"
        config.write_text(
            "Host srv1 srv2\n  HostName 10.0.0.1\nhost *.lan !interdit srv1 rebond?\n"
            "  Host  srv3\nMatch host srv4\n",
            encoding="utf-8",
        )
        assert skm._parse_ssh_config_hosts(config) == ["srv1", "srv2", "srv3"]

    def test_fichier_absent(self, tmp_path: Path) -> None:
        """Fichier absent."""
        assert skm._parse_ssh_config_hosts(tmp_path / "absent") == []


# ---------------------------------------------------------------------------
# Cible SSH et adaptateurs de fichiers
# ---------------------------------------------------------------------------


class TestCibleSsh:
    """Cible ``user@host[:port]`` et commande ssh de base."""

    def test_port_par_defaut(self) -> None:
        """Port par défaut."""
        cible = skm.SSHTargetSpec("alice", "srv")
        assert cible.display == "alice@srv"
        assert cible.ssh_base_cmd() == [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=6",
            "alice@srv",
        ]

    def test_port_explicite(self) -> None:
        """Port explicite."""
        cible = skm.SSHTargetSpec("root", "srv", 2222)
        assert cible.display == "root@srv:2222"
        assert cible.ssh_base_cmd()[-3:] == ["-p", "2222", "root@srv"]


class TestAdaptateurAbstrait:
    """Adaptateur de fichiers abstrait."""

    @pytest.mark.parametrize(
        ("methode", "args"),
        [
            ("is_remote", ()),
            ("label", ()),
            ("target_user", ()),
            ("read_text", ("f",)),
            ("write_text", ("f", "c")),
        ],
    )
    def test_methodes_a_surcharger(self, methode: str, args: tuple[str, ...]) -> None:
        """Méthodes à surcharger."""
        with pytest.raises(NotImplementedError):
            getattr(skm._FSAdapter(), methode)(*args)

    def test_connexion_toujours_ok_par_defaut(self) -> None:
        """Connexion toujours ok par défaut."""
        assert skm._FSAdapter().test_connection() == (True, "")


class TestFsLocal:
    """Adaptateur de fichiers local."""

    def test_lecture_ecriture_et_permissions(self, tmp_path: Path) -> None:
        """Lecture, écriture et permissions."""
        fs = skm._LocalFS(tmp_path / ".ssh")
        assert fs.is_remote() is False
        assert fs.label() == "local machine"
        assert fs.read_text("known_hosts") is None
        assert fs.write_text("known_hosts", "contenu\n", mode="644") is True
        chemin = tmp_path / ".ssh" / "known_hosts"
        assert fs.read_text("known_hosts") == "contenu\n"
        assert oct(chemin.stat().st_mode)[-3:] == "644"
        assert fs.write_text("authorized_keys", "k\n") is True
        assert oct((tmp_path / ".ssh" / "authorized_keys").stat().st_mode)[-3:] == "600"

    def test_ecriture_impossible(self, tmp_path: Path) -> None:
        """Écriture impossible."""
        bloquant = tmp_path / "fichier"
        bloquant.write_text("x")
        assert skm._LocalFS(bloquant).write_text("known_hosts", "c") is False

    def test_utilisateur_cible(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Utilisateur cible."""
        fs = skm._LocalFS(tmp_path)
        monkeypatch.setattr(skm.getpass, "getuser", lambda: "alice")
        assert fs.target_user() == "alice"

        def echec() -> str:
            """Échec."""
            raise OSError("x")

        monkeypatch.setattr(skm.getpass, "getuser", echec)
        monkeypatch.setenv("USER", "bob")
        assert fs.target_user() == "bob"
        monkeypatch.delenv("USER")
        monkeypatch.delenv("USERNAME", raising=False)
        assert fs.target_user() == ""


class TestFsDistant:
    """Adaptateur de fichiers distant (via ssh)."""

    def _fs(self, user: str = "alice") -> Any:
        """Adaptateur distant de test."""
        return skm._RemoteFS(skm.SSHTargetSpec(user, "srv", 2222))

    def test_identite(self) -> None:
        """Identité."""
        fs = self._fs()
        assert fs.is_remote() is True
        assert fs.label() == "alice@srv:2222"
        assert fs.target_user() == "alice"

    def test_lecture(self, faux_run: FauxRun) -> None:
        """Lecture."""
        faux_run.reponses = {"ssh": _res(0, "contenu distant\n")}
        assert self._fs().read_text("known hosts") == "contenu distant\n"
        commande = faux_run.appels[0]
        assert commande[:7] == [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=6",
            "-p",
            "2222",
        ]
        assert commande[-1] == "cat ~/.ssh/'known hosts' 2>/dev/null"

    @pytest.mark.parametrize("reponse", [_res(1), OSError("ssh absent")])
    def test_lecture_en_echec(self, faux_run: FauxRun, reponse: Any) -> None:
        """Lecture en échec."""
        faux_run.reponses = {"ssh": reponse}
        assert self._fs().read_text("known_hosts") is None

    def test_ecriture_atomique_en_base64(self, faux_run: FauxRun) -> None:
        """Écriture atomique en base64."""
        faux_run.reponses = {"ssh": _res(0)}
        contenu = "ligne avec 'guillemets' et $VAR\n"
        assert self._fs().write_text("authorized_keys", contenu, mode="600") is True
        distante = faux_run.appels[0][-1]
        b64 = base64.b64encode(contenu.encode()).decode()
        assert b64 in distante
        assert "chmod 600" in distante
        assert distante.endswith('mv "$tmp" ~/.ssh/authorized_keys')
        assert "$VAR" not in distante

    def test_ecriture_refusee(self, faux_run: FauxRun) -> None:
        """Écriture refusée."""
        faux_run.reponses = {"ssh": _res(1, "", "Permission denied")}
        assert self._fs().write_text("known_hosts", "x") is False

    def test_ecriture_exception(self, faux_run: FauxRun) -> None:
        """Écriture exception."""
        faux_run.reponses = {"ssh": subprocess.TimeoutExpired("ssh", 10)}
        assert self._fs().write_text("known_hosts", "x") is False

    @pytest.mark.parametrize(
        ("reponse", "attendu"),
        [
            (_res(0, "__OK__\n"), (True, "")),
            (_res(0, "bannière\n"), (False, "SSH connection failed")),
            (
                _res(255, "", "Permission denied (publickey).\n"),
                (False, "Permission denied (publickey)."),
            ),
            (subprocess.TimeoutExpired("ssh", 8), (False, "Connection timed out")),
            (OSError("ssh introuvable"), (False, "ssh introuvable")),
        ],
    )
    def test_connexion(self, faux_run: FauxRun, reponse: Any, attendu: tuple[bool, str]) -> None:
        """Connexion."""
        faux_run.reponses = {"ssh": reponse}
        assert self._fs().test_connection() == attendu


# ---------------------------------------------------------------------------
# Méthodes de SSHKeyManagerDialog (sans interface)
# ---------------------------------------------------------------------------


class Magasin:
    """Doublure de ``Gtk.ListStore`` : lignes en listes, itérateur = index."""

    def __init__(self) -> None:
        """Initialise un magasin vide."""
        self.lignes: list[list[Any]] = []

    def clear(self) -> None:
        """Vide le magasin."""
        self.lignes.clear()

    def append(self, ligne: list[Any]) -> None:
        """Ajoute une ligne."""
        self.lignes.append(list(ligne))

    def get_iter(self, chemin: str) -> int:
        """Itérateur d'un chemin TreeModel (« 0 », « 1 »…)."""
        return int(chemin)

    def __getitem__(self, it: int) -> list[Any]:
        """Ligne d'un itérateur."""
        return self.lignes[it]


class Arbre:
    """Doublure de ``Gtk.TreeView`` : sélection fixée par le test."""

    def __init__(self) -> None:
        """Aucune ligne sélectionnée au départ."""
        self.selection: int | None = None

    def get_selection(self) -> Any:
        """Rend une sélection dont ``get_selected`` donne ``(modèle, it)``."""
        return SimpleNamespace(get_selected=lambda: (None, self.selection))


class Libelle:
    """Doublure de ``Gtk.Label`` / ``Gtk.Entry``."""

    def __init__(self) -> None:
        """Texte vide au départ."""
        self.texte = ""

    def set_markup(self, texte: str) -> None:
        """Mémorise le balisage."""
        self.texte = texte

    def set_text(self, texte: str) -> None:
        """Mémorise le texte."""
        self.texte = texte

    def get_text(self) -> str:
        """Rend le texte."""
        return self.texte


@pytest.fixture
def dialogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """``SSHKeyManagerDialog`` sans interface, sur un ``~/.ssh`` temporaire."""
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    monkeypatch.setattr(skm, "_CA_CONFIG_PATH", ssh_dir / ".gcm_ca_config.json")
    monkeypatch.setattr(skm, "GLib", SimpleNamespace(markup_escape_text=lambda t: t))
    d = object.__new__(skm.SSHKeyManagerDialog)
    d._ssh_dir = ssh_dir
    d._local_fs = skm._LocalFS(ssh_dir)
    d._remote_fs = None
    d._gcm_hosts_provider = None
    d._ca_priv_path = None
    d._ca_pub_path = None
    d._priv_store, d._pub_store = Magasin(), Magasin()
    d._kh_store, d._ak_store = Magasin(), Magasin()
    d._kh_tree, d._ak_tree = Arbre(), Arbre()
    d._lbl_ca_status, d._lbl_target_status, d._entry_target = Libelle(), Libelle(), Libelle()
    d.notes = []
    d.reponses_confirm = []
    d._notify = lambda msg, duration_ms=4000: d.notes.append(msg)
    d._confirm = lambda _texte: d.reponses_confirm.pop(0) if d.reponses_confirm else True
    d._update_buttons = lambda: None
    return d


class TestCaDesignee:
    """Autorité de certification désignée."""

    def test_chargement_absent_ou_illisible(self, dialogue: Any) -> None:
        """Chargement absent ou illisible."""
        dialogue._load_ca_config()
        assert dialogue._ca_priv_path is None
        skm._CA_CONFIG_PATH.write_text("{pas du json")
        dialogue._load_ca_config()
        assert dialogue._ca_priv_path is None

    def test_chargement_sans_cle(self, dialogue: Any) -> None:
        """Chargement sans clé."""
        skm._CA_CONFIG_PATH.write_text(json.dumps({"ca_priv": ""}))
        dialogue._load_ca_config()
        assert dialogue._ca_priv_path is None

    def test_cle_introuvable_ignoree(self, dialogue: Any, tmp_path: Path) -> None:
        """Clé introuvable ignorée."""
        skm._CA_CONFIG_PATH.write_text(json.dumps({"ca_priv": str(tmp_path / "disparue")}))
        dialogue._load_ca_config()
        assert dialogue._ca_priv_path is None
        assert dialogue.has_designated_ca() is False

    def test_aller_retour(self, dialogue: Any) -> None:
        """Aller retour."""
        ca = dialogue._ssh_dir / "ca"
        ca.write_text("privée")
        dialogue._ca_priv_path = ca
        dialogue._save_ca_config()
        assert oct(skm._CA_CONFIG_PATH.stat().st_mode)[-3:] == "600"
        dialogue._ca_priv_path = None
        dialogue._load_ca_config()
        assert dialogue._ca_priv_path == ca
        assert dialogue._ca_pub_path is None
        (dialogue._ssh_dir / "ca.pub").write_text("publique")
        dialogue._load_ca_config()
        assert dialogue._ca_pub_path == dialogue._ssh_dir / "ca.pub"
        assert dialogue.has_designated_ca() is True

    def test_sauvegarde_sans_ca(self, dialogue: Any) -> None:
        """Sauvegarde sans ca."""
        dialogue._save_ca_config()
        assert json.loads(skm._CA_CONFIG_PATH.read_text()) == {"ca_priv": ""}

    def test_sauvegarde_impossible(self, dialogue: Any, tmp_path: Path) -> None:
        """Sauvegarde impossible."""
        bloquant = tmp_path / "bloquant"
        bloquant.write_text("x")
        dialogue._ssh_dir = bloquant / "sous"
        dialogue._save_ca_config()  # journalisé, sans exception

    def test_libelle_d_etat(self, dialogue: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Libellé d'état."""
        dialogue._update_ca_status_label()
        assert "none" in dialogue._lbl_ca_status.texte
        ca = dialogue._ssh_dir / "ca"
        dialogue._ca_priv_path = ca
        vus: list[Path] = []

        def empreinte(chemin: Path) -> tuple[str, str]:
            """Empreinte."""
            vus.append(chemin)
            return ("SHA256:ca", "ED25519") if chemin.suffix == ".pub" else ("", "")

        monkeypatch.setattr(dialogue, "_get_fingerprint_and_type", empreinte)
        dialogue._update_ca_status_label()
        assert dialogue._lbl_ca_status.texte == "<b>ca</b>"
        dialogue._ca_pub_path = dialogue._ssh_dir / "ca.pub"
        dialogue._update_ca_status_label()
        assert dialogue._lbl_ca_status.texte == "<b>ca</b> — ED25519 SHA256:ca"
        assert vus == [ca, dialogue._ssh_dir / "ca.pub"]


class TestEmpreinteEtPermissions:
    """Empreinte, type et permissions d'une clé."""

    def test_empreinte_et_type(self, dialogue: Any, faux_run: FauxRun) -> None:
        """Empreinte et type."""
        faux_run.reponses = {"ssh-keygen": _res(0, "256 SHA256:" + "y" * 40 + " a@b (ED25519)\n")}
        assert dialogue._get_fingerprint_and_type(Path("k")) == (
            ("SHA256:" + "y" * 40)[:32],
            "ED25519",
        )

    def test_sans_type(self, dialogue: Any, faux_run: FauxRun) -> None:
        """Sans type."""
        faux_run.reponses = {"ssh-keygen": _res(0, "256 SHA256:z\n")}
        assert dialogue._get_fingerprint_and_type(Path("k")) == ("SHA256:z", "")

    def test_sortie_vide(self, dialogue: Any, faux_run: FauxRun) -> None:
        """Sortie vide."""
        faux_run.reponses = {"ssh-keygen": _res(0, "")}
        assert dialogue._get_fingerprint_and_type(Path("k")) == ("", "")

    @pytest.mark.parametrize("reponse", [_res(1), OSError("absent")])
    def test_echec(self, dialogue: Any, faux_run: FauxRun, reponse: Any) -> None:
        """Échec."""
        faux_run.reponses = {"ssh-keygen": reponse}
        assert dialogue._get_fingerprint_and_type(Path("k")) == ("", "")

    @pytest.mark.parametrize(
        ("mode", "attendu"),
        [
            (0o600, ("600 ✓", True)),
            (0o400, ("400 ✓", True)),
            (0o644, ("644 ⚠", False)),
            (0o640, ("640 ⚠", False)),
        ],
    )
    def test_permissions(
        self, dialogue: Any, tmp_path: Path, mode: int, attendu: tuple[str, bool]
    ) -> None:
        """Permissions."""
        cle = tmp_path / "cle"
        cle.write_text("x")
        cle.chmod(mode)
        assert dialogue._check_permissions(cle) == attendu

    def test_permissions_fichier_absent(self, dialogue: Any, tmp_path: Path) -> None:
        """Permissions fichier absent."""
        assert dialogue._check_permissions(tmp_path / "absent") == ("?", False)


class TestScanDesCles:
    """Scan de ``~/.ssh/``."""

    def test_dossier_absent(self, dialogue: Any, tmp_path: Path) -> None:
        """Dossier absent."""
        dialogue._ssh_dir = tmp_path / "absent"
        dialogue._priv_store.append(["ancienne"])
        dialogue._load_keys()
        assert dialogue._priv_store.lignes == []
        assert dialogue.notes == ["~/.ssh/ not found — no keys loaded"]

    def test_classement_des_fichiers(
        self, dialogue: Any, monkeypatch: pytest.MonkeyPatch, faux_run: FauxRun
    ) -> None:
        """Classement des fichiers."""
        d = dialogue._ssh_dir
        for nom in [
            "id_ed25519",
            "id_ed25519.pub",
            "id_ed25519-cert.pub",
            "id_rsa",
            "orpheline.pub",
            "signee-cert.pub",
            "config",
            "config.bak",
            "known_hosts",
            "known_hosts.old",
            "authorized_keys.1",
            ".cache",
        ]:
            (d / nom).write_text("x")
            (d / nom).chmod(0o600)
        (d / "sous-dossier").mkdir()
        faux_run.reponses = {"stat": _res(0, "1700000000\n")}
        monkeypatch.setattr(
            dialogue, "_get_fingerprint_and_type", lambda p: (f"FP-{p.name}", "ED25519")
        )
        certificats: list[str] = []

        def cert(chemin: Path) -> dict[str, Any]:
            """Simule la lecture du certificat."""
            certificats.append(chemin.name)
            return {
                "valid_str": "forever",
                "expired": chemin.name.startswith("signee"),
                "signed_by": "CA",
            }

        monkeypatch.setattr(skm, "_parse_cert_info", cert)
        dialogue._load_keys()

        privees = {ligne[0]: ligne for ligne in dialogue._priv_store.lignes}
        orphelines = {ligne[0]: ligne for ligne in dialogue._pub_store.lignes}
        assert sorted(privees) == ["id_ed25519", "id_rsa"]
        # Comportement actuel : le certificat d'une clé privée présente
        # (``id_ed25519-cert.pub``) est aussi listé parmi les orphelines, car
        # ``with_suffix("")`` donne ``id_ed25519-cert``, qui n'existe pas.
        assert sorted(orphelines) == ["id_ed25519-cert.pub", "orpheline.pub", "signee-cert.pub"]
        ed = privees["id_ed25519"]
        assert ed[skm._COL_PUB_EXISTS] is True
        assert ed[skm._COL_PUB_PATH].endswith("id_ed25519.pub")
        assert ed[skm._COL_IS_CERT] is True
        assert ed[skm._COL_VALIDITY_STR] == "forever"
        assert ed[skm._COL_PERMS_OK] is True
        assert ed[skm._COL_CREATED_STR] == datetime.fromtimestamp(1700000000).strftime(
            "%Y-%m-%d %H:%M"
        )
        rsa = privees["id_rsa"]
        assert rsa[skm._COL_PUB_EXISTS] is False
        assert rsa[skm._COL_PUB_PATH] == ""
        assert rsa[skm._COL_IS_CERT] is False
        assert rsa[skm._COL_VALIDITY_STR] == "—"
        assert orphelines["orpheline.pub"][skm._COL_IS_CERT] is False
        assert orphelines["signee-cert.pub"][skm._COL_EXPIRED] is True
        assert sorted(certificats) == [
            "id_ed25519-cert.pub",
            "id_ed25519-cert.pub",
            "signee-cert.pub",
        ]
        assert dialogue.notes == ["2 private key(s), 3 Orphan Public key(s) loaded from ~/.ssh/"]

    def test_date_inconnue(self, dialogue: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Date inconnue."""
        (dialogue._ssh_dir / "cle").write_text("x")
        monkeypatch.setattr(skm, "_get_creation_datetime", lambda _p: None)
        monkeypatch.setattr(dialogue, "_get_fingerprint_and_type", lambda _p: ("", ""))
        dialogue._load_keys()
        assert dialogue._priv_store.lignes[0][skm._COL_CREATED_STR] == "?"


class TestAgent:
    """Présence dans ssh-agent, ajout et retrait."""

    SORTIE_FP = "256 SHA256:empreinte a@b (ED25519)\n"

    @pytest.mark.parametrize(
        ("reponses", "attendu"),
        [
            (
                {
                    "ssh-keygen": _res(0, SORTIE_FP),
                    "ssh-add": _res(0, "256 SHA256:empreinte a@b\n"),
                },
                True,
            ),
            ({"ssh-keygen": _res(0, SORTIE_FP), "ssh-add": _res(0, "256 SHA256:autre\n")}, False),
            ({"ssh-keygen": _res(0, SORTIE_FP), "ssh-add": _res(2, "", "no agent")}, False),
            ({"ssh-keygen": _res(1)}, False),
            ({"ssh-keygen": _res(0, "256\n")}, False),
            ({"ssh-keygen": OSError("absent")}, False),
        ],
    )
    def test_cle_dans_l_agent(
        self, dialogue: Any, faux_run: FauxRun, reponses: dict[str, Any], attendu: bool
    ) -> None:
        """Clé dans l'agent."""
        faux_run.reponses = reponses
        assert dialogue._is_key_in_agent(Path("k.pub")) is attendu

    def _cle(
        self, dialogue: Any, monkeypatch: pytest.MonkeyPatch, *, avec_pub: bool = True
    ) -> Path:
        """Clé."""
        priv = dialogue._ssh_dir / "id_ed25519"
        priv.write_text("x")
        if avec_pub:
            (dialogue._ssh_dir / "id_ed25519.pub").write_text(CLE_ED)
        monkeypatch.setattr(
            dialogue,
            "_get_selected_key",
            lambda: {"name": "id_ed25519", "path": str(priv), "pub": ""},
        )
        return priv

    def test_aucune_cle_selectionnee(self, dialogue: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Aucune clé sélectionnée."""
        monkeypatch.setattr(dialogue, "_get_selected_key", lambda: None)
        dialogue._on_toggle_agent(None)
        assert dialogue.notes == ["No private key selected"]

    def test_sans_cle_publique(self, dialogue: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sans clé publique."""
        self._cle(dialogue, monkeypatch, avec_pub=False)
        dialogue._on_toggle_agent(None)
        assert dialogue.notes == ["No public key available to identify this key in the agent"]

    @pytest.mark.parametrize(
        ("reponse", "note"),
        [
            (_res(0), "Added to ssh-agent"),
            (
                _res(1, "", "bad passphrase"),
                "Failed to add to agent (passphrase required?): bad passphrase",
            ),
            (subprocess.TimeoutExpired("ssh-add", 10), "ssh-add timed out"),
            (OSError("ssh-add absent"), "ssh-add failed: ssh-add absent"),
        ],
    )
    def test_ajout(
        self,
        dialogue: Any,
        monkeypatch: pytest.MonkeyPatch,
        faux_run: FauxRun,
        reponse: Any,
        note: str,
    ) -> None:
        """Ajout."""
        priv = self._cle(dialogue, monkeypatch)
        monkeypatch.setattr(dialogue, "_is_key_in_agent", lambda _p: False)
        faux_run.reponses = {"ssh-add": reponse}
        dialogue._on_toggle_agent(None)
        assert dialogue.notes[-1].startswith(note)
        assert faux_run.appels == [["ssh-add", str(priv)]]

    @pytest.mark.parametrize(
        ("reponse", "note"),
        [
            (_res(0), "Removed from ssh-agent"),
            (_res(1, "", "not found"), "Failed to remove from agent: not found"),
            (OSError("ssh-add absent"), "ssh-add failed: ssh-add absent"),
        ],
    )
    def test_retrait(
        self,
        dialogue: Any,
        monkeypatch: pytest.MonkeyPatch,
        faux_run: FauxRun,
        reponse: Any,
        note: str,
    ) -> None:
        """Retrait."""
        self._cle(dialogue, monkeypatch)
        monkeypatch.setattr(dialogue, "_is_key_in_agent", lambda _p: True)
        faux_run.reponses = {"ssh-add": reponse}
        dialogue._on_toggle_agent(None)
        assert dialogue.notes == [note]
        assert faux_run.appels[0][:2] == ["ssh-add", "-d"]

    @pytest.mark.parametrize("chargee", [True, False])
    def test_refus_de_confirmation(
        self, dialogue: Any, monkeypatch: pytest.MonkeyPatch, faux_run: FauxRun, chargee: bool
    ) -> None:
        """Refus de confirmation."""
        self._cle(dialogue, monkeypatch)
        monkeypatch.setattr(dialogue, "_is_key_in_agent", lambda _p: chargee)
        dialogue.reponses_confirm = [False]
        dialogue._on_toggle_agent(None)
        assert faux_run.appels == []
        assert dialogue.notes == []


class TestHotesCandidats:
    """Hôtes proposés pour la cible distante."""

    def test_config_puis_gcm_sans_doublon(self, dialogue: Any) -> None:
        """Config puis gcm sans doublon."""
        (dialogue._ssh_dir / "config").write_text("Host srv1 srv2\nHost *\n")
        dialogue._gcm_hosts_provider = lambda: ["srv2", "", "srv3"]
        assert dialogue._collect_candidate_hosts() == ["srv1", "srv2", "srv3"]

    def test_fournisseur_en_echec_ou_vide(self, dialogue: Any) -> None:
        """Fournisseur en échec ou vide."""
        (dialogue._ssh_dir / "config").write_text("Host srv1\n")

        def echec() -> list[str]:
            """Échec."""
            raise RuntimeError("inventaire indisponible")

        dialogue._gcm_hosts_provider = echec
        assert dialogue._collect_candidate_hosts() == ["srv1"]
        dialogue._gcm_hosts_provider = lambda: None
        assert dialogue._collect_candidate_hosts() == ["srv1"]


class FsMemoire(skm._FSAdapter):
    """Adaptateur en mémoire (cible distante simulée)."""

    def __init__(
        self, fichiers: dict[str, str], user: str = "alice", ecriture_ok: bool = True
    ) -> None:
        """Initialise les fichiers, le compte et le résultat des écritures."""
        self.fichiers = dict(fichiers)
        self.user = user
        self.ecriture_ok = ecriture_ok
        self.ecritures: list[tuple[str, str, str]] = []

    def is_remote(self) -> bool:
        """Cible distante."""
        return True

    def label(self) -> str:
        """Libellé de la cible."""
        return f"{self.user}@srv"

    def target_user(self) -> str:
        """Compte visé."""
        return self.user

    def read_text(self, filename: str) -> str | None:
        """Lit un fichier simulé."""
        return self.fichiers.get(filename)

    def write_text(self, filename: str, content: str, mode: str = "600") -> bool:
        """Écrit un fichier simulé."""
        self.ecritures.append((filename, content, mode))
        if self.ecriture_ok:
            self.fichiers[filename] = content
        return self.ecriture_ok


class TestKnownHostsDuDialogue:
    """Chargement et édition de ``known_hosts`` depuis le dialogue."""

    def test_chargement_et_absence(self, dialogue: Any, empreintes: list[str]) -> None:
        """Chargement et absence."""
        dialogue._load_known_hosts()
        assert dialogue.notes == ["known_hosts not found on local machine"]
        dialogue._remote_fs = FsMemoire(
            {"known_hosts": "srv ssh-ed25519 AAAA\n|1|h|s ssh-rsa BBBB\n"}
        )
        assert dialogue._current_fs() is dialogue._remote_fs
        dialogue._load_known_hosts()
        assert dialogue._kh_store.lignes == [
            ["srv", "ssh-ed25519", "SHA256:1", False, "srv ssh-ed25519 AAAA", 1],
            ["(hashed)", "ssh-rsa", "SHA256:2", True, "|1|h|s ssh-rsa BBBB", 2],
        ]
        assert dialogue.notes[-1] == "2 known_hosts entries loaded from alice@srv"

    def test_suppression(self, dialogue: Any, empreintes: list[str]) -> None:
        """Suppression."""
        fs = FsMemoire({"known_hosts": "a ssh-rsa A\nb ssh-rsa B\n"})
        dialogue._remote_fs = fs
        dialogue._load_known_hosts()
        dialogue._kh_tree.selection = 0
        dialogue._on_delete_known_host(None)
        assert fs.ecritures == [("known_hosts", "b ssh-rsa B\n", "644")]
        assert "known_hosts entry removed" in dialogue.notes
        dialogue._kh_tree.selection = 0
        dialogue._on_delete_known_host(None)
        assert fs.fichiers["known_hosts"] == ""

    def test_suppression_cas_limites(self, dialogue: Any, empreintes: list[str]) -> None:
        """Suppression cas limites."""
        dialogue._on_delete_known_host(None)
        assert dialogue.notes == ["No known_hosts entry selected"]
        fs = FsMemoire({"known_hosts": "a ssh-rsa A\n"}, ecriture_ok=False)
        dialogue._remote_fs = fs
        dialogue._load_known_hosts()
        dialogue._kh_tree.selection = 0
        dialogue.reponses_confirm = [False]
        dialogue._on_delete_known_host(None)
        assert fs.ecritures == []
        dialogue._on_delete_known_host(None)
        assert dialogue.notes[-2] == "Failed to update known_hosts"
        fs.fichiers["known_hosts"] = "modifié ssh-rsa Z\n"
        dialogue._on_delete_known_host(None)
        assert dialogue.notes[-2] == "Entry not found (file may have changed) — refreshing"

    def test_copie_d_empreinte(self, dialogue: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """Copie d'empreinte."""
        copies: list[str] = []
        monkeypatch.setattr(skm, "_copy_text_to_clipboard", lambda t: copies.append(t) or True)
        dialogue._on_copy_known_host_fingerprint(None)
        assert dialogue.notes == ["No known_hosts entry selected"]
        dialogue._kh_store.append(["srv", "ssh-rsa", "SHA256:x", False, "l", 1])
        dialogue._kh_store.append(["srv", "ssh-rsa", "", False, "l", 2])
        dialogue._kh_tree.selection = 0
        dialogue._on_copy_known_host_fingerprint(None)
        dialogue._kh_tree.selection = 1
        dialogue._on_copy_known_host_fingerprint(None)
        assert copies == ["SHA256:x"]
        assert dialogue.notes[1:] == [
            "Fingerprint copied to clipboard",
            "No fingerprint available for this entry",
        ]


class TestAuthorizedKeysDuDialogue:
    """Chargement et édition de ``authorized_keys`` depuis le dialogue."""

    def test_chargement(self, dialogue: Any, empreintes: list[str]) -> None:
        """Chargement."""
        dialogue._load_authorized_keys()
        assert dialogue.notes == ["authorized_keys not found on local machine"]
        dialogue._local_fs.write_text("authorized_keys", CLE_ED + "\n# " + CLE_RSA + "\n")
        dialogue._load_authorized_keys()
        assert [ligne[:4] for ligne in dialogue._ak_store.lignes] == [
            [True, "ssh-ed25519", "SHA256:1", "alice@poste"],
            [False, "ssh-rsa", "SHA256:2", "bob@poste"],
        ]

    def test_bascule_activee_desactivee(self, dialogue: Any, empreintes: list[str]) -> None:
        """Bascule activée / désactivée."""
        fs = FsMemoire({"authorized_keys": CLE_ED + "\n"})
        dialogue._remote_fs = fs
        dialogue._load_authorized_keys()
        dialogue._on_toggle_authorized_key(None, "0")
        assert fs.fichiers["authorized_keys"] == "# " + CLE_ED + "\n"
        dialogue._on_toggle_authorized_key(None, "0")
        assert fs.fichiers["authorized_keys"] == CLE_ED + "\n"
        assert dialogue.notes.count("authorized_keys entry updated") == 2

    def test_bascule_cas_limites(self, dialogue: Any, empreintes: list[str]) -> None:
        """Bascule cas limites."""
        fs = FsMemoire({"authorized_keys": CLE_ED + "\n"}, user="root", ecriture_ok=False)
        dialogue._remote_fs = fs
        dialogue._load_authorized_keys()
        dialogue.reponses_confirm = [False]
        dialogue._on_toggle_authorized_key(None, "0")
        dialogue.reponses_confirm = [True, False]  # garde-fou root refusé
        dialogue._on_toggle_authorized_key(None, "0")
        assert fs.ecritures == []
        dialogue._on_toggle_authorized_key(None, "0")
        assert "Failed to update authorized_keys" in dialogue.notes
        fs.fichiers["authorized_keys"] = "autre contenu\n"
        dialogue._on_toggle_authorized_key(None, "0")
        assert dialogue.notes[-2] == "File changed on disk — refreshing"

    def test_suppression(self, dialogue: Any, empreintes: list[str]) -> None:
        """Suppression."""
        fs = FsMemoire({"authorized_keys": CLE_ED + "\n" + CLE_RSA + "\n"})
        dialogue._remote_fs = fs
        dialogue._on_delete_authorized_key(None)
        assert dialogue.notes == ["No authorized_keys entry selected"]
        dialogue._load_authorized_keys()
        dialogue._ak_tree.selection = 1
        dialogue.reponses_confirm = [False]
        dialogue._on_delete_authorized_key(None)
        assert fs.ecritures == []
        dialogue._on_delete_authorized_key(None)
        assert fs.ecritures == [("authorized_keys", CLE_ED + "\n", "600")]
        dialogue._ak_tree.selection = 0
        dialogue._on_delete_authorized_key(None)
        assert fs.fichiers["authorized_keys"] == ""

    def test_suppression_cas_limites(self, dialogue: Any, empreintes: list[str]) -> None:
        """Suppression cas limites."""
        fs = FsMemoire({"authorized_keys": CLE_ED + "\n"}, user="root", ecriture_ok=False)
        dialogue._remote_fs = fs
        dialogue._load_authorized_keys()
        dialogue._ak_tree.selection = 0
        dialogue.reponses_confirm = [True, False]
        dialogue._on_delete_authorized_key(None)
        assert fs.ecritures == []
        dialogue._on_delete_authorized_key(None)
        assert dialogue.notes[-2] == "Failed to update authorized_keys"
        fs.fichiers["authorized_keys"] = "autre\n"
        dialogue._on_delete_authorized_key(None)
        assert dialogue.notes[-2] == "Entry not found (file may have changed) — refreshing"

    def _dialogue_ajout(self, monkeypatch: pytest.MonkeyPatch, reponse: int, ligne: str) -> None:
        """Installe un faux dialogue d'ajout."""

        class FauxDialogueAjout:
            """Doublure de ``_AddAuthorizedKeyDialog``."""

            def __init__(self, parent: object) -> None:
                """Initialise la doublure."""
                self.parent = parent

            def get_key_line(self) -> str:
                """Ligne de clé saisie."""
                return ligne

            def destroy(self) -> None:
                """Ferme le faux dialogue."""
                pass

        monkeypatch.setattr(skm, "_AddAuthorizedKeyDialog", FauxDialogueAjout)
        monkeypatch.setattr(skm, "run_dialog_sync", lambda _d: reponse)

    def test_ajout(
        self, dialogue: Any, monkeypatch: pytest.MonkeyPatch, empreintes: list[str]
    ) -> None:
        """Ajout."""
        monkeypatch.setattr(dialogue, "_top_window", lambda: None)
        ok = skm.Gtk.ResponseType.OK
        fs = FsMemoire({"authorized_keys": CLE_RSA})  # sans fin de ligne
        dialogue._remote_fs = fs
        self._dialogue_ajout(monkeypatch, ok, CLE_ED)
        dialogue._on_add_authorized_key(None)
        assert fs.fichiers["authorized_keys"] == CLE_RSA + "\n" + CLE_ED + "\n"
        assert "Key added to authorized_keys" in dialogue.notes
        fs.fichiers.pop("authorized_keys")
        dialogue._on_add_authorized_key(None)
        assert fs.fichiers["authorized_keys"] == CLE_ED + "\n"

    def test_ajout_cas_limites(
        self, dialogue: Any, monkeypatch: pytest.MonkeyPatch, empreintes: list[str]
    ) -> None:
        """Ajout cas limites."""
        monkeypatch.setattr(dialogue, "_top_window", lambda: None)
        ok = skm.Gtk.ResponseType.OK
        fs = FsMemoire({}, user="root", ecriture_ok=False)
        dialogue._remote_fs = fs
        self._dialogue_ajout(monkeypatch, ok + 1, CLE_ED)
        dialogue._on_add_authorized_key(None)
        self._dialogue_ajout(monkeypatch, ok, "")
        dialogue._on_add_authorized_key(None)
        self._dialogue_ajout(monkeypatch, ok, "pas une clé")
        dialogue._on_add_authorized_key(None)
        assert dialogue.notes == ["This does not look like a valid public key line"]
        self._dialogue_ajout(monkeypatch, ok, CLE_ED)
        dialogue.reponses_confirm = [False]
        dialogue._on_add_authorized_key(None)
        assert fs.ecritures == []
        dialogue._on_add_authorized_key(None)
        assert "Failed to add key" in dialogue.notes


class TestGardeFouRoot:
    """Confirmation avant d'écrire chez root."""

    def test_compte_ordinaire_sans_question(self, dialogue: Any) -> None:
        """Compte ordinaire sans question."""
        dialogue.reponses_confirm = [False]
        assert dialogue._confirm_root_write(FsMemoire({}), "agir") is True
        assert dialogue.reponses_confirm == [False]

    @pytest.mark.parametrize("reponse", [True, False])
    def test_root_demande_confirmation(self, dialogue: Any, reponse: bool) -> None:
        """Root demande confirmation."""
        textes: list[str] = []
        dialogue._confirm = lambda texte: textes.append(texte) or reponse
        assert dialogue._confirm_root_write(FsMemoire({}, user="root"), "agir") is reponse
        assert "<b>root</b>" in textes[0] and "root@srv" in textes[0] and "agir" in textes[0]


class TestBasculeLocalDistant:
    """Bascule entre la machine locale et une cible distante."""

    def test_saisie_vide_revient_en_local(self, dialogue: Any, empreintes: list[str]) -> None:
        """Saisie vide revient en local."""
        dialogue._remote_fs = FsMemoire({})
        dialogue._entry_target.set_text("  ")
        dialogue._on_connect_target(None)
        assert dialogue._remote_fs is None
        assert dialogue._lbl_target_status.texte == "<b>local machine</b>"
        assert "Switched back to local machine" in dialogue.notes

    def test_cible_invalide(self, dialogue: Any) -> None:
        """Cible invalide."""
        dialogue._entry_target.set_text("pas de arobase")
        dialogue._on_connect_target(None)
        assert dialogue.notes == ["Invalid target — expected user@host or user@host:port"]

    def test_connexion_en_echec(self, dialogue: Any, faux_run: FauxRun) -> None:
        """Connexion en échec."""
        faux_run.reponses = {"ssh": _res(255, "", "Connection refused")}
        dialogue._entry_target.set_text("alice@srv:2222")
        dialogue._on_connect_target(None)
        assert dialogue._remote_fs is None
        assert dialogue.notes == [
            "Testing connection to alice@srv:2222…",
            "Connection failed: Connection refused",
        ]

    def test_connexion_reussie(self, dialogue: Any, faux_run: FauxRun) -> None:
        """Connexion réussie."""
        faux_run.reponses = {
            "ssh": lambda cmd, _k: _res(0, "__OK__\n" if cmd[-1].startswith("echo") else "")
        }
        dialogue._entry_target.set_text("alice@srv")
        dialogue._on_connect_target(None)
        assert isinstance(dialogue._remote_fs, skm._RemoteFS)
        assert dialogue._remote_fs.label() == "alice@srv"
        assert dialogue._lbl_target_status.texte == "<b>alice@srv</b>"
        assert dialogue.notes[1].startswith("Connected to alice@srv")
        assert dialogue.notes[-2:] == [
            "0 known_hosts entries loaded from alice@srv",
            "0 authorized_keys entries loaded from alice@srv",
        ]
