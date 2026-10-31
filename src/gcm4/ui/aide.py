# SPDX-License-Identifier: GPL-3.0-or-later
"""Menu Aide de gcm4 (#86) : guide utilisateur et fenêtre des raccourcis.

- **Guide utilisateur** (F1) : ``Documentation-fr.md`` sur GitHub, contenu de
  ``docs/guide/utilisateur.md`` (:data:`gcm4.core.menu.GUIDE_UTILISATEUR`), ouvert dans le navigateur par
  :func:`gcm4.ui.mises_a_jour.ouvrir_page` ; rien n'est dupliqué dans
  l'application.
- **Raccourcis clavier** (Ctrl+?) : les raccourcis de Paramètres > Raccourcis
  (valeurs effectives, personnalisations comprises), en deux sections
  « Fenêtre principale » et « Hôtes ». Un raccourci désactivé n'est pas listé.

Compatibilité : ``Adw.ShortcutsDialog`` (libadwaita ≥ 1.8) ; sinon
``Gtk.ShortcutsWindow`` décrit en GtkBuilder (seule construction possible
avant GTK 4.14, déprécié depuis GTK 4.18 mais encore présent).
"""

from __future__ import annotations

from collections.abc import Mapping
from xml.sax.saxutils import escape

from gi.repository import Adw, Gtk
from loguru import logger

from gcm4.core import menu
from gcm4.core import preferences as prefs_core
from gcm4.core.i18n import N_, _
from gcm4.ui import mises_a_jour

#: Titres des sections (msgids), dans l'ordre d'affichage.
SECTIONS = (
    (prefs_core.FENETRE, N_("Main window")),
    (prefs_core.ARBRE, N_("Hosts")),
)


def ouvrir_guide(fenetre: Gtk.Window | None) -> None:
    """Aide > Guide utilisateur : ouvre le guide dans le navigateur."""
    logger.debug("ouvrir_guide: entrée")
    mises_a_jour.ouvrir_page(fenetre, menu.GUIDE_UTILISATEUR)
    logger.debug("ouvrir_guide: fait")


def sections(
    effectifs: Mapping[str, tuple[str, ...]] | None = None,
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Sections de la fenêtre : (titre traduit, [(libellé traduit, accélérateurs)]).

    Args:
        effectifs (Mapping[str, tuple[str, ...]] | None): Accélérateurs posés
            (``Gcm4Application.raccourcis_effectifs``) ; ``None`` : par défaut.

    Returns:
        list[tuple[str, list[tuple[str, str]]]]: Accélérateurs séparés par une
        espace (syntaxe de ``GtkShortcutLabel``) ; sections vides omises.
    """
    logger.debug("sections: entrée")
    par_portee: dict[str, list[tuple[str, str]]] = {portee: [] for portee, _t in SECTIONS}
    for raccourci in prefs_core.raccourcis(effectifs):
        if raccourci.touches:
            par_portee[raccourci.portee].append(
                (_(raccourci.libelle), " ".join(raccourci.touches))
            )
    resultat = [(_(titre), par_portee[portee]) for portee, titre in SECTIONS if par_portee[portee]]
    logger.debug("sections: {} section(s)", len(resultat))
    return resultat


def _xml_fenetre(contenu: list[tuple[str, list[tuple[str, str]]]]) -> str:
    """Description GtkBuilder d'un ``Gtk.ShortcutsWindow`` (repli libadwaita < 1.8)."""
    logger.debug("_xml_fenetre: {} section(s)", len(contenu))
    groupes = []
    for titre, lignes in contenu:
        raccourcis = "".join(
            '<child><object class="GtkShortcutsShortcut">'
            f'<property name="title">{escape(libelle)}</property>'
            f'<property name="accelerator">{escape(touches)}</property>'
            "</object></child>"
            for libelle, touches in lignes
        )
        groupes.append(
            '<child><object class="GtkShortcutsGroup">'
            f'<property name="title">{escape(titre)}</property>{raccourcis}'
            "</object></child>"
        )
    resultat = (
        '<interface><object class="GtkShortcutsWindow" id="fenetre">'
        '<property name="modal">1</property>'
        '<child><object class="GtkShortcutsSection">'
        '<property name="section-name">gcm4</property>'
        f"{''.join(groupes)}</object></child></object></interface>"
    )
    logger.debug("_xml_fenetre: fait")
    return resultat


def fenetre_raccourcis(
    effectifs: Mapping[str, tuple[str, ...]] | None = None,
) -> Gtk.Widget:
    """``Adw.ShortcutsDialog`` (libadwaita ≥ 1.8) ou ``Gtk.ShortcutsWindow``, non présentée."""
    logger.debug("fenetre_raccourcis: entrée")
    contenu = sections(effectifs)
    if hasattr(Adw, "ShortcutsDialog"):
        dialogue = Adw.ShortcutsDialog()
        for titre, lignes in contenu:
            section = Adw.ShortcutsSection.new(titre)
            for libelle, touches in lignes:
                section.add(Adw.ShortcutsItem.new(libelle, touches))
            dialogue.add(section)
        logger.debug("fenetre_raccourcis: Adw.ShortcutsDialog")
        return dialogue
    constructeur = Gtk.Builder.new_from_string(_xml_fenetre(contenu), -1)
    fenetre = constructeur.get_object("fenetre")
    logger.debug("fenetre_raccourcis: Gtk.ShortcutsWindow")
    return fenetre


def presenter_raccourcis(
    parent: Gtk.Window | None, effectifs: Mapping[str, tuple[str, ...]] | None = None
) -> Gtk.Widget:
    """Aide > Raccourcis clavier (Ctrl+?) : construit et présente la fenêtre."""
    logger.debug("presenter_raccourcis: entrée")
    fenetre = fenetre_raccourcis(effectifs)
    if isinstance(fenetre, Gtk.Window):  # Gtk.ShortcutsWindow (libadwaita < 1.8)
        fenetre.set_transient_for(parent)
        fenetre.present()
    else:  # Adw.ShortcutsDialog
        fenetre.present(parent)
    logger.debug("presenter_raccourcis: présentée")
    return fenetre
