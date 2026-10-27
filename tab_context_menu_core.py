#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""Logique metier pure du menu contextuel des onglets (zero import Gtk/Gio/Vte).

Extrait de ``widgets.TabContextMenu`` (session-31, portage GTK4-ready du
menu contextuel des onglets vers ``Gio.Menu``/``Gio.SimpleActionGroup``/
``Gtk.Popover`` — voir ``docs/gtk4-migration.md`` section 3.6-ter) pour
rester testable sans stub GTK, conformement a ``CONSIGNES-AGENTS-IA.md``
section 5 ("tester la logique extractible separement plutot que renoncer
aux tests") — meme principe deja applique a ``_compute_zoom_size()`` dans
``gnome_connection_manager.py`` (zoom terminal, #79).

Ce module ne connait aucun widget : il calcule uniquement *quelles* entrees
apparaissent dans le menu selon l'etat de l'onglet (onglet actif ou non,
notebook a plusieurs volets ou non), sous la forme d'une liste de sections
de noms d'action. ``widgets.TabContextMenu.rebuild()`` consomme ce resultat
pour construire le ``Gio.Menu`` reel et y attacher les libelles traduits.
"""

from loguru import logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
logger.disable(__name__)

# Association nom d'action (Gio.SimpleActionGroup, prefixe "tabmenu.") ->
# code d'action historique attendu par Wmain.on_popupmenu
# (gnome_connection_manager.py) — ces codes existaient deja du temps du
# Gtk.Menu classique (menuItem.connect("activate", self.on_popupmenu, code))
# et sont conserves a l'identique pour ne pas dupliquer la logique metier
# de ce gestionnaire.
TAB_CONTEXT_MENU_ACTIONS = (
    ("rename", "R"),
    ("reset", "RS"),
    ("clear", "RC"),
    ("reopen", "RO"),
    ("clone", "CC"),
    ("split-h", "SPH"),
    ("split-v", "SPV"),
    ("unsplit", "USP"),
)

#: Noms d'action valides pour build_tab_context_menu_layout(), "log" inclus
#: (action a etat gere separement de TAB_CONTEXT_MENU_ACTIONS car elle n'a
#: pas de code d'action "on/off" unique — voir widgets._LogActionShim).
_KNOWN_ACTION_NAMES = frozenset(name for name, _code in TAB_CONTEXT_MENU_ACTIONS) | {"log"}


def build_tab_context_menu_layout(*, is_active, multi_pane):
    """Calcule la disposition (sections/items) du menu contextuel d'un onglet.

    Reproduit a l'identique la visibilite conditionnelle de l'ancien
    ``Gtk.Menu`` (``mnuReopen``, ``mnuSplitH``/``mnuSplitV`` — voir
    ``docs/gtk4-migration.md`` section 3.6-ter, point 2) sous forme d'une
    pure fonction de ``(is_active, multi_pane)`` vers une liste de
    sections, chaque section etant une liste de noms d'action.

    Args:
        is_active (bool): Etat "actif" de l'onglet
            (``NotebookTabLabel.is_active``). Quand ``False``, ``"reopen"``
            est inclus dans la premiere section (ancien
            ``mnuReopen.show()``) ; quand ``True``, il est absent (ancien
            ``mnuReopen.hide()``).
        multi_pane (bool): ``True`` si le notebook contient plusieurs
            onglets (``nb.get_n_pages() > 1`` calcule par l'appelant) —
            conditionne la presence d'une section ``["split-h",
            "split-v"]`` (ancien ``mnuSplitH``/``mnuSplitV`` show/hide).

    Returns:
        list[list[str]]: Liste de sections, chaque section etant une liste
            de noms d'action (parmi ceux de ``TAB_CONTEXT_MENU_ACTIONS`` et
            ``"log"``), dans l'ordre d'affichage attendu.
    """
    logger.debug("build_tab_context_menu_layout: entrée")
    section1 = ["rename", "reset", "clear"]
    if not is_active:
        section1.append("reopen")
    section1.append("clone")

    sections = [section1, ["log"]]

    if multi_pane:
        sections.append(["split-h", "split-v"])

    sections.append(["unsplit"])
    logger.debug("build_tab_context_menu_layout: {}", sections)
    return sections


def flatten_action_names(sections):
    """Aplati une disposition de sections en une seule liste de noms d'action.

    Utilitaire de test/verification : permet de verifier facilement la
    presence/absence d'une action sans reproduire la structure en sections
    dans chaque test.

    Args:
        sections (list[list[str]]): Resultat de
            :func:`build_tab_context_menu_layout`.

    Returns:
        list[str]: Noms d'action, sections concatenees dans l'ordre.
    """
    logger.debug("flatten_action_names: entrée")
    logger.debug("flatten_action_names: sortie")
    return [name for section in sections for name in section]
