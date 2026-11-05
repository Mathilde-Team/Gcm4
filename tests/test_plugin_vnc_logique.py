# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin VNC : logique de connexion et formulaire (#293, palier 12).

``plugins/plugin_vnc.py`` (GTK3) n'était couvert qu'à 34,8 %, par les tests
de ``_build_cmd`` de ``tests/test_gcm.py``. Ce fichier couvre le reste sans
GTK, sans réseau et sans lancer de viewer :

- chargement du module avec et sans gtk-vnc (``_GTKVNC_OK``) ;
- ``VncTab`` en mode natif (``GtkVnc.Display`` : signaux, authentification,
  presse-papiers dans les deux sens, touches spéciales, menu contextuel) et
  en mode sous-processus (commande selon le viewer, display, fichier passwd
  RFB, lancement, attente, déconnexion) ;
- ``make_vnc_passwd`` / ``reverse_bits`` (clé DES fixe du protocole RFB) ;
- ``VncPlugin`` : formulaire d'édition, aller-retour ``load_host_fields`` /
  ``save_host_fields``, ``host_fields``, ``get_plugin``.

``Gtk``, ``GLib``, ``Gdk``, ``GtkVnc``, ``GVnc``, ``DES``, ``subprocess`` et
``threading`` y sont remplacés par des doublures (monkeypatch, sans effet sur
les autres tests).
"""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
sys.path.insert(0, os.path.join(RACINE, "plugins"))

import test_gcm  # noqa: E402, F401  (pose les doublures gi/Gtk avant l'import)

# Import après test_gcm (doublures gi) : un tri des imports l'inverserait.
pv: Any = importlib.import_module("plugin_vnc")

# ---------------------------------------------------------------------------
# Doublures
# ---------------------------------------------------------------------------


class Widget:
    """Widget générique : texte, case, sensibilité, enfants, signaux."""

    def __init__(self, *_args: Any, label: str = "", **_kwargs: Any) -> None:
        """Initialise l'état."""
        self.texte = label
        self.actif = False
        self.sensible = True
        self.enfants: list[Any] = []
        self.signaux: dict[str, Any] = {}
        self.ids: list[tuple[str, str]] = []
        self.id_actif: str | None = None
        self.infobulle = ""
        self.classes: list[str] = []

    def __getattr__(self, nom: str) -> Any:
        """Toute autre méthode GTK est acceptée et ne fait rien."""
        if nom.startswith("__"):
            raise AttributeError(nom)
        return lambda *_a, **_k: None

    def connect(self, signal: str, rappel: Any) -> None:
        """Mémorise le gestionnaire d'un signal."""
        self.signaux[signal] = rappel

    def set_text(self, texte: str) -> None:
        """Mémorise le texte."""
        self.texte = texte

    def get_text(self) -> str:
        """Rend le texte."""
        return self.texte

    def set_markup(self, texte: str) -> None:
        """Mémorise le balisage."""
        self.texte = texte

    def set_tooltip_text(self, texte: str) -> None:
        """Mémorise l'infobulle."""
        self.infobulle = texte

    def set_active(self, actif: bool) -> None:
        """Coche ou décoche."""
        self.actif = actif

    def get_active(self) -> bool:
        """État de la case."""
        return self.actif

    def set_sensitive(self, sensible: bool) -> None:
        """Active ou grise."""
        self.sensible = sensible

    def attach(self, enfant: Any, *_a: Any) -> None:
        """Ajoute un enfant dans une grille."""
        self.enfants.append(enfant)

    def pack_start(self, enfant: Any, *_a: Any) -> None:
        """Ajoute un enfant."""
        self.enfants.append(enfant)

    def append(self, item_id: str, libelle: str) -> None:
        """Ajoute une entrée de liste déroulante."""
        self.ids.append((item_id, libelle))

    def set_active_id(self, item_id: str) -> None:
        """Sélectionne une entrée."""
        self.id_actif = item_id

    def get_active_id(self) -> str | None:
        """Entrée sélectionnée."""
        return self.id_actif

    def get_style_context(self) -> Any:
        """Contexte de style qui note les classes."""
        return SimpleNamespace(add_class=self.classes.append)


class Presse:
    """Presse-papiers GTK factice."""

    def __init__(self, texte: str | None = None) -> None:
        """Texte initial."""
        self.texte = texte
        self.pose: list[tuple[str, int]] = []

    def set_text(self, texte: str, longueur: int) -> None:
        """Mémorise le texte posé."""
        self.pose.append((texte, longueur))

    def wait_for_text(self) -> str | None:
        """Rend le texte courant."""
        return self.texte


def _faux_gtk(presse: Presse) -> SimpleNamespace:
    return SimpleNamespace(
        Box=Widget,
        Label=Widget,
        Button=Widget,
        Entry=Widget,
        Separator=Widget,
        Grid=Widget,
        CheckButton=Widget,
        ComboBoxText=Widget,
        Orientation=SimpleNamespace(VERTICAL="v", HORIZONTAL="h"),
        Align=SimpleNamespace(START="start"),
        Clipboard=SimpleNamespace(get=lambda _sel: presse),
    )


