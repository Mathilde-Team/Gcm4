# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin de connexion IPMI Serial-over-LAN (console BMC : iLO, iDRAC, IMM…).

Deuxième plugin implémenté après Web. IPMI SOL n'a aujourd'hui AUCUNE page
d'édition dédiée dans le .glade — seuls les champs communs (Host/Port/User/
Password, gérés par la coquille Whost) sont utilisés. ``build_edit_page()``
retourne donc un widget vide : ceci est fidèle au comportement actuel, pas
une régression. ``host.extra_params`` (options ipmitool additionnelles) n'a
jamais eu de champ dédié dans l'UI existante — hors périmètre de ce plugin.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from gettext import gettext as _

import gi
from loguru import logger

gi.require_version("Vte", "2.91")
from gi.repository import GLib, Gtk, Vte
from plugin_base import ConnectionPlugin

from widgets import app_logger, vte_run

__all__ = ["IpmiSolPlugin", "IpmiSolTab"]

IPMITOOL_BIN = shutil.which("ipmitool")


class IpmiSolTab(Gtk.Box):
    """Onglet de console texte IPMI Serial-over-LAN (BMC : iLO, iDRAC, IMM…).

    Encapsule `ipmitool -I lanplus ... sol activate` dans un terminal VTE.
    Le mot de passe est transmis via la variable d'environnement
    IPMI_PASSWORD (option -E d'ipmitool) plutôt qu'en argument de ligne de
    commande, pour éviter qu'il apparaisse dans `ps aux`.

    Une session SOL mal terminée reste parfois verrouillée côté BMC : la
    déconnexion (bouton ou fermeture d'onglet) envoie donc systématiquement
    un `sol deactivate` explicite en plus du SIGTERM sur le process VTE.

    Args:
        host (Host): Hôte avec protocol='ipmi'.
            - host.host, host.user, host.password : identifiants IPMI LAN.
            - host.port : port UDP (623 par défaut).
            - host.extra_params : options ipmitool additionnelles (ex: -C 17).
    """

    def __init__(self, host):
        """Initialise le panneau IPMI/SOL."""
        logger.debug("IpmiSolTab.__init__: entrée")
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.host = host
        self._build_ui()
        logger.debug("IpmiSolTab.__init__: fin")

    def _build_ui(self):
        """Construit la barre d'outils + le terminal VTE embarqué."""
        logger.debug("IpmiSolTab._build_ui: entrée")
        h = self.host
        port = getattr(h, "port", "623") or "623"

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)
        toolbar.set_margin_top(4)
        toolbar.set_margin_bottom(4)

        title = Gtk.Label()
        title.set_markup(f"<b>IPMI/SOL</b> <small>{h.user}@{h.host}:{port}</small>")
        title.set_xalign(0)
        toolbar.pack_start(title, True, True, 0)

        self._lbl_status = Gtk.Label(label=_("Waiting…"))
        self._lbl_status.get_style_context().add_class("dim-label")
        toolbar.pack_start(self._lbl_status, False, False, 8)

        self._btn_connect = Gtk.Button(label=_("Connect"))
        self._btn_connect.get_style_context().add_class("suggested-action")
        self._btn_connect.connect("clicked", self._on_connect)
        toolbar.pack_start(self._btn_connect, False, False, 0)

        self._btn_disconnect = Gtk.Button(label=_("Disconnect"))
        self._btn_disconnect.set_sensitive(False)
        self._btn_disconnect.connect("clicked", self._on_disconnect)
        toolbar.pack_start(self._btn_disconnect, False, False, 0)

        self.pack_start(toolbar, False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 0)

        self._terminal = Vte.Terminal()
        self._terminal.set_scrollback_lines(5000)
        self._terminal.connect("child-exited", self._on_child_exited)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(self._terminal)
        sw.set_hexpand(True)
        sw.set_vexpand(True)
        self.pack_start(sw, True, True, 0)

        self.show_all()
        logger.debug("IpmiSolTab._build_ui: fin")

    def _ipmitool_base_args(self):
        """Construit les arguments ipmitool communs (hôte, interface, options).

        Returns:
            list[str]: Arguments (sans le sous-commande 'sol activate/deactivate').
        """
        logger.debug("IpmiSolTab._ipmitool_base_args: entrée")
        h = self.host
        port = str(getattr(h, "port", "623") or "623")
        args = [
            IPMITOOL_BIN,
            "-I",
            "lanplus",
            "-H",
            h.host,
            "-p",
            port,
            "-U",
            h.user or "",
            "-E",
        ]
        extra = (getattr(h, "extra_params", "") or "").strip()
        if extra:
            args += shlex.split(extra)
        logger.debug("IpmiSolTab._ipmitool_base_args: sortie")
        return args

    def _on_connect(self, widget):
        """Lance `ipmitool sol activate` dans le terminal VTE."""
        logger.debug("IpmiSolTab._on_connect: entrée")
        if not IPMITOOL_BIN:
            self._lbl_status.set_text(_("ipmitool introuvable"))
            logger.debug("IpmiSolTab._on_connect: sortie")
            return
        cmd = self._ipmitool_base_args() + ["sol", "activate"]
        self._lbl_status.set_text(_("Connecting…"))
        self._btn_connect.set_sensitive(False)
        self._btn_disconnect.set_sensitive(True)
        vte_run(
            self._terminal, cmd[0], cmd[1:], extra_env={"IPMI_PASSWORD": self.host.password or ""}
        )
        logger.debug("IpmiSolTab._on_connect: fin")

    def _on_disconnect(self, widget):
        """Termine la session SOL (SIGTERM + sol deactivate explicite)."""
        logger.debug("IpmiSolTab._on_disconnect: entrée")
        try:
            pid = self._terminal.get_pty().get_fd()
            if pid and pid > 0:
                import signal as _signal

                os.kill(pid, _signal.SIGTERM)
        except Exception as exc:
            logger.debug("ipmisol | arrêt du processus SOL impossible : {}", exc)
        self._deactivate_sol()
        self._lbl_status.set_text(_("Disconnect"))
        self._btn_connect.set_sensitive(True)
        self._btn_disconnect.set_sensitive(False)
        logger.debug("IpmiSolTab._on_disconnect: fin")

    def _on_child_exited(self, terminal, status):
        """Callback VTE : le process ipmitool s'est terminé (normal ou erreur)."""
        logger.debug("IpmiSolTab._on_child_exited: entrée")
        GLib.idle_add(self._lbl_status.set_text, _("Session ended"))
        GLib.idle_add(self._btn_connect.set_sensitive, True)
        GLib.idle_add(self._btn_disconnect.set_sensitive, False)
        self._deactivate_sol()
        logger.debug("IpmiSolTab._on_child_exited: fin")

    def _deactivate_sol(self):
        """Envoie `sol deactivate` pour libérer explicitement la session côté BMC.

        Nécessaire car une session SOL mal fermée (fenêtre fermée brutalement,
        GCM quitté...) reste verrouillée sur certains BMC (iLO/iDRAC/IMM)
        jusqu'à expiration d'un timeout, empêchant toute reconnexion.
        """
        logger.debug("IpmiSolTab._deactivate_sol: entrée")
        if not IPMITOOL_BIN:
            logger.debug("IpmiSolTab._deactivate_sol: sortie")
            return
        cmd = self._ipmitool_base_args() + ["sol", "deactivate"]
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "IPMI_PASSWORD": self.host.password or ""},
            )
        except Exception as exc:
            app_logger.warning(f"IPMI/SOL | échec deactivate pour host {self.host.name} : {exc}")
        logger.debug("IpmiSolTab._deactivate_sol: fin")

    def connect_ipmi(self):
        """Démarre la session SOL automatiquement (appelé depuis addTab)."""
        logger.debug("IpmiSolTab.connect_ipmi: entrée")
        self._on_connect(None)
        logger.debug("IpmiSolTab.connect_ipmi: fin")


