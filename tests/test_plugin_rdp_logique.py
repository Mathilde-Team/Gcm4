# SPDX-License-Identifier: GPL-3.0-or-later
r"""Plugin RDP : logique de connexion et formulaire (#293, palier 13).

``plugins/plugin_rdp.py`` (GTK3, gtk-frdp) n'était couvert qu'à 15,1 %. Ce
fichier couvre le reste sans GTK, sans réseau et sans monter d'ISO :

- ``FreeRdpTab`` : construction (``GtkFrdp.Display`` réglé, signaux, cible de
  glisser-déposer), cycle de vie de la session, erreurs, authentification
  (utilisateur ``DOMAINE\nom``), certificat, glisser-déposer de fichiers,
  touches spéciales, connexion (propriétés, presse-papiers, dossier ou ISO
  partagés, gtk-frdp ancien), déconnexion (démontage de l'ISO), menu ;
- ``RdpPlugin`` : formulaire d'édition, boîte « géométrie personnalisée »,
  aller-retour ``load_host_fields`` / ``save_host_fields``, ``host_fields``,
  ``get_plugin``.

``Gtk``, ``GLib``, ``Gdk``, ``GtkFrdp``, ``mount_iso_temp`` et
``umount_iso_temp`` y sont remplacés par des doublures (monkeypatch, sans
effet sur les autres tests).
"""

from __future__ import annotations

import importlib
import os
import sys
from types import SimpleNamespace
from typing import Any

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
sys.path.insert(0, os.path.join(RACINE, "plugins"))

import test_gcm  # noqa: E402, F401  (pose les doublures gi/Gtk avant l'import)

# Import après test_gcm (doublures gi) : un tri des imports l'inverserait.
pr: Any = importlib.import_module("plugin_rdp")

# ---------------------------------------------------------------------------
# Doublures
# ---------------------------------------------------------------------------


class Widget:
    """Widget générique : texte, case, visibilité, sensibilité, enfants, signaux."""

    def __init__(self, *_args: Any, label: str = "", **_kwargs: Any) -> None:
        """Initialise l'état."""
        self.texte = label
        self.actif = False
        self.visible = False
        self.sensible = True
        self.enfants: list[Any] = []
        self.signaux: dict[str, Any] = {}
        self.ids: list[tuple[str, str]] = []
        self.id_actif: str | None = None
        self.classes: list[str] = []
        self.no_show_all = False

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

    def set_active(self, actif: bool) -> None:
        """Coche ou décoche."""
        self.actif = actif

    def get_active(self) -> bool:
        """État de la case."""
        return self.actif

    def set_sensitive(self, sensible: bool) -> None:
        """Active ou grise."""
        self.sensible = sensible

    def show(self) -> None:
        """Affiche."""
        self.visible = True

    def hide(self) -> None:
        """Masque."""
        self.visible = False

    def show_all(self) -> None:
        """Affiche le widget et ses enfants (sauf ``no_show_all``)."""
        for enfant in [self, *self.enfants]:
            if isinstance(enfant, Widget) and not enfant.no_show_all:
                enfant.visible = True

    def set_no_show_all(self, valeur: bool) -> None:
        """Exclut le widget des ``show_all`` ancestraux."""
        self.no_show_all = valeur

    def attach(self, enfant: Any, *_a: Any) -> None:
        """Ajoute un enfant dans une grille."""
        self.enfants.append(enfant)

    def pack_start(self, enfant: Any, *_a: Any) -> None:
        """Ajoute un enfant."""
        self.enfants.append(enfant)

    def add(self, enfant: Any) -> None:
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


