# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique métier SSH de gcm4, zéro dépendance GTK — parsing de
``~/.ssh/config`` et migration/import des hôtes SSH de gcm.conf.

Fusion (session-26, plan validé le 2026-09-10 — voir
``docs/proposition-architecture-plugins-gtk4.md`` §3.0-bis/§6 et
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
    # sélecteur de clé (#97)
    "CleSsh",
    "lister_cles",
    "empreinte_cle",
    "permissions_cle",
    "chemin_prive",
    # éditeur de ~/.ssh/config (#97)
    "CHAMPS_EDITEUR",
    "CHAMPS_MULTIPLES",
    "Redirection",
    "redirections_de",
    "appliquer_redirections",
    "nouvelle_redirection",
    "erreurs_formulaire",
    "appliquer_formulaire",
    "valeurs_formulaire",
    "commande_ssh",
    "commande_test",
    "lignes_diff",
    "config_depuis_texte",
    # gestionnaire de clés (#97)
    "TYPES_CLE_PUBLIQUE",
    "ErreurCle",
    "InfoCertificat",
    "DetailsCle",
    "EntreeKnownHosts",
    "EntreeAuthorizedKeys",
    "CibleSsh",
    "FichiersLocaux",
    "FichiersDistants",
    "commentaire_par_defaut",
    "empreinte_et_type",
    "date_creation",
    "info_certificat",
    "details_cle",
    "empreinte_ligne",
    "lire_known_hosts",
    "lire_authorized_keys",
    "est_ligne_cle_publique",
    "retirer_ligne",
    "basculer_ligne",
    "ajouter_ligne",
    "hotes_ssh_config",
    "analyser_cible",
    "tester_known_hosts",
    "nom_libre",
    "generer_cle",
    "signer_cle",
    "changer_phrase",
    "cle_dans_agent",
    "ajouter_agent",
    "retirer_agent",
    "importer_paire",
    "renommer_cle",
    "supprimer_cle",
    "lire_config_ca",
    "ecrire_config_ca",
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
# Clés de ~/.ssh/ (sélecteur de clé GTK4, #97)
# ---------------------------------------------------------------------------

#: Fichiers de ``~/.ssh/`` qui ne sont jamais des clés.
NOMS_EXCLUS: frozenset[str] = frozenset(
    {"config", "known_hosts", "authorized_keys", "environment"}
)

#: Préfixes des copies de sauvegarde (``known_hosts.old``, ``config.bak``…),
#: qui ne sont pas des clés.
_PREFIXES_EXCLUS: tuple[str, ...] = ("config.", "known_hosts.", "authorized_keys.")


@dataclass(frozen=True)
class CleSsh:
    """Une clé de ``~/.ssh/`` telle que la liste le sélecteur de clé.

    Attributes:
        nom: Nom affiché (nom du fichier).
        chemin: Fichier listé : la clé privée, ou la clé publique orpheline.
        publique: Clé publique associée (``<nom>.pub``) ou ``None``.
        empreinte: Empreinte abrégée (``ssh-keygen -lf``) ou chaîne vide.
        permissions: Mode octal (``"600"``) ou ``"?"`` si illisible.
        permissions_ok: ``True`` si personne d'autre que le propriétaire n'y accède.
    """

    nom: str
    chemin: Path
    publique: Path | None
    empreinte: str
    permissions: str
    permissions_ok: bool

    @property
    def est_publique(self) -> bool:
        """Clé publique orpheline (aucune clé privée à côté)."""
        return self.chemin.suffix == ".pub"


def empreinte_cle(chemin: Path) -> str:
    """Empreinte abrégée (32 caractères) via ``ssh-keygen -lf``, ou ``""``.

    Args:
        chemin: Fichier de clé (publique de préférence).

    Returns:
        L'empreinte, ou une chaîne vide si ``ssh-keygen`` échoue ou manque.
    """
    logger.debug("empreinte_cle: entrée | {}", chemin)
    try:
        resultat = subprocess.run(
            ["ssh-keygen", "-lf", str(chemin)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("empreinte_cle: ssh-keygen indisponible pour {} : {}", chemin, exc)
        return ""
    morceaux = resultat.stdout.strip().split()
    if resultat.returncode != 0 or len(morceaux) < 2:
        logger.debug("empreinte_cle: sortie | rc={} -> vide", resultat.returncode)
        return ""
    logger.debug("empreinte_cle: sortie | empreinte lue")
    return morceaux[1][:32]


def permissions_cle(chemin: Path) -> tuple[str, bool]:
    """Mode octal d'une clé et son innocuité (aucun accès groupe ni autres).

    Args:
        chemin: Fichier de clé.

    Returns:
        ``("600", True)``, ``("644", False)``… ou ``("?", False)`` si illisible.
    """
    logger.debug("permissions_cle: entrée | {}", chemin)
    try:
        mode = chemin.stat().st_mode
    except OSError as exc:
        logger.warning("permissions_cle: {} illisible : {}", chemin, exc)
        return ("?", False)
    ok = not mode & (stat.S_IRWXG | stat.S_IRWXO)
    texte = f"{stat.S_IMODE(mode):03o}"
    logger.debug("permissions_cle: sortie | {} ok={}", texte, ok)
    return (texte, ok)


def lister_cles(dossier: Path | None = None) -> list[CleSsh]:
    """Clés de ``dossier`` (``~/.ssh/`` par défaut), triées par nom.

    Une clé privée est listée avec sa publique ``<nom>.pub`` si elle existe ;
    une publique sans privée est listée seule. Les fichiers cachés et ceux de
    :data:`NOMS_EXCLUS` sont ignorés.

    Args:
        dossier: Dossier à parcourir.

    Returns:
        Les clés trouvées ; liste vide si le dossier n'existe pas.
    """
    logger.debug("lister_cles: entrée | {}", dossier)
    dossier = dossier if dossier is not None else Path.home() / ".ssh"
    if not dossier.is_dir():
        logger.debug("lister_cles: sortie | {} absent -> []", dossier)
        return []
    cles: list[CleSsh] = []
    for chemin in sorted(dossier.iterdir()):
        nom = chemin.name
        if not chemin.is_file() or nom in NOMS_EXCLUS or nom.startswith((".",) + _PREFIXES_EXCLUS):
            continue
        if nom.endswith(".pub"):
            if chemin.with_suffix("").exists():
                continue  # listée avec sa clé privée
            publique: Path | None = None
        else:
            candidate = chemin.with_name(nom + ".pub")
            publique = candidate if candidate.exists() else None
        texte, ok = permissions_cle(chemin)
        cles.append(CleSsh(nom, chemin, publique, empreinte_cle(publique or chemin), texte, ok))
    logger.debug("lister_cles: sortie | {} clé(s)", len(cles))
    return cles


def chemin_prive(cle: CleSsh) -> str:
    """Chemin de clé privée à mettre dans ``IdentityFile``.

    Pour une publique orpheline, le chemin sans ``.pub`` (convention ssh).
    """
    logger.debug("chemin_prive: entrée | {}", cle.nom)
    chemin = cle.chemin.with_suffix("") if cle.est_publique else cle.chemin
    logger.debug("chemin_prive: sortie | {}", chemin)
    return str(chemin)


# ---------------------------------------------------------------------------
# Éditeur de ~/.ssh/config (GTK4, #97) : logique du formulaire, sans GTK
# ---------------------------------------------------------------------------

#: Directives du formulaire visuel, dans l'ordre d'affichage (``Host`` = alias).
CHAMPS_EDITEUR: tuple[str, ...] = (
    "Host",
    "HostName",
    "User",
    "Port",
    "IdentityFile",
    "ProxyJump",
    "ProxyCommand",
    "ForwardAgent",
    "ServerAliveInterval",
    "ServerAliveCountMax",
    "Compression",
    "StrictHostKeyChecking",
    "UserKnownHostsFile",
    "ConnectTimeout",
    "RequestTTY",
    "RemoteCommand",
    "ControlMaster",
    "ControlPersist",
    "PubkeyAuthentication",
    "PasswordAuthentication",
    "IdentitiesOnly",
    "LogLevel",
    "AddKeysToAgent",
)

#: Directives répétables : une valeur par ligne dans le fichier, séparées par
#: des virgules dans le formulaire.
CHAMPS_MULTIPLES: frozenset[str] = frozenset({"IdentityFile"})

#: Type de redirection → directive ssh_config.
_DIRECTIVES_REDIRECTION: dict[str, str] = {
    "dynamic": "DynamicForward",
    "local": "LocalForward",
    "remote": "RemoteForward",
}


@dataclass(frozen=True)
class Redirection:
    """Une redirection de port d'un bloc ``Host``.

    Attributes:
        type: ``"dynamic"``, ``"local"`` ou ``"remote"``.
        local: Port d'écoute (local, ou distant pour ``remote``).
        hote: Hôte cible (vide pour ``dynamic``).
        distant: Port cible (vide pour ``dynamic``).
        brut: Valeur telle qu'écrite dans le fichier (``"8080 web:80"``).
    """

    type: str
    local: str
    hote: str
    distant: str
    brut: str


def redirections_de(hote: SSHHost) -> list[Redirection]:
    """Redirections d'un hôte : ``DynamicForward``, puis ``LocalForward``, puis ``RemoteForward``."""
    logger.debug("redirections_de: entrée | {}", hote.patterns)
    resultat: list[Redirection] = []
    for brut in hote.get_options("DynamicForward"):
        morceaux = brut.split()
        resultat.append(Redirection("dynamic", morceaux[0] if morceaux else brut, "", "", brut))
    for type_, directive in (("local", "LocalForward"), ("remote", "RemoteForward")):
        for brut in hote.get_options(directive):
            morceaux = brut.split(None, 1)
            local = morceaux[0] if morceaux else brut
            cible = morceaux[1] if len(morceaux) > 1 else ""
            machine, _sep, port = cible.rpartition(":") if ":" in cible else (cible, "", "")
            resultat.append(Redirection(type_, local, machine, port, brut))
    logger.debug("redirections_de: sortie | {}", len(resultat))
    return resultat


def appliquer_redirections(hote: SSHHost, redirections: list[Redirection]) -> None:
    """Réécrit ``DynamicForward``/``LocalForward``/``RemoteForward`` depuis ``redirections``."""
    logger.debug("appliquer_redirections: entrée | {}", len(redirections))
    for type_, directive in _DIRECTIVES_REDIRECTION.items():
        hote.set_options(directive, [r.brut for r in redirections if r.type == type_])
    logger.debug("appliquer_redirections: sortie")


def nouvelle_redirection(type_: str, local: int, hote: str = "", distant: int = 0) -> Redirection:
    """Redirection saisie dans le formulaire.

    Args:
        type_: ``"dynamic"``, ``"local"`` ou ``"remote"``.
        local: Port d'écoute.
        hote: Hôte cible (ignoré pour ``dynamic``).
        distant: Port cible (ignoré pour ``dynamic``).

    Raises:
        ValueError: hôte cible vide pour ``local``/``remote``.
    """
    logger.debug("nouvelle_redirection: entrée | {}", type_)
    if type_ == "dynamic":
        logger.debug("nouvelle_redirection: sortie | dynamique")
        return Redirection("dynamic", str(local), "", "", str(local))
    hote = hote.strip()
    if not hote:
        logger.debug("nouvelle_redirection: sortie | hôte cible manquant")
        raise ValueError("hôte cible manquant")
    brut = f"{local} {hote}:{distant}"
    logger.debug("nouvelle_redirection: sortie | {}", brut)
    return Redirection(type_, str(local), hote, str(distant), brut)


def valeurs_formulaire(hote: SSHHost) -> dict[str, str]:
    """Valeurs à afficher dans le formulaire pour ``hote`` (une par champ)."""
    logger.debug("valeurs_formulaire: entrée | {}", hote.patterns)
    valeurs = {"Host": " ".join(hote.patterns)}
    for cle in CHAMPS_EDITEUR[1:]:
        if cle in CHAMPS_MULTIPLES:
            valeurs[cle] = ", ".join(hote.get_options(cle))
        else:
            valeurs[cle] = hote.get_option(cle) or ""
    logger.debug("valeurs_formulaire: sortie")
    return valeurs


def erreurs_formulaire(valeurs: dict[str, str]) -> dict[str, str]:
    """Erreurs de saisie par champ : ``{"Host": "alias", "Port": "port"}``.

    Codes : ``"alias"`` (aucun alias), ``"port"`` (pas un entier de 1 à 65535).
    """
    logger.debug("erreurs_formulaire: entrée")
    erreurs: dict[str, str] = {}
    if not valeurs.get("Host", "").split():
        erreurs["Host"] = "alias"
    port = valeurs.get("Port", "").strip()
    if port and not (port.isdigit() and 1 <= int(port) <= 65535):
        erreurs["Port"] = "port"
    logger.debug("erreurs_formulaire: sortie | {}", sorted(erreurs))
    return erreurs


def appliquer_formulaire(hote: SSHHost, valeurs: dict[str, str]) -> dict[str, str]:
    """Écrit le formulaire dans ``hote`` ; renvoie les erreurs (:func:`erreurs_formulaire`).

    Un champ en erreur (alias vide, port invalide) n'est pas écrit et garde
    son ancienne valeur ; un champ vide retire la directive.
    """
    logger.debug("appliquer_formulaire: entrée | {}", hote.patterns)
    erreurs = erreurs_formulaire(valeurs)
    if "Host" not in erreurs:
        hote.patterns = valeurs["Host"].split()
    for cle in CHAMPS_EDITEUR[1:]:
        valeur = valeurs.get(cle, "").strip()
        if cle in erreurs:
            continue
        if cle in CHAMPS_MULTIPLES:
            hote.set_options(cle, [v.strip() for v in valeur.split(",") if v.strip()])
        elif valeur:
            hote.set_option(cle, valeur)
        else:
            hote.remove_option(cle)
    logger.debug("appliquer_formulaire: sortie | {} erreur(s)", len(erreurs))
    return erreurs


def commande_ssh(hote: SSHHost) -> str:
    """Commande ``ssh -p PORT [USER@]MACHINE`` équivalente au bloc ``hote``."""
    logger.debug("commande_ssh: entrée | {}", hote.patterns)
    alias = hote.patterns[0] if hote.patterns else ""
    machine = hote.get_option("HostName") or alias
    port = hote.get_option("Port") or "22"
    utilisateur = hote.get_option("User") or ""
    cible = f"{utilisateur}@{machine}" if utilisateur else machine
    logger.debug("commande_ssh: sortie")
    return f"ssh -p {port} {cible}"


def commande_test(alias: str) -> list[str]:
    """Commande du test de connexion d'un alias (non interactive, 5 s)."""
    logger.debug("commande_test: entrée | {}", alias)
    commande = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-v", alias]
    logger.debug("commande_test: sortie")
    return commande


def lignes_diff(original: list[str], courant: str) -> list[str]:
    """Diff unifié (2 lignes de contexte) entre le fichier lu et le texte courant."""
    logger.debug("lignes_diff: entrée | {} ligne(s)", len(original))
    import difflib  # noqa: PLC0415 (seul usage)

    avant = ("\n".join(original) + "\n").splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            avant, courant.splitlines(keepends=True), fromfile="original", tofile="current", n=2
        )
    )
    logger.debug("lignes_diff: sortie | {} ligne(s)", len(diff))
    return diff


def config_depuis_texte(texte: str) -> SSHConfig:
    """Analyse un texte ``ssh_config`` (onglet Raw) comme s'il était un fichier.

    Le fichier temporaire est créé dans le dossier temporaire de
    l'utilisateur, jamais à côté de la cible (``/etc/ssh/ssh_config.d/``
    n'est pas inscriptible).

    Raises:
        OSError: fichier temporaire impossible à écrire.
    """
    logger.debug("config_depuis_texte: entrée | {} caractère(s)", len(texte))
    with tempfile.TemporaryDirectory(prefix="gcm-ssh-") as dossier:
        chemin = Path(dossier) / "config"
        chemin.write_text(texte, encoding="utf-8")
        analyseur = SSHConfigParser(config_path=chemin)
        config = analyseur.parse()
    logger.debug("config_depuis_texte: sortie | {} hôte(s)", len(config.hosts))
    return config


# ---------------------------------------------------------------------------
# Gestionnaire de clés (GTK4, #97) : clés, certificats, known_hosts,
# authorized_keys, cible distante, CA de signature — sans GTK
# ---------------------------------------------------------------------------

#: Types de clé reconnus dans ``known_hosts`` et ``authorized_keys``.
TYPES_CLE_PUBLIQUE: tuple[str, ...] = (
    "ssh-rsa",
    "ssh-ed25519",
    "ssh-dss",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
)

_CERT_VALIDITE_RE = re.compile(r"Valid: (forever|from (\S+) to (\S+))")
_CERT_CA_RE = re.compile(r"Signing CA:\s+(\S+)\s+(SHA256:\S+)")
_CIBLE_RE = re.compile(r"^([^@\s]+)@([^@\s:]+)(?::(\d+))?$")
_HOST_RE = re.compile(r"^\s*Host\s+(.+)$", re.IGNORECASE)


class ErreurCle(Exception):
    """Échec d'une opération sur les clés.

    Attributes:
        code: ``"absent"`` (outil introuvable), ``"echec"`` (code retour non
            nul), ``"delai"`` (délai dépassé), ``"existe"`` (fichier déjà
            présent), ``"nom"`` (nom invalide), ``"systeme"`` (erreur
            d'entrée/sortie).
        detail: Message de l'outil, nom du fichier en conflit, ou erreur système.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        """Erreur ``code`` avec son ``detail``."""
        logger.debug("ErreurCle.__init__: entrée | {}", code)
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        logger.debug("ErreurCle.__init__: sortie")


def _executer(
    commande: list[str], delai: float, entree: str | None = None
) -> subprocess.CompletedProcess:
    """Lance ``commande`` ; ``ErreurCle`` si elle manque, dépasse ``delai`` ou échoue.

    Les arguments ne sont jamais journalisés (phrases de passe).
    """
    logger.debug("_executer: entrée | {}", commande[0])
    try:
        resultat = subprocess.run(
            commande,
            input=entree,
            stdin=None if entree is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=delai,
            check=False,
        )
    except FileNotFoundError as exc:
        logger.warning("_executer: {} introuvable : {}", commande[0], exc)
        raise ErreurCle("absent", commande[0]) from exc
    except subprocess.TimeoutExpired as exc:
        logger.warning("_executer: {} au-delà de {} s", commande[0], delai)
        raise ErreurCle("delai", commande[0]) from exc
    if resultat.returncode != 0:
        logger.warning(
            "_executer: {} rc={} : {}", commande[0], resultat.returncode, resultat.stderr.strip()
        )
        raise ErreurCle("echec", resultat.stderr.strip())
    logger.debug("_executer: sortie | rc=0")
    return resultat


def commentaire_par_defaut() -> str:
    """Commentaire ``utilisateur@machine`` proposé à ``ssh-keygen -C``."""
    logger.debug("commentaire_par_defaut: entrée")
    try:
        utilisateur = getpass.getuser()
    except (KeyError, OSError) as exc:
        logger.debug("commentaire_par_defaut: getuser impossible : {}", exc)
        utilisateur = os.environ.get("USER") or "user"
    import socket  # noqa: PLC0415 (seul usage)

    commentaire = f"{utilisateur}@{socket.gethostname() or 'localhost'}"
    logger.debug("commentaire_par_defaut: sortie")
    return commentaire


def empreinte_et_type(chemin: Path) -> tuple[str, str]:
    """``(empreinte SHA256 complète, type)`` d'un fichier de clé, ou ``("", "")``.

    Le type est celui affiché par ``ssh-keygen -lf`` (``ED25519``, ``RSA``…).
    """
    logger.debug("empreinte_et_type: entrée | {}", chemin)
    try:
        resultat = _executer(["ssh-keygen", "-lf", str(chemin)], 3)
    except ErreurCle as exc:
        logger.debug("empreinte_et_type: sortie | indéterminable ({})", exc.code)
        return ("", "")
    morceaux = resultat.stdout.strip().split()
    empreinte = morceaux[1] if len(morceaux) >= 2 else ""
    type_ = morceaux[-1].strip("()") if morceaux and morceaux[-1].startswith("(") else ""
    logger.debug("empreinte_et_type: sortie | {}", type_)
    return (empreinte, type_)


def date_creation(chemin: Path) -> datetime | None:
    """Date de création (``stat -c %W`` si le système de fichiers la connaît, sinon ctime)."""
    logger.debug("date_creation: entrée | {}", chemin)
    try:
        brut = _executer(["stat", "-c", "%W", str(chemin)], 3).stdout.strip()
    except ErreurCle as exc:
        logger.debug("date_creation: stat indisponible ({})", exc.code)
        brut = ""
    if brut.isdigit() and int(brut) > 0:
        logger.debug("date_creation: sortie | naissance")
        return datetime.fromtimestamp(int(brut))
    try:
        date = datetime.fromtimestamp(chemin.stat().st_ctime)
    except OSError as exc:
        logger.debug("date_creation: sortie | illisible ({})", exc)
        return None
    logger.debug("date_creation: sortie | ctime")
    return date


@dataclass(frozen=True)
class InfoCertificat:
    """Validité et CA d'un certificat OpenSSH (``*-cert.pub``).

    Attributes:
        fin: Fin de validité (``"2027-11-05T10:00:00"``), ``None`` si
            ``forever``, ``"?"`` si illisible.
        expire: Fin de validité passée.
        ca: ``"ED25519 SHA256:…"`` (empreinte abrégée) ou ``None`` si inconnue.
    """

    fin: str | None
    expire: bool
    ca: str | None


def info_certificat(chemin: Path) -> InfoCertificat:
    """Lit un certificat avec ``ssh-keygen -L`` ; ``fin="?"`` s'il est illisible."""
    logger.debug("info_certificat: entrée | {}", chemin)
    try:
        sortie = _executer(["ssh-keygen", "-L", "-f", str(chemin)], 3).stdout
    except ErreurCle as exc:
        logger.debug("info_certificat: sortie | illisible ({})", exc.code)
        return InfoCertificat("?", False, None)
    ca = _CERT_CA_RE.search(sortie)
    signataire = f"{ca.group(1)} {ca.group(2)[:24]}" if ca else None
    validite = _CERT_VALIDITE_RE.search(sortie)
    if validite is None:
        logger.debug("info_certificat: sortie | validité absente")
        return InfoCertificat("?", False, signataire)
    if validite.group(1) == "forever":
        logger.debug("info_certificat: sortie | perpétuel")
        return InfoCertificat(None, False, signataire)
    fin = validite.group(3)
    try:
        expire = datetime.strptime(fin, "%Y-%m-%dT%H:%M:%S") < datetime.now()
    except ValueError as exc:
        logger.debug("info_certificat: date {} illisible : {}", fin, exc)
        expire = False
    logger.debug("info_certificat: sortie | fin={} expiré={}", fin, expire)
    return InfoCertificat(fin, expire, signataire)


@dataclass(frozen=True)
class DetailsCle:
    """Colonnes du gestionnaire de clés en plus de :class:`CleSsh`.

    Attributes:
        type: ``ED25519``, ``RSA``… ou ``""``.
        empreinte: Empreinte SHA256 complète ou ``""``.
        creation: Date de création ou ``None``.
        certificat: Certificat ``<clé>-cert.pub`` (ou la publique elle-même
            si c'est un certificat), sinon ``None``.
    """

    type: str
    empreinte: str
    creation: datetime | None
    certificat: InfoCertificat | None


def details_cle(cle: CleSsh) -> DetailsCle:
    """Type, empreinte complète, date de création et certificat de ``cle``."""
    logger.debug("details_cle: entrée | {}", cle.nom)
    empreinte, type_ = empreinte_et_type(cle.chemin)
    if cle.est_publique:
        certificat = cle.chemin if cle.nom.endswith("-cert.pub") else None
    else:
        candidat = cle.chemin.with_name(cle.nom + "-cert.pub")
        certificat = candidat if candidat.exists() else None
    info = info_certificat(certificat) if certificat is not None else None
    details = DetailsCle(type_, empreinte, date_creation(cle.chemin), info)
    logger.debug("details_cle: sortie | certificat={}", info is not None)
    return details


def empreinte_ligne(type_: str, base64_cle: str) -> str:
    """Empreinte ``SHA256:…`` d'une clé publique en base64, calculée sans ``ssh-keygen``.

    Identique à ``ssh-keygen -lf`` ; ``""`` si le base64 est invalide.
    """
    logger.debug("empreinte_ligne: entrée | {}", type_)
    import base64  # noqa: PLC0415
    import binascii  # noqa: PLC0415
    import hashlib  # noqa: PLC0415

    try:
        brut = base64.b64decode(base64_cle, validate=True)
    except (binascii.Error, ValueError) as exc:
        logger.debug("empreinte_ligne: sortie | base64 invalide ({})", exc)
        return ""
    empreinte = "SHA256:" + base64.b64encode(hashlib.sha256(brut).digest()).decode().rstrip("=")
    logger.debug("empreinte_ligne: sortie")
    return empreinte


@dataclass(frozen=True)
class EntreeKnownHosts:
    """Une ligne utile de ``known_hosts``.

    Attributes:
        ligne: Numéro de ligne (à partir de 1).
        brut: Ligne telle qu'écrite (pour la retrouver à la suppression).
        marqueur: ``@cert-authority``, ``@revoked`` ou ``""``.
        hotes: Champ des hôtes (vide si haché).
        hache: Hôtes hachés (``|1|…``).
        type: Type de clé.
        empreinte: Empreinte SHA256 ou ``""``.
        commentaire: Fin de ligne après la clé.
    """

    ligne: int
    brut: str
    marqueur: str
    hotes: str
    hache: bool
    type: str
    empreinte: str
    commentaire: str


def lire_known_hosts(texte: str) -> list[EntreeKnownHosts]:
    """Entrées de ``known_hosts`` ; commentaires, lignes vides et incomplètes ignorés."""
    logger.debug("lire_known_hosts: entrée | {} caractère(s)", len(texte))
    entrees: list[EntreeKnownHosts] = []
    for numero, brut in enumerate(texte.splitlines(), start=1):
        reste = brut.strip()
        if not reste or reste.startswith("#"):
            continue
        marqueur = ""
        if reste.startswith("@"):
            marqueur, _sep, reste = reste.partition(" ")
            reste = reste.strip()
        morceaux = reste.split(maxsplit=3)
        if len(morceaux) < 3:
            continue
        hotes, type_, base64_cle = morceaux[:3]
        hache = hotes.startswith("|1|")
        entrees.append(
            EntreeKnownHosts(
                numero,
                brut,
                marqueur,
                "" if hache else hotes,
                hache,
                type_,
                empreinte_ligne(type_, base64_cle),
                morceaux[3] if len(morceaux) > 3 else "",
            )
        )
    logger.debug("lire_known_hosts: sortie | {} entrée(s)", len(entrees))
    return entrees


@dataclass(frozen=True)
class EntreeAuthorizedKeys:
    """Une clé de ``authorized_keys``, active ou désactivée (commentée).

    Attributes:
        ligne: Numéro de ligne (à partir de 1).
        brut: Ligne telle qu'écrite.
        active: ``False`` si la ligne est commentée.
        options: Options avant le type (``from="…",no-pty``).
        type: Type de clé.
        empreinte: Empreinte SHA256 ou ``""``.
        commentaire: Fin de ligne après la clé.
    """

    ligne: int
    brut: str
    active: bool
    options: str
    type: str
    empreinte: str
    commentaire: str


def lire_authorized_keys(texte: str) -> list[EntreeAuthorizedKeys]:
    """Clés de ``authorized_keys`` : une ligne commentée contenant une clé est désactivée.

    Analyse par mots : une option contenant une espace entre guillemets
    (``command="a b"``) est coupée, mais la ligne reste reconnue.
    """
    logger.debug("lire_authorized_keys: entrée | {} caractère(s)", len(texte))
    entrees: list[EntreeAuthorizedKeys] = []
    for numero, brut in enumerate(texte.splitlines(), start=1):
        ligne = brut.strip()
        active = not ligne.startswith("#")
        if not active:
            ligne = ligne[1:].strip()
        mots = ligne.split()
        rang = next((i for i, mot in enumerate(mots) if mot in TYPES_CLE_PUBLIQUE), None)
        if rang is None:
            continue
        type_ = mots[rang]
        base64_cle = mots[rang + 1] if len(mots) > rang + 1 else ""
        entrees.append(
            EntreeAuthorizedKeys(
                numero,
                brut,
                active,
                " ".join(mots[:rang]),
                type_,
                empreinte_ligne(type_, base64_cle) if base64_cle else "",
                " ".join(mots[rang + 2 :]),
            )
        )
    logger.debug("lire_authorized_keys: sortie | {} entrée(s)", len(entrees))
    return entrees


def est_ligne_cle_publique(ligne: str) -> bool:
    """La ligne contient un type de clé connu suivi d'un base64 valide."""
    logger.debug("est_ligne_cle_publique: entrée")
    entrees = lire_authorized_keys(ligne.strip())
    valide = len(entrees) == 1 and entrees[0].active and bool(entrees[0].empreinte)
    logger.debug("est_ligne_cle_publique: sortie | {}", valide)
    return valide


def _joindre(lignes: list[str]) -> str:
    """Lignes → texte terminé par un saut de ligne (vide si aucune ligne)."""
    logger.debug("_joindre: entrée | {}", len(lignes))
    texte = "\n".join(lignes) + ("\n" if lignes else "")
    logger.debug("_joindre: sortie")
    return texte


def retirer_ligne(texte: str, brut: str) -> str | None:
    """Texte sans les lignes égales à ``brut`` ; ``None`` si aucune (fichier modifié entre-temps)."""
    logger.debug("retirer_ligne: entrée")
    lignes = texte.splitlines()
    restantes = [ligne for ligne in lignes if ligne != brut]
    if len(restantes) == len(lignes):
        logger.debug("retirer_ligne: sortie | introuvable")
        return None
    logger.debug("retirer_ligne: sortie | {} retirée(s)", len(lignes) - len(restantes))
    return _joindre(restantes)


def basculer_ligne(texte: str, numero: int, brut: str) -> str | None:
    """Commente ou décommente la ligne ``numero`` si elle vaut toujours ``brut``, sinon ``None``."""
    logger.debug("basculer_ligne: entrée | {}", numero)
    lignes = texte.splitlines()
    if not 1 <= numero <= len(lignes) or lignes[numero - 1] != brut:
        logger.debug("basculer_ligne: sortie | ligne changée")
        return None
    ligne = brut.strip()
    lignes[numero - 1] = ligne[1:].strip() if ligne.startswith("#") else "# " + brut
    logger.debug("basculer_ligne: sortie")
    return _joindre(lignes)


def ajouter_ligne(texte: str, ligne: str) -> str:
    """Ajoute ``ligne`` en fin de texte (saut de ligne inséré si besoin)."""
    logger.debug("ajouter_ligne: entrée")
    separateur = "\n" if texte and not texte.endswith("\n") else ""
    resultat = texte + separateur + ligne.strip() + "\n"
    logger.debug("ajouter_ligne: sortie")
    return resultat


def hotes_ssh_config(chemin: Path) -> list[str]:
    """Alias concrets des blocs ``Host`` (sans ``*``, ``?``, ``!``), sans doublon."""
    logger.debug("hotes_ssh_config: entrée | {}", chemin)
    try:
        texte = chemin.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("hotes_ssh_config: sortie | illisible ({})", exc)
        return []
    hotes: list[str] = []
    for ligne in texte.splitlines():
        trouve = _HOST_RE.match(ligne)
        for alias in trouve.group(1).split() if trouve else ():
            if not any(c in alias for c in "*?!") and alias not in hotes:
                hotes.append(alias)
    logger.debug("hotes_ssh_config: sortie | {}", len(hotes))
    return hotes


@dataclass(frozen=True)
class CibleSsh:
    """Compte distant ``utilisateur@hote[:port]`` dont on gère le ``~/.ssh/``."""

    utilisateur: str
    hote: str
    port: int = 22

    @property
    def affichage(self) -> str:
        """``utilisateur@hote``, suivi de ``:port`` hors port 22."""
        logger.debug("CibleSsh.affichage: entrée")
        suffixe = f":{self.port}" if self.port != 22 else ""
        logger.debug("CibleSsh.affichage: sortie")
        return f"{self.utilisateur}@{self.hote}{suffixe}"

    def commande(self, distante: str) -> list[str]:
        """Commande ssh non interactive exécutant ``distante`` sur la cible."""
        logger.debug("CibleSsh.commande: entrée | {}", self.affichage)
        commande = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6"]
        if self.port != 22:
            commande += ["-p", str(self.port)]
        commande += [f"{self.utilisateur}@{self.hote}", distante]
        logger.debug("CibleSsh.commande: sortie")
        return commande


def analyser_cible(texte: str) -> CibleSsh | None:
    """``"root@h:2222"`` → :class:`CibleSsh` ; ``None`` si la saisie est invalide."""
    logger.debug("analyser_cible: entrée")
    trouve = _CIBLE_RE.match(texte.strip())
    if trouve is None:
        logger.debug("analyser_cible: sortie | invalide")
        return None
    port = int(trouve.group(3)) if trouve.group(3) else 22
    if not 1 <= port <= 65535:
        logger.debug("analyser_cible: sortie | port {} invalide", port)
        return None
    cible = CibleSsh(trouve.group(1), trouve.group(2), port)
    logger.debug("analyser_cible: sortie | {}", cible.affichage)
    return cible


class FichiersLocaux:
    """``known_hosts``/``authorized_keys`` du ``~/.ssh/`` local."""

    distant = False

    def __init__(self, dossier: Path) -> None:
        """Fichiers de ``dossier``."""
        logger.debug("FichiersLocaux.__init__: entrée | {}", dossier)
        self.dossier = dossier
        logger.debug("FichiersLocaux.__init__: sortie")

    def utilisateur(self) -> str:
        """Compte qui exécute gcm4 (garde-fou root)."""
        logger.debug("FichiersLocaux.utilisateur: entrée")
        try:
            nom = getpass.getuser()
        except (KeyError, OSError) as exc:
            logger.debug("FichiersLocaux.utilisateur: getuser impossible : {}", exc)
            nom = os.environ.get("USER", "")
        logger.debug("FichiersLocaux.utilisateur: sortie")
        return nom

    def lire(self, nom: str) -> str | None:
        """Contenu de ``nom``, ``None`` s'il est absent ou illisible."""
        logger.debug("FichiersLocaux.lire: entrée | {}", nom)
        try:
            texte = (self.dossier / nom).read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("FichiersLocaux.lire: sortie | {}", exc)
            return None
        logger.debug("FichiersLocaux.lire: sortie | {} caractère(s)", len(texte))
        return texte

    def ecrire(self, nom: str, texte: str, mode: int = 0o600) -> bool:
        """Remplace ``nom`` par ``texte`` avec les permissions ``mode``."""
        logger.debug("FichiersLocaux.ecrire: entrée | {}", nom)
        chemin = self.dossier / nom
        try:
            self.dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
            chemin.write_text(texte, encoding="utf-8")
            chemin.chmod(mode)
        except OSError as exc:
            logger.error("FichiersLocaux.ecrire: {} : {}", chemin, exc)
            return False
        logger.info("FichiersLocaux | {} écrit", chemin)
        logger.debug("FichiersLocaux.ecrire: sortie")
        return True


class FichiersDistants:
    """``known_hosts``/``authorized_keys`` d'un compte distant, par ssh.

    Ne lit ni n'écrit jamais de clé privée.
    """

    distant = True

    def __init__(self, cible: CibleSsh) -> None:
        """Fichiers du compte ``cible``."""
        logger.debug("FichiersDistants.__init__: entrée | {}", cible.affichage)
        self.cible = cible
        logger.debug("FichiersDistants.__init__: sortie")

    def utilisateur(self) -> str:
        """Compte distant (garde-fou root)."""
        logger.debug("FichiersDistants.utilisateur: entrée")
        logger.debug("FichiersDistants.utilisateur: sortie")
        return self.cible.utilisateur

    def tester(self) -> str | None:
        """``None`` si la cible répond, sinon l'erreur ssh (``ErreurCle.code`` si vide)."""
        logger.debug("FichiersDistants.tester: entrée | {}", self.cible.affichage)
        try:
            sortie = _executer(self.cible.commande("echo __OK__"), 8).stdout
        except ErreurCle as exc:
            logger.debug("FichiersDistants.tester: sortie | {}", exc.code)
            return exc.detail if exc.code == "echec" and exc.detail else exc.code
        erreur = None if "__OK__" in sortie else "echec"
        logger.debug("FichiersDistants.tester: sortie | {}", erreur)
        return erreur

    def lire(self, nom: str) -> str | None:
        """Contenu de ``~/.ssh/<nom>`` distant, ``None`` s'il est absent ou injoignable."""
        logger.debug("FichiersDistants.lire: entrée | {}", nom)
        try:
            texte = _executer(self.cible.commande(f"cat ~/.ssh/{shlex.quote(nom)}"), 10).stdout
        except ErreurCle as exc:
            logger.debug("FichiersDistants.lire: sortie | {}", exc.code)
            return None
        logger.debug("FichiersDistants.lire: sortie | {} caractère(s)", len(texte))
        return texte

    def ecrire(self, nom: str, texte: str, mode: int = 0o600) -> bool:
        """Remplace ``~/.ssh/<nom>`` distant : fichier temporaire puis ``mv`` (atomique).

        Le contenu passe en base64 : aucun problème de guillemets côté shell.
        """
        logger.debug("FichiersDistants.ecrire: entrée | {}", nom)
        import base64  # noqa: PLC0415

        contenu = base64.b64encode(texte.encode("utf-8")).decode("ascii")
        distante = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f'tmp=$(mktemp) && echo {shlex.quote(contenu)} | base64 -d > "$tmp" && '
            f'chmod {mode:o} "$tmp" && mv "$tmp" ~/.ssh/{shlex.quote(nom)}'
        )
        try:
            _executer(self.cible.commande(distante), 10)
        except ErreurCle as exc:
            logger.error("FichiersDistants.ecrire: {} sur {} : {}", nom, self.cible.affichage, exc)
            return False
        logger.info("FichiersDistants | {} écrit sur {}", nom, self.cible.affichage)
        logger.debug("FichiersDistants.ecrire: sortie")
        return True


