# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers partagés par les imports hyperviseurs (libvirt / Proxmox).

Ce module N'EST PAS un plugin — volontairement pas préfixé ``plugin_`` pour
ne jamais être scanné par ``PluginRegistry.autoload()`` ni
``BatchPluginRegistry.autoload()`` (cf. ``plugin_base.py``) : ni l'un ni
l'autre n'y trouverait de point d'entrée, ce qui déclencherait un warning
« non chargé » à chaque démarrage pour un module qui n'a jamais eu vocation
à être découvert automatiquement.

Contient tout ce qui est commun aux deux hyperviseurs : connexion SSH par
clé, lecture des URIs libvirt dans dconf, exécution de commande distante,
scan réseau (nmap) pour retrouver l'IP d'une VM à partir de sa MAC, et sonde
de port TCP. Utilisé par ``plugin_import_libvirt.py`` et
``plugin_import_proxmox.py``.
"""

from __future__ import annotations

import os
import re
import subprocess

from loguru import logger

try:
    import paramiko

    PARAMIKO_OK = True
except ImportError as exc:
    logger.debug("<module>: {} : {}", type(exc).__name__, exc)
    PARAMIKO_OK = False


def dep_install_hint(pkg_debian, pkg_fedora, pkg_arch=None):
    """Retourne la commande d'installation selon l'OS détecté (/etc/os-release).

    Copie volontairement locale et minimale de l'utilitaire du même nom
    dans ``gnome_connection_manager.py`` (même logique qu'un plugin
    autonome ne partageant pas de dépendance croisée avec le cœur —
    cf. la note similaire dans ``plugin_ssh.py``).
    """
    logger.debug("dep_install_hint: entrée")
    try:
        with open("/etc/os-release") as _f:
            _rel = _f.read().lower()
    except OSError as exc:
        logger.debug("dep_install_hint: {} : {}", type(exc).__name__, exc)
        _rel = ""
    if any(
        x in _rel for x in ("ubuntu", "debian", "mint", "pop", "kali", "raspbian", "linuxmint")
    ):
        logger.debug("dep_install_hint: sortie")
        return f"sudo apt install {pkg_debian}"
    if any(x in _rel for x in ("fedora", "rhel", "centos", "rocky", "alma", "oracle")):
        logger.debug("dep_install_hint: sortie")
        return f"sudo dnf install {pkg_fedora}"
    if any(x in _rel for x in ("arch", "manjaro", "endeavouros", "garuda")):
        logger.debug("dep_install_hint: sortie")
        return f"sudo pacman -S {pkg_arch or pkg_fedora}"
    if any(x in _rel for x in ("opensuse", "suse")):
        logger.debug("dep_install_hint: sortie")
        return f"sudo zypper install {pkg_debian}"
    logger.debug("dep_install_hint: sortie")
    return f"Debian/Ubuntu : sudo apt install {pkg_debian}\n  Fedora/RHEL   : sudo dnf install {pkg_fedora}"


def vm_name_split(vm_name):
    """Segmente le nom d'une VM en (groupe, nom_court).

    Le premier token (séparateur _, - ou espace) devient le groupe en
    MAJUSCULES ; le reste est le nom court affiché. Sans séparateur, le
    groupe vaut "LIBVIRT".

    Args:
        vm_name (str): Nom brut de la VM libvirt.

    Returns:
        tuple[str, str]: (groupe, nom_court).
    """
    logger.debug("vm_name_split: entrée")
    m = re.match(r"^([^_\-\s]+)[_\-\s](.*)", vm_name)
    if m:
        logger.debug("vm_name_split: sortie")
        return m.group(1).upper(), m.group(2)
    logger.debug("vm_name_split: sortie")
    return "LIBVIRT", vm_name


def collect_ssh_keys():
    """Liste les clés privées SSH de ~/.ssh, Ed25519 en premier puis RSA.

    Returns:
        list[str]: Chemins absolus vers les fichiers de clé privée.
    """
    logger.debug("collect_ssh_keys: entrée")
    ssh_dir = os.path.expanduser("~/.ssh")
    if not os.path.isdir(ssh_dir):
        logger.debug("collect_ssh_keys: sortie")
        return []
    order = {"ed25519": 0, "rsa": 1}
    keys = []
    for fname in os.listdir(ssh_dir):
        if fname.endswith(".pub") or fname in (
            "known_hosts",
            "known_hosts.old",
            "config",
            "authorized_keys",
        ):
            continue
        full = os.path.join(ssh_dir, fname)
        if not os.path.isfile(full):
            continue
        try:
            head = open(full, "rb").read(80).decode(errors="replace")
            if "PRIVATE KEY" not in head:
                continue
        except OSError as exc:
            logger.debug("collect_ssh_keys: {} : {}", type(exc).__name__, exc)
            continue
        kt = "other"
        if "ed25519" in fname.lower() or "ED25519" in head:
            kt = "ed25519"
        elif "rsa" in fname.lower() or "RSA" in head:
            kt = "rsa"
        keys.append((order.get(kt, 2), full))
    keys.sort(key=lambda x: x[0])
    logger.debug("collect_ssh_keys: sortie")
    return [k for _, k in keys]


def paramiko_connect(hostname, port, username, log_fn=None):
    """Etablit une connexion SSH par clé (Ed25519 > RSA > agent).

    Args:
        hostname (str): Adresse de l'hôte SSH.
        port (int): Port SSH.
        username (str): Utilisateur SSH.
        log_fn (callable, optional): Fonction de log.

    Returns:
        paramiko.SSHClient or None: Client connecté ou None si echec.
    """
    logger.debug("paramiko_connect: entrée")
    if not PARAMIKO_OK:
        if log_fn:
            log_fn(
                "ERREUR : paramiko non installé → "
                + dep_install_hint("python3-paramiko", "python3-paramiko", "python-paramiko")
            )
        logger.debug("paramiko_connect: -> None")
        return None
    keys = collect_ssh_keys()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key_classes = [
        getattr(paramiko, "Ed25519Key", None),
        getattr(paramiko, "RSAKey", None),
        getattr(paramiko, "ECDSAKey", None),
        getattr(paramiko, "DSSKey", None),
    ]
    key_classes = [c for c in key_classes if c is not None]

    for key_path in keys:
        pkey = None
        for cls in key_classes:
            try:
                pkey = cls.from_private_key_file(key_path)
                break
            except Exception as exc:
                logger.debug("paramiko_connect: {} : {}", type(exc).__name__, exc)
                continue
        if pkey is None:
            continue
        try:
            client.connect(
                hostname=hostname,
                port=port,
                username=username,
                pkey=pkey,
                timeout=10,
                look_for_keys=False,
                allow_agent=False,
            )
            if log_fn:
                log_fn(f"  SSH OK → {username}@{hostname}:{port} [{os.path.basename(key_path)}]")
            logger.debug("paramiko_connect: sortie")
            return client
        except paramiko.AuthenticationException as exc:
            logger.debug("paramiko_connect: {} : {}", type(exc).__name__, exc)
            continue
        except Exception as e:
            logger.debug("paramiko_connect: {} : {}", type(e).__name__, e)
            if log_fn:
                log_fn(f"  Erreur clé {os.path.basename(key_path)} : {e}")
    try:
        client.connect(
            hostname=hostname,
            port=port,
            username=username,
            timeout=10,
            look_for_keys=False,
            allow_agent=True,
        )
        if log_fn:
            log_fn(f"  SSH OK → {username}@{hostname}:{port} [agent]")
        logger.debug("paramiko_connect: sortie")
        return client
    except Exception as e:
        logger.debug("paramiko_connect: {} : {}", type(e).__name__, e)
        if log_fn:
            log_fn(f"  ERREUR SSH (toutes clés échouées) : {e}")
        logger.debug("paramiko_connect: -> None")
        return None


def libvirt_get_uris_from_dconf():
    """Lit les URIs libvirt configurées dans dconf (virt-manager).

    Returns:
        list[str]: Liste d'URIs libvirt.
    """
    logger.debug("libvirt_get_uris_from_dconf: entrée")
    try:
        r = subprocess.run(
            ["dconf", "read", "/org/virt-manager/virt-manager/connections/uris"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        raw = r.stdout.strip()
        if not raw or raw == "@as []":
            logger.debug("libvirt_get_uris_from_dconf: sortie")
            return []
        logger.debug("libvirt_get_uris_from_dconf: sortie")
        return re.findall(r"'([^']+)'", raw)
    except Exception as exc:
        logger.debug("libvirt_get_uris_from_dconf: {} : {}", type(exc).__name__, exc)
        logger.debug("libvirt_get_uris_from_dconf: sortie")
        return []


def libvirt_ssh_run(client, cmd, timeout=30):
    """Execute une commande sur un client SSH paramiko.

    Args:
        client (paramiko.SSHClient): Client SSH connecté.
        cmd (str): Commande shell à exécuter.
        timeout (int, optional): Timeout en secondes. Defaults to 30.

    Returns:
        str: Sortie standard de la commande.
    """
    logger.debug("libvirt_ssh_run: entrée")
    _, stdout, _ = client.exec_command(cmd, timeout=timeout)
    logger.debug("libvirt_ssh_run: sortie")
    return stdout.read().decode(errors="replace").strip()


def libvirt_nmap_scan(client, run_fn, log_fn):
    """Lance nmap -sn sur tous les sous-réseaux de l'hyperviseur.

    Returns:
        dict: mac_lower → ip
    """
    logger.debug("libvirt_nmap_scan: entrée")
    result = {}
    # Obtenir les sous-réseaux
    out = run_fn(client, "ip -o -f inet addr show")
    subnets, seen = [], set()
    for line in out.splitlines():
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if not m:
            continue
        ip_str, prefix = m.group(1), int(m.group(2))
        if ip_str.startswith("127.") or ip_str.startswith("169.254."):
            continue
        parts = list(map(int, ip_str.split(".")))
        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        net_i = 0
        for p in parts:
            net_i = (net_i << 8) | p
        net_i &= mask
        np_ = [(net_i >> (8 * i)) & 0xFF for i in reversed(range(4))]
        cidr = f"{'.'.join(map(str, np_))}/{prefix}"
        if cidr not in seen:
            seen.add(cidr)
            subnets.append(cidr)
    if not subnets:
        logger.debug("libvirt_nmap_scan: sortie")
        return result
    # Vérifier que nmap est dispo
    nmap_bin = run_fn(client, "which nmap 2>/dev/null")
    if not nmap_bin:
        log_fn("  nmap non disponible sur l'hyperviseur — scan réseau ignoré")
        logger.debug("libvirt_nmap_scan: sortie")
        return result
    nmap_ver = run_fn(client, "nmap --version 2>/dev/null | head -1")
    ver_m = re.search(r"Nmap version (\d+)", nmap_ver)
    flag = "-sn" if ver_m and int(ver_m.group(1)) >= 6 else "-sP"
    for cidr in subnets:
        log_fn(f"  nmap {flag} {cidr}…")
        xml_out = run_fn(
            client,
            f"nmap {flag} {cidr} -oX - --host-timeout 5s 2>/dev/null",
            timeout=300,
        )
        if not xml_out:
            continue
        count = 0
        for host_block in re.findall(r"<host\b.*?</host>", xml_out, re.DOTALL):
            ip_m = re.search(r'<address addr="([^"]+)" addrtype="ipv4"', host_block)
            mac_m = re.search(r'<address addr="([^"]+)" addrtype="mac"', host_block)
            if ip_m and mac_m:
                result[mac_m.group(1).lower()] = ip_m.group(1)
                count += 1
        log_fn(f"    → {count} hôte(s) avec MAC sur {cidr}")
    logger.debug("libvirt_nmap_scan: sortie")
    return result


def check_port_open(client, run_fn, ip, port, timeout=5):
    """Vérifie si un port TCP est accessible depuis l'hyperviseur (nc ou nmap).

    Essaie netcat d'abord (plus rapide), puis nmap en fallback.

    Args:
        client: Client SSH paramiko connecté.
        run_fn (callable): run(client, cmd, timeout) → str.
        ip (str): IP cible.
        port (int): Port TCP à sonder.
        timeout (int): Timeout de sonde en secondes.

    Returns:
        bool: True si le port est ouvert.
    """
    logger.debug("check_port_open: entrée")
    if not ip:
        logger.debug("check_port_open: -> False")
        return False
    out = run_fn(
        client,
        f"nc -z -w {timeout} {ip} {port} 2>/dev/null && echo OPEN || echo CLOSED",
        timeout=timeout + 3,
    )
    if "OPEN" in out:
        logger.debug("check_port_open: -> True")
        return True
    if "CLOSED" in out:
        logger.debug("check_port_open: -> False")
        return False
    # Fallback nmap
    out = run_fn(
        client,
        f"nmap -p {port} -sT --host-timeout {timeout}s {ip} -oG - 2>/dev/null",
        timeout=timeout + 5,
    )
    logger.debug("check_port_open: sortie")
    return f"{port}/open" in out.lower()
