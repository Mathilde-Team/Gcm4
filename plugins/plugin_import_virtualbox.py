# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from VirtualBox » (BatchPlugin) pour GCM.

Porté depuis une révision antérieure du projet (code fonctionnel mais pas
encore aligné sur l'architecture ``GCMBase``/``open_management_tab`` — donc
adapté ici pour rejoindre exactement le même moule que
``plugin_import_libvirt.py``/``plugin_import_proxmox.py``). Découvert et
enregistré automatiquement par ``BatchPluginRegistry.autoload()`` (cf.
``plugin_base.py``) via ``get_batch_plugin()`` tout en bas de ce fichier.

Contrairement à libvirt/Proxmox, VirtualBox n'a pas d'équivalent dconf pour
découvrir automatiquement les hyperviseurs : les cibles se saisissent
manuellement (URI `vbox+ssh://user@host[:port]/`). L'inventaire des VMs
utilise `VBoxManage list vms` / `showvminfo --machinereadable` via SSH ; le
RDP natif de VirtualBox (VRDE) est sondé comme pour les autres imports. Pas
de SPICE (non supporté par VirtualBox). La logique bas niveau commune
(connexion SSH par clé, scan réseau, sonde de port) vit dans
``hypervisor_import_common.py``, partagée avec les autres imports
d'hyperviseur.
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

# Fallback gettext: injected by GCM at startup if available, no-op otherwise.
if "_" not in globals():

    def _(s: str) -> str:  # noqa: D401
        """Fallback no-op translation for isolated/test contexts."""
        return s


__all__ = [
    "VirtualBoxPrefsTab",
    "build_preferences_tab",
    "VirtualBoxImportBatchPlugin",
    "VirtualBoxImportDialog",
    "get_batch_plugin",
]


class VirtualBoxPrefsTab:
    """Onglet de préférences UI pour le plugin d'import VirtualBox.

    Configure l'utilisateur SSH par défaut utilisé lors des imports.
    """

    def __init__(self, notebook):
        """Initialise l'onglet de préférences VirtualBox.

        Args:
            notebook: Widget Gtk.Notebook auquel ajouter l'onglet.
        """
        logger.debug("VirtualBoxPrefsTab.__init__: entrée")
        self._build(notebook)
        logger.debug("VirtualBoxPrefsTab.__init__: fin")

    def _build(self, notebook):
        """Construit l'interface de l'onglet.

        Args:
            notebook: Widget Gtk.Notebook récepteur.
        """
        logger.debug("VirtualBoxPrefsTab._build: entrée")
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
        self._entry_user.set_text(getattr(conf, "VIRTUALBOX_DEFAULT_USER", "root") or "root")
        self._entry_user.set_tooltip_text(
            _("Used as default user in the VirtualBox import dialog.")
        )
        row.pack_start(self._entry_user, True, True, 0)
        outer.pack_start(row, False, False, 0)

        help_lbl = Gtk.Label()
        help_lbl.set_xalign(0)
        help_lbl.set_line_wrap(True)
        help_lbl.set_markup(
            _(
                "<i>Applies only to the VirtualBox import plugin. Can still be overridden per import run.</i>"
            )
        )
        outer.pack_start(help_lbl, False, False, 0)

        notebook.append_page(outer, Gtk.Label(label=_("VirtualBox")))
        outer.show_all()
        notebook.show_all()
        logger.debug("VirtualBoxPrefsTab._build: fin")

    def apply(self):
        """Valide et enregistre les préférences dans la configuration.

        Returns:
            None: Les valeurs sont sauvegardées dans conf.VIRTUALBOX_DEFAULT_USER.
        """
        logger.debug("VirtualBoxPrefsTab.apply: entrée")
        from utils import conf

        conf.VIRTUALBOX_DEFAULT_USER = (self._entry_user.get_text() or "").strip() or "root"
        logger.debug("VirtualBoxPrefsTab.apply: fin")