def tester_known_hosts(texte: str, hotes: list[str]) -> list[tuple[str, bool, str]]:
    """``ssh-keygen -F`` de chaque hôte dans le ``known_hosts`` ``texte``.

    Returns:
        ``(hôte, trouvé, ligne trouvée ou erreur)`` par hôte, dans l'ordre.
    """
    logger.debug("tester_known_hosts: entrée | {} hôte(s)", len(hotes))
    resultats: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="gcm-kh-") as dossier:
        chemin = Path(dossier) / "known_hosts"
        chemin.write_text(texte, encoding="utf-8")
        for hote in hotes:
            try:
                sortie = _executer(["ssh-keygen", "-F", hote, "-f", str(chemin)], 3).stdout
            except ErreurCle as exc:
                logger.debug("tester_known_hosts: {} absent ({})", hote, exc.code)
                resultats.append((hote, False, "" if exc.code == "echec" else exc.code))
                continue
            resultats.append((hote, True, sortie.strip()))
    logger.debug("tester_known_hosts: sortie | {} trouvé(s)", sum(r[1] for r in resultats))
    return resultats


def nom_libre(dossier: Path, nom: str) -> Path:
    """``dossier/nom``, ou ``nom_1``, ``nom_2``… si le fichier existe déjà."""
    logger.debug("nom_libre: entrée | {}", nom)
    chemin = dossier / nom
    rang = 0
    while chemin.exists() or chemin.with_name(chemin.name + ".pub").exists():
        rang += 1
        chemin = dossier / f"{nom}_{rang}"
    logger.debug("nom_libre: sortie | {}", chemin.name)
    return chemin