class Ecran:
    """``GtkVnc.Display`` factice."""

    def __init__(self) -> None:
        """Initialise l'état."""
        self.signaux: dict[str, Any] = {}
        self.reglages: dict[str, Any] = {}
        self.identifiants: list[tuple[str, str]] = []
        self.touches: list[tuple[Any, ...]] = []
        self.ouvert: tuple[str, str] | None = None
        self.ferme = False
        self.coupe: list[str] = []

    def connect(self, signal: str, rappel: Any) -> None:
        """Mémorise le gestionnaire d'un signal."""
        self.signaux[signal] = rappel

    def __getattr__(self, nom: str) -> Any:
        """Les ``set_*`` sont mémorisés."""
        if nom.startswith("set_"):
            return lambda valeur: self.reglages.__setitem__(nom[4:], valeur)
        raise AttributeError(nom)

    def set_credential(self, type_: str, valeur: str) -> None:
        """Note un identifiant transmis."""
        self.identifiants.append((type_, valeur))

    def send_keys_ex(self, touches: list[Any], sens: str) -> None:
        """Envoi avec appui ou relâchement explicites."""
        self.touches.append(("ex", tuple(touches), sens))

    def open_host(self, hote: str, port: str) -> None:
        """Ouvre la connexion."""
        self.ouvert = (hote, port)

    def close(self) -> None:
        """Ferme la connexion."""
        self.ferme = True

    def client_cut_text(self, texte: str) -> None:
        """Envoie le presse-papiers au serveur."""
        self.coupe.append(texte)


class EcranAncien(Ecran):
    """gtk-vnc ancien : pas de ``send_keys_ex``."""

    def send_keys_ex(self, touches: list[Any], sens: str) -> None:
        """Absent des anciennes versions."""
        raise AttributeError("send_keys_ex")

    def send_keys(self, touches: list[Any]) -> None:
        """Repli : clic de chaque touche."""
        self.touches.append(("clic", tuple(touches)))


