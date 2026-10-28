# SPDX-License-Identifier: GPL-3.0-or-later
"""gcm4 : Gnome Connection Manager en GTK4 (épopée #118).

Arbre ``src/gcm4/`` (décision D5) : ``core/`` sans GTK, ``ui/`` seul
endroit autorisé à importer ``gi``. L'application GTK3 historique
(``gnome_connection_manager.py``) reste exécutable jusqu'à la bascule.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from loguru import logger

# Convention bibliothèque (#130) : gcm4 et ses sous-modules se taisent
# jusqu'à logging_config.configure_logging() (CORE_MODULES).
logger.disable(__name__)

DISTRIBUTION = "gnome-connection-manager"


def _version() -> str:
    """Version de la distribution installée, ``0+inconnue`` hors installation."""
    logger.debug("gcm4._version: lecture des métadonnées de {}", DISTRIBUTION)
    try:
        trouvee = version(DISTRIBUTION)
    except PackageNotFoundError:
        logger.debug("gcm4._version: distribution non installée")
        return "0+inconnue"
    logger.debug("gcm4._version: {}", trouvee)
    return trouvee


__version__ = _version()
