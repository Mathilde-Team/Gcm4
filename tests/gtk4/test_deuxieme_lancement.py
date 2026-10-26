"""Deuxième lancement de gcm4 transmis à l'instance en cours (#275).

Deux niveaux :

- dans le processus : ``_on_command_line`` avec une ligne de commande
  distante simulée (arguments, environnement et dossier courant du
  deuxième lancement) ;
- en sous-processus : une vraie instance gcm4 sur un bus D-Bus privé
  (``dbus-daemon`` lancé par le test), puis de vrais deuxièmes
  lancements dont on vérifie le code de sortie, le message et l'effet dans
  le journal de l'instance en cours.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from tests.gtk4.conftest import _ouvrir

RACINE = Path(__file__).resolve().parents[2]


class LigneDistante:
    """Ligne de commande d'un deuxième lancement (interface de Gio.ApplicationCommandLine)."""

    def __init__(self, arguments, environ=None, cwd="/", distante=True):
        """Arguments sans ``argv[0]``, environnement en dictionnaire."""
        self.arguments = ["gcm4", *arguments]
        self.environ = [f"{k}={v}" for k, v in (environ or {}).items()]
        self.cwd = cwd
        self.distante = distante

    def get_is_remote(self):
        """Vrai pour un deuxième lancement."""
        return self.distante

    def get_arguments(self):
        """Arguments, ``argv[0]`` compris."""
        return self.arguments

    def get_environ(self):
        """Environnement ``CLE=valeur`` (``SEND_ENVIRONMENT``)."""
        return self.environ

    def get_cwd(self):
        """Dossier courant du deuxième lancement."""
        return self.cwd


def _principale(app):
    from gcm4.ui.main_window import MainWindow

    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


@pytest.fixture
def instance(tmp_path, monkeypatch, pomper):
    """Instance en cours sur la fixture aes ; journal non reconfiguré."""
    import logging_config

    debug: list[str | None] = []
    monkeypatch.setattr(
        logging_config, "enable_debug", lambda config_dir=None: debug.append(config_dir)
    )
    app, dossier = _ouvrir(tmp_path, monkeypatch, pomper)
    yield app, dossier, debug
    for fenetre in list(app.get_windows()):
        fenetre.destroy()
    pomper()


def test_drapeaux_transmettent_l_environnement():
    """Instance unique, ligne de commande et environnement du deuxième lancement transmis."""
    from gi.repository import Gio

    from gcm4.ui.application import DRAPEAUX

    assert DRAPEAUX & Gio.ApplicationFlags.HANDLES_COMMAND_LINE
    assert DRAPEAUX & Gio.ApplicationFlags.SEND_ENVIRONMENT
    assert not DRAPEAUX & Gio.ApplicationFlags.NON_UNIQUE


def test_premier_lancement_options_deja_appliquees(instance, pomper):
    """Ligne locale : la fenêtre est activée, rien n'est réanalysé."""
    app, _dossier, debug = instance
    assert app._on_command_line(app, LigneDistante(["--debug"], distante=False)) == 0
    assert debug == []
    assert app.args.debug is False


def test_debug_active_a_chaud_une_seule_fois(instance, pomper):
    """``gcm4 --debug`` alors que gcm4 tourne : mode debug activé dans l'instance."""
    app, dossier, debug = instance
    ligne = LigneDistante(["-c", str(dossier), "--debug"])
    assert app._on_command_line(app, ligne) == 0
    assert debug == [str(dossier)]
    assert app.args.debug is True  # Préférences > Journalisation : « remplacé par --debug »
    assert app._on_command_line(app, ligne) == 0
    assert debug == [str(dossier)]  # déjà actif : pas de reconfiguration


def test_hotes_selectionnes_dans_l_instance(instance, monkeypatch, pomper):
    """``gcm4 groupe/nom`` alors que gcm4 tourne : hôtes sélectionnés, absents signalés."""
    from gcm4.ui.main_window import MainWindow

    app, dossier, _debug = instance
    messages: list[str] = []
    monkeypatch.setattr(MainWindow, "notifier", lambda _self, texte: messages.append(texte))
    ligne = LigneDistante(["-c", str(dossier), "LAB/bases/db", "absent/x"])
    assert app._on_command_line(app, ligne) == 0
    pomper()
    fenetre = _principale(app)
    assert fenetre.arbre.chemin_selectionne() == "LAB/bases/db"
    assert messages == ["Host not found: absent/x"]


def test_config_relative_au_dossier_du_deuxieme_lancement(instance):
    """``-c`` relatif : résolu dans le dossier courant du deuxième lancement."""
    app, dossier, _debug = instance
    ligne = LigneDistante(["-c", dossier.name], cwd=str(dossier.parent))
    assert app._on_command_line(app, ligne) == 0


