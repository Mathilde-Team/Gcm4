# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin rdp GTK4 (``plugins/rdp/gtk4.py``, #105) avec un faux AsyncRdp.

AsyncRdp (FreeRDP 3) n'est pas installé par le job GTK4 : ses modules sont
remplacés par des doublures (``asyncrdp``, ``RdpSession``, ``RdpView``)
qui notent les appels. Les tâches tournent sur une boucle asyncio
ordinaire : Debian 12 et Rocky 9 n'ont pas ``gi.events`` (PyGObject < 3.50),
la boucle GLib est vérifiée à part (``test_boucle.py``). Essai réel contre
xrdp : voir la PR #379.
"""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from gi.repository import Gtk

from gcm4.core import registre
from gcm4.core.model import Host
from gcm4.ui import boucle

RACINE = Path(__file__).resolve().parents[2]


@dataclass
class DriveMapping:
    """Doublure de ``asyncrdp.DriveMapping``."""

    name: str
    path: str


@dataclass
class RemoteAppOptions:
    """Doublure de ``asyncrdp.RemoteAppOptions``."""

    program: str


@dataclass
class RdpOptions:
    """Doublure de ``asyncrdp.RdpOptions`` (champs utilisés par le plugin)."""

    width: int = 1920
    height: int = 1080
    ignore_certificate: bool = False
    redirect_clipboard: bool = True
    redirect_drives: bool = False
    drives: list = field(default_factory=list)
    remoteapp: object = None


class FausseVue(Gtk.Box):
    """Doublure de ``RdpView`` : note les redimensionnements demandés."""

    __gtype_name__ = "FausseVueRdpTest"

    def __init__(self):
        """Aucun redimensionnement."""
        super().__init__()
        self.redimensionnements = []

    def _on_widget_resize(self, width, height):
        """Redimensionnement distant demandé."""
        self.redimensionnements.append((width, height))


class FausseSession:
    """Doublure de ``RdpSession`` : connexion réussie, en échec ou bloquée."""

    instances: list = []
    mode = "ok"

    def __init__(self, *, view, gdk_display, connect_fn):
        """Note la vue et la fonction de connexion."""
        self.view, self.connect_fn = view, connect_fn
        self.appels, self.deconnexions = [], 0
        FausseSession.instances.append(self)

    async def connect(self, host, port, username, password, *, domain=None, options=None):
        """Connexion simulée."""
        self.appels.append((host, port, username, password, domain, options))
        if FausseSession.mode == "echec":
            raise ConnectionError("authentification refusée")
        if FausseSession.mode == "bloque":
            await asyncio.sleep(3600)

    async def disconnect(self):
        """Déconnexion notée."""
        self.deconnexions += 1


class Contexte:
    """Contexte minimal du contrat v1."""

    def __init__(self):
        """Pas de notification, mot de passe à donner par le test."""
        self.log = types.SimpleNamespace(warning=lambda *a: None, debug=lambda *a: None)
        self.notifications, self.demandes = [], []

    def config(self):
        """Section ``[plugin rdp]`` vide."""
        return {}

    def notify(self, message):
        """Toast noté."""
        self.notifications.append(message)

    def ask_password(self, host, done):
        """Demande notée ; le test appelle ``done``."""
        self.demandes.append(done)


@pytest.fixture
def boucle_glib(monkeypatch):
    """Boucle asyncio courante, déclarée disponible comme sous ``boucle.executer``."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    monkeypatch.setattr(boucle, "asyncio_disponible", lambda: True)
    yield loop
    asyncio.set_event_loop(None)
    loop.close()