def generer_cle(chemin: Path, type_: str, taille: int, commentaire: str, phrase: str) -> None:
    """``ssh-keygen`` : ``ed25519``, ``ecdsa`` ou ``rsa`` (``taille`` bits).

    Raises:
        ErreurCle: ``absent``, ``echec`` ou ``delai``.
    """
    logger.debug("generer_cle: entrée | {} {}", type_, chemin.name)
    commande = ["ssh-keygen", "-q", "-t", type_]
    if type_ == "rsa":
        commande += ["-b", str(taille)]
    commande += ["-f", str(chemin), "-N", phrase, "-C", commentaire]
    chemin.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _executer(commande, 60)
    logger.info("generer_cle | {} ({}) créée", chemin, type_)
    logger.debug("generer_cle: sortie")


def signer_cle(publique: Path, ca: Path, identite: str, principaux: str, validite: str) -> Path:
    """Signe ``publique`` avec la CA ``ca`` ; renvoie le certificat ``<clé>-cert.pub``.

    Une CA protégée par une phrase de passe ne peut pas signer sans
    terminal : ``ErreurCle("delai")``.

    Raises:
        ErreurCle: ``absent``, ``echec`` ou ``delai``.
    """
    logger.debug("signer_cle: entrée | {} par {}", publique.name, ca.name)
    commande = ["ssh-keygen", "-s", str(ca), "-I", identite, "-n", principaux]
    _executer(commande + ["-V", validite, str(publique)], 15, entree="")
    certificat = publique.with_name(publique.name[: -len(".pub")] + "-cert.pub")
    logger.info("signer_cle | {} signée par {}", publique, ca)
    logger.debug("signer_cle: sortie")
    return certificat


