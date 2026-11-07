# SPDX-License-Identifier: GPL-3.0-or-later
"""Gestionnaire de clés SSH GTK3 : construction et actions (#293, palier 16).

``ssh_key_manager_dialog.py`` restait à 43,9 % en mesure combinée : les
dialogues internes et la construction de la fenêtre principale n'étaient
jamais exécutés. Ce fichier couvre, sans vrai GTK ni vrai outil système :

- dialogues internes : génération (avec/sans CA, case de signature, type
  de clé), ajout de clé autorisée, test known hosts, renommage, changement
  de passphrase, affichage de clé publique ;
- construction de ``SSHKeyManagerDialog`` (barres cible/CA/actions, quatre
  onglets, raccourcis clavier) et bascule d'onglet ;
- actions : génération et signature par CA, import, copie publique, reveal,
  suppression, renommage, passphrase, agent, known hosts (copie, test,
  suppression), authorized_keys (bascule, ajout, suppression) ;
- helpers : notifications, confirmations, garde-fou root.

``Gtk`` et ``GLib`` sont remplacés par des doublures, ``subprocess.run``
par un faux qui répond par nom de commande, ``run_dialog_sync`` par une
file de réponses. Les doublures ``gi`` de ``tests/test_gcm.py`` servent à
l'import du module.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

import test_gcm  # noqa: E402, F401  (pose les doublures gi/Gtk avant l'import)

skm: Any = importlib.import_module("ssh_key_manager_dialog")

# ---------------------------------------------------------------------------
# Doublures GTK
# ---------------------------------------------------------------------------


class Widget:
    """Widget générique : propriétés, visibilité, sensibilité, enfants, signaux."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise l'état."""
        self.args = args
        self.kwargs = kwargs
        self.visible: bool | None = None
        self.sensible = True
        self.enfants: list[Any] = []
        self.signaux: dict[str, Any] = {}
        self.classes: list[str] = []
        self.proprietes: dict[str, Any] = {}
        self.texte = ""
        self.parent: Any = None
        self.fonctions: list[Any] = []
        self.selection = Selection()

    def __getattr__(self, nom: str) -> Any:
        """Toute autre méthode GTK est acceptée et ne fait rien."""
        if nom.startswith("__"):
            raise AttributeError(nom)
        return lambda *_a, **_k: None

    def connect(self, signal: str, rappel: Any) -> None:
        """Mémorise le gestionnaire d'un signal."""
        self.signaux[signal] = rappel

    def set_sensitive(self, sensible: bool) -> None:
        """Active ou grise."""
        self.sensible = sensible

    def set_visible(self, visible: bool) -> None:
        """Affiche ou masque."""
        self.visible = visible

    def show(self) -> None:
        """Affiche."""
        self.visible = True

    def hide(self) -> None:
        """Masque."""
        self.visible = False

    def set_markup(self, texte: str) -> None:
        """Mémorise le balisage."""
        self.texte = texte

    def set_text(self, texte: str) -> None:
        """Mémorise le texte."""
        self.texte = texte

    def set_property(self, nom: str, valeur: Any) -> None:
        """Mémorise une propriété."""
        self.proprietes[nom] = valeur

    def set_cell_data_func(self, cellule: Any, fonction: Any) -> None:
        """Mémorise une fonction de rendu."""
        self.fonctions.append((cellule, fonction))

    def pack_start(self, enfant: Any, *_a: Any) -> None:
        """Ajoute un enfant (et devient son parent)."""
        self.enfants.append(enfant)
        if isinstance(enfant, Widget):
            enfant.parent = self

    def pack_end(self, enfant: Any, *_a: Any) -> None:
        """Ajoute un enfant en fin (et devient son parent)."""
        self.enfants.append(enfant)
        if isinstance(enfant, Widget):
            enfant.parent = self

    add = pack_start
    append_column = pack_start

    def get_children(self) -> list[Any]:
        """Enfants directs."""
        return self.enfants

    def get_parent(self) -> Any:
        """Parent du widget."""
        return self.parent

    def get_selection(self) -> Any:
        """Sélection d'un TreeView."""
        return self.selection

    def get_style_context(self) -> Any:
        """Contexte de style qui note les classes."""
        return SimpleNamespace(add_class=self.classes.append)

    def foreach(self, rappel: Any) -> None:
        """Applique le rappel à chaque enfant."""
        for enfant in list(self.enfants):
            rappel(enfant)

    def destroy(self) -> None:
        """Marque le widget détruit."""
        self.detruit = True


class Selection:
    """``Gtk.TreeSelection`` factice : ligne sélectionnée fixable par le test."""

    def __init__(self) -> None:
        """Rien de sélectionné au départ."""
        self.lignes: list[int] = []
        self.signaux: dict[str, Any] = {}
        self.mode: Any = None
        self.donnees: list[list[Any]] = []

    def set_mode(self, mode: Any) -> None:
        """Mode de sélection."""
        self.mode = mode

    def connect(self, signal: str, rappel: Any) -> None:
        """Mémorise le gestionnaire."""
        self.signaux[signal] = rappel

    def get_selected_rows(self) -> tuple[Any, list[int]]:
        """Modèle et chemins sélectionnés."""
        return (None, list(self.lignes))

    def get_selected(self) -> tuple[Any, Any]:
        """Modèle et itérateur sélectionné (``None`` si rien)."""
        if not self.lignes:
            return (self.donnees, None)
        return (self.donnees, self.lignes[0])


class ListStore:
    """``Gtk.ListStore`` factice (itérateur = indice de ligne)."""

    def __init__(self, *_types: Any) -> None:
        """Modèle vide."""
        self.lignes: list[list[Any]] = []

    def append(self, valeurs: list[Any]) -> None:
        """Ajoute une ligne."""
        self.lignes.append(list(valeurs))

    def clear(self) -> None:
        """Vide le modèle."""
        self.lignes = []

    def get_iter(self, chemin: Any) -> Iter | None:
        """Itérateur d'un chemin, ``None`` hors limites."""
        indice = int(chemin)
        return Iter(indice) if 0 <= indice < len(self.lignes) else None

    def __len__(self) -> int:
        """Nombre de lignes."""
        return len(self.lignes)

    def __getitem__(self, it: Iter | int) -> list[Any]:
        """Ligne d'un itérateur."""
        return self.lignes[it.indice if isinstance(it, Iter) else it]

    def __setitem__(self, it: Iter | int, ligne: list[Any]) -> None:
        """Remplace une ligne."""
        self.lignes[it.indice if isinstance(it, Iter) else it] = list(ligne)


class Iter:
    """``Gtk.TreeIter`` factice : toujours vrai, même pour la ligne 0."""

    def __init__(self, indice: int) -> None:
        """Mémorise l'indice de la ligne."""
        self.indice = indice


class Entree(Widget):
    """``Gtk.Entry`` : texte mémorisé."""

    def get_text(self) -> str:
        """Rend le texte."""
        return self.texte


class CaseACocher(Widget):
    """``Gtk.CheckButton`` : état coché mémorisé."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Décochée par défaut."""
        super().__init__(*args, **kwargs)
        self.actif = False

    def set_active(self, actif: bool) -> None:
        """Coche ou décoche."""
        self.actif = actif

    def get_active(self) -> bool:
        """État de la case."""
        return self.actif


class ListeDeroulante(Widget):
    """``Gtk.ComboBoxText`` : éléments, actif, texte actif."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Rien d'actif au départ."""
        super().__init__(*args, **kwargs)
        self.elements: list[str] = []
        self.actif = -1

    def append_text(self, texte: str) -> None:
        """Ajoute un élément."""
        self.elements.append(texte)

    def set_active(self, indice: int) -> None:
        """Sélectionne un élément."""
        self.actif = indice

    def get_active_text(self) -> str | None:
        """Texte de l'élément actif."""
        if 0 <= self.actif < len(self.elements):
            return self.elements[self.actif]
        return None


class Tampon(Widget):
    """``Gtk.TextBuffer`` : texte mémorisé, bornes factices."""

    def get_bounds(self) -> tuple[str, str]:
        """Bornes factices."""
        return ("debut", "fin")

    def get_text(self, _debut: Any, _fin: Any, _cache: bool) -> str:
        """Rend le contenu."""
        return self.texte


class ZoneTexte(Widget):
    """``Gtk.TextView`` : rend son tampon."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Tampon neuf."""
        super().__init__(*args, **kwargs)
        self.tampon = Tampon()

    def get_buffer(self) -> Tampon:
        """Tampon de la zone."""
        return self.tampon


class Grille(Widget):
    """``Gtk.Grid`` : attache des enfants."""

    def attach(self, enfant: Any, *_a: Any) -> None:
        """Attache un enfant (et devient son parent)."""
        self.enfants.append(enfant)
        if isinstance(enfant, Widget):
            enfant.parent = self


class Carnet(Widget):
    """``Gtk.Notebook`` : pages, page courante, signaux."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Aucune page au départ."""
        super().__init__(*args, **kwargs)
        self.pages: list[Any] = []
        self.courante = 0

    def append_page(self, page: Any, _libelle: Any) -> None:
        """Ajoute une page."""
        self.pages.append(page)

    def get_current_page(self) -> int:
        """Indice de la page courante."""
        return self.courante

    def set_current_page(self, indice: int) -> None:
        """Change la page courante."""
        self.courante = indice


class BarreEtat(Widget):
    """``Gtk.Statusbar`` : message courant + historique complet."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Aucun message au départ."""
        super().__init__(*args, **kwargs)
        self.messages: list[str] = []
        self.historique: list[str] = []
        self.contexte = -1

    def get_context_id(self, _nom: str) -> int:
        """Identifiant de contexte factice."""
        self.contexte += 1
        return self.contexte

    def push(self, _ctx: int, message: str) -> None:
        """Affiche un message et l'ajoute à l'historique."""
        self.messages.append(message)
        self.historique.append(message)

    def pop(self, _ctx: int) -> None:
        """Retire le dernier message."""
        if self.messages:
            self.messages.pop()


