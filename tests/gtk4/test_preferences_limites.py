# SPDX-License-Identifier: GPL-3.0-or-later
"""Préférences GTK4 : replis système et chemins d'erreur du code actif (#293)."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gi.repository import Gio, GLib, Gtk, Pango

import gcm4.ui.preferences as ui
from gcm4.core.config import ConfigError


def test_etat_action_absente_ou_sans_etat():
    """Un objet sans actions, une action absente ou sans état renvoient None."""
    assert ui.PreferencesWindow._etat_action(object(), "absente") is None
    groupe = Gio.SimpleActionGroup()
    assert ui.PreferencesWindow._etat_action(groupe, "absente") is None
    groupe.add_action(Gio.SimpleAction.new("simple", None))
    assert ui.PreferencesWindow._etat_action(groupe, "simple") is None
    groupe.add_action(Gio.SimpleAction.new_stateful("etat", None, GLib.Variant("s", "clair")))
    assert ui.PreferencesWindow._etat_action(groupe, "etat").get_string() == "clair"


def test_police_repli_gtk_48(monkeypatch):
    """Le vrai FontButton ancien conserve la police et son alignement."""
    ancien_gtk = SimpleNamespace(FontButton=Gtk.FontButton, Align=Gtk.Align)
    monkeypatch.setattr(ui, "Gtk", ancien_gtk)
    bouton = ui._bouton_police()
    assert isinstance(bouton, Gtk.FontButton)
    assert bouton.get_valign() == Gtk.Align.CENTER
    ui._ecrire_police(bouton, "Serif Bold 15")
    assert ui._lire_police(bouton) == "Serif Bold 15"


@pytest.mark.parametrize("valeur", [None, ""])
def test_police_ancienne_vide(valeur):
    """Une sélection ancienne absente est normalisée en chaîne vide."""
    assert ui._lire_police(SimpleNamespace(get_font=lambda: valeur)) == ""


def test_police_moderne_sans_description():
    """Un FontDialogButton sans description n'invente pas de police."""
    assert ui._lire_police(SimpleNamespace(get_font_desc=lambda: None)) == ""


def test_police_ancienne_interface_texte():
    """Le repli get_font/set_font transmet la description sans conversion Pango."""
    ecrire = Mock()
    bouton = SimpleNamespace(get_font=lambda: "Serif 12", set_font=ecrire)
    assert ui._lire_police(bouton) == "Serif 12"
    ui._ecrire_police(bouton, "Sans 13")
    ecrire.assert_called_once_with("Sans 13")


def test_css_police_pixels_sans_famille(monkeypatch):
    """Les tailles absolues sont en pixels et aucune famille vide n'est émise."""
    description = Pango.FontDescription()
    description.set_absolute_size(17 * Pango.SCALE)
    description.set_style(Pango.Style.OBLIQUE)
    monkeypatch.setattr(
        ui,
        "Pango",
        SimpleNamespace(
            FontDescription=SimpleNamespace(from_string=lambda _texte: description),
            SCALE=Pango.SCALE,
            Style=Pango.Style,
        ),
    )
    css = ui.css_police("description contrôlée")
    assert "font-size: 17px;" in css
    assert "font-style: oblique;" in css
    assert "font-family" not in css


@pytest.mark.parametrize("echec", [False, True])
def test_lancer_dossier_rappel_moderne(tmp_path, monkeypatch, echec):
    """Le rappel asynchrone termine le lancement ou journalise son échec."""
    lancement = Mock()
    lanceur = SimpleNamespace(launch=lancement)
    constructeur = Mock(return_value=lanceur)
    monkeypatch.setattr(ui, "Gtk", SimpleNamespace(FileLauncher=SimpleNamespace(new=constructeur)))
    ui.lancer_dossier(None, str(tmp_path))
    assert constructeur.call_args.args[0].get_path() == str(tmp_path)
    parent, annulation, rappel = lancement.call_args.args
    assert parent is None and annulation is None
    fin = Mock(side_effect=GLib.Error("ouverture impossible") if echec else None)
    journal = Mock()
    monkeypatch.setattr(ui, "logger", journal)
    resultat = object()
    rappel(SimpleNamespace(launch_finish=fin), resultat)
    fin.assert_called_once_with(resultat)
    assert journal.warning.call_count == int(echec)


