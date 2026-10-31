# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique métier SSH de gcm4, zéro dépendance GTK — parsing de
``~/.ssh/config`` et migration/import des hôtes SSH de gcm.conf.

Fusion (session-26, plan validé le 2026-09-10 — voir
``proposition-architecture-plugins-gtk4.md`` §3.0-bis/§6 et
``docs/gtk4-migration.md`` §3.0-bis/§3.6) des anciens modules à la racine
du dépôt :

- ``ssh_config_parser.py`` — lecture/écriture/validation de
  ``~/.ssh/config`` (adapté de SSH-Studio, BuddySirJava/SSH-Studio, GPLv3).
- ``ssh_migrate_gcm.py`` — migration des hôtes SSH de ``gcm.conf`` vers
  ``~/.ssh/config`` (ou le fichier partagé ``/etc/ssh/ssh_config.d/``), et
  import inverse d'un ``ssh_config`` existant vers ``gcm.conf``.

Les deux modules étaient déjà 100% GTK-free ; le seul lien entre eux était
un import différé (`` from ssh_migrate_gcm import ... # noqa: PLC0415``)
à l'intérieur de trois méthodes de :class:`SSHConfigParser`, documenté à
l'époque comme nécessaire pour que ``ssh_config_parser.py`` reste
utilisable seul même sans ``ssh_migrate_gcm`` disponible. La fusion en un
seul fichier rend cette justification caduque : les appels sont désormais
directs (voir :meth:`SSHConfigParser._is_shared_target`,
:meth:`SSHConfigParser._backup_file`, :meth:`SSHConfigParser._atomic_write`).

Modifications d'origine (``ssh_config_parser.py``) :
- stdlib logging remplacé par loguru (avec fallback)
- Annotations modernisées (list/dict/tuple minuscules, X | Y)
- Docstrings Google ajoutées

``gtk4.py`` (dialogues et widgets réels : éditeur de config SSH, gestion
des clés) n'existe pas encore dans ce dossier — reste une session séparée
(voir ``docs/gtk4-migration.md`` §3.6, point 3), tributaire du portage du
cœur GTK4. ``plugins/plugin_ssh.py`` (toujours GTK3, application réelle
non portée) importe ce module à la place de ``ssh_config_parser``/
``ssh_migrate_gcm``, inchangé sinon.

GTK integration (rappel, migration) — dans ``gnome_connection_manager.py``,
:func:`migrate_ssh_hosts` est appelé une fois au démarrage (ou à la
demande depuis un menu). Le patch GTK du dialogue d'édition d'hôte (grisage
des champs SSH + notice rouge pour un hôte géré par ``~/.ssh/config``) vit
dans ``plugins/plugin_ssh.py`` (``SshPlugin.patch_edit_host_dialog``),
exposé via le hook générique ``ConnectionPlugin.patch_edit_host_dialog``
(voir ``docs/sessions/session-23.md``) — ce module reste un outil de
logique pure (parsing, migration, écriture de fichiers), jamais de GTK.

Usage CLI (inchangé, hérité de ``ssh_migrate_gcm.py``)::

    python3 -m plugins.ssh.core [--gcm-conf PATH] [--ssh-config PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import configparser
import fnmatch  # noqa: F401  (réexporté via __all__ pour usage futur)
import getpass
import glob
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

__all__ = [
    # ssh_config_parser.py
    "SSHOption",
    "SSHHost",
    "SSHConfig",
    "SSHConfigParser",
    # ssh_migrate_gcm.py
    "GCM_TYPE_SSH",
    "DEFAULT_SSH_PORT",
    "SHARED_SSH_CONFIG_PATH",
    "GCM_KEYS_RETAINED",
    "GCM_KEYS_SSH_ONLY",
    "is_shared_target",
    "backup_file",
    "migrate_ssh_hosts",
    "import_ssh_config",
    "main",
]


@dataclass
class SSHOption:
    """Représente une directive clé/valeur dans un bloc Host.

    Attributes:
        key: Nom de la directive SSH (ex. ``HostName``).
        value: Valeur associée.
        indentation: Préfixe d'indentation conservé à la réécriture.
    """

    key: str
    value: str
    indentation: str = "    "

    def __str__(self) -> str:
        """Sérialise la directive avec son indentation d'origine."""
        return f"{self.indentation}{self.key} {self.value}".rstrip()


@dataclass
class SSHHost:
    """Représente un bloc ``Host`` complet avec ses options.

    Attributes:
        patterns: Liste des patterns d'alias (ex. ``["myserver", "*.example.com"]``).
        options: Directives SSH du bloc.
        start_line: Numéro de ligne de début dans le fichier source.
        end_line: Numéro de ligne de fin dans le fichier source.
        raw_lines: Lignes brutes originales (pour reconstruction fidèle).
    """

    patterns: list[str] = field(default_factory=list)
    options: list[SSHOption] = field(default_factory=list)
    start_line: int = -1
    end_line: int = -1
    raw_lines: list[str] = field(default_factory=list)

    @classmethod
    def from_raw_lines(cls, lines: list[str]) -> SSHHost:
        """Construit un SSHHost depuis une liste de lignes brutes.

        Args:
            lines: Lignes brutes d'un seul bloc Host (avec commentaires).

        Returns:
            Instance SSHHost peuplée.

        Raises:
            ValueError: Si aucune directive ``Host`` n'est trouvée ou s'il y en a plusieurs.
        """
        logger.debug(
            "SSHHost.from_raw_lines: entrée | lines={} élément(s)",
            len(lines) if lines is not None else 0,
        )
        host = cls()
        found_host_line = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                host.raw_lines.append(line)
                continue

            if stripped.lower().startswith("host ") and not found_host_line:
                patterns = stripped.split(None, 1)[1].split()
                host.patterns = patterns
                host.raw_lines.append(line)
                found_host_line = True
                continue
            elif stripped.lower().startswith("host ") and found_host_line:
                raise ValueError(
                    "Multiple Host declarations found within a single raw host block."
                )

            m = re.match(r"^(\S+)\s+(.+)$", stripped)
            if m:
                key, value = m.group(1), m.group(2)
                indentation = line[: len(line) - len(line.lstrip())]
                host.options.append(SSHOption(key=key, value=value, indentation=indentation))
                host.raw_lines.append(line)
            else:
                host.raw_lines.append(line)

        if not found_host_line:
            raise ValueError("No Host declaration found in raw host block.")

        logger.debug("SSHHost.from_raw_lines: retour host")
        return host

    def get_option(self, key: str) -> str | None:
        """Retourne la valeur d'une directive (insensible à la casse).

        Args:
            key: Nom de la directive à rechercher.

        Returns:
            Valeur de la directive, ou ``None`` si absente.
        """
        logger.trace("SSHHost.get_option: entrée")
        for opt in self.options:
            if opt.key.lower() == key.lower():
                logger.trace("SSHHost.get_option: retour")
                return opt.value
        logger.trace("SSHHost.get_option: retour None")
        return None

    def set_option(self, key: str, value: str) -> None:
        """Définit ou ajoute une directive.

        Args:
            key: Nom de la directive.
            value: Valeur à affecter.
        """
        logger.debug("SSHHost.set_option: entrée | valeur={}", bool(value))
        for opt in self.options:
            if opt.key.lower() == key.lower():
                opt.value = value
                logger.debug("SSHHost.set_option: sortie")
                return
        self.options.append(SSHOption(key=key, value=value))
        logger.debug("SSHHost.set_option: fin")

    def remove_option(self, key: str) -> bool:
        """Supprime une directive si elle existe.

        Args:
            key: Nom de la directive à supprimer.

        Returns:
            ``True`` si la directive a été trouvée et supprimée, ``False`` sinon.
        """
        logger.debug("SSHHost.remove_option: entrée")
        for i, opt in enumerate(self.options):
            if opt.key.lower() == key.lower():
                del self.options[i]
                logger.debug("SSHHost.remove_option: retour True")
                return True
        logger.debug("SSHHost.remove_option: retour False")
        return False

    def get_options(self, key: str) -> list[str]:
        """Retourne toutes les valeurs d'une directive pouvant apparaître
        plusieurs fois dans un même bloc Host (ex. ``LocalForward``,
        ``RemoteForward``, ``DynamicForward``, ``IdentityFile``).

        Args:
            key: Nom de la directive à rechercher.

        Returns:
            Liste des valeurs (ordre d'apparition), vide si absente.
        """
        return [opt.value for opt in self.options if opt.key.lower() == key.lower()]

    def set_options(self, key: str, values: list[str]) -> None:
        """Remplace toutes les occurrences d'une directive multi-valeurs.

        Supprime toutes les directives existantes portant ce nom, puis les
        recrée (dans l'ordre) à l'emplacement de la première occurrence
        d'origine (ou en fin de liste si absente). Utilisé pour les champs
        du formulaire qui acceptent plusieurs valeurs séparées par des
        virgules et doivent être réécrits en plusieurs lignes distinctes.

        Args:
            key: Nom de la directive.
            values: Valeurs à écrire, une par ligne, dans l'ordre fourni.
        """
        logger.debug(
            "SSHHost.set_options: entrée | values={} élément(s)",
            len(values) if values is not None else 0,
        )
        insert_at = next(
            (i for i, opt in enumerate(self.options) if opt.key.lower() == key.lower()),
            len(self.options),
        )
        self.options = [opt for opt in self.options if opt.key.lower() != key.lower()]
        for offset, value in enumerate(values):
            self.options.insert(insert_at + offset, SSHOption(key=key, value=value))
        logger.debug("SSHHost.set_options: fin")


@dataclass
class SSHConfig:
    """Représente l'intégralité d'un fichier ``~/.ssh/config`` parsé.

    Attributes:
        file_path: Chemin vers le fichier de configuration.
        hosts: Liste ordonnée des blocs Host.
        global_options: Directives hors de tout bloc Host.
        include_directives: Directives ``Include`` détectées.
        includes_resolved: Contenu des fichiers inclus (chemin → lignes).
        original_lines: Lignes brutes d'origine (pour détection de modifications).
    """

    file_path: Path
    hosts: list[SSHHost] = field(default_factory=list)
    global_options: list[SSHOption] = field(default_factory=list)
    include_directives: list[str] = field(default_factory=list)
    includes_resolved: dict[Path, list[str]] = field(default_factory=dict)
    original_lines: list[str] = field(default_factory=list)

    def generate_content(self) -> str:
        """Génère le contenu texte du fichier de configuration.

        Returns:
            Contenu SSH config prêt à être écrit sur disque.
        """
        logger.debug("SSHConfig.generate_content: entrée")
        lines: list[str] = []
        for opt in self.global_options:
            lines.append(str(opt))
        if self.global_options and (not lines or lines[-1] != ""):
            lines.append("")
        for host in self.hosts:
            lines.append(f"Host {' '.join(host.patterns)}")
            for opt in host.options:
                lines.append(str(opt))
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()
        for inc in self.include_directives:
            lines.append(f"Include {inc}")
        logger.debug("SSHConfig.generate_content: retour calculé")
        return "\n".join(lines) + "\n"

    def is_dirty(self) -> bool:
        """Indique si le contenu a été modifié depuis le chargement.

        Returns:
            ``True`` si des modifications non sauvegardées existent.
        """
        logger.debug("SSHConfig.is_dirty: entrée")
        current = self.generate_content().splitlines()
        original = [line.rstrip("\n") for line in self.original_lines]
        while current and current[-1] == "":
            current.pop()
        while original and original[-1] == "":
            original.pop()
        logger.debug("SSHConfig.is_dirty: retour calculé")
        return current != original

    def get_host(self, alias: str) -> SSHHost | None:
        """Recherche un bloc Host par son alias exact.

        Args:
            alias: Pattern d'alias à rechercher.

        Returns:
            Le SSHHost correspondant, ou ``None`` si introuvable.
        """
        logger.debug("SSHConfig.get_host: entrée | alias={!r}", alias)
        for h in self.hosts:
            if alias in h.patterns:
                logger.debug("SSHConfig.get_host: retour h")
                return h
        logger.debug("SSHConfig.get_host: retour None")
        return None

    def add_host(self, host: SSHHost) -> None:
        """Ajoute un bloc Host à la fin de la liste.

        Args:
            host: Bloc SSH à ajouter.
        """
        logger.debug("SSHConfig.add_host: entrée")
        self.hosts.append(host)
        logger.debug("SSHConfig.add_host: fin")

    def remove_host(self, host: SSHHost) -> bool:
        """Supprime un bloc Host de la liste.

        Args:
            host: Bloc SSH à supprimer.

        Returns:
            ``True`` si trouvé et supprimé, ``False`` sinon.
        """
        logger.debug("SSHConfig.remove_host: entrée")
        try:
            self.hosts.remove(host)
            logger.debug("SSHConfig.remove_host: retour True")
            return True
        except ValueError:
            logger.debug("SSHConfig.remove_host | hôte absent de la configuration, rien à retirer")
            return False


class SSHConfigParser:
    """Lit, valide et écrit ``~/.ssh/config`` de façon sûre.

    Attributes:
        config_path: Chemin vers le fichier de configuration SSH.
        config: Objet SSHConfig en mémoire après parsing.
        auto_backup_enabled: Si ``True``, un backup est créé avant chaque écriture.
        backup_dir: Répertoire de backup (par défaut : même dossier que config).
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialise le parser.

        Args:
            config_path: Chemin explicite vers le fichier config SSH.
                Par défaut ``~/.ssh/config``.
        """
        logger.debug("SSHConfigParser.__init__: entrée | config_path={!r}", config_path)
        self.config_path: Path = config_path or Path.home() / ".ssh" / "config"
        self.config: SSHConfig = SSHConfig(file_path=self.config_path)
        self._have_backed_up_this_session: bool = False
        self.auto_backup_enabled: bool = True
        self.backup_dir: Path | None = None
        logger.debug("SSHConfigParser.__init__: fin")

    def parse(self) -> SSHConfig:
        """Lit et parse le fichier de configuration SSH.

        Returns:
            Objet SSHConfig peuplé depuis le fichier.
        """
        logger.debug("SSHConfigParser.parse: entrée")
        if not self.config_path.exists():
            logger.warning(f"SSH config file not found: {self.config_path}")
            return self.config

        with self.config_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        self.config.original_lines = [line.rstrip("\n") for line in lines]

        self._parse_main_lines(self.config.original_lines)
        self._resolve_includes()
        logger.debug("SSHConfigParser.parse: retour")
        return self.config

    def write(self, backup: bool = True) -> None:
        """Écrit la configuration sur disque (écriture atomique).

        Crée un backup automatique lors du premier appel de la session
        si ``auto_backup_enabled`` est ``True``.

        Args:
            backup: Si ``False``, désactive le backup pour cet appel.
        """
        logger.debug("SSHConfigParser.write: entrée | backup={!r}", backup)
        content = self._generate_content()

        if self.config_path.exists():
            try:
                with self.config_path.open("r", encoding="utf-8") as f:
                    current = f.read()
                if current == content:
                    logger.debug("SSHConfigParser.write: sortie")
                    return
            except OSError as exc:
                logger.debug(
                    "relecture de {} impossible ({}), réécriture complète", self.config_path, exc
                )

        effective_backup = backup and self.auto_backup_enabled and self.config_path.exists()
        if effective_backup and not self._have_backed_up_this_session:
            self._backup_file()
            self._have_backed_up_this_session = True

        self._atomic_write(content)
        logger.debug("SSHConfigParser.write: fin")

    def validate(self) -> list[str]:
        """Valide la configuration chargée.

        Vérifie :
        - Absence de doublons d'alias
        - Ports dans la plage 1–65535
        - Existence des IdentityFile référencés

        Returns:
            Liste de messages d'erreur (vide si tout est valide).
        """
        logger.debug("SSHConfigParser.validate: entrée")
        errors: list[str] = []
        seen: dict[str, SSHHost] = {}

        for host in self.config.hosts:
            for pat in host.patterns:
                if pat in seen:
                    errors.append(f"Duplicate host alias: {pat}")
                else:
                    seen[pat] = host

        for host in self.config.hosts:
            port = host.get_option("Port")
            if port:
                try:
                    p = int(port)
                    if p < 1 or p > 65535:
                        errors.append(f"Invalid port for host {host.patterns[0]}: {port}")
                except ValueError:
                    logger.debug("port non entier pour l'hôte {} : {!r}", host.patterns[0], port)
                    errors.append(f"Port is not an integer for host {host.patterns[0]}: {port}")

        for host in self.config.hosts:
            ident = host.get_option("IdentityFile")
            if ident:
                path = Path(ident).expanduser()
                if not path.is_absolute():
                    path = Path.home() / ".ssh" / ident
                if not path.exists():
                    errors.append(f"IdentityFile not found for host {host.patterns[0]}: {ident}")

        logger.debug("SSHConfigParser.validate: retour errors")
        return errors

    # ------------------------------------------------------------------
    # Méthodes privées
    # ------------------------------------------------------------------

    def _parse_main_lines(self, lines: list[str]) -> None:
        r"""Parse les lignes brutes et peuple self.config.

        Args:
            lines: Lignes du fichier config (sans ``\n`` terminaux).
        """
        logger.debug(
            "SSHConfigParser._parse_main_lines: entrée | lines={} élément(s)",
            len(lines) if lines is not None else 0,
        )
        self.config.hosts.clear()
        self.config.global_options.clear()
        self.config.include_directives.clear()

        current_host: SSHHost | None = None
        in_host = False

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                if in_host and current_host is not None:
                    current_host.raw_lines.append(line)
                continue

            if stripped.lower().startswith("include "):
                include_arg = stripped.split(None, 1)[1]
                self.config.include_directives.append(include_arg)
                continue

            if stripped.lower().startswith("host "):
                if current_host is not None:
                    current_host.end_line = idx - 1
                    self.config.hosts.append(current_host)
                patterns = stripped.split(None, 1)[1].split()
                current_host = SSHHost(patterns=patterns, start_line=idx, raw_lines=[line])
                in_host = True
                continue

            m = re.match(r"^(\S+)\s+(.+)$", stripped)
            if m:
                key, value = m.group(1), m.group(2)
                indentation = line[: len(line) - len(line.lstrip())]
                opt = SSHOption(key=key, value=value, indentation=indentation)
                if in_host and current_host is not None:
                    current_host.options.append(opt)
                    current_host.raw_lines.append(line)
                else:
                    self.config.global_options.append(opt)
                continue

            if in_host and current_host is not None:
                current_host.raw_lines.append(line)

        if current_host is not None:
            current_host.end_line = len(lines) - 1
            self.config.hosts.append(current_host)
        logger.debug("SSHConfigParser._parse_main_lines: fin")

    def _resolve_includes(self) -> None:
        """Résout les directives Include et stocke le contenu des fichiers inclus."""
        logger.debug("SSHConfigParser._resolve_includes: entrée")
        resolved: dict[Path, list[str]] = {}
        base_dir = self.config_path.parent

        for pattern in self.config.include_directives:
            expanded = os.path.expanduser(pattern)
            if not os.path.isabs(expanded):
                expanded = str(base_dir / expanded)

            try:
                matches = glob.glob(expanded, recursive=False)
                if not matches and "**" in expanded:
                    matches = glob.glob(expanded, recursive=True)
            except OSError as exc:
                logger.warning("Include {} ignoré, motif illisible : {}", expanded, exc)
                matches = []

            for path_str in matches:
                p = Path(path_str)
                try:
                    with p.open("r", encoding="utf-8") as f:
                        resolved[p] = f.readlines()
                except OSError as exc:
                    logger.warning("Include {} ignoré, fichier illisible : {}", p, exc)
                    continue

        self.config.includes_resolved = resolved
        logger.debug("SSHConfigParser._resolve_includes: fin")

    def _is_shared_target(self) -> bool:
        """Return True if ``config_path`` est le fichier ssh_config partagé.

        Délègue à :func:`is_shared_target` (chemin sous
        ``/etc/ssh/ssh_config.d/``) — même module depuis la fusion en
        ``plugins/ssh/core.py`` (session-26), plus d'import différé
        nécessaire.

        Returns:
            True si le fichier doit être traité comme une ressource
            partagée root-owned (écriture via pkexec).
        """
        return is_shared_target(self.config_path)

    def _backup_file(self) -> None:
        """Crée une copie de sauvegarde horodatée du fichier config.

        Pour le fichier partagé (:data:`SHARED_SSH_CONFIG_PATH`, cf. point
        4.a), la copie est elle aussi écrite via ``pkexec`` puisque son
        répertoire est root-owned.
        """
        logger.debug("SSHConfigParser._backup_file: entrée")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        target_dir: Path
        if self.backup_dir:
            target_dir = Path(self.backup_dir).expanduser()
        else:
            target_dir = self.config_path.parent

        if self._is_shared_target():
            backup = (target_dir / self.config_path.name).with_suffix(f".{ts}.bak")
            try:
                content = self.config_path.read_text(encoding="utf-8")
                _write_text_via_pkexec(backup, content, mode=0o644)
                logger.info(f"Backup created (shared, via pkexec): {backup}")
            except OSError as exc:
                logger.warning(f"Failed to create shared backup: {exc}")
            logger.debug("SSHConfigParser._backup_file: sortie")
            return

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "répertoire de sauvegarde {} indisponible ({}), repli sur {}",
                target_dir,
                exc,
                self.config_path.parent,
            )
            target_dir = self.config_path.parent
        backup = (target_dir / self.config_path.name).with_suffix(f".{ts}.bak")
        try:
            shutil.copy2(self.config_path, backup)
            logger.info(f"Backup created: {backup}")
        except OSError as exc:
            logger.warning(f"Failed to create backup: {exc}")

    def _generate_content(self) -> str:
        """Génère le contenu final à écrire.

        Returns:
            Contenu SSH config sous forme de chaîne.
        """
        return self.config.generate_content()

    def _atomic_write(self, content: str) -> None:
        """Écrit ``content`` dans un fichier temporaire puis le déplace atomiquement.

        Pour le fichier ssh_config partagé (point 4/4.a), l'écriture directe
        est impossible (répertoire root-owned) : on délègue à
        :func:`_write_text_via_pkexec` à la place, qui gère la création du
        répertoire (``0755``) et du fichier (``0644``) via ``pkexec``.

        Args:
            content: Contenu complet à écrire.

        Raises:
            OSError: En cas d'échec d'écriture ou de déplacement.
        """
        logger.debug(
            "SSHConfigParser._atomic_write: entrée | content={} caractère(s)", len(content)
        )
        if self._is_shared_target():
            _write_text_via_pkexec(self.config_path, content, mode=0o644)
            logger.debug("SSHConfigParser._atomic_write: sortie")
            return

        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(self.config_path.parent),
            delete=False,
        )
        tmp_path = Path(tmp.name)
        try:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            if self.config_path.exists():
                st = self.config_path.stat()
                os.chmod(tmp_path, stat.S_IMODE(st.st_mode))
            else:
                os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.config_path)
        except Exception as exc:
            logger.error("écriture atomique de {} échouée : {}", self.config_path, exc)
            try:
                tmp.close()
            except Exception as close_exc:
                logger.debug("fermeture du fichier temporaire {} : {}", tmp_path, close_exc)
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception as unlink_exc:
                logger.warning("fichier temporaire {} non supprimé : {}", tmp_path, unlink_exc)
            raise
        logger.debug("SSHConfigParser._atomic_write: fin")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GCM_TYPE_SSH = 0