def changer_phrase(chemin: Path, ancienne: str, nouvelle: str) -> None:
    """``ssh-keygen -p`` : remplace la phrase de passe (vide = aucune).

    Raises:
        ErreurCle: ``absent``, ``echec`` (mauvaise phrase…) ou ``delai``.
    """
    logger.debug("changer_phrase: entrée | {}", chemin.name)
    _executer(["ssh-keygen", "-p", "-f", str(chemin), "-P", ancienne, "-N", nouvelle], 10)
    logger.info("changer_phrase | phrase de {} changée", chemin)
    logger.debug("changer_phrase: sortie")


def cle_dans_agent(publique: Path) -> bool:
    """La clé est chargée dans ssh-agent (agent joignable et empreinte listée)."""
    logger.debug("cle_dans_agent: entrée | {}", publique.name)
    empreinte, _type = empreinte_et_type(publique)
    if not empreinte:
        logger.debug("cle_dans_agent: sortie | empreinte inconnue")
        return False
    try:
        liste = _executer(["ssh-add", "-l"], 5).stdout
    except ErreurCle as exc:
        logger.debug("cle_dans_agent: sortie | agent indisponible ({})", exc.code)
        return False
    present = empreinte in liste
    logger.debug("cle_dans_agent: sortie | {}", present)
    return present


