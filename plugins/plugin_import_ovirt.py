"""Outil « Import from oVirt » (BatchPlugin) pour GCM.

Porté depuis une révision antérieure du projet (mêmes conditions que
``plugin_import_virtualbox.py`` : code fonctionnel mais pas encore aligné
sur l'architecture ``GCMBase``/``open_management_tab``, adapté ici pour
rejoindre le même moule que les autres imports d'hyperviseur). Découvert et
enregistré automatiquement par ``BatchPluginRegistry.autoload()`` (cf.
``plugin_base.py``) via ``get_batch_plugin()`` tout en bas de ce fichier.

Contrairement à libvirt/Proxmox/VirtualBox, oVirt est piloté par un moteur
central (« Engine ») : au lieu d'une liste de cibles SSH, on saisit l'URL du
moteur, un identifiant/mot de passe API REST, et un utilisateur SSH utilisé
uniquement en repli (jump proxy). L'inventaire des VMs se fait via
``GET /vms`` sur l'API REST (bibliothèque standard ``urllib`` uniquement,
pas de dépendance ajoutée) ; la joignabilité de chaque VM est ensuite testée
activement : direct → jump moteur → jump hyperviseur → question interactive
à l'utilisateur en dernier recours (cf. ``_ovirt_resolve_route``, qui
utilise ``widgets.inputbox`` pour cette dernière étape). La logique SSH bas
niveau (connexion par clé, sonde de port) reste celle de
``hypervisor_import_common.py``, partagée avec les autres imports
d'hyperviseur.
"""

from __future__ import annotations

import base64
import json
import re
import socket
import ssl
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import gi
from loguru import logger

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

# isort: split
import hypervisor_import_common as hv_common  # noqa: E402, I001
from plugin_base import BatchPlugin  # noqa: E402, I001
from utils import GCMBase, msgbox  # noqa: E402, I001
from widgets import inputbox  # noqa: E402, I001

# Fallback gettext: injected by GCM at startup if available, no-op otherwise.
if "_" not in globals():

    def _(s: str) -> str:  # noqa: D401
        """Fallback no-op translation for isolated/test contexts."""
        return s


__all__ = ["OvirtImportBatchPlugin", "OvirtImportDialog", "get_batch_plugin"]


def _ovirt_api_get(engine_url, username, password, verify_ssl, path, log_fn=None, timeout=20):
    """Effectue un GET JSON authentifié contre l'API REST oVirt.

    Utilise uniquement la bibliothèque standard (``urllib``) : pas de
    dépendance supplémentaire (contrairement à ``requests``, absent du
    projet).

    Args:
        engine_url (str): URL de base du moteur, ex.
            ``https://engine.example.com/ovirt-engine/api`` (sans slash final).
        username (str): Utilisateur oVirt, ex. ``admin@internal``.
        password (str): Mot de passe.
        verify_ssl (bool): Si False, désactive la vérification du certificat TLS.
        path (str): Chemin relatif à ``engine_url`` (ex. ``/vms``).
        log_fn (callable, optional): Fonction de log.
        timeout (int): Timeout de la requête en secondes.

    Returns:
        dict | None: Corps JSON décodé, ou None en cas d'échec.
    """
    url = engine_url.rstrip("/") + path
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    req = Request(
        url,
        headers={
            "Authorization": f"Basic {creds}",
            "Accept": "application/json",
        },
    )
    ctx = None
    if not verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode(errors="replace"))
    except HTTPError as e:
        logger.warning("import ovirt | erreur HTTP {} sur {} : {}", e.code, path, e.reason)
        if log_fn:
            log_fn(f"  ERREUR HTTP {e.code} sur {path} : {e.reason}")
    except URLError as e:
        logger.warning("import ovirt | connexion à {} impossible : {}", engine_url, e.reason)
        if log_fn:
            log_fn(f"  ERREUR de connexion à {engine_url} : {e.reason}")
    except Exception as e:
        logger.warning("import ovirt | erreur inattendue sur {} : {}", path, e)
        if log_fn:
            log_fn(f"  ERREUR inattendue sur {path} : {e}")
    return None


