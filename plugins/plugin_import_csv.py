# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from CSV » (BatchPlugin) pour GCM.

Anciennement ``Wmain.on_mnu_import_csv_activate`` dans
``gnome_connection_manager.py``. Porté ici comme ``BatchPlugin`` (menu
Fichier → Imports), au même titre que ``plugin_import_json.py``,
``plugin_import_libvirt.py``, ``plugin_import_proxmox.py``,
``plugin_import_virtualbox.py`` et ``plugin_import_ovirt.py`` —
découvert et enregistré automatiquement par
``BatchPluginRegistry.autoload()`` (cf. ``plugin_base.py``) via
``get_batch_plugin()`` tout en bas de ce fichier.

Format CSV attendu : une ligne d'en-tête avec les noms de colonnes de
``Wmain._CSV_FIELDS`` (cf. ``gnome_connection_manager.py``), sans mot de
passe (jamais exporté ni importé en clair par ce format). La conversion
ligne → ``Host`` et l'insertion en base restent gérées par le cœur
(``Wmain._dict_to_host`` / ``Wmain._import_hosts_from_list``), auxquelles
ce plugin délègue entièrement — il ne fait que le choix du fichier et la
lecture CSV brute.
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


__all__ = ["CsvImportBatchPlugin", "get_batch_plugin"]


class CsvImportBatchPlugin(BatchPlugin):
    """Importe des connexions depuis un fichier CSV (sans mot de passe).

    Délègue entièrement la conversion en ``Host`` et l'insertion au cœur
    (``Wmain``). N'expose qu'un dialogue de choix de fichier et la lecture
    CSV brute.
    """

    tool_id = "import-csv"
    display_name = _("Import from CSV…")
    icon_name = "text-x-generic-symbolic"
    menu_section = "import"

    def activate(self) -> None:
        """Déclenche l'import CSV : dialogue fichier, lecture, insertion.

        Raises:
            RuntimeError: Si app n'est pas liée (erreur de configuration).
        """
        if self.app is None:
            raise RuntimeError(
                "CsvImportBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        app = self.app
        logger.debug("CsvImportBatchPlugin.activate | open file chooser")

        filename = show_open_dialog(
            parent=app.window, title=_("Import from CSV"), action=Gtk.FileChooserAction.OPEN
        )
        if not filename:
            return

        try:
            with open(filename, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                hosts = [app._dict_to_host(row) for row in reader if row.get("name")]
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"CsvImportBatchPlugin.activate | CSV import error: {exc}")
            msgbox(_("CSV import error: {exc}").format(exc=exc), parent=app.window)
            return

        if not hosts:
            msgbox(_("No valid entry found in CSV."), parent=app.window)
            return

        n = app._import_hosts_from_list(hosts)
        logger.info(f"CsvImportBatchPlugin.activate | imported={n} file={filename}")
        msgbox(_("{n} connection(s) imported from CSV.").format(n=n), parent=app.window)


def get_batch_plugin() -> CsvImportBatchPlugin:
    """Point d'entrée de découverte pour ``BatchPluginRegistry.autoload()``.

    Returns:
        CsvImportBatchPlugin: Instance du plugin CSV.
    """
    return CsvImportBatchPlugin()
