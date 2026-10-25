"""Plugin local GTK4 (``plugins/local/gtk4.py``, #295) avec un vrai ``Vte.Terminal``.

Un faux shell (script Python désigné par ``SHELL``) note ses arguments,
``TERM``, la présence d'un descripteur 3 et chaque ligne reçue. La session
VTE et la zone d'options sont communes avec telnet (``gcm4.ui.terminal``) :
leurs cas détaillés (couleurs, liaisons, lancement impossible) sont dans
``test_plugin_telnet.py`` ; ici, ce qui est propre à local.
"""

from __future__ import annotations

import os
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

FAUX_SHELL = """#!{python}
import os, sys
fd3 = os.path.exists("/proc/self/fd/3")  # avant tout open()
trace = open(os.environ["FAUX_SHELL_TRACE"], "a")
def noter(texte):
    trace.write(texte + "\\n")
    trace.flush()
noter("argv=" + repr(sys.argv[1:]))
noter("TERM=" + os.environ.get("TERM", ""))
noter("fd3=" + str(fd3))
noter("tty=" + str(os.isatty(0)))
sys.stdout.write("$ ")
sys.stdout.flush()
for ligne in sys.stdin:
    noter("cmd=" + ligne.rstrip("\\r\\n"))
    if ligne.strip() == "exit":
        break
    sys.stdout.write("$ ")
    sys.stdout.flush()
sys.exit(3)
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
        """Section ``[plugin local]`` vide."""
        return {}


@pytest.fixture
def plugin():
    """Plugin local chargé depuis ``plugins/`` du dépôt, comme en mode source."""
    candidats = registre.candidats_dossier(RACINE / "plugins")
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: Contexte(), candidats).etats}
    assert etats["local"].erreur is None
    instance = etats["local"].plugin
    yield instance
    registre._oublier_paquet("gcm4_plugin_local")


@pytest.fixture
def faux_shell(tmp_path, monkeypatch):
    """Faux shell désigné par ``SHELL`` ; rend le fichier de trace."""
    programme = tmp_path / "faux-shell"
    programme.write_text(FAUX_SHELL.format(python=sys.executable), encoding="utf-8")
    programme.chmod(0o755)
    trace = tmp_path / "trace"
    monkeypatch.setenv("SHELL", str(programme))
    monkeypatch.setenv("FAUX_SHELL_TRACE", str(trace))
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
    """Lignes de trace du faux shell."""
    return fichier.read_text(encoding="utf-8").splitlines() if fichier.exists() else []


def _hote(**champs):
    """Hôte local de test : pas d'adresse, pas de port."""
    valeurs = {"name": "poste", "host": "", "port": None, "protocol": "local"}
    valeurs.update(champs)
    return Host(**valeurs)


def _afficher(session):
    """Fenêtre 800×600 qui montre l'onglet."""
    fenetre = Gtk.Window(child=session.widget, default_width=800, default_height=600)
    fenetre.present()
    return fenetre


def _enfants(widget):
    """Enfants directs de ``widget``."""
    enfant = widget.get_first_child()
    while enfant is not None:
        yield enfant
        enfant = enfant.get_next_sibling()


def test_decouvert_depuis_les_sources(plugin):
    """Mode source : ``plugins/local`` est chargé (gtk4.py importe core.py et gcm4.ui.terminal)."""
    assert (plugin.name, plugin.protocols, plugin.version) == ("local", ("local",), "1.0")
    assert plugin.default_port("local") is None
    assert "gcm4_plugin_local.core" in sys.modules
    assert "gcm4.ui.terminal" in sys.modules


