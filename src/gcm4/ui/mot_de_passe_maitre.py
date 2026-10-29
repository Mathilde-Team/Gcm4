# SPDX-License-Identifier: GPL-3.0-or-later
"""Mot de passe maître de ``.gcm4.key`` : déverrouillage, recouvrement, réglages (#98).

Décisions de l'auteure (#98, 12/10/2026) :

- la protection est **optionnelle**, proposée dans les préférences, jamais
  forcée ; sans elle, rien ne change ;
- à l'activation, un **code de recouvrement** est affiché une seule fois ; il
  déverrouille les mots de passe si le mot de passe maître est oublié, puis
  impose d'en choisir un nouveau (un nouveau code est alors affiché).

La logique est dans :class:`gcm4.core.chiffrement.ChiffrementGcm` ; ce module
ne fait que l'interface. Aucun mot de passe ni code n'est journalisé.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk
from loguru import logger

from gcm4.core.chiffrement import ChiffrementError, ChiffrementGcm
from gcm4.core.i18n import _
from gcm4.ui import dialogues

#: Identifiants de réponse des dialogues.
ANNULER = "cancel"
VALIDER = "ok"


def _champ(titre: str) -> Adw.PasswordEntryRow:
    """Ligne de saisie masquée."""
    logger.trace("_champ")
    ligne = Adw.PasswordEntryRow(title=titre)
    logger.trace("_champ: fait")
    return ligne


def _boite(*lignes: Gtk.Widget) -> Gtk.ListBox:
    """Liste « boxed-list » contenant ``lignes`` (contenu additionnel d'un dialogue)."""
    logger.trace("_boite: {} ligne(s)", len(lignes))
    liste = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    liste.add_css_class("boxed-list")
    for ligne in lignes:
        liste.append(ligne)
    logger.trace("_boite: fait")
    return liste


def verifier_nouveau(nouveau: str, confirmation: str) -> str | None:
    """Message d'erreur traduit pour un nouveau mot de passe, ``None`` s'il convient."""
    logger.debug("verifier_nouveau (mots de passe masqués)")
    if not nouveau:
        logger.debug("verifier_nouveau: vide")
        return str(_("The master password must not be empty."))
    if nouveau != confirmation:
        logger.debug("verifier_nouveau: confirmation différente")
        return str(_("The two passwords do not match."))
    logger.debug("verifier_nouveau: ok")
    return None


def afficher_code(
    parent: Gtk.Widget, code: str, rappel: Callable[[], None] | None = None
) -> Adw.Dialog | Adw.MessageDialog:
    """Affiche **une seule fois** le code de recouvrement ; ``rappel()`` à la fermeture."""
    logger.debug("afficher_code (code masqué)")
    etiquette = Gtk.Label(label=code, selectable=True)
    etiquette.add_css_class("title-2")
    etiquette.add_css_class("monospace")

    def fin(_reponse: str) -> None:
        logger.debug("afficher_code.fin")
        if rappel is not None:
            rappel()
        logger.debug("afficher_code.fin: fait")

    dialogue = dialogues.creer(
        parent,
        _("Recovery code"),
        _(
            "Write down or print this code and keep it in a safe place: it will not be"
            " shown again. If you forget the master password, it unlocks the stored"
            " passwords, then you choose a new master password."
        ),
        [(VALIDER, _("I have written it down"), Adw.ResponseAppearance.SUGGESTED)],
        VALIDER,
        VALIDER,
        fin,
        extra=etiquette,
    )
    logger.debug("afficher_code: affiché")
    return dialogue


class PageVerrou(Gtk.Box):
    """Panneau gauche quand ``.gcm4.key`` est verrouillée : saisie du mot de passe maître."""

    def __init__(self, chiffrement: ChiffrementGcm, deverrouille: Callable[[], None]) -> None:
        """``deverrouille()`` est appelé une fois la clé déverrouillée."""
        logger.debug("PageVerrou.__init__")
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.chiffrement = chiffrement
        self.deverrouille = deverrouille
        #: Dernier dialogue « Mot de passe oublié » (tests).
        self.dialogue: Adw.Dialog | Adw.MessageDialog | None = None

        self.mot_de_passe = _champ(_("Master password"))
        self.mot_de_passe.connect("entry-activated", self._on_deverrouiller)
        self.message = Gtk.Label(wrap=True, visible=False)
        self.message.add_css_class("error")
        self.bouton = Gtk.Button(label=_("Unlock"), halign=Gtk.Align.CENTER)
        self.bouton.add_css_class("suggested-action")
        self.bouton.add_css_class("pill")
        self.bouton.connect("clicked", self._on_deverrouiller)
        self.oubli = Gtk.Button(label=_("Forgot password?"), halign=Gtk.Align.CENTER)
        self.oubli.add_css_class("flat")
        self.oubli.connect("clicked", self._on_oubli)
        self.oubli.set_visible(chiffrement.a_code_recouvrement())

        contenu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        contenu.append(_boite(self.mot_de_passe))
        contenu.append(self.message)
        contenu.append(self.bouton)
        contenu.append(self.oubli)
        self.page = Adw.StatusPage(
            icon_name="dialog-password-symbolic",
            title=_("Passwords locked"),
            description=_("Enter the master password to unlock the stored passwords."),
            child=contenu,
        )
        self.page.set_vexpand(True)
        self.append(self.page)
        logger.debug("PageVerrou.__init__: prête")

    def _erreur(self, message: str) -> None:
        """Affiche ``message`` sous le champ."""
        logger.debug("PageVerrou._erreur")
        self.message.set_label(message)
        self.message.set_visible(True)
        logger.debug("PageVerrou._erreur: fait")

    def _on_deverrouiller(self, *_args: object) -> None:
        """Bouton « Déverrouiller » ou Entrée dans le champ."""
        logger.debug("PageVerrou._on_deverrouiller")
        try:
            self.chiffrement.deverrouiller(self.mot_de_passe.get_text())
        except ChiffrementError as exc:
            logger.warning("PageVerrou._on_deverrouiller: refusé")
            self.mot_de_passe.set_text("")
            self._erreur(str(exc))
            logger.debug("PageVerrou._on_deverrouiller: refusé, fin")
            return
        self.mot_de_passe.set_text("")
        self.deverrouille()
        logger.debug("PageVerrou._on_deverrouiller: déverrouillée")

    def _on_oubli(self, *_args: object) -> None:
        """« Mot de passe oublié » : code de recouvrement et nouveau mot de passe."""
        logger.debug("PageVerrou._on_oubli")
        self.code = Adw.EntryRow(title=_("Recovery code"))
        self.nouveau = _champ(_("New master password"))
        self.confirmation = _champ(_("Confirm the new master password"))
        self.dialogue = dialogues.creer(
            self,
            _("Forgot password"),
            _("Enter the recovery code, then choose a new master password."),
            [
                (ANNULER, _("Cancel"), None),
                (VALIDER, _("Unlock"), Adw.ResponseAppearance.SUGGESTED),
            ],
            VALIDER,
            ANNULER,
            self._on_reponse_oubli,
            extra=_boite(self.code, self.nouveau, self.confirmation),
        )
        logger.debug("PageVerrou._on_oubli: affiché")

    def _on_reponse_oubli(self, reponse: str) -> None:
        """Réponse du dialogue « Mot de passe oublié »."""
        logger.debug("PageVerrou._on_reponse_oubli: {}", reponse)
        if reponse != VALIDER:
            logger.debug("PageVerrou._on_reponse_oubli: annulé")
            return
        erreur = verifier_nouveau(self.nouveau.get_text(), self.confirmation.get_text())
        if erreur is not None:
            self._erreur(erreur)
            logger.debug("PageVerrou._on_reponse_oubli: nouveau mot de passe refusé")
            return
        try:
            code = self.chiffrement.recuperer(self.code.get_text(), self.nouveau.get_text())
        except ChiffrementError as exc:
            logger.warning("PageVerrou._on_reponse_oubli: refusé")
            self._erreur(str(exc))
            logger.debug("PageVerrou._on_reponse_oubli: refusé, fin")
            return
        self.dialogue = afficher_code(self, code, self.deverrouille)
        logger.debug("PageVerrou._on_reponse_oubli: récupérée")


class GroupeMotDePasseMaitre(Adw.PreferencesGroup):
    """Paramètres > Sécurité : activer, changer, régénérer le code, désactiver."""

    def __init__(
        self, chiffrement: ChiffrementGcm | None, notifier: Callable[[str], None] | None = None
    ) -> None:
        """``chiffrement`` ``None`` (configuration illisible) : groupe inactif."""
        logger.debug("GroupeMotDePasseMaitre.__init__")
        super().__init__(
            title=_("Master password"),
            description=_(
                "Optional. Protects the key that encrypts the stored passwords: it will"
                " be asked at startup. A recovery code is shown once when it is enabled."
            ),
        )
        self.chiffrement = chiffrement
        self.notifier = notifier
        #: Dernier dialogue affiché (tests).
        self.dialogue: Adw.Dialog | Adw.MessageDialog | None = None
        self.etat = Adw.ActionRow(title=_("Status"))
        self.add(self.etat)
        #: Dernier échec (mot de passe faux, confirmation différente…).
        self.message = Gtk.Label(wrap=True, visible=False, xalign=0, margin_top=6)
        self.message.add_css_class("error")
        self.add(self.message)
        self.boutons: dict[str, Gtk.Button] = {}
        for nom, libelle in (
            ("activer", _("Enable…")),
            ("changer", _("Change…")),
            ("code", _("New recovery code…")),
            ("desactiver", _("Disable…")),
        ):
            bouton = Gtk.Button(label=libelle, valign=Gtk.Align.CENTER)
            bouton.connect("clicked", self._on_bouton, nom)
            self.etat.add_suffix(bouton)
            self.boutons[nom] = bouton
        self.boutons["desactiver"].add_css_class("destructive-action")
        self.rafraichir()
        logger.debug("GroupeMotDePasseMaitre.__init__: prêt")

    def rafraichir(self) -> None:
        """État affiché et boutons utilisables selon la clé."""
        logger.debug("GroupeMotDePasseMaitre.rafraichir")
        if self.chiffrement is None:
            self.etat.set_subtitle(_("Unavailable: the configuration is not loaded."))
            self.set_sensitive(False)
            logger.debug("GroupeMotDePasseMaitre.rafraichir: indisponible")
            return
        protegee = self.chiffrement.est_protegee()
        if not protegee:
            self.etat.set_subtitle(_("Disabled"))
        elif self.chiffrement.a_code_recouvrement():
            self.etat.set_subtitle(_("Enabled, with a recovery code"))
        else:
            self.etat.set_subtitle(_("Enabled, without a recovery code"))
        self.boutons["activer"].set_visible(not protegee)
        for nom in ("changer", "code", "desactiver"):
            self.boutons[nom].set_visible(protegee)
        logger.debug("GroupeMotDePasseMaitre.rafraichir: protégée={}", protegee)

    def _informer(self, message: str) -> None:
        """Toast de la fenêtre principale, s'il y en a une."""
        logger.debug("GroupeMotDePasseMaitre._informer")
        self.message.set_visible(False)
        if self.notifier is not None:
            self.notifier(message)
        logger.debug("GroupeMotDePasseMaitre._informer: fait")

    def _on_bouton(self, _bouton: Gtk.Button, nom: str) -> None:
        """Ouvre le dialogue de l'opération ``nom``."""
        logger.debug("GroupeMotDePasseMaitre._on_bouton: {}", nom)
        self.actuel = _champ(_("Current master password"))
        self.nouveau = _champ(_("New master password"))
        self.confirmation = _champ(_("Confirm the new master password"))
        if nom == "activer":
            titre, corps = (
                _("Enable the master password"),
                _(
                    "The master password will be asked at each startup to unlock the stored"
                    " passwords. A recovery code will then be shown once."
                ),
            )
            lignes: tuple[Gtk.Widget, ...] = (self.nouveau, self.confirmation)
        elif nom == "changer":
            titre, corps = _("Change the master password"), _("The recovery code stays valid.")
            lignes = (self.actuel, self.nouveau, self.confirmation)
        elif nom == "code":
            titre, corps = (
                _("New recovery code"),
                _("The current recovery code will no longer work."),
            )
            lignes = (self.actuel,)
        else:
            titre, corps = (
                _("Disable the master password"),
                _(
                    "The key that encrypts the stored passwords will be stored unprotected"
                    " again, as without a master password."
                ),
            )
            lignes = (self.actuel,)
        apparence = (
            Adw.ResponseAppearance.DESTRUCTIVE
            if nom == "desactiver"
            else Adw.ResponseAppearance.SUGGESTED
        )
        self.dialogue = dialogues.creer(
            self,
            titre,
            corps,
            [(ANNULER, _("Cancel"), None), (VALIDER, _("Confirm"), apparence)],
            VALIDER,
            ANNULER,
            lambda reponse: self._on_reponse(reponse, nom),
            extra=_boite(*lignes),
        )
        logger.debug("GroupeMotDePasseMaitre._on_bouton: affiché")

    def _on_reponse(self, reponse: str, nom: str) -> None:
        """Exécute l'opération ``nom`` si elle est confirmée."""
        logger.debug("GroupeMotDePasseMaitre._on_reponse: {} {}", nom, reponse)
        if reponse != VALIDER or self.chiffrement is None:
            logger.debug("GroupeMotDePasseMaitre._on_reponse: annulé")
            return
        if nom in ("activer", "changer"):
            erreur = verifier_nouveau(self.nouveau.get_text(), self.confirmation.get_text())
            if erreur is not None:
                self._echec(erreur)
                logger.debug("GroupeMotDePasseMaitre._on_reponse: nouveau refusé")
                return
        code: str | None = None
        try:
            if nom == "activer":
                code = self.chiffrement.activer_protection(self.nouveau.get_text())
                message = _("Master password enabled.")
            elif nom == "changer":
                self.chiffrement.changer_mot_de_passe(
                    self.actuel.get_text(), self.nouveau.get_text()
                )
                message = _("Master password changed.")
            elif nom == "code":
                code = self.chiffrement.regenerer_code(self.actuel.get_text())
                message = _("New recovery code created.")
            else:
                self.chiffrement.desactiver_protection(self.actuel.get_text())
                message = _("Master password disabled.")
        except ChiffrementError as exc:
            logger.warning("GroupeMotDePasseMaitre._on_reponse: {} refusé", nom)
            self._echec(str(exc))
            logger.debug("GroupeMotDePasseMaitre._on_reponse: refusé, fin")
            return
        self.rafraichir()
        self._informer(str(message))
        if code is not None:
            self.dialogue = afficher_code(self, code)
        logger.debug("GroupeMotDePasseMaitre._on_reponse: {} fait", nom)

    def _echec(self, message: str) -> None:
        """Signale un échec sous la ligne d'état."""
        logger.debug("GroupeMotDePasseMaitre._echec")
        self.message.set_label(message)
        self.message.set_visible(True)
        logger.debug("GroupeMotDePasseMaitre._echec: fait")