class FenetreFichier(Widget):
    """``Gtk.FileChooserDialog`` : nom de fichier fixé par le test."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Sans fichier choisi au départ."""
        super().__init__(*args, **kwargs)
        self.nom_fichier: str | None = None
        self.boutons: list[Any] = []
        self.filtres: list[Any] = []

    def add_button(self, libelle: str, _reponse: Any) -> Widget:
        """Ajoute un bouton (mémorisé)."""
        bouton = Widget(libelle)
        self.boutons.append(bouton)
        return bouton

    def add_filter(self, filtre: Any) -> None:
        """Mémorise un filtre."""
        self.filtres.append(filtre)

    def get_filename(self) -> str | None:
        """Nom du fichier choisi."""
        return self.nom_fichier


class FenetreMessage(Widget):
    """``Gtk.MessageDialog`` : zone de message factice."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Zone de message vide."""
        super().__init__(*args, **kwargs)
        self.zone = Widget()

    def get_message_area(self) -> Widget:
        """Zone de contenu."""
        return self.zone


def faux_gtk(etat: SimpleNamespace) -> SimpleNamespace:
    """Espace de noms ``Gtk`` factice pour tout le module testé."""
    gtk = SimpleNamespace(
        Window=Widget,
        Entry=Entree,
        CheckButton=CaseACocher,
        ComboBoxText=ListeDeroulante,
        TextView=ZoneTexte,
        Grid=Grille,
        Notebook=Carnet,
        Statusbar=BarreEtat,
        MessageDialog=FenetreMessage,
        FileFilter=Widget,
        ResponseType=SimpleNamespace(
            OK="OK", CANCEL="CANCEL", CLOSE="CLOSE", YES="YES", NO="NO", HELP="HELP"
        ),
        PolicyType=SimpleNamespace(AUTOMATIC=0, NEVER=1),
        ShadowType=SimpleNamespace(IN=0, NONE=1),
        SelectionMode=SimpleNamespace(MULTIPLE="multiple"),
        Justification=SimpleNamespace(CENTER=0),
        IconSize=SimpleNamespace(SMALL_TOOLBAR="petite"),
        Orientation=SimpleNamespace(VERTICAL=1, HORIZONTAL=0),
        WindowPosition=SimpleNamespace(CENTER_ON_PARENT=1),
        WrapMode=SimpleNamespace(CHAR=0),
        FileChooserAction=SimpleNamespace(OPEN=0),
        MessageType=SimpleNamespace(QUESTION=1),
        ButtonsType=SimpleNamespace(YES_NO=1),
    )
    for nom in (
        "Label",
        "ScrolledWindow",
        "TreeView",
        "TreeViewColumn",
        "CellRendererPixbuf",
        "CellRendererText",
        "CellRendererToggle",
        "Box",
        "Button",
        "Separator",
    ):
        setattr(gtk, nom, Widget)

    def nouveau_magasin(*types: Any) -> ListStore:
        magasin = ListStore(*types)
        etat.magasin.append(magasin)
        return magasin

    def nouveau_choisisseur(*args: Any, **kwargs: Any) -> FenetreFichier:
        fenetre = FenetreFichier(*args, **kwargs)
        fenetre.nom_fichier = etat.fichier_choisi
        return fenetre

    gtk.ListStore = nouveau_magasin
    gtk.FileChooserDialog = nouveau_choisisseur
    return gtk


# ---------------------------------------------------------------------------
# Doublures subprocess / dialogs
# ---------------------------------------------------------------------------


