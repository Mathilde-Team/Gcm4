# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Export to JSON » (BatchPlugin) pour GCM.

Complément de ``plugin_import_json.py`` : exporte toutes les connexions
configurées vers un fichier JSON (sans mot de passe). Découvert et
enregistré automatiquement par ``BatchPluginRegistry.autoload()``
(cf. ``plugin_base.py``) via ``get_batch_plugin()`` tout en bas de ce fichier.

Format JSON généré : une liste d'objets avec les mêmes clés que
``Wmain._CSV_FIELDS`` (cf. ``gnome_connection_manager.py``), sans mot de
passe (jamais exporté). Les données exportées peuvent être ré-importées
via ``plugin_import_json.py``.
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


__all__ = ["JsonExportBatchPlugin", "get_batch_plugin"]


class JsonExportBatchPlugin(BatchPlugin):
    """Exporte toutes les connexions vers un fichier JSON (sans mot de passe).

    Délègue entièrement la sérialisation au cœur (``Wmain._host_to_dict``),
    n'exposant que le dialogue de choix de fichier et l'écriture JSON brute.
    """

    tool_id = "export-json"
    display_name = _("Export to JSON…")
    icon_name = "text-x-generic-symbolic"
    menu_section = "export"

    def activate(self) -> None:
        """Déclenche l'export JSON : dialogue fichier, sérialisation, écriture.

        Raises:
            RuntimeError: Si app n'est pas liée (erreur de configuration).
        """
        if self.app is None:
            raise RuntimeError(
                "JsonExportBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        app = self.app
        logger.debug("JsonExportBatchPlugin.activate | open file chooser")

        filename = show_open_dialog(
            parent=app.window, title=_("Export to JSON"), action=Gtk.FileChooserAction.SAVE
        )
        if not filename:
            return

        if not filename.endswith(".json"):
            filename += ".json"

        try:
            hosts = app._all_hosts()
            data = [app._host_to_dict(h) for h in hosts]
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"JsonExportBatchPlugin.activate | JSON export error: {exc}")
            msgbox(_("JSON export error: {exc}").format(exc=exc), parent=app.window)
            return

        n = len(hosts)
        logger.info(f"JsonExportBatchPlugin.activate | exported={n} file={filename}")
        msgbox(
            _("{n} connection(s) exported to {filename}.").format(n=n, filename=filename),
            parent=app.window,
        )


def get_batch_plugin() -> JsonExportBatchPlugin:
    """Retourne l'instance unique du plugin JSON export (découverte autoload).

    Returns:
        JsonExportBatchPlugin: Instance du plugin d'export JSON.
    """
    return JsonExportBatchPlugin()
