# SPDX-License-Identifier: GPL-3.0-or-later
"""Widgets GTK3 : construction et actions (#293, palier 17).

``widgets.py`` restait à 13,7 % en mesure combinée : seules les lignes
d'import s'exécutaient, jamais les classes ni les fonctions. Ce fichier
couvre, sans vrai GTK ni vrai outil système :

- menus contextuels ``RemoteDesktopContextMenu``/``TabContextMenu``/
  ``FolderContextMenu``/``PopupMenu`` : construction, entrées, bascule de
  la journalisation, ``rebuild``, ``populate_commands`` et ``popup_at`` ;
- montages ISO (``mount_iso_temp``/``umount_iso_temp``) avec un faux
  ``subprocess.run`` : udisksctl absent, succès et échec ;
- ``vte_run`` : environnement transmis, chemins ``spawn_async`` et
  ``spawn_sync`` (VTE < 0.48) ;
- étiquettes d'onglet ``ManagementTabLabel``/``NotebookTabLabel`` :
  construction, sélection, fermeture (confirmée ou non), fermeture
  automatique par hôte et réglage global, clic droit/milieu ;
- dialogues ``EntryDialog``/``inputbox`` : saisie validée et annulée ;
- éditeur en ligne ``CellTextView``/``MultilineCellRenderer``.

``Gtk`` et ``GLib`` sont remplacés par les doublures posées par
``tests/test_gcm.py`` avant l'import, complétées ici (``GestureMultiPress``,
``icon_size_lookup``, ``Popover``, ``Gio.Menu``/``SimpleAction``…) via
``monkeypatch`` sur les classes du stub partagé.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

import test_gcm  # noqa: E402, F401  (pose les doublures gi/Gtk avant l'import)

widgets: object = importlib.import_module("widgets")

Gtk = widgets.Gtk
Gdk = widgets.Gdk
Gio = widgets.Gio
GLib = widgets.GLib
gcm = importlib.import_module("gnome_connection_manager")


# ---------------------------------------------------------------------------
# Doublures propres à widgets.py
# ---------------------------------------------------------------------------


class _FauxVariant:
    """GLib.Variant booléen : mémorise la valeur et la relit."""

    def __init__(self, valeur: bool) -> None:
        """Nouvelle variante booléenne."""
        self.valeur = bool(valeur)

    def get_boolean(self):  # noqa: N802
        """Valeur booléenne portée."""
        return self.valeur


class _FauxGeste:
    """Geste multi-pression : mémorise bouton, widget, signaux et état."""

    def __init__(self, cible=None) -> None:
        """Nouveau geste, bouton 0 (tous les boutons)."""
        self.cible = cible
        self.bouton = 0
        self.etat = None
        self.signaux: dict[str, object] = {}

    def get_current_button(self):  # noqa: N802
        """Bouton du clic en cours."""
        return self.bouton

    def get_widget(self):  # noqa: N802
        """Widget porteur du geste."""
        return self.cible

    def set_button(self, bouton) -> None:  # noqa: N802
        """Limite les boutons écoutés."""
        self.bouton = bouton

    def set_propagation_phase(self, phase) -> None:  # noqa: N802
        """Phase de propagation (mémorisée, sans effet)."""
        self.phase = phase

    def connect(self, signal, rappel) -> None:  # noqa: A003
        """Enregistre un gestionnaire de signal."""
        self.signaux[signal] = rappel

    def set_state(self, etat) -> None:  # noqa: N802
        """Revendique la séquence."""
        self.etat = etat


class _FauxPop:
    """Popover : mémorise ancrage, pointage, position, actions et affichage."""

    def __init__(self) -> None:
        """Popover masqué, sans parent."""
        self.relatif = None
        self.pointage = None
        self.visible = False
        self.groupes: dict[str, object] = {}
        self.position = None
        self.parent = None
        self.signaux: dict[str, object] = {}

    def set_relative_to(self, relatif) -> None:  # noqa: N802
        """Widget d'ancrage."""
        self.relatif = relatif

    def set_parent(self, parent) -> None:  # noqa: N802
        """Parent (PopoverMenu GTK4)."""
        self.parent = parent

    def set_pointing_to(self, rect) -> None:  # noqa: N802
        """Rectangle pointé."""
        self.pointage = rect

    def set_position(self, position) -> None:  # noqa: N802
        """Position souhaitée."""
        self.position = position

    def popup(self) -> None:  # noqa: A003
        """Affiche."""
        self.visible = True

    def popdown(self) -> None:  # noqa: A003
        """Masque."""
        self.visible = False

    def insert_action_group(self, prefixe, groupe) -> None:  # noqa: N802
        """Attache un groupe d'actions."""
        self.groupes[prefixe] = groupe

    def connect(self, signal, rappel) -> None:  # noqa: A003
        """Enregistre un gestionnaire."""
        self.signaux[signal] = rappel


