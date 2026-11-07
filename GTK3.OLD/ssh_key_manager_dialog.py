# SPDX-License-Identifier: GPL-3.0-or-later
"""Gestionnaire de clés SSH : liste, génération, import, suppression, copie.

Adapté de SSH-Studio (BuddySirJava/SSH-Studio, GPLv3) — GTK4/Adw → GTK3.

Ouvert comme onglet épinglé singleton (clé ``"sshkeys"``) dans nbConsole,
indépendant de toute connexion SSH — au même titre que l'onglet Paramètres
(cf. ``SshPlugin.manage_ssh_keys``). Un mode fenêtre autonome (non-modale)
legacy coexiste tant que les points d'appel sans plugin n'ont pas disparu.

Présente :
- Onglet « Private keys » : clés privées de ~/.ssh/ avec leur .pub associée,
  type, date de création, validité et CA signataire (certificats uniquement)
- Onglet « Orphan Public keys » : clés publiques orphelines
- Onglet « Known Hosts » : entrées de ~/.ssh/known_hosts (local ou distant),
  avec numéro de ligne, tri par colonne, et un bouton de test rapprochant
  ~/.ssh/config (et, si câblé, la liste d'hôtes de GCM) du known_hosts actif
- Onglet « Authorized Keys » : entrées de ~/.ssh/authorized_keys (local ou
  distant), avec tri par colonne
- Barre de cible : bascule entre la machine locale et un compte distant
  (user@host ou root@host) pour les onglets Known Hosts / Authorized Keys
- Barre de CA : désigne/importe/supprime une paire de clés utilisée comme CA
  de signature (``ssh-keygen -s``) ; la génération de clé propose alors de
  signer directement la nouvelle clé avec cette CA
- Barre d'actions contextuelle : Generate, Import, Copy public key, Reveal,
  Delete (onglets clés) ; Refresh, Copy fingerprint, Test, Delete (Known
  Hosts) ; Refresh, Add, Delete (Authorized Keys)

Notes importantes :
- Les paires de clés « classiques » (RSA/Ed25519/...) n'expirent jamais : seule
  une clé signée par une CA (fichier ``*-cert.pub``, cf. ``ssh-keygen -s``)
  porte une fenêtre de validité. La colonne « Validity » n'affiche donc une
  date que pour ces certificats ; les autres clés affichent « — ».
- La « date de création » est une best-effort : on tente ``stat -c %W``
  (birth time, dépend du système de fichiers) puis on retombe sur le ctime
  (date de changement de métadonnées), qui n'est pas garanti être la date de
  génération réelle si la clé a été déplacée/copiée.
- Le mode distant (onglets Known Hosts / Authorized Keys) n'exécute jamais de
  lecture du contenu d'une clé *privée* sur l'hôte distant : uniquement
  ``known_hosts`` et ``authorized_keys``, via des commandes ``ssh`` shell-
  quotées. Cela nécessite que l'authentification SSH vers la cible fonctionne
  déjà (agent / clé déjà autorisée) — ce module ne gère pas la saisie de
  mot de passe.
- Toute action destructive ou modificatrice (suppression, activation/
  désactivation d'une entrée authorized_keys, ajout de clé) demande une
  confirmation explicite ; aucune n'est silencieuse. Lorsque la cible visée
  (locale ou distante) est le compte ``root``, une confirmation
  supplémentaire est demandée.
- La signature par CA (``ssh-keygen -s``) échoue si la clé privée de la CA
  est protégée par une passphrase et exécutée sans terminal interactif ;
  garder la CA sans passphrase, ou la signer manuellement en dehors de GCM
  via l'agent SSH (``ssh-keygen -Us``).
- Pour brancher la liste d'hôtes propre à GCM dans le bouton « Test known
  hosts » (en plus de ~/.ssh/config), passer un callback au constructeur :
  ``SSHKeyManagerDialog(parent, gcm_hosts_provider=lambda: [...])``.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from gettext import gettext as _
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from loguru import logger

from utils import GCMBase, run_dialog_sync

__all__ = ["SSHKeyManagerDialog", "get_default_ssh_key_comment"]

_SSH_DIR = Path.home() / ".ssh"

# Types de clé supportés par ssh-keygen (génération)
_KEY_TYPES: list[str] = [
    "ed25519",
    "rsa",
    "dsa",
    "ecdsa",
    "ed25519-sk",
    "ecdsa-sk",
]
_RSA_SIZES: list[str] = ["512", "1024", "2048", "4096", "8192", "16384"]

# Noms de fichiers exclus du scan (onglets Private/Orphan)
_EXCLUDED_NAMES: frozenset[str] = frozenset(
    {"config", "known_hosts", "authorized_keys", "environment", "identity"}
)

# Types de clé reconnus dans known_hosts / authorized_keys
_AUTH_KEY_TYPES: tuple[str, ...] = (
    "ssh-rsa",
    "ssh-ed25519",
    "ssh-dss",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
)

_CERT_VALID_RE = re.compile(r"Valid: (forever|from (\S+) to (\S+))")
_CERT_CA_RE = re.compile(r"Signing CA:\s+(\S+)\s+(SHA256:\S+)")
_TARGET_RE = re.compile(r"^([^@\s]+)@([^@\s:]+)(?::(\d+))?$")
_SSH_CONFIG_HOST_RE = re.compile(r"^\s*Host\s+(.+)$", re.IGNORECASE)

# Fichier de désignation de la CA de signature (jamais versionné, permissions 600)
_CA_CONFIG_PATH = _SSH_DIR / ".gcm_ca_config.json"

# Index des colonnes du ListStore des onglets Private/Orphan keys
_COL_NAME = 0
_COL_PRIV_PATH = 1
_COL_PUB_PATH = 2
_COL_FINGERPRINT = 3
_COL_PERMS_STR = 4
_COL_PERMS_OK = 5
_COL_PUB_EXISTS = 6
_COL_KEY_TYPE = 7
_COL_CREATED_STR = 8
_COL_VALIDITY_STR = 9
_COL_IS_CERT = 10
_COL_EXPIRED = 11
_COL_SIGNED_BY = 12


def get_default_ssh_key_comment() -> str:
    """Retourne un commentaire par défaut pour ssh-keygen (user@hostname).

    Returns:
        Chaîne au format ``user@hostname``.
    """
    logger.debug("get_default_ssh_key_comment: entrée")
    try:
        username = getpass.getuser()
    except Exception:
        logger.debug("get_default_ssh_key_comment: except Exception")
        username = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    try:
        hostname = socket.gethostname()
    except Exception:
        logger.debug("get_default_ssh_key_comment: except Exception")
        hostname = "localhost"
    logger.debug("get_default_ssh_key_comment: sortie")
    return f"{username}@{hostname}"


def _copy_text_to_clipboard(text: str) -> bool:
    """Copie du texte dans le presse-papiers GTK3 (Gdk.Atom).

    Args:
        text: Texte à copier.

    Returns:
        True si la copie a réussi.
    """
    logger.debug("_copy_text_to_clipboard: entrée")
    try:
        clip = Gtk.Clipboard.get(Gdk.Atom.intern("CLIPBOARD", False))
        clip.set_text(text, -1)
        logger.debug("_copy_text_to_clipboard: sortie")
        return True
    except Exception as exc:
        logger.warning(f"_copy_text_to_clipboard | GTK failed: {exc}")

    for cmd in [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]:
        try:
            res = subprocess.run(cmd, input=text, text=True, capture_output=True, timeout=3)
            if res.returncode == 0:
                logger.debug("_copy_text_to_clipboard: sortie")
                return True
        except Exception:
            logger.debug("_copy_text_to_clipboard: except Exception")
            continue
    logger.debug("_copy_text_to_clipboard: sortie")
    return False


def _get_creation_datetime(path: Path) -> datetime | None:
    """Best-effort de la date de création d'un fichier.

    Tente ``stat -c %W`` (birth time, dépend du système de fichiers) puis
    retombe sur le ctime (date de changement de métadonnées).

    Args:
        path: Fichier à inspecter.

    Returns:
        Un ``datetime`` ou None si indéterminable.
    """
    logger.debug("_get_creation_datetime: entrée")
    try:
        result = subprocess.run(
            ["stat", "-c", "%W", str(path)], capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            if raw.isdigit() and int(raw) > 0:
                logger.debug("_get_creation_datetime: sortie")
                return datetime.fromtimestamp(int(raw))
    except Exception as exc:
        logger.debug(f"_get_creation_datetime | stat -c %W failed | path={path} exc={exc}")
    try:
        logger.debug("_get_creation_datetime: sortie")
        return datetime.fromtimestamp(path.stat().st_ctime)
    except OSError:
        logger.debug("_get_creation_datetime: sortie")
        return None
    logger.debug("_get_creation_datetime: fin")


def _parse_cert_info(cert_path: Path) -> dict[str, str | bool]:
    """Parse la validité et la CA signataire d'un certificat OpenSSH via ssh-keygen -L.

    Args:
        cert_path: Chemin du fichier ``*-cert.pub``.

    Returns:
        Dictionnaire ``{"valid_str": str, "expired": bool, "signed_by": str}``.
    """
    logger.debug("_parse_cert_info: entrée")
    default: dict[str, str | bool] = {"valid_str": "?", "expired": False, "signed_by": "?"}
    try:
        result = subprocess.run(
            ["ssh-keygen", "-L", "-f", str(cert_path)], capture_output=True, text=True, timeout=3
        )
    except Exception as exc:
        logger.debug(f"_parse_cert_info | {cert_path} → {exc}")
        logger.debug("_parse_cert_info: sortie")
        return default
    if result.returncode != 0:
        logger.debug("_parse_cert_info: sortie")
        return default

    ca_match = _CERT_CA_RE.search(result.stdout)
    signed_by = f"{ca_match.group(1)} {ca_match.group(2)[:24]}" if ca_match else _("unknown CA")

    match = _CERT_VALID_RE.search(result.stdout)
    if not match:
        logger.debug("_parse_cert_info: sortie")
        return {"valid_str": "?", "expired": False, "signed_by": signed_by}
    if match.group(1) == "forever":
        logger.debug("_parse_cert_info: sortie")
        return {"valid_str": _("forever"), "expired": False, "signed_by": signed_by}
    to_str = match.group(3)
    expired = False
    try:
        to_dt = datetime.strptime(to_str, "%Y-%m-%dT%H:%M:%S")
        expired = to_dt < datetime.now()
    except ValueError:
        logger.debug("_parse_cert_info: except ValueError")
        pass
    logger.debug("_parse_cert_info: sortie")
    return {
        "valid_str": _("until {end}").format(end=to_str),
        "expired": expired,
        "signed_by": signed_by,
    }


def _fingerprint_from_line(key_line: str) -> str:
    """Calcule le fingerprint SHA256 d'une seule ligne de clé publique.

    Args:
        key_line: Ligne ``keytype base64key`` (sans options ni commentaire).

    Returns:
        Fingerprint abrégé, ou chaîne vide si non calculable.
    """
    logger.debug("_fingerprint_from_line: entrée")
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".pub", delete=False) as tmp:
            tmp.write(key_line.strip() + "\n")
            tmp_path = tmp.name
        result = subprocess.run(
            ["ssh-keygen", "-lf", tmp_path], capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            logger.debug("_fingerprint_from_line: sortie")
            return parts[1][:48] if len(parts) >= 2 else ""
    except Exception as exc:
        logger.debug(f"_fingerprint_from_line | {exc}")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.debug("_fingerprint_from_line: except OSError")
                pass
    logger.debug("_fingerprint_from_line: sortie")
    return ""


def _parse_known_hosts(content: str) -> list[dict[str, object]]:
    """Parse le contenu d'un fichier known_hosts en entrées structurées.

    Args:
        content: Contenu texte du fichier.

    Returns:
        Liste de dictionnaires ``{lineno, raw, marker, host_display, keytype,
        hashed, fingerprint, comment}``.
    """
    logger.debug("_parse_known_hosts: entrée")
    entries: list[dict[str, object]] = []
    for lineno, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        marker = ""
        rest = line
        if rest.startswith("@"):
            marker, _sep, rest = rest.partition(" ")
            rest = rest.strip()
        tokens = rest.split(maxsplit=2)
        if len(tokens) < 3:
            continue
        hosts_field, keytype, remainder = tokens[0], tokens[1], tokens[2]
        parts = remainder.split(maxsplit=1)
        key_b64 = parts[0] if parts else ""
        comment = parts[1] if len(parts) > 1 else ""
        hashed = hosts_field.startswith("|1|")
        host_display = _("(hashed)") if hashed else hosts_field
        fingerprint = _fingerprint_from_line(f"{keytype} {key_b64}") if key_b64 else ""
        entries.append(
            {
                "lineno": lineno,
                "raw": raw,
                "marker": marker,
                "host_display": host_display,
                "keytype": keytype,
                "hashed": hashed,
                "fingerprint": fingerprint,
                "comment": comment,
            }
        )
    logger.debug("_parse_known_hosts: sortie")
    return entries


def _parse_authorized_keys(content: str) -> list[dict[str, object]]:
    """Parse le contenu d'un fichier authorized_keys en entrées structurées.

    Note:
        Analyse volontairement simple (split sur espaces) : ne gère pas les
        options contenant des espaces entre guillemets (ex.
        ``command="foo bar"``). Suffisant pour lister/activer/supprimer des
        entrées ; pour des options complexes, éditer le fichier directement.

    Args:
        content: Contenu texte du fichier.

    Returns:
        Liste de dictionnaires ``{lineno, raw, enabled, options, keytype,
        fingerprint, comment}``.
    """
    logger.debug("_parse_authorized_keys: entrée")
    entries: list[dict[str, object]] = []
    for lineno, raw in enumerate(content.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        enabled = not stripped.startswith("#")
        line = stripped[1:].strip() if not enabled else stripped
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        type_index = next((i for i, tok in enumerate(tokens) if tok in _AUTH_KEY_TYPES), None)
        if type_index is None:
            continue
        options = " ".join(tokens[:type_index])
        keytype = tokens[type_index]
        key_b64 = tokens[type_index + 1] if len(tokens) > type_index + 1 else ""
        comment = " ".join(tokens[type_index + 2 :])
        fingerprint = _fingerprint_from_line(f"{keytype} {key_b64}") if key_b64 else ""
        entries.append(
            {
                "lineno": lineno,
                "raw": raw,
                "enabled": enabled,
                "options": options,
                "keytype": keytype,
                "fingerprint": fingerprint,
                "comment": comment,
            }
        )
    logger.debug("_parse_authorized_keys: sortie")
    return entries


def _parse_ssh_config_hosts(path: Path) -> list[str]:
    """Extrait les alias d'hôtes définis par des directives ``Host`` dans un ssh_config.

    Les patterns génériques ou négatifs (``*``, ``?``, ``!foo``) sont ignorés
    car ``ssh-keygen -F`` n'a de sens que sur un nom d'hôte concret, pas sur
    un motif.

    Args:
        path: Chemin du fichier ssh_config (ex. ``~/.ssh/config``).

    Returns:
        Liste des alias d'hôtes concrets trouvés (sans doublons, ordre conservé).
    """
    logger.debug("_parse_ssh_config_hosts: entrée")
    hosts: list[str] = []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        logger.debug("_parse_ssh_config_hosts: sortie")
        return hosts
    for line in content.splitlines():
        match = _SSH_CONFIG_HOST_RE.match(line)
        if not match:
            continue
        for token in match.group(1).split():
            if any(ch in token for ch in "*?!"):
                continue
            if token not in hosts:
                hosts.append(token)
    logger.debug("_parse_ssh_config_hosts: sortie")
    return hosts


@dataclass
class SSHTargetSpec:
    """Décrit une cible SSH distante (compte + hôte + port)."""

    user: str
    host: str
    port: int = 22

    @property
    def display(self) -> str:
        """Représentation lisible ``user@host`` ou ``user@host:port``."""
        logger.debug("SSHTargetSpec.display: entrée")
        suffix = f":{self.port}" if self.port != 22 else ""
        logger.debug("SSHTargetSpec.display: sortie")
        return f"{self.user}@{self.host}{suffix}"

    def ssh_base_cmd(self) -> list[str]:
        """Construit la commande ssh de base pour cette cible.

        Returns:
            Liste d'arguments prête à être complétée par une commande distante.
        """
        logger.debug("SSHTargetSpec.ssh_base_cmd: entrée")
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6"]
        if self.port != 22:
            cmd += ["-p", str(self.port)]
        cmd.append(f"{self.user}@{self.host}")
        logger.debug("SSHTargetSpec.ssh_base_cmd: sortie")
        return cmd


class _FSAdapter:
    """Interface commune d'accès à un répertoire ~/.ssh, local ou distant."""

    def is_remote(self) -> bool:
        """Indique si l'adaptateur opère sur un hôte distant."""
        raise NotImplementedError

    def label(self) -> str:
        """Nom lisible de la cible (pour l'affichage)."""
        raise NotImplementedError

    def target_user(self) -> str:
        """Nom du compte système visé (utilisé pour le garde-fou root)."""
        raise NotImplementedError

    def read_text(self, filename: str) -> str | None:
        """Lit le contenu d'un fichier de ~/.ssh/, ou None si absent/illisible."""
        raise NotImplementedError

    def write_text(self, filename: str, content: str, mode: str = "600") -> bool:
        """Écrit (remplace) le contenu d'un fichier de ~/.ssh/."""
        raise NotImplementedError

    def test_connection(self) -> tuple[bool, str]:
        """Vérifie que la cible est joignable. Retourne ``(ok, message_erreur)``."""
        return True, ""


