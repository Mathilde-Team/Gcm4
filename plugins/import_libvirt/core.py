# SPDX-License-Identifier: GPL-3.0-or-later
"""Cœur de l'outil « Import from libvirt » (contrat v1, #439, portage #179).

Logique de scan extraite de ``plugins/plugin_import_libvirt.py`` (GTK3,
supprimé) : collecte des VMs libvirt via SSH + ``virsh``, résolution IP
(domifaddr → DHCP → ARP → nmap) et génération des entrées SSH/SPICE/RDP/VNC.
Zéro GTK ici : le module est utilisé par ``gtk4.py`` et testé sans fenêtre.

Le socle bas niveau (SSH par clé, nmap, sonde de port) vit dans le
paquet partagé ``gcm4_hv`` (``plugins/import_hyperviseurs/``), chargé
avec une identité stable — cf. :func:`partage`.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

#: Journal et progression du scan (texte brut, comme en GTK3).
Log = Callable[[str], None]
Progression = Callable[[float, str], None]


def partage() -> object:
    """Charge ``plugins/import_hyperviseurs`` en paquet ``gcm4_hv``.

    Identité stable dans ``sys.modules`` (même principe que les
    ``gcm4_plugin_<nom>`` de ``gcm4.core.registre``) : tous les plugins
    d'import d'hyperviseurs partagent la même instance du module.
    """
    logger.trace("partage: entrée")
    if "gcm4_hv" not in sys.modules:
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
        saisie: Cible saisie (IP, user@host, URI complète, etc.).

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
    port: int | None = None
    if ":" in hote_port:
        h, p = hote_port.rsplit(":", 1)
        if p.isdigit():
            hote, port = h, int(p)

    hote = hote.strip()
    if not hote:
        logger.debug("normaliser_cible: hôte absent")
        return ""
    if port is None or port == 22:
        logger.debug("normaliser_cible: sortie")
        return f"qemu+ssh://{utilisateur}@{hote}/system"
    logger.debug("normaliser_cible: sortie")
    return f"qemu+ssh://{utilisateur}@{hote}:{port}/system"


def collecter_hotes(
    uris: list[str],
    utilisateur_ssh: str,
    log_fn: Log,
    progression_fn: Progression,
    filtre_protocoles: set[str] | None = None,
) -> list[dict]:
    """Collecte les VMs libvirt via SSH sur les hyperviseurs donnés.

    Résolution IP : domifaddr (agent/arp) → DHCP leases → ARP noyau → nmap.

    Règles de génération des entrées :
    - SSH : jamais direct ; ProxyJump (-J) si IP connue, sinon -t ssh
      user@vm via le shell de l'hyperviseur. Le port de l'hyperviseur est
      toujours respecté.
    - SPICE : virt-viewer --connect qemu+ssh://user@host[:port]/system vm
      (tunnel SSH géré nativement par virt-viewer/remote-viewer).
    - RDP : seulement si port 3389 détecté ouvert depuis l'hyperviseur.
    - VNC : seulement si port 5900 détecté ouvert depuis l'hyperviseur.

    Args:
        uris: URIs libvirt (``qemu+ssh://...``).
        utilisateur_ssh: Utilisateur SSH pour se connecter aux VMs.
        log_fn: Fonction de log (msg: str).
        progression_fn: Progression (frac: float, texte: str).
        filtre_protocoles: ``{'ssh','spice','rdp','vnc'}`` ou None = tous.

    Returns:
        Hôtes prêts à importer dans gcm4 (dictionnaires).
    """
    logger.debug("collecter_hotes: entrée | {} URI(s)", len(uris))
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
        log_fn(f"\n── Hyperviseur : {uri}")
        progression_fn(idx / total, f"Connexion à {uri}…")
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

            # ── ARP noyau ────────────────────────────────────────────────
            arp: dict[str, str] = {}
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

            # ── DHCP leases virsh ─────────────────────────────────────────
            dhcp: dict[str, str] = {}
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

            # ── nmap ping scan ────────────────────────────────────────────
            nmap_table = hv_common.libvirt_nmap_scan(client, run, log_fn)
            combined = {**arp, **dhcp, **nmap_table}
            log_fn(f"  Table MAC→IP : {len(combined)} entrées")

            # ── énumération des VMs ───────────────────────────────────────
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
                vm_user = "Administrator" if is_windows else utilisateur_ssh
                added = False

                # ── SSH : toujours via l'hyperviseur (jamais direct) ─────
                if "ssh" in filtre_protocoles:
                    if ip_addr:
                        if hv_port != 22:
                            jump_flag = f"-J {hv_user}@{hv_host}:{hv_port}"
                        else:
                            jump_flag = f"-J {hv_user}@{hv_host}"
                        resultats.append(
                            {
                                "name": short,
                                "host": ip_addr,
                                "user": vm_user,
                                "port": 22,
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} — SSH ProxyJump via "
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

                # ── SPICE : virt-viewer --connect URI libvirt (tunnel) ────
                if "spice" in filtre_protocoles and spice_port:
                    if hv_port != 22:
                        lv_uri = f"qemu+ssh://{hv_user}@{hv_host}:{hv_port}/system"
                    else:
                        lv_uri = f"qemu+ssh://{hv_user}@{hv_host}/system"
                    resultats.append(
                        {
                            "name": short,
                            "host": hv_host,
                            "user": hv_user,
                            "port": int(spice_port),
                            "password": "",
                            "description": (
                                f"[{state}] {vm_name} — SPICE via {lv_uri} "
                                f"(tunnel SSH géré par virt-viewer)"
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

                # ── RDP : seulement si port 3389 ouvert (sondé depuis l'HV)
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

                # ── VNC : seulement si port 5900 ouvert (sondé depuis l'HV)
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
        progression_fn((idx + 1) / total, f"{idx + 1}/{total} hyperviseurs traités")
    logger.debug("collecter_hotes: sortie | {} connexion(s)", len(resultats))
    return resultats