DEFAULT_SSH_PORT = 22

# Cible "partagée" : profite à tous les utilisateurs de la machine (bastion
# d'équipe) via le mécanisme `Include` d'OpenSSH (voir ~/.ssh/config ou
# /etc/ssh/ssh_config qui doit contenir une ligne
# ``Include /etc/ssh/ssh_config.d/*.conf``). Contrairement à ~/.ssh/config,
# l'écriture y nécessite les droits root — cf. _write_text_privileged().
SHARED_SSH_CONFIG_PATH = Path("/etc/ssh/ssh_config.d/config_gcm.conf")

# Description injectée dans gcm.conf pour les hôtes importés depuis un
# fichier ssh_config existant (personnel ou partagé) — cf. import_ssh_config().
_IMPORT_DESCRIPTION_TEMPLATE = "⇩ Import depuis {source}"

# Keys kept in gcm.conf after migration (all others are removed for SSH hosts)
GCM_KEYS_RETAINED = {
    "name",
    "host",
    "user",
    "password",
    "type",
    "port",
    "group",
    # sentinel + UI hint
    "ssh_config_managed",
    "description",
    # non-SSH protocol fields that may coexist in a fork
    "rdp-domain",
    "rdp-width",
    "rdp-height",
    "vnc-password",
    "spice-password",
    "serial-device",
    "serial-baud",
}