class _LocalFS(_FSAdapter):
    """Adaptateur pour le ~/.ssh/ de la machine locale."""

    def __init__(self, ssh_dir: Path) -> None:
        """Initialise l'adaptateur local.

        Args:
            ssh_dir: Répertoire ~/.ssh/ local.
        """
        logger.debug("_LocalFS.__init__: entrée")
        self._dir = ssh_dir
        logger.debug("_LocalFS.__init__: fin")

    def is_remote(self) -> bool:
        """Toujours False pour la machine locale."""
        return False

    def label(self) -> str:
        """Retourne le libellé de la cible locale."""
        return _("local machine")

    def target_user(self) -> str:
        """Retourne l'utilisateur système exécutant ce processus."""
        logger.debug("_LocalFS.target_user: entrée")
        try:
            logger.debug("_LocalFS.target_user: sortie")
            return getpass.getuser()
        except Exception:
            logger.debug("_LocalFS.target_user: sortie")
            return os.environ.get("USER") or os.environ.get("USERNAME") or ""
        logger.debug("_LocalFS.target_user: fin")

    def read_text(self, filename: str) -> str | None:
        """Lit un fichier local de ~/.ssh/."""
        logger.debug("_LocalFS.read_text: entrée")
        path = self._dir / filename
        try:
            logger.debug("_LocalFS.read_text: sortie")
            return path.read_text(encoding="utf-8")
        except OSError:
            logger.debug("_LocalFS.read_text: sortie")
            return None
        logger.debug("_LocalFS.read_text: fin")

    def write_text(self, filename: str, content: str, mode: str = "600") -> bool:
        """Écrit un fichier local de ~/.ssh/ avec les permissions données."""
        logger.debug("_LocalFS.write_text: entrée")
        path = self._dir / filename
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            path.chmod(int(mode, 8))
            logger.debug("_LocalFS.write_text: sortie")
            return True
        except OSError as exc:
            logger.error(f"_LocalFS.write_text | path={path} exc={exc}")
            logger.debug("_LocalFS.write_text: sortie")
            return False
        logger.debug("_LocalFS.write_text: fin")


class _RemoteFS(_FSAdapter):
    """Adaptateur pour le ~/.ssh/ d'un compte distant, via la commande ssh.

    Sécurité : ne lit/écrit jamais de clé *privée* — uniquement known_hosts
    et authorized_keys, car transférer une clé privée sur le réseau pour un
    simple affichage serait une mauvaise pratique de sécurité.
    """

    def __init__(self, spec: SSHTargetSpec) -> None:
        """Initialise l'adaptateur distant.

        Args:
            spec: Cible SSH (user, host, port).
        """
        logger.debug("_RemoteFS.__init__: entrée")
        self._spec = spec
        logger.debug("_RemoteFS.__init__: fin")

    def is_remote(self) -> bool:
        """Toujours True pour une cible distante."""
        return True

    def label(self) -> str:
        """Retourne le libellé ``user@host`` de la cible."""
        return self._spec.display

    def target_user(self) -> str:
        """Retourne le nom du compte distant ciblé."""
        return self._spec.user

    def _ssh(self, remote_cmd: str, timeout: int = 10) -> subprocess.CompletedProcess:
        """Exécute une commande shell sur la cible distante via ssh.

        Args:
            remote_cmd: Commande shell à exécuter côté distant.
            timeout: Délai maximal en secondes.

        Returns:
            Résultat de subprocess.run.
        """
        logger.debug("_RemoteFS._ssh: entrée")
        cmd = self._spec.ssh_base_cmd() + [remote_cmd]
        logger.debug("_RemoteFS._ssh: sortie")
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def read_text(self, filename: str) -> str | None:
        """Lit un fichier de ~/.ssh/ sur la cible distante."""
        logger.debug("_RemoteFS.read_text: entrée")
        remote_cmd = f"cat ~/.ssh/{shlex.quote(filename)} 2>/dev/null"
        try:
            result = self._ssh(remote_cmd)
        except Exception as exc:
            logger.error(f"_RemoteFS.read_text | file={filename} exc={exc}")
            logger.debug("_RemoteFS.read_text: sortie")
            return None
        logger.debug("_RemoteFS.read_text: sortie")
        return result.stdout if result.returncode == 0 else None

    def write_text(self, filename: str, content: str, mode: str = "600") -> bool:
        """Écrit (remplace) un fichier de ~/.ssh/ sur la cible distante.

        Écriture atomique via fichier temporaire + mv, contenu transmis en
        base64 pour éviter les problèmes de quoting shell.
        """
        logger.debug("_RemoteFS.write_text: entrée")
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        remote_cmd = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f'tmp=$(mktemp) && echo {shlex.quote(b64)} | base64 -d > "$tmp" && '
            f'chmod {mode} "$tmp" && mv "$tmp" ~/.ssh/{shlex.quote(filename)}'
        )
        try:
            result = self._ssh(remote_cmd)
        except Exception as exc:
            logger.error(f"_RemoteFS.write_text | file={filename} exc={exc}")
            logger.debug("_RemoteFS.write_text: sortie")
            return False
        if result.returncode != 0:
            logger.warning(f"_RemoteFS.write_text | failed | stderr={result.stderr.strip()}")
        logger.debug("_RemoteFS.write_text: sortie")
        return result.returncode == 0

    def test_connection(self) -> tuple[bool, str]:
        """Vérifie que la cible distante est joignable en SSH."""
        logger.debug("_RemoteFS.test_connection: entrée")
        try:
            result = self._ssh("echo __OK__", timeout=8)
        except subprocess.TimeoutExpired:
            logger.debug("_RemoteFS.test_connection: sortie")
            return False, _("Connection timed out")
        except Exception as exc:
            logger.debug("_RemoteFS.test_connection: sortie")
            return False, str(exc)
        if result.returncode == 0 and "__OK__" in result.stdout:
            logger.debug("_RemoteFS.test_connection: sortie")
            return True, ""
        logger.debug("_RemoteFS.test_connection: sortie")
        return False, result.stderr.strip() or _("SSH connection failed")


