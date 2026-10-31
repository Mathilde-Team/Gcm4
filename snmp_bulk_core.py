#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cœur métier (sans GTK) du déploiement SNMP pour GCM.

Core simple et synchrone pour le plugin Push SNMP, adapté du style
``netmiko_bulk_core.py``. Utilise **EzSnmp** (bindings natifs libnetsnmp)
pour de meilleures performances sur volumes.

Structure : profils SNMP → inventaire CSV → gabarit → push par lot.

Dépendances::

    apt install libsnmp-dev snmp-mibs-downloader
    pip install ezsnmp --break-system-packages
"""

from __future__ import annotations

import configparser
import ftplib
import io
import re
import socket
import struct
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
logger.disable(__name__)

try:
    from ezsnmp import Session
    from ezsnmp.exceptions import (
        EasySNMPError,
        EasySNMPNoSuchInstanceError,
        EasySNMPNoSuchObjectError,
        EasySNMPTimeoutError,
    )
except ImportError as exc:
    logger.debug("easysnmp.exceptions incomplet ({}), import de repli", exc)
    from easysnmp import Session
    from easysnmp.exceptions import (
        EasySNMPError,
        EasySNMPNoSuchInstanceError,
        EasySNMPNoSuchObjectError,
        EasySNMPTimeoutError,
    )


__all__ = [
    "SNMP_VENDORS",
    "SnmpAuth",
    "SnmpProfile",
    "SnmpProfileStore",
    "InventoryRow",
    "PushResult",
    "load_inventory",
    "render_template",
    "find_missing_variables",
    "push_to_device",
    "run_bulk_push",
    "summarize_results",
    "FileTransferConfig",
    "TransferError",
    "TRANSFER_PROTOCOLS",
    "upload_config_file",
    "STATUS_OK",
    "STATUS_REJECTED",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
]

_VAR_RE = re.compile(r"#([A-Za-z0-9_]+)#")

# Statuts
STATUS_OK = "ok"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# Vendeurs disponibles
SNMP_VENDORS = {
    "h3c_comware": "H3C / HPE Comware",
    "cisco_ios": "Cisco IOS / IOS-XE",
    "huawei_vrp": "Huawei VRP",
}


# ────────────────────────────────────────────────────────────────────────
# Authentification et Profils SNMP
# ────────────────────────────────────────────────────────────────────────


@dataclass
class SnmpAuth:
    """Authentification SNMP (v2c ou v3)."""

    version: str = "v2c"
    community: str = "private"
    username: str | None = None
    auth_password: str | None = None
    auth_protocol: str = "SHA"
    priv_password: str | None = None
    priv_protocol: str = "AES"
    security_level: str = "auth_with_privacy"

    def session_kwargs(
        self, host: str, port: int = 161, timeout: int = 5, retries: int = 2
    ) -> dict:
        """Paramètres pour ezsnmp.Session(...).

        Args:
            host: IP/hôte de l'équipement cible.
            port: Port SNMP de l'équipement.
            timeout: Délai d'attente par requête, en secondes.
            retries: Nombre de nouvelles tentatives en cas de timeout.

        Returns:
            Dictionnaire de kwargs prêt à passer à ``ezsnmp.Session(...)``.
        """
        logger.debug(f"SnmpAuth.session_kwargs | host={host} port={port} version={self.version}")
        base = {
            "hostname": host,
            "port": port,
            "timeout": timeout,
            "retries": retries,
        }
        if self.version == "v2c":
            base["version"] = 2
            base["community"] = self.community
        else:  # v3
            base["version"] = 3
            base["security_username"] = self.username or ""
            base["auth_protocol"] = self.auth_protocol
            base["auth_password"] = self.auth_password or ""
            base["privacy_protocol"] = self.priv_protocol
            base["privacy_password"] = self.priv_password or ""
            base["security_level"] = self.security_level
        logger.debug("SnmpAuth.session_kwargs: retour base")
        return base


@dataclass
class SnmpProfile:
    """Profil de connexion SNMP."""

    name: str
    vendor: str
    auth: SnmpAuth
    port: int = 161
    timeout: int = 5
    retries: int = 2
    poll_interval: float = 2.0
    poll_timeout: float = 120.0


class SnmpProfileStore:
    """Magasin des profils SNMP (sur disque en .ini)."""

    def __init__(self, path: str | Path = "snmp_profiles.ini"):
        """Initialise le magasin.

        Args:
            path: Chemin du fichier ``.ini`` de profils SNMP.
        """
        logger.debug(f"SnmpProfileStore.__init__ | path={path}")
        self.path = Path(path)
        self._profiles: dict[str, SnmpProfile] = {}
        if self.path.exists():
            self.load()
        logger.debug("SnmpProfileStore.__init__: fin")

    def load(self) -> None:
        """Charge les profils depuis le fichier."""
        logger.debug(f"SnmpProfileStore.load | path={self.path}")
        config = configparser.ConfigParser()
        config.read(self.path)

        for section in config.sections():
            if section.startswith("profile:"):
                name = section[8:]
                profile = SnmpProfile(
                    name=name,
                    vendor=config.get(section, "vendor"),
                    auth=SnmpAuth(
                        version=config.get(section, "snmp_version", fallback="v2c"),
                        community=config.get(section, "community", fallback="private"),
                        username=config.get(section, "username", fallback=None),
                        auth_password=config.get(section, "auth_password", fallback=None),
                        auth_protocol=config.get(section, "auth_protocol", fallback="SHA"),
                        priv_password=config.get(section, "priv_password", fallback=None),
                        priv_protocol=config.get(section, "priv_protocol", fallback="AES"),
                        security_level=config.get(
                            section, "security_level", fallback="auth_with_privacy"
                        ),
                    ),
                    port=config.getint(section, "port", fallback=161),
                    timeout=config.getint(section, "timeout", fallback=5),
                    retries=config.getint(section, "retries", fallback=2),
                    poll_interval=config.getfloat(section, "poll_interval", fallback=2.0),
                    poll_timeout=config.getfloat(section, "poll_timeout", fallback=120.0),
                )
                self._profiles[name] = profile
        logger.debug("SnmpProfileStore.load: fin")

    def save(self) -> None:
        """Sauvegarde les profils sur disque."""
        logger.debug(f"SnmpProfileStore.save | path={self.path} n={len(self._profiles)}")
        config = configparser.ConfigParser()
        for name, p in self._profiles.items():
            section = f"profile:{name}"
            config[section] = {
                "vendor": p.vendor,
                "snmp_version": p.auth.version,
                "community": p.auth.community,
                "username": p.auth.username or "",
                "auth_password": p.auth.auth_password or "",
                "auth_protocol": p.auth.auth_protocol,
                "priv_password": p.auth.priv_password or "",
                "priv_protocol": p.auth.priv_protocol,
                "security_level": p.auth.security_level,
                "port": str(p.port),
                "timeout": str(p.timeout),
                "retries": str(p.retries),
                "poll_interval": str(p.poll_interval),
                "poll_timeout": str(p.poll_timeout),
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            config.write(f)
        logger.debug("SnmpProfileStore.save: fin")

    def get(self, name: str) -> SnmpProfile | None:
        """Récupère un profil par nom.

        Args:
            name: Nom du profil recherché.
        """
        logger.debug(f"SnmpProfileStore.get | name={name}")
        return self._profiles.get(name)

    def all(self) -> list[SnmpProfile]:
        """Retourne tous les profils."""
        logger.debug("SnmpProfileStore.all")
        return list(self._profiles.values())

    def set(self, name: str, profile: SnmpProfile) -> None:
        """Ajoute ou met à jour un profil.

        Args:
            name: Nom du profil.
            profile: Instance ``SnmpProfile`` à enregistrer.
        """
        logger.debug(f"SnmpProfileStore.set | name={name} vendor={profile.vendor}")
        self._profiles[name] = profile
        self.save()
        logger.debug("SnmpProfileStore.set: fin")

    def delete(self, name: str) -> None:
        """Supprime un profil.

        Args:
            name: Nom du profil à supprimer.
        """
        logger.debug(f"SnmpProfileStore.delete | name={name}")
        self._profiles.pop(name, None)
        self.save()
        logger.debug("SnmpProfileStore.delete: fin")


# ────────────────────────────────────────────────────────────────────────
# Inventaire CSV
# ────────────────────────────────────────────────────────────────────────


@dataclass
class InventoryRow:
    """Ligne de l'inventaire CSV."""

    line_no: int
    ip: str
    profile_name: str
    variables: dict[str, str] = field(default_factory=dict)