class IpmiSolPlugin(ConnectionPlugin):
    """Plugin pour le protocole ``ipmi`` (IPMI Serial-over-LAN via ipmitool)."""

    protocol_id = "ipmi"
    display_name = _("IPMI (SOL)")
    default_port = 623
    icon_name = "utilities-terminal-symbolic"
    ui_order = 6

    def build_tab(self, host, get_password: Callable[[], str] | None = None) -> Gtk.Widget:
        """Construit l'onglet de session IPMI SOL (délègue à ``IpmiSolTab``).

        Args:
            host: Instance ``Host`` avec ``protocol == "ipmi"``.
            get_password: Non utilisé — ``IpmiSolTab`` lit ``host.password``
                directement (transmis à ipmitool via la variable
                d'environnement ``IPMI_PASSWORD``).

        Returns:
            Une instance de ``IpmiSolTab`` (inchangée).
        """
        logger.debug(f"IpmiSolPlugin.build_tab | host={host.name}")
        return IpmiSolTab(host)

    def build_edit_page(self) -> Gtk.Widget:
        """Retourne une page vide : aucun champ spécifique à IPMI SOL aujourd'hui.

        Returns:
            Un ``Gtk.Box`` vide (les champs communs Host/Port/User/Password
            de la coquille Whost suffisent).
        """
        logger.debug("IpmiSolPlugin.build_edit_page: entrée")
        box = Gtk.Box()
        box.show()
        logger.debug("IpmiSolPlugin.build_edit_page: sortie")
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


def get_plugin() -> IpmiSolPlugin:
    """Point d'entree standard utilise par PluginRegistry.autoload().

    Returns:
        Une instance de IpmiSolPlugin, prete a etre enregistree.
    """
    return IpmiSolPlugin()
