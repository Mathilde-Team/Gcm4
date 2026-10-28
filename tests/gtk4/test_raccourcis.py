# SPDX-License-Identifier: GPL-3.0-or-later
"""Page Raccourcis modifiable (#247) : saisie, conflits, désactivation, réinitialisation."""

from __future__ import annotations

import configparser

from gi.repository import Gdk, Gtk

import gcm4.ui  # noqa: F401  (require_version avant gi.repository)
from gcm4.core import menu
from gcm4.core.config import save_config
from gcm4.ui import raccourcis as ui
from gcm4.ui.main_window import MainWindow
from gcm4.ui.preferences import PreferencesWindow

CTRL = Gdk.ModifierType.CONTROL_MASK
SHIFT = Gdk.ModifierType.SHIFT_MASK
AUCUN = Gdk.ModifierType(0)


def _principale(app) -> MainWindow:
    return next(f for f in app.get_windows() if isinstance(f, MainWindow))


def _ouvrir(app, pomper) -> PreferencesWindow:
    app.activate_action("preferences", None)
    pomper()
    return app.preferences


def _ini(dossier) -> configparser.RawConfigParser:
    cp = configparser.RawConfigParser(interpolation=None)
    cp.read(dossier / "gcm4.conf", encoding="utf-8")
    return cp


def _ecrire_section(dossier, **valeurs) -> None:
    cp = _ini(dossier)
    if not cp.has_section("shortcuts"):
        cp.add_section("shortcuts")
    for cle, valeur in valeurs.items():
        cp.set("shortcuts", cle, valeur)
    with open(dossier / "gcm4.conf", "w", encoding="utf-8") as fichier:
        cp.write(fichier)


def _saisir(d: PreferencesWindow, action: str, pomper) -> ui.SaisieRaccourci:
    ligne = next(
        lg for lg in d.lignes_raccourcis if d.touches_raccourcis.get(action) in _suffixes(lg)
    )
    ligne.emit("activated")
    pomper()
    assert d.saisie is not None and d.saisie.action == action
    return d.saisie


def _suffixes(ligne) -> list[Gtk.Widget]:
    trouves, pile = [], [ligne]
    while pile:
        w = pile.pop()
        trouves.append(w)
        enfant = w.get_first_child()
        while enfant is not None:
            pile.append(enfant)
            enfant = enfant.get_next_sibling()
    return trouves


def _repondre(saisie: ui.SaisieRaccourci, reponse: str, pomper) -> None:
    saisie.dialogue.emit("response", reponse)
    saisie.dialogue.close()  # un vrai clic ferme le dialogue ; emit() seul ne le fait pas
    pomper()


def _clavier(saisie: ui.SaisieRaccourci) -> Gtk.EventControllerKey:
    controleurs = saisie.dialogue.observe_controllers()
    return next(
        c
        for c in (controleurs.get_item(i) for i in range(controleurs.get_n_items()))
        if isinstance(c, Gtk.EventControllerKey)
    )


def test_defauts_et_page(app_fixture, pomper):
    """Sans [shortcuts] : accélérateurs par défaut ; lignes de la fenêtre activables, arbre fixe."""
    app, _ = app_fixture
    for action, touches in menu.RACCOURCIS.items():
        assert tuple(app.get_accels_for_action(action)) == touches
    d = _ouvrir(app, pomper)
    assert set(d.touches_raccourcis) == set(menu.RACCOURCIS)
    activables = [lg for lg in d.lignes_raccourcis if lg.get_activatable()]
    assert len(activables) == len(menu.RACCOURCIS) < len(d.lignes_raccourcis)


def test_valeurs_gtk3_appliquees(app_fixture, pomper):
    """Fichier écrit par GTK3 (défauts, terminal) et par gcm4 : accélérateurs posés."""
    app, dossier = app_fixture
    _ecrire_section(
        dossier,
        console_previous="CTRL+SHIFT+TAB",
        console_next="CTRL+SHIFT+ISO_LEFT_TAB",
        gcm4_quit="",
        gcm4_search="CTRL+SHIFT+F",
        gcm4_toggle_panel="F99",  # illisible : valeur par défaut
        copy="CTRL+SHIFT+C",
    )
    app.appliquer_raccourcis()  # comme au démarrage (_on_startup)
    assert app.get_accels_for_action("win.previous-tab") == list(
        menu.RACCOURCIS["win.previous-tab"]
    )
    assert app.get_accels_for_action("win.next-tab") == ["<Shift><Control>Tab"]
    assert app.get_accels_for_action("app.quit") == []
    assert app.get_accels_for_action("win.search") == ["<Shift><Control>f"]
    assert app.get_accels_for_action("win.toggle-panel") == ["F9"]
    d = _ouvrir(app, pomper)
    assert d.touches_raccourcis["app.quit"].get_label() == "Disabled"


