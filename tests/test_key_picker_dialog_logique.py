# SPDX-License-Identifier: GPL-3.0-or-later
"""Sélecteur de clé SSH GTK3 : logique de ``key_picker_dialog.py`` (#293, palier 15).

``key_picker_dialog.py`` n'était couvert qu'à 10,6 %. Ce fichier couvre sa
logique sans GTK et sans toucher au vrai ``~/.ssh`` (``Path.home`` pointe
vers un dossier temporaire) :

- construction (boutons, colonnes, liste) et lecture de ``~/.ssh`` : fichiers
  exclus, cachés, dossiers, clés privées avec ou sans ``.pub``, ``.pub``
  orphelines, dossier absent ou vide ;
- empreinte (``ssh-keygen -lf``) : succès, sortie courte, échec, exception ;
- permissions : correctes, trop larges, fichier illisible ;
- rendu des colonnes (icône, nom, type) ;
- sélection, double-clic, réponses Use / Generate… / Cancel, émission de
  ``key-selected`` (plusieurs clés jointes par des virgules) ;
- passage par le gestionnaire de clés et retour au premier plan.

``Gtk`` et ``subprocess.run`` sont remplacés par des doublures (monkeypatch,
sans effet sur les autres tests).
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

import test_gcm  # noqa: E402, F401  (pose les doublures gi/Gtk avant l'import)

# Import après test_gcm (doublures gi) : un tri des imports l'inverserait.
kpd: Any = importlib.import_module("key_picker_dialog")

# ---------------------------------------------------------------------------
# Doublures
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

    def set_markup(self, texte: str) -> None:
        """Mémorise le balisage."""
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

    add = pack_start
    append_column = pack_start

    def get_parent(self) -> Any:
        """Parent du widget."""
        return self.parent

    def get_selection(self) -> Selection:
        """Sélection d'un TreeView."""
        return self.selection

    def get_style_context(self) -> Any:
        """Contexte de style qui note les classes."""
        return SimpleNamespace(add_class=self.classes.append)


class Selection:
    """``Gtk.TreeSelection`` factice (sélection multiple)."""

    def __init__(self) -> None:
        """Rien de sélectionné au départ."""
        self.lignes: list[int] = []
        self.signaux: dict[str, Any] = {}
        self.mode: Any = None

    def set_mode(self, mode: Any) -> None:
        """Mode de sélection."""
        self.mode = mode

    def connect(self, signal: str, rappel: Any) -> None:
        """Mémorise le gestionnaire."""
        self.signaux[signal] = rappel

    def get_selected_rows(self) -> tuple[Any, list[int]]:
        """Modèle et chemins sélectionnés."""
        return (None, list(self.lignes))


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

    def get_iter(self, chemin: int) -> Iter | None:
        """Itérateur d'un chemin, ``None`` hors limites."""
        return Iter(chemin) if 0 <= chemin < len(self.lignes) else None

    def __len__(self) -> int:
        """Nombre de lignes."""
        return len(self.lignes)

    def __getitem__(self, it: Iter) -> list[Any]:
        """Ligne d'un itérateur."""
        return self.lignes[it.indice]


class Iter:
    """``Gtk.TreeIter`` factice : toujours vrai, même pour la ligne 0."""

    def __init__(self, indice: int) -> None:
        """Mémorise l'indice de la ligne."""
        self.indice = indice


