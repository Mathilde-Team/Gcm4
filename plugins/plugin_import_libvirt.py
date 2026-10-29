# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from libvirt » (BatchPlugin) pour GCM.

Anciennement du code intégré directement dans ``gnome_connection_manager.py``
(fonction ``_libvirt_fetch_hosts`` + classe ``LibvirtImportDialog``,
appelées depuis ``Wmain.on_mnu_import_libvirt_activate``). Porté ici comme
second exemple de ``BatchPlugin`` (après ``plugin_netmiko_push.py``) :
découvert et enregistré automatiquement par
``BatchPluginRegistry.autoload()`` (cf. ``plugin_base.py``) via
``get_batch_plugin()`` tout en bas de ce fichier — aucun câblage manuel
n'est nécessaire dans ``gnome_connection_manager.py``.

Ce plugin scanne un ou plusieurs hyperviseurs libvirt (via SSH + `virsh`),
retrouve l'IP de chaque VM par bail DHCP puis, à défaut, par corrélation
MAC/nmap, sonde RDP/VNC, et présente le résultat dans un dialogue de
sélection avant import. La logique bas niveau commune à l'import Proxmox
(connexion SSH par clé, scan réseau, sonde de port) vit dans
``hypervisor_import_common.py`` — voir ce module pour le pourquoi de son
absence de préfixe ``plugin_``.
"""

from __future__ import annotations

import re
import threading

import gi
from loguru import logger

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

# isort: split
import hypervisor_import_common as hv_common  # noqa: E402, I001
from plugin_base import BatchPlugin  # noqa: E402, I001
from utils import GCMBase, msgbox  # noqa: E402, I001

# Accès à la variable globale 'groups' du module principal pour vérifier
# si un hôte existe déjà — import paresseux (ici plutôt qu'en haut du
# fichier) pour éviter une dépendance circulaire au chargement du module.
try:
    from gnome_connection_manager import groups  # noqa: E402, F401
except (ImportError, NameError) as exc:
    logger.debug("import libvirt | groupes du module principal indisponibles : {}", exc)
    groups = {}  # noqa: F841

# Utilise le _() global injecté par bindtextdomain() si présent, sinon no-op
# — même convention que plugin_netmiko_push.py (ce module doit rester
# important-safe même si le domaine gettext de l'appli n'est pas encore
# initialisé, par ex. lors de tests unitaires isolés).
if "_" not in globals():

    def _(s: str) -> str:  # noqa: D401
        """Fallback no-op translation for isolated/test contexts."""
        return s


__all__ = [
    "LibvirtPrefsTab",
    "build_preferences_tab",
    "LibvirtImportBatchPlugin",
    "LibvirtImportDialog",
    "get_batch_plugin",
]


class LibvirtPrefsTab:
    """Onglet de préférences UI pour le plugin d'import Libvirt.

    Configure l'utilisateur SSH par défaut utilisé lors des imports.
    """

    def __init__(self, notebook):
        """Initialise l'onglet de préférences libvirt.

        Args:
            notebook: Widget Gtk.Notebook auquel ajouter l'onglet.
        """
        self._build(notebook)

    def _build(self, notebook):
        """Construit l'interface de l'onglet.

        Args:
            notebook: Widget Gtk.Notebook récepteur.
        """
        from utils import conf

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_start(10)
        outer.set_margin_end(10)
        outer.set_margin_top(10)
        outer.set_margin_bottom(10)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl = Gtk.Label(label=_("Default SSH user (VMs):"))
        lbl.set_xalign(0)
        row.pack_start(lbl, False, False, 0)

        self._entry_user = Gtk.Entry()
        self._entry_user.set_text(getattr(conf, "LIBVIRT_DEFAULT_USER", "root") or "root")
        self._entry_user.set_tooltip_text(_("Used as default user in the Libvirt import dialog."))
        row.pack_start(self._entry_user, True, True, 0)
        outer.pack_start(row, False, False, 0)

        help_lbl = Gtk.Label()
        help_lbl.set_xalign(0)
        help_lbl.set_line_wrap(True)
        help_lbl.set_markup(
            _(
                "<i>Applies only to the Libvirt import plugin. Can still be overridden per import run.</i>"
            )
        )
        outer.pack_start(help_lbl, False, False, 0)

        notebook.append_page(outer, Gtk.Label(label=_("Libvirt")))
        outer.show_all()
        notebook.show_all()

    def apply(self):
        """Valide et enregistre les préférences dans la configuration.

        Returns:
            None: Les valeurs sont sauvegardées dans conf.LIBVIRT_DEFAULT_USER.
        """
        from utils import conf

        conf.LIBVIRT_DEFAULT_USER = (self._entry_user.get_text() or "").strip() or "root"


def build_preferences_tab(notebook):
    """Construit l'onglet de préférences Libvirt dans le notebook donné.

    Args:
        notebook: Widget Gtk.Notebook récepteur.

    Returns:
        LibvirtPrefsTab: Instance de l'onglet (pour accès ultérieur si besoin).
    """
    return LibvirtPrefsTab(notebook)


def _libvirt_fetch_hosts(uris, ssh_user, log_fn, progress_fn, proto_filter=None):
    """Collecte les VMs libvirt via SSH sur les hyperviseurs donnés.

    Résolution IP : domifaddr (agent/arp) → DHCP leases → ARP noyau → nmap.

    Règles de génération des entrées :
    - SSH : jamais direct ; ProxyJump (-J) si IP connue, sinon -t ssh user@vm_name
      via le shell de l'hyperviseur.  Le port de l'hyperviseur est toujours
      respecté (ex : qemu+ssh://root@ip:542/system → ProxyJump port 542).
    - SPICE : virt-viewer --connect qemu+ssh://user@host[:port]/system vm_name
      (tunnel SSH géré nativement par virt-viewer/remote-viewer).
    - RDP : seulement si port 3389 détecté ouvert depuis l'hyperviseur (nc/nmap).

    Args:
        uris (list[str]): URIs libvirt (qemu+ssh://...).
        ssh_user (str): Utilisateur SSH pour se connecter aux VMs.
        log_fn (callable): Fonction de log (msg: str).
        progress_fn (callable): Progression (frac: float, texte: str).
        proto_filter (set[str] | None): {'ssh','spice','rdp'} ou None = tous.

    Returns:
        list[dict]: Hôtes prêts à importer dans GCM.
    """
    if not hv_common.PARAMIKO_OK:
        log_fn(
            "ERREUR : paramiko non installé → "
            + hv_common.dep_install_hint("python3-paramiko", "python3-paramiko", "python-paramiko")
        )
        return []
    if proto_filter is None:
        proto_filter = {"ssh", "spice", "rdp"}
    results = []
    total = len(uris)

    def run(client, cmd, timeout=30):
        return hv_common.libvirt_ssh_run(client, cmd, timeout)

    for idx, uri in enumerate(uris):
        log_fn(f"\n── Hyperviseur : {uri}")
        progress_fn(idx / total, f"Connexion à {uri}…")
        parsed = urlparse(uri)
        scheme = parsed.scheme
        transport = (
            scheme.split("+")[1] if "+" in scheme else ("tcp" if parsed.hostname else "local")
        )
        hv_host = parsed.hostname or "localhost"
        hv_port = parsed.port or 22
        hv_user = parsed.username or "root"
        if transport == "local":
            log_fn("  → URI locale ignorée (qemu:///system).")
            continue
        client = hv_common.paramiko_connect(hv_host, hv_port, hv_user, log_fn)
        if client is None:
            continue
        try:
            vm_names = [
                n for n in run(client, "virsh list --all --name").splitlines() if n.strip()
            ]
            log_fn(f"  {len(vm_names)} VM(s) trouvée(s)")

            # ── ARP noyau ────────────────────────────────────────────────────
            arp = {}
            for line in run(
                client, "ip neigh show 2>/dev/null || arp -n 2>/dev/null"
            ).splitlines():
                m = re.search(
                    r"(\d+\.\d+\.\d+\.\d+).*?([0-9a-f]{2}(?::[0-9a-f]{2}){5})",
                    line,
                    re.I,
                )
                if m:
                    arp[m.group(2).lower()] = m.group(1)

            # ── DHCP leases virsh ────────────────────────────────────────────
            dhcp = {}
            for net in run(client, "virsh net-list --name").splitlines():
                net = net.strip()
                if not net:
                    continue
                for line in run(client, f"virsh net-dhcp-leases {net} 2>/dev/null").splitlines():
                    parts = line.split()
                    if len(parts) >= 4 and ":" in parts[1]:
                        ip_raw = parts[3].split("/")[0]
                        if re.match(r"\d+\.\d+\.\d+\.\d+", ip_raw):
                            dhcp[parts[1].lower()] = ip_raw

            # ── nmap ping scan ────────────────────────────────────────────────
            nmap_table = hv_common.libvirt_nmap_scan(client, run, log_fn)
            combined = {**arp, **dhcp, **nmap_table}
            log_fn(f"  Table MAC→IP : {len(combined)} entrées")

            # ── enumération des VMs ──────────────────────────────────────────
            for vm_name in vm_names:
                state = run(client, f"virsh domstate {vm_name}").strip()
                xml = run(client, f"virsh dumpxml {vm_name}")
                ip_addr = ""
                spice_port = ""

                # Port SPICE depuis le XML
                m_spice = re.search(
                    r'<graphics[^>]+type=[\'"]spice[\'"][^>]*port=[\'"](\d+)[\'"]',
                    xml,
                )
                if m_spice and m_spice.group(1) != "-1":
                    spice_port = m_spice.group(1)

                # Résolution IP via interfaces XML
                for block in re.findall(r"<interface.*?</interface>", xml, re.DOTALL):
                    mac_m = re.search(r"<mac address=['\"]([^'\"]+)['\"]", block)
                    if not mac_m:
                        continue
                    mac = mac_m.group(1).lower()
                    if state == "running":
                        for src in ("agent", "arp"):
                            out2 = run(
                                client,
                                f"virsh domifaddr {vm_name} --source {src} 2>/dev/null",
                            )
                            for ln in out2.splitlines():
                                if mac in ln.lower():
                                    m2 = re.search(r"(\d+\.\d+\.\d+\.\d+)", ln)
                                    if m2:
                                        ip_addr = m2.group(1)
                                        break
                            if ip_addr:
                                break
                    if not ip_addr:
                        ip_addr = combined.get(mac) or ""
                    if ip_addr:
                        break

                grp, short = hv_common.vm_name_split(vm_name)
                is_windows = bool(
                    re.search(
                        r"win|w(?:2k|2019|2022|2016|2012|srv|dc|server)",
                        vm_name,
                        re.IGNORECASE,
                    )
                )
                vm_user = "Administrator" if is_windows else ssh_user
                added = False

                # ── SSH : toujours via l'hyperviseur (jamais direct) ──────────
                if "ssh" in proto_filter:
                    if ip_addr:
                        if hv_port != 22:
                            jump_flag = f"-J {hv_user}@{hv_host}:{hv_port}"
                        else:
                            jump_flag = f"-J {hv_user}@{hv_host}"
                        results.append(
                            {
                                "name": short,
                                "host": ip_addr,
                                "user": vm_user,
                                "port": 22,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} — SSH ProxyJump via {hv_user}@{hv_host}:{hv_port} → {ip_addr}"
                                ),
                                "group": f"{grp}/ssh" if grp else "ssh",
                                "hypervisor": hv_host,
                                "protocol": "ssh",
                                "extra_params": jump_flag,
                            }
                        )
                    else:
                        post_cmd = f"-t ssh {vm_user}@{vm_name}"
                        results.append(
                            {
                                "name": short,
                                "host": hv_host,
                                "user": hv_user,
                                "port": hv_port,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} — SSH via hyperviseur "
                                    f"{hv_host}:{hv_port} → ssh {vm_user}@{vm_name} (IP inconnue)"
                                ),
                                "group": f"{grp}/ssh" if grp else "ssh",
                                "hypervisor": hv_host,
                                "protocol": "ssh",
                                "extra_params": post_cmd,
                            }
                        )
                    log_fn(
                        f"  + [{grp}] {short:28s}  {ip_addr or '(IP inconnue)':16s}  [{state}]  SSH"
                    )
                    added = True

                # ── SPICE : virt-viewer --connect libvirt URI (tunnel SSH auto) ─
                if "spice" in proto_filter and spice_port:
                    if hv_port != 22:
                        lv_uri = f"qemu+ssh://{hv_user}@{hv_host}:{hv_port}/system"
                    else:
                        lv_uri = f"qemu+ssh://{hv_user}@{hv_host}/system"
                    results.append(
                        {
                            "name": short,
                            "host": hv_host,
                            "user": hv_user,
                            "port": int(spice_port),
                            "password": "",
                            "description": (
                                f"[{state}] {vm_name} — SPICE via {lv_uri} (tunnel SSH géré par virt-viewer)"
                            ),
                            "group": f"{grp}/spice" if grp else "spice",
                            "hypervisor": hv_host,
                            "protocol": "spice",
                            "extra_params": f"--connect {lv_uri} {vm_name}",
                        }
                    )
                    log_fn(
                        f"  + [{grp}] {short:28s}  port SPICE {spice_port:>5s}  [{state}]  SPICE"
                    )
                    added = True

                # ── RDP : seulement si port 3389 ouvert (sondé depuis l'HV) ──
                if "rdp" in proto_filter and ip_addr:
                    log_fn(f"  ↳ Sondage RDP 3389 sur {ip_addr}…")
                    if hv_common.check_port_open(client, run, ip_addr, 3389):
                        results.append(
                            {
                                "name": short,
                                "host": ip_addr,
                                "user": vm_user,
                                "port": 3389,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} — RDP (port 3389 confirmé ouvert)"
                                ),
                                "group": f"{grp}/rdp" if grp else "rdp",
                                "hypervisor": hv_host,
                                "protocol": "rdp",
                                "extra_params": "",
                            }
                        )
                        log_fn(f"  + [{grp}] {short:28s}  {ip_addr:16s}  [{state}]  RDP ✓")
                        added = True
                    else:
                        log_fn(f"  ↳ port 3389 fermé sur {ip_addr} — RDP ignoré")

                # ── VNC : seulement si port 5900 ouvert (sondé depuis l'HV) ──
                if "vnc" in proto_filter and ip_addr:
                    log_fn(f"  ↳ Sondage VNC 5900 sur {ip_addr}…")
                    if hv_common.check_port_open(client, run, ip_addr, 5900):
                        results.append(
                            {
                                "name": short,
                                "host": ip_addr,
                                "user": vm_user,
                                "port": 5900,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} — VNC (port 5900 confirmé ouvert)"
                                ),
                                "group": f"{grp}/vnc" if grp else "vnc",
                                "hypervisor": hv_host,
                                "protocol": "vnc",
                                "extra_params": "",
                            }
                        )
                        log_fn(f"  + [{grp}] {short:28s}  {ip_addr:16s}  [{state}]  VNC ✓")
                        added = True
                    else:
                        log_fn(f"  ↳ port 5900 fermé sur {ip_addr} — VNC ignoré")

                if not added:
                    log_fn(f"  - {vm_name} : aucune connexion générée")

        finally:
            client.close()
        progress_fn((idx + 1) / total, f"{idx + 1}/{total} hyperviseurs traités")
    return results


class LibvirtImportDialog(GCMBase):
    """Dialogue GTK3 d'import de VMs depuis les hyperviseurs libvirt.

    Flux en deux phases :
    1. Paramètres : URIs, user SSH, types de protocoles (SSH/SPICE/RDP).
       → bouton « Scanner »
    2. Tableau de prévisualisation : connexions découvertes, avec marquage
       des entrées déjà présentes dans GCM, cases à cocher, import sélectif.

    SSH  : jamais direct – ProxyJump (-J) si IP connue, sinon shell HV + -t.
    SPICE: virt-viewer/remote-viewer --connect qemu+ssh://… (tunnel auto).
    RDP  : uniquement si port 3389 confirmé ouvert (sondé depuis l'HV).
    """

    # Colonnes du ListStore de prévisualisation
    _COL_SEL = 0  # bool — à importer ?
    _COL_PROTO = 1  # str  — SSH / SPICE / RDP
    _COL_NAME = 2  # str  — nom court
    _COL_GROUP = 3  # str  — groupe GCM
    _COL_HOST = 4  # str  — IP ou hostname
    _COL_STATE = 5  # str  — état VM (running / shut off / …)
    _COL_EXISTS = 6  # bool — déjà présent dans GCM
    _COL_EXIST_LBL = 7  # str  — "✓ existe" ou ""
    _COL_FG = 8  # str  — couleur foreground
    _COL_IDX = 9  # int  — index dans self._discovered

    def __init__(self, parent_window, on_done_callback, default_user="root", show=True):
        """Initialise le dialogue d'import libvirt.

        Args:
            parent_window (Gtk.Window): Fenêtre parente.
            on_done_callback (callable): Appelée avec list[dict] lors de l'import.
            default_user (str): Utilisateur SSH par défaut.
            show (bool): Si False, le dialogue n'est pas affiché
                automatiquement (utilisé quand l'écran sera embarqué comme
                onglet épinglé, cf. ``Wmain.open_management_tab``).
        """
        self.parent = parent_window
        self.on_done = on_done_callback
        self.default_user = default_user
        self._discovered = []
        GCMBase.__init__(self, path=None, parent=parent_window, show=show)
        self._build_ui(show=show)

    # ──────────────────────────────────────────────────────────────────────────
    # Construction de l'UI
    # ──────────────────────────────────────────────────────────────────────────

    def _build_ui(self, show=True):
        """Construit l'interface utilisateur du dialogue d'import libvirt.

        Crée un Gtk.Stack à 3 phases : configuration (URI), suivi
        (progression du scan), résultats (grille de prévisualisation).

        Args:
            show: Si True, affiche le dialogue après construction.
        """
        dlg = Gtk.Dialog(
            title=_("Import from libvirt"),
            transient_for=self.parent,
            modal=True,
        )
        dlg.set_default_size(820, 720)
        self.main_widget = dlg
        self._dlg = dlg
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        stack_switcher = Gtk.StackSwitcher(stack=self._stack)
        stack_switcher.set_halign(Gtk.Align.CENTER)
        box.pack_start(stack_switcher, False, False, 0)
        box.pack_start(self._stack, True, True, 0)

        # ── Phase 1 : Paramètres de scan ─────────────────────────────────────
        self._scan_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        # User SSH hyperviseur
        hb_user = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hb_user.pack_start(Gtk.Label(label=_("Hypervisor SSH user:")), False, False, 0)
        self._entry_user = Gtk.Entry()
        self._entry_user.set_text(self.default_user)
        self._entry_user.set_width_chars(12)
        hb_user.pack_start(self._entry_user, False, False, 0)
        self._scan_box.pack_start(hb_user, False, False, 0)

        # Liste des URIs libvirt
        lbl_uri = Gtk.Label(label=_("Hypervisors (virt-manager / dconf):"))
        lbl_uri.set_xalign(0)
        self._scan_box.pack_start(lbl_uri, False, False, 2)
        self._lbl_uri = lbl_uri

        self._uri_store = Gtk.ListStore(bool, str)
        tv_uri = Gtk.TreeView(model=self._uri_store)
        tv_uri.set_headers_visible(True)
        cr_toggle = Gtk.CellRendererToggle()
        cr_toggle.connect("toggled", self._on_uri_toggled)
        tv_uri.append_column(Gtk.TreeViewColumn("", cr_toggle, active=0))
        col_uri_txt = Gtk.TreeViewColumn(_("Detected libvirt URI"), Gtk.CellRendererText(), text=1)
        col_uri_txt.set_expand(True)
        tv_uri.append_column(col_uri_txt)
        self._col_uri_txt = col_uri_txt
        sw_uri = Gtk.ScrolledWindow()
        sw_uri.set_min_content_height(150)
        sw_uri.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw_uri.add(tv_uri)
        self._scan_box.pack_start(sw_uri, False, False, 0)

        # Saisie manuelle d'une cible libvirt (en plus de dconf)
        hb_manual = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl_manual_uri = Gtk.Label(label=_("Import from libvirt"))
        hb_manual.pack_start(lbl_manual_uri, False, False, 0)
        self._lbl_manual_uri = lbl_manual_uri
        self._entry_manual_uri = Gtk.Entry()
        self._entry_manual_uri.set_placeholder_text(
            _("Ex: 192.168.105.41 | root@192.168.105.41 | qemu+ssh://root@192.168.105.41/system")
        )
        self._entry_manual_uri.connect("activate", self._on_add_manual_uri)
        hb_manual.pack_start(self._entry_manual_uri, True, True, 0)
        btn_add_uri = Gtk.Button(label=_("Add"))
        btn_add_uri.connect("clicked", self._on_add_manual_uri)
        hb_manual.pack_start(btn_add_uri, False, False, 0)
        self._scan_box.pack_start(hb_manual, False, False, 0)

        # Protocoles
        lbl_proto = Gtk.Label()
        lbl_proto.set_markup(_("<b>Connection types to import:</b>"))
        lbl_proto.set_xalign(0)
        self._scan_box.pack_start(lbl_proto, False, False, 4)

        proto_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._chk_ssh = Gtk.CheckButton(
            label=_(
                "SSH  (ProxyJump -J via hypervisor when VM IP is known, otherwise hypervisor shell + -t ssh user@vm)"
            )
        )
        self._chk_ssh.set_active(True)
        self._chk_spice = Gtk.CheckButton(
            label=_(
                "SPICE  (virt-viewer --connect qemu+ssh://... vm - SSH tunnel handled automatically)"
            )
        )
        self._chk_spice.set_active(True)
        self._chk_rdp = Gtk.CheckButton(
            label=_(
                "RDP  (probe port 3389 from hypervisor via nc/nmap - skipped if port is closed)"
            )
        )
        self._chk_rdp.set_active(True)
        self._chk_vnc = Gtk.CheckButton(
            label=_(
                "VNC  (probe port 5900 from hypervisor via nc/nmap - skipped if port is closed)"
            )
        )
        self._chk_vnc.set_active(False)
        for chk in (self._chk_ssh, self._chk_spice, self._chk_rdp, self._chk_vnc):
            proto_box.pack_start(chk, False, False, 0)
        self._scan_box.pack_start(proto_box, False, False, 0)

        # Zone de log (phase 2: suivi)
        self._log_buf = Gtk.TextBuffer()
        lv = Gtk.TextView(buffer=self._log_buf)
        lv.set_editable(False)
        lv.set_monospace(True)
        lv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        sw_log = Gtk.ScrolledWindow()
        sw_log.set_min_content_height(100)
        sw_log.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw_log.add(lv)
        self._log_view = lv

        # Barre de progression
        self._progress = Gtk.ProgressBar()
        self._progress.set_show_text(True)
        self._progress.set_text(_("Waiting…"))

        # Bouton Scanner (aligné à droite)
        self._btn_scan = Gtk.Button(label=_("🔍  Scan Libvirt hypervisors"))
        self._btn_scan.get_style_context().add_class("suggested-action")
        self._btn_scan.connect("clicked", self._on_scan_clicked)
        scan_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        scan_btn_box.pack_end(self._btn_scan, False, False, 0)
        self._scan_box.pack_start(scan_btn_box, False, False, 0)

        # Wrap _scan_box dans un ScrolledWindow pour éviter le dépassement écran
        sw_scan = Gtk.ScrolledWindow()
        sw_scan.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw_scan.set_min_content_height(200)
        sw_scan.set_max_content_height(400)
        sw_scan.add(self._scan_box)
        self._stack.add_titled(sw_scan, "config", _("1. Configuration"))

        self._monitor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._monitor_box.pack_start(sw_log, True, True, 0)
        self._monitor_box.pack_start(self._progress, False, False, 0)
        self._stack.add_titled(self._monitor_box, "monitor", _("2. Suivi"))

        # ── Phase 2 : Tableau de prévisualisation (caché au départ) ──────────
        self._preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        # Résumé
        self._lbl_summary = Gtk.Label(label="")
        self._lbl_summary.set_xalign(0)
        self._preview_box.pack_start(self._lbl_summary, False, False, 0)

        # Case « Écraser »
        self._chk_overwrite = Gtk.CheckButton(
            label=_("Overwrite existing connections with the same name and protocol")
        )
        self._chk_overwrite.set_active(False)
        self._preview_box.pack_start(self._chk_overwrite, False, False, 0)

        # Tout cocher / Tout décocher
        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_all = Gtk.Button(label=_("✓ Check all"))
        btn_all.connect("clicked", lambda w: self._select_all(True))
        btn_none = Gtk.Button(label=_("✗ Uncheck all"))
        btn_none.connect("clicked", lambda w: self._select_all(False))
        ctrl_box.pack_start(btn_all, False, False, 0)
        ctrl_box.pack_start(btn_none, False, False, 0)
        self._preview_box.pack_start(ctrl_box, False, False, 0)

        # TreeView de prévisualisation
        self._preview_store = Gtk.ListStore(
            bool,  # COL_SEL
            str,  # COL_PROTO
            str,  # COL_NAME
            str,  # COL_GROUP
            str,  # COL_HOST
            str,  # COL_STATE
            bool,  # COL_EXISTS
            str,  # COL_EXIST_LBL
            str,  # COL_FG
            int,  # COL_IDX
        )
        tv_prev = Gtk.TreeView(model=self._preview_store)
        tv_prev.set_enable_search(True)
        tv_prev.set_search_column(self._COL_NAME)
        self._tv_preview = tv_prev

        # Col 0 : case à cocher
        cr_sel = Gtk.CellRendererToggle()
        cr_sel.connect("toggled", self._on_preview_toggled)
        col_sel = Gtk.TreeViewColumn("", cr_sel, active=self._COL_SEL)
        col_sel.set_min_width(28)
        tv_prev.append_column(col_sel)

        # Col 1 : protocole
        cr_proto = Gtk.CellRendererText()
        col_proto = Gtk.TreeViewColumn(
            _("Proto"),
            cr_proto,
            text=self._COL_PROTO,
            foreground=self._COL_FG,
        )
        col_proto.set_min_width(55)
        tv_prev.append_column(col_proto)

        # Col 2 : nom VM
        cr_name = Gtk.CellRendererText()
        col_name = Gtk.TreeViewColumn(
            _("VM name"),
            cr_name,
            text=self._COL_NAME,
            foreground=self._COL_FG,
        )
        col_name.set_expand(True)
        col_name.set_min_width(170)
        tv_prev.append_column(col_name)

        # Col 3 : groupe
        cr_grp = Gtk.CellRendererText()
        col_grp = Gtk.TreeViewColumn(
            _("Group"),
            cr_grp,
            text=self._COL_GROUP,
            foreground=self._COL_FG,
        )
        col_grp.set_min_width(90)
        tv_prev.append_column(col_grp)

        # Col 4 : IP / hôte
        cr_host = Gtk.CellRendererText()
        col_host = Gtk.TreeViewColumn(
            _("IP / Host"),
            cr_host,
            text=self._COL_HOST,
            foreground=self._COL_FG,
        )
        col_host.set_min_width(120)
        tv_prev.append_column(col_host)

        # Col 5 : état VM
        cr_state = Gtk.CellRendererText()
        col_state = Gtk.TreeViewColumn(
            _("State"),
            cr_state,
            text=self._COL_STATE,
            foreground=self._COL_FG,
        )
        col_state.set_min_width(75)
        tv_prev.append_column(col_state)

        # Col 7 : déjà importé ?
        cr_exist = Gtk.CellRendererText()
        col_exist = Gtk.TreeViewColumn(
            _("Imported"),
            cr_exist,
            text=self._COL_EXIST_LBL,
            foreground=self._COL_FG,
        )
        col_exist.set_min_width(70)
        tv_prev.append_column(col_exist)

        # ScrolledWindow (~30 lignes visibles, défilable pour > 250 lignes)
        sw_prev = Gtk.ScrolledWindow()
        sw_prev.set_min_content_height(100)
        sw_prev.set_max_content_height(200)
        sw_prev.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        sw_prev.add(tv_prev)
        sw_prev.set_vexpand(True)
        self._preview_box.pack_start(sw_prev, True, True, 0)

        # Boutons de la phase 2
        import_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._btn_import = Gtk.Button(label=_("⬇  Import selected"))
        self._btn_import.get_style_context().add_class("suggested-action")
        self._btn_import.connect("clicked", self._on_import_clicked)
        btn_cancel2 = Gtk.Button(label=_("Cancel"))
        btn_cancel2.connect("clicked", lambda w: self.request_close())
        import_btn_box.pack_end(self._btn_import, False, False, 0)
        import_btn_box.pack_end(btn_cancel2, False, False, 6)
        self._preview_box.pack_start(import_btn_box, False, False, 0)
        self._stack.add_titled(self._preview_box, "results", _("3. Résultats"))

        # Bouton Fermer (barre d'action standard)
        dlg.add_button(_("✕  Close"), Gtk.ResponseType.CANCEL)
        dlg.connect("response", lambda d, r: self.request_close())

        if show:
            dlg.show_all()
        self._stack.set_visible_child_name("config")
        self._populate_uris()

    # ──────────────────────────────────────────────────────────────────────────
    # Peuplement des URIs
    # ──────────────────────────────────────────────────────────────────────────

    def _populate_uris(self):
        """Charge la liste initiale des URI libvirt depuis dconf.

        Si aucune URI n'est trouvée, un exemple par défaut est ajouté pour
        permettre un démarrage rapide du scan.

        Returns:
            None: Met à jour `self._uri_store` en place.
        """
        uris = hv_common.libvirt_get_uris_from_dconf()
        if not uris:
            self._log(
                _(
                    "No URI found in dconf (virt-manager not configured?).\nAdd your URIs manually below."
                )
            )
            self._uri_store.append([True, "qemu+ssh://root@hyperviseur/system"])
            return
        for uri in uris:
            self._uri_store.append([True, uri])

    def _normalize_manual_uri(self, raw_target):
        """Convertit une saisie libre en URI qemu+ssh://.../system.

        Args:
            raw_target (str): Cible saisie (IP, user@host, URI complète, etc.).

        Returns:
            str: URI normalisée, ou chaîne vide si entrée invalide.
        """
        raw = (raw_target or "").strip()
        if not raw:
            return ""
        if "://" in raw:
            return raw

        if "@" in raw:
            user, hostport = raw.split("@", 1)
            user = user or "root"
        else:
            user, hostport = "root", raw

        host = hostport
        port = None
        if ":" in hostport:
            h, p = hostport.rsplit(":", 1)
            if p.isdigit():
                host, port = h, int(p)

        host = host.strip()
        if not host:
            return ""
        if port is None or port == 22:
            return f"qemu+ssh://{user}@{host}/system"
        return f"qemu+ssh://{user}@{host}:{port}/system"

    def _on_add_manual_uri(self, widget):
        """Ajoute une URI libvirt saisie manuellement dans la liste.

        Args:
            widget (Gtk.Widget): Widget déclencheur (bouton ou entrée).

        Returns:
            None: Met à jour la liste des URI et le log UI.
        """
        uri = self._normalize_manual_uri(self._entry_manual_uri.get_text())
        if not uri:
            self._log(_("Invalid libvirt target."))
            return
        existing = [row[1] for row in self._uri_store]
        if uri in existing:
            self._log(_("Target already present: {uri}").format(uri=uri))
            self._entry_manual_uri.set_text("")
            return
        self._uri_store.append([True, uri])
        self._log(_("Target added: {uri}").format(uri=uri))
        self._entry_manual_uri.set_text("")

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers log / progress
    # ──────────────────────────────────────────────────────────────────────────

    def _log(self, msg):
        """Ajoute un message dans la zone de log du dialogue.

        Args:
            msg (str): Texte à afficher.

        Returns:
            None: Planifie l'ajout du message dans le thread GTK.
        """

        def _do():
            buf = self._log_buf
            buf.insert(buf.get_end_iter(), msg + "\n")
            self._log_view.scroll_mark_onscreen(buf.get_insert())
            return False

        GLib.idle_add(_do)

    def _set_progress(self, frac, text):
        """Met à jour la barre de progression du scan.

        Args:
            frac (float): Fraction de progression entre 0 et 1.
            text (str): Texte d'état à afficher.

        Returns:
            None: Planifie la mise à jour de la progress bar.
        """

        def _do():
            self._progress.set_fraction(frac)
            self._progress.set_text(text)
            return False

        GLib.idle_add(_do)

    # ──────────────────────────────────────────────────────────────────────────
    # URI toggle
    # ──────────────────────────────────────────────────────────────────────────

    def _on_uri_toggled(self, renderer, path):
        """Inverse l'état coché d'une URI dans la liste de scan.

        Args:
            renderer (Gtk.CellRendererToggle): Renderer source.
            path (str): Chemin de ligne dans le modèle Gtk.

        Returns:
            None: Inverse l'état de sélection de la ligne.
        """
        self._uri_store[path][0] = not self._uri_store[path][0]

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 1 → Scan
    # ──────────────────────────────────────────────────────────────────────────

    def _on_scan_clicked(self, widget):
        """Lance le scan des hyperviseurs libvirt sélectionnés.

        Ajoute aussi une URI saisie mais non encore validée avant de démarrer
        le thread de collecte.

        Args:
            widget (Gtk.Widget): Bouton scanner.

        Returns:
            None: Démarre un thread de scan et met à jour l'UI.
        """
        self._btn_scan.set_sensitive(False)
        self._stack.set_visible_child_name("monitor")
        logger.debug("LibvirtImportDialog._on_scan_clicked | scan started")
        if hasattr(self, "_entry_manual_uri"):
            pending_uri = self._normalize_manual_uri(self._entry_manual_uri.get_text())
            if pending_uri:
                existing = [row[1] for row in self._uri_store]
                if pending_uri not in existing:
                    self._uri_store.append([True, pending_uri])
                    self._log(
                        _("Target added automatically: {pending_uri}").format(
                            pending_uri=pending_uri
                        )
                    )
                self._entry_manual_uri.set_text("")
        uris = [row[1] for row in self._uri_store if row[0]]
        if not uris:
            self._log(_("No URI selected."))
            self._btn_scan.set_sensitive(True)
            return
        user = self._entry_user.get_text().strip() or "root"
        proto_filter = set()
        if self._chk_ssh.get_active():
            proto_filter.add("ssh")
        if self._chk_spice.get_active():
            proto_filter.add("spice")
        if self._chk_rdp.get_active():
            proto_filter.add("rdp")
        if hasattr(self, "_chk_vnc") and self._chk_vnc.get_active():
            proto_filter.add("vnc")
        if not proto_filter:
            self._log(_("No connection type selected."))
            self._btn_scan.set_sensitive(True)
            return

        def worker():
            try:
                results = _libvirt_fetch_hosts(
                    uris, user, self._log, self._set_progress, proto_filter
                )
            except Exception as exc:
                logger.exception(f"LibvirtImportDialog._on_scan_clicked | scan failed: {exc}")
                self._log(_("Scan failed: {exc}").format(exc=exc))
                results = []
            GLib.idle_add(self._show_preview, results)

        threading.Thread(target=worker, daemon=True).start()

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 2 → Tableau de prévisualisation
    # ──────────────────────────────────────────────────────────────────────────

    def _host_exists(self, name, grp):
        """Vérifie si un hôte avec ce nom existe déjà dans le groupe GCM."""
        if grp in groups:
            return any(h.name == name for h in groups[grp])
        return False

    def _show_preview(self, host_dicts):
        """Affiche les résultats du scan dans la grille de prévisualisation.

        Args:
            host_dicts (list[dict]): Connexions détectées prêtes à importer.

        Returns:
            None: Remplit la grille de prévisualisation et met à jour le résumé.
        """
        self._discovered = host_dicts
        self._preview_store.clear()

        n_new = 0
        n_exists = 0
        for idx, hd in enumerate(host_dicts):
            name = hd.get("name", "")
            grp = hd.get("group", "LIBVIRT")
            proto = hd.get("protocol", "ssh").upper()
            host = hd.get("host", "")
            desc = hd.get("description", "")
            # Extraire l'état de la VM depuis la description ([running], [shut off]…)
            m = re.search(r"\[([^\]]+)\]", desc)
            state_str = m.group(1) if m else ""
            exists = self._host_exists(name, grp)
            exist_lbl = "✓ existe" if exists else ""
            fg = "#888888" if exists else "black"
            # Cocher par défaut seulement les nouvelles entrées
            selected = not exists
            if exists:
                n_exists += 1
            else:
                n_new += 1
            self._preview_store.append(
                [
                    selected,
                    proto,
                    name,
                    grp,
                    host,
                    state_str,
                    exists,
                    exist_lbl,
                    fg,
                    idx,
                ]
            )

        total = len(host_dicts)
        self._lbl_summary.set_markup(
            f"<b>{total}</b> connexion(s) trouvée(s) — "
            f"<span foreground='#007700'>{n_new} nouvelle(s)</span>, "
            f"<span foreground='#888888'>{n_exists} déjà importée(s)</span>"
        )
        self._set_progress(1.0, f"{total} connexion(s) découverte(s)")
        self._log(
            f"\nScan terminé — {total} connexion(s) : {n_new} nouvelle(s), {n_exists} déjà présente(s)."
        )
        self._stack.set_visible_child_name("results")
        logger.info(
            f"LibvirtImportDialog._show_preview | total={total} new={n_new} existing={n_exists}"
        )
        # Réinitialiser le bouton Scanner
        self._btn_scan.set_label(_("🔄  Rescan"))
        self._btn_scan.set_sensitive(True)
        # Agrandir le dialogue pour afficher le tableau
        if not self.is_tab:
            self._dlg.resize(820, 900)

    # ──────────────────────────────────────────────────────────────────────────
    # Sélection globale
    # ──────────────────────────────────────────────────────────────────────────

    def _on_preview_toggled(self, renderer, path):
        """Inverse la sélection d'une ligne de prévisualisation.

        Args:
            renderer (Gtk.CellRendererToggle): Renderer source.
            path (str): Chemin de ligne dans le modèle Gtk.

        Returns:
            None: Inverse l'état coché de la ligne.
        """
        self._preview_store[path][self._COL_SEL] = not self._preview_store[path][self._COL_SEL]

    def _select_all(self, value):
        """Coche ou décoche toutes les lignes de prévisualisation.

        Args:
            value (bool): État à appliquer à toute la liste.

        Returns:
            None: Applique l'état à toutes les lignes.
        """
        for row in self._preview_store:
            row[self._COL_SEL] = value

    # ──────────────────────────────────────────────────────────────────────────
    # Import final
    # ──────────────────────────────────────────────────────────────────────────

    def _on_import_clicked(self, widget):
        """Importe les éléments sélectionnés vers la configuration GCM.

        Args:
            widget (Gtk.Widget): Bouton d'import.

        Returns:
            None: Déclenche l'import et met à jour l'UI.
        """
        self._btn_import.set_sensitive(False)
        overwrite = self._chk_overwrite.get_active()
        to_import = []
        for row in self._preview_store:
            if not row[self._COL_SEL]:
                continue
            exists = row[self._COL_EXISTS]
            if exists and not overwrite:
                continue  # ignorer les existants si écrasement désactivé
            idx = row[self._COL_IDX]
            to_import.append(self._discovered[idx])

        if not to_import:
            self._log(_("No connection to import (all already exist or none selected)."))
            self._btn_import.set_sensitive(True)
            return

        self.on_done(to_import)
        logger.info(f"LibvirtImportDialog._on_import_clicked | imported={len(to_import)}")
        self._btn_import.set_label(_("✓ Imported"))
        n = len(to_import)
        self._lbl_summary.set_markup(f"<b>{n}</b> connexion(s) importée(s) avec succès.")


# ══════════════════════════════════════════════════════════════════════
# Point d'entrée BatchPlugin (autoload par BatchPluginRegistry)
# ══════════════════════════════════════════════════════════════════════


class LibvirtImportBatchPlugin(BatchPlugin):
    """Adapte ``LibvirtImportDialog`` au contrat ``BatchPlugin``.

    Reprend exactement la logique de l'ancien
    ``Wmain.on_mnu_import_libvirt_activate`` : ouvre le dialogue comme
    onglet épinglé (``Wmain.open_management_tab``, singleton par ``key``)
    et route les hôtes importés vers ``Wmain._import_done`` avec le groupe
    par défaut ``"LIBVIRT"``.
    """

    tool_id = "import-libvirt"
    display_name = _("Import from libvirt")
    icon_name = "network-server-symbolic"
    menu_section = "import"

    def activate(self) -> None:
        """Déclenche l'import de VMs depuis des hyperviseurs libvirt.

        Lance un dialogue d'import dans un nouvel onglet de management,
        permettant à l'utilisateur de sélectionner des hyperviseurs,
        de scanner les VMs, et de les importer.

        Raises:
            RuntimeError: Si l'application n'est pas liée au plugin.
        """
        if self.app is None:
            raise RuntimeError(
                "LibvirtImportBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        app = self.app
        from utils import conf

        default_user = conf.__dict__.get("LIBVIRT_DEFAULT_USER", "root")

        def on_done(host_dicts):
            if not host_dicts:
                msgbox(_("No host imported."), parent=app.window)
                return
            n = app._import_done(host_dicts, default_group="LIBVIRT")
            if n:
                msgbox(
                    _("{n} connection(s) imported from libvirt.").format(n=n), parent=app.window
                )
            else:
                msgbox(_("No new host (duplicates ignored)."), parent=app.window)

        app.open_management_tab(
            "libvirt-import",
            _("Import from libvirt"),
            lambda: LibvirtImportDialog(app.window, on_done, default_user, show=False),
        )


def get_batch_plugin() -> LibvirtImportBatchPlugin:
    """Retourne l'instance unique du plugin libvirt (découverte autoload).

    Returns:
        LibvirtImportBatchPlugin: Instance du plugin d'import libvirt.
    """
    return LibvirtImportBatchPlugin()
