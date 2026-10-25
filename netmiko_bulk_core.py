"""Cœur métier (sans GTK) du plugin de déploiement de configuration en masse
via Netmiko pour GCM.

Ce module ne dépend ni de GTK ni de netmiko au niveau du import top-level
(netmiko est importé paresseusement dans ``push_to_device``) afin de rester
testable facilement en dehors de l'environnement graphique.

Notions, à l'image de rancid/.cloginrc :

- **Profils** (``profiles.ini``) : couples user/password/port/secret/
  device_type, un profil par type de matériel. ``VENDOR_DEVICE_TYPES``
  fournit le catalogue des ``device_type`` netmiko groupés par
  constructeur, pour peupler un sélecteur dans l'éditeur de profil.
- **Inventaire** (CSV) : une ligne par équipement. Colonnes minimales
  ``ip`` et ``type``/``profile``. Toute colonne supplémentaire devient une
  variable ``#nom#`` utilisable dans le gabarit.
- **Gabarits** (fichiers texte) : configuration à pousser, variables
  ``#colonne#`` remplacées par les valeurs de la ligne CSV.
- **Journaux** : chaque exécution écrit un fichier de log par hôte sur
  disque (indépendamment de toute UI), plus un manifeste JSON agrégeant
  statuts et statistiques, consultables après coup.
"""

from __future__ import annotations

import configparser
import csv
import json
import re
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from loguru import logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
logger.disable(__name__)

__all__ = [
    "VENDOR_DEVICE_TYPES",
    "iter_all_device_types",
    "DeviceProfile",
    "InventoryRow",
    "PushResult",
    "ProfileStore",
    "load_inventory",
    "render_template",
    "find_missing_variables",
    "scan_output_for_errors",
    "push_to_device",
    "run_bulk_push",
    "summarize_results",
    "STATUS_OK",
    "STATUS_REJECTED",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
]

_VAR_RE = re.compile(r"#([A-Za-z0-9_]+)#")


# ──────────────────────────────────────────────────────────────────────────
# Catalogue constructeurs → device_type netmiko
# ──────────────────────────────────────────────────────────────────────────

