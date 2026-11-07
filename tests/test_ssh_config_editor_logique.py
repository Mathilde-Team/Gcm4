# SPDX-License-Identifier: GPL-3.0-or-later
"""Éditeur de ``~/.ssh/config`` : logique de l'écran GTK3 (#293, palier 14).

``ssh_config_editor.py`` n'était couvert qu'à 0 %. Ce fichier couvre sa
logique sans GTK, sans réseau et sans toucher au vrai ``~/.ssh`` :

- ``SshConfigEditorDialog`` : construction (onglets Visual et Raw/Diff, liste
  fusionnée personnel + partagé, toast « fichier absent »), boutons
  Save/Cancel (fenêtre autonome et onglet épinglé), redirections de port,
  filtre, aller-retour formulaire ↔ hôte (alias, port, IdentityFile
  multiple), sélecteur de fichier Raw, synchronisation Raw ↔ modèle, diff,
  ``save`` (onglet Raw, formulaire invalide, échec d'écriture), validation,
  sélecteur et gestionnaire de clés, ajout, suppression, déplacement d'hôte,
  test de connexion, copie de la commande ``ssh``, changement d'onglet ;
- ``_SshTestDialog`` : sortie de ``ssh``, délai dépassé et ``ssh`` absent
  (#414) ;
- hôte à plusieurs alias chargé dans le formulaire (#415) ;
- ``_show_toast``.

Les fichiers personnel et partagé sont des fichiers temporaires ; ``Gtk``,
``GLib``, ``Gdk``, ``Pango``, ``run_dialog_sync``, ``KeyPickerDialog``,
``SSHKeyManagerDialog`` et ``subprocess.run`` sont remplacés par des
doublures (monkeypatch, sans effet sur les autres tests).
"""

from __future__ import annotations

import importlib
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

# Import après test_gcm (doublures gi) : un tri des imports l'inverserait.
sce: Any = importlib.import_module("ssh_config_editor")
core: Any = importlib.import_module("plugins.ssh.core")

PERSO = """\
Host web1 web
    HostName 10.0.0.1
    User root
    Port 2222
    IdentityFile ~/.ssh/id_a
    IdentityFile ~/.ssh/id_b
    DynamicForward 1080
    LocalForward 8080 localhost:80
    RemoteForward 9000 db:5432
    LocalForward 7000

Host bastion
    HostName b.example.com
"""

PARTAGE = """\
Host commun
    HostName s.example.com
"""

# ---------------------------------------------------------------------------
# Doublures
# ---------------------------------------------------------------------------


class Widget:
    """Widget générique : texte, sensibilité, visibilité, enfants, signaux."""

    def __init__(self, *_args: Any, label: str = "", **kwargs: Any) -> None:
        """Initialise l'état."""
        self.texte = label
        self.kwargs = kwargs
        self.visible = False
        self.sensible = True
        self.enfants: list[Any] = []
        self.signaux: dict[str, tuple[Any, tuple[Any, ...]]] = {}
        self.ids: list[tuple[str, str]] = []
        self.id_actif: str | None = None
        self.classes: list[str] = []
        self.page = 0
        self.enfant_visible: str | None = None
        self.valeur = 0
        self.curseur: Any = None
        self.selection = Selection()
        self.zone: Widget | None = None
        self.detruit = False
        self.secondaire = ""

    def __getattr__(self, nom: str) -> Any:
        """Toute autre méthode GTK est acceptée et ne fait rien."""
        if nom.startswith("__"):
            raise AttributeError(nom)
        return lambda *_a, **_k: None

    def connect(self, signal: str, rappel: Any, *extra: Any) -> None:
        """Mémorise le gestionnaire d'un signal."""
        self.signaux[signal] = (rappel, extra)

    def emettre(self, signal: str, *args: Any) -> Any:
        """Appelle le gestionnaire mémorisé, comme le ferait GTK."""
        rappel, extra = self.signaux[signal]
        return rappel(*args, *extra)

    def set_text(self, texte: str, *_a: Any) -> None:
        """Mémorise le texte."""
        self.texte = texte

    def get_text(self) -> str:
        """Rend le texte."""
        return self.texte

    def set_sensitive(self, sensible: bool) -> None:
        """Active ou grise."""
        self.sensible = sensible

    def show(self) -> None:
        """Affiche."""
        self.visible = True

    def hide(self) -> None:
        """Masque."""
        self.visible = False

    def pack_start(self, enfant: Any, *_a: Any) -> None:
        """Ajoute un enfant."""
        self.enfants.append(enfant)

    pack_end = pack_start
    attach = pack_start
    pack1 = pack_start
    pack2 = pack_start
    add = pack_start
    append_page = pack_start

    def add_named(self, enfant: Any, _nom: str) -> None:
        """Ajoute une page nommée."""
        self.enfants.append(enfant)

    def set_visible_child_name(self, nom: str) -> None:
        """Affiche une page nommée."""
        self.enfant_visible = nom

    def get_current_page(self) -> int:
        """Onglet courant."""
        return self.page

    def append(self, item_id: str, libelle: str) -> None:
        """Ajoute une entrée de liste déroulante."""
        self.ids.append((item_id, libelle))

    def set_active_id(self, item_id: str | None) -> None:
        """Sélectionne une entrée."""
        self.id_actif = item_id

    def get_active_id(self) -> str | None:
        """Entrée sélectionnée."""
        return self.id_actif

    def get_value_as_int(self) -> int:
        """Valeur d'un SpinButton."""
        return self.valeur

    def get_selection(self) -> Selection:
        """Sélection d'un TreeView."""
        return self.selection

    def set_cursor(self, chemin: Any, *_a: Any) -> None:
        """Note la ligne pointée."""
        self.curseur = chemin

    def get_content_area(self) -> Widget:
        """Zone de contenu (toujours la même)."""
        if self.zone is None:
            self.zone = Widget()
        return self.zone

    def get_style_context(self) -> Any:
        """Contexte de style qui note les classes."""
        return SimpleNamespace(add_class=self.classes.append)

    def format_secondary_text(self, texte: str) -> None:
        """Texte secondaire d'un MessageDialog."""
        self.secondaire = texte

    def destroy(self) -> None:
        """Détruit."""
        self.detruit = True


