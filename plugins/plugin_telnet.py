# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin de connexion Telnet.

Huitième plugin de la série. ``TelnetTab`` duplique volontairement la
construction du terminal VTE (déjà dupliquée dans ``plugin_ssh.SshTab`` et
``plugin_local.LocalTab``) plutôt que de la factoriser avec SSH — décision
explicite pour ne pas coupler ces trois protocoles entre eux dans le
nouveau chemin plugin, même s'ils partageaient cette logique dans l'ancien
``Wmain.addTab()``.

Telnet n'a aujourd'hui aucune page d'édition dédiée dans le .glade (comme
IPMI SOL) : seuls les champs communs (Host/Port/User/Password) sont
utilisés.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import GLib, Gtk, Pango, Vte
from loguru import logger
from plugin_base import ConnectionPlugin

from widgets import vte_run

__all__ = ["TelnetPlugin", "TelnetTab"]


class TelnetTab(Gtk.Box):
    """Onglet de session Telnet : terminal VTE + connexion telnet/ssh.expect.

    Symétrique de :class:`plugin_ssh.SshTab` — construction du terminal
    dans ``__init__``, connexion effective dans :meth:`connect_telnet`.
    """

    def __init__(self, host) -> None:
        """Construit le terminal VTE pour *host*.

        Args:
            host: Instance ``Host`` avec ``protocol == "telnet"``.
        """
        logger.debug("TelnetTab.__init__: entrée")
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.host = host
        self.terminal = self._build_terminal(host)
        scrollbar = Gtk.Scrollbar.new(Gtk.Orientation.VERTICAL, self.terminal.get_vadjustment())
        self.pack_start(self.terminal, True, True, 0)
        self.pack_start(scrollbar, False, False, 0)
        logger.debug("TelnetTab.__init__: fin")

    def _build_terminal(self, host) -> Vte.Terminal:
        """Construit et configure le ``Vte.Terminal`` (couleurs, police, scrollback).

        Args:
            host: Instance ``Host`` source des préférences d'affichage.

        Returns:
            Le ``Vte.Terminal`` configuré (pas encore de commande lancée).
        """
        logger.debug("TelnetTab._build_terminal: entrée")
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
        logger.debug("TelnetTab._build_terminal: sortie")
        return v

    def connect_telnet(self) -> None:
        """Construit la commande telnet et lance la connexion.

        Réplique fidèlement la branche ``else`` (non-ssh) de l'ancien
        ``Wmain.addTab()`` : ``telnet`` direct si aucun user/mot de passe
        n'est renseigné (pas d'auto-authentification possible), sinon
        ``ssh.expect`` pour saisir le mot de passe automatiquement.
        """
        logger.debug("TelnetTab.connect_telnet: entrée")
        from gnome_connection_manager import SSH_COMMAND, TEL_BIN, wMain  # noqa: PLC0415

        host = self.host
        v = self.terminal
        v.host = host

        password = host.password
        if host.user == "" or host.password == "":
            password = ""
            cmd = TEL_BIN
            args = [TEL_BIN]
        else:
            cmd = SSH_COMMAND
            args = [SSH_COMMAND, "telnet", "-l", host.user]
        if host.extra_params:
            import shlex  # noqa: PLC0415

            args += shlex.split(host.extra_params)
        args += [host.host, host.port]

        logger.debug(f"TelnetTab.connect_telnet | host={host.name} cmd={cmd} args={args}")
        v.command = (cmd, args, password)
        vte_run(v, cmd, args)

        while Gtk.events_pending():
            Gtk.main_iteration()

        if password:
            GLib.timeout_add(2000, wMain.send_data, v, password)

        if host.commands:
            basetime = 3000
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
        logger.debug("TelnetTab.connect_telnet: fin")


class TelnetPlugin(ConnectionPlugin):
    """Plugin pour le protocole ``telnet``."""

    protocol_id = "telnet"
    display_name = "Telnet"
    default_port = 23
    icon_name = "utilities-terminal-symbolic"
    ui_order = 1
    is_terminal = True

    def build_tab(self, host, get_password: Callable[[], str] | None = None) -> Gtk.Widget:
        """Construit l'onglet de session Telnet.

        Args:
            host: Instance ``Host`` avec ``protocol == "telnet"``.
            get_password: Non utilisé.

        Returns:
            Une instance de :class:`TelnetTab` (pas encore connectée —
            appeler ``.connect_telnet()`` après insertion dans le notebook).
        """
        logger.debug(f"TelnetPlugin.build_tab | host={host.name}")
        return TelnetTab(host)

    def build_edit_page(self) -> Gtk.Widget:
        """Retourne une page vide : aucun champ spécifique à Telnet aujourd'hui.

        Returns:
            Un ``Gtk.Box`` vide (les champs communs Host/Port/User/Password
            de la coquille Whost suffisent, comme pour IPMI SOL).
        """
        logger.debug("TelnetPlugin.build_edit_page: entrée")
        box = Gtk.Box()
        box.show()
        logger.debug("TelnetPlugin.build_edit_page: sortie")
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


def get_plugin() -> TelnetPlugin:
    """Point d'entree standard utilise par PluginRegistry.autoload().

    Returns:
        Une instance de TelnetPlugin, prete a etre enregistree.
    """
    return TelnetPlugin()
