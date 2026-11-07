# SPDX-License-Identifier: GPL-3.0-or-later
"""Boucle asyncio branchée sur la boucle GLib de l'application (#105).

Les plugins écrits en asyncio (rdp sur AsyncRdp, vnc de pluginvnc2)
lancent leurs coroutines depuis un callback GTK
(``asyncio.ensure_future``) : il faut qu'une boucle asyncio tourne dans
la boucle GLib. PyGObject 3.50 et plus le permet avec
``gi.events.GLibEventLoopPolicy`` : la politique doit être installée
**avant** ``Gtk.Application.run`` (installée depuis une application déjà
lancée, elle reste sans effet : constaté le 2026-11-01).

Sans ``gi.events`` (PyGObject plus ancien), l'application tourne sans
asyncio et les plugins qui en ont besoin signalent l'erreur à l'ouverture
(:func:`asyncio_disponible`). La politique précédente est restaurée à la
sortie.
"""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Callable

from loguru import logger


def _politique_glib() -> type | None:
    """Classe ``gi.events.GLibEventLoopPolicy``, ``None`` si PyGObject est trop ancien."""
    logger.debug("_politique_glib: entrée")
    try:
        from gi.events import GLibEventLoopPolicy
    except ImportError as exc:
        logger.debug("_politique_glib: absente ({})", exc)
        return None
    logger.debug("_politique_glib: disponible")
    return GLibEventLoopPolicy


def executer(lancer: Callable[[], int]) -> int:
    """Appelle ``lancer`` (``application.run``) avec asyncio branché sur GLib.

    Args:
        lancer: lance la boucle GTK et rend son code de sortie.

    Returns:
        Le code rendu par ``lancer``.
    """
    logger.debug("executer: entrée")
    politique = _politique_glib()
    if politique is None:
        code = lancer()
        logger.debug("executer: sortie {} (sans asyncio)", code)
        return code
    with warnings.catch_warnings():
        # asyncio.*_event_loop_policy : dépréciés en 3.14, retirés en 3.16 ;
        # PyGObject n'expose pas encore d'autre point d'entrée.
        warnings.simplefilter("ignore", DeprecationWarning)
        precedente = asyncio.get_event_loop_policy()
        asyncio.set_event_loop_policy(politique())
    try:
        code = lancer()
    finally:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(precedente)
    logger.debug("executer: sortie {} (asyncio sur GLib)", code)
    return code


def asyncio_disponible() -> bool:
    """Une boucle asyncio tourne-t-elle dans la boucle GLib (callback GTK en cours) ?"""
    logger.debug("asyncio_disponible: entrée")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        politique = asyncio.get_event_loop_policy()
    classe = _politique_glib()
    resultat = classe is not None and isinstance(politique, classe)
    logger.debug("asyncio_disponible: {}", resultat)
    return resultat