class Selection:
    """``Gtk.TreeSelection`` factice."""

    def __init__(self) -> None:
        """Rien de sélectionné au départ."""
        self.choix: tuple[Any, Any] = (None, None)
        self.signaux: dict[str, Any] = {}

    def connect(self, signal: str, rappel: Any) -> None:
        """Mémorise le gestionnaire."""
        self.signaux[signal] = rappel

    def get_selected(self) -> tuple[Any, Any]:
        """Modèle et itérateur sélectionnés."""
        return self.choix


class Ligne(list):
    """Ligne de ``ListStore`` : indexable, avec un ``path``."""

    path: int = 0


class ListStore:
    """``Gtk.ListStore`` factice (itérateur = indice de ligne)."""

    def __init__(self, *_types: Any) -> None:
        """Modèle vide."""
        self.lignes: list[Ligne] = []
        self.visible: Any = None
        self.refiltre = 0

    def append(self, valeurs: list[Any]) -> int:
        """Ajoute une ligne."""
        ligne = Ligne(valeurs)
        ligne.path = len(self.lignes)
        self.lignes.append(ligne)
        return ligne.path

    def clear(self) -> None:
        """Vide le modèle."""
        self.lignes = []

    def remove(self, it: int) -> None:
        """Retire une ligne."""
        del self.lignes[it]

    def __len__(self) -> int:
        """Nombre de lignes."""
        return len(self.lignes)

    def __iter__(self) -> Any:
        """Parcourt les lignes."""
        return iter(self.lignes)

    def __getitem__(self, it: int) -> Ligne:
        """Ligne d'un itérateur."""
        return self.lignes[it]

    def filter_new(self) -> ListStore:
        """Le filtre est le modèle lui-même."""
        return self

    def set_visible_func(self, fonction: Any) -> None:
        """Mémorise la fonction de filtre."""
        self.visible = fonction

    def refilter(self) -> None:
        """Compte les rafraîchissements du filtre."""
        self.refiltre += 1


