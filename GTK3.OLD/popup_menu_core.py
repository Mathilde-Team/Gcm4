#!/usr/bin/python3
# -*- coding: UTF-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique metier pure du menu contextuel du terminal (zero import Gtk/Gio/Vte).

Extrait de ``widgets.PopupMenu`` (session-33, portage GTK4-ready du menu
contextuel ``popupMenu`` vers ``Gio.Menu``/``Gio.SimpleActionGroup``/
``Gtk.Popover`` — voir ``docs/gtk4-migration.md`` section 3.6-sexies) pour
rester testable sans stub GTK, conformement a ``CONSIGNES-AGENTS-IA.md``
section 5 — meme principe que ``tab_context_menu_core.py`` (session-31,
``popupMenuTab``) et ``folder_context_menu_core.py`` (session-32,
``popupMenuFolder``). Troisieme et dernier des quatre menus contextuels
applicatifs identifies par l'audit session-29 a etre porte.

A la difference de ces deux modules, popupMenu n'a pas de visibilite
conditionnelle d'item : ses deux sections statiques
(:data:`POPUP_MENU_SECTIONS`) sont constantes, quel que soit le contexte du
clic droit. Ce qui varie a l'ouverture est la SENSIBILITE de quatre actions
(ancien ``.set_sensitive()`` sur ``mnuCopy``/``mnuSplitH``/``mnuSplitV``/
``mnuUnsplit``, voir :func:`compute_enabled_actions`) et l'etat de la case a
cocher "Enable logging" (``mnuLog``, geree separement comme "log" dans
``tab_context_menu_core.py`` — voir ``widgets._LogActionShim``, reutilise
tel quel par ``widgets.PopupMenu``).

