# SPDX-License-Identifier: GPL-3.0-or-later
"""Sélecteur de clé SSH GTK4 (``plugins/ssh/gtk4.py``, #97).

Chargé depuis ``plugins/`` du dépôt comme en mode source. Le dossier
``ssh`` n'a pas (encore) de ``PLUGIN`` (#396) : sa découverte échoue,
mais ses outils restent importables.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from gi.repository import GLib, Gtk

from gcm4.core import registre

RACINE = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def module():
    """``gcm4_plugin_ssh.gtk4`` chargé par le registre, en mode source."""
    candidats = [c for c in registre.candidats_dossier(RACINE / "plugins") if c.nom == "ssh"]
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: None, candidats).etats}
    assert etats["ssh"].plugin is None and "PLUGIN" in str(etats["ssh"].erreur)
    yield sys.modules["gcm4_plugin_ssh.gtk4"]
    registre._oublier_paquet("gcm4_plugin_ssh")


def _generer(dossier: Path, nom: str, mode: int = 0o600) -> Path:
    """Clé ed25519 sans phrase de passe."""
    chemin = dossier / nom
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", nom, "-f", str(chemin)], check=True
    )
    chemin.chmod(mode)
    return chemin


@pytest.fixture
def ssh(tmp_path: Path) -> Path:
    """Dossier de clés : ``a`` (600), ``b`` (644, sans .pub), ``c.pub`` orpheline."""
    dossier = tmp_path / ".ssh"
    dossier.mkdir(mode=0o700)
    _generer(dossier, "a")
    _generer(dossier, "b", mode=0o644).with_name("b.pub").unlink()
    _generer(dossier, "c").unlink()
    return dossier


def _attendre(condition, delai=5.0):
    """Traite les événements GTK jusqu'à ``condition()`` ou ``delai`` secondes."""
    contexte = GLib.MainContext.default()
    fin = time.monotonic() + delai
    while not condition() and time.monotonic() < fin:
        contexte.iteration(False)
        time.sleep(0.005)
    return condition()


@pytest.fixture
def ouvrir(module, ssh):
    """Fabrique de sélecteurs affichés ; ``choix`` reçoit les ``key-selected``."""
    ouverts = []
    choix: list[str] = []

    def fabrique(**options):
        options.setdefault("dossier", ssh)
        dialogue = module.KeyPickerDialog(**options)
        dialogue.connect("key-selected", lambda _d, chemins: choix.append(chemins))
        dialogue.present()
        ouverts.append(dialogue)
        return dialogue

    fabrique.choix = choix
    yield fabrique
    for dialogue in ouverts:
        dialogue.destroy()


def test_liste_des_cles(module, ouvrir, ssh):
    """Trois lignes triées ; type, empreinte et permissions affichés."""
    dialogue = ouvrir()
    lignes = dialogue.lignes()
    assert [ligne.nom.get_label() for ligne in lignes] == ["a", "b", "c.pub"]
    assert [ligne.type.get_label() for ligne in lignes] == [
        module._(t) for t in ("private", "private", "public")
    ]
    assert lignes[0].empreinte.get_label().startswith("SHA256:")
    assert lignes[0].permissions.get_label() == "600 ✓"
    assert lignes[1].permissions.get_label() == "644 ⚠"
    assert lignes[1].nom.has_css_class("warning") and lignes[1].nom.get_tooltip_text()
    assert not lignes[0].nom.has_css_class("warning")
    assert dialogue.defilement.get_visible() and not dialogue.vide.get_visible()
    dialogue.liste.unselect_all()  # le focus de la liste peut sélectionner la 1re ligne
    assert not dialogue.bouton_utiliser.get_sensitive()
    assert not dialogue.bouton_generer.get_visible()  # pas de gestionnaire fourni
    assert dialogue.get_title() == module._("Select SSH Key") and dialogue.get_modal()


def test_dossier_vide(ouvrir, tmp_path):
    """Aucune clé : message « Generate… » à la place de la liste."""
    dialogue = ouvrir(dossier=tmp_path / "rien")
    assert dialogue.lignes() == []
    assert dialogue.vide.get_visible() and not dialogue.defilement.get_visible()


def test_parent_transitoire(ouvrir):
    """La fenêtre parente est reprise (``transient_for``)."""
    parent = Gtk.Window()
    try:
        dialogue = ouvrir(parent=parent)
        assert dialogue.get_transient_for() is parent
    finally:
        parent.destroy()


def test_utiliser_plusieurs_cles(ouvrir, ssh):
    """« Use » : chemins privés joints par ", " ; la publique orpheline perd ``.pub``."""
    dialogue = ouvrir()
    a, _b, c = dialogue.lignes()
    dialogue.liste.unselect_all()
    dialogue.liste.select_row(a)
    assert dialogue.bouton_utiliser.get_sensitive()
    dialogue.liste.select_row(c)
    dialogue.bouton_utiliser.emit("clicked")
    assert ouvrir.choix == [f"{ssh / 'a'}, {ssh / 'c'}"]
    assert _attendre(lambda: not dialogue.get_visible())


def test_utiliser_sans_selection_ne_fait_rien(ouvrir):
    """« Use » sans sélection (bouton forcé) : rien émis, sélecteur ouvert."""
    dialogue = ouvrir()
    dialogue.liste.unselect_all()
    dialogue.bouton_utiliser.set_sensitive(True)
    dialogue.bouton_utiliser.emit("clicked")
    assert ouvrir.choix == [] and dialogue.get_visible()


def test_double_clic(ouvrir, ssh):
    """Activation d'une ligne : cette seule clé, puis fermeture."""
    dialogue = ouvrir()
    ligne = dialogue.lignes()[1]
    dialogue.liste.emit("row-activated", ligne)
    assert ouvrir.choix == [str(ssh / "b")]
    assert _attendre(lambda: not dialogue.get_visible())


def test_annuler(ouvrir):
    """« Cancel » ferme sans rien émettre."""
    dialogue = ouvrir()
    dialogue.bouton_annuler.emit("clicked")
    assert ouvrir.choix == [] and _attendre(lambda: not dialogue.get_visible())


def test_echap(ouvrir):
    """Échap ferme sans rien émettre."""
    dialogue = ouvrir()
    assert dialogue._on_echap() is True
    assert ouvrir.choix == [] and _attendre(lambda: not dialogue.get_visible())


def test_generer_puis_retour_recharge(ouvrir, ssh):
    """« Generate… » : non modal pendant le gestionnaire, puis modal et liste relue au retour."""
    appels = []
    dialogue = ouvrir(gestionnaire=lambda: appels.append(1))
    assert dialogue.bouton_generer.get_visible()
    dialogue.bouton_generer.emit("clicked")
    assert appels == [1] and not dialogue.get_modal()
    _generer(ssh, "d")
    dialogue.is_active = lambda: False
    dialogue._on_actif(dialogue, None)  # fenêtre pas encore active : rien
    assert not dialogue.get_modal()
    dialogue.is_active = lambda: True
    dialogue._on_actif(dialogue, None)
    assert dialogue.get_modal()
    assert [ligne.nom.get_label() for ligne in dialogue.lignes()] == ["a", "b", "c.pub", "d"]
    dialogue._on_actif(dialogue, None)  # retour déjà traité : liste non relue
    assert len(dialogue.lignes()) == 4