@pytest.fixture
def faux_asyncrdp(monkeypatch):
    """Modules ``asyncrdp``, ``gcm_gtk4_rdp_session``, ``gcm_gtk4_display_bridge`` simulés."""
    module = types.ModuleType("asyncrdp")
    module.RdpOptions, module.DriveMapping = RdpOptions, DriveMapping
    module.RemoteAppOptions = RemoteAppOptions
    module.connect = object()
    session = types.ModuleType("gcm_gtk4_rdp_session")
    session.RdpSession = FausseSession
    vue = types.ModuleType("gcm_gtk4_display_bridge")
    vue.RdpView = FausseVue
    for nom, mod in (
        ("asyncrdp", module),
        ("gcm_gtk4_rdp_session", session),
        ("gcm_gtk4_display_bridge", vue),
    ):
        monkeypatch.setitem(sys.modules, nom, mod)
    FausseSession.instances, FausseSession.mode = [], "ok"
    return module


@pytest.fixture
def chargement():
    """Plugin rdp chargé depuis ``plugins/``, comme en mode source, et son contexte."""
    contexte = Contexte()
    candidats = registre.candidats_dossier(RACINE / "plugins")
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: contexte, candidats).etats}
    assert etats["rdp"].erreur is None
    yield etats["rdp"].plugin, contexte
    registre._oublier_paquet("gcm4_plugin_rdp")


def _hote(options=None, **champs):
    """Hôte RDP de test."""
    valeurs = {"name": "bureau", "host": "10.0.0.7", "protocol": "rdp", "user": "mathilde"}
    valeurs.update(champs)
    hote = Host(**valeurs)
    hote.protocol_options["rdp"] = dict(options or {})
    return hote


def _finir(loop, futur):
    """Fait tourner la boucle GLib jusqu'à la fin de ``futur``."""
    return loop.run_until_complete(asyncio.wait_for(futur, 5))


def test_decouvert_depuis_les_sources(chargement):
    """Mode source : ``plugins/rdp`` chargé sans AsyncRdp (import paresseux)."""
    plugin, _contexte = chargement
    assert (plugin.name, plugin.protocols) == ("rdp", ("rdp",))
    assert plugin.default_port("rdp") == 3389
    assert plugin.default_options("rdp")["rdp_geometry"] == "fullscreen"


def test_sans_asyncio(chargement, faux_asyncrdp):
    """Pas de boucle asyncio sur GLib : échec explicite à l'ouverture."""
    plugin, _contexte = chargement
    assert not boucle.asyncio_disponible()
    with pytest.raises(RuntimeError):
        plugin.open(_hote(password="pw"))


def test_sans_asyncrdp(chargement, boucle_glib, monkeypatch):
    """AsyncRdp absent : FileNotFoundError au message traduit, pas de plantage."""
    plugin, _contexte = chargement
    monkeypatch.setitem(sys.modules, "asyncrdp", None)
    with pytest.raises(FileNotFoundError):
        plugin.open(_hote(password="pw"))


def test_connexion(chargement, boucle_glib, faux_asyncrdp, tmp_path):
    """Options GTK3 → RdpOptions ; le bureau s'affiche une fois connecté."""
    plugin, contexte = chargement
    hote = _hote(
        {
            "rdp_domain": "CORP",
            "rdp_geometry": "1024x768",
            "rdp_cert_ignore": "True",
            "rdp_share_folder": "True",
            "rdp_shared_folder_path": str(tmp_path),
            "rdp_remote_app": "||notepad",
        },
        password="pw",
        port=3390,
    )
    onglet = plugin.open(hote)
    assert onglet.widget.get_visible_child_name() == "etat"
    assert _finir(boucle_glib, onglet._tache) is True
    session = FausseSession.instances[0]
    hote_, port, utilisateur, mot_de_passe, domaine, options = session.appels[0]
    assert (hote_, port, utilisateur, mot_de_passe, domaine) == (
        "10.0.0.7",
        3390,
        "mathilde",
        "pw",
        "CORP",
    )
    assert (options.width, options.height, options.ignore_certificate) == (1024, 768, True)
    assert options.drives == [DriveMapping("gcm4", str(tmp_path))]
    assert options.remoteapp == RemoteAppOptions("||notepad")
    assert session.connect_fn is faux_asyncrdp.connect
    assert onglet.widget.get_visible_child_name() == "bureau"
    # Géométrie fixe sans rdp_dyn_res : la taille distante ne suit pas l'onglet.
    onglet.vue._on_widget_resize(640, 480)
    assert onglet.vue.redimensionnements == []
    onglet.close()
    _finir(boucle_glib, onglet._fermeture)
    assert session.deconnexions == 1
    assert contexte.notifications == []