def _probe_tcp_direct(host, port, timeout=4):
    """Teste une connexion TCP directe depuis la machine locale (GCM elle-même,
    sans passer par un rebond SSH).

    Args:
        host (str): Hôte cible.
        port (int): Port TCP.
        timeout (float): Timeout en secondes.

    Returns:
        bool: True si la connexion TCP directe aboutit.
    """
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError as exc:
        logger.debug("import ovirt | {}:{} injoignable : {}", host, port, exc)
        return False


def _ovirt_get_host_address(engine_url, username, password, verify_ssl, host_id, log_fn=None):
    """Récupère l'adresse (hostname/IP) d'un hôte oVirt à partir de son id.

    Args:
        engine_url (str): URL de l'API oVirt.
        username (str): Utilisateur oVirt.
        password (str): Mot de passe.
        verify_ssl (bool): Vérifier le certificat TLS.
        host_id (str): Id de l'hôte (lien ``vm.host.id`` renvoyé par l'API).
        log_fn (callable, optional): Fonction de log.

    Returns:
        str: Adresse de l'hôte, ou chaîne vide si indisponible.
    """
    if not host_id:
        return ""
    data = _ovirt_api_get(engine_url, username, password, verify_ssl, f"/hosts/{host_id}", log_fn)
    if not data:
        return ""
    return data.get("address", "") or data.get("name", "") or ""


def _prompt_manual_route(vm_name, log_fn):
    """Demande interactivement une route de secours (IP directe ou jump proxy)
    pour une VM injoignable par tous les moyens automatiques.

    Marshale l'affichage du dialogue sur le thread principal GTK via
    ``GLib.idle_add`` (obligatoire : GTK ne peut être piloté que depuis ce
    thread) et bloque le thread de scan appelant jusqu'à la réponse.

    Args:
        vm_name (str): Nom de la VM, pour le message affiché.
        log_fn (callable): Fonction de log.

    Returns:
        str | None: Texte saisi par l'utilisateur, ou None si annulé/vide.
    """
    result = {}
    done = threading.Event()

    def _do():
        try:
            result["value"] = inputbox(
                _("Unreachable VM: {vm}").format(vm=vm_name),
                _(
                    "Could not reach '{vm}' directly, via the oVirt engine, or via its "
                    "hypervisor.\n\n"
                    'Enter either a direct target — "ip" or "ip:port" — or a jump '
                    'proxy — "-J user@jumphost[:port] target_ip".\n'
                    "Leave empty to skip this VM."
                ).format(vm=vm_name),
                "",
            )
        finally:
            done.set()
        return False

    GLib.idle_add(_do)
    done.wait()
    value = (result.get("value") or "").strip()
    if not value:
        log_fn(f"  - {vm_name} : ignorée (aucune route fournie par l'utilisateur)")
        return None
    return value


def _parse_manual_route(manual, fallback_ip):
    """Interprète la route saisie manuellement par l'utilisateur.

    Args:
        manual (str): Texte saisi (cf. ``_prompt_manual_route``).
        fallback_ip (str): IP cible à utiliser si la saisie ne fournit
            qu'un jump host sans cible explicite.

    Returns:
        dict | None: {"mode": "direct"|"jump", "host": str, "extra_params": str}.
    """
    manual = manual.strip()
    if manual.startswith("-J"):
        parts = manual.split()
        target = (
            parts[-1]
            if len(parts) > 1 and re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[-1])
            else fallback_ip
        )
        jump_flag = " ".join(parts[:-1]) if target != parts[-1] else " ".join(parts)
        if not target:
            return None
        return {"mode": "jump", "host": target, "extra_params": jump_flag}
    host = manual.split(":")[0].strip()
    if not host:
        return None
    return {"mode": "direct", "host": host, "extra_params": ""}