def test_autre_configuration_refusee(instance, tmp_path):
    """Autre dossier (``-c`` ou ``XDG_CONFIG_HOME``) : refus, rien d'appliqué."""
    from gcm4.ui.application import CODE_AUTRE_CONFIG

    app, _dossier, debug = instance
    autre = LigneDistante(["-c", str(tmp_path / "autre"), "--debug"])
    assert app._on_command_line(app, autre) == CODE_AUTRE_CONFIG
    xdg = LigneDistante([], environ={"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)})
    assert app._on_command_line(app, xdg) == CODE_AUTRE_CONFIG
    assert debug == []


def test_ligne_invalide_refusee(instance):
    """Option inconnue (appel D-Bus forgé) : refus, l'instance continue."""
    from gcm4.ui.application import CODE_REFUS

    app, _dossier, _debug = instance
    assert app._on_command_line(app, LigneDistante(["--inexistante"])) == CODE_REFUS
    assert _principale(app).get_visible()


# --- vrais processus ---------------------------------------------------------


def _gcm4(*arguments, env, cwd=None, attendre=True):
    """Lance ``gcm4`` (``gcm4.app:main``) dans un sous-processus."""
    commande = [sys.executable, "-c", "import sys; from gcm4.app import main; sys.exit(main())"]
    if not attendre:
        return subprocess.Popen(
            [*commande, *arguments],
            env=env,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return subprocess.run(
        [*commande, *arguments], env=env, cwd=cwd, capture_output=True, text=True, timeout=60
    )


def _attendre(condition, delai=30.0):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.2)
    return False


@pytest.fixture
def bus_prive():
    """Bus de session D-Bus privé, propre au test ; ``dbus-daemon`` absent : erreur (#160).

    Un processus ``dbus-daemon`` à part plutôt que ``Gio.TestDBus`` : celui-ci
    remplace la connexion de session du processus de test et s'est bloqué
    (``up`` ou ``down``) après les autres tests GTK de la même session pytest.
    """
    demon = shutil.which("dbus-daemon")
    assert demon, "dbus-daemon introuvable (paquet dbus-daemon)"
    processus = subprocess.Popen(
        [demon, "--session", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        adresse = processus.stdout.readline().strip()
        assert adresse, "dbus-daemon n'a pas donné d'adresse"
        yield adresse
    finally:
        processus.terminate()
        processus.wait(timeout=10)


def test_deux_vrais_lancements(bus_prive, tmp_path):
    """Instance en cours puis deuxièmes lancements : debug, hôtes, autre configuration."""
    from gi.repository import Gio, GLib

    from gcm4.core import chemins
    from gcm4.ui.application import APPLICATION_ID, CODE_AUTRE_CONFIG
    from tests.gtk4.conftest import FIXTURE_AES

    # Instance en cours sans -c : dossier par défaut $XDG_CONFIG_HOME/gcm4.
    xdg = tmp_path / "xdg"
    dossier = xdg / "gcm4"
    shutil.copytree(FIXTURE_AES, dossier)
    chemins.preparer(str(dossier))
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GCM_DEBUG", "GCM_LOG_LEVEL", "GCM_LOG_FILE", "XDG_CONFIG_HOME")
    }
    env.update(
        DBUS_SESSION_BUS_ADDRESS=bus_prive,
        GCM_NO_UPDATE_CHECK="1",
        USER="utilisateur",
        HOME=str(tmp_path),
        XDG_CONFIG_HOME=str(xdg),
        PYTHONPATH=os.pathsep.join([str(RACINE / "src"), str(RACINE)]),
    )
    bus = Gio.DBusConnection.new_for_address_sync(
        bus_prive,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None,
        None,
    )

    def enregistree():
        reponse = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (APPLICATION_ID,)),
            GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        return reponse.unpack()[0]

    journal = dossier / "log" / "gcm-app.log"
    premiere = _gcm4(env=env, attendre=False)
    try:
        assert _attendre(enregistree), "l'instance gcm4 ne s'est pas enregistrée sur le bus"

        # Même environnement, sans -c : même configuration (environnement transmis).
        deuxieme = _gcm4("--debug", env=env)
        assert deuxieme.returncode == 0, deuxieme.stderr
        assert _attendre(
            lambda: journal.exists() and "activé par un deuxième lancement" in journal.read_text()
        ), "mode debug non activé dans l'instance en cours"

        # -c relatif, depuis le dossier parent : même configuration.
        hotes = _gcm4("-c", dossier.name, "LAB/bases/db", "absent/x", env=env, cwd=xdg)
        assert hotes.returncode == 0, hotes.stderr
        assert _attendre(lambda: "ouvrir_hotes: 1 absent(s)" in journal.read_text())
        lignes = [ligne for ligne in journal.read_text().splitlines() if "ouvrir_hotes:" in ligne]
        assert any("ouvrir_hotes: 2 hôte(s)" in ligne for ligne in lignes), lignes

        autre = _gcm4("-c", str(tmp_path / "autre"), env=env)
        assert autre.returncode == CODE_AUTRE_CONFIG
        assert "gcm4 is already running with another configuration folder" in autre.stderr
        assert str(tmp_path / "autre") in autre.stderr

        # Sans -c : dossier par défaut calculé avec l'environnement du deuxième lancement.
        autre_xdg = _gcm4(env={**env, "XDG_CONFIG_HOME": str(tmp_path / "xdg2")})
        assert autre_xdg.returncode == CODE_AUTRE_CONFIG
        assert str(tmp_path / "xdg2" / "gcm4") in autre_xdg.stderr

        assert premiere.poll() is None, "l'instance en cours s'est arrêtée"
    finally:
        premiere.terminate()
        try:
            premiere.wait(timeout=10)
        except subprocess.TimeoutExpired:
            premiere.kill()
            premiere.wait()
        bus.close_sync(None)