class _FauxAction:
    """Gio.SimpleAction : état, sensibilité, signaux, activation."""

    def __init__(self, nom, etat=None) -> None:
        """Action nommée, éventuellement à état initial."""
        self.nom = nom
        self.etat = etat
        self.actif = True
        self.signaux: dict[str, object] = {}

    @classmethod
    def new(cls, nom, _param):  # noqa: N802
        """Action simple."""
        return cls(nom)

    @classmethod
    def new_stateful(cls, nom, _param, etat):  # noqa: N802
        """Action à état (case à cocher)."""
        return cls(nom, etat=etat)

    def connect(self, signal, rappel) -> None:  # noqa: A003
        """Enregistre un gestionnaire de signal."""
        self.signaux[signal] = rappel

    def set_state(self, etat) -> None:  # noqa: N802
        """Change l'état affiché."""
        self.etat = etat

    def get_state(self):  # noqa: N802
        """État courant."""
        return self.etat

    def set_enabled(self, actif) -> None:  # noqa: N802
        """Active ou grise."""
        self.actif = actif

    def get_enabled(self):  # noqa: N802
        """Sensibilité courante."""
        return self.actif

    def activate(self, parametre=None) -> None:  # noqa: A003
        """Émet le signal « activate »."""
        self.signaux["activate"](self, parametre)

    def change_state(self, valeur) -> None:  # noqa: N802
        """Émet le signal « change-state »."""
        self.signaux["change-state"](self, valeur)


class _FauxGroupeActions:
    """Gio.SimpleActionGroup : actions nommées, ajout et retrait."""

    def __init__(self) -> None:
        """Groupe vide."""
        self.actions: dict[str, _FauxAction] = {}

    def add_action(self, action) -> None:  # noqa: N802
        """Ajoute (ou remplace) une action."""
        self.actions[action.nom] = action

    def lookup_action(self, nom):  # noqa: N802
        """Action par son nom."""
        return self.actions[nom]

    def remove_action(self, nom) -> None:  # noqa: N802
        """Retire une action."""
        self.actions.pop(nom, None)


class _FauxSection:
    """Gio.Menu : entrées (libellé, action), sections, sous-menus."""

    def __init__(self) -> None:
        """Menu vide."""
        self.entrees: list[tuple[str, str]] = []
        self.sections: list[_FauxSection] = []
        self.sous_menus: list[tuple[str, _FauxSection]] = []

    def append(self, libelle, action) -> None:  # noqa: A003
        """Ajoute une entrée."""
        self.entrees.append((libelle, action))

    def append_section(self, _titre, section) -> None:  # noqa: N802
        """Ajoute une section."""
        self.sections.append(section)

    def append_submenu(self, libelle, modele) -> None:  # noqa: N802
        """Ajoute un sous-menu."""
        self.sous_menus.append((libelle, modele))

    def append_item(self, item) -> None:  # noqa: N802
        """Ajoute un Gio.MenuItem (sous-menu des touches spéciales)."""
        self.sous_menus.append((item.libelle, item.sous_menu))

    def pack_start(self, enfant, *_a) -> None:  # noqa: N802
        """Empaquette un enfant (vbox/action_area du dialogue)."""
        self.entrees.append(enfant)

    def show(self) -> None:  # noqa: N802
        """Affiche le menu (sans effet)."""

    def remove_all(self) -> None:  # noqa: N802
        """Vide le menu."""
        self.entrees.clear()
        self.sections.clear()
        self.sous_menus.clear()


class _FauxItem:
    """Gio.MenuItem : libellé et sous-menu."""

    def __init__(self, libelle, sous_menu=None) -> None:
        """Item de menu."""
        self.libelle = libelle
        self.sous_menu = sous_menu

    @classmethod
    def new(cls, libelle, action):  # noqa: N802
        """Item simple (action détaillée posée à part)."""
        return cls(libelle)

    def set_detailed_action(self, action) -> None:  # noqa: N802
        """Action détaillée (ignorée par la doublure)."""
        self.action = action

    def set_submenu(self, sous_menu) -> None:  # noqa: N802
        """Sous-menu porté."""
        self.sous_menu = sous_menu


class _FauxBouton:
    """Bouton : sensibilité et clics comptés."""

    def __init__(self, sensible=True) -> None:
        """Bouton sensible par défaut."""
        self.sensible = sensible
        self.clics = 0

    def get_sensitive(self):  # noqa: N802
        """État de sensibilité."""
        return self.sensible

    def clicked(self):  # noqa: N802
        """Déclenche le gestionnaire « clicked »."""
        self.clics += 1


class _FauxTerminal:
    """Vte.Terminal : host, spawn_async/spawn_sync enregistrés."""

    def __init__(self, term="xterm") -> None:
        """Terminal avec un hôte factice."""
        self.host = SimpleNamespace(term=term)
        self.async_args: tuple = ()
        self.sync_args: tuple = ()

    def spawn_async(self, *args):  # noqa: N802
        """Mémorise l'appel spawn_async."""
        self.async_args = args

    def spawn_sync(self, *args):  # noqa: N802
        """Mémorise l'appel spawn_sync."""
        self.sync_args = args


