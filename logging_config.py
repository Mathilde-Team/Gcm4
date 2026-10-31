# SPDX-License-Identifier: GPL-3.0-or-later
"""logging_config -- configuration centrale de loguru (issue #130, sous-issue 4a).

Reprend le module ``netcross_core.logging_config`` de Netcross, adapté à
Gcm4 : en plus de stderr, l'application garde son journal permanent
``<dossier de configuration>/log/gcm-app.log``.

Aucun import GTK : utilisable par le cœur GTK3 actuel, par la future base
GTK4 (épopée #118) et par les tests.

Niveaux
-------

- par défaut : ``INFO`` (stderr et fichier) ;
- ``--debug`` (ligne de commande) ou ``GCM_DEBUG=1`` : ``DEBUG`` ;
- ``GCM_LOG_LEVEL=TRACE|DEBUG|INFO|WARNING|ERROR`` : prioritaire sur
  ``GCM_DEBUG``. Un niveau inconnu retombe sur ``INFO`` avec un
  avertissement ;
- ``GCM_LOG_FILE=chemin`` : copie supplémentaire des journaux.

En mode debug, chaque ligne indique le processus et le thread émetteurs
(connexions et vérification des mises à jour tournent dans des threads),
et ``logger.exception`` affiche la pile avec la valeur des variables
(``backtrace``/``diagnose``). Ces valeurs peuvent contenir des données
sensibles : relire un journal debug avant de le partager.

Usage :

    import logging_config
    app_logger = logging_config.configure_logging(config_dir=CONFIG_DIR)

``configure_logging`` est idempotent (sauf ``force=True``) : un module qui
l'appelle à l'import ne double pas les handlers.
"""

from __future__ import annotations

import enum
import os
import re
import sys
from collections.abc import Sized

from loguru import logger as _logger

DEBUG_LEVELS = frozenset({"TRACE", "DEBUG"})
DEFAULT_LEVEL = "INFO"
DEBUG_FLAG = "--debug"
ENV_LEVEL = "GCM_LOG_LEVEL"
ENV_DEBUG = "GCM_DEBUG"
ENV_FILE = "GCM_LOG_FILE"
APP_LOG_NAME = "gcm-app.log"
_TRUE = frozenset({"1", "true", "yes", "oui", "on"})

# Convention bibliothèque (issue #130) : ces modules sans GTK, utilisables
# seuls, se taisent à l'import (``logger.disable(__name__)``) ;
# configure_logging() les réactive. tests/test_logging_config.py vérifie que
# chaque ``*_core.py`` qui journalise figure ici et se désactive.
CORE_MODULES = (
    "gcm4",  # paquet src/gcm4 et ses sous-modules (#133)
    "folder_context_menu_core",
    "gcm4_core",
    "gesture_trigger_core",
    "inline_editor_core",
    "master_password_core",
    "netmiko_bulk_core",
    "popup_menu_core",
    "putty_import_core",
    "snmp_bulk_core",
    "tab_context_menu_core",
)

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)
_DEBUG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>{process.name}:{thread.name}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

_CONFIGURED = False
_LEVEL = DEFAULT_LEVEL
_HANDLER_IDS: list[int] = []
_FILES: list[str] = []


def level_from_env() -> str:
    """Niveau demandé par l'environnement (``GCM_LOG_LEVEL`` puis ``GCM_DEBUG``)."""
    _logger.trace("level_from_env: entrée")
    level = os.environ.get(ENV_LEVEL, "").strip()
    if level:
        _logger.debug("level_from_env: {} défini -> {}", ENV_LEVEL, level.upper())
        return level.upper()
    if os.environ.get(ENV_DEBUG, "").strip().lower() in _TRUE:
        _logger.debug("level_from_env: {} actif -> DEBUG", ENV_DEBUG)
        return "DEBUG"
    _logger.debug("level_from_env: aucun indicateur -> {}", DEFAULT_LEVEL)
    return DEFAULT_LEVEL


def is_debug_level(level: str) -> bool:
    """Vrai si ``level`` active le mode debug (``DEBUG`` ou ``TRACE``)."""
    _logger.trace("is_debug_level: entrée | level={!r}", level)
    result = level.strip().upper() in DEBUG_LEVELS
    _logger.debug("is_debug_level: niveau={} -> {}", level, result)
    return result


def app_log_path(config_dir: str) -> str:
    """Chemin du journal permanent de l'application dans ``config_dir``."""
    _logger.trace("app_log_path: entrée | config_dir={!r}", config_dir)
    path = os.path.join(config_dir, "log", APP_LOG_NAME)
    _logger.debug("app_log_path: -> {}", path)
    return path


