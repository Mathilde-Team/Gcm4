"""Plugin telnet GTK4 (``plugins/telnet/gtk4.py``, #179) avec un vrai ``Vte.Terminal``.

Un faux ``telnet`` (script Python placé en tête de ``PATH``) affiche les
invites ``login:`` et ``Password:``, puis note ses arguments, ``TERM``,
l'utilisateur, le mot de passe et les commandes reçus. La saisie
automatique passe par le vrai ``expect`` et le script du plugin : le job
« Tests GTK4 (Xvfb) » installe VTE GTK4 et expect.
"""

from __future__ import annotations

import os
import shutil
import signal
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

FAUX_TELNET = """#!{python}
import os, sys, termios
fd3 = os.path.exists("/proc/self/fd/3")  # avant tout open()
trace = open(os.environ["FAUX_TELNET_TRACE"], "a")
def noter(texte):
    trace.write(texte + "\\n")
    trace.flush()
noter("argv=" + repr(sys.argv[1:]))
noter("TERM=" + os.environ.get("TERM", ""))
noter("fd3=" + str(fd3))
if os.environ.get("FAUX_TELNET_MUET"):
    sys.stdin.readline()
    sys.exit(0)
sys.stdout.write("Bienvenue\\r\\nlogin: ")
sys.stdout.flush()
noter("user=" + sys.stdin.readline().rstrip("\\r\\n"))
attributs = termios.tcgetattr(0)
sans_echo = list(attributs)
sans_echo[3] &= ~termios.ECHO
termios.tcsetattr(0, termios.TCSANOW, sans_echo)
sys.stdout.write("Password: ")
sys.stdout.flush()
noter("pass=" + sys.stdin.readline().rstrip("\\r\\n"))
termios.tcsetattr(0, termios.TCSANOW, attributs)
sys.stdout.write("\\r\\nconnecte\\r\\n$ ")
sys.stdout.flush()
for ligne in sys.stdin:
    noter("cmd=" + ligne.rstrip("\\r\\n"))
    if ligne.strip() == "exit":
        break
    sys.stdout.write("$ ")
    sys.stdout.flush()
sys.exit(7)
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
        """Section ``[plugin telnet]`` vide."""
        return {}


@pytest.fixture
def plugin():
    """Plugin telnet chargé depuis ``plugins/`` du dépôt, comme en mode source."""
    candidats = registre.candidats_dossier(RACINE / "plugins")
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: Contexte(), candidats).etats}
    assert etats["telnet"].erreur is None
    instance = etats["telnet"].plugin
    yield instance
    registre._oublier_paquet("gcm4_plugin_telnet")


@pytest.fixture
def faux_telnet(tmp_path, monkeypatch):
    """Faux telnet en tête de PATH ; rend le fichier de trace."""
    dossier = tmp_path / "bin"
    dossier.mkdir()
    programme = dossier / "telnet"
    programme.write_text(FAUX_TELNET.format(python=sys.executable), encoding="utf-8")
    programme.chmod(0o755)
    trace = tmp_path / "trace"
    monkeypatch.setenv("PATH", f"{dossier}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAUX_TELNET_TRACE", str(trace))
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
    """Lignes de trace du faux telnet."""
    return fichier.read_text(encoding="utf-8").splitlines() if fichier.exists() else []


def _hote(**champs):
    """Hôte telnet de test."""
    valeurs = {"name": "routeur", "host": "r1.example", "port": None, "protocol": "telnet"}
    valeurs.update(champs)
    return Host(**valeurs)


def _afficher(session):
    """Fenêtre 800×600 qui montre l'onglet."""
    fenetre = Gtk.Window(child=session.widget, default_width=800, default_height=600)
    fenetre.present()
    return fenetre


def test_decouvert_depuis_les_sources(plugin):
    """Mode source : ``plugins/telnet`` est chargé (gtk4.py importe core.py, #288)."""
    assert (plugin.name, plugin.protocols, plugin.version) == ("telnet", ("telnet",), "1.0")
    assert plugin.default_port("telnet") == 23
    assert "gcm4_plugin_telnet.core" in sys.modules


