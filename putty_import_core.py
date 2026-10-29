#!/usr/bin/python3
# -*- coding: UTF-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cœur métier (sans GTK) de l'import de sessions PuTTY pour GCM.

Backlog §4.2 (urgence faible) de ``features.md`` : « Import PuTTY
(``~/.putty/sessions/``) ». Sur Unix, PuTTY stocke chaque session enregistrée
dans un fichier séparé sous ``~/.putty/sessions/`` (pas de registre) : un
fichier par session, nommé d'après le nom de la session avec les caractères
non alphanumériques échappés en ``%XX`` (même schéma que l'URL-encoding), et
contenant des lignes ``Clé=Valeur`` en clair (pas d'INI, pas de sections —
confirmé par les scripts de conversion PuTTY→autres clients existants et par
la documentation Unix de PuTTY sur l'emplacement ``~/.putty``).

Ce module ne dépend ni de GTK ni du reste de GCM au niveau du import
top-level, à l'image de ``netmiko_bulk_core.py``/``snmp_bulk_core.py`` : il
scanne un dossier de sessions PuTTY et produit des dicts « row » au format
attendu par ``Wmain._dict_to_host`` (mêmes clés que l'import CSV/JSON —
``name``/``host``/``user``/``port``/``type``/``protocol``/``description``),
sans jamais construire de ``Host`` lui-même (ceci reste la responsabilité du
cœur GTK, comme pour ``plugin_import_csv.py``).

Portée volontairement limitée à SSH et Telnet (protocoles PuTTY les plus
courants et directement équivalents à des plugins GCM existants) : Rlogin et
Raw n'ont pas d'équivalent GCM et sont ignorés (avec le nom de la session en
raison), et Serial est laissé de côté car PuTTY décrit une ligne série avec
des champs (``SerialLine``, ``SerialSpeed``...) sans rapport avec les champs
du plugin série de GCM (``plugins/plugin_serial.py``) — un import fidèle
demanderait un mapping dédié, hors périmètre de cette première version.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from loguru import logger as app_logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
app_logger.disable(__name__)

__all__ = [
    "DEFAULT_SESSIONS_DIR",
    "SUPPORTED_PROTOCOLS",
    "decode_putty_session_name",
    "parse_putty_session_file",
    "putty_session_to_row",
    "scan_putty_sessions",
]

#: Emplacement par défaut des sessions PuTTY sur Unix (voir PuTTY FAQ,
#: « On Unix, PuTTY stores all of this data in a directory ~/.putty »).
DEFAULT_SESSIONS_DIR = Path.home() / ".putty" / "sessions"

#: Nom de fichier du profil "Default Settings" de PuTTY (pas un hôte réel) —
#: toujours présent, toujours ignoré.
_DEFAULT_SETTINGS_FILENAME = "Default%20Settings"

#: Protocoles PuTTY -> (type de plugin GCM, port par défaut si absent).
#: Volontairement restreint à SSH/Telnet, voir docstring de module.
SUPPORTED_PROTOCOLS = {
    "ssh": ("ssh", "22"),
    "telnet": ("telnet", "23"),
}


def decode_putty_session_name(raw_filename: str) -> str:
    """Décode un nom de fichier de session PuTTY vers le nom de session réel.

    PuTTY (Unix) échappe tout caractère qui n'est pas alphanumérique en
    ``%XX`` (code hexadécimal), exactement comme un encodage d'URL — un
    espace devient ``%20``, un ``/`` devient ``%2F``, etc. ``unquote``
    applique le même algorithme de décodage.

    Args:
        raw_filename (str): Nom de fichier tel que lu sur le disque (dans
            ``~/.putty/sessions/``), potentiellement échappé.

    Returns:
        str: Nom de session PuTTY décodé (ex. ``"Default%20Settings"`` ->
        ``"Default Settings"``).
    """
    app_logger.debug(f"decode_putty_session_name() called | raw_filename={raw_filename!r}")
    decoded = unquote(raw_filename)
    app_logger.debug(f"decode_putty_session_name() returning | decoded={decoded!r}")
    return decoded


def parse_putty_session_file(path: Path) -> dict[str, str]:
    """Lit un fichier de session PuTTY et retourne ses paires clé/valeur brutes.

    Format attendu : une paire ``Clé=Valeur`` par ligne, pas de section INI.
    Les lignes vides ou sans ``=`` sont ignorées silencieusement (tolérance
    plutôt qu'échec, un fichier de session PuTTY n'étant jamais généré par
    GCM lui-même — robustesse face à un fichier partiellement corrompu ou
    à une variante de format non prévue ici).

    Args:
        path (Path): Chemin du fichier de session à lire.

    Returns:
        dict[str, str]: Paires clé/valeur brutes telles que lues (valeurs
        non typées, non converties).

    Raises:
        OSError: Si le fichier ne peut pas être lu (droits, disparu entre le
            listing du dossier et la lecture...).
    """
    app_logger.debug(f"parse_putty_session_file() called | path={path!r}")
    raw: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip("\n").strip("\r")
            if not line or "=" not in line:
                continue
            key, _sep, value = line.partition("=")
            raw[key.strip()] = value.strip()
    app_logger.debug(f"parse_putty_session_file() returning | keys={sorted(raw)!r}")
    return raw


def putty_session_to_row(
    session_name: str, raw: dict[str, str]
) -> tuple[dict[str, str] | None, str | None]:
    """Convertit une session PuTTY brute en row importable par ``_dict_to_host``.

    Args:
        session_name (str): Nom de session PuTTY décodé (voir
            ``decode_putty_session_name``), utilisé comme nom d'hôte GCM.
        raw (dict[str, str]): Paires clé/valeur brutes (voir
            ``parse_putty_session_file``).

    Returns:
        tuple[dict[str, str] | None, str | None]: ``(row, None)`` si la
        session est importable (row au format ``_dict_to_host``), ou
        ``(None, raison)`` si elle doit être ignorée (protocole non
        supporté, HostName absent...).
    """
    app_logger.debug(
        f"putty_session_to_row() called | session_name={session_name!r} keys={sorted(raw)!r}"
    )
    putty_protocol = (raw.get("Protocol") or "ssh").strip().lower()
    mapping = SUPPORTED_PROTOCOLS.get(putty_protocol)
    if mapping is None:
        reason = f"{session_name}: protocole PuTTY {putty_protocol!r} non pris en charge (SSH/Telnet uniquement)"
        app_logger.debug(f"putty_session_to_row() returning | skipped, reason={reason!r}")
        return None, reason

    host = (raw.get("HostName") or "").strip()
    if not host:
        reason = f"{session_name}: HostName absent ou vide"
        app_logger.debug(f"putty_session_to_row() returning | skipped, reason={reason!r}")
        return None, reason

    gcm_type, default_port = mapping
    row = {
        "name": session_name,
        "host": host,
        "user": (raw.get("UserName") or "").strip(),
        "port": (raw.get("PortNumber") or "").strip() or default_port,
        "type": gcm_type,
        "protocol": gcm_type,
        "description": f"Importé de PuTTY ({putty_protocol})",
    }
    app_logger.debug(f"putty_session_to_row() returning | row={row!r}")
    return row, None


def scan_putty_sessions(directory: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Scanne un dossier de sessions PuTTY et retourne les rows importables.

    Args:
        directory (Path): Dossier contenant les fichiers de session PuTTY
            (typiquement ``DEFAULT_SESSIONS_DIR``, ou un dossier choisi par
            l'utilisateur — copie de ``~/.putty/sessions`` depuis une autre
            machine, par exemple).

    Returns:
        tuple[list[dict[str, str]], list[str]]: ``(rows, skipped)`` — rows
        importables (format ``_dict_to_host``) et messages expliquant
        chaque session ignorée (protocole non supporté, fichier illisible,
        HostName absent...). Dossier absent ou vide : ``([], [])``.
    """
    app_logger.debug(f"scan_putty_sessions() called | directory={directory!r}")
    if not directory.is_dir():
        app_logger.debug("scan_putty_sessions() returning | dossier absent")
        return [], []

    rows: list[dict[str, str]] = []
    skipped: list[str] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.name == _DEFAULT_SETTINGS_FILENAME:
            continue
        session_name = decode_putty_session_name(entry.name)
        try:
            raw = parse_putty_session_file(entry)
        except OSError as exc:
            app_logger.debug("scan_putty_sessions: {} : {}", type(exc).__name__, exc)
            skipped.append(f"{session_name}: lecture impossible ({exc})")
            continue
        row, reason = putty_session_to_row(session_name, raw)
        if row is None:
            skipped.append(reason or f"{session_name}: ignoré")
        else:
            rows.append(row)

    app_logger.debug(
        f"scan_putty_sessions() returning | imported={len(rows)} skipped={len(skipped)}"
    )
    return rows, skipped
