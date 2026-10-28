# SPDX-License-Identifier: GPL-3.0-or-later
"""Restauration des onglets ouverts au démarrage (#246), avec le plugin démo."""

from __future__ import annotations

import argparse
import configparser

from gi.repository import Gio, GLib

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import onglets


def _fenetre(app):
    from gcm4.ui.main_window import MainWindow

    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def _preparer(fenetre, chemins):
    """Hôtes ``chemins`` en protocole démo (enregistré), confirmation désactivée."""
    for chemin in chemins:
        fenetre.config.store.find(chemin).protocol = "demo"
    fenetre.config.options.confirm_exit = False
    fenetre.enregistrer_config()


def _connecter(fenetre, chemin, pomper):
    fenetre.activate_action("win.connect-host", GLib.Variant.new_string(chemin))
    pomper()


def _relancer(dossier, pomper):
    from gcm4.core import registre
    from gcm4.ui.application import Gcm4Application
    from tests.gtk4.conftest import FIXTURES

    app = Gcm4Application(
        argparse.Namespace(config_dir=str(dossier), debug=False, hotes=[]),
        flags=Gio.ApplicationFlags.NON_UNIQUE,
        candidats=registre.candidats_dossier(FIXTURES),
    )
    app.register(None)
    app.activate()
    pomper()
    return app


def _ini(dossier):
    cp = configparser.RawConfigParser(interpolation=None)
    cp.read(dossier / "gcm4.conf", encoding="utf-8")
    return cp


def test_enregistre_dans_l_ordre_affiche_meme_option_inactive(app_demo, pomper):
    """Fermeture : open-tabs dans l'ordre des onglets (réordonnés), option désactivée."""
    app, dossier = app_demo
    fenetre = _fenetre(app)
    _preparer(fenetre, ["LAB/web", "PROD/eu/api"])
    assert fenetre.config.options.restore_open_tabs is False
    _connecter(fenetre, "LAB/web", pomper)
    _connecter(fenetre, "PROD/eu/api", pomper)
    fenetre.onglets.reorder_child(fenetre.onglets.get_nth_page(2), 1)  # api avant web
    assert fenetre.onglets_ouverts() == ["PROD/eu/api", "LAB/web"]
    fenetre.close()
    pomper()
    assert _ini(dossier).get("window", onglets.CLE) == "PROD/eu/api,LAB/web"


def test_restaure_au_demarrage(app_demo, pomper):
    """Option active : deux onglets fermés puis rouverts, même ordre."""
    app, dossier = app_demo
    fenetre = _fenetre(app)
    _preparer(fenetre, ["LAB/web", "PROD/eu/api"])
    fenetre.config.options.restore_open_tabs = True
    fenetre.enregistrer_config()
    _connecter(fenetre, "LAB/web", pomper)
    _connecter(fenetre, "PROD/eu/api", pomper)
    fenetre.close()
    pomper()
    app2 = _relancer(dossier, pomper)
    try:
        f2 = _fenetre(app2)
        assert f2.onglets_ouverts() == ["LAB/web", "PROD/eu/api"]
        assert f2.onglets.get_n_pages() == 3  # accueil + 2
        app2.activate()  # réactivation : pas de seconde restauration
        pomper()
        assert f2.onglets.get_n_pages() == 3
    finally:
        for f in list(app2.get_windows()):
            f.destroy()
        pomper()


def test_hote_disparu_ou_sans_plugin(app_demo, pomper):
    """Hôte disparu, protocole sans plugin : un message chacun, les autres s'ouvrent."""
    from gcm4.core.config import update_keys

    app, dossier = app_demo
    fenetre = _fenetre(app)
    _preparer(fenetre, ["LAB/web"])
    fenetre.config.options.restore_open_tabs = True
    update_keys(
        str(dossier / "gcm4.conf"), "window", {onglets.CLE: "LAB/disparu,LAB/web,LAB/bases/db"}
    )
    messages = []
    fenetre.notifier = messages.append
    fenetre.restaurer_onglets()
    pomper()
    assert fenetre.onglets_ouverts() == ["LAB/web"]
    assert len(messages) == 2
    assert "LAB/disparu" in messages[0] and "LAB/bases/db" in messages[1]


def test_option_inactive_rien_n_est_rouvert(app_demo, pomper):
    """Option désactivée : liste conservée mais aucun onglet rouvert."""
    from gcm4.core.config import update_keys

    app, dossier = app_demo
    fenetre = _fenetre(app)
    _preparer(fenetre, ["LAB/web"])
    update_keys(str(dossier / "gcm4.conf"), "window", {onglets.CLE: "LAB/web"})
    fenetre.restaurer_onglets()
    pomper()
    assert fenetre.onglets_ouverts() == []
