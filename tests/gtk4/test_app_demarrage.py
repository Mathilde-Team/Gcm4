# SPDX-License-Identifier: GPL-3.0-or-later
"""Démarrage de gcm4 sous GTK4 : fenêtre, actions, À propos, état (#134)."""

from __future__ import annotations

import configparser

from gi.repository import Adw, Gtk

import gcm4
import gcm4.ui  # noqa: F401  (require_version Gtk 4.0 / Adw 1 avant gi.repository)
from gcm4.core import menu
from gcm4.ui.main_window import MainWindow, construire_menu


def _fenetre(app) -> MainWindow:
    fenetre = app.get_active_window()
    assert isinstance(fenetre, MainWindow)
    return fenetre


def test_fenetre_et_page_d_accueil(application):
    """L'activation présente la fenêtre principale avec la page d'accueil."""
    fenetre = _fenetre(application)
    assert fenetre.get_visible()
    assert fenetre.onglets.get_n_pages() == 1
    assert isinstance(fenetre.onglets.get_nth_page(0), Adw.StatusPage)


def test_actions_du_menu_et_raccourcis(application):
    """Chaque action du menu existe ; Ctrl+Q, Ctrl+, et F9 sont posés."""
    fenetre = _fenetre(application)
    for nom in menu.actions_du_menu():
        portee, action = nom.split(".")
        cible = application if portee == "app" else fenetre
        assert cible.lookup_action(action) is not None, nom
    for action, touches in menu.RACCOURCIS.items():
        assert application.get_accels_for_action(action) == list(touches)
    assert application.lookup_action("preferences").get_enabled()  # lot 5a, #147


def test_menu_principal():
    """Le Gio.Menu reprend les 4 sous-menus de gcm4.core.menu."""
    assert construire_menu().get_n_items() == len(menu.MENU_PRINCIPAL)


def test_f9_cache_et_montre_le_panneau(application, pomper):
    """win.toggle-panel inverse la visibilité du panneau gauche."""
    fenetre = _fenetre(application)
    assert fenetre.panneau.get_visible()
    fenetre.activate_action("win.toggle-panel", None)
    pomper()
    assert not fenetre.panneau.get_visible()
    fenetre.activate_action("win.toggle-panel", None)
    pomper()
    assert fenetre.panneau.get_visible()


def test_a_propos(application, pomper):
    """À propos : version du paquet, licence GPL-3.0, ouverture sans exception."""
    boite = application.boite_a_propos()
    assert boite.get_version() == gcm4.__version__
    assert boite.get_license_type() == Gtk.License.GPL_3_0
    application.activate_action("about", None)
    pomper()


def test_identite_de_bureau(application):
    """Nom de programme = identifiant : la fenêtre se rattache au lanceur (#155).

    Le nom de programme donne la classe de fenêtre (WM_CLASS sous X11,
    app_id sous Wayland) que le lanceur annonce par StartupWMClass ; sans
    GLib.set_prgname, c'était « python3 ». La boîte À propos reprend nom et
    icône du lanceur.
    """
    from gi.repository import GLib

    from gcm4.ui.application import APPLICATION_ID, NOM_AFFICHE

    assert GLib.get_prgname() == APPLICATION_ID
    assert GLib.get_application_name() == NOM_AFFICHE
    boite = application.boite_a_propos()
    assert boite.get_application_name() == NOM_AFFICHE
    assert boite.get_application_icon() == APPLICATION_ID


def test_icone_des_fenetres(application):
    """Les fenêtres portent l'icône du lanceur, trouvée depuis les sources (#155)."""
    from gi.repository import Gdk

    from gcm4.ui.application import APPLICATION_ID

    assert Gtk.Window.get_default_icon_name() == APPLICATION_ID
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    assert theme.has_icon(APPLICATION_ID)


def test_quitter_enregistre_l_etat_sans_perdre_le_reste(application, tmp_path, pomper):
    """Ctrl+Q enregistre la géométrie ; les autres sections de gcm4.conf restent."""
    conf = tmp_path / "gcm4.conf"
    conf.write_text("[options]\nword-separators = -A-Za-z0-9\n", encoding="utf-8")
    fenetre = _fenetre(application)
    fenetre.separateur.set_position(240)
    fenetre.activate_action("win.toggle-panel", None)
    application.activate_action("quit", None)
    pomper()
    cp = configparser.RawConfigParser()
    cp.read(conf, encoding="utf-8")
    assert cp.get("options", "word-separators") == "-A-Za-z0-9"
    assert cp.getint("window", "left-panel-width") == 240
    assert cp.getboolean("window", "show-panel") is False


def test_etat_restaure(tmp_path, pomper):
    """Taille, largeur et visibilité du panneau sont relues dans gcm4.conf."""
    import argparse

    from gi.repository import Gio

    from gcm4.ui.application import Gcm4Application

    (tmp_path / "gcm4.conf").write_text(
        "[window]\nwindow-width = 800\nwindow-height = 500\nleft-panel-width = 180\nshow-panel = False\n",
        encoding="utf-8",
    )
    app = Gcm4Application(
        argparse.Namespace(config_dir=str(tmp_path), debug=False, hotes=[]),
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )
    app.register(None)
    app.activate()
    pomper()
    fenetre = _fenetre(app)
    try:
        assert fenetre.get_default_size() == (800, 500)
        assert fenetre.separateur.get_position() == 180
        assert not fenetre.panneau.get_visible()
    finally:
        fenetre.destroy()
        pomper()
