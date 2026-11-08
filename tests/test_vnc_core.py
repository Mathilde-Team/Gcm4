# SPDX-License-Identifier: GPL-3.0-or-later
"""Options VNC sans GTK, compatibilité INI et bornes du port (#179, #82)."""

from __future__ import annotations

import pytest

from gcm4.core.config import load_config, save_config
from gcm4.core.model import Host
from plugins.pluginvnc2 import core


def hote(options=None, **champs):
    """Construit un hôte VNC sans journaliser de données utilisateur."""
    return Host(name="test", protocol="vnc", protocol_options={"vnc": options or {}}, **champs)


def test_defauts_copie_independante():
    """Partage actif, contrôle et presse-papier conservés ; pas d'état global mutable."""
    premiere = core.options_defaut()
    premiere[core.OPT_PARTAGE] = "False"
    assert core.options_defaut()[core.OPT_PARTAGE] == "True"
    assert core.reglages(hote()) == core.Reglages(5900, False, False, True, True)


@pytest.mark.parametrize(
    "brut,defaut,attendu",
    [
        (" true ", False, True),
        ("YES", False, True),
        ("1", False, True),
        ("on", False, True),
        ("False", True, False),
        ("no", True, False),
        ("0", True, False),
        ("off", True, False),
        ("?", True, True),
        ("", False, False),
    ],
)
def test_booleens_ini(brut, defaut, attendu):
    """Les huit formes de ConfigParser sont relues, sans bool('False')."""
    assert core.booleen({"cle": brut}, "cle", defaut) is attendu
    assert core.booleen({}, "cle", defaut) is defaut


@pytest.mark.parametrize("affichage", ["-1", "a", "1.0", "²", "１", "59636", "9" * 5000])
def test_affichage_invalide(affichage):
    """Toute erreur devient une FieldError, même au-delà de la limite int de Python."""
    options = {core.OPT_AFFICHAGE: affichage}
    assert [e.champ for e in core.valider(options)] == [core.OPT_AFFICHAGE]
    with pytest.raises(ValueError, match="VNC display"):
        core.reglages(hote(options))


@pytest.mark.parametrize(
    "affichage,port",
    [("", 5999), ("0", 5900), (" 1 ", 5901), ("59635", 65535), ("0" * 5000, 5900)],
)
def test_priorite_affichage(affichage, port):
    """L'affichage renseigné remplace le port ; le port seul garde sa valeur."""
    assert core.reglages(hote({core.OPT_AFFICHAGE: affichage}, port=5999)).port == port


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_port_invalide(port):
    """Un appel direct ne contourne pas les bornes vérifiées par le dialogue."""
    with pytest.raises(ValueError, match="Port"):
        core.reglages(hote(port=port))


@pytest.mark.parametrize("lecture_seule", ["True", "False"])
def test_options_historiques_et_partage(lecture_seule):
    """La lecture seule interdit aussi le presse-papier sortant, même si coché."""
    options = {
        core.OPT_LECTURE_SEULE: lecture_seule,
        core.OPT_PARTAGE: "False",
        core.OPT_PLEIN_ECRAN: "True",
        core.OPT_PRESSE_PAPIER: "True",
        "vnc_viewer": "tigervnc",
        "inconnue": "conservée",
    }
    copie = options.copy()
    reglage = core.reglages(hote(options))
    assert reglage.partage is False
    assert reglage.plein_ecran is True
    assert reglage.lecture_seule is (lecture_seule == "True")
    assert reglage.presse_papier is (lecture_seule == "False")
    assert options == copie


def test_presse_papier_desactive():
    """Le mode contrôle n'active jamais implicitement le presse-papier."""
    assert core.reglages(hote({core.OPT_PRESSE_PAPIER: "False"})).presse_papier is False


def test_options_reellement_relues_depuis_ini(tmp_path):
    """Le partage et les clés historiques survivent à l'enregistrement puis rechargement."""
    chemin = tmp_path / "gcm4.conf"
    chemin.write_text(
        "[host 0]\nname = bureau\nhost = localhost\nprotocol = vnc\n"
        "vnc_shared = False\nvnc_view_only = True\nvnc_display = 7\n"
        "vnc_fullscreen = True\nvnc_share_clipboard = True\nvnc_viewer = ancien\n",
        encoding="utf-8",
    )
    config = load_config(str(chemin))
    premier = config.store.hosts[0]
    assert core.reglages(premier) == core.Reglages(5907, True, True, False, False)
    premier.protocol_options["vnc"][core.OPT_PARTAGE] = "True"
    save_config(str(chemin), config)
    recharge = load_config(str(chemin)).store.hosts[0]
    assert core.reglages(recharge).partage is True
    assert recharge.protocol_options["vnc"]["vnc_viewer"] == "ancien"
