# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin de connexion SPICE (SpiceClientGtk.Display natif, ou fallback subprocess).

Sixième plugin implémenté après Web, IPMI SOL, Serial, RDP et VNC — le plus
complexe : trois modes de connexion mutuellement exclusifs (URI directe,
libvirt, Proxmox), chacun avec sa propre sous-grille de champs, plus les
options de partage (presse-papier/USB/audio/dossier). Reproduit fidèlement
``gridSpiceProps`` et la logique des trois ``on_spiceRadioXxx_toggled``.

``SpiceTab`` (ci-dessous), ainsi que ses helpers exclusifs
``parse_spice_hosts``/``HostsUpdater``/``_ask_password_dialog``, vivent
désormais dans ce module et non plus dans ``widgets.py`` — ils ne
dépendaient d'aucun état de ``gnome_connection_manager`` (pas de
``wMain``/``conf``), cf. audit widgets.py/utils.py.
"""

from __future__ import annotations

import configparser
import json
import os
import shlex
import shutil
import subprocess
import threading
from collections.abc import Callable
from gettext import gettext as _
from pathlib import Path

import gi
from loguru import logger

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, GObject, Gtk
from plugin_base import ConnectionPlugin

from utils import run_dialog_sync
from widgets import (
    CONFIG_FILE,
    app_logger,
    build_remote_desktop_context_menu,
    mount_iso_temp,
    umount_iso_temp,
)

__all__ = ["HostsUpdater", "SpicePlugin", "SpiceTab", "parse_spice_hosts"]

# Widget SPICE natif (spice-gtk) : remplace subprocess remote-viewer par un
# GtkWidget directement embarqué, sans fenêtre externe.
# Dégrade gracieusement vers l'ancien mode subprocess si absent du système.
try:
    gi.require_version("SpiceClientGtk", "3.0")
    gi.require_version("SpiceClientGLib", "2.0")
    from gi.repository import SpiceClientGLib, SpiceClientGtk

    _SPICEGTK_OK = True
except (ValueError, ImportError) as exc:
    logger.debug("plugin spice | SpiceClientGtk indisponible : {}", exc)
    _SPICEGTK_OK = False

# libvirt-python : interrogation native d'un hyperviseur libvirt pour
# extraire les paramètres SPICE d'une VM sans passer par virt-viewer.
# Dégrade vers subprocess virt-viewer si absent (pip install libvirt-python).
try:
    import libvirt as _libvirt_mod

    _LIBVIRT_OK = True
except ImportError as exc:
    logger.debug("plugin spice | libvirt-python absent : {}", exc)
    _LIBVIRT_OK = False

# Détection des binaires SPICE disponibles
SPICE_BIN = (
    shutil.which("remote-viewer")
    or shutil.which("virt-viewer")
    or shutil.which("spicy")
    or "remote-viewer"
)
if _SPICEGTK_OK:
    app_logger.debug("SpiceTab | spice-gtk disponible | widget natif SpiceClientGtk.Display")
else:
    app_logger.warning(
        f"SpiceTab | spice-gtk indisponible (gir1.2-spiceclientgtk-3.0 manquant) | fallback subprocess {SPICE_BIN}",
    )
if _LIBVIRT_OK:
    app_logger.debug("SpiceTab | libvirt-python disponible | mode libvirt natif activé")
else:
    app_logger.warning(
        f"SpiceTab | libvirt-python indisponible (pip install libvirt-python) | mode libvirt → fallback subprocess "
        f"{SPICE_BIN}",
    )


# mask_password : partagée par tout le dépôt depuis l'issue #130.
from logging_config import mask_password  # noqa: E402


def parse_spice_hosts(conf_path: Path) -> list[tuple[str, str]]:
    """Extrait les paires (host, spice_px_node) des entrées SPICE de gcm.conf.

    Args:
        conf_path: Chemin vers le fichier gcm.conf.

    Returns:
        Liste de tuples ``(host_ip, node_name)`` uniques, ordre de déclaration préservé.

    Raises:
        FileNotFoundError: Si ``conf_path`` n'existe pas.
    """
    app_logger.debug(f"parse_spice_hosts | enter | conf_path={conf_path}")

    if not conf_path.exists():
        raise FileNotFoundError(f"gcm.conf introuvable : {conf_path}")

    raw = conf_path.read_text(encoding="utf-8")
    parser = configparser.RawConfigParser()
    parser.read_string(raw)

    entries: list[tuple[str, str]] = []

    for section in parser.sections():
        if not section.startswith("host "):
            continue

        proto = parser.get(section, "protocol", fallback="")
        host = parser.get(section, "host", fallback="").strip()
        node = parser.get(section, "spice_px_node", fallback="").strip()

        if proto != "spice":
            app_logger.trace(f"parse_spice_hosts | skip | section={section} proto={proto}")
            continue

        if not host or not node:
            app_logger.warning(
                f"parse_spice_hosts | section={section} ignorée — host ou spice_px_node vide"
            )
            continue

        app_logger.debug(f"parse_spice_hosts | found | section={section} host={host} node={node}")
        entries.append((host, node))

    # Dédoublonnage ordre-stable
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for pair in entries:
        if pair not in seen:
            seen.add(pair)
            unique.append(pair)

    app_logger.debug(f"parse_spice_hosts | exit | total={len(entries)} unique={len(unique)}")
    return unique


def _ask_password_dialog(prompt: str = "Mot de passe sudo requis :") -> str | None:
    """Affiche une boîte de dialogue GTK3 demandant le mot de passe.

    Note:
        Utilisait auparavant les constantes stock GTK3 `STOCK_CANCEL`/
        `STOCK_OK` de `Gtk` (supprimées en GTK4) — remplacées ici par des
        libellés traduits littéraux, comme déjà fait ailleurs dans le
        projet pour ce même nettoyage (voir `plugins/plugin_ssh.py`,
        `add_buttons()`) ; audit GTK4 session-28, 2026-09-10 (voir
        docs/gtk4-migration.md §3.2).

    Args:
        prompt: Message affiché au-dessus du champ de saisie.

    Returns:
        Le mot de passe saisi, ou None si annulé.
    """
    logger.debug("_ask_password_dialog: entrée")
    dialog = Gtk.Dialog(
        title="Authentification requise",
        flags=Gtk.DialogFlags.MODAL,
    )
    dialog.add_buttons(
        _("Cancel"),
        Gtk.ResponseType.CANCEL,
        _("OK"),
        Gtk.ResponseType.OK,
    )
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_border_width(12)

    content = dialog.get_content_area()
    content.set_spacing(8)

    label = Gtk.Label(label=prompt)
    label.set_halign(Gtk.Align.START)
    content.add(label)

    entry = Gtk.Entry()
    entry.set_visibility(False)
    entry.set_invisible_char("●")
    entry.set_activates_default(True)
    content.add(entry)

    dialog.show_all()
    response = run_dialog_sync(dialog)
    password = entry.get_text() if response == Gtk.ResponseType.OK else None
    dialog.destroy()
    logger.debug("_ask_password_dialog: sortie")
    return password


class HostsUpdater:
    """Mise à jour de /etc/hosts pour les nœuds SPICE, avec élévation GTK3.

    Si le processus est déjà root, l'écriture est directe.
    Sinon, un dialog GTK3 demande le mot de passe et l'écriture
    est déléguée à ``tee`` via ``sudo -S``, sans re-lancer l'appli.
    """

    HOSTS_FILE = "/etc/hosts"
    MARKER = "# gcm-spice"
    MAX_ATTEMPTS = 3

    def _existing_nodes(self, hosts_text: str) -> set[str]:
        """Retourne les noms d'hôtes déjà présents dans hosts_text.

        Args:
            hosts_text: Contenu brut de /etc/hosts.

        Returns:
            Ensemble de noms en minuscules.
        """
        logger.debug("HostsUpdater._existing_nodes: entrée")
        names: set[str] = set()
        for line in hosts_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            for name in parts[1:]:
                names.add(name.lower())
        logger.debug("HostsUpdater._existing_nodes: sortie")
        return names

    def _build_lines(self, pairs: list[tuple[str, str]], existing: set[str]) -> list[str]:
        """Calcule les lignes à ajouter.

        Args:
            pairs: Liste de ``(host_ip, node_name)``.
            existing: Noms déjà présents dans /etc/hosts.

        Returns:
            Lignes prêtes à être ajoutées (sans newline finale).
        """
        logger.debug("HostsUpdater._build_lines: entrée")
        lines: list[str] = []
        for host_ip, node_name in pairs:
            if node_name.lower() in existing:
                app_logger.debug(f"_build_lines | already present | node={node_name}")
                continue
            lines.append(f"{host_ip:<20}{node_name}  {self.MARKER}")
            app_logger.info(f"_build_lines | queued | ip={host_ip} node={node_name}")
        logger.debug("HostsUpdater._build_lines: sortie")
        return lines

    def _write_direct(self, new_text: str) -> None:
        """Écrit new_text dans /etc/hosts directement (déjà root).

        Args:
            new_text: Contenu complet du fichier.
        """
        logger.debug("HostsUpdater._write_direct: entrée")
        with open(self.HOSTS_FILE, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        app_logger.info("_write_direct | écriture directe OK")
        logger.debug("HostsUpdater._write_direct: fin")

    def _write_via_sudo(self, new_text: str) -> None:
        """Écrit new_text dans /etc/hosts via sudo -S tee, avec dialog GTK3.

        Demande le mot de passe jusqu'à MAX_ATTEMPTS fois.

        Args:
            new_text: Contenu complet du fichier.

        Raises:
            PermissionError: Si l'authentification échoue après MAX_ATTEMPTS tentatives
                             ou si l'utilisateur annule.
        """
        logger.debug("HostsUpdater._write_via_sudo: entrée")
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            prompt = (
                "Mot de passe sudo requis pour modifier /etc/hosts :"
                if attempt == 1
                else f"Mot de passe incorrect — tentative {attempt}/{self.MAX_ATTEMPTS} :"
            )
            password = _ask_password_dialog(prompt)

            if password is None:
                raise PermissionError("Authentification annulée par l'utilisateur")

            # Vérifie le mot de passe
            check = subprocess.run(
                ["sudo", "-S", "-v"],
                input=password + "\n",
                capture_output=True,
                text=True,
            )
            app_logger.debug(
                f"_write_via_sudo | sudo -v | attempt={attempt} rc={check.returncode}"
            )
            if check.returncode != 0:
                continue

            # Mot de passe valide → écriture via sudo tee (une seule opération)
            result = subprocess.run(
                ["sudo", "-S", "tee", self.HOSTS_FILE],
                input=password + "\n" + new_text,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise PermissionError(f"sudo tee a échoué : {result.stderr.strip()}")

            app_logger.info("_write_via_sudo | écriture via sudo tee OK")
            logger.debug("HostsUpdater._write_via_sudo: sortie")
            return

        raise PermissionError(f"Échec d'authentification après {self.MAX_ATTEMPTS} tentatives")

    def update(self, pairs: list[tuple[str, str]]) -> int:
        """Point d'entrée public : ajoute les entrées SPICE manquantes dans /etc/hosts.

        Args:
            pairs: Liste de ``(host_ip, spice_px_node)`` issues de gcm.conf.

        Returns:
            Nombre de lignes ajoutées (0 si déjà à jour).

        Raises:
            PermissionError: Si l'élévation est refusée ou annulée.
        """
        app_logger.debug(f"HostsUpdater.update | enter | pairs={pairs}")

        current_text = open(self.HOSTS_FILE, encoding="utf-8").read()
        existing = self._existing_nodes(current_text)
        lines = self._build_lines(pairs, existing)

        if not lines:
            app_logger.info("HostsUpdater.update | déjà à jour")
            return 0

        separator = "" if current_text.endswith("\n") else "\n"
        new_text = current_text + separator + "\n".join(lines) + "\n"

        if os.geteuid() == 0:
            self._write_direct(new_text)
        else:
            self._write_via_sudo(new_text)

        app_logger.debug(f"HostsUpdater.update | exit | added={len(lines)}")
        return len(lines)


def _bool_from_str(value: object) -> bool:
    """Convertit une valeur de config texte ("true"/"1"/"yes") en bool.

    Args:
        value: Valeur brute (souvent une chaîne issue de gcm.conf).
    """
    return str(value).lower() in ("true", "1", "yes")


class SpiceTab(Gtk.Box):
    """Widget affiche dans le Gtk.Notebook pour les connexions SPICE.

    Mode natif (prefere) : widget SpiceClientGtk.Display embarque directement
    dans l'onglet GCM, sans fenetre externe (gir1.2-spiceclientgtk-3.0).
    Mode degrade (fallback) : ancien comportement subprocess remote-viewer /
    virt-viewer / spicy en fenetre externe, conserve a l'identique si
    spice-gtk est absent du systeme.
    """

    def __init__(self, host, get_password_fn):
        """Initialise le panneau SPICE.

        Args:
            host (Host): Objet Host GCM avec protocol='spice'.
            get_password_fn (callable): Fonction retournant le mot de passe dechiffre.
        """
        logger.debug("SpiceTab.__init__: entrée")
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.host = host
        self._get_password = get_password_fn
        app_logger.debug(f"SpiceTab | init | mode={'spice-gtk' if _SPICEGTK_OK else 'subprocess'}")
        self._proc = None  # mode fallback uniquement
        self._spice_session = None  # mode spice-gtk uniquement
        self._spice_display = None  # mode spice-gtk uniquement
        self._spice_audio = None  # mode spice-gtk uniquement (lecture/capture audio)
        self._iso_loop_device = None  # peripherique loop si un ISO est monte
        self.set_margin_start(16)
        self.set_margin_end(16)
        self.set_margin_top(16)
        self.set_margin_bottom(16)
        self._build_ui()
        logger.debug("SpiceTab.__init__: fin")

    def _build_ui(self):
        """Construit l'interface du panneau SPICE (spice-gtk natif ou fallback)."""
        logger.debug("SpiceTab._build_ui: entrée")
        if _SPICEGTK_OK:
            self._build_ui_native()
        else:
            self._build_ui_legacy()
        logger.debug("SpiceTab._build_ui: fin")

    def _build_ui_native(self):
        """Construit la barre d'outils + le conteneur du widget SpiceClientGtk.Display."""
        logger.debug("SpiceTab._build_ui_native: entrée")
        h = self.host
        port = getattr(h, "port", "5930") or "5930"

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label()
        title.set_markup(f"<b>SPICE</b> <small>{h.host}:{port}</small>")
        title.set_xalign(0)
        toolbar.pack_start(title, True, True, 0)

        self._lbl_status = Gtk.Label(label=_("Waiting…"))
        self._lbl_status.get_style_context().add_class("dim-label")
        toolbar.pack_start(self._lbl_status, False, False, 8)

        # Connect/Disconnect/Peripheriques USB/Touches speciales : deplaces
        # dans le menu contextuel (clic droit sur l'onglet/titre, voir
        # build_context_menu). Boutons conserves hors-toolbar comme porteurs
        # d'etat/gestionnaire reutilises par le menu.
        self._btn_connect = Gtk.Button(label=_("Connect"))
        self._btn_connect.get_style_context().add_class("suggested-action")
        self._btn_connect.connect("clicked", self._on_connect)

        self._btn_disconnect = Gtk.Button(label=_("Disconnect"))
        self._btn_disconnect.set_sensitive(False)
        self._btn_disconnect.connect("clicked", self._on_disconnect)

        # Bouton de redirection USB (n'apparait dans le menu contextuel que
        # si autorise dans l'onglet SPICE du dialogue d'edition d'hote).
        self._btn_usb = Gtk.Button(label=_("USB devices…"))
        self._btn_usb.connect("clicked", self._on_usb_devices_clicked)
        self._btn_usb.set_no_show_all(True)
        self._btn_usb.set_visible(bool(getattr(h, "spice_share_usb", False)))

        self.pack_start(toolbar, False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 0)

        # Conteneur dans lequel on injecte le SpiceClientGtk.Display à la connexion.
        # Le Display ne peut pas être créé sans une session valide, donc on
        # diffère sa création à _connect_native() (même pattern que virt-viewer).
        self._display_box = Gtk.Box()
        self._display_box.set_hexpand(True)
        self._display_box.set_vexpand(True)
        self.pack_start(self._display_box, True, True, 0)

        self.show_all()
        logger.debug("SpiceTab._build_ui_native: fin")

    def _build_ui_legacy(self):
        """Construit l'interface fallback (ancien comportement subprocess)."""
        logger.debug("SpiceTab._build_ui_legacy: entrée")
        h = self.host
        port = getattr(h, "port", "5930") or "5930"
        title = Gtk.Label()
        title.set_markup(f"<b>SPICE — {h.name}</b><small>   {h.host}:{port}</small>")
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
        hb2.pack_start(Gtk.Label(label=_("remote-viewer options:")), False, False, 0)
        self._entry_opts = Gtk.Entry()
        self._entry_opts.set_text(getattr(h, "extra_params", "") or "")
        self._entry_opts.set_tooltip_text(
            _("Additional remote-viewer parameters\nE.g.: {example}").format(
                example="--spice-ca-file=/etc/ssl/certs/ca.crt --full-screen"
            )
        )
        hb2.pack_start(self._entry_opts, True, True, 0)
        self.pack_start(hb2, False, False, 0)
        lbl_bin = Gtk.Label()
        lbl_bin.set_markup(f"<small><i>Binaire détecté : {SPICE_BIN}</i></small>")
        lbl_bin.set_xalign(0)
        self.pack_start(lbl_bin, False, False, 0)
        app_logger.debug(f"SpiceTab._build_ui_legacy | SPICE_BIN={SPICE_BIN}")
        self.show_all()
        logger.debug("SpiceTab._build_ui_legacy: fin")

    # ── Mode natif (spice-gtk) ───────────────────────────────────────────────

    def _build_spice_uri(self) -> str | None:
        """Construit l'URI spice:// pour SpiceClientGLib.Session (mode natif URI).

        Returns:
            URI spice:// ou None si les informations sont insuffisantes.
        """
        logger.debug("SpiceTab._build_spice_uri: entrée")
        h = self.host
        port = str(getattr(h, "port", "5930") or "5930")
        pwd = self._get_password() or ""

        uri = f"spice://{h.host}?port={port}"
        if pwd:
            uri += f"&password={pwd}"

        tls_port = (getattr(h, "spice_tls_port", "") or "").strip()
        if tls_port:
            uri += f"&tls-port={tls_port}"

        app_logger.debug(f"SpiceTab._build_spice_uri | uri={mask_password(uri)}")
        logger.debug("SpiceTab._build_spice_uri: sortie")
        return uri

    def _fetch_proxmox_ticket(self) -> dict | None:
        """Récupère le ticket SPICE via SSH pvesh (mode Proxmox).

        Returns:
            Ticket JSON analysé, ou None si erreur.
        """
        logger.debug("SpiceTab._fetch_proxmox_ticket: entrée")
        h = self.host
        node_name = (getattr(h, "spice_px_node", "") or "").strip()
        vmid = (getattr(h, "spice_px_vmid", "") or "").strip()
        if not node_name or not vmid:
            self._set_status(_("Error: Proxmox node and VMID required"))
            logger.debug("SpiceTab._fetch_proxmox_ticket: sortie")
            return None

        hv_host = h.host
        hv_port = int(h.port) if str(h.port).isdigit() and int(h.port) not in (5900, 5930) else 22
        hv_user = h.user or "root"

        try:
            import paramiko as _paramiko

            _client = _paramiko.SSHClient()
            _client.set_missing_host_key_policy(_paramiko.AutoAddPolicy())
            _client.connect(hv_host, port=hv_port, username=hv_user, timeout=10)
            cmd_pvesh = (
                f"pvesh create /nodes/{node_name}/qemu/{vmid}/spiceproxy --output-format json"
            )
            app_logger.debug(
                f"SpiceTab._fetch_proxmox_ticket | SSH {hv_user}@{hv_host}:{hv_port} | cmd={cmd_pvesh}"
            )
            _stdin, stdout, _stderr = _client.exec_command(cmd_pvesh)
            out = stdout.read().decode().strip()
            _client.close()
        except Exception as exc:
            app_logger.error(
                f"SpiceTab._fetch_proxmox_ticket | SSH error | exc={exc}", exception=True
            )
            self._set_status(_("SSH error: {exc}").format(exc=exc))
            logger.debug("SpiceTab._fetch_proxmox_ticket: sortie")
            return None

        try:
            logger.debug("SpiceTab._fetch_proxmox_ticket: sortie")
            return json.loads(out)
        except Exception as exc:
            logger.warning("plugin spice | ticket SPICE non décodable : {}", exc)
            self._set_status(_("SPICE ticket error: {out}").format(out=out[:80]))
            logger.debug("SpiceTab._fetch_proxmox_ticket: sortie")
            return None
        logger.debug("SpiceTab._fetch_proxmox_ticket: fin")

    def _fetch_libvirt_spice_params(self) -> dict | None:
        """Récupère les paramètres SPICE d'une VM via libvirt-python.

        Se connecte à l'hyperviseur libvirt, lit le XML de la VM et extrait
        le bloc ``<graphics type='spice'>``.

        Returns:
            Dict ``{host, port, tls-port, password}`` (port/tls-port peuvent
            être None si désactivés), ou None si erreur.
        """
        logger.debug("SpiceTab._fetch_libvirt_spice_params: entrée")
        import xml.etree.ElementTree as _ET

        h = self.host
        libvirt_uri = (getattr(h, "spice_libvirt_uri", "") or "").strip()
        vm_name = (getattr(h, "spice_vm_name", "") or "").strip()

        if not libvirt_uri:
            self._set_status(_("Error: libvirt URI required"))
            logger.debug("SpiceTab._fetch_libvirt_spice_params: sortie")
            return None
        if not vm_name:
            self._set_status(_("Error: VM name required"))
            logger.debug("SpiceTab._fetch_libvirt_spice_params: sortie")
            return None

        # Connexion à l'hyperviseur
        try:
            conn = _libvirt_mod.open(libvirt_uri)
        except _libvirt_mod.libvirtError as exc:
            app_logger.error(
                f"SpiceTab._fetch_libvirt_spice_params | open failed | uri={libvirt_uri} | exc={exc}",
                exception=True,
            )
            self._set_status(_("libvirt error: {exc}").format(exc=exc))
            logger.debug("SpiceTab._fetch_libvirt_spice_params: sortie")
            return None

        # Recherche de la VM
        try:
            dom = conn.lookupByName(vm_name)
            xml_str = dom.XMLDesc(0)
        except _libvirt_mod.libvirtError as exc:
            conn.close()
            app_logger.error(
                f"SpiceTab._fetch_libvirt_spice_params | VM error | vm={vm_name} | exc={exc}"
            )
            self._set_status(_("VM introuvable : {vm}").format(vm=vm_name))
            logger.debug("SpiceTab._fetch_libvirt_spice_params: sortie")
            return None
        finally:
            try:
                conn.close()
            except Exception as exc:
                logger.debug(
                    "plugin spice | fermeture de la connexion libvirt impossible : {}", exc
                )

        # Parse XML : <graphics type='spice' port=… tlsPort=… passwd=…>
        try:
            root = _ET.fromstring(xml_str)
            gfx = root.find(".//graphics[@type='spice']")
        except _ET.ParseError as exc:
            app_logger.error(f"SpiceTab._fetch_libvirt_spice_params | XML parse error | exc={exc}")
            self._set_status(_("libvirt XML parsing error: {exc}").format(exc=exc))
            logger.debug("SpiceTab._fetch_libvirt_spice_params: sortie")
            return None

        if gfx is None:
            self._set_status(_("No SPICE console for {vm}").format(vm=vm_name))
            logger.debug("SpiceTab._fetch_libvirt_spice_params: sortie")
            return None

        # Adresse d'écoute : élément <listen type='address'> prioritaire
        listen_elem = gfx.find("listen[@type='address']")
        if listen_elem is not None:
            host_addr = listen_elem.get("address", "127.0.0.1") or "127.0.0.1"
        else:
            host_addr = gfx.get("listen", "127.0.0.1") or "127.0.0.1"

        port = gfx.get("port", "-1")
        tls_port = gfx.get("tlsPort", "-1")
        password = gfx.get("passwd", "")

        if port == "-1" and tls_port == "-1":
            self._set_status(_("SPICE disabled for {vm}").format(vm=vm_name))
            logger.debug("SpiceTab._fetch_libvirt_spice_params: sortie")
            return None

        result = {
            "host": host_addr,
            "port": port if port != "-1" else None,
            "tls-port": tls_port if tls_port != "-1" else None,
            "password": password,
        }
        app_logger.debug(
            f"SpiceTab._fetch_libvirt_spice_params | vm={vm_name} | host={host_addr} port={result['port']} tls="
            f"{result['tls-port']}",
        )
        logger.debug("SpiceTab._fetch_libvirt_spice_params: sortie")
        return result

    def _connect_native(self):
        """Lance la connexion SPICE via spice-gtk (mode natif).

        Modes :
        - ``proxmox`` : ticket SSH pvesh → SpiceClientGLib.Session direct.
        - ``libvirt``  : libvirt-python → XMLDesc → SpiceClientGLib.Session
          (fallback subprocess virt-viewer si libvirt-python absent).
        - ``uri``      : spice://host?port=… → SpiceClientGLib.Session.
        """
        logger.debug("SpiceTab._connect_native: entrée")
        h = self.host
        spice_mode = (getattr(h, "spice_mode", "uri") or "uri").strip()

        # Mode libvirt sans libvirt-python : fallback subprocess uniquement
        if spice_mode == "libvirt" and not _LIBVIRT_OK:
            app_logger.debug(
                "SpiceTab._connect_native | mode=libvirt | libvirt-python absent → subprocess"
            )
            cmd = self._build_cmd()
            if cmd is None:
                logger.debug("SpiceTab._connect_native: sortie")
                return
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError as exc:
                logger.debug("plugin spice | remote-viewer absent : {}", exc)
                self._set_status(_("remote-viewer not found"))
                logger.debug("SpiceTab._connect_native: sortie")
                return
            self._set_status(_("Connecting…"))
            self._btn_connect.set_sensitive(False)
            self._btn_disconnect.set_sensitive(True)
            threading.Thread(target=self._wait_proc, daemon=True).start()
            logger.debug("SpiceTab._connect_native: sortie")
            return

        # ── Teardown commun (tous les modes natifs) ─────────────────────────
        if self._spice_session is not None:
            try:
                self._spice_session.disconnect()
            except Exception as exc:
                logger.debug("plugin spice | déconnexion SPICE impossible : {}", exc)
            self._spice_session = None
        if self._spice_display is not None:
            self._display_box.remove(self._spice_display)
            self._spice_display.destroy()
            self._spice_display = None

        session = SpiceClientGLib.Session()

        if spice_mode == "proxmox":
            # ── Mode Proxmox : ticket SSH → paramètres session directs ─────
            pairs = parse_spice_hosts(Path(CONFIG_FILE))
            try:
                HostsUpdater().update(pairs)
            except PermissionError as exc:
                app_logger.error(f"hosts update | {exc}")

            ticket = self._fetch_proxmox_ticket()
            if ticket is None:
                logger.debug("SpiceTab._connect_native: sortie")
                return

            session.set_property("host", ticket["host"])
            session.set_property("tls-port", str(ticket["tls-port"]))
            session.set_property("password", ticket["password"])

            proxy = (ticket.get("proxy") or "").strip()
            if proxy:
                session.set_property("proxy", proxy)

            cert_subject = (ticket.get("host-subject") or "").strip()
            if cert_subject:
                session.set_property("cert-subject", cert_subject)

            ca_raw = (ticket.get("ca") or "").strip()
            if ca_raw:
                # PyGObject convertit automatiquement bytes → GLib.Bytes ;
                # passer GLib.Bytes.new() provoque "Must be sequence, not Bytes"
                ca_pem = ca_raw.replace("\\n", "\n").encode("utf-8")
                session.set_property("ca", ca_pem)

            app_logger.debug(
                f"SpiceTab._connect_native | proxmox | host={ticket['host']} tls-port={ticket['tls-port']}",
            )

        elif spice_mode == "libvirt":
            # ── Mode libvirt natif (libvirt-python disponible) ─────────────
            params = self._fetch_libvirt_spice_params()
            if params is None:
                logger.debug("SpiceTab._connect_native: sortie")
                return

            session.set_property("host", params["host"])
            if params["port"]:
                session.set_property("port", params["port"])
            if params["tls-port"]:
                session.set_property("tls-port", params["tls-port"])
            if params["password"]:
                session.set_property("password", params["password"])

            app_logger.debug(
                f"SpiceTab._connect_native | libvirt | host={params['host']} port={params['port']} tls="
                f"{params['tls-port']}",
            )

        else:
            # ── Mode URI directe : spice://host?port=… ─────────────────────
            uri = self._build_spice_uri()
            if uri is None:
                self._set_status(_("Configuration SPICE invalide"))
                logger.debug("SpiceTab._connect_native: sortie")
                return

            session.set_property("uri", uri)

            ca_cert = (getattr(h, "spice_ca_cert", "") or "").strip()
            if ca_cert and hasattr(SpiceClientGLib.Session.props, "ca_file"):
                session.set_property("ca-file", ca_cert)

            app_logger.debug(f"SpiceTab._connect_native | uri | uri={mask_password(uri)}")

        # ── Création couplée display → connexion (commun à tous les modes) ──
        # session.connect(signal, cb) est ambigu : SpiceSession.connect() est la
        # méthode de connexion au serveur et masque GObject.connect().
        # On passe explicitement par la classe de base GObject.
        GObject.GObject.connect(session, "channel-new", self._on_spice_channel_new)

        # Presse-papiers bidirectionnel texte/image (copier-coller hote <-> VM),
        # pilote par la case "Partager le presse-papiers" de l'onglet SPICE du
        # dialogue d'edition d'hote (active par defaut). Necessite spice-vdagent
        # dans l'invite.
        gtk_session = SpiceClientGtk.GtkSession.get(session)
        gtk_session.set_property("auto-clipboard", bool(getattr(h, "spice_share_clipboard", True)))

        # Redirection USB : pilotee par la case "Partager les peripheriques USB".
        # session.enable-usbredir doit etre positionne avant la connexion ; le
        # bouton toolbar (_btn_usb) reste visible/actif selon le meme attribut.
        share_usb = bool(getattr(h, "spice_share_usb", False))
        session.set_property("enable-usbredir", share_usb)
        self._btn_usb.set_visible(share_usb)

        # Audio (lecture + capture) : pilote par "Partager le son". L'objet
        # Audio doit etre instancie explicitement pour router le son local
        # (la propriete de session seule ne suffit pas).
        share_audio = bool(getattr(h, "spice_share_audio", False))
        session.set_property("enable-audio", share_audio)
        if share_audio:
            self._spice_audio = SpiceClientGLib.Audio.get(session, None)
        else:
            self._spice_audio = None

        # Dossier partage hote -> VM via webdav (spice-webdavd requis cote invite).
        # Un fichier .iso est d'abord monte localement (udisksctl) puis son
        # point de montage est partage comme un dossier classique.
        share_folder = bool(getattr(h, "spice_share_folder", False))
        raw_path = (getattr(h, "spice_shared_folder_path", "") or "").strip()
        folder_path = ""
        if share_folder and raw_path:
            if raw_path.lower().endswith(".iso"):
                mounted = mount_iso_temp(raw_path)
                if mounted:
                    folder_path, self._iso_loop_device = mounted
                else:
                    self._set_status(_("Error: could not mount the ISO"))
            else:
                folder_path = raw_path
        if folder_path:
            session.set_property("shared-dir", folder_path)
            session.set_property("share-dir-ro", False)
            app_logger.debug(
                f"SpiceTab._connect_native | dossier partage actif | path={folder_path}"
            )

        display = SpiceClientGtk.Display.new(session, 0)
        # Clavier/souris : capture complete des entrees locales (valeurs par
        # defaut de spice-gtk, rendues explicites ici).
        display.set_property("grab-keyboard", True)
        display.set_property("grab-mouse", True)
        display.set_property("disable-inputs", False)
        # Transfert de fichiers hote -> VM : glisser-deposer un fichier sur la
        # fenetre de l'ecran distant est gere nativement par SpiceClientGtk.Display
        # (appelle spice_main_channel_file_copy_async en interne). Necessite aussi
        # spice-vdagent dans l'invite ; aucun code supplementaire requis ici.
        display.set_hexpand(True)
        display.set_vexpand(True)
        display.set_tooltip_text(
            _("Drag and drop a file here to send it to the VM (requires spice-vdagent)")
        )
        self._display_box.pack_start(display, True, True, 0)
        display.show()

        self._spice_session = session
        self._spice_display = display

        self._set_status(_("Connecting…"))
        self._btn_connect.set_sensitive(False)
        session.connect()
        app_logger.debug(f"SpiceTab._connect_native | session.connect() lancé | mode={spice_mode}")
        logger.debug("SpiceTab._connect_native: fin")

    def _on_spice_channel_new(self, session, channel):
        """Callback spice-gtk : nouveau canal ouvert dans la session.

        Args:
            session (SpiceClientGLib.Session): Session SPICE émettrice.
            channel (SpiceClientGLib.Channel): Canal nouvellement créé.
        """
        logger.debug("SpiceTab._on_spice_channel_new: entrée")
        app_logger.debug(f"SpiceTab._on_spice_channel_new | channel-type={type(channel).__name__}")
        # channel.connect(signal, cb) est ambigu : SpiceClientGLib.Channel.connect()
        # est la methode de connexion du canal au serveur et masque GObject.connect()
        # (meme piege que pour session.connect() plus haut). On passe explicitement
        # par la classe de base GObject.
        GObject.GObject.connect(channel, "channel-event", self._on_spice_channel_event)
        logger.debug("SpiceTab._on_spice_channel_new: fin")

    def _on_spice_channel_event(self, channel, event):
        """Callback spice-gtk : événement sur un canal.

        Args:
            channel (SpiceClientGLib.Channel): Canal source.
            event (SpiceClientGLib.ChannelEvent): Type d'événement.
        """
        logger.debug("SpiceTab._on_spice_channel_event: entrée")
        app_logger.debug(f"SpiceTab._on_spice_channel_event | event={event}")
        if event == SpiceClientGLib.ChannelEvent.OPENED:
            GLib.idle_add(self._set_status, _("Connect"))
            GLib.idle_add(self._btn_connect.set_sensitive, False)
            GLib.idle_add(self._btn_disconnect.set_sensitive, True)
        elif event in (
            SpiceClientGLib.ChannelEvent.CLOSED,
            SpiceClientGLib.ChannelEvent.ERROR_CONNECT,
            SpiceClientGLib.ChannelEvent.ERROR_AUTH,
            SpiceClientGLib.ChannelEvent.ERROR_IO,
            SpiceClientGLib.ChannelEvent.ERROR_LINK,
            SpiceClientGLib.ChannelEvent.ERROR_TLS,
        ):
            GLib.idle_add(self._set_status, _("Session ended"))
            GLib.idle_add(self._btn_connect.set_sensitive, True)
            GLib.idle_add(self._btn_disconnect.set_sensitive, False)
        logger.debug("SpiceTab._on_spice_channel_event: fin")

    def _send_special_keys(self, key_names):
        """Envoie une combinaison de touches (Ctrl+Alt+Suppr, Alt+Tab…) à la session SPICE.

        Args:
            key_names (list[str]): noms de touches Gdk (ex: ["Control_L", "Alt_L", "Delete"]).
        """
        logger.debug("SpiceTab._send_special_keys: entrée")
        if self._spice_display is None:
            logger.debug("SpiceTab._send_special_keys: sortie")
            return
        keyvals = [Gdk.keyval_from_name(name) for name in key_names]
        self._spice_display.send_keys(keyvals, SpiceClientGtk.DisplayKeyEvent.PRESS)
        self._spice_display.send_keys(keyvals, SpiceClientGtk.DisplayKeyEvent.RELEASE)
        logger.debug("SpiceTab._send_special_keys: fin")

    def _on_usb_devices_clicked(self, widget):
        """Ouvre une boite de dialogue de redirection des peripheriques USB.

        Utilise le widget natif spice-gtk (SpiceClientGtk.UsbDeviceWidget), qui
        liste les peripheriques USB locaux et permet de les (de)rediriger vers
        la VM d'un simple clic. Necessite une session SPICE active avec
        "Partager les peripheriques USB" coche pour cet hote.

        Args:
            widget (Gtk.Button): Bouton declencheur.
        """
        logger.debug("SpiceTab._on_usb_devices_clicked: entrée")
        if self._spice_session is None:
            logger.debug("SpiceTab._on_usb_devices_clicked: sortie")
            return
        dialog = Gtk.Dialog(
            title=_("USB devices — {host}").format(host=self.host.name),
            transient_for=self.get_toplevel(),
            modal=True,
        )
        dialog.add_button(_("Fermer"), Gtk.ResponseType.CLOSE)
        dialog.set_default_size(480, 320)
        usb_widget = SpiceClientGtk.UsbDeviceWidget.new(self._spice_session, None)
        usb_widget.set_hexpand(True)
        usb_widget.set_vexpand(True)
        dialog.get_content_area().pack_start(usb_widget, True, True, 8)
        dialog.show_all()
        run_dialog_sync(dialog)
        dialog.destroy()
        logger.debug("SpiceTab._on_usb_devices_clicked: fin")

    def _disconnect_native(self):
        """Ferme la session spice-gtk en cours et retire le Display du conteneur."""
        logger.debug("SpiceTab._disconnect_native: entrée")
        self._spice_audio = None
        if self._iso_loop_device:
            umount_iso_temp(self._iso_loop_device)
            self._iso_loop_device = None
        if self._spice_session is not None:
            try:
                self._spice_session.disconnect()
            except Exception as exc:
                logger.debug("plugin spice | déconnexion SPICE impossible : {}", exc)
            self._spice_session = None
        if self._spice_display is not None:
            self._display_box.remove(self._spice_display)
            self._spice_display.destroy()
            self._spice_display = None
        # Déconnecter aussi le sous-processus proxmox/libvirt s'il tourne
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._set_status(_("Disconnect"))
        self._btn_connect.set_sensitive(True)
        self._btn_disconnect.set_sensitive(False)
        logger.debug("SpiceTab._disconnect_native: fin")

    # ── Fallback legacy : construction de la commande subprocess ────────────

    def _build_cmd(self):
        """Construit la commande remote-viewer depuis les attributs Host spice_*.

        Trois modes sélectionnés par host.spice_mode :
        - ``"proxmox"`` : ticket SPICE généré via pvesh sur l'hyperviseur
          (host=nœud PVE, user=root, spice_px_node, spice_px_vmid).
        - ``"libvirt"`` : tunnel libvirt natif virt-viewer
          (spice_libvirt_uri + spice_vm_name).
        - ``"uri"`` (défaut) : URI spice://host:port directe, avec TLS
          optionnel (spice_tls_port, spice_ca_cert).

        Returns:
            list[str]: Arguments pour subprocess.Popen, ou None si erreur.
        """

        logger.debug("SpiceTab._build_cmd: entrée")
        pairs = parse_spice_hosts(Path(CONFIG_FILE))
        try:
            HostsUpdater().update(pairs)
        except PermissionError as exc:
            app_logger.error(f"hosts update | {exc}")

        h = self.host
        port = str(getattr(h, "port", "5930") or "5930")
        pwd = self._get_password() or ""
        # _entry_opts n'existe qu'en mode fallback — accès défensif
        opts_str = self._entry_opts.get_text().strip() if hasattr(self, "_entry_opts") else ""
        spice_mode = (getattr(h, "spice_mode", "uri") or "uri").strip()

        # ── Mode Proxmox : génération de ticket SPICE via pvesh
        if spice_mode == "proxmox":
            node_name = (getattr(h, "spice_px_node", "") or "").strip()
            vmid = (getattr(h, "spice_px_vmid", "") or "").strip()
            if not node_name or not vmid:
                self._set_status(_("Error: Proxmox node and VMID required"))
                logger.debug("SpiceTab._build_cmd: sortie")
                return None
            # L'hôte SSH est le champ host standard (API Proxmox)
            hv_host = h.host
            hv_port = (
                int(h.port) if str(h.port).isdigit() and int(h.port) not in (5900, 5930) else 22
            )
            hv_user = h.user or "root"
            try:
                import paramiko as _paramiko

                _client = _paramiko.SSHClient()
                _client.set_missing_host_key_policy(_paramiko.AutoAddPolicy())
                _client.connect(hv_host, port=hv_port, username=hv_user, timeout=10)
                cmd_pvesh = (
                    f"pvesh create /nodes/{node_name}/qemu/{vmid}/spiceproxy --output-format json"
                )
                app_logger.debug(f"SPICE: SSH {hv_user}@{hv_host}:{hv_port} cmd = {cmd_pvesh}")
                _stdin, stdout, stderr = _client.exec_command(cmd_pvesh)
                out = stdout.read().decode().strip()
                _client.close()
            except Exception as exc:
                app_logger.debug(f"SPICE: SSH error: {exc}")
                self._set_status(_("SSH error: {exc}").format(exc=exc))
                logger.debug("SpiceTab._build_cmd: sortie")
                return None
            try:
                ticket = json.loads(out)
            except Exception as exc:
                logger.warning("plugin spice | ticket SPICE non décodable : {}", exc)
                self._set_status(_("SPICE ticket error: {out}").format(out=out[:80]))
                logger.debug("SpiceTab._build_cmd: sortie")
                return None
            vv_lines = [
                "[virt-viewer]",
                "type=spice",
                f"host={ticket['host']}",
                f"tls-port={ticket['tls-port']}",
                f"password={ticket['password']}",
                f"proxy={ticket.get('proxy', '')}",
                f"host-subject={ticket.get('host-subject', '')}",
                f"ca={ticket.get('ca', '')}",
                "delete-this-file=1",
            ]
            import tempfile as _tempfile

            vv_fd, vv_path = _tempfile.mkstemp(suffix=".vv", prefix="gcm-spice-")
            app_logger.debug(f"SPICE: fichier temporaire {vv_fd},{vv_path}")
            d = "\n"
            with os.fdopen(vv_fd, "w") as vv_f:
                vv_f.write(d.join(vv_lines) + d)

            # Le contenu du .vv (mot de passe du ticket) n'est jamais journalise.
            app_logger.debug(f"SPICE: fichier .vv ecrit ({len(vv_lines)} lignes)")
            app_logger.debug(f"SPICE: lancement remote-viewer {SPICE_BIN} {vv_path}")
            logger.debug("SpiceTab._build_cmd: sortie")
            return [SPICE_BIN, vv_path]

        # ── Mode libvirt : virt-viewer avec URI qemu+ssh://
        if spice_mode == "libvirt":
            libvirt_uri = (getattr(h, "spice_libvirt_uri", "") or "").strip()
            vm_name = (getattr(h, "spice_vm_name", "") or "").strip()
            if not libvirt_uri:
                self._set_status(_("Error: libvirt URI required"))
                logger.debug("SpiceTab._build_cmd: sortie")
                return None
            cmd = [SPICE_BIN, "--connect", libvirt_uri]

            if vm_name:
                cmd.append(vm_name)
            if opts_str:
                cmd += shlex.split(opts_str)
            app_logger.debug(f"SPICE: libvirt command = {mask_password(cmd)}")
            logger.debug("SpiceTab._build_cmd: sortie")
            return cmd

        # ── Mode URI directe : spice://host:port avec TLS optionnel
        if pwd:
            uri = f"spice://{h.host}?port={port}&password={pwd}"
        else:
            uri = f"spice://{h.host}?port={port}"
        cmd = [SPICE_BIN, uri]

        tls_port = (getattr(h, "spice_tls_port", "") or "").strip()
        ca_cert = (getattr(h, "spice_ca_cert", "") or "").strip()
        if tls_port:
            cmd.append(f"--spice-tls-port={tls_port}")
        if ca_cert:
            cmd.append(f"--spice-ca-file={ca_cert}")
        if opts_str:
            cmd += shlex.split(opts_str)
        app_logger.debug(f"SPICE: direct URI command = {mask_password(cmd)}")
        logger.debug("SpiceTab._build_cmd: sortie")
        return cmd

    def _on_connect(self, widget):
        """Lance la connexion SPICE (spice-gtk natif ou subprocess fallback).

        Args:
            widget (Gtk.Button): Bouton declencheur (peut etre None).
        """
        logger.debug("SpiceTab._on_connect: entrée")
        if _SPICEGTK_OK:
            self._connect_native()
            logger.debug("SpiceTab._on_connect: sortie")
            return

        # ── Mode fallback subprocess ────────────────────────────────────────
        if self._proc is not None and self._proc.poll() is None:
            logger.debug("SpiceTab._on_connect: sortie")
            return
        self.host.extra_params = self._entry_opts.get_text().strip()
        cmd = self._build_cmd()
        app_logger.debug(f"SpiceTab._on_connect | fallback cmd={mask_password(cmd)}")
        if cmd is None:
            logger.debug("SpiceTab._on_connect: sortie")
            return
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            app_logger.warning(f"SpiceTab._on_connect | viewer not found | bin={SPICE_BIN}")
            self._set_status(_("remote-viewer not found"))
            logger.debug("SpiceTab._on_connect: sortie")
            return
        except Exception as exc:
            app_logger.error(f"SpiceTab._on_connect | launch error | exc={exc}", exception=True)
            self._set_status(_("Error launching viewer"))
            logger.debug("SpiceTab._on_connect: sortie")
            return
        self._set_status(_("Connecting…"))
        self._btn_connect.set_sensitive(False)
        self._btn_disconnect.set_sensitive(True)
        threading.Thread(target=self._wait_proc, daemon=True).start()
        logger.debug("SpiceTab._on_connect: fin")

    def _wait_proc(self):
        """Attend la fin du processus SPICE en arriere-plan (mode fallback)."""
        logger.debug("SpiceTab._wait_proc: entrée")
        if self._proc:
            rc = self._proc.wait()
            if rc != 0:
                GLib.idle_add(self._set_status, _("Ended (code {rc})").format(rc=rc))
            else:
                GLib.idle_add(self._set_status, _("Session ended"))
        GLib.idle_add(self._btn_connect.set_sensitive, True)
        GLib.idle_add(self._btn_disconnect.set_sensitive, False)
        self._proc = None
        logger.debug("SpiceTab._wait_proc: fin")

    def _on_disconnect(self, widget):
        """Termine la session SPICE (spice-gtk natif ou subprocess fallback).

        Args:
            widget (Gtk.Button): Bouton declencheur.
        """
        logger.debug("SpiceTab._on_disconnect: entrée")
        if _SPICEGTK_OK:
            self._disconnect_native()
            logger.debug("SpiceTab._on_disconnect: sortie")
            return

        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._set_status(_("Disconnect"))
        self._btn_connect.set_sensitive(True)
        self._btn_disconnect.set_sensitive(False)
        logger.debug("SpiceTab._on_disconnect: fin")

    def _set_status(self, text: str) -> None:
        """Met a jour le label de statut.

        Args:
            text: Nouveau statut.
        """
        logger.debug("SpiceTab._set_status: entrée")
        app_logger.debug(f"SpiceTab._set_status | status={text}")
        self._lbl_status.set_text(text)
        logger.debug("SpiceTab._set_status: fin")

    def connect_spice(self):
        """Lance la connexion SPICE automatiquement (appele depuis addTab)."""
        logger.debug("SpiceTab.connect_spice: entrée")
        self._on_connect(None)
        logger.debug("SpiceTab.connect_spice: fin")

    def build_context_menu(self):
        """Construit le menu contextuel (clic droit sur l'onglet) de cet onglet SPICE.

        Uniquement disponible en mode natif (spice-gtk) ; en mode fallback,
        l'onglet conserve ses boutons historiques dans la barre d'outils.

        Returns:
            Gtk.Menu | None: Connect / Disconnect / Peripheriques USB (si
                autorises) / Touches spéciales, ou None en mode fallback.
        """
        logger.debug("SpiceTab.build_context_menu: entrée")
        if not _SPICEGTK_OK:
            logger.debug("SpiceTab.build_context_menu: sortie")
            return None
        extra_items = []
        if self._btn_usb.get_visible():
            extra_items.append((_("USB devices…"), lambda _w: self._on_usb_devices_clicked(None)))
        logger.debug("SpiceTab.build_context_menu: sortie")
        return build_remote_desktop_context_menu(
            self._btn_connect,
            self._btn_disconnect,
            self._send_special_keys,
            extra_items=extra_items,
        )