@pytest.mark.parametrize("echec", [False, True])
def test_lancer_dossier_repli_ancien(tmp_path, monkeypatch, echec):
    """GTK ancien utilise une URI et propage GLib.Error à l'appelant."""
    lancement = Mock(side_effect=GLib.Error("refus système") if echec else None)
    monkeypatch.setattr(ui, "Gtk", SimpleNamespace())
    monkeypatch.setattr(
        ui,
        "Gio",
        SimpleNamespace(File=Gio.File, AppInfo=SimpleNamespace(launch_default_for_uri=lancement)),
    )
    if echec:
        with pytest.raises(GLib.Error, match="refus système"):
            ui.lancer_dossier(None, str(tmp_path))
    else:
        ui.lancer_dossier(None, str(tmp_path))
    lancement.assert_called_once_with(tmp_path.as_uri(), None)


def test_preferences_sans_fenetre_chargee(app_fixture, pomper):
    """Sans modèle chargé, les options sont inactives et n'écrivent rien."""
    app, dossier = app_fixture
    avant = (dossier / "gcm4.conf").read_bytes()
    preferences = ui.PreferencesWindow(app, None)
    try:
        assert not preferences.interrupteurs["confirm_exit"].is_sensitive()
        assert not preferences.titre.is_sensitive()
        preferences._on_option(Gtk.Switch(active=True), None, "confirm_exit")
        preferences._on_titre(preferences.titre)
        assert (dossier / "gcm4.conf").read_bytes() == avant
        assert preferences._raccourcis_effectifs()
    finally:
        preferences.destroy()
        pomper()


def test_selections_invalides_sans_effet(app_fixture, pomper):
    """Une ComboRow sans sélection ne modifie ni thème ni niveau ni fichier."""
    app, dossier = app_fixture
    app.activate_action("preferences", None)
    pomper()
    preferences = app.preferences
    avant = (dossier / "gcm4.conf").read_bytes()
    invalide = SimpleNamespace(get_selected=lambda: Gtk.INVALID_LIST_POSITION)
    preferences._on_theme(invalide, None)
    preferences._on_niveau(invalide, None)
    assert (dossier / "gcm4.conf").read_bytes() == avant


def test_raccourci_ecriture_impossible(app_fixture, pomper, monkeypatch):
    """Une erreur de stockage est affichée sans prétendre appliquer le raccourci."""
    app, dossier = app_fixture
    app.activate_action("preferences", None)
    pomper()
    preferences = app.preferences
    avant = (dossier / "gcm4.conf").read_bytes()
    enregistrer = Mock(side_effect=ConfigError("stockage indisponible"))
    monkeypatch.setattr(ui.raccourcis_core, "enregistrer", enregistrer)
    appliquer = Mock()
    monkeypatch.setattr(app, "appliquer_raccourcis", appliquer)
    preferences._enregistrer_raccourci("win.next-tab", "set", None)
    assert preferences.erreur == "stockage indisponible"
    appliquer.assert_not_called()
    assert (dossier / "gcm4.conf").read_bytes() == avant


def test_preferences_application_minimale(tmp_path, pomper):
    """Une application sans helpers optionnels conserve les replis documentés."""
    application = Gtk.Application(application_id="org.example.Gcm4PreferencesTests")
    application.register(None)
    application.config_file = str(tmp_path / "gcm4.conf")
    preferences = ui.PreferencesWindow(application, None)
    try:
        assert preferences._raccourcis_effectifs()
        preferences._suspendre_raccourcis(True)
        preferences._suspendre_raccourcis(False)
        preferences._appliquer()
        preferences._on_panneau(Gtk.Switch(active=False), None)
        preferences._on_police()
        assert preferences.valeurs.show_panel is False
        assert preferences.valeurs.tree_font == ""
    finally:
        preferences.destroy()
        pomper()


def test_page_plugin_adw_titre_conserve(app_fixture, pomper):
    """Une page de plugin déjà titrée n'est pas renommée par le conteneur."""
    from gi.repository import Adw

    app, _ = app_fixture
    app.activate_action("preferences", None)
    pomper()
    page = Adw.PreferencesPage(title="Réglages du plugin")
    plugin = SimpleNamespace(build_preferences_page=lambda: page)
    assert app.preferences._page_du_plugin("nom-technique", plugin) == []
    assert app.preferences.pages_plugins[-1] is page
    assert page.get_title() == "Réglages du plugin"
