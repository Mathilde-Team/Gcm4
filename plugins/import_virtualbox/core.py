# SPDX-License-Identifier: GPL-3.0-or-later
"""Cœur de l'outil « Import from VirtualBox » (contrat v1, #439, portage #179).

Logique de scan extraite de ``plugins/plugin_import_virtualbox.py`` (GTK3,
supprimé) : collecte des VMs VirtualBox via SSH + ``VBoxManage``,
résolution IP (propriété invité → ARP par MAC → nmap) et génération des
entrées SSH/RDP (VRDE natif en priorité)/VNC. Zéro GTK ici.

Le socle bas niveau vit dans le paquet partagé ``gcm4_hv``
(``plugins/import_hyperviseurs/``), chargé avec une identité stable —
cf. :func:`partage` (même chargeur que ``import_libvirt``).
"""

from __future__ import annotations

import importlib.util
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


def normaliser_cible(saisie: str, utilisateur_defaut: str = "root") -> str:
    """Convertit une saisie libre en URI ``vbox+ssh://user@host[:port]/``.

    Args:
        saisie: Cible saisie (IP, user@host, host:port, URI complète).
        utilisateur_defaut: Utilisateur si la saisie n'en précise pas.

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
        utilisateur = utilisateur or utilisateur_defaut
    else:
        utilisateur, hote_port = utilisateur_defaut, brut

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
        return f"vbox+ssh://{utilisateur}@{hote}/"
    logger.debug("normaliser_cible: sortie")
    return f"vbox+ssh://{utilisateur}@{hote}:{port}/"


def parser_machinereadable(texte: str) -> dict[str, str]:
    """Parse la sortie ``VBoxManage showvminfo --machinereadable``.

    Args:
        texte: Sortie brute (une paire ``clef="valeur"`` par ligne).

    Returns:
        Paires clef/valeur, guillemets retirés.
    """
    logger.debug("parser_machinereadable: entrée")
    info: dict[str, str] = {}
    for line in texte.splitlines():
        if "=" not in line:
            continue
        cle, _, valeur = line.partition("=")
        info[cle.strip()] = valeur.strip().strip('"')
    logger.debug("parser_machinereadable: sortie")
    return info


def collecter_hotes(
    uris: list[str],
    utilisateur_ssh: str,
    log_fn: Log,
    progression_fn: Progression,
    filtre_protocoles: set[str] | None = None,
) -> list[dict]:
    """Collecte les VMs VirtualBox via SSH sur les hôtes donnés (``VBoxManage``).

    Contrairement à libvirt/Proxmox il n'existe pas de registre système
    équivalent à dconf pour découvrir les hôtes VirtualBox : la liste des
    cibles est entièrement saisie manuellement par l'utilisateur.

    Résolution IP : propriété invité ``/VirtualBox/GuestInfo/Net/0/V4/IP``
    (Guest Additions) → ARP noyau par MAC → nmap (même pipeline que
    libvirt/Proxmox).

    Args:
        uris: Cibles hôtes VirtualBox (``vbox+ssh://user@host[:port]/``).
        utilisateur_ssh: Utilisateur SSH pour se connecter aux VMs.
        log_fn: Fonction de log.
        progression_fn: Progression (frac: float, texte: str).
        filtre_protocoles: ``{'ssh','rdp','vnc'}`` ou None = tous.

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
        filtre_protocoles = {"ssh", "rdp"}
    resultats: list[dict] = []
    total = len(uris)

    def run(client: object, cmd: str, timeout: int = 30) -> str:
        return hv_common.libvirt_ssh_run(client, cmd, timeout)

    for idx, uri in enumerate(uris):
        log_fn(f"\n── Hôte VirtualBox : {uri}")
        progression_fn(idx / total, f"Connexion à {uri}…")
        parsed = urlparse(uri)
        hv_host = parsed.hostname or "localhost"
        hv_port = parsed.port or 22
        hv_user = parsed.username or utilisateur_ssh or "root"

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

            # ── ARP noyau (fallback résolution IP) ────────────────────────
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

            nmap_table: dict[str, str] = {}
            if any(vm_name in running for vm_name in vm_names):
                nmap_table = hv_common.libvirt_nmap_scan(client, run, log_fn)
            combined = {**arp, **nmap_table}

            for vm_name in vm_names:
                state = "running" if vm_name in running else "poweroff"
                info_out = run(client, f'{vbox_bin} showvminfo "{vm_name}" --machinereadable')
                info = parser_machinereadable(info_out)

                is_windows = "windows" in info.get("ostype", "").lower() or bool(
                    re.search(
                        r"win|w(?:2k|2019|2022|2016|2012|srv|dc|server)", vm_name, re.IGNORECASE
                    )
                )
                vm_user = "Administrator" if is_windows else utilisateur_ssh

                ip_addr = ""
                if state == "running":
                    prop_out = run(
                        client,
                        f'{vbox_bin} guestproperty get "{vm_name}" '
                        f'"/VirtualBox/GuestInfo/Net/0/V4/IP"',
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

                # ── SSH : toujours via l'hôte (jamais direct) ─────────────
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

                # ── RDP : natif via VRDE (prioritaire, aucune IP requise) ─
                if "rdp" in filtre_protocoles:
                    vrde_on = info.get("vrde", "").lower() == "on"
                    vrde_port = info.get("vrdeport", "")
                    if vrde_on and vrde_port and vrde_port != "0":
                        resultats.append(
                            {
                                "name": short,
                                "host": hv_host,
                                "user": vm_user,
                                "port": int(vrde_port),
                                "password": "",
                                "description": (
                                    f"[{state}] {vm_name} — RDP natif VirtualBox (VRDE) "
                                    f"sur {hv_host}:{vrde_port}"
                                ),
                                "group": f"{grp}/rdp" if grp else "rdp",
                                "hypervisor": hv_host,
                                "protocol": "rdp",
                                "extra_params": "",
                            }
                        )
                        log_fn(
                            f"  + [{grp}] {short:28s}  VRDE {hv_host}:{vrde_port:5s}  "
                            f"[{state}]  RDP (VRDE)"
                        )
                        added = True
                    elif ip_addr:
                        log_fn(
                            f"  ↳ VRDE désactivé pour {vm_name} — "
                            f"sondage RDP invité 3389 sur {ip_addr}…"
                        )
                        if hv_common.check_port_open(client, run, ip_addr, 3389):
                            resultats.append(
                                {
                                    "name": short,
                                    "host": ip_addr,
                                    "user": vm_user,
                                    "port": 3389,
                                    "password": "",
                                    "description": (
                                        f"[{state}] {vm_name} — "
                                        f"RDP invité (port 3389 confirmé ouvert)"
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

                # ── VNC : uniquement si un serveur tourne dans l'invité ────
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
        progression_fn((idx + 1) / total, f"{idx + 1}/{total} hôte(s) traité(s)")
    logger.debug("collecter_hotes: sortie | {} connexion(s)", len(resultats))
    return resultats