def load_inventory(csv_path: str | Path) -> list[InventoryRow]:
    """Charge l'inventaire CSV.

    Args:
        csv_path: Chemin du fichier CSV d'inventaire.

    Returns:
        Liste des lignes d'inventaire parsées.
    """
    logger.debug(f"load_inventory | csv_path={csv_path}")
    rows = []
    import csv

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        for line_no, row in enumerate(reader, start=2):
            ip = row.get("ip") or row.get("host") or ""
            profile_name = row.get("type") or row.get("profile") or ""
            if ip and profile_name:
                vars_dict = {
                    k: v for k, v in row.items() if k not in ("ip", "host", "type", "profile")
                }
                rows.append(
                    InventoryRow(
                        line_no=line_no, ip=ip, profile_name=profile_name, variables=vars_dict
                    )
                )
    logger.debug("load_inventory: retour rows")
    return rows


# ────────────────────────────────────────────────────────────────────────
# Gabarit
# ────────────────────────────────────────────────────────────────────────


def find_missing_variables(template: str, available: set[str]) -> set[str]:
    """Variables #...# du gabarit non trouvées dans le CSV.

    Args:
        template: Contenu du gabarit de configuration.
        available: Ensemble des noms de variables disponibles (colonnes CSV).

    Returns:
        Ensemble des noms de variables référencées par le gabarit mais absentes.
    """
    logger.debug(f"find_missing_variables | available={available}")
    template_vars = set(_VAR_RE.findall(template))
    logger.debug("find_missing_variables: retour calculé")
    return template_vars - available


def render_template(template: str, variables: dict[str, str]) -> str:
    """Remplace les variables #col# par leurs valeurs.

    Args:
        template: Contenu du gabarit de configuration.
        variables: Valeurs à substituer, indexées par nom de colonne.

    Returns:
        Le gabarit avec les variables remplacées.
    """
    logger.debug(f"render_template | n_variables={len(variables)}")

    def replace(match):
        """Substitue une occurrence ``#var#`` par sa valeur.

        Args:
            match: Correspondance regex sur ``#nom_variable#``.
        """
        logger.trace("replace: entrée")
        var_name = match.group(1)
        logger.trace("replace: retour de get()")
        return variables.get(var_name, f"#{var_name}#")

    logger.debug("render_template: retour de sub()")
    return _VAR_RE.sub(replace, template)


# ────────────────────────────────────────────────────────────────────────
# Résultat
# ────────────────────────────────────────────────────────────────────────


@dataclass
class PushResult:
    """Résultat du push sur un équipement."""

    row: InventoryRow
    status: str
    error: str = ""
    log_path: str = ""
    duration_s: float = 0.0


# ────────────────────────────────────────────────────────────────────────
# Dépôt du fichier rendu sur le serveur de dépôt (SFTP / FTP / TFTP)
# ────────────────────────────────────────────────────────────────────────
#
# Repris de l'ancien ``snmp_push_core.py`` (issue #74) : l'équipement va
# chercher son fichier de configuration sur un serveur de dépôt ; gcm4 doit
# donc d'abord l'y déposer. Le protocole de DÉPÔT (gcm4 -> serveur) peut
# différer du protocole de RÉCUPÉRATION (équipement -> serveur) : un dépôt
# SFTP sur un serveur qui sert ensuite le même dossier en TFTP est courant.

TRANSFER_PROTOCOLS = ("sftp", "ftp", "tftp")
_DEFAULT_PORTS = {"sftp": 22, "ftp": 21, "tftp": 69}
_TRANSFER_TIMEOUT_S = 15.0


class TransferError(RuntimeError):
    """Échec du dépôt du fichier sur le serveur de dépôt."""