def test_plein_ecran_suit_onglet(chargement, boucle_glib, faux_asyncrdp):
    """``fullscreen`` : la vue redemande la taille de l'onglet."""
    plugin, _contexte = chargement
    onglet = plugin.open(_hote(password="pw"))
    _finir(boucle_glib, onglet._tache)
    onglet.vue._on_widget_resize(640, 480)
    assert onglet.vue.redimensionnements == [(640, 480)]
    onglet.close()
    _finir(boucle_glib, onglet._fermeture)


def test_echec_affiche_et_notifie(chargement, boucle_glib, faux_asyncrdp):
    """Connexion refusée : message dans l'onglet et toast (#80)."""
    plugin, contexte = chargement
    FausseSession.mode = "echec"
    onglet = plugin.open(_hote(password="pw"))
    assert _finir(boucle_glib, onglet._tache) is False
    assert onglet.widget.get_visible_child_name() == "etat"
    assert "authentification refusée" in onglet.etat.get_label()
    assert len(contexte.notifications) == 1


def test_mot_de_passe_demande(chargement, boucle_glib, faux_asyncrdp):
    """Sans mot de passe enregistré : demandé, puis connexion ; annulation affichée."""
    plugin, contexte = chargement
    onglet = plugin.open(_hote())
    assert FausseSession.instances[0].appels == []
    contexte.demandes[0]("saisi")
    _finir(boucle_glib, onglet._tache)
    assert FausseSession.instances[0].appels[0][3] == "saisi"
    autre = plugin.open(_hote())
    contexte.demandes[1](None)
    assert autre._tache is None
    assert autre.etat.get_label() == "Connection cancelled"


def test_fermeture_pendant_la_connexion(chargement, boucle_glib, faux_asyncrdp):
    """Onglet fermé avant la fin de la connexion : tâche annulée, déconnexion."""
    plugin, _contexte = chargement
    FausseSession.mode = "bloque"
    onglet = plugin.open(_hote(password="pw"))
    boucle_glib.run_until_complete(asyncio.sleep(0.05))
    onglet.close()
    onglet.close()  # idempotent
    _finir(boucle_glib, onglet._fermeture)
    with pytest.raises(asyncio.CancelledError):
        _finir(boucle_glib, onglet._tache)
    assert FausseSession.instances[0].deconnexions == 1


def test_zone_options(chargement):
    """Géométrie « personnalisée » : taille visible ; cases écrites True/False."""
    plugin, _contexte = chargement
    options: dict[str, str] = {}
    changements = []
    zone = plugin.build_host_options_widget(_hote(), options, lambda: changements.append(1))
    assert options["rdp_geometry"] == "fullscreen"
    widgets = []
    pile = [zone]
    while pile:
        w = pile.pop()
        widgets.append(w)
        enfant = w.get_first_child()
        while enfant is not None:
            pile.append(enfant)
            enfant = enfant.get_next_sibling()
    liste = next(w for w in widgets if isinstance(w, Gtk.DropDown))
    taille = next(w for w in widgets if isinstance(w, Gtk.Box) and not w.get_visible())
    liste.set_selected(5)  # custom
    assert options["rdp_geometry"] == "custom"
    assert taille.get_visible()
    case = next(
        w
        for w in widgets
        if isinstance(w, Gtk.CheckButton) and w.get_label() == "Ignore certificate errors"
    )
    case.set_active(True)
    assert options["rdp_cert_ignore"] == "True"
    assert changements