def _ovirt_resolve_route(
    vm_name, ip_addr, engine_url, api_user, api_pass, verify_ssl, ssh_user, host_id, log_fn
):
    """Détermine comment atteindre une VM oVirt en SSH/RDP.

    Essaie dans l'ordre : (1) connexion TCP directe depuis GCM, (2) jump
    proxy SSH via la machine du moteur oVirt (extraite de ``engine_url``),
    (3) jump proxy SSH via l'hyperviseur qui héberge la VM (résolu via
    l'API). Si les trois échouent, demande une route de secours à
    l'utilisateur (cf. ``_prompt_manual_route``).

    Args:
        vm_name (str): Nom de la VM.
        ip_addr (str): IP invité rapportée par l'agent oVirt.
        engine_url (str): URL de l'API oVirt.
        api_user (str): Utilisateur de l'API oVirt.
        api_pass (str): Mot de passe de l'API oVirt.
        verify_ssl (bool): Vérifier le certificat TLS du moteur.
        ssh_user (str): Utilisateur SSH pour les rebonds (moteur/hyperviseur).
        host_id (str): Id de l'hôte oVirt hébergeant la VM (peut être vide).
        log_fn (callable): Fonction de log.

    Returns:
        dict | None: {"mode": "direct"|"jump", "host": str, "extra_params": str},
        ou None si la VM doit être ignorée.
    """
    # 1) Connexion directe depuis la machine locale (GCM)
    log_fn(f"  ↳ {vm_name} : test de connexion directe vers {ip_addr}:22…")
    if _probe_tcp_direct(ip_addr, 22):
        log_fn(f"  ↳ {vm_name} : accès direct OK")
        return {"mode": "direct", "host": ip_addr, "extra_params": ""}
    log_fn(f"  ↳ {vm_name} : accès direct impossible")

    # 2) Jump proxy via la machine du moteur oVirt
    engine_host = urlparse(engine_url).hostname or ""
    if engine_host:
        log_fn(f"  ↳ {vm_name} : test du jump proxy via le moteur oVirt ({engine_host})…")
        client = hv_common.paramiko_connect(engine_host, 22, ssh_user, log_fn)
        if client is not None:
            try:
                if hv_common.check_port_open(client, hv_common.libvirt_ssh_run, ip_addr, 22):
                    log_fn(f"  ↳ {vm_name} : jump proxy via le moteur OK")
                    return {
                        "mode": "jump",
                        "host": ip_addr,
                        "extra_params": f"-J {ssh_user}@{engine_host}",
                    }
                log_fn(
                    f"  ↳ {vm_name} : moteur joignable en SSH mais VM injoignable depuis le moteur"
                )
            finally:
                client.close()
        else:
            log_fn(f"  ↳ {vm_name} : SSH vers le moteur impossible")

    # 3) Jump proxy via l'hyperviseur hébergeant la VM
    hv_addr = _ovirt_get_host_address(engine_url, api_user, api_pass, verify_ssl, host_id, log_fn)
    if hv_addr:
        log_fn(f"  ↳ {vm_name} : test du jump proxy via l'hyperviseur ({hv_addr})…")
        client = hv_common.paramiko_connect(hv_addr, 22, ssh_user, log_fn)
        if client is not None:
            try:
                if hv_common.check_port_open(client, hv_common.libvirt_ssh_run, ip_addr, 22):
                    log_fn(f"  ↳ {vm_name} : jump proxy via l'hyperviseur OK")
                    return {
                        "mode": "jump",
                        "host": ip_addr,
                        "extra_params": f"-J {ssh_user}@{hv_addr}",
                    }
                log_fn(
                    f"  ↳ {vm_name} : hyperviseur joignable en SSH mais VM injoignable depuis l'hyperviseur"
                )
            finally:
                client.close()
        else:
            log_fn(f"  ↳ {vm_name} : SSH vers l'hyperviseur ({hv_addr}) impossible")
    else:
        log_fn(f"  ↳ {vm_name} : hyperviseur de la VM inconnu (id manquant dans l'API)")

    # 4) Secours manuel : demander à l'utilisateur
    manual = _prompt_manual_route(vm_name, log_fn)
    if not manual:
        return None
    route = _parse_manual_route(manual, ip_addr)
    if route:
        log_fn(
            f"  ↳ {vm_name} : route manuelle retenue → {route['mode']} {route['host']} {route['extra_params']}"
        )
    return route