# Groupé par constructeur pour peupler un sélecteur en cascade (constructeur
# puis device_type) dans l'éditeur de profil. Basé sur les drivers netmiko
# au moment de l'écriture ; une nouvelle version de netmiko peut en ajouter
# d'autres (cf. ``netmiko.ssh_dispatcher.CLASS_MAPPER.keys()`` pour la liste
# exhaustive à jour de l'installation locale).
VENDOR_DEVICE_TYPES: dict[str, list[tuple[str, str]]] = {
    "Cisco": [
        ("cisco_ios", "IOS / IOS-XE (switch, routeur)"),
        ("cisco_xe", "IOS-XE (explicite)"),
        ("cisco_xr", "IOS-XR"),
        ("cisco_nxos", "NX-OS (Nexus)"),
        ("cisco_asa", "ASA (pare-feu)"),
        ("cisco_ftd", "Firepower Threat Defense"),
        ("cisco_wlc", "Wireless LAN Controller"),
        ("cisco_s300", "Small Business SG/SF 300"),
        ("cisco_tp", "TelePresence"),
    ],
    "Huawei": [
        ("huawei", "VRP (générique)"),
        ("huawei_vrpv8", "VRP V8 (NE/CE/ARG)"),
        ("huawei_smartax", "SmartAX (accès/MSAN)"),
    ],
    "HPE / Aruba": [
        ("hp_comware", "Comware (H3C/HPE FlexNetwork)"),
        ("hp_procurve", "ProCurve / Aruba OS Switch (ancien)"),
        ("aruba_os", "ArubaOS (WLAN, contrôleurs)"),
        ("aruba_osswitch", "Aruba OS-Switch"),
    ],
    "Juniper": [
        ("juniper_junos", "Junos (recommandé)"),
        ("juniper", "Junos (générique, historique)"),
        ("juniper_screenos", "ScreenOS (NetScreen)"),
    ],
    "Arista": [
        ("arista_eos", "EOS"),
    ],
    "Fortinet": [
        ("fortinet", "FortiOS"),
    ],
    "Palo Alto": [
        ("paloalto_panos", "PAN-OS"),
    ],
    "Check Point": [
        ("checkpoint_gaia", "Gaia"),
    ],
    "Mikrotik": [
        ("mikrotik_routeros", "RouterOS"),
        ("mikrotik_switchos", "SwitchOS"),
    ],
    "Dell": [
        ("dell_force10", "FTOS (Force10 / S-series)"),
        ("dell_os10", "OS10"),
        ("dell_os9", "OS9 (PowerConnect N/Z)"),
        ("dell_os6", "OS6 (PowerConnect)"),
        ("dell_powerconnect", "PowerConnect (générique)"),
        ("dell_isilon", "Isilon OneFS"),
        ("dell_sonic", "SONiC"),
    ],
    "Extreme Networks": [
        ("extreme", "ExtremeWare (générique)"),
        ("extreme_exos", "EXOS"),
        ("extreme_ers", "Ethernet Routing Switch (ex-Avaya/Nortel)"),
        ("extreme_slx", "SLX (ex-Brocade)"),
        ("extreme_vdx", "VDX (ex-Brocade)"),
        ("extreme_wing", "WiNG (WLAN, ex-Motorola/Zebra)"),
        ("extreme_netiron", "NetIron (ex-Brocade/Foundry)"),
    ],
    "F5": [
        ("f5_ltm", "BIG-IP LTM (tmsh)"),
        ("f5_tmsh", "BIG-IP (tmsh générique)"),
        ("f5_linux", "BIG-IP (shell Linux sous-jacent)"),
    ],
    "Nokia / Alcatel": [
        ("nokia_sros", "SR OS"),
        ("alcatel_sros", "SR OS (marque Alcatel-Lucent)"),
        ("alcatel_aos", "AOS (switchs OmniSwitch)"),
    ],
    "Ubiquiti": [
        ("ubiquiti_edge", "EdgeSwitch / EdgeMax (ancien driver)"),
        ("ubiquiti_edgeswitch", "EdgeSwitch"),
        ("ubiquiti_unifiswitch", "UniFi Switch"),
    ],
    "Ruckus / Ruijie": [
        ("ruckus_fastiron", "FastIron (ICX, ex-Brocade/Foundry)"),
        ("ruijie_os", "Ruijie OS"),
    ],
    "Autres constructeurs réseau": [
        ("a10", "A10 Networks ACOS"),
        ("adtran_os", "Adtran AOS"),
        ("allied_telesis_awplus", "Allied Telesis AlliedWare Plus"),
        ("apresia_aeos", "Apresia AEOS"),
        ("broadcom_icos", "Broadcom ICOS"),
        ("calix_b6", "Calix B6"),
        ("casa_cbr", "Casa Systems CBR"),
        ("centec_os", "Centec OS"),
        ("ciena_saos", "Ciena SAOS"),
        ("cloudgenix_ion", "CloudGenix ION"),
        ("coriant", "Coriant"),
        ("digi_transport", "Digi TransPort"),
        ("eltex", "Eltex (générique)"),
        ("eltex_esr", "Eltex ESR"),
        ("endace", "Endace"),
        ("flexvnf", "Flexiwan/FlexVNF"),
        ("ipinfusion_ocnos", "IP Infusion OcNOS"),
        ("keymile", "Keymile (générique)"),
        ("keymile_nos", "Keymile NOS"),
        ("mrv_lx", "MRV LX"),
        ("mrv_optiswitch", "MRV OptiSwitch"),
        ("netgear_prosafe", "Netgear ProSafe"),
        ("netscaler", "Citrix NetScaler"),
        ("oneaccess_oneos", "OneAccess OneOS"),
        ("pluribus", "Pluribus Netvisor"),
        ("quanta_mesh", "Quanta Mesh"),
        ("rad_etx", "RAD ETX"),
        ("raisecom_ros", "Raisecom ROS"),
        ("sixwind_os", "6WIND 6WINDGate"),
        ("sophos_sfos", "Sophos SFOS (pare-feu)"),
        ("supermicro_smis", "Supermicro SMIS"),
        ("teldat_cit", "Teldat CIT"),
        ("tplink_jetstream", "TP-Link JetStream"),
        ("vyos", "VyOS"),
        ("vyatta_vyos", "Vyatta / VyOS (ancien driver)"),
        ("watchguard_fireware", "WatchGuard Fireware"),
        ("yamaha", "Yamaha (routeurs RTX)"),
        ("zte_zxros", "ZTE ZXROS"),
        ("zyxel_os", "Zyxel OS"),
    ],
    "Serveurs / générique": [
        ("linux", "Linux (shell générique via SSH)"),
        ("generic", "Générique (protocole SSH basique)"),
        ("generic_termserver", "Serveur de terminaux générique"),
        ("ovs_linux", "Open vSwitch sur Linux"),
        ("netapp_cdot", "NetApp cDOT"),
    ],
}


