"""Logique commune aux plugins terminal, sans GTK (``gcm4.core.terminal``, #295)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gcm4.core import terminal

RACINE = Path(__file__).resolve().parents[1]


def test_lancement_sans_mot_de_passe_dans_repr():
    """Le mot de passe n'apparaît jamais dans la représentation (journal)."""
    avec = terminal.Lancement(["/usr/bin/expect", "-f", "x"], "s3cr et")
    assert "s3cr" not in repr(avec) and "***" in repr(avec)
    sans = terminal.Lancement(["/bin/sh"])
    assert sans.mot_de_passe == "" and "***" not in repr(sans)
    assert repr(sans) == "Lancement(argv=['/bin/sh'], mot_de_passe='')"


def test_environnement():
    """TERM de l'option, ``xterm`` si absente, vide ou blanche."""
    assert terminal.environnement({}) == ["TERM=xterm"]
    assert terminal.environnement({terminal.OPT_TERM: "\t "}) == ["TERM=xterm"]
    assert terminal.environnement({terminal.OPT_TERM: " screen-256color "}) == [
        "TERM=screen-256color"
    ]


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [("", 0), ("1", 1), ("4", 4), ("5", 0), ("10", 0), ("-2", 0), ("1.5", 0), (" 2\n", 2)],
)
def test_liaison(brut, attendu):
    """Valeurs 0 à 4 de ``Vte.EraseBinding`` ; toute autre : automatique (0)."""
    assert terminal.liaison({terminal.OPT_SUPPRESSION: brut}, terminal.OPT_SUPPRESSION) == attendu
    assert terminal.liaison({terminal.OPT_SUPPRESSION: brut}, terminal.OPT_RETOUR_ARRIERE) == 0
    assert terminal.NB_LIAISONS == 5


def test_commandes_forme_ini():
    r"""Une ligne INI (``\n`` littéral), relue à l'identique ; NUL et CRLF de GTK3 acceptés."""
    texte = "cd /tmp\n##D=200\nls -l"
    brut = terminal.ecrire_commandes(texte)
    assert brut == "cd /tmp\\n##D=200\\nls -l" and "\n" not in brut
    assert terminal.lire_commandes(brut) == texte
    assert terminal.lire_commandes("a\x00b\x00") == "a\nb\n"
    assert terminal.ecrire_commandes("a\r\nb\n") == "a\\nb\\n"
    assert terminal.lire_commandes("") == "" and terminal.ecrire_commandes("") == ""


@pytest.mark.parametrize(
    ("ligne", "attendu"),
    [("##D=0", True), ("##D=1500", True), ("##D=", False), ("##D=1s", False), (" ##D=5", False)],
)
def test_est_delai(ligne, attendu):
    """Seule une ligne ``##D=`` suivie de chiffres, sans espace avant, est un délai."""
    assert terminal.est_delai(ligne) is attendu


@pytest.mark.parametrize("debut", [0, 700, 3000])
def test_etapes_debut_propre_au_plugin(debut):
    """Le premier envoi part à ``debut_ms`` ; les délais s'ajoutent ; lignes groupées, terminées par CR."""
    texte = "a\nb\n##D=100\nc\n##D=50\n##D=50\nd"
    assert terminal.etapes(texte, debut) == [
        (debut, "a\rb\r"),
        (debut + 100, "c\r"),
        (debut + 200, "d\r"),
    ]


def test_etapes_cas_limites():
    """Vide ou délais seuls : aucun envoi ; délai final ignoré ; ligne vide envoyée (Entrée)."""
    assert terminal.etapes("", 700) == []
    assert terminal.etapes("##D=10\n##D=20", 700) == []
    assert terminal.etapes("ls\n##D=100", 700) == [(700, "ls\r")]
    assert terminal.etapes("\nls", 0) == [(0, "\rls\r")]


def test_coeur_sans_gtk():
    """``gcm4.core.terminal`` n'importe ni ``gi`` ni ``gcm4.ui`` (contrat import-linter)."""
    arbre = ast.parse((RACINE / "src/gcm4/core/terminal.py").read_text(encoding="utf-8"))
    modules = {
        n.module if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(arbre)
        if isinstance(n, (ast.Import, ast.ImportFrom))  # noqa: UP038 (Python 3.9, #286)
        for a in n.names
    }
    assert not {m for m in modules if m and (m == "gi" or m.startswith(("gi.", "gcm4.ui")))}


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ("", "UTF-8"),
        ("  ", "UTF-8"),
        ("utf-8", "UTF-8"),
        ("ISO-8859-15", "ISO-8859-15"),
        (" windows-1252 ", "WINDOWS-1252"),
        ("shift_jis", "SHIFT_JIS"),
        ("BIDON", "UTF-8"),
        ("latin1", "UTF-8"),
    ],
)
def test_encodage(brut, attendu):
    """Nom de la liste, casse ignorée ; vide ou inconnu : UTF-8 (#85)."""
    assert terminal.encodage({terminal.OPT_ENCODAGE: brut}) == attendu
    assert terminal.encodage({}) == terminal.ENCODAGE_DEFAUT == "UTF-8"


def test_encodages_connus_de_python():
    """Chaque encodage proposé est connu de Python, sans doublon, UTF-8 en premier."""
    import codecs

    assert terminal.ENCODAGES[0] == terminal.ENCODAGE_DEFAUT
    assert len(set(terminal.ENCODAGES)) == len(terminal.ENCODAGES)
    for nom in terminal.ENCODAGES:
        assert nom == nom.upper()
        codecs.lookup(nom)


def test_encoder():
    """Message encodé dans l'encodage de l'onglet ; caractère absent remplacé par ``?``."""
    assert terminal.encoder("Session terminée", "UTF-8") == "Session terminée".encode()
    assert terminal.encoder("Session terminée", "ISO-8859-1") == b"Session termin\xe9e"
    assert terminal.encoder("€ ok", "ISO-8859-1") == b"? ok"
    assert terminal.encoder("€", "ISO-8859-15") == b"\xa4"