CRED = SimpleNamespace(PASSWORD="pwd", USERNAME="user", CLIENTNAME="client")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Remplace les modules GTK/gtk-vnc du module par des doublures."""
    presse = Presse()
    monkeypatch.setattr(pv, "Gtk", _faux_gtk(presse))
    monkeypatch.setattr(pv, "GLib", SimpleNamespace(idle_add=lambda f, *a: f(*a)))
    monkeypatch.setattr(
        pv,
        "Gdk",
        SimpleNamespace(keyval_from_name=lambda nom: f"kv:{nom}", SELECTION_CLIPBOARD="clip"),
    )
    monkeypatch.setattr(
        pv,
        "GtkVnc",
        SimpleNamespace(
            Display=Ecran, DisplayKeyEvent=SimpleNamespace(PRESS="press", RELEASE="release")
        ),
        raising=False,
    )
    monkeypatch.setattr(pv, "GVnc", SimpleNamespace(ConnectionCredential=CRED), raising=False)
    return SimpleNamespace(presse=presse, monkeypatch=monkeypatch)


def _hote(**champs: Any) -> SimpleNamespace:
    """Hôte VNC minimal, champs surchargeables."""
    base: dict[str, Any] = {
        "name": "poste1",
        "host": "10.0.0.7",
        "port": "5900",
        "user": "alice",
        "description": "",
        "extra_params": "",
    }
    base.update(champs)
    return SimpleNamespace(**base)


def _onglet(hote: Any, mot_de_passe: str = "", ecran: Any = None) -> Any:
    """``VncTab`` sans ``__init__``, widgets remplacés par des doublures."""
    onglet = object.__new__(pv.VncTab)
    onglet.host = hote
    onglet._get_password = lambda: mot_de_passe
    onglet._proc = None
    onglet._passwd_path = None
    onglet._vnc_display = ecran
    onglet._lbl_status = Widget()
    onglet._btn_connect = Widget()
    onglet._btn_disconnect = Widget()
    onglet._entry_opts = Widget()
    return onglet


# ---------------------------------------------------------------------------
# Chargement du module
# ---------------------------------------------------------------------------


def test_chargement_avec_gtk_vnc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avec gtk-vnc importable, ``_GTKVNC_OK`` vaut vrai (copie fraîche du module)."""
    depot = ModuleType("gi.repository")
    for nom in ("Gdk", "GLib", "Gtk"):
        setattr(depot, nom, getattr(pv, nom))
    depot.GtkVnc = SimpleNamespace(Display=Ecran)  # type: ignore[attr-defined]
    depot.GVnc = SimpleNamespace()  # type: ignore[attr-defined]
    faux_gi = ModuleType("gi")
    faux_gi.require_version = lambda *_a: None  # type: ignore[attr-defined]
    faux_gi.repository = depot  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gi", faux_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", depot)
    spec = importlib.util.spec_from_file_location(
        "plugin_vnc_palier12", os.path.join(RACINE, "plugins", "plugin_vnc.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._GTKVNC_OK is True
    assert module.GtkVnc.Display is Ecran


def test_chargement_sans_gtk_vnc() -> None:
    """Dans la suite (gi factice), gtk-vnc est absent : repli sous-processus."""
    assert pv._GTKVNC_OK is False
    assert pv.__all__ == ["VncPlugin", "VncTab"]


# ---------------------------------------------------------------------------
# Construction de l'onglet
# ---------------------------------------------------------------------------


class TestConstruction:
    """``__init__``, ``_build_ui`` et ses deux variantes."""

    def test_init_mode_natif(self, env: SimpleNamespace) -> None:
        """En mode natif, l'onglet embarque un ``GtkVnc.Display`` réglé et branché."""
        env.monkeypatch.setattr(pv, "_GTKVNC_OK", True)
        onglet = pv.VncTab(_hote(port=""), lambda: "x")
        ecran = onglet._vnc_display
        assert isinstance(ecran, Ecran)
        assert onglet._proc is None and onglet._passwd_path is None
        assert ecran.reglages == {
            "keyboard_grab": True,
            "pointer_grab": True,
            "scaling": True,
            "hexpand": True,
            "vexpand": True,
            "can_focus": True,
        }
        assert set(ecran.signaux) == {
            "vnc-auth-credential",
            "vnc-connected",
            "vnc-initialized",
            "vnc-disconnected",
            "vnc-auth-failure",
            "vnc-server-cut-text",
            "focus-in-event",
        }
        assert ecran.signaux["vnc-connected"] == onglet._on_vnc_connected
        assert onglet._btn_disconnect.sensible is False
        assert "suggested-action" in onglet._btn_connect.classes
        assert onglet._btn_connect.signaux["clicked"] == onglet._on_connect
        assert onglet._btn_disconnect.signaux["clicked"] == onglet._on_disconnect

    def test_init_mode_sous_processus(self, env: SimpleNamespace) -> None:
        """En repli, l'onglet affiche les boutons et le champ d'options du viewer."""
        env.monkeypatch.setattr(pv, "_GTKVNC_OK", False)
        env.monkeypatch.setattr(pv, "VNC_BIN", "/usr/bin/vncviewer")
        onglet = pv.VncTab(_hote(description="Bureau", extra_params="-Shared"), lambda: "")
        assert onglet._vnc_display is None
        assert onglet._entry_opts.get_text() == "-Shared"
        assert "-FullScreen" in onglet._entry_opts.infobulle
        assert onglet._btn_disconnect.sensible is False

    def test_legacy_sans_description_ni_options(self, env: SimpleNamespace) -> None:
        """Sans description ni ``extra_params``, le champ d'options reste vide."""
        onglet = _onglet(_hote(extra_params=None, port=None))
        onglet._build_ui_legacy()
        assert onglet._entry_opts.get_text() == ""


# ---------------------------------------------------------------------------
# Mode natif
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("champs", "attendu"),
    [
        ({}, ("10.0.0.7", "5900")),
        ({"port": None}, ("10.0.0.7", "5900")),
        ({"port": 5905}, ("10.0.0.7", "5905")),
        ({"vnc_display": " 2 "}, ("10.0.0.7", "5902")),
        ({"vnc_display": None}, ("10.0.0.7", "5900")),
        ({"vnc_display": "abc", "port": "5999"}, ("10.0.0.7", "5999")),
    ],
)
def test_hote_et_port_effectifs(champs: dict[str, Any], attendu: tuple[str, str]) -> None:
    """Le numéro de display, s'il est valide, prime sur le port."""
    assert _onglet(_hote(**champs))._effective_host_port() == attendu


class TestNatif:
    """Rappels gtk-vnc, presse-papiers et touches spéciales."""

    def test_authentification(self, env: SimpleNamespace) -> None:
        """Chaque identifiant demandé reçoit sa valeur ; les autres sont ignorés."""
        ecran = Ecran()
        demandes = ["pwd", "user", "client", "autre"]
        liste = SimpleNamespace(n_values=len(demandes), get_nth=demandes.__getitem__)
        _onglet(_hote(), "secret")._on_auth_credential(ecran, liste)
        assert ecran.identifiants == [("pwd", "secret"), ("user", "alice"), ("client", "gcm")]

    def test_authentification_valeurs_vides(self, env: SimpleNamespace) -> None:
        """Mot de passe ou utilisateur absents : chaînes vides."""
        ecran = Ecran()
        onglet = _onglet(_hote(user=None))
        onglet._get_password = lambda: None
        liste = SimpleNamespace(n_values=2, get_nth=["pwd", "user"].__getitem__)
        onglet._on_auth_credential(ecran, liste)
        assert ecran.identifiants == [("pwd", ""), ("user", "")]

    def test_cycle_de_vie(self, env: SimpleNamespace) -> None:
        """Connexion, initialisation, échec d'authentification, déconnexion."""
        onglet = _onglet(_hote())
        onglet._on_vnc_connected(None)
        assert onglet._lbl_status.texte == "Connecting…"
        onglet._on_vnc_initialized(None)
        assert onglet._lbl_status.texte == "Connect"
        assert onglet._btn_connect.sensible is False
        assert onglet._btn_disconnect.sensible is True
        onglet._on_vnc_auth_failure(None, "mauvais mot de passe")
        assert onglet._lbl_status.texte == "Authentication failed"
        onglet._on_vnc_disconnected(None)
        assert onglet._lbl_status.texte == "Session ended"
        assert onglet._btn_connect.sensible is True
        assert onglet._btn_disconnect.sensible is False

    def test_touches_speciales(self, env: SimpleNamespace) -> None:
        """``send_keys_ex`` : appui puis relâchement de toute la combinaison."""
        ecran = Ecran()
        _onglet(_hote(), ecran=ecran)._send_special_keys(["Control_L", "Alt_L", "Delete"])
        combo = ("kv:Control_L", "kv:Alt_L", "kv:Delete")
        assert ecran.touches == [("ex", combo, "press"), ("ex", combo, "release")]

    def test_touches_speciales_repli(self, env: SimpleNamespace) -> None:
        """Sans ``send_keys_ex`` (gtk-vnc ancien), repli sur ``send_keys``."""
        ecran = EcranAncien()
        _onglet(_hote(), ecran=ecran)._send_special_keys(["Alt_L", "Tab"])
        assert ecran.touches == [("clic", ("kv:Alt_L", "kv:Tab"))]

    def test_presse_papiers_distant_texte(self, env: SimpleNamespace) -> None:
        """Le texte du serveur est copié dans le presse-papiers local."""
        _onglet(_hote())._on_vnc_server_cut_text(None, "bonjour")
        assert env.presse.pose == [("bonjour", -1)]

    def test_presse_papiers_distant_octets(self, env: SimpleNamespace) -> None:
        """Des octets sont décodés en UTF-8 (caractères invalides remplacés)."""
        _onglet(_hote())._on_vnc_server_cut_text(None, "é".encode() + b"\xff")
        assert env.presse.pose == [("é\ufffd", -1)]

    @pytest.mark.parametrize(
        ("champs", "texte"),
        [({"vnc_share_clipboard": False}, "x"), ({}, ""), ({}, b""), ({}, 42)],
    )
    def test_presse_papiers_distant_ignore(
        self, env: SimpleNamespace, champs: dict[str, Any], texte: Any
    ) -> None:
        """Partage désactivé, texte vide ou illisible : rien n'est copié."""
        _onglet(_hote(**champs))._on_vnc_server_cut_text(None, texte)
        assert env.presse.pose == []

    def test_presse_papiers_local_envoye(self, env: SimpleNamespace) -> None:
        """À la prise de focus, le presse-papiers local part au serveur."""
        env.presse.texte = "local"
        ecran = Ecran()
        assert _onglet(_hote())._on_vnc_focus_in(ecran, None) is False
        assert ecran.coupe == ["local"]

    @pytest.mark.parametrize(
        ("champs", "texte"), [({"vnc_share_clipboard": False}, "local"), ({}, None)]
    )
    def test_presse_papiers_local_ignore(
        self, env: SimpleNamespace, champs: dict[str, Any], texte: str | None
    ) -> None:
        """Partage désactivé ou presse-papiers vide : rien n'est envoyé."""
        env.presse.texte = texte
        ecran = Ecran()
        assert _onglet(_hote(**champs))._on_vnc_focus_in(ecran, None) is False
        assert ecran.coupe == []

    def test_connexion_et_deconnexion(self, env: SimpleNamespace) -> None:
        """``connect_vnc`` ouvre l'hôte effectif ; ``_on_disconnect`` ferme."""
        env.monkeypatch.setattr(pv, "_GTKVNC_OK", True)
        ecran = Ecran()
        onglet = _onglet(_hote(vnc_display="1"), ecran=ecran)
        onglet.connect_vnc()
        assert ecran.ouvert == ("10.0.0.7", "5901")
        assert onglet._lbl_status.texte == "Connecting…"
        onglet._on_disconnect(None)
        assert ecran.ferme is True

    def test_menu_contextuel(self, env: SimpleNamespace) -> None:
        """Menu commun des bureaux distants en natif, aucun en repli."""
        appels: list[tuple[Any, ...]] = []
        env.monkeypatch.setattr(
            pv, "build_remote_desktop_context_menu", lambda *a: appels.append(a) or "menu"
        )
        onglet = _onglet(_hote())
        env.monkeypatch.setattr(pv, "_GTKVNC_OK", False)
        assert onglet.build_context_menu() is None
        env.monkeypatch.setattr(pv, "_GTKVNC_OK", True)
        assert onglet.build_context_menu() == "menu"
        assert appels == [(onglet._btn_connect, onglet._btn_disconnect, onglet._send_special_keys)]


# ---------------------------------------------------------------------------
# Mot de passe RFB
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("octet", "attendu"), [(0, 0), (1, 128), (0b00010111, 0b11101000), (255, 255), (6, 96)]
)
def test_reverse_bits(octet: int, attendu: int) -> None:
    """Inversion MSB↔LSB d'un octet."""
    assert _onglet(_hote()).reverse_bits(octet) == attendu