def test_saisie_enregistre_et_applique(app_fixture, pomper):
    """Ctrl+J pour la recherche : fichier, accélérateur, libellé ; autres clés conservées."""
    app, dossier = app_fixture
    _ecrire_section(dossier, copy="CTRL+SHIFT+C")
    d = _ouvrir(app, pomper)
    saisie = _saisir(d, "win.search", pomper)
    assert app.get_accels_for_action("app.quit") == []  # suspendus pendant la saisie
    assert "reset" not in _reponses(saisie)
    assert not _clavier(saisie).emit("key-pressed", Gdk.KEY_Control_L, 0, CTRL)
    assert _clavier(saisie).emit("key-pressed", Gdk.KEY_j, 0, CTRL)
    assert saisie.pret and saisie.dialogue.get_response_enabled("set")
    _repondre(saisie, "set", pomper)
    cp = _ini(dossier)
    assert cp.get("shortcuts", "gcm4_search") == "CTRL+J"
    assert cp.get("shortcuts", "copy") == "CTRL+SHIFT+C"
    assert app.get_accels_for_action("win.search") == ["<Control>j"]
    assert app.get_accels_for_action("app.quit") == ["<Control>q"]  # rétablis
    assert "J" in d.touches_raccourcis["win.search"].get_label()


def _reponses(saisie: ui.SaisieRaccourci) -> set[str]:
    return {r for r in ("cancel", "reset", "set") if saisie.dialogue.has_response(r)}


def test_conflit_refuse(app_fixture, pomper):
    """Combinaison d'une autre action ou de l'arbre : signalée, « Set » inactif, rien d'écrit."""
    app, dossier = app_fixture
    avant = (dossier / "gcm4.conf").read_text(encoding="utf-8")
    d = _ouvrir(app, pomper)
    saisie = _saisir(d, "win.search", pomper)
    assert saisie.proposer(Gdk.KEY_q, CTRL)
    assert not saisie.dialogue.get_response_enabled("set")
    assert saisie.message.get_visible() and "Quit" in saisie.message.get_label()
    assert saisie.proposer(Gdk.KEY_F2, AUCUN)  # raccourci de l'arbre
    assert saisie.message.get_visible() and not saisie.pret
    assert saisie.proposer(Gdk.KEY_a, AUCUN)  # lettre seule : invalide
    assert "Ctrl" in saisie.message.get_label() and not saisie.pret
    assert not saisie.proposer(Gdk.KEY_Escape, AUCUN)  # laissée au dialogue
    _repondre(saisie, "set", pomper)  # Entrée sur « Set » inactif : ignorée
    assert (dossier / "gcm4.conf").read_text(encoding="utf-8") == avant
    assert app.get_accels_for_action("win.search") == ["<Control>f"]
    assert app.get_accels_for_action("app.quit") == ["<Control>q"]


def test_propre_raccourci_et_shift_tab(app_fixture, pomper):
    """Ctrl+Maj+Tab pour l'onglet suivant : pris par l'onglet précédent ; Ctrl+Tab reste libre."""
    app, _ = app_fixture
    d = _ouvrir(app, pomper)
    saisie = _saisir(d, "win.next-tab", pomper)
    assert saisie.proposer(Gdk.KEY_ISO_Left_Tab, CTRL | SHIFT)
    assert not saisie.pret and "Previous tab" in saisie.message.get_label()
    assert saisie.proposer(Gdk.KEY_Tab, CTRL)  # déjà le sien : libre
    assert saisie.pret and not saisie.message.get_visible()
    _repondre(saisie, "cancel", pomper)
    assert app.get_accels_for_action("win.next-tab") == list(menu.RACCOURCIS["win.next-tab"])


def test_desactiver_puis_reinitialiser(app_fixture, pomper):
    """Retour arrière : désactivé (valeur vide) ; « Reset to default » retire la clé."""
    app, dossier = app_fixture
    d = _ouvrir(app, pomper)
    saisie = _saisir(d, "app.quit", pomper)
    assert saisie.proposer(Gdk.KEY_BackSpace, AUCUN) and saisie.pret
    assert saisie.touches.get_label() == "Disabled"
    _repondre(saisie, "set", pomper)
    assert _ini(dossier).get("shortcuts", "gcm4_quit") == ""
    assert app.get_accels_for_action("app.quit") == []
    assert d.touches_raccourcis["app.quit"].get_label() == "Disabled"
    # Ctrl+Q libéré : utilisable par une autre action.
    saisie = _saisir(d, "win.search", pomper)
    assert saisie.proposer(Gdk.KEY_q, CTRL) and saisie.pret
    _repondre(saisie, "cancel", pomper)
    saisie = _saisir(d, "app.quit", pomper)
    assert "reset" in _reponses(saisie)
    _repondre(saisie, "reset", pomper)
    assert not _ini(dossier).has_option("shortcuts", "gcm4_quit")
    assert app.get_accels_for_action("app.quit") == ["<Control>q"]
    assert d.touches_raccourcis["app.quit"].get_label() != "Disabled"


