# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from VirtualBox » (contrat v1, #439, portage #179).

Portage GTK4 de ``plugins/plugin_import_virtualbox.py`` (``BatchPlugin``
GTK3, supprimé) : plugin outil sur l'onglet générique des imports
d'hyperviseurs. VirtualBox n'a pas de registre système : les hôtes sont
saisis manuellement. Logique ``VBoxManage`` dans :mod:`core`, interface
commune dans ``gcm4_hv.ui``.
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


class OngletImportVirtualBox(ui.OngletImportHyperviseurs):
    """Onglet d'import VirtualBox : hôtes saisis, protocoles, scan, import."""

    def __init__(self, context: object) -> None:
        """Construit l'onglet (aucune découverte automatique des hôtes)."""
        logger.debug("OngletImportVirtualBox.__init__: entrée")
        super().__init__(context, "VirtualBox")
        logger.debug("OngletImportVirtualBox.__init__: sortie")

    def _construire_parametres(self) -> Gtk.Widget:
        """Page de configuration : utilisateur SSH, hôtes, protocoles, scan."""
        logger.debug("_construire_parametres: entrée")
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        ligne_utilisateur = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ligne_utilisateur.append(Gtk.Label(label=_("Host SSH user:")))
        utilisateur = str(self.context.config().get("default_user", "root") or "root")
        self.saisie_utilisateur = Gtk.Entry()
        self.saisie_utilisateur.set_text(utilisateur)
        self.saisie_utilisateur.set_width_chars(12)
        ligne_utilisateur.append(self.saisie_utilisateur)
        boite.append(ligne_utilisateur)

        boite.append(Gtk.Label(label=_("VirtualBox hosts:"), xalign=0))
        self.zone_cibles = ui.ZoneCibles(
            self.log,
            lambda saisie: core.normaliser_cible(saisie, self._utilisateur_defaut()),
            _("Invalid VirtualBox target."),
            titre_colonne=_("VirtualBox host (user@host[:port])"),
            libelle_saisie=_("Add a VirtualBox host:"),
            indice_saisie=_("Ex: 192.168.105.41 | root@192.168.105.41 | root@192.168.105.41:22"),
        )
        boite.append(self.zone_cibles)

        boite.append(
            Gtk.Label(label=_("<b>Connection types to import:</b>"), xalign=0, use_markup=True)
        )
        self.cases_protocole: dict[str, Gtk.CheckButton] = {}
        for cle, libelle, actif in (
            (
                "ssh",
                _(
                    "SSH  (ProxyJump -J via host when VM IP is known, "
                    "otherwise host shell + -t ssh user@vm)"
                ),
                True,
            ),
            (
                "rdp",
                _("RDP  (native VirtualBox VRDE if enabled, otherwise guest port 3389 probe)"),
                True,
            ),
            (
                "vnc",
                _("VNC  (probe port 5900 in the guest — off by default)"),
                False,
            ),
        ):
            case = Gtk.CheckButton(label=libelle)
            case.set_active(actif)
            self.cases_protocole[cle] = case
            boite.append(case)

        self.bouton_scan = Gtk.Button(label=_("🔍  Scan VirtualBox hosts"))
        self.bouton_scan.add_css_class("suggested-action")
        self.bouton_scan.set_halign(Gtk.Align.END)
        self.bouton_scan.connect("clicked", self._on_scan)
        boite.append(self.bouton_scan)

        defilement = Gtk.ScrolledWindow(vexpand=True)
        defilement.set_child(boite)
        logger.debug("_construire_parametres: sortie")
        return defilement

    def _utilisateur_defaut(self) -> str:
        """Utilisateur SSH par défaut de la saisie (champ utilisateur)."""
        return self.saisie_utilisateur.get_text().strip() or "root"

    def _on_scan(self, _bouton: Gtk.Button) -> None:
        """Lance le scan des hôtes cochés avec les protocoles choisis."""
        logger.debug("_on_scan: entrée")
        cibles = self.zone_cibles.cibles()
        if not cibles:
            self.log(_("No host selected."))
            logger.debug("_on_scan: aucun hôte")
            return
        utilisateur = self._utilisateur_defaut()
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


class ImportVirtualBoxPlugin(ToolPlugin):
    """« Import from VirtualBox » : onglet de scan des hôtes VirtualBox."""

    name = "import-virtualbox"
    version = "1.0"
    menu_label = N_("Import from VirtualBox")
    menu_section = SECTION_IMPORT
    icon_name = "network-server-symbolic"

    def activate(self) -> None:
        """Ouvre l'onglet d'import VirtualBox."""
        logger.debug("ImportVirtualBoxPlugin.activate: entrée")
        self.context.open_tab(_("Import from VirtualBox"), OngletImportVirtualBox(self.context))
        logger.debug("ImportVirtualBoxPlugin.activate: fait")


PLUGIN = ImportVirtualBoxPlugin