class FauxDES:
    """``Cryptodome.Cipher.DES`` factice : note la clé et le clair."""

    MODE_ECB = "ecb"

    def __init__(self) -> None:
        """Initialise les relevés."""
        self.cle: bytes | None = None
        self.clair: bytes | None = None

    def new(self, cle: bytes, mode: str) -> FauxDES:
        """Note la clé."""
        assert mode == "ecb"
        self.cle = cle
        return self

    def encrypt(self, clair: bytes) -> bytes:
        """Chiffrement factice réversible."""
        self.clair = clair
        return bytes(b ^ 0xFF for b in clair)


def test_make_vnc_passwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Clé RFB fixe à bits inversés, mot de passe tronqué à 8 octets, fichier 0600."""
    des = FauxDES()
    monkeypatch.setattr(pv, "DES", des)
    chemin = tmp_path / "sous" / "rep" / "passwd"
    _onglet(_hote()).make_vnc_passwd("motdepasse-long", str(chemin))
    assert des.cle == bytes([232, 74, 214, 96, 196, 114, 26, 224])
    assert des.clair == b"motdepas"
    assert chemin.read_bytes() == bytes(b ^ 0xFF for b in b"motdepas")
    assert stat.S_IMODE(chemin.stat().st_mode) == 0o600


def test_make_vnc_passwd_complete_a_huit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Un mot de passe court est complété par des octets nuls."""
    des = FauxDES()
    monkeypatch.setattr(pv, "DES", des)
    _onglet(_hote()).make_vnc_passwd("abc", str(tmp_path / "p"))
    assert des.clair == b"abc\x00\x00\x00\x00\x00"