def iter_all_device_types() -> list[tuple[str, str, str]]:
    """Aplati ``VENDOR_DEVICE_TYPES`` en tuples ``(constructeur, device_type,
    libellé)``, pratique pour peupler une simple liste de recherche.
    """
    logger.debug("iter_all_device_types: entrée")
    out = []
    for vendor, entries in VENDOR_DEVICE_TYPES.items():
        for device_type, label in entries:
            out.append((vendor, device_type, label))
    logger.debug("iter_all_device_types: sortie")
    return out


# ──────────────────────────────────────────────────────────────────────────
# Profils (équivalent .cloginrc)
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class DeviceProfile:
    """Un profil de connexion, référencé par nom depuis la colonne ``type``
    (ou ``profile``) de l'inventaire CSV.
    """

    name: str
    device_type: str  # valeur netmiko, ex. "cisco_ios", "hp_comware"
    vendor: str = ""  # étiquette constructeur (cf. VENDOR_DEVICE_TYPES), informative
    username: str = ""
    password: str = ""
    secret: str = ""  # mot de passe "enable", si applicable
    port: int = 22
    timeout: int = 10
    save_command: str = ""  # ex. "save force" (comware) ; vide -> conn.save_config() natif

    def to_netmiko_kwargs(self, host: str) -> dict:
        """Construit le dictionnaire de connexion attendu par ``netmiko.ConnectHandler``.

        Args:
            host (str): Adresse IP/nom d'hôte de l'équipement cible (issue
                de la ligne d'inventaire, distincte du profil lui-même).

        Returns:
            dict: Paramètres ``device_type``/``host``/``username``/
            ``password``/``port``/``timeout``, plus ``secret`` si défini
            sur le profil.
        """
        logger.debug("DeviceProfile.to_netmiko_kwargs: entrée")
        kwargs = {
            "device_type": self.device_type,
            "host": host,
            "username": self.username,
            "password": self.password,
            "port": self.port,
            "timeout": self.timeout,
        }
        if self.secret:
            kwargs["secret"] = self.secret
        logger.debug("DeviceProfile.to_netmiko_kwargs: sortie")
        return kwargs


