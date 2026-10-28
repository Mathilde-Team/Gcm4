# SPDX-License-Identifier: GPL-3.0-or-later
"""Raccourcis modifiables de la fenêtre, sans GTK : section ``[shortcuts]`` (#247)."""

from __future__ import annotations

import configparser

import pytest
from loguru import logger

from gcm4.core import config as config_mod
from gcm4.core import menu
from gcm4.core import preferences as p
from gcm4.core import raccourcis as r
from gcm4.core.config import ConfigError, load_config, save_config, update_keys

#: Résolution des noms GTK3 sans GDK : quelques touches connues, lettres en minuscule.
_NOMS = {"PAGE_DOWN": "Page_Down", "PAGE_UP": "Page_Up", "TAB": "Tab", "F11": "F11", "F9": "F9"}


def _resoudre(nom: str) -> str | None:
    if nom in _NOMS:
        return _NOMS[nom]
    return nom.lower() if len(nom) == 1 and nom.isalnum() else None


def _ini(chemin) -> configparser.RawConfigParser:
    cp = configparser.RawConfigParser(interpolation=None)
    cp.read(chemin, encoding="utf-8")
    return cp


@pytest.fixture
def conf(tmp_path, monkeypatch):
    """gcm4.conf avec une section [shortcuts] écrite par GTK3 (valeurs par défaut, terminal)."""
    monkeypatch.setattr(config_mod, "_sauvegardes", set())
    chemin = tmp_path / "gcm4.conf"
    chemin.write_text(
        "[options]\nconfirm-exit = True\n\n[shortcuts]\n"
        "copy = CTRL+SHIFT+C\nconsole_previous = CTRL+SHIFT+TAB\nconsole_next = CTRL+TAB\n"
        "shortcut1 = CTRL+F1\ncommand1 = ls\\n\n",
        encoding="utf-8",
    )
    return chemin


def test_cles_couvrent_les_raccourcis_du_menu():
    """Chaque raccourci de la fenêtre a une clé, et aucune clé n'est orpheline."""
    assert set(r.CLES) == set(menu.RACCOURCIS)
    assert len(set(r.CLES.values())) == len(r.CLES)
    # Clés partagées avec GTK3 : les seules sans préfixe gcm4_.
    assert {c for c in r.CLES.values() if not c.startswith("gcm4_")} == set(r.DEFAUTS_GTK3)


def test_defauts_gtk3_sont_des_defauts_gcm4():
    """La valeur par défaut écrite par GTK3 fait partie des valeurs par défaut de gcm4."""
    actions = {cle: action for action, cle in r.CLES.items()}
    for cle, valeur in r.DEFAUTS_GTK3.items():
        accel = r.depuis_gtk3(valeur, _resoudre).accelerateur
        defauts = {r.depuis_accelerateur(a).accelerateur for a in menu.RACCOURCIS[actions[cle]]}
        assert accel in defauts


def test_combinaison_notations():
    """Notation du fichier (GTK3) et accélérateur GTK, modificateurs dans l'ordre GTK3."""
    c = r.Combinaison(("CTRL", "SHIFT"), "Page_Down")
    assert c.gtk3 == "CTRL+SHIFT+PAGE_DOWN"
    assert c.accelerateur == "<Control><Shift>Page_Down"
    assert (
        r.Combinaison((), "F11").gtk3 == "F11" and r.Combinaison((), "F11").accelerateur == "F11"
    )


def test_depuis_accelerateur():
    """Accélérateurs du menu : alias (Primary, Ctrl, Mod1), ordre GTK3, erreurs."""
    assert r.depuis_accelerateur("<Shift><Control>Tab") == r.Combinaison(("CTRL", "SHIFT"), "Tab")
    assert r.depuis_accelerateur("<Primary>q").gtk3 == "CTRL+Q"
    assert r.depuis_accelerateur("<Ctrl><Mod1>x").gtk3 == "CTRL+ALT+X"
    assert r.depuis_accelerateur("<Super>F2").gtk3 == "SUPER+F2"
    assert r.depuis_accelerateur("Menu") == r.Combinaison((), "Menu")
    for invalide in ("<Control", "<Hyper>a", "<Control>", ""):
        with pytest.raises(ValueError):
            r.depuis_accelerateur(invalide)
    for accels in menu.RACCOURCIS.values():  # tous les défauts se lisent
        for accel in accels:
            r.depuis_accelerateur(accel)