# Keys migrated to ~/.ssh/config (removed from gcm.conf for SSH hosts)
GCM_KEYS_SSH_ONLY = {
    "private-key",
    "tunnel",
    "x11",
    "agent",
    "compression",
    "compressionlevel",
    "keepalive",
    "extra-params",
}

# Sentinel markers in ~/.ssh/config
_SSH_MARKER_BEGIN = "# === BEGIN gcm-managed-hosts ==="
_SSH_MARKER_END = "# === END gcm-managed-hosts ==="

# Description injected into gcm.conf for migrated hosts (read by GCM UI)
_MANAGED_DESCRIPTION = "⚙ SSH config: ~/.ssh/config → Host {name}"

# ---------------------------------------------------------------------------
# Helpers — configparser read / write
# ---------------------------------------------------------------------------


def _cp_get(cp: configparser.RawConfigParser, section: str, key: str, default: str = "") -> str:
    """Read a string value with a safe fallback."""
    logger.trace("_cp_get: entrée | section={!r}, default={!r}", section, default)
    try:
        logger.trace("_cp_get: retour de get()")
        return cp.get(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError) as exc:
        logger.debug("_cp_get | section {} : {}, valeur par défaut", section, type(exc).__name__)
        return default


def _cp_getbool(
    cp: configparser.RawConfigParser,
    section: str,
    key: str,
    default: bool = False,
) -> bool:
    """Read a boolean value with a safe fallback."""
    logger.trace("_cp_getbool: entrée | section={!r}, default={!r}", section, default)
    try:
        logger.trace("_cp_getbool: retour de getboolean()")
        return cp.getboolean(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as exc:
        logger.debug(
            "_cp_getbool | section {} : {}, valeur par défaut", section, type(exc).__name__
        )
        return default


def _cp_getint(
    cp: configparser.RawConfigParser,
    section: str,
    key: str,
    default: int = 0,
) -> int:
    """Read an integer value with a safe fallback."""
    logger.trace("_cp_getint: entrée | section={!r}, default={!r}", section, default)
    try:
        logger.trace("_cp_getint: retour de int()")
        return int(cp.get(section, key))
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError) as exc:
        logger.debug(
            "_cp_getint | section {} : {}, valeur par défaut", section, type(exc).__name__
        )
        return default


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def is_shared_target(path: Path) -> bool:
    """Return True when *path* lives under the shared ssh_config.d directory.

    Args:
        path: Path to test (typically a gcm.conf ``ssh_config`` target).

    Returns:
        True if the path must be written with elevated privileges (root).
    """
    logger.debug("is_shared_target: entrée | path={!r}", path)
    try:
        path.relative_to(SHARED_SSH_CONFIG_PATH.parent)
    except ValueError:
        logger.debug(
            "{} hors de {} : écriture sans élévation", path, SHARED_SSH_CONFIG_PATH.parent
        )
        return False
    logger.debug("is_shared_target: retour True")
    return True


