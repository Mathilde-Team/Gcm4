# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique sans GTK du plugin telnet (``plugins/telnet/core.py``, #179)."""

from __future__ import annotations

from pathlib import Path

import pytest

import plugins.telnet.core as core

TELNET = "/usr/bin/telnet"
EXPECT = "/usr/bin/expect"


def test_sans_mot_de_passe_telnet_direct():
    """Utilisateur sans mot de passe (ou l'inverse) : telnet direct, utilisateur ignoré (GTK3)."""
    for utilisateur, mot_de_passe in (("alice", ""), ("", "secret"), ("", "")):
        resultat = core.lancement("h.example", 2323, utilisateur, mot_de_passe, "", TELNET, EXPECT)
        assert resultat == core.Lancement([TELNET, "h.example", "2323"])
        assert resultat.mot_de_passe == ""


def test_port_absent_et_parametres():
    """Port vide : 23 ; paramètres découpés comme un shell, avant l'hôte."""
    resultat = core.lancement("h", None, "", "", "-8 -e '^]'", TELNET, None)
    assert resultat.argv == [TELNET, "-8", "-e", "^]", "h", "23"]


def test_avec_mot_de_passe_expect():
    """Utilisateur et mot de passe : script expect, mot de passe hors de argv."""
    resultat = core.lancement("h", 23, "alice", "s3 cr'et", "-8", TELNET, EXPECT)
    assert resultat.argv == [
        EXPECT,
        "-f",
        str(core.SCRIPT_EXPECT),
        TELNET,
        "-l",
        "alice",
        "-8",
        "h",
        "23",
    ]
    assert resultat.mot_de_passe == "s3 cr'et"
    assert all("s3" not in a for a in resultat.argv)
    assert "s3" not in repr(resultat) and "***" in repr(resultat)
    assert "***" not in repr(core.Lancement(["x"]))


def test_expect_absent():
    """Saisie automatique demandée sans expect : FileNotFoundError nommant le programme."""
    with pytest.raises(FileNotFoundError) as erreur:
        core.lancement("h", 23, "alice", "secret", "", TELNET, None)
    assert erreur.value.args == ("expect",)


def test_parametres_mal_formes():
    """Guillemet non fermé : ValueError de shlex, rien n'est lancé."""
    with pytest.raises(ValueError, match="quotation"):
        core.lancement("h", 23, "", "", "-e '", TELNET, EXPECT)


def test_environnement():
    """TERM de l'option, ``xterm`` si vide ou blanc."""
    assert core.environnement({}) == ["TERM=xterm"]
    assert core.environnement({core.OPT_TERM: "  "}) == ["TERM=xterm"]
    assert core.environnement({core.OPT_TERM: " vt100 "}) == ["TERM=vt100"]


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [("", 0), ("0", 0), ("2", 2), ("4", 4), ("5", 0), ("-1", 0), ("x", 0), (" 3 ", 3)],
)
def test_liaison(brut, attendu):
    """Liaison d'effacement 0 à 4 ; toute autre valeur : automatique (0)."""
    assert core.liaison({core.OPT_RETOUR_ARRIERE: brut}, core.OPT_RETOUR_ARRIERE) == attendu
    assert core.liaison({}, core.OPT_SUPPRESSION) == 0


def test_commandes_aller_retour():
    r"""Forme INI d'une ligne (``\n``), relue à l'identique ; NUL de GTK3 accepté."""
    texte = "enable\n##D=500\nshow run"
    brut = core.ecrire_commandes(texte)
    assert "\n" not in brut and brut == "enable\\n##D=500\\nshow run"
    assert core.lire_commandes(brut) == texte
    assert core.lire_commandes("a\x00b") == "a\nb"
    assert core.ecrire_commandes("a\r\nb") == "a\\nb"


def test_etapes_comme_gtk3():
    """3 s puis délais cumulés ; lignes groupées, séparées et terminées par CR."""
    texte = "enable\nsecret\n##D=1000\nterm len 0\n##D=250\n##D=250\nshow run"
    assert core.etapes(texte) == [
        (3000, "enable\rsecret\r"),
        (4000, "term len 0\r"),
        (4500, "show run\r"),
    ]


def test_etapes_cas_limites():
    """Vide : rien ; délai seul : rien ; ``##D=`` non numérique : ligne ordinaire."""
    assert core.etapes("") == []
    assert core.etapes("##D=100") == []
    assert core.etapes("##D=abc\n##D=\n#x") == [(3000, "##D=abc\r##D=\r#x\r")]
    assert core.etapes("ls\n##D=100") == [(3000, "ls\r")]
    assert core.est_delai("##D=10") and not core.est_delai(" ##D=10")


def test_script_expect_livre():
    """Le script expect est à côté du module et lit le mot de passe sur le descripteur 3."""
    texte = core.SCRIPT_EXPECT.read_text(encoding="utf-8")
    assert core.SCRIPT_EXPECT.parent == Path(core.__file__).parent
    assert "/dev/fd/3" in texte and "interact" in texte