class ProfileStore:
    """Charge, modifie et sauvegarde les profils définis dans un ``.ini``.

    Format (une section par profil) ::

        [comware]
        vendor = HPE / Aruba
        device_type = hp_comware
        username = admin
        password = secret
        port = 22
    """

    def __init__(self, path: str | Path):
        """Initialise le magasin de profils sur un fichier ``.ini``.

        Args:
            path (str | Path): Chemin du fichier de profils. Chargé
                immédiatement via :meth:`reload` s'il existe déjà, sinon
                le magasin démarre vide (fichier créé au premier
                :meth:`save`).
        """
        logger.debug("ProfileStore.__init__: entrée")
        self.path = Path(path)
        self._profiles: dict[str, DeviceProfile] = {}
        if self.path.exists():
            self.reload()
        logger.debug("ProfileStore.__init__: fin")

    def reload(self) -> None:
        """Recharge tous les profils depuis ``self.path``, en mémoire.

        Écrase l'état en mémoire courant (``self._profiles``) ; les
        profils modifiés via :meth:`set` mais non sauvegardés
        (:meth:`save`) sont donc perdus.
        """
        logger.debug("ProfileStore.reload: entrée")
        cp = configparser.ConfigParser()
        cp.read(self.path, encoding="utf-8")
        profiles: dict[str, DeviceProfile] = {}
        for section in cp.sections():
            s = cp[section]
            profiles[section] = DeviceProfile(
                name=section,
                device_type=s.get("device_type", fallback=""),
                vendor=s.get("vendor", fallback=""),
                username=s.get("username", fallback=""),
                password=s.get("password", fallback=""),
                secret=s.get("secret", fallback=""),
                port=s.getint("port", fallback=22),
                timeout=s.getint("timeout", fallback=10),
                save_command=s.get("save_command", fallback=""),
            )
        self._profiles = profiles
        logger.debug("ProfileStore.reload: fin")

    def get(self, name: str) -> DeviceProfile | None:
        """Retourne le profil nommé *name*, ou ``None`` s'il n'existe pas.

        Args:
            name (str): Nom du profil recherché (clé exacte, sensible à la
                casse).

        Returns:
            DeviceProfile | None: Le profil correspondant, ou ``None``.
        """
        logger.debug("ProfileStore.get: entrée")
        logger.debug("ProfileStore.get: sortie")
        return self._profiles.get(name)

    def names(self) -> list[str]:
        """Retourne les noms de profils connus, triés alphabétiquement.

        Returns:
            list[str]: Noms de profils en ordre alphabétique.
        """
        logger.debug("ProfileStore.names: entrée")
        logger.debug("ProfileStore.names: sortie")
        return sorted(self._profiles.keys())

    def all(self) -> list[DeviceProfile]:
        """Retourne tous les profils, triés par nom.

        Returns:
            list[DeviceProfile]: Profils dans l'ordre de :meth:`names`.
        """
        logger.debug("ProfileStore.all: entrée")
        logger.debug("ProfileStore.all: sortie")
        return [self._profiles[n] for n in self.names()]

    def set(self, profile: DeviceProfile) -> None:
        """Ajoute ou remplace un profil (par ``profile.name``), en mémoire."""
        logger.debug("ProfileStore.set: entrée")
        self._profiles[profile.name] = profile
        logger.debug("ProfileStore.set: fin")

    def delete(self, name: str) -> None:
        """Supprime un profil par son nom, en mémoire.

        Args:
            name (str): Nom du profil à supprimer. Aucune erreur n'est
                levée si ce profil n'existe pas déjà (``pop(..., None)``).
        """
        logger.debug("ProfileStore.delete: entrée")
        self._profiles.pop(name, None)
        logger.debug("ProfileStore.delete: fin")

    def save(self) -> None:
        """Écrit l'ensemble des profils courants dans ``self.path``
        (permissions restreintes, comme le ``.cloginrc`` de rancid).
        """
        logger.debug("ProfileStore.save: entrée")
        cp = configparser.ConfigParser()
        for name in self.names():
            p = self._profiles[name]
            cp[name] = {
                "vendor": p.vendor,
                "device_type": p.device_type,
                "username": p.username,
                "password": p.password,
                "secret": p.secret,
                "port": str(p.port),
                "timeout": str(p.timeout),
                "save_command": p.save_command,
            }
        with open(self.path, "w", encoding="utf-8") as fh:
            cp.write(fh)
        try:
            self.path.chmod(0o600)
        except OSError as exc:
            logger.debug("ProfileStore.save: {} : {}", type(exc).__name__, exc)
            pass
        logger.debug("ProfileStore.save: fin")

    @staticmethod
    def write_template(path: str | Path) -> None:
        """Écrit un fichier ``profiles.ini`` d'exemple si *path* n'existe pas."""
        logger.debug("ProfileStore.write_template: entrée")
        path = Path(path)
        if path.exists():
            logger.debug("ProfileStore.write_template: sortie")
            return
        store = ProfileStore.__new__(ProfileStore)
        store.path = path
        store._profiles = {
            "comware": DeviceProfile(
                name="comware",
                vendor="HPE / Aruba",
                device_type="hp_comware",
                username="admin",
                password="change_me",
                port=22,
            ),
            "cisco": DeviceProfile(
                name="cisco",
                vendor="Cisco",
                device_type="cisco_ios",
                username="admin",
                password="change_me",
                secret="change_me_enable",
                port=22,
            ),
        }
        store.save()
        logger.debug("ProfileStore.write_template: fin")