def faux_gtk(dialog_base: Any) -> SimpleNamespace:
    """Espace de noms ``Gtk`` factice ; ``Dialog`` reste la doublure de test_gcm."""
    gtk = SimpleNamespace(
        Dialog=dialog_base,
        ListStore=ListStore,
        ResponseType=SimpleNamespace(OK="OK", CANCEL="CANCEL", HELP="HELP"),
        PolicyType=SimpleNamespace(AUTOMATIC=0),
        ShadowType=SimpleNamespace(IN=0),
        SelectionMode=SimpleNamespace(MULTIPLE="multiple"),
        Justification=SimpleNamespace(CENTER=0),
        IconSize=SimpleNamespace(SMALL_TOOLBAR="petite"),
    )
    for nom in (
        "Label",
        "ScrolledWindow",
        "TreeView",
        "TreeViewColumn",
        "CellRendererPixbuf",
        "CellRendererText",
    ):
        setattr(gtk, nom, Widget)
    return gtk


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """Doublures GTK, ``~`` temporaire et ``ssh-keygen`` factice."""
    maison = tmp_path / "maison"
    ssh = maison / ".ssh"
    ssh.mkdir(parents=True)
    monkeypatch.setattr(kpd.Path, "home", classmethod(lambda _cls: maison))
    monkeypatch.setattr(kpd, "Gtk", faux_gtk(kpd.Gtk.Dialog))

    etat = SimpleNamespace(
        ssh=ssh,
        boutons=[],
        emis=[],
        detruit=0,
        modal=[],
        deconnectes=[],
        keygen=[],
        zone=Widget(),
    )

    def keygen(cmd: list[str], **_k: Any) -> Any:
        etat.keygen.append(cmd)
        return SimpleNamespace(returncode=0, stdout="256 SHA256:" + "x" * 50 + " moi (ED25519)\n")

    monkeypatch.setattr(kpd.subprocess, "run", keygen)

    def ajouter_bouton(_self: Any, libelle: str, reponse: Any) -> Widget:
        bouton = Widget(libelle, reponse)
        etat.boutons.append(bouton)
        return bouton

    signaux: dict[str, Any] = {}

    def connecter(_self: Any, signal: str, rappel: Any) -> int:
        signaux[signal] = rappel
        return len(signaux)

    classe = kpd.KeyPickerDialog
    for nom, valeur in (
        ("set_default_size", lambda _self, *t: None),
        ("add_button", ajouter_bouton),
        ("get_content_area", lambda _self: etat.zone),
        ("show_all", lambda _self: None),
        ("connect", connecter),
        ("emit", lambda _self, signal, valeur: etat.emis.append((signal, valeur))),
        ("destroy", lambda _self: setattr(etat, "detruit", etat.detruit + 1)),
        ("set_modal", lambda _self, m: etat.modal.append(m)),
        ("disconnect", lambda _self, hid: etat.deconnectes.append(hid)),
    ):
        monkeypatch.setattr(classe, nom, valeur, raising=False)
    etat.signaux = signaux
    return etat


def cle(dossier: Path, nom: str, mode: int = 0o600, pub: bool = True) -> Path:
    """Crée une clé privée (et sa publique) dans *dossier*."""
    priv = dossier / nom
    priv.write_text("PRIVEE", encoding="utf-8")
    priv.chmod(mode)
    if pub:
        (dossier / f"{nom}.pub").write_text("ssh-ed25519 AAAA moi", encoding="utf-8")
    return priv


@pytest.fixture
def ssh_rempli(env: SimpleNamespace) -> SimpleNamespace:
    """``~/.ssh`` avec clés, fichiers exclus, caché, dossier et ``.pub`` orpheline."""
    cle(env.ssh, "id_ed25519")
    cle(env.ssh, "id_large", mode=0o644, pub=False)
    (env.ssh / "orpheline.pub").write_text("ssh-rsa AAAA x", encoding="utf-8")
    for nom in ("config", "known_hosts", "authorized_keys", "environment", ".cache"):
        (env.ssh / nom).write_text("x", encoding="utf-8")
    (env.ssh / "sous_dossier").mkdir()
    return env


@pytest.fixture
def dlg(ssh_rempli: SimpleNamespace) -> Any:
    """Sélecteur construit sur ``~/.ssh`` rempli."""
    return kpd.KeyPickerDialog(parent="parent")


# ---------------------------------------------------------------------------
# Construction et lecture de ~/.ssh
# ---------------------------------------------------------------------------


def test_construction(env: SimpleNamespace, dlg: Any) -> None:
    """Trois boutons, Use grisé et mis en avant ; colonnes ; réponse branchée."""
    assert [b.args for b in env.boutons] == [
        ("_Cancel", "CANCEL"),
        ("⚙ Generate…", "HELP"),
        ("_Use", "OK"),
    ]
    assert dlg._btn_use.sensible is False
    assert dlg._btn_use.classes == ["suggested-action"]
    assert dlg._tree.selection.mode == "multiple"
    assert len(dlg._tree.enfants) == 4
    assert set(env.signaux) == {"response"}
    assert set(dlg._tree.signaux) == {"row-activated"}
    assert set(dlg._tree.selection.signaux) == {"changed"}
    assert "~/.ssh/" in env.zone.enfants[0].texte