class TestFichierPasswd:
    """``_remove_passwd_file``."""

    def test_sans_fichier(self) -> None:
        """Aucun fichier en cours : rien à faire."""
        onglet = _onglet(_hote())
        onglet._remove_passwd_file()
        assert onglet._passwd_path is None

    def test_supprime(self, tmp_path: Path) -> None:
        """Le fichier de la connexion précédente est supprimé."""
        fichier = tmp_path / "p"
        fichier.write_bytes(b"x")
        onglet = _onglet(_hote())
        onglet._passwd_path = str(fichier)
        onglet._remove_passwd_file()
        assert not fichier.exists() and onglet._passwd_path is None

    def test_deja_supprime(self, tmp_path: Path) -> None:
        """Un fichier déjà disparu n'est pas une erreur."""
        onglet = _onglet(_hote())
        onglet._passwd_path = str(tmp_path / "absent")
        onglet._remove_passwd_file()
        assert onglet._passwd_path is None

    def test_attribut_absent(self) -> None:
        """Instance sans ``_passwd_path`` : pas d'erreur."""
        onglet = _onglet(_hote())
        del onglet._passwd_path
        onglet._remove_passwd_file()
        assert onglet._passwd_path is None


# ---------------------------------------------------------------------------
# Mode sous-processus : commande
# ---------------------------------------------------------------------------