class TextBuffer:
    """``Gtk.TextBuffer`` factice : texte et morceaux étiquetés."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        """Tampon vide."""
        self.texte = ""
        self.morceaux: list[tuple[str, str | None]] = []
        self.signaux: dict[str, Any] = {}
        self.bloques: list[Any] = []

    def connect(self, signal: str, rappel: Any) -> None:
        """Mémorise le gestionnaire."""
        self.signaux[signal] = rappel

    def create_tag(self, *_a: Any, **_k: Any) -> None:
        """Étiquette de style (sans effet)."""

    def handler_block_by_func(self, fonction: Any) -> None:
        """Bloque un gestionnaire."""
        self.bloques.append(fonction)

    def handler_unblock_by_func(self, fonction: Any) -> None:
        """Débloque un gestionnaire."""
        self.bloques.remove(fonction)

    def set_text(self, texte: str) -> None:
        """Remplace le texte."""
        self.texte = texte
        self.morceaux = []

    def get_bounds(self) -> tuple[int, int]:
        """Bornes du texte."""
        return (0, len(self.texte))

    def get_text(self, _debut: int, _fin: int, _cache: bool) -> str:
        """Texte complet."""
        return self.texte

    def get_end_iter(self) -> int:
        """Fin du texte."""
        return len(self.texte)

    def insert(self, _it: int, texte: str) -> None:
        """Ajoute du texte brut."""
        self.texte += texte
        self.morceaux.append((texte, None))

    def insert_with_tags_by_name(self, _it: int, texte: str, etiquette: str) -> None:
        """Ajoute du texte étiqueté."""
        self.texte += texte
        self.morceaux.append((texte, etiquette))


class FauxGLib:
    """``GLib`` factice : les rappels différés sont seulement notés."""

    def __init__(self) -> None:
        """Aucun rappel au départ."""
        self.inactifs: list[tuple[Any, tuple[Any, ...]]] = []
        self.delais: list[tuple[int, Any]] = []

    def idle_add(self, fonction: Any, *args: Any) -> int:
        """Note un rappel « au repos »."""
        self.inactifs.append((fonction, args))
        return 1

    def timeout_add(self, delai: int, fonction: Any) -> int:
        """Note un rappel minuté."""
        self.delais.append((delai, fonction))
        return 2


class Presse:
    """Presse-papiers factice."""

    texte: str | None = None

    @classmethod
    def get(cls, _atome: Any) -> Any:
        """Rend le presse-papiers."""
        return SimpleNamespace(set_text=lambda t, _n: setattr(cls, "texte", t))


def faux_gtk(dialog_base: Any) -> SimpleNamespace:
    """Espace de noms ``Gtk`` factice ; ``Dialog`` reste la doublure de test_gcm."""
    reponses = SimpleNamespace(OK="OK", CANCEL="CANCEL", YES="YES", NO="NO", CLOSE="CLOSE")
    gtk = SimpleNamespace(
        Dialog=dialog_base,
        ListStore=ListStore,
        TextBuffer=TextBuffer,
        TreePath=lambda i: ("chemin", i),
        Clipboard=Presse,
        ResponseType=reponses,
        Orientation=SimpleNamespace(HORIZONTAL=0, VERTICAL=1),
        PolicyType=SimpleNamespace(AUTOMATIC=0, NEVER=1),
        ShadowType=SimpleNamespace(IN=0),
        Align=SimpleNamespace(CENTER=0),
        MessageType=SimpleNamespace(ERROR=0, INFO=1, QUESTION=2),
        ButtonsType=SimpleNamespace(YES_NO=0),
    )
    for nom in (
        "InfoBar",
        "Label",
        "Notebook",
        "Paned",
        "Box",
        "SearchEntry",
        "ScrolledWindow",
        "TreeView",
        "TreeViewColumn",
        "CellRendererText",
        "Button",
        "ComboBoxText",
        "Grid",
        "Entry",
        "Stack",
        "SpinButton",
        "Adjustment",
        "TextView",
        "MessageDialog",
    ):
        setattr(gtk, nom, Widget)
    return gtk


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """Doublures GTK et fichiers personnel/partagé temporaires."""
    perso = tmp_path / "config"
    partage = tmp_path / "config_gcm.conf"
    perso.write_text(PERSO, encoding="utf-8")
    partage.write_text(PARTAGE, encoding="utf-8")

    vrai_parser = core.SSHConfigParser
    glib = FauxGLib()
    gtk = faux_gtk(sce.Gtk.Dialog)
    monkeypatch.setattr(sce, "Gtk", gtk)
    monkeypatch.setattr(sce, "GLib", glib)
    monkeypatch.setattr(
        sce,
        "Gdk",
        SimpleNamespace(
            WindowTypeHint=SimpleNamespace(NORMAL="normal"),
            Atom=SimpleNamespace(intern=lambda nom, _b: nom),
        ),
    )
    monkeypatch.setattr(sce, "Pango", SimpleNamespace(Weight=SimpleNamespace(BOLD=700)))
    monkeypatch.setattr(
        sce, "SSHConfigParser", lambda config_path=None: vrai_parser(config_path or perso)
    )
    monkeypatch.setattr(sce, "SHARED_SSH_CONFIG_PATH", partage)

    etat = SimpleNamespace(
        perso=perso,
        partage=partage,
        glib=glib,
        gtk=gtk,
        vrai_parser=vrai_parser,
        reponses=[],
        modal=[],
        dialogues=[],
    )
    zone = Widget()
    classe = sce.SshConfigEditorDialog
    for nom, valeur in (
        ("get_content_area", lambda _self: zone),
        ("set_type_hint", lambda _self, hint: setattr(etat, "hint", hint)),
        ("set_default_size", lambda _self, *t: setattr(etat, "taille", t)),
        ("response", lambda _self, r: etat.reponses.append(r)),
        ("set_modal", lambda _self, m: etat.modal.append(m)),
    ):
        monkeypatch.setattr(classe, nom, valeur, raising=False)
    etat.zone = zone

    def lancer(dlg: Any) -> Any:
        etat.dialogues.append(dlg)
        return getattr(etat, "reponse_dialogue", "YES")

    monkeypatch.setattr(sce, "run_dialog_sync", lancer)
    return etat


@pytest.fixture
def dlg(env: SimpleNamespace) -> Any:
    """Éditeur construit sur les fichiers temporaires."""
    return sce.SshConfigEditorDialog(parent="parent")


def selectionner(d: Any, alias: str, source: str = "personal") -> None:
    """Sélectionne une ligne de la liste, comme un clic, puis émet « changed »."""
    for ligne in d._host_store:
        if ligne[2] == alias and ligne[3] == source:
            d._tree.selection.choix = (d._host_store, ligne.path)
            break
    else:
        raise AssertionError(alias)
    d._on_host_selected(d._tree.selection)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_liste_fusionnee_et_raw(env: SimpleNamespace, dlg: Any) -> None:
    """La liste réunit les deux fichiers ; Raw contient le fichier personnel."""
    assert env.hint == "normal"
    assert env.taille == (940, 620)
    assert dlg.main_widget is dlg
    assert dlg.is_tab is False and dlg._initialized is True
    lignes = [list(lg) for lg in dlg._host_store]
    assert [lg[0] for lg in lignes] == ["web1 web", "bastion", "🌐 commun"]
    assert lignes[1][1].startswith("bastion\nb.example.com\n[")
    assert lignes[2][3] == "shared"
    assert dlg._raw_buffer.texte == dlg._config.generate_content()
    assert dlg._raw_buffer.bloques == []
    assert dlg._diff_buffer.morceaux == [("No changes.", "header")]
    assert dlg._form_stack.enfant_visible == "placeholder"
    assert dlg._pf_combo.get_active_id() == "local"
    assert dlg._raw_target_combo.get_active_id() == "personal"
    assert [i for i, _l in dlg._host_file_combo.ids] == ["personal", "shared"]
    assert dlg._entries["IdentityFile"].kwargs == {}
    assert env.glib.inactifs == []
    # Validation initiale : les clés ~/.ssh/id_a et id_b n'existent pas.
    assert dlg._error_bar.visible is True
    assert "IdentityFile not found for host web1" in dlg._error_label.texte


def test_construction_fichier_personnel_absent(env: SimpleNamespace) -> None:
    """Sans ~/.ssh/config, un toast explique qu'il sera créé."""
    env.perso.unlink()
    d = sce.SshConfigEditorDialog(parent="parent")
    assert [lg[0] for lg in d._host_store] == ["🌐 commun"]
    ((fonction, args),) = env.glib.inactifs
    assert fonction is sce._show_toast
    assert args[0] is d and "does not exist yet" in args[1]


def test_erreur_bar_se_masque_sur_reponse(dlg: Any) -> None:
    """La croix de l'InfoBar d'erreur la masque."""
    dlg._error_bar.show()
    dlg._error_bar.emettre("response", dlg._error_bar, 0)
    assert dlg._error_bar.visible is False


# ---------------------------------------------------------------------------
# Boutons Save/Cancel et fenêtre parente
# ---------------------------------------------------------------------------


def test_boutons_fenetre_autonome(env: SimpleNamespace, dlg: Any) -> None:
    """En fenêtre autonome, Save/Cancel renvoient OK/CANCEL à l'appelant."""
    boite = dlg._make_action_buttons()
    annuler, enregistrer = boite.enfants
    assert enregistrer.classes == ["suggested-action"]
    enregistrer.emettre("clicked", enregistrer)
    annuler.emettre("clicked", annuler)
    assert env.reponses == ["OK", "CANCEL"]
    assert dlg._top_window() is dlg


def test_boutons_onglet_epingle(
    monkeypatch: pytest.MonkeyPatch, env: SimpleNamespace, dlg: Any
) -> None:
    """En onglet, Save enregistre puis ferme ; Cancel ferme seulement."""
    appels: list[str] = []
    monkeypatch.setattr(dlg, "save", lambda: appels.append("save"))
    monkeypatch.setattr(dlg, "request_close", lambda: appels.append("close"))
    dlg.is_tab = True
    dlg._on_dialog_save_clicked(None)
    dlg._on_dialog_cancel_clicked(None)
    assert appels == ["save", "close", "close"]
    assert env.reponses == []