# ──────────────────────────────────────────────────────────────────────────
# Inventaire CSV
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class InventoryRow:
    """Une ligne d'inventaire = un équipement + ses variables de gabarit."""

    ip: str
    profile_name: str
    variables: dict[str, str] = field(default_factory=dict)
    line_no: int = 0

    def render_context(self) -> dict[str, str]:
        """Construit le dictionnaire de variables utilisable par :func:`render_template`.

        Returns:
            dict[str, str]: Copie de ``self.variables`` complétée par
            ``ip``/``host`` (défauts à ``self.ip`` s'ils ne sont pas déjà
            fournis par une colonne CSV du même nom).
        """
        logger.debug("InventoryRow.render_context: entrée")
        ctx = dict(self.variables)
        ctx.setdefault("ip", self.ip)
        ctx.setdefault("host", self.ip)
        logger.debug("InventoryRow.render_context: sortie")
        return ctx


def _sniff_delimiter(sample: str) -> str:
    logger.debug("_sniff_delimiter: entrée")
    for candidate in (";", ",", "\t"):
        if candidate in sample.splitlines()[0]:
            logger.debug("_sniff_delimiter: sortie")
            return candidate
    logger.debug("_sniff_delimiter: sortie")
    return ";"


def load_inventory(path: str | Path) -> list[InventoryRow]:
    """Charge un inventaire CSV.

    Colonnes reconnues (insensibles à la casse) :
    - ``ip`` ou ``host`` (obligatoire).
    - ``type`` ou ``profile`` (obligatoire) : nom du profil à utiliser.
    - toute autre colonne devient une variable ``#nom_colonne#``.
    """
    logger.debug("load_inventory: entrée")
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError("Fichier d'inventaire vide.")
    delimiter = _sniff_delimiter(text)
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("Impossible de lire l'en-tête du CSV.")

    fieldmap = {f.strip().lower(): f for f in reader.fieldnames}
    ip_col = fieldmap.get("ip") or fieldmap.get("host")
    profile_col = fieldmap.get("type") or fieldmap.get("profile")
    if not ip_col:
        raise ValueError("Le CSV doit contenir une colonne 'ip' (ou 'host').")
    if not profile_col:
        raise ValueError("Le CSV doit contenir une colonne 'type' (ou 'profile').")

    rows: list[InventoryRow] = []
    for i, raw in enumerate(reader, start=2):  # ligne 1 = en-tête
        ip = (raw.get(ip_col) or "").strip()
        profile_name = (raw.get(profile_col) or "").strip()
        if not ip:
            continue
        variables = {
            k.strip(): (v or "").strip()
            for k, v in raw.items()
            if k not in (ip_col, profile_col) and k is not None
        }
        rows.append(InventoryRow(ip=ip, profile_name=profile_name, variables=variables, line_no=i))
    logger.debug("load_inventory: sortie")
    return rows


# ──────────────────────────────────────────────────────────────────────────
# Gabarits
# ──────────────────────────────────────────────────────────────────────────


def find_missing_variables(template_text: str, context: dict[str, str]) -> list[str]:
    """Liste les variables ``#nom#`` référencées par le gabarit mais absentes du contexte.

    Args:
        template_text (str): Texte du gabarit de commandes à envoyer,
            contenant d'éventuels placeholders ``#nom_variable#``.
        context (dict[str, str]): Variables disponibles pour la
            substitution (typiquement :meth:`InventoryRow.render_context`).

    Returns:
        list[str]: Noms de variables référencées par *template_text* mais
        absentes de *context*, triés alphabétiquement (liste vide si tout
        est résolvable).
    """
    logger.debug("find_missing_variables: entrée")
    needed = set(_VAR_RE.findall(template_text))
    logger.debug("find_missing_variables: sortie")
    return sorted(v for v in needed if v not in context)


