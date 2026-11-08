# SPDX-License-Identifier: GPL-3.0-or-later
"""Évite de confondre une suite GTK sous Xvfb avec une validation Wayland (#77)."""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkWayland", "4.0")

from gi.repository import Gdk, GdkWayland, Gtk  # noqa: E402


def test_backend_wayland_sans_affichage_x():
    """Le processus de test utilise Wayland même si X11 existe sur le runner."""
    assert "DISPLAY" not in os.environ
    assert os.environ["GDK_BACKEND"] == "wayland"
    assert os.environ["GCM_TESTS_GTK4"] == "1"
    assert Gtk.init_check()
    affichage = Gdk.Display.get_default()
    assert isinstance(affichage, GdkWayland.WaylandDisplay)
    assert affichage.get_name() == os.environ["WAYLAND_DISPLAY"]