class Ecran:
    """``GtkFrdp.Display`` factice."""

    def __init__(self, proprietes_absentes: tuple[str, ...] = ()) -> None:
        """Initialise l'état ; certaines propriétés peuvent manquer (gtk-frdp ancien)."""
        self.absentes = proprietes_absentes
        self.proprietes: dict[str, Any] = {}
        self.signaux: dict[str, Any] = {}
        self.cible: tuple[Any, ...] | None = None
        self.ouvert: tuple[str, int] | None = None
        self.ferme = False
        self.auth: tuple[str, str, str] | None = None
        self.certificat: int | None = None
        self.fichiers: list[Any] = []
        self.combos: list[list[Any]] = []
        self.expansion: list[bool] = []

    def set_hexpand(self, valeur: bool) -> None:
        """Expansion horizontale."""
        self.expansion.append(valeur)

    def set_vexpand(self, valeur: bool) -> None:
        """Expansion verticale."""
        self.expansion.append(valeur)

    def set_property(self, nom: str, valeur: Any) -> None:
        """Propriété GObject ; absente → ``TypeError`` comme PyGObject."""
        if nom in self.absentes:
            raise TypeError(f"propriété inconnue {nom}")
        self.proprietes[nom] = valeur

    def connect(self, signal: str, rappel: Any) -> None:
        """Mémorise le gestionnaire d'un signal."""
        self.signaux[signal] = rappel

    def drag_dest_set(self, *args: Any) -> None:
        """Note la cible de glisser-déposer."""
        self.cible = args

    def open_host(self, hote: str, port: int) -> None:
        """Ouvre la connexion."""
        self.ouvert = (hote, port)

    def close(self) -> None:
        """Ferme la connexion."""
        self.ferme = True

    def authenticate_finish(self, user: str, mdp: str, domaine: str) -> None:
        """Termine l'authentification."""
        self.auth = (user, mdp, domaine)

    def certificate_verify_ex_finish(self, verdict: int) -> None:
        """Termine la vérification du certificat."""
        self.certificat = verdict

    def offer_dropped_files(self, uris: list[str]) -> None:
        """Annonce des fichiers déposés."""
        self.fichiers.append(uris)

    def send_key_combo(self, touches: list[Any]) -> None:
        """Envoie une combinaison de touches."""
        self.combos.append(touches)


class EcranAncien(Ecran):
    """gtk-frdp ancien : ni ``offer_dropped_files`` ni ``send_key_combo``."""

    def offer_dropped_files(self, uris: list[str]) -> None:
        """Absent des anciennes versions."""
        raise AttributeError("offer_dropped_files")

    def send_key_combo(self, touches: list[Any]) -> None:
        """Absent des anciennes versions."""
        raise AttributeError("send_key_combo")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Remplace les modules GTK/gtk-frdp et l'ISO du module par des doublures."""
    fins: list[tuple[Any, ...]] = []
    montages: list[str] = []
    demontages: list[str] = []
    ecrans: list[Ecran] = []
    resultat_montage: dict[str, Any] = {"valeur": ("/run/media/iso", "/dev/loop7")}

    def nouvel_ecran() -> Ecran:
        ecran = Ecran()
        ecrans.append(ecran)
        return ecran

    def monter(chemin: str) -> Any:
        montages.append(chemin)
        return resultat_montage["valeur"]

    monkeypatch.setattr(
        pr,
        "Gtk",
        SimpleNamespace(
            Box=Widget,
            Label=Widget,
            Button=Widget,
            Entry=Widget,
            Separator=Widget,
            Grid=Widget,
            CheckButton=Widget,
            ComboBoxText=Widget,
            ScrolledWindow=Widget,
            Orientation=SimpleNamespace(VERTICAL="v", HORIZONTAL="h"),
            Align=SimpleNamespace(START="start"),
            PolicyType=SimpleNamespace(NEVER="never"),
            DestDefaults=SimpleNamespace(ALL="all"),
            TargetEntry=SimpleNamespace(new=lambda *a: ("cible", *a)),
            drag_finish=lambda *a: fins.append(a),
        ),
    )
    monkeypatch.setattr(pr, "GLib", SimpleNamespace(idle_add=lambda f, *a: f(*a)))
    monkeypatch.setattr(
        pr,
        "Gdk",
        SimpleNamespace(
            keyval_from_name=lambda nom: f"kv:{nom}", DragAction=SimpleNamespace(COPY="copy")
        ),
    )
    monkeypatch.setattr(pr, "GtkFrdp", SimpleNamespace(Display=SimpleNamespace(new=nouvel_ecran)))
    monkeypatch.setattr(pr, "mount_iso_temp", monter)
    monkeypatch.setattr(pr, "umount_iso_temp", demontages.append)
    return SimpleNamespace(
        fins=fins,
        montages=montages,
        demontages=demontages,
        ecrans=ecrans,
        montage=resultat_montage,
        monkeypatch=monkeypatch,
    )


