# SPDX-License-Identifier: GPL-3.0-or-later
"""Services du contexte des plugins (#97) : question, chemin, rechargement.

``ContexteApplication`` offre aux plugins ``question`` (dialogue modal
au-dessus de la fenêtre), ``chemin_configuration`` et ``recharger``
(relecture de ``gcm4.conf`` après une écriture directe — migration ou
import ssh_config). Les dialogues sont pilotés en émettant ``response``,
comme ``test_dialogues.py``.
"""

from __future__ import annotations

from pathlib import Path

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core.config import load_config, save_config
from gcm4.core.model import Host
from gcm4.ui.contexte import ContexteApplication
from gcm4.ui.main_window import MainWindow


def _fenetre(app) -> MainWindow:
    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def test_chemin_configuration(app_fixture):
    """Le chemin annoncé est celui du gcm4.conf ouvert par la fenêtre."""
    app, dossier = app_fixture
    contexte = ContexteApplication(app, "ssh")
    assert contexte.chemin_configuration() == str(Path(dossier) / "gcm4.conf")


def test_chemin_configuration_sans_fenetre(application):
    """Aucune fenêtre : le chemin est ``None`` (rien à annoncer)."""
    for fenetre in list(application.get_windows()):
        fenetre.destroy()
    contexte = ContexteApplication(application, "ssh")
    assert contexte.chemin_configuration() is None


def test_question_reponse(app_fixture, pomper):
    """La question s'affiche sur la fenêtre et le rappel reçoit la réponse."""
    app, _dossier = app_fixture
    contexte = ContexteApplication(app, "ssh")
    recus: list[str] = []
    contexte.question(
        "Titre",
        "Corps",
        [("perso", "Perso"), ("partage", "Partagé")],
        "perso",
        "annuler",
        recus.append,
    )
    pomper()
    assert contexte.dialogue is not None
    contexte.dialogue.emit("response", "partage")
    pomper()
    assert recus == ["partage"]


def test_question_sans_fenetre_annulee(application, pomper):
    """Sans fenêtre, le rappel reçoit ``fermeture`` : la question est annulée."""
    for fenetre in list(application.get_windows()):
        fenetre.destroy()
    contexte = ContexteApplication(application, "ssh")
    recus: list[str] = []
    contexte.question("Titre", "Corps", [("perso", "Perso")], "perso", "annuler", recus.append)
    pomper()
    assert recus == ["annuler"]


def test_recharger_relit_le_fichier(app_fixture, pomper):
    """Une écriture externe de gcm4.conf : ``recharger`` reconstruit l'arbre."""
    app, dossier = app_fixture
    fenetre = _fenetre(app)
    contexte = ContexteApplication(app, "ssh")
    # Écriture « externe » : un hôte de plus, directement sur le disque,
    # sans passer par la fenêtre (comme la migration ssh_config).
    chemin_conf = str(Path(dossier) / "gcm4.conf")
    cfg = load_config(chemin_conf, fenetre.chiffrement)
    cfg.store.hosts.append(Host(name="nouveau", group="PROD", host="nouveau.example.org"))
    save_config(chemin_conf, cfg, fenetre.chiffrement)
    assert "nouveau" not in {h.name for h in fenetre.config.store.hosts}

    contexte.recharger()
    pomper()

    assert "nouveau" in {h.name for h in fenetre.config.store.hosts}
    assert fenetre.arbre is not None
    assert fenetre.lookup_action("search").get_enabled()


def test_recharger_configuration_illisible_puis_reparee(app_fixture, pomper):
    """Fichier cassé : message d'erreur, plus d'arbre ; réparé : l'arbre revient."""
    app, dossier = app_fixture
    fenetre = _fenetre(app)
    contexte = ContexteApplication(app, "ssh")

    (Path(dossier) / "gcm4.conf").write_text("[options\n", encoding="utf-8")
    contexte.recharger()
    pomper()
    assert fenetre.arbre is None
    assert fenetre.erreur_config is not None
    assert not fenetre.lookup_action("search").get_enabled()

    (Path(dossier) / "gcm4.conf").write_text("[options]\n", encoding="utf-8")
    contexte.recharger()
    pomper()
    assert fenetre.arbre is not None
    assert fenetre.erreur_config is None
    assert fenetre.lookup_action("search").get_enabled()


def test_recharger_sans_rien_a_recharger(app_fixture, pomper):
    """Rien de chargé (fenêtre verrouillée) : le rechargement est sans effet."""
    app, _dossier = app_fixture
    fenetre = _fenetre(app)
    arbre_avant = fenetre.arbre
    fenetre.config = None
    fenetre.erreur_config = None
    fenetre.recharger_configuration()
    pomper()
    assert fenetre.arbre is arbre_avant  # pas de reconstruction, pas d'erreur