def build_preferences_tab(notebook):
    """Construit l'onglet de préférences VirtualBox dans le notebook donné.

    Args:
        notebook: Widget Gtk.Notebook récepteur.

    Returns:
        VirtualBoxPrefsTab: Instance de l'onglet (pour accès ultérieur si besoin).
    """
    return VirtualBoxPrefsTab(notebook)


def _virtualbox_parse_machinereadable(text):
    """Parse la sortie ``VBoxManage showvminfo --machinereadable``.

    Args:
        text (str): Sortie brute (une paire ``clef="valeur"`` par ligne).

    Returns:
        dict[str, str]: Paires clef/valeur, guillemets retirés.
    """
    logger.debug("_virtualbox_parse_machinereadable: entrée")
    info = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        info[key.strip()] = value.strip().strip('"')
    logger.debug("_virtualbox_parse_machinereadable: sortie")
    return info


def _virtualbox_fetch_hosts(uris, ssh_user, log_fn, progress_fn, proto_filter=None):
    """Collecte les VMs VirtualBox via SSH sur les hôtes donnés (`VBoxManage`).

    Contrairement à libvirt/Proxmox il n'existe pas de registre système
    équivalent à dconf pour découvrir les hôtes VirtualBox automatiquement :
    la liste des cibles est entièrement saisie manuellement par
    l'utilisateur (cf. ``VirtualBoxImportDialog._populate_uris``).

    Résolution IP : propriété invité ``/VirtualBox/GuestInfo/Net/0/V4/IP``
    (nécessite les Guest Additions) → ARP noyau par MAC → nmap (même
    pipeline que libvirt/Proxmox).

    Règles de génération des entrées :
    - SSH : jamais direct ; ProxyJump (-J) si IP connue, sinon shell de
      l'hôte + -t ssh user@vm (best effort, suppose une résolution de nom
      côté hôte).
    - RDP : VirtualBox expose nativement un service compatible RDP (VRDE)
      directement sur l'hôte — utilisé en priorité s'il est activé sur la
      VM (``vrde="on"``), sans dépendre de l'IP de la VM ni de Guest
      Additions. À défaut, RDP « invité » classique est proposé si le port
      3389 est confirmé ouvert sur l'IP de la VM.
    - VNC : sondage du port 5900 sur l'IP de la VM (désactivé par défaut,
      VirtualBox n'exposant pas de VNC nativement — utile seulement si un
      serveur VNC tourne dans l'invité).

    Args:
        uris (list[str]): Cibles hôtes VirtualBox (URI SSH, ex.
            ``vbox+ssh://user@host:22/``).
        ssh_user (str): Utilisateur SSH pour se connecter aux VMs.
        log_fn (callable): Fonction de log (msg: str).
        progress_fn (callable): Progression (frac: float, texte: str).
        proto_filter (set[str] | None): {'ssh','rdp','vnc'} ou None = tous.

    Returns:
        list[dict]: Hôtes prêts à importer dans GCM.
    """
    logger.debug("_virtualbox_fetch_hosts: entrée")
    if not hv_common.PARAMIKO_OK:
        log_fn(
            "ERREUR : paramiko non installé → "
            + hv_common.dep_install_hint("python3-paramiko", "python3-paramiko", "python-paramiko")
        )
        logger.debug("_virtualbox_fetch_hosts: sortie")
        return []
    if proto_filter is None:
        proto_filter = {"ssh", "rdp"}
    results = []
    total = len(uris)

    def run(client, cmd, timeout=30):
        return hv_common.libvirt_ssh_run(client, cmd, timeout)

    for idx, uri in enumerate(uris):
        log_fn(f"\n── Hôte VirtualBox : {uri}")
        progress_fn(idx / total, f"Connexion à {uri}…")
        parsed = urlparse(uri)
        hv_host = parsed.hostname or "localhost"
        hv_port = parsed.port or 22
        hv_user = parsed.username or ssh_user or "root"

        client = hv_common.paramiko_connect(hv_host, hv_port, hv_user, log_fn)
        if client is None:
            continue

        try:
            vbox_bin = run(client, "which VBoxManage 2>/dev/null || which vboxmanage 2>/dev/null")
            if not vbox_bin:
                log_fn("  ERREUR : VBoxManage introuvable sur cet hôte — hôte ignoré.")
                continue
            vbox_bin = vbox_bin.splitlines()[0].strip()

            vms_out = run(client, f"{vbox_bin} list vms")
            running = set(
                re.findall(r'^"([^"]+)"', run(client, f"{vbox_bin} list runningvms"), re.MULTILINE)
            )
            vm_names = re.findall(r'^"([^"]+)"', vms_out, re.MULTILINE)
            log_fn(f"  {len(vm_names)} VM(s) trouvée(s)")

            # ── ARP noyau (fallback résolution IP) ────────────────────────────
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

            nmap_table = {}
            if any(vm_name in running for vm_name in vm_names):
                nmap_table = hv_common.libvirt_nmap_scan(client, run, log_fn)
            combined = {**arp, **nmap_table}

            for vm_name in vm_names:
                state = "running" if vm_name in running else "poweroff"
                info_out = run(client, f'{vbox_bin} showvminfo "{vm_name}" --machinereadable')
                info = _virtualbox_parse_machinereadable(info_out)

                is_windows = "windows" in info.get("ostype", "").lower() or bool(
                    re.search(
                        r"win|w(?:2k|2019|2022|2016|2012|srv|dc|server)", vm_name, re.IGNORECASE
                    )
                )
                vm_user = "Administrator" if is_windows else ssh_user

                ip_addr = ""
                if state == "running":
                    prop_out = run(
                        client,
                        f'{vbox_bin} guestproperty get "{vm_name}" "/VirtualBox/GuestInfo/Net/0/V4/IP"',
                        timeout=15,
                    )
                    m_ip = re.search(r"Value:\s*(\d+\.\d+\.\d+\.\d+)", prop_out)
                    if m_ip:
                        ip_addr = m_ip.group(1)
                if not ip_addr:
                    mac_raw = info.get("macaddress1", "")
                    if mac_raw:
                        mac = ":".join(
                            mac_raw[i : i + 2] for i in range(0, len(mac_raw), 2)
                        ).lower()
                        ip_addr = combined.get(mac, "")

                grp, short = hv_common.vm_name_split(vm_name)
                added = False

                # ── SSH : toujours via l'hôte (jamais direct) ─────────────────
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
                                    f"[{state}] {vm_name} — SSH via hôte {hv_host}:{hv_port} → "
                                    f"ssh {vm_user}@{vm_name} (IP inconnue)"
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

                # ── RDP : natif via VRDE (prioritaire, aucune IP requise) ─────
                if "rdp" in proto_filter:
                    vrde_on = info.get("vrde", "").lower() == "on"
                    vrde_port = info.get("vrdeport", "")
                    if vrde_on and vrde_port and vrde_port != "0":
                        results.append(
                            {
                                "name": short,
                                "host": hv_host,
                                "user": vm_user,
                                "port": int(vrde_port),
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} — RDP natif VirtualBox (VRDE) sur {hv_host}:{vrde_port}"
                                ),
                                "group": f"{grp}/rdp" if grp else "rdp",
                                "hypervisor": hv_host,
                                "protocol": "rdp",
                                "extra_params": "",
                            }
                        )
                        log_fn(
                            f"  + [{grp}] {short:28s}  VRDE {hv_host}:{vrde_port:5s}  [{state}]  RDP (VRDE)"
                        )
                        added = True
                    elif ip_addr:
                        log_fn(
                            f"  ↳ VRDE désactivé pour {vm_name} — sondage RDP invité 3389 sur {ip_addr}…"
                        )
                        if hv_common.check_port_open(client, run, ip_addr, 3389):
                            results.append(
                                {
                                    "name": short,
                                    "host": ip_addr,
                                    "user": vm_user,
                                    "port": 3389,
                                    "password": "",
                                    "description": (
                                        f"[{state}] {vm_name} — RDP invité (port 3389 confirmé ouvert)"
                                    ),
                                    "group": f"{grp}/rdp" if grp else "rdp",
                                    "hypervisor": hv_host,
                                    "protocol": "rdp",
                                    "extra_params": "",
                                }
                            )
                            log_fn(
                                f"  + [{grp}] {short:28s}  {ip_addr:16s}  [{state}]  RDP (invité) ✓"
                            )
                            added = True
                        else:
                            log_fn(f"  ↳ port 3389 fermé sur {ip_addr} — RDP ignoré")

                # ── VNC : uniquement si un serveur tourne dans l'invité ───────
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
        progress_fn((idx + 1) / total, f"{idx + 1}/{total} hôte(s) traité(s)")
    logger.debug("_virtualbox_fetch_hosts: sortie")
    return results


