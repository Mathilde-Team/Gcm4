# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from JSON » (BatchPlugin) pour GCM.

Anciennement ``Wmain.on_mnu_import_json_activate`` dans
``gnome_connection_manager.py``. Voir ``plugin_import_csv.py`` pour le
contexte complet (même famille, même délégation au cœur pour la conversion
en ``Host`` et l'insertion) — seul le format de fichier change.

Format JSON attendu : une liste d'objets (ou un objet dont les valeurs sont
de tels objets), avec les mêmes clés que ``Wmain._CSV_FIELDS``, sans mot de
passe.
"""

from __future__ import annotations

import json

import gi
from loguru import logger

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

# isort: split
from plugin_base import BatchPlugin  # noqa: E402, I001
from utils import msgbox, show_open_dialog  # noqa: E402, I001

# Fallback gettext: injected by GCM at startup if available, no-op otherwise.
if "_" not in globals():

    def _(s: str) -> str:  # noqa: D401
        """Fallback no-op translation for isolated/test contexts."""
        return s


__all__ = ["JsonImportBatchPlugin", "get_batch_plugin"]


class JsonImportBatchPlugin(BatchPlugin):
    """Importe des connexions depuis un fichier JSON (sans mot de passe).

    Délègue entièrement la conversion en ``Host`` et l'insertion au cœur
    (``Wmain``). N'expose qu'un dialogue de choix de fichier et la lecture
    JSON brute.
    """

    tool_id = "import-json"
    display_name = _("Import from JSON…")
    icon_name = "text-x-generic-symbolic"
    menu_section = "import"

    def activate(self) -> None:
        """Déclenche l'import JSON : dialogue fichier, lecture, insertion.

        Raises:
            RuntimeError: Si app n'est pas liée (erreur de configuration).
        """
        if self.app is None:
            raise RuntimeError(
                "JsonImportBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        app = self.app
        logger.debug("JsonImportBatchPlugin.activate | open file chooser")

        filename = show_open_dialog(
            parent=app.window, title=_("Import from JSON"), action=Gtk.FileChooserAction.OPEN
        )
        if not filename:
            return

        try:
            with open(filename, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = list(data.values())
            hosts = [app._dict_to_host(d) for d in data if isinstance(d, dict) and d.get("name")]
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"JsonImportBatchPlugin.activate | JSON import error: {exc}")
            msgbox(_("JSON import error: {exc}").format(exc=exc), parent=app.window)
            return

        if not hosts:
            msgbox(_("No valid entry found in JSON."), parent=app.window)
            return

        n = app._import_hosts_from_list(hosts)
        logger.info(f"JsonImportBatchPlugin.activate | imported={n} file={filename}")
        msgbox(_("{n} connection(s) imported from JSON.").format(n=n), parent=app.window)


def get_batch_plugin() -> JsonImportBatchPlugin:
    """Point d'entrée de découverte pour ``BatchPluginRegistry.autoload()``.

    Returns:
        JsonImportBatchPlugin: Instance du plugin JSON.
    """
    return JsonImportBatchPlugin()
