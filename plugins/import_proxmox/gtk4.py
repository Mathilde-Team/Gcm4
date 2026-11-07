# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from Proxmox » (contrat v1, #439, portage #179).

Portage GTK4 de ``plugins/plugin_import_proxmox.py`` (``BatchPlugin``
GTK3, supprimé) : plugin outil sur l'onglet générique des imports
d'hyperviseurs — cibles (URIs dconf qemu+ssh + saisie manuelle), types
de connexions, suivi, prévisualisation à cases à cocher, import via
``PluginContext.import_hosts`` (#436). Logique ``qm`` dans :mod:`core`,
interface commune dans ``gcm4_hv.ui``.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import N_, _  # noqa: E402
from gcm4.core.plugins import SECTION_IMPORT, ToolPlugin  # noqa: E402

from . import core  # noqa: E402
from .core import partager_ui

ui = partager_ui()


class OngletImportProxmox(ui.OngletImportHyperviseurs):
    """Onglet d'import Proxmox : cibles, types de connexions, scan, import."""

    def __init__(self, context: object) -> None:
        """Construit l'onglet (les URIs dconf sont proposées d'office)."""
        logger.debug("OngletImportProxmox.__init__: entrée")
        super().__init__(context, "Proxmox")
        logger.debug("OngletImportProxmox.__init__: sortie")

    def _construire_parametres(self) -> Gtk.Widget:
        """Page de configuration : utilisateur SSH, cibles, protocoles, scan."""
        logger.debug("_construire_parametres: entrée")
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        ligne_utilisateur = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ligne_utilisateur.append(Gtk.Label(label=_("Hypervisor SSH user:")))
        self.saisie_utilisateur = Gtk.Entry()
        self.saisie_utilisateur.set_text(
            str(self.context.config().get("default_user", "root") or "root")
        )
        self.saisie_utilisateur.set_width_chars(12)
        ligne_utilisateur.append(self.saisie_utilisateur)
        boite.append(ligne_utilisateur)

        boite.append(Gtk.Label(label=_("Proxmox targets:"), xalign=0))
        self.zone_cibles = ui.ZoneCibles(
            self.log,
            core.normaliser_cible,
            _("Invalid Proxmox target."),
            titre_colonne=_("Proxmox target (qemu+ssh://user@host/system)"),
        )
        cibles = [
            u
            for u in core.hv_common.libvirt_get_uris_from_dconf()
            if "+ssh://" in u and "/system" in u
        ]
        if not cibles:
            self.log(_("No SSH URI found in dconf.\nEnter an IP/host below and click Add."))
        self.zone_cibles.definir(cibles)
        boite.append(self.zone_cibles)

        boite.append(
            Gtk.Label(label=_("<b>Connection types to import:</b>"), xalign=0, use_markup=True)
        )
        self.cases_protocole: dict[str, Gtk.CheckButton] = {}
        for cle, libelle, actif in (
            (
                "ssh",
                _(
                    "SSH  (ProxyJump -J via hypervisor when VM IP is known, "
                    "otherwise hypervisor shell + -t ssh user@vm)"
                ),
                True,
            ),
            (
                "spice",
                _("SPICE  (vga:qxl required — ticket generated at connection time via pvesh)"),
                True,
            ),
            (
                "rdp",
                _(
                    "RDP  (probe port 3389 from hypervisor via nc/nmap - "
                    "skipped if port is closed)"
                ),
                True,
            ),
            (
                "vnc",
                _(
                    "VNC  (probe port 5900 from hypervisor via nc/nmap - "
                    "skipped if port is closed)"
                ),
                False,
            ),
        ):
            case = Gtk.CheckButton(label=libelle)
            case.set_active(actif)
            self.cases_protocole[cle] = case
            boite.append(case)

        self.bouton_scan = Gtk.Button(label=_("🔍  Scan Proxmox hosts"))
        self.bouton_scan.add_css_class("suggested-action")
        self.bouton_scan.set_halign(Gtk.Align.END)
        self.bouton_scan.connect("clicked", self._on_scan)
        boite.append(self.bouton_scan)

        defilement = Gtk.ScrolledWindow(vexpand=True)
        defilement.set_child(boite)
        logger.debug("_construire_parametres: sortie")
        return defilement

    def _on_scan(self, _bouton: Gtk.Button) -> None:
        """Lance le scan des cibles cochées avec les protocoles choisis."""
        logger.debug("_on_scan: entrée")
        cibles = self.zone_cibles.cibles()
        if not cibles:
            self.log(_("No Proxmox target selected."))
            logger.debug("_on_scan: aucune cible")
            return
        utilisateur = self.saisie_utilisateur.get_text().strip() or "root"
        protocoles = {cle for cle, case in self.cases_protocole.items() if case.get_active()}
        if not protocoles:
            self.log(_("No connection type selected."))
            logger.debug("_on_scan: aucun protocole")
            return
        self.bouton_scan.set_sensitive(False)
        self._stack.set_visible_child_name("monitor")
        self.lancer_scan(
            lambda: core.collecter_hotes(
                cibles, utilisateur, self.log, self.progression, protocoles
            )
        )
        logger.debug("_on_scan: scan lancé")


class ImportProxmoxPlugin(ToolPlugin):
    """« Import from Proxmox » : onglet de scan des hyperviseurs Proxmox."""

    name = "import-proxmox"
    version = "1.0"
    menu_label = N_("Import from Proxmox")
    menu_section = SECTION_IMPORT
    icon_name = "network-server-symbolic"

    def activate(self) -> None:
        """Ouvre l'onglet d'import Proxmox."""
        logger.debug("ImportProxmoxPlugin.activate: entrée")
        self.context.open_tab(_("Import from Proxmox"), OngletImportProxmox(self.context))
        logger.debug("ImportProxmoxPlugin.activate: fait")


PLUGIN = ImportProxmoxPlugin
