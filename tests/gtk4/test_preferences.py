# SPDX-License-Identifier: GPL-3.0-or-later
"""Préférences GTK4 : pages Général et Apparence, application immédiate (#147)."""

from __future__ import annotations

import configparser

from gi.repository import GLib

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import preferences as prefs_core
from gcm4.ui.main_window import MainWindow
from gcm4.ui.preferences import PreferencesWindow, css_police


def _principale(app) -> MainWindow:
    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def _ouvrir(app, pomper) -> PreferencesWindow:
    app.activate_action("preferences", None)
    pomper()
    assert isinstance(app.preferences, PreferencesWindow) and app.preferences.get_visible()
    return app.preferences


def _ini(dossier) -> configparser.RawConfigParser:
    cp = configparser.RawConfigParser(interpolation=None)
    cp.read(dossier / "gcm4.conf", encoding="utf-8")
    return cp


def test_css_police():
    """Description Pango → règle CSS de l'arbre ; vide → police du système."""
    css = css_police("Serif Bold Italic 14")
    for morceau in ('font-family: "Serif";', "font-size: 14pt;", "font-weight: 700;"):
        assert morceau in css
    assert "font-style: italic;" in css and css.startswith(".gcm-arbre {")
    assert css_police("") == "" and css_police("   ") == ""


def test_ouverture_et_pages(app_fixture, pomper):
    """Ctrl+, ouvre une seule fenêtre, valeurs du fichier, pages Général et Apparence."""
    app, _ = app_fixture
    d = _ouvrir(app, pomper)
    assert d.interrupteurs["confirm_exit"].get_active()  # confirm-exit = True (fixture)
    assert d.panneau.get_active() and d.police_systeme.get_active()
    assert not d.ligne_police.get_sensitive()
    app.activate_action("preferences", None)
    pomper()
    assert sum(isinstance(f, PreferencesWindow) for f in app.get_windows()) == 1


def test_general_applique_et_enregistre(app_fixture, pomper):
    """Interrupteur : modèle en mémoire et fichier à jour aussitôt, reste conservé."""
    app, dossier = app_fixture
    fenetre = _principale(app)
    d = _ouvrir(app, pomper)
    for nom, cle in (("confirm_exit", "confirm-exit"), ("restore_open_tabs", "restore-open-tabs")):
        avant = d.interrupteurs[nom].get_active()
        d.interrupteurs[nom].set_active(not avant)
        pomper()
        assert getattr(fenetre.config.options, nom) is (not avant)
        assert _ini(dossier).getboolean("options", cle) is (not avant)
    d.police_systeme.set_active(False)
    pomper()
    # Enregistrement ultérieur du modèle (glisser-déposer, dialogue d'hôte) :
    # les préférences ne sont pas remises à l'ancienne valeur.
    fenetre.enregistrer_config()
    cp = _ini(dossier)
    assert cp.getboolean("options", "confirm-exit") is False
    assert cp.getboolean("options", "restore-open-tabs") is True
    assert cp.get("window", "tree-font") != ""
    noms = {cp.get(sec, "name") for sec in cp.sections() if sec.startswith("host ")}
    assert {"api", "web", "db"} <= noms and cp.has_section("plugin inconnu")


def test_apparence_theme_panneau_police(app_fixture, pomper):
    """Thème via app.theme, panneau via win.toggle-panel, police appliquée en CSS."""
    app, dossier = app_fixture
    fenetre = _principale(app)
    d = _ouvrir(app, pomper)
    d.theme.set_selected(2)
    pomper()
    assert app.lookup_action("theme").get_state().get_string() == "dark"
    assert _ini(dossier).get("window", "theme-mode") == "dark"
    d.panneau.set_active(False)
    pomper()
    assert not fenetre.lookup_action("toggle-panel").get_state().get_boolean()
    assert _ini(dossier).getboolean("window", "show-panel") is False
    d.police_systeme.set_active(False)
    pomper()
    assert d.ligne_police.get_sensitive()
    from gcm4.ui.preferences import _ecrire_police

    _ecrire_police(d.police, "Serif Bold 14")
    pomper()
    assert _ini(dossier).get("window", "tree-font") == "Serif Bold 14"
    assert 'font-family: "Serif"' in app.css_police.to_string()
    d.police_systeme.set_active(True)
    pomper()
    assert _ini(dossier).get("window", "tree-font") == ""
    assert "font-family" not in app.css_police.to_string()