@pytest.fixture(autouse=True)
def _complements_gtk(monkeypatch):
    """Complète les doublures Gtk/Gdk/Gio pour widgets.py."""
    monkeypatch.setattr(
        Gtk, "GestureMultiPress", SimpleNamespace(new=lambda cible: _FauxGeste(cible))
    )
    monkeypatch.setattr(
        Gtk, "EventControllerKey", SimpleNamespace(new=lambda cible: _FauxGeste(cible))
    )
    monkeypatch.setattr(Gtk, "icon_size_lookup", lambda taille: (True, 18, 18))
    monkeypatch.setattr(
        Gtk,
        "Image",
        SimpleNamespace(new_from_icon_name=lambda *_a, **_k: SimpleNamespace()),
        raising=False,
    )
    monkeypatch.setattr(Gtk, "EventSequenceState", SimpleNamespace(CLAIMED="claimed"))
    monkeypatch.setattr(Gtk, "PositionType", SimpleNamespace(BOTTOM="bottom"))
    monkeypatch.setattr(Gtk, "PropagationPhase", SimpleNamespace(TARGET="target"))
    monkeypatch.setattr(Gtk, "IconSize", SimpleNamespace(MENU="menu"), raising=False)
    monkeypatch.setattr(Gtk, "ReliefStyle", SimpleNamespace(NONE="none"), raising=False)
    monkeypatch.setattr(
        Gdk, "EventMask", SimpleNamespace(SCROLL_MASK=1, SMOOTH_SCROLL_MASK=2), raising=False
    )
    # popup-menu (signal GTK3) : signal_lookup retourne 0 => chemin non câblé.
    monkeypatch.setattr(widgets.GObject, "signal_lookup", lambda *_a: 0, raising=False)
    monkeypatch.setattr(
        Gtk, "EventControllerFocus", SimpleNamespace(new=lambda *_a: _FauxGeste()), raising=False
    )
    # Enregistrement des enfants packés et des signaux connectés, pour
    # atteindre boutons et gestionnaires depuis les tests.
    _Base = Gtk.Box.__mro__[1]

    def _pack(self, enfant, *_a):
        if not hasattr(self, "enfants"):
            self.enfants = []
        self.enfants.append(enfant)

    def _add(self, enfant):
        if not hasattr(self, "enfants"):
            self.enfants = []
        self.enfants.append(enfant)

    def _connect(self, signal, rappel, *_a):
        if not hasattr(self, "signaux"):
            self.signaux = {}
        self.signaux[signal] = rappel

    def _add_class(self, nom):
        if not hasattr(self, "classes"):
            self.classes = set()
        self.classes.add(nom)

    def _remove_class(self, nom):
        if not hasattr(self, "classes"):
            self.classes = set()
        self.classes.discard(nom)

    def _label_init(self, label="", *_a, **_kw):
        self._text = label

    monkeypatch.setattr(_Base, "pack_start", _pack)
    monkeypatch.setattr(_Base, "add", _add)
    monkeypatch.setattr(_Base, "connect", _connect)
    monkeypatch.setattr(_Base, "add_class", _add_class, raising=False)
    monkeypatch.setattr(_Base, "remove_class", _remove_class, raising=False)
    monkeypatch.setattr(Gtk.Label, "__init__", _label_init)
    monkeypatch.setattr(Gtk.Box, "set_border_width", lambda self, *a: None, raising=False)
    monkeypatch.setattr(
        Gdk, "Rectangle", lambda: SimpleNamespace(x=0, y=0, width=0, height=0), raising=False
    )
    monkeypatch.setattr(
        GLib, "Variant", SimpleNamespace(new_boolean=lambda v: _FauxVariant(v)), raising=False
    )
    monkeypatch.setattr(
        Gtk, "Popover", SimpleNamespace(new_from_model=lambda *_a, **_k: _FauxPop()), raising=False
    )
    monkeypatch.setattr(
        Gtk,
        "PopoverMenu",
        SimpleNamespace(new_from_model=lambda *_a, **_k: _FauxPop()),
        raising=False,
    )
    monkeypatch.setattr(Gio, "Menu", _FauxSection)
    monkeypatch.setattr(Gio, "MenuItem", _FauxItem, raising=False)
    monkeypatch.setattr(Gio, "SimpleAction", _FauxAction)
    monkeypatch.setattr(Gio, "SimpleActionGroup", _FauxGroupeActions)
    # Méthodes absentes des classes du stub partagé (posées puis retirées).
    monkeypatch.setattr(_Base, "queue_draw", lambda self: None, raising=False)
    monkeypatch.setattr(_Base, "add_events", lambda self, *a: None, raising=False)
    monkeypatch.setattr(_Base, "show", lambda self: None, raising=False)
    monkeypatch.setattr(Gtk.Button, "set_relief", lambda self, *a: None, raising=False)
    monkeypatch.setattr(Gtk.Button, "set_size_request", lambda self, *a: None, raising=False)
    monkeypatch.setattr(Gtk.Button, "set_can_default", lambda self, *a: None, raising=False)
    monkeypatch.setattr(Gtk.Button, "grab_default", lambda self: None, raising=False)
    monkeypatch.setattr(Gtk.Entry, "grab_focus", lambda self: None, raising=False)
    monkeypatch.setattr(Gtk.Entry, "set_visibility", lambda self, *a: None, raising=False)

    def _init_dialog(self, *a, **_k):
        """Dialogue avec zones vbox/action_area détachables."""
        self.vbox = _FauxSection()
        self.action_area = _FauxSection()
        self.reponses: list[object] = []
        self.visible = True

    monkeypatch.setattr(Gtk.Dialog, "__init__", _init_dialog)
    monkeypatch.setattr(Gtk.Dialog, "set_modal", lambda self, *a: None, raising=False)
    monkeypatch.setattr(
        Gtk.Dialog, "response", lambda self, r: self.reponses.append(r), raising=False
    )
    monkeypatch.setattr(Gtk.Dialog, "hide", lambda self: None, raising=False)

    # TextView / CellTextView : tampon, signaux, édition en ligne.
    def _emit(self, signal, *args):
        """Émet un signal GTK sur le widget."""
        gestionnaire = self.signaux.get(signal)
        if gestionnaire is not None:
            gestionnaire(self, *args)

    monkeypatch.setattr(Gtk.TextView, "emit", _emit, raising=False)
    monkeypatch.setattr(Gtk.TextView, "remove_widget", lambda self: None, raising=False)
    monkeypatch.setattr(Gtk.TextView, "get_buffer", lambda self: self._tampon, raising=False)
    monkeypatch.setattr(Gtk.TextView, "set_size_request", lambda self, *a: None, raising=False)
    monkeypatch.setattr(Gtk.TextView, "set_border_width", lambda self, *a: None, raising=False)
    monkeypatch.setattr(Gtk.TextView, "grab_focus", lambda self: None, raising=False)
    monkeypatch.setattr(Gtk.TextView, "add_controller", lambda self, *a: None, raising=False)
    monkeypatch.setattr(Gtk.TextView, "insert_action_group", lambda self, *a: None, raising=False)
    monkeypatch.setattr(Gtk.TextView, "show", lambda self: None, raising=False)
    monkeypatch.setattr(
        Gtk.CellRendererText,
        "props",
        SimpleNamespace(font_desc="Sans 10", text="texte", xpad=2, ypad=2),
        raising=False,
    )

    def _textview_init(self, *a, **_k):
        """TextView avec tampon factice et signaux."""
        self._tampon = SimpleNamespace(
            texte="",
            set_text=lambda t: setattr(self._tampon, "texte", t),
            get_bounds=lambda: ("debut", "fin"),
            get_text=lambda *a, **k: self._tampon.texte,
        )
        self.signaux: dict[str, object] = {}

    monkeypatch.setattr(Gtk.TextView, "__init__", _textview_init)


