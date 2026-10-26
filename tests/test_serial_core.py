"""Logique sans GTK du plugin serial (``plugins/serial/core.py``, #179)."""

from __future__ import annotations

import pytest

import plugins.serial.core as core


def _faux_outils(tmp_path, noms):
    """Répertoire avec un exécutable vide par nom ; le répertoire est renvoyé."""
    for nom in noms:
        programme = tmp_path / nom
        programme.write_text("#!/bin/sh\n", encoding="utf-8")
        programme.chmod(0o755)
    return str(tmp_path)


def test_outil_prefere_prio(tmp_path):
    """L'outil choisi pour l'hôte gagne s'il est installé."""
    chemin = _faux_outils(tmp_path, ("picocom", "screen"))
    assert core.outil({"PATH": chemin}, "screen").endswith("screen")
    assert core.outil({"PATH": chemin}, "minicom").endswith("picocom")


def test_outil_detection_et_absence(tmp_path):
    """Premier outil installé (picocom > minicom > screen) ; aucun : ``None``."""
    chemin = _faux_outils(tmp_path, ("screen",))
    assert core.outil({"PATH": chemin}).endswith("screen")
    assert core.outil({"PATH": ""}) is None


def test_parametres_defauts():
    """Sans options : 9600 8N1, aucun template, aucun outil imposé."""
    assert core.parametres({}) == dict(core.DEFAUTS)


def test_parametres_repli_debit_sur_port():
    """Héritage GTK3 : le champ Port de l'hôte sert de débit sans option ``baud``."""
    assert core.parametres({}, port_hote=115200)["baud"] == "115200"
    assert core.parametres({"baud": "4800"}, port_hote=115200)["baud"] == "4800"


def test_parametres_normalise_les_valeurs_inconnues():
    """Valeurs inconnues (configurations anciennes) : défauts 9600 8N1."""
    parametres = core.parametres(
        {
            "baud": "7",
            "databits": "9",
            "parity": "z",
            "stopbits": "3",
            "flow": "y",
            "template": "Inconnu",
            "tool": "tip",
        }
    )
    assert (
        parametres["baud"],
        parametres["databits"],
        parametres["parity"],
        parametres["stopbits"],
        parametres["flow"],
    ) == ("9600", "8", "n", "1", "n")
    assert parametres["template"] == ""
    assert parametres["tool"] == "tip"


def test_parametres_ignore_les_cles_etrangeres():
    """Seules les clés série sont gardées (les autres options du protocole passent à travers)."""
    assert "commands" not in core.parametres({"commands": "ls"})


def test_parametres_template():
    """Paramètres d'un template connu ; inconnu : ``None``."""
    assert core.parametres_template("F5 TMOS") == ("19200", "n", "n", "8", "1")
    assert core.parametres_template("Inconnu") is None


def test_commande_picocom():
    """Picocom : débit, flux, parité, bits, stop, options libres, puis l'appareil."""
    argv = core.commande(
        "/usr/bin/picocom",
        "/dev/ttyUSB0",
        {"baud": "115200", "flow": "x", "parity": "e", "databits": "7", "stopbits": "2"},
        "--logfile /tmp/s.log",
    )
    assert argv == [
        "/usr/bin/picocom",
        "--baud",
        "115200",
        "--flow",
        "x",
        "--parity",
        "e",
        "--databits",
        "7",
        "--stopbits",
        "2",
        "--logfile",
        "/tmp/s.log",
        "/dev/ttyUSB0",
    ]


def test_commande_picocom_appareil_defaut():
    """Appareil vide : ``/dev/ttyUSB0`` comme GTK3."""
    argv = core.commande(
        "/usr/bin/picocom",
        "",
        {"baud": "9600", "flow": "n", "parity": "n", "databits": "8", "stopbits": "1"},
    )
    assert argv[-1] == "/dev/ttyUSB0"


def test_commande_minicom():
    """Minicom : ``-b`` ``-D``, bits et stop, flux logiciel ou matériel, options libres."""
    base = {"baud": "9600", "flow": "n", "parity": "n", "databits": "8", "stopbits": "1"}
    argv = core.commande("/usr/bin/minicom", "/dev/ttyS0", dict(base, flow="x"), "-o")
    assert argv == [
        "/usr/bin/minicom",
        "-b",
        "9600",
        "-D",
        "/dev/ttyS0",
        "--databits",
        "8",
        "--stopbits",
        "1",
        "-f",
        "on",
        "-o",
    ]
    argv = core.commande("/usr/bin/minicom", "/dev/ttyS0", dict(base, flow="h"))
    assert "--rtscts" in argv
    argv = core.commande("/usr/bin/minicom", "/dev/ttyS0", base)
    assert "-f" not in argv and "--rtscts" not in argv


def test_commande_screen():
    """Screen : appareil, débit, puis bits-parité-stop collés (8n1), options libres."""
    argv = core.commande(
        "/usr/bin/screen",
        "/dev/ttyUSB1",
        {"baud": "115200", "flow": "n", "parity": "e", "databits": "7", "stopbits": "1"},
        "-L",
    )
    assert argv == ["/usr/bin/screen", "/dev/ttyUSB1", "115200", "7e1", "-L"]


def test_commande_extra_mal_forme():
    """Options libres mal formées : ``ValueError`` de ``shlex`` (documenté dans le contrat)."""
    with pytest.raises(ValueError):
        core.commande("/usr/bin/picocom", "/dev/ttyUSB0", core.DEFAUTS, 'mauvais "')