def test_top_window_onglet(monkeypatch: pytest.MonkeyPatch, dlg: Any) -> None:
    """En onglet, les dialogues enfants se rattachent à la fenêtre principale."""
    faux = SimpleNamespace(wMain=SimpleNamespace(window="principale"))
    monkeypatch.setitem(sys.modules, "gnome_connection_manager", faux)
    dlg.is_tab = True
    assert dlg._top_window() == "principale"


# ---------------------------------------------------------------------------
# Redirections de port
# ---------------------------------------------------------------------------


def test_type_de_redirection(dlg: Any) -> None:
    """Libellés et champs actifs suivent le type choisi."""
    dlg._pf_combo.set_active_id("remote")
    dlg._on_pf_type_changed(dlg._pf_combo)
    assert dlg._pf_lbl_local.texte == "Remote listen port"
    assert dlg._pf_lbl_host.texte == "Local target host"
    assert dlg._pf_lbl_remote.texte == "Local target port"
    assert dlg._pf_txt_host.sensible is True
    dlg._pf_combo.set_active_id("dynamic")
    dlg._on_pf_type_changed(dlg._pf_combo)
    assert dlg._pf_lbl_local.texte == "Local Port"
    assert dlg._pf_txt_host.sensible is False and dlg._pf_txt_remote.sensible is False
    dlg._pf_combo.set_active_id(None)
    dlg._on_pf_type_changed(dlg._pf_combo)
    assert dlg._pf_txt_host.sensible is True


def test_ajout_et_retrait_de_redirection(dlg: Any) -> None:
    """Ajout dynamique, local, distant ; hôte manquant ; retrait."""
    dlg._pf_txt_local.valeur = 1080
    dlg._pf_txt_remote.valeur = 22
    dlg._pf_combo.set_active_id("dynamic")
    dlg._on_pf_add(None)
    dlg._pf_combo.set_active_id("local")
    dlg._pf_txt_host.set_text(" srv ")
    dlg._on_pf_add(None)
    dlg._pf_combo.set_active_id("remote")
    dlg._on_pf_add(None)
    assert [list(lg) for lg in dlg._pf_store] == [
        ["1080", "", "", "1080", "Dynamic"],
        ["1080", "srv", "22", "1080 srv:22", "Local"],
        ["1080", "srv", "22", "1080 srv:22", "Remote"],
    ]
    assert dlg._dirty is True and dlg._pf_error_label.visible is False

    dlg._pf_txt_host.set_text("  ")
    dlg._on_pf_add(None)
    assert dlg._pf_error_label.texte == "Enter remote host"
    assert dlg._pf_error_label.visible is True
    assert len(dlg._pf_store) == 3

    dlg._dirty = False
    dlg._on_pf_remove(None)
    assert len(dlg._pf_store) == 3 and dlg._dirty is False
    dlg._pf_tree.selection.choix = (dlg._pf_store, 0)
    dlg._on_pf_remove(None)
    assert [lg[4] for lg in dlg._pf_store] == ["Local", "Remote"]
    assert dlg._dirty is True


# ---------------------------------------------------------------------------
# Filtre, sélection et formulaire
# ---------------------------------------------------------------------------


def test_filtre(dlg: Any) -> None:
    """Le filtre porte sur l'affichage et l'infobulle, sans casse."""
    filtre = dlg._host_store.visible
    assert filtre(dlg._host_store, 0, None) is True
    dlg._search_entry.set_text(" BASTION ")
    assert filtre(dlg._host_store, 1, None) is True
    assert filtre(dlg._host_store, 0, None) is False
    dlg._search_entry.set_text("b.example")
    assert filtre(dlg._host_store, 1, None) is True
    dlg._on_search_changed(None)
    dlg._search_entry.emettre("search-changed", dlg._search_entry)
    assert dlg._host_store.refiltre == 2


def test_selection_hote_a_plusieurs_alias(dlg: Any) -> None:
    """Un hôte « Host web1 web » se charge dans le formulaire (#415)."""
    selectionner(dlg, "web1 web")
    hote = dlg._current_host
    assert hote is not None and hote.patterns == ["web1", "web"]
    assert dlg._current_host_source == "personal"
    assert dlg._host_file_combo.get_active_id() == "personal"
    assert dlg._suspend_file_combo_signal is False
    assert dlg._form_stack.enfant_visible == "form"
    assert dlg._entries["Host"].texte == "web1 web"
    assert dlg._entries["HostName"].texte == "10.0.0.1"
    assert dlg._entries["Port"].texte == "2222"
    assert dlg._entries["IdentityFile"].texte == "~/.ssh/id_a, ~/.ssh/id_b"
    assert dlg._btn_remove.sensible is True and dlg._btn_test.sensible is True
    assert [list(lg) for lg in dlg._pf_store] == [
        ["1080", "", "", "1080", "Dynamic"],
        ["8080", "localhost", "80", "8080 localhost:80", "Local"],
        ["7000", "", "", "7000", "Local"],
        ["9000", "db", "5432", "9000 db:5432", "Remote"],
    ]


def test_selection_hote_partage_puis_rien(dlg: Any) -> None:
    """Hôte du fichier partagé, puis désélection : retour au message d'invite."""
    selectionner(dlg, "commun", "shared")
    assert dlg._current_host_source == "shared"
    assert dlg._host_file_combo.get_active_id() == "shared"
    dlg._entries["User"].set_text("alice")
    dlg._tree.selection.choix = (dlg._host_store, None)
    dlg._tree.selection.signaux["changed"](dlg._tree.selection)
    assert dlg._configs["shared"].hosts[0].get_option("User") == "alice"
    assert dlg._current_host is None and dlg._current_host_source is None
    assert dlg._form_stack.enfant_visible == "placeholder"
    assert dlg._btn_remove.sensible is False and dlg._btn_test.sensible is False