def _hote(**champs: Any) -> SimpleNamespace:
    """Hôte RDP minimal, champs surchargeables."""
    base: dict[str, Any] = {"name": "srv1", "host": "10.0.0.9", "port": "3390", "user": "alice"}
    base.update(champs)
    return SimpleNamespace(**base)


def _onglet(hote: Any, mot_de_passe: str | None = "", ecran: Any = None) -> Any:
    """``FreeRdpTab`` sans ``__init__``, widgets remplacés par des doublures."""
    onglet = object.__new__(pr.FreeRdpTab)
    onglet.host = hote
    onglet._get_password = lambda: mot_de_passe
    onglet._frdp_display = ecran if ecran is not None else Ecran()
    onglet._iso_loop_device = None
    onglet._lbl_status = Widget()
    onglet._btn_connect = Widget()
    onglet._btn_disconnect = Widget()
    return onglet


# ---------------------------------------------------------------------------
# Construction de l'onglet
# ---------------------------------------------------------------------------


class TestConstruction:
    """``__init__`` et ``_build_ui``."""

    def test_init(self, env: SimpleNamespace) -> None:
        """L'onglet embarque un ``GtkFrdp.Display`` réglé, branché et déposable."""
        onglet = pr.FreeRdpTab(_hote(port=None), lambda: "x")
        (ecran,) = env.ecrans
        assert onglet._frdp_display is ecran
        assert onglet._iso_loop_device is None
        assert ecran.proprietes == {"allow-resize": True, "scaling": False}
        assert ecran.expansion == [True, True]
        assert set(ecran.signaux) == {
            "rdp-connected",
            "rdp-disconnected",
            "rdp-error",
            "rdp-auth-failure",
            "rdp-needs-authentication",
            "rdp-needs-certificate-verification",
            "drag-data-received",
        }
        assert ecran.signaux["rdp-error"] == onglet._on_rdp_error
        assert ecran.cible == ("all", [("cible", "text/uri-list", 0, 0)], "copy")
        assert onglet._btn_disconnect.sensible is False
        assert "suggested-action" in onglet._btn_connect.classes
        assert "dim-label" in onglet._lbl_status.classes
        assert onglet._btn_connect.signaux["clicked"] == onglet._on_connect
        assert onglet._btn_disconnect.signaux["clicked"] == onglet._on_disconnect


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class TestSession:
    """Rappels gtk-frdp."""

    def test_connectee_puis_terminee(self, env: SimpleNamespace) -> None:
        """Connexion établie puis fin de session : statut et boutons."""
        onglet = _onglet(_hote())
        onglet._on_rdp_connected(None)
        assert onglet._lbl_status.texte == "Connect"
        assert onglet._btn_connect.sensible is False
        assert onglet._btn_disconnect.sensible is True
        onglet._on_rdp_disconnected(None)
        assert onglet._lbl_status.texte == "Session ended"
        assert onglet._btn_connect.sensible is True
        assert onglet._btn_disconnect.sensible is False
        assert env.demontages == []

    def test_fin_demonte_iso(self, env: SimpleNamespace) -> None:
        """Fin de session : l'ISO monté est démonté une seule fois."""
        onglet = _onglet(_hote())
        onglet._iso_loop_device = "/dev/loop3"
        onglet._on_rdp_disconnected(None)
        onglet._on_rdp_disconnected(None)
        assert env.demontages == ["/dev/loop3"]
        assert onglet._iso_loop_device is None

    def test_erreur(self, env: SimpleNamespace) -> None:
        """Erreur FreeRDP affichée dans le statut."""
        onglet = _onglet(_hote())
        onglet._on_rdp_error(None, "ERRCONNECT_CONNECT_FAILED")
        assert onglet._lbl_status.texte == "Error: ERRCONNECT_CONNECT_FAILED"

    def test_echec_authentification(self, env: SimpleNamespace) -> None:
        """Échec d'authentification : statut, reconnexion possible."""
        onglet = _onglet(_hote())
        onglet._btn_connect.set_sensitive(False)
        onglet._on_rdp_auth_failure(None, "refus")
        assert onglet._lbl_status.texte == "Authentication failed"
        assert onglet._btn_connect.sensible is True
        assert onglet._btn_disconnect.sensible is False

    @pytest.mark.parametrize(
        ("utilisateur", "mdp", "attendu"),
        [
            ("alice", "secret", ("alice", "secret", "")),
            ("CORP\\bob", "pw", ("bob", "pw", "CORP")),
            ("CORP\\sous\\bob", None, ("sous\\bob", "", "CORP")),
            (None, None, ("", "", "")),
        ],
    )
    def test_authentification(
        self, env: SimpleNamespace, utilisateur: str | None, mdp: str | None, attendu: tuple
    ) -> None:
        """``authenticate_finish`` reçoit utilisateur, mot de passe et domaine."""
        ecran = Ecran()
        _onglet(_hote(user=utilisateur), mdp)._on_rdp_needs_authentication(ecran)
        assert ecran.auth == attendu

    def test_certificat_accepte(self, env: SimpleNamespace) -> None:
        """Le certificat est accepté pour la session (verdict 1)."""
        ecran = Ecran()
        _onglet(_hote())._on_rdp_needs_certificate_verification(
            ecran, "10.0.0.9", 3389, "srv1", "CN=srv1", "CN=ca", "ab:cd", 0
        )
        assert ecran.certificat == 1