def _write_text_via_pkexec(path: Path, content: str, mode: int) -> None:
    """Write *content* to *path* using ``pkexec`` (polkit privilege escalation).

    Used for targets under ``/etc/ssh/ssh_config.d/`` where the GCM process
    (running as a normal user) has no write access. Requires a graphical
    polkit agent to be running (standard on GNOME/KDE sessions).

    Args:
        path: Destination path (e.g. :data:`SHARED_SSH_CONFIG_PATH`).
        content: File content to write.
        mode: Octal permission mode to apply to the final file (e.g. ``0o644``).

    Raises:
        OSError: When pkexec is unavailable, refused, or the write fails.
    """
    logger.debug(
        "_write_text_via_pkexec: entrée | path={!r}, content={} caractère(s), mode={!r}",
        path,
        len(content),
        mode,
    )
    fd, tmp_name = tempfile.mkstemp(suffix=".conf", prefix="gcm_ssh_config_")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        script = (
            f"install -d -m 0755 -o root -g root {shlex.quote(str(path.parent))} && "
            f"install -m {oct(mode)[2:]} -o root -g root {shlex.quote(str(tmp_path))} {shlex.quote(str(path))}"
        )
        logger.debug(f"_write_text_via_pkexec | escalating | path={path}")
        proc = subprocess.run(
            ["pkexec", "sh", "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise OSError(
                f"pkexec write to {path} failed (rc={proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
            )
        logger.info(f"_write_text_via_pkexec | written | path={path}")
    finally:
        tmp_path.unlink(missing_ok=True)


def _write_text_privileged(path: Path, content: str, mode: int, *, shared: bool) -> None:
    """Write *content* to *path*, escalating via pkexec for shared targets.

    Args:
        path: Destination file.
        content: Text content to write.
        mode: Octal permission mode for the final file.
        shared: When True, always go through :func:`_write_text_via_pkexec`
            (the shared target is root-owned by design — no direct write is
            attempted, so a partially-privileged/inconsistent state can't
            happen).

    Raises:
        OSError: When the write (direct or privileged) fails.
    """
    logger.debug(
        "_write_text_privileged: entrée | path={!r}, content={} caractère(s), mode={!r}, shared={!r}",
        path,
        len(content),
        mode,
        shared,
    )
    if not shared:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)
        logger.debug("_write_text_privileged: sortie")
        return
    _write_text_via_pkexec(path, content, mode)
    logger.debug("_write_text_privileged: fin")


def backup_file(path: Path, *, shared: bool = False) -> Path | None:
    """Create a timestamped backup copy of *path* next to the original.

    The backup name follows the pattern ``<name>.bak.<YYYYMMDD_HHMMSS>``.
    If *path* does not exist the function is a no-op and returns ``None``
    (point 4.a : pas de backup si le fichier n'existait pas encore).

    Args:
        path: File to back up.
        shared: When True, *path* is the root-owned shared file
            (:data:`SHARED_SSH_CONFIG_PATH`) — the backup copy is written via
            pkexec instead of ``shutil.copy2``, since the backup also lands
            in a root-owned directory.

    Returns:
        Path of the backup file, or ``None`` if *path* did not exist.

    Raises:
        OSError: When the copy operation fails.
    """
    logger.debug("backup_file: entrée | path={!r}, shared={!r}", path, shared)
    if not path.exists():
        logger.debug(f"backup_file | source does not exist, skipping | path={path}")
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = path.with_name(f"{path.name}.bak.{stamp}")

    if shared:
        content = path.read_text(encoding="utf-8")  # 0644 root-owned → lisible par tous
        _write_text_via_pkexec(dest, content, mode=0o644)
    else:
        shutil.copy2(str(path), str(dest))

    logger.info(f"backup_file | created | src={path} dst={dest} shared={shared}")
    return dest