def test_selection_alias_introuvable(dlg: Any) -> None:
    """Une ligne qui ne correspond plus à aucun hôte ne charge rien."""
    dlg._host_store.append(["fantome", "", "fantome", "personal"])
    dlg._tree.selection.choix = (dlg._host_store, 3)
    dlg._on_host_selected(dlg._tree.selection)
    assert dlg._current_host is None


def test_test_desactive_sans_hostname_ni_alias(dlg: Any) -> None:
    """Le bouton Test reste gris pour un hôte sans HostName ni motif."""
    hote = core.SSHHost(patterns=[])
    dlg._load_host_in_form(hote, "personal")
    assert dlg._btn_test.sensible is False


def test_formulaire_vers_hote(dlg: Any) -> None:
    """Le formulaire réécrit alias, options simples et multiples, redirections."""
    assert dlg._save_form_to_host() is True  # aucun hôte courant
    selectionner(dlg, "web1 web")
    dlg._entries["Host"].set_text("  web2   w2 ")
    dlg._entries["User"].set_text("")
    dlg._entries["Port"].set_text("")
    dlg._entries["IdentityFile"].set_text("~/.ssh/k1, , ~/.ssh/k2")
    dlg._entries["ProxyJump"].set_text("bastion")
    dlg._pf_store.clear()
    dlg._pf_store.append(["5000", "x", "1", "5000 x:1", "Remote"])
    assert dlg._save_form_to_host() is True
    hote = dlg._current_host
    assert hote.patterns == ["web2", "w2"]
    assert hote.get_option("User") is None
    assert hote.get_options("IdentityFile") == ["~/.ssh/k1", "~/.ssh/k2"]
    assert hote.get_option("ProxyJump") == "bastion"
    assert hote.get_options("RemoteForward") == ["5000 x:1"]
    assert hote.get_options("LocalForward") == []
    assert hote.get_options("DynamicForward") == []


def test_aller_retour_redirections(dlg: Any) -> None:
    """Formulaire → hôte sans retouche : les trois types de redirection sont conservés."""
    selectionner(dlg, "web1 web")
    assert dlg._save_form_to_host() is True
    hote = dlg._current_host
    assert hote.get_options("DynamicForward") == ["1080"]
    assert hote.get_options("LocalForward") == ["8080 localhost:80", "7000"]
    assert hote.get_options("RemoteForward") == ["9000 db:5432"]


@pytest.mark.parametrize("port", ["abc", "0", "70000"])
def test_formulaire_port_invalide(dlg: Any, port: str) -> None:
    """Port hors plage ou non entier : erreur sous le champ."""
    selectionner(dlg, "bastion")
    dlg._entries["Port"].set_text(port)
    assert dlg._save_form_to_host() is False
    assert dlg._error_labels["Port"].visible is True
    assert "between 1 and 65535" in dlg._error_labels["Port"].texte


def test_formulaire_alias_vide(dlg: Any) -> None:
    """Sans alias, l'hôte garde ses motifs et le champ est en erreur."""
    selectionner(dlg, "bastion")
    dlg._entries["Host"].set_text("  ")
    dlg._entries["Port"].set_text("22")
    assert dlg._save_form_to_host() is False
    assert dlg._current_host.patterns == ["bastion"]
    assert dlg._error_labels["Host"].texte == "At least one alias is required"
    assert dlg._error_labels["Port"].visible is False


def test_erreur_champ_inconnu(dlg: Any) -> None:
    """Une erreur sur un champ inconnu est ignorée."""
    dlg._show_field_error("Inconnu", "x")


def test_redirections_sans_hote_courant(dlg: Any) -> None:
    """Sans hôte courant, l'enregistrement des redirections ne fait rien."""
    dlg._save_port_forwarding_to_host()


def test_champs_et_raw_marquent_modifie(dlg: Any) -> None:
    """Saisie dans un champ ou dans Raw : formulaire marqué modifié."""
    entree = dlg._entries["User"]
    entree.emettre("changed", entree)
    assert dlg._dirty is True
    dlg._dirty = False
    dlg._raw_buffer.signaux["changed"](dlg._raw_buffer)
    assert dlg._dirty is True


# ---------------------------------------------------------------------------
# Onglet Raw/Diff
# ---------------------------------------------------------------------------


def test_cible_raw(dlg: Any) -> None:
    """Le sélecteur Raw bascule vers le fichier partagé ; même cible ou rien : ignoré."""
    combo = dlg._raw_target_combo
    combo.set_active_id(None)
    dlg._on_raw_target_changed(combo)
    combo.set_active_id("personal")
    dlg._on_raw_target_changed(combo)
    assert dlg._raw_target == "personal"
    combo.set_active_id("shared")
    combo.emettre("changed", combo)
    assert dlg._raw_target == "shared"
    assert dlg._parser is dlg._parsers["shared"]
    assert "commun" in dlg._raw_buffer.texte


def test_diff_avec_modifications(dlg: Any) -> None:
    """Le diff étiquette en-têtes, ajouts, retraits et contexte."""
    dlg._raw_buffer.set_text(PERSO.replace("User root", "User admin"))
    dlg._refresh_diff()
    etiquettes = {e for _t, e in dlg._diff_buffer.morceaux}
    assert etiquettes == {"header", "added", "removed", None}
    assert ("+    User admin\n", "added") in dlg._diff_buffer.morceaux
    assert ("-    User root\n", "removed") in dlg._diff_buffer.morceaux


def test_bouton_rafraichir_diff(dlg: Any) -> None:
    """Le bouton « Refresh diff » recalcule le diff."""
    rangee = dlg._notebook.enfants[1].enfants[-1]
    bouton = rangee.enfants[0]
    dlg._raw_buffer.set_text("Host z\n")
    bouton.emettre("clicked", bouton)
    assert ("+Host z\n", "added") in dlg._diff_buffer.morceaux


def test_raw_vers_modele(dlg: Any) -> None:
    """Le texte Raw est relu et remplace les hôtes du fichier courant."""
    dlg._raw_buffer.set_text("Compression yes\nHost seul\n    HostName 1.2.3.4\n")
    assert dlg._sync_model_from_raw() is True
    assert [h.patterns for h in dlg._config.hosts] == [["seul"]]
    assert dlg._configs["personal"] is dlg._config


