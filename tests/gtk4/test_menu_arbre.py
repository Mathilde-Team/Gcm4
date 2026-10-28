# SPDX-License-Identifier: GPL-3.0-or-later
"""Menu contextuel, actions et activation de l'arbre sous Xvfb (#142)."""

from __future__ import annotations

import configparser

from gi.repository import Gtk

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import ConfigError, load_config


def _relire(dossier):
    return load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))


def _noeud(arbre, chemin):
    return arbre.ligne(chemin).get_item().noeud


def _libelles(menu):
    modele = menu.popover.get_menu_model()
    resultat = []
    for i in range(modele.get_n_items()):
        section = modele.get_item_link(i, "section")
        for j in range(section.get_n_items()):
            resultat.append(section.get_item_attribute_value(j, "action", None).get_string())
    return resultat


class _Geste:
    """GestureClick minimal pour appeler le gestionnaire directement."""

    def __init__(self):
        self.etat = None

    def set_state(self, etat):
        self.etat = etat


def test_clic_droit_sur_un_groupe_et_dans_le_vide(app_fixture, pomper):
    """Clic droit sur LAB : ligne sélectionnée, menu du groupe ; dans le vide : menu racine."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    menu = arbre.menu
    enfant = arbre.liste.get_first_child()
    while not (
        isinstance(enfant.get_first_child(), Gtk.TreeExpander)
        and enfant.get_first_child().get_list_row() is arbre.ligne("LAB")
    ):
        enfant = enfant.get_next_sibling()
    ok, bornes = enfant.compute_bounds(arbre.liste)
    assert ok
    geste = _Geste()
    menu._on_clic_droit(geste, 1, bornes.get_x() + 30, bornes.get_y() + bornes.get_height() / 2)
    pomper()
    assert geste.etat == Gtk.EventSequenceState.CLAIMED
    assert arbre.chemin_selectionne() == "LAB" and menu.cible.chemin == "LAB"
    assert _libelles(menu) == [
        "arbre.add",
        "arbre.new-group",
        "arbre.rename-group",
        "arbre.group-color",
        "arbre.group-color-reset",
        "arbre.delete",
        "arbre.expand",
        "arbre.collapse",
    ]
    assert menu.popover.get_visible()
    menu.popover.popdown()
    menu._on_clic_droit(_Geste(), 1, 5, arbre.liste.get_height() - 2)
    pomper()
    assert menu.cible is None and _libelles(menu) == [
        "arbre.add",
        "arbre.new-group",
        "arbre.expand",
        "arbre.collapse",
    ]
    menu.popover.popdown()


def test_touche_menu_sur_la_selection(app_fixture, pomper):
    """Touche Menu / Maj+F10 : menu de l'hôte sélectionné, sans entrée Connecter (zéro plugin)."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    arbre.selectionner("LAB/web")
    arbre.menu._menu_clavier()
    pomper()
    assert arbre.menu.cible.chemin == "LAB/web"
    actions = _libelles(arbre.menu)
    assert actions[:3] == ["arbre.copy-address", "arbre.add", "arbre.new-group"]
    assert "arbre.edit" in actions and "arbre.connect" not in actions
    arbre.menu.popover.popdown()


def test_nouveau_groupe_valide_et_refuse(app_fixture, pomper):
    """Nouveau groupe dans LAB : « a/b » ou « bases » refusés (bouton inactif) ; « neuf » créé et écrit."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    arbre.menu.cible = _noeud(arbre, "LAB/web")
    arbre.menu.executer("new-group")
    dialogue = arbre.menu.dialogue
    entree = dialogue.get_extra_child()
    for refuse in ("", "a/b", "bases"):
        entree.set_text(refuse)
        assert not dialogue.get_response_enabled("valider"), refuse
    entree.set_text("neuf")
    assert dialogue.get_response_enabled("valider")
    dialogue.emit("response", "valider")
    pomper()
    assert "LAB/neuf" in _relire(dossier).store.groups
    assert arbre.chemin_selectionne() == "LAB/neuf"


def test_renommer_groupe_contenu_suit(app_fixture, pomper):
    """F2 sur LAB/bases → « sql » : db suit, écrit ; annuler ne change rien."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    arbre.selectionner("LAB/bases")
    arbre.menu._clavier("rename-group")
    dialogue = arbre.menu.dialogue
    assert dialogue.get_extra_child().get_text() == "bases"
    assert not dialogue.get_response_enabled("valider")  # nom inchangé
    dialogue.get_extra_child().set_text("sql")
    dialogue.emit("response", "valider")
    pomper()
    relu = _relire(dossier)
    assert relu.store.find("LAB/sql/db") is not None and "LAB/bases" not in relu.store.groups
    avant = (dossier / "gcm4.conf").read_bytes()
    arbre.menu.cible = _noeud(arbre, "LAB/sql")
    arbre.menu.executer("rename-group")
    arbre.menu.dialogue.get_extra_child().set_text("autre")
    arbre.menu.dialogue.emit("response", "annuler")
    pomper()
    assert (dossier / "gcm4.conf").read_bytes() == avant


