# SPDX-License-Identifier: GPL-3.0-or-later
"""Dialogues d'import Proxmox et libvirt (#293, palier 9).

``ProxmoxImportDialog`` et ``LibvirtImportDialog`` (GTK3) partagent le même
parcours : cibles lues dans dconf ou saisies à la main, scan dans un fil,
prévisualisation (nouveaux / déjà importés), import des lignes cochées. Les
onglets de préférences et les greffons ``BatchPlugin`` complètent chaque
module. Ici, ``Gtk`` et ``GLib`` sont remplacés dans chaque module par des
doublures qui gardent l'état (textes, cases, lignes) ; ``GLib.idle_add`` et
le fil de scan s'exécutent sur-le-champ, la collecte SSH est remplacée :
aucun réseau, aucun GTK, aucun fil réel (les doublures ``gi`` de
``tests/test_gcm.py`` sont réutilisées pour l'import des modules).
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
)

import test_gcm  # noqa: E402, F401  (pose les doublures gi/Gtk avant l'import des plugins)

# isort: split
import plugin_import_libvirt as lvt  # noqa: E402
import plugin_import_proxmox as pxm  # noqa: E402

import utils  # noqa: E402


class Widget:
    """Doublure générique d'un widget GTK : toute méthode est sans effet."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialise la doublure."""
        self.signaux: dict[str, object] = {}

    def connect(self, signal: str, rappel, *args) -> None:
        """Simule ``connect`` en gardant le rappel."""
        self.signaux[signal] = rappel

    def __getattr__(self, nom: str):
        """Méthode GTK sans effet (set_margin_start, pack_start…)."""
        if nom.startswith("__"):
            raise AttributeError(nom)
        return lambda *a, **k: MagicMock()


class Entree(Widget):
    """Doublure de ``Gtk.Entry``."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialise la doublure."""
        super().__init__()
        self.texte = ""

    def set_text(self, texte: str) -> None:
        """Simule ``set_text``."""
        self.texte = texte

    def get_text(self) -> str:
        """Simule ``get_text``."""
        return self.texte


class Case(Widget):
    """Doublure de ``Gtk.CheckButton``."""

    def __init__(self, *args, label: str = "", **kwargs) -> None:
        """Initialise la doublure."""
        super().__init__()
        self.actif = False

    def set_active(self, valeur: bool) -> None:
        """Simule ``set_active``."""
        self.actif = valeur

    def get_active(self) -> bool:
        """Simule ``get_active``."""
        return self.actif


class Bouton(Widget):
    """Doublure de ``Gtk.Button`` : libellé et sensibilité."""

    def __init__(self, *args, label: str = "", **kwargs) -> None:
        """Initialise la doublure."""
        super().__init__()
        self.libelle = label
        self.sensible = True

    def set_label(self, libelle: str) -> None:
        """Simule ``set_label``."""
        self.libelle = libelle

    def set_sensitive(self, valeur: bool) -> None:
        """Simule ``set_sensitive``."""
        self.sensible = valeur


class Etiquette(Widget):
    """Doublure de ``Gtk.Label`` : garde le balisage."""

    def __init__(self, *args, label: str = "", **kwargs) -> None:
        """Initialise la doublure."""
        super().__init__()
        self.balisage = label

    def set_markup(self, balisage: str) -> None:
        """Simule ``set_markup``."""
        self.balisage = balisage


class Tampon(Widget):
    """Doublure de ``Gtk.TextBuffer`` : concatène le texte inséré."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialise la doublure."""
        super().__init__()
        self.texte = ""

    def insert(self, _iter, texte: str) -> None:
        """Simule ``insert``."""
        self.texte += texte


class Pile(Widget):
    """Doublure de ``Gtk.Stack`` : page visible et pages titrées."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialise la doublure."""
        super().__init__()
        self.visible: str | None = None
        self.pages: list[str] = []

    def add_titled(self, _widget, nom: str, _titre: str) -> None:
        """Simule ``add_titled``."""
        self.pages.append(nom)

    def set_visible_child_name(self, nom: str) -> None:
        """Simule ``set_visible_child_name``."""
        self.visible = nom


