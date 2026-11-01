# SPDX-License-Identifier: GPL-3.0-or-later
"""Boucle asyncio sur GLib (``gcm4.ui.boucle``, #105)."""

from __future__ import annotations

import asyncio
import warnings

from gcm4.ui import boucle


class PolitiqueGlib(asyncio.DefaultEventLoopPolicy):
    """Doublure de ``gi.events.GLibEventLoopPolicy`` (absente sur Debian 12, Rocky 9)."""


def _politique():
    """Politique asyncio courante."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return asyncio.get_event_loop_policy()


def test_politique_detectee():
    """``gi.events`` présent (PyGObject 3.50+) : GLibEventLoopPolicy, sinon None."""
    try:
        from gi.events import GLibEventLoopPolicy
    except ImportError:
        GLibEventLoopPolicy = None
    assert boucle._politique_glib() is GLibEventLoopPolicy


def test_politique_glib_pendant_run_puis_restauree(monkeypatch):
    """La politique GLib est installée pendant ``lancer``, l'ancienne remise après."""
    monkeypatch.setattr(boucle, "_politique_glib", lambda: PolitiqueGlib)
    avant = _politique()
    vus = []

    def lancer():
        """Faux ``application.run`` : note la politique et l'état d'asyncio."""
        vus.append((isinstance(_politique(), PolitiqueGlib), boucle.asyncio_disponible()))
        return 3

    assert boucle.executer(lancer) == 3
    assert vus == [(True, True)]
    assert _politique() is avant
    assert boucle.asyncio_disponible() is False


def test_politique_restauree_apres_exception(monkeypatch):
    """Une exception de ``lancer`` remonte, la politique est quand même restaurée."""
    monkeypatch.setattr(boucle, "_politique_glib", lambda: PolitiqueGlib)
    avant = _politique()

    def lancer():
        """Faux ``application.run`` qui échoue."""
        raise RuntimeError("boum")

    try:
        boucle.executer(lancer)
    except RuntimeError:
        pass
    assert _politique() is avant


def test_sans_gi_events(monkeypatch):
    """PyGObject sans ``gi.events`` : ``lancer`` est appelé tel quel."""
    monkeypatch.setattr(boucle, "_politique_glib", lambda: None)
    assert boucle.executer(lambda: 0) == 0
    assert boucle.asyncio_disponible() is False
