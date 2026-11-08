# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin spice de gcm4 (contrat v1, ``docs/plugins.md``) : session dans ``remote-viewer``.

Portage de ``plugins/plugin_spice.py`` (GTK3, ``SpiceClientGtk.Display``) par
la voie choisie le 08/11/2026 (#179) : spice-gtk n'a pas de version GTK4, la
session s'ouvre donc dans la visionneuse externe du paquet ``virt-viewer``
(``remote-viewer`` pour les modes URI et Proxmox, ``virt-viewer`` pour
libvirt). L'onglet de gcm4 suit la visionneuse : état, reconnexion, arrêt ;
fermer l'onglet ferme la visionneuse.

Tout passe par ``Gio.Subprocess`` en asynchrone (lancement, attente de la
fin, demande du ticket Proxmox par ssh) : rien ne bloque la boucle GTK.
La logique sans GTK (options, validation, commandes, fichier ``.vv``) est
dans :mod:`core`.
"""

from __future__ import annotations

import signal
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Gio, GLib, Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402
from gcm4.core.model import Host  # noqa: E402
from gcm4.core.plugins import ConnectionPlugin  # noqa: E402
from gcm4.core.validation import FieldError  # noqa: E402

from . import core  # noqa: E402

__all__ = ["PLUGIN", "SessionSpice", "SpicePlugin", "zone_options"]


class SessionSpice:
    """Onglet SPICE : barre (titre, état, reconnecter, arrêter) et visionneuse externe."""

    def __init__(self, host: Host, context: Any, options: Mapping[str, str], binaire: str) -> None:
        """Construit l'onglet et lance la visionneuse.

        Args:
            host: hôte ouvert (mot de passe jamais journalisé).
            context: contexte du plugin (``log``, ``notify``).
            options: options ``spice`` validées (:func:`core.valider`).
            binaire: chemin de la visionneuse (:func:`core.trouver_visionneuse`).
        """
        logger.debug("SessionSpice.__init__: {!r}", host.path)
        self.host = host
        self.context = context
        self.options = dict(options)
        self.mode = core.mode(options)
        self.binaire = binaire
        self.processus: Gio.Subprocess | None = None
        self.annulation = Gio.Cancellable()
        self.fichier_vv: Path | None = None
        self.ferme = False
        self._construire()
        self.connecter()
        logger.debug("SessionSpice.__init__: fait")

    def _construire(self) -> None:
        """Widgets de l'onglet ; ``self.widget`` est la boîte racine."""
        logger.debug("SessionSpice._construire: entrée")
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outils = Gtk.Box(spacing=6)
        for cote in ("top", "start", "end"):
            getattr(outils, f"set_margin_{cote}")(6)
        self.titre = Gtk.Label(xalign=0, hexpand=True, selectable=True)
        self.titre.set_markup(
            "<b>{}</b> <small>{}</small>".format(
                GLib.markup_escape_text(_("SPICE")),
                GLib.markup_escape_text(core.titre(self.host)),
            )
        )
        outils.append(self.titre)
        self.etat = Gtk.Label(label=_("Waiting…"), css_classes=["dim-label"])
        outils.append(self.etat)
        self.bouton_connecter = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text=_("Reconnect")
        )
        self.bouton_connecter.connect("clicked", lambda _b: self.connecter())
        outils.append(self.bouton_connecter)
        self.bouton_arreter = Gtk.Button(
            icon_name="process-stop-symbolic", tooltip_text=_("Disconnect")
        )
        self.bouton_arreter.connect("clicked", lambda _b: self.arreter())
        outils.append(self.bouton_arreter)
        self.widget.append(outils)
        self.widget.append(Gtk.Separator())
        self.explication = Gtk.Label(
            label=_("The SPICE session opens in a separate remote-viewer window."),
            justify=Gtk.Justification.CENTER,
            wrap=True,
            vexpand=True,
            css_classes=["dim-label"],
        )
        self.widget.append(self.explication)
        self._boutons(en_cours=False)
        logger.debug("SessionSpice._construire: fait")

    def _statut(self, texte: str) -> None:
        """Texte de l'état, à droite du titre."""
        logger.debug("SessionSpice._statut: {!r}", texte)
        self.etat.set_text(texte)
        logger.debug("SessionSpice._statut: fait")

    def _boutons(self, en_cours: bool) -> None:
        """Reconnecter sans visionneuse active, arrêter pendant la session."""
        logger.debug("SessionSpice._boutons: {}", en_cours)
        self.bouton_connecter.set_sensitive(not en_cours)
        self.bouton_arreter.set_sensitive(en_cours)
        logger.debug("SessionSpice._boutons: fait")

    @property
    def en_cours(self) -> bool:
        """Vrai si une visionneuse ou une demande de ticket est en cours."""
        logger.trace("SessionSpice.en_cours: entrée")
        resultat = self.processus is not None
        logger.trace("SessionSpice.en_cours: {}", resultat)
        return resultat

    def _echec(self, message: str) -> None:
        """Échec affiché dans l'onglet, journalisé et notifié ; reconnexion possible."""
        logger.debug("SessionSpice._echec: entrée")
        self.context.log.warning("spice {} : {}", self.host.path, message)
        self._statut(message)
        self.context.notify(message)
        self._effacer_vv()
        self._boutons(en_cours=False)
        logger.debug("SessionSpice._echec: fait")

    def connecter(self) -> None:
        """Lance la session : ticket Proxmox d'abord, sinon la visionneuse directement."""
        logger.debug("SessionSpice.connecter: {!r}", self.mode)
        if self.ferme or self.en_cours:
            logger.debug("SessionSpice.connecter: ignoré")
            return
        self.annulation = Gio.Cancellable()
        try:
            if self.mode == core.MODE_PROXMOX:
                self._demander_ticket()
            elif self.mode == core.MODE_LIBVIRT:
                self._lancer(core.commande_libvirt(self.binaire, self.host, self.options))
            else:
                self.fichier_vv = core.ecrire_vv(core.champs_vv_direct(self.host, self.options))
                self._lancer(
                    core.commande_vv(self.binaire, self.fichier_vv, self.host, self.options)
                )
        except (OSError, ValueError) as exc:
            logger.debug("SessionSpice.connecter: lancement impossible ({})", exc)
            self._echec(_("Error launching viewer") + f" : {exc}")
        logger.debug("SessionSpice.connecter: fait")

    def _demander_ticket(self) -> None:
        """Demande le ticket SPICE à ``pvesh`` par ssh, sans bloquer la boucle GTK."""
        logger.debug("SessionSpice._demander_ticket: entrée")
        argv = core.commande_ticket(self.host, self.options)
        try:
            self.processus = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            )
        except GLib.Error as exc:
            self._echec(_("SSH error: {exc}").format(exc=exc.message))
            logger.debug("SessionSpice._demander_ticket: ssh introuvable")
            return
        self._statut(_("Requesting the SPICE ticket…"))
        self._boutons(en_cours=True)
        self.processus.communicate_utf8_async(None, self.annulation, self._ticket_recu)
        logger.debug("SessionSpice._demander_ticket: demande lancée")

    def _ticket_recu(self, processus: Gio.Subprocess, resultat: Gio.AsyncResult) -> None:
        """Réponse de ``pvesh`` : ``.vv`` écrit puis visionneuse lancée, ou erreur affichée."""
        logger.debug("SessionSpice._ticket_recu: entrée")
        self.processus = None
        if self.ferme:
            logger.debug("SessionSpice._ticket_recu: onglet fermé")
            return
        try:
            _ok, sortie, erreurs = processus.communicate_utf8_finish(resultat)
        except GLib.Error as exc:
            self._echec(_("SSH error: {exc}").format(exc=exc.message))
            logger.debug("SessionSpice._ticket_recu: échec de lecture")
            return
        if processus.get_exit_status() != 0:
            detail = (erreurs or "").strip().splitlines()
            raison = detail[-1] if detail else str(processus.get_exit_status())
            self._echec(_("SSH error: {exc}").format(exc=raison))
            logger.debug("SessionSpice._ticket_recu: pvesh en échec")
            return
        try:
            champs = core.lire_ticket(sortie or "")
            champs.setdefault("title", core.titre(self.host))
            self.fichier_vv = core.ecrire_vv(champs)
            argv = core.commande_vv(
                self.binaire, self.fichier_vv, self.host, self.options, ca=False
            )
        except (OSError, ValueError) as exc:
            self._echec(str(exc))
            logger.debug("SessionSpice._ticket_recu: ticket refusé")
            return
        self._lancer(argv)
        logger.debug("SessionSpice._ticket_recu: fait")

    def _lancer(self, argv: list[str]) -> None:
        """Lance la visionneuse et attend sa fin en asynchrone."""
        logger.debug("SessionSpice._lancer: {} argument(s)", len(argv))
        try:
            self.processus = Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
        except GLib.Error as exc:
            self._echec(_("Error launching viewer") + f" : {exc.message}")
            logger.debug("SessionSpice._lancer: échec")
            return
        self.context.log.debug("spice {} : visionneuse lancée", self.host.path)
        self._statut(_("Opened in {program}").format(program=Path(argv[0]).name))
        self._boutons(en_cours=True)
        self.processus.wait_async(self.annulation, self._fin)
        logger.debug("SessionSpice._lancer: fait")

    def _fin(self, processus: Gio.Subprocess, resultat: Gio.AsyncResult) -> None:
        """Visionneuse fermée : « Session terminée » ou code de sortie."""
        logger.debug("SessionSpice._fin: entrée")
        try:
            processus.wait_finish(resultat)
        except GLib.Error as exc:
            logger.debug("SessionSpice._fin: attente interrompue ({})", exc.message)
        if processus is self.processus:
            self.processus = None
        self._effacer_vv()
        if self.ferme:
            logger.debug("SessionSpice._fin: onglet fermé")
            return
        code = processus.get_exit_status() if processus.get_if_exited() else -1
        if code == 0:
            self._statut(_("Session ended"))
        else:
            self._statut(_("Ended (code {rc})").format(rc=code))
        self._boutons(en_cours=False)
        logger.debug("SessionSpice._fin: code {}", code)

    def arreter(self) -> None:
        """Ferme la visionneuse (SIGTERM) ou abandonne la demande de ticket."""
        logger.debug("SessionSpice.arreter: entrée")
        if self.processus is not None:
            self.processus.send_signal(signal.SIGTERM)
        logger.debug("SessionSpice.arreter: fait")

    def _effacer_vv(self) -> None:
        """Supprime le ``.vv`` s'il reste (visionneuse qui ne l'a pas lu)."""
        logger.debug("SessionSpice._effacer_vv: entrée")
        if self.fichier_vv is not None:
            self.fichier_vv.unlink(missing_ok=True)
            self.fichier_vv = None
        logger.debug("SessionSpice._effacer_vv: fait")

    def close(self) -> None:
        """Onglet fermé : visionneuse arrêtée, attentes annulées, ``.vv`` effacé ; appelé une fois."""
        logger.debug("SessionSpice.close: {!r}", self.host.path)
        self.ferme = True
        self.arreter()
        self.annulation.cancel()
        self._effacer_vv()
        logger.debug("SessionSpice.close: fait")