def test_une_seule_reponse(app_fixture, pomper):
    """Deuxième réponse du même dialogue : ignorée (pas de seconde écriture)."""
    app, dossier = app_fixture
    d = _ouvrir(app, pomper)
    saisie = _saisir(d, "win.search", pomper)
    saisie.proposer(Gdk.KEY_j, CTRL)
    saisie.dialogue.emit("response", "set")
    saisie.proposer(Gdk.KEY_k, CTRL)
    saisie.dialogue.emit("response", "set")
    saisie.dialogue.close()
    pomper()
    assert _ini(dossier).get("shortcuts", "gcm4_search") == "CTRL+J"


def test_persiste_apres_save_config(app_fixture, pomper):
    """Modifié en session puis enregistrement du modèle (glisser-déposer…) : valeur conservée."""
    app, dossier = app_fixture
    fenetre = _principale(app)
    d = _ouvrir(app, pomper)
    saisie = _saisir(d, "win.toggle-panel", pomper)
    assert saisie.proposer(Gdk.KEY_F8, AUCUN) and saisie.pret  # touche de fonction seule
    _repondre(saisie, "set", pomper)
    save_config(app.config_file, fenetre.config)
    assert _ini(dossier).get("shortcuts", "gcm4_toggle_panel") == "F8"
    assert app.raccourcis_effectifs()["win.toggle-panel"] == ("F8",)


def test_fichier_corrompu_signale(app_fixture, pomper):
    """Fichier devenu illisible : enregistrement refusé, signalé, fichier intact."""
    app, dossier = app_fixture
    d = _ouvrir(app, pomper)
    saisie = _saisir(d, "win.search", pomper)
    saisie.proposer(Gdk.KEY_j, CTRL)
    (dossier / "gcm4.conf").write_text("pas un INI", encoding="utf-8")
    _repondre(saisie, "set", pomper)
    assert d.erreur and (dossier / "gcm4.conf").read_text(encoding="utf-8") == "pas un INI"
    assert app.get_accels_for_action("app.quit") == ["<Control>q"]  # rétablis malgré l'échec


def test_resoudre_et_noms():
    """Noms GTK3 en majuscules -> noms GDK ; XF86 et ISO_Left_Tab normalisés."""
    assert ui.resoudre("PAGE_DOWN") == "Page_Down"
    assert ui.resoudre("A") == "a" and ui.resoudre("KP_ENTER") == "KP_Enter"
    assert ui.resoudre("ISO_LEFT_TAB") == "Tab"
    assert ui.resoudre("XF86AUDIOPLAY") == "XF86AudioPlay" == ui.resoudre("AUDIOPLAY")
    assert ui.resoudre("PAS_UNE_TOUCHE") is None
    assert ui.nom_touche(0) is None
    verdict, combinaison = ui.depuis_evenement(Gdk.KEY_A, CTRL | SHIFT)
    assert verdict == ui.VALIDE and combinaison is not None and combinaison.gtk3 == "CTRL+SHIFT+A"
    assert ui.depuis_evenement(Gdk.KEY_Tab, AUCUN)[0] == ui.IGNOREE
    assert ui.depuis_evenement(Gdk.KEY_ISO_Left_Tab, SHIFT)[0] == ui.IGNOREE
    assert ui.depuis_evenement(0x1008FF14, AUCUN)[0] == ui.VALIDE  # touche multimédia seule
    assert ui.libelle(None) == "Disabled"


def test_entree_valide_une_saisie_prete(app_fixture, pomper):
    """Entrée : sans saisie prête, rien ; avec, comme « Set » (le bouton n'a pas le focus)."""
    app, dossier = app_fixture
    d = _ouvrir(app, pomper)
    saisie = _saisir(d, "win.search", pomper)
    clavier = _clavier(saisie)
    assert clavier.emit("key-pressed", Gdk.KEY_Return, 0, AUCUN)
    pomper()
    assert not saisie.traite and not _ini(dossier).has_section("shortcuts")
    clavier.emit("key-pressed", Gdk.KEY_j, 0, CTRL)
    assert clavier.emit("key-pressed", Gdk.KEY_KP_Enter, 0, AUCUN)
    pomper()
    assert saisie.traite and not saisie.dialogue.get_mapped()
    assert _ini(dossier).get("shortcuts", "gcm4_search") == "CTRL+J"
    assert app.get_accels_for_action("app.quit") == ["<Control>q"]
    # Ctrl+Entrée n'est pas « valider » : c'est une combinaison comme une autre.
    saisie = _saisir(d, "win.search", pomper)
    assert saisie.proposer(Gdk.KEY_Return, CTRL) and saisie.pret
    assert "Ctrl" in saisie.touches.get_label() and not saisie.traite
    _repondre(saisie, "cancel", pomper)