# ---------------------------------------------------------------------------
# RemoteDesktopContextMenu / build_remote_desktop_context_menu
# ---------------------------------------------------------------------------


def test_menu_bureau_distant_construction():
    """Le menu bâtit actions, section principale et sous-menu « Special keys »."""
    menu = widgets.build_remote_desktop_context_menu(
        _FauxBouton(), _FauxBouton(sensible=False), lambda _c: None
    )
    # Le popover porte le groupe d'actions sous le préfixe documenté.
    assert "rdmenu" in menu.popover.groupes
    # Section principale : Connect, Disconnect.
    section = menu.model.sections[0]
    assert [e[1] for e in section.entrees[:2]] == ["rdmenu.connect", "rdmenu.disconnect"]
    # Sous-menu « Special keys » : autant d'entrées que de combinaisons.
    _libelle, touches = menu.model.sous_menus[0]
    from remote_desktop_menu_core import REMOTE_DESKTOP_SPECIAL_KEY_COMBOS

    assert len(touches.entrees) == len(REMOTE_DESKTOP_SPECIAL_KEY_COMBOS)


def test_menu_bureau_distant_extra_items():
    """Les entrées supplémentaires sont numérotées extra0, extra1…"""
    menu = widgets.RemoteDesktopContextMenu(
        _FauxBouton(), _FauxBouton(), lambda _c: None, extra_items=[("USB", lambda _a: None)]
    )
    section = menu.model.sections[0]
    assert section.entrees[2] == ("USB", "rdmenu.extra0")


def test_menu_bureau_distant_popup_at():
    """popup_at ancre le popover, pointe le clic et affiche."""
    menu = widgets.RemoteDesktopContextMenu(_FauxBouton(), _FauxBouton(), lambda _c: None)
    ancre = SimpleNamespace(nom="etiquette")
    menu.popup_at(ancre, 12, 34)
    assert menu.popover.relatif is ancre
    assert (menu.popover.pointage.x, menu.popover.pointage.y) == (12, 34)
    assert menu.popover.visible


# ---------------------------------------------------------------------------
# mount_iso_temp / umount_iso_temp
# ---------------------------------------------------------------------------


def test_mount_iso_sans_udisksctl(monkeypatch):
    """Sans udisksctl : aucun montage, avertissement journalisé."""
    monkeypatch.setattr(widgets.shutil, "which", lambda nom: None)
    assert widgets.mount_iso_temp("/tmp/a.iso") is None


def _sorties_udisks(monkeypatch, reponses):
    """Faux subprocess.run qui rend les réponses dans l'ordre."""
    appels: list[list[str]] = []

    def faux_run(cmd, **_kwargs):
        appels.append(cmd)
        out = reponses.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(stdout=out)

    monkeypatch.setattr(widgets.shutil, "which", lambda nom: "/usr/bin/udisksctl")
    monkeypatch.setattr(widgets.subprocess, "run", faux_run)
    return appels


def test_mount_iso_succes(monkeypatch):
    """Montage complet : point de montage et périphérique loop."""
    appels = _sorties_udisks(
        monkeypatch,
        [
            "Mapped file /tmp/a.iso as /dev/loop0.",
            "Mounted /dev/loop0 at /run/media/user/LABEL.",
        ],
    )
    assert widgets.mount_iso_temp("/tmp/a.iso") == ("/run/media/user/LABEL", "/dev/loop0")
    assert appels[0][:3] == ["udisksctl", "loop-setup", "-r"]


def test_mount_iso_echec(monkeypatch):
    """Échec du loop-setup : None, sans lever."""
    _sorties_udisks(monkeypatch, [subprocess.CalledProcessError(1, "udisksctl", stderr="boom")])
    assert widgets.mount_iso_temp("/tmp/a.iso") is None


def test_umount_iso_sans_peripherique():
    """Périphérique vide : aucun appel, retour immédiat."""
    assert widgets.umount_iso_temp("") is None


def test_umount_iso_succes(monkeypatch):
    """Démontage puis libération du périphérique loop."""
    appels: list[list[str]] = []
    monkeypatch.setattr(widgets.subprocess, "run", lambda cmd, **_k: appels.append(cmd))
    widgets.umount_iso_temp("/dev/loop0")
    assert appels[0][:2] == ["udisksctl", "unmount"]
    assert appels[1][:2] == ["udisksctl", "loop-delete"]


