# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin outil fragile des tests (#73) échoue toujours, sans être fatal."""

from __future__ import annotations

from loguru import logger  # noqa: E402

from gcm4.core.i18n import N_  # noqa: E402
from gcm4.core.plugins import ToolPlugin  # noqa: E402


class OutilFragile(ToolPlugin):
    """Outil qui échoue toujours : l'échec doit être notifié, pas fatal."""

    name = "outil-fragile"
    version = "1.0"
    menu_label = N_("Fragile Tool…")

    def activate(self) -> None:
        """Échoue systématiquement."""
        logger.debug("OutilFragile.activate: entrée")
        raise RuntimeError("outil cassé")


PLUGIN = OutilFragile