@pytest.fixture
def binaires(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """``shutil.which`` piloté par un dictionnaire."""
    table: dict[str, str | None] = {
        "vncviewer": "/usr/bin/vncviewer",
        "vinagre": "/usr/bin/vinagre",
        "remmina": "/usr/bin/remmina",
        "krdc": "/usr/bin/krdc",
    }
    monkeypatch.setattr(pv.shutil, "which", lambda nom: table.get(nom))
    monkeypatch.setattr(pv, "VNC_BIN", "/opt/xtightvncviewer")
    return table


class TestBuildCmd:
    """``_build_cmd`` selon le viewer et les options de l'hôte."""

    @pytest.mark.parametrize(
        ("viewer", "attendu"),
        [
            ("vinagre", ["/usr/bin/vinagre", "vnc://10.0.0.7:5900"]),
            ("remmina", ["/usr/bin/remmina", "vnc://10.0.0.7:5900"]),
            ("krdc", ["/usr/bin/krdc", "vnc:/10.0.0.7:5900"]),
            ("tigervnc", ["/usr/bin/vncviewer", "10.0.0.7:5900"]),
            ("", ["/opt/xtightvncviewer", "10.0.0.7:5900"]),
            ("inconnu", ["/opt/xtightvncviewer", "10.0.0.7:5900"]),
        ],
    )
    def test_viewer(
        self, binaires: dict[str, str | None], viewer: str, attendu: list[str]
    ) -> None:
        """Le viewer choisi fixe le binaire et la syntaxe d'adresse."""
        cmd, mdp = _onglet(_hote(vnc_viewer=viewer))._build_cmd()
        assert (cmd, mdp) == (attendu, "")

    def test_viewer_choisi_absent(self, binaires: dict[str, str | None]) -> None:
        """Viewer choisi mais non installé : binaire détecté par défaut."""
        binaires["krdc"] = None
        cmd, _ = _onglet(_hote(vnc_viewer="krdc"))._build_cmd()
        assert cmd == ["/opt/xtightvncviewer", "10.0.0.7:5900"]

    @pytest.mark.parametrize(
        ("champs", "adresse"),
        [
            ({"vnc_display": "3"}, "10.0.0.7:5903"),
            ({"vnc_display": "x", "port": "6000"}, "10.0.0.7:6000"),
            ({"port": None}, "10.0.0.7:5900"),
        ],
    )
    def test_display(
        self, binaires: dict[str, str | None], champs: dict[str, Any], adresse: str
    ) -> None:
        """Numéro de display valide → 5900 + n ; invalide → port de l'hôte."""
        cmd, _ = _onglet(_hote(**champs))._build_cmd()
        assert cmd[1] == adresse

    def test_options_lecture_seule_plein_ecran(self, binaires: dict[str, str | None]) -> None:
        """``-ViewOnly``, ``-FullScreen`` et options libres découpées comme un shell."""
        onglet = _onglet(_hote(vnc_view_only="true", vnc_fullscreen=True))
        onglet._entry_opts.set_text("  -Quality 5 '-Name=A B'  ")
        cmd, _ = onglet._build_cmd()
        assert cmd == [
            "/opt/xtightvncviewer",
            "10.0.0.7:5900",
            "-ViewOnly",
            "-FullScreen",
            "-Quality",
            "5",
            "-Name=A B",
        ]

    def test_options_libres_avec_uri(self, binaires: dict[str, str | None]) -> None:
        """Les options libres s'ajoutent aussi aux viewers à URI."""
        onglet = _onglet(_hote(vnc_viewer="remmina", vnc_view_only=True))
        onglet._entry_opts.set_text("--enable-fullscreen")
        cmd, _ = onglet._build_cmd()
        assert cmd == ["/usr/bin/remmina", "vnc://10.0.0.7:5900", "--enable-fullscreen"]

    def test_fichier_passwd(
        self, binaires: dict[str, str | None], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Avec un mot de passe, un fichier passwd unique est passé par ``-passwd``."""
        ancien = tmp_path / "ancien"
        ancien.write_bytes(b"x")
        ecrits: list[tuple[str, str]] = []
        onglet = _onglet(_hote(), "secret")
        onglet._passwd_path = str(ancien)
        monkeypatch.setattr(
            onglet, "make_vnc_passwd", lambda mdp, chemin: ecrits.append((mdp, chemin))
        )
        cmd, mdp = onglet._build_cmd()
        assert mdp == "secret"
        assert not ancien.exists()
        chemin = onglet._passwd_path
        assert cmd == ["/opt/xtightvncviewer", "10.0.0.7:5900", "-passwd", chemin]
        assert ecrits == [("secret", chemin)]
        assert os.path.basename(chemin).startswith("gcm-vnc-") and chemin.endswith(".passwd")
        assert "secret" not in " ".join(cmd)
        onglet._remove_passwd_file()

    def test_fichier_passwd_echec(
        self, binaires: dict[str, str | None], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Si le fichier ne peut être écrit, il est supprimé et l'erreur remonte."""
        onglet = _onglet(_hote(), "secret")
        crees: list[str] = []

        def echoue(_mdp: str, chemin: str) -> None:
            crees.append(chemin)
            raise OSError("disque plein")

        monkeypatch.setattr(onglet, "make_vnc_passwd", echoue)
        with pytest.raises(OSError, match="disque plein"):
            onglet._build_cmd()
        assert onglet._passwd_path is None
        assert not os.path.exists(crees[0])


# ---------------------------------------------------------------------------
# Mode sous-processus : lancement, attente, déconnexion
# ---------------------------------------------------------------------------


class FauxProc:
    """``subprocess.Popen`` factice."""

    def __init__(self, cmd: list[str], rc: int | None = None, **kwargs: Any) -> None:
        """Note la commande et les flux."""
        self.cmd = cmd
        self.kwargs = kwargs
        self.rc = rc
        self.ecrit = b""
        self.stdin = self if kwargs.get("stdin") is not None else None
        self.ferme = False
        self.termine = False

    def write(self, donnees: bytes) -> None:
        """Écriture sur stdin."""
        self.ecrit += donnees

    def close(self) -> None:
        """Fermeture de stdin."""
        self.ferme = True

    def poll(self) -> int | None:
        """Code de retour (None tant qu'il tourne)."""
        return self.rc

    def wait(self) -> int:
        """Attend la fin."""
        return 0 if self.rc is None else self.rc

    def terminate(self) -> None:
        """Demande l'arrêt."""
        self.termine = True


@pytest.fixture
def lanceur(env: SimpleNamespace) -> SimpleNamespace:
    """Popen et Thread factices, mode sous-processus."""
    procs: list[FauxProc] = []
    fils: list[Any] = []

    def popen(cmd: list[str], **kwargs: Any) -> FauxProc:
        proc = FauxProc(cmd, **kwargs)
        procs.append(proc)
        return proc

    class Fil:
        def __init__(self, target: Any, daemon: bool) -> None:
            self.cible = target
            self.daemon = daemon
            self.lance = False

        def start(self) -> None:
            self.lance = True
            fils.append(self)

    env.monkeypatch.setattr(pv, "_GTKVNC_OK", False)
    env.monkeypatch.setattr(
        pv,
        "subprocess",
        SimpleNamespace(Popen=popen, PIPE="pipe", DEVNULL="devnull"),
    )
    env.monkeypatch.setattr(pv.threading, "Thread", Fil)
    return SimpleNamespace(procs=procs, fils=fils, monkeypatch=env.monkeypatch)


class TestLancement:
    """``_on_connect``, ``_wait_proc`` et ``_on_disconnect`` sans gtk-vnc."""

    def _onglet(self, lanceur: SimpleNamespace, cmd: list[str], mdp: str, binaire: str) -> Any:
        lanceur.monkeypatch.setattr(pv, "VNC_BIN", binaire)
        onglet = _onglet(_hote(), mdp)
        onglet._entry_opts.set_text("  -Shared  ")
        onglet._build_cmd = lambda: (cmd, mdp)
        return onglet

    def test_mot_de_passe_sur_stdin(self, lanceur: SimpleNamespace) -> None:
        """Le viewer vncviewer avec mot de passe : envoyé sur stdin, attente en tâche de fond."""
        onglet = self._onglet(lanceur, ["vncviewer", "h:1"], "secret", "/usr/bin/vncviewer")
        onglet._on_connect(None)
        (proc,) = lanceur.procs
        assert proc.kwargs == {"stdin": "pipe", "stdout": "devnull", "stderr": "devnull"}
        assert proc.ecrit == b"secret\n" and proc.ferme
        assert onglet.host.extra_params == "-Shared"
        assert onglet._lbl_status.texte == "Connecting…"
        assert onglet._btn_connect.sensible is False
        assert onglet._btn_disconnect.sensible is True
        (fil,) = lanceur.fils
        assert fil.lance and fil.daemon and fil.cible == onglet._wait_proc

    @pytest.mark.parametrize(
        ("binaire", "mdp"), [("/usr/bin/vinagre", "secret"), ("/usr/bin/vncviewer", "")]
    )
    def test_sans_stdin(self, lanceur: SimpleNamespace, binaire: str, mdp: str) -> None:
        """vinagre/remmina, ou pas de mot de passe : stdin n'est pas ouvert."""
        onglet = self._onglet(lanceur, ["v", "h"], mdp, binaire)
        onglet._on_connect(None)
        (proc,) = lanceur.procs
        assert proc.kwargs == {"stdout": "devnull", "stderr": "devnull"}
        assert proc.stdin is None

    def test_deja_lance(self, lanceur: SimpleNamespace) -> None:
        """Un viewer encore actif n'est pas relancé."""
        onglet = self._onglet(lanceur, ["v"], "", "/usr/bin/vncviewer")
        onglet._proc = FauxProc(["v"], rc=None)
        onglet._on_connect(None)
        assert lanceur.procs == [] and lanceur.fils == []

    def test_relance_apres_fin(self, lanceur: SimpleNamespace) -> None:
        """Un viewer terminé peut être relancé."""
        onglet = self._onglet(lanceur, ["v"], "", "/usr/bin/vncviewer")
        onglet._proc = FauxProc(["v"], rc=0)
        onglet._on_connect(None)
        assert len(lanceur.procs) == 1

    def test_viewer_absent(self, lanceur: SimpleNamespace, tmp_path: Path) -> None:
        """Viewer introuvable : fichier passwd supprimé, statut d'erreur, pas d'attente."""
        fichier = tmp_path / "p"
        fichier.write_bytes(b"x")
        onglet = self._onglet(lanceur, ["v"], "", "/usr/bin/vncviewer")
        onglet._passwd_path = str(fichier)

        def absent(*_a: Any, **_k: Any) -> None:
            raise FileNotFoundError("v")

        lanceur.monkeypatch.setattr(pv.subprocess, "Popen", absent)
        onglet._on_connect(None)
        assert not fichier.exists()
        assert onglet._lbl_status.texte == "xfreerdp not found"
        assert lanceur.fils == []
        assert onglet._btn_connect.sensible is True

    @pytest.mark.parametrize(("rc", "statut"), [(0, "Session ended"), (3, "Ended (code 3)")])
    def test_attente(self, env: SimpleNamespace, tmp_path: Path, rc: int, statut: str) -> None:
        """Fin du viewer : passwd supprimé, statut selon le code, boutons rétablis."""
        fichier = tmp_path / "p"
        fichier.write_bytes(b"x")
        onglet = _onglet(_hote())
        onglet._proc = FauxProc(["v"], rc=rc)
        onglet._passwd_path = str(fichier)
        onglet._btn_connect.set_sensitive(False)
        onglet._wait_proc()
        assert not fichier.exists()
        assert onglet._lbl_status.texte == statut
        assert onglet._btn_connect.sensible is True
        assert onglet._btn_disconnect.sensible is False
        assert onglet._proc is None

    def test_attente_sans_processus(self, env: SimpleNamespace) -> None:
        """Sans processus, seuls les boutons sont rétablis."""
        onglet = _onglet(_hote())
        onglet._lbl_status.set_text("avant")
        onglet._wait_proc()
        assert onglet._lbl_status.texte == "avant"
        assert onglet._btn_connect.sensible is True

    @pytest.mark.parametrize(("rc", "termine"), [(None, True), (0, False)])
    def test_deconnexion(self, lanceur: SimpleNamespace, rc: int | None, termine: bool) -> None:
        """Seul un viewer encore actif reçoit ``terminate``."""
        onglet = _onglet(_hote())
        proc = FauxProc(["v"], rc=rc)
        onglet._proc = proc
        onglet._btn_connect.set_sensitive(False)
        onglet._on_disconnect(None)
        assert proc.termine is termine
        assert onglet._lbl_status.texte == "Disconnect"
        assert onglet._btn_connect.sensible is True
        assert onglet._btn_disconnect.sensible is False

    def test_deconnexion_sans_processus(self, lanceur: SimpleNamespace) -> None:
        """Sans viewer, la déconnexion rétablit simplement les boutons."""
        onglet = _onglet(_hote())
        onglet._on_disconnect(None)
        assert onglet._btn_connect.sensible is True


# ---------------------------------------------------------------------------
# VncPlugin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        (True, True),
        ("no", False),
        ("", False),
        (False, False),
        (None, False),
    ],
)
def test_bool_from_str(valeur: object, attendu: bool) -> None:
    """Conversion des booléens texte de gcm.conf."""
    assert pv._bool_from_str(valeur) is attendu