@dataclass
class FileTransferConfig:
    """Serveur de dépôt du fichier de configuration rendu.

    Attributes:
        protocol: Protocole de dépôt par gcm4 : ``"sftp"``, ``"ftp"`` ou ``"tftp"``.
        host: Adresse du serveur, joignable depuis ce poste ET depuis les
            équipements (c'est elle qui est écrite dans le ``SNMP SET``).
        port: Port du service de dépôt ; 0 : port par défaut du protocole.
        username: Utilisateur SFTP/FTP (aussi transmis à l'équipement en FTP).
        password: Mot de passe SFTP/FTP (idem).
        remote_dir: Dossier distant existant où déposer le fichier.
        fetch_protocol: Protocole de récupération par l'équipement
            (``"tftp"`` ou ``"ftp"``) ; vide : celui du dépôt, ou TFTP si le
            dépôt se fait en SFTP.
        fetch_path: Chemin du fichier tel que l'équipement le demande ;
            vide : le nom du fichier seul (racine du service de récupération).
        known_hosts: Fichier ``known_hosts`` supplémentaire pour la clé
            d'hôte SFTP (en plus de ``~/.ssh/known_hosts``).
    """

    protocol: str
    host: str
    port: int = 0
    username: str = ""
    password: str = field(default="", repr=False)
    remote_dir: str = "/"
    fetch_protocol: str = ""
    fetch_path: str = ""
    known_hosts: str = ""

    def effective_port(self) -> int:
        """Port de dépôt : ``port`` s'il est renseigné, sinon celui du protocole.

        Returns:
            Le port à utiliser, 0 si le protocole est inconnu.
        """
        logger.debug(f"FileTransferConfig.effective_port | protocol={self.protocol}")
        port = self.port or _DEFAULT_PORTS.get(self.protocol, 0)
        logger.debug(f"FileTransferConfig.effective_port | port={port}")
        return port

    def effective_fetch_protocol(self) -> str:
        """Protocole que l'équipement utilisera pour récupérer le fichier.

        Returns:
            ``fetch_protocol`` s'il est renseigné, sinon le protocole de dépôt,
            ou ``"tftp"`` pour un dépôt SFTP.
        """
        logger.debug(f"FileTransferConfig.effective_fetch_protocol | protocol={self.protocol}")
        fetch = self.fetch_protocol or ("tftp" if self.protocol == "sftp" else self.protocol)
        logger.debug(f"FileTransferConfig.effective_fetch_protocol | fetch={fetch}")
        return fetch

    def remote_path(self, filename: str) -> str:
        """Chemin distant de dépôt de *filename* (``remote_dir`` + nom).

        Args:
            filename: Nom du fichier déposé.

        Returns:
            ``remote_dir/filename``, ou *filename* seul si ``remote_dir`` est
            vide ou ``/`` (racine du service, cas usuel en TFTP).
        """
        logger.debug(f"FileTransferConfig.remote_path | filename={filename}")
        base = self.remote_dir.rstrip("/")
        path = f"{base}/{filename}" if base else filename
        logger.debug(f"FileTransferConfig.remote_path | path={path}")
        return path

    def device_filename(self, filename: str) -> str:
        """Nom de fichier écrit dans le ``SNMP SET`` pour l'équipement.

        Args:
            filename: Nom du fichier déposé.

        Returns:
            ``fetch_path/filename`` si ``fetch_path`` est renseigné, sinon
            *filename*.
        """
        logger.debug(f"FileTransferConfig.device_filename | filename={filename}")
        base = self.fetch_path.rstrip("/")
        name = f"{base}/{filename}" if base else filename
        logger.debug(f"FileTransferConfig.device_filename | name={name}")
        return name


def _upload_sftp(cfg: FileTransferConfig, remote_path: str, content: bytes) -> None:
    """Dépose *content* en SFTP (paramiko).

    La clé d'hôte du serveur doit être connue (``~/.ssh/known_hosts`` ou
    ``cfg.known_hosts``) : une clé inconnue ou changée est refusée, comme
    ``ssh`` avec ``StrictHostKeyChecking=yes`` (l'ancien module l'acceptait
    sans vérification).

    Args:
        cfg: Serveur de dépôt.
        remote_path: Chemin distant du fichier à créer.
        content: Contenu à écrire.

    Raises:
        TransferError: Bibliothèque absente, clé d'hôte refusée, erreur SSH ou SFTP.
    """
    logger.debug(f"_upload_sftp | host={cfg.host} remote_path={remote_path}")
    try:
        import paramiko  # dépendance optionnelle, importée au premier dépôt SFTP
    except ImportError as exc:
        logger.warning(f"_upload_sftp | paramiko absent : {exc}")
        raise TransferError("Dépôt SFTP impossible : paramiko n'est pas installé.") from exc

    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        if cfg.known_hosts:
            client.load_host_keys(str(Path(cfg.known_hosts).expanduser()))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            cfg.host,
            port=cfg.effective_port(),
            username=cfg.username or None,
            password=cfg.password or None,
            timeout=_TRANSFER_TIMEOUT_S,
            allow_agent=not cfg.password,
            look_for_keys=not cfg.password,
        )
        sftp = client.open_sftp()
        try:
            with sftp.open(remote_path, "wb") as fh:
                fh.write(content)
        finally:
            sftp.close()
    except paramiko.BadHostKeyException as exc:
        logger.warning(f"_upload_sftp | clé d'hôte changée : {cfg.host}")
        raise TransferError(
            f"Clé d'hôte SFTP de {cfg.host} différente de celle de known_hosts : dépôt refusé."
        ) from exc
    except paramiko.SSHException as exc:
        logger.warning(f"_upload_sftp | erreur SSH : {exc}")
        if "not found in known_hosts" in str(exc):
            raise TransferError(
                f"Clé d'hôte SFTP de {cfg.host} inconnue : se connecter une fois avec ssh "
                "ou l'ajouter à known_hosts (ssh-keyscan)."
            ) from exc
        raise TransferError(f"Erreur SSH vers {cfg.host} : {exc}") from exc
    except OSError as exc:
        logger.warning(f"_upload_sftp | erreur réseau ou SFTP : {exc}")
        raise TransferError(f"Dépôt SFTP vers {cfg.host} impossible : {exc}") from exc
    finally:
        client.close()
    logger.debug(f"_upload_sftp | {len(content)} octet(s) déposé(s)")


def _upload_ftp(cfg: FileTransferConfig, remote_path: str, content: bytes) -> None:
    """Dépose *content* en FTP (ftplib, mode binaire).

    Args:
        cfg: Serveur de dépôt.
        remote_path: Chemin distant du fichier à créer.
        content: Contenu à écrire.

    Raises:
        TransferError: Connexion, authentification ou écriture refusée.
    """
    logger.debug(f"_upload_ftp | host={cfg.host} remote_path={remote_path}")
    try:
        with ftplib.FTP() as ftp:
            ftp.connect(cfg.host, cfg.effective_port(), timeout=_TRANSFER_TIMEOUT_S)
            ftp.login(cfg.username or "anonymous", cfg.password)
            ftp.storbinary(f"STOR {remote_path}", io.BytesIO(content))
    except (ftplib.Error, OSError, EOFError) as exc:
        logger.warning(f"_upload_ftp | échec : {exc}")
        raise TransferError(f"Dépôt FTP vers {cfg.host} impossible : {exc}") from exc
    logger.debug(f"_upload_ftp | {len(content)} octet(s) déposé(s)")


# Client TFTP minimal (RFC 1350) : WRQ puis blocs DATA/ACK de 512 octets,
# mode octet, sans options (RFC 2347). La stdlib n'a pas de client TFTP.
_TFTP_WRQ = 2
_TFTP_DATA = 3
_TFTP_ACK = 4
_TFTP_ERROR = 5
_TFTP_BLOCK_SIZE = 512
_TFTP_TIMEOUT_S = 5.0
_TFTP_MAX_RETRIES = 4