# ---------------------------------------------------------------------------
# SSH config rendering
# ---------------------------------------------------------------------------


def _render_ssh_stanza(
    name: str, host: str, cp: configparser.RawConfigParser, section: str
) -> str:
    """Render a single ``Host`` stanza for ~/.ssh/config.

    Args:
        name: Host name (used as the ``Host`` keyword value).
        host: Hostname / IP address.
        cp: Config parser holding the full gcm.conf.
        section: The ``[host …]`` section name for this entry.

    Returns:
        Multi-line string for the stanza (no trailing newline).
    """
    logger.debug(
        "_render_ssh_stanza: entrée | name={!r}, host={!r}, section={!r}", name, host, section
    )
    safe_name = name.replace(" ", "_")
    port = _cp_getint(cp, section, "port", DEFAULT_SSH_PORT)
    user = _cp_get(cp, section, "user", getpass.getuser())
    private_key = _cp_get(cp, section, "private_key")
    tunnel = _cp_get(cp, section, "tunnel")
    x11 = _cp_getbool(cp, section, "x11")
    agent = _cp_getbool(cp, section, "agent")
    compression = _cp_getbool(cp, section, "compression")
    keep_alive = _cp_getint(cp, section, "keepalive")
    extra_params = _cp_get(cp, section, "extra_params")
    group = _cp_get(cp, section, "group")
    description = _cp_get(cp, section, "description")

    # Options SSH avancees (onglet "SSH" de Edit host / editeur ~/.ssh/config).
    proxy_jump_direct = _cp_get(cp, section, "proxy_jump")
    proxy_command = _cp_get(cp, section, "proxy_command")
    strict_host_key_checking = _cp_get(cp, section, "strict_host_key_checking")
    user_known_hosts_file = _cp_get(cp, section, "user_known_hosts_file")
    connect_timeout = _cp_get(cp, section, "connect_timeout")
    request_tty = _cp_get(cp, section, "request_tty")
    remote_command = _cp_get(cp, section, "remote_command")
    control_master = _cp_get(cp, section, "control_master")
    control_persist = _cp_get(cp, section, "control_persist")
    pubkey_authentication = _cp_get(cp, section, "pubkey_authentication")
    password_authentication = _cp_get(cp, section, "password_authentication")
    ssh_log_level = _cp_get(cp, section, "ssh_log_level")
    add_keys_to_agent = _cp_get(cp, section, "add_keys_to_agent")
    server_alive_count_max = _cp_get(cp, section, "server_alive_count_max")
    identities_only = _cp_get(cp, section, "identities_only")

    lines: list[str] = []

    # Header comment
    meta_parts: list[str] = []
    if group:
        meta_parts.append(f"group={group}")
    if description:
        meta_parts.append(description)
    comment = "# GCM"
    if meta_parts:
        comment += " | " + " | ".join(meta_parts)
    lines.append(comment)

    lines.append(f"Host {safe_name}")
    lines.append(f"    HostName {host}")

    if user:
        lines.append(f"    User {user}")

    if port != DEFAULT_SSH_PORT:
        lines.append(f"    Port {port}")

    if private_key:
        expanded = os.path.expanduser(private_key)
        lines.append(f"    IdentityFile {expanded}")
        lines.append(f"    IdentitiesOnly {identities_only or 'yes'}")
    else:
        lines.append("    # No IdentityFile — run ssh-copy-id to install a public key")
        if identities_only:
            lines.append(f"    IdentitiesOnly {identities_only}")

    # Jump host : le champ direct ProxyJump (onglet SSH) prime sur l'ancien
    # format "tunnel" si les deux sont renseignes.
    proxy = proxy_jump_direct or (_parse_tunnel_to_proxyjump(tunnel) if tunnel else "")
    if proxy:
        lines.append(f"    ProxyJump {proxy}")

    if x11:
        lines.append("    ForwardX11 yes")
        lines.append("    ForwardX11Trusted yes")

    if agent:
        lines.append("    ForwardAgent yes")

    if compression:
        lines.append("    Compression yes")

    if keep_alive > 0:
        lines.append(f"    ServerAliveInterval {keep_alive}")
        lines.append(f"    ServerAliveCountMax {server_alive_count_max or 3}")

    for keyword, value in (
        ("ProxyCommand", proxy_command),
        ("StrictHostKeyChecking", strict_host_key_checking),
        ("UserKnownHostsFile", user_known_hosts_file),
        ("ConnectTimeout", connect_timeout),
        ("RequestTTY", request_tty),
        ("RemoteCommand", remote_command),
        ("ControlMaster", control_master),
        ("ControlPersist", control_persist),
        ("PubkeyAuthentication", pubkey_authentication),
        ("PasswordAuthentication", password_authentication),
        ("LogLevel", ssh_log_level),
        ("AddKeysToAgent", add_keys_to_agent),
    ):
        if value:
            lines.append(f"    {keyword} {value}")

    if extra_params:
        lines.append(f"    # GCM extra-params: {extra_params}")

    logger.debug("_render_ssh_stanza: retour de join()")
    return "\n".join(lines)


def _parse_tunnel_to_proxyjump(raw: str) -> str:
    """Convert a GCM tunnel string to an SSH ``ProxyJump`` value.

    GCM format: ``user@jumphost:local_port:dest_host:dest_port``

    Args:
        raw: Raw tunnel string from gcm.conf.

    Returns:
        ProxyJump string (e.g. ``ops@bastion.example.com:2222``), or empty
        string if *raw* is malformed.
    """
    logger.debug("_parse_tunnel_to_proxyjump: entrée | raw={!r}", raw)
    if not raw:
        logger.debug("_parse_tunnel_to_proxyjump: retour ''")
        return ""
    try:
        at_idx = raw.find("@")
        jump_user = raw[:at_idx] if at_idx != -1 else ""
        rest = raw[at_idx + 1 :] if at_idx != -1 else raw

        colon_idx = rest.find(":")
        jump_host = rest[:colon_idx] if colon_idx != -1 else rest
        jump_port = DEFAULT_SSH_PORT

        if colon_idx != -1:
            after_first_colon = rest[colon_idx + 1 :]
            next_colon = after_first_colon.find(":")
            port_str = after_first_colon[:next_colon] if next_colon != -1 else after_first_colon
            try:
                jump_port = int(port_str)
            except ValueError:
                logger.debug("port de rebond non entier {!r}, port SSH par défaut", port_str)
                jump_port = DEFAULT_SSH_PORT

        if not jump_host:
            logger.debug("_parse_tunnel_to_proxyjump: retour ''")
            return ""

        user_prefix = f"{jump_user}@" if jump_user else ""
        port_suffix = f":{jump_port}" if jump_port != DEFAULT_SSH_PORT else ""
        logger.debug("_parse_tunnel_to_proxyjump: retour")
        return f"{user_prefix}{jump_host}{port_suffix}"
    except Exception as exc:
        logger.warning(f"_parse_tunnel_to_proxyjump | parse error | raw={raw} exc={exc}")
        return ""


# ---------------------------------------------------------------------------
# ~/.ssh/config writer
# ---------------------------------------------------------------------------