def render_template(template_text: str, context: dict[str, str]) -> str:
    """Substitue chaque placeholder ``#nom#`` de *template_text* par sa valeur dans *context*.

    Args:
        template_text (str): Texte du gabarit de commandes à envoyer.
        context (dict[str, str]): Variables disponibles pour la
            substitution. Un placeholder sans correspondance dans
            *context* est laissé tel quel (non substitué) — voir
            :func:`find_missing_variables` pour les détecter en amont.

    Returns:
        str: Texte du gabarit avec les placeholders résolus substitués.
    """

    logger.debug("render_template: entrée")

    def _sub(m: re.Match) -> str:
        logger.debug("render_template._sub: entrée")
        key = m.group(1)
        logger.debug("render_template._sub: sortie")
        return context.get(key, m.group(0))

    logger.debug("render_template: sortie")
    return _VAR_RE.sub(_sub, template_text)


# ──────────────────────────────────────────────────────────────────────────
# Détection d'erreurs dans les logs (indépendante du constructeur)
# ──────────────────────────────────────────────────────────────────────────

# Marqueurs d'erreur couramment renvoyés par les CLI réseau lorsqu'une
# commande est rejetée (syntaxe invalide, commande inconnue, droits
# insuffisants...). Volontairement générique et multi-constructeurs plutôt
# qu'une liste par OS : netmiko ne lève pas d'exception dans ce cas, le
# rejet n'apparaît que dans le texte renvoyé par l'équipement.
_ERROR_PATTERNS = [
    re.compile(r"%\s*invalid", re.IGNORECASE),
    re.compile(r"%\s*unknown command", re.IGNORECASE),
    re.compile(r"%\s*ambiguous command", re.IGNORECASE),
    re.compile(r"%\s*incomplete command", re.IGNORECASE),
    re.compile(r"%\s*bad command", re.IGNORECASE),
    re.compile(r"unrecognized command", re.IGNORECASE),
    re.compile(r"invalid input", re.IGNORECASE),
    re.compile(r"syntax error", re.IGNORECASE),
    re.compile(r"command rejected", re.IGNORECASE),
    re.compile(r"permission denied", re.IGNORECASE),
    re.compile(r"authorization failed", re.IGNORECASE),
    re.compile(r"too many parameters", re.IGNORECASE),
    re.compile(r"error:\s", re.IGNORECASE),
]


def scan_output_for_errors(output_text: str) -> list[str]:
    """Retourne les lignes de *output_text* qui ressemblent à un rejet de
    commande par l'équipement (indépendamment du constructeur).
    """
    logger.debug("scan_output_for_errors: entrée")
    hits = []
    for line in output_text.splitlines():
        for pat in _ERROR_PATTERNS:
            if pat.search(line):
                hits.append(line.strip())
                break
    logger.debug("scan_output_for_errors: sortie")
    return hits


# ──────────────────────────────────────────────────────────────────────────
# Exécution
# ──────────────────────────────────────────────────────────────────────────

# Statuts possibles d'un PushResult
STATUS_OK = "ok"  # connecté, config envoyée, aucun marqueur d'erreur détecté
STATUS_REJECTED = "rejected"  # connecté, mais l'équipement a signalé une erreur dans sa sortie
STATUS_FAILED = "failed"  # échec de connexion/authentification/exécution
STATUS_SKIPPED = "skipped"  # non tenté (annulé, profil/variable manquants)


@dataclass
class PushResult:
    """Résultat d'une tentative de push de configuration sur un équipement.

    Un statut ``STATUS_*`` (``ok``/``rejected``/``failed``/``skipped``) ;
    voir :attr:`ok` pour la vue booléenne compat utilisée par l'UI.
    """

    row: InventoryRow
    status: str  # STATUS_*
    output: str = ""
    error: str = ""
    config_errors: list[str] = field(default_factory=list)
    log_path: str = ""
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        """Compat : True seulement si aucune erreur, ni de connexion ni de
        configuration.
        """
        logger.debug("PushResult.ok: entrée")
        logger.debug("PushResult.ok: sortie")
        return self.status == STATUS_OK


