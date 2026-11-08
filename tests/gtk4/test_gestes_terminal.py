# SPDX-License-Identifier: GPL-3.0-or-later
"""Vérification empirique des gestes dans un ``Vte.Terminal`` (#72).

Les sélections VTE des portages des sessions 39-44 étaient vérifiées en
logique pure. Ces tests injectent de vrais clics (XTEST) dans un terminal
posé dans un onglet et observent le comportement réel :

- double-clic : mot seul ; triple-clic : ligne entière (sélection VTE
  native, lue dans le presse-papiers primaire) ;
- clic simple : le focus suit le clic ;
- clic milieu sur l'étiquette : fermeture confirmée par défaut, directe
  quand l'option est désactivée.

Fichier à part de ``test_gestures_reels.py`` : VTE GTK4 (Vte-3.91)
n'existe pas sur Rocky 9 (#223), le job Rocky ignore ce fichier — les
gestes y sont couverts par le job GTK4 (ubuntu:26.04, VTE installé).
Ailleurs, le saut reste possible sans VTE (``-rs`` en donne la raison).
"""

from __future__ import annotations

import shutil
import time

import pytest
from test_gestures_reels import (
    _attendre_disparition,
    _attendre_presentation,
    _bouton,
    _clic_sur,
    _presse_papiers_primaire,
)

XDOTOOL = shutil.which("xdotool")

pytestmark = pytest.mark.skipif(XDOTOOL is None, reason="xdotool absent : clics XTEST impossibles")


@pytest.fixture(autouse=True)
def _refermer_dialogues_oublies(pomper):
    """Referme les fenêtres laissées ouvertes par les tests précédents.

    Voir ``test_gestures_reels`` : sur les anciennes libadwaita, les
    fenêtres de premier niveau restées vivantes gardent leur grab modal
    et dévient silencieusement l'entrée XTEST.
    """
    from gi.repository import Gtk

    for fenetre in reversed(list(Gtk.Window.list_toplevels())):
        fenetre.destroy()
        pomper(2)
    yield


def _vte_disponible() -> bool:
    """VTE est-il introspectable ? Rocky 9 n'offre pas la typelib Vte."""
    try:
        from gi.repository import Vte  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


#: Tests exigeant un terminal : sautés sans VTE (Rocky 9, #72).
VTE = pytest.mark.skipif(not _vte_disponible(), reason="Vte absent : terminal indisponible")


def _terminal_en_onglet(fenetre, pomper):
    """Onglet « Manuel » contenant un ``Vte.Terminal`` nu, comme gcm4 le crée."""
    from gi.repository import Vte

    terminal = Vte.Terminal()
    fenetre.ajouter_onglet("Manuel", terminal)
    for _essai in range(50):  # la page prend sa taille définitive tard
        pomper(2)
        if terminal.get_height() > 100:
            break
        time.sleep(0.05)
    pomper(5)
    return terminal


@VTE
def test_selection_mot_et_ligne_du_terminal(app_fixture, pomper):
    """Double-clic : mot seul ; triple-clic : ligne entière (sélection VTE native)."""
    app, _dossier = app_fixture
    fenetre = app.get_active_window()
    terminal = _terminal_en_onglet(fenetre, pomper)
    terminal.feed(b"alpha beta\ngamma delta\n")
    pomper()
    cw, ch = terminal.get_char_width(), terminal.get_char_height()
    assert cw > 0 and ch > 0

    # clic simple de prise de focus, sur une zone sans texte : les
    # pressions suivantes, à une autre position, repartent d'un clic
    # simple (le déplacement remet le compteur de clics à zéro) ; assez
    # loin du séparateur pour échapper au geste de glissement du panneau
    _clic_sur(terminal, 60, terminal.get_height() - 10, 1, pomper)
    assert terminal.has_focus()

    # triple-clic sur la ligne « gamma delta » (ligne 1) : ligne entière
    _clic_sur(terminal, 4 * cw, 1.5 * ch, 1, pomper, repetitions=3)
    # La marge gauche du terminal fait partie de la sélection de ligne
    # (cellules de remplissage), d où le nettoyage des espaces de bord.
    assert _presse_papiers_primaire().strip() == "gamma delta"

    # double-clic sur « beta » (colonnes 6-9 de la ligne 0) : mot seul
    _clic_sur(terminal, 6.5 * cw, ch / 2, 1, pomper, repetitions=2)
    assert _presse_papiers_primaire() == "beta"


@VTE
def test_focus_apres_clic_simple(app_fixture, pomper):
    """Clic simple : le focus suit le clic (le contournement GTK3 est mort)."""
    app, _dossier = app_fixture
    fenetre = app.get_active_window()
    terminal = _terminal_en_onglet(fenetre, pomper)
    # le focus part de la recherche, pas du terminal
    _clic_sur(fenetre.arbre.recherche, 30, 10, 1, pomper)
    assert not terminal.has_focus()
    # clic au milieu du terminal : le bord gauche est trop proche du
    # séparateur, que le geste de glissement du ``Gtk.Paned`` revendique
    # jusque GTK 4.12 (la pression y est consommée, sans focus)
    _clic_sur(terminal, terminal.get_width() // 2, terminal.get_height() // 2, 1, pomper)
    assert terminal.has_focus()


@VTE
def test_clic_milieu_ferme_l_onglet(app_fixture, pomper):
    """Clic milieu : confirmation par défaut (Échap annule), directe sans l'option."""
    from gcm4.core.i18n import _

    app, _dossier = app_fixture
    fenetre = app.get_active_window()
    terminal = _terminal_en_onglet(fenetre, pomper)
    etiquettes = fenetre.onglets.get_n_pages()
    assert etiquettes >= 2  # « Home » + le terminal
    etiquette = fenetre.onglets.get_tab_label(terminal)
    assert etiquette is not None

    # Clic sur la zone de texte de l'étiquette : le bouton « fermer » est à
    # l'extrémité droite, et un clic-milieu dessus déclencherait la
    # confirmation « fermer l'onglet » (bouton ordinaire), pas celle du
    # clic-milieu.
    _clic_sur(etiquette, etiquette.get_width() // 4, etiquette.get_height() // 2, 2, pomper)
    # comportement par défaut : confirmation demandée, onglet encore là
    assert terminal in fenetre.confirmations_onglets
    dialogue = fenetre.confirmations_onglets[terminal]
    annuler = _bouton(dialogue, _("Cancel"))
    assert annuler is not None
    _attendre_presentation(dialogue, pomper)
    _clic_sur(annuler, annuler.get_width() // 2, annuler.get_height() // 2, 1, pomper)
    assert fenetre.onglets.get_n_pages() == etiquettes
    # le rappel « annuler » a tourné, l'animation de fermeture est finie
    assert terminal not in fenetre.confirmations_onglets
    _attendre_disparition(dialogue, pomper)

    # option désactivée : fermeture directe
    fenetre.config.options.confirm_close_tab_middle = False
    _clic_sur(etiquette, etiquette.get_width() // 4, etiquette.get_height() // 2, 2, pomper)
    assert fenetre.onglets.get_n_pages() == etiquettes - 1