def _write_ssh_config(ssh_config_path: Path, stanzas: dict[str, str]) -> None:
    """Merge SSH stanzas into the target ssh_config-style file.

    The block between ``_SSH_MARKER_BEGIN`` / ``_SSH_MARKER_END`` is replaced
    on every call; content outside the markers is preserved.

    Target-aware (point 4/4.a) : quand *ssh_config_path* pointe vers
    :data:`SHARED_SSH_CONFIG_PATH` (ou plus généralement sous
    ``/etc/ssh/ssh_config.d/``), le fichier est traité comme une ressource
    partagée entre utilisateurs d'un bastion : répertoire ``0755``,
    fichier ``0644`` (lisible par tous), écriture via ``pkexec`` puisque le
    process GCM tourne en utilisateur normal. Pour ``~/.ssh/config``
    (cible personnelle) le comportement historique est inchangé :
    répertoire ``0700``, fichier ``0600``, écriture directe.

    Args:
        ssh_config_path: Chemin du fichier ssh_config cible (personnel ou
            partagé).
        stanzas: Mapping alias → stanza rendue.

    Raises:
        OSError: When the file cannot be written.
    """
    logger.debug(
        "_write_ssh_config: entrée | ssh_config_path={!r}, stanzas={} élément(s)",
        ssh_config_path,
        len(stanzas) if stanzas is not None else 0,
    )
    shared = is_shared_target(ssh_config_path)

    existing = ""
    if ssh_config_path.exists():
        existing = ssh_config_path.read_text(encoding="utf-8")

    # Strip existing managed block
    if _SSH_MARKER_BEGIN in existing:
        begin = existing.find(_SSH_MARKER_BEGIN)
        end = existing.find(_SSH_MARKER_END)
        if end != -1:
            tail = existing[end + len(_SSH_MARKER_END) :]
            existing = existing[:begin] + tail.lstrip("\n")
            logger.debug("_write_ssh_config | removed previous managed block")

    block_lines = [_SSH_MARKER_BEGIN]
    block_lines.append(
        f"# Generated by gcm_ssh_migrate.py — {datetime.now().isoformat(timespec='seconds')}"
    )
    block_lines.append(f"# {len(stanzas)} SSH host(s) managed here")
    if shared:
        block_lines.append(
            "# Fichier PARTAGE — visible et utilisable par tous les utilisateurs de cette machine."
        )
    block_lines.append("")
    for stanza in stanzas.values():
        block_lines.append(stanza)
        block_lines.append("")
    block_lines.append(_SSH_MARKER_END)

    separator = "\n" if existing and not existing.endswith("\n\n") else ""
    final = existing + separator + "\n".join(block_lines) + "\n"

    mode = 0o644 if shared else 0o600
    _write_text_privileged(ssh_config_path, final, mode, shared=shared)
    logger.info(
        f"_write_ssh_config | written | path={ssh_config_path} hosts={len(stanzas)} shared={shared}"
    )


# ---------------------------------------------------------------------------
# gcm.conf rewriter
# ---------------------------------------------------------------------------


def _rewrite_gcm_conf(gcm_conf_path: Path, migrated_aliases: set[str]) -> None:
    """Rewrite gcm.conf in-place, stripping SSH-only keys from migrated entries.

    For each section in *migrated_aliases*:
      - Removes all keys in :data:`GCM_KEYS_SSH_ONLY`.
      - Sets ``ssh_config_managed = true``.
      - Replaces ``description`` with :data:`_MANAGED_DESCRIPTION`.

    Non-SSH entries and non-migrated SSH entries are written back verbatim.

    Args:
        gcm_conf_path: Path to the gcm.conf to rewrite.
        migrated_aliases: Set of host aliases that were migrated.

    Raises:
        OSError: When the file cannot be written.
    """
    logger.debug(
        "_rewrite_gcm_conf: entrée | gcm_conf_path={!r}, migrated_aliases={} élément(s)",
        gcm_conf_path,
        len(migrated_aliases) if migrated_aliases is not None else 0,
    )
    cp = configparser.RawConfigParser()
    cp.optionxform = str  # type: ignore[assignment,method-assign]  # preserve key case
    cp.read(str(gcm_conf_path), encoding="utf-8")

    for section in cp.sections():
        if not section.startswith("host "):
            continue

        alias = section[5:].strip()
        name = _cp_get(cp, section, "name")
        if alias not in migrated_aliases:
            continue

        # Remove SSH-only keys
        for option in GCM_KEYS_SSH_ONLY:
            if cp.has_option(section, option):
                cp.remove_option(section, option)
                logger.debug(
                    f"_rewrite_gcm_conf | removed key | alias={alias} name={name} key={option}"
                )

        # Mark as managed + update description
        safe_name = name.replace(" ", "_")
        cp.set(section, "ssh_config_managed", "true")
        cp.set(section, "description", _MANAGED_DESCRIPTION.format(name=name))
        cp.set(section, "name", safe_name)
        cp.set(section, "host", safe_name)
        logger.info(
            f"_rewrite_gcm_conf | patched section | alias={alias} name={name} safe_name={safe_name}"
        )

    with gcm_conf_path.open("w", encoding="utf-8") as fh:
        cp.write(fh)

    gcm_conf_path.chmod(0o600)
    logger.info(f"_rewrite_gcm_conf | written | path={gcm_conf_path}")


# ---------------------------------------------------------------------------
# Public API — callable from gnome_connection_manager.py
# ---------------------------------------------------------------------------


