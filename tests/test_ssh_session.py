# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique sans GTK de la session SSH (``plugins/ssh/session.py``, #396)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import plugins.ssh.session as session
from gcm4.core.config import load_config, save_config
from gcm4.core.model import Host

SSH = "/usr/bin/ssh"
EXPECT = "/usr/bin/expect"


def test_sans_mot_de_passe_ssh_direct():
    """Sans mot de passe : ssh direct, utilisateur et port passés, adresse en dernier."""
    resultat = session.lancement("h.example", 2222, "alice", "", "", {}, SSH, EXPECT)
    assert resultat == session.Lancement([SSH, "-l", "alice", "-p", "2222", "h.example"])
    assert resultat.mot_de_passe == ""


def test_sans_utilisateur_ni_port():
    """Utilisateur et port vides : ni ``-l`` ni ``-p`` (``~/.ssh/config`` s'applique)."""
    resultat = session.lancement("alias", None, "", "", "", {}, SSH, None)
    assert resultat.argv == [SSH, "alias"]


@pytest.mark.parametrize("nom", ["", "  ", "gcm4", "equipe-42_a", "A" * 64])
def test_partage_tmux(nom):
    """Tmux crée ou rejoint la session nommée, sous un vrai pseudo-terminal."""
    options = {
        session.OPT_SESSION_PARTAGEE: "yes",
        session.OPT_NOM_SESSION: nom,
        "remote_command": "autre-commande",
        "request_tty": "no",
    }
    resultat = session.lancement("alias", 2222, "alice", "", "-T", options, SSH, None)
    assert resultat.argv[1:3] == ["-o", "RemoteCommand=none"]
    assert resultat.argv[-4:] == [
        "-tt",
        "--",
        "alias",
        f"exec tmux new-session -A -s {nom.strip() or 'gcm4'}",
    ]
    assert resultat.argv.index("-T") < resultat.argv.index("-tt")


@pytest.mark.parametrize(
    "nom", ["a.b", "a:b", "a b", "$(id)", "a;id", "a\nid", "'a'", "équipe", "A" * 65]
)
def test_nom_tmux_invalide(nom):
    """Aucun métacaractère shell/tmux ni nom trop long n'atteint ssh."""
    options = {session.OPT_SESSION_PARTAGEE: "True", session.OPT_NOM_SESSION: nom}
    with pytest.raises(ValueError, match="Session name"):
        session.lancement("h", None, "", "", "", options, SSH, None)


def test_partage_desactive_conserve_la_commande():
    """Un nom invalide conservé dans un profil désactivé n'altère pas SSH."""
    options = {session.OPT_SESSION_PARTAGEE: "False", session.OPT_NOM_SESSION: "a;id"}
    assert session.lancement("h", None, "", "", "", options, SSH, None).argv == [SSH, "h"]


def test_partage_avec_expect():
    """Le même lancement tmux passe par expect sans exposer le mot de passe."""
    options = {session.OPT_SESSION_PARTAGEE: "True"}
    resultat = session.lancement("h", None, "", "secret", "", options, SSH, EXPECT)
    assert resultat.argv[:5] == [EXPECT, "-f", str(session.SCRIPT_EXPECT), SSH, "-o"]
    assert resultat.argv[-1] == "exec tmux new-session -A -s gcm4"
    assert resultat.mot_de_passe == "secret" and "secret" not in repr(resultat)


def test_partage_persiste_dans_ini(tmp_path):
    """Le partage et son nom font un aller-retour réel sans préfixe INI ajouté."""
    chemin = tmp_path / "gcm4.conf"
    cfg = load_config(str(chemin))
    options = {session.OPT_SESSION_PARTAGEE: "True", session.OPT_NOM_SESSION: "equipe-42"}
    cfg.store.add(Host("serveur", host="h", protocol="ssh", protocol_options={"ssh": options}))
    save_config(str(chemin), cfg)
    relu = load_config(str(chemin)).store.find("serveur")
    assert relu.protocol_options["ssh"] == options
    assert "shared_session_name = equipe-42" in chemin.read_text()


def test_avec_mot_de_passe_expect():
    """Mot de passe : script expect, mot de passe hors de argv et du journal."""
    resultat = session.lancement("h", 22, "alice", "s3 cr'et", "-v", {}, SSH, EXPECT)
    assert resultat.argv == [
        EXPECT,
        "-f",
        str(session.SCRIPT_EXPECT),
        SSH,
        "-l",
        "alice",
        "-p",
        "22",
        "-v",
        "h",
    ]
    assert resultat.mot_de_passe == "s3 cr'et"
    assert all("s3" not in a for a in resultat.argv)
    assert "s3" not in repr(resultat) and "***" in repr(resultat)


