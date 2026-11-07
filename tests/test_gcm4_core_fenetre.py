# SPDX-License-Identifier: GPL-3.0-or-later
"""Cœur sans GTK du lot 1b (#134) : état de fenêtre, menu, traductions."""

from __future__ import annotations

import configparser
from pathlib import Path

from gcm4.core import i18n, menu, window_state
from gcm4.core.window_state import WindowState, load_window_state, save_window_state

ROOT = Path(__file__).resolve().parents[1]


def test_fichier_absent_valeurs_par_defaut(tmp_path):
    """Sans gcm.conf : valeurs par défaut de l'application GTK3 (utils.conf)."""
    assert load_window_state(str(tmp_path / "absent.conf")) == WindowState(-1, -1, 100, True)


def test_cles_de_l_application_gtk3(tmp_path):
    """Les clés [window] écrites par Wmain.writeConfig sont relues."""
    conf = tmp_path / "gcm.conf"
    conf.write_text(
        "[window]\nwindow-width = 1200\nwindow-height = 700\nleft-panel-width = 260\nshow-panel = False\n",
        encoding="utf-8",
    )
    assert load_window_state(str(conf)) == WindowState(1200, 700, 260, False)


def test_valeur_invalide_remplacee_par_le_defaut(tmp_path):
    """Une valeur non numérique ou non booléenne retombe sur le défaut, sans exception."""
    conf = tmp_path / "gcm.conf"
    conf.write_text("[window]\nwindow-width = large\nshow-panel = peut-etre\n", encoding="utf-8")
    assert load_window_state(str(conf)) == WindowState()


def test_ini_corrompu_lu_comme_vide(tmp_path):
    """Un INI illisible donne l'état par défaut (pas d'exception au démarrage)."""
    conf = tmp_path / "gcm.conf"
    conf.write_text("ceci n'est pas un INI\n[window\n", encoding="utf-8")
    assert load_window_state(str(conf)) == WindowState()


def test_aller_retour_et_autres_cles_conservees(tmp_path):
    """L'écriture ne change que les 4 clés ; sections et clés inconnues restent."""
    conf = tmp_path / "gcm.conf"
    conf.write_text(
        "[options]\nfont = Monospace 10\n[window]\ngroup-colors = LAB:#ff0000\n[host 1]\nname = web\n",
        encoding="utf-8",
    )
    save_window_state(str(conf), WindowState(900, 600, 210, False))
    assert load_window_state(str(conf)) == WindowState(900, 600, 210, False)
    cp = configparser.RawConfigParser()
    cp.read(conf, encoding="utf-8")
    assert cp.get("options", "font") == "Monospace 10"
    assert cp.get("window", "group-colors") == "LAB:#ff0000"
    assert cp.get("host 1", "name") == "web"


def test_ecriture_cree_le_dossier_sans_temporaire_restant(tmp_path):
    """Dossier créé si besoin ; le fichier .tmp est renommé, jamais laissé."""
    conf = tmp_path / "nouveau" / "gcm.conf"
    save_window_state(str(conf), WindowState())
    assert conf.is_file()
    assert not (tmp_path / "nouveau" / "gcm.conf.tmp").exists()
    assert window_state.SECTION in conf.read_text(encoding="utf-8")


def test_menu_actions_et_raccourcis():
    """Le menu de la base n'utilise que des actions app.* et win.* ; chaque raccourci vise une action du menu."""
    actions = menu.actions_du_menu()
    assert actions == [
        "win.import-hosts",
        "win.export-hosts",
        "win.save-config-as",
        "app.quit",
        "app.preferences",
        "win.toggle-panel",
        "win.search",
        "win.previous-tab",
        "win.next-tab",
        "app.theme",
        "app.help",
        "app.shortcuts",
        "app.about",
    ]
    assert set(menu.RACCOURCIS) <= set(actions)


def test_menu_aide():
    """Aide (#86) : guide (F1) et raccourcis (Ctrl+?) avant « À propos », séparés de lui."""
    aide = next(sm for sm in menu.MENU_PRINCIPAL if sm.titre == "Help")
    assert [[e.action for e in section] for section in aide.sections] == [
        ["app.help", "app.shortcuts"],
        ["app.about"],
    ]
    assert menu.RACCOURCIS["app.help"] == ("F1",)
    assert menu.RACCOURCIS["app.shortcuts"] == ("<Control>question",)
    assert menu.GUIDE_UTILISATEUR == (
        "https://github.com/Mathilde-Team/Gcm4/blob/main/Documentation-fr.md"
    )
    # Le guide ouvert est bien celui que docs/guide/utilisateur.md inclut.
    inclusion = (ROOT / "docs" / "guide" / "utilisateur.md").read_text(encoding="utf-8")
    assert '"Documentation-fr.md"' in inclusion
    assert (ROOT / "Documentation-fr.md").is_file()


def test_libelles_du_menu_deja_traduits():
    """Chaque libellé du menu existe dans lang/fr_FR.po (msgids de l'application GTK3)."""
    po = (ROOT / "lang" / "fr_FR.po").read_text(encoding="utf-8")
    libelles = {sm.titre for sm in menu.MENU_PRINCIPAL} | {
        e.libelle for sm in menu.MENU_PRINCIPAL for s in sm.sections for e in s
    }
    assert [m for m in sorted(libelles) if f'msgid "{m}"' not in po] == []


def test_traduction_francaise(monkeypatch):
    """Avec LANGUAGE=fr_FR, « Show Panel » est traduit (catalogue gcm-lang du dépôt)."""
    monkeypatch.setenv("LANGUAGE", "fr_FR")
    try:
        i18n.install(ROOT / "lang")
        assert i18n._("Show Panel") == "Afficher le panneau"
    finally:
        monkeypatch.setenv("LANGUAGE", "C")
        i18n.install(ROOT / "lang")


def test_sans_catalogue_texte_d_origine(tmp_path):
    """Dossier sans catalogue : le msgid anglais est renvoyé tel quel."""
    i18n.install(tmp_path)
    assert i18n._("Quit") == "Quit"


def test_ini_corrompu_jamais_reecrit(tmp_path):
    """Enregistrer l'état sur un gcm.conf corrompu : ConfigError, fichier intact (pas d'écrasement)."""
    import pytest

    from gcm4.core.config import ConfigError

    conf = tmp_path / "gcm.conf"
    conf.write_text("[host 1\nname = precieux\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        save_window_state(str(conf), WindowState(800, 600, 200, True))
    assert conf.read_text(encoding="utf-8") == "[host 1\nname = precieux\n"


def test_etat_ecrit_en_0600(tmp_path):
    """gcm.conf contient des mots de passe : l'état de fenêtre l'écrit en 0600."""
    import stat

    conf = tmp_path / "gcm.conf"
    conf.write_text("[options]\n", encoding="utf-8")
    conf.chmod(0o644)
    save_window_state(str(conf), WindowState())
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