Le sous-menu dynamique "Custom commands" (ancien ``populateCommandsMenu()``)
est le seul mecanisme parmi les quatre menus contextuels audites a etre un
veritable sous-menu imbrique (``Gtk.Menu`` distinct attache via
``set_submenu()``) plutot qu'une liste d'items a plat ou une simple section
— sa disposition n'est donc pas modelisee dans :data:`POPUP_MENU_SECTIONS`
(voir ``widgets.PopupMenu.__init__``/``populate_commands()``). Seule la
partie de sa logique qui reste pure (quelles entrees de ``shortcuts`` y
figurent, comment formater leur libelle) est extraite ici — voir
:func:`select_command_shortcuts` et :func:`format_command_label`.
"""

from loguru import logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
logger.disable(__name__)

# Association nom d'action (Gio.SimpleActionGroup, prefixe "popupmenu.") ->
# code d'action historique attendu par Wmain.on_popupmenu
# (gnome_connection_manager.py) — ces codes existaient deja du temps du
# Gtk.Menu classique (menuItem.connect("activate", self.on_popupmenu, code))
# et sont conserves a l'identique. Les suffixes "2" (RS2/RC2/CC2) etaient
# deja presents dans le code original pour distinguer ces items de leurs
# equivalents sans suffixe de popupMenuTab (codes "RS"/"RC"/"CC", meme
# gestionnaire mais widget cible different — voir
# tab_context_menu_core.TAB_CONTEXT_MENU_ACTIONS et
# gnome_connection_manager.py::on_popupmenu). "log" est geree separement
# (action a etat, absente de ce tuple) — meme principe que "log" hors de
# TAB_CONTEXT_MENU_ACTIONS.
POPUP_MENU_ACTIONS = (
    ("copy", "C"),
    ("paste", "V"),
    ("copy-paste", "CV"),
    ("select-all", "A"),
    ("copy-all", "CA"),
    ("save-buffer", "S"),
    ("split-h", "SPH"),
    ("split-v", "SPV"),
    ("unsplit", "USP"),
    ("reset", "RS2"),
    ("clear", "RC2"),
    ("clone", "CC2"),
    ("close", "X"),
)

#: Disposition (sections/items) du menu, CONSTANTE — a la difference de
#: ``tab_context_menu_core.build_tab_context_menu_layout()``/
#: ``folder_context_menu_core.build_folder_context_menu_layout()``, qui
#: varient selon l'etat de l'onglet/la cible du clic, popupMenu affiche
#: toujours les memes items. Reproduit a l'identique l'ordre et le
#: regroupement en 2 sections de l'ancien ``Gtk.Menu`` (2 separateurs au
#: total avec la 3eme section non modelisee ici, voir le docstring de
#: module — createMenu() / ``docs/gtk4-migration.md`` section 3.6-ter).
POPUP_MENU_SECTIONS = (
    (
        "copy",
        "paste",
        "copy-paste",
        "select-all",
        "copy-all",
        "save-buffer",
        "split-h",
        "split-v",
        "unsplit",
    ),
    ("reset", "clear", "clone", "log", "close"),
)

#: Noms d'action dont la SENSIBILITE (pas la visibilite) varie a l'ouverture
#: du menu, calculee par :func:`compute_enabled_actions` a partir de l'etat
#: du terminal cible (ancien ``mnuCopy``/``mnuSplitH``/``mnuSplitV``/
#: ``mnuUnsplit.set_sensitive()``, voir
#: ``gnome_connection_manager.py::on_terminal_click``).
VARIABLE_SENSITIVITY_ACTIONS = ("copy", "split-h", "split-v", "unsplit")


def compute_enabled_actions(*, has_selection, multi_pane, has_split_notebook):
    """Calcule la sensibilite des quatre actions dont l'etat varie a l'ouverture.

    Reproduit a l'identique les quatre ``.set_sensitive()`` de l'ancien
    ``Gtk.Menu`` (``on_terminal_click``, voir ``docs/gtk4-migration.md``
    section 3.6-sexies) sous forme d'une fonction pure d'etat vers un
    ``dict`` nom d'action -> sensibilite. Les actions non listees ici (dont
    ``"log"``, dont l'etat COCHE plutot que la sensibilite varie — voir le
    parametre separe ``log_active`` de ``widgets.PopupMenu.rebuild()``)
    restent toujours actives, comme avant ce portage.

    Args:
        has_selection (bool): Le terminal cible a-t-il une selection en
            cours (ancien ``widget.get_has_selection()``) — conditionne
            ``"copy"``.
        multi_pane (bool): Le notebook contient-il plusieurs onglets
            (ancien ``nb.get_n_pages() > 1``) — conditionne ``"split-h"`` et
            ``"split-v"``.
        has_split_notebook (bool): Un notebook scinde existe-t-il (ancien
            ``self.find_notebook(self.hpMain, self.nbConsole) is not
            None``) — conditionne ``"unsplit"``.

    Returns:
        dict[str, bool]: Sensibilite a appliquer a chacune des quatre
            actions de :data:`VARIABLE_SENSITIVITY_ACTIONS`.
    """
    logger.debug("compute_enabled_actions: entrée")
    logger.debug("compute_enabled_actions: sortie")
    return {
        "copy": bool(has_selection),
        "split-h": bool(multi_pane),
        "split-v": bool(multi_pane),
        "unsplit": bool(has_split_notebook),
    }


def select_command_shortcuts(shortcuts):
    """Filtre les entrees de ``shortcuts`` eligibles au sous-menu "Custom commands".

    Reproduit a l'identique le filtre de l'ancien ``populateCommandsMenu()``
    (``type(shortcuts[x]) != list``) : seules les entrees a valeur simple
    (une commande unique) apparaissent dans ce menu ; les entrees a valeur
    liste (sequences/groupes de commandes, gerees ailleurs dans
    l'application) en sont exclues. L'ordre d'insertion de ``shortcuts`` est
    preserve (``dict`` ordonne depuis Python 3.7, deja le comportement de
    l'ancienne boucle ``for x in shortcuts:``).

    Args:
        shortcuts (dict[str, str | list]): Raccourcis configures (section
            ``[shortcuts]`` de ``gcm.ini``, ancien global ``shortcuts`` de
            ``gnome_connection_manager.py``).

    Returns:
        list[tuple[str, str]]: Paires ``(cle_raccourci, commande)``, dans
            l'ordre de ``shortcuts``, pour les seules entrees a valeur
            simple.
    """
    logger.debug("select_command_shortcuts: entrée")
    logger.debug("select_command_shortcuts: sortie")
    return [(key, value) for key, value in shortcuts.items() if not isinstance(value, list)]


def format_command_label(shortcut, command, max_command_length=30):
    """Formate le libelle d'une entree du sous-menu "Custom commands".

    Reproduit le texte de l'ancien ``createMenuItem(x, shortcuts[x][0:30])``
    (``"[%s] %s" % (shortcut, label)``) — SANS la mise en forme Pango
    (couleur bleue et taille reduite du raccourci) que portait
    ``Gtk.MenuItem.get_child().set_attributes()`` : ``Gio.MenuItem`` n'a
    qu'un libelle texte brut, pas d'attributs Pango par item. Difference de
    rendu assumee et documentee — voir ``docs/gtk4-migration.md`` section
    3.6-sexies (meme discipline que la case "Enable logging" en
    ``Gio.SimpleAction`` a etat plutot que ``Gtk.CheckMenuItem``,
    session-31).

    Args:
        shortcut (str): Cle du raccourci (ancien parametre ``shortcut`` de
            ``createMenuItem``).
        command (str): Commande complete associee — seuls les
            ``max_command_length`` premiers caracteres apparaissent dans le
            libelle (la commande complete reste envoyee au terminal, non
            tronquee — voir le code d'action ``"CP"`` de
            ``Wmain.on_popupmenu``).
        max_command_length (int): Nombre de caracteres de ``command``
            affiches dans le libelle (defaut 30, ancien
            ``shortcuts[x][0:30]`` en dur).

    Returns:
        str: Libelle ``"[shortcut] commande tronquee"``.
    """
    logger.debug("format_command_label: entrée")
    logger.debug("format_command_label: sortie")
    return f"[{shortcut}] {command[:max_command_length]}"


def flatten_action_names(sections):
    """Aplati une disposition de sections en une seule liste de noms d'action.

    Utilitaire de test/verification, identique en esprit a
    ``tab_context_menu_core.flatten_action_names()``/
    ``folder_context_menu_core.flatten_action_names()``. Utilise ici sur
    :data:`POPUP_MENU_SECTIONS` (disposition constante) plutot que sur le
    retour d'une fonction de disposition — popupMenu n'en a pas, voir le
    docstring de module.

    Args:
        sections (tuple[tuple[str, ...], ...]): Disposition en sections,
            typiquement :data:`POPUP_MENU_SECTIONS`.

    Returns:
        list[str]: Noms d'action, sections concatenees dans l'ordre.
    """
    logger.debug("flatten_action_names: entrée")
    logger.debug("flatten_action_names: sortie")
    return [name for section in sections for name in section]
