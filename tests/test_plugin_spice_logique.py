# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin SPICE : logique de connexion et formulaire (#293, palier 11).

``plugins/plugin_spice.py`` (GTK3) n'était couvert qu'à 24,9 %, par les
tests de ``_build_cmd`` de ``tests/test_gcm.py``. Ce fichier couvre le reste
sans GTK, sans réseau et sans toucher au système :

- ``parse_spice_hosts``, ``HostsUpdater`` (/etc/hosts direct ou via
  ``sudo -S tee``, tentatives, annulation) et ``_ask_password_dialog`` ;
- ``SpiceTab`` : URI ``spice://``, ticket Proxmox (``pvesh`` par SSH),
  paramètres libvirt (XML ``<graphics type='spice'>``), les trois modes de
  ``_build_cmd`` et de ``_connect_native`` (session spice-gtk, presse-papiers,
  USB, audio, dossier ou ISO partagés), connexion et déconnexion en mode
  sous-processus, événements de canal, touches spéciales, menu contextuel ;
- ``SpicePlugin`` : formulaire d'édition (trois modes exclusifs), aller-retour
  ``load_host_fields`` / ``save_host_fields``.

``tests/test_gcm.py`` neutralise ``parse_spice_hosts`` et ``HostsUpdater``
dans ``plugin_spice`` pour toute la session : une copie fraîche du module
est donc chargée sous un autre nom (même fichier, même mesure de
couverture), sans effet sur les autres tests. ``Gtk``, ``GLib``,
``GObject``, ``SpiceClientGLib``, ``SpiceClientGtk``, ``paramiko``,
``libvirt`` et ``subprocess`` y sont remplacés par des doublures.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
sys.path.insert(0, os.path.join(RACINE, "plugins"))

import test_gcm  # noqa: E402, F401  (pose les doublures gi/Gtk avant l'import)


