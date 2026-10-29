# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Export to CSV » (BatchPlugin) pour GCM.

Complément de ``plugin_import_csv.py`` : exporte toutes les connexions
configurées vers un fichier CSV (sans mot de passe). Découvert et
enregistré automatiquement par ``BatchPluginRegistry.autoload()``
(cf. ``plugin_base.py``) via ``get_batch_plugin()`` tout en bas de ce fichier.

Format CSV généré : une ligne d'en-tête avec les noms de colonnes de
``Wmain._CSV_FIELDS`` (cf. ``gnome_connection_manager.py``), sans mot de
passe (jamais exporté). Les données exportées peuvent être ré-importées
via ``plugin_import_csv.py``.
"""

from __future__ import annotations

import csv

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


__all__ = ["CsvExportBatchPlugin", "get_batch_plugin"]


class CsvExportBatchPlugin(BatchPlugin):
    """Exporte toutes les connexions vers un fichier CSV (sans mot de passe).

    Délègue entièrement la sérialisation au cœur (``Wmain._host_to_dict``),
    n'exposant que le dialogue de choix de fichier et l'écriture CSV brute.
    """

    tool_id = "export-csv"
    display_name = _("Export to CSV…")
    icon_name = "text-x-generic-symbolic"
    menu_section = "export"

    def activate(self) -> None:
        """Déclenche l'export CSV : dialogue fichier, sérialisation, écriture.

        Raises:
            RuntimeError: Si app n'est pas liée (erreur de configuration).
        """
        if self.app is None:
            raise RuntimeError(
                "CsvExportBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        app = self.app
        logger.debug("CsvExportBatchPlugin.activate | open file chooser")

        filename = show_open_dialog(
            parent=app.window, title=_("Export to CSV"), action=Gtk.FileChooserAction.SAVE
        )
        if not filename:
            return

        if not filename.endswith(".csv"):
            filename += ".csv"

        try:
            hosts = app._all_hosts()
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=app._csv_fields())
                writer.writeheader()
                for h in hosts:
                    writer.writerow(app._host_to_dict(h))
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"CsvExportBatchPlugin.activate | CSV export error: {exc}")
            msgbox(_("CSV export error: {exc}").format(exc=exc), parent=app.window)
            return

        n = len(hosts)
        logger.info(f"CsvExportBatchPlugin.activate | exported={n} file={filename}")
        msgbox(
            _("{n} connection(s) exported to {filename}.").format(n=n, filename=filename),
            parent=app.window,
        )


def get_batch_plugin() -> CsvExportBatchPlugin:
    """Retourne l'instance unique du plugin CSV export (découverte autoload).

    Returns:
        CsvExportBatchPlugin: Instance du plugin d'export CSV.
    """
    return CsvExportBatchPlugin()
