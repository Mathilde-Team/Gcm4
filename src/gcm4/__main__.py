# SPDX-License-Identifier: GPL-3.0-or-later
"""``python -m gcm4`` : même point d'entrée que la commande ``gcm4``."""

from gcm4.app import main

raise SystemExit(main())