class Progression(Widget):
    """Doublure de ``Gtk.ProgressBar``."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialise la doublure."""
        super().__init__()
        self.fraction = 0.0
        self.texte = ""

    def set_fraction(self, valeur: float) -> None:
        """Simule ``set_fraction``."""
        self.fraction = valeur

    def set_text(self, texte: str) -> None:
        """Simule ``set_text``."""
        self.texte = texte


class Dialogue(Widget):
    """Doublure de ``Gtk.Dialog`` : boutons, taille, destruction."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialise la doublure."""
        super().__init__()
        self.titre = kwargs.get("title")
        self.taille: tuple[int, int] | None = None
        self.affiche = False
        self.detruit = False

    def get_content_area(self) -> Widget:
        """Simule ``get_content_area``."""
        return Widget()

    def resize(self, largeur: int, hauteur: int) -> None:
        """Simule ``resize``."""
        self.taille = (largeur, hauteur)

    def show_all(self) -> None:
        """Simule ``show_all``."""
        self.affiche = True

    def destroy(self) -> None:
        """Simule ``destroy``."""
        self.detruit = True


class Magasin:
    """Doublure de ``Gtk.ListStore`` : lignes modifiables, chemin str ou int."""

    def __init__(self, *types_colonnes) -> None:
        """Initialise la doublure."""
        self.lignes: list[list] = []

    def append(self, ligne: list) -> None:
        """Simule ``append``."""
        self.lignes.append(list(ligne))

    def clear(self) -> None:
        """Simule ``clear``."""
        self.lignes.clear()

    def __iter__(self):
        """Parcourt les lignes."""
        return iter(self.lignes)

    def __len__(self) -> int:
        """Nombre de lignes."""
        return len(self.lignes)

    def __getitem__(self, chemin) -> list:
        """Ligne par chemin GTK (``"0"``) ou indice."""
        return self.lignes[int(chemin)]


class Carnet(Widget):
    """Doublure de ``Gtk.Notebook`` : garde les pages ajoutées."""

    def __init__(self) -> None:
        """Initialise la doublure."""
        super().__init__()
        self.pages: list[tuple] = []

    def append_page(self, page, etiquette) -> None:
        """Simule ``append_page``."""
        self.pages.append((page, etiquette))


class FauxGtk(types.SimpleNamespace):
    """Espace ``Gtk`` : widgets à état, le reste en ``MagicMock``."""

    def __getattr__(self, nom: str):
        """Enumérations et widgets sans état (Orientation, Box, Align…)."""
        if nom.startswith("__"):
            raise AttributeError(nom)
        valeur = MagicMock(name=f"Gtk.{nom}")
        setattr(self, nom, valeur)
        return valeur


def faux_gtk() -> FauxGtk:
    """Fabrique l'espace ``Gtk`` des doublures."""
    gtk = FauxGtk(
        Dialog=Dialogue,
        Entry=Entree,
        CheckButton=Case,
        Button=Bouton,
        Label=Etiquette,
        TextBuffer=Tampon,
        Stack=Pile,
        ProgressBar=Progression,
        ListStore=Magasin,
    )
    for nom in (
        "Box",
        "TreeView",
        "TreeViewColumn",
        "CellRendererToggle",
        "CellRendererText",
        "ScrolledWindow",
        "TextView",
        "StackSwitcher",
        "Frame",
        "Grid",
        "Separator",
        "Expander",
    ):
        setattr(gtk, nom, Widget)
    return gtk


class FilImmediat:
    """Doublure de ``threading.Thread`` : la cible s'exécute à ``start``."""

    def __init__(self, target, daemon: bool = False) -> None:
        """Initialise la doublure."""
        self.cible = target

    def start(self) -> None:
        """Exécute la cible sur-le-champ."""
        self.cible()


