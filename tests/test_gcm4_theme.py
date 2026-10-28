# SPDX-License-Identifier: GPL-3.0-or-later
"""Thème de la base GTK4, sans GTK (#135)."""

from __future__ import annotations

import configparser

import pytest

from gcm4.core.config import ConfigError
from gcm4.core.theme import MODES, SCHEMAS, color_class, colors_css, load_theme, save_theme


@pytest.mark.parametrize(
    ("contenu", "attendu"),
    [
        ("", "system"),
        ("[window]\ntheme-mode = dark\n", "dark"),
        ("[window]\ntheme-mode = LIGHT\n", "light"),
        ("[window]\ntheme-mode = violet\n", "system"),
        ("[window]\ndark-mode = True\n", "dark"),  # ancienne clé seule (GTK3 avant theme-mode)
        ("[window]\ndark-mode = peut-etre\n", "system"),
        ("[window]\ntheme-mode = light\ndark-mode = True\n", "light"),  # theme-mode prime
    ],
)
def test_lecture(tmp_path, contenu, attendu):
    """theme-mode validée par gcm4_core.resolve_theme_mode ; repli sur dark-mode puis system."""
    conf = tmp_path / "gcm.conf"
    conf.write_text(contenu, encoding="utf-8")
    assert load_theme(str(conf)) == attendu


def test_fichier_absent(tmp_path):
    """Sans gcm.conf : system."""
    assert load_theme(str(tmp_path / "absent.conf")) == "system"


def test_enregistrement_conserve_le_reste(tmp_path):
    """theme-mode et dark-mode écrits ; les autres clés restent ; relu après « redémarrage »."""
    conf = tmp_path / "gcm.conf"
    conf.write_text("[window]\nleft-panel-width = 260\n[host 1]\nname = web\n", encoding="utf-8")
    save_theme(str(conf), "dark")
    cp = configparser.RawConfigParser()
    cp.read(conf, encoding="utf-8")
    assert (cp.get("window", "theme-mode"), cp.get("window", "dark-mode")) == ("dark", "True")
    assert cp.get("window", "left-panel-width") == "260" and cp.get("host 1", "name") == "web"
    assert load_theme(str(conf)) == "dark"
    save_theme(str(conf), "light")
    assert load_theme(str(conf)) == "light"


def test_mode_inconnu_refuse(tmp_path):
    """Mode hors system/light/dark : ValueError, rien d'écrit."""
    with pytest.raises(ValueError):
        save_theme(str(tmp_path / "gcm.conf"), "violet")
    assert not (tmp_path / "gcm.conf").exists()


def test_fichier_corrompu_jamais_reecrit(tmp_path):
    """gcm.conf corrompu : ConfigError, fichier intact."""
    conf = tmp_path / "gcm.conf"
    conf.write_text("[window\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        save_theme(str(conf), "dark")
    assert conf.read_text(encoding="utf-8") == "[window\n"


def test_schemas_pour_chaque_mode():
    """Chaque mode a son Adw.ColorScheme."""
    assert set(SCHEMAS) == set(MODES)
    assert SCHEMAS["dark"] == "FORCE_DARK"


@pytest.mark.parametrize(
    ("couleur", "classe"),
    [
        ("#FF8800", "gcm-couleur-ff8800"),
        ("#f80", "gcm-couleur-ff8800"),
        (" #00aaff ", "gcm-couleur-00aaff"),
    ],
)
def test_classe_de_couleur(couleur, classe):
    """Classe CSS normalisée (minuscules, forme longue)."""
    assert color_class(couleur) == classe


@pytest.mark.parametrize(
    "couleur", ["", "rouge", "#12345", "#gggggg", "ff8800", "#ff8800; } * { color: red"]
)
def test_couleur_invalide_ou_injection(couleur):
    """Couleur invalide (ou tentative d'injection CSS) : aucune classe."""
    assert color_class(couleur) is None


def test_feuille_css():
    """Une règle par couleur valide, triées, sans doublon."""
    assert colors_css(["#00aaff", "#FF8800", "#f80", "invalide"]) == (
        ".gcm-couleur-00aaff { color: #00aaff; }\n.gcm-couleur-ff8800 { color: #ff8800; }\n"
    )
    assert colors_css([]) == ""
