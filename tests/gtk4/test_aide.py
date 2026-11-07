# SPDX-License-Identifier: GPL-3.0-or-later
"""Aide contextuelle (#86) : guide (F1), raccourcis (Ctrl+?), info-bulles, sous-titres."""

from __future__ import annotations

import types

from gi.repository import Adw, Gtk

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import menu
from gcm4.core import preferences as prefs_core
from gcm4.ui import aide, mises_a_jour
from gcm4.ui.preferences import OPTIONS_FENETRE, OPTIONS_GENERALES


def test_f1_ouvre_le_guide(application, pomper, monkeypatch):
    """Aide > Guide utilisateur : la page du guide, avec la fenêtre active pour parent."""
    ouvertes = []
    monkeypatch.setattr(mises_a_jour, "ouvrir_page", lambda f, url: ouvertes.append((f, url)))
    assert application.get_accels_for_action("app.help") == ["F1"]
    application.activate_action("help", None)
    pomper()
    assert ouvertes == [(application.get_active_window(), menu.GUIDE_UTILISATEUR)]


def test_sections_par_defaut():
    """Fenêtre principale puis Hôtes ; libellés traduits, accélérateurs séparés par une espace."""
    contenu = aide.sections()
    assert [titre for titre, _l in contenu] == ["Main window", "Hosts"]
    fenetre = dict(contenu[0][1])
    assert fenetre["User Guide"] == "F1"
    assert fenetre["Keyboard Shortcuts"] == "<Control>question"
    assert fenetre["Next tab"] == "<Control>Page_Down <Control>Tab"
    assert dict(contenu[1][1])["Open the context menu"] == "Menu <Shift>F10"
    total = sum(len(lignes) for _t, lignes in contenu)
    assert total == len(prefs_core.raccourcis())


def test_raccourcis_desactives_omis():
    """Un raccourci désactivé n'est pas listé ; une section vide disparaît."""
    effectifs = dict(menu.RACCOURCIS, **{"app.quit": ()})
    libelles = [libelle for libelle, _t in aide.sections(effectifs)[0][1]]
    assert "Quit" not in libelles and "Settings" in libelles
    tous_desactives = dict.fromkeys(menu.RACCOURCIS, ())
    assert [titre for titre, _l in aide.sections(tous_desactives)] == ["Hosts"]


def test_ctrl_point_d_interrogation(application, pomper):
    """Aide > Raccourcis clavier : Adw.ShortcutsDialog (libadwaita ≥ 1.8) présenté."""
    assert application.get_accels_for_action("app.shortcuts") == ["<Control>question"]
    application.activate_action("shortcuts", None)
    pomper()
    dialogue = application.fenetre_raccourcis
    attendu = Adw.ShortcutsDialog if hasattr(Adw, "ShortcutsDialog") else Gtk.ShortcutsWindow
    assert isinstance(dialogue, attendu)
    dialogue.close()
    pomper()


def test_repli_gtk_shortcuts_window(application, pomper, monkeypatch):
    """Sans Adw.ShortcutsDialog : Gtk.ShortcutsWindow modale, rattachée à la fenêtre."""
    monkeypatch.setattr(aide, "Adw", types.SimpleNamespace())
    parent = application.get_active_window()
    fenetre = aide.presenter_raccourcis(parent)
    pomper()
    assert isinstance(fenetre, Gtk.ShortcutsWindow)
    assert fenetre.get_transient_for() is parent and fenetre.get_modal()
    fenetre.destroy()
    pomper()


def test_xml_echappe_les_textes():
    """Les libellés traduits sont échappés dans la description GtkBuilder."""
    xml = aide._xml_fenetre([("A & B", [("x < y", "<Control>q")])])
    assert "A &amp; B" in xml and "x &lt; y" in xml and "&lt;Control&gt;q" in xml
    fenetre = Gtk.Builder.new_from_string(xml, -1).get_object("fenetre")
    assert isinstance(fenetre, Gtk.ShortcutsWindow)
    fenetre.destroy()


def test_menu_aide_construit(application):
    """Le sous-menu Aide du Gio.Menu a deux sections : aide, puis À propos."""
    from gcm4.ui.main_window import construire_menu

    modele = construire_menu()
    aide_menu = modele.get_item_link(len(menu.MENU_PRINCIPAL) - 1, "submenu")
    assert aide_menu.get_n_items() == 2


def _dialogue_hote(app_fixture, pomper):
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    arbre.menu.cible = None
    arbre.menu.executer("add")
    pomper()
    return arbre.menu.dialogue_hote


def test_info_bulles_du_dialogue_d_hote(app_fixture, pomper):
    """Chaque champ a son info-bulle ; une erreur la remplace, sa correction la rend."""
    d = _dialogue_hote(app_fixture, pomper)
    for ligne in (
        d.nom,
        d.groupe,
        d.adresse,
        d.port,
        d.utilisateur,
        d.mot_de_passe,
        d.protocole,
        d.description,
        d.fermeture,
        d.avant,
        d.apres,
        d.parametres,
    ):
        assert ligne.get_tooltip_text(), ligne.get_title()
    aide_port = d.port.get_tooltip_text()
    assert aide_port == "Leave blank to use the default port of the protocol"
    d.port.set_text("abc")
    assert d.port.get_tooltip_text() != aide_port and d.port.has_css_class("error")
    d.port.set_text("")
    assert d.port.get_tooltip_text() == aide_port
    d.nom.set_text("libre")
    assert d.nom.get_tooltip_text() == "Name shown in the host list, unique within its group"
    ligne_couleurs = d.couleurs_par_defaut.get_ancestor(Adw.ActionRow)
    assert ligne_couleurs.get_subtitle()
    d.enregistre = True
    d.close()
    pomper()


def test_sous_titres_des_parametres(app_fixture, pomper):
    """Chaque option des pages Général et Apparence a un sous-titre explicatif."""
    app, _ = app_fixture
    app.activate_action("preferences", None)
    pomper()
    d = app.preferences
    for nom, _libelle, sous_titre in OPTIONS_GENERALES + OPTIONS_FENETRE:
        ligne = d.interrupteurs[nom].get_ancestor(Adw.ActionRow)
        assert ligne.get_subtitle() == sous_titre, nom
    for widget in (d.panneau, d.police_systeme):
        assert widget.get_ancestor(Adw.ActionRow).get_subtitle()
    assert d.theme.get_subtitle() and d.ligne_police.get_subtitle()
    assert d.titre.get_tooltip_text()
    d.close()
    pomper()
