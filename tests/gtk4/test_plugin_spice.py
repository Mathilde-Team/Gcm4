# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin spice GTK4 (``plugins/spice/gtk4.py``, #179) : formulaire et sessions.

Les visionneuses (``remote-viewer``, ``virt-viewer``) et ``ssh`` sont de faux
scripts placés en tête du ``PATH`` : ils notent leurs arguments (et le
``.vv`` reçu) dans un fichier de trace puis sortent avec le code demandé.
Les sessions passent donc par les vrais ``Gio.Subprocess`` asynchrones.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from gi.repository import GLib, Gtk

from gcm4.core import registre
from gcm4.core.model import Host

RACINE = Path(__file__).resolve().parents[2]

FAUSSE_VISIONNEUSE = """#!/bin/sh
{
  printf 'argv:%s\\n' "$(basename "$0")"
  printf '%s\\n' "$@"
  for a in "$@"; do
    case "$a" in *.vv) [ -f "$a" ] && { echo 'vv:'; cat "$a"; echo 'mode:'; stat -c %a "$a"; } ;; esac
  done
} >> "$FAUX_SPICE_TRACE"
if [ -n "$FAUX_SPICE_ATTENTE" ]; then
  sleep 30 &
  trap 'kill $! 2>/dev/null; echo signal >> "$FAUX_SPICE_TRACE"; exit 0' TERM
  wait
fi
exit "${FAUX_SPICE_CODE:-0}"
"""

FAUX_SSH = """#!/bin/sh
printf 'ssh:%s\\n' "$@" >> "$FAUX_SPICE_TRACE"
if [ -n "$FAUX_SSH_ERREUR" ]; then
  echo "ligne 1" >&2
  echo "$FAUX_SSH_ERREUR" >&2
  exit 255
fi
if [ -n "$FAUX_SSH_BINAIRE" ]; then printf '\\377\\376'; exit 0; fi
printf '%s' "$FAUX_SSH_SORTIE"
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
        """Journal et notifications vides."""
        self.log = Journal()
        self.notifications = []

    def config(self):
        """Section ``[plugin spice]`` vide."""
        return {}

    def notify(self, message):
        """Notification notée."""
        self.notifications.append(message)


@pytest.fixture
def plugin():
    """Plugin spice chargé depuis ``plugins/`` du dépôt, comme en mode source."""
    candidats = registre.candidats_dossier(RACINE / "plugins")
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: Contexte(), candidats).etats}
    assert etats["spice"].erreur is None
    yield etats["spice"].plugin
    registre._oublier_paquet("gcm4_plugin_spice")


@pytest.fixture
def module(plugin):
    """Module ``gcm4_plugin_spice.gtk4`` du plugin chargé."""
    return sys.modules[type(plugin).__module__]


@pytest.fixture
def faux(tmp_path, monkeypatch):
    """Faux ``remote-viewer``, ``virt-viewer`` et ``ssh`` en tête du ``PATH`` ; rend la trace."""
    outils = tmp_path / "bin"
    outils.mkdir()
    for nom, script in (
        ("remote-viewer", FAUSSE_VISIONNEUSE),
        ("virt-viewer", FAUSSE_VISIONNEUSE),
        ("ssh", FAUX_SSH),
    ):
        chemin = outils / nom
        chemin.write_text(script, encoding="utf-8")
        chemin.chmod(0o755)
    trace = tmp_path / "trace.txt"
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    monkeypatch.setenv("PATH", str(outils) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAUX_SPICE_TRACE", str(trace))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    for variable in (
        "FAUX_SPICE_CODE",
        "FAUX_SPICE_ATTENTE",
        "FAUX_SSH_ERREUR",
        "FAUX_SSH_BINAIRE",
    ):
        monkeypatch.delenv(variable, raising=False)
    return trace


def _attendre(condition, delai=15.0):
    """Traite les événements GTK jusqu'à ``condition()`` ou ``delai`` secondes."""
    contexte = GLib.MainContext.default()
    fin = time.monotonic() + delai
    while not condition() and time.monotonic() < fin:
        contexte.iteration(False)
        time.sleep(0.005)
    return condition()


