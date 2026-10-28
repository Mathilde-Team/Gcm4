# SPDX-License-Identifier: GPL-3.0-or-later
"""Préférences générales sans GTK : inventaire de Wconfig, lecture, écriture (#147)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gcm4.core import preferences as p
from gcm4.core.config import ConfigError

ROOT = Path(__file__).resolve().parents[1]


def _options_de_wconfig() -> set[str]:
    """Noms ``conf.X`` utilisés dans la classe GTK3 ``Wconfig``."""
    arbre = ast.parse((ROOT / "gnome_connection_manager.py").read_text(encoding="utf-8"))
    classe = next(
        n for n in ast.walk(arbre) if isinstance(n, ast.ClassDef) and n.name == "Wconfig"
    )
    noms = set()
    for noeud in ast.walk(classe):
        if (
            isinstance(noeud, ast.Attribute)
            and isinstance(noeud.value, ast.Name)
            and noeud.value.id == "conf"
            and noeud.attr.isupper()
        ):
            noms.add(noeud.attr)
        if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
            if noeud.value.startswith("conf.") and noeud.value[5:].isupper():
                noms.add(noeud.value[5:])
    return noms


def test_chaque_option_de_wconfig_est_classee():
    """Critère de #147 : aucune option de Wconfig ne disparaît sans classement."""
    trouvees = _options_de_wconfig()
    assert {"CONFIRM_ON_EXIT", "WORD_SEPARATORS", "THEME_MODE"} <= trouvees  # garde-fou
    classees = {o.nom for o in p.INVENTAIRE}
    assert trouvees - classees == set()


def test_inventaire_coherent():
    """Noms uniques, classement connu, destination renseignée."""
    noms = [o.nom for o in p.INVENTAIRE]
    assert len(noms) == len(set(noms))
    for option in p.INVENTAIRE:
        assert option.classement in p.CLASSEMENTS and option.destination
    pages_5a = {o.nom for o in p.INVENTAIRE if "#147" in o.destination}
    assert pages_5a == {"RESTORE_OPEN_TABS", "CONFIRM_ON_EXIT", "CHECK_UPDATES", "THEME_MODE"}


def test_valeurs_par_defaut_sans_fichier(tmp_path):
    """Fichier absent : valeurs par défaut de GTK3."""
    assert p.load_preferences(str(tmp_path / "absent.conf")) == p.Preferences()


def test_lecture_des_cles_gtk3(tmp_path):
    """Les clés de GTK3 ([options], [window]) sont relues telles quelles."""
    conf = tmp_path / "gcm4.conf"
    conf.write_text(
        "[options]\nconfirm-exit = False\ncheck-updates = False\nrestore-open-tabs = True\n"
        "[window]\ntheme-mode = dark\nshow-panel = False\ntree-font =  Serif 12 \n",
        encoding="utf-8",
    )
    assert p.load_preferences(str(conf)) == p.Preferences(
        confirm_exit=False,
        check_updates=False,
        restore_open_tabs=True,
        theme="dark",
        show_panel=False,
        tree_font="Serif 12",
    )


def test_booleen_invalide_valeur_par_defaut(tmp_path):
    """Valeur illisible : valeur par défaut, pas d'exception."""
    conf = tmp_path / "gcm4.conf"
    conf.write_text("[options]\nconfirm-exit = peut-etre\n", encoding="utf-8")
    assert p.load_preferences(str(conf)).confirm_exit is True


def test_enregistrement_ne_touche_que_la_cle(tmp_path):
    """Une préférence écrite : le reste du fichier (hôtes, autres clés) est conservé."""
    conf = tmp_path / "gcm4.conf"
    conf.write_text(
        "[options]\nfont = Monospace 11\nconfirm-exit = True\n"
        "[host 1]\nname = api\npass = secret\n[window]\nleft-panel-width = 260\n",
        encoding="utf-8",
    )
    p.save_preference(str(conf), "confirm_exit", False)
    p.save_preference(str(conf), "tree_font", "  Sans Bold 10 ")
    p.save_preference(str(conf), "show_panel", False)
    p.save_preference(str(conf), "theme", "dark")
    texte = conf.read_text(encoding="utf-8")
    for ligne in ("font = Monospace 11", "name = api", "pass = secret", "left-panel-width = 260"):
        assert ligne in texte
    relu = p.load_preferences(str(conf))
    assert (relu.confirm_exit, relu.tree_font, relu.show_panel, relu.theme) == (
        False,
        "Sans Bold 10",
        False,
        "dark",
    )
    assert "dark-mode = True" in texte  # compatibilité GTK3 (#135)