def test_depuis_gtk3():
    """Texte du fichier : casse et espaces libres, modificateurs réordonnés, erreurs."""
    assert r.depuis_gtk3("CTRL+PAGE_DOWN", _resoudre) == r.Combinaison(("CTRL",), "Page_Down")
    assert r.depuis_gtk3(" shift + ctrl + a ", _resoudre) == r.Combinaison(("CTRL", "SHIFT"), "a")
    assert r.depuis_gtk3("F11", _resoudre).accelerateur == "F11"
    for invalide in ("", "CTRL+", "HYPER+A", "CTRL+INCONNUE", "+"):
        with pytest.raises(ValueError):
            r.depuis_gtk3(invalide, _resoudre)


def test_effectifs_et_personnalise():
    """Absent ou défaut GTK3 : défauts ; vide : désactivé ; valeur : remplace les défauts."""
    assert r.effectifs({}, _resoudre) == menu.RACCOURCIS
    section = {
        "console_previous": "ctrl+shift+tab",  # défaut GTK3, casse libre
        "console_next": "CTRL+PAGE_DOWN",
        "gcm4_quit": "",
        "gcm4_search": "CTRL+SHIFT+F",
        "copy": "CTRL+SHIFT+C",  # clé du terminal : ignorée
    }
    eff = r.effectifs(section, _resoudre)
    assert eff["win.previous-tab"] == menu.RACCOURCIS["win.previous-tab"]
    assert eff["win.next-tab"] == ("<Control>Page_Down",)
    assert eff["app.quit"] == ()
    assert eff["win.search"] == ("<Control><Shift>f",)
    assert eff["app.preferences"] == menu.RACCOURCIS["app.preferences"]
    assert not r.personnalise(section, "win.previous-tab")
    assert not r.personnalise(section, "app.preferences")
    assert r.personnalise(section, "win.next-tab") and r.personnalise(section, "app.quit")


def test_effectifs_valeur_illisible():
    """Valeur illisible : défaut de l'action, avertissement dans le journal, rien d'autre perdu."""
    messages: list[str] = []
    ident = logger.add(messages.append, level="WARNING", format="{message}")
    logger.enable("gcm4")
    try:
        eff = r.effectifs({"gcm4_quit": "CTRL+", "gcm4_search": "CTRL+J"}, _resoudre)
    finally:
        logger.disable("gcm4")
        logger.remove(ident)
    assert eff["app.quit"] == menu.RACCOURCIS["app.quit"]
    assert eff["win.search"] == ("<Control>j",)
    assert len(messages) == 1 and "gcm4_quit" in messages[0]


def test_valeur_a_ecrire():
    """Notation GTK3, vide pour « désactivé »."""
    assert r.valeur_a_ecrire(None) == ""
    assert r.valeur_a_ecrire(r.Combinaison(("CTRL", "ALT"), "Page_Up")) == "CTRL+ALT+PAGE_UP"


def test_conflit():
    """Autre action, raccourci de l'arbre ; la propre action et une touche libre ne comptent pas."""
    actuels = dict(menu.RACCOURCIS)
    ctrl_q = r.Combinaison(("CTRL",), "q")
    assert r.conflit(ctrl_q, "win.search", actuels) == "app.quit"
    assert r.conflit(ctrl_q, "app.quit", actuels) is None
    assert r.conflit(r.Combinaison(("CTRL", "SHIFT"), "Tab"), "win.next-tab", actuels) == (
        "win.previous-tab"
    )
    assert r.conflit(r.Combinaison(("CTRL",), "j"), "win.search", actuels) is None
    arbre = p.RACCOURCIS_ARBRE
    assert r.conflit(r.Combinaison((), "F2"), "win.search", actuels, arbre) == arbre[2][1]
    assert (
        r.conflit(r.Combinaison(("SHIFT",), "F10"), "win.search", actuels, arbre) == (arbre[0][1])
    )
    actuels["app.quit"] = ()  # désactivé : Ctrl+Q redevient libre
    assert r.conflit(ctrl_q, "win.search", actuels, arbre) is None


def test_lire(conf, tmp_path):
    """Section lue telle quelle ; fichier absent, sans section ou illisible : vide."""
    section = r.lire(str(conf))
    assert section["console_next"] == "CTRL+TAB" and section["command1"] == "ls\\n"
    assert r.lire(str(tmp_path / "absent.conf")) == {}
    sans = tmp_path / "sans.conf"
    sans.write_text("[options]\nconfirm-exit = True\n", encoding="utf-8")
    assert r.lire(str(sans)) == {}
    illisible = tmp_path / "illisible.conf"
    illisible.write_text("pas un INI", encoding="utf-8")
    assert r.lire(str(illisible)) == {}


