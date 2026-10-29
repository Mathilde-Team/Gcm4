# SPDX-License-Identifier: GPL-3.0-or-later
"""Options de base de Wconfig (#247) : onglets, rayures de l'arbre, titre de la fenêtre."""

from __future__ import annotations

import configparser

from gi.repository import Gdk, Gtk

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core.comportement_onglets import NOM_APPLICATION
from gcm4.ui.main_window import MainWindow
from gcm4.ui.preferences import PreferencesWindow


def _principale(app) -> MainWindow:
    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def _ouvrir(app, pomper) -> PreferencesWindow:
    app.activate_action("preferences", None)
    pomper()
    return app.preferences


def _ini(dossier) -> configparser.RawConfigParser:
    cp = configparser.RawConfigParser(interpolation=None)
    cp.read(dossier / "gcm4.conf", encoding="utf-8")
    return cp


def _onglets(fenetre, *titres) -> list[Gtk.Widget]:
    contenus = [Gtk.Label(label=t) for t in titres]
    for rang, titre in enumerate(titres):  # pas de zip(strict=True) : Python 3.9 (#223)
        fenetre.ajouter_onglet(titre, contenus[rang])
    return contenus


def _repondre(dialogue, reponse, pomper) -> None:
    dialogue.emit("response", reponse)
    dialogue.close()  # un vrai clic ferme le dialogue ; emit() seul ne le fait pas
    pomper()


def test_valeurs_par_defaut_gtk3(app_fixture, pomper):
    """Fichier sans ces clés : valeurs par défaut de GTK3, dans le modèle et les Préférences."""
    app, _ = app_fixture
    fenetre = _principale(app)
    options = fenetre.config.options
    assert (options.confirm_close_tab, options.confirm_close_tab_middle, options.cycle_tabs) == (
        True,
        True,
        True,
    )
    assert (options.disable_hosts_stripes, options.update_title, options.app_title) == (
        False,
        False,
        "",
    )
    assert fenetre.get_title() == NOM_APPLICATION
    assert fenetre.arbre.liste.has_css_class("gcm-rayures")
    d = _ouvrir(app, pomper)
    for nom in ("confirm_close_tab", "confirm_close_tab_middle", "cycle_tabs"):
        assert d.interrupteurs[nom].get_active(), nom
    assert not d.interrupteurs["disable_hosts_stripes"].get_active()
    assert not d.interrupteurs["update_title"].get_active() and d.titre.get_text() == ""


def test_onglet_suivant_precedent_avec_et_sans_bouclage(app_fixture, pomper):
    """win.next-tab / win.previous-tab ; cycle-tabs désactivé : arrêt aux extrémités."""
    app, dossier = app_fixture
    fenetre = _principale(app)
    _onglets(fenetre, "web", "db")
    onglets = fenetre.onglets
    assert onglets.get_current_page() == 2
    fenetre.activate_action("win.next-tab", None)
    assert onglets.get_current_page() == 0  # bouclage (défaut GTK3)
    fenetre.activate_action("win.previous-tab", None)
    assert onglets.get_current_page() == 2
    fenetre.activate_action("win.previous-tab", None)
    assert onglets.get_current_page() == 1

    d = _ouvrir(app, pomper)
    d.interrupteurs["cycle_tabs"].set_active(False)
    pomper()
    assert fenetre.config.options.cycle_tabs is False
    assert _ini(dossier).getboolean("options", "cycle-tabs") is False
    onglets.set_current_page(2)
    fenetre.activate_action("win.next-tab", None)
    assert onglets.get_current_page() == 2
    onglets.set_current_page(0)
    fenetre.activate_action("win.previous-tab", None)
    assert onglets.get_current_page() == 0
    raccourcis = app.get_accels_for_action("win.next-tab")
    assert "<Control>Tab" in raccourcis and "<Control>Page_Down" in raccourcis


def test_molette_sur_les_onglets(app_fixture, pomper):
    """Molette vers le bas : suivant ; vers le haut : précédent ; horizontale : ignorée."""
    app, _ = app_fixture
    fenetre = _principale(app)
    (web,) = _onglets(fenetre, "web")
    entete = fenetre.onglets.get_tab_label(web)
    controleurs = [
        c for c in list(entete.observe_controllers()) if isinstance(c, Gtk.EventControllerScroll)
    ]
    assert len(controleurs) == 1
    assert controleurs[0].get_flags() & Gtk.EventControllerScrollFlags.DISCRETE
    accueil = fenetre.onglets.get_tab_label(fenetre.page_accueil)
    assert any(isinstance(c, Gtk.EventControllerScroll) for c in accueil.observe_controllers())
    assert fenetre.onglets.get_current_page() == 1
    assert controleurs[0].emit("scroll", 0.0, -1.0) is True
    assert fenetre.onglets.get_current_page() == 0
    assert controleurs[0].emit("scroll", 0.0, 1.0) is True
    assert fenetre.onglets.get_current_page() == 1
    assert controleurs[0].emit("scroll", 1.0, 0.0) is False
    assert fenetre.onglets.get_current_page() == 1


