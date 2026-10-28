# SPDX-License-Identifier: GPL-3.0-or-later
"""Vérification des mises à jour au démarrage : accord, fil, toast (#245)."""

from __future__ import annotations

import configparser

import gcm4
import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import mises_a_jour as maj
from gcm4.ui import mises_a_jour as ui


def _fenetre(app):
    from gcm4.ui.main_window import MainWindow

    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def _ini(dossier):
    cp = configparser.RawConfigParser(interpolation=None)
    cp.read(dossier / "gcm4.conf", encoding="utf-8")
    return cp


#: Fonctions d'origine (``_demarrer`` peut être appelé plusieurs fois par test).
LANCER, DEMANDER = ui.lancer_verification, ui.demander_accord

NOUVELLE = maj.MiseAJour("v9.0.0", "https://github.com/Mathilde-Team/Gcm4/releases/tag/v9.0.0")


def _repondre(fenetre, reponse, pomper):
    """Réponse au dialogue d'accord (close() émettrait en plus la réponse de fermeture)."""
    fenetre._dialogue_accord.emit("response", reponse)
    pomper()


def _demarrer(fenetre, verification, monkeypatch):
    """au_demarrage sans GCM_NO_UPDATE_CHECK ; garde le dialogue et le fil."""
    fils = []
    vrai_lancer, vrai_demander = LANCER, DEMANDER
    monkeypatch.setattr(
        ui, "lancer_verification", lambda f, v=verification: fils.append(vrai_lancer(f, v))
    )

    def demander(f, v=verification):
        f._dialogue_accord = vrai_demander(f, v)
        return f._dialogue_accord

    monkeypatch.setattr(ui, "demander_accord", demander)
    choix = ui.au_demarrage(fenetre, environ={}, verification=verification)
    return choix, fils


def test_accord_demande_puis_autorise(app_fixture, pomper, monkeypatch):
    """Jamais demandé : question ; « Autoriser » → accord enregistré, vérification, toast."""
    app, dossier = app_fixture
    fenetre = _fenetre(app)
    appels, ouvertures = [], []
    monkeypatch.setattr(ui, "ouvrir_page", lambda _f, url: ouvertures.append(url))
    choix, fils = _demarrer(fenetre, lambda v: appels.append(v) or NOUVELLE, monkeypatch)
    assert choix == maj.DEMANDER and appels == [] and fils == []  # rien avant l'accord
    _repondre(fenetre, "autoriser", pomper)
    fils[0].join(5)
    pomper()
    assert _ini(dossier).get("window", maj.CLE_ACCORD) == "True"
    assert appels == [gcm4.__version__]
    fenetre.toast_mise_a_jour.emit("button-clicked")
    assert ouvertures == [NOUVELLE.url]
    # Démarrage suivant : vérification directe, sans question.
    choix, fils = _demarrer(fenetre, lambda _v: None, monkeypatch)
    assert choix == maj.VERIFIER and len(fils) == 1


def test_accord_refuse(app_fixture, pomper, monkeypatch):
    """« Non » : option désactivée (fichier et modèle), plus jamais de question."""
    app, dossier = app_fixture
    fenetre = _fenetre(app)
    appels = []
    choix, fils = _demarrer(fenetre, lambda v: appels.append(v), monkeypatch)
    _repondre(fenetre, "non", pomper)
    cp = _ini(dossier)
    assert cp.get("window", maj.CLE_ACCORD) == "False"
    assert cp.getboolean("options", "check-updates") is False
    assert fenetre.config.options.check_updates is False and appels == [] and fils == []
    assert _demarrer(fenetre, lambda v: appels.append(v), monkeypatch)[0] == maj.NON


def test_hors_ligne_ou_erreur_rien_a_l_ecran(app_fixture, pomper, monkeypatch):
    """Hors ligne ou exception de la vérification : aucun toast, aucune exception."""
    from gcm4.ui.mises_a_jour import enregistrer_accord

    app, dossier = app_fixture
    fenetre = _fenetre(app)
    enregistrer_accord(str(dossier / "gcm4.conf"), True)

    def casse(_v):
        raise RuntimeError("boum")

    for verification in (lambda _v: None, casse):
        _choix, fils = _demarrer(fenetre, verification, monkeypatch)
        fils[0].join(5)
        pomper()
        assert not hasattr(fenetre, "toast_mise_a_jour")


def test_variable_d_environnement_et_config_illisible(app_fixture, pomper):
    """GCM_NO_UPDATE_CHECK=1 (posée pour tous les tests) : rien ; sans config : rien."""
    app, _ = app_fixture
    fenetre = _fenetre(app)
    assert ui.au_demarrage(fenetre) == maj.NON
    fenetre.config = None
    assert ui.au_demarrage(fenetre, environ={}) == maj.NON


def test_preferences_activer_vaut_accord(app_fixture, pomper):
    """Préférences : réactiver « Vérifier les mises à jour » enregistre l'accord."""
    app, dossier = app_fixture
    app.activate_action("preferences", None)
    pomper()
    interrupteur = app.preferences.interrupteurs["check_updates"]
    interrupteur.set_active(False)
    pomper()
    assert not _ini(dossier).has_option("window", maj.CLE_ACCORD)
    interrupteur.set_active(True)
    pomper()
    assert _ini(dossier).get("window", maj.CLE_ACCORD) == "True"