def test_saisie_automatique(plugin, faux_telnet):
    """Utilisateur et mot de passe : expect répond aux invites ; le mot de passe n'est jamais affiché."""
    if shutil.which("expect") is None:
        pytest.fail("expect est requis par ces tests (paquet expect)")
    hote = _hote(
        user="alice",
        password="s3cr et'",
        font_color="#FFFFFF",
        back_color="#000000",
        protocol_options={
            "telnet": {"commands": "show ver\\n##D=200\\nexit", "term": "vt100"},
        },
    )
    session = plugin.open(hote)
    fenetre = _afficher(session)
    assert _attendre(lambda: session.code_de_sortie is not None)
    assert _attendre(lambda: "Session ended" in _texte(session.terminal))
    assert _trace(faux_telnet) == [
        "argv=['-l', 'alice', 'r1.example', '23']",
        "TERM=vt100",
        "fd3=False",
        "user=alice",
        "pass=s3cr et'",
        "cmd=show ver",
        "cmd=exit",
    ]
    texte = _texte(session.terminal)
    assert "login: alice" in texte and "connecte" in texte
    assert "s3cr" not in texte
    assert os.waitstatus_to_exitcode(session.code_de_sortie) == 7
    assert session.pid == 0 and session._minuteries == {}
    session.close()
    fenetre.destroy()


def test_telnet_direct_sans_mot_de_passe(plugin, faux_telnet):
    """Sans mot de passe : telnet direct (pas d'expect), utilisateur ignoré, TERM=xterm, port donné."""
    hote = _hote(user="alice", port=2323, extra_params="-8")
    session = plugin.open(hote)
    fenetre = _afficher(session)
    assert _attendre(lambda: len(_trace(faux_telnet)) >= 3)
    assert _trace(faux_telnet)[:3] == [
        "argv=['-8', 'r1.example', '2323']",
        "TERM=xterm",
        "fd3=False",
    ]
    assert _attendre(lambda: "login:" in _texte(session.terminal))
    session.terminal.feed_child(b"bob\r")
    assert _attendre(lambda: "user=bob" in _trace(faux_telnet))
    session.close()
    assert _attendre(lambda: session.code_de_sortie is not None)
    assert os.WIFSIGNALED(session.code_de_sortie)
    assert os.WTERMSIG(session.code_de_sortie) == signal.SIGHUP
    fenetre.destroy()


def test_fermeture_annule_les_commandes(plugin, faux_telnet, monkeypatch):
    """Onglet fermé avant l'envoi des commandes : rien n'est envoyé, telnet reçoit SIGHUP."""
    monkeypatch.setenv("FAUX_TELNET_MUET", "1")
    hote = _hote(protocol_options={"telnet": {"commands": "ls"}})
    session = plugin.open(hote)
    assert len(session._minuteries) == 1
    assert _attendre(lambda: session.pid > 0)
    session.close()
    assert session._minuteries == {}
    assert _attendre(lambda: session.code_de_sortie is not None)
    assert not [ligne for ligne in _trace(faux_telnet) if ligne.startswith("cmd=")]
    session.close()  # idempotent : processus déjà terminé


def test_fermeture_avant_le_lancement(plugin, faux_telnet, monkeypatch):
    """Onglet fermé avant la fin du lancement asynchrone : telnet est arrêté dès son pid connu."""
    monkeypatch.setenv("FAUX_TELNET_MUET", "1")
    session = plugin.open(_hote())
    session.close()
    assert session.pid == 0
    assert _attendre(lambda: session.code_de_sortie is not None)
    assert os.WIFSIGNALED(session.code_de_sortie)


def test_processus_disparu(plugin, faux_telnet, monkeypatch):
    """SIGHUP vers un pid déjà terminé : ignoré (ProcessLookupError)."""
    monkeypatch.setenv("FAUX_TELNET_MUET", "1")
    session = plugin.open(_hote())
    assert _attendre(lambda: session.pid > 0)
    pid = session.pid
    os.kill(pid, signal.SIGKILL)
    assert _attendre(lambda: session.code_de_sortie is not None)
    session.pid = pid  # pid d'un processus terminé et récupéré par VTE
    session.close()
    assert session.fermee