def _res(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    """Résultat de ``subprocess.run`` minimal."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FauxRun:
    """Remplace ``subprocess.run`` : réponses par nom de commande, appels gardés."""

    def __init__(self, reponses: dict[str, Any] | None = None) -> None:
        """Initialise la doublure.

        Args:
            reponses: commande (premier argument) → résultat, exception à
                lever, ou fonction ``(cmd, kwargs)``.
        """
        self.reponses = reponses or {}
        self.appels: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        """Rend la réponse enregistrée pour la commande."""
        self.appels.append(list(cmd))
        cle = cmd[0] if cmd else ""
        reponse = self.reponses.get(cle)
        if isinstance(reponse, BaseException):
            raise reponse
        if isinstance(reponse, SimpleNamespace):
            return reponse
        if callable(reponse):
            return reponse(cmd, kwargs)
        return _res()


class FauxPopen:
    """Remplace ``subprocess.Popen`` : appels gardés, exception optionnelle."""

    def __init__(self) -> None:
        """Aucun appel au départ."""
        self.appels: list[list[str]] = []
        self.exception: BaseException | None = None

    def __call__(self, cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        """Enregistre l'appel, lève l'exception configurée le cas échéant."""
        self.appels.append(list(cmd))
        if self.exception is not None:
            raise self.exception
        return SimpleNamespace(pid=1)


def _enfants_rec(widget: Any) -> list[Any]:
    """Tous les descendants d'un widget (récursif)."""
    trouves: list[Any] = []
    for enfant in getattr(widget, "enfants", []):
        trouves.append(enfant)
        trouves.extend(_enfants_rec(enfant))
    return trouves


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """Doublures GTK/GLib/Gdk/subprocess, ``~/.ssh`` temporaire, file de réponses."""
    maison = tmp_path / "maison"
    ssh = maison / ".ssh"
    ssh.mkdir(parents=True)
    etat = SimpleNamespace(
        ssh=ssh,
        magasin=[],
        fichier_choisi=None,
        reponses=[],
        signaux={},
        zone=Widget(),
    )
    gtk = faux_gtk(etat)
    etat.gtk = gtk
    monkeypatch.setattr(skm, "Gtk", gtk)
    monkeypatch.setattr(
        skm,
        "GLib",
        SimpleNamespace(
            markup_escape_text=lambda t: f"<esc>{t}</esc>",
            idle_add=lambda f, *a: (f(*a) if callable(f) else None) or True,
            timeout_add=lambda _ms, f, *a: True,
        ),
    )
    monkeypatch.setattr(
        skm,
        "Gdk",
        SimpleNamespace(
            ModifierType=SimpleNamespace(CONTROL_MASK=4),
            KEY_Escape=0x0FF1B,
            KEY_n=0x006E,
            KEY_i=0x0069,
            KEY_Delete=0x0FFFF,
        ),
    )
    monkeypatch.setattr(skm, "_SSH_DIR", ssh)
    monkeypatch.setattr(skm, "_CA_CONFIG_PATH", ssh / ".gcm_ca_config.json")
    monkeypatch.setattr(skm, "_copy_text_to_clipboard", lambda texte: True)
    faux_run = FauxRun(
        {
            "ssh-keygen": _res(0, stdout="256 SHA256:abcdefghijklmnop moi (ED25519)\n"),
            "stat": _res(0, stdout=""),
        }
    )
    etat.faux_run = faux_run
    monkeypatch.setattr(skm.subprocess, "run", faux_run)
    faux_popen = FauxPopen()
    etat.faux_popen = faux_popen
    monkeypatch.setattr(skm.subprocess, "Popen", faux_popen)
    monkeypatch.setattr(
        skm, "run_dialog_sync", lambda dlg: etat.reponses.pop(0) if etat.reponses else "CANCEL"
    )

    # Méthodes de fenêtre absentes des doublures _Base de test_gcm.
    classe = skm.SSHKeyManagerDialog
    for nom, valeur in (
        ("add", lambda _self, _enfant: None),
        ("set_transient_for", lambda _self, _p: None),
        ("set_default_size", lambda _self, *_a: None),
        ("set_position", lambda _self, *_a: None),
        (
            "connect",
            lambda _self, signal, rappel: etat.signaux.setdefault(signal, rappel) or 1,
        ),
    ):
        monkeypatch.setattr(classe, nom, valeur, raising=False)

    # Sous-dialogues internes : contenu suivi dans une zone commune.
    for classe_dialogue in (
        skm._GenerateKeyDialog,
        skm._AddAuthorizedKeyDialog,
        skm._TestKnownHostsDialog,
        skm._RenameKeyDialog,
        skm._ChangePassphraseDialog,
        skm._ViewPublicKeyDialog,
    ):
        monkeypatch.setattr(
            classe_dialogue, "set_default_size", lambda _self, *_a: None, raising=False
        )
        monkeypatch.setattr(
            classe_dialogue,
            "connect",
            lambda _self, signal, rappel, _c=classe_dialogue: (
                etat.signaux.setdefault(f"{_c.__name__}:{signal}", rappel) or 1
            ),
            raising=False,
        )
        monkeypatch.setattr(
            classe_dialogue,
            "add_button",
            lambda _self, libelle, _reponse: Widget(libelle),
            raising=False,
        )
        monkeypatch.setattr(
            classe_dialogue, "get_content_area", lambda _self: etat.zone, raising=False
        )
        monkeypatch.setattr(classe_dialogue, "destroy", lambda _self: None, raising=False)
    return etat


def cle(dossier: Path, nom: str, mode: int = 0o600, pub: bool = True) -> Path:
    """Crée une clé privée (et sa publique) dans *dossier*."""
    privee = dossier / nom
    privee.write_text("PRIVEE", encoding="utf-8")
    privee.chmod(mode)
    if pub:
        (dossier / f"{nom}.pub").write_text("ssh-ed25519 AAAA moi", encoding="utf-8")
    return privee


@pytest.fixture
def gestionnaire(env: SimpleNamespace) -> Any:
    """Gestionnaire construit pour de vrai sur un ``~/.ssh`` avec deux clés."""
    cle(env.ssh, "id_ed25519")
    cle(env.ssh, "id_rsa", pub=False)
    (env.ssh / "known_hosts").write_text("hote.example ssh-ed25519 AAAA\n", encoding="utf-8")
    (env.ssh / "authorized_keys").write_text("ssh-ed25519 BBBB moi\n", encoding="utf-8")
    (env.ssh / "config").write_text("Host hote.example\n  User moi\n", encoding="utf-8")
    return skm.SSHKeyManagerDialog(parent="parent")


def notifie(d: Any) -> str:
    """Dernier message poussé dans la barre d'état du gestionnaire."""
    return d._status_bar.messages[-1]


def a_notifie(d: Any, texte: str) -> bool:
    """Le texte apparaît dans l'historique des messages de la barre d'état."""
    return any(texte in message for message in d._status_bar.historique)


# ---------------------------------------------------------------------------
# Dialogues internes
# ---------------------------------------------------------------------------


class TestDialogueGeneration:
    """Formulaire de génération de clé."""

    def test_sans_ca(self, env: SimpleNamespace) -> None:
        """Sans CA : pas de case de signature, taille masquée, nom par défaut."""
        dlg = skm._GenerateKeyDialog(parent="p")
        assert dlg._sign_widgets == ()
        assert dlg._lbl_size.visible is False
        assert dlg._cmb_size.visible is False
        assert dlg._entry_name.texte == "id_ed25519"
        assert dlg._cmb_type.elements == skm._KEY_TYPES
        assert dlg._cmb_size.elements == skm._RSA_SIZES
        assert dlg._cmb_size.actif == 3  # 4096

    def test_avec_ca(self, env: SimpleNamespace) -> None:
        """Avec CA : case de signature, identité, principals, validité."""
        dlg = skm._GenerateKeyDialog(parent="p", ca_available=True, ca_name="ma_ca")
        assert "toggled" in dlg._chk_sign.signaux
        assert len(dlg._sign_widgets) == 6
        for widget in dlg._sign_widgets:
            assert widget.visible is False  # décochée à la construction
        assert dlg._entry_validity.texte == "+52w"

    def test_bascule_signature(self, env: SimpleNamespace) -> None:
        """Cocher/décocher affiche/masque les champs de signature."""
        dlg = skm._GenerateKeyDialog(parent="p", ca_available=True, ca_name="ma_ca")
        dlg._chk_sign.set_active(True)
        dlg._on_sign_toggled(dlg._chk_sign)
        assert all(w.visible is True for w in dlg._sign_widgets)
        dlg._chk_sign.set_active(False)
        dlg._on_sign_toggled(dlg._chk_sign)
        assert all(w.visible is False for w in dlg._sign_widgets)

    def test_changement_de_type(self, env: SimpleNamespace) -> None:
        """Type rsa : taille révélée et fichier renommé ; ed25519 masque."""
        dlg = skm._GenerateKeyDialog(parent="p")
        dlg._cmb_type.set_active(skm._KEY_TYPES.index("rsa"))
        dlg._on_type_changed(dlg._cmb_type)
        assert dlg._lbl_size.visible is True
        assert dlg._cmb_size.visible is True
        assert dlg._entry_name.texte == "id_rsa"
        dlg._cmb_type.set_active(skm._KEY_TYPES.index("ed25519"))
        dlg._on_type_changed(dlg._cmb_type)
        assert dlg._lbl_size.visible is False
        assert dlg._entry_name.texte == "id_ed25519"

    def test_options_ed25519(self, env: SimpleNamespace) -> None:
        """Options ed25519 : taille 0, valeurs saisies conservées."""
        dlg = skm._GenerateKeyDialog(parent="p")
        dlg._entry_name.set_text("ma_cle")
        dlg._entry_comment.set_text("mon commentaire")
        dlg._entry_pass.set_text("secret")
        opts = dlg.get_options()
        assert opts == {
            "type": "ed25519",
            "size": 0,
            "name": "ma_cle",
            "comment": "mon commentaire",
            "passphrase": "secret",
            "sign_with_ca": False,
        }

    def test_options_rsa_et_signature(self, env: SimpleNamespace) -> None:
        """Type rsa : taille prise ; la signature ajoute identité et principals."""
        dlg = skm._GenerateKeyDialog(parent="p", ca_available=True, ca_name="ca")
        dlg._cmb_type.set_active(skm._KEY_TYPES.index("rsa"))
        dlg._cmb_size.set_active(skm._RSA_SIZES.index("2048"))
        dlg._chk_sign.set_active(True)
        dlg._entry_identity.set_text("identite")
        dlg._entry_principals.set_text("principal1,principal2")
        dlg._entry_validity.set_text("+30d")
        opts = dlg.get_options()
        assert opts["size"] == 2048
        assert opts["sign_with_ca"] is True
        assert opts["ca_identity"] == "identite"
        assert opts["ca_principals"] == "principal1,principal2"
        assert opts["ca_validity"] == "+30d"

    def test_options_replis(self, env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
        """Champs vides : replis sur commentaire par défaut, utilisateur, +52w."""
        dlg = skm._GenerateKeyDialog(parent="p", ca_available=True, ca_name="ca")
        dlg._chk_sign.set_active(True)
        dlg._entry_comment.set_text("  ")
        dlg._entry_identity.set_text("")
        dlg._entry_principals.set_text("")
        dlg._entry_validity.set_text("  ")
        monkeypatch.setattr(skm.getpass, "getuser", lambda: "utilisateur")
        opts = dlg.get_options()
        assert opts["comment"] == skm.get_default_ssh_key_comment()
        assert opts["ca_identity"] == skm.get_default_ssh_key_comment()
        assert opts["ca_principals"] == "utilisateur"
        assert opts["ca_validity"] == "+52w"


class TestDialogueAjoutCleAutorisee:
    """Saisie d'une ligne authorized_keys."""

    def test_lecture(self, env: SimpleNamespace) -> None:
        """Le texte du tampon est rendu nettoyé des espaces."""
        dlg = skm._AddAuthorizedKeyDialog(parent="p")
        dlg._textview.tampon.set_text("  ssh-ed25519 AAAA moi  \n")
        assert dlg.get_key_line() == "ssh-ed25519 AAAA moi"


class TestDialogueTestKnownHosts:
    """Fenêtre de résultats du test known hosts."""

    def test_construction(self, env: SimpleNamespace) -> None:
        """Lignes du magasin, fonction de rendu ✓/✗ et couleurs."""
        dlg = skm._TestKnownHostsDialog(
            parent="p",
            target_label="machine locale",
            results=[("hote", True, "ligne"), ("autre", False, "")],
        )
        magasin = env.magasin[-1]
        assert magasin.lignes == [["hote", True, "ligne"], ["autre", False, ""]]
        colonnes_avec_rendu = [
            w for w in _enfants_rec(env.zone) if isinstance(w, Widget) and w.fonctions
        ]
        assert colonnes_avec_rendu, "colonne « Found » sans fonction de rendu"
        fonction = colonnes_avec_rendu[0].fonctions[0][1]
        cellule = Widget()
        fonction(None, cellule, magasin, Iter(0), None)
        assert cellule.proprietes["text"] == "✓"
        assert cellule.proprietes["foreground"] == "#2d9e44"
        fonction(None, cellule, magasin, Iter(1), None)
        assert cellule.proprietes["text"] == "✗"
        assert cellule.proprietes["foreground"] == "#c0392b"


class TestDialogueRenommage:
    """Formulaire de renommage."""

    def test_valeur(self, env: SimpleNamespace) -> None:
        """Le nom courant est pré-rempli, rendu nettoyé."""
        dlg = skm._RenameKeyDialog(parent="p", current_name="id_ed25519")
        assert dlg._entry.texte == "id_ed25519"
        dlg._entry.set_text("  nouveau  ")
        assert dlg.get_new_name() == "nouveau"


class TestDialoguePassphrase:
    """Formulaire de changement de passphrase."""

    def test_valeurs(self, env: SimpleNamespace) -> None:
        """Les trois champs sont rendus dans l'ordre."""
        dlg = skm._ChangePassphraseDialog(parent="p", key_name="id_ed25519")
        dlg._entry_old.set_text("ancien")
        dlg._entry_new.set_text("nouveau")
        dlg._entry_confirm.set_text("nouveau")
        assert dlg.get_values() == ("ancien", "nouveau", "nouveau")


class TestDialogueVuePublique:
    """Affichage du contenu complet d'une clé publique."""

    def test_construction_et_copie(self, env: SimpleNamespace) -> None:
        """Le contenu est posé dans le tampon ; le bouton copie la clé."""
        skm._ViewPublicKeyDialog(parent="p", key_name="id_ed25519", content="CLE PUBLIQUE")
        zones = [w for w in _enfants_rec(env.zone) if isinstance(w, ZoneTexte)]
        assert zones and zones[0].tampon.texte == "CLE PUBLIQUE"
        boutons = [w for w in _enfants_rec(env.zone) if "Copy" in str(w.args) + str(w.kwargs)]
        assert boutons, "bouton copie absent"
        copies: list[str] = []
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(skm, "_copy_text_to_clipboard", lambda t: copies.append(t) or True)
        boutons[0].signaux["clicked"](boutons[0])
        monkeypatch.undo()
        assert copies == ["CLE PUBLIQUE"]


# ---------------------------------------------------------------------------
# Construction de la fenêtre principale
# ---------------------------------------------------------------------------


class TestConstruction:
    """Construction réelle de ``SSHKeyManagerDialog``."""

    def test_fenetres_et_onglets(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Quatre pages, magasins peuplés, boutons grisés sans sélection."""
        d = gestionnaire
        assert len(d._notebook.pages) == 4
        assert [ligne[0] for ligne in d._priv_store.lignes] == ["id_ed25519", "id_rsa"]
        assert d._pub_store.lignes == []
        assert d._kh_store.lignes[0][0] == "hote.example"
        assert d._ak_store.lignes[0][3] == "moi"
        for btn in (
            d._btn_copy,
            d._btn_view,
            d._btn_rename,
            d._btn_passphrase,
            d._btn_agent,
            d._btn_delete,
        ):
            assert btn.sensible is False
        assert d._btn_delete.classes == ["destructive-action"]

    def test_bascule_d_onglet(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Chaque page n'affiche que son groupe de boutons."""
        d = gestionnaire
        d._on_page_switched(None, None, 0)
        assert d._keys_action_box.visible is True
        assert d._kh_action_box.visible is False
        assert d._ak_action_box.visible is False
        d._on_page_switched(None, None, 2)
        assert d._keys_action_box.visible is False
        assert d._kh_action_box.visible is True
        d._on_page_switched(None, None, 3)
        assert d._ak_action_box.visible is True
        assert d._kh_action_box.visible is False

    def test_rendu_statut_publique(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Colonne « Orphan Public key » : ✓ .pub, ✗ none, fond rouge si expirée."""
        d = gestionnaire
        cellule = Widget()
        d._render_pub_status(None, cellule, d._priv_store, Iter(0), None)
        assert cellule.proprietes["text"] == "✓ .pub"
        assert cellule.proprietes["foreground"] == "#2d9e44"
        assert cellule.proprietes["background-set"] is False
        ligne = list(d._priv_store.lignes[0])
        ligne[skm._COL_EXPIRED] = True
        d._priv_store.lignes[0] = ligne
        d._render_pub_status(None, cellule, d._priv_store, Iter(0), None)
        assert cellule.proprietes["background"] == "#ffdede"

    def test_selection_et_boutons(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sélectionner une clé active les boutons."""
        d = gestionnaire
        d._priv_tree.selection.lignes = [0]
        d._priv_tree.selection.donnees = d._priv_store.lignes
        d._on_selection_changed(None)
        for btn in (
            d._btn_copy,
            d._btn_view,
            d._btn_rename,
            d._btn_passphrase,
            d._btn_agent,
            d._btn_delete,
        ):
            assert btn.sensible is True

    def test_cle_selectionnee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """La clé sélectionnée expose nom, chemins et permissions."""
        d = gestionnaire
        d._priv_tree.selection.lignes = [0]
        d._priv_tree.selection.donnees = d._priv_store.lignes
        cle_selectionnee = d._get_selected_key()
        assert cle_selectionnee["name"] == "id_ed25519"
        assert cle_selectionnee["pub"].endswith("id_ed25519.pub")
        assert cle_selectionnee["perms_ok"] is True
        d._priv_tree.selection.lignes = []
        assert d._get_selected_key() is None

    def test_fenetre_de_haut_niveau(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fenêtre autonome : soi-même ; onglet épinglé : fenêtre principale."""
        d = gestionnaire
        assert d._top_window() is d
        d.is_tab = True
        faux_module = SimpleNamespace(wMain=SimpleNamespace(window="wMain.window"))
        monkeypatch.setitem(sys.modules, "gnome_connection_manager", faux_module)
        assert d._top_window() == "wMain.window"

    def test_raccourcis_clavier(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Escape ferme, Ctrl+N/I génèrent/importent, Delete supprime selon l'onglet."""
        d = gestionnaire
        fermetures: list[int] = []
        generation: list[int] = []
        imports: list[int] = []
        suppression: list[str] = []
        d.request_close = lambda: fermetures.append(1)  # type: ignore[method-assign]
        d._on_generate = lambda _w: generation.append(1)  # type: ignore[method-assign]
        d._on_import = lambda _w: imports.append(1)  # type: ignore[method-assign]
        d._on_delete = lambda _w: suppression.append("cle")  # type: ignore[method-assign]
        d._on_delete_known_host = lambda _w: suppression.append("kh")  # type: ignore[method-assign]
        d._on_delete_authorized_key = lambda _w: suppression.append("ak")  # type: ignore[method-assign]
        d._setup_keyboard()

        assert d._on_key_press(None, SimpleNamespace(state=0, keyval=0x0FF1B)) is True
        assert fermetures == [1]
        assert d._on_key_press(None, SimpleNamespace(state=4, keyval=0x006E)) is True
        assert generation == [1]
        assert d._on_key_press(None, SimpleNamespace(state=4, keyval=0x0069)) is True
        assert imports == [1]
        for page, attendu in ((0, "cle"), (2, "kh"), (3, "ak")):
            d._notebook.courante = page
            assert d._on_key_press(None, SimpleNamespace(state=0, keyval=0x0FFFF)) is True
            assert suppression[-1] == attendu
        assert d._on_key_press(None, SimpleNamespace(state=0, keyval=0x0041)) is False


# ---------------------------------------------------------------------------
# Cible locale / distante
# ---------------------------------------------------------------------------


class TestCibleDistante:
    """Bascule local ↔ distant pour known_hosts / authorized_keys."""

    def test_cible_vide_revient_au_local(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Une cible vide bascule en local."""
        d = gestionnaire
        d._entry_target.set_text("   ")
        d._remote_fs = "distante"
        d._on_connect_target(None)
        assert d._remote_fs is None
        assert a_notifie(d, "Switched back")

    def test_cible_invalide(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Un format invalide est refusé."""
        d = gestionnaire
        d._entry_target.set_text("pas-une-cible")
        d._on_connect_target(None)
        assert "Invalid target" in notifie(d)

    def test_connexion_echouee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Échec du test de connexion : on reste en local."""
        d = gestionnaire
        d._entry_target.set_text("moi@hote:2222")
        env.faux_run.reponses["ssh"] = _res(255, stderr="refusé")
        d._on_connect_target(None)
        assert d._remote_fs is None
        assert "Connection failed" in notifie(d)

    def test_connexion_reussie(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Connexion OK : les onglets distants se rechargent depuis la cible."""
        d = gestionnaire
        d._entry_target.set_text("moi@hote")
        env.faux_run.reponses["ssh"] = _res(0, stdout="__OK__\n")
        d._on_connect_target(None)
        assert d._remote_fs is not None
        assert a_notifie(d, "Connected to moi@hote")

    def test_retour_local(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Le bouton Local vide la cible et recharge les onglets."""
        d = gestionnaire
        d._remote_fs = "distante"
        d._on_use_local(None)
        assert d._remote_fs is None
        assert d._entry_target.texte == ""
        assert "local machine" in d._lbl_target_status.texte


# ---------------------------------------------------------------------------
# CA de signature (interface)
# ---------------------------------------------------------------------------


class TestCaInterface:
    """Désignation, import et suppression de la CA (côté interface)."""

    def test_designation_annulee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Annulation du sélecteur : aucun changement."""
        d = gestionnaire
        d._on_designate_ca(None)
        assert d._ca_priv_path is None

    def test_designation_pub_refusee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Choisir le .pub est refusé."""
        d = gestionnaire
        cle(env.ssh, "ca")
        env.fichier_choisi = str(env.ssh / "ca.pub")
        env.reponses.append("OK")
        d._on_designate_ca(None)
        assert d._ca_priv_path is None
        assert "not the .pub file" in notifie(d)

    def test_designation_sans_pub(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Une clé privée sans .pub ne peut pas devenir CA."""
        d = gestionnaire
        privee = cle(env.ssh, "ca_solo", pub=False)
        env.fichier_choisi = str(privee)
        env.reponses.append("OK")
        d._on_designate_ca(None)
        assert d._ca_priv_path is None
        assert "No matching .pub" in notifie(d)

    def test_designation_valide(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Une paire valide est désignée et sauvegardée."""
        d = gestionnaire
        privee = cle(env.ssh, "ca")
        env.fichier_choisi = str(privee)
        env.reponses.append("OK")
        d._on_designate_ca(None)
        assert d._ca_priv_path == privee
        assert d._ca_pub_path == privee.with_name("ca.pub")
        assert json.loads(skm._CA_CONFIG_PATH.read_text())["ca_priv"] == str(privee)
        assert "Designated CA" in notifie(d)

    def test_import_annule(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Annulation de l'import : rien ne bouge."""
        d = gestionnaire
        d._on_import_ca(None)
        assert not (env.ssh / "ca_importee").exists()

    def test_import_sans_pub(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Import refusé si le .pub manque à côté de la clé privée."""
        d = gestionnaire
        externe = env.ssh.parent / "externe"
        externe.mkdir()
        privee = cle(externe, "ca_importee", pub=False)
        env.fichier_choisi = str(privee)
        env.reponses.append("OK")
        d._on_import_ca(None)
        assert "No matching .pub" in notifie(d)

    def test_import_valide(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """La paire est copiée dans ~/.ssh, désignée et déclarée CA."""
        d = gestionnaire
        externe = env.ssh.parent / "externe"
        externe.mkdir()
        privee = cle(externe, "ca_importee")
        env.fichier_choisi = str(privee)
        env.reponses.append("OK")
        d._on_import_ca(None)
        assert (env.ssh / "ca_importee").exists()
        assert (env.ssh / "ca_importee.pub").exists()
        assert d._ca_priv_path == env.ssh / "ca_importee"
        assert a_notifie(d, "CA imported")

    def test_import_ecrasement_refuse(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Un fichier déjà présent exige la confirmation d'écrasement."""
        d = gestionnaire
        externe = env.ssh.parent / "externe"
        externe.mkdir()
        privee = cle(externe, "deja_la")
        cle(env.ssh, "deja_la")
        env.fichier_choisi = str(privee)
        env.reponses.extend(["OK", "NO"])
        d._on_import_ca(None)
        assert (env.ssh / "deja_la").read_text() == "PRIVEE"
        assert "Key imported" not in d._status_bar.messages

    def test_suppression_sans_ca(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans CA désignée : simple notification."""
        d = gestionnaire
        d._on_delete_ca(None)
        assert "No CA currently designated" in notifie(d)

    def test_suppression_refusee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Refus de la confirmation : la CA reste en place."""
        d = gestionnaire
        privee = cle(env.ssh, "ca")
        d._ca_priv_path = privee
        d._ca_pub_path = privee.with_name("ca.pub")
        d._confirm = lambda _t: False  # type: ignore[method-assign]
        d._on_delete_ca(None)
        assert privee.exists()

    def test_suppression_valide(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Confirmation : la paire disparaît, la configuration est vidée."""
        d = gestionnaire
        privee = cle(env.ssh, "ca")
        d._ca_priv_path = privee
        d._ca_pub_path = privee.with_name("ca.pub")
        skm._CA_CONFIG_PATH.write_text(json.dumps({"ca_priv": str(privee)}))
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._on_delete_ca(None)
        assert not privee.exists()
        assert not privee.with_name("ca.pub").exists()
        assert d._ca_priv_path is None
        assert json.loads(skm._CA_CONFIG_PATH.read_text())["ca_priv"] == ""
        assert a_notifie(d, "CA deleted")


# ---------------------------------------------------------------------------
# Génération, import et actions sur les clés
# ---------------------------------------------------------------------------


class TestGeneration:
    """Génération de clé et signature par la CA."""

    def test_bouton_annule(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Annulation du formulaire : ssh-keygen n'est jamais appelé."""
        d = gestionnaire
        env.faux_run.appels.clear()
        d._on_generate(None)
        assert env.faux_run.appels == []

    def test_bouton_valide_genere(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """OK dans le formulaire lance la génération."""
        d = gestionnaire
        env.reponses.append("OK")
        env.faux_run.appels.clear()
        d._on_generate(None)
        assert any(cmd[0] == "ssh-keygen" for cmd in env.faux_run.appels)

    def test_generation_ed25519(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Type ed25519 : commande minimale, notification de succès."""
        d = gestionnaire
        env.faux_run.appels.clear()
        d._generate_key(
            {"type": "ed25519", "name": "toute_neuve", "comment": "c", "passphrase": ""}
        )
        cmd = env.faux_run.appels[0]
        assert cmd[:2] == ["ssh-keygen", "-t"]
        assert "ed25519" in cmd
        assert str(env.ssh / "toute_neuve") in cmd
        assert a_notifie(d, "Key generated")

    def test_generation_suffixe_si_existe(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Un nom déjà pris reçoit un suffixe _1."""
        d = gestionnaire
        cle(env.ssh, "existante")
        env.faux_run.appels.clear()
        d._generate_key({"type": "ed25519", "name": "existante", "comment": "c", "passphrase": ""})
        chemin = [c for c in env.faux_run.appels[0] if "existante" in c][0]
        assert chemin.endswith("existante_1")

    def test_generation_rsa_et_ecdsa(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Type rsa : -b et la taille ; ecdsa sans -b."""
        d = gestionnaire
        env.faux_run.appels.clear()
        d._generate_key(
            {"type": "rsa", "size": 2048, "name": "r", "comment": "c", "passphrase": ""}
        )
        assert "-b" in env.faux_run.appels[0]
        assert "2048" in env.faux_run.appels[0]
        env.faux_run.appels.clear()
        d._generate_key({"type": "ecdsa", "name": "e", "comment": "c", "passphrase": ""})
        assert "-b" not in env.faux_run.appels[0]
        assert "ecdsa" in env.faux_run.appels[0]

    def test_generation_ssh_keygen_absent(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """ssh-keygen absent : notification dédiée."""
        d = gestionnaire
        env.faux_run.reponses["ssh-keygen"] = FileNotFoundError()
        d._generate_key({"type": "ed25519", "name": "x", "comment": "c", "passphrase": ""})
        assert "not found" in notifie(d)

    def test_generation_echec(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Échec de ssh-keygen : message d'erreur tronqué."""
        d = gestionnaire
        env.faux_run.reponses["ssh-keygen"] = subprocess.CalledProcessError(
            1, "ssh-keygen", stderr="trop court"
        )
        d._generate_key({"type": "ed25519", "name": "x", "comment": "c", "passphrase": ""})
        assert "ssh-keygen failed" in notifie(d)

    def test_generation_erreur_inattendue(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Une erreur inattendue est notifiée sans faire planter l'interface."""
        d = gestionnaire
        env.faux_run.reponses["ssh-keygen"] = RuntimeError("boom")
        d._generate_key({"type": "ed25519", "name": "x", "comment": "c", "passphrase": ""})
        assert "Generation failed" in notifie(d)

    def test_generation_avec_signature(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """sign_with_ca déclenche la signature après la génération."""
        d = gestionnaire
        cle(env.ssh, "ca")
        d._ca_priv_path = env.ssh / "ca"
        signatures: list[Any] = []
        d._sign_key_with_ca = lambda *a, **_k: signatures.append(a)  # type: ignore[method-assign]
        d._generate_key(
            {
                "type": "ed25519",
                "name": "s",
                "comment": "c",
                "passphrase": "",
                "sign_with_ca": True,
                "ca_identity": "id",
                "ca_principals": "p",
                "ca_validity": "+52w",
            }
        )
        assert len(signatures) == 1

    def test_signature_sans_ca(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans CA désignée : notification, pas d'appel ssh-keygen."""
        d = gestionnaire
        d._ca_priv_path = None
        env.faux_run.appels.clear()
        d._sign_key_with_ca(Path("/tmp/cle"), "id", "p", "+52w")
        assert env.faux_run.appels == []
        assert "unsigned" in notifie(d)

    def test_signature_depassee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Délai dépassé : message explicite (passphrase CA non interactive)."""
        d = gestionnaire
        d._ca_priv_path = cle(env.ssh, "ca")
        env.faux_run.reponses["ssh-keygen"] = subprocess.TimeoutExpired("ssh-keygen", 15)
        d._sign_key_with_ca(Path("/tmp/cle"), "id", "p", "+52w")
        assert "timed out" in notifie(d)

    def test_signature_echec(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Échec ssh-keygen -s : stderr remontée."""
        d = gestionnaire
        d._ca_priv_path = cle(env.ssh, "ca")
        env.faux_run.reponses["ssh-keygen"] = _res(1, stderr="erreur de signature")
        d._sign_key_with_ca(Path("/tmp/cle"), "id", "p", "+52w")
        assert "CA signing failed" in notifie(d)

    def test_signature_exception(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Exception générale : notifiée, sans crash."""
        d = gestionnaire
        d._ca_priv_path = cle(env.ssh, "ca")
        env.faux_run.reponses["ssh-keygen"] = OSError("reseau")
        d._sign_key_with_ca(Path("/tmp/cle"), "id", "p", "+52w")
        assert "CA signing failed" in notifie(d)

    def test_signature_reussie(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Signature OK : commande -s complète, notification de succès."""
        d = gestionnaire
        ca = cle(env.ssh, "ca")
        d._ca_priv_path = ca
        env.faux_run.appels.clear()
        d._sign_key_with_ca(Path("/tmp/ma_cle"), "identite", "p1,p2", "+30d")
        cmd = env.faux_run.appels[0]
        assert cmd[:2] == ["ssh-keygen", "-s"]
        assert str(ca) in cmd
        assert "+30d" in cmd
        assert "signed by CA" in notifie(d)


class TestImport:
    """Import d'une clé privée existante dans ~/.ssh."""

    def test_annulation(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Annulation : aucun fichier copié."""
        d = gestionnaire
        d._on_import(None)
        assert not (env.ssh / "importee").exists()

    def test_import_avec_pub(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """La clé et son .pub sont copiés avec les bonnes permissions."""
        d = gestionnaire
        exterieur = env.ssh.parent / "ailleurs"
        exterieur.mkdir()
        source = cle(exterieur, "importee")
        env.fichier_choisi = str(source)
        env.reponses.append("OK")
        d._on_import(None)
        assert (env.ssh / "importee").exists()
        assert (env.ssh / "importee.pub").exists()
        assert oct((env.ssh / "importee").stat().st_mode)[-3:] == "600"
        assert oct((env.ssh / "importee.pub").stat().st_mode)[-3:] == "644"
        assert a_notifie(d, "Key imported")

    def test_import_ecrasement_refuse(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Fichier déjà présent : sans confirmation, l'import s'arrête."""
        d = gestionnaire
        exterieur = env.ssh.parent / "ailleurs"
        exterieur.mkdir()
        source = cle(exterieur, "id_ed25519")
        env.fichier_choisi = str(source)
        env.reponses.extend(["OK", "NO"])
        d._on_import(None)
        assert (env.ssh / "id_ed25519").read_text() == "PRIVEE"

    def test_import_echec(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Échec de copie notifié sans crash."""
        d = gestionnaire
        exterieur = env.ssh.parent / "ailleurs"
        exterieur.mkdir()
        source = cle(exterieur, "importee")
        env.fichier_choisi = str(source)
        env.reponses.append("OK")

        def copie_echoue(*_a: Any, **_k: Any) -> None:
            raise OSError("disque plein")

        monkeypatch.setattr(skm.shutil, "copy2", copie_echoue)
        d._on_import(None)
        assert "Import failed" in notifie(d)


class TestCopiePublique:
    """Copie de la clé publique dans le presse-papiers."""

    def _selectionne(self, d: Any) -> None:
        """Sélectionne la première clé privée."""
        d._priv_tree.selection.lignes = [0]
        d._priv_tree.selection.donnees = d._priv_store.lignes

    def test_sans_selection(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans clé sélectionnée : notification."""
        d = gestionnaire
        d._on_copy_public(None)
        assert "No Public key" in notifie(d)

    def test_fichier_absent(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Fichier .pub disparu : notification."""
        d = gestionnaire
        self._selectionne(d)
        (env.ssh / "id_ed25519.pub").unlink()
        d._on_copy_public(None)
        assert "not found" in notifie(d)

    def test_copie_reussie(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Copie OK : notification de succès."""
        d = gestionnaire
        self._selectionne(d)
        d._on_copy_public(None)
        assert "copied to clipboard" in notifie(d)

    def test_presse_papiers_indisponible(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Presse-papiers indisponible : repli notifié."""
        d = gestionnaire
        self._selectionne(d)
        monkeypatch.setattr(skm, "_copy_text_to_clipboard", lambda _t: False)
        d._on_copy_public(None)
        assert "Clipboard unavailable" in notifie(d)

    def test_erreur_de_lecture(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Erreur de lecture : notifiée sans crash."""
        d = gestionnaire
        self._selectionne(d)
        (env.ssh / "id_ed25519.pub").chmod(0o000)
        d._on_copy_public(None)
        (env.ssh / "id_ed25519.pub").chmod(0o644)
        assert "Copy failed" in notifie(d)


class TestReveal:
    """Ouverture de ~/.ssh dans le gestionnaire de fichiers."""

    def test_ouverture(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """xdg-open appelé sur ~/.ssh."""
        d = gestionnaire
        d._on_reveal(None)
        assert env.faux_popen.appels[-1] == ["xdg-open", str(env.ssh)]

    def test_erreur(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Échec d'ouverture : notification."""
        d = gestionnaire
        env.faux_popen.exception = OSError("aucun gestionnaire")
        d._on_reveal(None)
        assert "Cannot open file manager" in notifie(d)


class TestSuppression:
    """Suppression d'une clé sélectionnée."""

    def _selectionne(self, d: Any) -> None:
        """Sélectionne la première clé privée."""
        d._priv_tree.selection.lignes = [0]
        d._priv_tree.selection.donnees = d._priv_store.lignes

    def test_sans_selection(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans sélection : rien ne se passe."""
        d = gestionnaire
        d._on_delete(None)
        assert (env.ssh / "id_ed25519").exists()

    def test_refusee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Refus de la confirmation : la clé reste."""
        d = gestionnaire
        self._selectionne(d)
        d._confirm = lambda _t: False  # type: ignore[method-assign]
        d._on_delete(None)
        assert (env.ssh / "id_ed25519").exists()

    def test_supprime_cle_et_pub(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Confirmation : clé privée et publique supprimées, liste rechargée."""
        d = gestionnaire
        self._selectionne(d)
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._on_delete(None)
        assert not (env.ssh / "id_ed25519").exists()
        assert not (env.ssh / "id_ed25519.pub").exists()
        assert a_notifie(d, "Key deleted")
        assert [ligne[0] for ligne in d._priv_store.lignes] == ["id_rsa"]


class TestVuePublique:
    """Affichage du contenu d'une clé publique."""

    def _selectionne(self, d: Any) -> None:
        """Sélectionne la première clé privée."""
        d._priv_tree.selection.lignes = [0]
        d._priv_tree.selection.donnees = d._priv_store.lignes

    def test_sans_selection(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans clé : notification."""
        d = gestionnaire
        d._on_view_public_key(None)
        assert "No public key" in notifie(d)

    def test_fichier_illisible(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Fichier illisible : notification."""
        d = gestionnaire
        self._selectionne(d)
        (env.ssh / "id_ed25519.pub").chmod(0o000)
        d._on_view_public_key(None)
        (env.ssh / "id_ed25519.pub").chmod(0o644)
        assert "Cannot read" in notifie(d)

    def test_affiche(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Le contenu est lu et passé au dialogue d'affichage."""
        d = gestionnaire
        self._selectionne(d)
        d._on_view_public_key(None)
        zones = [w for w in _enfants_rec(env.zone) if isinstance(w, ZoneTexte)]
        assert zones and zones[0].tampon.texte == "ssh-ed25519 AAAA moi"


class TestRenommage:
    """Renommage d'une clé et de ses fichiers associés."""

    def _selectionne(self, d: Any) -> None:
        """Sélectionne la première clé privée."""
        d._priv_tree.selection.lignes = [0]
        d._priv_tree.selection.donnees = d._priv_store.lignes

    def test_sans_selection(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans clé sélectionnée : notification."""
        d = gestionnaire
        d._on_rename_key(None)
        assert "No key selected" in notifie(d)

    def test_annulation(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Annulation : aucun renommage."""
        d = gestionnaire
        self._selectionne(d)
        env.reponses.append("CANCEL")
        d._on_rename_key(None)
        assert (env.ssh / "id_ed25519").exists()

    def test_separateur_refuse(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un nom avec séparateur de chemin est refusé."""
        d = gestionnaire
        self._selectionne(d)
        monkeypatch.setattr(
            skm,
            "_RenameKeyDialog",
            lambda **_k: SimpleNamespace(
                get_new_name=lambda: "a/b",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._on_rename_key(None)
        assert a_notifie(d, "path separator")

    def test_renommage_complet(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Clé privée, .pub et certificat sont renommés ensemble."""
        d = gestionnaire
        self._selectionne(d)
        (env.ssh / "id_ed25519-cert.pub").write_text("cert", encoding="utf-8")
        monkeypatch.setattr(
            skm,
            "_RenameKeyDialog",
            lambda **_k: SimpleNamespace(
                get_new_name=lambda: "toute_belle",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._on_rename_key(None)
        assert (env.ssh / "toute_belle").exists()
        assert (env.ssh / "toute_belle.pub").exists()
        assert (env.ssh / "toute_belle-cert.pub").exists()
        assert not (env.ssh / "id_ed25519").exists()
        assert a_notifie(d, "Renamed")

    def test_conflit_privee(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un nom déjà pris côté privé est refusé."""
        d = gestionnaire
        self._selectionne(d)
        cle(env.ssh, "existante")
        monkeypatch.setattr(
            skm,
            "_RenameKeyDialog",
            lambda **_k: SimpleNamespace(
                get_new_name=lambda: "existante",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._on_rename_key(None)
        assert a_notifie(d, "already exists")

    def test_conflit_certificat(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un certificat déjà présent sous le nouveau nom est refusé."""
        d = gestionnaire
        self._selectionne(d)
        (env.ssh / "id_ed25519-cert.pub").write_text("cert", encoding="utf-8")
        (env.ssh / "cible-cert.pub").write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            skm,
            "_RenameKeyDialog",
            lambda **_k: SimpleNamespace(
                get_new_name=lambda: "cible",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._on_rename_key(None)
        assert a_notifie(d, "certificate")

    def test_conflit_publique(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un .pub déjà présent sous le nouveau nom est refusé."""
        d = gestionnaire
        self._selectionne(d)
        cle(env.ssh, "cible")
        monkeypatch.setattr(
            skm,
            "_RenameKeyDialog",
            lambda **_k: SimpleNamespace(
                get_new_name=lambda: "cible",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._on_rename_key(None)
        assert a_notifie(d, "already exists")

    def test_publique_seule(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Une .pub orpheline se renomme sans clé privée."""
        d = gestionnaire
        d._notebook.courante = 1
        (env.ssh / "orpheline.pub").write_text("pub", encoding="utf-8")
        d._pub_store.append(
            [
                "orpheline.pub",
                "",
                str(env.ssh / "orpheline.pub"),
                "",
                "600 ✓",
                True,
                False,
                "",
                "?",
                "—",
                False,
                False,
                "—",
            ]
        )
        d._pub_tree.selection.lignes = [0]
        d._pub_tree.selection.donnees = d._pub_store.lignes
        monkeypatch.setattr(
            skm,
            "_RenameKeyDialog",
            lambda **_k: SimpleNamespace(
                get_new_name=lambda: "renommee.pub",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._on_rename_key(None)
        assert (env.ssh / "renommee.pub").exists()

    def test_rien_a_renommer(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ligne sans chemin : notification."""
        d = gestionnaire
        d._notebook.courante = 1
        d._pub_store.append(
            ["fantome", "", "", "", "600 ✓", True, False, "", "?", "—", False, False, "—"]
        )
        d._pub_tree.selection.lignes = [0]
        d._pub_tree.selection.donnees = d._pub_store.lignes
        monkeypatch.setattr(
            skm,
            "_RenameKeyDialog",
            lambda **_k: SimpleNamespace(
                get_new_name=lambda: "autre",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._on_rename_key(None)
        assert a_notifie(d, "Nothing to rename")


class TestPassphrase:
    """Changement de passphrase d'une clé privée."""

    def _selectionne(self, d: Any) -> None:
        """Sélectionne la première clé privée."""
        d._priv_tree.selection.lignes = [0]
        d._priv_tree.selection.donnees = d._priv_store.lignes

    def test_sans_cle(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans clé privée : notification."""
        d = gestionnaire
        d._on_change_passphrase(None)
        assert "No private key" in notifie(d)

    def test_annulation(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Annulation : ssh-keygen -p jamais appelé."""
        d = gestionnaire
        self._selectionne(d)
        env.reponses.append("CANCEL")
        env.faux_run.appels.clear()
        d._on_change_passphrase(None)
        assert env.faux_run.appels == []

    def test_mots_de_passe_differents(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confirmation différente : refus."""
        d = gestionnaire
        self._selectionne(d)
        monkeypatch.setattr(
            skm,
            "_ChangePassphraseDialog",
            lambda **_k: SimpleNamespace(
                get_values=lambda: ("a", "b", "c"),
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._on_change_passphrase(None)
        assert a_notifie(d, "do not match")

    def test_changement_reussi(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Changement OK : commande -p, notification."""
        d = gestionnaire
        self._selectionne(d)
        monkeypatch.setattr(
            skm,
            "_ChangePassphraseDialog",
            lambda **_k: SimpleNamespace(
                get_values=lambda: ("ancien", "nouveau", "nouveau"),
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        env.faux_run.appels.clear()
        d._on_change_passphrase(None)
        cmd = env.faux_run.appels[0]
        assert cmd[1] == "-p"
        assert "Passphrase changed" in notifie(d)

    def test_echec_ssh_keygen(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Échec de ssh-keygen -p : stderr remontée."""
        d = gestionnaire
        self._selectionne(d)
        monkeypatch.setattr(
            skm,
            "_ChangePassphraseDialog",
            lambda **_k: SimpleNamespace(
                get_values=lambda: ("a", "b", "b"),
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        env.faux_run.reponses["ssh-keygen"] = _res(1, stderr="mauvaise passphrase")
        d._on_change_passphrase(None)
        assert "Passphrase change failed" in notifie(d)

    def test_exception(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exception générale : notifiée."""
        d = gestionnaire
        self._selectionne(d)
        monkeypatch.setattr(
            skm,
            "_ChangePassphraseDialog",
            lambda **_k: SimpleNamespace(
                get_values=lambda: ("a", "b", "b"),
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        env.faux_run.reponses["ssh-keygen"] = OSError("boom")
        d._on_change_passphrase(None)
        assert "Passphrase change failed" in notifie(d)


class TestAgent:
    """Charge/décharge d'une clé dans ssh-agent."""

    def _selectionne(self, d: Any) -> None:
        """Sélectionne la première clé privée."""
        d._priv_tree.selection.lignes = [0]
        d._priv_tree.selection.donnees = d._priv_store.lignes

    def test_sans_cle(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans clé privée : notification."""
        d = gestionnaire
        d._on_toggle_agent(None)
        assert "No private key" in notifie(d)

    def test_sans_publique(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans .pub : impossible d'identifier la clé dans l'agent."""
        d = gestionnaire
        self._selectionne(d)
        (env.ssh / "id_ed25519.pub").unlink()
        d._on_toggle_agent(None)
        assert "No public key available" in notifie(d)

    def test_dans_agent(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Empreinte détectée — ou non — dans l'agent."""
        d = gestionnaire
        env.faux_run.reponses["ssh-add"] = _res(
            0, stdout="SHA256:abcdefghijklmnop moi (ED25519)\n"
        )
        assert d._is_key_in_agent(env.ssh / "id_ed25519.pub") is True
        env.faux_run.reponses["ssh-add"] = _res(0, stdout="")
        assert d._is_key_in_agent(env.ssh / "id_ed25519.pub") is False

    def test_agent_indisponible(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """ssh-keygen/ssh-add en échec ou empreinte vide : pas dans l'agent."""
        d = gestionnaire
        env.faux_run.reponses["ssh-keygen"] = _res(1)
        assert d._is_key_in_agent(env.ssh / "id_ed25519.pub") is False
        env.faux_run.reponses["ssh-keygen"] = _res(0, stdout="court")
        env.faux_run.reponses["ssh-add"] = _res(1)
        assert d._is_key_in_agent(env.ssh / "id_ed25519.pub") is False
        env.faux_run.reponses["ssh-add"] = OSError("boom")
        assert d._is_key_in_agent(env.ssh / "id_ed25519.pub") is False

    def test_dechargement_refuse(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Clé dans l'agent, retrait refusé : rien ne bouge."""
        d = gestionnaire
        self._selectionne(d)
        env.faux_run.reponses["ssh-keygen"] = _res(
            0, stdout="256 SHA256:abcdefghijklmnop moi (ED25519)\n"
        )
        env.faux_run.reponses["ssh-add"] = _res(
            0, stdout="SHA256:abcdefghijklmnop moi (ED25519)\n"
        )
        d._confirm = lambda _t: False  # type: ignore[method-assign]
        env.faux_run.appels.clear()
        d._on_toggle_agent(None)
        assert not any(c[0] == "ssh-add" and "-d" in c for c in env.faux_run.appels)

    def test_dechargement(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Retrait confirmé : ssh-add -d."""
        d = gestionnaire
        self._selectionne(d)
        env.faux_run.reponses["ssh-keygen"] = _res(
            0, stdout="256 SHA256:abcdefghijklmnop moi (ED25519)\n"
        )
        env.faux_run.reponses["ssh-add"] = _res(
            0, stdout="SHA256:abcdefghijklmnop moi (ED25519)\n"
        )
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        env.faux_run.appels.clear()
        d._on_toggle_agent(None)
        cmds_ssh_add = [
            c for c in env.faux_run.appels if c[0] == "ssh-add" and len(c) > 1 and c[1] == "-d"
        ]
        assert cmds_ssh_add and cmds_ssh_add[0][2] == str(env.ssh / "id_ed25519.pub")
        assert a_notifie(d, "Removed from ssh-agent")

    def test_dechargement_echec(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Échec du retrait : message d'erreur."""
        d = gestionnaire
        self._selectionne(d)
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._is_key_in_agent = lambda _p: True  # type: ignore[method-assign]
        env.faux_run.reponses["ssh-add"] = _res(1, stderr="refus")
        d._on_toggle_agent(None)
        assert "Failed to remove" in notifie(d)

    def test_dechargement_exception(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Exception pendant ssh-add -d : notifiée."""
        d = gestionnaire
        self._selectionne(d)
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._is_key_in_agent = lambda _p: True  # type: ignore[method-assign]
        env.faux_run.reponses["ssh-add"] = OSError("boom")
        d._on_toggle_agent(None)
        assert "ssh-add failed" in notifie(d)

    def test_chargement_refuse(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Clé absente de l'agent, ajout refusé : rien ne bouge."""
        d = gestionnaire
        self._selectionne(d)
        env.faux_run.reponses["ssh-keygen"] = _res(
            0, stdout="256 SHA256:abcdefghijklmnop moi (ED25519)\n"
        )
        env.faux_run.reponses["ssh-add"] = _res(0, stdout="")
        d._confirm = lambda _t: False  # type: ignore[method-assign]
        env.faux_run.appels.clear()
        d._on_toggle_agent(None)
        assert not any(c[0] == "ssh-add" and c[1:] != ["-l"] for c in env.faux_run.appels)

    def test_chargement(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Ajout confirmé : ssh-add, notification."""
        d = gestionnaire
        self._selectionne(d)
        env.faux_run.reponses["ssh-keygen"] = _res(
            0, stdout="256 SHA256:abcdefghijklmnop moi (ED25519)\n"
        )
        env.faux_run.reponses["ssh-add"] = _res(0, stdout="")
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        env.faux_run.appels.clear()
        d._on_toggle_agent(None)
        cmds_ssh_add = [c for c in env.faux_run.appels if c[0] == "ssh-add" and c[1:] != ["-l"]]
        assert cmds_ssh_add and cmds_ssh_add[0] == ["ssh-add", str(env.ssh / "id_ed25519")]
        assert a_notifie(d, "Added to ssh-agent")

    def test_chargement_echec(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Échec de l'ajout (passphrase ?) : message."""
        d = gestionnaire
        self._selectionne(d)
        env.faux_run.reponses["ssh-keygen"] = _res(
            0, stdout="256 SHA256:abcdefghijklmnop moi (ED25519)\n"
        )
        env.faux_run.reponses["ssh-add"] = _res(1, stdout="", stderr="passphrase requise")
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        env.faux_run.appels.clear()
        d._on_toggle_agent(None)
        assert "Failed to add" in notifie(d)

    def test_chargement_expire(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Délai dépassé : message explicite."""
        d = gestionnaire
        self._selectionne(d)
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        env.faux_run.reponses["ssh-keygen"] = _res(
            0, stdout="256 SHA256:abcdefghijklmnop moi (ED25519)\n"
        )
        env.faux_run.reponses["ssh-add"] = subprocess.TimeoutExpired("ssh-add", 10)
        d._on_toggle_agent(None)
        assert "timed out" in notifie(d)

    def test_chargement_exception(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Exception pendant ssh-add : notifiée."""
        d = gestionnaire
        self._selectionne(d)
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        env.faux_run.reponses["ssh-keygen"] = _res(
            0, stdout="256 SHA256:abcdefghijklmnop moi (ED25519)\n"
        )
        env.faux_run.reponses["ssh-add"] = OSError("boom")
        d._on_toggle_agent(None)
        assert "ssh-add failed" in notifie(d)


# ---------------------------------------------------------------------------
# Known Hosts (interface)
# ---------------------------------------------------------------------------


class TestKnownHostsInterface:
    """Copie, test et suppression d'entrées known_hosts."""

    def test_copie_sans_selection(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans ligne : notification."""
        d = gestionnaire
        d._on_copy_known_host_fingerprint(None)
        assert "No known_hosts entry" in notifie(d)

    def test_copie(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Empreinte copiée : notification."""
        d = gestionnaire
        d._kh_tree.selection.lignes = [0]
        d._kh_tree.selection.donnees = d._kh_store.lignes
        d._kh_tree.selection.get_selected = lambda: (d._kh_store, 0)  # type: ignore[method-assign]
        d._on_copy_known_host_fingerprint(None)
        assert "Fingerprint copied" in notifie(d)

    def test_test_fichier_absent(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """known_hosts introuvable : notification."""
        d = gestionnaire
        (env.ssh / "known_hosts").unlink()
        d._on_test_known_hosts(None)
        assert "not found" in notifie(d)

    def test_test_sans_candidats(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Aucun hôte candidat : notification."""
        d = gestionnaire
        (env.ssh / "config").unlink()
        d._gcm_hosts_provider = None
        d._on_test_known_hosts(None)
        assert "No candidate hosts" in notifie(d)

    def test_test_avec_resultats(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Le test passe par ssh-keygen -F et affiche la fenêtre de résultats."""
        d = gestionnaire
        env.faux_run.reponses["ssh-keygen"] = _res(0, stdout="hote.example\n")
        env.faux_run.appels.clear()
        d._on_test_known_hosts(None)
        assert any(c[1] == "-F" for c in env.faux_run.appels)
        assert "hote.example" in env.faux_run.appels[0]

    def test_test_exception_par_hote(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Une exception par hôte devient un résultat négatif, pas un crash."""
        d = gestionnaire
        compteur = {"n": 0}

        def alternance(_cmd: list[str], _kwargs: Any) -> Any:
            compteur["n"] += 1
            if compteur["n"] % 2:
                return _res(0, stdout="trouvé")
            raise OSError("boom")

        env.faux_run.reponses["ssh-keygen"] = alternance
        d._on_test_known_hosts(None)
        # La fenêtre de résultats a été créée sans lever.

    def test_suppression_sans_selection(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans ligne : notification."""
        d = gestionnaire
        d._on_delete_known_host(None)
        assert "No known_hosts entry" in notifie(d)

    def test_suppression_refusee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Refus de la confirmation : l'entrée reste."""
        d = gestionnaire
        d._kh_tree.selection.lignes = [0]
        d._kh_tree.selection.donnees = d._kh_store.lignes
        d._kh_tree.selection.get_selected = lambda: (d._kh_store, 0)  # type: ignore[method-assign]
        d._confirm = lambda _t: False  # type: ignore[method-assign]
        d._on_delete_known_host(None)
        assert "hote.example" in (env.ssh / "known_hosts").read_text()

    def test_suppression_introuvable(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Ligne disparue du fichier : rafraîchissement, pas d'écriture."""
        d = gestionnaire
        d._kh_tree.selection.lignes = [0]
        d._kh_tree.selection.donnees = d._kh_store.lignes
        d._kh_tree.selection.get_selected = lambda: (d._kh_store, 0)  # type: ignore[method-assign]
        (env.ssh / "known_hosts").write_text("autre.example ssh-ed25519 AAAA\n", encoding="utf-8")
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._on_delete_known_host(None)
        assert a_notifie(d, "Entry not found")

    def test_suppression(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Confirmation : la ligne disparaît du fichier."""
        d = gestionnaire
        d._kh_tree.selection.lignes = [0]
        d._kh_tree.selection.donnees = d._kh_store.lignes
        d._kh_tree.selection.get_selected = lambda: (d._kh_store, 0)  # type: ignore[method-assign]
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._confirm_root_write = lambda _fs, _a: True  # type: ignore[method-assign]
        d._on_delete_known_host(None)
        assert "hote.example" not in (env.ssh / "known_hosts").read_text()
        assert a_notifie(d, "entry removed")

    def test_suppression_ecriture_refusee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Écriture refusée : message d'échec."""
        d = gestionnaire
        d._kh_tree.selection.lignes = [0]
        d._kh_tree.selection.donnees = d._kh_store.lignes
        d._kh_tree.selection.get_selected = lambda: (d._kh_store, 0)  # type: ignore[method-assign]
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._confirm_root_write = lambda _fs, _a: True  # type: ignore[method-assign]
        d._local_fs.write_text = lambda *_a, **_k: False  # type: ignore[method-assign]
        d._on_delete_known_host(None)
        assert a_notifie(d, "Failed to update")


# ---------------------------------------------------------------------------
# Authorized Keys (interface)
# ---------------------------------------------------------------------------


class TestAuthorizedKeysInterface:
    """Bascule, ajout et suppression d'entrées authorized_keys."""

    def test_bascule_refusee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Refus de la confirmation : aucun changement."""
        d = gestionnaire
        d._ak_store.append([True, "ssh-ed25519", "fp", "moi", "ssh-ed25519 BBBB moi", 1])
        d._confirm = lambda _t: False  # type: ignore[method-assign]
        d._on_toggle_authorized_key(None, "0")
        assert "ssh-ed25519 BBBB moi" in (env.ssh / "authorized_keys").read_text()

    def test_bascule_commente(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Une entrée active est commentée."""
        d = gestionnaire
        d._ak_store.append([True, "ssh-ed25519", "fp", "moi", "ssh-ed25519 BBBB moi", 1])
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._confirm_root_write = lambda _fs, _a: True  # type: ignore[method-assign]
        d._on_toggle_authorized_key(None, "0")
        assert "# ssh-ed25519 BBBB moi" in (env.ssh / "authorized_keys").read_text()

    def test_bascule_decommente(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Une entrée commentée est décommentée."""
        d = gestionnaire
        (env.ssh / "authorized_keys").write_text("# ssh-ed25519 BBBB moi\n", encoding="utf-8")
        d._load_authorized_keys()
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._confirm_root_write = lambda _fs, _a: True  # type: ignore[method-assign]
        d._on_toggle_authorized_key(None, "0")
        assert (env.ssh / "authorized_keys").read_text().startswith("ssh-ed25519 BBBB moi")

    def test_bascule_fichier_change(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Ligne décalée dans le fichier : rafraîchissement, pas d'écriture."""
        d = gestionnaire
        d._ak_store.append([True, "ssh-ed25519", "fp", "moi", "ssh-ed25519 BBBB moi", 1])
        (env.ssh / "authorized_keys").write_text("autre ligne\n", encoding="utf-8")
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._confirm_root_write = lambda _fs, _a: True  # type: ignore[method-assign]
        d._on_toggle_authorized_key(None, "0")
        assert a_notifie(d, "File changed")

    def test_bascule_ecriture_refusee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Écriture impossible : message d'échec."""
        d = gestionnaire
        d._ak_store.append([True, "ssh-ed25519", "fp", "moi", "ssh-ed25519 BBBB moi", 1])
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._confirm_root_write = lambda _fs, _a: True  # type: ignore[method-assign]
        d._local_fs.write_text = lambda *_a, **_k: False  # type: ignore[method-assign]
        d._on_toggle_authorized_key(None, "0")
        assert a_notifie(d, "Failed to update")

    def test_ajout_annule(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Annulation : rien d'ajouté."""
        d = gestionnaire
        d._on_add_authorized_key(None)
        assert (env.ssh / "authorized_keys").read_text() == "ssh-ed25519 BBBB moi\n"

    def test_ajout_vide(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ligne vide : rien d'ajouté."""
        d = gestionnaire
        monkeypatch.setattr(
            skm,
            "_AddAuthorizedKeyDialog",
            lambda **_k: SimpleNamespace(
                get_key_line=lambda: "   ",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._on_add_authorized_key(None)
        assert (env.ssh / "authorized_keys").read_text() == "ssh-ed25519 BBBB moi\n"

    def test_ajout_invalide(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Une ligne sans type de clé connu est refusée."""
        d = gestionnaire
        monkeypatch.setattr(
            skm,
            "_AddAuthorizedKeyDialog",
            lambda **_k: SimpleNamespace(
                get_key_line=lambda: "ceci n est pas une cle",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._on_add_authorized_key(None)
        assert a_notifie(d, "does not look like")

    def test_ajout(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Une ligne valide est ajoutée en fin de fichier."""
        d = gestionnaire
        monkeypatch.setattr(
            skm,
            "_AddAuthorizedKeyDialog",
            lambda **_k: SimpleNamespace(
                get_key_line=lambda: "ssh-rsa CCCC autre",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._confirm_root_write = lambda _fs, _a: True  # type: ignore[method-assign]
        d._on_add_authorized_key(None)
        assert "ssh-rsa CCCC autre" in (env.ssh / "authorized_keys").read_text()
        assert a_notifie(d, "Key added")

    def test_ajout_sans_retour_ligne_final(
        self, env: SimpleNamespace, gestionnaire: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        r"""Un fichier sans \n final ne colle pas la nouvelle clé à la dernière."""
        d = gestionnaire
        (env.ssh / "authorized_keys").write_text("ssh-ed25519 BBBB moi", encoding="utf-8")
        monkeypatch.setattr(
            skm,
            "_AddAuthorizedKeyDialog",
            lambda **_k: SimpleNamespace(
                get_key_line=lambda: "ssh-rsa CCCC autre",
                destroy=lambda: None,
            ),
        )
        env.reponses.append("OK")
        d._confirm_root_write = lambda _fs, _a: True  # type: ignore[method-assign]
        d._on_add_authorized_key(None)
        assert "\nssh-rsa CCCC autre" in (env.ssh / "authorized_keys").read_text()

    def test_suppression_sans_selection(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Sans ligne : notification."""
        d = gestionnaire
        d._on_delete_authorized_key(None)
        assert "No authorized_keys entry" in notifie(d)

    def test_suppression_refusee(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Refus : l'entrée reste."""
        d = gestionnaire
        d._ak_tree.selection.lignes = [0]
        d._ak_tree.selection.donnees = d._ak_store.lignes
        d._ak_tree.selection.get_selected = lambda: (d._ak_store, 0)  # type: ignore[method-assign]
        d._confirm = lambda _t: False  # type: ignore[method-assign]
        d._on_delete_authorized_key(None)
        assert "ssh-ed25519 BBBB moi" in (env.ssh / "authorized_keys").read_text()

    def test_suppression(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Confirmation : la ligne disparaît."""
        d = gestionnaire
        d._ak_tree.selection.lignes = [0]
        d._ak_tree.selection.donnees = d._ak_store.lignes
        d._ak_tree.selection.get_selected = lambda: (d._ak_store, 0)  # type: ignore[method-assign]
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._confirm_root_write = lambda _fs, _a: True  # type: ignore[method-assign]
        d._on_delete_authorized_key(None)
        assert "ssh-ed25519 BBBB moi" not in (env.ssh / "authorized_keys").read_text()
        assert a_notifie(d, "entry removed")

    def test_suppression_introuvable(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Ligne introuvable dans le fichier : rafraîchissement."""
        d = gestionnaire
        d._ak_store.append([True, "ssh-ed25519", "fp", "moi", "ssh-ed25519 BBBB moi", 1])
        d._ak_tree.selection.lignes = [0]
        d._ak_tree.selection.donnees = d._ak_store.lignes
        d._ak_tree.selection.get_selected = lambda: (d._ak_store, 0)  # type: ignore[method-assign]
        (env.ssh / "authorized_keys").write_text("autre\n", encoding="utf-8")
        d._confirm = lambda _t: True  # type: ignore[method-assign]
        d._confirm_root_write = lambda _fs, _a: True  # type: ignore[method-assign]
        d._on_delete_authorized_key(None)
        assert a_notifie(d, "Entry not found")


# ---------------------------------------------------------------------------
# Notifications et confirmations
# ---------------------------------------------------------------------------


class TestNotifications:
    """Barre d'état et dialogues de confirmation."""

    def test_notification(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Un message poussé remplace le précédent."""
        d = gestionnaire
        d._notify("premier")
        d._notify("second")
        assert d._status_bar.messages == ["second"]

    def test_confirmation_oui(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """run_dialog_sync → YES : confirmation acceptée."""
        d = gestionnaire
        env.reponses.append("YES")
        assert d._confirm("une question ?") is True

    def test_confirmation_non(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """run_dialog_sync → NO : confirmation refusée."""
        d = gestionnaire
        env.reponses.append("NO")
        assert d._confirm("une question ?") is False

    def test_garde_fou_root(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Compte non root : pas de confirmation supplémentaire."""
        d = gestionnaire
        assert d._confirm_root_write(d._local_fs, "modifier") is True

    def test_garde_fou_root_refuse(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Compte root : confirmation exigée, refus possible."""
        d = gestionnaire
        distant = skm.SSHTargetSpec(user="root", host="hote", port=22)
        fs = skm._RemoteFS(distant)
        env.reponses.append("NO")
        assert d._confirm_root_write(fs, "modifier") is False

    def test_garde_fou_root_accepte(self, env: SimpleNamespace, gestionnaire: Any) -> None:
        """Compte root : confirmation donnée."""
        d = gestionnaire
        distant = skm.SSHTargetSpec(user="root", host="hote", port=22)
        fs = skm._RemoteFS(distant)
        env.reponses.append("YES")
        assert d._confirm_root_write(fs, "modifier") is True