def test_preference_inconnue_ou_mal_typee(tmp_path):
    """Nom inconnu ou type inattendu : ValueError, rien n'est écrit."""
    conf = tmp_path / "gcm4.conf"
    with pytest.raises(ValueError):
        p.save_preference(str(conf), "inconnue", True)
    with pytest.raises(ValueError):
        p.save_preference(str(conf), "confirm_exit", "oui")
    assert not conf.exists()


def test_fichier_corrompu_jamais_reecrit(tmp_path):
    """Fichier illisible : ConfigError, contenu intact."""
    conf = tmp_path / "gcm4.conf"
    conf.write_text("pas une section\n= =\n", encoding="utf-8")
    avant = conf.read_bytes()
    with pytest.raises(ConfigError):
        p.save_preference(str(conf), "confirm_exit", False)
    assert conf.read_bytes() == avant


def test_inventaire_documente():
    """docs/architecture.md §2.11 liste chaque option de l'inventaire."""
    doc = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert "### 2.11 Préférences" in doc
    for option in p.INVENTAIRE:
        assert f"| `{option.nom}` |" in doc, option.nom


# --- lot 5b (#148) : journalisation, raccourcis -----------------------------------


def test_niveau_de_journal_lu_et_ecrit(tmp_path):
    """[options] log-level : relu en majuscules ; inconnu → INFO ; écriture vérifiée."""
    conf = tmp_path / "gcm4.conf"
    conf.write_text("[options]\nlog-level = debug\n", encoding="utf-8")
    assert p.load_preferences(str(conf)).log_level == "DEBUG"
    p.save_preference(str(conf), "log_level", "WARNING")
    assert p.load_preferences(str(conf)).log_level == "WARNING"
    with pytest.raises(ValueError):
        p.save_preference(str(conf), "log_level", "BAVARD")
    conf.write_text("[options]\nlog-level = bavard\n", encoding="utf-8")
    assert p.load_preferences(str(conf)).log_level == "INFO"


def test_priorite_du_niveau_de_journal():
    """--debug, puis GCM_LOG_LEVEL / GCM_DEBUG, puis la préférence."""
    assert p.niveau_journal(True, "ERROR", {"GCM_LOG_LEVEL": "TRACE"}) == ("DEBUG", "cli")
    assert p.niveau_journal(False, "ERROR", {"GCM_LOG_LEVEL": "TRACE"}) == ("", "env")
    assert p.niveau_journal(False, "ERROR", {"GCM_DEBUG": "1"}) == ("", "env")
    assert p.niveau_journal(False, "ERROR", {"GCM_DEBUG": " "}) == ("ERROR", "preferences")
    assert p.niveau_journal(False, "BAVARD", {}) == ("INFO", "preferences")


def test_main_applique_le_niveau_enregistre(tmp_path, monkeypatch):
    """gcm4 sans --debug ni variable : niveau de Préférences > Journalisation."""
    import sys

    import logging_config
    from gcm4 import app
    from gcm4.core import chemins

    (tmp_path / "gcm4.conf").write_text("[options]\nlog-level = WARNING\n", encoding="utf-8")
    appels = []
    monkeypatch.delenv("GCM_LOG_LEVEL", raising=False)
    monkeypatch.delenv("GCM_DEBUG", raising=False)
    monkeypatch.setattr(chemins, "preparer", lambda *_a: None)
    monkeypatch.setattr(
        logging_config, "configure_logging", lambda niveau=None, **kw: appels.append(niveau)
    )
    monkeypatch.setitem(sys.modules, "gcm4.ui", None)  # GTK « absent » : main s'arrête là
    app.main(["-c", str(tmp_path)])
    assert appels[-1] == "WARNING"