def _tftp_exchange(
    sock: socket.socket, packet: bytes, block: int, addr: tuple[str, int]
) -> tuple[str, int]:
    """Envoie *packet* jusqu'à recevoir l'ACK du bloc *block*.

    Args:
        sock: Socket UDP du transfert.
        packet: Paquet à (ré)émettre.
        block: Numéro de bloc attendu dans l'ACK.
        addr: Destinataire ``(hôte, port)``.

    Returns:
        L'adresse qui a répondu (le serveur répond depuis un port éphémère
        après le WRQ, cf. RFC 1350 §4).

    Raises:
        TransferError: Paquet ERROR du serveur, réponse inattendue ou
            absence d'ACK après ``_TFTP_MAX_RETRIES`` émissions.
    """
    logger.debug(f"_tftp_exchange | block={block} addr={addr}")
    for _attempt in range(_TFTP_MAX_RETRIES):
        sock.sendto(packet, addr)
        try:
            data, peer = sock.recvfrom(516)
        except TimeoutError:
            logger.debug(f"_tftp_exchange | pas de réponse pour le bloc {block}, nouvel essai")
            continue
        opcode = struct.unpack("!H", data[:2])[0] if len(data) >= 4 else 0
        if opcode == _TFTP_ERROR:
            message = data[4:].rstrip(b"\x00").decode("ascii", "replace")
            logger.warning(f"_tftp_exchange | erreur du serveur : {message}")
            raise TransferError(f"Erreur TFTP du serveur : {message}")
        if opcode == _TFTP_ACK:
            if struct.unpack("!H", data[2:4])[0] == block:
                logger.debug(f"_tftp_exchange | ACK {block} de {peer}")
                return peer
            continue  # ACK en double ou en retard : on attend le bon
        raise TransferError(f"Réponse TFTP inattendue (opcode {opcode}).")
    raise TransferError(f"Pas d'ACK TFTP pour le bloc {block} après {_TFTP_MAX_RETRIES} essais.")


def _upload_tftp(cfg: FileTransferConfig, remote_path: str, content: bytes) -> None:
    """Dépose *content* en TFTP (client RFC 1350 intégré).

    Args:
        cfg: Serveur de dépôt (ni utilisateur ni mot de passe en TFTP).
        remote_path: Nom distant demandé dans le WRQ.
        content: Contenu à écrire.

    Raises:
        TransferError: Nom non ASCII, erreur du serveur ou délai dépassé.
    """
    logger.debug(f"_upload_tftp | host={cfg.host} remote_path={remote_path}")
    try:
        name = remote_path.encode("ascii")
    except UnicodeEncodeError as exc:
        logger.warning(f"_upload_tftp | nom non ASCII : {remote_path!r}")
        raise TransferError(f"Nom de fichier TFTP non ASCII : {remote_path!r}") from exc
    try:
        infos = socket.getaddrinfo(cfg.host, cfg.effective_port(), type=socket.SOCK_DGRAM)
    except OSError as exc:
        logger.warning(f"_upload_tftp | adresse invalide : {exc}")
        raise TransferError(f"Serveur TFTP {cfg.host} introuvable : {exc}") from exc
    family, _type, _proto, _name, addr = infos[0]
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(_TFTP_TIMEOUT_S)
        wrq = struct.pack("!H", _TFTP_WRQ) + name + b"\x00octet\x00"
        peer = _tftp_exchange(sock, wrq, 0, (str(addr[0]), int(addr[1])))
        block = 1
        offset = 0
        while True:
            chunk = content[offset : offset + _TFTP_BLOCK_SIZE]
            packet = struct.pack("!HH", _TFTP_DATA, block & 0xFFFF) + chunk
            _tftp_exchange(sock, packet, block & 0xFFFF, peer)
            offset += len(chunk)
            if len(chunk) < _TFTP_BLOCK_SIZE:
                break  # dernier bloc (vide si la taille est un multiple de 512)
            block += 1
    logger.debug(f"_upload_tftp | {len(content)} octet(s) en {block} bloc(s)")


_UPLOADERS: dict[str, Callable[[FileTransferConfig, str, bytes], None]] = {
    "sftp": _upload_sftp,
    "ftp": _upload_ftp,
    "tftp": _upload_tftp,
}


def upload_config_file(cfg: FileTransferConfig, filename: str, content: str) -> str:
    """Dépose *content* (UTF-8) sous *filename* sur le serveur de dépôt.

    Bloquant : à appeler depuis un fil de travail (``run_bulk_push`` le fait).

    Args:
        cfg: Serveur de dépôt.
        filename: Nom du fichier à créer dans ``cfg.remote_dir``.
        content: Configuration rendue.

    Returns:
        Le chemin distant écrit.

    Raises:
        TransferError: Protocole inconnu ou échec du dépôt.
    """
    logger.debug(
        f"upload_config_file | protocol={cfg.protocol} host={cfg.host} filename={filename}"
    )
    uploader = _UPLOADERS.get(cfg.protocol)
    if uploader is None:
        logger.warning(f"upload_config_file | protocole inconnu : {cfg.protocol!r}")
        raise TransferError(f"Protocole de dépôt inconnu : {cfg.protocol!r}")
    remote_path = cfg.remote_path(filename)
    uploader(cfg, remote_path, content.encode("utf-8"))
    logger.debug(f"upload_config_file | déposé : {remote_path}")
    return remote_path


# ────────────────────────────────────────────────────────────────────────
# Drivers SNMP (par constructeur)
# ────────────────────────────────────────────────────────────────────────