# Les deux modules partagent le parcours ; seuls changent les noms, le groupe
# par défaut, la fonction de collecte et la normalisation du port 22.
MODULES = {
    "proxmox": {
        "module": pxm,
        "dialogue": "ProxmoxImportDialog",
        "collecte": "_proxmox_fetch_hosts",
        "groupe": "PROXMOX",
        "cle": "proxmox-import",
        "conf": "PROXMOX_DEFAULT_USER",
        "onglet": "ProxmoxPrefsTab",
        "greffon": "ProxmoxImportBatchPlugin",
        "port_22": "qemu+ssh://root@10.0.0.1:22/system",
    },
    "libvirt": {
        "module": lvt,
        "dialogue": "LibvirtImportDialog",
        "collecte": "_libvirt_fetch_hosts",
        "groupe": "LIBVIRT",
        "cle": "libvirt-import",
        "conf": "LIBVIRT_DEFAULT_USER",
        "onglet": "LibvirtPrefsTab",
        "greffon": "LibvirtImportBatchPlugin",
        "port_22": "qemu+ssh://root@10.0.0.1/system",
    },
}


@pytest.fixture(params=sorted(MODULES))
def cas(request, monkeypatch):
    """Module d'import avec Gtk, GLib et threading remplacés."""
    info = dict(MODULES[request.param])
    module = info["module"]
    monkeypatch.setattr(module, "Gtk", faux_gtk())
    glib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a))
    monkeypatch.setattr(module, "GLib", glib)
    monkeypatch.setattr(module.threading, "Thread", FilImmediat)
    monkeypatch.setattr(module, "groups", {})
    monkeypatch.setattr(module.hv_common, "libvirt_get_uris_from_dconf", lambda: [])
    info["nom"] = request.param
    return info


def ouvrir(cas, uris=None, monkeypatch=None, show=True):
    """Construit le dialogue du module ; renvoie (dialogue, imports reçus)."""
    module = cas["module"]
    if uris is not None:
        monkeypatch.setattr(module.hv_common, "libvirt_get_uris_from_dconf", lambda: uris)
    recus: list[list[dict]] = []
    dialogue = getattr(module, cas["dialogue"])(None, recus.append, "admin", show=show)
    return dialogue, recus


def journal(dialogue) -> str:
    """Texte de la zone de journal du dialogue."""
    return dialogue._log_buf.texte


HOTES = [
    {
        "name": "web",
        "group": "G1",
        "protocol": "ssh",
        "host": "10.0.0.5",
        "description": "[running]",
    },
    {
        "name": "ad",
        "group": "G1",
        "protocol": "rdp",
        "host": "10.0.0.6",
        "description": "sans état",
    },
    {"name": "spice", "protocol": "spice", "host": "hv"},
]


# ── Construction et cibles ────────────────────────────────────────────────


def test_construction_affiche_le_dialogue(cas):
    """Le dialogue est construit, affiché, avec ses trois pages."""
    dialogue, _recus = ouvrir(cas)
    assert dialogue._dlg.affiche is True
    assert dialogue._stack.pages == ["config", "monitor", "results"]
    assert dialogue._stack.visible == "config"
    assert dialogue._entry_user.get_text() == "admin"
    assert dialogue.main_widget is dialogue._dlg


def test_construction_sans_affichage(cas):
    """``show=False`` (onglet épinglé) : le dialogue n'est pas affiché."""
    dialogue, _recus = ouvrir(cas, show=False)
    assert dialogue._dlg.affiche is False


def test_fermeture_par_reponse_detruit_le_dialogue(cas):
    """La réponse du dialogue (bouton Fermer) détruit la fenêtre autonome."""
    dialogue, _recus = ouvrir(cas)
    dialogue._dlg.signaux["response"](dialogue._dlg, 0)
    assert dialogue._dlg.detruit is True