class TestPlugin:
    """Formulaire d'édition et champs de ``VncPlugin``."""

    def test_identite(self) -> None:
        """Identifiant, port par défaut et point d'entrée."""
        plugin = pv.get_plugin()
        assert isinstance(plugin, pv.VncPlugin)
        assert (plugin.protocol_id, plugin.default_port, plugin.ui_order) == ("vnc", 5900, 3)
        assert plugin._cmb_viewer is None and plugin._chk_share_clipboard is None

    def test_host_fields(self) -> None:
        """Champs ``Host`` propres à VNC et leurs valeurs par défaut."""
        assert pv.VncPlugin().host_fields() == [
            ("vnc_viewer", "tigervnc"),
            ("vnc_display", ""),
            ("vnc_view_only", False),
            ("vnc_fullscreen", False),
            ("vnc_share_clipboard", True),
        ]

    def test_build_tab(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``build_tab`` délègue à ``VncTab``."""
        monkeypatch.setattr(pv, "VncTab", lambda h, f: ("onglet", h, f))
        hote, rappel = _hote(), lambda: "x"
        assert pv.VncPlugin().build_tab(hote, rappel) == ("onglet", hote, rappel)

    def test_page_edition(self, env: SimpleNamespace) -> None:
        """La page propose les viewers, le display et trois cases."""
        plugin = pv.VncPlugin()
        grille = plugin.build_edit_page()
        assert isinstance(grille, Widget)
        assert [i for i, _ in plugin._cmb_viewer.ids] == ["tigervnc", "vinagre", "remmina", "krdc"]
        assert plugin._cmb_viewer.get_active_id() == "tigervnc"
        assert plugin._chk_share_clipboard.get_active() is True
        assert plugin._chk_view_only.get_active() is False
        assert plugin._txt_display in grille.enfants
        assert len(grille.enfants) == 7

    def test_aller_retour(self, env: SimpleNamespace) -> None:
        """``load_host_fields`` puis ``save_host_fields`` conservent les valeurs."""
        plugin = pv.VncPlugin()
        plugin.build_edit_page()
        source = _hote(
            vnc_viewer="krdc",
            vnc_display=2,
            vnc_view_only="true",
            vnc_fullscreen="1",
            vnc_share_clipboard="false",
        )
        plugin.load_host_fields(source)
        plugin._txt_display.set_text("  2 ")
        cible = _hote()
        plugin.save_host_fields(cible)
        assert (
            cible.vnc_viewer,
            cible.vnc_display,
            cible.vnc_view_only,
            cible.vnc_fullscreen,
            cible.vnc_share_clipboard,
        ) == ("krdc", "2", True, True, False)

    def test_chargement_valeurs_absentes(self, env: SimpleNamespace) -> None:
        """Hôte sans champs VNC : valeurs par défaut."""
        plugin = pv.VncPlugin()
        plugin.build_edit_page()
        plugin.load_host_fields(SimpleNamespace(vnc_viewer="", vnc_display=None))
        assert plugin._cmb_viewer.get_active_id() == "tigervnc"
        assert plugin._txt_display.get_text() == ""
        assert plugin._chk_view_only.get_active() is False
        assert plugin._chk_share_clipboard.get_active() is True

    def test_sans_formulaire(self) -> None:
        """Avant ``build_edit_page`` : chargement sans effet, sauvegarde par défaut."""
        plugin = pv.VncPlugin()
        plugin.load_host_fields(_hote(vnc_viewer="krdc"))
        cible = _hote()
        plugin.save_host_fields(cible)
        assert (
            cible.vnc_viewer,
            cible.vnc_display,
            cible.vnc_view_only,
            cible.vnc_fullscreen,
            cible.vnc_share_clipboard,
        ) == ("tigervnc", "", False, False, True)


def test_module_independant_de_gcm() -> None:
    """Le plugin n'importe pas ``gnome_connection_manager``."""
    assert "gnome_connection_manager" not in vars(pv)
    assert isinstance(pv, ModuleType)