def test_umount_iso_erreur_non_fatale(monkeypatch):
    """Une erreur de démontage est journalisée, jamais levée."""

    def faux_run(cmd, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(widgets.subprocess, "run", faux_run)
    widgets.umount_iso_temp("/dev/loop0")  # ne lève pas


# ---------------------------------------------------------------------------
# vte_run
# ---------------------------------------------------------------------------


def test_vte_run_spawn_async(monkeypatch):
    """spawn_async reçoit la commande, l'environnement et le dossier."""
    # TERMINAL_V048 vaut False à l'import (Vte.Terminal est un MagicMock
    # des doublures), on force ici la branche moderne.
    monkeypatch.setattr(widgets, "TERMINAL_V048", True)
    term = _FauxTerminal(term="xterm-256color")
    widgets.vte_run(term, "/bin/ssh", ["user@hote"], extra_env={"IPMI_PASSWORD": "x" * 8})
    pty, dossier, argv, envv = term.async_args[:4]
    assert argv[0] == "/bin/ssh" and "user@hote" in argv
    assert any(e.startswith("TERM=xterm-256color") for e in envv)
    assert any(e.startswith("IPMI_PASSWORD=") for e in envv)
    assert dossier == os.getenv("HOME")


def test_vte_run_spawn_sync(monkeypatch):
    """VTE < 0.48 : repli sur spawn_sync avec DO_NOT_REAP_CHILD."""
    monkeypatch.setattr(widgets, "TERMINAL_V048", False)
    term = _FauxTerminal()
    widgets.vte_run(term, widgets.SHELL, None)
    argv, envv, flags = term.sync_args[2:5]
    assert argv == [widgets.SHELL]
    assert flags & GLib.SpawnFlags.DO_NOT_REAP_CHILD


# ---------------------------------------------------------------------------
# ManagementTabLabel / _LogActionShim
# ---------------------------------------------------------------------------


def test_management_tab_label():
    """Étiquette de gestion : titre lu, bouton fermeture câblé."""
    fermetures = []
    etq = widgets.ManagementTabLabel("Paramètres", lambda: fermetures.append(1))
    assert etq.get_text() == "Paramètres"
    bouton = etq.enfants[-1]
    bouton.signaux["clicked"](bouton)
    assert fermetures == [1]


def test_log_action_shim():
    """L'adaptateur lit et écrit l'état booléen de l'action."""
    action = _FauxAction("log", etat=_FauxVariant(True))
    shim = widgets._LogActionShim(action)
    assert shim.get_active() is True
    shim.set_active(False)
    assert action.etat.valeur is False


# ---------------------------------------------------------------------------
# TabContextMenu
# ---------------------------------------------------------------------------


def test_tab_context_menu_construction():
    """Le groupe porte l'action « log » et les huit entrées du menu."""
    menu = widgets.TabContextMenu(lambda code: None)
    noms = set(menu.actions.actions)
    assert "log" in noms
    assert {"rename", "reset", "clear", "reopen", "clone", "split-h", "split-v", "unsplit"} <= noms


def test_tab_context_menu_activation_relayee():
    """L'activation d'une action appelle dispatch avec le code historique."""
    recus = []
    menu = widgets.TabContextMenu(lambda widget, code: recus.append(code))
    menu.actions.actions["rename"].activate()
    assert recus == ["R"]


def test_tab_context_menu_rebuild():
    """Rebuild reconstruit le modèle selon l'état de l'onglet."""
    menu = widgets.TabContextMenu(lambda code: None)
    menu.rebuild(is_active=False, multi_pane=True, log_active=True)
    aplati = [nom for section in menu.model.sections for _l, nom in section.entrees]
    assert "tabmenu.reopen" in aplati and "tabmenu.split-h" in aplati
    assert menu._log_action.etat.valeur is True
    menu.rebuild(is_active=True, multi_pane=False, log_active=False)
    aplati = [nom for section in menu.model.sections for _l, nom in section.entrees]
    assert "tabmenu.reopen" not in aplati and "tabmenu.split-h" not in aplati
    assert menu._log_action.etat.valeur is False


def test_tab_context_menu_log_change_state():
    """La bascule « Enable logging » relaie le shim au dispatch avec « L »."""
    recus = []
    menu = widgets.TabContextMenu(lambda widget, code: recus.append(code))
    menu.actions.actions["log"].change_state(_FauxVariant(True))
    assert recus == ["L"]
    assert menu.actions.actions["log"].etat.valeur is True


def test_tab_context_menu_popup_at():
    """popup_at ancre et affiche le popover."""
    menu = widgets.TabContextMenu(lambda code: None)
    ancre = SimpleNamespace(nom="eb")
    menu.popup_at(ancre, 5, 7)
    assert menu.popover.relatif is ancre and menu.popover.visible


# ---------------------------------------------------------------------------
# FolderContextMenu
# ---------------------------------------------------------------------------


def test_folder_context_menu_construction():
    """Le groupe porte les actions statiques et les deux entrées dispatch."""
    menu = widgets.FolderContextMenu(
        {"connect": lambda: None, "add": lambda: None}, lambda w, code: None
    )
    noms = set(menu.actions.actions)
    assert {"connect", "add", "copy-address", "duplicate"} <= noms


def test_folder_context_menu_rebuild(monkeypatch):
    """Rebuild bâtit la disposition de la cible du clic et les items protocole."""
    menu = widgets.FolderContextMenu({}, lambda w, code: None)
    menu.rebuild(click_target="host")
    aplati = [nom for section in menu.model.sections for _l, nom in section.entrees]
    assert "foldermenu.connect" in aplati and "foldermenu.edit" in aplati
    # Items spécifiques au protocole, fournis par l'appelant.
    menu.protocol_items_provider = lambda: [("Ouvrir SSH", lambda: None)]
    menu.rebuild(click_target="host")
    protocole = menu.model.sections[-1]
    assert protocole.entrees[0][0] == "Ouvrir SSH"


def test_folder_context_menu_rebuild_vide():
    """Clic sur zone vide : pas d'entrée « Edit », actions de dossier grises."""
    menu = widgets.FolderContextMenu({}, lambda w, code: None)
    menu.rebuild(click_target="empty")
    aplati = [nom for section in menu.model.sections for _l, nom in section.entrees]
    assert "foldermenu.edit" not in aplati and "foldermenu.delete" not in aplati


# ---------------------------------------------------------------------------
# PopupMenu
# ---------------------------------------------------------------------------


def test_popup_menu_construction():
    """Le menu du terminal bâtit ses sections et le sous-menu « Custom commands »."""
    menu = widgets.PopupMenu(lambda w, code: None)
    aplati = [nom for section in menu.model.sections for _l, nom in section.entrees]
    assert "popupmenu.copy" in aplati and "popupmenu.log" in aplati
    assert menu.model.sections[-1].sous_menus[0][0] == "Custom commands"


def test_popup_menu_rebuild():
    """Rebuild règle la sensibilité des actions variables."""
    menu = widgets.PopupMenu(lambda w, code: None)
    menu.rebuild(has_selection=False, multi_pane=False, has_split_notebook=False, log_active=False)
    assert menu.actions.actions["copy"].actif is False
    menu.rebuild(has_selection=True, multi_pane=True, has_split_notebook=True, log_active=True)
    assert menu.actions.actions["copy"].actif is True


def test_popup_menu_populate_commands():
    """populate_commands repeuple le sous-menu et retire les actions orphelines."""
    menu = widgets.PopupMenu(lambda w, code: None)
    menu.populate_commands([("Ctrl+1", "echo un"), ("Ctrl+2", "echo deux")])
    assert len(menu.commands_model.entrees) == 2
    assert set(menu.actions.actions) >= {"command-0", "command-1"}
    menu.populate_commands([("Ctrl+1", "echo un")])
    assert len(menu.commands_model.entrees) == 1
    assert "command-1" not in menu.actions.actions


def test_popup_menu_popup_at():
    """popup_at et popup_commands_at affichent leurs popovers."""
    menu = widgets.PopupMenu(lambda w, code: None)
    menu.popup_at(SimpleNamespace(), 1, 2)
    assert menu.popover.visible
    menu.popup_commands_at(SimpleNamespace(nom="hamburger"))
    assert menu.commands_popover.visible


# ---------------------------------------------------------------------------
# NotebookTabLabel
# ---------------------------------------------------------------------------


class _FauxNotebook:
    """Notebook : pages, parent des widgets, numéro de page."""

    def __init__(self, pages=None) -> None:
        """Notebook avec des pages (widgets)."""
        self.pages = pages if pages is not None else []

    def get_n_pages(self):  # noqa: N802
        """Nombre de pages."""
        return len(self.pages)

    def page_num(self, widget):  # noqa: N802
        """Index d'un widget."""
        return self.pages.index(widget) if widget in self.pages else -1

    def get_nth_page(self, numero):  # noqa: N802
        """Page d'index donné."""
        return self.pages[numero]

    def remove_page(self, numero):  # noqa: N802
        """Retire une page."""
        self.pages.pop(numero)


def _contenu_terminal(nb, exit_status=0):
    """Widget d'onglet : enfant d'un notebook, terminal accessible."""
    terminal = SimpleNamespace(log_handler_id=0, get_child_exit_status=lambda: exit_status)
    contenu = SimpleNamespace(
        parent=nb,
        get_parent=lambda: nb,
        get_children=lambda: [terminal],
        get_child=lambda: terminal,
        destroy=lambda: None,
    )
    return contenu


def _etiquette_onglet(monkeypatch, auto_close="", conf_close=False):
    """NotebookTabLabel sur un faux notebook, avec conf maîtrisée."""
    nb = _FauxNotebook()
    contenu = _contenu_terminal(nb)
    etq = widgets.NotebookTabLabel(
        "hote1",
        nb,
        contenu,
        widgets.TabContextMenu(lambda code: None),
        host_auto_close_tab=auto_close,
    )
    nb.pages = [contenu]
    gcm.conf.CONFIRM_ON_CLOSE_TAB = conf_close
    gcm.conf.CONFIRM_ON_CLOSE_TAB_MIDDLE = conf_close
    return etq, nb, contenu


def test_notebook_tab_label_construction():
    """Titre, état actif, geste sur l'étiquette et bouton de fermeture."""
    etq, _nb, _contenu = _etiquette_onglet(None)
    assert etq.get_text() == "hote1" and etq.is_active
    assert etq.eb.enfants and etq.eb2.enfants


def test_notebook_tab_label_set_selected():
    """set_selected ajoute ou retire la classe « selected »."""
    etq, _nb, _contenu = _etiquette_onglet(None)
    etq.set_selected(True)
    assert "selected" in etq.classes
    etq.set_selected(False)
    assert "selected" not in etq.classes


def test_notebook_tab_label_close_tab():
    """close_tab retire la page et détruit le widget."""
    etq, nb, contenu = _etiquette_onglet(None)
    etq.close_tab(contenu)
    assert nb.pages == []


def test_notebook_tab_label_on_close_tab_sans_confirmation(monkeypatch):
    """Sans confirmation demandée : fermeture directe."""
    etq, nb, contenu = _etiquette_onglet(monkeypatch)
    assert etq.on_close_tab(None, nb) is None
    assert nb.pages == []


def test_notebook_tab_label_on_close_tab_annulee(monkeypatch):
    """Confirmation refusée : l'onglet reste ouvert."""
    etq, nb, contenu = _etiquette_onglet(monkeypatch, conf_close=True)
    monkeypatch.setattr(gcm, "msgconfirm", lambda *_a: Gtk.ResponseType.CANCEL)
    assert etq.on_close_tab(None, nb) is True
    assert nb.pages == [contenu]


def test_mark_tab_as_closed_jamais(monkeypatch):
    """Fermeture automatique « jamais » : onglet conservé, marqué inactif."""
    etq, nb, contenu = _etiquette_onglet(monkeypatch)
    monkeypatch.setattr(gcm, "send_desktop_notification", lambda *_a: None)
    etq.mark_tab_as_closed()
    assert etq.is_active is False and nb.pages == [contenu]


def test_mark_tab_as_closed_toujours(monkeypatch):
    """Fermeture automatique « toujours » : la page est retirée."""
    etq, nb, contenu = _etiquette_onglet(monkeypatch, auto_close="1")
    monkeypatch.setattr(gcm, "send_desktop_notification", lambda *_a: None)
    etq.mark_tab_as_closed()
    assert nb.pages == []


def test_mark_tab_as_closed_sortie_propre(monkeypatch):
    """Mode « sortie propre » : fermé si exit 0, conservé sinon."""
    etq, nb, contenu = _etiquette_onglet(monkeypatch, auto_close="2")
    monkeypatch.setattr(gcm, "send_desktop_notification", lambda *_a: None)
    etq.mark_tab_as_closed()
    assert nb.pages == []  # exit 0 : sortie propre
    # Sortie non propre (exit 1) : l'onglet est conservé.
    etq2, nb2, contenu2 = _etiquette_onglet(monkeypatch, auto_close="2")
    contenu2.get_children()[0].get_child_exit_status = lambda: 1
    etq2.mark_tab_as_closed()
    assert nb2.pages


def test_mark_tab_as_closed_sans_parent(monkeypatch):
    """Mode « sortie propre » sans notebook parent : onglet conservé."""
    etq, nb, contenu = _etiquette_onglet(monkeypatch, auto_close="2")
    monkeypatch.setattr(gcm, "send_desktop_notification", lambda *_a: None)
    contenu.get_parent = lambda: None
    etq.mark_tab_as_closed()
    assert nb.pages  # notebook.getPageNum fallback : aucune page connue, rien fermé


def test_mark_tab_as_closed_commande_post_connexion(monkeypatch):
    """La commande « après connexion » de l'hôte est lancée à la fermeture."""
    lancees = []
    monkeypatch.setattr(gcm, "send_desktop_notification", lambda *_a: None)
    monkeypatch.setattr(gcm, "_launch_hook_command", lambda cmd, _ctx: lancees.append(cmd))
    etq, _nb, _contenu = _etiquette_onglet(monkeypatch)
    etq.host_post_connect_command = "echo fait"
    etq.mark_tab_as_closed()
    assert lancees == ["echo fait"]


def test_effective_auto_close_tab(monkeypatch):
    """La surcharge par hôte prime sur le réglage global."""
    etq, _nb, _contenu = _etiquette_onglet(monkeypatch, auto_close="2")
    gcm.conf.AUTO_CLOSE_TAB = 0
    assert etq.effective_auto_close_tab() == 2
    etq.host_auto_close_tab = ""
    assert etq.effective_auto_close_tab() == 0


def test_on_label_pressed_menu_onglet_terminal(monkeypatch):
    """Clic droit sur un onglet terminal : menu reconstruit et affiché."""
    etq, nb, contenu = _etiquette_onglet(monkeypatch)
    geste = _FauxGeste(cible=etq.eb)
    geste.bouton = 3
    etq.on_label_pressed(geste, 1, 3, 4)
    assert geste.etat == "claimed" and etq.popup.popover.visible
    aplati = [nom for section in etq.popup.model.sections for _l, nom in section.entrees]
    assert "tabmenu.rename" in aplati


def test_on_label_pressed_menu_bureau_distant(monkeypatch):
    """Clic droit sur un onglet RDP/VNC : menu du widget distant."""
    etq, _nb, contenu = _etiquette_onglet(monkeypatch)
    menu_distant = widgets.RemoteDesktopContextMenu(_FauxBouton(), _FauxBouton(), lambda _c: None)
    distant = SimpleNamespace(build_context_menu=lambda: menu_distant)
    contenu.get_child = lambda: distant
    # RDP : le widget distant est l'onglet lui-même (pas de ScrolledWindow).
    etq.widget_ = SimpleNamespace(
        parent=None,
        get_parent=lambda: None,
        get_child=lambda: distant,
        destroy=lambda: None,
        build_context_menu=lambda: menu_distant,
    )
    geste = _FauxGeste(cible=etq.eb)
    geste.bouton = 3
    etq.on_label_pressed(geste, 1, 2, 3)
    assert geste.etat == "claimed"
    assert menu_distant.popover.visible and menu_distant.popover.relatif is etq.eb


def test_on_label_pressed_clic_milieu_ferme(monkeypatch):
    """Clic milieu sans confirmation : l'onglet est fermé."""
    etq, nb, contenu = _etiquette_onglet(monkeypatch)
    geste = _FauxGeste(cible=etq.eb)
    geste.bouton = 2
    etq.on_label_pressed(geste, 1, 1, 1)
    assert nb.pages == []


def test_on_label_pressed_clic_milieu_annule(monkeypatch):
    """Clic milieu avec confirmation refusée : onglet conservé, séquence revendiquée."""
    etq, nb, contenu = _etiquette_onglet(monkeypatch, conf_close=True)
    monkeypatch.setattr(gcm, "msgconfirm", lambda *_a: Gtk.ResponseType.CANCEL)
    geste = _FauxGeste(cible=etq.eb)
    geste.bouton = 2
    etq.on_label_pressed(geste, 1, 1, 1)
    assert nb.pages == [contenu] and geste.etat == "claimed"


def test_on_label_pressed_clic_gauche_propage():
    """Clic gauche : l'événement se propage, rien ne se passe."""
    etq, nb, contenu = _etiquette_onglet(None)
    geste = _FauxGeste(cible=etq.eb)
    geste.bouton = 1
    etq.on_label_pressed(geste, 1, 1, 1)
    assert geste.etat is None and nb.pages == [contenu]


# ---------------------------------------------------------------------------
# EntryDialog / inputbox
# ---------------------------------------------------------------------------


def test_entry_dialog_saisie_validee():
    """« OK » mémorise la saisie et répond OK."""
    dlg = widgets.EntryDialog("Titre", "Message", default_text="defaut")
    assert dlg.entry.get_text() == "defaut"
    dlg.entry.set_text("valeur")
    dlg.click(None)
    assert dlg.value == "valeur"
    assert dlg.reponses == [Gtk.ResponseType.OK]


def test_entry_dialog_annule():
    """« Cancel » masque et détruit sans valeur."""
    dlg = widgets.EntryDialog("Titre", "Message")
    dlg.quit()
    assert dlg.reponses == []


def test_inputbox_ok(monkeypatch):
    """Dialogue validé : la saisie est rendue."""
    dlg_recus = []

    def faux_run_dialog(dlg):
        dlg_recus.append(dlg)
        dlg.value = "reponse"
        return Gtk.ResponseType.OK

    monkeypatch.setattr(widgets, "run_dialog_sync", faux_run_dialog)
    assert widgets.inputbox("Titre", "Texte", default="x") == "reponse"


def test_inputbox_annule(monkeypatch):
    """Dialogue annulé : None."""
    monkeypatch.setattr(widgets, "run_dialog_sync", lambda dlg: Gtk.ResponseType.CANCEL)
    assert widgets.inputbox("Titre", "Texte") is None


# ---------------------------------------------------------------------------
# CellTextView / MultilineCellRenderer
# ---------------------------------------------------------------------------


def test_cell_text_view_texte():
    """Le texte passe par le tampon de la vue."""
    vue = widgets.CellTextView()
    vue.set_text("ligne1\nligne2")
    assert vue.get_text() == "ligne1\nligne2"


def test_multiline_cell_renderer_start_editing(monkeypatch):
    """do_start_editing construit l'éditeur et câble ses contrôleurs."""
    monkeypatch.setattr(gcm, "set_widget_font", lambda *a: None)
    rendu = widgets.MultilineCellRenderer()
    editeur = rendu.do_start_editing(
        None, None, "3", None, SimpleNamespace(width=10, height=8), None
    )
    assert editeur is not None and editeur.path == "3"
    assert "pressed" in editeur._button_gesture.signaux
    assert "key-pressed" in editeur._key_controller.signaux


def test_multiline_cell_renderer_focus_leave(monkeypatch):
    """Perte de focus hors popover : l'édition est validée (« edited »)."""
    monkeypatch.setattr(gcm, "set_widget_font", lambda *a: None)
    rendu = widgets.MultilineCellRenderer()
    editeur = rendu.do_start_editing(
        None, None, "5", None, SimpleNamespace(width=1, height=1), None
    )
    editeur.set_text("nouveau")
    emis = []
    monkeypatch.setattr(rendu, "emit", lambda signal, *a: emis.append((signal, a)), raising=False)
    rendu._on_editor_focus_leave(editeur)
    assert emis == [("edited", ("5", "nouveau"))]
    assert editeur._done is True
    # Une seconde perte de focus ne revalide pas (déjà fait).
    emis.clear()
    rendu._on_editor_focus_leave(editeur)
    assert emis == []


def test_multiline_cell_renderer_focus_leave_menu_ouvert(monkeypatch):
    """Popover ouvert : la perte de focus ne valide pas l'édition."""
    monkeypatch.setattr(gcm, "set_widget_font", lambda *a: None)
    rendu = widgets.MultilineCellRenderer()
    editeur = rendu.do_start_editing(
        None, None, "5", None, SimpleNamespace(width=1, height=1), None
    )
    emis = []
    monkeypatch.setattr(rendu, "emit", lambda signal, *a: emis.append(signal), raising=False)
    rendu._in_editor_menu = True
    rendu._on_editor_focus_leave(editeur)
    assert emis == []


def test_multiline_cell_renderer_menu_editeur(monkeypatch):
    """Clic droit dans l'éditeur : popover maîtrisé ouvert, items protocole ajoutés."""
    monkeypatch.setattr(gcm, "set_widget_font", lambda *a: None)
    rendu = widgets.MultilineCellRenderer()
    editeur = rendu.do_start_editing(
        None, None, "5", None, SimpleNamespace(width=1, height=1), None
    )
    geste = _FauxGeste(cible=editeur)
    geste.bouton = 3
    rendu._on_editor_button_pressed(geste, 1, 3, 3)
    assert rendu._in_editor_menu is True
    assert editeur._menu_popover.visible