def _hote(options=None, **champs):
    """Hôte ``spice`` de test avec ses options."""
    base = {"name": "vm1", "host": "kvm.example.org", "protocol": "spice"}
    base.update(champs)
    hote = Host(**base)
    hote.protocol_options["spice"] = dict(options or {})
    return hote


def _lignes(trace):
    """Lignes de la trace des faux programmes."""
    return trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []


def _enfants(widget):
    """Descendants d'un widget GTK4, en profondeur."""
    enfant = widget.get_first_child()
    while enfant is not None:
        yield enfant
        yield from _enfants(enfant)
        enfant = enfant.get_next_sibling()


def test_identite(plugin):
    """Nom, version, protocole, port 5930 et options GTK3."""
    assert (plugin.name, plugin.version, plugin.protocols) == ("spice", "1.0", ("spice",))
    assert plugin.default_port("spice") == 5930
    assert plugin.default_options("spice")["spice_mode"] == "uri"
    assert plugin.validate_options("spice", {"spice_mode": "proxmox"})[0].champ == "spice_px_node"


def test_formulaire(plugin):
    """Mode choisi : sa grille seule visible ; saisies et cases écrivent les options."""
    options = plugin.default_options("spice")
    appels = []
    zone = plugin.build_host_options_widget(_hote(), options, lambda: appels.append(1))
    grilles = zone.grilles_mode
    assert [grilles[m].get_visible() for m in ("uri", "libvirt", "proxmox")] == [
        True,
        False,
        False,
    ]
    zone.boutons_mode["proxmox"].set_active(True)
    assert options["spice_mode"] == "proxmox"
    assert [grilles[m].get_visible() for m in ("uri", "libvirt", "proxmox")] == [
        False,
        False,
        True,
    ]
    assert not zone.boutons_mode["uri"].get_active()
    saisies = [w for w in _enfants(grilles["proxmox"]) if isinstance(w, Gtk.Entry)]
    saisies[0].set_text(" pve ")
    saisies[1].set_text("101")
    assert (options["spice_px_node"], options["spice_px_vmid"]) == ("pve", "101")
    cases = [w for w in _enfants(zone) if isinstance(w, Gtk.CheckButton)]
    usb = next(c for c in cases if c.get_label() == "Share USB devices")
    usb.set_active(True)
    assert options["spice_share_usb"] == "True"
    usb.set_active(False)
    assert options["spice_share_usb"] == "False"
    assert len(appels) >= 5


def test_formulaire_mode_existant(plugin):
    """Hôte en libvirt : bouton libvirt actif, grille libvirt visible."""
    options = {"spice_mode": "libvirt", "spice_libvirt_uri": "qemu:///system"}
    zone = plugin.build_host_options_widget(_hote(), options, lambda: None)
    assert zone.boutons_mode["libvirt"].get_active()
    assert zone.grilles_mode["libvirt"].get_visible()
    assert not zone.grilles_mode["uri"].get_visible()


def test_open_refuse(plugin, faux, monkeypatch):
    """Options invalides, paramètres illisibles ou visionneuse absente : exception claire."""
    with pytest.raises(ValueError):
        plugin.open(_hote({"spice_mode": "libvirt"}))
    with pytest.raises(ValueError):
        plugin.open(_hote(extra_params='-t "x'))
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(FileNotFoundError, match="virt-viewer"):
        plugin.open(_hote())


