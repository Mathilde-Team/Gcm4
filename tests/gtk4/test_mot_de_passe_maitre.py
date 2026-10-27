"""Mot de passe maître GTK4 : verrou au démarrage, recouvrement, préférences (#98)."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from gi.repository import Gio

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import chemins
from gcm4.core import config as config_mod
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.ui.application import Gcm4Application
from gcm4.ui.main_window import MainWindow
from gcm4.ui.mot_de_passe_maitre import (
    ANNULER,
    VALIDER,
    GroupeMotDePasseMaitre,
    PageVerrou,
    verifier_nouveau,
)
from gcm4.ui.preferences import PreferencesWindow

FIXTURE_AES = Path(__file__).resolve().parents[1] / "data" / "config" / "aes"
U = "utilisateur"


def _dossier(tmp_path, monkeypatch):
    """Copie de la fixture AES migrée vers gcm4.conf / .gcm4.key."""
    monkeypatch.setenv("USER", U)
    monkeypatch.setattr(config_mod, "_sauvegardes", set())
    dossier = tmp_path / "gcm"
    shutil.copytree(FIXTURE_AES, dossier)
    chemins.preparer(str(dossier))
    return dossier


def _lancer(dossier, pomper):
    """Application ouverte sur ``dossier`` sans plugin ; renvoie (app, fenêtre)."""
    app = Gcm4Application(
        argparse.Namespace(config_dir=str(dossier), debug=False, hotes=[]),
        flags=Gio.ApplicationFlags.NON_UNIQUE,
        candidats=[],
    )
    app.register(None)
    app.activate()
    pomper()
    fenetre = next(f for f in app.get_windows() if isinstance(f, MainWindow))
    return app, fenetre


def _fermer(app, pomper):
    """Détruit les fenêtres de ``app``."""
    for fenetre in list(app.get_windows()):
        fenetre.destroy()
    pomper()


def test_verifier_nouveau():
    """Vide ou confirmation différente : message ; sinon None."""
    assert "empty" in verifier_nouveau("", "")
    assert "match" in verifier_nouveau("a", "b")
    assert verifier_nouveau("a", "a") is None


def test_sans_protection_aucun_verrou(tmp_path, monkeypatch, pomper):
    """Comportement inchangé sans mot de passe maître : arbre chargé directement."""
    app, fenetre = _lancer(_dossier(tmp_path, monkeypatch), pomper)
    assert fenetre.verrou is None and fenetre.arbre is not None
    _fermer(app, pomper)


def test_verrou_puis_deverrouillage(tmp_path, monkeypatch, pomper):
    """Clé protégée : page de verrou, rien de chargé ; mot de passe faux refusé ; bon : arbre."""
    dossier = _dossier(tmp_path, monkeypatch)
    ChiffrementGcm(str(dossier), U).activer_protection("maître")
    app, fenetre = _lancer(dossier, pomper)
    assert isinstance(fenetre.verrou, PageVerrou)
    assert fenetre.config is None and fenetre.arbre is None
    assert not fenetre.lookup_action("search").get_enabled()
    verrou = fenetre.verrou
    assert verrou.oubli.get_visible()
    verrou.mot_de_passe.set_text("faux")
    verrou.bouton.emit("clicked")
    assert verrou.message.get_visible() and "Incorrect" in verrou.message.get_label()
    assert verrou.mot_de_passe.get_text() == "" and fenetre.config is None
    verrou.mot_de_passe.set_text("maître")
    verrou.mot_de_passe.emit("entry-activated")
    pomper()
    assert fenetre.verrou is None and fenetre.arbre is not None
    assert fenetre.lookup_action("search").get_enabled()
    assert fenetre.config.store.find("PROD/eu/api").password == "Mot2Passe!é"
    _fermer(app, pomper)


def test_mot_de_passe_oublie(tmp_path, monkeypatch, pomper):
    """Code de recouvrement : nouveau mot de passe imposé, nouveau code affiché, puis arbre."""
    dossier = _dossier(tmp_path, monkeypatch)
    code = ChiffrementGcm(str(dossier), U).activer_protection("oublié")
    app, fenetre = _lancer(dossier, pomper)
    verrou = fenetre.verrou
    verrou.oubli.emit("clicked")
    pomper()
    verrou.dialogue.emit("response", ANNULER)
    assert fenetre.config is None
    # Confirmation différente, puis code faux : refus, toujours verrouillée.
    verrou.oubli.emit("clicked")
    verrou.code.set_text(code)
    verrou.nouveau.set_text("neuf")
    verrou.confirmation.set_text("autre")
    verrou.dialogue.emit("response", VALIDER)
    assert "match" in verrou.message.get_label() and fenetre.config is None
    verrou.oubli.emit("clicked")
    verrou.code.set_text("AAAA-AAAA")
    verrou.nouveau.set_text("neuf")
    verrou.confirmation.set_text("neuf")
    verrou.dialogue.emit("response", VALIDER)
    assert "recovery code" in verrou.message.get_label() and fenetre.config is None
    verrou.oubli.emit("clicked")
    verrou.code.set_text(code.lower())
    verrou.nouveau.set_text("neuf")
    verrou.confirmation.set_text("neuf")
    verrou.dialogue.emit("response", VALIDER)
    pomper()
    assert verrou.dialogue.get_heading() == "Recovery code"
    assert fenetre.config is None  # chargée seulement après fermeture du code
    verrou.dialogue.emit("response", VALIDER)
    pomper()
    assert fenetre.arbre is not None and fenetre.verrou is None
    relu = ChiffrementGcm(str(dossier), U)
    relu.deverrouiller("neuf")
    _fermer(app, pomper)


def test_preferences_cycle_complet(tmp_path, monkeypatch, pomper):
    """Paramètres > Sécurité : activer, changer, nouveau code, désactiver ; erreurs signalées."""
    dossier = _dossier(tmp_path, monkeypatch)
    cle = (dossier / ".gcm4.key").read_text()
    app, fenetre = _lancer(dossier, pomper)
    notes: list[str] = []
    monkeypatch.setattr(fenetre, "notifier", notes.append)
    app.activate_action("preferences", None)
    pomper()
    groupe = app.preferences.mot_de_passe_maitre
    assert isinstance(groupe, GroupeMotDePasseMaitre)
    assert groupe.etat.get_subtitle() == "Disabled"
    assert groupe.boutons["activer"].get_visible() and not groupe.boutons["changer"].get_visible()

    def operation(nom, actuel="", nouveau="", confirmation=None, reponse=VALIDER):
        groupe.boutons[nom].emit("clicked")
        groupe.actuel.set_text(actuel)
        groupe.nouveau.set_text(nouveau)
        groupe.confirmation.set_text(nouveau if confirmation is None else confirmation)
        groupe.dialogue.emit("response", reponse)
        pomper()

    operation("activer", nouveau="a", reponse=ANNULER)
    assert not fenetre.chiffrement.est_protegee()
    operation("activer", nouveau="a", confirmation="b")
    assert groupe.message.get_visible() and not fenetre.chiffrement.est_protegee()
    operation("activer", nouveau="premier")
    assert fenetre.chiffrement.est_protegee() and not groupe.message.get_visible()
    assert groupe.etat.get_subtitle() == "Enabled, with a recovery code"
    assert notes[-1] == "Master password enabled."
    assert groupe.dialogue.get_heading() == "Recovery code"
    groupe.dialogue.emit("response", VALIDER)
    assert groupe.boutons["changer"].get_visible() and not groupe.boutons["activer"].get_visible()

    operation("changer", actuel="faux", nouveau="second")
    assert "Incorrect master password" in groupe.message.get_label()
    operation("changer", actuel="premier", nouveau="second")
    assert notes[-1] == "Master password changed."
    operation("code", actuel="second")
    assert notes[-1] == "New recovery code created."
    assert groupe.dialogue.get_heading() == "Recovery code"
    groupe.dialogue.emit("response", VALIDER)
    operation("desactiver", actuel="second")
    assert notes[-1] == "Master password disabled."
    assert (dossier / ".gcm4.key").read_text() == cle
    assert groupe.etat.get_subtitle() == "Disabled"
    # Les mots de passe des hôtes restent lisibles après tout le cycle.
    fenetre.enregistrer_config()
    relu = config_mod.load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier), U))
    assert relu.store.find("PROD/eu/api").password == "Mot2Passe!é"
    _fermer(app, pomper)


def test_groupe_indisponible_et_v1(tmp_path, monkeypatch, pomper):
    """Configuration non chargée : groupe inactif ; clé v1 : « sans code », pas de lien oubli."""
    import master_password_core as mpc

    groupe = GroupeMotDePasseMaitre(None)
    assert not groupe.get_sensitive() and "not loaded" in groupe.etat.get_subtitle()
    groupe._on_reponse(VALIDER, "activer")  # sans chiffrement : rien
    dossier = _dossier(tmp_path, monkeypatch)
    cle = dossier / ".gcm4.key"
    cle.write_text(mpc.protect_key_file_content(cle.read_text(), "pw"))
    app, fenetre = _lancer(dossier, pomper)
    assert not fenetre.verrou.oubli.get_visible()
    fenetre.verrou.mot_de_passe.set_text("pw")
    fenetre.verrou.bouton.emit("clicked")
    pomper()
    app.activate_action("preferences", None)
    pomper()
    assert isinstance(app.preferences, PreferencesWindow)
    sous_titre = app.preferences.mot_de_passe_maitre.etat.get_subtitle()
    assert sous_titre == "Enabled, without a recovery code"
    _fermer(app, pomper)


def test_cle_illisible_au_demarrage(tmp_path, monkeypatch, pomper):
    """.gcm4.key illisible : pas de verrou, message d'erreur de configuration."""
    dossier = _dossier(tmp_path, monkeypatch)
    (dossier / ".gcm4.key").write_bytes(b"\xff\xfe")
    app, fenetre = _lancer(dossier, pomper)
    assert fenetre.verrou is None and fenetre.arbre is None
    assert fenetre.erreur_config is not None
    _fermer(app, pomper)
