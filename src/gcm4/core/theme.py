# SPDX-License-Identifier: GPL-3.0-or-later
"""Thème de la base GTK4, sans GTK (#135).

Clés reprises de l'application GTK3 (``Wmain.loadConfig``) :
``[window] theme-mode`` (``system`` / ``light`` / ``dark``, validée par
``gcm4_core.resolve_theme_mode``) et, à défaut, l'ancienne
``[window] dark-mode`` (booléen). ``gcm4.ui`` applique le résultat avec
``Adw.StyleManager`` (:data:`SCHEMAS`).

Couleurs : classes CSS réutilisables (arbre des hôtes, #121) générées
depuis les couleurs de groupe ``#rrggbb``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from loguru import logger

import gcm4_core
from gcm4.core.config import update_keys
from gcm4.core.window_state import SECTION, _lire

CLE_THEME = "theme-mode"
CLE_SOMBRE = "dark-mode"
MODES: tuple[str, ...] = ("system", "light", "dark")
#: Mode → nom de membre de ``Adw.ColorScheme``.
SCHEMAS: dict[str, str] = {"system": "DEFAULT", "light": "FORCE_LIGHT", "dark": "FORCE_DARK"}
_COULEUR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def load_theme(config_file: str) -> str:
    """Mode enregistré ; ``theme-mode`` absent → ``dark-mode`` (GTK3) ; invalide → ``system``."""
    logger.debug("load_theme: {}", config_file)
    cp = _lire(config_file)
    if cp.has_option(SECTION, CLE_THEME):
        mode: str = gcm4_core.resolve_theme_mode(cp.get(SECTION, CLE_THEME).strip().lower())
        logger.debug("load_theme: {}", mode)
        return mode
    try:
        sombre = cp.getboolean(SECTION, CLE_SOMBRE, fallback=False)
    except ValueError:
        logger.warning("load_theme: [{}] {} invalide", SECTION, CLE_SOMBRE)
        sombre = False
    mode = "dark" if sombre else "system"
    logger.debug("load_theme: {} (d'après {})", mode, CLE_SOMBRE)
    return mode


def save_theme(config_file: str, mode: str) -> None:
    """Enregistre ``mode`` (et ``dark-mode`` pour l'application GTK3) sans toucher au reste.

    Raises:
        ValueError: mode inconnu.
        ConfigError: fichier corrompu (jamais réécrit) ou écriture impossible.
    """
    logger.debug("save_theme: {} {}", config_file, mode)
    if mode not in MODES:
        raise ValueError(mode)
    update_keys(config_file, SECTION, {CLE_THEME: mode, CLE_SOMBRE: str(mode == "dark")})
    logger.debug("save_theme: écrit")


def color_class(couleur: str) -> str | None:
    """``#FF8800`` → ``gcm-couleur-ff8800`` ; ``#f80`` → ``gcm-couleur-ff8800`` ; invalide → ``None``."""
    logger.trace("color_class: {!r}", couleur)
    couleur = couleur.strip()
    if not _COULEUR.match(couleur):
        logger.trace("color_class: invalide")
        return None
    hexa = couleur[1:].lower()
    if len(hexa) == 3:
        hexa = "".join(c * 2 for c in hexa)
    resultat = f"gcm-couleur-{hexa}"
    logger.trace("color_class: -> {}", resultat)
    return resultat


def colors_css(couleurs: Iterable[str]) -> str:
    """Feuille CSS : une classe par couleur valide (triées, sans doublon)."""
    logger.debug("colors_css")
    classes = sorted({c for c in (color_class(x) for x in couleurs) if c})
    resultat = "".join(f".{c} {{ color: #{c.rsplit('-', 1)[1]}; }}\n" for c in classes)
    logger.debug("colors_css: {} classe(s)", len(classes))
    return resultat