def test_session_uri(plugin, faux):
    """``.vv`` en 0600 passé à remote-viewer, effacé à la fin ; reconnexion possible."""
    session = plugin.open(_hote({"spice_share_audio": "True"}, port=5901, password="s3cret"))
    assert session.widget is not None
    assert _attendre(lambda: not session.en_cours)
    lignes = _lignes(faux)
    assert lignes[0] == "argv:remote-viewer"
    assert "--spice-disable-usbredir" in lignes
    assert "--spice-disable-audio" not in lignes
    assert "password=s3cret" in lignes and "port=5901" in lignes and "delete-this-file=1" in lignes
    assert lignes[lignes.index("mode:") + 1] == "600"
    assert session.fichier_vv is None
    assert list(Path(os.environ["XDG_RUNTIME_DIR"]).iterdir()) == []
    assert session.etat.get_text() == "Session ended"
    assert session.bouton_connecter.get_sensitive()
    assert not session.bouton_arreter.get_sensitive()
    session.bouton_connecter.emit("clicked")
    assert _attendre(lambda: not session.en_cours)
    assert _lignes(faux).count("argv:remote-viewer") == 2
    session.close()


def test_session_code_de_sortie(plugin, faux, monkeypatch):
    """Visionneuse en échec : code de sortie affiché."""
    monkeypatch.setenv("FAUX_SPICE_CODE", "3")
    session = plugin.open(_hote())
    assert _attendre(lambda: not session.en_cours)
    assert session.etat.get_text() == "Ended (code 3)"
    session.close()


def test_session_arreter_et_close(plugin, faux, monkeypatch):
    """Arrêter envoie SIGTERM ; un clic de reconnexion pendant la session est ignoré."""
    monkeypatch.setenv("FAUX_SPICE_ATTENTE", "1")
    session = plugin.open(_hote())
    assert session.en_cours
    assert session.bouton_arreter.get_sensitive()
    assert session.etat.get_text() == "Opened in remote-viewer"
    assert _attendre(lambda: "argv:remote-viewer" in _lignes(faux))
    session.connecter()
    assert _lignes(faux).count("argv:remote-viewer") == 1
    time.sleep(0.2)
    session.bouton_arreter.emit("clicked")
    assert _attendre(lambda: not session.en_cours)
    assert "signal" in _lignes(faux)
    session.close()
    session.connecter()
    assert not session.en_cours


def test_close_pendant_la_session(plugin, faux, monkeypatch):
    """Fermer l'onglet arrête la visionneuse et efface le ``.vv``."""
    monkeypatch.setenv("FAUX_SPICE_ATTENTE", "1")
    session = plugin.open(_hote())
    vv = session.fichier_vv
    assert _attendre(lambda: "argv:remote-viewer" in _lignes(faux))
    time.sleep(0.2)
    session.close()
    session.processus = None  # fin d'une ancienne visionneuse : ne touche pas à l'actuelle
    assert not vv.exists()
    assert _attendre(lambda: "signal" in _lignes(faux))
    _attendre(lambda: False, delai=0.3)
    assert session.etat.get_text() == "Opened in remote-viewer"


def test_session_libvirt(plugin, faux):
    """Mode libvirt : virt-viewer --connect, sans fichier ``.vv``."""
    options = {
        "spice_mode": "libvirt",
        "spice_libvirt_uri": "qemu:///system",
        "spice_vm_name": "w",
    }
    session = plugin.open(_hote(options))
    assert session.etat.get_text() == "Opened in virt-viewer"
    assert _attendre(lambda: not session.en_cours)
    lignes = _lignes(faux)
    assert lignes[:4] == ["argv:virt-viewer", "--connect", "qemu:///system", "--title"]
    assert lignes[-1] == "w"
    session.close()


def test_session_lancement_impossible(plugin, faux, monkeypatch, module):
    """Visionneuse disparue entre ``open`` et le lancement : erreur dans l'onglet."""
    session = plugin.open(_hote())
    assert _attendre(lambda: not session.en_cours)
    session.binaire = str(faux.parent / "absent")
    session.connecter()
    assert "Error launching viewer" in session.etat.get_text()
    assert session.context.notifications
    assert session.fichier_vv is None

    def refus(*_args, **_kw):
        raise OSError("disque plein")

    monkeypatch.setattr(module.core, "ecrire_vv", refus)
    session.connecter()
    assert "disque plein" in session.etat.get_text()
    session.close()