class SnmpDriver:
    """Interface pour un driver SNMP (constructeur)."""

    vendor_id: str = ""
    display_name: str = ""
    # Protocole de récupération du fichier par l'équipement -> valeur MIB.
    FETCH_PROTOCOLS: dict[str, int] = {}

    def __init__(
        self,
        auth: SnmpAuth,
        port: int = 161,
        timeout: int = 5,
        retries: int = 2,
        poll_interval: float = 2.0,
        poll_timeout: float = 120.0,
    ):
        """Initialise le driver.

        Args:
            auth: Authentification SNMP (v2c ou v3).
            port: Port SNMP de l'équipement.
            timeout: Délai d'attente par requête, en secondes.
            retries: Nombre de nouvelles tentatives en cas de timeout.
            poll_interval: Intervalle entre deux sondages de statut, en secondes.
            poll_timeout: Délai maximal d'attente du résultat, en secondes.
        """
        logger.debug(f"SnmpDriver.__init__ | vendor={self.vendor_id} port={port}")
        self.auth = auth
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        logger.debug("SnmpDriver.__init__: fin")

    def _session(self, host: str) -> Session:
        """Crée une session EzSnmp.

        Args:
            host: IP/hôte de l'équipement cible.
        """
        logger.debug(f"SnmpDriver._session | host={host}")
        return Session(**self.auth.session_kwargs(host, self.port, self.timeout, self.retries))

    def _set_multiple(self, host: str, oid_values: list[tuple[str, str, str]]) -> tuple[bool, str]:
        """Effectue un SET multi-varbind.

        Args:
            host: IP/hôte de l'équipement cible.
            oid_values: Liste de tuples ``(oid, valeur, type)`` à écrire.

        Returns:
            Tuple ``(ok, detail)``.
        """
        logger.debug(f"SnmpDriver._set_multiple | host={host} n_oid={len(oid_values)}")
        try:
            self._session(host).set_multiple(oid_values)
            logger.debug("SnmpDriver._set_multiple: retour True « OK »")
            return True, "OK"
        except (EasySNMPTimeoutError, EasySNMPError) as exc:
            logger.warning("SNMP SET vers {} échoué : {}", host, exc)
            return False, str(exc)

    def _get(self, host: str, oid: str) -> tuple[bool, str]:
        """Effectue un GET.

        Args:
            host: IP/hôte de l'équipement cible.
            oid: OID SNMP à lire.

        Returns:
            Tuple ``(ok, valeur_ou_detail)``.
        """
        logger.debug(f"SnmpDriver._get | host={host} oid={oid}")
        try:
            var = self._session(host).get(oid)
            logger.debug("SnmpDriver._get: retour True")
            return True, var.value
        except (
            EasySNMPTimeoutError,
            EasySNMPNoSuchObjectError,
            EasySNMPNoSuchInstanceError,
            EasySNMPError,
        ) as exc:
            logger.debug("SNMP GET {} sur {} échoué : {}", oid, host, exc)
            return False, str(exc)

    def push(
        self,
        host: str,
        server_ip: str,
        filename: str,
        username: str = "",
        password: str = "",
        protocol: str = "tftp",
    ) -> tuple[bool, str]:
        """À implémenter : effectue le push de config.

        Args:
            host: IP/hôte de l'équipement cible.
            server_ip: IP du serveur de dépôt (TFTP/FTP/SFTP).
            filename: Nom du fichier de configuration à récupérer.
            username: Identifiant optionnel pour le serveur de dépôt.
            password: Mot de passe optionnel pour le serveur de dépôt.
            protocol: Protocole de récupération par l'équipement (``FETCH_PROTOCOLS``).

        Raises:
            NotImplementedError: Toujours, cette méthode doit être surchargée.
        """
        logger.debug(f"SnmpDriver.push | host={host} vendor={self.vendor_id}")
        raise NotImplementedError

    def protocol_value(self, protocol: str) -> int | None:
        """Valeur MIB du protocole de récupération *protocol*.

        Args:
            protocol: ``"tftp"`` ou ``"ftp"``.

        Returns:
            La valeur entière à écrire dans le ``SET``, ``None`` si ce
            constructeur ne sait pas récupérer un fichier par ce protocole.
        """
        logger.debug(f"SnmpDriver.protocol_value | vendor={self.vendor_id} protocol={protocol}")
        value = self.FETCH_PROTOCOLS.get(protocol)
        logger.debug(f"SnmpDriver.protocol_value | value={value}")
        return value

    def _unsupported_protocol(self, protocol: str) -> tuple[bool, str]:
        """Résultat d'échec pour un protocole de récupération non géré.

        Args:
            protocol: Protocole demandé.

        Returns:
            Tuple ``(False, detail)``.
        """
        logger.debug(f"SnmpDriver._unsupported_protocol | protocol={protocol}")
        supported = ", ".join(sorted(self.FETCH_PROTOCOLS))
        detail = (
            f"{self.display_name} ne récupère pas de fichier en {protocol} "
            f"(protocoles gérés : {supported})"
        )
        logger.debug(f"SnmpDriver._unsupported_protocol | {detail}")
        return False, detail


class H3cComwareDriver(SnmpDriver):
    """Driver H3C / HPE Comware (HH3C-CONFIG-MAN-MIB)."""

    vendor_id = "h3c_comware"
    display_name = "H3C / HPE Comware"
    FETCH_PROTOCOLS = {"ftp": 1, "tftp": 2}  # hh3cCfgOperateProtocol

    BASE = "1.3.6.1.4.1.25506.2.4.1.2.4.1"
    RESULT_BASE = "1.3.6.1.4.1.25506.2.4.1.2.5.1"
    FAIL_REASON_COL = 7

    _STATE_IN_PROGRESS = 1
    _STATE_SUCCESS = 2

    def push(
        self,
        host: str,
        server_ip: str,
        filename: str,
        username: str = "",
        password: str = "",
        protocol: str = "tftp",
    ) -> tuple[bool, str]:
        """Push config sur H3C/Comware.

        Args:
            host: IP/hôte de l'équipement cible.
            server_ip: IP du serveur de dépôt (TFTP/FTP).
            filename: Nom du fichier de configuration à récupérer.
            username: Identifiant optionnel pour le serveur de dépôt.
            password: Mot de passe optionnel pour le serveur de dépôt.
            protocol: Protocole de récupération par l'équipement (``FETCH_PROTOCOLS``).

        Returns:
            Tuple ``(ok, detail)``.
        """
        logger.debug(
            f"H3cComwareDriver.push | host={host} server_ip={server_ip} filename={filename}"
        )
        proto = self.protocol_value(protocol)
        if proto is None:
            logger.debug("H3cComwareDriver.push: retour de _unsupported_protocol()")
            return self._unsupported_protocol(protocol)
        t0 = time.time()
        idx = int(t0) % 2147483647 or 1

        oid_values = [
            (f"{self.BASE}.2.{idx}", "4", "i"),
            (f"{self.BASE}.3.{idx}", str(proto), "i"),
            (f"{self.BASE}.4.{idx}", filename, "s"),
            (f"{self.BASE}.5.{idx}", server_ip, "a"),
        ]
        if username:
            oid_values.append((f"{self.BASE}.6.{idx}", username, "s"))
        if password:
            oid_values.append((f"{self.BASE}.7.{idx}", password, "s"))
        oid_values.append((f"{self.BASE}.9.{idx}", "4", "i"))

        ok, detail = self._set_multiple(host, oid_values)
        if not ok:
            logger.debug("H3cComwareDriver.push: retour False (SET initial échoué)")
            return False, f"SET initial échoué: {detail}"

        logger.debug("H3cComwareDriver.push: retour de _poll_result_table()")
        return self._poll_result_table(host, idx)

    def _poll_result_table(self, host: str, op_idx: int) -> tuple[bool, str]:
        """Sonde la table de résultat jusqu'à succès/échec/timeout.

        Args:
            host: IP/hôte de l'équipement cible.
            op_idx: Index de l'opération à surveiller dans la table de résultat.

        Returns:
            Tuple ``(ok, detail)``.
        """
        logger.debug(f"H3cComwareDriver._poll_result_table | host={host} op_idx={op_idx}")
        session = self._session(host)
        deadline = time.time() + self.poll_timeout

        while time.time() < deadline:
            try:
                rows = session.walk(f"{self.RESULT_BASE}.2")
            except (EasySNMPTimeoutError, EasySNMPError) as exc:
                logger.warning("walk OperateResultTable sur {} échoué : {}", host, exc)
                return False, f"walk OperateResultTable échoué: {exc}"

            match_row = None
            for row in rows:
                try:
                    if int(row.value) == op_idx:
                        match_row = row.oid_index
                        break
                except (ValueError, TypeError) as exc:
                    logger.debug(
                        "ligne OperateResultTable ignorée (OptIndex {!r}) : {}", row.value, exc
                    )
                    continue

            if match_row is not None:
                try:
                    state_var = session.get(f"{self.RESULT_BASE}.4.{match_row}")
                    state = int(state_var.value)
                    if state != self._STATE_IN_PROGRESS:
                        if state == self._STATE_SUCCESS:
                            logger.debug(
                                "H3cComwareDriver._poll_result_table: retour True « succès »"
                            )
                            return True, "succès"
                        detail = f"état terminal non-succès (code={state})"
                        try:
                            reason = session.get(
                                f"{self.RESULT_BASE}.{self.FAIL_REASON_COL}.{match_row}"
                            )
                            detail += f" — raison: {reason.value}"
                        except (
                            EasySNMPError,
                            EasySNMPNoSuchObjectError,
                            EasySNMPNoSuchInstanceError,
                        ) as exc:
                            logger.debug(
                                "raison d'échec illisible pour la ligne {} : {}", match_row, exc
                            )
                        logger.debug("H3cComwareDriver._poll_result_table: retour False")
                        return False, detail
                except (EasySNMPError, ValueError) as exc:
                    logger.warning("lecture de State sur {} échouée : {}", host, exc)
                    return False, f"erreur lecture State: {exc}"

            time.sleep(self.poll_interval)

        logger.debug(
            "H3cComwareDriver._poll_result_table: retour False « timeout en attente du résultat »"
        )
        return False, "timeout en attente du résultat"


