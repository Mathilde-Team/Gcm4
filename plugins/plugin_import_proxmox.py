# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from Proxmox » (BatchPlugin) pour GCM.

Anciennement du code intégré directement dans ``gnome_connection_manager.py``
(fonction ``_proxmox_fetch_hosts`` + classe ``ProxmoxImportDialog``,
appelées depuis ``Wmain.on_mnu_import_proxmox_activate``). Porté ici comme
troisième exemple de ``BatchPlugin`` (après ``plugin_netmiko_push.py`` et
``plugin_import_libvirt.py``) : découvert et enregistré automatiquement par
``BatchPluginRegistry.autoload()`` (cf. ``plugin_base.py``) via
``get_batch_plugin()`` tout en bas de ce fichier — aucun câblage manuel
n'est nécessaire dans ``gnome_connection_manager.py``.

Scan natif `qm`/`pvesh` via SSH sur un ou plusieurs noeuds Proxmox,
résolution d'IP (bail DHCP puis nmap/MAC en repli), sonde RDP/VNC, dialogue
de sélection avant import — même déroulé que l'import libvirt. La logique
bas niveau commune (connexion SSH par clé, scan réseau, sonde de port) vit
dans ``hypervisor_import_common.py``, partagée avec ``plugin_import_libvirt.py``.
"""

from __future__ import annotations

import json
import re
import threading
from urllib.parse import urlparse

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
    logger.debug("import proxmox | groupes du module principal indisponibles : {}", exc)
    groups = {}  # noqa: F841

# Utilise le _() global injecté par bindtextdomain() si présent, sinon no-op
# — même convention que plugin_netmiko_push.py / plugin_import_libvirt.py.
if "_" not in globals():

    def _(s: str) -> str:  # noqa: D401
        """Fallback no-op translation for isolated/test contexts."""
        return s


# Accès à la variable globale 'groups' du module principal pour vérifier
# si un hôte existe déjà — import paresseux (ici plutôt qu'en haut du
# fichier) pour éviter une dépendance circulaire au chargement du module.
try:
    from gnome_connection_manager import groups  # noqa: E402, F401
except (ImportError, NameError) as exc:
    logger.debug("import proxmox | groupes du module principal indisponibles : {}", exc)
    groups = {}  # noqa: F841


__all__ = [
    "ProxmoxPrefsTab",
    "build_preferences_tab",
    "ProxmoxImportBatchPlugin",
    "ProxmoxImportDialog",
    "get_batch_plugin",
]


class ProxmoxPrefsTab:
    """Onglet de préférences UI pour le plugin d'import Proxmox.

    Configure l'utilisateur SSH par défaut utilisé lors des imports.
    """

    def __init__(self, notebook):
        """Initialise l'onglet de préférences Proxmox.

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
        self._entry_user.set_text(getattr(conf, "PROXMOX_DEFAULT_USER", "root") or "root")
        self._entry_user.set_tooltip_text(_("Used as default user in the Proxmox import dialog."))
        row.pack_start(self._entry_user, True, True, 0)
        outer.pack_start(row, False, False, 0)

        help_lbl = Gtk.Label()
        help_lbl.set_xalign(0)
        help_lbl.set_line_wrap(True)
        help_lbl.set_markup(
            _(
                "<i>Applies only to the Proxmox import plugin. Can still be overridden per import run.</i>"
            )
        )
        outer.pack_start(help_lbl, False, False, 0)

        notebook.append_page(outer, Gtk.Label(label=_("Proxmox")))
        outer.show_all()
        notebook.show_all()

    def apply(self):
        """Valide et enregistre les préférences dans la configuration.

        Returns:
            None: Les valeurs sont sauvegardées dans conf.PROXMOX_DEFAULT_USER.
        """
        from utils import conf

        conf.PROXMOX_DEFAULT_USER = (self._entry_user.get_text() or "").strip() or "root"


def build_preferences_tab(notebook):
    """Construit l'onglet de préférences Proxmox dans le notebook donné.

    Args:
        notebook: Widget Gtk.Notebook récepteur.

    Returns:
        ProxmoxPrefsTab: Instance de l'onglet (pour accès ultérieur si besoin).
    """
    return ProxmoxPrefsTab(notebook)


