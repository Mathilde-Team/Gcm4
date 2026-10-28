# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from PuTTY sessions » (BatchPlugin) pour GCM.

Backlog §4.2 (urgence faible) de ``features.md``. Au même titre que
``plugin_import_csv.py``/``plugin_import_json.py``, découvert et enregistré
automatiquement par ``BatchPluginRegistry.autoload()`` via
``get_batch_plugin()`` tout en bas de ce fichier.

Toute la logique de scan/parsing (sans mot de passe : PuTTY ne stocke jamais
de mot de passe en clair dans un fichier de session) vit dans
``putty_import_core.py`` (zéro GTK, testable directement) — ce module ne fait
que le choix du dossier et l'appel au cœur métier, à l'image de
``plugin_netmiko_push.py``/``netmiko_bulk_core.py``.
"""

from __future__ import annotations

import gi
from loguru import logger

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

# isort: split
from plugin_base import BatchPlugin  # noqa: E402, I001
from putty_import_core import DEFAULT_SESSIONS_DIR, scan_putty_sessions  # noqa: E402, I001
from utils import msgbox  # noqa: E402, I001

# Fallback gettext: injected by GCM at startup if available, no-op otherwise.
if "_" not in globals():

    def _(s: str) -> str:  # noqa: D401
        """Fallback no-op translation for isolated/test contexts."""
        return s


__all__ = ["PuttyImportBatchPlugin", "get_batch_plugin"]


class PuttyImportBatchPlugin(BatchPlugin):
    """Importe des connexions SSH/Telnet depuis des sessions PuTTY (Unix).

    Délègue entièrement le scan/parsing à ``putty_import_core`` et la
    conversion en ``Host``/l'insertion au cœur (``Wmain``). N'expose qu'un
    dialogue de choix de dossier et le résumé de l'import.
    """

    tool_id = "import-putty"
    display_name = _("Import from PuTTY sessions…")
    icon_name = "text-x-generic-symbolic"
    menu_section = "import"

    def activate(self) -> None:
        """Déclenche l'import PuTTY : dialogue dossier, scan, insertion.

        Raises:
            RuntimeError: Si app n'est pas liée (erreur de configuration).
        """
        if self.app is None:
            raise RuntimeError(
                "PuttyImportBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        app = self.app
        logger.debug("PuttyImportBatchPlugin.activate | open folder chooser")

        dlg = Gtk.FileChooserDialog(
            title=_("Select PuTTY sessions folder"),
            parent=app.window,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dlg.add_button(_("Open"), Gtk.ResponseType.OK)
        if DEFAULT_SESSIONS_DIR.is_dir():
            dlg.set_current_folder(str(DEFAULT_SESSIONS_DIR))
        response = dlg.run()
        folder = dlg.get_filename() if response == Gtk.ResponseType.OK else None
        dlg.destroy()
        if not folder:
            logger.debug("PuttyImportBatchPlugin.activate | cancelled")
            return

        try:
            from pathlib import Path

            rows, skipped = scan_putty_sessions(Path(folder))
            hosts = [app._dict_to_host(row) for row in rows]
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"PuttyImportBatchPlugin.activate | PuTTY import error: {exc}")
            msgbox(_("PuTTY import error: {exc}").format(exc=exc), parent=app.window)
            return

        if not hosts:
            logger.debug(
                f"PuttyImportBatchPlugin.activate | nothing importable, skipped={len(skipped)}"
            )
            msgbox(_("No importable SSH/Telnet session found in this folder."), parent=app.window)
            return

        n = app._import_hosts_from_list(hosts)
        logger.info(
            f"PuttyImportBatchPlugin.activate | imported={n} skipped={len(skipped)} folder={folder}"
        )
        message = _("{n} connection(s) imported from PuTTY.").format(n=n)
        if skipped:
            message += "\n" + _(
                "{count} session(s) ignored (unsupported protocol or missing host)."
            ).format(count=len(skipped))
        msgbox(message, parent=app.window)


def get_batch_plugin() -> PuttyImportBatchPlugin:
    """Point d'entrée de découverte pour ``BatchPluginRegistry.autoload()``.

    Returns:
        PuttyImportBatchPlugin: Instance du plugin d'import PuTTY.
    """
    return PuttyImportBatchPlugin()