def migrate_ssh_hosts(
    gcm_conf: Path | str | None = None,
    ssh_config: Path | str | None = None,
    *,
    dry_run: bool = False,
    only_names: set[str] | None = None,
) -> dict[str, list[str]]:
    """Migrate SSH hosts from gcm.conf to ~/.ssh/config.

    This is the single entry point intended to be called from
    ``gnome_connection_manager.py``. By default all SSH hosts found in
    gcm.conf are migrated; pass ``only_names`` to limit the operation to
    one or several specific hosts (matched on the host's ``name`` field),
    e.g. for a "move this host only" action triggered from a context menu.

    Args:
        gcm_conf: Path to gcm.conf. Defaults to ``~/.gcm/gcm.conf``.
        ssh_config: Path to the SSH config target. Defaults to
            ``~/.ssh/config`` (personal). Pass :data:`SHARED_SSH_CONFIG_PATH`
            (or any path under ``/etc/ssh/ssh_config.d/``) to migrate to the
            shared bastion file instead — permissions and privilege
            escalation are handled automatically, see :func:`is_shared_target`.
        dry_run: When ``True``, compute everything but write nothing.
        only_names: When provided, restrict migration to hosts whose
            ``name`` field is in this set; every other host is left
            untouched in gcm.conf. When ``None`` (default), all eligible
            hosts are migrated.

    Returns:
        A result dict with keys:

        - ``"migrated"``  — list of alias strings successfully migrated
        - ``"skipped"``   — list of alias strings skipped (non-SSH)
        - ``"already"``   — list of alias strings already managed
        - ``"backups"``   — list of backup file paths created (as strings)
        - ``"errors"``    — list of error messages

    Raises:
        FileNotFoundError: When gcm.conf does not exist.
        configparser.Error: When gcm.conf cannot be parsed.
    """
    logger.debug(
        "migrate_ssh_hosts: entrée | gcm_conf={!r}, ssh_config={!r}, dry_run={!r}, only_names={} élément(s)",
        gcm_conf,
        ssh_config,
        dry_run,
        len(only_names) if only_names is not None else 0,
    )
    gcm_path = Path(gcm_conf).expanduser() if gcm_conf else Path.home() / ".gcm" / "gcm.conf"
    ssh_path = Path(ssh_config).expanduser() if ssh_config else Path.home() / ".ssh" / "config"

    logger.info(f"migrate_ssh_hosts | start | gcm={gcm_path} ssh={ssh_path} dry_run={dry_run}")

    if not gcm_path.exists():
        raise FileNotFoundError(f"gcm.conf not found: {gcm_path}")

    cp = configparser.RawConfigParser()
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    cp.read(str(gcm_path), encoding="utf-8")

    result: dict[str, list[str]] = {
        "migrated": [],
        "skipped": [],
        "already": [],
        "backups": [],
        "errors": [],
    }

    stanzas: dict[str, str] = {}
    migrated_aliases: set[str] = set()

    for section in cp.sections():
        if not section.startswith("host "):
            continue

        alias = section[5:].strip()
        conn_type = _cp_getint(cp, section, "type", GCM_TYPE_SSH)
        hostname = _cp_get(cp, section, "host")
        name = _cp_get(cp, section, "name")

        if only_names is not None and name not in only_names:
            logger.debug(
                f"migrate_ssh_hosts | not in only_names, skipping | alias={alias} name={name}",
            )
            continue

        if conn_type != GCM_TYPE_SSH:
            logger.debug(
                f"migrate_ssh_hosts | skip non-SSH | alias={alias} name={name} type={conn_type}"
            )
            result["skipped"].append(
                f"{alias}/{name}"
            )  # bug corrigé : chaîne non interpolée (littéral "{alias}/{name}")
            continue

        already = _cp_getbool(cp, section, "ssh_config_managed")
        if already:
            logger.debug(f"migrate_ssh_hosts | already managed | alias={alias} name={name}")
            result["already"].append(f"{alias}/{name}")  # bug corrigé (idem)
            continue

        if not hostname:
            msg = f"alias={alias}: no hostname, skipping"
            logger.warning(f"migrate_ssh_hosts | {msg}")
            result["errors"].append(msg)
            continue

        if not name:
            msg = f"alias={alias}: no name, skipping"
            logger.warning(f"migrate_ssh_hosts | {msg}")
            result["errors"].append(msg)
            continue

        stanza = _render_ssh_stanza(name, hostname, cp, section)
        stanzas[name] = stanza
        migrated_aliases.add(alias)
        result["migrated"].append(f"{alias}/{name}")  # bug corrigé (idem)
        logger.debug(f"migrate_ssh_hosts | queued | alias={alias} name={name}")

    if not migrated_aliases:
        logger.info("migrate_ssh_hosts | nothing to migrate")
        return result

    if dry_run:
        logger.info("migrate_ssh_hosts | dry-run — no files written")
        for stanza_name, stanza in stanzas.items():
            logger.info(f"migrate_ssh_hosts | [dry-run] stanza for {stanza_name}:\n{stanza}")
        logger.debug("migrate_ssh_hosts: retour result")
        return result

    shared = is_shared_target(ssh_path)

    # ------------------------------------------------------------------
    # Backups — must happen before any write
    # ------------------------------------------------------------------
    backup = backup_file(gcm_path)
    if backup:
        result["backups"].append(str(backup))
    backup = backup_file(ssh_path, shared=shared)
    if backup:
        result["backups"].append(str(backup))

    # ------------------------------------------------------------------
    # Write le fichier ssh_config cible (personnel ~/.ssh/config ou
    # partagé /etc/ssh/ssh_config.d/config_gcm.conf, cf. is_shared_target())
    # ------------------------------------------------------------------
    # Load any stanzas already managed from a previous run so they are
    # preserved alongside the new ones.
    existing_stanzas = _load_existing_stanzas(ssh_path)
    merged = {**existing_stanzas, **stanzas}  # new entries overwrite old

    try:
        _write_ssh_config(ssh_path, merged)
    except OSError as exc:
        msg = f"Failed to write {ssh_path}: {exc}"
        logger.error(f"migrate_ssh_hosts | {msg}")
        result["errors"].append(msg)
        logger.debug("migrate_ssh_hosts: retour result")
        return result

    # ------------------------------------------------------------------
    # Rewrite gcm.conf
    # ------------------------------------------------------------------
    # Seuls les alias fraîchement migrés dans cette passe doivent être
    # patchés ici : les alias déjà "already managed" ont été patchés lors
    # d'une exécution précédente et n'ont pas besoin de l'être à nouveau
    # (leur stanza ssh_config a simplement été préservée ci-dessus via
    # `existing_stanzas`). NB : anciennement une variable `all_managed`
    # calculait `migrated_aliases | result["already"]` mais n'était jamais
    # utilisée (code mort) — supprimée.
    try:
        _rewrite_gcm_conf(gcm_path, migrated_aliases)
    except OSError as exc:
        msg = f"Failed to rewrite {gcm_path}: {exc}"
        logger.error(f"migrate_ssh_hosts | {msg}")
        result["errors"].append(msg)
        logger.debug("migrate_ssh_hosts: retour result")
        return result

    logger.info(
        f"migrate_ssh_hosts | done | migrated={len(result['migrated'])} skipped={len(result['skipped'])} already={len(result['already'])} errors={len(result['errors'])}"
    )
    return result


def _load_existing_stanzas(ssh_path: Path) -> dict[str, str]:
    """Extract the per-host stanzas from the existing managed block.

    Reads the ``_SSH_MARKER_BEGIN`` / ``_SSH_MARKER_END`` block and splits
    it into individual ``Host …`` stanzas, keyed by alias.

    Args:
        ssh_path: Path to ``~/.ssh/config``.

    Returns:
        Dict mapping alias → stanza string (may be empty if no block exists).
    """
    logger.debug("_load_existing_stanzas: entrée | ssh_path={!r}", ssh_path)
    if not ssh_path.exists():
        logger.debug("_load_existing_stanzas: retour collection")
        return {}

    text = ssh_path.read_text(encoding="utf-8")
    begin = text.find(_SSH_MARKER_BEGIN)
    end = text.find(_SSH_MARKER_END)
    if begin == -1 or end == -1:
        logger.debug("_load_existing_stanzas: retour collection")
        return {}

    block = text[begin + len(_SSH_MARKER_BEGIN) : end]
    stanzas: dict[str, str] = {}
    current_lines: list[str] = []
    current_alias: str | None = None

    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("Host ") and not stripped.startswith("HostName"):
            if current_alias is not None:
                stanzas[current_alias] = "\n".join(current_lines).strip()
            current_alias = stripped[5:].strip()
            current_lines = [line]
        elif current_alias is not None:
            current_lines.append(line)

    if current_alias is not None and current_lines:
        stanzas[current_alias] = "\n".join(current_lines).strip()

    logger.debug(f"_load_existing_stanzas | found={len(stanzas)}")
    return stanzas


# ---------------------------------------------------------------------------
# Import — ssh_config (personnel ou partagé) → gcm.conf  (point 7/8)
# ---------------------------------------------------------------------------


def _extract_ssh_config_aliases(path: Path) -> list[str]:
    """Extract literal ``Host`` alias tokens from an ssh_config-style file.

    Wildcard patterns (containing ``*`` or ``?``, e.g. the catch-all
    ``Host *`` block) are ignored since they don't name a single addressable
    connection. Duplicate tokens are de-duplicated while preserving order.

    Args:
        path: Path to the ssh_config-style file to scan.

    Returns:
        Ordered list of alias strings (may be empty).
    """
    logger.debug("_extract_ssh_config_aliases: entrée | path={!r}", path)
    aliases: list[str] = []
    if not path.exists():
        logger.debug(f"_extract_ssh_config_aliases | source does not exist | path={path}")
        return aliases

    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.lower().startswith("host "):
                continue
            for token in line.split()[1:]:
                if "*" in token or "?" in token:
                    continue
                if token not in aliases:
                    aliases.append(token)

    logger.debug(f"_extract_ssh_config_aliases | path={path} found={len(aliases)}")
    return aliases