def _ovirt_probe_port_via_route(route, port, ssh_user, log_fn):
    """Sonde un port TCP sur la cible d'une route déjà résolue (directe ou jump).

    Args:
        route (dict): Route retournée par ``_ovirt_resolve_route``/``_parse_manual_route``.
        port (int): Port TCP à sonder.
        ssh_user (str): Utilisateur SSH pour se reconnecter au jump host si besoin.
        log_fn (callable): Fonction de log.

    Returns:
        bool: True si le port est confirmé ouvert.
    """
    if route["mode"] == "direct":
        return _probe_tcp_direct(route["host"], port)
    m = re.search(r"-J\s+(?:[^@\s]+@)?([^\s:]+)(?::(\d+))?", route.get("extra_params", ""))
    if not m:
        return False
    jump_host = m.group(1)
    jump_port = int(m.group(2)) if m.group(2) else 22
    client = hv_common.paramiko_connect(jump_host, jump_port, ssh_user, log_fn)
    if client is None:
        return False
    try:
        return hv_common.check_port_open(client, hv_common.libvirt_ssh_run, route["host"], port)
    finally:
        client.close()


def _ovirt_fetch_hosts(
    engine_url, username, password, verify_ssl, ssh_user, log_fn, progress_fn, proto_filter=None
):
    """Collecte les VMs depuis un moteur oVirt/RHV via son API REST.

    Pour chaque VM dont l'IP invité est connue, la joignabilité est
    vérifiée activement avant de générer une entrée SSH/RDP, dans l'ordre :
    connexion directe depuis GCM → jump proxy SSH via le moteur oVirt →
    jump proxy SSH via l'hyperviseur qui héberge la VM. Si tout échoue,
    l'utilisateur est interrogé pour fournir une route de secours (IP
    directe ou jump proxy) ; sans réponse, la VM est ignorée. Voir
    ``_ovirt_resolve_route``.

    Résolution IP : agent invité rapporté par l'API (``guest_info``) —
    nécessite l'agent invité oVirt installé dans la VM.

    Args:
        engine_url (str): URL de l'API oVirt, ex.
            ``https://engine.example.com/ovirt-engine/api``.
        username (str): Utilisateur oVirt (ex. ``admin@internal``).
        password (str): Mot de passe.
        verify_ssl (bool): Vérifier le certificat TLS du moteur.
        ssh_user (str): Utilisateur SSH pour les rebonds (moteur/hyperviseur)
            et pour les VMs (sauf invités Windows détectés).
        log_fn (callable): Fonction de log (msg: str).
        progress_fn (callable): Progression (frac: float, texte: str).
        proto_filter (set[str] | None): {'ssh','rdp'} ou None = tous.

    Returns:
        list[dict]: Hôtes prêts à importer dans GCM.
    """
    if proto_filter is None:
        proto_filter = {"ssh", "rdp"}
    results = []

    progress_fn(0.05, f"Connexion à {engine_url}…")
    data = _ovirt_api_get(engine_url, username, password, verify_ssl, "/vms", log_fn)
    if data is None:
        log_fn("  ERREUR : impossible de récupérer la liste des VMs (vérifier URL/identifiants).")
        return results

    vms = data.get("vm", [])
    if isinstance(vms, dict):  # certaines versions renvoient un objet unique
        vms = [vms]
    total = len(vms) or 1
    log_fn(f"  {len(vms)} VM(s) trouvée(s) sur le moteur")

    for idx, vm in enumerate(vms):
        vm_name = vm.get("name", "")
        state = vm.get("status", "")
        if isinstance(state, dict):
            state = state.get("#text", "") or ""
        os_info = vm.get("os", {}) or {}
        os_type = os_info.get("type", "") if isinstance(os_info, dict) else ""
        is_windows = "windows" in str(os_type).lower() or bool(
            re.search(r"win|w(?:2k|2019|2022|2016|2012|srv|dc|server)", vm_name, re.IGNORECASE)
        )
        vm_user = "Administrator" if is_windows else ssh_user

        ip_addr = ""
        guest_info = vm.get("guest_info", {}) or {}
        ips = guest_info.get("ips", {}) if isinstance(guest_info, dict) else {}
        ip_list = ips.get("ip", []) if isinstance(ips, dict) else []
        if isinstance(ip_list, dict):
            ip_list = [ip_list]
        for ip_entry in ip_list:
            addr = ip_entry.get("address", "") if isinstance(ip_entry, dict) else ""
            if addr and not addr.startswith("127.") and ":" not in addr:  # IPv4 uniquement
                ip_addr = addr
                break

        grp, short = hv_common.vm_name_split(vm_name) if vm_name else ("OVIRT", vm_name)
        added = False
        host_link = vm.get("host", {}) or {}
        host_id = host_link.get("id", "") if isinstance(host_link, dict) else ""

        if ip_addr:
            route = _ovirt_resolve_route(
                vm_name,
                ip_addr,
                engine_url,
                username,
                password,
                verify_ssl,
                ssh_user,
                host_id,
                log_fn,
            )
        else:
            log_fn(f"  ↳ {vm_name or vm.get('id', '?')} : IP inconnue (agent invité absent ?)")
            manual = _prompt_manual_route(vm_name or vm.get("id", "?"), log_fn)
            route = _parse_manual_route(manual, "") if manual else None

        if route is None:
            progress_fn((idx + 1) / total, f"{idx + 1}/{total} VM(s) traitée(s)")
            continue

        route_desc = (
            "connexion directe testée"
            if route["mode"] == "direct"
            else f"jump proxy ({route['extra_params']})"
        )

        if "ssh" in proto_filter:
            results.append(
                {
                    "name": short,
                    "host": route["host"],
                    "user": vm_user,
                    "port": 22,
                    "password": "",
                    "description": f"[{state}] {vm_name} — SSH via oVirt ({route_desc})",
                    "group": f"{grp}/ssh" if grp else "ssh",
                    "hypervisor": engine_url,
                    "protocol": "ssh",
                    "extra_params": route.get("extra_params", ""),
                }
            )
            log_fn(
                f"  + [{grp}] {short:28s}  {route['host']:16s}  [{state}]  SSH ({route['mode']})"
            )
            added = True

        if "rdp" in proto_filter and is_windows:
            log_fn(f"  ↳ Sondage RDP 3389 sur {route['host']} ({route['mode']})…")
            if _ovirt_probe_port_via_route(route, 3389, ssh_user, log_fn):
                results.append(
                    {
                        "name": short,
                        "host": route["host"],
                        "user": vm_user,
                        "port": 3389,
                        "password": "",
                        "description": f"[{state}] {vm_name} — RDP via oVirt ({route_desc}, port confirmé ouvert)",
                        "group": f"{grp}/rdp" if grp else "rdp",
                        "hypervisor": engine_url,
                        "protocol": "rdp",
                        "extra_params": route.get("extra_params", ""),
                    }
                )
                log_fn(
                    f"  + [{grp}] {short:28s}  {route['host']:16s}  [{state}]  RDP ({route['mode']}) ✓"
                )
                added = True
            else:
                log_fn(f"  ↳ port 3389 fermé/injoignable sur {route['host']} — RDP ignoré")

        if not added:
            log_fn(f"  - {vm_name} : aucune connexion générée")

        progress_fn((idx + 1) / total, f"{idx + 1}/{total} VM(s) traitée(s)")

    return results