def _saisie(
    grille: Gtk.Grid,
    ligne: int,
    libelle: str,
    options: dict[str, str],
    cle: str,
    changed: Callable[[], None],
    indication: str = "",
) -> Gtk.Entry:
    """Ligne « libellé + saisie » liée à l'option ``cle``."""
    logger.debug("_saisie: {!r}", cle)
    grille.attach(Gtk.Label(label=libelle, xalign=0), 0, ligne, 1, 1)
    saisie = Gtk.Entry(hexpand=True, text=options.get(cle, ""), placeholder_text=indication)

    def modifiee(entree: Gtk.Entry) -> None:
        """Saisie modifiée : option mise à jour."""
        logger.debug("_saisie.modifiee: {!r}", cle)
        options[cle] = entree.get_text().strip()
        changed()
        logger.debug("_saisie.modifiee: fait")

    saisie.connect("changed", modifiee)
    grille.attach(saisie, 1, ligne, 1, 1)
    logger.debug("_saisie: fait")
    return saisie


def _case(
    libelle: str,
    options: dict[str, str],
    cle: str,
    defaut: bool,
    changed: Callable[[], None],
) -> Gtk.CheckButton:
    """Case liée au booléen ``cle`` (écrit ``True``/``False`` comme GTK3)."""
    logger.debug("_case: {!r}", cle)
    case = Gtk.CheckButton(label=libelle, active=core.lire_booleen(options, cle, defaut))

    def basculee(bouton: Gtk.CheckButton) -> None:
        """Case cochée ou décochée : option mise à jour."""
        logger.debug("_case.basculee: {!r}", cle)
        options[cle] = core.ecrire_booleen(bouton.get_active())
        changed()
        logger.debug("_case.basculee: fait")

    case.connect("toggled", basculee)
    logger.debug("_case: fait")
    return case