def _charger_copie_fraiche() -> Any:
    """Charge ``plugins/plugin_spice.py`` sous un nom distinct de ``plugin_spice``."""
    spec = importlib.util.spec_from_file_location(
        "plugin_spice_palier11", os.path.join(RACINE, "plugins", "plugin_spice.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sp = _charger_copie_fraiche()


# ---------------------------------------------------------------------------
# Doublures GTK à état
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
        self.signaux: dict[str, list[Any]] = {}
        self.detruit = False

    def __getattr__(self, nom: str) -> Any:
        """Toute autre méthode GTK est acceptée et ne fait rien."""
        if nom.startswith("__"):
            raise AttributeError(nom)
        return lambda *_a, **_k: None

    def connect(self, signal: str, rappel: Any, *args: Any) -> None:
        """Mémorise un gestionnaire de signal."""
        self.signaux.setdefault(signal, []).append(rappel)

    def emettre(self, signal: str) -> None:
        """Appelle les gestionnaires d'un signal."""
        for rappel in self.signaux.get(signal, []):
            rappel(self)

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

    def set_visible(self, visible: bool) -> None:
        """Affiche ou masque."""
        self.visible = visible

    def get_visible(self) -> bool:
        """Visibilité."""
        return self.visible

    def set_sensitive(self, sensible: bool) -> None:
        """Active ou grise."""
        self.sensible = sensible

    def get_sensitive(self) -> bool:
        """Sensibilité."""
        return self.sensible

    def pack_start(self, enfant: Any, *_a: Any) -> None:
        """Ajoute un enfant."""
        self.enfants.append(enfant)

    def add(self, enfant: Any) -> None:
        """Ajoute un enfant."""
        self.enfants.append(enfant)

    def attach(self, enfant: Any, *_a: Any) -> None:
        """Ajoute un enfant dans une grille."""
        self.enfants.append(enfant)

    def remove(self, enfant: Any) -> None:
        """Retire un enfant."""
        self.enfants.remove(enfant)

    def destroy(self) -> None:
        """Détruit le widget."""
        self.detruit = True

    def get_content_area(self) -> Widget:
        """Zone de contenu d'un dialogue (créée à la demande)."""
        if "_zone" not in self.__dict__:
            self.__dict__["_zone"] = Widget()
        return self.__dict__["_zone"]

    def get_style_context(self) -> Any:
        """Contexte de style sans effet."""
        return SimpleNamespace(add_class=lambda *_a: None)


class RadioButton(Widget):
    """Bouton radio : un seul actif par groupe, ``toggled`` émis aux changements."""

    def __init__(self, label: str = "", groupe: list[RadioButton] | None = None) -> None:
        """Rejoint (ou crée) un groupe."""
        super().__init__(label=label)
        self.groupe = groupe if groupe is not None else []
        self.groupe.append(self)
        self.actif = len(self.groupe) == 1

    @classmethod
    def new_with_label_from_widget(cls, membre: RadioButton | None, label: str) -> RadioButton:
        """Crée un bouton dans le groupe de ``membre``."""
        return cls(label, membre.groupe if membre is not None else None)

    def set_active(self, actif: bool) -> None:
        """Active ce bouton et désactive les autres du groupe."""
        if not actif:
            return
        changes = [b for b in self.groupe if b.actif != (b is self)]
        for bouton in self.groupe:
            bouton.actif = bouton is self
        for bouton in changes:
            bouton.emettre("toggled")


FAUX_GTK = SimpleNamespace(
    Box=Widget,
    Label=Widget,
    Button=Widget,
    Entry=Widget,
    Separator=Widget,
    Grid=Widget,
    CheckButton=Widget,
    Dialog=Widget,
    RadioButton=RadioButton,
    Orientation=SimpleNamespace(VERTICAL="v", HORIZONTAL="h"),
    Align=SimpleNamespace(START="start"),
    DialogFlags=SimpleNamespace(MODAL="modal"),
    ResponseType=SimpleNamespace(OK=-5, CANCEL=-6, CLOSE=-7),
)


@pytest.fixture
def gtk(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Remplace ``Gtk``, ``GLib``, ``GObject`` et ``Gdk`` dans la copie du module."""
    connexions: list[tuple[Any, str, Any]] = []
    monkeypatch.setattr(sp, "Gtk", FAUX_GTK)
    monkeypatch.setattr(sp, "GLib", SimpleNamespace(idle_add=lambda f, *a: f(*a)))
    monkeypatch.setattr(
        sp,
        "GObject",
        SimpleNamespace(
            GObject=SimpleNamespace(connect=lambda obj, sig, cb: connexions.append((obj, sig, cb)))
        ),
    )
    monkeypatch.setattr(sp, "Gdk", SimpleNamespace(keyval_from_name=lambda nom: f"kv:{nom}"))
    return SimpleNamespace(connexions=connexions)


def _hote(**champs: Any) -> SimpleNamespace:
    """Hôte SPICE minimal, champs surchargeables."""
    base: dict[str, Any] = {
        "name": "vm1",
        "host": "10.0.0.5",
        "port": "5930",
        "user": "root",
        "description": "",
        "extra_params": "",
        "spice_mode": "uri",
    }
    base.update(champs)
    return SimpleNamespace(**base)


def _onglet(hote: Any, mot_de_passe: str = "") -> Any:
    """``SpiceTab`` sans ``__init__``, widgets remplacés par des doublures."""
    onglet = object.__new__(sp.SpiceTab)
    onglet.host = hote
    onglet._get_password = lambda: mot_de_passe
    onglet._proc = None
    onglet._spice_session = None
    onglet._spice_display = None
    onglet._spice_audio = None
    onglet._iso_loop_device = None
    onglet._lbl_status = Widget()
    onglet._btn_connect = Widget()
    onglet._btn_disconnect = Widget()
    onglet._btn_usb = Widget()
    onglet._display_box = Widget()
    return onglet


# ---------------------------------------------------------------------------
# parse_spice_hosts, _bool_from_str
# ---------------------------------------------------------------------------


class TestParseSpiceHosts:
    """Paires ``(host, spice_px_node)`` lues dans gcm.conf."""

    def test_fichier_absent(self, tmp_path: Path) -> None:
        """Un gcm.conf absent lève ``FileNotFoundError``."""
        with pytest.raises(FileNotFoundError):
            sp.parse_spice_hosts(tmp_path / "gcm.conf")

    def test_filtre_et_dedoublonne(self, tmp_path: Path) -> None:
        """Seules les sections ``host`` SPICE complètes sont gardées, sans doublon."""
        conf = tmp_path / "gcm.conf"
        conf.write_text(
            "[options]\nprotocol = spice\nhost = 1.1.1.1\nspice_px_node = x\n"
            "[host a]\nprotocol = spice\nhost = 10.0.0.1\nspice_px_node = pve1\n"
            "[host b]\nprotocol = ssh\nhost = 10.0.0.2\nspice_px_node = pve2\n"
            "[host c]\nprotocol = spice\nhost = 10.0.0.3\nspice_px_node =\n"
            "[host d]\nprotocol = spice\nhost =  \nspice_px_node = pve4\n"
            "[host e]\nprotocol = spice\nhost = 10.0.0.1\nspice_px_node = pve1\n"
            "[host f]\nprotocol = spice\nhost = 10.0.0.6\nspice_px_node = pve6\n",
            encoding="utf-8",
        )
        assert sp.parse_spice_hosts(conf) == [("10.0.0.1", "pve1"), ("10.0.0.6", "pve6")]


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
    assert sp._bool_from_str(valeur) is attendu


# ---------------------------------------------------------------------------
# HostsUpdater et dialogue de mot de passe
# ---------------------------------------------------------------------------


class TestHostsUpdater:
    """Ajout des nœuds SPICE manquants dans /etc/hosts."""

    def test_noms_existants(self) -> None:
        """Noms d'hôtes déjà présents, en minuscules, commentaires ignorés."""
        texte = "127.0.0.1 localhost Poste\n# 10.0.0.9 cache\n\n10.0.0.1   PVE1 pve1.lan\n"
        assert sp.HostsUpdater()._existing_nodes(texte) == {
            "localhost",
            "poste",
            "pve1",
            "pve1.lan",
        }

    def test_lignes_a_ajouter(self) -> None:
        """Les nœuds déjà connus sont sautés, les autres portent le marqueur."""
        lignes = sp.HostsUpdater()._build_lines(
            [("10.0.0.1", "PVE1"), ("10.0.0.2", "pve2")], {"pve1"}
        )
        assert lignes == [f"{'10.0.0.2':<20}pve2  # gcm-spice"]

    def _maj(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, contenu: str
    ) -> tuple[Any, Path]:
        hosts = tmp_path / "hosts"
        hosts.write_text(contenu, encoding="utf-8")
        maj = sp.HostsUpdater()
        monkeypatch.setattr(maj, "HOSTS_FILE", str(hosts))
        return maj, hosts

    def test_deja_a_jour(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Rien à ajouter : 0, fichier inchangé."""
        maj, hosts = self._maj(monkeypatch, tmp_path, "10.0.0.1 pve1\n")
        assert maj.update([("10.0.0.1", "pve1")]) == 0
        assert hosts.read_text() == "10.0.0.1 pve1\n"

    def test_ecriture_directe_en_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """En root, écriture directe ; un saut de ligne est ajouté si besoin."""
        maj, hosts = self._maj(monkeypatch, tmp_path, "127.0.0.1 localhost")
        monkeypatch.setattr(sp.os, "geteuid", lambda: 0)
        assert maj.update([("10.0.0.2", "pve2")]) == 1
        assert hosts.read_text() == f"127.0.0.1 localhost\n{'10.0.0.2':<20}pve2  # gcm-spice\n"

    def test_sans_root_passe_par_sudo(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Sans root, le contenu complet part vers ``_write_via_sudo``."""
        maj, _hosts = self._maj(monkeypatch, tmp_path, "127.0.0.1 localhost\n")
        monkeypatch.setattr(sp.os, "geteuid", lambda: 1000)
        recus: list[str] = []
        monkeypatch.setattr(maj, "_write_via_sudo", recus.append)
        assert maj.update([("10.0.0.2", "pve2")]) == 1
        assert recus == [f"127.0.0.1 localhost\n{'10.0.0.2':<20}pve2  # gcm-spice\n"]

    def _sudo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mots_de_passe: list[str | None],
        codes: dict[str, list[int]],
    ) -> tuple[list[str], list[tuple[list[str], str]]]:
        invites: list[str] = []
        appels: list[tuple[list[str], str]] = []

        def demander(invite: str) -> str | None:
            invites.append(invite)
            return mots_de_passe.pop(0)

        def run(cmd: list[str], input: str, **_k: Any) -> SimpleNamespace:  # noqa: A002
            appels.append((cmd, input))
            return SimpleNamespace(returncode=codes[cmd[2]].pop(0), stderr="refusé\n")

        monkeypatch.setattr(sp, "_ask_password_dialog", demander)
        monkeypatch.setattr(sp.subprocess, "run", run)
        return invites, appels

    def test_sudo_reussi_apres_une_erreur(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un mauvais mot de passe, puis le bon : ``tee`` reçoit mot de passe et contenu."""
        invites, appels = self._sudo(monkeypatch, ["faux", "bon"], {"-v": [1, 0], "tee": [0]})
        sp.HostsUpdater()._write_via_sudo("contenu\n")
        assert invites[0].startswith("Mot de passe sudo requis")
        assert invites[1] == "Mot de passe incorrect — tentative 2/3 :"
        assert appels[-1] == (["sudo", "-S", "tee", "/etc/hosts"], "bon\ncontenu\n")

    def test_sudo_annule(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Annuler le dialogue lève ``PermissionError``."""
        self._sudo(monkeypatch, [None], {})
        with pytest.raises(PermissionError, match="annulée"):
            sp.HostsUpdater()._write_via_sudo("x")

    def test_tee_en_echec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``tee`` refusé : ``PermissionError`` avec le message de sudo."""
        self._sudo(monkeypatch, ["bon"], {"-v": [0], "tee": [1]})
        with pytest.raises(PermissionError, match="sudo tee a échoué : refusé"):
            sp.HostsUpdater()._write_via_sudo("x")

    def test_trois_echecs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Après trois mots de passe refusés : ``PermissionError``."""
        invites, _appels = self._sudo(monkeypatch, ["a", "b", "c"], {"-v": [1, 1, 1]})
        with pytest.raises(PermissionError, match="après 3 tentatives"):
            sp.HostsUpdater()._write_via_sudo("x")
        assert len(invites) == 3


class TestDialogueMotDePasse:
    """``_ask_password_dialog`` : saisie masquée, OK ou Annuler."""

    @pytest.mark.parametrize(("reponse", "attendu"), [(-5, "s3cret"), (-6, None)])
    def test_reponse(
        self,
        monkeypatch: pytest.MonkeyPatch,
        gtk: SimpleNamespace,
        reponse: int,
        attendu: str | None,
    ) -> None:
        """OK rend la saisie, Annuler rend ``None`` ; le dialogue est détruit."""
        dialogues: list[Widget] = []

        def lancer(dialogue: Widget) -> int:
            dialogues.append(dialogue)
            libelle, champ = dialogue.get_content_area().enfants
            assert libelle.texte == "Invite ?"
            champ.set_text("s3cret")
            return reponse

        monkeypatch.setattr(sp, "run_dialog_sync", lancer)
        assert sp._ask_password_dialog("Invite ?") == attendu
        assert dialogues[0].detruit is True


# ---------------------------------------------------------------------------
# SpiceTab : URI, ticket Proxmox, paramètres libvirt
# ---------------------------------------------------------------------------


class TestUriSpice:
    """URI ``spice://`` du mode natif."""

    def test_complete(self) -> None:
        """Mot de passe et port TLS ajoutés."""
        onglet = _onglet(_hote(port="5931", spice_tls_port=" 5932 "), "pw")
        assert onglet._build_spice_uri() == "spice://10.0.0.5?port=5931&password=pw&tls-port=5932"

    def test_minimale(self) -> None:
        """Sans mot de passe ni TLS ; port par défaut 5930."""
        assert _onglet(_hote(port=""))._build_spice_uri() == "spice://10.0.0.5?port=5930"


class FauxParamiko:
    """Doublure du module ``paramiko`` : sortie de ``pvesh`` imposée."""

    def __init__(self, sortie: str = "", erreur: Exception | None = None) -> None:
        """Initialise la sortie rendue ou l'erreur levée à la connexion."""
        self.sortie = sortie
        self.erreur = erreur
        self.connexions: list[tuple[str, int, str]] = []
        self.commandes: list[str] = []
        self.AutoAddPolicy = lambda: "auto"
        faux = self

        class SSHClient:
            def set_missing_host_key_policy(self, _p: object) -> None:
                """Politique de clé d'hôte ignorée."""

            def connect(self, hote: str, port: int, username: str, timeout: int) -> None:
                """Connexion simulée."""
                if faux.erreur:
                    raise faux.erreur
                faux.connexions.append((hote, port, username))

            def exec_command(self, cmd: str) -> tuple[None, Any, None]:
                """Exécution simulée."""
                faux.commandes.append(cmd)
                return None, SimpleNamespace(read=lambda: faux.sortie.encode()), None

            def close(self) -> None:
                """Fermeture simulée."""

        self.SSHClient = SSHClient


TICKET = {
    "host": "pvespiceproxy:abc",
    "tls-port": 61000,
    "password": "ticket-secret",
    "proxy": "http://pve1:3128",
    "host-subject": "OU=PVE,CN=pve1",
    "ca": "-----BEGIN CERTIFICATE-----\\nMIIB\\n-----END CERTIFICATE-----",
}


class TestTicketProxmox:
    """``_fetch_proxmox_ticket`` : pvesh par SSH."""

    def test_noeud_ou_vmid_manquant(self) -> None:
        """Sans nœud ni VMID : ``None`` et message d'erreur."""
        onglet = _onglet(_hote(spice_px_node="pve1", spice_px_vmid=""))
        assert onglet._fetch_proxmox_ticket() is None
        assert onglet._lbl_status.texte == "Error: Proxmox node and VMID required"

    @pytest.mark.parametrize(
        ("port", "port_ssh"), [("5930", 22), ("5900", 22), ("2222", 2222), ("x", 22)]
    )
    def test_ticket_decode(
        self, monkeypatch: pytest.MonkeyPatch, port: str, port_ssh: int
    ) -> None:
        """Ticket JSON rendu ; port SSH 22 sauf port explicite hors SPICE/VNC."""
        faux = FauxParamiko(json.dumps(TICKET))
        monkeypatch.setitem(sys.modules, "paramiko", faux)
        onglet = _onglet(_hote(port=port, user="", spice_px_node="pve1", spice_px_vmid="101"))
        assert onglet._fetch_proxmox_ticket() == TICKET
        assert faux.connexions == [("10.0.0.5", port_ssh, "root")]
        assert faux.commandes == [
            "pvesh create /nodes/pve1/qemu/101/spiceproxy --output-format json"
        ]

    def test_erreur_ssh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Erreur SSH : ``None`` et message."""
        monkeypatch.setitem(sys.modules, "paramiko", FauxParamiko(erreur=OSError("refusé")))
        onglet = _onglet(_hote(spice_px_node="pve1", spice_px_vmid="101"))
        assert onglet._fetch_proxmox_ticket() is None
        assert onglet._lbl_status.texte == "SSH error: refusé"

    def test_ticket_illisible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sortie non JSON : ``None``, début de la sortie affiché."""
        monkeypatch.setitem(sys.modules, "paramiko", FauxParamiko("403 Permission check failed"))
        onglet = _onglet(_hote(spice_px_node="pve1", spice_px_vmid="101"))
        assert onglet._fetch_proxmox_ticket() is None
        assert onglet._lbl_status.texte == "SPICE ticket error: 403 Permission check failed"


class ErreurLibvirt(Exception):
    """Doublure de ``libvirt.libvirtError``."""


def _faux_libvirt(
    xml: str = "", erreur_open: bool = False, erreur_vm: bool = False, erreur_close: bool = False
) -> Any:
    """Doublure du module ``libvirt``."""

    def fermer() -> None:
        if erreur_close:
            raise RuntimeError("déjà fermée")

    def chercher(_nom: str) -> Any:
        if erreur_vm:
            raise ErreurLibvirt("Domain not found")
        return SimpleNamespace(XMLDesc=lambda _f: xml)

    def ouvrir(_uri: str) -> Any:
        if erreur_open:
            raise ErreurLibvirt("connexion refusée")
        return SimpleNamespace(lookupByName=chercher, close=fermer)

    return SimpleNamespace(open=ouvrir, libvirtError=ErreurLibvirt)


class TestParametresLibvirt:
    """``_fetch_libvirt_spice_params`` : bloc ``<graphics type='spice'>``."""

    def _lire(self, monkeypatch: pytest.MonkeyPatch, **options: Any) -> tuple[Any, Any]:
        monkeypatch.setattr(sp, "_libvirt_mod", _faux_libvirt(**options), raising=False)
        onglet = _onglet(
            _hote(spice_libvirt_uri="qemu+ssh://root@kvm/system", spice_vm_name="vm1")
        )
        return onglet._fetch_libvirt_spice_params(), onglet._lbl_status.texte

    @pytest.mark.parametrize(
        ("champs", "message"),
        [
            ({"spice_libvirt_uri": ""}, "Error: libvirt URI required"),
            (
                {"spice_libvirt_uri": "qemu:///system", "spice_vm_name": " "},
                "Error: VM name required",
            ),
        ],
    )
    def test_champs_requis(self, champs: dict[str, str], message: str) -> None:
        """URI et nom de VM obligatoires."""
        onglet = _onglet(_hote(**champs))
        assert onglet._fetch_libvirt_spice_params() is None
        assert onglet._lbl_status.texte == message

    def test_connexion_refusee(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hyperviseur injoignable."""
        assert self._lire(monkeypatch, erreur_open=True) == (
            None,
            "libvirt error: connexion refusée",
        )

    def test_vm_introuvable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VM inconnue."""
        assert self._lire(monkeypatch, erreur_vm=True) == (None, "VM introuvable : vm1")

    def test_fermeture_en_echec_ignoree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Après lecture du XML, une fermeture qui échoue (``finally``) est ignorée."""
        xml = "<domain><devices><graphics type='spice' port='5901'/></devices></domain>"
        resultat, _message = self._lire(monkeypatch, xml=xml, erreur_close=True)
        assert resultat is not None and resultat["port"] == "5901"

    def test_xml_illisible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """XML mal formé."""
        resultat, message = self._lire(monkeypatch, xml="<domain>")
        assert resultat is None
        assert message.startswith("libvirt XML parsing error:")

    def test_sans_console_spice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pas de bloc SPICE (VNC seul)."""
        xml = "<domain><devices><graphics type='vnc' port='5900'/></devices></domain>"
        assert self._lire(monkeypatch, xml=xml) == (None, "No SPICE console for vm1")

    def test_spice_desactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ports à -1 : SPICE désactivé."""
        xml = "<domain><devices><graphics type='spice'/></devices></domain>"
        assert self._lire(monkeypatch, xml=xml) == (None, "SPICE disabled for vm1")

    def test_element_listen_prioritaire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``<listen type='address'>`` l'emporte sur l'attribut ``listen``."""
        xml = (
            "<domain><devices><graphics type='spice' port='5901' tlsPort='-1' passwd='pw' "
            "listen='0.0.0.0'><listen type='address' address='192.168.1.10'/></graphics>"
            "</devices></domain>"
        )
        resultat, _message = self._lire(monkeypatch, xml=xml)
        assert resultat == {
            "host": "192.168.1.10",
            "port": "5901",
            "tls-port": None,
            "password": "pw",
        }

    def test_attribut_listen_et_tls_seul(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sans élément ``<listen>`` : attribut ``listen`` ; TLS seul."""
        xml = (
            "<domain><devices><graphics type='spice' tlsPort='5902' listen=''/></devices></domain>"
        )
        resultat, _message = self._lire(monkeypatch, xml=xml)
        assert resultat == {"host": "127.0.0.1", "port": None, "tls-port": "5902", "password": ""}


# ---------------------------------------------------------------------------
# SpiceTab._build_cmd (mode sous-processus)
# ---------------------------------------------------------------------------


@pytest.fixture
def sans_hosts(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Neutralise gcm.conf et /etc/hosts ; ``HostsUpdater.update`` lève ``PermissionError``."""
    paires: list[Any] = []
    monkeypatch.setattr(sp, "parse_spice_hosts", lambda _p: [("10.0.0.1", "pve1")])

    class MajRefusee:
        def update(self, p: Any) -> int:
            """Refus d'élévation."""
            paires.append(p)
            raise PermissionError("refusée")

    monkeypatch.setattr(sp, "HostsUpdater", MajRefusee)
    return paires


class TestCommandeSousProcessus:
    """Commande remote-viewer des trois modes."""

    def test_uri_complete(self, sans_hosts: list[Any]) -> None:
        """URI avec mot de passe, TLS, CA et options supplémentaires."""
        onglet = _onglet(_hote(spice_tls_port="5932", spice_ca_cert="/ca.pem"), "pw")
        onglet._entry_opts = Widget()
        onglet._entry_opts.set_text("--full-screen --title 'Ma VM'")
        assert onglet._build_cmd() == [
            sp.SPICE_BIN,
            "spice://10.0.0.5?port=5930&password=pw",
            "--spice-tls-port=5932",
            "--spice-ca-file=/ca.pem",
            "--full-screen",
            "--title",
            "Ma VM",
        ]
        assert sans_hosts == [[("10.0.0.1", "pve1")]]

    def test_uri_minimale(self, sans_hosts: list[Any]) -> None:
        """Sans mot de passe ni champ d'options (mode natif)."""
        assert _onglet(_hote(spice_mode=None))._build_cmd() == [
            sp.SPICE_BIN,
            "spice://10.0.0.5?port=5930",
        ]

    def test_libvirt(self, sans_hosts: list[Any]) -> None:
        """``--connect`` + nom de VM + options."""
        onglet = _onglet(
            _hote(spice_mode="libvirt", spice_libvirt_uri="qemu:///system", spice_vm_name="vm1")
        )
        onglet._entry_opts = Widget()
        onglet._entry_opts.set_text("--attach")
        assert onglet._build_cmd() == [
            sp.SPICE_BIN,
            "--connect",
            "qemu:///system",
            "vm1",
            "--attach",
        ]

    def test_libvirt_sans_vm(self, sans_hosts: list[Any]) -> None:
        """Sans nom de VM : virt-viewer choisit."""
        onglet = _onglet(_hote(spice_mode="libvirt", spice_libvirt_uri="qemu:///system"))
        assert onglet._build_cmd() == [sp.SPICE_BIN, "--connect", "qemu:///system"]

    def test_libvirt_sans_uri(self, sans_hosts: list[Any]) -> None:
        """URI libvirt obligatoire."""
        onglet = _onglet(_hote(spice_mode="libvirt"))
        assert onglet._build_cmd() is None
        assert onglet._lbl_status.texte == "Error: libvirt URI required"

    def test_proxmox_sans_vmid(self, sans_hosts: list[Any]) -> None:
        """Nœud et VMID obligatoires."""
        onglet = _onglet(_hote(spice_mode="proxmox", spice_px_node="pve1"))
        assert onglet._build_cmd() is None
        assert onglet._lbl_status.texte == "Error: Proxmox node and VMID required"

    def test_proxmox_fichier_vv(
        self, monkeypatch: pytest.MonkeyPatch, sans_hosts: list[Any]
    ) -> None:
        """Ticket écrit dans un fichier .vv temporaire passé à remote-viewer."""
        faux = FauxParamiko(
            json.dumps({k: v for k, v in TICKET.items() if k not in ("proxy", "ca")})
        )
        monkeypatch.setitem(sys.modules, "paramiko", faux)
        onglet = _onglet(
            _hote(spice_mode="proxmox", port="2222", spice_px_node="pve1", spice_px_vmid="101")
        )
        cmd = onglet._build_cmd()
        assert cmd[0] == sp.SPICE_BIN
        vv = Path(cmd[1])
        try:
            assert vv.suffix == ".vv"
            assert vv.read_text().splitlines() == [
                "[virt-viewer]",
                "type=spice",
                "host=pvespiceproxy:abc",
                "tls-port=61000",
                "password=ticket-secret",
                "proxy=",
                "host-subject=OU=PVE,CN=pve1",
                "ca=",
                "delete-this-file=1",
            ]
        finally:
            vv.unlink()
        assert faux.connexions == [("10.0.0.5", 2222, "root")]

    def test_proxmox_erreur_ssh(
        self, monkeypatch: pytest.MonkeyPatch, sans_hosts: list[Any]
    ) -> None:
        """Erreur SSH : ``None``."""
        monkeypatch.setitem(sys.modules, "paramiko", FauxParamiko(erreur=TimeoutError("délai")))
        onglet = _onglet(_hote(spice_mode="proxmox", spice_px_node="pve1", spice_px_vmid="101"))
        assert onglet._build_cmd() is None
        assert onglet._lbl_status.texte == "SSH error: délai"

    def test_proxmox_ticket_illisible(
        self, monkeypatch: pytest.MonkeyPatch, sans_hosts: list[Any]
    ) -> None:
        """Sortie non JSON : ``None``."""
        monkeypatch.setitem(sys.modules, "paramiko", FauxParamiko("pas du json"))
        onglet = _onglet(_hote(spice_mode="proxmox", spice_px_node="pve1", spice_px_vmid="101"))
        assert onglet._build_cmd() is None
        assert onglet._lbl_status.texte == "SPICE ticket error: pas du json"


# ---------------------------------------------------------------------------
# Connexion / déconnexion en mode sous-processus
# ---------------------------------------------------------------------------


class FauxProc:
    """Doublure de ``subprocess.Popen``."""

    def __init__(self, en_cours: bool = True, code: int = 0) -> None:
        """Initialise l'état et le code de sortie."""
        self.en_cours = en_cours
        self.code = code
        self.termine = False

    def poll(self) -> int | None:
        """``None`` tant que le processus tourne."""
        return None if self.en_cours else self.code

    def wait(self) -> int:
        """Code de sortie."""
        return self.code

    def terminate(self) -> None:
        """Arrêt demandé."""
        self.termine = True


@pytest.fixture
def fils(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """``threading.Thread`` remplacé : cibles gardées, rien n'est lancé."""
    cibles: list[Any] = []

    class FauxThread:
        def __init__(self, target: Any, daemon: bool) -> None:
            self.target = target

        def start(self) -> None:
            """Mémorise la cible."""
            cibles.append(self.target)

    monkeypatch.setattr(sp, "threading", SimpleNamespace(Thread=FauxThread))
    return cibles


class TestConnexionSousProcessus:
    """``_on_connect`` / ``_on_disconnect`` / ``_wait_proc`` sans spice-gtk."""

    def _onglet(self, monkeypatch: pytest.MonkeyPatch, cmd: Any) -> Any:
        monkeypatch.setattr(sp, "_SPICEGTK_OK", False)
        onglet = _onglet(_hote())
        onglet._entry_opts = Widget()
        onglet._entry_opts.set_text("  --full-screen ")
        monkeypatch.setattr(onglet, "_build_cmd", lambda: cmd)
        return onglet

    def test_lancement(
        self, monkeypatch: pytest.MonkeyPatch, gtk: SimpleNamespace, fils: list[Any]
    ) -> None:
        """Viewer lancé, boutons basculés, attente dans un fil."""
        onglet = self._onglet(monkeypatch, ["remote-viewer", "spice://x"])
        lances: list[list[str]] = []
        monkeypatch.setattr(
            sp.subprocess, "Popen", lambda cmd, **_k: lances.append(cmd) or FauxProc()
        )
        onglet.connect_spice()
        assert lances == [["remote-viewer", "spice://x"]]
        assert onglet.host.extra_params == "--full-screen"
        assert onglet._lbl_status.texte == "Connecting…"
        assert (onglet._btn_connect.sensible, onglet._btn_disconnect.sensible) == (False, True)
        assert fils == [onglet._wait_proc]

    def test_deja_en_cours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Processus vivant : rien n'est relancé."""
        onglet = self._onglet(monkeypatch, None)
        onglet._proc = FauxProc()
        onglet._on_connect(None)
        assert onglet._lbl_status.texte == ""

    def test_commande_invalide(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``_build_cmd`` rend ``None`` : pas de lancement."""
        onglet = self._onglet(monkeypatch, None)
        onglet._proc = FauxProc(en_cours=False)
        onglet._on_connect(None)
        assert onglet._btn_connect.sensible is True

    @pytest.mark.parametrize(
        ("erreur", "message"),
        [
            (FileNotFoundError("remote-viewer"), "remote-viewer not found"),
            (PermissionError("x"), "Error launching viewer"),
        ],
    )
    def test_lancement_en_echec(
        self, monkeypatch: pytest.MonkeyPatch, erreur: Exception, message: str
    ) -> None:
        """Binaire absent ou erreur de lancement."""
        onglet = self._onglet(monkeypatch, ["remote-viewer"])

        def popen(*_a: Any, **_k: Any) -> None:
            raise erreur

        monkeypatch.setattr(sp.subprocess, "Popen", popen)
        onglet._on_connect(None)
        assert onglet._lbl_status.texte == message

    @pytest.mark.parametrize(("code", "message"), [(0, "Session ended"), (3, "Ended (code 3)")])
    def test_attente(self, gtk: SimpleNamespace, code: int, message: str) -> None:
        """Fin du viewer : statut, boutons, processus oublié."""
        onglet = _onglet(_hote())
        onglet._proc = FauxProc(code=code)
        onglet._btn_connect.set_sensitive(False)
        onglet._wait_proc()
        assert onglet._lbl_status.texte == message
        assert (onglet._btn_connect.sensible, onglet._btn_disconnect.sensible) == (True, False)
        assert onglet._proc is None

    def test_attente_sans_processus(self, gtk: SimpleNamespace) -> None:
        """Sans processus : boutons seulement."""
        onglet = _onglet(_hote())
        onglet._wait_proc()
        assert onglet._lbl_status.texte == ""
        assert onglet._btn_connect.sensible is True

    @pytest.mark.parametrize("en_cours", [True, False])
    def test_deconnexion(self, monkeypatch: pytest.MonkeyPatch, en_cours: bool) -> None:
        """Le viewer vivant est arrêté ; statut et boutons remis."""
        onglet = self._onglet(monkeypatch, None)
        onglet._proc = FauxProc(en_cours=en_cours)
        onglet._on_disconnect(None)
        assert onglet._proc.termine is en_cours
        assert onglet._lbl_status.texte == "Disconnect"
        assert onglet._btn_disconnect.sensible is False

    def test_aiguillage_natif(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Avec spice-gtk, connexion et déconnexion passent par le mode natif."""
        monkeypatch.setattr(sp, "_SPICEGTK_OK", True)
        onglet = _onglet(_hote())
        appels: list[str] = []
        monkeypatch.setattr(onglet, "_connect_native", lambda: appels.append("connect"))
        monkeypatch.setattr(onglet, "_disconnect_native", lambda: appels.append("disconnect"))
        onglet._on_connect(None)
        onglet._on_disconnect(None)
        assert appels == ["connect", "disconnect"]


# ---------------------------------------------------------------------------
# Mode natif spice-gtk
# ---------------------------------------------------------------------------


class Objet:
    """Objet GObject simulé : propriétés gardées, appels notés."""

    def __init__(self, *args: Any) -> None:
        """Initialise les propriétés."""
        self.args = args
        self.props: dict[str, Any] = {}
        self.appels: list[Any] = []
        self.connecte = False
        self.detruit = False

    def set_property(self, nom: str, valeur: Any) -> None:
        """Mémorise une propriété."""
        self.props[nom] = valeur

    def connect(self) -> None:
        """``SpiceSession.connect`` : connexion au serveur."""
        self.connecte = True

    def disconnect(self) -> None:
        """Déconnexion."""
        self.appels.append("disconnect")

    def send_keys(self, touches: list[str], evenement: str) -> None:
        """Touches envoyées."""
        self.appels.append((touches, evenement))

    def destroy(self) -> None:
        """Destruction."""
        self.detruit = True

    def __getattr__(self, nom: str) -> Any:
        """Autres méthodes sans effet."""
        if nom.startswith("__"):
            raise AttributeError(nom)
        return lambda *_a, **_k: None


@pytest.fixture
def spice_gtk(monkeypatch: pytest.MonkeyPatch, gtk: SimpleNamespace) -> SimpleNamespace:
    """Doublures ``SpiceClientGLib`` / ``SpiceClientGtk`` dans la copie du module."""
    etat = SimpleNamespace(sessions=[], gtk_sessions=[], audios=[], displays=[], usb=[])

    class Session(Objet):
        props = SimpleNamespace(ca_file=None)

        def __init__(self) -> None:
            super().__init__()
            etat.sessions.append(self)

    def gtk_session(session: Any) -> Objet:
        objet = Objet(session)
        etat.gtk_sessions.append(objet)
        return objet

    def audio(session: Any, _ctx: Any) -> Objet:
        objet = Objet(session)
        etat.audios.append(objet)
        return objet

    def display(session: Any, canal: int) -> Objet:
        objet = Objet(session, canal)
        etat.displays.append(objet)
        return objet

    def usb(session: Any, _fmt: Any) -> Widget:
        widget = Widget()
        etat.usb.append((session, widget))
        return widget

    evenements = SimpleNamespace(
        OPENED="opened",
        CLOSED="closed",
        ERROR_CONNECT="e1",
        ERROR_AUTH="e2",
        ERROR_IO="e3",
        ERROR_LINK="e4",
        ERROR_TLS="e5",
    )
    monkeypatch.setattr(
        sp,
        "SpiceClientGLib",
        SimpleNamespace(
            Session=Session, Audio=SimpleNamespace(get=audio), ChannelEvent=evenements
        ),
        raising=False,
    )
    monkeypatch.setattr(
        sp,
        "SpiceClientGtk",
        SimpleNamespace(
            GtkSession=SimpleNamespace(get=gtk_session),
            Display=SimpleNamespace(new=display),
            DisplayKeyEvent=SimpleNamespace(PRESS="press", RELEASE="release"),
            UsbDeviceWidget=SimpleNamespace(new=usb),
        ),
        raising=False,
    )
    monkeypatch.setattr(sp, "_SPICEGTK_OK", True)
    monkeypatch.setattr(sp, "_LIBVIRT_OK", True)
    return etat


class TestConnexionNative:
    """``_connect_native`` : session spice-gtk des trois modes et options de partage."""

    def test_uri_par_defaut(self, spice_gtk: SimpleNamespace, gtk: SimpleNamespace) -> None:
        """URI, CA, presse-papiers, sans USB/audio/dossier ; ancienne session fermée."""
        onglet = _onglet(_hote(spice_ca_cert="/ca.pem"), "pw")
        ancienne_session, ancien_display = Objet(), Objet()
        onglet._spice_session, onglet._spice_display = ancienne_session, ancien_display
        onglet._display_box.enfants.append(ancien_display)
        onglet._connect_native()
        session = spice_gtk.sessions[0]
        assert ancienne_session.appels == ["disconnect"]
        assert ancien_display.detruit is True
        assert session.props == {
            "uri": "spice://10.0.0.5?port=5930&password=pw",
            "ca-file": "/ca.pem",
            "enable-usbredir": False,
            "enable-audio": False,
        }
        assert spice_gtk.gtk_sessions[0].props == {"auto-clipboard": True}
        assert gtk.connexions == [(session, "channel-new", onglet._on_spice_channel_new)]
        display = spice_gtk.displays[0]
        assert display.args == (session, 0)
        assert display.props == {
            "grab-keyboard": True,
            "grab-mouse": True,
            "disable-inputs": False,
        }
        assert onglet._display_box.enfants == [display]
        assert (onglet._spice_session, onglet._spice_display, onglet._spice_audio) == (
            session,
            display,
            None,
        )
        assert session.connecte is True
        assert onglet._lbl_status.texte == "Connecting…"
        assert onglet._btn_usb.visible is False

    def test_uri_invalide(
        self, spice_gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """URI impossible à construire : pas de connexion ; une déconnexion qui échoue est ignorée."""
        onglet = _onglet(_hote())
        session_cassee = Objet()
        monkeypatch.setattr(
            session_cassee, "disconnect", lambda: (_ for _ in ()).throw(RuntimeError("x"))
        )
        onglet._spice_session = session_cassee
        monkeypatch.setattr(onglet, "_build_spice_uri", lambda: None)
        onglet._connect_native()
        assert onglet._lbl_status.texte == "Configuration SPICE invalide"
        assert spice_gtk.sessions[0].connecte is False
        assert onglet._spice_session is None

    def test_partages(self, spice_gtk: SimpleNamespace, tmp_path: Path) -> None:
        """USB, audio et dossier partagé ; presse-papiers désactivé."""
        hote = _hote(
            spice_share_clipboard=False,
            spice_share_usb=True,
            spice_share_audio=True,
            spice_share_folder=True,
            spice_shared_folder_path=f" {tmp_path} ",
        )
        onglet = _onglet(hote)
        onglet._connect_native()
        session = spice_gtk.sessions[0]
        assert session.props["enable-usbredir"] is True
        assert session.props["enable-audio"] is True
        assert session.props["shared-dir"] == str(tmp_path)
        assert session.props["share-dir-ro"] is False
        assert spice_gtk.gtk_sessions[0].props == {"auto-clipboard": False}
        assert onglet._spice_audio is spice_gtk.audios[0]
        assert onglet._btn_usb.visible is True

    @pytest.mark.parametrize("montage", [("/run/media/iso", "/dev/loop7"), None])
    def test_iso_partagee(
        self,
        spice_gtk: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        montage: tuple[str, str] | None,
    ) -> None:
        """Une ISO est montée puis partagée ; montage impossible signalé."""
        monkeypatch.setattr(sp, "mount_iso_temp", lambda _p: montage)
        onglet = _onglet(
            _hote(spice_share_folder=True, spice_shared_folder_path="/iso/Debian.ISO")
        )
        onglet._connect_native()
        props = spice_gtk.sessions[0].props
        if montage:
            assert props["shared-dir"] == "/run/media/iso"
            assert onglet._iso_loop_device == "/dev/loop7"
        else:
            assert "shared-dir" not in props
            assert onglet._iso_loop_device is None
            assert spice_gtk.sessions[0].connecte is True

    def test_proxmox(
        self, spice_gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, sans_hosts: list[Any]
    ) -> None:
        """Ticket Proxmox : hôte, port TLS, mot de passe, proxy, sujet, CA en PEM."""
        onglet = _onglet(_hote(spice_mode="proxmox"))
        monkeypatch.setattr(onglet, "_fetch_proxmox_ticket", lambda: dict(TICKET))
        onglet._connect_native()
        props = spice_gtk.sessions[0].props
        assert props["host"] == "pvespiceproxy:abc"
        assert props["tls-port"] == "61000"
        assert props["password"] == "ticket-secret"
        assert props["proxy"] == "http://pve1:3128"
        assert props["cert-subject"] == "OU=PVE,CN=pve1"
        assert props["ca"] == b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
        assert sans_hosts == [[("10.0.0.1", "pve1")]]

    def test_proxmox_ticket_minimal(
        self, spice_gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, sans_hosts: list[Any]
    ) -> None:
        """Ticket sans proxy, sujet ni CA."""
        onglet = _onglet(_hote(spice_mode="proxmox"))
        monkeypatch.setattr(
            onglet, "_fetch_proxmox_ticket", lambda: {"host": "h", "tls-port": 1, "password": "p"}
        )
        onglet._connect_native()
        assert set(spice_gtk.sessions[0].props) == {
            "host",
            "tls-port",
            "password",
            "enable-usbredir",
            "enable-audio",
        }

    def test_proxmox_sans_ticket(
        self, spice_gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, sans_hosts: list[Any]
    ) -> None:
        """Pas de ticket : pas de connexion."""
        onglet = _onglet(_hote(spice_mode="proxmox"))
        monkeypatch.setattr(onglet, "_fetch_proxmox_ticket", lambda: None)
        onglet._connect_native()
        assert spice_gtk.sessions[0].connecte is False

    @pytest.mark.parametrize(
        ("params", "attendu"),
        [
            (
                {"host": "h", "port": "5901", "tls-port": "5902", "password": "pw"},
                {"host": "h", "port": "5901", "tls-port": "5902", "password": "pw"},
            ),
            ({"host": "h", "port": None, "tls-port": None, "password": ""}, {"host": "h"}),
        ],
    )
    def test_libvirt_natif(
        self,
        spice_gtk: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        params: dict[str, Any],
        attendu: dict[str, Any],
    ) -> None:
        """Paramètres libvirt reportés dans la session (champs vides omis)."""
        onglet = _onglet(_hote(spice_mode="libvirt"))
        monkeypatch.setattr(onglet, "_fetch_libvirt_spice_params", lambda: params)
        onglet._connect_native()
        props = spice_gtk.sessions[0].props
        assert {k: v for k, v in props.items() if not k.startswith("enable")} == attendu

    def test_libvirt_sans_parametres(
        self, spice_gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pas de paramètres libvirt : pas de connexion."""
        onglet = _onglet(_hote(spice_mode="libvirt"))
        monkeypatch.setattr(onglet, "_fetch_libvirt_spice_params", lambda: None)
        onglet._connect_native()
        assert spice_gtk.sessions[0].connecte is False

    def test_libvirt_sans_libvirt_python(
        self, spice_gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, fils: list[Any]
    ) -> None:
        """Sans libvirt-python : repli virt-viewer en sous-processus."""
        monkeypatch.setattr(sp, "_LIBVIRT_OK", False)
        onglet = _onglet(_hote(spice_mode="libvirt"))
        monkeypatch.setattr(
            onglet, "_build_cmd", lambda: ["virt-viewer", "--connect", "qemu:///system"]
        )
        monkeypatch.setattr(sp.subprocess, "Popen", lambda cmd, **_k: FauxProc())
        onglet._connect_native()
        assert spice_gtk.sessions == []
        assert onglet._lbl_status.texte == "Connecting…"
        assert fils == [onglet._wait_proc]

    def test_libvirt_sans_libvirt_python_echecs(
        self, spice_gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repli : commande invalide, puis viewer absent."""
        monkeypatch.setattr(sp, "_LIBVIRT_OK", False)
        onglet = _onglet(_hote(spice_mode="libvirt"))
        monkeypatch.setattr(onglet, "_build_cmd", lambda: None)
        onglet._connect_native()
        assert onglet._lbl_status.texte == ""
        monkeypatch.setattr(onglet, "_build_cmd", lambda: ["virt-viewer"])

        def popen(*_a: Any, **_k: Any) -> None:
            raise FileNotFoundError("virt-viewer")

        monkeypatch.setattr(sp.subprocess, "Popen", popen)
        onglet._connect_native()
        assert onglet._lbl_status.texte == "remote-viewer not found"


class TestNatifDivers:
    """Événements de canal, touches, USB, déconnexion et menu du mode natif."""

    def test_nouveau_canal(self, spice_gtk: SimpleNamespace, gtk: SimpleNamespace) -> None:
        """Chaque canal est suivi via ``GObject.connect`` (pas ``channel.connect``)."""
        onglet = _onglet(_hote())
        canal = Objet()
        onglet._on_spice_channel_new(None, canal)
        assert gtk.connexions == [(canal, "channel-event", onglet._on_spice_channel_event)]

    @pytest.mark.parametrize(
        ("evenement", "statut", "boutons"),
        [
            ("opened", "Connect", (False, True)),
            ("closed", "Session ended", (True, False)),
            ("e2", "Session ended", (True, False)),
            ("autre", "", (True, True)),
        ],
    )
    def test_evenement_de_canal(
        self, spice_gtk: SimpleNamespace, evenement: str, statut: str, boutons: tuple[bool, bool]
    ) -> None:
        """Ouverture, fermeture ou erreur : statut et boutons ; autres ignorés."""
        onglet = _onglet(_hote())
        onglet._on_spice_channel_event(None, evenement)
        assert onglet._lbl_status.texte == statut
        assert (onglet._btn_connect.sensible, onglet._btn_disconnect.sensible) == boutons

    def test_touches_speciales(self, spice_gtk: SimpleNamespace) -> None:
        """Appui puis relâchement ; rien sans écran."""
        onglet = _onglet(_hote())
        onglet._send_special_keys(["Control_L", "Alt_L", "Delete"])
        display = Objet()
        onglet._spice_display = display
        onglet._send_special_keys(["Control_L", "Alt_L", "Delete"])
        touches = ["kv:Control_L", "kv:Alt_L", "kv:Delete"]
        assert display.appels == [(touches, "press"), (touches, "release")]

    def test_peripheriques_usb(
        self, spice_gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dialogue USB seulement avec une session active."""
        lances: list[Widget] = []
        monkeypatch.setattr(sp, "run_dialog_sync", lambda d: lances.append(d) or -7)
        onglet = _onglet(_hote())
        onglet.get_toplevel = lambda: None
        onglet._on_usb_devices_clicked(None)
        assert lances == []
        session = Objet()
        onglet._spice_session = session
        onglet._on_usb_devices_clicked(None)
        assert len(lances) == 1 and lances[0].detruit is True
        assert spice_gtk.usb[0][0] is session
        assert lances[0].get_content_area().enfants == [spice_gtk.usb[0][1]]

    def test_deconnexion_native(
        self, spice_gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ISO démontée, session fermée (même en erreur), écran retiré, viewer arrêté."""
        demontes: list[str] = []
        monkeypatch.setattr(sp, "umount_iso_temp", demontes.append)
        onglet = _onglet(_hote())
        session, display, proc = Objet(), Objet(), FauxProc()
        monkeypatch.setattr(
            session, "disconnect", lambda: (_ for _ in ()).throw(RuntimeError("x"))
        )
        onglet._spice_session, onglet._spice_display, onglet._proc = session, display, proc
        onglet._spice_audio = Objet()
        onglet._iso_loop_device = "/dev/loop7"
        onglet._display_box.enfants.append(display)
        onglet._disconnect_native()
        assert demontes == ["/dev/loop7"]
        assert (
            onglet._spice_session,
            onglet._spice_display,
            onglet._spice_audio,
            onglet._iso_loop_device,
        ) == (None, None, None, None)
        assert display.detruit is True and onglet._display_box.enfants == []
        assert proc.termine is True
        assert onglet._lbl_status.texte == "Disconnect"
        assert (onglet._btn_connect.sensible, onglet._btn_disconnect.sensible) == (True, False)

    def test_deconnexion_native_a_vide(self, spice_gtk: SimpleNamespace) -> None:
        """Rien à fermer : statut et boutons seulement."""
        onglet = _onglet(_hote())
        onglet._disconnect_native()
        assert onglet._lbl_status.texte == "Disconnect"

    def test_menu_contextuel(
        self, spice_gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Menu avec entrée USB si le bouton est visible ; ``None`` sans spice-gtk."""
        menus: list[tuple[Any, ...]] = []
        monkeypatch.setattr(
            sp,
            "build_remote_desktop_context_menu",
            lambda *a, extra_items: menus.append((*a, extra_items)) or "menu",
        )
        onglet = _onglet(_hote())
        usb: list[Any] = []
        monkeypatch.setattr(onglet, "_on_usb_devices_clicked", usb.append)
        assert onglet.build_context_menu() == "menu"
        assert menus[0][:3] == (
            onglet._btn_connect,
            onglet._btn_disconnect,
            onglet._send_special_keys,
        )
        assert menus[0][3] == []
        onglet._btn_usb.set_visible(True)
        onglet.build_context_menu()
        libelle, action = menus[1][3][0]
        assert libelle == "USB devices…"
        action(None)
        assert usb == [None]
        monkeypatch.setattr(sp, "_SPICEGTK_OK", False)
        assert onglet.build_context_menu() is None


class TestConstructionOnglet:
    """``SpiceTab.__init__`` : interface native ou de repli."""

    def test_repli(self, gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
        """Interface de repli : description, options remote-viewer pré-remplies."""
        monkeypatch.setattr(sp, "_SPICEGTK_OK", False)
        onglet = sp.SpiceTab(
            _hote(description="VM de test", extra_params="--full-screen"), lambda: ""
        )
        assert onglet._entry_opts.get_text() == "--full-screen"
        assert onglet._lbl_status.texte == "Waiting…"
        assert onglet._btn_disconnect.sensible is False
        assert onglet._btn_connect.signaux["clicked"] == [onglet._on_connect]

    def test_natif(self, gtk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
        """Interface native : bouton USB visible selon l'hôte, conteneur d'écran prêt."""
        monkeypatch.setattr(sp, "_SPICEGTK_OK", True)
        onglet = sp.SpiceTab(_hote(port="", spice_share_usb=True), lambda: "")
        assert onglet._btn_usb.visible is True
        assert onglet._btn_usb.signaux["clicked"] == [onglet._on_usb_devices_clicked]
        assert isinstance(onglet._display_box, Widget)
        assert onglet._proc is None and onglet._spice_session is None


# ---------------------------------------------------------------------------
# SpicePlugin
# ---------------------------------------------------------------------------


class TestPlugin:
    """Formulaire d'édition et champs ``Host`` du plugin SPICE."""

    def test_identite_et_onglet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Protocole, port, champs par défaut ; ``build_tab`` délègue à ``SpiceTab``."""
        plugin = sp.get_plugin()
        assert (plugin.protocol_id, plugin.default_port) == ("spice", 5930)
        champs = dict(plugin.host_fields())
        assert champs["spice_mode"] == "uri"
        assert champs["spice_share_clipboard"] is True
        assert len(champs) == 12
        monkeypatch.setattr(sp, "SpiceTab", lambda hote, mdp: ("onglet", hote, mdp))
        hote = _hote()
        assert plugin.build_tab(hote, str) == ("onglet", hote, str)

    def test_formulaire_exclusif(self, gtk: SimpleNamespace) -> None:
        """Une seule sous-grille visible, selon le bouton radio actif."""
        plugin = sp.SpicePlugin()
        plugin.build_edit_page()
        grilles = (plugin._grid_uri, plugin._grid_libvirt, plugin._grid_proxmox)
        assert [g.visible for g in grilles] == [True, False, False]
        assert plugin._chk_share_clipboard.actif is True
        plugin._radio_proxmox.set_active(True)
        assert [g.visible for g in grilles] == [False, False, True]
        plugin._radio_libvirt.set_active(True)
        assert [g.visible for g in grilles] == [False, True, False]

    @pytest.mark.parametrize("mode", ["uri", "libvirt", "proxmox"])
    def test_aller_retour(self, gtk: SimpleNamespace, mode: str) -> None:
        """``load_host_fields`` puis ``save_host_fields`` restituent l'hôte."""
        source = _hote(
            spice_mode=mode,
            spice_tls_port="5932",
            spice_ca_cert="/ca.pem",
            spice_libvirt_uri="qemu:///system",
            spice_vm_name="vm1",
            spice_px_node="pve1",
            spice_px_vmid=101,
            spice_share_clipboard="false",
            spice_share_usb="true",
            spice_share_audio="1",
            spice_share_folder="yes",
            spice_shared_folder_path="/partage",
        )
        plugin = sp.SpicePlugin()
        plugin.build_edit_page()
        plugin.load_host_fields(source)
        cible = SimpleNamespace(name="vm1")
        plugin.save_host_fields(cible)
        assert vars(cible) == {
            "name": "vm1",
            "spice_mode": mode,
            "spice_tls_port": "5932",
            "spice_ca_cert": "/ca.pem",
            "spice_libvirt_uri": "qemu:///system",
            "spice_vm_name": "vm1",
            "spice_px_node": "pve1",
            "spice_px_vmid": "101",
            "spice_share_clipboard": False,
            "spice_share_usb": True,
            "spice_share_audio": True,
            "spice_share_folder": True,
            "spice_shared_folder_path": "/partage",
        }

    def test_bascule_ignoree_si_desactive(self, gtk: SimpleNamespace) -> None:
        """``toggled`` d'un bouton qui se désactive ne recalcule rien."""
        plugin = sp.SpicePlugin()
        plugin.build_edit_page()
        plugin._grid_uri.set_visible(False)
        plugin._on_mode_toggled(plugin._radio_libvirt)
        assert plugin._grid_uri.visible is False

    def test_sans_formulaire(self) -> None:
        """Sans ``build_edit_page`` : chargement sans effet, valeurs par défaut sauvées."""
        plugin = sp.SpicePlugin()
        plugin.load_host_fields(_hote(spice_mode="libvirt"))
        plugin.load_host_fields(_hote(spice_mode="proxmox"))
        cible = SimpleNamespace()
        plugin.save_host_fields(cible)
        assert cible.spice_mode == "uri"
        assert cible.spice_share_clipboard is True
        assert cible.spice_share_usb is False
        assert cible.spice_tls_port == ""
