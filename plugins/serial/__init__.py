# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin serial : port série local (picocom, minicom, screen) dans un terminal.

Découpage du portage GTK4 (#179) : :mod:`core` sans GTK, :mod:`gtk4` pour
l'interface ; chargé en mode source comme ``gcm4_plugin_serial.gtk4``
(#288).
"""
