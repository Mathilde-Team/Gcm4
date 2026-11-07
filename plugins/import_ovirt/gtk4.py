# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Import from oVirt » (contrat v1, #439, portage #179).

Portage GTK4 de ``plugins/plugin_import_ovirt.py`` (``BatchPlugin``
GTK3, supprimé) : plugin outil sur l'onglet générique des imports
d'hyperviseurs. La phase de configuration saisit l'URL du moteur, un
identifiant API et l'utilisateur SSH de repli ; la question de route
manuelle pour une VM injoignable (GTK3 : ``inputbox`` bloquant) passe
par :func:`gcm4.ui.dialogues.creer` avec une saisie, toujours asynchrone
côté boucle GTK. Logique API REST dans :mod:`core`, interface commune
dans ``gcm4_hv.ui``.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Adw, GLib, Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import N_, _  # noqa: E402
from gcm4.core.plugins import SECTION_IMPORT, ToolPlugin  # noqa: E402
from gcm4.ui import dialogues  # noqa: E402

from . import core  # noqa: E402
from .core import partager_ui

ui = partager_ui()

#: Corps du dialogue de route manuelle (msgid repris de la version GTK3).
_CORPS_ROUTE = _(
    "Could not reach '{vm}' directly, via the oVirt engine, or via its hypervisor.\n\n"
    'Enter either a direct target — "ip" or "ip:port" — or a jump '
    'proxy — "-J user@jumphost[:port] target_ip".\n'
    "Leave empty to skip this VM."
)


