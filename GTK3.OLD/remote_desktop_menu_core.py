#!/usr/bin/python3
# -*- coding: UTF-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique métier pure du menu contextuel bureau distant (zéro import Gtk/Gio).

Extrait de ``widgets.RemoteDesktopContextMenu`` (session-53, portage
GTK4-ready du menu contextuel ``build_remote_desktop_context_menu()`` vers
``Gio.Menu``/``Gio.SimpleActionGroup``/``Gtk.Popover`` — voir
``docs/gtk4-migration.md`` §3.8) pour rester testable sans stub GTK,
conformément à ``CONSIGNES-AGENTS-IA.md`` section 5 — même principe que
``tab_context_menu_core.py`` (session-31, ``popupMenuTab``),
``folder_context_menu_core.py`` (session-32, ``popupMenuFolder``) et
``popup_menu_core.py`` (session-33, ``popupMenu``). Quatrième et dernier
des quatre menus contextuels applicatifs identifiés par l'audit session-29
à être porté.

À la différence de ces trois modules, ce menu est entièrement
*paramétré par l'appelant* (les plugins RDP/VNC/SPICE via
``build_context_menu()``) : les libellés viennent des boutons existants
et les entrées supplémentaires (ex: périphériques USB pour SPICE) d'une
liste passée en argument. Ce qui reste pure et verrouillable ici :

- la *disposition* du menu : une section principale
  (Connect/Disconnect/entrées supplémentaires) suivie du sous-menu
  « Special keys » — voir :func:`build_remote_desktop_menu_layout` ;
- la sensibilité des deux actions principales, lue sur les boutons
  existants au moment de la construction — voir
  :func:`compute_remote_desktop_enabled_actions` ;
- les combinaisons de touches spéciales historiques (ancien
  ``widgets._SPECIAL_KEY_COMBOS``, déplacé ici inchangé), dont les
  libellés sont des msgids traduits par ``widgets`` au moment du
  rendu — voir :data:`REMOTE_DESKTOP_SPECIAL_KEY_COMBOS`.
"""

from loguru import logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
logger.disable(__name__)

#: Combinaisons de touches usuellement interceptées par le gestionnaire de
#: fenêtres local avant d'atteindre un widget d'affichage distant embarqué
#: (RDP/VNC/SPICE) — noms de touches Gdk (cf. gdk_keyval_from_name).
#: Déplacé inchangé depuis ``widgets._SPECIAL_KEY_COMBOS`` (sessions
#: 28-46) : libellés français littéraux, jamais traduits par ``_()``.
REMOTE_DESKTOP_SPECIAL_KEY_COMBOS = (
    ("Ctrl+Alt+Suppr", ("Control_L", "Alt_L", "Delete")),
    ("Ctrl+Échap", ("Control_L", "Escape")),
    ("Alt+Tab", ("Alt_L", "Tab")),
    ("Alt+F4", ("Alt_L", "F4")),
    ("Impr. écran", ("Print",)),
)


def build_remote_desktop_menu_layout(extra_items=None):
    """Disposition du menu bureau distant pour un appel donné.

    Reproduit la structure de l'ancien ``Gtk.Menu`` construit par
    ``widgets.build_remote_desktop_context_menu()`` (sessions 28-46) :
    une section principale Connect/Disconnect/entrées supplémentaires,
    puis (après le séparateur historique) le sous-menu « Special keys ».

    Args:
        extra_items (list[tuple] | None): Entrées supplémentaires
            (libellé, callback) passées par le plugin (ex: périphériques
            USB pour SPICE). Seul leur *nombre* influe sur la
            disposition.

    Returns:
        tuple[tuple[str, ...], tuple[str, ...]]: Noms d'action de la
        section principale (``"connect"``, ``"disconnect"``, puis
        ``"extra0"``, ``"extra1"``, ...) et noms d'action du sous-menu
        touches spéciales (``"special-key-0"`` à
        ``"special-key-4"``, un par entrée de
        :data:`REMOTE_DESKTOP_SPECIAL_KEY_COMBOS`).
    """
    logger.debug("build_remote_desktop_menu_layout: entrée")
    main_actions = ("connect", "disconnect") + tuple(
        f"extra{i}" for i in range(len(extra_items or []))
    )
    special_key_actions = tuple(
        f"special-key-{i}" for i in range(len(REMOTE_DESKTOP_SPECIAL_KEY_COMBOS))
    )
    logger.debug("build_remote_desktop_menu_layout: sortie")
    return main_actions, special_key_actions


def compute_remote_desktop_enabled_actions(connect_sensitive, disconnect_sensitive):
    """Sensibilité des actions principales à la construction du menu.

    Remplace l'ancien ``item.set_sensitive(btn.get_sensitive())`` sur les
    ``Gtk.MenuItem`` : le menu étant reconstruit à chaque clic droit par
    ``build_context_menu()`` des plugins, l'état lu sur les boutons au
    moment de la construction est le même qu'avant le portage.

    Args:
        connect_sensitive (bool): État de sensibilité du bouton Connect.
        disconnect_sensitive (bool): État de sensibilité du bouton
            Disconnect.

    Returns:
        dict[str, bool]: Sensibilité attendue de chaque action
        principale, clé = nom d'action.
    """
    logger.debug("compute_remote_desktop_enabled_actions: entrée")
    enabled = {
        "connect": bool(connect_sensitive),
        "disconnect": bool(disconnect_sensitive),
    }
    logger.debug("compute_remote_desktop_enabled_actions: sortie")
    return enabled