def test_lecture_des_cles(env: SimpleNamespace, dlg: Any) -> None:
    """Clés listées dans l'ordre ; exclus, cachés et dossiers ignorés."""
    lignes = dlg._store.lignes
    assert [lg[0] for lg in lignes] == ["id_ed25519", "id_large", "orpheline.pub"]
    ed, large, orpheline = lignes
    assert ed[1] == str(env.ssh / "id_ed25519")
    assert ed[2] == str(env.ssh / "id_ed25519.pub")
    assert ed[3] == "SHA256:" + "x" * 25
    assert ed[4:] == ["600 ✓", True]
    assert large[2] == "" and large[4:] == ["644 ⚠", False]
    assert orpheline[1] == str(env.ssh / "orpheline.pub") and orpheline[2] == ""
    # Empreinte calculée sur la publique quand elle existe, sinon sur le fichier.
    assert [c[-1] for c in env.keygen] == [
        str(env.ssh / "id_ed25519.pub"),
        str(env.ssh / "id_large"),
        str(env.ssh / "orpheline.pub"),
    ]
    assert dlg._no_key_label.visible is False
    assert dlg._tree.parent.visible is True


def test_dossier_ssh_absent(env: SimpleNamespace) -> None:
    """Sans ~/.ssh : message « aucune clé », liste masquée."""
    env.ssh.rmdir()
    d = kpd.KeyPickerDialog(parent="parent")
    assert d._store.lignes == []
    assert d._no_key_label.visible is True
    assert d._tree.parent.visible is False


def test_dossier_ssh_vide(env: SimpleNamespace) -> None:
    """~/.ssh sans clé : message « aucune clé »."""
    (env.ssh / "config").write_text("Host x\n", encoding="utf-8")
    d = kpd.KeyPickerDialog(parent="parent")
    assert d._store.lignes == []
    assert d._no_key_label.visible is True


def test_liste_sans_parent(dlg: Any) -> None:
    """Liste pas encore rangée dans son ScrolledWindow : seul le message bascule."""
    dlg._tree.parent = None
    dlg._show_empty(True)
    assert dlg._no_key_label.visible is True


# ---------------------------------------------------------------------------
# Empreinte et permissions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("retour", "sortie", "attendu"),
    [
        (0, "256 SHA256:court moi\n", "SHA256:court"),
        (0, "seul\n", ""),
        (1, "", ""),
    ],
)
def test_empreinte(
    monkeypatch: pytest.MonkeyPatch,
    dlg: Any,
    retour: int,
    sortie: str,
    attendu: str,
) -> None:
    """Empreinte abrégée ; sortie trop courte ou échec : chaîne vide."""
    monkeypatch.setattr(
        kpd.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=retour, stdout=sortie),
    )
    assert dlg._get_fingerprint(Path("/x")) == attendu


