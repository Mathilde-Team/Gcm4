# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin de connexion VNC (GtkVnc.Display natif, ou fallback subprocess).

Cinquième plugin implémenté après Web, IPMI SOL, Serial et RDP.

Reproduit ``gridVncProps``. Bug pré-existant noté en portant cette logique
(non corrigé dans l'ancien code, qui reste le fallback du flag) : le
chargement historique du champ ``vncTxtDisplay`` teste
``if self.vncTxtDisplay != "":`` — une comparaison widget-vs-chaîne
toujours vraie (un ``Gtk.Entry`` n'est jamais égal à ``""``), ce qui rend le
garde-fou inopérant. Le plugin utilise ici un test ``is not None`` correct.

``VncTab`` (ci-dessous) vit désormais dans ce module, et non plus dans
``widgets.py`` : elle ne dépendait déjà d'aucun état de
``gnome_connection_manager`` (pas de ``wMain``/``conf``), cf. audit
widgets.py/utils.py.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from gettext import gettext as _

import gi
from Cryptodome.Cipher import DES
from loguru import logger

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk
from plugin_base import ConnectionPlugin

from widgets import app_logger, build_remote_desktop_context_menu

__all__ = ["VncPlugin", "VncTab"]

# Widget VNC natif (gtk-vnc) : remplace XEmbed/subprocess par un GtkWidget
# directement embarque, sans fenetre externe. Degrade gracieusement vers
# l'ancien mode subprocess (vncviewer/vinagre/remmina) si absent du systeme.
try:
    gi.require_version("GtkVnc", "2.0")
    gi.require_version("GVnc", "1.0")
    from gi.repository import GtkVnc, GVnc

    _GTKVNC_OK = True
except (ValueError, ImportError) as exc:
    logger.debug("plugin vnc | GtkVnc indisponible : {}", exc)
    _GTKVNC_OK = False

VNC_BIN = (
    shutil.which("vncviewer")
    or shutil.which("tigervnc")
    or shutil.which("xtightvncviewer")
    or shutil.which("xvnc4viewer")
    or shutil.which("vinagre")
    or shutil.which("remmina")
    or "vncviewer"
)
if _GTKVNC_OK:
    app_logger.debug("VncTab | gtk-vnc disponible | widget natif GtkVnc.Display")
else:
    app_logger.warning(
        f"VncTab | gtk-vnc indisponible (gir1.2-gtk-vnc-2.0 manquant) | fallback subprocess {VNC_BIN}"
    )


class VncTab(Gtk.Box):
    """Widget affiche dans le Gtk.Notebook pour les connexions VNC.

    Mode natif (prefere) : widget GtkVnc.Display embarque directement dans
    l'onglet GCM, sans fenetre externe ni XEmbed (gir1.2-gtk-vnc-2.0).
    Mode degrade (fallback) : ancien comportement subprocess
    vncviewer/vinagre/remmina en fenetre externe, conserve a l'identique
    si gtk-vnc est absent du systeme.
    """

    def __init__(self, host, get_password_fn):
        """Initialise le panneau VNC.

        Args:
            host (Host): Objet Host GCM avec protocol='vnc'.
            get_password_fn (callable): Fonction retournant le mot de passe dechiffre.
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.host = host
        self._get_password = get_password_fn
        app_logger.debug(f"VncTab | init | mode={'gtk-vnc' if _GTKVNC_OK else 'subprocess'}")
        self._proc = None  # mode fallback uniquement
        # Fichier passwd RFB du viewer (fallback TigerVNC) : unique par
        # connexion (mkstemp, 0600), supprime a la fin du processus.
        self._passwd_path = None
        self._vnc_display = None  # mode gtk-vnc uniquement
        self.set_margin_start(16)
        self.set_margin_end(16)
        self.set_margin_top(16)
        self.set_margin_bottom(16)
        self._build_ui()

    def _build_ui(self):
        """Construit l'interface du panneau VNC (gtk-vnc natif ou fallback)."""
        if _GTKVNC_OK:
            self._build_ui_native()
        else:
            self._build_ui_legacy()

    def _build_ui_native(self):
        """Construit la barre d'outils + le widget GtkVnc.Display embarque."""
        h = self.host
        port = getattr(h, "port", "5900") or "5900"

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label()
        title.set_markup(f"<b>VNC</b> <small>{h.host}:{port}</small>")
        title.set_xalign(0)
        toolbar.pack_start(title, True, True, 0)

        self._lbl_status = Gtk.Label(label=_("Waiting…"))
        self._lbl_status.get_style_context().add_class("dim-label")
        toolbar.pack_start(self._lbl_status, False, False, 8)

        # Connect/Disconnect/Touches speciales : deplaces dans le menu
        # contextuel (clic droit sur l'onglet/titre, voir build_context_menu).
        # Boutons conserves hors-toolbar comme porteurs d'etat/gestionnaire.
        self._btn_connect = Gtk.Button(label=_("Connect"))
        self._btn_connect.get_style_context().add_class("suggested-action")
        self._btn_connect.connect("clicked", self._on_connect)

        self._btn_disconnect = Gtk.Button(label=_("Disconnect"))
        self._btn_disconnect.set_sensitive(False)
        self._btn_disconnect.connect("clicked", self._on_disconnect)

        self.pack_start(toolbar, False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 0)

        # Widget GtkVnc.Display : GtkWidget natif, pas de Gtk.Socket/XID requis
        self._vnc_display = GtkVnc.Display()
        # Clavier/souris : grab natif gtk-vnc (capture complete des entrees
        # locales et retransmission au serveur RFB des que le widget a le focus).
        self._vnc_display.set_keyboard_grab(True)
        self._vnc_display.set_pointer_grab(True)
        self._vnc_display.set_scaling(True)
        self._vnc_display.set_hexpand(True)
        self._vnc_display.set_vexpand(True)
        self._vnc_display.set_can_focus(True)
        self._vnc_display.connect("vnc-auth-credential", self._on_auth_credential)
        self._vnc_display.connect("vnc-connected", self._on_vnc_connected)
        self._vnc_display.connect("vnc-initialized", self._on_vnc_initialized)
        self._vnc_display.connect("vnc-disconnected", self._on_vnc_disconnected)
        self._vnc_display.connect("vnc-auth-failure", self._on_vnc_auth_failure)
        # Presse-papiers RFB (texte uniquement : le protocole VNC ne supporte
        # pas le transfert de fichiers, contrairement a RDP/CLIPRDR).
        # - distant -> local : signal natif gtk-vnc, pousse dans le presse-papiers GTK.
        # - local -> distant : renvoye au serveur des que la fenetre reprend le focus
        #   (meme comportement que TigerVNC/RealVNC), pret pour un Ctrl+V cote serveur.
        self._vnc_display.connect("vnc-server-cut-text", self._on_vnc_server_cut_text)
        self._vnc_display.connect("focus-in-event", self._on_vnc_focus_in)
        self.pack_start(self._vnc_display, True, True, 0)

        self.show_all()

    def _build_ui_legacy(self):
        """Construit l'interface fallback (ancien comportement subprocess)."""
        h = self.host
        port = getattr(h, "port", "5900") or "5900"
        title = Gtk.Label()
        title.set_markup(f"<b>VNC — {h.name}</b><small>   {h.host}:{port}</small>")
        title.set_xalign(0)
        self.pack_start(title, False, False, 0)
        if h.description:
            d = Gtk.Label(label=h.description)
            d.set_xalign(0)
            d.get_style_context().add_class("dim-label")
            self.pack_start(d, False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 4)
        self._lbl_status = Gtk.Label(label=_("Waiting…"))
        self._lbl_status.set_xalign(0)
        self.pack_start(self._lbl_status, False, False, 0)
        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._btn_connect = Gtk.Button(label=_("Connect"))
        self._btn_connect.get_style_context().add_class("suggested-action")
        self._btn_connect.connect("clicked", self._on_connect)
        hb.pack_start(self._btn_connect, False, False, 0)
        self._btn_disconnect = Gtk.Button(label=_("Disconnect"))
        self._btn_disconnect.set_sensitive(False)
        self._btn_disconnect.connect("clicked", self._on_disconnect)
        hb.pack_start(self._btn_disconnect, False, False, 0)
        self.pack_start(hb, False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 4)
        hb2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hb2.pack_start(Gtk.Label(label=_("xfreerdp options:")), False, False, 0)
        self._entry_opts = Gtk.Entry()
        self._entry_opts.set_text(getattr(h, "extra_params", "") or "")
        self._entry_opts.set_tooltip_text(
            _("Additional vncviewer parameters\nE.g.: {example}").format(
                example="-FullScreen -FullColour -CompressLevel 6"
            )
        )
        hb2.pack_start(self._entry_opts, True, True, 0)
        self.pack_start(hb2, False, False, 0)
        # Binaire détecté
        lbl_bin = Gtk.Label()
        lbl_bin.set_markup(f"<small><i>Binaire détecté : {VNC_BIN}</i></small>")
        lbl_bin.set_xalign(0)
        self.pack_start(lbl_bin, False, False, 0)
        self.show_all()

    # ── Mode natif (gtk-vnc) ─────────────────────────────────────────────

    def _effective_host_port(self):
        """Resout (host, port_str) en tenant compte de host.vnc_display.

        Returns:
            tuple[str, str]: Adresse et port effectifs sous forme de chaines.
        """
        h = self.host
        port = str(getattr(h, "port", "5900") or "5900")
        display = (getattr(h, "vnc_display", "") or "").strip()
        if display:
            try:
                port = str(5900 + int(display))
            except ValueError as exc:
                logger.debug("plugin vnc | numéro de display invalide {!r} : {}", display, exc)
        return h.host, port

    def _on_auth_credential(self, display, cred_list):
        """Repond aux demandes d'authentification gtk-vnc (signal natif).

        Args:
            display (GtkVnc.Display): Widget emetteur du signal.
            cred_list (list): Liste de GVnc.ConnectionCredential demandes.
        """
        h = self.host
        for i in range(cred_list.n_values):
            cred = cred_list.get_nth(i)
            if cred == GVnc.ConnectionCredential.PASSWORD:
                pwd = self._get_password() or ""
                app_logger.debug(f"VNC native | credential PASSWORD for host {h.name}")
                display.set_credential(GVnc.ConnectionCredential.PASSWORD, pwd)
            elif cred == GVnc.ConnectionCredential.USERNAME:
                display.set_credential(GVnc.ConnectionCredential.USERNAME, h.user or "")
            elif cred == GVnc.ConnectionCredential.CLIENTNAME:
                display.set_credential(GVnc.ConnectionCredential.CLIENTNAME, "gcm")

    def _on_vnc_connected(self, display):
        """Callback gtk-vnc : connexion TCP/RFB etablie (avant negociation)."""
        GLib.idle_add(self._set_status, _("Connecting…"))

    def _on_vnc_initialized(self, display):
        """Callback gtk-vnc : session RFB pleinement initialisee."""
        GLib.idle_add(self._set_status, _("Connect"))
        GLib.idle_add(self._btn_connect.set_sensitive, False)
        GLib.idle_add(self._btn_disconnect.set_sensitive, True)

    def _on_vnc_disconnected(self, display):
        """Callback gtk-vnc : session terminee (normale ou erreur)."""
        GLib.idle_add(self._set_status, _("Session ended"))
        GLib.idle_add(self._btn_connect.set_sensitive, True)
        GLib.idle_add(self._btn_disconnect.set_sensitive, False)

    def _on_vnc_auth_failure(self, display, message):
        """Callback gtk-vnc : echec d'authentification RFB.

        Args:
            display (GtkVnc.Display): Widget emetteur.
            message (str): Message d'erreur retourne par le serveur.
        """
        app_logger.warning(f"VNC native | auth failure for host {self.host.name}: {message}")
        GLib.idle_add(self._set_status, _("Authentication failed"))

    def _send_special_keys(self, key_names):
        """Envoie une combinaison de touches (Ctrl+Alt+Suppr, Alt+Tab…) à la session VNC.

        Args:
            key_names (list[str]): noms de touches Gdk (ex: ["Control_L", "Alt_L", "Delete"]).
        """
        keyvals = [Gdk.keyval_from_name(name) for name in key_names]
        try:
            self._vnc_display.send_keys_ex(keyvals, GtkVnc.DisplayKeyEvent.PRESS)
            self._vnc_display.send_keys_ex(keyvals, GtkVnc.DisplayKeyEvent.RELEASE)
        except AttributeError as exc:
            # Anciennes versions de gtk-vnc sans send_keys_ex : repli sur un
            # "clic" (appui + relachement immediat pour chaque touche).
            logger.debug("plugin vnc | send_keys_ex absent, repli send_keys : {}", exc)
            self._vnc_display.send_keys(keyvals)

    def _on_vnc_server_cut_text(self, display, text):
        """Callback gtk-vnc : le serveur VNC pousse son presse-papiers (copier distant).

        Recopie le texte recu (RFB ServerCutText) dans le presse-papiers GTK
        local, pret a etre colle (Ctrl+V) dans une application locale.

        Args:
            display (GtkVnc.Display): Widget emetteur.
            text (str): Texte pousse par le serveur.
        """
        if not getattr(self.host, "vnc_share_clipboard", True):
            return
        try:
            content = text if isinstance(text, str) else text.decode("utf-8", "replace")
        except Exception as exc:
            logger.debug("plugin vnc | presse-papiers distant illisible : {}", exc)
            return
        if not content:
            return
        app_logger.debug(
            f"VNC native | server-cut-text ({len(content)} car.) pour host {self.host.name}"
        )
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(content, -1)

    def _on_vnc_focus_in(self, display, event):
        """Pousse le presse-papiers local vers le serveur VNC (copier local -> distant).

        Le protocole RFB n'a pas de notion de "presse-papiers partage" en
        continu : il faut explicitement envoyer un ClientCutText. On le fait
        au moment ou l'utilisateur reprend le focus sur l'ecran distant (donc
        juste avant un Ctrl+V probable), comme le font TigerVNC/RealVNC.

        Args:
            display (GtkVnc.Display): Widget emetteur.
            event (Gdk.EventFocus): Evenement de focus.

        Returns:
            bool: False pour laisser GTK poursuivre le traitement normal du focus.
        """
        if not getattr(self.host, "vnc_share_clipboard", True):
            return False
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text()
        if text:
            display.client_cut_text(text)
        return False

    # ── Fallback legacy (subprocess) ────────────────────────────────────

    def _build_cmd(self):
        """Construit la commande VNC depuis les attributs Host vnc_* (mode fallback uniquement).

        Returns:
            tuple[list[str], str]: (arguments Popen, mot de passe en clair).
        """
        h = self.host
        port = str(getattr(h, "port", "5900+") or "5900")
        pwd = self._get_password() or ""
        # Jamais le mot de passe lui-meme dans le journal : seulement sa presence.
        app_logger.debug(f"VNC password for host {h.name} provided={bool(pwd)}")
        opts_str = self._entry_opts.get_text().strip()
        truthy = (True, "True", "true", "1", "yes")

        # ── Résolution viewer : attribut structuré > auto-détection
        viewer_id = (getattr(h, "vnc_viewer", "") or "").strip()
        _VNC_BINS = {
            "tigervnc": shutil.which("vncviewer"),
            "vinagre": shutil.which("vinagre"),
            "remmina": shutil.which("remmina"),
            "krdc": shutil.which("krdc"),
        }
        vnc_bin = (_VNC_BINS.get(viewer_id) or VNC_BIN) if viewer_id else VNC_BIN
        bin_name = os.path.basename(vnc_bin)

        # ── Display number → port effectif
        display = (getattr(h, "vnc_display", "") or "").strip()
        if display:
            try:
                effective_port = str(5900 + int(display))
            except ValueError as exc:
                logger.debug("plugin vnc | numéro de display invalide {!r} : {}", display, exc)
                effective_port = port
        else:
            effective_port = port

        # ── Construction de la commande selon le binaire
        if bin_name in ("vinagre",) or bin_name == "remmina":
            cmd = [vnc_bin, f"vnc://{h.host}:{effective_port}"]
        elif bin_name == "krdc":
            cmd = [vnc_bin, f"vnc:/{h.host}:{effective_port}"]
        else:
            # tigervnc / xtightvncviewer / vncviewer générique
            cmd = [vnc_bin, f"{h.host}:{effective_port}"]
            if pwd:
                # Chemin unique et non previsible (avant : un nom fixe dans /tmp,
                # partage entre utilisateurs -> lien symbolique pre-pose possible).
                self._remove_passwd_file()
                fd, passwd_path = tempfile.mkstemp(prefix="gcm-vnc-", suffix=".passwd")
                os.close(fd)
                self._passwd_path = passwd_path
                try:
                    self.make_vnc_passwd(pwd, passwd_path)
                except Exception as exc:
                    logger.warning("plugin vnc | fichier de mot de passe VNC non créé : {}", exc)
                    self._remove_passwd_file()
                    raise
                cmd += ["-passwd", passwd_path]
            if getattr(h, "vnc_view_only", False) in truthy:
                cmd.append("-ViewOnly")
            if getattr(h, "vnc_fullscreen", False) in truthy:
                cmd.append("-FullScreen")
                app_logger.debug(f"VNC fullscreen requested for host {h.name}; {cmd} ")

        if opts_str:
            cmd += shlex.split(opts_str)

        app_logger.debug(f"VNC command built for host {h.name}: {cmd}")
        return cmd, pwd

    def make_vnc_passwd(self, password: str, path: str) -> None:
        """Génère un fichier passwd compatible TigerVNC/RFB.

        Args:
            password: Mot de passe en clair (tronqué à 8 caractères).
            path: Chemin de destination du fichier passwd.
        """
        # Clé fixe RFB, bits inversés par octet
        key = bytes([self.reverse_bits(b) for b in [23, 82, 107, 6, 35, 78, 88, 7]])
        # Pad/tronque à 8 octets
        pwd_bytes = password[:8].encode("ascii").ljust(8, b"\x00")
        cipher = DES.new(key, DES.MODE_ECB)
        encrypted = cipher.encrypt(pwd_bytes)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(encrypted)
        os.chmod(path, 0o600)

    def reverse_bits(self, b: int) -> int:
        """Inverse l'ordre des bits d'un octet (obfuscation DES du protocole RFB/VNC).

        Args:
            b (int): Octet source (0-255).

        Returns:
            int: L'octet avec ses 8 bits inversés (MSB<->LSB), tel
            qu'attendu par la clé DES du mot de passe VNC (spec RFB).
        """
        return int(f"{b:08b}"[::-1], 2)

    def _remove_passwd_file(self):
        """Supprime le fichier passwd RFB de la connexion precedente, s'il existe."""
        path = getattr(self, "_passwd_path", None)
        self._passwd_path = None
        if path:
            try:
                os.remove(path)
            except OSError as exc:
                app_logger.debug(f"VNC passwd file already removed: {exc}")

    def _wait_proc(self):
        """Attend la fin du processus VNC en arriere-plan (mode fallback)."""
        if self._proc:
            rc = self._proc.wait()
            self._remove_passwd_file()
            if rc != 0:
                GLib.idle_add(self._set_status, _("Ended (code {rc})").format(rc=rc))
            else:
                GLib.idle_add(self._set_status, _("Session ended"))
        GLib.idle_add(self._btn_connect.set_sensitive, True)
        GLib.idle_add(self._btn_disconnect.set_sensitive, False)
        self._proc = None

    # ── API commune (point d'entree partage natif / fallback) ──────────

    def _on_connect(self, widget):
        """Lance la connexion VNC (gtk-vnc natif ou subprocess fallback).

        Args:
            widget (Gtk.Button): Bouton declencheur (peut etre None).
        """
        if _GTKVNC_OK:
            host_addr, port = self._effective_host_port()
            app_logger.debug(f"VNC native | open_host host={host_addr} port={port}")
            self._set_status(_("Connecting…"))
            self._vnc_display.open_host(host_addr, port)
            return

        if self._proc is not None and self._proc.poll() is None:
            return
        self.host.extra_params = self._entry_opts.get_text().strip()
        cmd, pwd = self._build_cmd()
        bin_name = os.path.basename(VNC_BIN)
        try:
            if bin_name not in ("vinagre", "remmina") and pwd:
                # Passer le mot de passe sur stdin
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._proc.stdin.write(pwd.encode() + b"\n")
                self._proc.stdin.close()
            else:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except FileNotFoundError as exc:
            logger.debug("plugin vnc | viewer absent : {}", exc)
            self._remove_passwd_file()  # viewer absent : _wait_proc ne tournera pas
            self._set_status(_("xfreerdp not found"))
            return
        self._set_status(_("Connecting…"))
        self._btn_connect.set_sensitive(False)
        self._btn_disconnect.set_sensitive(True)
        threading.Thread(target=self._wait_proc, daemon=True).start()

    def _on_disconnect(self, widget):
        """Termine la session VNC (gtk-vnc natif ou subprocess fallback).

        Args:
            widget (Gtk.Button): Bouton declencheur.
        """
        if _GTKVNC_OK:
            self._vnc_display.close()
            return

        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._set_status(_("Disconnect"))
        self._btn_connect.set_sensitive(True)
        self._btn_disconnect.set_sensitive(False)

    def _set_status(self, text):
        """Met a jour le label de statut.

        Args:
            text (str): Nouveau statut.
        """
        self._lbl_status.set_text(text)

    def connect_vnc(self):
        """Lance la connexion VNC automatiquement (appele depuis addTab)."""
        self._on_connect(None)

    def build_context_menu(self):
        """Construit le menu contextuel (clic droit sur l'onglet) de cet onglet VNC.

        Uniquement disponible en mode natif (gtk-vnc) ; en mode fallback,
        l'onglet conserve ses boutons historiques dans la barre d'outils.

        Returns:
            Gtk.Menu | None: Connect / Disconnect / Touches spéciales, ou
                None en mode fallback (pas de menu contextuel dedie).
        """
        if not _GTKVNC_OK:
            return None
        return build_remote_desktop_context_menu(
            self._btn_connect, self._btn_disconnect, self._send_special_keys
        )


_VIEWER_ITEMS = [
    ("tigervnc", "tigervnc (vncviewer)"),
    ("vinagre", "vinagre"),
    ("remmina", "remmina"),
    ("krdc", "krdc"),
]


def _bool_from_str(value: object) -> bool:
    """Convertit une valeur de config texte ("true"/"1"/"yes") en bool.

    Args:
        value: Valeur brute (souvent une chaîne issue de gcm.conf).
    """
    return str(value).lower() in ("true", "1", "yes")


class VncPlugin(ConnectionPlugin):
    """Plugin pour le protocole ``vnc``."""

    protocol_id = "vnc"
    display_name = _("VNC")
    default_port = 5900
    icon_name = "video-display-symbolic"
    ui_order = 3

    def __init__(self) -> None:
        """Initialise les références de widgets du formulaire VNC (peuplées par ``build_edit_page()``)."""
        self._cmb_viewer: Gtk.ComboBoxText | None = None
        self._txt_display: Gtk.Entry | None = None
        self._chk_view_only: Gtk.CheckButton | None = None
        self._chk_fullscreen: Gtk.CheckButton | None = None
        self._chk_share_clipboard: Gtk.CheckButton | None = None

    def build_tab(self, host, get_password: Callable[[], str] | None = None) -> Gtk.Widget:
        """Construit l'onglet de session VNC (délègue à ``VncTab``).

        Args:
            host: Instance ``Host`` avec ``protocol == "vnc"``.
            get_password: Callable retournant le mot de passe déchiffré.

        Returns:
            Une instance de ``VncTab`` (inchangée — natif GtkVnc ou fallback
            subprocess selon disponibilité de gtk-vnc, logique interne
            intacte).
        """
        logger.debug(f"VncPlugin.build_tab | host={host.name}")
        return VncTab(host, get_password)

    def build_edit_page(self) -> Gtk.Widget:
        """Construit la page de champs Edit Host pour VNC.

        Returns:
            Un ``Gtk.Grid`` autonome reproduisant ``gridVncProps``.
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

        self._cmb_viewer = Gtk.ComboBoxText()
        for item_id, label in _VIEWER_ITEMS:
            self._cmb_viewer.append(item_id, label)
        self._cmb_viewer.set_active_id("tigervnc")
        add_row(_("Viewer (fallback)"), self._cmb_viewer)

        self._txt_display = Gtk.Entry()
        self._txt_display.set_placeholder_text(_("e.g. 0 for :0, leave empty for default"))
        add_row(_("Display"), self._txt_display)

        self._chk_view_only = Gtk.CheckButton(label=_("View only"))
        grid.attach(self._chk_view_only, 1, row, 1, 1)
        row += 1

        self._chk_fullscreen = Gtk.CheckButton(label=_("Fullscreen"))
        grid.attach(self._chk_fullscreen, 1, row, 1, 1)
        row += 1

        self._chk_share_clipboard = Gtk.CheckButton(label=_("Share clipboard"))
        self._chk_share_clipboard.set_active(True)
        grid.attach(self._chk_share_clipboard, 1, row, 1, 1)
        row += 1

        grid.show_all()
        return grid

    def load_host_fields(self, host) -> None:
        """Recharge tous les champs VNC depuis *host*.

        Args:
            host: Instance ``Host`` source.
        """
        if self._cmb_viewer is not None:
            self._cmb_viewer.set_active_id(getattr(host, "vnc_viewer", "tigervnc") or "tigervnc")
        if self._txt_display is not None:
            self._txt_display.set_text(str(getattr(host, "vnc_display", "") or ""))
        if self._chk_view_only is not None:
            self._chk_view_only.set_active(_bool_from_str(getattr(host, "vnc_view_only", False)))
        if self._chk_fullscreen is not None:
            self._chk_fullscreen.set_active(_bool_from_str(getattr(host, "vnc_fullscreen", False)))
        if self._chk_share_clipboard is not None:
            self._chk_share_clipboard.set_active(
                _bool_from_str(getattr(host, "vnc_share_clipboard", True))
            )
        logger.debug(f"VncPlugin.load_host_fields | host={getattr(host, 'name', '?')}")

    def save_host_fields(self, host) -> None:
        """Reporte tous les champs VNC dans *host*.

        Args:
            host: Instance ``Host`` à mettre à jour.
        """
        host.vnc_viewer = self._cmb_viewer.get_active_id() if self._cmb_viewer else "tigervnc"
        host.vnc_display = self._txt_display.get_text().strip() if self._txt_display else ""
        host.vnc_view_only = self._chk_view_only.get_active() if self._chk_view_only else False
        host.vnc_fullscreen = self._chk_fullscreen.get_active() if self._chk_fullscreen else False
        host.vnc_share_clipboard = (
            self._chk_share_clipboard.get_active() if self._chk_share_clipboard else True
        )
        logger.debug(f"VncPlugin.save_host_fields | host={getattr(host, 'name', '?')}")

    def host_fields(self) -> list[tuple[str, object]]:
        """Champs ``Host`` propres au protocole VNC."""
        return [
            ("vnc_viewer", "tigervnc"),
            ("vnc_display", ""),
            ("vnc_view_only", False),
            ("vnc_fullscreen", False),
            ("vnc_share_clipboard", True),
        ]


def get_plugin() -> VncPlugin:
    """Point d'entree standard utilise par PluginRegistry.autoload().

    Returns:
        Une instance de VncPlugin, prete a etre enregistree.
    """
    return VncPlugin()