class OngletImportOvirt(ui.OngletImportHyperviseurs):
    """Onglet d'import oVirt : moteur, protocoles, scan, import."""

    def __init__(self, context: object) -> None:
        """Construit l'onglet (aucune valeur par défaut : tout est saisi)."""
        logger.debug("OngletImportOvirt.__init__: entrée")
        super().__init__(context, "oVirt")
        logger.debug("OngletImportOvirt.__init__: sortie")

    def _construire_parametres(self) -> Gtk.Widget:
        """Page de configuration : moteur, identifiants, protocoles, scan."""
        logger.debug("_construire_parametres: entrée")
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        grille = Gtk.Grid(column_spacing=6, row_spacing=6)
        self.saisie_url = Gtk.Entry(hexpand=True)
        self.saisie_url.set_placeholder_text("https://engine.example.com/ovirt-engine/api")
        self.saisie_utilisateur = Gtk.Entry(hexpand=True)
        self.saisie_utilisateur.set_text("admin@internal")
        self.saisie_mot_de_passe = Gtk.PasswordEntry(hexpand=True, show_peek_icon=True)
        self.saisie_utilisateur_ssh = Gtk.Entry(hexpand=True)
        self.saisie_utilisateur_ssh.set_text("root")
        self.case_tls = Gtk.CheckButton(label=_("Verify TLS certificate"))
        self.case_tls.set_active(True)
        for ligne, (libelle, saisie) in enumerate(
            (
                (_("Engine URL:"), self.saisie_url),
                (_("Username:"), self.saisie_utilisateur),
                (_("Password:"), self.saisie_mot_de_passe),
                (_("SSH user (engine/hypervisor jump):"), self.saisie_utilisateur_ssh),
            )
        ):
            grille.attach(Gtk.Label(label=libelle, xalign=0), 0, ligne, 1, 1)
            grille.attach(saisie, 1, ligne, 1, 1)
        grille.attach(self.case_tls, 1, 4, 1, 1)
        boite.append(grille)

        boite.append(
            Gtk.Label(label=_("<b>Connection types to import:</b>"), xalign=0, use_markup=True)
        )
        self.cases_protocole: dict[str, Gtk.CheckButton] = {}
        for cle, libelle, actif in (
            ("ssh", _("SSH  (direct — oVirt VMs are usually directly routable)"), True),
            (
                "rdp",
                _("RDP  (Windows guests only — port 3389 probed via the resolved route)"),
                True,
            ),
        ):
            case = Gtk.CheckButton(label=libelle)
            case.set_active(actif)
            self.cases_protocole[cle] = case
            boite.append(case)

        self.bouton_scan = Gtk.Button(label=_("🔍  Scan oVirt engine"))
        self.bouton_scan.add_css_class("suggested-action")
        self.bouton_scan.set_halign(Gtk.Align.END)
        self.bouton_scan.connect("clicked", self._on_scan)
        boite.append(self.bouton_scan)

        defilement = Gtk.ScrolledWindow(vexpand=True)
        defilement.set_child(boite)
        logger.debug("_construire_parametres: sortie")
        return defilement

    def _demander_route(self, nom_vm: str) -> str | None:
        """Demande une route de secours pour une VM injoignable.

        Bloque le thread de scan (comme la version GTK3) le temps de la
        réponse ; le dialogue est affiché depuis la boucle GTK via
        ``GLib.idle_add``. Renvoie la saisie, ou None si ignorée.
        """
        logger.debug("_demander_route: entrée | {}", nom_vm)
        resultat: dict[str, str] = {}
        fini = threading.Event()

        def afficher() -> bool:
            logger.debug("_demander_route: affichage du dialogue")
            try:
                entree = Gtk.Entry(hexpand=True)

                def repondu(reponse: str) -> None:
                    logger.debug("_demander_route: réponse {!r}", reponse)
                    if reponse == "valider":
                        resultat["valeur"] = entree.get_text()
                    fini.set()
                    logger.debug("repondu: sortie")

                dialogues.creer(
                    self.get_root(),
                    _("Unreachable VM: {vm}").format(vm=nom_vm),
                    _CORPS_ROUTE.format(vm=nom_vm),
                    [
                        ("valider", _("Add"), Adw.ResponseAppearance.SUGGESTED),
                        ("ignorer", _("Cancel"), None),
                    ],
                    "valider",
                    "ignorer",
                    repondu,
                    extra=entree,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("import oVirt : dialogue de route impossible : {}", exc)
                fini.set()
            logger.debug("afficher: sortie")
            return False

        GLib.idle_add(afficher)
        fini.wait(timeout=600)
        valeur = (resultat.get("valeur") or "").strip()
        logger.debug("_demander_route: sortie | {!r}", valeur)
        return valeur or None

    def _on_scan(self, _bouton: Gtk.Button) -> None:
        """Lance le scan du moteur avec les protocoles choisis."""
        logger.debug("_on_scan: entrée")
        url = self.saisie_url.get_text().strip()
        if not url:
            self.log(_("Engine URL is required."))
            logger.debug("_on_scan: pas d'URL")
            return
        protocoles = {cle for cle, case in self.cases_protocole.items() if case.get_active()}
        if not protocoles:
            self.log(_("No connection type selected."))
            logger.debug("_on_scan: aucun protocole")
            return
        self.bouton_scan.set_sensitive(False)
        self._stack.set_visible_child_name("monitor")
        self.lancer_scan(
            lambda: core.collecter_hotes(
                url,
                self.saisie_utilisateur.get_text().strip() or "admin@internal",
                self.saisie_mot_de_passe.get_text(),
                self.case_tls.get_active(),
                self.saisie_utilisateur_ssh.get_text().strip() or "root",
                self.log,
                self.progression,
                protocoles,
                self._demander_route,
            )
        )
        logger.debug("_on_scan: scan lancé")


class ImportOvirtPlugin(ToolPlugin):
    """« Import from oVirt » : onglet de scan d'un moteur oVirt/RHV."""

    name = "import-ovirt"
    version = "1.0"
    menu_label = N_("Import from oVirt")
    menu_section = SECTION_IMPORT
    icon_name = "network-server-symbolic"

    def activate(self) -> None:
        """Ouvre l'onglet d'import oVirt."""
        logger.debug("ImportOvirtPlugin.activate: entrée")
        self.context.open_tab(_("Import from oVirt"), OngletImportOvirt(self.context))
        logger.debug("ImportOvirtPlugin.activate: fait")


PLUGIN = ImportOvirtPlugin