def test_cle_options_hors_modele_conservee(tmp_path):
    """Une clé [options] sans champ AppConfig écrite en session survit à save_config."""
    from gcm4.core.config import load_config, save_config

    conf = tmp_path / "gcm4.conf"
    conf.write_text("[options]\nconfirm-exit = True\n", encoding="utf-8")
    cfg = load_config(str(conf))
    p.save_preference(str(conf), "log_level", "ERROR")  # après la lecture du modèle
    save_config(str(conf), cfg)
    assert p.load_preferences(str(conf)).log_level == "ERROR"


def test_raccourcis_de_la_base():
    """Chaque raccourci du menu et de l'arbre est listé, avec un libellé du menu."""
    from gcm4.core import menu

    liste = p.raccourcis()
    fenetre = {r.touches for r in liste if r.portee == p.FENETRE}
    assert fenetre == set(menu.RACCOURCIS.values())
    arbre = {t for r in liste if r.portee == p.ARBRE for t in r.touches}
    assert {"Delete", "F2", "Menu", "<Shift>F10", "Return"} <= arbre
    assert next(r for r in liste if r.touches == ("<Control>q",)).libelle == "Quit"


def test_section_options_creee_en_session_conservee(tmp_path):
    """Fichier lu sans [options], préférence écrite ensuite : conservée par save_config."""
    from gcm4.core.config import load_config, save_config

    conf = tmp_path / "gcm4.conf"
    conf.write_text("[window]\nshow-panel = True\n", encoding="utf-8")
    cfg = load_config(str(conf))
    cfg.brut.remove_section("options") if cfg.brut.has_section("options") else None
    p.save_preference(str(conf), "log_level", "TRACE")
    save_config(str(conf), cfg)
    assert p.load_preferences(str(conf)).log_level == "TRACE"


# --- #247 : onglets, rayures, titre -------------------------------------------------


def test_options_de_base_247_lues_et_ecrites(tmp_path):
    """Clés GTK3 de #247 relues ; écriture d'une seule clé, relue à l'identique."""
    conf = tmp_path / "gcm4.conf"
    conf.write_text(
        "[options]\nconfirm-close-tab = False\nconfirm-close-tab-middle = False\n"
        "cycle-tabs = False\ndisable-hosts-stripes = True\nupdate-title = True\n"
        "app-title =  Production \n",
        encoding="utf-8",
    )
    prefs = p.load_preferences(str(conf))
    assert (prefs.confirm_close_tab, prefs.confirm_close_tab_middle, prefs.cycle_tabs) == (
        False,
        False,
        False,
    )
    assert (prefs.disable_hosts_stripes, prefs.update_title, prefs.app_title) == (
        True,
        True,
        "Production",
    )
    p.save_preference(str(conf), "cycle_tabs", True)
    p.save_preference(str(conf), "app_title", "  Recette ")
    relu = p.load_preferences(str(conf))
    assert (relu.cycle_tabs, relu.app_title, relu.confirm_close_tab) == (True, "Recette", False)
    with pytest.raises(ValueError):
        p.save_preference(str(conf), "update_title", "oui")


def test_titre_par_defaut_ecrit_par_gtk3(tmp_path):
    """GTK3 écrit le nom de l'application pour un titre vide : relu comme vide."""
    conf = tmp_path / "gcm4.conf"
    conf.write_text("[options]\napp-title = Gnome Connection Manager\n", encoding="utf-8")
    assert p.load_preferences(str(conf)).app_title == ""
    assert p.Preferences().confirm_close_tab and p.Preferences().cycle_tabs  # défauts GTK3
    assert not p.Preferences().disable_hosts_stripes and not p.Preferences().update_title


def test_options_247_classees_page_preferences():
    """Les six options et l'édition des raccourcis (#247) ne renvoient plus à une « issue de suivi »."""
    options = {
        "CONFIRM_ON_CLOSE_TAB",
        "CONFIRM_ON_CLOSE_TAB_MIDDLE",
        "CYCLE_TABS",
        "DISABLE_HOSTS_STRIPES",
        "UPDATE_TITLE",
        "APP_TITLE",
        "shortcuts",
    }
    assert {o.nom for o in p.INVENTAIRE if "#247" in o.destination} == options
    assert all("issue de suivi" not in o.destination for o in p.INVENTAIRE if o.nom in options)
    assert {"confirm_close_tab", "app_title"} <= set(p.CLES)
