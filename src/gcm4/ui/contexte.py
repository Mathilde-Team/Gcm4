# SPDX-License-Identifier: GPL-3.0-or-later
"""Contexte injecté dans chaque plugin (contrat v1, ``docs/plugins.md``, #151).

Le plugin ne voit ni l'application ni la fenêtre : seulement les membres
du contrat (log, config, ask_password, notify, open_tab, import_hosts,
question, chemin_configuration, recharger). La fenêtre est cherchée à
chaque appel (le registre est construit au démarrage, avant elle).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from gi.repository import Adw, Gtk
from loguru import logger

from gcm4.core.config import ConfigError
from gcm4.core.exchange import Conflit, ExchangeError, Rapport, importer
from gcm4.core.i18n import _
from gcm4.ui import dialogues

if TYPE_CHECKING:
    from gcm4.core.model import Host

#: Section de ``gcm4.conf`` propre au plugin ``nom``.
SECTION = "plugin {nom}"


class ContexteApplication:
    """``PluginContext`` d'un plugin, relié à l'application gcm4."""

    def __init__(self, application: Gtk.Application, nom: str) -> None:
        """Contexte du plugin ``nom`` ; ``log`` est lié à ce nom."""
        logger.debug("ContexteApplication.__init__: {!r}", nom)
        self._application = application
        self.nom = nom
        self.log = logger.bind(plugin=nom)
        logger.debug("ContexteApplication.__init__: prêt")

    def _fenetre(self) -> Any:
        """Fenêtre principale (active, sinon la première), ou ``None``."""
        logger.trace("_fenetre: entrée")
        fenetres = [self._application.get_active_window(), *self._application.get_windows()]
        resultat = next(
            (f for f in fenetres if f is not None and hasattr(f, "ajouter_onglet")), None
        )
        logger.trace("_fenetre: {}", resultat is not None)
        return resultat

    def config(self) -> Mapping[str, str]:
        """Section ``[plugin <nom>]`` de ``gcm4.conf``, en lecture seule (vide si absente)."""
        logger.debug("config: {!r}", self.nom)
        fenetre = self._fenetre()
        brut = getattr(getattr(fenetre, "config", None), "brut", None)
        section = SECTION.format(nom=self.nom)
        valeurs = (
            dict(brut.items(section)) if brut is not None and brut.has_section(section) else {}
        )
        logger.debug("config: {} clé(s)", len(valeurs))
        return MappingProxyType(valeurs)

    def ask_password(self, host: Host, done: Callable[[str | None], None]) -> None:
        """Demande un mot de passe ; ``done(texte)``, ou ``done(None)`` si annulé. Jamais journalisé."""
        logger.debug("ask_password: {!r}", host.path)
        fenetre = self._fenetre()
        if fenetre is None:
            done(None)
            logger.debug("ask_password: aucune fenêtre, annulé")
            return
        entree = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
        cible = f"{host.user}@{host.host}" if host.user else host.host

        def repondre(reponse: str) -> None:
            logger.debug("ask_password.repondre: {}", reponse)
            done(entree.get_text() if reponse == "ok" else None)
            logger.debug("ask_password.repondre: fait")

        self.dialogue = dialogues.creer(
            fenetre,
            _("Password"),
            _("Password for {target}").format(target=cible),
            [
                ("cancel", _("Cancel"), None),
                ("ok", _("Connect"), Adw.ResponseAppearance.SUGGESTED),
            ],
            "ok",
            "cancel",
            repondre,
            extra=entree,
        )
        entree.grab_focus()
        logger.debug("ask_password: dialogue affiché")

    def notify(self, message: str) -> None:
        """Toast dans la fenêtre principale."""
        logger.debug("notify: {!r}", self.nom)
        fenetre = self._fenetre()
        if fenetre is not None:
            fenetre.notifier(message)
        logger.debug("notify: fait")

    def open_tab(self, title: str, widget: Gtk.Widget) -> None:
        """Onglet supplémentaire (sans session : fermé sans appel au plugin)."""
        logger.debug("open_tab: {!r}", title)
        fenetre = self._fenetre()
        if fenetre is not None:
            fenetre.ajouter_onglet(title, widget)
        logger.debug("open_tab: fait")

    def import_hosts(self, hotes: Sequence[Host], conflit: Conflit = Conflit.IGNORER) -> Rapport:
        """Importe ``hotes`` : magasin complété, ``gcm4.conf`` enregistré, arbre rechargé (#436).

        Le magasin reprend son état d'avant l'import si l'enregistrement
        échoue ; l'erreur est relancée en :class:`ExchangeError` (message
        traduit) pour que l'outil la notifie. Sans fenêtre ni configuration
        ouverte, il n'y a rien à importer : même erreur.

        Args:
            hotes: Hôtes à importer (copiés, jamais partagés).
            conflit: Politique en cas de nom déjà pris.

        Returns:
            Rapport: Bilan de :func:`gcm4.core.exchange.importer`.
        """
        logger.debug("import_hosts: {} hôte(s), conflit={}", len(hotes), conflit.value)
        fenetre = self._fenetre()
        config = getattr(fenetre, "config", None)
        if fenetre is None or config is None:
            logger.debug("import_hosts: aucune configuration ouverte")
            raise ExchangeError(_("No configuration is open: nothing to import into."))
        avant = (list(config.store.hosts), dict(config.store.groups))
        rapport = importer(config.store, hotes, conflit)
        try:
            fenetre.enregistrer_config()
        except ConfigError as exc:
            config.store.hosts, config.store.groups = avant
            logger.debug("import_hosts: non enregistré, import annulé ({})", exc)
            raise ExchangeError(str(exc)) from exc
        if fenetre.arbre is not None:
            fenetre.arbre.recharger()
        logger.debug("import_hosts: {} ajouté(s)", len(rapport.ajoutes))
        return rapport

    def question(
        self,
        titre: str,
        corps: str,
        reponses: Sequence[tuple[str, str]],
        defaut: str,
        fermeture: str,
        rappel: Callable[[str], None],
    ) -> None:
        """Question modale au-dessus de la fenêtre ; ``rappel(identifiant)`` (#97).

        Sans fenêtre, le rappel reçoit ``fermeture`` immédiatement : la
        question est annulée, jamais perdue.
        """
        logger.debug("question: {!r}", titre)
        fenetre = self._fenetre()
        if fenetre is None:
            rappel(fermeture)
            logger.debug("question: aucune fenêtre, annulé")
            return
        self.dialogue = dialogues.creer(
            fenetre,
            titre,
            corps,
            [(identifiant, libelle, None) for identifiant, libelle in reponses],
            defaut,
            fermeture,
            rappel,
        )
        logger.debug("question: dialogue affiché")

    def chemin_configuration(self) -> str | None:
        """Chemin du ``gcm4.conf`` ouvert, ou ``None`` si rien n'est chargé (#97)."""
        logger.debug("chemin_configuration: {!r}", self.nom)
        fenetre = self._fenetre()
        if fenetre is None or getattr(fenetre, "config", None) is None:
            logger.debug("chemin_configuration: aucune configuration")
            return None
        chemin = str(fenetre.config_file)
        logger.debug("chemin_configuration: {}", chemin)
        return chemin

    def recharger(self) -> None:
        """Relit ``gcm4.conf`` depuis le disque et reconstruit l'arbre (#97)."""
        logger.debug("recharger: {!r}", self.nom)
        fenetre = self._fenetre()
        if fenetre is not None:
            fenetre.recharger_configuration()
        logger.debug("recharger: fait")
