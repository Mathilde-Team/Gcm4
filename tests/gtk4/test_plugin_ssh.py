# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin ssh GTK4 (``plugins/ssh/gtk4.py``, #396 partie 2) avec un vrai ``Vte.Terminal``.

Un faux ``ssh`` (script Python placé en tête de ``PATH``) note ses
arguments, ``TERM``, l'existence du descripteur 3, le mot de passe reçu et
les commandes. La saisie automatique passe par le vrai ``expect`` et le
script du plugin ; la zone d'options écrit les options ssh de l'hôte
(mêmes clés INI que GTK3). Les cas de la session commune (fermeture,
commandes, encodage) sont dans ``test_plugin_telnet.py``.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import gi
import pytest

gi.require_version("Vte", "3.91")

from gi.repository import GLib, Gtk, Vte  # noqa: E402

from gcm4.core import registre  # noqa: E402
from gcm4.core.model import Host  # noqa: E402
from gcm4.ui.terminal import zone_options as zone_commune  # noqa: E402

RACINE = Path(__file__).resolve().parents[2]

FAUX_SSH = """#!{python}
import os, sys, termios
fd3 = os.path.exists("/proc/self/fd/3")  # avant tout open()
trace = open(os.environ["FAUX_SSH_TRACE"], "a")
def noter(texte):
    trace.write(texte + "\\n")
    trace.flush()
noter("argv=" + repr(sys.argv[1:]))
noter("TERM=" + os.environ.get("TERM", ""))
noter("fd3=" + str(fd3))
if os.environ.get("FAUX_SSH_MUET"):
    sys.stdin.readline()
    sys.exit(0)
sys.stdout.write("Bienvenue\\r\\n$ ")
sys.stdout.flush()
if os.environ.get("FAUX_SSH_AVEC_MOT_DE_PASSE"):
    attributs = termios.tcgetattr(0)
    sans_echo = list(attributs)
    sans_echo[3] &= ~termios.ECHO
    termios.tcsetattr(0, termios.TCSANOW, sans_echo)  # écho coupé avant l'invite, comme ssh
    sys.stdout.write("alice@h's password: ")
    sys.stdout.flush()
    passer = sys.stdin.readline().rstrip("\\r\\n")
    termios.tcsetattr(0, termios.TCSANOW, attributs)
    noter("pass=" + passer)
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
        """Journal vide, notifications vides, configuration vide."""
        self.log = Journal()
        self.notifications: list[str] = []

    def config(self):
        """Section ``[plugin ssh]`` vide."""
        return {}

    def notify(self, message):
        """Notification notée (#80 : toasts d'échec de connexion)."""
        self.notifications.append(message)


@pytest.fixture
def plugin():
    """Plugin ssh chargé depuis ``plugins/`` du dépôt, comme en mode source."""
    candidats = registre.candidats_dossier(RACINE / "plugins")
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: Contexte(), candidats).etats}
    assert etats["ssh"].erreur is None
    instance = etats["ssh"].plugin
    yield instance
    registre._oublier_paquet("gcm4_plugin_ssh")


@pytest.fixture
def faux_ssh(tmp_path, monkeypatch):
    """Faux ssh en tête de PATH ; rend le fichier de trace."""
    dossier = tmp_path / "bin"
    dossier.mkdir()
    programme = dossier / "ssh"
    programme.write_text(FAUX_SSH.format(python=sys.executable), encoding="utf-8")
    programme.chmod(0o755)
    trace = tmp_path / "trace"
    monkeypatch.setenv("PATH", f"{dossier}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAUX_SSH_TRACE", str(trace))
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
    """Lignes de trace du faux ssh."""
    return fichier.read_text(encoding="utf-8").splitlines() if fichier.exists() else []


def _hote(**champs):
    """Hôte ssh de test."""
    valeurs = {"name": "serveur", "host": "s1.example", "port": None, "protocol": "ssh"}
    valeurs.update(champs)
    return Host(**valeurs)


def _afficher(session):
    """Fenêtre 800×600 qui montre l'onglet."""
    fenetre = Gtk.Window(child=session.widget, default_width=800, default_height=600)
    fenetre.present()
    return fenetre


def _enfants(widget):
    """Descendants de ``widget``, en profondeur d'abord (ordre du document)."""
    enfant = widget.get_first_child()
    while enfant is not None:
        yield enfant
        yield from _enfants(enfant)
        enfant = enfant.get_next_sibling()


def _trouver(zone, type_, libelle=None):
    """Premier descendant du type ``type_``, par libellé si donné."""
    for enfant in _enfants(zone):
        if isinstance(enfant, type_) and (libelle is None or enfant.get_label() == libelle):
            return enfant
    raise AssertionError(f"{type_.__name__} {libelle!r} introuvable dans la zone")


def test_decouvert_depuis_les_sources(plugin):
    """Mode source : ``plugins/ssh`` est chargé, protocole ``ssh``, port 22 (#396)."""
    assert (plugin.name, plugin.protocols, plugin.version) == ("ssh", ("ssh",), "1.0")
    assert plugin.default_port("ssh") == 22
    assert "gcm4_plugin_ssh.session" in sys.modules
    assert plugin.context.notifications == []


