# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin web : console HTML5 (iLO, iDRAC, IMM…) ou toute interface web dans un onglet.

Découpage du portage GTK4 (#179, #397) : :mod:`core` sans GTK, :mod:`gtk4`
pour l'interface ; chargé en mode source comme ``gcm4_plugin_web.gtk4`` (#288).
"""