def configure_logging(
    level: str | None = None,
    *,
    config_dir: str | None = None,
    log_file: str | None = None,
    force: bool = False,
):
    """Configure loguru (idempotent sauf ``force=True``).

    Args:
        level: niveau explicite, sinon ``level_from_env()``.
        config_dir: dossier de configuration ; s'il est donné, le journal
            permanent ``config_dir/log/gcm-app.log`` est ajouté (rotation à
            10 Mo, rétention de 14 jours).
        log_file: copie supplémentaire, sinon ``GCM_LOG_FILE``.
        force: remplace la configuration déjà posée (point d'entrée qui a
            lu ``--debug`` ou ``--config`` après un premier appel à l'import).

    Returns:
        loguru.Logger: le logger global, configuré.
    """
    _logger.trace(
        "configure_logging: entrée | level={!r}, config_dir={!r}, log_file={!r}, force={!r}",
        level,
        config_dir,
        log_file,
        force,
    )
    global _CONFIGURED, _LEVEL
    if _CONFIGURED and not force:
        _logger.debug("configure_logging: déjà configuré (force=False) -> sans effet")
        return _logger

    # Retirer les anciens handlers AVANT tout autre appel : sinon les
    # debug() de level_from_env() passent par le handler par défaut de
    # loguru (stderr, DEBUG) et polluent la sortie.
    if _CONFIGURED:
        for handler_id in _HANDLER_IDS:
            _logger.remove(handler_id)
    else:
        _logger.remove()  # handler par défaut de loguru, et ceux posés à l'import
    _HANDLER_IDS.clear()
    _FILES.clear()

    requested = (level or level_from_env()).strip().upper()
    invalide = None
    try:
        _logger.level(requested)
    except ValueError:
        _logger.debug("configure_logging: niveau {!r} refusé par loguru", requested)
        invalide, requested = requested, DEFAULT_LEVEL
    if log_file is None:
        log_file = os.environ.get(ENV_FILE, "").strip() or None

    for module in CORE_MODULES:
        _logger.enable(module)
    debug = is_debug_level(requested)
    fmt = _DEBUG_FORMAT if debug else _FORMAT
    _HANDLER_IDS.append(
        _logger.add(sys.stderr, level=requested, format=fmt, backtrace=debug, diagnose=debug)
    )
    fichiers: list[tuple[str, str | int]] = []
    if config_dir:
        fichiers.append((app_log_path(config_dir), "14 days"))
    if log_file:
        fichiers.append((log_file, 5))
    for chemin, retention in fichiers:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(chemin)), exist_ok=True)
            _HANDLER_IDS.append(
                _logger.add(
                    chemin,
                    level=requested,
                    format=fmt,
                    colorize=False,
                    backtrace=debug,
                    diagnose=debug,
                    rotation="10 MB",
                    retention=retention,
                    encoding="utf-8",
                )
            )
            _FILES.append(chemin)
        except OSError as exc:
            # Un journal inaccessible ne doit pas empêcher l'application de
            # démarrer : stderr reste disponible.
            _logger.warning("journal {} inaccessible ({}) : ignoré", chemin, exc)
    _CONFIGURED = True
    _LEVEL = requested

    if invalide is not None:
        _logger.warning("niveau de log inconnu {!r}, repli sur {}", invalide, DEFAULT_LEVEL)
    _logger.debug(
        "tracing debug actif : niveau={} fichiers={} pid={} python={}",
        requested,
        _FILES or "aucun",
        os.getpid(),
        sys.version.split()[0],
    )
    return _logger


def enable_debug(level: str = "DEBUG", *, config_dir: str | None = None):
    """Active le mode debug à chaud (option ``--debug``)."""
    _logger.trace("enable_debug: entrée | level={!r}, config_dir={!r}", level, config_dir)
    result = configure_logging(level, config_dir=config_dir, force=True)
    _logger.debug("enable_debug: fin (level={})", level)
    return result


def current_level() -> str:
    """Niveau effectivement configuré."""
    _logger.trace("current_level: entrée")
    if not _CONFIGURED:
        configure_logging()
    _logger.debug("current_level: -> {}", _LEVEL)
    return _LEVEL


def log_files() -> list[str]:
    """Fichiers de journal actifs (copie de la liste)."""
    _logger.debug("log_files: -> {} fichier(s)", len(_FILES))
    return list(_FILES)


def is_debug_enabled() -> bool:
    """Vrai si le mode debug est actif."""
    _logger.trace("is_debug_enabled: entrée")
    result = is_debug_level(current_level())
    _logger.debug("is_debug_enabled: -> {}", result)
    return result


def extract_debug_flag(argv: list[str]) -> tuple[bool, list[str]]:
    """Retire ``--debug`` de ``argv`` (le cœur GTK3 lit ``sys.argv`` à la main).

    Returns:
        tuple[bool, list[str]]: (option présente, arguments restants dans
        leur ordre d'origine).
    """
    _logger.trace(
        "extract_debug_flag: entrée | argv={} élément(s)", len(argv) if argv is not None else 0
    )
    rest = [a for a in argv if a != DEBUG_FLAG]
    found = len(rest) != len(argv)
    _logger.debug("extract_debug_flag: present={} restants={}", found, len(rest))
    return found, rest


