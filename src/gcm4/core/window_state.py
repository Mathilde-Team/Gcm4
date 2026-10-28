# SPDX-License-Identifier: GPL-3.0-or-later
"""Géométrie de la fenêtre principale, dans ``gcm4.conf`` (#134).

Mêmes clés que l'application GTK3 (section ``[window]``) : on peut passer
de l'une à l'autre sans perdre ses réglages. L'écriture ne modifie que ces
clés ; tout le reste du fichier est conservé (lu et réécrit par
``RawConfigParser``, comme ``Wmain.writeConfig``), via un fichier
temporaire renommé : jamais de fichier tronqué.

GTK4 ne permet plus de placer une fenêtre (Wayland) : seule la taille est
restaurée, pas la position.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass

from loguru import logger

from gcm4.core.config import update_keys

SECTION = "window"
CLE_LARGEUR = "window-width"
CLE_HAUTEUR = "window-height"
CLE_PANNEAU = "left-panel-width"
CLE_PANNEAU_VISIBLE = "show-panel"


@dataclass
class WindowState:
    """Taille de la fenêtre (-1 : taille par défaut), largeur et visibilité du panneau."""

    width: int = -1
    height: int = -1
    panel_width: int = 100
    show_panel: bool = True


def _lire(config_file: str) -> configparser.RawConfigParser:
    logger.debug("_lire: {}", config_file)
    cp = configparser.RawConfigParser()
    try:
        cp.read(config_file, encoding="utf-8")
    except configparser.Error as exc:
        logger.warning("_lire: {} illisible ({}), valeurs par défaut", config_file, exc)
        cp = configparser.RawConfigParser()
    logger.debug("_lire: {} section(s)", len(cp.sections()))
    return cp


def load_window_state(config_file: str) -> WindowState:
    """État lu dans ``config_file`` ; clé absente ou invalide : valeur par défaut."""
    logger.debug("load_window_state: {}", config_file)
    cp = _lire(config_file)
    defaut = WindowState()
    etat = WindowState()
    for attr, cle in (
        ("width", CLE_LARGEUR),
        ("height", CLE_HAUTEUR),
        ("panel_width", CLE_PANNEAU),
    ):
        try:
            setattr(etat, attr, cp.getint(SECTION, cle, fallback=getattr(defaut, attr)))
        except ValueError:
            logger.warning("load_window_state: [{}] {} invalide, défaut conservé", SECTION, cle)
    try:
        etat.show_panel = cp.getboolean(SECTION, CLE_PANNEAU_VISIBLE, fallback=defaut.show_panel)
    except ValueError:
        logger.warning(
            "load_window_state: [{}] {} invalide, défaut conservé", SECTION, CLE_PANNEAU_VISIBLE
        )
    logger.debug("load_window_state: {}", etat)
    return etat


def save_window_state(config_file: str, etat: WindowState) -> None:
    """Écrit ``etat`` dans ``config_file`` sans toucher aux autres clés.

    Écriture sûre de :func:`gcm4.core.config.update_keys` (0600, ``fsync``,
    ``.bak``) ; un ``gcm4.conf`` corrompu n'est jamais réécrit.

    Raises:
        ConfigError: fichier illisible ou corrompu, ou écriture impossible.
    """
    logger.debug("save_window_state: {} {}", config_file, etat)
    update_keys(
        config_file,
        SECTION,
        {
            CLE_LARGEUR: str(etat.width),
            CLE_HAUTEUR: str(etat.height),
            CLE_PANNEAU: str(etat.panel_width),
            CLE_PANNEAU_VISIBLE: str(etat.show_panel),
        },
    )
    logger.debug("save_window_state: écrit")