class TestGlisserDeposer:
    """``_on_rdp_drag_data_received``."""

    def _donnees(self, uris: list[str] | None) -> Any:
        return SimpleNamespace(get_uris=lambda: uris)

    def test_fichiers_annonces(self, env: SimpleNamespace) -> None:
        """Les URI déposées sont annoncées ; le dépôt est validé."""
        ecran = Ecran()
        uris = ["file:///tmp/a.txt", "file:///tmp/b.txt"]
        _onglet(_hote(), ecran=ecran)._on_rdp_drag_data_received(
            ecran, "ctx", 1, 2, self._donnees(uris), 0, 99
        )
        assert ecran.fichiers == [uris]
        assert env.fins == [("ctx", True, False, 99)]

    @pytest.mark.parametrize("uris", [[], None])
    def test_rien_depose(self, env: SimpleNamespace, uris: list[str] | None) -> None:
        """Aucune URI : rien n'est annoncé, le dépôt est refusé."""
        ecran = Ecran()
        _onglet(_hote(), ecran=ecran)._on_rdp_drag_data_received(
            ecran, "ctx", 0, 0, self._donnees(uris), 0, 5
        )
        assert ecran.fichiers == []
        assert env.fins == [("ctx", False, False, 5)]

    def test_gtk_frdp_ancien(self, env: SimpleNamespace) -> None:
        """Sans ``offer_dropped_files``, le dépôt est tout de même terminé."""
        ecran = EcranAncien()
        _onglet(_hote(), ecran=ecran)._on_rdp_drag_data_received(
            ecran, "ctx", 0, 0, self._donnees(["file:///x"]), 0, 7
        )
        assert env.fins == [("ctx", True, False, 7)]


class TestTouches:
    """``_send_special_keys``."""

    def test_combinaison(self, env: SimpleNamespace) -> None:
        """La combinaison est envoyée en une fois."""
        ecran = Ecran()
        _onglet(_hote(), ecran=ecran)._send_special_keys(["Control_L", "Alt_L", "Delete"])
        assert ecran.combos == [["kv:Control_L", "kv:Alt_L", "kv:Delete"]]

    def test_gtk_frdp_ancien(self, env: SimpleNamespace) -> None:
        """Sans ``send_key_combo``, l'appel est ignoré sans erreur."""
        ecran = EcranAncien()
        _onglet(_hote(), ecran=ecran)._send_special_keys(["Alt_L", "Tab"])
        assert ecran.combos == []


# ---------------------------------------------------------------------------
# Connexion et déconnexion
# ---------------------------------------------------------------------------