class _GenerateKeyDialog(Gtk.Dialog):
    """Dialogue de saisie des paramètres ssh-keygen.

    Interne à ce module — utilisé par SSHKeyManagerDialog._on_generate.
    """

    def __init__(self, parent: Gtk.Window, ca_available: bool = False, ca_name: str = "") -> None:
        """Initialise le formulaire de génération de clé.

        Args:
            parent: Fenêtre parente pour centrage modal.
            ca_available: Si True, affiche l'option de signature par la CA désignée.
            ca_name: Nom de la CA désignée, affiché dans le libellé de la case à cocher.
        """
        logger.debug("_GenerateKeyDialog.__init__: entrée")
        super().__init__(
            title=_("Generate SSH Key"),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(440, 0)
        self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        self._btn_gen = self.add_button(_("⚙ Generate"), Gtk.ResponseType.OK)
        self._btn_gen.get_style_context().add_class("suggested-action")
        self._ca_available = ca_available
        self._ca_name = ca_name
        self._build_ui()
        self.show_all()
        logger.debug("_GenerateKeyDialog.__init__: fin")

    def _build_ui(self) -> None:
        """Construit la grille de formulaire."""
        logger.debug("_GenerateKeyDialog._build_ui: entrée")
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(8)
        grid.set_margin_start(16)
        grid.set_margin_end(16)
        grid.set_margin_top(12)
        grid.set_margin_bottom(12)

        lbl_type = Gtk.Label(label=_("Key type:"), xalign=1.0)
        self._cmb_type = Gtk.ComboBoxText()
        for t in _KEY_TYPES:
            self._cmb_type.append_text(t)
        self._cmb_type.set_active(0)
        self._cmb_type.connect("changed", self._on_type_changed)
        grid.attach(lbl_type, 0, 0, 1, 1)
        grid.attach(self._cmb_type, 1, 0, 1, 1)

        self._lbl_size = Gtk.Label(label=_("RSA size:"), xalign=1.0)
        self._cmb_size = Gtk.ComboBoxText()
        for s in _RSA_SIZES:
            self._cmb_size.append_text(s)
        self._cmb_size.set_active(3)
        grid.attach(self._lbl_size, 0, 1, 1, 1)
        grid.attach(self._cmb_size, 1, 1, 1, 1)
        self._lbl_size.hide()
        self._cmb_size.hide()

        lbl_name = Gtk.Label(label=_("File name:"), xalign=1.0)
        self._entry_name = Gtk.Entry()
        self._entry_name.set_text("id_ed25519")
        self._entry_name.set_hexpand(True)
        self._entry_name.set_tooltip_text(_("Saved in ~/.ssh/ — leave blank for default name"))
        grid.attach(lbl_name, 0, 2, 1, 1)
        grid.attach(self._entry_name, 1, 2, 1, 1)

        lbl_comment = Gtk.Label(label=_("Comment:"), xalign=1.0)
        self._entry_comment = Gtk.Entry()
        self._entry_comment.set_text(get_default_ssh_key_comment())
        self._entry_comment.set_hexpand(True)
        grid.attach(lbl_comment, 0, 3, 1, 1)
        grid.attach(self._entry_comment, 1, 3, 1, 1)

        lbl_pass = Gtk.Label(label=_("Passphrase:"), xalign=1.0)
        self._entry_pass = Gtk.Entry()
        self._entry_pass.set_visibility(False)
        self._entry_pass.set_placeholder_text(_("(empty = no passphrase)"))
        self._entry_pass.set_hexpand(True)
        grid.attach(lbl_pass, 0, 4, 1, 1)
        grid.attach(self._entry_pass, 1, 4, 1, 1)

        lbl_info = Gtk.Label()
        lbl_info.set_markup(
            "<small><i>"
            + _("Keys are saved in <b>~/.ssh/</b> with permissions 600.")
            + "</i></small>"
        )
        lbl_info.set_xalign(0.0)
        lbl_info.set_line_wrap(True)
        grid.attach(lbl_info, 0, 5, 2, 1)

        row = 6
        self._chk_sign = Gtk.CheckButton()
        if self._ca_available:
            self._chk_sign.set_label(
                _("Sign with designated CA ({name})").format(name=self._ca_name)
            )
            self._chk_sign.connect("toggled", self._on_sign_toggled)
            grid.attach(self._chk_sign, 0, row, 2, 1)
            row += 1

            lbl_identity = Gtk.Label(label=_("Certificate identity:"), xalign=1.0)
            self._entry_identity = Gtk.Entry()
            self._entry_identity.set_text(get_default_ssh_key_comment())
            self._entry_identity.set_hexpand(True)
            grid.attach(lbl_identity, 0, row, 1, 1)
            grid.attach(self._entry_identity, 1, row, 1, 1)
            row += 1

            lbl_principals = Gtk.Label(label=_("Principals (comma-separated):"), xalign=1.0)
            self._entry_principals = Gtk.Entry()
            self._entry_principals.set_text(get_default_ssh_key_comment().split("@", 1)[0])
            self._entry_principals.set_hexpand(True)
            grid.attach(lbl_principals, 0, row, 1, 1)
            grid.attach(self._entry_principals, 1, row, 1, 1)
            row += 1

            lbl_validity = Gtk.Label(label=_("Validity:"), xalign=1.0)
            self._entry_validity = Gtk.Entry()
            self._entry_validity.set_text("+52w")
            self._entry_validity.set_tooltip_text(
                _("ssh-keygen -V syntax, e.g. +52w (1 year), +30d, or 'always:forever'")
            )
            self._entry_validity.set_hexpand(True)
            grid.attach(lbl_validity, 0, row, 1, 1)
            grid.attach(self._entry_validity, 1, row, 1, 1)
            row += 1

            self._sign_widgets = (
                lbl_identity,
                self._entry_identity,
                lbl_principals,
                self._entry_principals,
                lbl_validity,
                self._entry_validity,
            )
            self._on_sign_toggled(self._chk_sign)
        else:
            self._sign_widgets = ()

        self.get_content_area().pack_start(grid, True, True, 0)
        logger.debug("_GenerateKeyDialog._build_ui: fin")

    def _on_sign_toggled(self, checkbutton: Gtk.CheckButton) -> None:
        """Affiche/masque les champs de signature selon l'état de la case à cocher.

        Args:
            checkbutton: La case à cocher « Sign with designated CA ».
        """
        logger.debug("_GenerateKeyDialog._on_sign_toggled: entrée")
        active = checkbutton.get_active()
        for widget in self._sign_widgets:
            widget.set_visible(active)
        logger.debug("_GenerateKeyDialog._on_sign_toggled: fin")

    def _on_type_changed(self, _cmb: Gtk.ComboBoxText) -> None:
        """Affiche ou masque le champ de taille RSA selon le type sélectionné."""
        logger.debug("_GenerateKeyDialog._on_type_changed: entrée")
        key_type = self._cmb_type.get_active_text() or "ed25519"
        visible = key_type == "rsa"
        self._lbl_size.set_visible(visible)
        self._cmb_size.set_visible(visible)
        self._entry_name.set_text(f"id_{key_type}")
        logger.debug("_GenerateKeyDialog._on_type_changed: fin")

    def get_options(self) -> dict[str, str | int | bool]:
        """Retourne les options saisies dans le formulaire.

        Returns:
            Dictionnaire avec les clés ``type``, ``size``, ``name``, ``comment``,
            ``passphrase`` et, si la CA est disponible, ``sign_with_ca``,
            ``ca_identity``, ``ca_principals``, ``ca_validity``.
        """
        logger.debug("_GenerateKeyDialog.get_options: entrée")
        key_type = self._cmb_type.get_active_text() or "ed25519"
        size_str = self._cmb_size.get_active_text() or "4096"
        opts: dict[str, str | int | bool] = {
            "type": key_type,
            "size": int(size_str) if key_type == "rsa" else 0,
            "name": self._entry_name.get_text().strip(),
            "comment": self._entry_comment.get_text().strip() or get_default_ssh_key_comment(),
            "passphrase": self._entry_pass.get_text(),
            "sign_with_ca": False,
        }
        if self._ca_available and self._chk_sign.get_active():
            opts["sign_with_ca"] = True
            opts["ca_identity"] = (
                self._entry_identity.get_text().strip() or get_default_ssh_key_comment()
            )
            opts["ca_principals"] = self._entry_principals.get_text().strip() or getpass.getuser()
            opts["ca_validity"] = self._entry_validity.get_text().strip() or "+52w"
        logger.debug("_GenerateKeyDialog.get_options: sortie")
        return opts


class _AddAuthorizedKeyDialog(Gtk.Dialog):
    """Dialogue de saisie d'une nouvelle ligne authorized_keys."""

    def __init__(self, parent: Gtk.Window) -> None:
        """Initialise le dialogue.

        Args:
            parent: Fenêtre parente.
        """
        logger.debug("_AddAuthorizedKeyDialog.__init__: entrée")
        super().__init__(
            title=_("Add Authorized Key"),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(540, 220)
        self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        btn_add = self.add_button(_("_Add"), Gtk.ResponseType.OK)
        btn_add.get_style_context().add_class("suggested-action")

        box = self.get_content_area()
        box.set_spacing(6)
        box.set_border_width(12)
        lbl = Gtk.Label(
            label=_("Paste the full public key line (options, type, base64 key, comment):"),
            xalign=0.0,
        )
        lbl.set_line_wrap(True)
        box.pack_start(lbl, False, False, 0)

        self._textview = Gtk.TextView()
        self._textview.set_wrap_mode(Gtk.WrapMode.CHAR)
        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(100)
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.add(self._textview)
        box.pack_start(scroller, True, True, 0)
        self.show_all()
        logger.debug("_AddAuthorizedKeyDialog.__init__: fin")

    def get_key_line(self) -> str:
        """Retourne le texte saisi.

        Returns:
            Le contenu du champ de texte, nettoyé des espaces superflus.
        """
        logger.debug("_AddAuthorizedKeyDialog.get_key_line: entrée")
        buf = self._textview.get_buffer()
        start, end = buf.get_bounds()
        logger.debug("_AddAuthorizedKeyDialog.get_key_line: sortie")
        return buf.get_text(start, end, True).strip()


class _TestKnownHostsDialog(Gtk.Dialog):
    """Fenêtre de résultats du test « known hosts » (ssh_config / GCM vs known_hosts)."""

    def __init__(
        self, parent: Gtk.Window, target_label: str, results: list[tuple[str, bool, str]]
    ) -> None:
        """Construit et affiche les résultats du test.

        Args:
            parent: Fenêtre parente.
            target_label: Libellé de la cible testée (ex. « local machine »).
            results: Liste de tuples ``(host, found, info)`` où ``info`` est la
                ligne known_hosts correspondante si trouvée, sinon vide.
        """
        logger.debug("_TestKnownHostsDialog.__init__: entrée")
        found_count = sum(1 for _h, found, _i in results if found)
        super().__init__(
            title=_("Known Hosts Test — {n}/{total} found on {target}").format(
                n=found_count, total=len(results), target=target_label
            ),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(720, 420)
        self.add_button(_("_Close"), Gtk.ResponseType.CLOSE)

        store = Gtk.ListStore(str, bool, str)  # host, found, info
        for host, found, info in results:
            store.append([host, found, info])

        tree = Gtk.TreeView(model=store)
        tree.set_headers_clickable(True)

        col_host = Gtk.TreeViewColumn(_("Host"), Gtk.CellRendererText(), text=0)
        col_host.set_expand(True)
        col_host.set_sort_column_id(0)
        tree.append_column(col_host)

        renderer_found = Gtk.CellRendererText()
        col_found = Gtk.TreeViewColumn(_("Found"))
        col_found.pack_start(renderer_found, True)

        def _found_data_func(_col, cell, model, it, _data):
            logger.debug("_found_data_func: entrée")
            found = model[it][1]
            cell.set_property("text", "✓" if found else "✗")
            cell.set_property("foreground", "#2d9e44" if found else "#c0392b")
            logger.debug("_found_data_func: fin")

        col_found.set_cell_data_func(renderer_found, _found_data_func)
        col_found.set_sort_column_id(1)
        tree.append_column(col_found)

        col_info = Gtk.TreeViewColumn(
            _("Info (matching known_hosts line)"), Gtk.CellRendererText(), text=2
        )
        col_info.set_expand(True)
        col_info.set_sort_column_id(2)
        tree.append_column(col_info)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.IN)
        sw.add(tree)

        box = self.get_content_area()
        box.set_border_width(8)
        box.pack_start(sw, True, True, 0)
        self.show_all()
        logger.debug("_TestKnownHostsDialog.__init__: fin")


class _RenameKeyDialog(Gtk.Dialog):
    """Dialogue de renommage d'un fichier de clé (+ .pub / certificat associés)."""

    def __init__(self, parent: Gtk.Window, current_name: str) -> None:
        """Construit le formulaire de renommage.

        Args:
            parent: Fenêtre parente.
            current_name: Nom actuel de la clé, pré-rempli dans le champ.
        """
        logger.debug("_RenameKeyDialog.__init__: entrée")
        super().__init__(
            title=_("Rename Key"),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(380, 0)
        self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        btn_ok = self.add_button(_("_Rename"), Gtk.ResponseType.OK)
        btn_ok.get_style_context().add_class("suggested-action")
        btn_ok.set_can_default(True)

        box = self.get_content_area()
        box.set_spacing(6)
        box.set_border_width(12)
        lbl = Gtk.Label(label=_("New name for {name}:").format(name=current_name), xalign=0.0)
        box.pack_start(lbl, False, False, 0)
        self._entry = Gtk.Entry()
        self._entry.set_text(current_name)
        self._entry.set_activates_default(True)
        box.pack_start(self._entry, False, False, 0)
        btn_ok.grab_default()
        self.show_all()
        logger.debug("_RenameKeyDialog.__init__: fin")

    def get_new_name(self) -> str:
        """Retourne le nouveau nom saisi.

        Returns:
            Le texte du champ, nettoyé des espaces superflus.
        """
        return self._entry.get_text().strip()


class _ChangePassphraseDialog(Gtk.Dialog):
    """Dialogue de changement de passphrase (ssh-keygen -p)."""

    def __init__(self, parent: Gtk.Window, key_name: str) -> None:
        """Construit le formulaire de changement de passphrase.

        Args:
            parent: Fenêtre parente.
            key_name: Nom de la clé concernée, affiché dans le titre.
        """
        logger.debug("_ChangePassphraseDialog.__init__: entrée")
        super().__init__(
            title=_("Change Passphrase — {name}").format(name=key_name),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(380, 0)
        self.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        btn_ok = self.add_button(_("_Change"), Gtk.ResponseType.OK)
        btn_ok.get_style_context().add_class("suggested-action")

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(8)
        grid.set_margin_start(16)
        grid.set_margin_end(16)
        grid.set_margin_top(12)
        grid.set_margin_bottom(12)

        lbl_old = Gtk.Label(label=_("Current passphrase:"), xalign=1.0)
        self._entry_old = Gtk.Entry()
        self._entry_old.set_visibility(False)
        self._entry_old.set_placeholder_text(_("(empty if none)"))
        self._entry_old.set_hexpand(True)
        grid.attach(lbl_old, 0, 0, 1, 1)
        grid.attach(self._entry_old, 1, 0, 1, 1)

        lbl_new = Gtk.Label(label=_("New passphrase:"), xalign=1.0)
        self._entry_new = Gtk.Entry()
        self._entry_new.set_visibility(False)
        self._entry_new.set_placeholder_text(_("(empty = remove passphrase)"))
        self._entry_new.set_hexpand(True)
        grid.attach(lbl_new, 0, 1, 1, 1)
        grid.attach(self._entry_new, 1, 1, 1, 1)

        lbl_confirm = Gtk.Label(label=_("Confirm new passphrase:"), xalign=1.0)
        self._entry_confirm = Gtk.Entry()
        self._entry_confirm.set_visibility(False)
        self._entry_confirm.set_hexpand(True)
        grid.attach(lbl_confirm, 0, 2, 1, 1)
        grid.attach(self._entry_confirm, 1, 2, 1, 1)

        self.get_content_area().pack_start(grid, True, True, 0)
        self.show_all()
        logger.debug("_ChangePassphraseDialog.__init__: fin")

    def get_values(self) -> tuple[str, str, str]:
        """Retourne les valeurs saisies.

        Returns:
            Tuple ``(old_passphrase, new_passphrase, confirm_passphrase)``.
        """
        return (
            self._entry_old.get_text(),
            self._entry_new.get_text(),
            self._entry_confirm.get_text(),
        )


class _ViewPublicKeyDialog(Gtk.Dialog):
    """Fenêtre non-bloquante affichant le contenu complet d'une clé publique."""

    def __init__(self, parent: Gtk.Window, key_name: str, content: str) -> None:
        """Construit la fenêtre d'affichage.

        Args:
            parent: Fenêtre parente.
            key_name: Nom de la clé, affiché dans le titre.
            content: Contenu texte complet de la clé publique.
        """
        logger.debug("_ViewPublicKeyDialog.__init__: entrée")
        super().__init__(
            title=_("Public Key — {name}").format(name=key_name),
            transient_for=parent,
            destroy_with_parent=True,
        )
        self.set_default_size(640, 220)
        self.add_button(_("_Close"), Gtk.ResponseType.CLOSE)
        self.connect("response", lambda _d, _r: self.destroy())

        box = self.get_content_area()
        box.set_border_width(8)
        box.set_spacing(6)

        textview = Gtk.TextView()
        textview.set_editable(False)
        textview.set_wrap_mode(Gtk.WrapMode.CHAR)
        textview.get_buffer().set_text(content)
        scroller = Gtk.ScrolledWindow()
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.add(textview)
        box.pack_start(scroller, True, True, 0)

        btn_copy = Gtk.Button(label=_("⎘ Copy to clipboard"))
        btn_copy.connect("clicked", lambda _w: _copy_text_to_clipboard(content))
        box.pack_start(btn_copy, False, False, 0)

        self.show_all()
        logger.debug("_ViewPublicKeyDialog.__init__: fin")


class SSHKeyManagerDialog(GCMBase, Gtk.Window):
    """Gestionnaire de clés SSH — clés locales, known_hosts, authorized_keys.

    Écran 100% Python (pas de Glade) — hérite directement de ``Gtk.Window``
    (pas ``Gtk.Dialog`` : pas de zone de boutons OK/Annuler standard, juste
    un bouton « Close »), en plus de ``GCMBase`` (mode ``path=None``, cf.
    ``GCMBase.__init__``) qui lui fournit ``is_tab``/``tab_key``/
    ``request_close()``/``on_tab_will_close()``, nécessaires pour être
    ouvert comme onglet épinglé **singleton** (clé ``"sshkeys"``,
    indépendant de toute connexion SSH — au même titre que ``"settings"``)
    via ``Wmain.open_management_tab``, propriété du plugin SSH (cf.
    ``SshPlugin.manage_ssh_keys``).

    Layout :
    - Barre de cible : machine locale ou compte distant (user@host)
    - Notebook à 4 onglets : Private Keys / Orphan Public keys / Known Hosts /
      Authorized Keys
    - Barre d'actions contextuelle selon l'onglet actif
    - Barre de statut pour les notifications temporaires

    Deux modes d'usage coexistent tant que le mode legacy (sans plugin)
    n'a pas disparu :

    Mode fenêtre autonome (legacy, ``is_tab`` reste ``False``) ::

        dlg = SSHKeyManagerDialog(parent=self.window)
        dlg.show_all()

    Mode onglet épinglé (``Wmain.open_management_tab`` positionne
    ``is_tab``/``tab_key`` après construction, cf. ``SshPlugin.manage_ssh_keys``) ::

        wMain.open_management_tab(
            "sshkeys",
            _("SSH Key Manager"),
            lambda: SSHKeyManagerDialog(parent=wMain.window, show=False),
        )
    """

    def __init__(
        self,
        parent: Gtk.Window,
        gcm_hosts_provider: Callable[[], list[str]] | None = None,
        show: bool = True,
    ) -> None:
        """Initialise le gestionnaire et charge la liste des clés.

        Args:
            parent: Fenêtre parente GCM (pour centrage en mode fenêtre
                autonome ; sans effet en mode onglet, cf. ``_top_window()``).
            gcm_hosts_provider: Callback optionnel retournant les alias/hôtes
                connus de GCM (ex. ``lambda: [c.hostname for c in self.connections]``).
                Utilisé par le bouton « Test known hosts » en complément de
                ``~/.ssh/config``. Si None, seule ``~/.ssh/config`` est utilisée.
            show (bool): Sans effet direct ici (ce gestionnaire n'a jamais
                été affiché automatiquement par son propre ``__init__`` —
                c'est l'appelant qui décide via ``show_all()`` ou
                ``embed_dialog_content``). Conservé pour cohérence d'API
                avec les autres écrans de gestion.
        """
        logger.debug("SSHKeyManagerDialog.__init__: entrée")
        Gtk.Window.__init__(self, title=_("SSH Key Manager"))
        # Mode 100% Python (pas de Glade) : initialise juste is_tab/tab_key,
        # ne touche pas a la construction GTK deja faite ci-dessus (cf.
        # GCMBase.__init__, branche path=None).
        GCMBase.__init__(self, path=None, parent=parent, show=show)
        # L'instance EST deja le Gtk.Window (heritage direct) : main_widget
        # pointe donc vers self, exactement comme pour SshConfigEditorDialog.
        self.main_widget = self
        self.set_transient_for(parent)
        self.set_default_size(1180, 640)
        self.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

        self._ssh_dir = _SSH_DIR
        self._local_fs = _LocalFS(self._ssh_dir)
        self._remote_fs: _RemoteFS | None = None
        self._gcm_hosts_provider = gcm_hosts_provider

        self._ca_priv_path: Path | None = None
        self._ca_pub_path: Path | None = None
        self._load_ca_config()

        self._build_ui()
        self._load_keys()
        self._load_known_hosts()
        self._load_authorized_keys()
        self._setup_keyboard()
        logger.info(f"SSHKeyManagerDialog | ouvert ssh_dir={self._ssh_dir}")
        logger.debug("SSHKeyManagerDialog.__init__: fin")

    def _top_window(self) -> Gtk.Window:
        """Retourne la fenêtre de haut niveau à utiliser comme parent pour
        les sous-dialogues internes (_GenerateKeyDialog, _RenameKeyDialog,
        _ChangePassphraseDialog, _ViewPublicKeyDialog, _TestKnownHostsDialog,
        _AddAuthorizedKeyDialog).

        Piège identique à celui rencontré sur ``Whost``/``SshConfigEditorDialog``
        (cf. leurs ``_top_window()``) : en mode onglet épinglé, ``self`` (ce
        gestionnaire) n'est jamais affiché/réalisé comme fenêtre top-level —
        l'utiliser comme ``transient_for``/parent donnerait un rattachement à
        une fenêtre invisible. On utilise alors la fenêtre principale, dont
        l'onglet fait désormais partie. En mode fenêtre autonome, ``self``
        est bien affiché et reste la fenêtre parente appropriée.

        Returns:
            Gtk.Window: Fenêtre parente appropriée.
        """
        logger.debug("SSHKeyManagerDialog._top_window: entrée")
        if self.is_tab:
            from gnome_connection_manager import wMain  # noqa: PLC0415

            logger.debug("SSHKeyManagerDialog._top_window: sortie")
            return wMain.window
        logger.debug("SSHKeyManagerDialog._top_window: sortie")
        return self

    # ------------------------------------------------------------------
    # Construction UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construit la fenêtre principale : cible + notebook + actions + statut."""
        logger.debug("SSHKeyManagerDialog._build_ui: entrée")
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        vbox.pack_start(self._build_target_bar(), False, False, 0)
        vbox.pack_start(Gtk.Separator(), False, False, 0)
        vbox.pack_start(self._build_ca_bar(), False, False, 0)
        vbox.pack_start(Gtk.Separator(), False, False, 0)

        self._notebook = Gtk.Notebook()
        self._notebook.set_margin_start(8)
        self._notebook.set_margin_end(8)
        self._notebook.set_margin_top(8)
        self._notebook.connect("switch-page", self._on_page_switched)
        vbox.pack_start(self._notebook, True, True, 0)

        self._priv_store, priv_tab = self._build_key_tab(show_pub_status=True)
        self._pub_store, pub_tab = self._build_key_tab(show_pub_status=False)
        self._kh_store, self._kh_tree, kh_tab = self._build_known_hosts_tab()
        self._ak_store, self._ak_tree, ak_tab = self._build_authorized_keys_tab()

        self._notebook.append_page(priv_tab, Gtk.Label(label=_("Private keys")))
        self._notebook.append_page(pub_tab, Gtk.Label(label=_("Orphan Public keys")))
        self._notebook.append_page(kh_tab, Gtk.Label(label=_("Known Hosts")))
        self._notebook.append_page(ak_tab, Gtk.Label(label=_("Authorized Keys")))

        self._priv_tree: Gtk.TreeView = priv_tab.get_children()[0]
        self._pub_tree: Gtk.TreeView = pub_tab.get_children()[0]

        for tree in (self._priv_tree, self._pub_tree):
            tree.get_selection().connect("changed", self._on_selection_changed)

        vbox.pack_start(Gtk.Separator(), False, False, 0)

        action_bar_scroller = Gtk.ScrolledWindow()
        action_bar_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        action_bar_scroller.set_shadow_type(Gtk.ShadowType.NONE)
        action_bar_scroller.add(self._build_action_bar())
        vbox.pack_start(action_bar_scroller, False, False, 0)

        self._status_bar = Gtk.Statusbar()
        self._status_ctx = self._status_bar.get_context_id("main")
        vbox.pack_start(self._status_bar, False, False, 0)
        logger.debug("SSHKeyManagerDialog._build_ui: fin")

    def _build_target_bar(self) -> Gtk.Box:
        """Construit la barre de sélection de cible (locale / distante).

        Returns:
            Le Gtk.Box contenant les widgets de la barre.
        """
        logger.debug("SSHKeyManagerDialog._build_target_bar: entrée")
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_top(6)
        bar.set_margin_bottom(6)

        lbl = Gtk.Label(label=_("Remote target (Known Hosts / Authorized Keys):"))
        self._entry_target = Gtk.Entry()
        self._entry_target.set_placeholder_text(_("user@host or root@host[:port]"))
        self._entry_target.set_hexpand(True)
        self._entry_target.connect("activate", self._on_connect_target)

        btn_connect = Gtk.Button(label=_("🔌 Connect"))
        btn_connect.connect("clicked", self._on_connect_target)

        btn_local = Gtk.Button(label=_("🏠 Local"))
        btn_local.connect("clicked", self._on_use_local)

        self._lbl_target_status = Gtk.Label()
        self._lbl_target_status.set_markup(
            "<b>{}</b>".format(GLib.markup_escape_text(_("local machine")))
        )

        bar.pack_start(lbl, False, False, 0)
        bar.pack_start(self._entry_target, True, True, 0)
        bar.pack_start(btn_connect, False, False, 0)
        bar.pack_start(btn_local, False, False, 0)
        bar.pack_start(self._lbl_target_status, False, False, 6)
        logger.debug("SSHKeyManagerDialog._build_target_bar: sortie")
        return bar

    def _build_ca_bar(self) -> Gtk.Box:
        """Construit la barre de désignation de la CA de signature (~/.ssh/ local uniquement).

        Returns:
            Le Gtk.Box contenant les widgets de la barre.
        """
        logger.debug("SSHKeyManagerDialog._build_ca_bar: entrée")
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_top(6)
        bar.set_margin_bottom(6)

        lbl = Gtk.Label(label=_("Signing CA:"))
        self._lbl_ca_status = Gtk.Label()
        self._update_ca_status_label()

        btn_designate = Gtk.Button(label=_("🔏 Designate"))
        btn_designate.set_tooltip_text(_("Choose an existing local key pair as the signing CA"))
        btn_designate.connect("clicked", self._on_designate_ca)

        btn_import = Gtk.Button(label=_("⬆ Import CA"))
        btn_import.set_tooltip_text(
            _("Import an external CA key pair into ~/.ssh/ and designate it")
        )
        btn_import.connect("clicked", self._on_import_ca)

        btn_delete = Gtk.Button(label=_("🗑 Delete CA"))
        btn_delete.set_tooltip_text(_("Permanently delete the designated CA key pair"))
        btn_delete.get_style_context().add_class("destructive-action")
        btn_delete.connect("clicked", self._on_delete_ca)

        bar.pack_start(lbl, False, False, 0)
        bar.pack_start(self._lbl_ca_status, True, True, 0)
        bar.pack_start(btn_designate, False, False, 0)
        bar.pack_start(btn_import, False, False, 0)
        bar.pack_start(btn_delete, False, False, 0)
        logger.debug("SSHKeyManagerDialog._build_ca_bar: sortie")
        return bar

    def _build_action_bar(self) -> Gtk.Box:
        """Construit la barre d'actions contextuelle (change selon l'onglet actif).

        Returns:
            Le Gtk.Box conteneur, packant les trois groupes de boutons.
        """
        logger.debug("SSHKeyManagerDialog._build_action_bar: entrée")
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_start(8)
        bar.set_margin_end(8)
        bar.set_margin_top(6)
        bar.set_margin_bottom(6)

        # --- Groupe clés locales (pages 0 et 1) ---
        self._keys_action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_generate = Gtk.Button(label=_("⚙ Generate"))
        btn_generate.set_tooltip_text(_("Generate a new SSH key pair (ssh-keygen)"))
        btn_generate.connect("clicked", self._on_generate)
        btn_import = Gtk.Button(label=_("↑ Import"))
        btn_import.set_tooltip_text(_("Import an existing private key file into ~/.ssh/"))
        btn_import.connect("clicked", self._on_import)
        self._btn_copy = Gtk.Button(label=_("⎘ Copy public key"))
        self._btn_copy.set_tooltip_text(_("Copy the public key content to clipboard"))
        self._btn_copy.set_sensitive(False)
        self._btn_copy.connect("clicked", self._on_copy_public)
        self._btn_view = Gtk.Button(label=_("👁 View public key"))
        self._btn_view.set_tooltip_text(
            _("Show the full public key content in a dedicated window")
        )
        self._btn_view.set_sensitive(False)
        self._btn_view.connect("clicked", self._on_view_public_key)
        self._btn_rename = Gtk.Button(label=_("✎ Rename"))
        self._btn_rename.set_tooltip_text(
            _("Rename this key (and its .pub / certificate, if any)")
        )
        self._btn_rename.set_sensitive(False)
        self._btn_rename.connect("clicked", self._on_rename_key)
        self._btn_passphrase = Gtk.Button(label=_("🔒 Passphrase"))
        self._btn_passphrase.set_tooltip_text(
            _("Change the passphrase of this private key (ssh-keygen -p)")
        )
        self._btn_passphrase.set_sensitive(False)
        self._btn_passphrase.connect("clicked", self._on_change_passphrase)
        self._btn_agent = Gtk.Button(label=_("🔑 Agent"))
        self._btn_agent.set_tooltip_text(
            _("Load/unload this key in ssh-agent (ssh-add / ssh-add -d)")
        )
        self._btn_agent.set_sensitive(False)
        self._btn_agent.connect("clicked", self._on_toggle_agent)
        self._btn_reveal = Gtk.Button(label=_("📂 Reveal"))
        self._btn_reveal.set_tooltip_text(_("Open ~/.ssh/ in the file manager"))
        self._btn_reveal.connect("clicked", self._on_reveal)
        self._btn_delete = Gtk.Button(label=_("🗑 Delete"))
        self._btn_delete.set_tooltip_text(_("Permanently delete the selected key"))
        self._btn_delete.set_sensitive(False)
        self._btn_delete.get_style_context().add_class("destructive-action")
        self._btn_delete.connect("clicked", self._on_delete)
        for btn in (
            btn_generate,
            btn_import,
            self._btn_copy,
            self._btn_view,
            self._btn_rename,
            self._btn_passphrase,
            self._btn_agent,
            self._btn_reveal,
            self._btn_delete,
        ):
            self._keys_action_box.pack_start(btn, False, False, 0)

        # --- Groupe Known Hosts (page 2) ---
        self._kh_action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_kh_refresh = Gtk.Button(label=_("⟳ Refresh"))
        btn_kh_refresh.connect("clicked", lambda _w: self._load_known_hosts())
        btn_kh_copy_fp = Gtk.Button(label=_("⎘ Copy fingerprint"))
        btn_kh_copy_fp.connect("clicked", self._on_copy_known_host_fingerprint)
        btn_kh_test = Gtk.Button(label=_("🧪 Test known hosts"))
        btn_kh_test.set_tooltip_text(
            _("Check ~/.ssh/config (and GCM hosts, if wired in) against known_hosts")
        )
        btn_kh_test.connect("clicked", self._on_test_known_hosts)
        btn_kh_delete = Gtk.Button(label=_("🗑 Delete"))
        btn_kh_delete.get_style_context().add_class("destructive-action")
        btn_kh_delete.connect("clicked", self._on_delete_known_host)
        for btn in (btn_kh_refresh, btn_kh_copy_fp, btn_kh_test, btn_kh_delete):
            self._kh_action_box.pack_start(btn, False, False, 0)
        # self._kh_action_box.set_no_show_all(True)
        # self._kh_action_box.hide()

        # --- Groupe Authorized Keys (page 3) ---
        self._ak_action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_ak_refresh = Gtk.Button(label=_("⟳ Refresh"))
        btn_ak_refresh.connect("clicked", lambda _w: self._load_authorized_keys())
        btn_ak_add = Gtk.Button(label=_("＋ Add"))
        btn_ak_add.connect("clicked", self._on_add_authorized_key)
        btn_ak_delete = Gtk.Button(label=_("🗑 Delete"))
        btn_ak_delete.get_style_context().add_class("destructive-action")
        btn_ak_delete.connect("clicked", self._on_delete_authorized_key)
        for btn in (btn_ak_refresh, btn_ak_add, btn_ak_delete):
            self._ak_action_box.pack_start(btn, False, False, 0)
        # self._ak_action_box.set_no_show_all(True)
        # self._ak_action_box.hide()

        btn_close = Gtk.Button(label=_("Close"))
        btn_close.connect("clicked", lambda _w: self.request_close())

        bar.pack_start(self._keys_action_box, False, False, 0)
        bar.pack_start(self._kh_action_box, False, False, 0)
        bar.pack_start(self._ak_action_box, False, False, 0)
        bar.pack_end(btn_close, False, False, 0)
        logger.debug("SSHKeyManagerDialog._build_action_bar: sortie")
        return bar

    def _on_page_switched(self, _notebook: Gtk.Notebook, _page: Gtk.Widget, page_num: int) -> None:
        """Affiche le bon groupe de boutons d'actions selon l'onglet actif.

        Args:
            _notebook: Le notebook (non utilisé).
            _page: La page nouvellement affichée (non utilisée).
            page_num: Index de la nouvelle page (0..3).
        """
        logger.debug("SSHKeyManagerDialog._on_page_switched: entrée")
        self._keys_action_box.set_visible(page_num in (0, 1))
        self._kh_action_box.set_visible(page_num == 2)
        self._ak_action_box.set_visible(page_num == 3)
        logger.debug("SSHKeyManagerDialog._on_page_switched: fin")

    def _build_key_tab(self, show_pub_status: bool) -> tuple[Gtk.ListStore, Gtk.ScrolledWindow]:
        """Crée un onglet avec un TreeView de clés (privées ou publiques orphelines).

        Args:
            show_pub_status: Si True, ajoute la colonne « Orphan Public key ».

        Returns:
            Tuple (ListStore, ScrolledWindow contenant le TreeView).
        """
        # [name, priv_path, pub_path, fingerprint, perms_str, perms_ok, pub_exists,
        #  key_type, created_str, validity_str, is_cert, expired, signed_by]
        logger.debug("SSHKeyManagerDialog._build_key_tab: entrée")
        store = Gtk.ListStore(str, str, str, str, str, bool, bool, str, str, str, bool, bool, str)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.IN)

        tree = Gtk.TreeView(model=store)
        tree.set_headers_visible(True)
        tree.set_headers_clickable(True)

        def _text_column(title: str, index: int, expand: bool = False) -> Gtk.TreeViewColumn:
            """Colonne texte, triable, dont le fond passe en rouge clair si la clé est expirée."""
            logger.debug("_text_column: entrée")
            renderer = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title)
            col.pack_start(renderer, True)

            def _data_func(_col, cell, model, it, _data, idx=index):
                logger.debug("_data_func: entrée")
                cell.set_property("text", model[it][idx])
                expired = model[it][_COL_EXPIRED]
                cell.set_property("background-set", expired)
                if expired:
                    cell.set_property("background", "#ffdede")
                logger.debug("_data_func: fin")

            col.set_cell_data_func(renderer, _data_func)
            col.set_sort_column_id(index)
            if expand:
                col.set_expand(True)
            logger.debug("_text_column: sortie")
            return col

        tree.append_column(_text_column(_("Name"), _COL_NAME, expand=True))
        tree.append_column(_text_column(_("Type"), _COL_KEY_TYPE))
        tree.append_column(_text_column(_("Fingerprint"), _COL_FINGERPRINT, expand=True))
        tree.append_column(_text_column(_("Created"), _COL_CREATED_STR))
        tree.append_column(_text_column(_("Validity"), _COL_VALIDITY_STR))
        tree.append_column(_text_column(_("Signed by"), _COL_SIGNED_BY, expand=True))
        tree.append_column(_text_column(_("Perms"), _COL_PERMS_STR))

        if show_pub_status:
            renderer_pub = Gtk.CellRendererText()
            col_pub = Gtk.TreeViewColumn(_("Orphan Public key"))
            col_pub.pack_start(renderer_pub, True)
            col_pub.set_cell_data_func(renderer_pub, self._render_pub_status)
            col_pub.set_sort_column_id(_COL_PUB_EXISTS)
            tree.append_column(col_pub)

        sw.add(tree)
        logger.debug("SSHKeyManagerDialog._build_key_tab: sortie")
        return store, sw

    def _build_known_hosts_tab(self) -> tuple[Gtk.ListStore, Gtk.TreeView, Gtk.ScrolledWindow]:
        """Crée l'onglet Known Hosts.

        Returns:
            Tuple (ListStore, TreeView, ScrolledWindow).
        """
        # [host_display, keytype, fingerprint, hashed, raw_line, lineno]
        logger.debug("SSHKeyManagerDialog._build_known_hosts_tab: entrée")
        store = Gtk.ListStore(str, str, str, bool, str, int)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.IN)

        tree = Gtk.TreeView(model=store)
        tree.set_headers_clickable(True)

        renderer_lineno = Gtk.CellRendererText()
        col_lineno = Gtk.TreeViewColumn(_("#"))
        col_lineno.pack_start(renderer_lineno, True)
        col_lineno.set_cell_data_func(
            renderer_lineno,
            lambda _c, cell, model, it, _d: cell.set_property("text", str(model[it][5])),
        )
        col_lineno.set_sort_column_id(5)
        col_lineno.set_min_width(40)
        tree.append_column(col_lineno)

        col_host = Gtk.TreeViewColumn(_("Host"), Gtk.CellRendererText(), text=0)
        col_host.set_expand(True)
        col_host.set_sort_column_id(0)
        tree.append_column(col_host)
        col_type = Gtk.TreeViewColumn(_("Type"), Gtk.CellRendererText(), text=1)
        col_type.set_sort_column_id(1)
        tree.append_column(col_type)
        col_fp = Gtk.TreeViewColumn(_("Fingerprint"), Gtk.CellRendererText(), text=2)
        col_fp.set_expand(True)
        col_fp.set_sort_column_id(2)
        tree.append_column(col_fp)

        renderer_hashed = Gtk.CellRendererText()
        col_hashed = Gtk.TreeViewColumn(_("Hashed"))
        col_hashed.pack_start(renderer_hashed, True)
        col_hashed.set_cell_data_func(
            renderer_hashed,
            lambda _c, cell, model, it, _d: cell.set_property("text", "✓" if model[it][3] else ""),
        )
        col_hashed.set_sort_column_id(3)
        tree.append_column(col_hashed)

        sw.add(tree)
        logger.debug("SSHKeyManagerDialog._build_known_hosts_tab: sortie")
        return store, tree, sw

    def _build_authorized_keys_tab(self) -> tuple[Gtk.ListStore, Gtk.TreeView, Gtk.ScrolledWindow]:
        """Crée l'onglet Authorized Keys.

        Returns:
            Tuple (ListStore, TreeView, ScrolledWindow).
        """
        # [enabled, keytype, fingerprint, comment, raw_line, lineno]
        logger.debug("SSHKeyManagerDialog._build_authorized_keys_tab: entrée")
        store = Gtk.ListStore(bool, str, str, str, str, int)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.IN)

        tree = Gtk.TreeView(model=store)
        tree.set_headers_clickable(True)
        renderer_toggle = Gtk.CellRendererToggle()
        renderer_toggle.connect("toggled", self._on_toggle_authorized_key)
        col_enabled = Gtk.TreeViewColumn(_("Enabled"), renderer_toggle, active=0)
        col_enabled.set_sort_column_id(0)
        tree.append_column(col_enabled)
        col_type = Gtk.TreeViewColumn(_("Type"), Gtk.CellRendererText(), text=1)
        col_type.set_sort_column_id(1)
        tree.append_column(col_type)
        col_fp = Gtk.TreeViewColumn(_("Fingerprint"), Gtk.CellRendererText(), text=2)
        col_fp.set_expand(True)
        col_fp.set_sort_column_id(2)
        tree.append_column(col_fp)
        col_comment = Gtk.TreeViewColumn(_("Comment"), Gtk.CellRendererText(), text=3)
        col_comment.set_expand(True)
        col_comment.set_sort_column_id(3)
        tree.append_column(col_comment)

        sw.add(tree)
        logger.debug("SSHKeyManagerDialog._build_authorized_keys_tab: sortie")
        return store, tree, sw

    # ------------------------------------------------------------------
    # Cible locale / distante
    # ------------------------------------------------------------------

    def _current_fs(self) -> _FSAdapter:
        """Retourne l'adaptateur actif (distant si connecté, sinon local).

        Returns:
            L'adaptateur de fichiers à utiliser pour Known Hosts / Authorized Keys.
        """
        return self._remote_fs if self._remote_fs is not None else self._local_fs

    def _on_connect_target(self, _widget: Gtk.Widget) -> None:
        """Tente de se connecter à la cible saisie et bascule les onglets distants."""
        logger.debug("SSHKeyManagerDialog._on_connect_target: entrée")
        text = self._entry_target.get_text().strip()
        if not text:
            self._on_use_local(_widget)
            logger.debug("SSHKeyManagerDialog._on_connect_target: sortie")
            return
        match = _TARGET_RE.match(text)
        if not match:
            self._notify(_("Invalid target — expected user@host or user@host:port"))
            logger.debug("SSHKeyManagerDialog._on_connect_target: sortie")
            return
        user, host, port_str = match.group(1), match.group(2), match.group(3)
        spec = SSHTargetSpec(user=user, host=host, port=int(port_str) if port_str else 22)
        fs = _RemoteFS(spec)
        self._notify(_("Testing connection to {t}…").format(t=spec.display))
        ok, err = fs.test_connection()
        if not ok:
            self._notify(_("Connection failed: {err}").format(err=err[:120]))
            logger.warning(f"_on_connect_target | target={spec.display} err={err}")
            logger.debug("SSHKeyManagerDialog._on_connect_target: sortie")
            return
        self._remote_fs = fs
        self._lbl_target_status.set_markup(f"<b>{GLib.markup_escape_text(spec.display)}</b>")
        self._notify(
            _("Connected to {t} — Known Hosts / Authorized Keys now read from there").format(
                t=spec.display
            )
        )
        logger.info(f"_on_connect_target | connected to {spec.display}")
        self._load_known_hosts()
        self._load_authorized_keys()
        logger.debug("SSHKeyManagerDialog._on_connect_target: fin")

    def _on_use_local(self, _widget: Gtk.Widget) -> None:
        """Revient à la machine locale pour Known Hosts / Authorized Keys."""
        logger.debug("SSHKeyManagerDialog._on_use_local: entrée")
        self._remote_fs = None
        self._entry_target.set_text("")
        self._lbl_target_status.set_markup(
            "<b>{}</b>".format(GLib.markup_escape_text(_("local machine")))
        )
        self._notify(_("Switched back to local machine"))
        self._load_known_hosts()
        self._load_authorized_keys()
        logger.debug("SSHKeyManagerDialog._on_use_local: fin")

    # ------------------------------------------------------------------
    # Gestion de la CA de signature (toujours locale)
    # ------------------------------------------------------------------

    def _load_ca_config(self) -> None:
        """Charge la CA désignée depuis ~/.ssh/.gcm_ca_config.json, si présent et valide."""
        logger.debug("SSHKeyManagerDialog._load_ca_config: entrée")
        try:
            data = json.loads(_CA_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.debug("SSHKeyManagerDialog._load_ca_config: sortie")
            return
        priv = data.get("ca_priv", "")
        if not priv:
            logger.debug("SSHKeyManagerDialog._load_ca_config: sortie")
            return
        priv_path = Path(priv)
        if not priv_path.exists():
            logger.warning(
                f"_load_ca_config | CA introuvable, désignation ignorée | path={priv_path}"
            )
            logger.debug("SSHKeyManagerDialog._load_ca_config: sortie")
            return
        pub_path = priv_path.with_name(priv_path.name + ".pub")
        self._ca_priv_path = priv_path
        self._ca_pub_path = pub_path if pub_path.exists() else None
        logger.debug("SSHKeyManagerDialog._load_ca_config: fin")

    def _save_ca_config(self) -> None:
        """Persiste la CA désignée dans ~/.ssh/.gcm_ca_config.json (permissions 600)."""
        logger.debug("SSHKeyManagerDialog._save_ca_config: entrée")
        try:
            self._ssh_dir.mkdir(parents=True, exist_ok=True)
            payload = {"ca_priv": str(self._ca_priv_path) if self._ca_priv_path else ""}
            _CA_CONFIG_PATH.write_text(json.dumps(payload), encoding="utf-8")
            _CA_CONFIG_PATH.chmod(0o600)
        except OSError as exc:
            logger.error(f"_save_ca_config | {exc}")
        logger.debug("SSHKeyManagerDialog._save_ca_config: fin")

    def _update_ca_status_label(self) -> None:
        """Met à jour le libellé affichant la CA actuellement désignée."""
        logger.debug("SSHKeyManagerDialog._update_ca_status_label: entrée")
        if self._ca_priv_path is None:
            self._lbl_ca_status.set_markup(
                "<i>{}</i>".format(
                    GLib.markup_escape_text(_("(none — keys are generated unsigned)"))
                )
            )
            logger.debug("SSHKeyManagerDialog._update_ca_status_label: sortie")
            return
        fp, key_type = self._get_fingerprint_and_type(
            self._ca_pub_path if self._ca_pub_path else self._ca_priv_path
        )
        detail = f" — {key_type} {fp}" if fp else ""
        text = f"<b>{GLib.markup_escape_text(self._ca_priv_path.name)}</b>{GLib.markup_escape_text(detail)}"
        self._lbl_ca_status.set_markup(text)
        logger.debug("SSHKeyManagerDialog._update_ca_status_label: fin")

    def has_designated_ca(self) -> bool:
        """Indique si une CA de signature est actuellement désignée.

        Returns:
            True si une clé privée de CA est désignée et existe sur disque.
        """
        return self._ca_priv_path is not None and self._ca_priv_path.exists()

    def _on_designate_ca(self, _widget: Gtk.Widget) -> None:
        """Ouvre un FileChooser pour désigner une paire de clés existante comme CA."""
        logger.debug("SSHKeyManagerDialog._on_designate_ca: entrée")
        dlg = Gtk.FileChooserDialog(
            title=_("Designate Signing CA"),
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        btn_ok = dlg.add_button(_("_Designate"), Gtk.ResponseType.OK)
        btn_ok.get_style_context().add_class("suggested-action")
        if self._ssh_dir.exists():
            dlg.set_current_folder(str(self._ssh_dir))

        response = run_dialog_sync(dlg)
        filename = dlg.get_filename()
        dlg.destroy()
        if response != Gtk.ResponseType.OK or not filename:
            logger.debug("SSHKeyManagerDialog._on_designate_ca: sortie")
            return

        priv_path = Path(filename)
        if priv_path.name.endswith(".pub"):
            self._notify(_("Select the private key file, not the .pub file"))
            logger.debug("SSHKeyManagerDialog._on_designate_ca: sortie")
            return
        pub_path = priv_path.with_name(priv_path.name + ".pub")
        if not pub_path.exists():
            self._notify(
                _("No matching .pub file found next to {name} — cannot use as CA").format(
                    name=priv_path.name
                )
            )
            logger.debug("SSHKeyManagerDialog._on_designate_ca: sortie")
            return

        self._ca_priv_path = priv_path
        self._ca_pub_path = pub_path
        self._save_ca_config()
        self._update_ca_status_label()
        self._notify(_("Designated CA: {name}").format(name=priv_path.name))
        logger.info(f"_on_designate_ca | designated {priv_path}")
        logger.debug("SSHKeyManagerDialog._on_designate_ca: fin")

    def _on_import_ca(self, _widget: Gtk.Widget) -> None:
        """Importe une paire de clés externe dans ~/.ssh/ et la désigne comme CA."""
        logger.debug("SSHKeyManagerDialog._on_import_ca: entrée")
        dlg = Gtk.FileChooserDialog(
            title=_("Import CA Private Key"),
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        btn_ok = dlg.add_button(_("_Import"), Gtk.ResponseType.OK)
        btn_ok.get_style_context().add_class("suggested-action")

        flt_all = Gtk.FileFilter()
        flt_all.set_name(_("All files"))
        flt_all.add_pattern("*")
        dlg.add_filter(flt_all)

        response = run_dialog_sync(dlg)
        filename = dlg.get_filename()
        dlg.destroy()
        if response != Gtk.ResponseType.OK or not filename:
            logger.debug("SSHKeyManagerDialog._on_import_ca: sortie")
            return

        src = Path(filename)
        pub_src = src.with_name(src.name + ".pub")
        if not pub_src.exists():
            self._notify(
                _("No matching .pub file found next to {name} — cannot import as CA").format(
                    name=src.name
                )
            )
            logger.debug("SSHKeyManagerDialog._on_import_ca: sortie")
            return

        dst = self._ssh_dir / src.name
        if dst.exists():
            if not self._confirm(
                _("File {name} already exists in ~/.ssh/ — overwrite?").format(name=src.name)
            ):
                logger.debug("SSHKeyManagerDialog._on_import_ca: sortie")
                return

        try:
            self._ssh_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            dst.chmod(0o600)
            pub_dst = dst.with_name(dst.name + ".pub")
            shutil.copy2(pub_src, pub_dst)
            pub_dst.chmod(0o644)
        except Exception as exc:
            self._notify(_("CA import failed: {err}").format(err=str(exc)))
            logger.error(f"_on_import_ca | {exc}")
            logger.debug("SSHKeyManagerDialog._on_import_ca: sortie")
            return

        self._ca_priv_path = dst
        self._ca_pub_path = pub_dst
        self._save_ca_config()
        self._update_ca_status_label()
        self._notify(_("CA imported and designated: {name}").format(name=dst.name))
        logger.info(f"_on_import_ca | imported and designated {dst}")
        self._load_keys()
        logger.debug("SSHKeyManagerDialog._on_import_ca: fin")

    def _on_delete_ca(self, _widget: Gtk.Widget) -> None:
        """Supprime définitivement la paire de clés de la CA désignée, après confirmation."""
        logger.debug("SSHKeyManagerDialog._on_delete_ca: entrée")
        if self._ca_priv_path is None:
            self._notify(_("No CA currently designated"))
            logger.debug("SSHKeyManagerDialog._on_delete_ca: sortie")
            return
        name = self._ca_priv_path.name
        if not self._confirm(
            _(
                "Permanently delete the CA key pair <b>{name}</b>?\n"
                "Certificates already signed by this CA will no longer be trustable to verify.\n"
                "<b>This cannot be undone.</b>"
            ).format(name=name)
        ):
            logger.debug("SSHKeyManagerDialog._on_delete_ca: sortie")
            return
        try:
            self._ca_priv_path.unlink(missing_ok=True)
            if self._ca_pub_path:
                self._ca_pub_path.unlink(missing_ok=True)
        except Exception as exc:
            self._notify(_("CA deletion failed: {err}").format(err=str(exc)))
            logger.error(f"_on_delete_ca | {exc}")
            logger.debug("SSHKeyManagerDialog._on_delete_ca: sortie")
            return
        self._ca_priv_path = None
        self._ca_pub_path = None
        self._save_ca_config()
        self._update_ca_status_label()
        self._notify(_("CA deleted: {name}").format(name=name))
        logger.info(f"_on_delete_ca | deleted {name}")
        self._load_keys()
        logger.debug("SSHKeyManagerDialog._on_delete_ca: fin")

    # ------------------------------------------------------------------
    # Chargement — Private / Orphan keys (toujours locales)
    # ------------------------------------------------------------------

    def _load_keys(self) -> None:
        """Scanne ~/.ssh/ et peuple les ListStore Private/Orphan."""
        logger.debug("SSHKeyManagerDialog._load_keys: entrée")
        for store in (self._priv_store, self._pub_store):
            store.clear()

        if not self._ssh_dir.exists():
            self._notify(_("~/.ssh/ not found — no keys loaded"))
            logger.debug("SSHKeyManagerDialog._load_keys: sortie")
            return

        priv_count = 0
        pub_count = 0

        for path in sorted(self._ssh_dir.iterdir()):
            if not path.is_file():
                continue
            name = path.name
            if (
                name in _EXCLUDED_NAMES
                or name.startswith(".")
                or name.startswith("config.")
                or name.startswith("known_hosts.")
                or name.startswith("authorized_keys.")
            ):
                continue

            perms_str, perms_ok = self._check_permissions(path)
            created_dt = _get_creation_datetime(path)
            created_str = created_dt.strftime("%Y-%m-%d %H:%M") if created_dt else "?"

            if name.endswith(".pub"):
                priv = path.with_suffix("")
                if priv.exists():
                    continue  # traité dans la branche clé privée ci-dessous
                fp, key_type = self._get_fingerprint_and_type(path)
                is_cert = name.endswith("-cert.pub")
                validity_str, expired, signed_by = "—", False, "—"
                if is_cert:
                    info = _parse_cert_info(path)
                    validity_str = str(info["valid_str"])
                    expired = bool(info["expired"])
                    signed_by = str(info["signed_by"])
                self._pub_store.append(
                    [
                        name,
                        "",
                        str(path),
                        fp,
                        perms_str,
                        perms_ok,
                        False,
                        key_type,
                        created_str,
                        validity_str,
                        is_cert,
                        expired,
                        signed_by,
                    ]
                )
                pub_count += 1
            else:
                pub = path.with_name(name + ".pub")
                pub_exists = pub.exists()
                fp, key_type = self._get_fingerprint_and_type(path)
                cert_path = Path(str(path) + "-cert.pub")
                is_cert = cert_path.exists()
                validity_str, expired, signed_by = "—", False, "—"
                if is_cert:
                    info = _parse_cert_info(cert_path)
                    validity_str = str(info["valid_str"])
                    expired = bool(info["expired"])
                    signed_by = str(info["signed_by"])
                self._priv_store.append(
                    [
                        name,
                        str(path),
                        str(pub) if pub_exists else "",
                        fp,
                        perms_str,
                        perms_ok,
                        pub_exists,
                        key_type,
                        created_str,
                        validity_str,
                        is_cert,
                        expired,
                        signed_by,
                    ]
                )
                priv_count += 1

        self._notify(
            _("{priv} private key(s), {pub} Orphan Public key(s) loaded from ~/.ssh/").format(
                priv=priv_count, pub=pub_count
            )
        )
        logger.debug(f"SSHKeyManagerDialog._load_keys | priv={priv_count} pub={pub_count}")
        self._update_buttons()
        logger.debug("SSHKeyManagerDialog._load_keys: fin")

    def _get_fingerprint_and_type(self, path: Path) -> tuple[str, str]:
        """Calcule le fingerprint et le type de clé via ssh-keygen -lf.

        Args:
            path: Chemin du fichier de clé.

        Returns:
            Tuple (fingerprint, key_type) — chaînes vides si indéterminable.
        """
        logger.debug("SSHKeyManagerDialog._get_fingerprint_and_type: entrée")
        try:
            result = subprocess.run(
                ["ssh-keygen", "-lf", str(path)], capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                fingerprint = parts[1][:32] if len(parts) >= 2 else ""
                key_type = parts[-1].strip("()") if parts and parts[-1].startswith("(") else ""
                logger.debug("SSHKeyManagerDialog._get_fingerprint_and_type: sortie")
                return fingerprint, key_type
        except Exception as exc:
            logger.debug(f"_get_fingerprint_and_type | {path} → {exc}")
        logger.debug("SSHKeyManagerDialog._get_fingerprint_and_type: sortie")
        return "", ""

    def _check_permissions(self, path: Path) -> tuple[str, bool]:
        """Vérifie les permissions du fichier de clé.

        Args:
            path: Chemin du fichier à inspecter.

        Returns:
            Tuple (texte_permissions, permissions_correctes).
        """
        logger.debug("SSHKeyManagerDialog._check_permissions: entrée")
        try:
            mode = path.stat().st_mode
            perms = stat.S_IMODE(mode)
            ok = perms <= 0o600 and not (mode & stat.S_IRWXG) and not (mode & stat.S_IRWXO)
            logger.debug("SSHKeyManagerDialog._check_permissions: sortie")
            return (f"{oct(perms)[-3:]} ✓" if ok else f"{oct(perms)[-3:]} ⚠", ok)
        except OSError as exc:
            logger.debug(f"_check_permissions | {path} → {exc}")
            logger.debug("SSHKeyManagerDialog._check_permissions: sortie")
            return ("?", False)
        logger.debug("SSHKeyManagerDialog._check_permissions: fin")

    def _render_pub_status(
        self,
        _col: Gtk.TreeViewColumn,
        cell: Gtk.CellRendererText,
        model: Gtk.TreeModel,
        it: Gtk.TreeIter,
        _data: object,
    ) -> None:
        """Affiche un indicateur de présence de la clé publique.

        Args:
            _col: Colonne (non utilisée).
            cell: CellRenderer à configurer.
            model: Modèle de données.
            it: Itérateur courant.
            _data: Données utilisateur (non utilisées).
        """
        logger.debug("SSHKeyManagerDialog._render_pub_status: entrée")
        pub_exists = model[it][_COL_PUB_EXISTS]
        cell.set_property("text", "✓ .pub" if pub_exists else "✗ none")
        cell.set_property("foreground", "#2d9e44" if pub_exists else "#888888")
        expired = model[it][_COL_EXPIRED]
        cell.set_property("background-set", expired)
        if expired:
            cell.set_property("background", "#ffdede")
        logger.debug("SSHKeyManagerDialog._render_pub_status: fin")

    def _get_selected_key(self) -> dict[str, str | bool] | None:
        """Retourne les infos de la clé sélectionnée dans l'onglet actif.

        Returns:
            Dictionnaire ``{name, path, pub, perms_ok}`` ou None.
        """
        logger.debug("SSHKeyManagerDialog._get_selected_key: entrée")
        page = self._notebook.get_current_page()
        tree = self._priv_tree if page == 0 else self._pub_tree
        store = self._priv_store if page == 0 else self._pub_store
        _model, it = tree.get_selection().get_selected()
        if it is None:
            logger.debug("SSHKeyManagerDialog._get_selected_key: sortie")
            return None
        logger.debug("SSHKeyManagerDialog._get_selected_key: sortie")
        return {
            "name": store[it][_COL_NAME],
            "path": store[it][_COL_PRIV_PATH],
            "pub": store[it][_COL_PUB_PATH],
            "perms_ok": store[it][_COL_PERMS_OK],
        }

    def _on_selection_changed(self, _sel: Gtk.TreeSelection) -> None:
        """Met à jour la sensibilité des boutons d'action."""
        logger.debug("SSHKeyManagerDialog._on_selection_changed: entrée")
        self._update_buttons()
        logger.debug("SSHKeyManagerDialog._on_selection_changed: fin")

    def _update_buttons(self) -> None:
        """Active/désactive les boutons selon la sélection courante."""
        logger.debug("SSHKeyManagerDialog._update_buttons: entrée")
        key = self._get_selected_key()
        has_sel = key is not None
        has_pub = has_sel and bool(key.get("pub"))  # type: ignore[union-attr]
        has_priv = has_sel and bool(key.get("path"))  # type: ignore[union-attr]
        self._btn_copy.set_sensitive(has_pub)
        self._btn_view.set_sensitive(has_pub)
        self._btn_rename.set_sensitive(has_sel)
        self._btn_passphrase.set_sensitive(has_priv)
        self._btn_agent.set_sensitive(has_priv)
        self._btn_delete.set_sensitive(has_sel)
        logger.debug("SSHKeyManagerDialog._update_buttons: fin")

    # ------------------------------------------------------------------
    # Actions — Private / Orphan keys
    # ------------------------------------------------------------------

    def _on_generate(self, _widget: Gtk.Widget) -> None:
        """Ouvre le formulaire de génération et lance ssh-keygen."""
        logger.debug("SSHKeyManagerDialog._on_generate: entrée")
        ca_name = self._ca_priv_path.name if self._ca_priv_path else ""
        dlg = _GenerateKeyDialog(
            parent=self._top_window(), ca_available=self.has_designated_ca(), ca_name=ca_name
        )
        response = run_dialog_sync(dlg)
        opts: dict[str, str | int | bool] = dlg.get_options()
        dlg.destroy()
        if response != Gtk.ResponseType.OK:
            logger.debug("SSHKeyManagerDialog._on_generate: sortie")
            return
        self._generate_key(opts)
        logger.debug("SSHKeyManagerDialog._on_generate: fin")

    def _generate_key(self, opts: dict[str, str | int | bool]) -> None:
        """Lance ssh-keygen avec les options données, et signe avec la CA si demandé.

        Args:
            opts: Dictionnaire ``{type, size, name, comment, passphrase, sign_with_ca,
                ca_identity, ca_principals, ca_validity}``.
        """
        logger.debug("SSHKeyManagerDialog._generate_key: entrée")
        try:
            self._ssh_dir.mkdir(parents=True, exist_ok=True)

            name = str(opts.get("name") or "id_ed25519").strip()
            if not name:
                name = f"id_{opts['type']}"

            final_name = name
            suffix = 0
            while (self._ssh_dir / final_name).exists():
                suffix += 1
                final_name = f"{name}_{suffix}"

            key_path = self._ssh_dir / final_name
            key_type = str(opts.get("type") or "ed25519").lower()
            comment = str(opts.get("comment") or get_default_ssh_key_comment())
            passphrase = str(opts.get("passphrase") or "")

            if key_type == "rsa":
                size = int(opts.get("size") or 3072)
                cmd = [
                    "ssh-keygen",
                    "-t",
                    "rsa",
                    "-b",
                    str(size),
                    "-f",
                    str(key_path),
                    "-N",
                    passphrase,
                    "-C",
                    comment,
                ]
            elif key_type == "ecdsa":
                cmd = [
                    "ssh-keygen",
                    "-t",
                    "ecdsa",
                    "-f",
                    str(key_path),
                    "-N",
                    passphrase,
                    "-C",
                    comment,
                ]
            else:
                cmd = [
                    "ssh-keygen",
                    "-t",
                    "ed25519",
                    "-f",
                    str(key_path),
                    "-N",
                    passphrase,
                    "-C",
                    comment,
                ]

            logger.info(
                f"SSHKeyManagerDialog._generate_key | type={key_type} name={final_name} path={key_path}"
            )
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.debug(f"_generate_key | stdout={result.stdout.strip()}")

            self._notify(_("Key generated: {name}").format(name=final_name))

            if opts.get("sign_with_ca"):
                self._sign_key_with_ca(
                    key_path,
                    identity=str(opts.get("ca_identity") or comment),
                    principals=str(opts.get("ca_principals") or getpass.getuser()),
                    validity=str(opts.get("ca_validity") or "+52w"),
                )

            GLib.idle_add(self._load_keys)

        except FileNotFoundError:
            logger.debug("SSHKeyManagerDialog._generate_key: except FileNotFoundError")
            self._notify(_("ssh-keygen not found — install openssh-client"))
        except subprocess.CalledProcessError as exc:
            self._notify(_("ssh-keygen failed: {err}").format(err=exc.stderr.strip()[:80]))
            logger.error(f"_generate_key | CalledProcessError: {exc}")
        except Exception as exc:
            self._notify(_("Generation failed: {err}").format(err=str(exc)))
            logger.exception("_generate_key | unexpected error", exception=True)
        logger.debug("SSHKeyManagerDialog._generate_key: fin")

    def _sign_key_with_ca(
        self, key_path: Path, identity: str, principals: str, validity: str
    ) -> None:
        """Signe la clé publique générée avec la CA désignée (produit ``<name>-cert.pub``).

        Note:
            Si la clé privée de la CA est protégée par une passphrase,
            ``ssh-keygen -s`` demandera cette passphrase de façon interactive ;
            sans TTY cette étape échouera. Pour signer sans interaction,
            garder la CA sans passphrase ou la charger dans ssh-agent puis
            signer manuellement avec ``ssh-keygen -Us <ca_pub> ...``.

        Args:
            key_path: Chemin de la clé privée nouvellement générée (sans ``.pub``).
            identity: Identité du certificat (``-I``).
            principals: Liste de principals séparés par des virgules (``-n``).
            validity: Fenêtre de validité au format ``ssh-keygen -V`` (ex. ``+52w``).
        """
        logger.debug("SSHKeyManagerDialog._sign_key_with_ca: entrée")
        if self._ca_priv_path is None:
            self._notify(_("No CA designated — key generated unsigned"))
            logger.debug("SSHKeyManagerDialog._sign_key_with_ca: sortie")
            return
        pub_path = key_path.with_name(key_path.name + ".pub")
        cmd = [
            "ssh-keygen",
            "-s",
            str(self._ca_priv_path),
            "-I",
            identity,
            "-n",
            principals,
            "-V",
            validity,
            str(pub_path),
        ]
        try:
            result = subprocess.run(cmd, input="", capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            self._notify(
                _(
                    "CA signing timed out — CA key may require a passphrase (unsupported non-interactively)"
                )
            )
            logger.warning(f"_sign_key_with_ca | timeout signing {pub_path}")
            logger.debug("SSHKeyManagerDialog._sign_key_with_ca: sortie")
            return
        except Exception as exc:
            self._notify(_("CA signing failed: {err}").format(err=str(exc)))
            logger.error(f"_sign_key_with_ca | {exc}")
            logger.debug("SSHKeyManagerDialog._sign_key_with_ca: sortie")
            return
        if result.returncode != 0:
            self._notify(_("CA signing failed: {err}").format(err=result.stderr.strip()[:120]))
            logger.error(
                f"_sign_key_with_ca | ssh-keygen -s failed | stderr={result.stderr.strip()}"
            )
            logger.debug("SSHKeyManagerDialog._sign_key_with_ca: sortie")
            return
        self._notify(
            _("Key signed by CA {ca} (identity={id})").format(
                ca=self._ca_priv_path.name, id=identity
            )
        )
        logger.info(f"_sign_key_with_ca | signed {pub_path} with CA {self._ca_priv_path}")
        logger.debug("SSHKeyManagerDialog._sign_key_with_ca: fin")

    def _on_import(self, _widget: Gtk.Widget) -> None:
        """Ouvre un FileChooser pour importer une clé privée dans ~/.ssh/."""
        logger.debug("SSHKeyManagerDialog._on_import: entrée")
        dlg = Gtk.FileChooserDialog(
            title=_("Import Private Key"),
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_button(_("_Cancel"), Gtk.ResponseType.CANCEL)
        btn_import = dlg.add_button(_("_Import"), Gtk.ResponseType.OK)
        btn_import.get_style_context().add_class("suggested-action")

        flt_all = Gtk.FileFilter()
        flt_all.set_name(_("All files"))
        flt_all.add_pattern("*")
        dlg.add_filter(flt_all)

        if self._ssh_dir.exists():
            dlg.set_current_folder(str(self._ssh_dir))

        response = run_dialog_sync(dlg)
        filename = dlg.get_filename()
        dlg.destroy()

        if response != Gtk.ResponseType.OK or not filename:
            logger.debug("SSHKeyManagerDialog._on_import: sortie")
            return

        src = Path(filename)
        dst = self._ssh_dir / src.name

        if dst.exists():
            overwrite = self._confirm(
                _("File {name} already exists in ~/.ssh/ — overwrite?").format(name=src.name)
            )
            if not overwrite:
                logger.debug("SSHKeyManagerDialog._on_import: sortie")
                return

        try:
            self._ssh_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            dst.chmod(0o600)
            pub_src = src.with_name(src.name + ".pub")
            if pub_src.exists():
                pub_dst = dst.with_name(dst.name + ".pub")
                shutil.copy2(pub_src, pub_dst)
                pub_dst.chmod(0o644)
            logger.info(f"_on_import | imported {src} → {dst}")
            self._notify(_("Key imported: {name}").format(name=src.name))
            self._load_keys()
        except Exception as exc:
            self._notify(_("Import failed: {err}").format(err=str(exc)))
            logger.error(f"_on_import | {exc}")
        logger.debug("SSHKeyManagerDialog._on_import: fin")

    def _on_copy_public(self, _widget: Gtk.Widget) -> None:
        """Copie la clé publique sélectionnée dans le presse-papiers."""
        logger.debug("SSHKeyManagerDialog._on_copy_public: entrée")
        key = self._get_selected_key()
        if not key or not key.get("pub"):
            self._notify(_("No Public key available for selected entry"))
            logger.debug("SSHKeyManagerDialog._on_copy_public: sortie")
            return

        pub_path = Path(str(key["pub"]))
        if not pub_path.exists():
            self._notify(_("Public key file not found: {p}").format(p=pub_path))
            logger.debug("SSHKeyManagerDialog._on_copy_public: sortie")
            return

        try:
            text = pub_path.read_text(encoding="utf-8").strip()
            if _copy_text_to_clipboard(text):
                self._notify(_("Public key copied to clipboard"))
            else:
                self._notify(_("Clipboard unavailable — key content logged"))
                logger.info(f"public key content: {text}")
        except Exception as exc:
            self._notify(_("Copy failed: {err}").format(err=str(exc)))
            logger.error(f"_on_copy_public | {exc}")
        logger.debug("SSHKeyManagerDialog._on_copy_public: fin")

    def _on_reveal(self, _widget: Gtk.Widget) -> None:
        """Ouvre ~/.ssh/ dans le gestionnaire de fichiers."""
        logger.debug("SSHKeyManagerDialog._on_reveal: entrée")
        try:
            subprocess.Popen(["xdg-open", str(self._ssh_dir)])
        except Exception as exc:
            logger.debug("SSHKeyManagerDialog._on_reveal: except Exception — {}", exc)
            self._notify(_("Cannot open file manager: {err}").format(err=str(exc)))
        logger.debug("SSHKeyManagerDialog._on_reveal: fin")

    def _on_delete(self, _widget: Gtk.Widget) -> None:
        """Supprime la clé sélectionnée après confirmation."""
        logger.debug("SSHKeyManagerDialog._on_delete: entrée")
        key = self._get_selected_key()
        if not key:
            logger.debug("SSHKeyManagerDialog._on_delete: sortie")
            return

        name = key["name"]
        confirmed = self._confirm(
            _(
                "Permanently delete <b>{name}</b> (and its Orphan Public key if present)?\n<b>This cannot be undone.</b>"
            ).format(name=name)
        )
        if not confirmed:
            logger.debug("SSHKeyManagerDialog._on_delete: sortie")
            return

        try:
            Path(str(key["path"])).unlink(missing_ok=True)
            if key.get("pub"):
                Path(str(key["pub"])).unlink(missing_ok=True)
            logger.info(f"_on_delete | deleted {name}")
            self._notify(_("Key deleted: {name}").format(name=name))
            self._load_keys()
        except Exception as exc:
            self._notify(_("Delete failed: {err}").format(err=str(exc)))
            logger.error(f"_on_delete | {exc}")
        logger.debug("SSHKeyManagerDialog._on_delete: fin")

    def _on_view_public_key(self, _widget: Gtk.Widget) -> None:
        """Affiche le contenu complet de la clé publique sélectionnée."""
        logger.debug("SSHKeyManagerDialog._on_view_public_key: entrée")
        key = self._get_selected_key()
        if not key or not key.get("pub"):
            self._notify(_("No public key available for selected entry"))
            logger.debug("SSHKeyManagerDialog._on_view_public_key: sortie")
            return
        pub_path = Path(str(key["pub"]))
        try:
            content = pub_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            self._notify(_("Cannot read public key: {err}").format(err=str(exc)))
            logger.debug("SSHKeyManagerDialog._on_view_public_key: sortie")
            return
        _ViewPublicKeyDialog(parent=self._top_window(), key_name=str(key["name"]), content=content)
        logger.debug("SSHKeyManagerDialog._on_view_public_key: fin")

    def _on_rename_key(self, _widget: Gtk.Widget) -> None:
        """Renomme la clé sélectionnée (et sa .pub / son certificat, si présents)."""
        logger.debug("SSHKeyManagerDialog._on_rename_key: entrée")
        key = self._get_selected_key()
        if not key:
            self._notify(_("No key selected"))
            logger.debug("SSHKeyManagerDialog._on_rename_key: sortie")
            return
        old_name = str(key["name"])
        dlg = _RenameKeyDialog(parent=self._top_window(), current_name=old_name)
        response = run_dialog_sync(dlg)
        new_name = dlg.get_new_name()
        dlg.destroy()
        if response != Gtk.ResponseType.OK or not new_name or new_name == old_name:
            logger.debug("SSHKeyManagerDialog._on_rename_key: sortie")
            return
        if "/" in new_name or "\\" in new_name:
            self._notify(_("Invalid name — must not contain a path separator"))
            logger.debug("SSHKeyManagerDialog._on_rename_key: sortie")
            return

        priv_path_str = str(key.get("path") or "")
        pub_path_str = str(key.get("pub") or "")

        try:
            if priv_path_str:
                priv_path = Path(priv_path_str)
                new_priv = priv_path.with_name(new_name)
                if new_priv.exists():
                    self._notify(_("A file named {name} already exists").format(name=new_name))
                    logger.debug("SSHKeyManagerDialog._on_rename_key: sortie")
                    return
                cert_path = Path(priv_path_str + "-cert.pub")
                new_cert = Path(str(new_priv) + "-cert.pub")
                if cert_path.exists() and new_cert.exists():
                    self._notify(
                        _("A certificate named {name} already exists").format(name=new_cert.name)
                    )
                    logger.debug("SSHKeyManagerDialog._on_rename_key: sortie")
                    return
                new_pub = new_priv.with_name(new_priv.name + ".pub")
                if pub_path_str and Path(pub_path_str).exists() and new_pub.exists():
                    self._notify(_("A file named {name} already exists").format(name=new_pub.name))
                    logger.debug("SSHKeyManagerDialog._on_rename_key: sortie")
                    return

                priv_path.rename(new_priv)
                if pub_path_str and Path(pub_path_str).exists():
                    Path(pub_path_str).rename(new_pub)
                if cert_path.exists():
                    cert_path.rename(new_cert)
            elif pub_path_str:
                pub_path = Path(pub_path_str)
                new_pub = pub_path.with_name(new_name)
                if new_pub.exists():
                    self._notify(_("A file named {name} already exists").format(name=new_name))
                    logger.debug("SSHKeyManagerDialog._on_rename_key: sortie")
                    return
                pub_path.rename(new_pub)
            else:
                self._notify(_("Nothing to rename"))
                logger.debug("SSHKeyManagerDialog._on_rename_key: sortie")
                return
        except OSError as exc:
            self._notify(_("Rename failed: {err}").format(err=str(exc)))
            logger.error(f"_on_rename_key | {exc}")
            logger.debug("SSHKeyManagerDialog._on_rename_key: sortie")
            return

        logger.info(f"_on_rename_key | {old_name} → {new_name}")
        self._notify(_("Renamed {old} to {new}").format(old=old_name, new=new_name))
        self._load_keys()
        logger.debug("SSHKeyManagerDialog._on_rename_key: fin")

    def _on_change_passphrase(self, _widget: Gtk.Widget) -> None:
        """Change la passphrase de la clé privée sélectionnée (ssh-keygen -p)."""
        logger.debug("SSHKeyManagerDialog._on_change_passphrase: entrée")
        key = self._get_selected_key()
        if not key or not key.get("path"):
            self._notify(_("No private key selected"))
            logger.debug("SSHKeyManagerDialog._on_change_passphrase: sortie")
            return
        priv_path = Path(str(key["path"]))
        dlg = _ChangePassphraseDialog(parent=self._top_window(), key_name=str(key["name"]))
        response = run_dialog_sync(dlg)
        old_pass, new_pass, confirm_pass = dlg.get_values()
        dlg.destroy()
        if response != Gtk.ResponseType.OK:
            logger.debug("SSHKeyManagerDialog._on_change_passphrase: sortie")
            return
        if new_pass != confirm_pass:
            self._notify(_("New passphrase and confirmation do not match"))
            logger.debug("SSHKeyManagerDialog._on_change_passphrase: sortie")
            return

        cmd = ["ssh-keygen", "-p", "-f", str(priv_path), "-P", old_pass, "-N", new_pass]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except Exception as exc:
            self._notify(_("Passphrase change failed: {err}").format(err=str(exc)))
            logger.error(f"_on_change_passphrase | {exc}")
            logger.debug("SSHKeyManagerDialog._on_change_passphrase: sortie")
            return
        if result.returncode != 0:
            self._notify(
                _("Passphrase change failed: {err}").format(err=result.stderr.strip()[:120])
            )
            logger.warning(
                f"_on_change_passphrase | ssh-keygen -p failed | stderr={result.stderr.strip()}"
            )
            logger.debug("SSHKeyManagerDialog._on_change_passphrase: sortie")
            return
        self._notify(_("Passphrase changed for {name}").format(name=key["name"]))
        logger.info(f"_on_change_passphrase | changed passphrase for {key['name']}")
        logger.debug("SSHKeyManagerDialog._on_change_passphrase: fin")

    def _is_key_in_agent(self, pub_path: Path) -> bool:
        """Vérifie si le fingerprint de cette clé est actuellement chargé dans ssh-agent.

        Args:
            pub_path: Chemin de la clé publique.

        Returns:
            True si l'agent SSH est joignable et connaît ce fingerprint.
        """
        logger.debug("SSHKeyManagerDialog._is_key_in_agent: entrée")
        try:
            fp_result = subprocess.run(
                ["ssh-keygen", "-lf", str(pub_path)], capture_output=True, text=True, timeout=3
            )
            if fp_result.returncode != 0:
                logger.debug("SSHKeyManagerDialog._is_key_in_agent: sortie")
                return False
            parts = fp_result.stdout.strip().split()
            fingerprint = parts[1] if len(parts) >= 2 else ""
            if not fingerprint:
                logger.debug("SSHKeyManagerDialog._is_key_in_agent: sortie")
                return False
            agent_result = subprocess.run(
                ["ssh-add", "-l"], capture_output=True, text=True, timeout=5
            )
            if agent_result.returncode != 0:
                logger.debug("SSHKeyManagerDialog._is_key_in_agent: sortie")
                return False
            logger.debug("SSHKeyManagerDialog._is_key_in_agent: sortie")
            return fingerprint in agent_result.stdout
        except Exception as exc:
            logger.debug(f"_is_key_in_agent | {exc}")
            logger.debug("SSHKeyManagerDialog._is_key_in_agent: sortie")
            return False
        logger.debug("SSHKeyManagerDialog._is_key_in_agent: fin")

    def _on_toggle_agent(self, _widget: Gtk.Widget) -> None:
        """Charge ou décharge la clé sélectionnée dans ssh-agent (ssh-add / ssh-add -d)."""
        logger.debug("SSHKeyManagerDialog._on_toggle_agent: entrée")
        key = self._get_selected_key()
        if not key or not key.get("path"):
            self._notify(_("No private key selected"))
            logger.debug("SSHKeyManagerDialog._on_toggle_agent: sortie")
            return
        priv_path = Path(str(key["path"]))
        pub_path_str = str(key.get("pub") or "")
        pub_path = (
            Path(pub_path_str) if pub_path_str else priv_path.with_name(priv_path.name + ".pub")
        )
        if not pub_path.exists():
            self._notify(_("No public key available to identify this key in the agent"))
            logger.debug("SSHKeyManagerDialog._on_toggle_agent: sortie")
            return

        loaded = self._is_key_in_agent(pub_path)
        if loaded:
            if not self._confirm(_("Remove {name} from ssh-agent?").format(name=key["name"])):
                logger.debug("SSHKeyManagerDialog._on_toggle_agent: sortie")
                return
            try:
                result = subprocess.run(
                    ["ssh-add", "-d", str(pub_path)], capture_output=True, text=True, timeout=5
                )
            except Exception as exc:
                self._notify(_("ssh-add failed: {err}").format(err=str(exc)))
                logger.error(f"_on_toggle_agent | -d | {exc}")
                logger.debug("SSHKeyManagerDialog._on_toggle_agent: sortie")
                return
            if result.returncode == 0:
                self._notify(_("Removed from ssh-agent"))
            else:
                self._notify(
                    _("Failed to remove from agent: {err}").format(err=result.stderr.strip()[:100])
                )
        else:
            if not self._confirm(_("Add {name} to ssh-agent?").format(name=key["name"])):
                logger.debug("SSHKeyManagerDialog._on_toggle_agent: sortie")
                return
            try:
                result = subprocess.run(
                    ["ssh-add", str(priv_path)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                self._notify(
                    _(
                        "ssh-add timed out — the key may require a passphrase (no terminal available here)"
                    )
                )
                logger.warning(f"_on_toggle_agent | timeout adding {priv_path}")
                logger.debug("SSHKeyManagerDialog._on_toggle_agent: sortie")
                return
            except Exception as exc:
                self._notify(_("ssh-add failed: {err}").format(err=str(exc)))
                logger.error(f"_on_toggle_agent | add | {exc}")
                logger.debug("SSHKeyManagerDialog._on_toggle_agent: sortie")
                return
            if result.returncode == 0:
                self._notify(_("Added to ssh-agent"))
            else:
                self._notify(
                    _("Failed to add to agent (passphrase required?): {err}").format(
                        err=result.stderr.strip()[:100]
                    )
                )
        logger.debug("SSHKeyManagerDialog._on_toggle_agent: fin")

    # ------------------------------------------------------------------
    # Chargement / actions — Known Hosts
    # ------------------------------------------------------------------

    def _load_known_hosts(self) -> None:
        """Recharge le contenu de known_hosts depuis la cible active."""
        logger.debug("SSHKeyManagerDialog._load_known_hosts: entrée")
        self._kh_store.clear()
        fs = self._current_fs()
        content = fs.read_text("known_hosts")
        if content is None:
            self._notify(_("known_hosts not found on {t}").format(t=fs.label()))
            logger.debug("SSHKeyManagerDialog._load_known_hosts: sortie")
            return
        entries = _parse_known_hosts(content)
        for entry in entries:
            self._kh_store.append(
                [
                    str(entry["host_display"]),
                    str(entry["keytype"]),
                    str(entry["fingerprint"]),
                    bool(entry["hashed"]),
                    str(entry["raw"]),
                    int(entry["lineno"]),
                ]
            )
        self._notify(
            _("{n} known_hosts entries loaded from {t}").format(n=len(entries), t=fs.label())
        )
        logger.debug(f"_load_known_hosts | target={fs.label()} n={len(entries)}")
        logger.debug("SSHKeyManagerDialog._load_known_hosts: fin")

    def _on_copy_known_host_fingerprint(self, _widget: Gtk.Widget) -> None:
        """Copie le fingerprint de l'entrée known_hosts sélectionnée."""
        logger.debug("SSHKeyManagerDialog._on_copy_known_host_fingerprint: entrée")
        _model, it = self._kh_tree.get_selection().get_selected()
        if it is None:
            self._notify(_("No known_hosts entry selected"))
            logger.debug("SSHKeyManagerDialog._on_copy_known_host_fingerprint: sortie")
            return
        fingerprint = self._kh_store[it][2]
        if fingerprint and _copy_text_to_clipboard(fingerprint):
            self._notify(_("Fingerprint copied to clipboard"))
        else:
            self._notify(_("No fingerprint available for this entry"))
        logger.debug("SSHKeyManagerDialog._on_copy_known_host_fingerprint: fin")

    def _collect_candidate_hosts(self) -> list[str]:
        """Rassemble les hôtes à tester : ~/.ssh/config + hôtes GCM (si fournis).

        Returns:
            Liste dédupliquée d'alias d'hôtes concrets (sans motifs génériques).
        """
        logger.debug("SSHKeyManagerDialog._collect_candidate_hosts: entrée")
        hosts: list[str] = []
        for host in _parse_ssh_config_hosts(self._ssh_dir / "config"):
            if host not in hosts:
                hosts.append(host)
        if self._gcm_hosts_provider is not None:
            try:
                gcm_hosts = self._gcm_hosts_provider() or []
            except Exception as exc:
                logger.warning(f"_collect_candidate_hosts | gcm_hosts_provider failed: {exc}")
                gcm_hosts = []
            for host in gcm_hosts:
                if host and host not in hosts:
                    hosts.append(host)
        logger.debug("SSHKeyManagerDialog._collect_candidate_hosts: sortie")
        return hosts

    def _on_test_known_hosts(self, _widget: Gtk.Widget) -> None:
        """Teste les hôtes connus (ssh_config + GCM) contre le known_hosts de la cible active.

        Équivalent de :
            while read host; do
                ssh-keygen -F "$host" -f known_hosts >/dev/null && echo "$host trouvé"
            done < hosts.txt
        """
        logger.debug("SSHKeyManagerDialog._on_test_known_hosts: entrée")
        fs = self._current_fs()
        content = fs.read_text("known_hosts")
        if content is None:
            self._notify(_("known_hosts not found on {t}").format(t=fs.label()))
            logger.debug("SSHKeyManagerDialog._on_test_known_hosts: sortie")
            return

        hosts = self._collect_candidate_hosts()
        if not hosts:
            self._notify(
                _("No candidate hosts found in ~/.ssh/config (and no GCM host list wired in)")
            )
            logger.debug("SSHKeyManagerDialog._on_test_known_hosts: sortie")
            return

        tmp_path = ""
        results: list[tuple[str, bool, str]] = []
        try:
            with tempfile.NamedTemporaryFile("w", suffix="_known_hosts", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            for host in hosts:
                try:
                    res = subprocess.run(
                        ["ssh-keygen", "-F", host, "-f", tmp_path],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    found = res.returncode == 0
                    info = res.stdout.strip() if found else ""
                except Exception as exc:
                    logger.debug(
                        "SSHKeyManagerDialog._on_test_known_hosts: except Exception — {}", exc
                    )
                    found, info = False, str(exc)
                results.append((host, found, info))
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.debug("SSHKeyManagerDialog._on_test_known_hosts: except OSError")
                    pass

        logger.info(
            f"_on_test_known_hosts | target={fs.label()} tested={len(results)} found="
            f"{sum((1 for _h, found, _i in results if found))}",
        )
        dlg = _TestKnownHostsDialog(
            parent=self._top_window(), target_label=fs.label(), results=results
        )
        run_dialog_sync(dlg)
        dlg.destroy()
        logger.debug("SSHKeyManagerDialog._on_test_known_hosts: fin")

    def _on_delete_known_host(self, _widget: Gtk.Widget) -> None:
        """Supprime l'entrée known_hosts sélectionnée après confirmation."""
        logger.debug("SSHKeyManagerDialog._on_delete_known_host: entrée")
        _model, it = self._kh_tree.get_selection().get_selected()
        if it is None:
            self._notify(_("No known_hosts entry selected"))
            logger.debug("SSHKeyManagerDialog._on_delete_known_host: sortie")
            return
        raw_line = self._kh_store[it][4]
        if not self._confirm(_("Remove this known_hosts entry?\n<b>This cannot be undone.</b>")):
            logger.debug("SSHKeyManagerDialog._on_delete_known_host: sortie")
            return
        fs = self._current_fs()
        if not self._confirm_root_write(fs, _("remove a known_hosts entry")):
            logger.debug("SSHKeyManagerDialog._on_delete_known_host: sortie")
            return
        content = fs.read_text("known_hosts") or ""
        lines = content.splitlines()
        new_lines = [line for line in lines if line != raw_line]
        if len(new_lines) == len(lines):
            self._notify(_("Entry not found (file may have changed) — refreshing"))
            self._load_known_hosts()
            logger.debug("SSHKeyManagerDialog._on_delete_known_host: sortie")
            return
        new_content = "\n".join(new_lines) + ("\n" if new_lines else "")
        ok = fs.write_text("known_hosts", new_content, mode="644")
        self._notify(_("known_hosts entry removed") if ok else _("Failed to update known_hosts"))
        self._load_known_hosts()
        logger.debug("SSHKeyManagerDialog._on_delete_known_host: fin")

    # ------------------------------------------------------------------
    # Chargement / actions — Authorized Keys
    # ------------------------------------------------------------------

    def _load_authorized_keys(self) -> None:
        """Recharge le contenu de authorized_keys depuis la cible active."""
        logger.debug("SSHKeyManagerDialog._load_authorized_keys: entrée")
        self._ak_store.clear()
        fs = self._current_fs()
        content = fs.read_text("authorized_keys")
        if content is None:
            self._notify(_("authorized_keys not found on {t}").format(t=fs.label()))
            logger.debug("SSHKeyManagerDialog._load_authorized_keys: sortie")
            return
        entries = _parse_authorized_keys(content)
        for entry in entries:
            self._ak_store.append(
                [
                    bool(entry["enabled"]),
                    str(entry["keytype"]),
                    str(entry["fingerprint"]),
                    str(entry["comment"]),
                    str(entry["raw"]),
                    int(entry["lineno"]),
                ]
            )
        self._notify(
            _("{n} authorized_keys entries loaded from {t}").format(n=len(entries), t=fs.label())
        )
        logger.debug(f"_load_authorized_keys | target={fs.label()} n={len(entries)}")
        logger.debug("SSHKeyManagerDialog._load_authorized_keys: fin")

    def _on_toggle_authorized_key(self, _renderer: Gtk.CellRendererToggle, path_str: str) -> None:
        """Active/désactive (commente) l'entrée authorized_keys correspondante.

        Args:
            _renderer: Le CellRendererToggle (non utilisé).
            path_str: Chemin TreeModel (chaîne) de la ligne cliquée.
        """
        logger.debug("SSHKeyManagerDialog._on_toggle_authorized_key: entrée")
        it = self._ak_store.get_iter(path_str)
        raw_line = self._ak_store[it][4]
        lineno = self._ak_store[it][5]
        currently_enabled = self._ak_store[it][0]
        action_label = _("Disable") if currently_enabled else _("Re-enable")
        if not self._confirm(
            _("{action} this authorized_keys entry?").format(action=action_label)
        ):
            logger.debug("SSHKeyManagerDialog._on_toggle_authorized_key: sortie")
            return
        fs = self._current_fs()
        if not self._confirm_root_write(fs, _("enable/disable an authorized_keys entry")):
            logger.debug("SSHKeyManagerDialog._on_toggle_authorized_key: sortie")
            return
        content = fs.read_text("authorized_keys") or ""
        lines = content.splitlines()
        if lineno - 1 >= len(lines) or lines[lineno - 1] != raw_line:
            self._notify(_("File changed on disk — refreshing"))
            self._load_authorized_keys()
            logger.debug("SSHKeyManagerDialog._on_toggle_authorized_key: sortie")
            return
        line = lines[lineno - 1]
        lines[lineno - 1] = (
            line.strip()[1:].strip() if line.strip().startswith("#") else "# " + line
        )
        ok = fs.write_text("authorized_keys", "\n".join(lines) + "\n", mode="600")
        self._notify(
            _("authorized_keys entry updated") if ok else _("Failed to update authorized_keys")
        )
        self._load_authorized_keys()
        logger.debug("SSHKeyManagerDialog._on_toggle_authorized_key: fin")

    def _on_add_authorized_key(self, _widget: Gtk.Widget) -> None:
        """Ouvre le dialogue d'ajout et ajoute la clé saisie à authorized_keys."""
        logger.debug("SSHKeyManagerDialog._on_add_authorized_key: entrée")
        dlg = _AddAuthorizedKeyDialog(parent=self._top_window())
        response = run_dialog_sync(dlg)
        key_line = dlg.get_key_line()
        dlg.destroy()
        if response != Gtk.ResponseType.OK or not key_line:
            logger.debug("SSHKeyManagerDialog._on_add_authorized_key: sortie")
            return
        tokens = key_line.split()
        if not any(tok in _AUTH_KEY_TYPES for tok in tokens):
            self._notify(_("This does not look like a valid public key line"))
            logger.debug("SSHKeyManagerDialog._on_add_authorized_key: sortie")
            return
        fs = self._current_fs()
        if not self._confirm_root_write(fs, _("add a key to authorized_keys")):
            logger.debug("SSHKeyManagerDialog._on_add_authorized_key: sortie")
            return
        content = fs.read_text("authorized_keys") or ""
        separator = "\n" if content and not content.endswith("\n") else ""
        new_content = content + separator + key_line + "\n"
        ok = fs.write_text("authorized_keys", new_content, mode="600")
        self._notify(_("Key added to authorized_keys") if ok else _("Failed to add key"))
        self._load_authorized_keys()
        logger.debug("SSHKeyManagerDialog._on_add_authorized_key: fin")

    def _on_delete_authorized_key(self, _widget: Gtk.Widget) -> None:
        """Supprime l'entrée authorized_keys sélectionnée après confirmation."""
        logger.debug("SSHKeyManagerDialog._on_delete_authorized_key: entrée")
        _model, it = self._ak_tree.get_selection().get_selected()
        if it is None:
            self._notify(_("No authorized_keys entry selected"))
            logger.debug("SSHKeyManagerDialog._on_delete_authorized_key: sortie")
            return
        raw_line = self._ak_store[it][4]
        if not self._confirm(
            _("Remove this authorized_keys entry?\n<b>This cannot be undone.</b>")
        ):
            logger.debug("SSHKeyManagerDialog._on_delete_authorized_key: sortie")
            return
        fs = self._current_fs()
        if not self._confirm_root_write(fs, _("remove an authorized_keys entry")):
            logger.debug("SSHKeyManagerDialog._on_delete_authorized_key: sortie")
            return
        content = fs.read_text("authorized_keys") or ""
        lines = content.splitlines()
        new_lines = [line for line in lines if line != raw_line]
        if len(new_lines) == len(lines):
            self._notify(_("Entry not found (file may have changed) — refreshing"))
            self._load_authorized_keys()
            logger.debug("SSHKeyManagerDialog._on_delete_authorized_key: sortie")
            return
        new_content = "\n".join(new_lines) + ("\n" if new_lines else "")
        ok = fs.write_text("authorized_keys", new_content, mode="600")
        self._notify(
            _("authorized_keys entry removed") if ok else _("Failed to update authorized_keys")
        )
        self._load_authorized_keys()
        logger.debug("SSHKeyManagerDialog._on_delete_authorized_key: fin")

    # ------------------------------------------------------------------
    # Clavier
    # ------------------------------------------------------------------

    def _setup_keyboard(self) -> None:
        """Connecte les raccourcis clavier (Ctrl+N, Ctrl+I, Delete, Escape)."""
        logger.debug("SSHKeyManagerDialog._setup_keyboard: entrée")
        self.connect("key-press-event", self._on_key_press)
        logger.debug("SSHKeyManagerDialog._setup_keyboard: fin")

    def _on_key_press(self, _widget: Gtk.Window, event: Gdk.EventKey) -> bool:
        """Gère les raccourcis clavier.

        Args:
            _widget: Fenêtre source (non utilisée).
            event: Événement clavier.

        Returns:
            True si l'événement est consommé.
        """
        logger.debug("SSHKeyManagerDialog._on_key_press: entrée")
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if event.keyval == Gdk.KEY_Escape:
            self.request_close()
            logger.debug("SSHKeyManagerDialog._on_key_press: sortie")
            return True
        if ctrl and event.keyval == Gdk.KEY_n:
            self._on_generate(None)
            logger.debug("SSHKeyManagerDialog._on_key_press: sortie")
            return True
        if ctrl and event.keyval == Gdk.KEY_i:
            self._on_import(None)
            logger.debug("SSHKeyManagerDialog._on_key_press: sortie")
            return True
        if event.keyval == Gdk.KEY_Delete:
            page = self._notebook.get_current_page()
            if page in (0, 1):
                self._on_delete(None)
            elif page == 2:
                self._on_delete_known_host(None)
            elif page == 3:
                self._on_delete_authorized_key(None)
            logger.debug("SSHKeyManagerDialog._on_key_press: sortie")
            return True
        logger.debug("SSHKeyManagerDialog._on_key_press: sortie")
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _notify(self, msg: str, duration_ms: int = 4000) -> None:
        """Affiche un message dans la barre de statut pendant duration_ms.

        Args:
            msg: Message à afficher.
            duration_ms: Durée d'affichage en millisecondes.
        """
        logger.debug("SSHKeyManagerDialog._notify: entrée")
        self._status_bar.pop(self._status_ctx)
        self._status_bar.push(self._status_ctx, msg)
        GLib.timeout_add(duration_ms, lambda: self._status_bar.pop(self._status_ctx))
        logger.debug("SSHKeyManagerDialog._notify: fin")

    def _confirm(self, markup_text: str) -> bool:
        """Affiche une boîte de confirmation Yes/No.

        Args:
            markup_text: Texte Pango markup de la question.

        Returns:
            True si l'utilisateur a confirmé.
        """
        logger.debug("SSHKeyManagerDialog._confirm: entrée")
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
        )
        dlg.get_message_area().foreach(lambda w: w.destroy())
        lbl = Gtk.Label()
        lbl.set_markup(markup_text)
        lbl.set_line_wrap(True)
        lbl.show()
        dlg.get_message_area().pack_start(lbl, False, False, 0)
        response = run_dialog_sync(dlg)
        dlg.destroy()
        logger.debug("SSHKeyManagerDialog._confirm: sortie")
        return response == Gtk.ResponseType.YES

    def _confirm_root_write(self, fs: _FSAdapter, action_desc: str) -> bool:
        """Demande une confirmation supplémentaire avant d'écrire sur un compte root.

        S'applique aussi bien en local (process exécuté en root) qu'à distance
        (cible ``root@host``), car une erreur sur ce compte peut couper l'accès
        SSH à toute la machine.

        Args:
            fs: Adaptateur cible (local ou distant).
            action_desc: Description courte de l'action, insérée dans le message.

        Returns:
            True si l'écriture peut continuer (pas root, ou confirmée).
        """
        logger.debug("SSHKeyManagerDialog._confirm_root_write: entrée")
        if fs.target_user() != "root":
            logger.debug("SSHKeyManagerDialog._confirm_root_write: sortie")
            return True
        logger.debug("SSHKeyManagerDialog._confirm_root_write: sortie")
        return self._confirm(
            _(
                "⚠ You are about to {action} on the <b>root</b> account of {target}.\n"
                "This can affect SSH access to the whole system.\n<b>Are you sure?</b>"
            ).format(
                action=GLib.markup_escape_text(action_desc),
                target=GLib.markup_escape_text(fs.label()),
            )
        )