def test_enregistrer_et_reinitialiser(conf):
    """Écriture d'une seule clé ; le reste de [shortcuts] et du fichier reste intact."""
    r.enregistrer(str(conf), "win.next-tab", r.Combinaison(("CTRL",), "Page_Down"))
    r.enregistrer(str(conf), "app.quit", None)
    cp = _ini(conf)
    assert cp.get("shortcuts", "console_next") == "CTRL+PAGE_DOWN"
    assert cp.get("shortcuts", "gcm4_quit") == ""
    assert cp.get("shortcuts", "copy") == "CTRL+SHIFT+C"
    assert cp.get("shortcuts", "command1") == "ls\\n"
    assert cp.get("options", "confirm-exit") == "True"
    assert conf.with_name("gcm4.conf.bak").exists()
    r.reinitialiser(str(conf), "app.quit")
    r.reinitialiser(str(conf), "app.preferences")  # déjà absente : rien à faire
    cp = _ini(conf)
    assert not cp.has_option("shortcuts", "gcm4_quit")
    assert r.effectifs(r.lire(str(conf)), _resoudre)["app.quit"] == menu.RACCOURCIS["app.quit"]
    with pytest.raises(KeyError):
        r.enregistrer(str(conf), "app.about", None)


def test_enregistrer_refuse_un_fichier_corrompu(conf):
    """Fichier corrompu : ConfigError, jamais réécrit."""
    conf.write_text("pas un INI", encoding="utf-8")
    with pytest.raises(ConfigError):
        r.enregistrer(str(conf), "win.search", r.Combinaison(("CTRL",), "j"))
    assert conf.read_text(encoding="utf-8") == "pas un INI"


def test_save_config_ne_remet_pas_les_raccourcis_du_demarrage(conf):
    """Raccourci modifié ou réinitialisé en session : save_config ne restaure pas l'ancien."""
    cfg = load_config(str(conf))
    r.enregistrer(str(conf), "win.search", r.Combinaison(("CTRL",), "j"))
    r.reinitialiser(str(conf), "win.next-tab")  # console_next présent au démarrage
    save_config(str(conf), cfg)
    cp = _ini(conf)
    assert cp.get("shortcuts", "gcm4_search") == "CTRL+J"
    assert not cp.has_option("shortcuts", "console_next")
    assert cp.get("shortcuts", "copy") == "CTRL+SHIFT+C"
    assert cp.get("shortcuts", "console_previous") == "CTRL+SHIFT+TAB"


def test_save_config_section_retiree_du_disque(conf):
    """Section [shortcuts] supprimée du fichier en session : le fichier fait foi."""
    cfg = load_config(str(conf))
    cp = _ini(conf)
    cp.remove_section("shortcuts")
    with open(conf, "w", encoding="utf-8") as fichier:
        cp.write(fichier)
    save_config(str(conf), cfg)
    assert not _ini(conf).has_section("shortcuts")


def test_update_keys_none_retire_la_cle(conf):
    """Valeur None : clé retirée ; clé absente : sans effet ; les autres valeurs écrites."""
    update_keys(str(conf), "shortcuts", {"copy": None, "absente": None, "paste": "CTRL+V"})
    cp = _ini(conf)
    assert not cp.has_option("shortcuts", "copy") and not cp.has_option("shortcuts", "absente")
    assert cp.get("shortcuts", "paste") == "CTRL+V"


def test_raccourcis_affiches():
    """Page Raccourcis : effectifs pour la fenêtre (action renseignée), arbre fixe."""
    eff = dict(menu.RACCOURCIS, **{"app.quit": ()})
    liste = p.raccourcis(eff)
    fenetre = [x for x in liste if x.portee == p.FENETRE]
    assert [x.action for x in fenetre] == list(menu.RACCOURCIS)
    assert next(x for x in fenetre if x.action == "app.quit").touches == ()
    assert all(x.action is None for x in liste if x.portee == p.ARBRE)
    assert p.raccourcis() == p.raccourcis(menu.RACCOURCIS)
    assert set(p.libelles_actions()) == set(menu.RACCOURCIS)