def test_confirmation_bouton_et_clic_milieu(app_fixture, pomper):
    """Bouton et clic du milieu : dialogue « Close console » ; une seule demande à la fois."""
    app, _ = app_fixture
    fenetre = _principale(app)
    web, db = _onglets(fenetre, "web", "db")
    bouton = fenetre.onglets.get_tab_label(web).get_last_child()
    bouton.emit("clicked")
    pomper()
    dialogue = fenetre.confirmations_onglets[web]
    assert dialogue.get_body() == "web"
    bouton.emit("clicked")  # déjà demandée : pas de second dialogue
    pomper()
    assert fenetre.confirmations_onglets == {web: dialogue}
    _repondre(dialogue, "annuler", pomper)
    assert fenetre.onglets.page_num(web) >= 0 and fenetre.confirmations_onglets == {}

    entete = fenetre.onglets.get_tab_label(db)
    gestes = [c for c in list(entete.observe_controllers()) if isinstance(c, Gtk.GestureClick)]
    assert [g.get_button() for g in gestes] == [Gdk.BUTTON_MIDDLE]
    fenetre.demander_fermeture(db, milieu=True)
    pomper()
    dialogue = fenetre.confirmations_onglets[db]
    _repondre(dialogue, "fermer", pomper)
    assert fenetre.onglets.page_num(db) == -1 and db not in fenetre.titres_onglets
    dialogue.emit("response", "fermer")  # seconde réponse : ignorée, sans erreur
    pomper()
    assert fenetre.onglets.get_n_pages() == 2


def test_fermeture_sans_confirmation(app_fixture, pomper):
    """Options désactivées dans les Préférences : fermeture directe, chacune séparément."""
    app, dossier = app_fixture
    fenetre = _principale(app)
    web, db = _onglets(fenetre, "web", "db")
    d = _ouvrir(app, pomper)
    d.interrupteurs["confirm_close_tab"].set_active(False)
    pomper()
    cp = _ini(dossier)
    assert cp.getboolean("options", "confirm-close-tab") is False
    assert not cp.has_option("options", "confirm-close-tab-middle")  # seule la clé modifiée
    fenetre.demander_fermeture(web)
    assert fenetre.onglets.page_num(web) == -1 and fenetre.confirmations_onglets == {}
    fenetre.demander_fermeture(db, milieu=True)  # option du clic du milieu toujours active
    pomper()
    assert db in fenetre.confirmations_onglets
    d.interrupteurs["confirm_close_tab_middle"].set_active(False)
    pomper()
    _repondre(fenetre.confirmations_onglets[db], "annuler", pomper)
    fenetre.demander_fermeture(db, milieu=True)
    assert fenetre.onglets.get_n_pages() == 1


def test_onglet_ferme_pendant_la_confirmation(app_fixture, pomper):
    """Onglet fermé autrement (fenêtre fermée) : le dialogue en attente est fermé avec lui."""
    app, _ = app_fixture
    fenetre = _principale(app)
    (web,) = _onglets(fenetre, "web")
    fenetre.demander_fermeture(web)
    pomper()
    assert web in fenetre.confirmations_onglets
    fenetre.fermer_onglet(web)
    pomper()
    assert fenetre.confirmations_onglets == {} and fenetre.onglets.get_n_pages() == 1


def test_titre_dynamique_et_personnalise(app_fixture, pomper):
    """update-title : « titre - onglet » ; app-title appliqué tout de suite ; vide : nom de l'app."""
    app, dossier = app_fixture
    fenetre = _principale(app)
    web, db = _onglets(fenetre, "web", "db")
    assert fenetre.get_title() == NOM_APPLICATION  # titre dynamique désactivé par défaut
    d = _ouvrir(app, pomper)
    d.interrupteurs["update_title"].set_active(True)
    pomper()
    assert _ini(dossier).getboolean("options", "update-title") is True
    assert fenetre.get_title() == f"{NOM_APPLICATION} - db"
    fenetre.onglets.set_current_page(fenetre.onglets.page_num(web))
    assert fenetre.get_title() == f"{NOM_APPLICATION} - web"
    fenetre.onglets.set_current_page(0)
    assert fenetre.get_title() == NOM_APPLICATION  # page d'accueil
    fenetre.onglets.set_current_page(1)

    d.titre.set_text("  Production  ")
    d.titre.emit("apply")
    pomper()
    assert fenetre.config.options.app_title == "Production"
    assert _ini(dossier).get("options", "app-title") == "Production"
    assert fenetre.get_title() == "Production - web"
    fenetre.enregistrer_config()  # save_config réécrit [options] depuis le modèle
    assert _ini(dossier).get("options", "app-title") == "Production"
    d.titre.set_text("")
    d.titre.emit("apply")
    pomper()
    assert _ini(dossier).get("options", "app-title") == ""
    assert fenetre.get_title() == f"{NOM_APPLICATION} - web"


def test_rayures_appliquees_tout_de_suite(app_fixture, pomper):
    """disable-hosts-stripes : classe gcm-rayures retirée puis remise, fichier à jour."""
    app, dossier = app_fixture
    fenetre = _principale(app)
    d = _ouvrir(app, pomper)
    d.interrupteurs["disable_hosts_stripes"].set_active(True)
    pomper()
    assert not fenetre.arbre.liste.has_css_class("gcm-rayures")
    assert _ini(dossier).getboolean("options", "disable-hosts-stripes") is True
    d.interrupteurs["disable_hosts_stripes"].set_active(False)
    pomper()
    assert fenetre.arbre.liste.has_css_class("gcm-rayures")


def test_titre_ecrit_par_gtk3(app_fixture, pomper):
    """app-title = nom de l'application (ce que GTK3 écrit) : champ vide, titre inchangé."""
    app, _ = app_fixture
    fenetre = _principale(app)
    fenetre.config.options.app_title = NOM_APPLICATION
    fenetre.config.options.update_title = True
    fenetre.appliquer_options()
    assert fenetre.get_title() == NOM_APPLICATION
    d = _ouvrir(app, pomper)  # première ouverture : construite avec ces valeurs
    assert d.titre.get_text() == ""
