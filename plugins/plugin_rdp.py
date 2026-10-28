# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin de connexion RDP (GtkFrdp.Display embarqué).

Quatrième plugin implémenté après Web, IPMI SOL et Serial.

Reproduit fidèlement ``gridRdpProps``, y compris le comportement condition-
nel de la boîte "géométrie personnalisée" (visible seulement si la
géométrie choisie est "custom") — avec, dès le départ, le correctif déjà
appliqué au chemin historique (cf. audit ``show_all()``/point C) : la boîte
est explicitement masquée pour toute géométrie non-custom, jamais laissée
dans un état hérité de l'affichage précédent.

``FreeRdpTab`` (ci-dessous) vit désormais dans ce module, et non plus dans
``widgets.py`` : elle ne dépendait déjà d'aucun état de ``gnome_connection_
manager`` (pas de ``wMain``/``conf``), donc aucune interface supplémentaire
n'était nécessaire pour ce déplacement — seul un plugin l'utilisait de
toute façon (cf. audit widgets.py/utils.py).
"""

from __future__ import annotations

import gi
from loguru import logger

gi.require_version("GtkFrdp", "0.2")

from collections.abc import Callable
from gettext import gettext as _

from gi.repository import Gdk, GLib, Gtk, GtkFrdp
from plugin_base import ConnectionPlugin

from widgets import app_logger, build_remote_desktop_context_menu, mount_iso_temp, umount_iso_temp

__all__ = ["FreeRdpTab", "RdpPlugin"]


class FreeRdpTab(Gtk.Box):
    """Widget affiché dans le Gtk.Notebook pour les connexions RDP.

    Widget GtkFrdp.Display embarqué directement dans l'onglet GCM, Seule solution RDP de GCM (pas de fallback) :
    nécessite que gtk-frdp (namespace GObject Introspection GtkFrdp 0.2)
    soit installé sur le système.
    """

    def __init__(self, host, get_password_fn):
        """Initialise le panneau RDP embarqué.

        Args:
            host (Host): Objet Host GCM avec protocol='rdp'.
            get_password_fn (callable): Fonction retournant le mot de passe déchiffré.
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.host = host
        self._get_password = get_password_fn
        app_logger.debug(f"FreeRdpTab | init | host={host.host}")
        self._frdp_display = None
        self._iso_loop_device = None  # peripherique loop si un ISO est monte
        self._build_ui()

    def _build_ui(self):
        """Construit la barre d'outils + le widget GtkFrdp.Display embarqué."""
        h = self.host
        port = getattr(h, "port", 3389) or 3389

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)
        toolbar.set_margin_top(4)
        toolbar.set_margin_bottom(4)

        title = Gtk.Label()
        title.set_markup(f"<b>RDP</b> <small>{h.user}@{h.host}:{port}</small>")
        title.set_xalign(0)
        toolbar.pack_start(title, True, True, 0)

        self._lbl_status = Gtk.Label(label=_("Waiting…"))
        self._lbl_status.get_style_context().add_class("dim-label")
        toolbar.pack_start(self._lbl_status, False, False, 8)

        # Connect/Disconnect/Touches speciales : ne sont plus des boutons de
        # la barre d'outils, mais des entrees du menu contextuel (clic droit
        # sur l'onglet/titre, voir build_context_menu ci-dessous). Les
        # boutons sont conserves hors-toolbar comme simples porteurs d'etat
        # (sensitivity) et de gestionnaire "clicked" reutilises par le menu.
        self._btn_connect = Gtk.Button(label=_("Connect"))
        self._btn_connect.get_style_context().add_class("suggested-action")
        self._btn_connect.connect("clicked", self._on_connect)

        self._btn_disconnect = Gtk.Button(label=_("Disconnect"))
        self._btn_disconnect.set_sensitive(False)
        self._btn_disconnect.connect("clicked", self._on_disconnect)

        self.pack_start(toolbar, False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 0)

        # Widget GtkFrdp.Display : GtkWidget natif (sous-classe de
        # GtkDrawingArea).
        app_logger.debug(f"FreeRdpTab | creating GtkFrdp.Display for host {h.host}")
        self._frdp_display = GtkFrdp.Display.new()
        self._frdp_display.set_hexpand(True)
        self._frdp_display.set_vexpand(True)
        # Redimensionnement dynamique du bureau distant avec la fenêtre GCM.
        # IMPORTANT : allow-resize et scaling sont mutuellement exclusifs côté
        # gtk-frdp (frdp_session_configure_event ne calcule priv->scale que
        # dans la branche "else" de allow_resize). Les activer tous les deux
        # laisse priv->scale à 0, ce qui casse cairo_scale() (matrice non
        # inversible) et gdk_cursor_new_from_surface() (surface curseur 0x0).
        self._frdp_display.set_property("allow-resize", True)
        self._frdp_display.set_property("scaling", False)

        self._frdp_display.connect("rdp-connected", self._on_rdp_connected)
        self._frdp_display.connect("rdp-disconnected", self._on_rdp_disconnected)
        self._frdp_display.connect("rdp-error", self._on_rdp_error)
        self._frdp_display.connect("rdp-auth-failure", self._on_rdp_auth_failure)
        self._frdp_display.connect("rdp-needs-authentication", self._on_rdp_needs_authentication)
        self._frdp_display.connect(
            "rdp-needs-certificate-verification", self._on_rdp_needs_certificate_verification
        )
        # Glisser-deposer de fichiers : les URI recues sont placees sur le
        # presse-papiers GTK (text/uri-list), ce qui declenche automatiquement
        # l'annonce FileGroupDescriptorW existante du canal CLIPRDR (voir
        # frdp_display_offer_dropped_files). Un Ctrl+V cote distant termine le transfert.
        self._frdp_display.drag_dest_set(
            Gtk.DestDefaults.ALL, [Gtk.TargetEntry.new("text/uri-list", 0, 0)], Gdk.DragAction.COPY
        )
        self._frdp_display.connect("drag-data-received", self._on_rdp_drag_data_received)
        app_logger.debug(f"FreeRdpTab | adding GtkFrdp.Display to FreeRdpTab for host {h.host}")
        # gtk-frdp calcule la taille disponible via gtk_widget_get_ancestor(...,
        # GTK_TYPE_SCROLLED_WINDOW) dans frdp_session_configure_event(). Sans cet
        # ancêtre, gtk_widget_get_allocated_width/height(NULL) déclenchent des
        # assertions GTK-CRITICAL et retournent 0.
        frdp_scrolled = Gtk.ScrolledWindow()
        frdp_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        frdp_scrolled.set_hexpand(True)
        frdp_scrolled.set_vexpand(True)
        frdp_scrolled.add(self._frdp_display)
        self.pack_start(frdp_scrolled, True, True, 0)

        self.show_all()

    def _on_rdp_connected(self, display):
        """Callback gtk-frdp : session RDP pleinement établie."""
        GLib.idle_add(self._set_status, _("Connect"))
        GLib.idle_add(self._btn_connect.set_sensitive, False)
        GLib.idle_add(self._btn_disconnect.set_sensitive, True)

    def _on_rdp_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        """Callback GTK : fichiers deposes sur la fenetre RDP (glisser-deposer).

        Args:
            widget (GtkFrdp.Display): Widget cible du depot.
            drag_context (Gdk.DragContext): Contexte du glisser-deposer.
            x (int): Position X du depot.
            y (int): Position Y du depot.
            data (Gtk.SelectionData): Donnees deposees (URIs).
            info (int): Identifiant de cible negocie.
            time (int): Horodatage de l'evenement.
        """
        uris = data.get_uris()
        app_logger.debug(
            f"RDP native | drag-data-received pour host {self.host.name} | uris={uris} | rdp_share_clipboard="
            f"{getattr(self.host, 'rdp_share_clipboard', True)}",
        )
        if uris:
            try:
                self._frdp_display.offer_dropped_files(uris)
                app_logger.debug(
                    f"RDP native | offer_dropped_files() appele avec succes pour host {self.host.name} ({len(uris)}"
                    f" fichier(s)) — faites Ctrl+V cote distant pour terminer le transfert",
                )
            except AttributeError as exc:
                app_logger.warning(
                    f"RDP native | offer_dropped_files indisponible (gtk-frdp trop ancien ?) : {exc}"
                )
        Gtk.drag_finish(drag_context, bool(uris), False, time)

    def _send_special_keys(self, key_names):
        """Envoie une combinaison de touches (Ctrl+Alt+Suppr, Alt+Tab…) à la session RDP.

        Args:
            key_names (list[str]): noms de touches Gdk (ex: ["Control_L", "Alt_L", "Delete"]).
        """
        keyvals = [Gdk.keyval_from_name(name) for name in key_names]
        try:
            self._frdp_display.send_key_combo(keyvals)
        except AttributeError as exc:
            app_logger.warning(
                f"RDP native | send_key_combo indisponible (gtk-frdp trop ancien ?) : {exc}"
            )

    def _on_rdp_disconnected(self, display):
        """Callback gtk-frdp : session terminée (normale ou erreur)."""
        GLib.idle_add(self._set_status, _("Session ended"))
        GLib.idle_add(self._btn_connect.set_sensitive, True)
        GLib.idle_add(self._btn_disconnect.set_sensitive, False)
        if self._iso_loop_device:
            umount_iso_temp(self._iso_loop_device)
            self._iso_loop_device = None

    def _on_rdp_error(self, display, message):
        """Callback gtk-frdp : erreur de connexion/exécution.

        Args:
            display (GtkFrdp.Display): Widget émetteur.
            message (str): Message d'erreur FreeRDP.
        """
        app_logger.warning(f"RDP native | error for host {self.host.name}: {message}")
        GLib.idle_add(self._set_status, _("Error: {msg}").format(msg=message))

    def _on_rdp_auth_failure(self, display, message):
        """Callback gtk-frdp : échec d'authentification.

        Args:
            display (GtkFrdp.Display): Widget émetteur.
            message (str): Message d'erreur retourné par le serveur.
        """
        app_logger.warning(f"RDP native | auth failure for host {self.host.name}: {message}")
        GLib.idle_add(self._set_status, _("Authentication failed"))
        GLib.idle_add(self._btn_connect.set_sensitive, True)
        GLib.idle_add(self._btn_disconnect.set_sensitive, False)

    def _on_rdp_needs_authentication(self, display):
        """Callback gtk-frdp : le serveur demande des identifiants.

        Appelé si username/password n'ont pas été fixés à l'avance (ou ont
        été refusés). Renseigne les propriétés depuis les infos GCM connues.

        Args:
            display (GtkFrdp.Display): Widget émetteur.
        """
        h = self.host
        user = h.user or ""
        domain = ""
        if "\\" in user:
            domain, user = user.split("\\", 1)
        app_logger.debug(
            f"RDP native | needs authentication for host {self.host.name} (user={user}, domain={domain})"
        )
        password = self._get_password() or ""
        # IMPORTANT : authenticate_finish() doit être appelé pour débloquer
        # la boucle d'attente côté C (frdp_display_authenticate). Sans cet
        # appel, la connexion reste figée indéfiniment après la négociation
        # (voir priv->awaiting_authentication dans frdp-display.c).
        display.authenticate_finish(user, password, domain)

    def _on_rdp_needs_certificate_verification(
        self, display, host, port, common_name, subject, issuer, fingerprint, flags
    ):
        """Callback gtk-frdp : confirmation du certificat TLS distant requise.

        Accepte automatiquement le certificat (équivalent du /cert:ignore
        utilisé par l'ancien mode XEmbed). À durcir si une vérification
        manuelle est souhaitée côté GCM.

        Args:
            display (GtkFrdp.Display): Widget émetteur.
            host (str): Hôte distant.
            port (int): Port distant.
            common_name (str): CN du certificat.
            subject (str): Sujet du certificat.
            issuer (str): Émetteur du certificat.
            fingerprint (str): Empreinte du certificat.
            flags (int): Indicateurs FreeRDP associés.
        """
        app_logger.debug(
            f"RDP native | certificate accepted for {host}:{port} (fingerprint={fingerprint})"
        )
        # 1 = accepter pour cette session (voir verify_ex_finish dans gtk-frdp)
        display.certificate_verify_ex_finish(1)

    def _on_connect(self, widget):
        """Lance la connexion RDP via gtk-frdp.

        Args:
            widget (Gtk.Button): Bouton déclencheur (peut être None).
        """
        h = self.host
        port = int(getattr(h, "port", 3389) or 3389)
        app_logger.debug(f"RDP native | connect button clicked for host={h.host} port={port}")
        # Empêche les doubles/triples clics de relancer open_host() en concurrence
        # sur un widget déjà en cours de connexion (corrompt le state machine FreeRDP).
        self._btn_connect.set_sensitive(False)
        user = h.user or ""
        domain = ""
        if "\\" in user:
            domain, user = user.split("\\", 1)

        self._frdp_display.set_property("username", user)
        self._frdp_display.set_property("password", self._get_password() or "")
        if domain:
            self._frdp_display.set_property("domain", domain)
        # Partage presse-papiers (texte + fichiers, canal CLIPRDR) : case a
        # cocher "Partager le presse-papiers" de l'onglet RDP du dialogue d'edition.
        # set_property() sur une propriete GObject inexistante leve une exception
        # Python fatale pour CETTE fonction (contrairement a une exception dans un
        # signal handler GTK, ici on est appele en direct depuis connect_rdp()) :
        # si gtk-frdp deploye est plus ancien que celui compile ici (proprietes
        # redirect-clipboard/shared-folder-path absentes), open_host() plus bas
        # ne serait JAMAIS appele. On protege donc chaque nouvelle propriete.
        try:
            self._frdp_display.set_property(
                "redirect-clipboard", getattr(h, "rdp_share_clipboard", True)
            )
        except TypeError as exc:
            app_logger.warning(
                f"RDP native | propriete 'redirect-clipboard' indisponible (gtk-frdp trop ancien ?) : {exc}",
            )

        # Partage dossier/ISO (redirection RDPDR "drive"). Un fichier .iso est
        # d'abord monte localement (udisksctl) puis son point de montage est
        # redirige comme un dossier classique.
        folder_path = ""
        if getattr(h, "rdp_share_folder", False):
            raw_path = (getattr(h, "rdp_shared_folder_path", "") or "").strip()
            if raw_path.lower().endswith(".iso"):
                mounted = mount_iso_temp(raw_path)
                if mounted:
                    folder_path, self._iso_loop_device = mounted
                else:
                    self._set_status(_("Error: could not mount the ISO"))
            else:
                folder_path = raw_path
        try:
            self._frdp_display.set_property("shared-folder-path", folder_path)
        except TypeError as exc:
            app_logger.warning(
                f"RDP native | propriete 'shared-folder-path' indisponible (gtk-frdp trop ancien ?) : {exc}",
            )

        app_logger.debug(f"RDP native | open_host host={h.host} port={port}")
        self._set_status(_("Connecting…"))
        self._frdp_display.open_host(h.host, port)

    def _on_disconnect(self, widget):
        """Termine la session RDP.

        Args:
            widget (Gtk.Button): Bouton déclencheur.
        """
        app_logger.debug(f"RDP native | disconnect button clicked for host={self.host.host}")
        self._frdp_display.close()
        if self._iso_loop_device:
            umount_iso_temp(self._iso_loop_device)
            self._iso_loop_device = None

    def connect_rdp(self):
        """Lance la connexion RDP automatiquement (appelé depuis addTab)."""
        self._on_connect(None)

    def _set_status(self, text):
        """Met à jour le label de statut.

        Args:
            text (str): Nouveau statut.
        """
        self._lbl_status.set_text(text)

    def build_context_menu(self):
        """Construit le menu contextuel (clic droit sur l'onglet) de cet onglet RDP.

        Returns:
            Gtk.Menu: Connect / Disconnect / Touches spéciales.
        """
        return build_remote_desktop_context_menu(
            self._btn_connect, self._btn_disconnect, self._send_special_keys
        )


