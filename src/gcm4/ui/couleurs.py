# SPDX-License-Identifier: GPL-3.0-or-later
"""Conversions de couleurs partagées par l'interface GTK4."""

from __future__ import annotations

from gi.repository import Gdk
from loguru import logger


def rgba_en_hex(rgba: Gdk.RGBA) -> str:
    """``Gdk.RGBA`` → ``#rrggbb`` (format de ``group-colors``, ``font-color``, ``back-color``)."""
    logger.trace("rgba_en_hex")
    resultat = "#{:02x}{:02x}{:02x}".format(
        *(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue))
    )
    logger.trace("rgba_en_hex: {}", resultat)
    return resultat