def test_raw_vers_modele_echec(monkeypatch: pytest.MonkeyPatch, dlg: Any) -> None:
    """Échec de relecture : le modèle est conservé et False est rendu."""

    def casse(**_k: Any) -> Any:
        raise ValueError("illisible")

    monkeypatch.setattr(sce, "SSHConfigParser", casse)
    avant = list(dlg._config.hosts)
    assert dlg._sync_model_from_raw() is False
    assert dlg._config.hosts == avant


def test_raw_vers_modele_nettoyage_impossible(monkeypatch: pytest.MonkeyPatch, dlg: Any) -> None:
    """Un fichier temporaire non supprimable n'empêche pas la relecture."""
    vrai_unlink = Path.unlink
    refuses: list[Path] = []

    def refus(chemin: Path, missing_ok: bool = False) -> None:
        if chemin.name.startswith(".gcm_raw_tmp_"):
            refuses.append(chemin)
            raise OSError("occupé")
        vrai_unlink(chemin, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refus)
    assert dlg._sync_model_from_raw() is True
    monkeypatch.setattr(Path, "unlink", vrai_unlink)
    for chemin in refuses:
        chemin.unlink(missing_ok=True)
    assert refuses and not refuses[0].exists()


# ---------------------------------------------------------------------------
# Changement d'onglet
# ---------------------------------------------------------------------------


def test_changement_onglet(dlg: Any) -> None:
    """Visual → Raw applique le formulaire ; Raw → Visual relit le texte."""
    selectionner(dlg, "bastion")
    dlg._entries["User"].set_text("bob")
    dlg._notebook.emettre("switch-page", dlg._notebook, None, 1)
    assert "User bob" in dlg._raw_buffer.texte
    dlg._raw_buffer.set_text(dlg._raw_buffer.texte + "Host neuf\n")
    dlg._on_tab_switch(None, None, 0)
    assert [lg[2] for lg in dlg._host_store][-2:] == ["neuf", "commun"]


def test_changement_onglet_sans_hote_et_raw_illisible(
    monkeypatch: pytest.MonkeyPatch, dlg: Any
) -> None:
    """Sans hôte courant vers Raw ; relecture Raw en échec : liste inchangée."""
    dlg._on_tab_switch(None, None, 1)
    monkeypatch.setattr(dlg, "_sync_model_from_raw", lambda: False)
    monkeypatch.setattr(dlg, "_populate_list", lambda: pytest.fail("non attendu"))
    dlg._on_tab_switch(None, None, 0)


def test_changement_onglet_avant_initialisation(dlg: Any) -> None:
    """Le « switch-page » initial de GTK est ignoré."""
    dlg._initialized = False
    dlg._raw_buffer.set_text("")
    dlg._on_tab_switch(None, None, 0)
    assert len(dlg._config.hosts) == 2


# ---------------------------------------------------------------------------
# Enregistrement et validation
# ---------------------------------------------------------------------------


def test_save_depuis_le_formulaire(env: SimpleNamespace, dlg: Any) -> None:
    """Save applique le formulaire non synchronisé et écrit les deux fichiers."""
    selectionner(dlg, "bastion")
    dlg._entries["User"].set_text("carol")
    dlg.save()
    assert "User carol" in env.perso.read_text(encoding="utf-8")
    assert env.partage.read_text(encoding="utf-8") == PARTAGE
    assert dlg._error_label.texte.startswith("[")  # validation, pas d'échec d'écriture


def test_save_sans_hote_courant(env: SimpleNamespace, dlg: Any) -> None:
    """Sans hôte courant, le modèle en mémoire est écrit tel quel."""
    dlg._configs["shared"].hosts[0].set_option("User", "dan")
    dlg.save()
    assert "User dan" in env.partage.read_text(encoding="utf-8")


def test_save_formulaire_invalide(env: SimpleNamespace, dlg: Any) -> None:
    """Formulaire invalide : rien n'est écrit, la validation est relancée."""
    selectionner(dlg, "bastion")
    dlg._entries["Port"].set_text("x")
    dlg._entries["User"].set_text("eve")
    dlg.save()
    assert "eve" not in env.perso.read_text(encoding="utf-8")


def test_save_depuis_raw(env: SimpleNamespace, dlg: Any) -> None:
    """Onglet Raw actif : le texte est relu puis écrit ; texte illisible : rien."""
    dlg._notebook.page = 1
    dlg._raw_buffer.set_text("Host brut\n    HostName 9.9.9.9\n")
    dlg.save()
    assert env.perso.read_text(encoding="utf-8").startswith("Host brut")


def test_save_depuis_raw_illisible(
    monkeypatch: pytest.MonkeyPatch, env: SimpleNamespace, dlg: Any
) -> None:
    """Relecture Raw en échec : aucun fichier écrit."""
    dlg._notebook.page = 1
    monkeypatch.setattr(dlg, "_sync_model_from_raw", lambda: False)
    dlg._config.hosts[1].set_option("User", "zoe")
    dlg.save()
    assert "zoe" not in env.perso.read_text(encoding="utf-8")


def test_save_echec_ecriture(monkeypatch: pytest.MonkeyPatch, dlg: Any) -> None:
    """Une écriture refusée est signalée dans l'InfoBar, l'autre fichier passe."""
    appels: list[str] = []

    def refus() -> None:
        raise PermissionError("lecture seule")

    monkeypatch.setattr(dlg._parsers["shared"], "write", refus)
    monkeypatch.setattr(dlg._parsers["personal"], "write", lambda: appels.append("perso"))
    dlg.save()
    assert appels == ["perso"]
    assert dlg._error_bar.visible is True
    assert "lecture seule" in dlg._error_label.texte
    assert dlg._error_label.texte.startswith(sce._TARGET_LABELS["shared"])