def test_ssh_direct_avec_options(plugin, faux_ssh, tmp_path):
    """Sans mot de passe : ssh direct, toutes les options de l'hôte dans l'ordre GTK3."""
    cle = tmp_path / "id_test"
    cle.write_text("cle", encoding="utf-8")
    hote = _hote(
        user="alice",
        port=2222,
        font_color="#FFFFFF",
        back_color="#000000",
        extra_params="-4",
        protocol_options={
            "ssh": {
                "keepalive": "30",
                "tunnel": "9000:web:80,R:5900:bureau:5900,1080:*:*",
                "x11": "True",
                "agent": "True",
                "compression": "True",
                "compression-level": "9",
                "private_key": str(cle),
                "proxy_jump": "bastion",
                "term": "vt100",
            }
        },
    )
    session = plugin.open(hote)
    fenetre = _afficher(session)
    assert _attendre(lambda: "argv=" in "\n".join(_trace(faux_ssh)))
    assert _trace(faux_ssh)[:3] == [
        "argv=['-l', 'alice', '-p', '2222',"
        " '-o', 'ServerAliveInterval=30',"
        " '-L', '9000:web:80', '-R', '5900:bureau:5900', '-D', '1080',"
        " '-X', '-A', '-C', '-o', 'CompressionLevel=9',"
        f" '-i', '{cle}', '-o', 'ProxyJump=bastion', '-4', 's1.example']",
        "TERM=vt100",
        "fd3=False",
    ]
    assert _attendre(lambda: "Bienvenue" in _texte(session.terminal))
    session.terminal.feed_child(b"exit\r")
    assert _attendre(lambda: session.code_de_sortie is not None)
    assert os.waitstatus_to_exitcode(session.code_de_sortie) == 7
    session.close()
    fenetre.destroy()


def test_saisie_automatique_par_expect(plugin, faux_ssh, monkeypatch):
    """Mot de passe : expect le transmet ; il n'apparaît jamais à l'écran."""
    if shutil.which("expect") is None:
        pytest.fail("expect est requis par ces tests (paquet expect)")
    monkeypatch.setenv("FAUX_SSH_AVEC_MOT_DE_PASSE", "1")
    hote = _hote(user="alice", password="s3cr et'")
    session = plugin.open(hote)
    fenetre = _afficher(session)
    assert _attendre(lambda: "pass=" in "\n".join(_trace(faux_ssh)))
    assert "pass=s3cr et'" in _trace(faux_ssh)
    assert _attendre(lambda: "connecte" in _texte(session.terminal))
    texte = _texte(session.terminal)
    assert "s3cr" not in texte
    session.terminal.feed_child(b"exit\r")
    assert _attendre(lambda: session.code_de_sortie is not None)
    assert os.waitstatus_to_exitcode(session.code_de_sortie) == 7
    session.close()
    fenetre.destroy()


def test_cle_absente_signalée_puis_lancée(plugin, faux_ssh):
    """Clé renseignée mais introuvable : notification, ssh lancé quand même (GTK3)."""
    notifications = plugin.context.notifications
    hote = _hote(
        protocol_options={"ssh": {"private_key": "/chemin/absent/id_test"}},
    )
    session = plugin.open(hote)
    assert len(notifications) == 1
    assert "/chemin/absent/id_test" in notifications[0]
    assert "SSH private key file not found" in notifications[0]
    fenetre = _afficher(session)
    assert _attendre(lambda: "argv=" in "\n".join(_trace(faux_ssh)))
    assert "'-i', '/chemin/absent/id_test'" in _trace(faux_ssh)[0]
    session.close()
    fenetre.destroy()


def test_ssh_absent(plugin, monkeypatch, tmp_path):
    """Aucun ssh dans PATH : FileNotFoundError, message traduit."""
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(FileNotFoundError) as diagnostique:
        plugin.open(_hote())
    assert "ssh" in str(diagnostique.value)


def test_expect_absent_avec_mot_de_passe(plugin, faux_ssh, monkeypatch):
    """Mot de passe sans expect : FileNotFoundError, message traduit."""
    monkeypatch.setenv("PATH", str(faux_ssh.parent / "bin"))
    with pytest.raises(FileNotFoundError) as diagnostique:
        plugin.open(_hote(password="x"))
    assert "expect" in str(diagnostique.value)


# -- Zone d'options de l'hôte --------------------------------------------------


def _zone(plugin, options, changements):
    """Zone d'options de l'hôte ssh : carnet Advanced / Port forwarding + zone commune."""
    zone = plugin.build_host_options_widget(_hote(), options, lambda: changements.append(True))
    carnets = [e for e in _enfants(zone) if isinstance(e, Gtk.Notebook)]
    assert len(carnets) == 1
    return zone, carnets[0]


def _page(zone, carnet, numero):
    """Descendants de la page ``numero`` du carnet d'options."""
    return list(_enfants(carnet.get_nth_page(numero)))