def test_cibles_dconf_chargees(cas, monkeypatch):
    """Les URI SSH de dconf sont proposées, cochées."""
    uris = ["qemu+ssh://root@hv1/system", "qemu+ssh://root@hv2/system"]
    dialogue, _recus = ouvrir(cas, uris, monkeypatch)
    assert [ligne[1] for ligne in dialogue._uri_store] == uris
    assert all(ligne[0] for ligne in dialogue._uri_store)


def test_aucune_cible_dconf(cas):
    """Sans URI dans dconf : message d'aide (libvirt ajoute un exemple)."""
    dialogue, _recus = ouvrir(cas)
    assert "dconf" in journal(dialogue)
    if cas["nom"] == "libvirt":
        assert [ligne[1] for ligne in dialogue._uri_store] == [
            "qemu+ssh://root@hyperviseur/system"
        ]
    else:
        assert len(dialogue._uri_store) == 0


def test_proxmox_ignore_les_uri_non_ssh(cas, monkeypatch):
    """Proxmox ne garde que les URI ``+ssh://…/system`` de dconf."""
    uris = ["qemu:///system", "qemu+ssh://root@hv1/system", "qemu+ssh://root@hv2/session"]
    dialogue, _recus = ouvrir(cas, uris, monkeypatch)
    attendues = ["qemu+ssh://root@hv1/system"] if cas["nom"] == "proxmox" else uris
    assert [ligne[1] for ligne in dialogue._uri_store] == attendues


@pytest.mark.parametrize(
    ("saisie", "attendue"),
    [
        ("", ""),
        ("   ", ""),
        ("qemu+ssh://u@h/system", "qemu+ssh://u@h/system"),
        ("u@h", None),
        ("@h", None),
        ("h:2222", "qemu+ssh://root@h:2222/system"),
        ("h:abc", "qemu+ssh://root@h:abc{port}/system"),
        ("u@:22", ""),
    ],
)
def test_normalisation_des_cibles(cas, saisie, attendue):
    """Saisie libre → URI ``qemu+ssh://user@hôte[:port]/system``."""
    dialogue, _recus = ouvrir(cas)
    port_defaut = ":22" if cas["nom"] == "proxmox" else ""
    if saisie == "u@h":
        attendue = f"qemu+ssh://u@h{port_defaut}/system"
    elif saisie == "@h":
        attendue = f"qemu+ssh://root@h{port_defaut}/system"
    elif attendue and "{port}" in attendue:
        attendue = attendue.format(port=port_defaut)
    assert dialogue._normalize_manual_uri(saisie) == attendue


def test_normalisation_port_22(cas):
    """Port 22 explicite : gardé par Proxmox, omis par libvirt."""
    dialogue, _recus = ouvrir(cas)
    assert dialogue._normalize_manual_uri("10.0.0.1:22") == cas["port_22"]
    assert dialogue._normalize_manual_uri(None) == ""


def test_ajout_manuel(cas):
    """Ajout d'une cible saisie, refus d'un doublon et d'une saisie vide."""
    dialogue, _recus = ouvrir(cas)
    depart = len(dialogue._uri_store)
    dialogue._entry_manual_uri.set_text("u@hv9:2200")
    dialogue._on_add_manual_uri(None)
    assert dialogue._uri_store[depart] == [True, "qemu+ssh://u@hv9:2200/system"]
    assert dialogue._entry_manual_uri.get_text() == ""
    assert "qemu+ssh://u@hv9:2200/system" in journal(dialogue)

    dialogue._entry_manual_uri.set_text("u@hv9:2200")
    dialogue._on_add_manual_uri(None)
    assert len(dialogue._uri_store) == depart + 1
    assert dialogue._entry_manual_uri.get_text() == ""

    dialogue._entry_manual_uri.set_text("   ")
    dialogue._on_add_manual_uri(None)
    assert len(dialogue._uri_store) == depart + 1
    assert "nvalid" in journal(dialogue)