class SpicePlugin(ConnectionPlugin):
    """Plugin pour le protocole ``spice``."""

    protocol_id = "spice"
    display_name = _("SPICE")
    default_port = 5930
    icon_name = "video-display-symbolic"
    ui_order = 4

    def __init__(self) -> None:
        """Initialise les références de widgets du formulaire SPICE (peuplées par ``build_edit_page()``)."""
        logger.debug("SpicePlugin.__init__: entrée")
        self._radio_uri: Gtk.RadioButton | None = None
        self._radio_libvirt: Gtk.RadioButton | None = None
        self._radio_proxmox: Gtk.RadioButton | None = None
        self._grid_uri: Gtk.Widget | None = None
        self._grid_libvirt: Gtk.Widget | None = None
        self._grid_proxmox: Gtk.Widget | None = None
        self._txt_tls_port: Gtk.Entry | None = None
        self._txt_ca_cert: Gtk.Entry | None = None
        self._txt_libvirt_uri: Gtk.Entry | None = None
        self._txt_vm_name: Gtk.Entry | None = None
        self._txt_px_node: Gtk.Entry | None = None
        self._txt_px_vmid: Gtk.Entry | None = None
        self._chk_share_clipboard: Gtk.CheckButton | None = None
        self._chk_share_usb: Gtk.CheckButton | None = None
        self._chk_share_audio: Gtk.CheckButton | None = None
        self._chk_share_folder: Gtk.CheckButton | None = None
        self._txt_shared_folder_path: Gtk.Entry | None = None
        logger.debug("SpicePlugin.__init__: fin")

    def build_tab(self, host, get_password: Callable[[], str] | None = None) -> Gtk.Widget:
        """Construit l'onglet de session SPICE (délègue à ``SpiceTab``).

        Args:
            host: Instance ``Host`` avec ``protocol == "spice"``.
            get_password: Callable retournant le mot de passe déchiffré.

        Returns:
            Une instance de ``SpiceTab`` (inchangée).
        """
        logger.debug(f"SpicePlugin.build_tab | host={host.name}")
        return SpiceTab(host, get_password)

    def build_edit_page(self) -> Gtk.Widget:
        """Construit la page de champs Edit Host pour SPICE.

        Returns:
            Un ``Gtk.Box`` autonome reproduisant ``gridSpiceProps`` (3 modes
            mutuellement exclusifs + options de partage communes).
        """
        logger.debug("SpicePlugin.build_edit_page: entrée")
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        outer.set_margin_top(8)

        radio_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._radio_uri = Gtk.RadioButton.new_with_label_from_widget(None, _("Direct URI"))
        self._radio_libvirt = Gtk.RadioButton.new_with_label_from_widget(
            self._radio_uri, _("Via libvirt")
        )
        self._radio_proxmox = Gtk.RadioButton.new_with_label_from_widget(
            self._radio_uri, _("Via Proxmox")
        )
        radio_box.pack_start(self._radio_uri, False, False, 0)
        radio_box.pack_start(self._radio_libvirt, False, False, 0)
        radio_box.pack_start(self._radio_proxmox, False, False, 0)
        outer.pack_start(radio_box, False, False, 0)

        self._radio_uri.connect("toggled", self._on_mode_toggled)
        self._radio_libvirt.connect("toggled", self._on_mode_toggled)
        self._radio_proxmox.connect("toggled", self._on_mode_toggled)

        def make_grid(*rows):
            logger.debug("make_grid: entrée")
            g = Gtk.Grid()
            g.set_row_spacing(4)
            g.set_column_spacing(6)
            g.set_margin_start(10)
            for i, (label_text, widget) in enumerate(rows):
                lbl = Gtk.Label(label=label_text)
                lbl.set_halign(Gtk.Align.START)
                g.attach(lbl, 0, i, 1, 1)
                widget.set_hexpand(True)
                g.attach(widget, 1, i, 1, 1)
            logger.debug("make_grid: sortie")
            return g

        self._txt_tls_port = Gtk.Entry()
        self._txt_ca_cert = Gtk.Entry()
        self._grid_uri = make_grid(
            (_("TLS port"), self._txt_tls_port), (_("CA certificate"), self._txt_ca_cert)
        )
        outer.pack_start(self._grid_uri, False, False, 0)

        self._txt_libvirt_uri = Gtk.Entry()
        self._txt_vm_name = Gtk.Entry()
        self._grid_libvirt = make_grid(
            (_("libvirt URI"), self._txt_libvirt_uri), (_("VM name"), self._txt_vm_name)
        )
        outer.pack_start(self._grid_libvirt, False, False, 0)

        self._txt_px_node = Gtk.Entry()
        self._txt_px_vmid = Gtk.Entry()
        self._grid_proxmox = make_grid(
            (_("Proxmox node"), self._txt_px_node), (_("VMID"), self._txt_px_vmid)
        )
        outer.pack_start(self._grid_proxmox, False, False, 0)

        sharing_grid = Gtk.Grid()
        sharing_grid.set_row_spacing(4)
        sharing_grid.set_margin_top(6)
        self._chk_share_clipboard = Gtk.CheckButton(label=_("Share clipboard"))
        self._chk_share_clipboard.set_active(True)
        self._chk_share_usb = Gtk.CheckButton(label=_("Share USB devices"))
        self._chk_share_audio = Gtk.CheckButton(label=_("Share audio"))
        self._chk_share_folder = Gtk.CheckButton(label=_("Share folder"))
        sharing_grid.attach(self._chk_share_clipboard, 0, 0, 1, 1)
        sharing_grid.attach(self._chk_share_usb, 0, 1, 1, 1)
        sharing_grid.attach(self._chk_share_audio, 0, 2, 1, 1)
        sharing_grid.attach(self._chk_share_folder, 0, 3, 1, 1)
        self._txt_shared_folder_path = Gtk.Entry()
        self._txt_shared_folder_path.set_hexpand(True)
        sharing_grid.attach(Gtk.Label(label=_("Shared folder path")), 0, 4, 1, 1)
        sharing_grid.attach(self._txt_shared_folder_path, 1, 4, 1, 1)
        outer.pack_start(sharing_grid, False, False, 0)

        outer.show_all()
        self._sync_mode_visibility()  # applique l'exclusivité mutuelle initiale (mode "uri" par défaut)
        logger.debug("SpicePlugin.build_edit_page: sortie")
        return outer

    def _on_mode_toggled(self, widget: Gtk.RadioButton) -> None:
        """Bascule la sous-grille visible selon le mode SPICE choisi.

        Args:
            widget: Le bouton radio qui vient de changer d'état (ignoré —
                on relit l'état des trois pour rester simple et robuste).
        """
        logger.debug("SpicePlugin._on_mode_toggled: entrée")
        if widget.get_active():
            self._sync_mode_visibility()
        logger.debug("SpicePlugin._on_mode_toggled: fin")

    def _sync_mode_visibility(self) -> None:
        """Affiche la sous-grille correspondant au mode actif, masque les deux autres."""
        logger.debug("SpicePlugin._sync_mode_visibility: entrée")
        is_libvirt = self._radio_libvirt is not None and self._radio_libvirt.get_active()
        is_proxmox = self._radio_proxmox is not None and self._radio_proxmox.get_active()
        is_uri = not is_libvirt and not is_proxmox
        if self._grid_uri is not None:
            self._grid_uri.set_visible(is_uri)
        if self._grid_libvirt is not None:
            self._grid_libvirt.set_visible(is_libvirt)
        if self._grid_proxmox is not None:
            self._grid_proxmox.set_visible(is_proxmox)
        logger.debug("SpicePlugin._sync_mode_visibility: fin")

    def load_host_fields(self, host) -> None:
        """Recharge tous les champs SPICE depuis *host*, y compris le mode actif.

        Args:
            host: Instance ``Host`` source.
        """
        logger.debug("SpicePlugin.load_host_fields: entrée")
        mode = getattr(host, "spice_mode", "uri") or "uri"
        if mode == "libvirt" and self._radio_libvirt is not None:
            self._radio_libvirt.set_active(True)
        elif mode == "proxmox" and self._radio_proxmox is not None:
            self._radio_proxmox.set_active(True)
        elif self._radio_uri is not None:
            self._radio_uri.set_active(True)
        self._sync_mode_visibility()

        if self._txt_tls_port is not None:
            self._txt_tls_port.set_text(getattr(host, "spice_tls_port", "") or "")
        if self._txt_ca_cert is not None:
            self._txt_ca_cert.set_text(getattr(host, "spice_ca_cert", "") or "")
        if self._txt_libvirt_uri is not None:
            self._txt_libvirt_uri.set_text(str(getattr(host, "spice_libvirt_uri", "") or ""))
        if self._txt_vm_name is not None:
            self._txt_vm_name.set_text(str(getattr(host, "spice_vm_name", "") or ""))
        if self._txt_px_node is not None:
            self._txt_px_node.set_text(str(getattr(host, "spice_px_node", "") or ""))
        if self._txt_px_vmid is not None:
            self._txt_px_vmid.set_text(str(getattr(host, "spice_px_vmid", "") or ""))
        if self._chk_share_clipboard is not None:
            self._chk_share_clipboard.set_active(
                _bool_from_str(getattr(host, "spice_share_clipboard", True))
            )
        if self._chk_share_usb is not None:
            self._chk_share_usb.set_active(_bool_from_str(getattr(host, "spice_share_usb", False)))
        if self._chk_share_audio is not None:
            self._chk_share_audio.set_active(
                _bool_from_str(getattr(host, "spice_share_audio", False))
            )
        if self._chk_share_folder is not None:
            self._chk_share_folder.set_active(
                _bool_from_str(getattr(host, "spice_share_folder", False))
            )
        if self._txt_shared_folder_path is not None:
            self._txt_shared_folder_path.set_text(
                getattr(host, "spice_shared_folder_path", "") or ""
            )
        logger.debug(
            f"SpicePlugin.load_host_fields | host={getattr(host, 'name', '?')} mode={mode}"
        )
        logger.debug("SpicePlugin.load_host_fields: fin")

    def save_host_fields(self, host) -> None:
        """Reporte tous les champs SPICE dans *host*, y compris le mode actif.

        Args:
            host: Instance ``Host`` à mettre à jour.
        """
        logger.debug("SpicePlugin.save_host_fields: entrée")
        if self._radio_libvirt is not None and self._radio_libvirt.get_active():
            host.spice_mode = "libvirt"
        elif self._radio_proxmox is not None and self._radio_proxmox.get_active():
            host.spice_mode = "proxmox"
        else:
            host.spice_mode = "uri"
        host.spice_tls_port = self._txt_tls_port.get_text().strip() if self._txt_tls_port else ""
        host.spice_ca_cert = self._txt_ca_cert.get_text().strip() if self._txt_ca_cert else ""
        host.spice_libvirt_uri = (
            self._txt_libvirt_uri.get_text().strip() if self._txt_libvirt_uri else ""
        )
        host.spice_vm_name = self._txt_vm_name.get_text().strip() if self._txt_vm_name else ""
        host.spice_px_node = self._txt_px_node.get_text().strip() if self._txt_px_node else ""
        host.spice_px_vmid = self._txt_px_vmid.get_text().strip() if self._txt_px_vmid else ""
        host.spice_share_clipboard = (
            self._chk_share_clipboard.get_active() if self._chk_share_clipboard else True
        )
        host.spice_share_usb = self._chk_share_usb.get_active() if self._chk_share_usb else False
        host.spice_share_audio = (
            self._chk_share_audio.get_active() if self._chk_share_audio else False
        )
        host.spice_share_folder = (
            self._chk_share_folder.get_active() if self._chk_share_folder else False
        )
        host.spice_shared_folder_path = (
            self._txt_shared_folder_path.get_text().strip() if self._txt_shared_folder_path else ""
        )
        logger.debug(f"SpicePlugin.save_host_fields | host={getattr(host, 'name', '?')}")
        logger.debug("SpicePlugin.save_host_fields: fin")

    def host_fields(self) -> list[tuple[str, object]]:
        """Champs ``Host`` propres au protocole SPICE."""
        return [
            ("spice_mode", "uri"),
            ("spice_tls_port", ""),
            ("spice_ca_cert", ""),
            ("spice_libvirt_uri", ""),
            ("spice_vm_name", ""),
            ("spice_px_node", ""),
            ("spice_px_vmid", ""),
            ("spice_share_clipboard", True),
            ("spice_share_usb", False),
            ("spice_share_audio", False),
            ("spice_share_folder", False),
            ("spice_shared_folder_path", ""),
        ]


def get_plugin() -> SpicePlugin:
    """Point d'entree standard utilise par PluginRegistry.autoload().

    Returns:
        Une instance de SpicePlugin, prete a etre enregistree.
    """
    return SpicePlugin()