def ajouter_agent(chemin: Path) -> None:
    """``ssh-add`` sans terminal : une clé à phrase de passe échoue.

    Raises:
        ErreurCle: ``absent``, ``echec`` ou ``delai``.
    """
    logger.debug("ajouter_agent: entrée | {}", chemin.name)
    _executer(["ssh-add", str(chemin)], 10)
    logger.info("ajouter_agent | {} chargée", chemin)
    logger.debug("ajouter_agent: sortie")


def retirer_agent(publique: Path) -> None:
    """``ssh-add -d``.

    Raises:
        ErreurCle: ``absent``, ``echec`` ou ``delai``.
    """
    logger.debug("retirer_agent: entrée | {}", publique.name)
    _executer(["ssh-add", "-d", str(publique)], 5)
    logger.info("retirer_agent | {} déchargée", publique)
    logger.debug("retirer_agent: sortie")


def importer_paire(source: Path, dossier: Path, exiger_publique: bool = False) -> Path:
    """Copie une clé privée (et sa ``.pub`` si présente) dans ``dossier``, en 600/644.

    Écrase un fichier de même nom (l'interface demande confirmation avant).

    Args:
        source: Clé privée à importer.
        dossier: Destination (``~/.ssh/``).
        exiger_publique: Refuser une clé sans ``.pub`` (import de CA).

    Raises:
        ErreurCle: ``nom`` (pas de ``.pub`` exigée), ``systeme`` (copie impossible).
    """
    logger.debug("importer_paire: entrée | {}", source.name)
    publique = source.with_name(source.name + ".pub")
    if exiger_publique and not publique.exists():
        logger.debug("importer_paire: sortie | pas de .pub")
        raise ErreurCle("nom", publique.name)
    cible = dossier / source.name
    try:
        dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(source, cible)
        cible.chmod(0o600)
        if publique.exists():
            shutil.copy2(publique, cible.with_name(cible.name + ".pub"))
            cible.with_name(cible.name + ".pub").chmod(0o644)
    except OSError as exc:
        logger.error("importer_paire: {} → {} : {}", source, cible, exc)
        raise ErreurCle("systeme", str(exc)) from exc
    logger.info("importer_paire | {} → {}", source, cible)
    logger.debug("importer_paire: sortie")
    return cible