class OvirtImportDialog(GCMBase):
    """Dialogue GTK3 d'import de VMs depuis un moteur oVirt/RHV (API REST).

    Classe indépendante — même structure en deux phases (scan → prévisualisation)
    que Libvirt/Proxmox/VirtualBox, mais la phase 1 diffère car oVirt est
    piloté par un moteur central : au lieu d'une liste de cibles SSH, on
    saisit l'URL du moteur, un identifiant/mot de passe API, et un
    utilisateur SSH utilisé uniquement en repli (jump proxy). L'inventaire
    des VMs se fait via ``GET /vms`` sur l'API REST (bibliothèque standard
    ``urllib`` uniquement, pas de dépendance ajoutée) ; la joignabilité de
    chaque VM est ensuite testée activement (direct → jump moteur → jump
    hyperviseur → question à l'utilisateur en dernier recours), cf.
    ``_ovirt_resolve_route``.
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

    def __init__(self, parent_window, on_done_callback, default_user="admin@internal", show=True):
        """Initialise le dialogue d'import oVirt.

        Args:
            parent_window (Gtk.Window): Fenêtre parente.
            on_done_callback (callable): Appelée avec list[dict] lors de l'import.
            default_user (str): Utilisateur oVirt par défaut (ex. admin@internal).
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
        """Construit l'interface utilisateur du dialogue d'import oVirt.

        Crée un Gtk.Stack à 3 phases : configuration (API), suivi
        (progression du scan), résultats (grille de prévisualisation).

        Args:
            show: Si True, affiche le dialogue après construction.
        """
        dlg = Gtk.Dialog(
            title=_("Import from oVirt"),
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

        # ── Phase 1 : Paramètres de connexion au moteur ──────────────────────
        self._scan_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        grid.attach(Gtk.Label(label=_("Engine URL:"), xalign=0), 0, 0, 1, 1)
        self._entry_url = Gtk.Entry()
        self._entry_url.set_placeholder_text("https://engine.example.com/ovirt-engine/api")
        self._entry_url.set_hexpand(True)
        grid.attach(self._entry_url, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label=_("Username:"), xalign=0), 0, 1, 1, 1)
        self._entry_user = Gtk.Entry()
        self._entry_user.set_text(self.default_user)
        grid.attach(self._entry_user, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label=_("Password:"), xalign=0), 0, 2, 1, 1)
        self._entry_pass = Gtk.Entry()
        self._entry_pass.set_visibility(False)
        grid.attach(self._entry_pass, 1, 2, 1, 1)

        grid.attach(Gtk.Label(label=_("SSH user (engine/hypervisor jump):"), xalign=0), 0, 3, 1, 1)
        self._entry_ssh_user = Gtk.Entry()
        self._entry_ssh_user.set_text("root")
        self._entry_ssh_user.set_tooltip_text(
            _(
                "Used only as a fallback when a VM is not directly reachable: SSH user "
                "for jump-proxy attempts via the engine machine or the VM's hypervisor, "
                "and default SSH user for the VMs themselves (non-Windows guests)."
            )
        )
        grid.attach(self._entry_ssh_user, 1, 3, 1, 1)

        self._scan_box.pack_start(grid, False, False, 4)

        self._chk_verify_ssl = Gtk.CheckButton(label=_("Verify TLS certificate"))
        self._chk_verify_ssl.set_active(True)
        self._scan_box.pack_start(self._chk_verify_ssl, False, False, 0)

        # Protocoles
        lbl_proto = Gtk.Label()
        lbl_proto.set_markup(_("<b>Connection types to import:</b>"))
        lbl_proto.set_xalign(0)
        self._scan_box.pack_start(lbl_proto, False, False, 4)

        proto_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._chk_ssh = Gtk.CheckButton(
            label=_("SSH  (direct — oVirt VMs are usually directly routable)")
        )
        self._chk_ssh.set_active(True)
        self._chk_rdp = Gtk.CheckButton(
            label=_("RDP  (Windows guests only — port 3389 probed via the resolved route)")
        )
        self._chk_rdp.set_active(True)
        for chk in (self._chk_ssh, self._chk_rdp):
            proto_box.pack_start(chk, False, False, 0)
        self._scan_box.pack_start(proto_box, False, False, 0)

        lbl_hint = Gtk.Label(
            label=_(
                "Note: IP resolution requires the oVirt guest agent to be installed in each VM.\n"
                "Reachability is tested automatically (direct, then jump proxy via the engine, "
                "then via the VM's hypervisor) — if all three fail, you will be asked for a "
                "manual route per VM during the scan."
            )
        )
        lbl_hint.set_xalign(0)
        lbl_hint.set_line_wrap(True)
        self._scan_box.pack_start(lbl_hint, False, False, 2)

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

        self._btn_scan = Gtk.Button(label=_("🔍  Scan oVirt engine"))
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
    # Phase 1 → Scan
    # ──────────────────────────────────────────────────────────────────────────

    def _on_scan_clicked(self, widget):
        """Lance l'interrogation du moteur oVirt.

        Args:
            widget (Gtk.Widget): Bouton scanner.

        Returns:
            None: Démarre un thread de scan et met à jour l'UI.
        """
        engine_url = self._entry_url.get_text().strip()
        if not engine_url:
            self._log(_("Engine URL is required."))
            return
        username = self._entry_user.get_text().strip()
        password = self._entry_pass.get_text()
        verify_ssl = self._chk_verify_ssl.get_active()
        ssh_user = self._entry_ssh_user.get_text().strip() or "root"

        proto_filter = set()
        if self._chk_ssh.get_active():
            proto_filter.add("ssh")
        if self._chk_rdp.get_active():
            proto_filter.add("rdp")
        if not proto_filter:
            self._log(_("No connection type selected."))
            return

        self._btn_scan.set_sensitive(False)
        self._stack.set_visible_child_name("monitor")
        logger.debug("OvirtImportDialog._on_scan_clicked | scan started")

        def worker():
            try:
                results = _ovirt_fetch_hosts(
                    engine_url,
                    username,
                    password,
                    verify_ssl,
                    ssh_user,
                    self._log,
                    self._set_progress,
                    proto_filter,
                )
            except Exception as exc:
                logger.exception(f"OvirtImportDialog._on_scan_clicked | scan failed: {exc}")
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
            grp = hd.get("group", "OVIRT")
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
            f"OvirtImportDialog._show_preview | total={total} new={n_new} existing={n_exists}"
        )
        self._btn_scan.set_label(_("🔄  Rescan"))
        self._btn_scan.set_sensitive(True)
        self._dlg.resize(820, 900)

    def _on_preview_toggled(self, renderer, path):
        """Inverse la sélection d'une ligne de prévisualisation."""
        self._preview_store[path][self._COL_SEL] = not self._preview_store[path][self._COL_SEL]

    def _select_all(self, value):
        """Coche ou décoche toutes les lignes de prévisualisation."""
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
            if row[self._COL_EXISTS] and not overwrite:
                continue
            to_import.append(self._discovered[row[self._COL_IDX]])
        if not to_import:
            self._log(_("No connection to import (all already exist or none selected)."))
            self._btn_import.set_sensitive(True)
            return
        self.on_done(to_import)
        logger.info(f"OvirtImportDialog._on_import_clicked | imported={len(to_import)}")
        self._btn_import.set_label(_("✓ Imported"))
        self._lbl_summary.set_markup(
            f"<b>{len(to_import)}</b> connexion(s) importée(s) avec succès."
        )