class CiscoConfigCopyDriver(SnmpDriver):
    """Driver Cisco IOS / IOS-XE (CISCO-CONFIG-COPY-MIB)."""

    vendor_id = "cisco_ios"
    display_name = "Cisco IOS / IOS-XE"
    # ccCopyProtocol (rcp(3)/scp(4) non proposés).
    FETCH_PROTOCOLS = {"tftp": 1, "ftp": 2, "sftp": 5}

    BASE = "1.3.6.1.4.1.9.9.96.1.1.1.1"
    STATE_OID = f"{BASE}.10"

    _STATE_WAITING = 1
    _STATE_RUNNING = 2
    _STATE_SUCCESSFUL = 3
    _STATE_FAILED = 4

    def push(
        self,
        host: str,
        server_ip: str,
        filename: str,
        username: str = "",
        password: str = "",
        protocol: str = "tftp",
    ) -> tuple[bool, str]:
        """Push config sur Cisco IOS/IOS-XE.

        Args:
            host: IP/hôte de l'équipement cible.
            server_ip: IP du serveur de dépôt (TFTP/FTP).
            filename: Nom du fichier de configuration à récupérer.
            username: Identifiant optionnel pour le serveur de dépôt.
            password: Mot de passe optionnel pour le serveur de dépôt.
            protocol: Protocole de récupération par l'équipement (``FETCH_PROTOCOLS``).

        Returns:
            Tuple ``(ok, detail)``.
        """
        logger.debug(
            f"CiscoConfigCopyDriver.push | host={host} server_ip={server_ip} filename={filename}"
        )
        proto = self.protocol_value(protocol)
        if proto is None:
            logger.debug("CiscoConfigCopyDriver.push: retour de _unsupported_protocol()")
            return self._unsupported_protocol(protocol)
        t0 = time.time()
        idx = int(t0) % 2147483647 or 1

        oid_values = [
            (f"{self.BASE}.2.{idx}", str(proto), "i"),
            (f"{self.BASE}.3.{idx}", "1", "i"),
            (f"{self.BASE}.4.{idx}", "4", "i"),
            (f"{self.BASE}.5.{idx}", server_ip, "a"),
            (f"{self.BASE}.6.{idx}", filename, "s"),
        ]
        if username:
            oid_values.append((f"{self.BASE}.7.{idx}", username, "s"))
        if password:
            oid_values.append((f"{self.BASE}.8.{idx}", password, "s"))
        oid_values.append((f"{self.BASE}.14.{idx}", "4", "i"))

        ok, detail = self._set_multiple(host, oid_values)
        if not ok:
            logger.debug("CiscoConfigCopyDriver.push: retour False (SET initial échoué)")
            return False, f"SET initial échoué: {detail}"

        deadline = time.time() + self.poll_timeout
        while time.time() < deadline:
            ok, state_str = self._get(host, f"{self.STATE_OID}.{idx}")
            if not ok:
                time.sleep(self.poll_interval)
                continue

            try:
                state = int(state_str)
            except ValueError as exc:
                logger.warning("state invalide {!r} : {}", state_str, exc)
                return False, f"state invalide: {state_str}"

            if state == self._STATE_SUCCESSFUL:
                logger.debug("CiscoConfigCopyDriver.push: retour True « succès »")
                return True, "succès"
            elif state == self._STATE_FAILED:
                logger.debug(
                    "CiscoConfigCopyDriver.push: retour False « la copie a échoué côté équipement »"
                )
                return False, "la copie a échoué côté équipement"
            elif state in (self._STATE_WAITING, self._STATE_RUNNING):
                time.sleep(self.poll_interval)
                continue
            else:
                logger.debug("CiscoConfigCopyDriver.push: retour False (état inconnu)")
                return False, f"état inconnu: {state}"

        logger.debug(
            "CiscoConfigCopyDriver.push: retour False « timeout en attente de ccCopyState »"
        )
        return False, "timeout en attente de ccCopyState"


