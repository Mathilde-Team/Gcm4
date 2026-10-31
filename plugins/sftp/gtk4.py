# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin SFTP de gcm4 (contrat v1, ``docs/plugins.md``) : navigateur de fichiers distant (#75).

L'onglet liste un dossier du serveur et permet d'y naviguer, de télécharger
un fichier (dans le dossier des téléchargements, ou ``download-dir`` de la
section ``[plugin sftp]``), d'envoyer des fichiers (bouton ou glisser-déposer
depuis le gestionnaire de fichiers), de créer un dossier, de renommer et de
supprimer (fichier ou dossier vide, après confirmation).

Le réseau (paramiko, bloquant) passe par **un** fil de travail : les
opérations s'exécutent dans l'ordre, la boucle GTK n'attend jamais. Les
questions (clé d'hôte inconnue, suppression, nom d'un dossier) s'affichent
dans une barre en haut de l'onglet, pas dans une fenêtre modale.

La logique sans GTK (connexion, clés d'hôte, listes, transferts) est dans
:mod:`core`. Chargé en mode source comme ``gcm4_plugin_sftp.gtk4`` (#288).
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402
from gcm4.core.model import Host  # noqa: E402
from gcm4.core.plugins import ConnectionPlugin  # noqa: E402
from gcm4.core.validation import FieldError  # noqa: E402

from . import core  # noqa: E402

__all__ = ["PLUGIN", "SessionSftp", "SftpPlugin"]

#: Progression : au plus une mise à jour de la barre par intervalle (secondes).
INTERVALLE_PROGRESSION_S = 0.1


class SessionSftp:
    """Onglet SFTP : barre d'outils, barre de question, liste du dossier, état."""

    def __init__(
        self,
        host: Host,
        context: Any,
        connexion: core.ConnexionSftp,
        fichier_cle: str,
        dossier_initial: str,
        telechargements: Path,
    ) -> None:
        """Construit l'onglet et lance la connexion (asynchrone, rien ne bloque).

        Args:
            host: hôte ouvert (mot de passe éventuel).
            context: contexte du plugin (``ask_password``, ``notify``, ``log``).
            connexion: connexion pas encore ouverte.
            fichier_cle: clé privée (option ``identity-file``), vide : agent et ``~/.ssh``.
            dossier_initial: dossier ouvert après connexion, vide : dossier d'accueil.
            telechargements: dossier local des téléchargements.
        """
        logger.debug("SessionSftp.__init__: {!r}", host.path)
        self.host = host
        self.context = context
        self.connexion = connexion
        self.fichier_cle = fichier_cle
        self.dossier_initial = dossier_initial
        self.telechargements = telechargements
        self.dossier = ""
        self.entrees: list[core.Entree] = []
        self.ferme = False
        self.occupe = False
        self.connecte = False
        self._mot_de_passe = host.password or None
        self._reponse: Callable[[str], None] | None = None
        self._derniere_progression = 0.0
        self._travaux: queue.Queue = queue.Queue()
        self._construire()
        self._fil = threading.Thread(target=self._boucle, name="gcm4-sftp", daemon=True)
        self._fil.start()
        self._connecter()
        logger.debug("SessionSftp.__init__: fait")

    # ── Interface ────────────────────────────────────────────────────────

    def _bouton(self, icone: str, infobulle: str, action: Callable[[], None]) -> Gtk.Button:
        """Bouton d'icône de la barre d'outils."""
        logger.debug("SessionSftp._bouton: {!r}", icone)
        bouton = Gtk.Button(icon_name=icone, tooltip_text=infobulle)
        bouton.connect("clicked", lambda _b: action())
        self.outils.append(bouton)
        logger.debug("SessionSftp._bouton: fait")
        return bouton

    def _construire(self) -> None:
        """Widgets de l'onglet ; ``self.widget`` est la boîte racine."""
        logger.debug("SessionSftp._construire: entrée")
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for cote in ("top", "bottom", "start", "end"):
            getattr(self.widget, f"set_margin_{cote}")(6)

        self.outils = Gtk.Box(spacing=6)
        self.bouton_parent = self._bouton("go-up-symbolic", _("Parent folder"), self.monter)
        self.bouton_actualiser = self._bouton(
            "view-refresh-symbolic", _("Refresh"), self.actualiser
        )
        self.chemin = Gtk.Entry(hexpand=True, placeholder_text=_("Remote folder"))
        self.chemin.connect("activate", lambda e: self.aller(e.get_text().strip()))
        self.outils.append(self.chemin)
        self.bouton_dossier = self._bouton(
            "folder-new-symbolic", _("New folder"), self.demander_dossier
        )
        self.bouton_envoyer = self._bouton(
            "document-send-symbolic", _("Upload files"), self.choisir_envois
        )
        self.bouton_telecharger = self._bouton(
            "document-save-symbolic", _("Download"), self.telecharger_selection
        )
        self.bouton_renommer = self._bouton(
            "document-edit-symbolic", _("Rename"), self.demander_renommage
        )
        self.bouton_supprimer = self._bouton(
            "user-trash-symbolic", _("Delete"), self.demander_suppression
        )
        self.widget.append(self.outils)

        self.barre = Gtk.Box(spacing=6, visible=False)
        self.question = Gtk.Label(xalign=0, wrap=True, hexpand=True, selectable=True)
        self.saisie = Gtk.Entry(visible=False, hexpand=True)
        self.saisie.connect("activate", lambda _e: self._repondre_oui())
        self.bouton_oui = Gtk.Button()
        self.bouton_oui.connect("clicked", lambda _b: self._repondre_oui())
        self.bouton_non = Gtk.Button(label=_("Cancel"))
        self.bouton_non.connect("clicked", lambda _b: self._fermer_question())
        for enfant in (self.question, self.saisie, self.bouton_oui, self.bouton_non):
            self.barre.append(enfant)
        self.widget.append(self.barre)

        self.liste = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.liste.connect("row-activated", lambda _l, ligne: self.activer(ligne.entree))
        self.liste.connect("selected-rows-changed", lambda _l: self._sensibilites())
        defilement = Gtk.ScrolledWindow(child=self.liste, vexpand=True)
        self.widget.append(defilement)
        depot = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        depot.connect("drop", self._sur_depot)
        self.liste.add_controller(depot)

        bas = Gtk.Box(spacing=6)
        self.etat = Gtk.Label(
            xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, selectable=True
        )
        self.progres = Gtk.ProgressBar(visible=False, valign=Gtk.Align.CENTER)
        bas.append(self.etat)
        bas.append(self.progres)
        self.widget.append(bas)
        self._sensibilites()
        logger.debug("SessionSftp._construire: fait")

    def _sensibilites(self) -> None:
        """Boutons actifs selon la connexion, l'activité et la sélection."""
        logger.debug("SessionSftp._sensibilites: entrée")
        libre = self.connecte and not self.occupe and not self.ferme
        choisie = self.selection() is not None
        for bouton in (self.bouton_parent, self.bouton_actualiser, self.bouton_dossier):
            bouton.set_sensitive(libre)
        self.bouton_envoyer.set_sensitive(libre)
        self.chemin.set_sensitive(libre)
        entree = self.selection()
        self.bouton_telecharger.set_sensitive(libre and choisie and not entree.dossier)
        self.bouton_renommer.set_sensitive(libre and choisie)
        self.bouton_supprimer.set_sensitive(libre and choisie)
        logger.debug("SessionSftp._sensibilites: fait (libre={})", libre)

    def _etat(self, texte: str) -> None:
        """Ligne d'état en bas de l'onglet."""
        logger.debug("SessionSftp._etat: {!r}", texte)
        self.etat.set_text(texte)
        logger.debug("SessionSftp._etat: fait")

    def _poser_question(
        self,
        texte: str,
        oui: str,
        reponse: Callable[[str], None],
        saisie: str | None = None,
        destructive: bool = False,
    ) -> None:
        """Barre de question : ``reponse(texte saisi)`` si l'utilisateur confirme.

        Args:
            texte: question affichée.
            oui: libellé du bouton de confirmation.
            reponse: appelée avec le texte de la saisie (vide sans saisie).
            saisie: texte initial du champ ; ``None`` : pas de champ.
            destructive: bouton de confirmation en rouge.
        """
        logger.debug("SessionSftp._poser_question: {!r}", oui)
        self.question.set_text(texte)
        self.bouton_oui.set_label(oui)
        if destructive:
            self.bouton_oui.add_css_class("destructive-action")
        else:
            self.bouton_oui.remove_css_class("destructive-action")
        self.saisie.set_visible(saisie is not None)
        self.saisie.set_text(saisie or "")
        self._reponse = reponse
        self.barre.set_visible(True)
        if saisie is not None:
            self.saisie.grab_focus()
        logger.debug("SessionSftp._poser_question: fait")

    def _fermer_question(self) -> None:
        """Masque la barre de question sans répondre."""
        logger.debug("SessionSftp._fermer_question: entrée")
        self._reponse = None
        self.barre.set_visible(False)
        logger.debug("SessionSftp._fermer_question: fait")

    def _repondre_oui(self) -> None:
        """Confirmation : ferme la barre puis appelle la réponse avec la saisie."""
        logger.debug("SessionSftp._repondre_oui: entrée")
        reponse = self._reponse
        texte = self.saisie.get_text().strip()
        self._fermer_question()
        if reponse is not None:
            reponse(texte)
        logger.debug("SessionSftp._repondre_oui: fait")

    def _remplir(self) -> None:
        """Une ligne par entrée : icône, nom, taille, date, droits."""
        logger.debug("SessionSftp._remplir: {} entrées", len(self.entrees))
        self._vider()
        for entree in self.entrees:
            boite = Gtk.Box(spacing=12)
            icone = "folder-symbolic" if entree.dossier else "text-x-generic-symbolic"
            boite.append(Gtk.Image(icon_name=icone))
            nom = entree.nom + (" →" if entree.lien else "")
            boite.append(
                Gtk.Label(label=nom, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            )
            taille = "" if entree.dossier else core.taille_lisible(entree.taille)
            boite.append(Gtk.Label(label=taille, xalign=1, width_chars=10))
            date = time.strftime("%Y-%m-%d %H:%M", time.localtime(entree.modifie))
            boite.append(Gtk.Label(label=date, width_chars=16))
            boite.append(Gtk.Label(label=entree.droits, width_chars=10))
            ligne = Gtk.ListBoxRow(child=boite)
            ligne.entree = entree
            self.liste.append(ligne)
        logger.debug("SessionSftp._remplir: fait")

    def _vider(self) -> None:
        """Retire toutes les lignes (``remove_all`` n'existe qu'à partir de GTK 4.12)."""
        logger.debug("SessionSftp._vider: entrée")
        ligne = self.liste.get_first_child()
        while ligne is not None:
            suivante = ligne.get_next_sibling()
            self.liste.remove(ligne)
            ligne = suivante
        logger.debug("SessionSftp._vider: fait")

    def selection(self) -> core.Entree | None:
        """Entrée de la ligne sélectionnée, ou ``None``."""
        logger.debug("SessionSftp.selection: entrée")
        ligne = self.liste.get_selected_row()
        entree = getattr(ligne, "entree", None)
        logger.debug("SessionSftp.selection: {!r}", entree.nom if entree else None)
        return entree

    def selectionner(self, nom: str) -> bool:
        """Sélectionne la ligne ``nom`` ; faux si elle n'existe pas."""
        logger.debug("SessionSftp.selectionner: {!r}", nom)
        for index, entree in enumerate(self.entrees):
            if entree.nom == nom:
                self.liste.select_row(self.liste.get_row_at_index(index))
                logger.debug("SessionSftp.selectionner: ligne {}", index)
                return True
        logger.debug("SessionSftp.selectionner: absente")
        return False

    # ── Fil de travail ───────────────────────────────────────────────────

    def _soumettre(
        self,
        description: str,
        travail: Callable[[], Any],
        reussite: Callable[[Any], None],
        echec: Callable[[Exception], None] | None = None,
    ) -> None:
        """Ajoute ``travail`` à la file ; ``reussite``/``echec`` appelés dans la boucle GTK."""
        logger.debug("SessionSftp._soumettre: {!r}", description)
        self.occupe = True
        self._sensibilites()
        self._etat(description)
        self._travaux.put((travail, reussite, echec or self._echec))
        logger.debug("SessionSftp._soumettre: fait")

    def _boucle(self) -> None:
        """Fil de travail : exécute la file dans l'ordre, ferme la connexion à la fin."""
        logger.debug("SessionSftp._boucle: entrée")
        while True:
            element = self._travaux.get()
            if element is None:
                break
            travail, reussite, echec = element
            try:
                resultat = travail()
            except Exception as exc:  # noqa: BLE001 (rendu à l'onglet, rien ne se perd)
                logger.debug("SessionSftp._boucle: échec {!r}", exc)
                GLib.idle_add(self._terminer, echec, exc)
            else:
                GLib.idle_add(self._terminer, reussite, resultat)
        self.connexion.fermer()
        logger.debug("SessionSftp._boucle: fait")

    def _terminer(self, rappel: Callable[[Any], None], valeur: Any) -> bool:
        """Dans la boucle GTK : fin d'un travail (rien si l'onglet est fermé)."""
        logger.debug("SessionSftp._terminer: entrée")
        if self.ferme:
            logger.debug("SessionSftp._terminer: onglet fermé")
            return GLib.SOURCE_REMOVE
        self.occupe = False
        self.progres.set_visible(False)
        rappel(valeur)
        self._sensibilites()
        logger.debug("SessionSftp._terminer: fait")
        return GLib.SOURCE_REMOVE

    def _echec(self, exc: Exception) -> None:
        """Échec d'une opération : message dans la ligne d'état."""
        logger.debug("SessionSftp._echec: {!r}", exc)
        self.context.log.warning("SFTP {} : {}", self.host.path, exc)
        self._etat(_("Error: {error}").format(error=exc))
        logger.debug("SessionSftp._echec: fait")

    def _progression(self, fait: int, total: int) -> None:
        """Appelée par paramiko depuis le fil de travail : barre mise à jour au plus tous les 0,1 s."""
        logger.trace("SessionSftp._progression: {}/{}", fait, total)
        maintenant = time.monotonic()
        if fait < total and maintenant - self._derniere_progression < INTERVALLE_PROGRESSION_S:
            logger.trace("SessionSftp._progression: trop tôt")
            return
        self._derniere_progression = maintenant
        GLib.idle_add(self._afficher_progression, fait / total if total else 1.0)
        logger.debug("SessionSftp._progression: fait")

    def _afficher_progression(self, fraction: float) -> bool:
        """Dans la boucle GTK : barre de progression."""
        logger.debug("SessionSftp._afficher_progression: {:.2f}", fraction)
        if self.occupe and not self.ferme:
            self.progres.set_visible(True)
            self.progres.set_fraction(fraction)
        logger.debug("SessionSftp._afficher_progression: fait")
        return GLib.SOURCE_REMOVE

    # ── Connexion ────────────────────────────────────────────────────────

    def _connecter(self) -> None:
        """Ouvre la connexion avec le mot de passe courant (ou l'agent et les clés)."""
        logger.debug("SessionSftp._connecter: {!r}", self.host.path)
        mot_de_passe, fichier_cle = self._mot_de_passe, self.fichier_cle
        self._soumettre(
            _("Connecting to {host}…").format(host=self.connexion.nom),
            lambda: self.connexion.ouvrir(mot_de_passe, fichier_cle),
            self._connecte,
            self._echec_connexion,
        )
        logger.debug("SessionSftp._connecter: fait")

    def _connecte(self, accueil: str) -> None:
        """Connexion ouverte : dossier initial (ou d'accueil)."""
        logger.debug("SessionSftp._connecte: {!r}", accueil)
        self.connecte = True
        self.aller(self.dossier_initial or accueil)
        logger.debug("SessionSftp._connecte: fait")

    def _echec_connexion(self, exc: Exception) -> None:
        """Clé à accepter, mot de passe à demander, ou erreur affichée avec « Réessayer »."""
        logger.debug("SessionSftp._echec_connexion: {}", type(exc).__name__)
        if isinstance(exc, core.CleHoteInconnue):
            self._etat(_("Unknown host key"))
            self._poser_question(
                _(
                    "The authenticity of host {host} can't be established.\n"
                    "Key fingerprint is {fingerprint}. Trust it and add it to {file}?"
                ).format(host=exc.nom, fingerprint=exc.empreinte, file=self.connexion.known_hosts),
                _("Trust and connect"),
                lambda _texte: self._accepter_cle(exc),
            )
        elif isinstance(exc, core.CleHoteChangee):
            self.context.log.warning("SFTP {} : clé d'hôte changée", self.host.path)
            message = _(
                "Host key of {host} has changed (fingerprint {fingerprint}). Connection "
                "refused: check the server, then remove the old key from {file}."
            ).format(host=exc.nom, fingerprint=exc.empreinte, file=self.connexion.known_hosts)
            self._etat(message)
            self.context.notify(message)  # échec de connexion (#80), onglet en arrière-plan
        elif isinstance(exc, core.AuthentificationRefusee):
            self._etat(_("Authentication failed"))
            self.context.notify(_("Authentication failed"))  # échec de connexion (#80)
            self.context.ask_password(self.host, self._mot_de_passe_saisi)
        else:
            self._echec(exc)
            self.context.notify(_("Connection failed: {error}").format(error=exc))  # #80
            self._poser_question(
                _("Connection failed: {error}").format(error=exc),
                _("Retry"),
                lambda _texte: self._connecter(),
            )
        logger.debug("SessionSftp._echec_connexion: fait")

    def _accepter_cle(self, exc: core.CleHoteInconnue) -> None:
        """Clé acceptée : ajoutée à ``known_hosts``, puis nouvelle connexion."""
        logger.debug("SessionSftp._accepter_cle: {!r}", exc.nom)
        try:
            core.ajouter_cle_connue(self.connexion.known_hosts, exc.nom, exc.cle)
        except OSError as erreur:
            logger.debug("SessionSftp._accepter_cle: écriture impossible {!r}", erreur)
            self._echec(erreur)
            logger.debug("SessionSftp._accepter_cle: abandon")
            return
        self.context.log.info("SFTP {} : clé {} ajoutée", exc.nom, exc.empreinte)
        self._connecter()
        logger.debug("SessionSftp._accepter_cle: fait")

    def _mot_de_passe_saisi(self, texte: str | None) -> None:
        """Réponse de ``ask_password`` : nouvel essai, ou abandon."""
        logger.debug("SessionSftp._mot_de_passe_saisi: saisi={}", texte is not None)
        if self.ferme:
            logger.debug("SessionSftp._mot_de_passe_saisi: onglet fermé")
            return
        if texte is None:
            self._etat(_("Authentication cancelled"))
            self._poser_question(
                _("Authentication cancelled."), _("Retry"), lambda _t: self._connecter()
            )
            logger.debug("SessionSftp._mot_de_passe_saisi: annulé")
            return
        self._mot_de_passe = texte
        self._connecter()
        logger.debug("SessionSftp._mot_de_passe_saisi: fait")

    # ── Navigation ───────────────────────────────────────────────────────

    def aller(self, chemin: str) -> None:
        """Ouvre le dossier ``chemin`` (relatif au dossier d'accueil, ou absolu)."""
        logger.debug("SessionSftp.aller: {!r}", chemin)

        def lister() -> tuple[str, list[core.Entree]]:
            """Dans le fil : chemin absolu et contenu."""
            logger.debug("SessionSftp.aller.lister: entrée")
            absolu = self.connexion.normaliser(chemin)
            entrees = self.connexion.lister(absolu)
            logger.debug("SessionSftp.aller.lister: fait")
            return absolu, entrees

        self._soumettre(_("Listing {path}…").format(path=chemin), lister, self._liste)
        logger.debug("SessionSftp.aller: fait")

    def _liste(self, resultat: tuple[str, list[core.Entree]]) -> None:
        """Dossier lu : chemin, lignes et ligne d'état."""
        logger.debug("SessionSftp._liste: {!r}", resultat[0])
        self.dossier, self.entrees = resultat
        self.chemin.set_text(self.dossier)
        self._remplir()
        self._etat(_("{path}: {count} items").format(path=self.dossier, count=len(self.entrees)))
        logger.debug("SessionSftp._liste: fait")

    def monter(self) -> None:
        """Dossier parent."""
        logger.debug("SessionSftp.monter: {!r}", self.dossier)
        self.aller(core.parent(self.dossier))
        logger.debug("SessionSftp.monter: fait")

    def actualiser(self) -> None:
        """Relit le dossier courant."""
        logger.debug("SessionSftp.actualiser: {!r}", self.dossier)
        self.aller(self.dossier)
        logger.debug("SessionSftp.actualiser: fait")

    def activer(self, entree: core.Entree) -> None:
        """Double-clic ou Entrée : ouvre un dossier, télécharge un fichier."""
        logger.debug("SessionSftp.activer: {!r}", entree.nom)
        if self.occupe or not self.connecte:
            logger.debug("SessionSftp.activer: occupé")
            return
        if entree.dossier:
            self.aller(entree.chemin)
        else:
            self.telecharger(entree)
        logger.debug("SessionSftp.activer: fait")

    # ── Transferts ───────────────────────────────────────────────────────

    def telecharger_selection(self) -> None:
        """Télécharge le fichier sélectionné."""
        logger.debug("SessionSftp.telecharger_selection: entrée")
        entree = self.selection()
        if entree is not None and not entree.dossier:
            self.telecharger(entree)
        logger.debug("SessionSftp.telecharger_selection: fait")

    def telecharger(self, entree: core.Entree) -> None:
        """Copie ``entree`` dans le dossier des téléchargements, sans écraser."""
        logger.debug("SessionSftp.telecharger: {!r}", entree.chemin)

        def copier() -> Path:
            """Dans le fil : destination libre puis copie."""
            logger.debug("SessionSftp.telecharger.copier: entrée")
            self.telechargements.mkdir(parents=True, exist_ok=True)
            destination = core.destination_libre(self.telechargements, entree.nom)
            resultat = self.connexion.telecharger(entree.chemin, destination, self._progression)
            logger.debug("SessionSftp.telecharger.copier: fait")
            return resultat

        self._soumettre(_("Downloading {name}…").format(name=entree.nom), copier, self._telecharge)
        logger.debug("SessionSftp.telecharger: fait")

    def _telecharge(self, destination: Path) -> None:
        """Téléchargement terminé : état et notification."""
        logger.debug("SessionSftp._telecharge: {}", destination)
        message = _("Downloaded to {path}").format(path=destination)
        self._etat(message)
        self.context.notify(message)
        logger.debug("SessionSftp._telecharge: fait")

    def choisir_envois(self) -> None:
        """Sélecteur de fichiers locaux à envoyer dans le dossier courant."""
        logger.debug("SessionSftp.choisir_envois: entrée")
        fenetre = self.widget.get_root()
        classe = getattr(Gtk, "FileDialog", None)
        if classe is not None:
            dialogue = classe(title=_("Upload files"), modal=True)
            dialogue.open_multiple(fenetre, None, self._fichiers_choisis)
        else:  # GTK < 4.10 (Debian 12, Rocky 9)
            dialogue = Gtk.FileChooserNative.new(
                _("Upload files"), fenetre, Gtk.FileChooserAction.OPEN, None, None
            )
            dialogue.set_select_multiple(True)
            dialogue.connect("response", self._reponse_selecteur)
            dialogue.show()
        self._dialogue = dialogue
        logger.debug("SessionSftp.choisir_envois: fait")

    def _fichiers_choisis(self, dialogue: Any, resultat: Gio.AsyncResult) -> None:
        """Fin de ``Gtk.FileDialog.open_multiple`` (annulation comprise)."""
        logger.debug("SessionSftp._fichiers_choisis: entrée")
        try:
            fichiers = dialogue.open_multiple_finish(resultat)
        except GLib.Error as exc:
            logger.debug("SessionSftp._fichiers_choisis: annulé ({})", exc.message)
            return
        self.envoyer([Path(f.get_path()) for f in fichiers if f.get_path()])
        logger.debug("SessionSftp._fichiers_choisis: fait")

    def _reponse_selecteur(self, dialogue: Any, reponse: int) -> None:
        """Fin de ``Gtk.FileChooserNative`` (GTK < 4.10)."""
        logger.debug("SessionSftp._reponse_selecteur: {}", reponse)
        if reponse == Gtk.ResponseType.ACCEPT:
            fichiers = dialogue.get_files()
            liste = [fichiers.get_item(i) for i in range(fichiers.get_n_items())]
            self.envoyer([Path(f.get_path()) for f in liste if f.get_path()])
        dialogue.destroy()
        logger.debug("SessionSftp._reponse_selecteur: fait")

    def _sur_depot(self, _cible: Gtk.DropTarget, valeur: Any, _x: float, _y: float) -> bool:
        """Fichiers déposés depuis le gestionnaire de fichiers : envoyés au dossier courant."""
        logger.debug("SessionSftp._sur_depot: entrée")
        if not self.connecte or self.occupe:
            self._etat(_("Busy, try again when the current operation is finished"))
            logger.debug("SessionSftp._sur_depot: refusé, occupé")
            return False
        chemins = [Path(f.get_path()) for f in valeur.get_files() if f.get_path()]
        self.envoyer(chemins)
        logger.debug("SessionSftp._sur_depot: fait")
        return bool(chemins)

    def envoyer(self, chemins: list[Path]) -> None:
        """Envoie des fichiers locaux dans le dossier courant ; les dossiers sont ignorés."""
        logger.debug("SessionSftp.envoyer: {} chemin(s)", len(chemins))
        fichiers = [c for c in chemins if c.is_file()]
        ignores = [c.name for c in chemins if not c.is_file()]
        if not fichiers:
            self._etat(_("Nothing to upload (folders are not uploaded)"))
            logger.debug("SessionSftp.envoyer: rien à envoyer")
            return
        dossier = self.dossier

        def copier() -> list[str]:
            """Dans le fil : un fichier après l'autre."""
            logger.debug("SessionSftp.envoyer.copier: entrée")
            envoyes = [self.connexion.envoyer(c, dossier, self._progression) for c in fichiers]
            logger.debug("SessionSftp.envoyer.copier: fait")
            return envoyes

        def termine(envoyes: list[str]) -> None:
            """Liste relue ; les dossiers ignorés sont signalés."""
            logger.debug("SessionSftp.envoyer.termine: {}", len(envoyes))
            self.aller(dossier)
            if ignores:
                self.context.notify(
                    _("Folders not uploaded: {names}").format(names=", ".join(ignores))
                )
            logger.debug("SessionSftp.envoyer.termine: fait")

        self._soumettre(
            _("Uploading {count} file(s)…").format(count=len(fichiers)), copier, termine
        )
        logger.debug("SessionSftp.envoyer: fait")

    # ── Dossiers, renommage, suppression ─────────────────────────────────

    def _nom_refuse(self, nom: str) -> bool:
        """Vrai (et message) si ``nom`` n'est pas utilisable."""
        logger.debug("SessionSftp._nom_refuse: {!r}", nom)
        refuse = not core.nom_valide(nom)
        if refuse:
            self._etat(_("Invalid name: {name}").format(name=nom))
        logger.debug("SessionSftp._nom_refuse: {}", refuse)
        return refuse

    def demander_dossier(self) -> None:
        """Barre de question : nom du dossier à créer."""
        logger.debug("SessionSftp.demander_dossier: entrée")
        self._poser_question(_("New folder name:"), _("Create"), self.creer_dossier, saisie="")
        logger.debug("SessionSftp.demander_dossier: fait")

    def creer_dossier(self, nom: str) -> None:
        """Crée ``nom`` dans le dossier courant puis relit la liste."""
        logger.debug("SessionSftp.creer_dossier: {!r}", nom)
        if self._nom_refuse(nom):
            logger.debug("SessionSftp.creer_dossier: nom refusé")
            return
        dossier = self.dossier
        self._soumettre(
            _("Creating {name}…").format(name=nom),
            lambda: self.connexion.creer_dossier(dossier, nom),
            lambda _chemin: self.aller(dossier),
        )
        logger.debug("SessionSftp.creer_dossier: fait")

    def demander_renommage(self) -> None:
        """Barre de question : nouveau nom de l'entrée sélectionnée."""
        logger.debug("SessionSftp.demander_renommage: entrée")
        entree = self.selection()
        if entree is not None:
            self._poser_question(
                _("Rename {name} to:").format(name=entree.nom),
                _("Rename"),
                lambda nom: self.renommer(entree, nom),
                saisie=entree.nom,
            )
        logger.debug("SessionSftp.demander_renommage: fait")

    def renommer(self, entree: core.Entree, nom: str) -> None:
        """Renomme ``entree`` puis relit la liste."""
        logger.debug("SessionSftp.renommer: {!r} -> {!r}", entree.nom, nom)
        if self._nom_refuse(nom) or nom == entree.nom:
            logger.debug("SessionSftp.renommer: rien à faire")
            return
        dossier = self.dossier
        self._soumettre(
            _("Renaming {name}…").format(name=entree.nom),
            lambda: self.connexion.renommer(entree, nom),
            lambda _chemin: self.aller(dossier),
        )
        logger.debug("SessionSftp.renommer: fait")

    def demander_suppression(self) -> None:
        """Barre de question : confirmation avant suppression."""
        logger.debug("SessionSftp.demander_suppression: entrée")
        entree = self.selection()
        if entree is not None:
            texte = (
                _("Delete the empty folder {name}?")
                if entree.dossier and not entree.lien
                else _("Delete {name}?")
            )
            self._poser_question(
                texte.format(name=entree.nom),
                _("Delete"),
                lambda _t: self.supprimer(entree),
                destructive=True,
            )
        logger.debug("SessionSftp.demander_suppression: fait")

    def supprimer(self, entree: core.Entree) -> None:
        """Supprime ``entree`` puis relit la liste."""
        logger.debug("SessionSftp.supprimer: {!r}", entree.chemin)
        dossier = self.dossier
        self._soumettre(
            _("Deleting {name}…").format(name=entree.nom),
            lambda: self.connexion.supprimer(entree),
            lambda _r: self.aller(dossier),
        )
        logger.debug("SessionSftp.supprimer: fait")

    # ── Fermeture ────────────────────────────────────────────────────────

    def close(self) -> None:
        """Onglet fermé : la file s'arrête après le travail en cours, puis la connexion se ferme."""
        logger.debug("SessionSftp.close: {!r}", self.host.path)
        if self.ferme:
            logger.debug("SessionSftp.close: déjà fermée")
            return
        self.ferme = True
        self._travaux.put(None)
        logger.debug("SessionSftp.close: fait")


class SftpPlugin(ConnectionPlugin):
    """Protocole ``sftp`` : navigateur de fichiers distant (paramiko)."""

    name = "sftp"
    version = "1.0"
    protocols = ("sftp",)

    def default_port(self, protocol: str) -> int | None:
        """Port 22."""
        logger.debug("SftpPlugin.default_port: {!r}", protocol)
        logger.debug("SftpPlugin.default_port: {}", core.PORT_DEFAUT)
        return core.PORT_DEFAUT

    def default_options(self, protocol: str) -> dict[str, str]:
        """Dossier d'accueil, agent SSH et clés par défaut."""
        logger.debug("SftpPlugin.default_options: {!r}", protocol)
        options = {core.OPT_DOSSIER: "", core.OPT_CLE: ""}
        logger.debug("SftpPlugin.default_options: fait")
        return options

    def validate_options(self, protocol: str, options: Mapping[str, str]) -> list[FieldError]:
        """Le fichier de clé, s'il est donné, doit exister."""
        logger.debug("SftpPlugin.validate_options: {!r}", protocol)
        erreurs = []
        cle = options.get(core.OPT_CLE, "").strip()
        if cle and not Path(cle).expanduser().is_file():
            erreurs.append(FieldError(champ=core.OPT_CLE, message=_("File not found")))
        logger.debug("SftpPlugin.validate_options: {} erreur(s)", len(erreurs))
        return erreurs

    def build_host_options_widget(
        self, host: Host, options: dict[str, str], changed: Callable[[], None]
    ) -> Gtk.Widget:
        """Dossier distant initial et fichier de clé privée."""
        logger.debug("SftpPlugin.build_host_options_widget: {!r}", host.path)
        grille = Gtk.Grid(column_spacing=12, row_spacing=6)
        champs = (
            (core.OPT_DOSSIER, _("Remote folder:"), _("Home folder")),
            (core.OPT_CLE, _("Private key file:"), _("SSH agent and ~/.ssh keys")),
        )
        for ligne, (cle, titre, indication) in enumerate(champs):
            grille.attach(Gtk.Label(label=titre, xalign=0), 0, ligne, 1, 1)
            saisie = Gtk.Entry(
                hexpand=True, text=options.get(cle, ""), placeholder_text=indication
            )

            def modifie(entree: Gtk.Entry, cle: str = cle) -> None:
                """Saisie modifiée : option mise à jour."""
                logger.debug("SftpPlugin.build_host_options_widget.modifie: {!r}", cle)
                options[cle] = entree.get_text().strip()
                changed()
                logger.debug("SftpPlugin.build_host_options_widget.modifie: fait")

            saisie.connect("changed", modifie)
            grille.attach(saisie, 1, ligne, 1, 1)
        logger.debug("SftpPlugin.build_host_options_widget: fait")
        return grille

    def _telechargements(self) -> Path:
        """``download-dir`` de ``[plugin sftp]``, sinon le dossier XDG des téléchargements."""
        logger.debug("SftpPlugin._telechargements: entrée")
        configure = self.context.config().get(core.CONF_TELECHARGEMENTS, "").strip()
        xdg = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        dossier = Path(configure or xdg or GLib.get_home_dir()).expanduser()
        logger.debug("SftpPlugin._telechargements: {}", dossier)
        return dossier

    def open(self, host: Host) -> SessionSftp:
        """Onglet de navigation ; la connexion se fait en arrière-plan.

        Raises:
            ValueError: hôte sans adresse.
        """
        logger.debug("SftpPlugin.open: {!r}", host.path)
        if not host.host:
            logger.debug("SftpPlugin.open: sans adresse")
            raise ValueError(_("No address"))
        options = host.protocol_options.get("sftp", {})
        connexion = core.ConnexionSftp(host.host, host.port, host.user, core.known_hosts_defaut())
        cle = options.get(core.OPT_CLE, "").strip()
        session = SessionSftp(
            host,
            self.context,
            connexion,
            str(Path(cle).expanduser()) if cle else "",
            options.get(core.OPT_DOSSIER, "").strip(),
            self._telechargements(),
        )
        logger.debug("SftpPlugin.open: session ouverte")
        return session


PLUGIN = SftpPlugin