def test_expect_absent():
    """Mot de passe sans expect : FileNotFoundError nommant le programme."""
    with pytest.raises(FileNotFoundError) as erreur:
        session.lancement("h", 22, "alice", "secret", "", {}, SSH, None)
    assert erreur.value.args == ("expect",)


def test_parametres_mal_formes():
    """Guillemet non fermé : ValueError de shlex, rien n'est lancé."""
    with pytest.raises(ValueError, match="quotation"):
        session.lancement("h", 22, "", "", "-o '", {}, SSH, EXPECT)


def test_arguments_ordre_gtk3(monkeypatch):
    """Toutes les options : même ordre que ``SshTab.connect_ssh`` de GTK3."""
    monkeypatch.setenv("HOME", "/home/alice")
    options = {
        "keepalive": "30",
        "tunnel": "R:9000:localhost:80, 1080:*:*,,8080:intra:80",
        "x11": "True",
        "agent": "yes",
        "compression": "1",
        "compression-level": "6",
        "private_key": "~/.ssh/id_ed25519",
        "proxy_jump": "bastion",
        "identities_only": " yes ",
        "connect_timeout": "",
    }
    assert session.arguments(options) == [
        "-o",
        "ServerAliveInterval=30",
        "-R",
        "9000:localhost:80",
        "-D",
        "1080",
        "-L",
        "8080:intra:80",
        "-X",
        "-A",
        "-C",
        "-o",
        "CompressionLevel=6",
        "-i",
        "/home/alice/.ssh/id_ed25519",
        "-o",
        "ProxyJump=bastion",
        "-o",
        "IdentitiesOnly=yes",
    ]


def test_arguments_valeurs_neutres():
    """Valeurs GTK3 par défaut (``0``, ``False``) : aucun argument ; niveau sans compression ignoré."""
    options = {
        "keepalive": "0",
        "tunnel": "",
        "x11": "False",
        "agent": "False",
        "compression": "False",
        "compression-level": "9",
        "private_key": "",
    }
    assert session.arguments(options) == []
    assert session.arguments({}) == []
    assert session.arguments({"compression": "True"}) == ["-C"]


def test_lancement_options_puis_parametres():
    """Options de l'hôte avant les paramètres supplémentaires, adresse en dernier."""
    resultat = session.lancement("h", None, "", "", "-v", {"x11": "True"}, SSH, None)
    assert resultat.argv == [SSH, "-X", "-v", "h"]


def test_tunnels_aller_retour():
    """Lecture et écriture de l'option ``tunnel`` (virgules, entrées vides retirées)."""
    assert session.lire_tunnels(" a , ,b") == ["a", "b"]
    assert session.lire_tunnels("") == []
    assert session.ecrire_tunnels(["a", " ", " b "]) == "a,b"
    assert session.redirection("8080:h:80") == ["-L", "8080:h:80"]


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [("True", True), (" yes", True), ("1", True), ("on", True), ("False", False), ("", False)],
)
def test_booleen(brut, attendu):
    """Booléens INI de GTK3."""
    assert session.booleen(brut) is attendu


def test_cle_absente(tmp_path):
    """Clé renseignée mais introuvable : son chemin ; présente ou vide : ``None``."""
    cle = tmp_path / "id"
    assert session.cle_absente({"private_key": str(cle)}) == str(cle)
    cle.write_text("x", encoding="utf-8")
    assert session.cle_absente({"private_key": str(cle)}) is None
    assert session.cle_absente({"private_key": " "}) is None
    assert session.cle_absente({}) is None


def test_etapes():
    """Commandes envoyées au bout de 3 s, comme GTK3."""
    assert session.etapes("ls\n##D=100\npwd") == [(3000, "ls\r"), (3100, "pwd\r")]


def test_champs_avances_comme_gtk3():
    """Les 15 options avancées de GTK3, clés INI distinctes."""
    assert len(session.CHAMPS_AVANCES) == 15
    assert len({cle for cle, _o, _a in session.CHAMPS_AVANCES}) == 15


def test_script_expect_livre():
    """Le script expect est à côté du module, lit le mot de passe sur le descripteur 3."""
    texte = session.SCRIPT_EXPECT.read_text(encoding="utf-8")
    assert session.SCRIPT_EXPECT.parent == Path(session.__file__).parent
    assert "/dev/fd/3" in texte and "interact" in texte
    # Plus de « yes » automatique à l'empreinte d'un hôte inconnu.
    assert 'send "yes' not in texte and 'send -- "yes' not in texte
    assert os.path.basename(session.SCRIPT_EXPECT) == "ssh.expect"