class HuaweiVrpDriver(SnmpDriver):
    """Driver Huawei VRP (HUAWEI-CONFIG-MAN-MIB).

    Portage de ``HuaweiConfigMan`` (ancienne variante EasySNMP autonome,
    ``snmp_bulk_core_easysnmp.py``) vers l'architecture ``SnmpDriver``
    partagée par ce module. Résout l'écart où ``SNMP_VENDORS`` proposait déjà
    ``huawei_vrp`` dans l'UI (`plugin_snmp_push.py`) sans qu'aucun driver ne
    soit enregistré dans ``DRIVERS`` — un push Huawei échouait donc
    systématiquement avec « Constructeur inconnu: huawei_vrp ».

    ⚠️ Codes ``hwCfgOperateState`` (colonne 4 de la table de résultat) :
    vérifiés sur le texte officiel de HUAWEI-CONFIG-MAN-MIB V2.36, mais la
    documentation constructeur elle-même signale que la plage de valeurs a
    été révisée plusieurs fois selon les versions ; à confirmer par un
    ``snmpwalk -Ov`` sur le firmware VRP exact avant mise en prod (au
    minimum ``opInProgress`` = 1 est stable, le code de succès l'est moins).
    """

    vendor_id = "huawei_vrp"
    display_name = "Huawei VRP"
    # hwCfgOperateProtocol ; en sftp(3), authentification par mot de passe seulement.
    FETCH_PROTOCOLS = {"ftp": 1, "tftp": 2, "sftp": 3}

    # hwConfig(1.3.6.1.4.1.2011.6.10).hwConfigManObjects(1)
    BASE = "1.3.6.1.4.1.2011.6.10.1.2.4.1"  # hwCfgOperateEntry (requête)
    RESULT_BASE = "1.3.6.1.4.1.2011.6.10.1.2.5.1"  # hwCfgOperateResultEntry (résultat)
    FAIL_REASON_COL = 8  # hwCfgOperateErrorReason

    _STATE_IN_PROGRESS = 1
    _STATE_SUCCESS = 2

    def push(
        self,
        host: str,
        server_ip: str,
        filename: str,
        username: str = "",
        password: str = "",
        protocol: str = "tftp",
    ) -> tuple[bool, str]:
        """Push config sur Huawei VRP.

        Args:
            host: IP/hôte de l'équipement cible.
            server_ip: IP du serveur de dépôt (TFTP/FTP).
            filename: Nom du fichier de configuration à récupérer.
            username: Identifiant optionnel pour le serveur de dépôt.
            password: Mot de passe optionnel pour le serveur de dépôt.
            protocol: Protocole de récupération par l'équipement (``FETCH_PROTOCOLS``).

        Returns:
            Tuple ``(ok, detail)``.
        """
        logger.debug(
            f"HuaweiVrpDriver.push | host={host} server_ip={server_ip} filename={filename}"
        )
        proto = self.protocol_value(protocol)
        if proto is None:
            logger.debug("HuaweiVrpDriver.push: retour de _unsupported_protocol()")
            return self._unsupported_protocol(protocol)
        t0 = time.time()
        idx = int(t0) % 2147483647 or 1

        oid_values = [
            (f"{self.BASE}.2.{idx}", "4", "i"),  # hwCfgOperateType = net2Running
            (f"{self.BASE}.3.{idx}", str(proto), "i"),  # hwCfgOperateProtocol
            (f"{self.BASE}.4.{idx}", filename, "s"),  # hwCfgOperateFileName
            # hwCfgOperateServerAddress — PAS .11 (hwCfgOperateSourceAddress,
            # adresse source optionnelle du client, pas le serveur cible)
            (f"{self.BASE}.5.{idx}", server_ip, "a"),
        ]
        if username:
            oid_values.append((f"{self.BASE}.6.{idx}", username, "s"))
        if password:
            oid_values.append((f"{self.BASE}.7.{idx}", password, "s"))
        oid_values.append(
            (f"{self.BASE}.9.{idx}", "4", "i")
        )  # hwCfgOperateRowStatus = createAndGo

        ok, detail = self._set_multiple(host, oid_values)
        if not ok:
            logger.debug("HuaweiVrpDriver.push: retour False (SET initial échoué)")
            return False, f"SET initial échoué: {detail}"

        logger.debug("HuaweiVrpDriver.push: retour de _poll_result_table()")
        return self._poll_result_table(host, idx)

    def _poll_result_table(self, host: str, op_idx: int) -> tuple[bool, str]:
        """Sonde la table de résultat jusqu'à succès/échec/timeout.

        Args:
            host: IP/hôte de l'équipement cible.
            op_idx: Index de l'opération à surveiller dans la table de résultat.

        Returns:
            Tuple ``(ok, detail)``.
        """
        logger.debug(f"HuaweiVrpDriver._poll_result_table | host={host} op_idx={op_idx}")
        session = self._session(host)
        deadline = time.time() + self.poll_timeout

        while time.time() < deadline:
            try:
                rows = session.walk(f"{self.RESULT_BASE}.2")  # colonne OptIndex
            except (EasySNMPTimeoutError, EasySNMPError) as exc:
                logger.warning("walk OperateResultTable sur {} échoué : {}", host, exc)
                return False, f"walk OperateResultTable échoué: {exc}"

            match_row = None
            for row in rows:
                try:
                    if int(row.value) == op_idx:
                        match_row = row.oid_index
                        break
                except (ValueError, TypeError) as exc:
                    logger.debug(
                        "ligne OperateResultTable ignorée (OptIndex {!r}) : {}", row.value, exc
                    )
                    continue

            if match_row is not None:
                try:
                    state_var = session.get(f"{self.RESULT_BASE}.4.{match_row}")
                    state = int(state_var.value)
                    if state != self._STATE_IN_PROGRESS:
                        if state == self._STATE_SUCCESS:
                            logger.debug(
                                "HuaweiVrpDriver._poll_result_table: retour True « succès »"
                            )
                            return True, "succès"
                        detail = f"état terminal non-succès (code brut={state})"
                        try:
                            reason = session.get(
                                f"{self.RESULT_BASE}.{self.FAIL_REASON_COL}.{match_row}"
                            )
                            detail += f" — raison: {reason.value}"
                        except (
                            EasySNMPError,
                            EasySNMPNoSuchObjectError,
                            EasySNMPNoSuchInstanceError,
                        ) as exc:
                            logger.debug(
                                "raison d'échec illisible pour la ligne {} : {}", match_row, exc
                            )
                        logger.debug("HuaweiVrpDriver._poll_result_table: retour False")
                        return False, detail
                except (EasySNMPError, ValueError) as exc:
                    logger.warning("lecture de State sur {} échouée : {}", host, exc)
                    return False, f"erreur lecture State: {exc}"

            time.sleep(self.poll_interval)

        logger.debug(
            "HuaweiVrpDriver._poll_result_table: retour False « timeout en attente du résultat »"
        )
        return False, "timeout en attente du résultat"


# Registry des drivers
DRIVERS = {
    "h3c_comware": H3cComwareDriver,
    "cisco_ios": CiscoConfigCopyDriver,
    "huawei_vrp": HuaweiVrpDriver,
}