class TestConnexion:
    """``_on_connect``, ``connect_rdp``, ``_on_disconnect`` et menu."""

    def test_connexion_simple(self, env: SimpleNamespace) -> None:
        """Propriétés posées, dossier partagé vide, ouverture de l'hôte."""
        ecran = Ecran()
        onglet = _onglet(_hote(), "secret", ecran)
        onglet.connect_rdp()
        assert ecran.proprietes == {
            "username": "alice",
            "password": "secret",
            "redirect-clipboard": True,
            "shared-folder-path": "",
        }
        assert ecran.ouvert == ("10.0.0.9", 3390)
        assert onglet._btn_connect.sensible is False
        assert onglet._lbl_status.texte == "Connecting…"
        assert env.montages == []

    def test_domaine_et_port_par_defaut(self, env: SimpleNamespace) -> None:
        r"""``DOMAINE\nom`` fixe ``domain`` ; port absent → 3389 ; mot de passe absent → vide."""
        ecran = Ecran()
        onglet = _onglet(
            _hote(user="CORP\\bob", port=None, rdp_share_clipboard=False), None, ecran
        )
        onglet._on_connect(None)
        assert ecran.proprietes["username"] == "bob"
        assert ecran.proprietes["domain"] == "CORP"
        assert ecran.proprietes["password"] == ""
        assert ecran.proprietes["redirect-clipboard"] is False
        assert ecran.ouvert == ("10.0.0.9", 3389)

    def test_utilisateur_absent(self, env: SimpleNamespace) -> None:
        """Sans utilisateur : nom vide, pas de domaine."""
        ecran = Ecran()
        _onglet(_hote(user=None), "", ecran)._on_connect(None)
        assert ecran.proprietes["username"] == ""
        assert "domain" not in ecran.proprietes

    def test_dossier_partage(self, env: SimpleNamespace) -> None:
        """Un dossier partagé est redirigé tel quel (espaces retirés)."""
        ecran = Ecran()
        hote = _hote(rdp_share_folder=True, rdp_shared_folder_path="  /srv/partage  ")
        _onglet(hote, "", ecran)._on_connect(None)
        assert ecran.proprietes["shared-folder-path"] == "/srv/partage"
        assert env.montages == []

    def test_partage_coche_sans_chemin(self, env: SimpleNamespace) -> None:
        """Partage coché sans chemin : chemin vide."""
        ecran = Ecran()
        _onglet(_hote(rdp_share_folder=True, rdp_shared_folder_path=None), "", ecran)._on_connect(
            None
        )
        assert ecran.proprietes["shared-folder-path"] == ""

    def test_partage_decoche_ignore_le_chemin(self, env: SimpleNamespace) -> None:
        """Partage décoché : le chemin enregistré n'est pas redirigé."""
        ecran = Ecran()
        hote = _hote(rdp_share_folder=False, rdp_shared_folder_path="/srv/partage")
        _onglet(hote, "", ecran)._on_connect(None)
        assert ecran.proprietes["shared-folder-path"] == ""

    def test_iso_monte(self, env: SimpleNamespace) -> None:
        """Un ``.iso`` est monté, son point de montage redirigé, le loop retenu."""
        ecran = Ecran()
        hote = _hote(rdp_share_folder=True, rdp_shared_folder_path="/isos/Win.ISO")
        onglet = _onglet(hote, "", ecran)
        onglet._on_connect(None)
        assert env.montages == ["/isos/Win.ISO"]
        assert ecran.proprietes["shared-folder-path"] == "/run/media/iso"
        assert onglet._iso_loop_device == "/dev/loop7"
        onglet._on_disconnect(None)
        assert ecran.ferme is True
        assert env.demontages == ["/dev/loop7"]
        assert onglet._iso_loop_device is None

    def test_iso_non_monte(self, env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
        """Échec du montage : message d'erreur, aucun dossier redirigé, connexion tentée."""
        env.montage["valeur"] = None
        statuts: list[str] = []
        ecran = Ecran()
        hote = _hote(rdp_share_folder=True, rdp_shared_folder_path="/isos/x.iso")
        onglet = _onglet(hote, "", ecran)
        original = onglet._set_status
        monkeypatch.setattr(onglet, "_set_status", lambda t: (statuts.append(t), original(t)))
        onglet._on_connect(None)
        assert statuts == ["Error: could not mount the ISO", "Connecting…"]
        assert ecran.proprietes["shared-folder-path"] == ""
        assert onglet._iso_loop_device is None
        assert ecran.ouvert == ("10.0.0.9", 3390)

    def test_gtk_frdp_ancien(self, env: SimpleNamespace) -> None:
        """Propriétés de partage absentes : la connexion est quand même ouverte."""
        ecran = Ecran(("redirect-clipboard", "shared-folder-path"))
        _onglet(_hote(), "", ecran)._on_connect(None)
        assert "redirect-clipboard" not in ecran.proprietes
        assert "shared-folder-path" not in ecran.proprietes
        assert ecran.ouvert == ("10.0.0.9", 3390)

    def test_deconnexion_sans_iso(self, env: SimpleNamespace) -> None:
        """Déconnexion simple : fermeture, rien à démonter."""
        ecran = Ecran()
        _onglet(_hote(), "", ecran)._on_disconnect(None)
        assert ecran.ferme is True
        assert env.demontages == []

    def test_menu_contextuel(self, env: SimpleNamespace) -> None:
        """Menu commun des bureaux distants."""
        appels: list[tuple[Any, ...]] = []
        env.monkeypatch.setattr(
            pr, "build_remote_desktop_context_menu", lambda *a: appels.append(a) or "menu"
        )
        onglet = _onglet(_hote())
        assert onglet.build_context_menu() == "menu"
        assert appels == [(onglet._btn_connect, onglet._btn_disconnect, onglet._send_special_keys)]


# ---------------------------------------------------------------------------
# RdpPlugin
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
    assert pr._bool_from_str(valeur) is attendu


CHAMPS_RDP = (
    "rdp_domain",
    "rdp_geometry",
    "rdp_width",
    "rdp_height",
    "rdp_cert_ignore",
    "rdp_dyn_res",
    "rdp_remote_app",
    "rdp_share_clipboard",
    "rdp_share_folder",
    "rdp_shared_folder_path",
)


def _valeurs(hote: Any) -> tuple[Any, ...]:
    return tuple(getattr(hote, nom) for nom in CHAMPS_RDP)


class TestPlugin:
    """Formulaire d'édition et champs de ``RdpPlugin``."""

    def test_identite(self) -> None:
        """Identifiant, port par défaut, défilement propre et point d'entrée."""
        plugin = pr.get_plugin()
        assert isinstance(plugin, pr.RdpPlugin)
        assert (plugin.protocol_id, plugin.default_port, plugin.ui_order) == ("rdp", 3389, 2)
        assert plugin.self_scrolling is True
        assert plugin._cmb_geometry is None and plugin._txt_shared_folder_path is None
        assert pr.__all__ == ["FreeRdpTab", "RdpPlugin"]

    def test_host_fields(self) -> None:
        """Champs ``Host`` propres à RDP et leurs valeurs par défaut."""
        champs = pr.RdpPlugin().host_fields()
        assert tuple(nom for nom, _ in champs) == CHAMPS_RDP
        assert dict(champs)["rdp_geometry"] == "fullscreen"
        assert dict(champs)["rdp_share_clipboard"] is True

    def test_build_tab(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``build_tab`` délègue à ``FreeRdpTab``."""
        monkeypatch.setattr(pr, "FreeRdpTab", lambda h, f: ("onglet", h, f))
        hote, rappel = _hote(), lambda: "x"
        assert pr.RdpPlugin().build_tab(hote, rappel) == ("onglet", hote, rappel)

    def test_page_edition(self, env: SimpleNamespace) -> None:
        """Géométries proposées, boîte personnalisée masquée malgré ``show_all``."""
        plugin = pr.RdpPlugin()
        grille = plugin.build_edit_page()
        assert isinstance(grille, Widget)
        assert [i for i, _ in plugin._cmb_geometry.ids] == [
            "fullscreen",
            "1920x1080",
            "1280x1024",
            "1024x768",
            "800x600",
            "custom",
        ]
        assert plugin._cmb_geometry.get_active_id() == "fullscreen"
        assert plugin._cmb_geometry.signaux["changed"] == plugin._on_geometry_changed
        assert plugin._box_custom_geo.no_show_all is True
        assert plugin._box_custom_geo.visible is False
        assert plugin._box_custom_geo.enfants[0] is plugin._txt_width
        assert plugin._box_custom_geo.enfants[2] is plugin._txt_height
        assert plugin._chk_share_clipboard.get_active() is True
        assert plugin._chk_share_folder.get_active() is False
        assert len(grille.enfants) == 13

    def test_changement_de_geometrie(self, env: SimpleNamespace) -> None:
        """La boîte largeur×hauteur n'apparaît que pour « custom »."""
        plugin = pr.RdpPlugin()
        plugin.build_edit_page()
        combo = plugin._cmb_geometry
        combo.set_active_id("custom")
        plugin._on_geometry_changed(combo)
        assert plugin._box_custom_geo.visible is True
        combo.set_active_id("1024x768")
        plugin._on_geometry_changed(combo)
        assert plugin._box_custom_geo.visible is False

    def test_changement_sans_formulaire(self) -> None:
        """Avant ``build_edit_page``, un changement de géométrie est sans effet."""
        plugin = pr.RdpPlugin()
        plugin._on_geometry_changed(SimpleNamespace(get_active_id=lambda: "custom"))
        assert plugin._box_custom_geo is None

    def test_aller_retour_personnalise(self, env: SimpleNamespace) -> None:
        """Géométrie personnalisée : largeur et hauteur rechargées et conservées."""
        plugin = pr.RdpPlugin()
        plugin.build_edit_page()
        source = _hote(
            rdp_domain="CORP",
            rdp_geometry="custom",
            rdp_width="1600",
            rdp_height="900",
            rdp_cert_ignore="true",
            rdp_dyn_res="1",
            rdp_remote_app="||notepad",
            rdp_share_clipboard="false",
            rdp_share_folder="yes",
            rdp_shared_folder_path="/srv/p",
        )
        plugin.load_host_fields(source)
        assert plugin._box_custom_geo.visible is True
        plugin._txt_domain.set_text(" CORP ")
        cible = _hote()
        plugin.save_host_fields(cible)
        assert _valeurs(cible) == (
            "CORP",
            "custom",
            "1600",
            "900",
            True,
            True,
            "||notepad",
            False,
            True,
            "/srv/p",
        )

    def test_chargement_non_personnalise(self, env: SimpleNamespace) -> None:
        """Géométrie standard : boîte masquée, largeur et hauteur non rechargées."""
        plugin = pr.RdpPlugin()
        plugin.build_edit_page()
        plugin._box_custom_geo.show()
        plugin._txt_width.set_text("ancien")
        plugin.load_host_fields(_hote(rdp_geometry="800x600", rdp_width="1"))
        assert plugin._box_custom_geo.visible is False
        assert plugin._txt_width.get_text() == "ancien"
        assert plugin._cmb_geometry.get_active_id() == "800x600"

    def test_chargement_valeurs_absentes(self, env: SimpleNamespace) -> None:
        """Hôte sans champs RDP (ou à None) : valeurs par défaut."""
        plugin = pr.RdpPlugin()
        plugin.build_edit_page()
        plugin.load_host_fields(
            SimpleNamespace(rdp_geometry=None, rdp_domain=None, rdp_shared_folder_path=None)
        )
        assert plugin._cmb_geometry.get_active_id() == "fullscreen"
        assert plugin._txt_domain.get_text() == ""
        assert plugin._chk_share_clipboard.get_active() is True
        assert plugin._chk_cert_ignore.get_active() is False

    def test_chargement_personnalise_sans_dimensions(self, env: SimpleNamespace) -> None:
        """« custom » sans largeur ni hauteur, entrées absentes : pas d'erreur."""
        plugin = pr.RdpPlugin()
        plugin.build_edit_page()
        plugin.load_host_fields(_hote(rdp_geometry="custom"))
        assert plugin._txt_width.get_text() == ""
        plugin._txt_width = None
        plugin._txt_height = None
        plugin.load_host_fields(_hote(rdp_geometry="custom"))
        assert plugin._box_custom_geo.visible is True

    def test_sans_formulaire(self) -> None:
        """Avant ``build_edit_page`` : chargement sans effet, sauvegarde par défaut."""
        plugin = pr.RdpPlugin()
        plugin.load_host_fields(_hote(rdp_geometry="custom"))
        cible = _hote()
        plugin.save_host_fields(cible)
        assert _valeurs(cible) == ("", "fullscreen", "", "", False, False, "", True, False, "")
