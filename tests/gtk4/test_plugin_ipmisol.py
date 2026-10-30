# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin ipmisol GTK4 (``plugins/ipmisol/gtk4.py``, #179) avec un vrai ``Vte.Terminal``.

Un faux ``ipmitool`` (script Python désigné par le ``PATH``) note ses
arguments, la présence d'``IPMI_PASSWORD`` et répond « SOL » ; la fermeture
de la session doit envoyer ``sol deactivate``. La session VTE et la zone
d'options sont communes avec telnet et local (``gcm4.ui.terminal``) : leurs
cas détaillés sont dans ``test_plugin_telnet.py`` et
``test_plugin_local.py`` ; ici, ce qui est propre à ipmisol.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import gi
import pytest

gi.require_version("Vte", "3.91")

from gi.repository import GLib, Gtk, Vte  # noqa: E402

from gcm4.core import registre  # noqa: E402
from gcm4.core.model import Host  # noqa: E402

RACINE = Path(__file__).resolve().parents[2]

FAUX_IPMITOOL = """#!{python}
import os, sys, time
trace = open(os.environ["FAUX_IPMITOOL_TRACE"], "a")
def noter(texte):
    trace.write(texte + "\\n")
    trace.flush()
noter("argv=" + " ".join(sys.argv[1:]))
noter("IPMI_PASSWORD=" + ("present" if os.environ.get("IPMI_PASSWORD") else "absent"))
if "deactivate" in sys.argv[1:]:  # sol deactivate : rend la main aussitôt
    sys.exit(0)
sys.stdout.write("[SOL] ")
sys.stdout.flush()
for _ in range(300):  # reste ouvert jusqu'à SIGHUP (fermeture de l'onglet)
    time.sleep(0.1)
noter("fin")
sys.exit(0)
"""


class Journal:
    """Journal du contexte : garde les avertissements."""

    def __init__(self):
        """Aucun avertissement."""
        self.avertissements = []

    def warning(self, message, *args):
        """Avertissement formaté comme loguru."""
        self.avertissements.append(message.format(*args))

    def debug(self, *_args):
        """Ignoré."""


class Contexte:
    """Contexte minimal du contrat v1 (``docs/plugins.md``)."""

    def __init__(self):
        """Journal vide, configuration vide."""
        self.log = Journal()

    def config(self):
        """Section ``[plugin ipmisol]`` vide."""
        return {}


@pytest.fixture
def plugin():
    """Plugin ipmisol chargé depuis ``plugins/`` du dépôt, comme en mode source."""
    candidats = registre.candidats_dossier(RACINE / "plugins")
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: Contexte(), candidats).etats}
    assert etats["ipmisol"].erreur is None
    instance = etats["ipmisol"].plugin
    yield instance
    registre._oublier_paquet("gcm4_plugin_ipmisol")


@pytest.fixture
def faux_ipmitool(tmp_path, monkeypatch):
    """Faux ``ipmitool`` désigné par le ``PATH`` ; rend le fichier de trace."""
    programme = tmp_path / "ipmitool"
    programme.write_text(FAUX_IPMITOOL.format(python=sys.executable), encoding="utf-8")
    programme.chmod(0o755)
    trace = tmp_path / "trace"
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAUX_IPMITOOL_TRACE", str(trace))
    return trace


def _attendre(condition, delai=15.0):
    """Traite les événements GTK jusqu'à ``condition()`` ou ``delai`` secondes."""
    contexte = GLib.MainContext.default()
    fin = time.monotonic() + delai
    while not condition() and time.monotonic() < fin:
        contexte.iteration(False)
        time.sleep(0.005)
    return condition()


def _texte(terminal):
    """Texte du terminal, historique compris (API de VTE 0.70 à 0.84)."""
    lignes = terminal.get_row_count() + 200
    if hasattr(terminal, "get_text_range_format"):
        return terminal.get_text_range_format(Vte.Format.TEXT, 0, 0, lignes, 500)[0]
    return terminal.get_text_range(0, 0, lignes, 500, None)[0]


def _trace(fichier):
    """Lignes de trace du faux ipmitool."""
    return fichier.read_text(encoding="utf-8").splitlines() if fichier.exists() else []


def _hote(**champs):
    """Hôte ``ipmi`` de test."""
    valeurs = {
        "name": "bmc",
        "host": "10.0.0.5",
        "port": None,
        "protocol": "ipmi",
        "user": "admin",
        "password": "s3cret",
    }
    valeurs.update(champs)
    return Host(**valeurs)


def _afficher(session):
    """Fenêtre 800×600 qui montre l'onglet."""
    fenetre = Gtk.Window(child=session.widget, default_width=800, default_height=600)
    fenetre.present()
    return fenetre


def test_decouvert_depuis_les_sources(plugin):
    """Mode source : ``plugins/ipmisol`` est chargé, protocole ``ipmi``, port 623."""
    assert (plugin.name, plugin.protocols, plugin.version) == ("ipmisol", ("ipmi",), "1.0")
    assert plugin.default_port("ipmi") == 623
    assert "gcm4_plugin_ipmisol.core" in sys.modules


def test_ipmitool_lance_avec_ipmi_password(plugin, faux_ipmitool):
    """``sol activate`` lancé, mot de passe dans ``IPMI_PASSWORD``, jamais dans la ligne de commande."""
    hote = _hote(extra_params="-C 17")
    session = plugin.open(hote)
    fenetre = _afficher(session)
    assert _attendre(lambda: "argv=" in "\n".join(_trace(faux_ipmitool)))
    lignes = _trace(faux_ipmitool)
    assert "argv=-I lanplus -H 10.0.0.5 -p 623 -U admin -E -C 17 sol activate" in lignes
    assert "IPMI_PASSWORD=present" in lignes
    assert "s3cret" not in "\n".join(lignes)
    assert _attendre(lambda: "[SOL]" in _texte(session.terminal))
    session.close()
    fenetre.destroy()


def test_fermeture_envoie_sol_deactivate(plugin, faux_ipmitool):
    """Fermeture de l'onglet : ``sol deactivate`` envoyé (BMC déverrouillé, #179)."""
    session = plugin.open(_hote())
    fenetre = _afficher(session)
    assert _attendre(lambda: "IPMI_PASSWORD=" in "\n".join(_trace(faux_ipmitool)))
    session.close()
    assert _attendre(lambda: "deactivate" in "\n".join(_trace(faux_ipmitool)))
    lignes = _trace(faux_ipmitool)
    assert any("sol deactivate" in ligne for ligne in lignes)
    fenetre.destroy()