# ────────────────────────────────────────────────────────────────────────
# Push et exécution par lot
# ────────────────────────────────────────────────────────────────────────


def push_to_device(
    row: InventoryRow,
    profile: SnmpProfile,
    rendered_config: str,
    log_dir: Path,
    server_ip: str = "192.168.1.1",
    transfer: FileTransferConfig | None = None,
) -> PushResult:
    """Push la config vers un équipement.

    Avec *transfer*, *rendered_config* est d'abord déposé sur le serveur de
    dépôt (``upload_config_file``), puis l'équipement est invité à le
    récupérer depuis ``transfer.host`` par ``transfer.effective_fetch_protocol()``.
    Sans *transfer*, comportement historique : rien n'est déposé, l'équipement
    récupère ``config_<ip>.txt`` en TFTP sur *server_ip*.

    Args:
        row: Ligne d'inventaire de l'équipement cible.
        profile: Profil SNMP (constructeur + identifiants) à utiliser.
        rendered_config: Configuration déjà rendue (variables substituées).
        log_dir: Dossier où écrire le journal de cet envoi.
        server_ip: IP du serveur de dépôt (TFTP/FTP/SFTP), sans *transfer*.
        transfer: Serveur de dépôt où déposer le fichier rendu.

    Returns:
        Le ``PushResult`` de l'envoi.
    """
    logger.debug(f"push_to_device | ip={row.ip} vendor={profile.vendor} server_ip={server_ip}")
    t0 = time.time()

    driver_class = DRIVERS.get(profile.vendor)
    if not driver_class:
        logger.debug("push_to_device: retour de PushResult()")
        return PushResult(
            row=row,
            status=STATUS_REJECTED,
            error=f"Constructeur inconnu: {profile.vendor}",
            duration_s=time.time() - t0,
        )

    try:
        driver = driver_class(
            auth=profile.auth,
            port=profile.port,
            timeout=profile.timeout,
            retries=profile.retries,
            poll_interval=profile.poll_interval,
            poll_timeout=profile.poll_timeout,
        )

        if transfer is None:
            filename = f"config_{row.ip}.txt"
            ok, detail = driver.push(row.ip, server_ip, filename)
        else:
            fetch = transfer.effective_fetch_protocol()
            if driver.protocol_value(fetch) is None:
                ok, detail = driver._unsupported_protocol(fetch)
            else:
                safe_ip = re.sub(r"[^A-Za-z0-9_.-]", "_", row.ip)
                filename = f"gcm_push_{safe_ip}_{row.line_no}.cfg"
                upload_config_file(transfer, filename, rendered_config)
                credentials = (
                    ("", "") if fetch == "tftp" else (transfer.username, transfer.password)
                )
                ok, detail = driver.push(
                    row.ip,
                    transfer.host,
                    transfer.device_filename(filename),
                    *credentials,
                    protocol=fetch,
                )

        status = STATUS_OK if ok else STATUS_FAILED

    except TransferError as exc:
        status = STATUS_FAILED
        detail = f"Dépôt du fichier impossible : {exc}"
        logger.warning(f"push_to_device | {row.ip} : {detail}")
    except Exception as exc:
        status = STATUS_FAILED
        detail = str(exc)
        logger.exception(f"Exception durant push vers {row.ip}: {exc}")

    log_path = log_dir / f"{row.line_no}_{row.ip}.log"
    try:
        log_path.write_text(f"{status}: {detail}\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("journal de poussée {} non écrit : {}", log_path, exc)

    logger.debug("push_to_device: retour de PushResult()")
    return PushResult(
        row=row,
        status=status,
        error=detail if status != STATUS_OK else "",
        log_path=str(log_path),
        duration_s=time.time() - t0,
    )


def run_bulk_push(
    rows: list[InventoryRow],
    profiles: SnmpProfileStore,
    template_text: str,
    log_dir: str | Path,
    max_workers: int = 10,
    on_row_start: Callable[[InventoryRow], None] | None = None,
    on_row_done: Callable[[PushResult], None] | None = None,
    cancel_event: threading.Event | None = None,
    server_ip: str = "192.168.1.1",
    transfer: FileTransferConfig | None = None,
) -> list[PushResult]:
    """Pousse la config sur tous les équipements en parallèle.

    Args:
        rows: Lignes d'inventaire à traiter.
        profiles: Magasin de profils SNMP.
        template_text: Contenu du gabarit de configuration.
        log_dir: Dossier où écrire les journaux d'envoi.
        max_workers: Nombre d'envois parallèles.
        on_row_start: Callback appelé avant l'envoi d'une ligne.
        on_row_done: Callback appelé après l'envoi d'une ligne.
        cancel_event: Événement permettant d'annuler l'exécution en cours.
        server_ip: IP du serveur de dépôt (TFTP/FTP/SFTP), sans *transfer*.
        transfer: Serveur de dépôt où déposer chaque fichier rendu (voir
            ``push_to_device``).

    Returns:
        Liste des ``PushResult`` de tous les envois effectués.
    """
    logger.debug(
        f"run_bulk_push | n_rows={len(rows)} max_workers={max_workers} server_ip={server_ip}"
    )
    if cancel_event is None:
        cancel_event = threading.Event()

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for row in rows:
            profile = profiles.get(row.profile_name)
            if not profile:
                result = PushResult(
                    row=row,
                    status=STATUS_REJECTED,
                    error=f"Profil '{row.profile_name}' inexistant",
                    duration_s=0.0,
                )
                results.append(result)
                if on_row_done:
                    on_row_done(result)
                continue

            if on_row_start:
                on_row_start(row)

            rendered = render_template(template_text, row.variables)
            future = executor.submit(
                push_to_device, row, profile, rendered, log_path, server_ip, transfer
            )
            futures[future] = row

        for future in as_completed(futures):
            if cancel_event.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                break

            result = future.result()
            results.append(result)
            if on_row_done:
                on_row_done(result)

    logger.debug("run_bulk_push: retour results")
    return results


# ────────────────────────────────────────────────────────────────────────
# Résumé
# ────────────────────────────────────────────────────────────────────────


def summarize_results(results: list[PushResult]) -> dict[str, int]:
    """Résumé des résultats.

    Args:
        results: Liste des ``PushResult`` à résumer.

    Returns:
        Dictionnaire des compteurs (total/ok/rejected/failed/skipped).
    """
    logger.debug(f"summarize_results | n={len(results)}")
    return {
        "total": len(results),
        "ok": sum(1 for r in results if r.status == STATUS_OK),
        "rejected": sum(1 for r in results if r.status == STATUS_REJECTED),
        "failed": sum(1 for r in results if r.status == STATUS_FAILED),
        "skipped": sum(1 for r in results if r.status == STATUS_SKIPPED),
    }