# ══════════════════════════════════════════════════════════════════════
# Point d'entrée BatchPlugin (autoload par BatchPluginRegistry)
# ══════════════════════════════════════════════════════════════════════


class OvirtImportBatchPlugin(BatchPlugin):
    """Adapte ``OvirtImportDialog`` au contrat ``BatchPlugin``.

    Reprend la logique de l'ancien ``Wmain.on_mnu_import_ovirt_activate``
    (jamais mergé sur cette base de code, porté depuis une révision
    antérieure du projet), adaptée pour ouvrir le dialogue comme onglet
    épinglé (``Wmain.open_management_tab``, singleton par ``key``) au lieu
    d'une fenêtre modale autonome — même convention que les autres imports
    d'hyperviseur. Contrairement à eux, pas de ``default_user`` tiré de la
    config : le champ « utilisateur API » garde son défaut
    ``"admin@internal"`` propre à oVirt (le champ « utilisateur SSH » pour
    les rebonds a lui-même son propre défaut ``"root"``, géré dans le
    dialogue), exactement comme dans l'ancien code.
    """

    tool_id = "import-ovirt"
    display_name = _("Import from oVirt")
    icon_name = "network-server-symbolic"
    menu_section = "import"

    def activate(self) -> None:
        """Déclenche l'import de VMs depuis un moteur oVirt/RHV.

        Lance un dialogue d'import dans un nouvel onglet de management,
        permettant à l'utilisateur de configurer l'accès à l'API REST,
        de scanner les VMs, et de les importer.

        Raises:
            RuntimeError: Si l'application n'est pas liée au plugin.
        """
        if self.app is None:
            raise RuntimeError(
                "OvirtImportBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        app = self.app

        def on_done(host_dicts):
            if not host_dicts:
                msgbox(_("No host imported."), parent=app.window)
                return
            n = app._import_done(host_dicts, default_group="OVIRT")
            if n:
                msgbox(_("{n} connection(s) imported from oVirt.").format(n=n), parent=app.window)
            else:
                msgbox(_("No new host (duplicates ignored)."), parent=app.window)

        app.open_management_tab(
            "ovirt-import",
            _("Import from oVirt"),
            lambda: OvirtImportDialog(app.window, on_done, show=False),
        )


def get_batch_plugin() -> OvirtImportBatchPlugin:
    """Retourne l'instance unique du plugin oVirt (découverte autoload).

    Returns:
        OvirtImportBatchPlugin: Instance du plugin d'import oVirt.
    """
    return OvirtImportBatchPlugin()