def test_supprimer_groupe_avec_decompte(app_fixture, pomper):
    """Supprimer LAB : le dialogue annonce 2 hôtes et 1 sous-groupe ; confirmé, tout part et c'est écrit."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    arbre.menu.cible = _noeud(arbre, "LAB")
    arbre.menu.executer("delete")
    dialogue = arbre.menu.dialogue
    assert "2" in dialogue.get_body() and "1" in dialogue.get_body()
    dialogue.emit("response", "annuler")
    pomper()
    assert "LAB" in arbre.store.groups
    arbre.menu.executer("delete")
    arbre.menu.dialogue.emit("response", "supprimer")
    pomper()
    relu = _relire(dossier)
    assert "LAB" not in relu.store.groups and relu.store.find("LAB/web") is None
    assert relu.store.find("PROD/eu/api") is not None
    assert (0, "LAB") not in arbre.lignes_visibles()


def test_supprimer_hote_par_la_touche_suppr(app_fixture, pomper):
    """Suppr sur web : confirmation, puis web retiré du fichier."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    arbre.selectionner("LAB/web")
    arbre.menu._clavier("delete")
    assert "web" in arbre.menu.dialogue.get_heading()
    arbre.menu.dialogue.emit("response", "supprimer")
    pomper()
    assert _relire(dossier).store.find("LAB/web") is None


def test_dupliquer_et_copier_l_adresse(app_fixture, pomper):
    """Dupliquer web → dialogue « web (2) », enregistré et sélectionné ; copier l'adresse → presse-papiers et toast."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    messages = []
    arbre.notifier = messages.append
    arbre.menu.cible = _noeud(arbre, "LAB/web")
    arbre.menu.executer("duplicate")
    pomper()
    dialogue = arbre.menu.dialogue_hote
    assert dialogue.nom.get_text() == "web (2)"
    assert dialogue.enregistrer()
    pomper()
    assert _relire(dossier).store.find("LAB/web (2)") is not None
    assert arbre.chemin_selectionne() == "LAB/web (2)"
    arbre.menu.cible = _noeud(arbre, "LAB/web")
    arbre.menu.executer("copy-address")
    pomper()
    contenu = arbre.get_clipboard().get_content()
    assert contenu is not None and messages


def test_couleur_appliquee_puis_reinitialisee(app_fixture, pomper):
    """Couleur de PROD → feuille CSS et fichier ; réinitialisée → héritage, clé retirée."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    assert arbre.menu.appliquer_couleur("PROD", "#00ff00")
    assert ".gcm-couleur-00ff00" in app.css_couleurs.to_string()
    cp = configparser.RawConfigParser()
    cp.read(dossier / "gcm4.conf", encoding="utf-8")
    assert "PROD=#00ff00" in cp.get("window", "group-colors")
    arbre.menu.cible = _noeud(arbre, "PROD")
    arbre.menu.executer("group-color-reset")
    pomper()
    assert _relire(dossier).store.groups["PROD"].color == ""


def test_dialogue_de_couleur_s_ouvre(app_fixture, pomper):
    """Choisir une couleur… ouvre le dialogue GTK sans erreur (le choix est testé ci-dessus)."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    arbre.menu.cible = _noeud(arbre, "LAB")
    arbre.menu.executer("group-color")
    pomper()


def test_echec_d_ecriture_annule(app_fixture, pomper):
    """Enregistrer lève ConfigError : nouveau groupe absent, arbre inchangé, toast."""
    app, dossier = app_fixture
    arbre = app.get_active_window().arbre
    messages = []
    arbre.notifier = messages.append
    lignes = arbre.lignes_visibles()

    def echoue():
        raise ConfigError("disque plein")

    arbre.enregistrer = echoue
    assert not arbre.menu.modifier(lambda: arbre.store.remove_group("LAB", avec_contenu=True))
    assert "LAB" in arbre.store.groups and arbre.store.find("LAB/bases/db") is not None
    assert arbre.lignes_visibles() == lignes
    assert messages and "disque plein" in messages[0]


def test_tout_deplier_replier(app_fixture, pomper):
    """Tout déplier : PROD/eu/api visible ; tout replier : seules les racines."""
    app, _ = app_fixture
    arbre = app.get_active_window().arbre
    arbre.menu.executer("expand")
    pomper()
    assert (2, "PROD/eu/api") in arbre.lignes_visibles()
    arbre.menu.executer("collapse")
    pomper()
    assert [p for p, _c in arbre.lignes_visibles()] == [0, 0, 0]


def test_activation_groupe_et_hote(app_fixture, pomper):
    """Entrée / double-clic : groupe replié ou déplié ; hôte → win.connect-host, message zéro plugin."""
    app, _ = app_fixture
    fenetre = app.get_active_window()
    arbre = fenetre.arbre
    messages = []
    fenetre.notifier = messages.append
    position = [c for _p, c in arbre.lignes_visibles()].index("LAB")
    arbre.menu._on_activate(arbre.liste, position)
    pomper()
    assert arbre.store.groups["LAB"].collapsed
    arbre.menu._on_activate(arbre.liste, position)
    pomper()
    position = [c for _p, c in arbre.lignes_visibles()].index("LAB/web")
    arbre.menu._on_activate(arbre.liste, position)
    pomper()
    assert messages and "proto-b" in messages[0]
