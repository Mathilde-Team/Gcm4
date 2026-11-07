#!/usr/bin/python3
# -*- coding: UTF-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique metier pure du menu contextuel du panneau de serveurs (zero import Gtk/Gio).

Extrait de ``widgets.FolderContextMenu`` (session-32, portage GTK4-ready du
menu contextuel ``popupMenuFolder`` vers ``Gio.Menu``/``Gio.SimpleActionGroup``/
``Gtk.Popover`` — voir ``docs/gtk4-migration.md`` section 3.6-quinquies) pour
rester testable sans stub GTK, conformement a ``CONSIGNES-AGENTS-IA.md``
section 5 — meme principe deja applique a ``tab_context_menu_core.py``
(session-31, ``popupMenuTab``) et a ``_compute_zoom_size()``.

Ce module ne connait aucun widget : il calcule uniquement *quelles* entrees
statiques apparaissent dans le menu selon la cible du clic droit sur
``treeServers`` (vide, dossier/groupe, ou hote), sous forme d'une liste de
sections de noms d'action. Les items *dynamiques* injectes par plugin
(``plugin.build_folder_context_menu_items()``, section "protocol") ne sont
pas modelises ici par leur libelle/callback (ceux-ci restent fournis a
l'execution par l'appelant, comme avant ce portage) : seule leur *visibilite*
(section presente ou non selon la cible) est une fonction pure de l'etat,
comme pour les autres entrees.
"""

from loguru import logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
logger.disable(__name__)

#: Cibles possibles du clic droit sur treeServers (ancien
#: on_tvServers_button_press_event) : "empty" = clic dans le vide (aucun
#: pthinfo), "folder" = noeud dossier/groupe, "host" = noeud hote.
CLICK_TARGETS = ("empty", "folder", "host")

# Association nom d'action (Gio.SimpleActionGroup, prefixe "foldermenu.") ->
# code d'action historique attendu par Wmain.on_popupmenu
# (gnome_connection_manager.py), pour les deux seules entrees qui passaient
# deja par ce dispatcher avant ce portage ("H" = Copy Address, "D" =
# Duplicate Host) — les autres entrees statiques restent des actions
# dediees, cablees directement sur les methodes existantes de Wmain
# (on_btnConnect_clicked, on_btnAdd_clicked, etc.), comme c'etait deja le
# cas cote Gtk.MenuItem.connect() avant ce portage.
FOLDER_CONTEXT_MENU_DISPATCH_ACTIONS = (
    ("copy-address", "H"),
    ("duplicate", "D"),
)

#: Toutes les entrees statiques (hors items "protocol" injectes par plugin),
#: dans l'ordre d'affichage historique de createMenu().
_STATIC_ACTION_NAMES = (
    "connect",
    "copy-address",
    "add",
    "new-group",
    "rename-group",
    "group-color",
    "group-color-reset",
    "edit",
    "delete",
    "duplicate",
    "expand",
    "collapse",
)

#: Entrees toujours visibles, quelle que soit la cible du clic (ancien
#: comportement : jamais touchees par .show()/.hide() dans
#: on_tvServers_button_press_event).
_ALWAYS_VISIBLE = frozenset(
    {"connect", "add", "new-group", "group-color", "group-color-reset", "expand", "collapse"}
)


def build_folder_context_menu_layout(*, click_target):
    """Calcule la disposition (sections/items) du menu contextuel du panneau de serveurs.

    Reproduit a l'identique la visibilite conditionnelle de l'ancien
    ``Gtk.Menu`` (``on_tvServers_button_press_event``, voir
    ``docs/gtk4-migration.md`` section 3.6-quinquies) sous forme d'une pure
    fonction de ``click_target`` vers une liste de sections, chaque section
    etant une liste de noms d'action parmi ``_STATIC_ACTION_NAMES``.

    Args:
        click_target (str): Cible du clic droit, une valeur de
            :data:`CLICK_TARGETS` : ``"empty"`` (clic dans le vide, ancien
            ``pthinfo is None``), ``"folder"`` (noeud dossier/groupe) ou
            ``"host"`` (noeud hote).

    Returns:
        list[list[str]]: Liste de sections, chaque section etant une liste
            de noms d'action, dans l'ordre d'affichage attendu.

    Raises:
        ValueError: Si ``click_target`` n'est pas une valeur de
            :data:`CLICK_TARGETS`.
    """
    logger.debug("build_folder_context_menu_layout: entrée")
    if click_target not in CLICK_TARGETS:
        raise ValueError(
            f"click_target invalide : {click_target!r} (attendu parmi {CLICK_TARGETS})"
        )

    section1 = ["connect", "add", "new-group"]
    if click_target == "host":
        section1.insert(1, "copy-address")
    if click_target == "folder":
        section1.append("rename-group")
    section1 += ["group-color", "group-color-reset"]

    sections = [section1]

    # Section "edit" : edit/delete/duplicate ne sont jamais tous absents en
    # meme temps sauf clic dans le vide (ancien : mnuDel egalement cache
    # dans ce cas, contrairement aux deux autres branches qui l'affichent
    # inconditionnellement une fois un noeud trouve).
    section2 = []
    if click_target == "host":
        section2 += ["edit", "duplicate"]
    if click_target != "empty":
        section2.append("delete")
    if section2:
        sections.append(section2)

    sections.append(["expand", "collapse"])
    logger.debug("build_folder_context_menu_layout: {}", sections)
    return sections


def should_show_protocol_items(*, click_target):
    """Indique si la section d'items specifiques au protocole doit apparaitre.

    Les items injectes par plugin (``plugin.build_folder_context_menu_items()``,
    ex. "Move to ssh_config…" pour SSH) ne concernent que les hotes, jamais
    les dossiers/groupes ni un clic dans le vide (ancien comportement :
    ``for item in self.popupMenuFolder.protocol_extra_items: item.show()``
    uniquement dans la branche "noeud hote").

    Args:
        click_target (str): Cible du clic droit, une valeur de
            :data:`CLICK_TARGETS`.

    Returns:
        bool: ``True`` si et seulement si ``click_target == "host"``.

    Raises:
        ValueError: Si ``click_target`` n'est pas une valeur de
            :data:`CLICK_TARGETS`.
    """
    logger.debug("should_show_protocol_items: entrée")
    if click_target not in CLICK_TARGETS:
        raise ValueError(
            f"click_target invalide : {click_target!r} (attendu parmi {CLICK_TARGETS})"
        )
    logger.debug("should_show_protocol_items: sortie")
    return click_target == "host"


def flatten_action_names(sections):
    """Aplati une disposition de sections en une seule liste de noms d'action.

    Utilitaire de test/verification, identique en esprit a
    ``tab_context_menu_core.flatten_action_names()``.

    Args:
        sections (list[list[str]]): Resultat de
            :func:`build_folder_context_menu_layout`.

    Returns:
        list[str]: Noms d'action, sections concatenees dans l'ordre.
    """
    logger.debug("flatten_action_names: entrée")
    logger.debug("flatten_action_names: sortie")
    return [name for section in sections for name in section]
