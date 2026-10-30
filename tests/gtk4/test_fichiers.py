# SPDX-License-Identifier: GPL-3.0-or-later
"""Choix asynchrone d'un fichier ou dossier (``src/gcm4/ui/fichiers.py``, #153).

Le vrai ``Gtk.FileDialog`` (GTK ≥ 4.10) est exercé sous Xvfb : affiché, il
n'est jamais manipulé — le rappel n'est donc pas déclenché, aucun test ne
dépend d'une boucle imbriquée. Les rappels ``fin`` et ``reponse``, dus à
l'utilisateur, sont rejoués par des faux sélecteurs : un ``Gtk.FileDialog``
factice pour la branche GTK ≥ 4.10, puis le repli ``Gtk.FileChooserNative``
(GTK < 4.10, Debian 12) où le module ``Gtk`` de ``fichiers`` est remplacé
par une vue sans ``FileDialog``.
"""

from __future__ import annotations

from gi.repository import Gio, GLib, Gtk

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.ui import fichiers


class _FauxFileDialog:
    """``Gtk.FileDialog`` factice : le rappel ``fin`` est rejoué à la demande.

    ``open``/``save``/``select_folder`` ne font que retenir le rappel ; la
    méthode :meth:`repondre` le déclenche comme le portail le ferait. Les
    ``*_finish`` renvoient ``fichier``, ou lèvent ``erreur`` (annulation,
    portail indisponible).
    """

    dernier: _FauxFileDialog | None = None

    def __init__(self, title: str = "", modal: bool = False) -> None:
        self.titre = title
        self.modal = modal
        self.filtres: object | None = None
        self.nom_initial: str | None = None
        self.fichier: str | None = None
        self.erreur: GLib.Error | None = None
        self.fin: object | None = None
        _FauxFileDialog.dernier = self

    def set_filters(self, liste: object) -> None:
        self.filtres = liste

    def set_initial_name(self, nom: str) -> None:
        self.nom_initial = nom

    def open(self, parent: object, annulable: object, fin: object) -> None:
        self.fin = fin

    def save(self, parent: object, annulable: object, fin: object) -> None:
        self.fin = fin

    def select_folder(self, parent: object, annulable: object, fin: object) -> None:
        self.fin = fin

    def repondre(self) -> None:
        """Déclenche le rappel ``fin`` retenu, comme le portail répondrait."""
        self.fin(self, None)  # type: ignore[operator]

    def _resultat(self) -> Gio.File | None:
        if self.erreur is not None:
            raise self.erreur
        return Gio.File.new_for_path(self.fichier) if self.fichier else None

    def open_finish(self, resultat: object) -> Gio.File | None:
        return self._resultat()

    def save_finish(self, resultat: object) -> Gio.File | None:
        return self._resultat()

    def select_folder_finish(self, resultat: object) -> Gio.File | None:
        return self._resultat()


class _FauxFileChooserNative:
    """``Gtk.FileChooserNative`` factice : réponse rejouable à la demande."""

    def __init__(self) -> None:
        self.fichier: str | None = None
        self.detruit = False
        self.nom_courant: str | None = None
        self.affiche = False
        self.modal: bool | None = None
        self._rappel: object | None = None

    @classmethod
    def new(
        cls,
        titre: str,
        parent: object,
        action: object,
        accepter: object,
        annuler: object,
    ) -> _FauxFileChooserNative:
        return cls()

    def set_modal(self, modal: bool) -> None:
        self.modal = modal

    def add_filter(self, filtre: object) -> None:
        pass

    def set_current_name(self, nom: str) -> None:
        self.nom_courant = nom

    def connect(self, signal: str, rappel: object) -> None:
        self._rappel = rappel

    def show(self) -> None:
        self.affiche = True

    def get_file(self) -> Gio.File | None:
        return Gio.File.new_for_path(self.fichier) if self.fichier else None

    def destroy(self) -> None:
        self.detruit = True

    def repondre(self, code: int) -> None:
        """Émet la réponse enregistrée, comme le vrai signal ``response``."""
        self._rappel(self, code)  # type: ignore[operator]


class _VueGtk:
    """Vue de ``Gtk`` pour ``choisir`` : sans FileDialog, avec le faux native."""

    FileFilter = Gtk.FileFilter
    FileChooserAction = Gtk.FileChooserAction
    ResponseType = Gtk.ResponseType
    FileChooserNative = _FauxFileChooserNative


def _parent() -> Gtk.Window:
    return Gtk.Window()


# --- Filtres et chemins ---


def test_filtres_json_csv():
    """Deux filtres : JSON puis CSV, avec leurs motifs."""
    filtres = fichiers._filtres()
    assert [f.get_name() for f in filtres] == ["JSON", "CSV"]


def test_chemin_sans_fichier():
    """Aucun fichier sélectionné : le rappel reçoit ``None``."""
    assert fichiers._chemin(None) is None


def test_chemin_fichier_local():
    """Un fichier local : son chemin, tel quel."""
    assert fichiers._chemin(Gio.File.new_for_path("/tmp/exemple.json")) == "/tmp/exemple.json"


# --- Vrai sélecteur : Gtk.FileDialog (GTK ≥ 4.10) ou FileChooserNative ---