def test_validation(monkeypatch: pytest.MonkeyPatch, dlg: Any) -> None:
    """Les erreurs des deux fichiers sont préfixées et affichées ; sinon masquées."""
    monkeypatch.setattr(dlg._parsers["personal"], "validate", lambda: ["doublon"])
    monkeypatch.setattr(dlg._parsers["shared"], "validate", lambda: ["port"])
    erreurs = dlg._run_validation()
    assert erreurs == [
        f"[{sce._TARGET_LABELS['personal']}] doublon",
        f"[{sce._TARGET_LABELS['shared']}] port",
    ]
    assert dlg._error_bar.visible is True
    monkeypatch.setattr(dlg._parsers["personal"], "validate", lambda: [])
    monkeypatch.setattr(dlg._parsers["shared"], "validate", lambda: [])
    assert dlg._run_validation() == []
    assert dlg._error_bar.visible is False


# ---------------------------------------------------------------------------
# Clés SSH
# ---------------------------------------------------------------------------


def test_choix_de_cle(monkeypatch: pytest.MonkeyPatch, env: SimpleNamespace, dlg: Any) -> None:
    """Le sélecteur de clé remplit IdentityFile via « key-selected »."""
    creees: list[Widget] = []

    def fabrique(parent: Any) -> Widget:
        w = Widget(parent=parent)
        creees.append(w)
        return w

    monkeypatch.setattr(sce, "KeyPickerDialog", fabrique)
    dlg._on_pick_identity_file(None)
    (picker,) = creees
    assert picker.kwargs["parent"] is dlg
    assert env.dialogues == [picker] and picker.detruit is True
    picker.emettre("key-selected", picker, "/home/u/.ssh/id_c")
    assert dlg._entries["IdentityFile"].texte == "/home/u/.ssh/id_c"
    assert dlg._dirty is True


def test_gestionnaire_de_cles_fenetre(
    monkeypatch: pytest.MonkeyPatch, env: SimpleNamespace, dlg: Any
) -> None:
    """Fenêtre autonome : modalité levée, puis rétablie à la fermeture."""
    creees: list[Widget] = []

    def fabrique(parent: Any) -> Widget:
        creees.append(Widget(parent=parent))
        return creees[-1]

    monkeypatch.setattr(sce, "SSHKeyManagerDialog", fabrique)
    dlg._on_open_key_manager(None)
    assert env.modal == [False]
    assert creees[0].kwargs["parent"] is dlg
    creees[0].emettre("destroy", creees[0])
    assert env.modal == [False, True]


def test_gestionnaire_de_cles_onglet(
    monkeypatch: pytest.MonkeyPatch, env: SimpleNamespace, dlg: Any
) -> None:
    """Onglet épinglé : parent = fenêtre principale, modalité intacte."""
    faux = SimpleNamespace(wMain=SimpleNamespace(window="principale"))
    monkeypatch.setitem(sys.modules, "gnome_connection_manager", faux)
    parents: list[Any] = []

    def fabrique(parent: Any) -> Widget:
        parents.append(parent)
        return Widget()

    monkeypatch.setattr(sce, "SSHKeyManagerDialog", fabrique)
    dlg.is_tab = True
    dlg._on_open_key_manager(None)
    assert parents == ["principale"] and env.modal == []


# ---------------------------------------------------------------------------
# Ajout, suppression, déplacement
# ---------------------------------------------------------------------------


def test_ajout_hote(dlg: Any) -> None:
    """« Add » crée « new-host » côté personnel et le pointe dans l'arbre."""
    dlg._on_add_host(None)
    assert dlg._configs["personal"].hosts[-1].patterns == ["new-host"]
    assert dlg._tree.curseur == ("chemin", 3)
    assert dlg._dirty is True


def test_ajout_hote_liste_vide(monkeypatch: pytest.MonkeyPatch, dlg: Any) -> None:
    """Liste restée vide (filtre) : aucun curseur posé."""
    monkeypatch.setattr(dlg, "_populate_list", dlg._host_store.clear)
    dlg._on_add_host(None)
    assert dlg._tree.curseur is None


def test_suppression_hote(env: SimpleNamespace, dlg: Any) -> None:
    """Sans hôte : rien ; « Non » : conservé ; « Oui » : retiré."""
    dlg._on_remove_host(None)
    assert env.dialogues == []

    selectionner(dlg, "bastion")
    env.reponse_dialogue = "NO"
    dlg._on_remove_host(None)
    (question,) = env.dialogues
    assert question.detruit is True
    assert "«bastion»" in question.secondaire and str(env.perso) in question.secondaire
    assert dlg._current_host is not None

    env.reponse_dialogue = "YES"
    dlg._on_remove_host(None)
    assert [h.patterns for h in dlg._configs["personal"].hosts] == [["web1", "web"]]
    assert dlg._current_host is None and dlg._current_host_source is None
    assert dlg._form_stack.enfant_visible == "placeholder"


def test_deplacement_hote(dlg: Any) -> None:
    """Le sélecteur « Fichier » déplace l'hôte et le re-sélectionne."""
    combo = dlg._host_file_combo
    combo.set_active_id("shared")
    dlg._on_host_file_changed(combo)  # aucun hôte courant : ignoré
    selectionner(dlg, "bastion")
    dlg._suspend_file_combo_signal = True
    combo.set_active_id("shared")
    dlg._on_host_file_changed(combo)
    assert dlg._current_host_source == "personal"
    dlg._suspend_file_combo_signal = False
    combo.set_active_id("personal")
    dlg._on_host_file_changed(combo)
    combo.set_active_id(None)
    dlg._on_host_file_changed(combo)
    assert dlg._current_host_source == "personal"

    combo.set_active_id("shared")
    combo.emettre("changed", combo)
    assert dlg._current_host_source == "shared"
    assert [h.patterns[0] for h in dlg._configs["shared"].hosts] == ["commun", "bastion"]
    assert [lg[0] for lg in dlg._host_store] == ["web1 web", "🌐 commun", "🌐 bastion"]
    assert dlg._tree.curseur == 2


def test_deplacement_sans_ligne_correspondante(monkeypatch: pytest.MonkeyPatch, dlg: Any) -> None:
    """Hôte déplacé mais absent de la liste (filtre) : pas de curseur."""
    selectionner(dlg, "bastion")
    monkeypatch.setattr(dlg, "_populate_list", dlg._host_store.clear)
    dlg._host_file_combo.set_active_id("shared")
    dlg._on_host_file_changed(dlg._host_file_combo)
    assert dlg._tree.curseur is None


