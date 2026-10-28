# SPDX-License-Identifier: GPL-3.0-or-later
"""Interface GTK4 de gcm4 : seul endroit autorisé à importer ``gi``.

Versions exigées ici et nulle part ailleurs (#119) : GTK 4.0 et
libadwaita 1 (décision D1 de #118). Un ``ValueError`` de
``require_version`` ou un ``ImportError`` remonte à ``gcm4.app.main``,
qui affiche la commande d'installation.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