def test_bascule_d_une_cible(cas, monkeypatch):
    """La case d'une cible s'inverse par son chemin GTK."""
    dialogue, _recus = ouvrir(cas, ["qemu+ssh://root@hv1/system"], monkeypatch)
    dialogue._on_uri_toggled(None, "0")
    assert dialogue._uri_store[0][0] is False
    dialogue._on_uri_toggled(None, "0")
    assert dialogue._uri_store[0][0] is True


def test_progression(cas):
    """``_set_progress`` met à jour la barre (via ``GLib.idle_add``)."""
    dialogue, _recus = ouvrir(cas)
    dialogue._set_progress(0.5, "moitié")
    assert (dialogue._progress.fraction, dialogue._progress.texte) == (0.5, "moitié")


# ── Scan ──────────────────────────────────────────────────────────────────


def remplacer_collecte(cas, monkeypatch, resultat=None, erreur=None):
    """Remplace la collecte SSH ; renvoie la liste des appels reçus."""
    appels: list[tuple] = []

    def collecte(uris, user, log_fn, progress_fn, proto_filter):
        appels.append((list(uris), user, set(proto_filter)))
        log_fn("collecte simulée")
        progress_fn(0.5, "en cours")
        if erreur is not None:
            raise erreur
        return list(resultat or [])

    monkeypatch.setattr(cas["module"], cas["collecte"], collecte)
    return appels


def test_scan_complet(cas, monkeypatch):
    """Scan : cible en attente ajoutée, protocoles cochés, prévisualisation."""
    appels = remplacer_collecte(cas, monkeypatch, HOTES)
    dialogue, _recus = ouvrir(cas, ["qemu+ssh://root@hv1/system"], monkeypatch)
    dialogue._chk_vnc.set_active(True)
    dialogue._entry_user.set_text("  ")
    dialogue._entry_manual_uri.set_text("hv2")
    dialogue._on_scan_clicked(None)

    uri2 = dialogue._normalize_manual_uri("hv2")
    assert appels == [
        (["qemu+ssh://root@hv1/system", uri2], "root", {"ssh", "spice", "rdp", "vnc"})
    ]
    assert dialogue._entry_manual_uri.get_text() == ""
    assert "collecte simulée" in journal(dialogue)
    assert dialogue._stack.visible == "results"
    assert dialogue._btn_scan.sensible is True
    assert "3</b> connexion(s)" in dialogue._lbl_summary.balisage
    assert dialogue._progress.fraction == 1.0
    assert dialogue._dlg.taille == (820, 900)
    lignes = dialogue._preview_store.lignes
    assert [ligne[1] for ligne in lignes] == ["SSH", "RDP", "SPICE"]
    assert [ligne[5] for ligne in lignes] == ["running", "", ""]
    assert lignes[2][3] == cas["groupe"]


def test_scan_cible_en_attente_deja_presente(cas, monkeypatch):
    """Une cible en attente déjà listée n'est pas dupliquée."""
    appels = remplacer_collecte(cas, monkeypatch, [])
    uri = "qemu+ssh://root@hv1/system"
    dialogue, _recus = ouvrir(cas, [uri], monkeypatch)
    dialogue._entry_manual_uri.set_text(uri)
    dialogue._on_scan_clicked(None)
    assert appels[0][0] == [uri]
    assert dialogue._entry_manual_uri.get_text() == ""


def test_scan_sans_cible_cochee(cas, monkeypatch):
    """Aucune cible cochée : pas de collecte, bouton réactivé."""
    appels = remplacer_collecte(cas, monkeypatch, HOTES)
    dialogue, _recus = ouvrir(cas, ["qemu+ssh://root@hv1/system"], monkeypatch)
    dialogue._on_uri_toggled(None, 0)
    dialogue._on_scan_clicked(None)
    assert appels == []
    assert dialogue._btn_scan.sensible is True
    assert "No " in journal(dialogue)