# ---------------------------------------------------------------------------
# Test de connexion et copie de la commande
# ---------------------------------------------------------------------------


def test_test_de_connexion(
    monkeypatch: pytest.MonkeyPatch, env: SimpleNamespace, dlg: Any
) -> None:
    """Sans hôte ou sans alias : rien ; sinon dialogue de test sur le 1er alias."""
    creees: list[Widget] = []

    def fabrique(parent: Any, alias: str) -> Widget:
        w = Widget(parent=parent, alias=alias)
        creees.append(w)
        return w

    monkeypatch.setattr(sce, "_SshTestDialog", fabrique)
    dlg._on_test_connection(None)
    dlg._current_host = core.SSHHost(patterns=[])
    dlg._on_test_connection(None)
    assert creees == []
    selectionner(dlg, "web1 web")
    dlg._btn_test.emettre("clicked", dlg._btn_test)
    (test,) = creees
    assert test.kwargs == {"parent": dlg, "alias": "web1"}
    assert env.dialogues == [test] and test.detruit is True


def test_copie_commande_ssh(monkeypatch: pytest.MonkeyPatch, dlg: Any) -> None:
    """La commande copiée tient compte de l'utilisateur, du port et du HostName."""
    toasts: list[str] = []
    monkeypatch.setattr(sce, "_show_toast", lambda _p, msg: toasts.append(msg))
    Presse.texte = None
    dlg._on_copy_ssh_cmd(None)
    assert Presse.texte is None
    selectionner(dlg, "web1 web")
    dlg._on_copy_ssh_cmd(None)
    assert Presse.texte == "ssh -p 2222 root@10.0.0.1"
    dlg._current_host = core.SSHHost(patterns=["seul"])
    dlg._on_copy_ssh_cmd(None)
    assert Presse.texte == "ssh -p 22 seul"
    dlg._current_host = core.SSHHost(patterns=[])
    dlg._on_copy_ssh_cmd(None)
    assert Presse.texte == "ssh -p 22 "
    assert toasts[0] == "Copied: ssh -p 2222 root@10.0.0.1"


# ---------------------------------------------------------------------------
# _SshTestDialog
# ---------------------------------------------------------------------------


@pytest.fixture
def testeur(monkeypatch: pytest.MonkeyPatch, env: SimpleNamespace) -> Any:
    """``_SshTestDialog`` construit sur les doublures."""
    zone = Widget()
    classe = sce._SshTestDialog
    boutons: list[tuple[Any, ...]] = []
    for nom, valeur in (
        ("get_content_area", lambda _self: zone),
        ("set_default_size", lambda _self, *t: None),
        ("add_button", lambda _self, *b: boutons.append(b)),
        ("show_all", lambda _self: None),
    ):
        monkeypatch.setattr(classe, nom, valeur, raising=False)
    d = classe(parent="parent", alias="srv")
    d.zone_test = zone
    d.boutons = boutons
    return d


def test_dialogue_de_test_construction(env: SimpleNamespace, testeur: Any) -> None:
    """Le test est programmé au repos ; la sortie s'affiche dans la zone."""
    assert testeur._alias == "srv"
    assert testeur.boutons == [("Close", "CLOSE")]
    assert len(testeur.zone_test.enfants) == 1
    assert env.glib.inactifs == [(testeur._run_test, ())]


def test_dialogue_de_test_sortie(monkeypatch: pytest.MonkeyPatch, testeur: Any) -> None:
    """Sortie et code de retour de ssh affichés."""
    vus: list[list[str]] = []

    def faux_run(cmd: list[str], **_k: Any) -> Any:
        vus.append(cmd)
        return SimpleNamespace(stdout="debug1: ok\n", stderr="", returncode=255)

    monkeypatch.setattr(sce.subprocess, "run", faux_run)
    assert testeur._run_test() is False
    assert vus == [["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-v", "srv"]]
    assert testeur._buf.texte.startswith("Running: ssh -o ConnectTimeout=5")
    assert testeur._buf.texte.endswith("debug1: ok\n\nExit code: 255")


def test_dialogue_de_test_sans_sortie(monkeypatch: pytest.MonkeyPatch, testeur: Any) -> None:
    """Aucune sortie : mention explicite."""
    monkeypatch.setattr(
        sce.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(stdout="", stderr="", returncode=0),
    )
    testeur._run_test()
    assert "(no output)\n" in testeur._buf.texte


def test_dialogue_de_test_delai_depasse(monkeypatch: pytest.MonkeyPatch, testeur: Any) -> None:
    """Délai dépassé : message, puis False sans UnboundLocalError (#414)."""

    def lent(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired("ssh", 10)

    monkeypatch.setattr(sce.subprocess, "run", lent)
    assert testeur._run_test() is False
    assert testeur._buf.texte.endswith("[Timeout after 10 s]")


def test_dialogue_de_test_ssh_absent(monkeypatch: pytest.MonkeyPatch, testeur: Any) -> None:
    """``ssh`` introuvable : erreur affichée, puis False (#414)."""

    def absent(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("ssh")

    monkeypatch.setattr(sce.subprocess, "run", absent)
    assert testeur._run_test() is False
    assert testeur._buf.texte.endswith("[Error: ssh]")


# ---------------------------------------------------------------------------
# _show_toast
# ---------------------------------------------------------------------------


def test_toast_dans_un_dialogue(env: SimpleNamespace, dlg: Any) -> None:
    """Dans un dialogue : barre ajoutée au contenu, puis détruite au délai."""
    sce._show_toast(dlg, "Bonjour", duration_ms=10)
    barre = env.zone.enfants[-1]
    assert barre.get_content_area().enfants[0].texte == "Bonjour"
    ((delai, rappel),) = env.glib.delais
    assert delai == 10
    assert rappel() is False and barre.detruit is True


def test_toast_hors_dialogue(env: SimpleNamespace) -> None:
    """Parent qui n'est pas un dialogue : rien n'est ajouté ni programmé."""
    sce._show_toast(Widget(), "Bonjour")
    assert env.glib.delais == []
