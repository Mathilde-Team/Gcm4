# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin de connexion SSH.

Septième plugin de la série (après Web, IPMI SOL, Serial, RDP, VNC, SPICE).

``SshTab`` (ci-dessous) est une classe autonome au même niveau que
``VncTab``/``SpiceTab``/etc. : elle construit son propre terminal VTE et sa
propre commande SSH, sans dépendre de ``Wmain.addTab()``. Cette dernière
gardait déjà exactement la même logique pour telnet/local (partagée avant
ce plugin avec ssh) — le code correspondant y est simplement dupliqué,
volontairement, plutôt que factorisé, pour ne pas risquer de casser
telnet/local en les couplant au chemin plugin.

Ce module embarque également l'éditeur ~/.ssh/config (import/migration/
édition visuelle) et la gestion des clés SSH : ce sont des fonctionnalités
propres à SSH, qui n'ont pas leur place dans le cœur de l'application.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from gettext import gettext as _

from gi.repository import GLib, GObject, Gtk, Pango, Vte
from loguru import logger
from plugin_base import ConnectionPlugin

from widgets import vte_run

__all__ = ["SshPlugin", "SshTab"]

# (clé de champ Host, libellé, tooltip) pour les 15 options SSH avancées,
# généralisées depuis l'éditeur visuel de ~/.ssh/config (cf. gridHostSsh).
_ADVANCED_FIELDS: list[tuple[str, str, str]] = [
    ("proxy_jump", "ProxyJump", _("Bastion host(s) to hop through, e.g. user@bastion")),
    (
        "proxy_command",
        "ProxyCommand",
        _("Arbitrary command to reach the target, e.g. via corkscrew or nc"),
    ),
    ("strict_host_key_checking", "StrictHostKeyChecking", _("yes / no / accept-new")),
    ("user_known_hosts_file", "UserKnownHostsFile", ""),
    ("connect_timeout", "ConnectTimeout", _("Seconds before giving up on the connection")),
    ("request_tty", "RequestTTY", _("yes / no / force / auto")),
    ("remote_command", "RemoteCommand", _("Command to run instead of a shell")),
    ("control_master", "ControlMaster", _("yes / no / ask / auto")),
    ("control_persist", "ControlPersist", _("e.g. 10m, or yes")),
    ("pubkey_authentication", "PubkeyAuthentication", _("yes / no")),
    ("password_authentication", "PasswordAuthentication", _("yes / no")),
    ("ssh_log_level", "LogLevel", _("QUIET / ERROR / INFO / VERBOSE / DEBUG…")),
    ("add_keys_to_agent", "AddKeysToAgent", _("yes / no / ask / confirm")),
    ("server_alive_count_max", "ServerAliveCountMax", ""),
    ("identities_only", "IdentitiesOnly", _("yes / no")),
]

_TUNNEL_TYPE_ITEMS = [
    ("local", _("Local (-L)")),
    ("remote", _("Remote (-R)")),
    ("dynamic", _("Dynamic / SOCKS (-D)")),
]


def _bool_from_str(value: object) -> bool:
    """Convertit une valeur de config texte ("true"/"1"/"yes") en bool.

    Args:
        value: Valeur brute (souvent une chaîne issue de gcm.conf).
    """
    return str(value).lower() in ("true", "1", "yes")