def import_ssh_config(
    source: Path | str,
    gcm_conf: Path | str | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Import ``Host`` aliases from an ssh_config-style file into gcm.conf.

    Point 7 — règle stricte : pour chaque alias importé, ``name`` ET
    ``host`` sont tous les deux positionnés sur le littéral ``Host`` du
    fichier ssh_config (jamais sur un ``HostName`` résolu). GCM se contente
    ensuite de lancer ``ssh <alias>``, laissant le client SSH (et ses
    ``Include``) résoudre la connexion — exactement comme le ferait un
    utilisateur en ligne de commande.

    Un alias déjà présent dans gcm.conf (``name`` ET ``host`` identiques,
    dans N'IMPORTE QUEL groupe) est ignoré (point 7, dé-duplication).

    Args:
        source: Fichier source (``~/.ssh/config`` ou
            :data:`SHARED_SSH_CONFIG_PATH`).
        gcm_conf: Chemin de gcm.conf. Défaut : ``~/.gcm/gcm.conf``.
        dry_run: Si ``True``, calcule tout sans rien écrire.

    Returns:
        Dict avec les clés ``"imported"``, ``"skipped_existing"``,
        ``"errors"`` (listes de chaînes).

    Raises:
        OSError: En cas d'échec d'écriture de gcm.conf.
    """
    logger.debug(
        "import_ssh_config: entrée | source={!r}, gcm_conf={!r}, dry_run={!r}",
        source,
        gcm_conf,
        dry_run,
    )
    source_path = Path(source).expanduser()
    gcm_path = Path(gcm_conf).expanduser() if gcm_conf else Path.home() / ".gcm" / "gcm.conf"

    result: dict[str, list[str]] = {"imported": [], "skipped_existing": [], "errors": []}

    aliases = _extract_ssh_config_aliases(source_path)
    if not aliases:
        logger.info(f"import_ssh_config | no Host alias found | source={source_path}")
        return result

    cp = configparser.RawConfigParser()
    cp.optionxform = str  # type: ignore[assignment,method-assign]
    if gcm_path.exists():
        cp.read(str(gcm_path), encoding="utf-8")

    # Index (name, host) sur TOUS les groupes existants — c'est la règle de
    # dé-duplication demandée : "sauf si cette connexion existe déjà dans
    # n'importe quel groupe (name et host egal à Host des fichiers ssh_config)".
    existing_pairs: set[tuple[str, str]] = set()
    for section in cp.sections():
        if not section.startswith("host "):
            continue
        existing_pairs.add((_cp_get(cp, section, "name"), _cp_get(cp, section, "host")))

    description = _IMPORT_DESCRIPTION_TEMPLATE.format(source=source_path)

    for alias in aliases:
        if (alias, alias) in existing_pairs:
            result["skipped_existing"].append(alias)
            logger.debug(f"import_ssh_config | already present, skipping | alias={alias}")
            continue

        section = f"host {alias}"
        suffix = 2
        original_section = section
        while cp.has_section(section):
            # Collision de section improbable (alias déjà couvert par
            # existing_pairs) mais gardée par prudence si la casse ou des
            # espaces diffèrent entre deux entrées.
            section = f"{original_section} ({suffix})"
            suffix += 1

        cp.add_section(section)
        cp.set(section, "name", alias)
        cp.set(section, "host", alias)
        cp.set(section, "group", "ssh_config")
        cp.set(section, "description", description)
        cp.set(section, "type", str(GCM_TYPE_SSH))
        # Un hôte importé DEPUIS ssh_config est par nature déjà "managé" par
        # ce fichier : il ne doit jamais être proposé au "move to ssh_config"
        # (point 4, règle "ne peut pas être déplacé une 2e fois").
        cp.set(section, "ssh_config_managed", "true")

        existing_pairs.add((alias, alias))
        result["imported"].append(alias)
        logger.info(f"import_ssh_config | queued | alias={alias} source={source_path}")

    if dry_run:
        logger.info(
            f"import_ssh_config | dry-run — imported={len(result['imported'])}, nothing written"
        )
        return result

    if not result["imported"]:
        logger.debug("import_ssh_config: retour result")
        return result

    backup = backup_file(gcm_path)
    if backup:
        logger.info(f"import_ssh_config | backup created | path={backup}")

    gcm_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with gcm_path.open("w", encoding="utf-8") as fh:
        cp.write(fh)
    gcm_path.chmod(0o600)
    logger.info(
        f"import_ssh_config | written | path={gcm_path} imported={len(result['imported'])}"
    )

    return result


# ---------------------------------------------------------------------------
# NOTE : le patch GTK du dialogue d'édition d'hôte (grisage des champs SSH +
# notice rouge pour un hôte géré par ~/.ssh/config) a été rapatrié dans
# ``plugins/plugin_ssh.py`` (``SshPlugin.patch_edit_host_dialog``), exposé
# via le hook générique ``ConnectionPlugin.patch_edit_host_dialog`` — ce
# module reste un outil de migration CLI/logique pur (pas de dépendance GTK),
# ``_cp_getbool``/``_MANAGED_DESCRIPTION`` ci-dessus restant partagés entre
# les deux. Voir docs/sessions/session-23.md pour le détail.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    logger.debug("_build_parser: entrée")
    p = argparse.ArgumentParser(
        description="Migrate GCM SSH hosts to ~/.ssh/config",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--gcm-conf",
        default=str(Path.home() / ".gcm" / "gcm.conf"),
        metavar="PATH",
        help="Path to gcm.conf (default: ~/.gcm/gcm.conf)",
    )
    p.add_argument(
        "--ssh-config",
        default=str(Path.home() / ".ssh" / "config"),
        metavar="PATH",
        help="Path to SSH config (default: ~/.ssh/config)",
    )
    p.add_argument(
        "--import-from",
        default=None,
        metavar="PATH",
        help=(
            "Import Host aliases FROM this ssh_config-style file into gcm.conf "
            "instead of migrating (e.g. /etc/ssh/ssh_config.d/config_gcm.conf)"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without writing any file",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity (default: INFO)",
    )
    logger.debug("_build_parser: retour p")
    return p


def _configure_logging(level: str) -> None:
    """Configure loguru output.

    Args:
        level: Minimum log level string.
    """
    logger.debug("_configure_logging: entrée | level={!r}", level)
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"
        ),
        colorize=True,
    )
    logger.debug("_configure_logging: fin")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to sys.argv).

    Returns:
        Exit code (0 = success, 1 = error).
    """
    logger.debug("main: entrée | argv={} élément(s)", len(argv) if argv is not None else 0)
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)

    if args.import_from:
        try:
            result = import_ssh_config(
                source=args.import_from,
                gcm_conf=args.gcm_conf,
                dry_run=args.dry_run,
            )
        except OSError as exc:
            logger.error(f"main | import failed | {exc}")
            return 1
        prefix = "[DRY-RUN] " if args.dry_run else ""
        logger.info(
            f"main | {prefix}imported={len(result['imported'])} "
            f"skipped_existing={len(result['skipped_existing'])} errors={len(result['errors'])}"
        )
        if result["imported"]:
            logger.info(f"main | imported aliases: {', '.join(result['imported'])}")
        if result["errors"]:
            for err in result["errors"]:
                logger.error(f"main | error: {err}")
            logger.debug("main: retour 1")
            return 1
        logger.debug("main: retour 0")
        return 0

    try:
        result = migrate_ssh_hosts(
            gcm_conf=args.gcm_conf,
            ssh_config=args.ssh_config,
            dry_run=args.dry_run,
        )
    except FileNotFoundError as exc:
        logger.error(f"main | {exc}")
        return 1
    except configparser.Error as exc:
        logger.error(f"main | config parse error | {exc}")
        return 1

    prefix = "[DRY-RUN] " if args.dry_run else ""
    logger.info(
        f"main | {prefix}migrated={len(result['migrated'])} skipped={len(result['skipped'])} already={len(result['already'])} backups={len(result['backups'])} errors={len(result['errors'])}"
    )

    if result["migrated"]:
        logger.info(f"main | migrated hosts: {', '.join(result['migrated'])}")
    if result["backups"]:
        logger.info(f"main | backups created: {', '.join(result['backups'])}")
    if result["errors"]:
        for err in result["errors"]:
            logger.error(f"main | error: {err}")
        logger.debug("main: retour 1")
        return 1

    logger.debug("main: retour 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
