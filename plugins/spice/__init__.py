# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin spice : bureau à distance SPICE dans la visionneuse externe ``remote-viewer``.

Découpage du portage GTK4 (#179) : :mod:`core` sans GTK, :mod:`gtk4` pour
l'interface ; chargé en mode source comme ``gcm4_plugin_spice.gtk4`` (#288).
"""
