# SPDX-License-Identifier: GPL-3.0-or-later
"""``gcm4 groupe/nom…`` : hôtes de la ligne de commande sélectionnés au démarrage (#154)."""

from __future__ import annotations

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from tests.gtk4.conftest import FIXTURES, _ouvrir


def _fenetre(app):
    from gcm4.ui.main_window import MainWindow

    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def _selection(fenetre):
    return fenetre.arbre.chemin_selectionne()


def test_demarrage_selectionne_l_hote_et_signale_les_absents(tmp_path, monkeypatch, pomper):
    """Hôte trouvé (``/`` tolérés) : sélectionné ; inconnus : un seul message ; aucun onglet."""
    from gcm4.ui.main_window import MainWindow

    messages: list[str] = []
    monkeypatch.setattr(MainWindow, "notifier", lambda _self, texte: messages.append(texte))
    app, _dossier = _ouvrir(
        tmp_path, monkeypatch, pomper, hotes=["/LAB/bases/db/", "absent/x", "PROD/zz"]
    )
    fenetre = _fenetre(app)
    assert _selection(fenetre) == "LAB/bases/db"
    assert messages == ["Host not found: absent/x, PROD/zz"]
    assert fenetre.onglets.get_n_pages() == 1  # accueil seul : pas de plugin pour proto-*
    fenetre.config.options.confirm_exit = False
    fenetre.close()
    pomper()


def test_reveler_deplie_les_groupes_replies(app_fixture, pomper):
    """Groupe parent replié : déplié, hôte sélectionné ; le dernier hôte demandé l'emporte."""
    app, _dossier = app_fixture
    fenetre = _fenetre(app)
    fenetre.arbre.ligne("LAB").set_expanded(False)
    pomper()
    assert fenetre.arbre.ligne("LAB/bases/db") is None
    fenetre.ouvrir_hotes(["PROD/eu/api", "LAB/bases/db"])
    pomper()
    assert _selection(fenetre) == "LAB/bases/db"
    assert fenetre.config.store.groups["LAB"].collapsed is False
    assert fenetre.config.store.groups["LAB/bases"].collapsed is False


def test_sans_hote_ni_configuration_rien_ne_se_passe(app_fixture, monkeypatch):
    """Liste vide, ou configuration non chargée : aucune sélection, aucun message."""
    app, _dossier = app_fixture
    fenetre = _fenetre(app)
    messages: list[str] = []
    monkeypatch.setattr(fenetre, "notifier", messages.append)
    avant = _selection(fenetre)
    fenetre.ouvrir_hotes([])
    monkeypatch.setattr(fenetre, "config", None)
    fenetre.ouvrir_hotes(["LAB/web"])
    assert _selection(fenetre) == avant
    assert messages == []


def test_plugin_present_connecte(tmp_path, monkeypatch, pomper):
    """Protocole géré par un plugin (démo) : l'hôte est aussi connecté (un onglet)."""
    from gcm4.core import registre

    app, _dossier = _ouvrir(
        tmp_path, monkeypatch, pomper, candidats=registre.candidats_dossier(FIXTURES)
    )
    fenetre = _fenetre(app)
    fenetre.config.store.find("LAB/web").protocol = "demo"
    pages = fenetre.onglets.get_n_pages()
    fenetre.ouvrir_hotes(["LAB/web"])
    pomper()
    assert _selection(fenetre) == "LAB/web"
    assert fenetre.onglets.get_n_pages() == pages + 1
    fenetre.config.options.confirm_exit = False
    fenetre.close()
    pomper()