def test_shell_et_commandes(plugin, faux_shell):
    """``$SHELL`` lancé seul dans un vrai pty, TERM de l'hôte, commandes envoyées après 700 ms."""
    hote = _hote(
        font_color="#EEEEEC",
        back_color="#2E3436",
        protocol_options={
            "local": {"commands": "echo un\\necho deux\\n##D=100\\nexit", "term": "vt100"}
        },
    )
    debut = time.monotonic()
    session = plugin.open(hote)
    assert len(session._minuteries) == 2
    fenetre = _afficher(session)
    assert _attendre(lambda: "cmd=echo un" in _trace(faux_shell))
    assert time.monotonic() - debut >= 0.7
    assert _attendre(lambda: session.code_de_sortie is not None)
    assert _trace(faux_shell) == [
        "argv=[]",
        "TERM=vt100",
        "fd3=False",
        "tty=True",
        "cmd=echo un",
        "cmd=echo deux",
        "cmd=exit",
    ]
    assert os.waitstatus_to_exitcode(session.code_de_sortie) == 3
    assert _attendre(lambda: "Session ended" in _texte(session.terminal))
    assert session.pid == 0 and session._minuteries == {}
    session.close()
    fenetre.destroy()


def test_saisie_puis_fermeture(plugin, faux_shell):
    """Sans commandes : TERM=xterm, ce que l'utilisateur tape arrive au shell ; fermer l'onglet envoie SIGHUP."""
    session = plugin.open(_hote())
    assert session._minuteries == {}
    fenetre = _afficher(session)
    assert _attendre(lambda: "$ " in _texte(session.terminal))
    assert _trace(faux_shell)[:2] == ["argv=[]", "TERM=xterm"]
    session.terminal.feed_child(b"pwd\r")
    assert _attendre(lambda: "cmd=pwd" in _trace(faux_shell))
    session.close()
    assert _attendre(lambda: session.code_de_sortie is not None)
    assert os.WIFSIGNALED(session.code_de_sortie)
    assert os.WTERMSIG(session.code_de_sortie) == signal.SIGHUP
    fenetre.destroy()


def test_fermeture_annule_les_commandes(plugin, faux_shell):
    """Onglet fermé avant l'envoi : aucune commande ne part, les minuteries sont annulées."""
    session = plugin.open(_hote(protocol_options={"local": {"commands": "ls"}}))
    assert len(session._minuteries) == 1
    assert _attendre(lambda: session.pid > 0)
    session.close()
    assert session._minuteries == {}
    assert _attendre(lambda: session.code_de_sortie is not None)
    assert not [ligne for ligne in _trace(faux_shell) if ligne.startswith("cmd=")]


def test_shell_absent(plugin, tmp_path, monkeypatch):
    """``SHELL`` introuvable : erreur nommant le programme, rien n'est lancé."""
    absent = str(tmp_path / "pas-de-shell")
    monkeypatch.setenv("SHELL", absent)
    with pytest.raises(FileNotFoundError, match="pas-de-shell not found"):
        plugin.open(_hote())


def test_shell_par_defaut(plugin, tmp_path, monkeypatch):
    """``SHELL`` vide : ``/bin/sh`` (cherché par ``shutil.which``, comme un chemin absolu)."""
    monkeypatch.setenv("SHELL", "")
    session = plugin.open(_hote())
    assert _attendre(lambda: session.pid > 0)
    with open(f"/proc/{session.pid}/cmdline", "rb") as fichier:
        argv = fichier.read().split(b"\x00")
    assert argv[0] == b"/bin/sh"
    session.close()
    assert _attendre(lambda: session.code_de_sortie is not None)


def test_zone_d_options(plugin):
    """Même zone que telnet : commandes, touches d'effacement et TERM écrits dans la copie des options."""
    options = {"commands": "cd /srv", "backspace-key": "1"}
    appels = []
    zone = plugin.build_host_options_widget(_hote(), options, lambda: appels.append(1))
    case, defilement, _aide, grille = list(_enfants(zone))
    assert case.get_active()
    tampon = defilement.get_child().get_buffer()
    assert tampon.get_text(tampon.get_start_iter(), tampon.get_end_iter(), False) == "cd /srv"
    tampon.set_text("cd /srv\nls")
    assert options["commands"] == "cd /srv\\nls"
    assert grille.get_child_at(1, 0).get_selected() == 1
    grille.get_child_at(1, 1).set_selected(4)
    assert options["delete-key"] == "4"
    grille.get_child_at(1, 2).set_text("linux")
    assert options["term"] == "linux"
    assert len(appels) >= 3