def test_programmes_absents(plugin, tmp_path, monkeypatch):
    """Programme telnet absent, ou expect absent pour la saisie automatique : erreur nommant le programme."""
    vide = tmp_path / "vide"
    vide.mkdir()
    monkeypatch.setenv("PATH", str(vide))
    with pytest.raises(FileNotFoundError, match="telnet not found"):
        plugin.open(_hote())
    (vide / "telnet").write_text("#!/bin/sh\n", encoding="utf-8")
    (vide / "telnet").chmod(0o755)
    with pytest.raises(FileNotFoundError, match="expect not found"):
        plugin.open(_hote(user="alice", password="x"))


def test_lancement_impossible(plugin, tmp_path):
    """Échec du lancement asynchrone : message dans le terminal et avertissement au journal."""
    gtk4 = sys.modules["gcm4_plugin_telnet.gtk4"]
    core = sys.modules["gcm4_plugin_telnet.core"]
    absent = str(tmp_path / "absent")
    session = gtk4.TelnetSession(_hote(), core.Lancement([absent]), plugin.context.log)
    fenetre = _afficher(session)
    assert _attendre(lambda: bool(plugin.context.log.avertissements))
    assert "telnet: lancement impossible" in plugin.context.log.avertissements[0]
    assert _attendre(lambda: "absent" in _texte(session.terminal))
    assert session.pid == 0
    fenetre.destroy()


def test_couleurs_illisibles():
    """Couleur vide ou illisible : aucune ; ``#rrggbb`` lue."""
    gtk4 = sys.modules.get("gcm4_plugin_telnet.gtk4")
    if gtk4 is None:
        registre._importer_fichier("telnet", RACINE / "plugins" / "telnet" / "gtk4.py")
        gtk4 = sys.modules["gcm4_plugin_telnet.gtk4"]
    try:
        assert gtk4._rgba("") is None and gtk4._rgba("pas une couleur") is None
        couleur = gtk4._rgba("#ff0000")
        assert (couleur.red, couleur.green, couleur.blue) == (1.0, 0.0, 0.0)
        assert len(gtk4.PALETTE) == 16 and len(set(gtk4.PALETTE)) == 16
    finally:
        registre._oublier_paquet("gcm4_plugin_telnet")


def test_zone_d_options(plugin):
    """Zone d'options : commandes (case GTK3), touches d'effacement et TERM écrits dans la copie."""
    options = {"commands": "a\\nb", "delete-key": "3"}
    appels = []
    zone = plugin.build_host_options_widget(_hote(), options, lambda: appels.append(1))
    case, defilement, _aide, grille = list(_enfants(zone))
    vue = defilement.get_child()
    assert case.get_active() and vue.get_sensitive()
    tampon = vue.get_buffer()
    assert tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False) == "a\nb"
    tampon.set_text("enable\n##D=500\nshow run")
    assert options["commands"] == "enable\\n##D=500\\nshow run"
    case.set_active(False)
    assert options["commands"] == "" and not vue.get_sensitive()
    case.set_active(True)
    assert options["commands"] == "enable\\n##D=500\\nshow run"
    retour_arriere = grille.get_child_at(1, 0)
    suppression = grille.get_child_at(1, 1)
    assert retour_arriere.get_selected() == 0 and suppression.get_selected() == 3
    assert retour_arriere.get_model().get_n_items() == 5
    retour_arriere.set_selected(2)
    assert options["backspace-key"] == "2"
    term = grille.get_child_at(1, 2)
    assert term.get_placeholder_text() == "xterm"
    term.set_text(" vt220 ")
    assert options["term"] == "vt220"
    assert len(appels) >= 5


def test_zone_d_options_vide(plugin):
    """Sans commandes : case décochée, zone de texte inactive, rien écrit tant que rien ne change."""
    options = {}
    zone = plugin.build_host_options_widget(_hote(), options, lambda: None)
    case, defilement, _aide, _grille = list(_enfants(zone))
    assert not case.get_active() and not defilement.get_child().get_sensitive()
    assert options == {}


def _enfants(widget):
    """Enfants directs de ``widget``."""
    enfant = widget.get_first_child()
    while enfant is not None:
        yield enfant
        enfant = enfant.get_next_sibling()