_GEOMETRY_ITEMS = [
    ("fullscreen", _("Fullscreen")),
    ("1920x1080", "1920×1080"),
    ("1280x1024", "1280×1024"),
    ("1024x768", "1024×768"),
    ("800x600", "800×600"),
    ("custom", _("Custom…")),
]


def _bool_from_str(value: object) -> bool:
    """Convertit une valeur de config texte ("true"/"1"/"yes") en bool.

    Args:
        value: Valeur brute (souvent une chaîne issue de gcm.conf).
    """
    return str(value).lower() in ("true", "1", "yes")


class RdpPlugin(ConnectionPlugin):
    """Plugin pour le protocole ``rdp`` (Remote Desktop Protocol via gtk-frdp)."""

    protocol_id = "rdp"
    display_name = _("RDP")
    default_port = 3389
    icon_name = "video-display-symbolic"
    ui_order = 2
    self_scrolling = True

    def __init__(self) -> None:
        """Initialise les références de widgets du formulaire RDP (peuplées par ``build_edit_page()``)."""
        self._txt_domain: Gtk.Entry | None = None
        self._cmb_geometry: Gtk.ComboBoxText | None = None
        self._box_custom_geo: Gtk.Box | None = None
        self._txt_width: Gtk.Entry | None = None
        self._txt_height: Gtk.Entry | None = None
        self._chk_cert_ignore: Gtk.CheckButton | None = None
        self._chk_dyn_res: Gtk.CheckButton | None = None
        self._txt_remote_app: Gtk.Entry | None = None
        self._chk_share_clipboard: Gtk.CheckButton | None = None
        self._chk_share_folder: Gtk.CheckButton | None = None
        self._txt_shared_folder_path: Gtk.Entry | None = None

    def build_tab(self, host, get_password: Callable[[], str] | None = None) -> Gtk.Widget:
        """Construit l'onglet de session RDP (délègue à ``FreeRdpTab``).

        Args:
            host: Instance ``Host`` avec ``protocol == "rdp"``.
            get_password: Callable retournant le mot de passe déchiffré,
                requis par ``FreeRdpTab``.

        Returns:
            Une instance de ``FreeRdpTab`` (inchangée).
        """
        logger.debug(f"RdpPlugin.build_tab | host={host.name}")
        return FreeRdpTab(host, get_password)

    def build_edit_page(self) -> Gtk.Widget:
        """Construit la page de champs Edit Host pour RDP.

        Returns:
            Un ``Gtk.Grid`` autonome reproduisant ``gridRdpProps``.
        """
        grid = Gtk.Grid()
        grid.set_row_spacing(4)
        grid.set_column_spacing(6)
        grid.set_margin_start(8)
        grid.set_margin_end(8)
        grid.set_margin_top(8)
        row = 0

        def add_row(label_text, widget):
            nonlocal row
            lbl = Gtk.Label(label=label_text)
            lbl.set_halign(Gtk.Align.START)
            grid.attach(lbl, 0, row, 1, 1)
            widget.set_margin_start(10)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
            row += 1

        self._txt_domain = Gtk.Entry()
        add_row(_("Domain"), self._txt_domain)

        self._cmb_geometry = Gtk.ComboBoxText()
        for item_id, label in _GEOMETRY_ITEMS:
            self._cmb_geometry.append(item_id, label)
        self._cmb_geometry.set_active_id("fullscreen")
        self._cmb_geometry.connect("changed", self._on_geometry_changed)
        add_row(_("Geometry"), self._cmb_geometry)

        self._box_custom_geo = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._box_custom_geo.set_margin_start(10)
        self._txt_width = Gtk.Entry()
        self._txt_width.set_placeholder_text(_("Width"))
        self._txt_width.set_width_chars(6)
        self._txt_height = Gtk.Entry()
        self._txt_height.set_placeholder_text(_("Height"))
        self._txt_height.set_width_chars(6)
        self._box_custom_geo.pack_start(self._txt_width, False, False, 0)
        self._box_custom_geo.pack_start(Gtk.Label(label="×"), False, False, 0)
        self._box_custom_geo.pack_start(self._txt_height, False, False, 0)
        self._box_custom_geo.set_no_show_all(
            True
        )  # cf. point C : jamais montré par un show_all() ancestral
        self._box_custom_geo.hide()
        grid.attach(self._box_custom_geo, 1, row, 1, 1)
        row += 1

        self._chk_cert_ignore = Gtk.CheckButton(label=_("Ignore certificate errors"))
        grid.attach(self._chk_cert_ignore, 1, row, 1, 1)
        row += 1

        self._chk_dyn_res = Gtk.CheckButton(label=_("Dynamic resolution"))
        grid.attach(self._chk_dyn_res, 1, row, 1, 1)
        row += 1

        self._txt_remote_app = Gtk.Entry()
        add_row(_("RemoteApp"), self._txt_remote_app)

        self._chk_share_clipboard = Gtk.CheckButton(label=_("Share clipboard"))
        self._chk_share_clipboard.set_active(True)
        grid.attach(self._chk_share_clipboard, 1, row, 1, 1)
        row += 1

        self._chk_share_folder = Gtk.CheckButton(label=_("Share folder"))
        grid.attach(self._chk_share_folder, 1, row, 1, 1)
        row += 1

        self._txt_shared_folder_path = Gtk.Entry()
        add_row(_("Shared folder path"), self._txt_shared_folder_path)

        grid.show_all()
        self._box_custom_geo.hide()  # show_all() ci-dessus masqué juste après (pattern point C)
        return grid

    def _on_geometry_changed(self, cmb: Gtk.ComboBoxText) -> None:
        """Affiche/masque la boîte largeur×hauteur selon la géométrie choisie.

        Args:
            cmb: Combo ``rdpCmbGeometry`` qui vient de changer.
        """
        is_custom = cmb.get_active_id() == "custom"
        if self._box_custom_geo is not None:
            if is_custom:
                self._box_custom_geo.show()
            else:
                self._box_custom_geo.hide()

    def load_host_fields(self, host) -> None:
        """Recharge tous les champs RDP depuis *host*.

        Args:
            host: Instance ``Host`` source.
        """
        if self._txt_domain is not None:
            self._txt_domain.set_text(getattr(host, "rdp_domain", "") or "")
        geo = getattr(host, "rdp_geometry", "fullscreen") or "fullscreen"
        if self._cmb_geometry is not None:
            self._cmb_geometry.set_active_id(geo)
        # Bug corrigé (cf. point C) : masquer explicitement pour toute
        # géométrie non-custom, jamais laissé à l'état précédent.
        if self._box_custom_geo is not None:
            if geo == "custom":
                self._box_custom_geo.show()
                if self._txt_width is not None:
                    self._txt_width.set_text(getattr(host, "rdp_width", "") or "")
                if self._txt_height is not None:
                    self._txt_height.set_text(getattr(host, "rdp_height", "") or "")
            else:
                self._box_custom_geo.hide()
        if self._chk_cert_ignore is not None:
            self._chk_cert_ignore.set_active(
                _bool_from_str(getattr(host, "rdp_cert_ignore", False))
            )
        if self._chk_dyn_res is not None:
            self._chk_dyn_res.set_active(_bool_from_str(getattr(host, "rdp_dyn_res", False)))
        if self._txt_remote_app is not None:
            self._txt_remote_app.set_text(getattr(host, "rdp_remote_app", "") or "")
        if self._chk_share_clipboard is not None:
            self._chk_share_clipboard.set_active(
                _bool_from_str(getattr(host, "rdp_share_clipboard", True))
            )
        if self._chk_share_folder is not None:
            self._chk_share_folder.set_active(
                _bool_from_str(getattr(host, "rdp_share_folder", False))
            )
        if self._txt_shared_folder_path is not None:
            self._txt_shared_folder_path.set_text(
                getattr(host, "rdp_shared_folder_path", "") or ""
            )
        logger.debug(f"RdpPlugin.load_host_fields | host={getattr(host, 'name', '?')} geo={geo}")

    def save_host_fields(self, host) -> None:
        """Reporte tous les champs RDP dans *host*.

        Args:
            host: Instance ``Host`` à mettre à jour.
        """
        host.rdp_domain = self._txt_domain.get_text().strip() if self._txt_domain else ""
        host.rdp_geometry = (
            self._cmb_geometry.get_active_id() if self._cmb_geometry else "fullscreen"
        )
        host.rdp_width = self._txt_width.get_text().strip() if self._txt_width else ""
        host.rdp_height = self._txt_height.get_text().strip() if self._txt_height else ""
        host.rdp_cert_ignore = (
            self._chk_cert_ignore.get_active() if self._chk_cert_ignore else False
        )
        host.rdp_dyn_res = self._chk_dyn_res.get_active() if self._chk_dyn_res else False
        host.rdp_remote_app = (
            self._txt_remote_app.get_text().strip() if self._txt_remote_app else ""
        )
        host.rdp_share_clipboard = (
            self._chk_share_clipboard.get_active() if self._chk_share_clipboard else True
        )
        host.rdp_share_folder = (
            self._chk_share_folder.get_active() if self._chk_share_folder else False
        )
        host.rdp_shared_folder_path = (
            self._txt_shared_folder_path.get_text().strip() if self._txt_shared_folder_path else ""
        )
        logger.debug(f"RdpPlugin.save_host_fields | host={getattr(host, 'name', '?')}")

    def host_fields(self) -> list[tuple[str, object]]:
        """Champs ``Host`` propres au protocole RDP."""
        return [
            ("rdp_domain", ""),
            ("rdp_geometry", "fullscreen"),
            ("rdp_width", ""),
            ("rdp_height", ""),
            ("rdp_cert_ignore", False),
            ("rdp_dyn_res", False),
            ("rdp_remote_app", ""),
            ("rdp_share_clipboard", True),
            ("rdp_share_folder", False),
            ("rdp_shared_folder_path", ""),
        ]


def get_plugin() -> RdpPlugin:
    """Point d'entree standard utilise par PluginRegistry.autoload().

    Returns:
        Une instance de RdpPlugin, prete a etre enregistree.
    """
    return RdpPlugin()