def test_police_relue_au_demarrage(app_fixture, pomper):
    """tree-font enregistrée : appliquée au démarrage suivant."""
    import argparse

    from gi.repository import Gio

    from gcm4.ui.application import Gcm4Application

    app, dossier = app_fixture
    prefs_core.save_preference(str(dossier / "gcm4.conf"), "tree_font", "Serif 13")
    for fenetre in list(app.get_windows()):
        fenetre.destroy()
    pomper()
    app2 = Gcm4Application(
        argparse.Namespace(config_dir=str(dossier), debug=False, hotes=[]),
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app2.register(None)
    app2.activate()
    pomper()
    try:
        assert "font-size: 13pt" in app2.css_police.to_string()
    finally:
        for fenetre in list(app2.get_windows()):
            fenetre.destroy()
        pomper()


def test_echec_d_ecriture_signale(app_fixture, pomper):
    """Fichier devenu illisible : changement appliqué, erreur signalée, fichier intact."""
    app, dossier = app_fixture
    fenetre = _principale(app)
    d = _ouvrir(app, pomper)
    (dossier / "gcm4.conf").write_text("corrompu\n= =\n", encoding="utf-8")
    d.interrupteurs["check_updates"].set_active(False)
    pomper()
    assert fenetre.config.options.check_updates is False and d.erreur
    assert (dossier / "gcm4.conf").read_text(encoding="utf-8") == "corrompu\n= =\n"


def test_confirmation_a_la_fermeture(app_demo, pomper):
    """Connexion ouverte : « Quitter quand même ? » ; Annuler garde la fenêtre."""
    app, _ = app_demo
    fenetre = _principale(app)
    fenetre.config.store.find("LAB/web").protocol = "demo"
    fenetre.activate_action("win.connect-host", GLib.Variant.new_string("LAB/web"))
    pomper()
    assert fenetre.sessions
    app.activate_action("quit", None)
    pomper()
    assert fenetre.confirmation_fermeture is not None and fenetre.get_visible()
    dialogue = fenetre.confirmation_fermeture
    dialogue.emit("response", "annuler")
    dialogue.close()  # un vrai clic ferme le dialogue ; emit() seul ne le fait pas
    pomper()
    assert fenetre.confirmation_fermeture is None and fenetre.get_visible()
    # Option désactivée : fermeture directe.
    d = _ouvrir(app, pomper)
    d.interrupteurs["confirm_exit"].set_active(False)
    d.close()
    pomper()
    assert fenetre.config.options.confirm_exit is False
    fenetre.close()
    pomper()
    assert fenetre.confirmation_fermeture is None and fenetre.sessions == {}


# --- lot 5b (#148) : Raccourcis, Journalisation, Extensions -------------------------


def test_page_raccourcis(app_fixture, pomper):
    """Chaque raccourci de la base a sa ligne, avec un libellé de touches lisible."""
    from gi.repository import Gtk

    from gcm4.ui.preferences import libelle_touches

    app, _ = app_fixture
    d = _ouvrir(app, pomper)
    assert len(d.lignes_raccourcis) == len(prefs_core.raccourcis())
    assert "Q" in libelle_touches(("<Control>q",)) and " / " in libelle_touches(("Menu", "F10"))
    assert libelle_touches(("pas-une-touche",)) == "pas-une-touche"
    assert Gtk.accelerator_parse("<Control>comma")[0]


def test_page_journalisation(app_fixture, pomper, monkeypatch):
    """Niveau enregistré et appliqué ; dossier des journaux créé puis ouvert."""
    import gcm4.ui.preferences as ui

    app, dossier = app_fixture
    monkeypatch.delenv("GCM_LOG_LEVEL", raising=False)
    monkeypatch.delenv("GCM_DEBUG", raising=False)
    appliques, ouverts = [], []
    monkeypatch.setattr(
        ui.logging_config, "configure_logging", lambda n=None, **kw: appliques.append(n)
    )
    monkeypatch.setattr(ui, "lancer_dossier", lambda _p, chemin: ouverts.append(chemin))
    d = _ouvrir(app, pomper)
    assert d.source_journal == "preferences" and not d.niveau.get_subtitle()
    d.niveau.set_selected(prefs_core.NIVEAUX_JOURNAL.index("WARNING"))
    pomper()
    assert appliques == ["WARNING"] and _ini(dossier).get("options", "log-level") == "WARNING"
    d.bouton_journaux.emit("clicked")
    assert ouverts == [d.dossier_journaux] and (dossier / "log").is_dir()

    def echoue(*_a):
        raise OSError("pas de gestionnaire de fichiers")

    monkeypatch.setattr(ui, "lancer_dossier", echoue)
    d.bouton_journaux.emit("clicked")
    assert "gestionnaire" in d.erreur


def test_journalisation_imposee_par_debug(app_fixture, pomper, monkeypatch):
    """--debug : niveau enregistré mais pas appliqué, sous-titre explicatif."""
    import gcm4.ui.preferences as ui

    app, dossier = app_fixture
    appliques = []
    monkeypatch.setattr(
        ui.logging_config, "configure_logging", lambda n=None, **kw: appliques.append(n)
    )
    app.args.debug = True
    d = _ouvrir(app, pomper)
    assert d.source_journal == "cli" and d.niveau.get_subtitle()
    d.niveau.set_selected(prefs_core.NIVEAUX_JOURNAL.index("ERROR"))
    pomper()
    assert appliques == [] and _ini(dossier).get("options", "log-level") == "ERROR"


def test_page_extensions(app_fixture, pomper):
    """Chargé ou en erreur avec sa raison ; page du plugin ajoutée ; exception tolérée."""
    from gi.repository import Adw, Gtk

    from gcm4.core.registre import EtatPlugin, PluginRegistry

    class AvecPage:
        def build_preferences_page(self):
            return Gtk.Label(label="réglages")

    class AvecPageAdw:
        def build_preferences_page(self):
            return Adw.PreferencesPage()

    class Casse:
        def build_preferences_page(self):
            raise RuntimeError("boum")

    app, _ = app_fixture
    app.registre = PluginRegistry(
        [
            EtatPlugin("ok", "paquet", "1.2", ("ok",), AvecPage()),
            EtatPlugin("adw", "paquet", "", ("adw",), AvecPageAdw()),
            EtatPlugin("casse", "dossier", "0.1", ("casse",), Casse()),
            EtatPlugin("refuse", "dossier", erreur="contrat v2 attendu <b>"),
        ]
    )
    d = _ouvrir(app, pomper)
    lignes = d.lignes_extensions
    assert "1.2" in lignes["ok:paquet"].get_subtitle()
    assert "contrat v2 attendu <b>" in lignes["refuse:dossier"].get_subtitle()
    assert "boum" in lignes["casse:dossier"].get_subtitle()
    assert [p.get_title() for p in d.pages_plugins] == ["adw", "ok"]


def test_extensions_sans_plugin(app_fixture, pomper):
    """Mode zéro plugin : message, aucune ligne."""
    app, _ = app_fixture
    d = _ouvrir(app, pomper)
    assert d.lignes_extensions == {} and d.pages_plugins == []