def zone_options(options: dict[str, str], changed: Callable[[], None]) -> Gtk.Box:
    """Mode (URI, libvirt, Proxmox), champs du mode choisi, partages.

    Les grilles des modes non choisis sont masquées ; leurs valeurs restent
    dans les options (retour possible sans ressaisie, comme GTK3).
    """
    logger.debug("zone_options: entrée")
    zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    choix = Gtk.Box(spacing=12)
    grilles: dict[str, Gtk.Grid] = {}
    boutons: dict[str, Gtk.CheckButton] = {}
    actuel = core.mode(options)
    premier: Gtk.CheckButton | None = None
    for nom, libelle in (
        (core.MODE_URI, _("Direct URI")),
        (core.MODE_LIBVIRT, _("Via libvirt")),
        (core.MODE_PROXMOX, _("Via Proxmox")),
    ):
        bouton = Gtk.CheckButton(label=libelle, active=nom == actuel)
        if premier is None:
            premier = bouton
        else:
            bouton.set_group(premier)
        boutons[nom] = bouton
        choix.append(bouton)
    zone.append(choix)

    def grille() -> Gtk.Grid:
        """Grille de champs d'un mode."""
        logger.trace("zone_options.grille: entrée")
        g = Gtk.Grid(column_spacing=12, row_spacing=6)
        zone.append(g)
        logger.trace("zone_options.grille: fait")
        return g

    grilles[core.MODE_URI] = g = grille()
    _saisie(g, 0, _("TLS port"), options, core.OPT_PORT_TLS, changed, "5931")
    _saisie(g, 1, _("CA certificate"), options, core.OPT_CA, changed, "/etc/pki/ca.pem")
    grilles[core.MODE_LIBVIRT] = g = grille()
    _saisie(
        g,
        0,
        _("libvirt URI"),
        options,
        core.OPT_URI_LIBVIRT,
        changed,
        "qemu+ssh://root@kvm/system",
    )
    _saisie(g, 1, _("VM name"), options, core.OPT_VM, changed)
    grilles[core.MODE_PROXMOX] = g = grille()
    _saisie(g, 0, _("Proxmox node"), options, core.OPT_NOEUD, changed, "pve")
    _saisie(g, 1, _("VMID"), options, core.OPT_VMID, changed, "100")

    def afficher() -> None:
        """Seule la grille du mode choisi est visible."""
        logger.debug("zone_options.afficher: entrée")
        choisi = core.mode(options)
        for nom, g in grilles.items():
            g.set_visible(nom == choisi)
        logger.debug("zone_options.afficher: {!r}", choisi)

    def mode_choisi(bouton: Gtk.CheckButton, nom: str) -> None:
        """Bouton de mode activé : option ``spice_mode`` mise à jour."""
        logger.debug("zone_options.mode_choisi: {!r}", nom)
        if bouton.get_active():
            options[core.OPT_MODE] = nom
            afficher()
            changed()
        logger.debug("zone_options.mode_choisi: fait")

    for nom, bouton in boutons.items():
        bouton.connect("toggled", mode_choisi, nom)
    afficher()

    partages = Gtk.Grid(column_spacing=12, row_spacing=6)
    partages.attach(
        _case(_("Share USB devices"), options, core.OPT_USB, False, changed), 0, 0, 2, 1
    )
    partages.attach(_case(_("Share audio"), options, core.OPT_AUDIO, False, changed), 0, 1, 2, 1)
    partages.attach(
        _case(_("Share folder"), options, core.OPT_DOSSIER, False, changed), 0, 2, 2, 1
    )
    _saisie(
        partages, 3, _("Shared folder path"), options, core.OPT_CHEMIN_DOSSIER, changed, "~/Public"
    )
    zone.append(partages)
    zone.boutons_mode = boutons  # type: ignore[attr-defined]  # accès des tests
    zone.grilles_mode = grilles  # type: ignore[attr-defined]
    logger.debug("zone_options: fait")
    return zone