class VirtualBoxImportDialog(GCMBase):
    """Dialogue GTK3 d'import de VMs depuis VirtualBox (scan natif `VBoxManage`).

    Classe indépendante — aucun lien avec LibvirtImportDialog/ProxmoxImportDialog,
    même si elle en reprend la structure (scan → prévisualisation) par
    cohérence avec les autres imports d'hyperviseurs. Différences :
    - Pas de découverte automatique (pas d'équivalent dconf) : les hôtes
      VirtualBox se saisissent manuellement.
    - Inventaire via `VBoxManage list vms` / `showvminfo --machinereadable`.
    - RDP natif via VRDE (Remote Display de VirtualBox, compatible RDP) —
      pas de SPICE (non supporté par VirtualBox).
    """

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
        """Initialise le dialogue d'import VirtualBox.

        Args:
            parent_window (Gtk.Window): Fenêtre parente.
            on_done_callback (callable): Appelée avec list[dict] lors de l'import.
            default_user (str): Utilisateur SSH par défaut.
            show (bool): Si False, le dialogue n'est pas affiché
                automatiquement (utilisé quand l'écran sera embarqué comme
                onglet épinglé, cf. ``Wmain.open_management_tab``).
        """
        logger.debug("VirtualBoxImportDialog.__init__: entrée")
        self.parent = parent_window
        self.on_done = on_done_callback
        self.default_user = default_user
        self._discovered = []
        GCMBase.__init__(self, path=None, parent=parent_window, show=show)
        self._build_ui(show=show)
        logger.debug("VirtualBoxImportDialog.__init__: fin")

    # ──────────────────────────────────────────────────────────────────────────
    # Construction de l'UI
    # ──────────────────────────────────────────────────────────────────────────

    def _build_ui(self, show=True):
        """Construit l'interface utilisateur du dialogue d'import VirtualBox.

        Crée un Gtk.Stack à 3 phases : configuration (URI), suivi
        (progression du scan), résultats (grille de prévisualisation).

        Args:
            show: Si True, affiche le dialogue après construction.
        """
        logger.debug("VirtualBoxImportDialog._build_ui: entrée")
        dlg = Gtk.Dialog(
            title=_("Import from VirtualBox"),
            transient_for=self.parent,
            modal=True,
        )
        dlg.set_default_size(820, 720)
        self._dlg = dlg
        self.main_widget = dlg
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

        hb_user = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hb_user.pack_start(Gtk.Label(label=_("Host SSH user:")), False, False, 0)
        self._entry_user = Gtk.Entry()
        self._entry_user.set_text(self.default_user)
        self._entry_user.set_width_chars(12)
        hb_user.pack_start(self._entry_user, False, False, 0)
        self._scan_box.pack_start(hb_user, False, False, 0)

        lbl_uri = Gtk.Label(label=_("VirtualBox hosts:"))
        lbl_uri.set_xalign(0)
        self._scan_box.pack_start(lbl_uri, False, False, 2)

        self._uri_store = Gtk.ListStore(bool, str)
        tv_uri = Gtk.TreeView(model=self._uri_store)
        tv_uri.set_headers_visible(True)
        cr_toggle = Gtk.CellRendererToggle()
        cr_toggle.connect("toggled", self._on_uri_toggled)
        tv_uri.append_column(Gtk.TreeViewColumn("", cr_toggle, active=0))
        col_uri_txt = Gtk.TreeViewColumn(
            _("VirtualBox host (user@host[:port])"), Gtk.CellRendererText(), text=1
        )
        col_uri_txt.set_expand(True)
        tv_uri.append_column(col_uri_txt)
        sw_uri = Gtk.ScrolledWindow()
        sw_uri.set_min_content_height(120)
        sw_uri.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw_uri.add(tv_uri)
        self._scan_box.pack_start(sw_uri, False, False, 0)

        # Saisie manuelle d'une cible VirtualBox (aucune découverte automatique)
        hb_manual = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hb_manual.pack_start(Gtk.Label(label=_("Add a VirtualBox host:")), False, False, 0)
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
                "SSH  (ProxyJump -J via host when VM IP is known, otherwise host shell + -t ssh user@vm)"
            )
        )
        self._chk_ssh.set_active(True)
        self._chk_rdp = Gtk.CheckButton(
            label=_("RDP  (native VirtualBox VRDE if enabled, otherwise guest port 3389 probe)")
        )
        self._chk_rdp.set_active(True)
        self._chk_vnc = Gtk.CheckButton(
            label=_("VNC  (probe port 5900 in the guest — off by default)")
        )
        self._chk_vnc.set_active(False)
        for chk in (self._chk_ssh, self._chk_rdp, self._chk_vnc):
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

        self._progress = Gtk.ProgressBar()
        self._progress.set_show_text(True)
        self._progress.set_text(_("Waiting…"))

        self._btn_scan = Gtk.Button(label=_("🔍  Scan VirtualBox hosts"))
        self._btn_scan.get_style_context().add_class("suggested-action")
        self._btn_scan.connect("clicked", self._on_scan_clicked)
        scan_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        scan_btn_box.pack_end(self._btn_scan, False, False, 0)
        self._scan_box.pack_start(scan_btn_box, False, False, 0)

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

        col_proto = Gtk.TreeViewColumn(
            _("Proto"), Gtk.CellRendererText(), text=self._COL_PROTO, foreground=self._COL_FG
        )
        col_proto.set_min_width(55)
        tv_prev.append_column(col_proto)

        col_name = Gtk.TreeViewColumn(
            _("VM name"), Gtk.CellRendererText(), text=self._COL_NAME, foreground=self._COL_FG
        )
        col_name.set_expand(True)
        col_name.set_min_width(170)
        tv_prev.append_column(col_name)

        col_grp = Gtk.TreeViewColumn(
            _("Group"), Gtk.CellRendererText(), text=self._COL_GROUP, foreground=self._COL_FG
        )
        col_grp.set_min_width(90)
        tv_prev.append_column(col_grp)

        col_host = Gtk.TreeViewColumn(
            _("IP / Host"), Gtk.CellRendererText(), text=self._COL_HOST, foreground=self._COL_FG
        )
        col_host.set_min_width(120)
        tv_prev.append_column(col_host)

        col_state = Gtk.TreeViewColumn(
            _("State"), Gtk.CellRendererText(), text=self._COL_STATE, foreground=self._COL_FG
        )
        col_state.set_min_width(75)
        tv_prev.append_column(col_state)

        col_exist = Gtk.TreeViewColumn(
            _("Imported"),
            Gtk.CellRendererText(),
            text=self._COL_EXIST_LBL,
            foreground=self._COL_FG,
        )
        col_exist.set_min_width(70)
        tv_prev.append_column(col_exist)

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
        logger.debug("VirtualBoxImportDialog._build_ui: fin")

    # ──────────────────────────────────────────────────────────────────────────
    # Peuplement des cibles (pas de découverte automatique pour VirtualBox)
    # ──────────────────────────────────────────────────────────────────────────

    def _populate_uris(self):
        """Amorce la liste des cibles avec un exemple, faute de découverte auto.

        Returns:
            None: Met à jour `self._uri_store` en place.
        """
        logger.debug("VirtualBoxImportDialog._populate_uris: entrée")
        self._log(
            _(
                "VirtualBox has no central registry (unlike virt-manager/dconf).\nAdd your hosts manually below."
            )
        )
        self._uri_store.append([False, f"{self.default_user}@hyperviseur"])
        logger.debug("VirtualBoxImportDialog._populate_uris: fin")

    def _normalize_manual_uri(self, raw_target):
        """Convertit une saisie libre en URI `vbox+ssh://user@host[:port]/`.

        Args:
            raw_target (str): Cible saisie (IP, user@host, host:port, etc.).

        Returns:
            str: URI normalisée, ou chaîne vide si entrée invalide.
        """
        logger.debug("VirtualBoxImportDialog._normalize_manual_uri: entrée")
        raw = (raw_target or "").strip()
        if not raw:
            logger.debug("VirtualBoxImportDialog._normalize_manual_uri: sortie")
            return ""
        if "://" in raw:
            logger.debug("VirtualBoxImportDialog._normalize_manual_uri: sortie")
            return raw
        if "@" in raw:
            user, hostport = raw.split("@", 1)
            user = user or self.default_user
        else:
            user, hostport = self.default_user, raw
        host = hostport
        port = None
        if ":" in hostport:
            h, p = hostport.rsplit(":", 1)
            if p.isdigit():
                host, port = h, int(p)
        host = host.strip()
        if not host:
            logger.debug("VirtualBoxImportDialog._normalize_manual_uri: sortie")
            return ""
        if port is None or port == 22:
            logger.debug("VirtualBoxImportDialog._normalize_manual_uri: sortie")
            return f"vbox+ssh://{user}@{host}/"
        logger.debug("VirtualBoxImportDialog._normalize_manual_uri: sortie")
        return f"vbox+ssh://{user}@{host}:{port}/"

    def _on_add_manual_uri(self, widget):
        """Ajoute une cible VirtualBox saisie manuellement dans la liste.

        Args:
            widget (Gtk.Widget): Widget déclencheur (bouton ou entrée).

        Returns:
            None: Met à jour la liste des cibles et le log UI.
        """
        logger.debug("VirtualBoxImportDialog._on_add_manual_uri: entrée")
        uri = self._normalize_manual_uri(self._entry_manual_uri.get_text())
        if not uri:
            self._log(_("Invalid VirtualBox target."))
            logger.debug("VirtualBoxImportDialog._on_add_manual_uri: sortie")
            return
        existing = [row[1] for row in self._uri_store]
        if uri in existing:
            self._log(_("Target already present: {uri}").format(uri=uri))
            self._entry_manual_uri.set_text("")
            logger.debug("VirtualBoxImportDialog._on_add_manual_uri: sortie")
            return
        self._uri_store.append([True, uri])
        self._log(_("Target added: {uri}").format(uri=uri))
        self._entry_manual_uri.set_text("")
        logger.debug("VirtualBoxImportDialog._on_add_manual_uri: fin")

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

        logger.debug("VirtualBoxImportDialog._log: entrée")

        def _do():
            logger.debug("_do: entrée")
            buf = self._log_buf
            buf.insert(buf.get_end_iter(), msg + "\n")
            self._log_view.scroll_mark_onscreen(buf.get_insert())
            logger.debug("_do: sortie")
            return False

        GLib.idle_add(_do)
        logger.debug("VirtualBoxImportDialog._log: fin")

    def _set_progress(self, frac, text):
        """Met à jour la barre de progression du scan.

        Args:
            frac (float): Fraction de progression entre 0 et 1.
            text (str): Texte d'état à afficher.

        Returns:
            None: Planifie la mise à jour de la progress bar.
        """

        logger.debug("VirtualBoxImportDialog._set_progress: entrée")

        def _do():
            logger.debug("_do: entrée")
            self._progress.set_fraction(frac)
            self._progress.set_text(text)
            logger.debug("_do: sortie")
            return False

        GLib.idle_add(_do)
        logger.debug("VirtualBoxImportDialog._set_progress: fin")

    def _on_uri_toggled(self, renderer, path):
        """Inverse l'état coché d'une cible dans la liste de scan."""
        logger.debug("VirtualBoxImportDialog._on_uri_toggled: entrée")
        self._uri_store[path][0] = not self._uri_store[path][0]
        logger.debug("VirtualBoxImportDialog._on_uri_toggled: fin")

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 1 → Scan
    # ──────────────────────────────────────────────────────────────────────────

    def _on_scan_clicked(self, widget):
        """Lance le scan des hôtes VirtualBox sélectionnés.

        Args:
            widget (Gtk.Widget): Bouton scanner.

        Returns:
            None: Démarre un thread de scan et met à jour l'UI.
        """
        logger.debug("VirtualBoxImportDialog._on_scan_clicked: entrée")
        self._btn_scan.set_sensitive(False)
        self._stack.set_visible_child_name("monitor")
        logger.debug("VirtualBoxImportDialog._on_scan_clicked | scan started")
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
            self._log(_("No host selected."))
            self._btn_scan.set_sensitive(True)
            logger.debug("VirtualBoxImportDialog._on_scan_clicked: sortie")
            return
        user = self._entry_user.get_text().strip() or "root"
        proto_filter = set()
        if self._chk_ssh.get_active():
            proto_filter.add("ssh")
        if self._chk_rdp.get_active():
            proto_filter.add("rdp")
        if self._chk_vnc.get_active():
            proto_filter.add("vnc")
        if not proto_filter:
            self._log(_("No connection type selected."))
            self._btn_scan.set_sensitive(True)
            logger.debug("VirtualBoxImportDialog._on_scan_clicked: sortie")
            return

        def worker():
            logger.debug("worker: entrée")
            try:
                results = _virtualbox_fetch_hosts(
                    uris, user, self._log, self._set_progress, proto_filter
                )
            except Exception as exc:
                logger.exception(f"VirtualBoxImportDialog._on_scan_clicked | scan failed: {exc}")
                self._log(_("Scan failed: {exc}").format(exc=exc))
                results = []
            GLib.idle_add(self._show_preview, results)
            logger.debug("worker: fin")

        threading.Thread(target=worker, daemon=True).start()
        logger.debug("VirtualBoxImportDialog._on_scan_clicked: fin")

    # ──────────────────────────────────────────────────────────────────────────
    # Phase 2 → Tableau de prévisualisation
    # ──────────────────────────────────────────────────────────────────────────

    def _host_exists(self, name, grp):
        """Vérifie si un hôte avec ce nom existe déjà dans le groupe GCM."""
        logger.debug("VirtualBoxImportDialog._host_exists: entrée")
        if grp in groups:
            logger.debug("VirtualBoxImportDialog._host_exists: sortie")
            return any(h.name == name for h in groups[grp])
        logger.debug("VirtualBoxImportDialog._host_exists: sortie")
        return False

    def _show_preview(self, host_dicts):
        """Affiche les résultats du scan dans la grille de prévisualisation.

        Args:
            host_dicts (list[dict]): Connexions détectées prêtes à importer.

        Returns:
            None: Remplit la grille de prévisualisation et met à jour le résumé.
        """
        logger.debug("VirtualBoxImportDialog._show_preview: entrée")
        self._discovered = host_dicts
        self._preview_store.clear()

        n_new = 0
        n_exists = 0
        for idx, hd in enumerate(host_dicts):
            name = hd.get("name", "")
            grp = hd.get("group", "VIRTUALBOX")
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
            f"\nScan terminé — {total} connexion(s) : {n_new} nouvelle(s), {n_exists} déjà présente(s)."
        )
        self._stack.set_visible_child_name("results")
        logger.info(
            f"VirtualBoxImportDialog._show_preview | total={total} new={n_new} existing={n_exists}"
        )
        self._btn_scan.set_label(_("🔄  Rescan"))
        self._btn_scan.set_sensitive(True)
        self._dlg.resize(820, 900)
        logger.debug("VirtualBoxImportDialog._show_preview: fin")

    def _on_preview_toggled(self, renderer, path):
        """Inverse la sélection d'une ligne de prévisualisation."""
        logger.debug("VirtualBoxImportDialog._on_preview_toggled: entrée")
        self._preview_store[path][self._COL_SEL] = not self._preview_store[path][self._COL_SEL]
        logger.debug("VirtualBoxImportDialog._on_preview_toggled: fin")

    def _select_all(self, value):
        """Coche ou décoche toutes les lignes de prévisualisation."""
        logger.debug("VirtualBoxImportDialog._select_all: entrée")
        for row in self._preview_store:
            row[self._COL_SEL] = value
        logger.debug("VirtualBoxImportDialog._select_all: fin")

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
        logger.debug("VirtualBoxImportDialog._on_import_clicked: entrée")
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
            logger.debug("VirtualBoxImportDialog._on_import_clicked: sortie")
            return
        self.on_done(to_import)
        logger.info(f"VirtualBoxImportDialog._on_import_clicked | imported={len(to_import)}")
        self._btn_import.set_label(_("✓ Imported"))
        self._lbl_summary.set_markup(
            f"<b>{len(to_import)}</b> connexion(s) importée(s) avec succès."
        )
        logger.debug("VirtualBoxImportDialog._on_import_clicked: fin")