def _proxmox_fetch_hosts(uris, ssh_user, log_fn, progress_fn, proto_filter=None):
    """Collecte les VMs Proxmox via SSH sur les hyperviseurs donnés.

    Les hôtes d'entrée utilisent le même format que l'import libvirt
    (URI type qemu+ssh://user@host[:port]/system), mais l'inventaire est
    réalisé nativement avec les commandes Proxmox (`qm`).

    Args:
        uris (list[str]): Cibles hyperviseurs (URI SSH).
        ssh_user (str): Utilisateur SSH des VMs invitées.
        log_fn (callable): Fonction de log.
        progress_fn (callable): Progression (frac: float, texte: str).
        proto_filter (set[str] | None): {'ssh','spice','rdp','vnc'} ou None = tous.

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

    def _extract_first_ipv4(qm_agent_output):
        """Extrait la première IPv4 non-loopback de la sortie QGA ``network-get-interfaces``.

        Args:
            qm_agent_output: Sortie brute (JSON attendu) de
                ``qm guest cmd <vmid> network-get-interfaces``.

        Returns:
            str: La première IPv4 non-loopback trouvée, ou ``""`` si aucune.
        """
        logger.debug(f"_extract_first_ipv4 | len(output)={len(qm_agent_output or '')}")
        try:
            data = json.loads(qm_agent_output)
        except Exception as exc:
            logger.debug(f"_extract_first_ipv4 | json.loads a échoué, repli regex : {exc}")
            data = None

        if data is not None:
            # QGA `network-get-interfaces` renvoie en général directement une
            # liste d'interfaces (`[{"name": "lo", "ip-addresses": [...]}, ...]`),
            # mais certaines versions/wrappers l'enveloppent dans un dict
            # `{"result": [...]}` — on accepte les deux formes (bug corrigé :
            # `data.get("result", [])` plantait avec "'list' object has no
            # attribute 'get'" quand `data` était directement la liste).
            if isinstance(data, list):
                interfaces = data
            elif isinstance(data, dict):
                interfaces = data.get("result", [])
            else:
                interfaces = []
                logger.debug(f"_extract_first_ipv4 | type JSON inattendu : {type(data).__name__}")
            for iface in interfaces:
                if not isinstance(iface, dict):
                    continue
                for addr in iface.get("ip-addresses", []):
                    ip = addr.get("ip-address", "")
                    if re.match(r"\d+\.\d+\.\d+\.\d+$", ip) and not ip.startswith("127."):
                        logger.debug(f"_extract_first_ipv4 | trouvé via JSON : {ip}")
                        return ip
            logger.debug("_extract_first_ipv4 | JSON parsé mais aucune IPv4 non-loopback trouvée")

        # Repli regex (JSON invalide OU aucune IPv4 valable dans le JSON) :
        # on ne doit JAMAIS retourner le loopback ici non plus (bug corrigé :
        # l'ancienne version retournait la 1re "ip-address" du texte, qui est
        # très souvent 127.0.0.1 car l'interface "lo" est listée en premier
        # par QGA — d'où des hôtes SSH importés avec 127.0.0.1 comme IP).
        for m in re.finditer(r'"ip-address"\s*:\s*"(\d+\.\d+\.\d+\.\d+)"', qm_agent_output or ""):
            ip = m.group(1)
            if not ip.startswith("127."):
                logger.debug(f"_extract_first_ipv4 | trouvé via regex de repli : {ip}")
                return ip
        logger.debug("_extract_first_ipv4 | aucune IPv4 non-loopback trouvée (JSON et regex)")
        return ""

    for idx, uri in enumerate(uris):
        log_fn(f"\n── Hyperviseur Proxmox : {uri}")
        progress_fn(idx / total, f"Connexion à {uri}…")
        parsed = urlparse(uri)
        hv_host = parsed.hostname or "localhost"
        hv_port = parsed.port or 22
        hv_user = parsed.username or "root"

        client = hv_common.paramiko_connect(hv_host, hv_port, hv_user, log_fn)
        if client is None:
            continue

        try:
            qm_out = run(client, "qm list")
            vm_rows = []
            for line in qm_out.splitlines():
                line = line.strip()
                if not line or line.lower().startswith("vmid"):
                    continue
                parts = line.split()
                if len(parts) >= 3 and parts[0].isdigit():
                    vm_rows.append((parts[0], parts[1], parts[2]))
            log_fn(f"  {len(vm_rows)} VM(s) trouvée(s)")

            for vmid, vm_name, state in vm_rows:
                grp, short = hv_common.vm_name_split(vm_name)
                is_windows = bool(
                    re.search(
                        r"win|w(?:2k|2019|2022|2016|2012|srv|dc|server)",
                        vm_name,
                        re.IGNORECASE,
                    )
                )
                vm_user = "Administrator" if is_windows else ssh_user
                ip_addr = ""

                if state == "running":
                    qga_out = run(
                        client,
                        f"qm guest cmd {vmid} network-get-interfaces 2>/dev/null",
                        timeout=20,
                    )
                    ip_addr = _extract_first_ipv4(qga_out)

                # Fallback 1 : config ipconfig0 (cloud-init / statique Proxmox)
                if not ip_addr:
                    cfg_out = run(client, f"qm config {vmid} | grep '^ipconfig'", timeout=10)
                    m_cfg = re.search(r"ip=(\d+\.\d+\.\d+\.\d+)", cfg_out)
                    if m_cfg and not m_cfg.group(1).startswith("169."):
                        ip_addr = m_cfg.group(1)

                # Fallback 2 : ARP filtré par MAC de la VM
                if not ip_addr:
                    # Récupérer la MAC de la VM depuis qm config
                    mac_out = run(
                        client,
                        f"qm config {vmid} | grep -oP '(?<=virtio=|e1000=|rtl8139=)[0-9A-Fa-f:]+' | head -1",
                        timeout=10,
                    )
                    vm_mac = mac_out.strip().lower() if mac_out.strip() else ""
                    arp_out = run(client, "ip neigh show 2>/dev/null || arp -n 2>/dev/null")
                    for line in arp_out.splitlines():
                        if vm_mac and vm_mac not in line.lower():
                            continue
                        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                        if m and not m.group(1).startswith("127."):
                            ip_addr = m.group(1)
                            break

                # Fallback 3 : nmap ping scan (même pipeline que libvirt)
                if not ip_addr and state == "running":
                    nmap_table = hv_common.libvirt_nmap_scan(client, run, log_fn)
                    if nmap_table:
                        # Prendre la première IP découverte correspondant à une MAC VM connue
                        mac_out2 = run(
                            client,
                            f"qm config {vmid} | grep -oP '[0-9A-Fa-f]{{2}}(:[0-9A-Fa-f]{{2}}){{5}}' | head -1",
                            timeout=10,
                        )
                        vm_mac2 = mac_out2.strip().lower() if mac_out2.strip() else ""
                        if vm_mac2 and vm_mac2 in nmap_table:
                            ip_addr = nmap_table[vm_mac2]
                        elif not ip_addr and nmap_table:
                            # dernier recours : première IP du subnet (peu fiable)
                            log_fn(f"  ↳ nmap : MAC VM introuvable, IP non résolue pour {vm_name}")

                if ip_addr:
                    log_fn(f"  ↳ IP {vm_name} : {ip_addr}")
                else:
                    log_fn(f"  ↳ IP {vm_name} : inconnue (QGA/ARP/nmap ont échoué)")

                added = False

                if "ssh" in proto_filter:
                    if ip_addr:
                        jump_flag = (
                            f"-J {hv_user}@{hv_host}:{hv_port}"
                            if hv_port != 22
                            else f"-J {hv_user}@{hv_host}"
                        )
                        results.append(
                            {
                                "name": short,
                                "host": ip_addr,
                                "user": vm_user,
                                "port": 22,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} (VMID {vmid}) — SSH ProxyJump via "
                                    f"{hv_user}@{hv_host}:{hv_port} → {ip_addr}"
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
                                    f"[{state}] {vm_name} (VMID {vmid}) — SSH via hyperviseur "
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

                if "spice" in proto_filter:
                    # Proxmox SPICE : détecter vga qxl depuis qm config
                    vga_out = run(client, f"qm config {vmid} | grep '^vga:'", timeout=10)
                    has_qxl = "qxl" in vga_out.lower()
                    if has_qxl:
                        node_name = run(client, "hostname").strip()
                        results.append(
                            {
                                "name": short,
                                "host": hv_host,
                                "user": hv_user,
                                "port": hv_port,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} (VMID {vmid}) — SPICE via Proxmox proxy "
                                    f"{hv_host} (ticket généré à la connexion)"
                                ),
                                "group": f"{grp}/spice" if grp else "spice",
                                "hypervisor": hv_host,
                                "protocol": "spice",
                                "extra_params": f"--proxmox {node_name} {vmid}",
                            }
                        )
                        log_fn(f"  + [{grp}] {short:28s}  VMID {vmid:>5s}  [{state}]  SPICE (qxl)")
                        added = True
                    else:
                        log_fn(
                            f"  ↳ SPICE ignoré pour {vm_name} : pas de vga qxl (vga='{vga_out.strip()}')"
                        )

                if "rdp" in proto_filter and ip_addr:
                    log_fn(f"  ↳ Sondage RDP 3389 sur {ip_addr}…")
                    if hv_common.check_port_open(client, run, ip_addr, 3389):
                        results.append(
                            {
                                "name": f"{short}",
                                "host": ip_addr,
                                "user": vm_user,
                                "port": 3389,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} (VMID {vmid}) — RDP (port 3389 confirmé ouvert)"
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

                if "vnc" in proto_filter and ip_addr:
                    log_fn(f"  ↳ Sondage VNC 5900 sur {ip_addr}…")
                    if hv_common.check_port_open(client, run, ip_addr, 5900):
                        results.append(
                            {
                                "name": f"{short}",
                                "host": ip_addr,
                                "user": vm_user,
                                "port": 5900,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} (VMID {vmid}) — VNC (port 5900 confirmé ouvert)"
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


class ProxmoxImportDialog(GCMBase):
    """Dialogue GTK3 d'import de VMs depuis Proxmox (scan natif `qm`).

    Classe indépendante — aucun lien avec LibvirtImportDialog.
    Même structure UI en deux phases (scan → prévisualisation) mais :
    - Inventaire via `qm list` / `qm config` (Proxmox natif)
    - SPICE via ticket pvesh (vga:qxl requis)
    - Résolution IP : QGA → ipconfig0 → ARP/MAC → nmap
    """

    # Colonnes du ListStore de prévisualisation (identiques à libvirt)
    _COL_SEL = 0
    _COL_PROTO = 1
    _COL_NAME = 2
    _COL_GROUP = 3
    _COL_HOST = 4
    _COL_STATE = 5
    _COL_EXISTS = 6
    _COL_EXIST_LBL = 7
    _COL_FG = 8
    _COL_IDX = 9

    def __init__(self, parent_window, on_done_callback, default_user="root", show=True):
        """Initialise le dialogue d'import Proxmox.

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
        """Construit l'interface utilisateur du dialogue d'import Proxmox.

        Crée un Gtk.Stack à 3 phases : configuration (URI), suivi
        (progression du scan), résultats (grille de prévisualisation).

        Args:
            show: Si True, affiche le dialogue après construction.
        """
        dlg = Gtk.Dialog(
            title=_("Import from Proxmox"),
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

        # Liste des cibles Proxmox
        lbl_uri = Gtk.Label(label=_("Proxmox targets:"))
        lbl_uri.set_xalign(0)
        self._scan_box.pack_start(lbl_uri, False, False, 2)

        self._uri_store = Gtk.ListStore(bool, str)
        tv_uri = Gtk.TreeView(model=self._uri_store)
        tv_uri.set_headers_visible(True)
        cr_toggle = Gtk.CellRendererToggle()
        cr_toggle.connect("toggled", self._on_uri_toggled)
        tv_uri.append_column(Gtk.TreeViewColumn("", cr_toggle, active=0))
        col_uri_txt = Gtk.TreeViewColumn(
            _("Proxmox target (qemu+ssh://user@host/system)"),
            Gtk.CellRendererText(),
            text=1,
        )
        col_uri_txt.set_expand(True)
        tv_uri.append_column(col_uri_txt)
        sw_uri = Gtk.ScrolledWindow()
        sw_uri.set_min_content_height(120)
        sw_uri.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw_uri.add(tv_uri)
        self._scan_box.pack_start(sw_uri, False, False, 0)

        # Saisie manuelle d'une cible Proxmox
        hb_manual = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hb_manual.pack_start(Gtk.Label(label=_("Add a Proxmox target:")), False, False, 0)
        self._entry_manual_uri = Gtk.Entry()
        self._entry_manual_uri.set_placeholder_text(
            _("Ex: 192.168.105.41 | root@192.168.105.41 | root@192.168.105.41:22")
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
            label=_("SPICE  (vga:qxl required — ticket generated at connection time via pvesh)")
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

        # Bouton Scanner
        self._btn_scan = Gtk.Button(label=_("🔍  Scan Proxmox hosts"))
        self._btn_scan.get_style_context().add_class("suggested-action")
        self._btn_scan.connect("clicked", self._on_scan_clicked)
        scan_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        scan_btn_box.pack_end(self._btn_scan, False, False, 0)
        self._scan_box.pack_start(scan_btn_box, False, False, 0)

        sw_scan = Gtk.ScrolledWindow()
        sw_scan.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw_scan.set_min_content_height(100)
        sw_scan.set_max_content_height(200)
        sw_scan.add(self._scan_box)
        self._stack.add_titled(sw_scan, "config", _("1. Configuration"))

        self._monitor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._monitor_box.pack_start(sw_log, True, True, 0)
        self._monitor_box.pack_start(self._progress, False, False, 0)
        self._stack.add_titled(self._monitor_box, "monitor", _("2. Suivi"))

        # ── Phase 2 : Tableau de prévisualisation (caché au départ) ──────────
        self._preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        self._lbl_summary = Gtk.Label(label="")
        self._lbl_summary.set_xalign(0)
        self._preview_box.pack_start(self._lbl_summary, False, False, 0)

        self._chk_overwrite = Gtk.CheckButton(
            label=_("Overwrite existing connections with the same name and protocol")
        )
        self._chk_overwrite.set_active(False)
        self._preview_box.pack_start(self._chk_overwrite, False, False, 0)

        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_all = Gtk.Button(label=_("✓ Check all"))
        btn_all.connect("clicked", lambda w: self._select_all(True))
        btn_none = Gtk.Button(label=_("✗ Uncheck all"))
        btn_none.connect("clicked", lambda w: self._select_all(False))
        ctrl_box.pack_start(btn_all, False, False, 0)
        ctrl_box.pack_start(btn_none, False, False, 0)
        self._preview_box.pack_start(ctrl_box, False, False, 0)

        self._preview_store = Gtk.ListStore(bool, str, str, str, str, str, bool, str, str, int)
        tv_prev = Gtk.TreeView(model=self._preview_store)
        tv_prev.set_enable_search(True)
        tv_prev.set_search_column(self._COL_NAME)
        self._tv_preview = tv_prev

        cr_sel = Gtk.CellRendererToggle()
        cr_sel.connect("toggled", self._on_preview_toggled)
        col_sel = Gtk.TreeViewColumn("", cr_sel, active=self._COL_SEL)
        col_sel.set_min_width(28)
        tv_prev.append_column(col_sel)

        for title, col_idx, min_w, expand in (
            (_("Proto"), self._COL_PROTO, 55, False),
            (_("VM name"), self._COL_NAME, 170, True),
            (_("Group"), self._COL_GROUP, 90, False),
            (_("IP / Host"), self._COL_HOST, 120, False),
            (_("State"), self._COL_STATE, 75, False),
            (_("Imported"), self._COL_EXIST_LBL, 70, False),
        ):
            cr = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, cr, text=col_idx, foreground=self._COL_FG)
            col.set_min_width(min_w)
            col.set_expand(expand)
            tv_prev.append_column(col)

        sw_prev = Gtk.ScrolledWindow()
        sw_prev.set_min_content_height(100)
        sw_prev.set_max_content_height(200)
        sw_prev.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)
        sw_prev.add(tv_prev)
        sw_prev.set_vexpand(True)
        self._preview_box.pack_start(sw_prev, True, True, 0)

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

        dlg.add_button(_("✕  Close"), Gtk.ResponseType.CANCEL)
        dlg.connect("response", lambda d, r: self.request_close())

        if show:
            dlg.show_all()
        self._stack.set_visible_child_name("config")
        self._populate_uris()

    # ──────────────────────────────────────────────────────────────────────────
    # Peuplement des cibles Proxmox
    # ──────────────────────────────────────────────────────────────────────────

    def _populate_uris(self):
        uris = [
            u for u in hv_common.libvirt_get_uris_from_dconf() if "+ssh://" in u and "/system" in u
        ]
        if not uris:
            self._log(_("No SSH URI found in dconf.\nEnter an IP/host below and click Add."))
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
        port = 22
        if ":" in hostport:
            h, p = hostport.rsplit(":", 1)
            if p.isdigit():
                host, port = h, int(p)
        host = host.strip()
        if not host:
            return ""
        return f"qemu+ssh://{user}@{host}:{port}/system"

    def _on_add_manual_uri(self, widget):
        """Ajoute une cible Proxmox saisie manuellement à la liste.

        Args:
            widget (Gtk.Widget): Widget déclencheur.

        Returns:
            None: Met à jour la liste des cibles et le log UI.
        """
        uri = self._normalize_manual_uri(self._entry_manual_uri.get_text())
        if not uri:
            self._log(_("Invalid Proxmox target."))
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
        def _do():
            buf = self._log_buf
            buf.insert(buf.get_end_iter(), msg + "\n")
            self._log_view.scroll_mark_onscreen(buf.get_insert())
            return False

        GLib.idle_add(_do)

    def _set_progress(self, frac, text):
        def _do():
            self._progress.set_fraction(frac)
            self._progress.set_text(text)
            return False

        GLib.idle_add(_do)

    # ──────────────────────────────────────────────────────────────────────────
    # URI toggle
    # ──────────────────────────────────────────────────────────────────────────

    def _on_uri_toggled(self, renderer, path):
        self._uri_store[path][0] = not self._uri_store[path][0]

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 1 → Scan Proxmox
    # ──────────────────────────────────────────────────────────────────────────

    def _on_scan_clicked(self, widget):
        self._btn_scan.set_sensitive(False)
        self._btn_scan.set_label(_("Scanning…"))
        self._btn_import.set_sensitive(True)
        self._stack.set_visible_child_name("monitor")
        logger.debug("ProxmoxImportDialog._on_scan_clicked | scan started")
        pending_uri = self._normalize_manual_uri(self._entry_manual_uri.get_text())
        if pending_uri:
            existing = [row[1] for row in self._uri_store]
            if pending_uri not in existing:
                self._uri_store.append([True, pending_uri])
                self._log(
                    _("Target added automatically: {pending_uri}").format(pending_uri=pending_uri)
                )
            self._entry_manual_uri.set_text("")
        uris = [row[1] for row in self._uri_store if row[0]]
        if not uris:
            self._log(_("No Proxmox target selected."))
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
        if self._chk_vnc.get_active():
            proto_filter.add("vnc")
        if not proto_filter:
            self._log(_("No connection type selected."))
            self._btn_scan.set_sensitive(True)
            return

        def worker():
            try:
                results = _proxmox_fetch_hosts(
                    uris, user, self._log, self._set_progress, proto_filter
                )
            except Exception as exc:
                logger.exception(f"ProxmoxImportDialog._on_scan_clicked | scan failed: {exc}")
                self._log(_("Scan failed: {exc}").format(exc=exc))
                results = []
            GLib.idle_add(self._show_preview, results)

        threading.Thread(target=worker, daemon=True).start()

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 2 → Tableau de prévisualisation
    # ──────────────────────────────────────────────────────────────────────────

    def _host_exists(self, name, grp):
        if grp in groups:
            return any(h.name == name for h in groups[grp])
        return False

    def _show_preview(self, host_dicts):
        self._discovered = host_dicts
        self._preview_store.clear()
        n_new = 0
        n_exists = 0
        for idx, hd in enumerate(host_dicts):
            name = hd.get("name", "")
            grp = hd.get("group", "PROXMOX")
            proto = hd.get("protocol", "ssh").upper()
            host = hd.get("host", "")
            desc = hd.get("description", "")
            m = re.search(r"\[([^\]]+)\]", desc)
            state_str = m.group(1) if m else ""
            exists = self._host_exists(name, grp)
            exist_lbl = "✓ existe" if exists else ""
            fg = "#888888" if exists else "black"
            selected = not exists
            if exists:
                n_exists += 1
            else:
                n_new += 1
            self._preview_store.append(
                [selected, proto, name, grp, host, state_str, exists, exist_lbl, fg, idx]
            )
        total = len(host_dicts)
        self._lbl_summary.set_markup(
            f"<b>{total}</b> connexion(s) trouvée(s) — "
            f"<span foreground='#007700'>{n_new} nouvelle(s)</span>, "
            f"<span foreground='#888888'>{n_exists} déjà importée(s)</span>"
        )
        self._set_progress(1.0, f"{total} connexion(s) découverte(s)")
        self._log(
            f"\nScan Proxmox terminé — {total} connexion(s) : {n_new} nouvelle(s), {n_exists} déjà présente(s)."
        )
        self._stack.set_visible_child_name("results")
        logger.info(
            f"ProxmoxImportDialog._show_preview | total={total} new={n_new} existing={n_exists}"
        )
        self._btn_scan.set_label(_("🔄  Rescan"))
        self._btn_scan.set_sensitive(True)
        if not self.is_tab:
            self._dlg.resize(820, 900)

    def _on_preview_toggled(self, renderer, path):
        self._preview_store[path][self._COL_SEL] = not self._preview_store[path][self._COL_SEL]

    def _select_all(self, value):
        for row in self._preview_store:
            row[self._COL_SEL] = value

    # ──────────────────────────────────────────────────────────────────────────
    # Import final
    # ──────────────────────────────────────────────────────────────────────────

    def _on_import_clicked(self, widget):
        self._btn_import.set_sensitive(False)
        overwrite = self._chk_overwrite.get_active()
        to_import = []
        for row in self._preview_store:
            if not row[self._COL_SEL]:
                continue
            if row[self._COL_EXISTS] and not overwrite:
                continue
            to_import.append(self._discovered[row[self._COL_IDX]])
        if not to_import:
            self._log(_("No connection to import (all already exist or none selected)."))
            self._btn_import.set_sensitive(True)
            return
        self.on_done(to_import)
        logger.info(f"ProxmoxImportDialog._on_import_clicked | imported={len(to_import)}")
        self._btn_import.set_label(_("✓ Imported"))
        self._lbl_summary.set_markup(
            f"<b>{len(to_import)}</b> connexion(s) importée(s) avec succès."
        )