def test_choisir_ouvrir_vrai_dialogue(pomper):
    """Mode ouvrir : vrai sélecteur modal, filtres JSON et CSV.

    ``Gtk.FileDialog`` (GTK ≥ 4.10) quand il existe, sinon le vrai
    ``Gtk.FileChooserNative`` (Debian 12 : GTK 4.8) — jamais de saut, le
    job Debian 12 les refuse.
    """
    recus: list[str | None] = []
    dialogue = fichiers.choisir(_parent(), fichiers.OUVRIR, "Ouvrir", recus.append)
    if hasattr(Gtk, "FileDialog"):
        assert isinstance(dialogue, Gtk.FileDialog)
        assert dialogue.get_title() == "Ouvrir"
        assert dialogue.get_modal()
        assert dialogue.get_filters().get_n_items() == 2
    else:  # Debian 12 : GTK 4.8, repli natif
        assert isinstance(dialogue, Gtk.FileChooserNative)
        assert dialogue.get_filters().get_n_items() == 2
    assert recus == []  # aucune boucle imbriquée : rien avant validation
    pomper()


def test_choisir_enregistrer_nom_propose(pomper):
    """Mode enregistrer : nom de fichier proposé, filtres posés."""
    recus: list[str | None] = []
    dialogue = fichiers.choisir(
        _parent(), fichiers.ENREGISTRER, "Enregistrer", recus.append, "gcm4-hosts.json"
    )
    if hasattr(Gtk, "FileDialog"):
        assert isinstance(dialogue, Gtk.FileDialog)
        assert dialogue.get_initial_name() == "gcm4-hosts.json"
    else:  # Debian 12 : GTK 4.8, repli natif
        assert isinstance(dialogue, Gtk.FileChooserNative)
        assert dialogue.get_current_name() == "gcm4-hosts.json"
    assert dialogue.get_filters().get_n_items() == 2
    assert recus == []
    pomper()


def test_choisir_dossier_sans_filtres(pomper):
    """Mode dossier : aucun filtre."""
    recus: list[str | None] = []
    dialogue = fichiers.choisir(_parent(), fichiers.DOSSIER, "Dossier", recus.append)
    if hasattr(Gtk, "FileDialog"):
        assert isinstance(dialogue, Gtk.FileDialog)
        assert dialogue.get_filters() is None
    else:  # Debian 12 : GTK 4.8, repli natif
        assert isinstance(dialogue, Gtk.FileChooserNative)
        assert dialogue.get_filters().get_n_items() == 0
    assert recus == []
    pomper()


# --- Rappels fin : Gtk.FileDialog factice ---


def test_fin_ouvrir(monkeypatch):
    """Le choix d'un fichier à ouvrir remonte son chemin au rappel."""
    _brancher_faux_dialog(monkeypatch)
    recus: list[str | None] = []
    fichiers.choisir(_parent(), fichiers.OUVRIR, "Ouvrir", recus.append)
    faux = _FauxFileDialog.dernier
    assert faux is not None
    faux.fichier = "/tmp/ouvert.json"
    faux.repondre()
    assert recus == ["/tmp/ouvert.json"]


def test_fin_enregistrer(monkeypatch):
    """Le choix d'un fichier à enregistrer remonte son chemin."""
    _brancher_faux_dialog(monkeypatch)
    recus: list[str | None] = []
    fichiers.choisir(_parent(), fichiers.ENREGISTRER, "Enregistrer", recus.append)
    faux = _FauxFileDialog.dernier
    assert faux is not None
    faux.fichier = "/tmp/enregistre.json"
    faux.repondre()
    assert recus == ["/tmp/enregistre.json"]


def test_fin_dossier(monkeypatch):
    """Le choix d'un dossier remonte son chemin."""
    _brancher_faux_dialog(monkeypatch)
    recus: list[str | None] = []
    fichiers.choisir(_parent(), fichiers.DOSSIER, "Dossier", recus.append)
    faux = _FauxFileDialog.dernier
    assert faux is not None
    faux.fichier = "/tmp/dossier"
    faux.repondre()
    assert recus == ["/tmp/dossier"]


def test_fin_annule(monkeypatch):
    """Annulation (GLib.Error) : le rappel reçoit ``None``."""
    _brancher_faux_dialog(monkeypatch)
    recus: list[str | None] = []
    fichiers.choisir(_parent(), fichiers.OUVRIR, "Ouvrir", recus.append)
    faux = _FauxFileDialog.dernier
    assert faux is not None
    faux.fichier = None
    faux.erreur = GLib.Error()
    faux.repondre()
    assert recus == [None]


def _brancher_faux_dialog(monkeypatch) -> None:
    """``fichiers.Gtk`` voit un FileDialog factice ; le filtre reste réel."""
    vue = _VueGtk()
    vue.FileDialog = _FauxFileDialog
    monkeypatch.setattr(fichiers, "Gtk", vue)


# --- Repli Gtk.FileChooserNative (GTK < 4.10) ---


def test_repli_native_choix(monkeypatch):
    """Vue sans FileDialog : native, réponse ACCEPT remonte le chemin."""
    monkeypatch.setattr(fichiers, "Gtk", _VueGtk())
    recus: list[str | None] = []
    natif = fichiers.choisir(_parent(), fichiers.OUVRIR, "Ouvrir", recus.append, "propose.txt")
    assert isinstance(natif, _FauxFileChooserNative)
    assert natif.nom_courant == "propose.txt"
    assert natif.affiche
    assert natif.modal
    natif.fichier = "/tmp/choisi.txt"
    natif.repondre(Gtk.ResponseType.ACCEPT)
    assert recus == ["/tmp/choisi.txt"]
    assert natif.detruit


def test_repli_native_annule(monkeypatch):
    """Réponse autre qu'ACCEPT : ``None``, le native est détruit ensuite."""
    monkeypatch.setattr(fichiers, "Gtk", _VueGtk())
    recus: list[str | None] = []
    natif = fichiers.choisir(_parent(), fichiers.DOSSIER, "Dossier", recus.append)
    natif.fichier = "/tmp/choisi.txt"
    natif.repondre(Gtk.ResponseType.CANCEL)
    assert recus == [None]
    assert natif.detruit
