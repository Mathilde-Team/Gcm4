# SPDX-License-Identifier: GPL-3.0-or-later
"""Cœur de l'outil « Import from oVirt » (contrat v1, #439, portage #179).

Logique de scan extraite de ``plugins/plugin_import_ovirt.py`` (GTK3,
supprimé) : inventaire des VMs via l'API REST du moteur (``urllib`` de
la bibliothèque standard, aucune dépendance ajoutée), résolution de
route par VM (directe → jump moteur → jump hyperviseur → saisie
manuelle) et génération des entrées SSH/RDP. Zéro GTK ici : la question
de route manuelle est posée par ``gtk4.py`` via le paramètre
``demander_route``.

Le socle bas niveau (SSH, sonde de port) vit dans le paquet partagé
``gcm4_hv`` (``plugins/import_hyperviseurs/``), chargé avec une
identité stable — cf. :func:`partage` (même chargeur que
``import_libvirt``).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import re
import socket
import ssl
import sys
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from loguru import logger

#: Journal et progression du scan (texte brut, comme en GTK3).
Log = Callable[[str], None]
Progression = Callable[[float, str], None]
#: Question de route manuelle : nom de VM → saisie (ou None si ignorée).
DemanderRoute = Callable[[str], "str | None"]


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


def api_get(
    url_moteur: str,
    utilisateur: str,
    mot_de_passe: str,
    verifier_tls: bool,
    chemin: str,
    log_fn: Log | None = None,
    timeout: int = 20,
) -> dict | None:
    """Effectue un GET JSON authentifié contre l'API REST oVirt.

    Args:
        url_moteur: URL de base du moteur, sans slash final.
        utilisateur: Utilisateur oVirt (ex. ``admin@internal``).
        mot_de_passe: Mot de passe.
        verifier_tls: Si False, désactive la vérification du certificat TLS.
        chemin: Chemin relatif à ``url_moteur`` (ex. ``/vms``).
        log_fn: Fonction de log (facultative).
        timeout: Timeout de la requête en secondes.

    Returns:
        Corps JSON décodé, ou None en cas d'échec.
    """
    logger.debug("api_get: {}", chemin)
    url = url_moteur.rstrip("/") + chemin
    creds = base64.b64encode(f"{utilisateur}:{mot_de_passe}".encode()).decode()
    req = Request(
        url,
        headers={
            "Authorization": f"Basic {creds}",
            "Accept": "application/json",
        },
    )
    ctx = None
    if not verifier_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            logger.debug("api_get: sortie")
            return json.loads(resp.read().decode(errors="replace"))
    except HTTPError as e:
        logger.warning("import ovirt | erreur HTTP {} sur {} : {}", e.code, chemin, e.reason)
        if log_fn:
            log_fn(f"  ERREUR HTTP {e.code} sur {chemin} : {e.reason}")
    except URLError as e:
        logger.warning("import ovirt | connexion à {} impossible : {}", url_moteur, e.reason)
        if log_fn:
            log_fn(f"  ERREUR de connexion à {url_moteur} : {e.reason}")
    except Exception as e:  # noqa: BLE001
        logger.warning("import ovirt | erreur inattendue sur {} : {}", chemin, e)
        if log_fn:
            log_fn(f"  ERREUR inattendue sur {chemin} : {e}")
    logger.debug("api_get: échec")
    return None


def sonder_tcp_direct(hote: str, port: int, timeout: float = 4) -> bool:
    """Teste une connexion TCP directe depuis la machine locale.

    Args:
        hote: Hôte cible.
        port: Port TCP.
        timeout: Timeout en secondes.

    Returns:
        True si la connexion TCP directe aboutit.
    """
    logger.debug("sonder_tcp_direct: entrée | {}:{}", hote, port)
    if not hote:
        logger.debug("sonder_tcp_direct: pas d'hôte")
        return False
    try:
        with socket.create_connection((hote, port), timeout=timeout):
            logger.debug("sonder_tcp_direct: ouvert")
            return True
    except OSError as exc:
        logger.debug("import ovirt | {}:{} injoignable : {}", hote, port, exc)
        logger.debug("sonder_tcp_direct: sortie")
        return False
    logger.debug("sonder_tcp_direct: fin")


def adresse_hote(
    url_moteur: str,
    utilisateur: str,
    mot_de_passe: str,
    verifier_tls: bool,
    id_hote: str,
    log_fn: Log | None = None,
) -> str:
    """Récupère l'adresse (hostname/IP) d'un hôte oVirt à partir de son id.

    Args:
        url_moteur: URL de l'API oVirt.
        utilisateur: Utilisateur de l'API oVirt.
        mot_de_passe: Mot de passe de l'API.
        verifier_tls: Vérifier le certificat TLS.
        id_hote: Id de l'hôte (lien ``vm.host.id`` renvoyé par l'API).
        log_fn: Fonction de log (facultative).

    Returns:
        Adresse de l'hôte, ou chaîne vide si indisponible.
    """
    logger.debug("adresse_hote: entrée | {}", id_hote)
    if not id_hote:
        logger.debug("adresse_hote: pas d'id")
        return ""
    data = api_get(
        url_moteur, utilisateur, mot_de_passe, verifier_tls, f"/hosts/{id_hote}", log_fn
    )
    if not data:
        logger.debug("adresse_hote: sortie (aucune donnée)")
        return ""
    logger.debug("adresse_hote: sortie")
    return data.get("address", "") or data.get("name", "") or ""


def interpreter_route_manuelle(manuelle: str, ip_repli: str) -> dict | None:
    """Interprète la route saisie manuellement par l'utilisateur.

    Args:
        manuelle: Texte saisi (cf. ``gtk4.py``).
        ip_repli: IP cible à utiliser si la saisie ne fournit qu'un jump
            host sans cible explicite.

    Returns:
        ``{"mode": "direct"|"jump", "host": str, "extra_params": str}``, ou None.
    """
    logger.debug("interpreter_route_manuelle: entrée")
    manuelle = manuelle.strip()
    if manuelle.startswith("-J"):
        parts = manuelle.split()
        cible = (
            parts[-1]
            if len(parts) > 1 and re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[-1])
            else ip_repli
        )
        jump_flag = " ".join(parts[:-1]) if cible != parts[-1] else " ".join(parts)
        if not cible:
            logger.debug("interpreter_route_manuelle: sortie (sans cible)")
            return None
        logger.debug("interpreter_route_manuelle: sortie")
        return {"mode": "jump", "host": cible, "extra_params": jump_flag}
    hote = manuelle.split(":")[0].strip()
    if not hote:
        logger.debug("interpreter_route_manuelle: sortie (vide)")
        return None
    logger.debug("interpreter_route_manuelle: sortie")
    return {"mode": "direct", "host": hote, "extra_params": ""}


def resoudre_route(
    nom_vm: str,
    ip_addr: str,
    url_moteur: str,
    utilisateur_api: str,
    mot_de_passe_api: str,
    verifier_tls: bool,
    utilisateur_ssh: str,
    id_hote: str,
    log_fn: Log,
    demander_route: DemanderRoute | None = None,
) -> dict | None:
    """Détermine comment atteindre une VM oVirt en SSH/RDP.

    Essaie dans l'ordre : (1) connexion TCP directe depuis gcm4, (2) jump
    proxy SSH via la machine du moteur oVirt (extraite de ``url_moteur``),
    (3) jump proxy SSH via l'hyperviseur qui héberge la VM (résolu via
    l'API). Si les trois échouent, demande une route de secours à
    l'utilisateur (``demander_route`` ; sans réponse, la VM est ignorée).

    Args:
        nom_vm: Nom de la VM.
        ip_addr: IP invité rapportée par l'agent oVirt.
        url_moteur: URL de l'API oVirt.
        utilisateur_api: Utilisateur de l'API oVirt.
        mot_de_passe_api: Mot de passe de l'API.
        verifier_tls: Vérifier le certificat TLS du moteur.
        utilisateur_ssh: Utilisateur SSH pour les rebonds (moteur/hyperviseur).
        id_hote: Id de l'hôte oVirt hébergeant la VM (peut être vide).
        log_fn: Fonction de log.
        demander_route: Question de route manuelle (UI), ou None pour
            ignorer directement les VMs injoignables.

    Returns:
        ``{"mode": "direct"|"jump", "host": str, "extra_params": str}``,
        ou None si la VM doit être ignorée.
    """
    # 1) Connexion directe depuis la machine locale (gcm4)
    logger.debug("resoudre_route: entrée | {}", nom_vm)
    log_fn(f"  ↳ {nom_vm} : test de connexion directe vers {ip_addr}:22…")
    if sonder_tcp_direct(ip_addr, 22):
        log_fn(f"  ↳ {nom_vm} : accès direct OK")
        logger.debug("resoudre_route: sortie (direct)")
        return {"mode": "direct", "host": ip_addr, "extra_params": ""}
    log_fn(f"  ↳ {nom_vm} : accès direct impossible")

    # 2) Jump proxy via la machine du moteur oVirt
    hote_moteur = urlparse(url_moteur).hostname or ""
    if hote_moteur:
        log_fn(f"  ↳ {nom_vm} : test du jump proxy via le moteur oVirt ({hote_moteur})…")
        client = hv_common.paramiko_connect(hote_moteur, 22, utilisateur_ssh, log_fn)
        if client is not None:
            try:
                if hv_common.check_port_open(client, hv_common.libvirt_ssh_run, ip_addr, 22):
                    log_fn(f"  ↳ {nom_vm} : jump proxy via le moteur OK")
                    logger.debug("resoudre_route: sortie (jump moteur)")
                    return {
                        "mode": "jump",
                        "host": ip_addr,
                        "extra_params": f"-J {utilisateur_ssh}@{hote_moteur}",
                    }
                log_fn(
                    f"  ↳ {nom_vm} : moteur joignable en SSH mais VM injoignable depuis le moteur"
                )
            finally:
                client.close()
        else:
            log_fn(f"  ↳ {nom_vm} : SSH vers le moteur impossible")

    # 3) Jump proxy via l'hyperviseur hébergeant la VM
    adresse_hv = adresse_hote(
        url_moteur, utilisateur_api, mot_de_passe_api, verifier_tls, id_hote, log_fn
    )
    if adresse_hv:
        log_fn(f"  ↳ {nom_vm} : test du jump proxy via l'hyperviseur ({adresse_hv})…")
        client = hv_common.paramiko_connect(adresse_hv, 22, utilisateur_ssh, log_fn)
        if client is not None:
            try:
                if hv_common.check_port_open(client, hv_common.libvirt_ssh_run, ip_addr, 22):
                    log_fn(f"  ↳ {nom_vm} : jump proxy via l'hyperviseur OK")
                    logger.debug("resoudre_route: sortie (jump hyperviseur)")
                    return {
                        "mode": "jump",
                        "host": ip_addr,
                        "extra_params": f"-J {utilisateur_ssh}@{adresse_hv}",
                    }
                log_fn(
                    f"  ↳ {nom_vm} : hyperviseur joignable en SSH mais VM injoignable "
                    f"depuis l'hyperviseur"
                )
            finally:
                client.close()
        else:
            log_fn(f"  ↳ {nom_vm} : SSH vers l'hyperviseur ({adresse_hv}) impossible")
    else:
        log_fn(f"  ↳ {nom_vm} : hyperviseur de la VM inconnu (id manquant dans l'API)")

    # 4) Secours manuel : demander à l'utilisateur
    if demander_route is None:
        logger.debug("resoudre_route: sortie (aucune route, ignorée)")
        return None
    manuelle = demander_route(nom_vm)
    if not manuelle:
        logger.debug("resoudre_route: sortie (route manuelle vide)")
        return None
    route = interpreter_route_manuelle(manuelle, ip_addr)
    if route:
        log_fn(
            f"  ↳ {nom_vm} : route manuelle retenue → {route['mode']} "
            f"{route['host']} {route['extra_params']}"
        )
    logger.debug("resoudre_route: sortie")
    return route


def sonder_port_via_route(route: dict, port: int, utilisateur_ssh: str, log_fn: Log) -> bool:
    """Sonde un port TCP sur la cible d'une route déjà résolue (directe ou jump).

    Args:
        route: Route retournée par :func:`resoudre_route`.
        port: Port TCP à sonder.
        utilisateur_ssh: Utilisateur SSH pour se reconnecter au jump host.
        log_fn: Fonction de log.

    Returns:
        True si le port est confirmé ouvert.
    """
    logger.debug("sonder_port_via_route: entrée | {}", port)
    if route["mode"] == "direct":
        logger.debug("sonder_port_via_route: sortie (direct)")
        return sonder_tcp_direct(route["host"], port)
    m = re.search(r"-J\s+(?:[^@\s]+@)?([^\s:]+)(?::(\d+))?", route.get("extra_params", ""))
    if not m:
        logger.debug("sonder_port_via_route: sortie (pas de jump)")
        return False
    jump_host = m.group(1)
    jump_port = int(m.group(2)) if m.group(2) else 22
    client = hv_common.paramiko_connect(jump_host, jump_port, utilisateur_ssh, log_fn)
    if client is None:
        logger.debug("sonder_port_via_route: sortie (jump injoignable)")
        return False
    try:
        logger.debug("sonder_port_via_route: sortie")
        return hv_common.check_port_open(client, hv_common.libvirt_ssh_run, route["host"], port)
    finally:
        client.close()


def collecter_hotes(
    url_moteur: str,
    utilisateur: str,
    mot_de_passe: str,
    verifier_tls: bool,
    utilisateur_ssh: str,
    log_fn: Log,
    progression_fn: Progression,
    filtre_protocoles: set[str] | None = None,
    demander_route: DemanderRoute | None = None,
) -> list[dict]:
    """Collecte les VMs depuis un moteur oVirt/RHV via son API REST.

    Pour chaque VM dont l'IP invité est connue, la joignabilité est
    vérifiée activement avant de générer une entrée SSH/RDP (cf.
    :func:`resoudre_route`). Sans IP, la VM est ignorée (l'agent invité
    oVirt ne rapporte rien).

    Args:
        url_moteur: URL de l'API oVirt.
        utilisateur: Utilisateur oVirt (ex. ``admin@internal``).
        mot_de_passe: Mot de passe.
        verifier_tls: Vérifier le certificat TLS du moteur.
        utilisateur_ssh: Utilisateur SSH pour les rebonds et les VMs.
        log_fn: Fonction de log.
        progression_fn: Progression (frac: float, texte: str).
        filtre_protocoles: ``{'ssh','rdp'}`` ou None = tous.
        demander_route: Question de route manuelle (UI), ou None.

    Returns:
        Hôtes prêts à importer dans gcm4 (dictionnaires).
    """
    logger.debug("collecter_hotes: entrée | {}", url_moteur)
    if filtre_protocoles is None:
        filtre_protocoles = {"ssh", "rdp"}
    resultats: list[dict] = []

    progression_fn(0.05, f"Connexion à {url_moteur}…")
    data = api_get(url_moteur, utilisateur, mot_de_passe, verifier_tls, "/vms", log_fn)
    if data is None:
        log_fn("  ERREUR : impossible de récupérer la liste des VMs (vérifier URL/identifiants).")
        logger.debug("collecter_hotes: sortie (API injoignable)")
        return resultats

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
        vm_user = "Administrator" if is_windows else utilisateur_ssh

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
            route = resoudre_route(
                vm_name,
                ip_addr,
                url_moteur,
                utilisateur,
                mot_de_passe,
                verifier_tls,
                utilisateur_ssh,
                host_id,
                log_fn,
                demander_route,
            )
        else:
            log_fn(f"  ↳ {vm_name or vm.get('id', '?')} : IP inconnue (agent invité absent ?)")
            route = None

        if route is None:
            progression_fn((idx + 1) / total, f"{idx + 1}/{total} VM(s) traitée(s)")
            continue

        route_desc = (
            "connexion directe testée"
            if route["mode"] == "direct"
            else f"jump proxy ({route['extra_params']})"
        )

        if "ssh" in filtre_protocoles:
            resultats.append(
                {
                    "name": short,
                    "host": route["host"],
                    "user": vm_user,
                    "port": 22,
                    "password": "",
                    "description": f"[{state}] {vm_name} — SSH via oVirt ({route_desc})",
                    "group": f"{grp}/ssh" if grp else "ssh",
                    "hypervisor": url_moteur,
                    "protocol": "ssh",
                    "extra_params": route.get("extra_params", ""),
                }
            )
            log_fn(
                f"  + [{grp}] {short:28s}  {route['host']:16s}  [{state}]  SSH ({route['mode']})"
            )
            added = True

        if "rdp" in filtre_protocoles and is_windows:
            log_fn(f"  ↳ Sondage RDP 3389 sur {route['host']} ({route['mode']})…")
            if sonder_port_via_route(route, 3389, utilisateur_ssh, log_fn):
                resultats.append(
                    {
                        "name": short,
                        "host": route["host"],
                        "user": vm_user,
                        "port": 3389,
                        "password": "",
                        "description": (
                            f"[{state}] {vm_name} — RDP via oVirt "
                            f"({route_desc}, port confirmé ouvert)"
                        ),
                        "group": f"{grp}/rdp" if grp else "rdp",
                        "hypervisor": url_moteur,
                        "protocol": "rdp",
                        "extra_params": route.get("extra_params", ""),
                    }
                )
                log_fn(
                    f"  + [{grp}] {short:28s}  {route['host']:16s}  [{state}]  RDP "
                    f"({route['mode']}) ✓"
                )
                added = True
            else:
                log_fn(f"  ↳ port 3389 fermé/injoignable sur {route['host']} — RDP ignoré")

        if not added:
            log_fn(f"  - {vm_name} : aucune connexion générée")

        progression_fn((idx + 1) / total, f"{idx + 1}/{total} VM(s) traitée(s)")

    logger.debug("collecter_hotes: sortie | {} connexion(s)", len(resultats))
    return resultats