PROXMOX = {"spice_mode": "proxmox", "spice_px_node": "pve", "spice_px_vmid": "100"}


def test_session_proxmox(plugin, faux, monkeypatch):
    """Ticket demandé à pvesh par ssh puis ``.vv`` du ticket passé à remote-viewer."""
    ticket = {
        "type": "spice",
        "host": "pvespiceproxy:abc",
        "password": "ticket1",
        "proxy": "http://kvm.example.org:3128",
        "tls-port": 61000,
        "ca": "L1\nL2",
    }
    monkeypatch.setenv("FAUX_SSH_SORTIE", json.dumps(ticket))
    session = plugin.open(_hote(PROXMOX, port=5930, user="admin"))
    assert session.etat.get_text() == "Requesting the SPICE ticket…"
    assert _attendre(lambda: "argv:remote-viewer" in _lignes(faux))
    assert _attendre(lambda: not session.en_cours)
    lignes = _lignes(faux)
    assert lignes[:3] == ["ssh:-p", "ssh:22", "ssh:-o"]
    assert "ssh:admin" in lignes
    assert any("spiceproxy --proxy kvm.example.org" in ligne for ligne in lignes)
    assert "password=ticket1" in lignes and "ca=L1\\nL2" in lignes and "title=vm1" in lignes
    assert not any(ligne.startswith("--spice-ca-file") for ligne in lignes)
    assert session.etat.get_text() == "Session ended"
    session.close()


def test_session_proxmox_ssh_en_echec(plugin, faux, monkeypatch):
    """Échec de ssh : dernière ligne d'erreur affichée et notifiée."""
    monkeypatch.setenv("FAUX_SSH_ERREUR", "Permission denied (publickey).")
    session = plugin.open(_hote(PROXMOX))
    assert _attendre(lambda: not session.en_cours)
    assert session.etat.get_text() == "SSH error: Permission denied (publickey)."
    assert session.context.notifications == ["SSH error: Permission denied (publickey)."]
    assert "argv:remote-viewer" not in _lignes(faux)
    session.close()


def test_session_proxmox_ticket_illisible(plugin, faux, monkeypatch):
    """Ticket incomplet : erreur affichée, visionneuse non lancée."""
    monkeypatch.setenv("FAUX_SSH_SORTIE", json.dumps({"host": "h"}))
    session = plugin.open(_hote(PROXMOX))
    assert _attendre(lambda: not session.en_cours)
    assert "incomplete ticket" in session.etat.get_text()
    assert "argv:remote-viewer" not in _lignes(faux)
    session.close()


def test_session_proxmox_sans_ssh(plugin, faux, monkeypatch, module):
    """Programme ssh introuvable : erreur affichée tout de suite."""
    monkeypatch.setattr(module.core, "commande_ticket", lambda *_a: ["/nonexistent/ssh"])
    session = plugin.open(_hote(PROXMOX))
    assert not session.en_cours
    assert session.etat.get_text().startswith("SSH error: ")
    session.close()


def test_session_proxmox_fermee_pendant_la_demande(plugin, faux, monkeypatch):
    """Onglet fermé pendant la demande de ticket : rien n'est lancé ensuite."""
    monkeypatch.setenv("FAUX_SSH_SORTIE", "{}")
    session = plugin.open(_hote(PROXMOX))
    session.close()
    _attendre(lambda: False, delai=0.5)
    assert "argv:remote-viewer" not in _lignes(faux)
    assert session.etat.get_text() == "Requesting the SPICE ticket…"


def test_session_proxmox_sortie_illisible(plugin, faux, monkeypatch):
    """Sortie de ssh qui n'est pas de l'UTF-8 : erreur de lecture affichée."""
    monkeypatch.setenv("FAUX_SSH_BINAIRE", "1")
    session = plugin.open(_hote(PROXMOX))
    assert _attendre(lambda: not session.en_cours)
    assert session.etat.get_text().startswith("SSH error: ")
    assert "argv:remote-viewer" not in _lignes(faux)
    session.close()