class PushResultLike(Protocol):
    """Forme commune de ``PushResult`` et ``snmp_push_core.SnmpPushResult``
    (journal, manifeste et synthèse sont partagés par les deux transports).
    """

    row: InventoryRow
    status: str
    output: str
    error: str
    config_errors: list[str]
    log_path: str
    duration_s: float


def _write_log(log_dir: Path, row: InventoryRow, result: PushResultLike) -> str:
    logger.debug("_write_log: entrée")
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_ip = re.sub(r"[^A-Za-z0-9_.-]", "_", row.ip)
    log_file = log_dir / f"{row.line_no:04d}_{safe_ip}.log"
    lines = [
        f"# Hôte : {row.ip}  (profil : {row.profile_name}, ligne CSV : {row.line_no})",
        f"# Statut : {result.status}",
    ]
    if result.error:
        lines.append(f"# Erreur de connexion : {result.error}")
    if result.config_errors:
        lines.append("# Lignes rejetées détectées :")
        lines.extend(f"#   {e}" for e in result.config_errors)
    lines.append("# ── Sortie brute de l'équipement ──")
    lines.append(result.output or "(aucune sortie)")
    log_file.write_text("\n".join(lines), encoding="utf-8")
    logger.debug("_write_log: sortie")
    return str(log_file)


def push_to_device(
    row: InventoryRow,
    profile: DeviceProfile,
    rendered_config: str,
    save: bool = False,
) -> PushResult:
    """Se connecte à un équipement et envoie la configuration rendue.

    Ne journalise pas sur disque elle-même (cf. ``run_bulk_push``) : cette
    fonction peut donc être appelée isolément (ex. tests, aperçu réel) sans
    effet de bord fichier.
    """
    logger.debug("push_to_device: entrée")
    try:
        from netmiko import ConnectHandler
        from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException
    except ImportError as exc:  # pragma: no cover - dépendance externe
        logger.debug("push_to_device: {} : {}", type(exc).__name__, exc)
        logger.debug("push_to_device: sortie")
        return PushResult(row=row, status=STATUS_FAILED, error=f"netmiko non installé : {exc}")

    lines = [line for line in rendered_config.splitlines() if line.strip() != ""]
    if not lines:
        logger.debug("push_to_device: sortie")
        return PushResult(
            row=row, status=STATUS_FAILED, error="Gabarit rendu vide, rien à envoyer."
        )

    started = time.monotonic()
    conn = None
    try:
        conn = ConnectHandler(**profile.to_netmiko_kwargs(row.ip))
        if profile.secret:
            conn.enable()
        output = conn.send_config_set(lines)
        if save:
            if profile.save_command:
                output += "\n" + conn.send_command_timing(profile.save_command)
            else:
                output += "\n" + conn.save_config()
        errors = scan_output_for_errors(output)
        status = STATUS_REJECTED if errors else STATUS_OK
        logger.debug("push_to_device: sortie")
        return PushResult(
            row=row,
            status=status,
            output=output,
            config_errors=errors,
            duration_s=time.monotonic() - started,
        )
    except NetmikoAuthenticationException as exc:
        logger.debug("push_to_device: {} : {}", type(exc).__name__, exc)
        logger.debug("push_to_device: sortie")
        return PushResult(
            row=row,
            status=STATUS_FAILED,
            error=f"Authentification refusée : {exc}",
            duration_s=time.monotonic() - started,
        )
    except NetmikoTimeoutException as exc:
        logger.debug("push_to_device: {} : {}", type(exc).__name__, exc)
        logger.debug("push_to_device: sortie")
        return PushResult(
            row=row,
            status=STATUS_FAILED,
            error=f"Délai dépassé / injoignable : {exc}",
            duration_s=time.monotonic() - started,
        )
    except Exception as exc:  # noqa: BLE001 - on veut consigner toute erreur par hôte
        logger.debug("push_to_device: {} : {}", type(exc).__name__, exc)
        logger.debug("push_to_device: sortie")
        return PushResult(
            row=row, status=STATUS_FAILED, error=str(exc), duration_s=time.monotonic() - started
        )
    finally:
        if conn is not None:
            try:
                conn.disconnect()
            except Exception as exc:
                logger.debug("push_to_device: {} : {}", type(exc).__name__, exc)
                pass