def add_debug_argument(parser) -> None:
    """Ajoute l'option ``--debug`` commune à un ``argparse.ArgumentParser``."""
    _logger.trace("add_debug_argument: entrée")
    parser.add_argument(
        DEBUG_FLAG,
        action="store_true",
        help="Active le tracing debug : niveau DEBUG, thread émetteur, tracebacks détaillés "
        f"(équivaut à {ENV_DEBUG}=1 ; {ENV_FILE}=chemin copie les journaux dans un fichier).",
    )
    _logger.debug("add_debug_argument: option --debug ajoutée")


def apply_debug_argument(args, *, config_dir: str | None = None) -> None:
    """Active le mode debug si ``args.debug`` est vrai (après ``parse_args``)."""
    _logger.trace("apply_debug_argument: entrée | config_dir={!r}", config_dir)
    active = bool(getattr(args, "debug", False))
    if active:
        enable_debug(config_dir=config_dir)
    _logger.debug("apply_debug_argument: fin (args.debug={})", active)


def get_logger(name: str):
    """Logger loguru lié au module ``name`` (configure au besoin)."""
    _logger.trace("get_logger: entrée | name={!r}", name)
    if not _CONFIGURED:
        configure_logging()
    _logger.debug("get_logger: logger lié à {}", name)
    return _logger.bind(name=name)


# -- Résumés sûrs pour les traces debug (Netcross #441) ----------------------

_SECRET_NAME_RE = re.compile(
    r"pass|pwd|secret|token|cred|auth|api_?key|private|cookie|signature|ticket", re.IGNORECASE
)
_URL_USERINFO_RE = re.compile(r"(://)[^/@\s]+@")
_MAX_STR = 80


def summarize(value: object, name: str = "") -> str:
    """Résumé court et sans secret d'une valeur, pour un message de debug.

    Scalaires et chaînes courtes tels quels (identifiants d'URL masqués),
    chaînes longues et octets par leur taille, collections par leur type et
    leur nombre d'éléments, le reste par son type. Un paramètre dont le nom
    évoque un secret (mot de passe, jeton, clé, ticket...) est toujours
    rendu ``***``.
    """
    _logger.trace("summarize: entrée | name={!r}", name)
    if name and _SECRET_NAME_RE.search(name):
        _logger.debug("summarize: nom sensible -> ***")
        return "***"
    if value is None or isinstance(value, (bool, int, float)):
        _logger.debug("summarize: scalaire -> repr")
        return repr(value)
    if isinstance(value, str):
        text = _URL_USERINFO_RE.sub(r"\1***@", value)
        if len(text) <= _MAX_STR:
            _logger.debug("summarize: chaîne courte -> repr")
            return repr(text)
        _logger.debug("summarize: chaîne longue ({} car.) -> taille", len(text))
        return f"<str {len(text)} car.>"
    if isinstance(value, (bytes, bytearray, memoryview)):
        _logger.debug("summarize: octets ({}) -> taille", len(value))
        return f"<{type(value).__name__} {len(value)} octets>"
    if isinstance(value, os.PathLike):
        _logger.debug("summarize: PathLike -> fspath")
        return summarize(os.fspath(value))
    if isinstance(value, enum.Enum):
        _logger.debug("summarize: Enum -> nom qualifié")
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, Sized):
        _logger.debug("summarize: Sized ({}) -> taille", len(value))
        return f"<{type(value).__name__} {len(value)}>"
    _logger.debug("summarize: type par défaut -> nom du type")
    return f"<{type(value).__name__}>"


_PASSWORD_PARAM_RE = re.compile(r"(password=)[^&\s'\"]*", re.IGNORECASE)
_URL_PASSWORD_RE = re.compile(r"(://[^/:@\s]+):[^/@\s]*@")


def mask_password(text: object) -> str:
    """Masque les mots de passe d'une URI ou d'une commande avant journalisation.

    ``password=...`` (paramètre d'URI SPICE, option de commande) et
    ``user:motdepasse@`` (identifiants d'URL) deviennent ``***`` ; le nom
    d'utilisateur reste visible. Accepte toute valeur convertible en
    ``str`` (liste d'arguments comprise).
    """
    _logger.debug("mask_password: entrée de {} car.", len(str(text)))
    masque = _URL_PASSWORD_RE.sub(r"\1:***@", _PASSWORD_PARAM_RE.sub(r"\1***", str(text)))
    _logger.debug("mask_password: sortie de {} car.", len(masque))
    return masque


def _reset_for_tests() -> None:
    """Remet le module dans son état initial (tests uniquement)."""
    _logger.trace("_reset_for_tests: entrée")
    global _CONFIGURED, _LEVEL
    for handler_id in _HANDLER_IDS:
        try:
            _logger.remove(handler_id)
        except ValueError:
            _logger.debug("_reset_for_tests: handler {} déjà retiré", handler_id)
    _HANDLER_IDS.clear()
    _FILES.clear()
    _CONFIGURED = False
    _LEVEL = DEFAULT_LEVEL
    _logger.debug("_reset_for_tests: fin")
