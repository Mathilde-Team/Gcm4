# SPDX-License-Identifier: GPL-3.0-or-later
"""Cœur de l'outil « Import from Proxmox » (contrat v1, #439, portage #179).

Logique de scan extraite de ``plugins/plugin_import_proxmox.py`` (GTK3,
supprimé) : collecte des VMs Proxmox via SSH + ``qm``, résolution IP
(agent QGA → ipconfig cloud-init → ARP par MAC → nmap) et génération
des entrées SSH/SPICE/RDP/VNC. Zéro GTK ici.

Le socle bas niveau vit dans le paquet partagé ``gcm4_hv``
(``plugins/import_hyperviseurs/``), chargé avec une identité stable —
cf. :func:`partage` (même chargeur que ``import_libvirt``).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Callable
from urllib.parse import urlparse

from loguru import logger

#: Journal et progression du scan (texte brut, comme en GTK3).
Log = Callable[[str], None]
Progression = Callable[[float, str], None]


def partage() -> object:
    """Charge ``plugins/import_hyperviseurs`` en paquet ``gcm4_hv`` (identité stable)."""
    logger.trace("partage: entrée")
    if "gcm4_hv" not in sys.modules:
        from pathlib import Path

        dossier = Path(__file__).resolve().parent.parent / "import_hyperviseurs"
        spec = importlib.util.spec_from_file_location(
            "gcm4_hv", dossier / "__init__.py", submodule_search_locations=[str(dossier)]
        )
        if spec is None or spec.loader is None:  # pragma: no cover — chemin interne
            raise ImportError(str(dossier))
        module = importlib.util.module_from_spec(spec)
        sys.modules["gcm4_hv"] = module
        spec.loader.exec_module(module)
    logger.trace("partage: fait")
    return sys.modules["gcm4_hv"]


def partager_ui() -> object:
    """Importe ``gcm4_hv.ui`` (interface GTK4 partagée des imports)."""
    logger.trace("partager_ui")
    partage()
    module = importlib.import_module("gcm4_hv.ui")
    logger.trace("partager_ui: sortie")
    return module


partage()
hv_common = importlib.import_module("gcm4_hv.core")


def normaliser_cible(saisie: str) -> str:
    """Convertit une saisie libre en URI ``qemu+ssh://.../system``.

    Args:
        saisie: Cible saisie (IP, user@host, user@host:port, URI complète).

    Returns:
        URI normalisée, ou chaîne vide si l'entrée est invalide.
    """
    logger.debug("normaliser_cible: {!r}", saisie)
    brut = (saisie or "").strip()
    if not brut:
        logger.debug("normaliser_cible: vide")
        return ""
    if "://" in brut:
        logger.debug("normaliser_cible: URI complète")
        return brut

    if "@" in brut:
        utilisateur, hote_port = brut.split("@", 1)
        utilisateur = utilisateur or "root"
    else:
        utilisateur, hote_port = "root", brut

    hote = hote_port
    port = 22
    if ":" in hote_port:
        h, p = hote_port.rsplit(":", 1)
        if p.isdigit():
            hote, port = h, int(p)

    hote = hote.strip()
    if not hote:
        logger.debug("normaliser_cible: hôte absent")
        return ""
    logger.debug("normaliser_cible: sortie")
    return (
        f"qemu+ssh://{utilisateur}@{hote}:{port}/system"
        if port != 22
        else f"qemu+ssh://{utilisateur}@{hote}/system"
    )


def extraire_premiere_ipv4(sortie_qga: str) -> str:
    """Extrait la première IPv4 non-loopback de la sortie QGA.

    Args:
        sortie_qga: Sortie brute (JSON attendu) de
            ``qm guest cmd <vmid> network-get-interfaces``.

    Returns:
        La première IPv4 non-loopback trouvée, ou "" si aucune.
    """
    logger.debug("extraire_premiere_ipv4 | len(sortie)={}", len(sortie_qga or ""))
    try:
        data = json.loads(sortie_qga)
    except Exception as exc:  # noqa: BLE001
        logger.debug("extraire_premiere_ipv4 | json.loads a échoué, repli regex : {}", exc)
        data = None

    if data is not None:
        # QGA renvoie en général une liste d'interfaces, mais certaines
        # versions l'enveloppent dans un dict {"result": [...]} — les deux
        # formes sont acceptées.
        if isinstance(data, list):
            interfaces = data
        elif isinstance(data, dict):
            interfaces = data.get("result", [])
        else:
            interfaces = []
            logger.debug("extraire_premiere_ipv4 | type JSON inattendu : {}", type(data).__name__)
        for interface in interfaces:
            if not isinstance(interface, dict):
                continue
            for adresse in interface.get("ip-addresses", []):
                ip = adresse.get("ip-address", "")
                if re.match(r"\d+\.\d+\.\d+\.\d+$", ip) and not ip.startswith("127."):
                    logger.debug("extraire_premiere_ipv4 | trouvé via JSON : {}", ip)
                    return ip
        logger.debug("extraire_premiere_ipv4 | JSON parsé mais aucune IPv4 non-loopback")

    # Repli regex (JSON invalide ou aucune IPv4 valable) : jamais le
    # loopback (l'interface « lo » est listée en premier par QGA).
    for m in re.finditer(r'"ip-address"\s*:\s*"(\d+\.\d+\.\d+\.\d+)"', sortie_qga or ""):
        ip = m.group(1)
        if not ip.startswith("127."):
            logger.debug("extraire_premiere_ipv4 | trouvé via regex de repli : {}", ip)
            return ip
    logger.debug("extraire_premiere_ipv4 | aucune IPv4 non-loopback trouvée (JSON et regex)")
    return ""


def collecter_hotes(
    uris: list[str],
    utilisateur_ssh: str,
    log_fn: Log,
    progression_fn: Progression,
    filtre_protocoles: set[str] | None = None,
) -> list[dict]:
    """Collecte les VMs Proxmox via SSH sur les hyperviseurs donnés.

    Les cibles d'entrée utilisent le même format que l'import libvirt
    (URI type ``qemu+ssh://user@host[:port]/system``), mais l'inventaire
    est réalisé nativement avec les commandes Proxmox (``qm``).

    Args:
        uris: Cibles hyperviseurs (URI SSH).
        utilisateur_ssh: Utilisateur SSH des VMs invitées.
        log_fn: Fonction de log.
        progression_fn: Progression (frac: float, texte: str).
        filtre_protocoles: ``{'ssh','spice','rdp','vnc'}`` ou None = tous.

    Returns:
        Hôtes prêts à importer dans gcm4 (dictionnaires).
    """
    logger.debug("collecter_hotes: entrée | {} cible(s)", len(uris))
    if not hv_common.PARAMIKO_OK:
        log_fn(
            "ERREUR : paramiko non installé → "
            + hv_common.dep_install_hint("python3-paramiko", "python3-paramiko", "python-paramiko")
        )
        logger.debug("collecter_hotes: sortie (paramiko absent)")
        return []
    if filtre_protocoles is None:
        filtre_protocoles = {"ssh", "spice", "rdp"}

    resultats: list[dict] = []
    total = len(uris)

    def run(client: object, cmd: str, timeout: int = 30) -> str:
        return hv_common.libvirt_ssh_run(client, cmd, timeout)

    for idx, uri in enumerate(uris):
        log_fn(f"\n── Hyperviseur Proxmox : {uri}")
        progression_fn(idx / total, f"Connexion à {uri}…")
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
                vm_user = "Administrator" if is_windows else utilisateur_ssh
                ip_addr = ""

                if state == "running":
                    qga_out = run(
                        client,
                        f"qm guest cmd {vmid} network-get-interfaces 2>/dev/null",
                        timeout=20,
                    )
                    ip_addr = extraire_premiere_ipv4(qga_out)

                # Repli 1 : config ipconfig0 (cloud-init / statique Proxmox)
                if not ip_addr:
                    cfg_out = run(client, f"qm config {vmid} | grep '^ipconfig'", timeout=10)
                    m_cfg = re.search(r"ip=(\d+\.\d+\.\d+\.\d+)", cfg_out)
                    if m_cfg and not m_cfg.group(1).startswith("169."):
                        ip_addr = m_cfg.group(1)

                # Repli 2 : ARP filtré par MAC de la VM
                if not ip_addr:
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

                # Repli 3 : nmap ping scan (même pipeline que libvirt)
                if not ip_addr and state == "running":
                    nmap_table = hv_common.libvirt_nmap_scan(client, run, log_fn)
                    if nmap_table:
                        mac_out2 = run(
                            client,
                            f"qm config {vmid} | grep -oP '[0-9A-Fa-f]{{2}}(:[0-9A-Fa-f]{{2}}){{5}}' | head -1",
                            timeout=10,
                        )
                        vm_mac2 = mac_out2.strip().lower() if mac_out2.strip() else ""
                        if vm_mac2 and vm_mac2 in nmap_table:
                            ip_addr = nmap_table[vm_mac2]
                        else:
                            log_fn(f"  ↳ nmap : MAC VM introuvable, IP non résolue pour {vm_name}")

                if ip_addr:
                    log_fn(f"  ↳ IP {vm_name} : {ip_addr}")
                else:
                    log_fn(f"  ↳ IP {vm_name} : inconnue (QGA/ARP/nmap ont échoué)")

                added = False

                if "ssh" in filtre_protocoles:
                    if ip_addr:
                        jump_flag = (
                            f"-J {hv_user}@{hv_host}:{hv_port}"
                            if hv_port != 22
                            else f"-J {hv_user}@{hv_host}"
                        )
                        resultats.append(
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
                        resultats.append(
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

                if "spice" in filtre_protocoles:
                    # Proxmox SPICE : détecter vga qxl depuis qm config
                    vga_out = run(client, f"qm config {vmid} | grep '^vga:'", timeout=10)
                    has_qxl = "qxl" in vga_out.lower()
                    if has_qxl:
                        node_name = run(client, "hostname").strip()
                        resultats.append(
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

                if "rdp" in filtre_protocoles and ip_addr:
                    log_fn(f"  ↳ Sondage RDP 3389 sur {ip_addr}…")
                    if hv_common.check_port_open(client, run, ip_addr, 3389):
                        resultats.append(
                            {
                                "name": short,
                                "host": ip_addr,
                                "user": vm_user,
                                "port": 3389,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} (VMID {vmid}) — "
                                    f"RDP (port 3389 confirmé ouvert)"
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

                if "vnc" in filtre_protocoles and ip_addr:
                    log_fn(f"  ↳ Sondage VNC 5900 sur {ip_addr}…")
                    if hv_common.check_port_open(client, run, ip_addr, 5900):
                        resultats.append(
                            {
                                "name": short,
                                "host": ip_addr,
                                "user": vm_user,
                                "port": 5900,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} (VMID {vmid}) — "
                                    f"VNC (port 5900 confirmé ouvert)"
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

        progression_fn((idx + 1) / total, f"{idx + 1}/{total} hyperviseurs traités")
    logger.debug("collecter_hotes: sortie | {} connexion(s)", len(resultats))
    return resultats