def test_zone_options_avancee(plugin):
    """La page Advanced écrit les options ssh, mêmes clés INI que GTK3."""
    options = {
        "keepalive": "30",
        "x11": "True",
        "agent": "False",
        "compression": "True",
        "compression-level": "9",
        "private_key": "~/.ssh/id_test",
        "proxy_jump": "bastion",
    }
    changements = []
    zone, carnet = _zone(plugin, options, changements)
    page = carnet.get_nth_page(0)
    # Keep alive : case cochée, délai 30 ; décochée -> « 0 ».
    case = _trouver(page, Gtk.CheckButton, "Keep alive (seconds)")
    assert case.get_active()
    spins = [e for e in _enfants(page) if isinstance(e, Gtk.SpinButton)]
    delai = next(s for s in spins if s.get_adjustment().get_upper() > 100)
    assert delai.get_value() == 30 and delai.get_sensitive()
    case.set_active(False)
    assert options["keepalive"] == "0" and not delai.get_sensitive()
    case.set_active(True)
    delai.set_value(60)
    assert options["keepalive"] == "60"
    # X11 et agent.
    _trouver(page, Gtk.CheckButton, "X11 forwarding (-X)").set_active(False)
    assert options["x11"] == "False"
    _trouver(page, Gtk.CheckButton, "Agent forwarding (-A)").set_active(True)
    assert options["agent"] == "True"
    # Compression et niveau (1-9).
    case_compression = _trouver(page, Gtk.CheckButton, "Compression (-C)")
    assert case_compression.get_active()
    niveau = next(s for s in spins if s.get_adjustment().get_upper() == 9)
    assert niveau.get_value() == 9 and niveau.get_sensitive()
    case_compression.set_active(False)
    assert options["compression"] == "False" and not niveau.get_sensitive()
    # Clé privée et option avancée.
    saisies = [e for e in _enfants(page) if isinstance(e, Gtk.Entry)]
    cle = next(e for e in saisies if e.get_text() == "~/.ssh/id_test")
    cle.set_text("~/.ssh/autre")
    assert options["private_key"] == "~/.ssh/autre"
    saut = next(e for e in saisies if e.get_text() == "bastion")
    saut.set_text("")
    assert options["proxy_jump"] == ""
    assert changements


def test_zone_options_redirections(plugin):
    """La page Port forwarding écrit l'option ``tunnel`` au format GTK3."""
    options = {"tunnel": "9000:web:80"}
    changements = []
    zone, carnet = _zone(plugin, options, changements)
    page = carnet.get_nth_page(1)
    liste = _trouver(page, Gtk.ListBox)

    def rangées():
        """Lignes de redirection de la liste."""
        return [r for r in _enfants(liste) if isinstance(r, Gtk.ListBoxRow)]

    assert len(rangées()) == 1
    # Ajout d'une redirection locale « hôte:port » en un seul champ.
    saisies = [e for e in _enfants(page) if isinstance(e, Gtk.Entry)]
    hote = next(e for e in saisies if e.get_text() == "")
    hote.set_text("db:5432")
    _trouver(page, Gtk.Button, "Add").emit("clicked")
    assert options["tunnel"] == "9000:web:80,8080:db:5432"
    assert len(rangées()) == 2
    # Port local déjà pris : ajout ignoré, comme GTK3.
    _trouver(page, Gtk.Button, "Add").emit("clicked")
    assert options["tunnel"] == "9000:web:80,8080:db:5432"
    assert len(rangées()) == 2
    # Retrait de la sélection.
    liste.select_row(liste.get_row_at_index(1))
    _trouver(page, Gtk.Button, "Remove").emit("clicked")
    assert options["tunnel"] == "9000:web:80"
    assert len(rangées()) == 1
    # Type dynamique : hôte et port distant grisés, « *:* ».
    type_ = _trouver(page, Gtk.DropDown)
    type_.set_selected(2)
    assert not hote.get_sensitive()
    _trouver(page, Gtk.Button, "Add").emit("clicked")
    assert options["tunnel"] == "9000:web:80,8080:*:*"
    # Type distante : libellés retournés, préfixe « R: » ; doublon ignoré.
    type_.set_selected(1)
    _trouver(page, Gtk.Button, "Add").emit("clicked")
    assert options["tunnel"] == "9000:web:80,8080:*:*"
    liste.select_row(liste.get_row_at_index(1))
    _trouver(page, Gtk.Button, "Remove").emit("clicked")
    hote.set_text("bureau")
    _trouver(page, Gtk.Button, "Add").emit("clicked")
    assert "R:8080:bureau:8080" in options["tunnel"]
    assert changements


def test_zone_options_structure(plugin):
    """Deux pages dans le carnet, puis la zone commune des plugins terminal."""
    options = {}
    zone, carnet = _zone(plugin, options, [])
    assert carnet.get_n_pages() == 2
    assert carnet.get_tab_label_text(carnet.get_nth_page(0)) == "Advanced"
    assert carnet.get_tab_label_text(carnet.get_nth_page(1)) == "Port forwarding"
    assert isinstance(zone_commune(options, lambda: None), Gtk.Widget)