# ══════════════════════════════════════════════════════════════════════
# Point d'entrée BatchPlugin (autoload par BatchPluginRegistry)
# ══════════════════════════════════════════════════════════════════════


class ProxmoxImportBatchPlugin(BatchPlugin):
    """Adapte ``ProxmoxImportDialog`` au contrat ``BatchPlugin``.

    Reprend exactement la logique de l'ancien
    ``Wmain.on_mnu_import_proxmox_activate`` : ouvre le dialogue comme
    onglet épinglé (``Wmain.open_management_tab``, singleton par ``key``)
    et route les hôtes importés vers ``Wmain._import_done`` avec le groupe
    par défaut ``"PROXMOX"``.
    """

    tool_id = "import-proxmox"
    display_name = _("Import from Proxmox")
    icon_name = "network-server-symbolic"
    menu_section = "import"

    def activate(self) -> None:
        """Déclenche l'import de VMs depuis des nœuds Proxmox.

        Lance un dialogue d'import dans un nouvel onglet de management,
        permettant à l'utilisateur de sélectionner des nœuds,
        de scanner les VMs avec `qm`/`pvesh`, et de les importer.

        Raises:
            RuntimeError: Si l'application n'est pas liée au plugin.
        """
        if self.app is None:
            raise RuntimeError(
                "ProxmoxImportBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        app = self.app
        from utils import conf

        default_user = conf.__dict__.get("PROXMOX_DEFAULT_USER", "root")

        def on_done(host_dicts):
            if not host_dicts:
                msgbox(_("No host imported."), parent=app.window)
                return
            n = app._import_done(host_dicts, default_group="PROXMOX")
            if n:
                msgbox(
                    _("{n} connection(s) imported from Proxmox.").format(n=n), parent=app.window
                )
            else:
                msgbox(_("No new host (duplicates ignored)."), parent=app.window)

        app.open_management_tab(
            "proxmox-import",
            _("Import from Proxmox"),
            lambda: ProxmoxImportDialog(app.window, on_done, default_user, show=False),
        )


def get_batch_plugin() -> ProxmoxImportBatchPlugin:
    """Retourne l'instance unique du plugin Proxmox (découverte autoload).

    Returns:
        ProxmoxImportBatchPlugin: Instance du plugin d'import Proxmox.
    """
    return ProxmoxImportBatchPlugin()
