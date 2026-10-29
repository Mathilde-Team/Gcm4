# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin de connexion "Local" (shell local, sans hôte distant).

Neuvième et dernier plugin de la série. ``LocalTab`` duplique volontairement
la construction du terminal VTE (partagée avec :class:`plugin_ssh.SshTab` et
:class:`plugin_telnet.TelnetTab`) — c'est le plus simple des trois : pas de
commande à construire, juste ``$SHELL``.

Local n'a aujourd'hui aucune page d'édition dédiée dans le .glade (comme
IPMI SOL et Telnet).
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gtk, Pango, Vte
from loguru import logger
from plugin_base import ConnectionPlugin

from widgets import vte_run

__all__ = ["LocalPlugin", "LocalTab"]


class LocalTab(Gtk.Box):
    """Onglet de shell local : terminal VTE + ``$SHELL``.

    Symétrique de :class:`plugin_ssh.SshTab` — construction du terminal
    dans ``__init__``, connexion (ici : simple spawn de ``$SHELL``, sans
    argument ni mot de passe) dans :meth:`connect_local`.
    """

    def __init__(self, host) -> None:
        """Construit le terminal VTE pour *host*.

        Args:
            host: Instance ``Host`` avec ``protocol == "local"`` (ou
                ``host.host`` vide).
        """
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.host = host
        self.terminal = self._build_terminal(host)
        scrollbar = Gtk.Scrollbar.new(Gtk.Orientation.VERTICAL, self.terminal.get_vadjustment())
        self.pack_start(self.terminal, True, True, 0)
        self.pack_start(scrollbar, False, False, 0)

    def _build_terminal(self, host) -> Vte.Terminal:
        """Construit et configure le ``Vte.Terminal`` (couleurs, police, scrollback).

        Args:
            host: Instance ``Host`` source des préférences d'affichage.

        Returns:
            Le ``Vte.Terminal`` configuré (pas encore de commande lancée).
        """
        from gnome_connection_manager import conf, parse_color_rgba, wMain  # noqa: PLC0415

        v = Vte.Terminal()
        v.set_word_char_exceptions(conf.WORD_SEPARATORS)
        v.set_scrollback_lines(conf.BUFFER_LINES)
        if (Vte.MAJOR_VERSION, Vte.MINOR_VERSION) >= (0, 50):
            v.set_allow_hyperlink(True)
        wMain.registerUrlRegexes(v)

        fcolor = getattr(host, "font_color", "") or conf.FONT_COLOR
        bcolor = getattr(host, "back_color", "") or conf.BACK_COLOR
        palette_components = [
            "#000000",
            "#CC0000",
            "#4E9A06",
            "#C4A000",
            "#3465A4",
            "#75507B",
            "#06989A",
            "#D3D7CF",
            "#555753",
            "#EF2929",
            "#8AE234",
            "#FCE94F",
            "#729FCF",
            "#729FCF",
            "#34E2E2",
            "#EEEEEC",
        ]
        palette = [parse_color_rgba(c) for c in palette_components]
        if fcolor and bcolor:
            v.set_colors(parse_color_rgba(fcolor), parse_color_rgba(bcolor), palette)

        if not conf.FONT:
            conf.FONT = "monospace"
        else:
            v.set_font(Pango.FontDescription(conf.FONT))

        if conf.TRANSPARENCY > 0 and getattr(wMain.window, "transparency", False):
            from gnome_connection_manager import DEFAULT_BGCOLOR  # noqa: PLC0415

            c = parse_color_rgba(bcolor if bcolor else DEFAULT_BGCOLOR)
            c.alpha = 1 - (conf.TRANSPARENCY / 100)
            v.set_color_background(c)

        v.set_backspace_binding(host.backspace_key)
        v.set_delete_binding(host.delete_key)
        return v

    def connect_local(self) -> None:
        """Lance ``$SHELL`` dans le terminal (aucune commande à construire)."""
        from gnome_connection_manager import SHELL, wMain  # noqa: PLC0415

        host = self.host
        v = self.terminal
        v.host = host

        logger.debug(f"LocalTab.connect_local | host={host.name} shell={SHELL}")
        vte_run(v, SHELL)

        while Gtk.events_pending():
            Gtk.main_iteration()

        if host.commands:
            from gi.repository import GLib  # noqa: PLC0415

            basetime = 700
            lines: list[str] = []
            for line in host.commands.splitlines():
                if line.startswith("##D=") and line[4:].isdigit():
                    if lines:
                        GLib.timeout_add(basetime, wMain.send_data, v, "\r".join(lines))
                        lines = []
                    basetime += int(line[4:])
                else:
                    lines.append(line)
            if lines:
                GLib.timeout_add(basetime, wMain.send_data, v, "\r".join(lines))
        v.queue_draw()


class LocalPlugin(ConnectionPlugin):
    """Plugin pour le protocole ``local`` (shell local, sans hôte distant)."""

    protocol_id = "local"
    display_name = "Local"
    default_port = None
    icon_name = "utilities-terminal-symbolic"
    ui_order = 8
    is_terminal = True

    def build_tab(self, host, get_password: Callable[[], str] | None = None) -> Gtk.Widget:
        """Construit l'onglet de shell local.

        Args:
            host: Instance ``Host`` avec ``protocol == "local"``.
            get_password: Non utilisé.

        Returns:
            Une instance de :class:`LocalTab` (pas encore connectée —
            appeler ``.connect_local()`` après insertion dans le notebook).
        """
        logger.debug(f"LocalPlugin.build_tab | host={host.name}")
        return LocalTab(host)

    def build_edit_page(self) -> Gtk.Widget:
        """Retourne une page vide : aucun champ spécifique à Local aujourd'hui.

        Returns:
            Un ``Gtk.Box`` vide.
        """
        box = Gtk.Box()
        box.show()
        return box

    def load_host_fields(self, host) -> None:
        """Ne fait rien : aucun champ dédié à charger.

        Args:
            host: Instance ``Host`` (non utilisée ici).
        """

    def save_host_fields(self, host) -> None:
        """Ne fait rien : aucun champ dédié à sauvegarder.

        Args:
            host: Instance ``Host`` (non utilisée ici).
        """


def get_plugin() -> LocalPlugin:
    """Point d'entree standard utilise par PluginRegistry.autoload().

    Returns:
        Une instance de LocalPlugin, prete a etre enregistree.
    """
    return LocalPlugin()
