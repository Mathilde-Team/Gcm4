# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from libvirt » (contrat v1, #439, portage #179).

Portage GTK4 de ``plugins/plugin_import_libvirt.py`` (``BatchPlugin``
GTK3, supprimé) : un plugin outil ouvre l'onglet générique des imports
d'hyperviseurs — cibles (URIs dconf + saisie manuelle), types de
connexions, suivi du scan, prévisualisation à cases à cocher et import
via le service du contexte (``PluginContext.import_hosts``, #436). La
logique de scan (zéro GTK) vit dans :mod:`core` ; l'interface commune
dans ``gcm4_hv.ui``.
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


class OngletImportLibvirt(ui.OngletImportHyperviseurs):
    """Onglet d'import libvirt : URIs, types de connexions, scan, import."""

    def __init__(self, context: object) -> None:
        """Construit l'onglet (les URIs dconf sont proposées d'office)."""
        logger.debug("OngletImportLibvirt.__init__: entrée")
        super().__init__(context, "libvirt")
        logger.debug("OngletImportLibvirt.__init__: sortie")

    def _construire_parametres(self) -> Gtk.Widget:
        """Page de configuration : utilisateur SSH, URIs, protocoles, scan."""
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

        boite.append(Gtk.Label(label=_("Hypervisors (virt-manager / dconf):"), xalign=0))
        self.zone_cibles = ui.ZoneCibles(
            self.log,
            core.normaliser_cible,
            _("Invalid libvirt target."),
            titre_colonne=_("Detected libvirt URI"),
        )
        uris = core.hv_common.libvirt_get_uris_from_dconf()
        if not uris:
            self.log(
                _(
                    "No URI found in dconf (virt-manager not configured?).\n"
                    "Add your URIs manually below."
                )
            )
            uris = ["qemu+ssh://root@hyperviseur/system"]
        self.zone_cibles.definir(uris)
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
                _(
                    "SPICE  (virt-viewer --connect qemu+ssh://... vm - "
                    "SSH tunnel handled automatically)"
                ),
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

        self.bouton_scan = Gtk.Button(label=_("🔍  Scan Libvirt hypervisors"))
        self.bouton_scan.add_css_class("suggested-action")
        self.bouton_scan.set_halign(Gtk.Align.END)
        self.bouton_scan.connect("clicked", self._on_scan)
        boite.append(self.bouton_scan)

        defilement = Gtk.ScrolledWindow(vexpand=True)
        defilement.set_child(boite)
        logger.debug("_construire_parametres: sortie")
        return defilement

    def _on_scan(self, _bouton: Gtk.Button) -> None:
        """Lance le scan des URIs cochées avec les protocoles choisis."""
        logger.debug("_on_scan: entrée")
        uris = self.zone_cibles.cibles()
        if not uris:
            self.log(_("No URI selected."))
            logger.debug("_on_scan: aucune URI")
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
            lambda: core.collecter_hotes(uris, utilisateur, self.log, self.progression, protocoles)
        )
        logger.debug("_on_scan: scan lancé")


class ImportLibvirtPlugin(ToolPlugin):
    """« Import from libvirt » : onglet de scan des hyperviseurs libvirt."""

    name = "import-libvirt"
    version = "1.0"
    menu_label = N_("Import from libvirt")
    menu_section = SECTION_IMPORT
    icon_name = "network-server-symbolic"

    def activate(self) -> None:
        """Ouvre l'onglet d'import libvirt."""
        logger.debug("ImportLibvirtPlugin.activate: entrée")
        self.context.open_tab(_("Import from libvirt"), OngletImportLibvirt(self.context))
        logger.debug("ImportLibvirtPlugin.activate: fait")


PLUGIN = ImportLibvirtPlugin