# ══════════════════════════════════════════════════════════════════════
# Point d'entrée BatchPlugin (autoload par BatchPluginRegistry)
# ══════════════════════════════════════════════════════════════════════


class VirtualBoxImportBatchPlugin(BatchPlugin):
    """Adapte ``VirtualBoxImportDialog`` au contrat ``BatchPlugin``.

    Reprend la logique de l'ancien ``Wmain.on_mnu_import_virtualbox_activate``
    (jamais mergé sur cette base de code, porté depuis une révision antérieure
    du projet), adaptée pour ouvrir le dialogue comme onglet épinglé
    (``Wmain.open_management_tab``, singleton par ``key``) au lieu d'une
    fenêtre modale autonome — même convention que Libvirt/Proxmox.
    """

    tool_id = "import-virtualbox"
    display_name = _("Import from VirtualBox")
    icon_name = "network-server-symbolic"
    menu_section = "import"

    def activate(self) -> None:
        """Déclenche l'import de VMs depuis des hyperviseurs VirtualBox.

        Lance un dialogue d'import dans un nouvel onglet de management,
        permettant à l'utilisateur de saisir des cibles SSH, de scanner
        les VMs via `VBoxManage`, et de les importer.

        Raises:
            RuntimeError: Si l'application n'est pas liée au plugin.
        """
        logger.debug("VirtualBoxImportBatchPlugin.activate: entrée")
        if self.app is None:
            raise RuntimeError(
                "VirtualBoxImportBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        app = self.app
        from utils import conf

        default_user = conf.__dict__.get("VIRTUALBOX_DEFAULT_USER", "root")

        def on_done(host_dicts):
            logger.debug("on_done: entrée")
            if not host_dicts:
                msgbox(_("No host imported."), parent=app.window)
                logger.debug("on_done: sortie")
                return
            n = app._import_done(host_dicts, default_group="VIRTUALBOX")
            if n:
                msgbox(
                    _("{n} connection(s) imported from VirtualBox.").format(n=n), parent=app.window
                )
            else:
                msgbox(_("No new host (duplicates ignored)."), parent=app.window)
            logger.debug("on_done: fin")

        app.open_management_tab(
            "virtualbox-import",
            _("Import from VirtualBox"),
            lambda: VirtualBoxImportDialog(app.window, on_done, default_user, show=False),
        )
        logger.debug("VirtualBoxImportBatchPlugin.activate: fin")


def get_batch_plugin() -> VirtualBoxImportBatchPlugin:
    """Retourne l'instance unique du plugin VirtualBox (découverte autoload).

    Returns:
        VirtualBoxImportBatchPlugin: Instance du plugin d'import VirtualBox.
    """
    return VirtualBoxImportBatchPlugin()