def test_scan_sans_protocole(cas, monkeypatch):
    """Aucun type de connexion coché : pas de collecte."""
    appels = remplacer_collecte(cas, monkeypatch, HOTES)
    dialogue, _recus = ouvrir(cas, ["qemu+ssh://root@hv1/system"], monkeypatch)
    for case in (dialogue._chk_ssh, dialogue._chk_spice, dialogue._chk_rdp, dialogue._chk_vnc):
        case.set_active(False)
    dialogue._on_scan_clicked(None)
    assert appels == []
    assert "No connection type selected." in journal(dialogue)
    assert dialogue._btn_scan.sensible is True


def test_scan_en_echec(cas, monkeypatch):
    """Une collecte qui lève : erreur journalisée, prévisualisation vide."""
    remplacer_collecte(cas, monkeypatch, erreur=RuntimeError("ssh coupé"))
    dialogue, _recus = ouvrir(cas, ["qemu+ssh://root@hv1/system"], monkeypatch)
    dialogue._on_scan_clicked(None)
    assert "Scan failed: ssh coupé" in journal(dialogue)
    assert dialogue._preview_store.lignes == []
    assert "0</b> connexion(s)" in dialogue._lbl_summary.balisage


def test_onglet_ne_redimensionne_pas(cas, monkeypatch):
    """Ouvert comme onglet épinglé, le dialogue n'est pas redimensionné."""
    remplacer_collecte(cas, monkeypatch, HOTES)
    dialogue, _recus = ouvrir(cas, ["qemu+ssh://root@hv1/system"], monkeypatch)
    dialogue.is_tab = True
    dialogue._on_scan_clicked(None)
    assert dialogue._dlg.taille is None


# ── Prévisualisation et import ────────────────────────────────────────────


def avec_existant(cas, monkeypatch):
    """Groupe G1 contenant déjà l'hôte ``web``."""
    monkeypatch.setattr(cas["module"], "groups", {"G1": [types.SimpleNamespace(name="web")]})


def test_existence_des_hotes(cas, monkeypatch):
    """``_host_exists`` cherche le nom dans le groupe GCM."""
    avec_existant(cas, monkeypatch)
    dialogue, _recus = ouvrir(cas)
    assert dialogue._host_exists("web", "G1") is True
    assert dialogue._host_exists("autre", "G1") is False
    assert dialogue._host_exists("web", "G2") is False


def test_previsualisation_existants_decoches(cas, monkeypatch):
    """Un hôte déjà importé est grisé, décoché et compté à part."""
    avec_existant(cas, monkeypatch)
    dialogue, _recus = ouvrir(cas)
    dialogue._show_preview(HOTES)
    web = dialogue._preview_store[0]
    assert web[0] is False and web[6] is True and web[8] == "#888888"
    assert "✓" in web[7]
    assert dialogue._preview_store[1][0] is True
    assert "2 nouvelle(s)" in dialogue._lbl_summary.balisage
    assert "1 déjà importée(s)" in dialogue._lbl_summary.balisage


def test_selection_et_import(cas, monkeypatch):
    """Bascule, tout cocher / décocher, puis import des lignes cochées."""
    avec_existant(cas, monkeypatch)
    dialogue, recus = ouvrir(cas)
    dialogue._show_preview(HOTES)

    dialogue._select_all(False)
    assert not any(ligne[0] for ligne in dialogue._preview_store)
    dialogue._on_import_clicked(None)
    assert recus == []
    assert dialogue._btn_import.sensible is True
    assert "No connection to import" in journal(dialogue)

    dialogue._select_all(True)
    dialogue._on_preview_toggled(None, "1")
    dialogue._on_import_clicked(None)
    # « web » existe et l'écrasement est désactivé ; « ad » a été décoché.
    assert recus == [[HOTES[2]]]
    assert dialogue._btn_import.libelle == "✓ Imported"
    assert "1</b> connexion(s) importée(s)" in dialogue._lbl_summary.balisage