def test_empreinte_exception(monkeypatch: pytest.MonkeyPatch, dlg: Any) -> None:
    """``ssh-keygen`` absent : chaîne vide."""

    def absent(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("ssh-keygen")

    monkeypatch.setattr(kpd.subprocess, "run", absent)
    assert dlg._get_fingerprint(Path("/x")) == ""


def test_permissions(env: SimpleNamespace, dlg: Any) -> None:
    """400 correct, 640 trop large, fichier disparu illisible."""
    assert dlg._check_permissions(cle(env.ssh, "k400", 0o400, pub=False)) == ("400 ✓", True)
    assert dlg._check_permissions(cle(env.ssh, "k640", 0o640, pub=False)) == ("640 ⚠", False)
    assert dlg._check_permissions(env.ssh / "absente") == ("?", False)


# ---------------------------------------------------------------------------
# Rendu des colonnes
# ---------------------------------------------------------------------------


def test_rendu_icone_nom_type(dlg: Any) -> None:
    """Icône, nom (orange si permissions larges) et type de chaque ligne."""
    modele = dlg._store
    icones, noms, types = [], [], []
    for it in map(Iter, range(3)):
        cellule = Widget()
        dlg._render_icon(None, cellule, modele, it, None)
        icones.append(cellule.proprietes["icon-name"])
        assert cellule.proprietes["stock-size"] == "petite"
        cellule = Widget()
        dlg._render_name(None, cellule, modele, it, None)
        noms.append(cellule.proprietes)
        cellule = Widget()
        dlg._render_type(None, cellule, modele, it, None)
        types.append(cellule.proprietes["text"])
    assert icones == [
        "channel-secure-symbolic",
        "channel-insecure-symbolic",
        "dialog-information-symbolic",
    ]
    assert noms == [
        {"text": "id_ed25519"},
        {"markup": '<span color="orange">id_large</span>'},
        {"text": "orpheline.pub"},
    ]
    assert types == ["private", "private", "public"]


def test_fonctions_de_rendu_branchees(dlg: Any) -> None:
    """Les colonnes Nom et Type délèguent aux fonctions de rendu."""
    colonne_nom, colonne_type = dlg._tree.enfants[:2]
    assert [f for _c, f in colonne_nom.fonctions] == [dlg._render_icon, dlg._render_name]
    assert [f for _c, f in colonne_type.fonctions] == [dlg._render_type]


# ---------------------------------------------------------------------------
# Sélection et réponses
# ---------------------------------------------------------------------------


def test_selection_active_use(dlg: Any) -> None:
    """Use s'active dès qu'une ligne est sélectionnée."""
    selection = dlg._tree.selection
    selection.lignes = [1]
    selection.signaux["changed"](selection)
    assert dlg._btn_use.sensible is True
    selection.lignes = []
    dlg._on_selection_changed(selection)
    assert dlg._btn_use.sensible is False


def test_double_clic(env: SimpleNamespace, dlg: Any) -> None:
    """Double-clic : émet la clé de la ligne et ferme ; ligne hors limites : rien."""
    dlg._tree.signaux["row-activated"](dlg._tree, 7, None)
    assert env.emis == [] and env.detruit == 0
    dlg._on_row_activated(dlg._tree, 0, None)
    assert env.emis == [("key-selected", str(env.ssh / "id_ed25519"))]
    assert env.detruit == 1


def test_use_plusieurs_cles(env: SimpleNamespace, dlg: Any) -> None:
    """Use : clés jointes par des virgules, ``.pub`` orpheline ramenée à la privée."""
    dlg._tree.selection.lignes = [0, 2, 9]
    env.signaux["response"](dlg, "OK")
    assert env.emis == [
        ("key-selected", f"{env.ssh / 'id_ed25519'}, {env.ssh / 'orpheline'}"),
    ]
    assert env.detruit == 1


def test_use_sans_selection(env: SimpleNamespace, dlg: Any) -> None:
    """Use sans ligne valide : rien n'est émis, le dialogue reste ouvert."""
    dlg._tree.selection.lignes = [9]
    dlg._on_response(dlg, "OK")
    assert env.emis == [] and env.detruit == 0


def test_cancel(env: SimpleNamespace, dlg: Any) -> None:
    """Cancel (ou fermeture) détruit le dialogue sans rien émettre."""
    dlg._on_response(dlg, "CANCEL")
    assert env.emis == [] and env.detruit == 1


# ---------------------------------------------------------------------------
# Generate… : gestionnaire de clés
# ---------------------------------------------------------------------------


def test_generate_puis_retour(
    monkeypatch: pytest.MonkeyPatch, env: SimpleNamespace, dlg: Any
) -> None:
    """Generate… ouvre le gestionnaire ; au retour, modalité et liste rétablies."""
    ouverts: list[str] = []
    plugin = SimpleNamespace(manage_ssh_keys=lambda: ouverts.append("sshkeys"))
    registre = SimpleNamespace(get=lambda nom: plugin if nom == "ssh" else None)
    faux = SimpleNamespace(wMain=SimpleNamespace(plugin_registry=registre))
    monkeypatch.setitem(sys.modules, "gnome_connection_manager", faux)

    dlg._on_response(dlg, "HELP")
    assert ouverts == ["sshkeys"] and env.modal == [False]
    gestionnaire = env.signaux["focus-in-event"]
    identifiant = dlg._key_manager_focus_handler_id
    assert identifiant is not None

    # Second passage avant le retour : pas de second gestionnaire de focus.
    dlg._open_key_manager()
    assert dlg._key_manager_focus_handler_id == identifiant
    assert ouverts == ["sshkeys", "sshkeys"]

    cle(env.ssh, "id_nouvelle")
    assert gestionnaire(dlg, None) is False
    assert env.deconnectes == [identifiant]
    assert dlg._key_manager_focus_handler_id is None
    assert env.modal == [False, False, True]
    assert "id_nouvelle" in [lg[0] for lg in dlg._store.lignes]

    # Retour sans gestionnaire en attente : pas de déconnexion.
    dlg._on_regain_focus_from_key_manager(dlg, None)
    assert env.deconnectes == [identifiant]
