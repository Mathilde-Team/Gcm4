"""Plugin serial GTK4 (``plugins/serial/gtk4.py``, #179) avec un vrai ``Vte.Terminal``.

Un faux ``picocom`` (script désigné par le ``PATH``) note ses arguments ;
la zone d'options écrit les paramètres série dans les options du
protocole. La session VTE est commune avec telnet et local
(``gcm4.ui.terminal``) : leurs cas détaillés sont dans
``test_plugin_telnet.py`` et ``test_plugin_local.py`` ; ici, ce qui est
propre à serial.
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

FAUX_PICOCOM = """#!{python}
import os, sys, time
trace = open(os.environ["FAUX_PICOCOM_TRACE"], "a")
trace.write("argv=" + " ".join(sys.argv[1:]) + "\\n")
trace.flush()
sys.stdout.write("[picocom] ")
sys.stdout.flush()
for _ in range(300):  # reste ouvert jusqu'à SIGHUP (fermeture de l'onglet)
    time.sleep(0.1)
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
        """Section ``[plugin serial]`` vide."""
        return {}


@pytest.fixture
def plugin():
    """Plugin serial chargé depuis ``plugins/`` du dépôt, comme en mode source."""
    candidats = registre.candidats_dossier(RACINE / "plugins")
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: Contexte(), candidats).etats}
    assert etats["serial"].erreur is None
    instance = etats["serial"].plugin
    yield instance
    registre._oublier_paquet("gcm4_plugin_serial")


@pytest.fixture
def faux_picocom(tmp_path, monkeypatch):
    """Faux ``picocom`` désigné par le ``PATH`` ; rend le fichier de trace."""
    programme = tmp_path / "picocom"
    programme.write_text(FAUX_PICOCOM.format(python=sys.executable), encoding="utf-8")
    programme.chmod(0o755)
    trace = tmp_path / "trace"
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAUX_PICOCOM_TRACE", str(trace))
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
    """Lignes de trace du faux picocom."""
    return fichier.read_text(encoding="utf-8").splitlines() if fichier.exists() else []


def _hote(**champs):
    """Hôte ``serial`` de test : l'adresse est le chemin du port."""
    valeurs = {
        "name": "console",
        "host": "/dev/ttyUSB0",
        "port": None,
        "protocol": "serial",
    }
    valeurs.update(champs)
    return Host(**valeurs)


def _afficher(session):
    """Fenêtre 800×600 qui montre l'onglet."""
    fenetre = Gtk.Window(child=session.widget, default_width=800, default_height=600)
    fenetre.present()
    return fenetre


def test_decouvert_depuis_les_sources(plugin):
    """Mode source : ``plugins/serial`` est chargé, protocole ``serial``, sans port par défaut."""
    assert (plugin.name, plugin.protocols, plugin.version) == ("serial", ("serial",), "1.0")
    assert plugin.default_port("serial") is None
    assert "gcm4_plugin_serial.core" in sys.modules


def test_picocom_lance_avec_les_parametres(plugin, faux_picocom):
    """9600 8N1 par défaut, options libres de l'hôte, appareil en fin d'arguments."""
    hote = _hote(extra_params="--logfile /tmp/s.log")
    hote.protocol_options["serial"] = {"baud": "115200"}
    session = plugin.open(hote)
    fenetre = _afficher(session)
    assert _attendre(lambda: "argv=" in "\n".join(_trace(faux_picocom)))
    assert (
        "argv=--baud 115200 --flow n --parity n --databits 8 --stopbits 1 --logfile /tmp/s.log /dev/ttyUSB0"
        in _trace(faux_picocom)
    )
    assert _attendre(lambda: "[picocom]" in _texte(session.terminal))
    session.close()
    fenetre.destroy()


def test_debit_repris_du_port_hote(plugin, faux_picocom):
    """Héritage GTK3 : sans option ``baud``, le champ Port de l'hôte sert de débit."""
    session = plugin.open(_hote(port=19200))
    fenetre = _afficher(session)
    assert _attendre(lambda: "argv=" in "\n".join(_trace(faux_picocom)))
    assert any("--baud 19200" in ligne for ligne in _trace(faux_picocom))
    session.close()
    fenetre.destroy()


def test_zone_options_remplit_les_parametres(plugin, faux_picocom):
    """La zone d'options écrit les paramètres série, le template remplit les autres."""
    hote = _hote()
    options: dict[str, str] = {}
    changements = []

    def changed():
        """Note chaque appel (une modification par action de la zone)."""
        changements.append(True)

    zone = plugin.build_host_options_widget(hote, options, changed)
    assert isinstance(zone, Gtk.Widget)
    # Template « F5 TMOS » : 19200 8N1, flux aucun.
    assert list(_enfants(zone))  # la zone contient la grille série et la zone commune
    _selectionner_template(zone, "F5 TMOS")
    assert options["template"] == "F5 TMOS"
    assert (
        options["baud"],
        options["flow"],
        options["parity"],
        options["databits"],
        options["stopbits"],
    ) == ("19200", "n", "n", "8", "1")
    assert changements


def _enfants(widget):
    """Descendants de ``widget``, en profondeur d'abord (ordre du document)."""
    enfant = widget.get_first_child()
    while enfant is not None:
        yield enfant
        yield from _enfants(enfant)
        enfant = enfant.get_next_sibling()


def _selectionner_template(zone, nom):
    """Sélectionne ``nom`` dans le combo Template (premier Gtk.DropDown de la zone)."""
    for enfant in _enfants(zone):
        if isinstance(enfant, Gtk.DropDown):
            modele = enfant.get_model()
            for indice in range(modele.get_n_items()):
                if modele.get_string(indice) == nom:
                    enfant.set_selected(indice)
                    return
    raise AssertionError(f"template {nom!r} introuvable dans la zone")