def test_import_avec_ecrasement(cas, monkeypatch):
    """Avec l'écrasement coché, les hôtes existants sont importés aussi."""
    avec_existant(cas, monkeypatch)
    dialogue, recus = ouvrir(cas)
    dialogue._show_preview(HOTES)
    dialogue._select_all(True)
    dialogue._chk_overwrite.set_active(True)
    dialogue._on_import_clicked(None)
    assert recus == [HOTES]


# ── Onglet de préférences ─────────────────────────────────────────────────


def test_onglet_de_preferences(cas, monkeypatch):
    """L'onglet lit puis enregistre l'utilisateur SSH par défaut."""
    monkeypatch.setattr(utils.conf, cas["conf"], "ops", raising=False)
    carnet = Carnet()
    onglet = cas["module"].build_preferences_tab(carnet)
    assert isinstance(onglet, getattr(cas["module"], cas["onglet"]))
    assert len(carnet.pages) == 1
    assert onglet._entry_user.get_text() == "ops"

    onglet._entry_user.set_text("  deploy ")
    onglet.apply()
    assert getattr(utils.conf, cas["conf"]) == "deploy"

    onglet._entry_user.set_text("   ")
    onglet.apply()
    assert getattr(utils.conf, cas["conf"]) == "root"


def test_onglet_de_preferences_valeur_vide(cas, monkeypatch):
    """Une valeur vide en configuration retombe sur ``root``."""
    monkeypatch.setattr(utils.conf, cas["conf"], "", raising=False)
    onglet = cas["module"].build_preferences_tab(Carnet())
    assert onglet._entry_user.get_text() == "root"


# ── Greffon BatchPlugin ───────────────────────────────────────────────────


class Appli:
    """Doublure de ``Wmain`` vue par les greffons d'import."""

    def __init__(self, importes: int) -> None:
        """Initialise la doublure."""
        self.window = object()
        self.importes = importes
        self.recus: list[tuple] = []
        self.onglets: list[tuple] = []

    def _import_done(self, hotes, default_group):
        """Simule ``Wmain._import_done``."""
        self.recus.append((hotes, default_group))
        return self.importes

    def open_management_tab(self, cle, titre, fabrique):
        """Simule ``Wmain.open_management_tab`` : construit l'écran."""
        self.onglets.append((cle, titre, fabrique()))


def test_greffon_sans_application(cas):
    """Sans application liée, ``activate`` lève ``RuntimeError``."""
    greffon = cas["module"].get_batch_plugin()
    assert isinstance(greffon, getattr(cas["module"], cas["greffon"]))
    greffon.app = None
    with pytest.raises(RuntimeError, match="sans app liée"):
        greffon.activate()


@pytest.mark.parametrize(("importes", "message"), [(2, "2 connection(s)"), (0, "No new host")])
def test_greffon_ouvre_l_onglet_et_importe(cas, monkeypatch, importes, message):
    """``activate`` ouvre l'onglet épinglé ; l'import passe par ``_import_done``."""
    monkeypatch.setattr(utils.conf, cas["conf"], "ops", raising=False)
    messages: list[str] = []
    monkeypatch.setattr(cas["module"], "msgbox", lambda texte, parent=None: messages.append(texte))
    appli = Appli(importes)
    greffon = cas["module"].get_batch_plugin()
    greffon.app = appli
    greffon.activate()

    cle, _titre, dialogue = appli.onglets[0]
    assert cle == cas["cle"]
    assert isinstance(dialogue, getattr(cas["module"], cas["dialogue"]))
    assert dialogue._entry_user.get_text() == "ops"
    assert dialogue._dlg.affiche is False

    dialogue.on_done([])
    assert messages == ["No host imported."]
    dialogue.on_done(HOTES)
    assert appli.recus == [(HOTES, cas["groupe"])]
    assert message in messages[-1]