def run_bulk_push(
    rows: list[InventoryRow],
    profiles: ProfileStore,
    template_text: str,
    *,
    log_dir: str | Path,
    save: bool = False,
    max_workers: int = 5,
    on_row_start: Callable[[InventoryRow], None] | None = None,
    on_row_done: Callable[[PushResult], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[PushResult]:
    """Pousse la configuration sur tous les *rows* en parallèle (borné par
    *max_workers*), en journalisant chaque hôte sur disque dans *log_dir*
    et en écrivant un manifeste JSON agrégé à la fin.

    ``on_row_start``/``on_row_done`` sont appelés depuis les threads
    ouvriers — si utilisés pour mettre à jour une UI GTK, l'appelant doit
    les faire rebondir sur le thread principal (ex. ``GLib.idle_add``).
    """
    logger.debug("run_bulk_push: entrée")
    log_dir = Path(log_dir)
    results: list[PushResult] = []

    def _work(row: InventoryRow) -> PushResult:
        logger.debug("run_bulk_push._work: entrée")
        if cancel_event is not None and cancel_event.is_set():
            result = PushResult(row=row, status=STATUS_SKIPPED, error="Annulé avant envoi.")
            result.log_path = _write_log(log_dir, row, result)
            logger.debug("run_bulk_push._work: sortie")
            return result

        profile = profiles.get(row.profile_name)
        if profile is None:
            result = PushResult(
                row=row, status=STATUS_FAILED, error=f"Profil inconnu : '{row.profile_name}'"
            )
            result.log_path = _write_log(log_dir, row, result)
            logger.debug("run_bulk_push._work: sortie")
            return result

        context = row.render_context()
        missing = find_missing_variables(template_text, context)
        if missing:
            result = PushResult(
                row=row,
                status=STATUS_FAILED,
                error=f"Variables manquantes dans le CSV : {', '.join(missing)}",
            )
            result.log_path = _write_log(log_dir, row, result)
            logger.debug("run_bulk_push._work: sortie")
            return result

        if on_row_start:
            on_row_start(row)
        rendered = render_template(template_text, context)
        result = push_to_device(row, profile, rendered, save=save)
        result.log_path = _write_log(log_dir, row, result)
        logger.debug("run_bulk_push._work: sortie")
        return result

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {pool.submit(_work, row): row for row in rows}
        for fut in as_completed(futures):
            result = fut.result()
            results.append(result)
            if on_row_done:
                on_row_done(result)

    _write_manifest(log_dir, results)
    logger.debug("run_bulk_push: sortie")
    return results


def _write_manifest(log_dir: Path, results: Sequence[PushResultLike]) -> None:
    logger.debug("_write_manifest: entrée")
    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summarize_results(results),
        "hosts": [
            {
                "ip": r.row.ip,
                "profile": r.row.profile_name,
                "line_no": r.row.line_no,
                "status": r.status,
                "error": r.error,
                "config_errors": r.config_errors,
                "log_path": r.log_path,
                "duration_s": round(r.duration_s, 2),
            }
            for r in results
        ],
    }
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.debug("_write_manifest: fin")


def summarize_results(results: Sequence[PushResultLike]) -> dict:
    """Statistiques d'une exécution, pour affichage dans la phase Résultats."""
    logger.debug("summarize_results: entrée")
    total = len(results)
    by_status = {STATUS_OK: 0, STATUS_REJECTED: 0, STATUS_FAILED: 0, STATUS_SKIPPED: 0}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    error_kinds: dict[str, int] = {}
    for r in results:
        if r.status == STATUS_FAILED and r.error:
            key = r.error.split(":")[0].strip()
            error_kinds[key] = error_kinds.get(key, 0) + 1
    logger.debug("summarize_results: sortie")
    return {
        "total": total,
        "ok": by_status[STATUS_OK],
        "rejected": by_status[STATUS_REJECTED],
        "failed": by_status[STATUS_FAILED],
        "skipped": by_status[STATUS_SKIPPED],
        "error_kinds": error_kinds,
    }