class SshTab(Gtk.Box):
    """Onglet de session SSH : terminal VTE + connexion ssh/ssh.expect.

    Symétrique de ``VncTab``/``SpiceTab``/``SerialTab`` (widgets.py) : cette
    classe construit et possède son propre ``Vte.Terminal``, indépendamment
    de ``Wmain.addTab()``. La construction du terminal (couleurs, police,
    scrollback) se fait dans ``__init__`` ; la connexion effective (calcul
    de la commande ssh, spawn, envoi du mot de passe, montage SSHFS, envoi
    des commandes personnalisées) se fait dans :meth:`connect_ssh`, appelée
    par l'appelant une fois le widget inséré dans le notebook — même
    contrat que ``VncTab.connect_vnc()``/``SerialTab.connect_serial()``.
    """

    def __init__(self, host) -> None:
        """Construit le terminal VTE pour *host*.

        Args:
            host: Instance ``Host`` avec ``protocol == "ssh"``.
        """
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.host = host
        self.terminal = self._build_terminal(host)
        scrollbar = Gtk.Scrollbar.new(Gtk.Orientation.VERTICAL, self.terminal.get_vadjustment())
        self.pack_start(self.terminal, True, True, 0)
        self.pack_start(scrollbar, False, False, 0)

    def _build_terminal(self, host) -> Vte.Terminal:
        """Construit et configure le ``Vte.Terminal`` (couleurs, police, scrollback).

        Réplique fidèlement la configuration commune de l'ancien
        ``Wmain.addTab()`` (couleurs par défaut GCM, palette 16 couleurs,
        police, transparence, bindings backspace/delete).

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

    def connect_ssh(self) -> None:
        """Construit la commande SSH et lance la connexion.

        Réplique fidèlement la branche ``host.type == "ssh"`` de l'ancien
        ``Wmain.addTab()`` : construction des arguments (utilisateur, port,
        keep-alive, tunnels, X11/agent/compression, clé privée, options SSH
        avancées, ``extra_params``), lancement via :func:`widgets.vte_run`,
        envoi différé du mot de passe si nécessaire, montage SSHFS, puis
        envoi des commandes personnalisées le cas échéant.
        """
        import shlex  # noqa: PLC0415

        from gnome_connection_manager import (  # noqa: PLC0415
            SSH_BIN,
            SSH_COMMAND,
            get_username,
            wMain,
        )

        host = self.host
        v = self.terminal
        v.host = host

        if not host.user:
            host.user = get_username()

        password = host.password
        if host.password == "":
            cmd = SSH_BIN
            args = [SSH_BIN, "-l", host.user, "-p", host.port]
        else:
            cmd = SSH_COMMAND
            args = [SSH_COMMAND, "ssh", "-l", host.user, "-p", host.port]

        if host.keep_alive not in ("0", ""):
            args += ["-o", f"ServerAliveInterval={host.keep_alive}"]
        for t in host.tunnel:
            if not t:
                continue
            if t.startswith("R:"):
                args += ["-R", t[2:]]
            elif t.endswith(":*:*"):
                args += ["-D", t[:-4]]
            else:
                args += ["-L", t]
        if host.x11:
            args.append("-X")
        if host.agent:
            args.append("-A")
        if host.compression:
            args.append("-C")
            if host.compressionLevel:
                args += ["-o", f"CompressionLevel={host.compressionLevel}"]
        if host.private_key:
            key_path = os.path.expanduser(host.private_key)
            if not os.path.exists(key_path):
                from gnome_connection_manager import msginfo  # noqa: PLC0415

                msginfo(
                    _(
                        "SSH private key file not found:\n\n{}\n\nPlease check the key path configuration."
                    ).format(key_path)
                )
            args += ["-i", host.private_key]
        for field_key, opt_name, _tooltip in _ADVANCED_FIELDS:
            opt_value = getattr(host, field_key, "")
            if opt_value:
                args += ["-o", f"{opt_name}={opt_value}"]
        if host.extra_params:
            args += shlex.split(host.extra_params)
        args.append(host.host)

        logger.debug(f"SshTab.connect_ssh | host={host.name} cmd={cmd} args={args}")
        v.command = (cmd, args, password)
        vte_run(v, cmd, args)

        while Gtk.events_pending():
            Gtk.main_iteration()

        if password:
            GLib.timeout_add(2000, wMain.send_data, v, password)

        wMain._start_sshfs_mount(v, host)

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


class SshPlugin(ConnectionPlugin):
    """Plugin pour le protocole ``ssh``."""

    protocol_id = "ssh"
    display_name = _("SSH")
    default_port = 22
    icon_name = "utilities-terminal-symbolic"
    ui_order = 0
    is_terminal = True

    def __init__(self) -> None:
        """Initialise les références de widgets du formulaire SSH (peuplées par ``build_edit_page()``)."""
        self._advanced_entries: dict[str, Gtk.Entry] = {}
        self._chk_keep_alive: Gtk.CheckButton | None = None
        self._txt_keep_alive: Gtk.SpinButton | None = None
        self._chk_x11: Gtk.CheckButton | None = None
        self._chk_agent: Gtk.CheckButton | None = None
        self._chk_compression: Gtk.CheckButton | None = None
        self._txt_compression_level: Gtk.SpinButton | None = None
        self._txt_extra_params: Gtk.Entry | None = None
        # Port forwarding
        self._cmb_tunnel_type: Gtk.ComboBoxText | None = None
        self._lbl_local_port: Gtk.Label | None = None
        self._lbl_remote_host: Gtk.Label | None = None
        self._lbl_remote_port: Gtk.Label | None = None
        self._txt_local_port: Gtk.SpinButton | None = None
        self._txt_remote_host: Gtk.Entry | None = None
        self._txt_remote_port: Gtk.SpinButton | None = None
        self._tree_tunnel: Gtk.TreeView | None = None
        self._tunnel_model: Gtk.ListStore | None = None
        # SSHFS
        self._chk_sshfs_mount: Gtk.CheckButton | None = None
        self._txt_sshfs_remote_path: Gtk.Entry | None = None
        self._txt_sshfs_local_mount: Gtk.Entry | None = None

    # ------------------------------------------------------------------
    # build_tab() — implémenté fidèlement, NON câblé dans addTab() (cf.
    # docstring de module : nécessite une validation manuelle en conditions
    # réelles avant intégration au dispatcher).
    # ------------------------------------------------------------------
    def build_tab(self, host, get_password: Callable[[], str] | None = None) -> Gtk.Widget:
        """Construit l'onglet de session SSH.

        Args:
            host: Instance ``Host`` avec ``protocol == "ssh"``.
            get_password: Non utilisé — le mot de passe est lu directement
                depuis ``host.password``, comme dans le code historique.

        Returns:
            Une instance de :class:`SshTab` (terminal VTE construit, pas
            encore connecté — l'appelant doit ensuite appeler
            ``.connect_ssh()`` une fois le widget inséré dans le notebook,
            même contrat que ``VncTab.connect_vnc()``).
        """
        logger.debug(f"SshPlugin.build_tab | host={host.name}")
        return SshTab(host)

    # ------------------------------------------------------------------
    # Page d'édition — complète et câblée (Advanced SSH + Port forwarding +
    # SSHFS).
    # ------------------------------------------------------------------
    def build_edit_page(self) -> Gtk.Widget:
        """Construit la page de champs Edit Host pour SSH.

        Returns:
            Un ``Gtk.Notebook`` à 3 onglets internes (Advanced / Port
            forwarding / SSHFS), reproduisant ``pgHostSsh`` +
            ``tunnelGrid`` + ``pgHostSshfs``.
        """
        sub_notebook = Gtk.Notebook()
        sub_notebook.append_page(self._build_advanced_page(), Gtk.Label(label=_("Advanced")))
        sub_notebook.append_page(
            self._build_port_forwarding_page(), Gtk.Label(label=_("Port forwarding"))
        )
        sub_notebook.append_page(self._build_sshfs_page(), Gtk.Label(label=_("SSHFS")))
        sub_notebook.show_all()
        return sub_notebook

    def _build_advanced_page(self) -> Gtk.Widget:
        """Construit la sous-page "Advanced" (options SSH + keep-alive/X11/agent/compression)."""
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid = Gtk.Grid()
        grid.set_row_spacing(4)
        grid.set_column_spacing(6)
        grid.set_margin_start(8)
        grid.set_margin_end(8)
        grid.set_margin_top(8)
        row = 0

        self._chk_keep_alive = Gtk.CheckButton(label=_("Keep alive (seconds)"))
        self._chk_keep_alive.connect("toggled", self._on_keep_alive_toggled)
        grid.attach(self._chk_keep_alive, 0, row, 1, 1)
        self._txt_keep_alive = Gtk.SpinButton()
        self._txt_keep_alive.set_adjustment(
            Gtk.Adjustment(value=120, lower=0, upper=3600, step_increment=10)
        )
        self._txt_keep_alive.set_sensitive(False)
        grid.attach(self._txt_keep_alive, 1, row, 1, 1)
        row += 1

        self._chk_x11 = Gtk.CheckButton(label=_("X11 forwarding (-X)"))
        grid.attach(self._chk_x11, 0, row, 2, 1)
        row += 1
        self._chk_agent = Gtk.CheckButton(label=_("Agent forwarding (-A)"))
        grid.attach(self._chk_agent, 0, row, 2, 1)
        row += 1

        self._chk_compression = Gtk.CheckButton(label=_("Compression (-C)"))
        self._chk_compression.connect("toggled", self._on_compression_toggled)
        grid.attach(self._chk_compression, 0, row, 1, 1)
        self._txt_compression_level = Gtk.SpinButton()
        self._txt_compression_level.set_adjustment(
            Gtk.Adjustment(value=6, lower=1, upper=9, step_increment=1)
        )
        self._txt_compression_level.set_sensitive(False)
        grid.attach(self._txt_compression_level, 1, row, 1, 1)
        row += 1

        for field_key, opt_name, tooltip in _ADVANCED_FIELDS:
            lbl = Gtk.Label(label=opt_name)
            lbl.set_halign(Gtk.Align.START)
            grid.attach(lbl, 0, row, 1, 1)
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            if tooltip:
                entry.set_tooltip_text(tooltip)
            grid.attach(entry, 1, row, 1, 1)
            self._advanced_entries[field_key] = entry
            row += 1

        lbl_extra = Gtk.Label(label=_("Extra ssh args"))
        lbl_extra.set_halign(Gtk.Align.START)
        grid.attach(lbl_extra, 0, row, 1, 1)
        self._txt_extra_params = Gtk.Entry()
        self._txt_extra_params.set_hexpand(True)
        grid.attach(self._txt_extra_params, 1, row, 1, 1)

        scroller.add(grid)
        return scroller

    def _on_keep_alive_toggled(self, widget: Gtk.CheckButton) -> None:
        """Active/désactive le champ de délai keep-alive selon la case à cocher.

        Args:
            widget: Case à cocher "Keep alive".
        """
        self._txt_keep_alive.set_sensitive(widget.get_active())
        self._txt_keep_alive.set_text("120" if widget.get_active() else "0")

    def _on_compression_toggled(self, widget: Gtk.CheckButton) -> None:
        """Active/désactive le champ de niveau de compression selon la case à cocher.

        Args:
            widget: Case à cocher "Compression".
        """
        self._txt_compression_level.set_sensitive(widget.get_active())

    def _build_port_forwarding_page(self) -> Gtk.Widget:
        """Construit la sous-page "Port forwarding" (formulaire + TreeView + boutons)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)

        form = Gtk.Grid()
        form.set_row_spacing(4)
        form.set_column_spacing(6)

        form.attach(Gtk.Label(label=_("Type")), 0, 0, 1, 1)
        self._cmb_tunnel_type = Gtk.ComboBoxText()
        for item_id, label in _TUNNEL_TYPE_ITEMS:
            self._cmb_tunnel_type.append(item_id, label)
        self._cmb_tunnel_type.set_active_id("local")
        self._cmb_tunnel_type.connect("changed", self._on_tunnel_type_changed)
        form.attach(self._cmb_tunnel_type, 1, 0, 1, 1)

        self._lbl_local_port = Gtk.Label(label=_("Local Port"))
        self._lbl_local_port.set_halign(Gtk.Align.START)
        form.attach(self._lbl_local_port, 0, 1, 1, 1)
        self._txt_local_port = Gtk.SpinButton()
        self._txt_local_port.set_adjustment(
            Gtk.Adjustment(value=8080, lower=1, upper=65535, step_increment=1)
        )
        form.attach(self._txt_local_port, 1, 1, 1, 1)

        self._lbl_remote_host = Gtk.Label(label=_("Remote Host"))
        self._lbl_remote_host.set_halign(Gtk.Align.START)
        form.attach(self._lbl_remote_host, 0, 2, 1, 1)
        self._txt_remote_host = Gtk.Entry()
        form.attach(self._txt_remote_host, 1, 2, 1, 1)

        self._lbl_remote_port = Gtk.Label(label=_("Remote Port"))
        self._lbl_remote_port.set_halign(Gtk.Align.START)
        form.attach(self._lbl_remote_port, 0, 3, 1, 1)
        self._txt_remote_port = Gtk.SpinButton()
        self._txt_remote_port.set_adjustment(
            Gtk.Adjustment(value=8080, lower=1, upper=65535, step_increment=1)
        )
        form.attach(self._txt_remote_port, 1, 3, 1, 1)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_add = Gtk.Button(label=_("Add"))
        btn_add.connect("clicked", self._on_add_tunnel_clicked)
        btn_del = Gtk.Button(label=_("Remove"))
        btn_del.connect("clicked", self._on_del_tunnel_clicked)
        btn_box.pack_start(btn_add, False, False, 0)
        btn_box.pack_start(btn_del, False, False, 0)
        form.attach(btn_box, 0, 4, 2, 1)

        box.pack_start(form, False, False, 0)

        self._tunnel_model = Gtk.ListStore(
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
            GObject.TYPE_STRING,
        )
        self._tree_tunnel = Gtk.TreeView(model=self._tunnel_model)
        for col_idx, title in ((0, _("Local")), (1, _("Host")), (2, _("Remote")), (4, _("Type"))):
            self._tree_tunnel.append_column(
                Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=col_idx)
            )
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)
        sw.add(self._tree_tunnel)
        box.pack_start(sw, True, True, 0)

        return box

    def _on_tunnel_type_changed(self, widget: Gtk.ComboBoxText) -> None:
        """Adapte la sensibilité/les libellés des champs selon le type de redirection.

        Args:
            widget: Sélecteur de type de tunnel qui vient de changer.
        """
        tunnel_type = widget.get_active_id() or "local"
        is_dynamic = tunnel_type == "dynamic"
        self._txt_remote_host.set_sensitive(not is_dynamic)
        self._txt_remote_port.set_sensitive(not is_dynamic)
        if tunnel_type == "remote":
            self._lbl_local_port.set_text(_("Remote listen port"))
            self._lbl_remote_host.set_text(_("Local target host"))
            self._lbl_remote_port.set_text(_("Local target port"))
        else:
            self._lbl_local_port.set_text(_("Local Port"))
            self._lbl_remote_host.set_text(_("Remote Host"))
            self._lbl_remote_port.set_text(_("Remote Port"))

    def _on_add_tunnel_clicked(self, _widget: Gtk.Button) -> None:
        """Ajoute une redirection de port à la liste, après validation.

        Args:
            _widget: Bouton "Add" cliqué.
        """
        local = self._txt_local_port.get_text().strip()
        host_field = self._txt_remote_host.get_text().strip()
        remote = self._txt_remote_port.get_text().strip()
        tunnel_type = self._cmb_tunnel_type.get_active_id() or "local"

        if tunnel_type == "dynamic":
            host_field = "*"
            remote = "*"

        if tunnel_type != "dynamic" and ":" in host_field and remote == "":
            parts = host_field.rsplit(":", 1)
            if parts[1].isdigit():
                host_field, remote = parts[0], parts[1]
                self._txt_remote_host.set_text(host_field)
                self._txt_remote_port.set_text(remote)

        if host_field == "":
            return  # validation déléguée à validate()

        for row in self._tunnel_model:
            if row[0] == local:
                return  # port local déjà utilisé — géré par validate()

        prefix = "R:" if tunnel_type == "remote" else ""
        type_label = {"dynamic": _("Dynamic"), "remote": _("Remote")}.get(tunnel_type, _("Local"))
        self._tunnel_model.append(
            [local, host_field, remote, f"{prefix}{local}:{host_field}:{remote}", type_label]
        )

    def _on_del_tunnel_clicked(self, _widget: Gtk.Button) -> None:
        """Supprime la redirection sélectionnée dans la liste.

        Args:
            _widget: Bouton "Remove" cliqué.
        """
        selected = self._tree_tunnel.get_selection().get_selected()[1]
        if selected is not None:
            self._tunnel_model.remove(selected)

    def _build_sshfs_page(self) -> Gtk.Widget:
        """Construit la sous-page "SSHFS" (montage automatique du système de fichiers distant)."""
        grid = Gtk.Grid()
        grid.set_row_spacing(4)
        grid.set_column_spacing(6)
        grid.set_margin_start(8)
        grid.set_margin_end(8)
        grid.set_margin_top(8)

        self._chk_sshfs_mount = Gtk.CheckButton(
            label=_("Mount remote filesystem via SSHFS on connect")
        )
        grid.attach(self._chk_sshfs_mount, 0, 0, 2, 1)

        grid.attach(Gtk.Label(label=_("Remote path"), halign=Gtk.Align.START), 0, 1, 1, 1)
        self._txt_sshfs_remote_path = Gtk.Entry()
        self._txt_sshfs_remote_path.set_hexpand(True)
        grid.attach(self._txt_sshfs_remote_path, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label=_("Local mount point"), halign=Gtk.Align.START), 0, 2, 1, 1)
        self._txt_sshfs_local_mount = Gtk.Entry()
        self._txt_sshfs_local_mount.set_hexpand(True)
        grid.attach(self._txt_sshfs_local_mount, 1, 2, 1, 1)

        return grid

    # ------------------------------------------------------------------
    # Chargement / sauvegarde
    # ------------------------------------------------------------------
    def load_host_fields(self, host) -> None:
        """Recharge tous les champs SSH (avancé + tunnels + SSHFS) depuis *host*.

        Args:
            host: Instance ``Host`` source.
        """
        for field_key, entry in self._advanced_entries.items():
            entry.set_text(getattr(host, field_key, "") or "")

        use_keep_alive = getattr(host, "keep_alive", "") not in ("", "0", None)
        self._chk_keep_alive.set_active(use_keep_alive)
        self._txt_keep_alive.set_sensitive(use_keep_alive)
        self._txt_keep_alive.set_text(str(getattr(host, "keep_alive", "0") or "0"))
        self._chk_x11.set_active(bool(getattr(host, "x11", False)))
        self._chk_agent.set_active(bool(getattr(host, "agent", False)))
        compression = bool(getattr(host, "compression", False))
        self._chk_compression.set_active(compression)
        self._txt_compression_level.set_sensitive(compression)
        self._txt_compression_level.set_text(str(getattr(host, "compressionLevel", "6") or "6"))
        self._txt_extra_params.set_text(getattr(host, "extra_params", "") or "")

        self._tunnel_model.clear()
        for t in getattr(host, "tunnel", []) or []:
            if not t:
                continue
            raw = t[2:] if t.startswith("R:") else t
            tun = raw.split(":", 2)
            if len(tun) < 3:
                tun += [""] * (3 - len(tun))
            tun.append(t)
            if t.startswith("R:"):
                tun.append(_("Remote"))
            elif t.endswith(":*:*"):
                tun.append(_("Dynamic"))
            else:
                tun.append(_("Local"))
            self._tunnel_model.append(tun)

        self._chk_sshfs_mount.set_active(_bool_from_str(getattr(host, "ssh_sshfs_mount", False)))
        self._txt_sshfs_remote_path.set_text(getattr(host, "ssh_sshfs_remote_path", "") or "")
        self._txt_sshfs_local_mount.set_text(getattr(host, "ssh_sshfs_local_mount", "") or "")
        logger.debug(f"SshPlugin.load_host_fields | host={getattr(host, 'name', '?')}")

    def save_host_fields(self, host) -> None:
        """Reporte tous les champs SSH (avancé + tunnels + SSHFS) dans *host*.

        Args:
            host: Instance ``Host`` à mettre à jour.
        """
        for field_key, entry in self._advanced_entries.items():
            setattr(host, field_key, entry.get_text().strip())

        host.keep_alive = (
            self._txt_keep_alive.get_text().strip() if self._chk_keep_alive.get_active() else "0"
        )
        host.x11 = self._chk_x11.get_active()
        host.agent = self._chk_agent.get_active()
        host.compression = self._chk_compression.get_active()
        host.compressionLevel = self._txt_compression_level.get_text().strip()
        host.extra_params = self._txt_extra_params.get_text().strip()

        tunnels = []
        for row in self._tunnel_model:
            tunnels.append(row[3])
        host.tunnel = tunnels

        host.ssh_sshfs_mount = self._chk_sshfs_mount.get_active()
        host.ssh_sshfs_remote_path = self._txt_sshfs_remote_path.get_text().strip()
        host.ssh_sshfs_local_mount = self._txt_sshfs_local_mount.get_text().strip()
        logger.debug(f"SshPlugin.save_host_fields | host={getattr(host, 'name', '?')}")

    def host_fields(self) -> list[tuple[str, object]]:
        """Champs ``Host`` propres à SSH (options avancées + SSHFS)."""
        return [(field_key, "") for field_key, _label, _tooltip in _ADVANCED_FIELDS] + [
            ("ssh_sshfs_mount", False),
            ("ssh_sshfs_remote_path", ""),
            ("ssh_sshfs_local_mount", ""),
        ]

    def validate(self) -> list[str]:
        """Validation du formulaire SSH.

        Returns:
            Liste vide : les contraintes de saisie du port forwarding
            (hôte requis, port local déjà utilisé) sont déjà appliquées de
            façon silencieuse par ``_on_add_tunnel_clicked`` avant tout ajout
            à la liste — comportement identique au code historique
            (``on_btnAdd_clicked``), qui n'empêche pas non plus la
            sauvegarde du reste du formulaire.
        """
        return []

    # ------------------------------------------------------------------
    # Éditeur ~/.ssh/config + gestion des clés SSH — embarqués dans le
    # plugin (ce ne sont pas des fonctionnalités du cœur de l'application,
    # elles n'ont de sens que pour SSH).
    # ------------------------------------------------------------------
    def menu_actions(self) -> list[tuple[str, str, Callable]]:
        """Actions de menu exposées par le plugin SSH.

        Returns:
            Liste de ``(action_name, label, callback)`` — utilisée par
            ``Wmain._build_primary_menu()`` pour peupler les entrées
            "Edit ~/.ssh/config…", "Move all to ssh_config…", "Import from
            ssh_config…" et "Manage SSH Keys…" du menu hamburger.
        """
        return [
            ("edit-ssh-config", _("Edit ~/.ssh/config…"), self.edit_ssh_config),
            ("migrate-all-ssh", _("Move all to ssh_config…"), self.migrate_all_ssh),
            ("import-ssh-config", _("Import from ssh_config…"), self.import_ssh_config_action),
            ("manage-ssh-keys", _("Manage SSH Keys…"), self.manage_ssh_keys),
        ]

    def build_folder_context_menu_items(self) -> list[tuple[str, Callable]]:
        """Items du menu contextuel du panneau de serveurs propres à SSH.

        Reprend les deux actions historiquement codées en dur dans
        ``Wmain.createMenu()`` ("Move to ssh_config…" / "Import from
        ssh_config…") — plus aucune trace de "ssh" dans le cœur de
        l'application, tout vit maintenant ici.

        Returns:
            Liste de tuples ``(libellé, callback)``.
        """
        return [
            (_("Move to ssh_config…"), self._on_migrate_selected_host_menu_activate),
            (_("Import from ssh_config…"), self.import_ssh_config_action),
        ]

    def _on_migrate_selected_host_menu_activate(self) -> None:
        """Callback du menu contextuel : migre l'hôte actuellement
        sélectionné dans l'arbre des serveurs vers un fichier ssh_config.
        """
        from gnome_connection_manager import wMain  # noqa: PLC0415

        selected = wMain.treeServers.get_selection().get_selected()[1]
        if selected is None or wMain.treeModel.iter_has_child(selected):
            return
        host = wMain.treeModel.get_value(selected, 1)
        self.migrate_ssh_host(host)

    def edit_ssh_config(self) -> None:
        """Ouvre l'éditeur visuel de ~/.ssh/config + fichier partagé comme
        onglet épinglé dans nbConsole (réutilise l'onglet existant s'il est
        déjà ouvert, cf. ``Wmain.open_management_tab``).
        """
        from gnome_connection_manager import wMain  # noqa: PLC0415

        try:
            from ssh_config_editor import SshConfigEditorDialog  # noqa: PLC0415
        except ImportError as exc:
            logger.error("plugin ssh | module ssh_config_editor introuvable : {}", exc)
            self._show_missing_module_error(
                _(
                    "ssh_config_editor module not found.\n"
                    "Make sure ssh_config_editor.py and ssh_config_parser.py "
                    "are in the same directory as gnome_connection_manager.py."
                )
            )
            return

        logger.debug("SshPlugin.edit_ssh_config | ouverture de l'onglet SshConfigEditorDialog")
        wMain.open_management_tab(
            "ssh-config",
            _("Edit SSH config"),
            lambda: SshConfigEditorDialog(parent=wMain.window, show=False),
        )

    def manage_ssh_keys(self) -> None:
        """Ouvre le gestionnaire de clés SSH comme onglet épinglé singleton
        (clé ``"sshkeys"``) dans nbConsole, indépendant de toute connexion
        SSH — au même titre que l'onglet Paramètres. Réutilise l'onglet
        existant s'il est déjà ouvert (cf. ``Wmain.open_management_tab``).
        """
        from gnome_connection_manager import wMain  # noqa: PLC0415

        try:
            from ssh_key_manager_dialog import SSHKeyManagerDialog  # noqa: PLC0415
        except ImportError as exc:
            logger.error("plugin ssh | module ssh_key_manager_dialog introuvable : {}", exc)
            self._show_missing_module_error(
                _(
                    "ssh_key_manager_dialog module not found.\n"
                    "Make sure ssh_key_manager_dialog.py is in the same directory "
                    "as gnome_connection_manager.py."
                )
            )
            return

        logger.debug("SshPlugin.manage_ssh_keys | ouverture de l'onglet SSHKeyManagerDialog")
        wMain.open_management_tab(
            "sshkeys",
            _("SSH Key Manager"),
            lambda: SSHKeyManagerDialog(parent=wMain.window, show=False),
        )

    def _ask_ssh_config_target(self):
        """Demande à l'utilisateur la cible ssh_config (personnelle ou partagée).

        Returns:
            Path | None: ``~/.ssh/config`` (personnel),
            ``SHARED_SSH_CONFIG_PATH`` (partagé), ou ``None`` si annulé.
        """
        from pathlib import Path  # noqa: PLC0415

        from gnome_connection_manager import wMain  # noqa: PLC0415
        from plugins.ssh.core import SHARED_SSH_CONFIG_PATH  # noqa: PLC0415
        from utils import run_dialog_sync  # noqa: PLC0415

        dlg = Gtk.MessageDialog(
            transient_for=wMain.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            text=_("Which file?"),
        )
        dlg.format_secondary_text(
            _(
                "Personal: only for you, ~/.ssh/config.\n"
                "Shared: visible and usable by every user of this machine "
                "(team bastion), %s — requires root privileges."
            )
            % SHARED_SSH_CONFIG_PATH
        )
        dlg.add_buttons(
            _("Cancel"),
            Gtk.ResponseType.CANCEL,
            _("Shared (everyone)"),
            Gtk.ResponseType.NO,
            _("Personal (me)"),
            Gtk.ResponseType.YES,
        )
        response = run_dialog_sync(dlg)
        dlg.destroy()
        if response == Gtk.ResponseType.YES:
            return Path.home() / ".ssh" / "config"
        if response == Gtk.ResponseType.NO:
            return SHARED_SSH_CONFIG_PATH
        return None

    def migrate_all_ssh(self) -> None:
        """Déplace TOUS les hôtes SSH de gcm.conf vers le fichier ssh_config choisi."""
        from gnome_connection_manager import app_logger, msgconfirm, msginfo  # noqa: PLC0415
        from plugins.ssh.core import is_shared_target, migrate_ssh_hosts  # noqa: PLC0415

        target = self._ask_ssh_config_target()
        if target is None:
            return
        shared = is_shared_target(target)
        confirm_msg = (
            _(
                "Move all SSH hosts from gcm.conf to the SHARED file %s?\n"
                "This will be usable by every user of this machine and requires root "
                "privileges (a polkit prompt may appear). A timestamped backup of both "
                "files will be created."
            )
            % target
            if shared
            else _(
                "Move all SSH hosts from gcm.conf to %s?\nA backup of both files will be created."
            )
            % target
        )
        if msgconfirm(confirm_msg) != Gtk.ResponseType.OK:
            return
        try:
            result = migrate_ssh_hosts(ssh_config=target)
        except Exception as exc:
            app_logger.error(f"SshPlugin.migrate_all_ssh | migration failed | exc={exc}")
            msginfo(_("Migration failed: %s") % exc)
            return
        msginfo(
            _("Migration done — moved: %d, skipped: %d, already managed: %d, errors: %d")
            % (
                len(result["migrated"]),
                len(result["skipped"]),
                len(result["already"]),
                len(result["errors"]),
            )
        )

    def migrate_ssh_host(self, host) -> None:
        """Déplace uniquement *host* de gcm.conf vers le fichier ssh_config choisi.

        Args:
            host: Instance ``Host`` sélectionnée (menu contextuel de l'arbre).
        """
        from gnome_connection_manager import app_logger, msgconfirm, msginfo  # noqa: PLC0415
        from plugins.ssh.core import is_shared_target, migrate_ssh_hosts  # noqa: PLC0415

        target = self._ask_ssh_config_target()
        if target is None:
            return
        shared = is_shared_target(target)
        confirm_msg = (
            _(
                "Move host «%s» from gcm.conf to the SHARED file %s?\n"
                "This will be usable by every user of this machine and requires root "
                "privileges (a polkit prompt may appear). A timestamped backup of both "
                "files will be created."
            )
            % (host.name, target)
            if shared
            else _("Move host «%s» from gcm.conf to %s?\nA backup of both files will be created.")
            % (host.name, target)
        )
        if msgconfirm(confirm_msg) != Gtk.ResponseType.OK:
            return
        try:
            result = migrate_ssh_hosts(only_names={host.name}, ssh_config=target)
        except Exception as exc:
            app_logger.error(f"SshPlugin.migrate_ssh_host | migration failed | exc={exc}")
            msginfo(_("Migration failed: %s") % exc)
            return
        if result["migrated"]:
            msginfo(_("Host «%s» moved to %s.") % (host.name, target))
        elif result["already"]:
            msginfo(
                _(
                    "Host «%s» is already managed by a ssh_config file — it cannot be moved a 2nd time."
                )
                % host.name
            )
        elif result["errors"]:
            msginfo(_("Migration failed: %s") % "; ".join(result["errors"]))
        else:
            msginfo(_("Host «%s» was not migrated (not an SSH host?).") % host.name)

    def import_ssh_config_action(self) -> None:
        """Importe les connexions depuis un fichier ssh_config vers gcm.conf.

        Règle stricte : ``name == host == alias`` du fichier ssh_config,
        dé-duplication sur ``(name, host)`` tous groupes confondus (cf.
        ``plugins.ssh.core.import_ssh_config``).
        """
        from pathlib import Path  # noqa: PLC0415

        from gnome_connection_manager import app_logger, msginfo, wMain  # noqa: PLC0415
        from plugins.ssh.core import SHARED_SSH_CONFIG_PATH, import_ssh_config  # noqa: PLC0415
        from utils import run_dialog_sync  # noqa: PLC0415

        dlg = Gtk.MessageDialog(
            transient_for=wMain.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            text=_("Import from which file?"),
        )
        dlg.format_secondary_text(
            _(
                "Connections will be added to the « ssh_config » group, with name = host = SSH alias."
            )
        )
        dlg.add_buttons(
            _("Cancel"),
            Gtk.ResponseType.CANCEL,
            _("Shared"),
            Gtk.ResponseType.NO,
            _("Personal (~/.ssh/config)"),
            Gtk.ResponseType.YES,
        )
        response = run_dialog_sync(dlg)
        dlg.destroy()
        if response == Gtk.ResponseType.YES:
            source = Path.home() / ".ssh" / "config"
        elif response == Gtk.ResponseType.NO:
            source = SHARED_SSH_CONFIG_PATH
        else:
            return

        try:
            result = import_ssh_config(source=source)
        except Exception as exc:
            app_logger.error(f"SshPlugin.import_ssh_config_action | import failed | exc={exc}")
            msginfo(_("Import failed: %s") % exc)
            return

        msginfo(
            _("Import done — imported: %d, already present: %d, errors: %d")
            % (len(result["imported"]), len(result["skipped_existing"]), len(result["errors"]))
        )
        wMain.updateTree()

    @staticmethod
    def _show_missing_module_error(message: str) -> None:
        """Affiche une boîte d'erreur pour un module optionnel manquant.

        Args:
            message: Texte à afficher.
        """
        from gnome_connection_manager import msgbox  # noqa: PLC0415

        msgbox(message)

    # ------------------------------------------------------------------
    # Dialogue d'édition d'hôte (wHost, cœur) — hôte géré par ssh_config.
    #
    # Rapatrié depuis ``ssh_migrate_gcm.patch_edit_host_dialog`` (avant :
    # importé en dur par le cœur, cf. `gnome_connection_manager.py` avant
    # la session-23 — dernier vestige SSH codé en dur dans `Whost.init`).
    # ``plugins/ssh/core.py`` (fusion session-26 de l'ancien
    # ``ssh_migrate_gcm.py`` avec ``ssh_config_parser.py``) reste
    # responsable de la migration CLI/logique (_cp_getbool,
    # _MANAGED_DESCRIPTION, migrate_ssh_hosts...) ; seul le patch de
    # widgets GTK, propre à SSH, est désormais ici, exposé via le hook
    # générique ``ConnectionPlugin.patch_edit_host_dialog``.
    # ------------------------------------------------------------------
    def patch_edit_host_dialog(self, builder: object, host_section: str, cp) -> None:
        """Grise les champs SSH et affiche la notice rouge si l'hôte est géré par ~/.ssh/config.

        No-op si l'hôte n'est pas managé (flag ``ssh_config_managed`` posé
        par ``plugins.ssh.core.migrate_ssh_hosts``) — c'est ce qui permet à
        ``PluginRegistry`` d'appeler cette méthode pour TOUT hôte, quel que
        soit son protocole réel, sans condition côté cœur (cf.
        ``ConnectionPlugin.patch_edit_host_dialog``).

        IDs de widgets utilisés (dialogue ``wHost``) :

        * ``txtPort``        — GtkSpinButton pour le port
        * ``txtPrivateKey``  — GtkEntry pour le chemin de la clé privée
        * ``btnBrowse``      — GtkButton pour choisir la clé privée
        * ``txtKeepAlive``   — GtkSpinButton pour l'intervalle keep-alive
        * ``chkKeepAlive``   — GtkCheckButton d'activation du keep-alive
        * ``txtExtraParams`` — GtkEntry pour les paramètres SSH additionnels
        * ``chkX11``         — GtkCheckButton pour le forwarding X11
        * ``chkAgent``       — GtkCheckButton pour le forwarding agent
        * ``chkCompression`` — GtkCheckButton pour la compression
        * ``treeTunel``      — GtkTreeView pour les tunnels
        * ``txtDescription`` — GtkEntry portant la notice rouge

        Args:
            builder: Objet exposant ``get_object(widget_id)`` (adaptateur
                vers ``Whost.get_widget`` côté cœur).
            host_section: Nom de la section ``configparser`` de l'hôte
                actuellement édité, ex. ``"host 3"``.
            cp: ``gcm.conf`` déjà chargé (``configparser.RawConfigParser``).
        """
        from plugins.ssh.core import _MANAGED_DESCRIPTION, _cp_getbool  # noqa: PLC0415

        managed = _cp_getbool(cp, host_section, "ssh_config_managed")
        if not managed:
            return

        alias = host_section[5:].strip() if host_section.startswith("host ") else host_section
        safe_alias = alias.replace(" ", "_")

        try:

            def _get_widget(widget_id: str) -> object | None:
                obj = builder.get_object(widget_id)
                if obj is None:
                    logger.debug(
                        f"SshPlugin.patch_edit_host_dialog | widget not found | id={widget_id}"
                    )
                return obj

            for widget_id in (
                "txtPort",
                "txtPrivateKey",
                "btnBrowse",
                "txtKeepAlive",
                "chkKeepAlive",
                "txtExtraParams",
                "chkX11",
                "chkAgent",
                "chkCompression",
                "treeTunel",
            ):
                w = _get_widget(widget_id)
                if w is not None:
                    w.set_sensitive(False)

            notice = _MANAGED_DESCRIPTION.format(name=safe_alias)
            desc = _get_widget("txtDescription")
            if desc is not None:
                if not (desc.get_text() or "").strip():
                    desc.set_text(notice)
                desc.set_tooltip_text(notice)
                desc.set_editable(False)
                try:
                    provider = Gtk.CssProvider()
                    provider.load_from_data(b"entry { color: #cc0000; font-weight: bold; }")
                    desc.get_style_context().add_provider(
                        provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                    )
                except Exception as exc:
                    logger.debug("plugin ssh | style CSS non appliqué : {}", exc)

            logger.info(
                f"SshPlugin.patch_edit_host_dialog | dialog patched for managed host | alias={alias}"
            )

        except Exception as exc:
            # Ne jamais faire planter GCM pour un simple habillage UI.
            logger.warning(
                f"SshPlugin.patch_edit_host_dialog | GTK patch failed | alias={alias} exc={exc}"
            )


def get_plugin() -> SshPlugin:
    """Point d'entree standard utilise par PluginRegistry.autoload().

    Returns:
        Une instance de SshPlugin, prete a etre enregistree.
    """
    return SshPlugin()