class SpicePlugin(ConnectionPlugin):
    """Protocole ``spice`` : visionneuse externe ``remote-viewer`` (paquet ``virt-viewer``)."""

    name = "spice"
    version = "1.0"
    protocols = ("spice",)

    def default_port(self, protocol: str) -> int | None:
        """Port 5930, comme GTK3."""
        logger.debug("SpicePlugin.default_port: {!r}", protocol)
        logger.debug("SpicePlugin.default_port: {}", core.PORT_DEFAUT)
        return core.PORT_DEFAUT

    def default_options(self, protocol: str) -> dict[str, str]:
        """Mode URI, presse-papier partagé, USB, audio et dossier non partagés (GTK3)."""
        logger.debug("SpicePlugin.default_options: {!r}", protocol)
        options = core.options_defaut()
        logger.debug("SpicePlugin.default_options: fait")
        return options

    def validate_options(self, protocol: str, options: Mapping[str, str]) -> list[FieldError]:
        """Erreurs des options du mode choisi (:func:`core.valider`)."""
        logger.debug("SpicePlugin.validate_options: {!r}", protocol)
        erreurs = core.valider(options)
        logger.debug("SpicePlugin.validate_options: {} erreur(s)", len(erreurs))
        return erreurs

    def build_host_options_widget(
        self, host: Host, options: dict[str, str], changed: Callable[[], None]
    ) -> Gtk.Widget:
        """Mode, champs du mode et partages (mêmes libellés que GTK3)."""
        logger.debug("SpicePlugin.build_host_options_widget: {!r}", host.path)
        zone = zone_options(options, changed)
        logger.debug("SpicePlugin.build_host_options_widget: fait")
        return zone

    def open(self, host: Host) -> SessionSpice:
        """Onglet SPICE et visionneuse externe.

        Raises:
            ValueError: options ou paramètres supplémentaires invalides.
            FileNotFoundError: visionneuse absente (paquet ``virt-viewer``).
        """
        logger.debug("SpicePlugin.open: {!r}", host.path)
        options = host.protocol_options.get("spice", {})
        erreurs = core.valider(options)
        if erreurs:
            logger.debug("SpicePlugin.open: options refusées")
            raise ValueError(erreurs[0].message)
        core.parametres_supplementaires(host)
        choisi = core.mode(options)
        binaire = core.trouver_visionneuse(choisi)
        if binaire is None:
            logger.debug("SpicePlugin.open: visionneuse absente")
            raise FileNotFoundError(
                _("{program} not found: install the virt-viewer package").format(
                    program=core.VISIONNEUSES[choisi]
                )
            )
        session = SessionSpice(host, self.context, options, binaire)
        logger.debug("SpicePlugin.open: session ouverte")
        return session


PLUGIN = SpicePlugin