def renommer_cle(cle: CleSsh, nouveau: str) -> None:
    """Renomme la clé, sa ``.pub`` et son certificat ``-cert.pub`` ensemble.

    Raises:
        ErreurCle: ``nom`` (séparateur de chemin ou nom vide), ``existe``
            (``detail`` = fichier en conflit), ``systeme``.
    """
    logger.debug("renommer_cle: entrée | {} → {}", cle.nom, nouveau)
    if not nouveau or "/" in nouveau or "\\" in nouveau or nouveau in (".", ".."):
        logger.debug("renommer_cle: sortie | nom invalide")
        raise ErreurCle("nom", nouveau)
    renommages = [(cle.chemin, cle.chemin.with_name(nouveau))]
    if not cle.est_publique:
        for suffixe in (".pub", "-cert.pub"):
            ancien = cle.chemin.with_name(cle.nom + suffixe)
            if ancien.exists():
                renommages.append((ancien, cle.chemin.with_name(nouveau + suffixe)))
    for _ancien, cible in renommages:
        if cible.exists():
            logger.debug("renommer_cle: sortie | {} existe", cible.name)
            raise ErreurCle("existe", cible.name)
    try:
        for ancien, cible in renommages:
            ancien.rename(cible)
    except OSError as exc:
        logger.error("renommer_cle: {} : {}", cle.chemin, exc)
        raise ErreurCle("systeme", str(exc)) from exc
    logger.info("renommer_cle | {} → {} ({} fichier(s))", cle.nom, nouveau, len(renommages))
    logger.debug("renommer_cle: sortie")


