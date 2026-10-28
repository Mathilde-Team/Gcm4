# SPDX-License-Identifier: GPL-3.0-or-later
"""Contexte injecté dans chaque plugin (contrat v1, ``docs/plugins.md``, #151).

Le plugin ne voit ni l'application ni la fenêtre : seulement ces cinq
membres. La fenêtre est cherchée à chaque appel (le registre est construit
au démarrage, avant elle).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from gi.repository import Adw, Gtk
from loguru import logger

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
