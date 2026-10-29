# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin de connexion Web (console BMC HTML5 : iLO/iDRAC/IMM, ou toute URL).

Premier plugin implémenté, choisi comme preuve de concept car ``WebTab``
(widgets.py) est la classe d'onglet la plus simple du projet (104 lignes,
un seul champ non-trivial : l'URL).

``build_tab()`` enveloppe ``WebTab`` telle quelle — aucune logique de
connexion réécrite. ``build_edit_page()`` reconstruit en Python pur les deux
champs actuellement définis dans ``gridWebProps`` (fragment du .glade
monolithique) : ceci prouve qu'un plugin peut posséder sa propre UI d'édition
sans dépendre du fichier .glade partagé. Le fragment .glade existant
(``pgHostWeb``) n'est PAS retiré à ce stade — ce plugin coexiste avec lui,
il ne le remplace pas encore (cf. étape suivante : brancher Wmain/Whost sur
le PluginRegistry).
"""

from __future__ import annotations

from collections.abc import Callable
from gettext import gettext as _

import gi
from gi.repository import Gio, Gtk
from loguru import logger
from plugin_base import ConnectionPlugin

from widgets import app_logger

__all__ = ["WebPlugin", "WebTab"]

# Widget de console web natif (WebKitGTK) : utilise pour les BMC HTML5
# (iLO/iDRAC/IMM) et toute interface web generique (Proxmox, switches...).
# Degrade gracieusement vers l'ouverture dans le navigateur systeme si le
# typelib WebKit2 est absent (paquet gir1.2-webkit2-4.1 non installe).
_WEBKIT2_OK = False
for _webkit_version in ("4.1", "4.0"):
    try:
        gi.require_version("WebKit2", _webkit_version)
        from gi.repository import WebKit2

        _WEBKIT2_OK = True
        break
    except (ValueError, ImportError) as exc:
        logger.debug("plugin web | WebKit2 {} indisponible : {}", _webkit_version, exc)
        continue


class WebTab(Gtk.Box):
    """Onglet de console web (BMC iLO/iDRAC/IMM HTML5, ou toute interface web).

    Mode natif (préféré) : widget WebKit2.WebView embarqué directement dans
    l'onglet GCM (gir1.2-webkit2-4.1 ou 4.0).
    Mode dégradé (fallback) : bouton ouvrant l'URL dans le navigateur système
    si WebKitGTK est absent.

    Args:
        host (Host): Hôte avec protocol='web'.
            - host.web_url : URL de la console (ex: https://ilo-host/).
            - host.web_ignore_cert : ignorer les erreurs de certificat TLS
              (utile pour les certificats auto-signés des BMC).
    """

    def __init__(self, host):
        """Initialise le panneau Web."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.host = host
        self._webview = None
        self.set_margin_start(16)
        self.set_margin_end(16)
        self.set_margin_top(16)
        self.set_margin_bottom(16)
        self._build_ui()

    def _build_ui(self):
        """Construit la barre d'outils + le WebView natif ou le repli navigateur."""
        h = self.host
        url = (getattr(h, "web_url", "") or "").strip()

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label()
        shown = url or _("URL not configured")
        title.set_markup(f"<b>Web</b> <small>{shown}</small>")
        title.set_xalign(0)
        toolbar.pack_start(title, True, True, 0)

        self._lbl_status = Gtk.Label(label=_("Waiting…"))
        self._lbl_status.get_style_context().add_class("dim-label")
        toolbar.pack_start(self._lbl_status, False, False, 8)

        self._btn_connect = Gtk.Button(label=_("Connect"))
        self._btn_connect.get_style_context().add_class("suggested-action")
        self._btn_connect.connect("clicked", self._on_connect)
        toolbar.pack_start(self._btn_connect, False, False, 0)

        self.pack_start(toolbar, False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 0)

        if _WEBKIT2_OK:
            self._webview = WebKit2.WebView()
            settings = self._webview.get_settings()
            if getattr(h, "web_ignore_cert", True) and hasattr(WebKit2, "TLSErrorsPolicy"):
                # Autorise les certificats auto-signes, courants sur les BMC.
                context = self._webview.get_context()
                if hasattr(context, "set_tls_errors_policy"):
                    context.set_tls_errors_policy(WebKit2.TLSErrorsPolicy.IGNORE)
            settings.set_enable_javascript(True)
            self._webview.set_hexpand(True)
            self._webview.set_vexpand(True)
            self.pack_start(self._webview, True, True, 0)
        else:
            fallback_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            fallback_box.set_valign(Gtk.Align.CENTER)
            msg = Gtk.Label(
                label=_(
                    "WebKitGTK unavailable (gir1.2-webkit2-4.1 package missing).\n"
                    "The web console will open in the system browser."
                )
            )
            msg.set_justify(Gtk.Justification.CENTER)
            fallback_box.pack_start(msg, False, False, 0)
            self.pack_start(fallback_box, True, True, 0)

        self.show_all()

    def _on_connect(self, widget):
        """Charge l'URL configurée (WebView natif) ou l'ouvre dans le navigateur."""
        url = (getattr(self.host, "web_url", "") or "").strip()
        if not url:
            self._set_status(_("Error: URL not configured"))
            return
        app_logger.debug(f"WebTab | ouverture de {url}")
        if self._webview is not None:
            self._set_status(_("Connecting…"))
            self._webview.load_uri(url)
        else:
            try:
                Gio.AppInfo.launch_default_for_uri(url, None)
                self._set_status(_("Opened in the system browser"))
            except Exception as exc:
                app_logger.error(f"WebTab | échec ouverture navigateur : {exc}")
                self._set_status(_("Error: could not open the URL"))

    def _set_status(self, text):
        """Met à jour le label de statut.

        Args:
            text (str): Nouveau statut.
        """
        self._lbl_status.set_text(text)

    def connect_web(self):
        """Charge la console web automatiquement (appelé depuis addTab)."""
        self._on_connect(None)


class WebPlugin(ConnectionPlugin):
    """Plugin pour le protocole ``web`` (console HTML5 embarquée ou navigateur système)."""

    protocol_id = "web"
    display_name = _("Web")
    default_port = 443
    icon_name = "web-browser-symbolic"
    ui_order = 7

    def __init__(self) -> None:
        """Initialise les références de widgets du formulaire Web (peuplées par ``build_edit_page()``)."""
        self._txt_url: Gtk.Entry | None = None
        self._chk_ignore_cert: Gtk.CheckButton | None = None
        self._error_label: Gtk.Label | None = None

    def build_tab(self, host, get_password: Callable[[], str] | None = None) -> Gtk.Widget:
        """Construit l'onglet de session Web (délègue entièrement à ``WebTab``).

        Args:
            host: Instance ``Host`` avec ``protocol == "web"``.
            get_password: Non utilisé par ce protocole (pas d'authentification
                gérée par GCM lui-même — la console web gère sa propre auth).

        Returns:
            Une instance de ``WebTab`` (inchangée).
        """
        logger.debug(f"WebPlugin.build_tab | host={host.name}")
        return WebTab(host)

    def build_edit_page(self) -> Gtk.Widget:
        """Construit la page de champs Edit Host pour le protocole Web.

        Reproduit fidèlement ``gridWebProps`` (URL + case "ignorer les
        erreurs de certificat"), en Python pur.

        Returns:
            Un ``Gtk.Grid`` autonome, prêt à être inséré dans le notebook du
            dialogue Edit Host.
        """
        grid = Gtk.Grid()
        grid.set_row_homogeneous(True)
        grid.set_row_spacing(4)
        grid.set_column_spacing(6)
        grid.set_margin_start(8)
        grid.set_margin_end(8)
        grid.set_margin_top(8)

        lbl_url = Gtk.Label(label=_("URL"))
        lbl_url.set_halign(Gtk.Align.START)
        lbl_url.set_tooltip_text(
            _(
                "BMC web console (iLO, iDRAC, IMM…) or any web interface (Proxmox, switch, TrueNAS…)"
            )
        )
        grid.attach(lbl_url, 0, 0, 1, 1)

        self._txt_url = Gtk.Entry()
        self._txt_url.set_margin_start(10)
        self._txt_url.set_hexpand(True)
        self._txt_url.set_placeholder_text("https://ilo-host.example.org/")
        grid.attach(self._txt_url, 1, 0, 1, 1)

        lbl_cert = Gtk.Label(label=_("Ignore certificate errors"))
        lbl_cert.set_halign(Gtk.Align.START)
        lbl_cert.set_tooltip_text(_("Useful for self-signed certificates on BMC interfaces"))
        grid.attach(lbl_cert, 0, 1, 1, 1)

        self._chk_ignore_cert = Gtk.CheckButton()
        self._chk_ignore_cert.set_margin_start(10)
        self._chk_ignore_cert.set_active(True)
        grid.attach(self._chk_ignore_cert, 1, 1, 1, 1)

        self._error_label = Gtk.Label()
        self._error_label.set_halign(Gtk.Align.START)
        self._error_label.get_style_context().add_class("error")
        self._error_label.hide()
        grid.attach(self._error_label, 1, 2, 1, 1)

        grid.show_all()
        self._error_label.hide()  # show_all() masqué immédiatement après (cf. point C — pattern correct)
        return grid

    def load_host_fields(self, host) -> None:
        """Recharge URL + case à cocher depuis *host*.

        Args:
            host: Instance ``Host`` source.
        """
        if self._txt_url is not None:
            self._txt_url.set_text(getattr(host, "web_url", "") or "")
        if self._chk_ignore_cert is not None:
            self._chk_ignore_cert.set_active(bool(getattr(host, "web_ignore_cert", True)))
        if self._error_label is not None:
            self._error_label.hide()
        logger.debug(
            f"WebPlugin.load_host_fields | host={host.name} url={getattr(host, 'web_url', '')}"
        )

    def save_host_fields(self, host) -> None:
        """Reporte URL + case à cocher dans *host*.

        Args:
            host: Instance ``Host`` à mettre à jour.
        """
        if self._txt_url is not None:
            host.web_url = self._txt_url.get_text().strip()
        if self._chk_ignore_cert is not None:
            host.web_ignore_cert = self._chk_ignore_cert.get_active()
        logger.debug(
            f"WebPlugin.save_host_fields | host={getattr(host, 'name', '?')} url={getattr(host, 'web_url', '')}"
        )

    def host_fields(self) -> list[tuple[str, object]]:
        """Champs ``Host`` propres au protocole Web."""
        return [
            ("web_url", ""),
            ("web_ignore_cert", True),
        ]

    def validate(self) -> list[str]:
        """Vérifie qu'une URL non vide est renseignée.

        Returns:
            Liste d'erreurs (vide si l'URL est renseignée).
        """
        errors: list[str] = []
        url = self._txt_url.get_text().strip() if self._txt_url is not None else ""
        if not url:
            errors.append(_("URL is required for a Web connection."))
            if self._error_label is not None:
                self._error_label.set_text(_("URL is required."))
                self._error_label.show()
        elif self._error_label is not None:
            self._error_label.hide()
        return errors


def get_plugin() -> WebPlugin:
    """Point d'entree standard utilise par PluginRegistry.autoload().

    Returns:
        Une instance de WebPlugin, prete a etre enregistree.
    """
    return WebPlugin()