def supprimer_cle(cle: CleSsh) -> None:
    """Supprime la clé, sa ``.pub`` et son certificat.

    Raises:
        ErreurCle: ``systeme``.
    """
    logger.debug("supprimer_cle: entrée | {}", cle.nom)
    fichiers = [cle.chemin]
    if not cle.est_publique:
        fichiers += [cle.chemin.with_name(cle.nom + s) for s in (".pub", "-cert.pub")]
    try:
        for fichier in fichiers:
            fichier.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("supprimer_cle: {} : {}", cle.chemin, exc)
        raise ErreurCle("systeme", str(exc)) from exc
    logger.info("supprimer_cle | {} supprimée", cle.nom)
    logger.debug("supprimer_cle: sortie")


def lire_config_ca(chemin: Path) -> Path | None:
    """CA désignée (``{"ca_priv": "…"}``) si le fichier et la clé existent, sinon ``None``."""
    logger.debug("lire_config_ca: entrée | {}", chemin)
    import json  # noqa: PLC0415

    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("lire_config_ca: sortie | aucune ({})", exc)
        return None
    brut = donnees.get("ca_priv", "") if isinstance(donnees, dict) else ""
    ca = Path(brut) if brut else None
    if ca is not None and not ca.exists():
        logger.warning("lire_config_ca: CA {} introuvable, désignation ignorée", ca)
        ca = None
    logger.debug("lire_config_ca: sortie | {}", ca)
    return ca


def ecrire_config_ca(chemin: Path, ca: Path | None) -> bool:
    """Enregistre la CA désignée (``None`` = aucune), en 600."""
    logger.debug("ecrire_config_ca: entrée | {}", ca)
    import json  # noqa: PLC0415

    try:
        chemin.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        chemin.write_text(json.dumps({"ca_priv": str(ca) if ca else ""}), encoding="utf-8")
        chemin.chmod(0o600)
    except OSError as exc:
        logger.error("ecrire_config_ca: {} : {}", chemin, exc)
        return False
    logger.debug("ecrire_config_ca: sortie")
    return True


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
