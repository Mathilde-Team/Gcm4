# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin rdp : bureau à distance RDP sur AsyncRdp (FreeRDP 3), sans gtk-frdp.

Découpage du portage GTK4 (#105, #71) : :mod:`core` sans GTK (options INI,
validation, réglages de connexion), :mod:`gtk4` pour l'interface ; chargé
en mode source comme ``gcm4_plugin_rdp.gtk4`` (#288).
"""
