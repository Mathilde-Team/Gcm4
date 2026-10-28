# SPDX-License-Identifier: GPL-3.0-or-later
"""Vérification des mises à jour au démarrage, côté interface (#245).

- décision : :func:`gcm4.core.mises_a_jour.decision` (option, accord, variable
  ``GCM_NO_UPDATE_CHECK``) ;
- accord jamais demandé : une question, une seule fois ; « Autoriser » enregistre
  l'accord et vérifie, « Non » désactive l'option ;
- vérification dans un fil d'exécution (jamais bloquante), résultat remis à
  la boucle GTK par ``GLib.idle_add`` ; version plus récente : toast avec un
  bouton qui ouvre la page de la version. Hors ligne : rien à l'écran, une
  ligne de journal.

Compatibilité : ``Gtk.UriLauncher`` (GTK ≥ 4.10), sinon
``Gio.AppInfo.launch_default_for_uri``.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping

from gi.repository import Adw, Gio, GLib, Gtk
from loguru import logger

import gcm4
from gcm4.core import mises_a_jour as maj
from gcm4.core import preferences as prefs_core
from gcm4.core.config import ConfigError, update_keys
from gcm4.core.i18n import _
from gcm4.ui import dialogues


def enregistrer_accord(config_file: str, accord: bool) -> None:
    """Écrit ``[window] check-updates-consent`` ; un échec est journalisé."""
    logger.debug("enregistrer_accord: {}", accord)
    try:
        update_keys(config_file, "window", {maj.CLE_ACCORD: str(accord)})
    except ConfigError as exc:
        logger.warning("enregistrer_accord: non enregistré ({})", exc)
    logger.debug("enregistrer_accord: fait")


def au_demarrage(
    fenetre: Gtk.Window,
    environ: Mapping[str, str] = os.environ,
    verification: Callable[[str], maj.MiseAJour | None] = maj.verifier,
) -> str:
    """Vérifie, demande l'accord ou ne fait rien ; renvoie la décision prise."""
    logger.debug("au_demarrage: entrée")
    config = getattr(fenetre, "config", None)
    if config is None:  # configuration illisible : ni question ni écriture
        logger.debug("au_demarrage: aucune configuration")
        return maj.NON
    choix = maj.decision(
        config.options.check_updates, maj.lire_accord(fenetre.config_file), environ
    )
    if choix == maj.VERIFIER:
        lancer_verification(fenetre, verification)
    elif choix == maj.DEMANDER:
        demander_accord(fenetre, verification)
    logger.debug("au_demarrage: {}", choix)
    return choix


def demander_accord(
    fenetre: Gtk.Window, verification: Callable[[str], maj.MiseAJour | None] = maj.verifier
) -> Adw.Dialog | Adw.MessageDialog:
    """Question unique « Vérifier les mises à jour ? » ; la réponse est enregistrée."""
    logger.debug("demander_accord: entrée")

    def rappel(reponse: str) -> None:
        logger.debug("demander_accord.rappel: {}", reponse)
        accord = reponse == "autoriser"
        enregistrer_accord(fenetre.config_file, accord)
        if accord:
            lancer_verification(fenetre, verification)
        else:  # « Non » : l'option est désactivée, visible dans les préférences
            fenetre.config.options.check_updates = False
            try:
                prefs_core.save_preference(fenetre.config_file, "check_updates", False)
            except ConfigError as exc:
                logger.warning("demander_accord: option non enregistrée ({})", exc)
        logger.debug("demander_accord.rappel: fait")

    dialogue = dialogues.creer(
        fenetre,
        _("Check for updates?"),
        _(
            "At startup, Gnome Connection Manager can ask GitHub (api.github.com) "
            "whether a newer version has been published. "
            "You can change this later in the preferences."
        ),
        [
            ("non", _("No"), None),
            ("autoriser", _("Allow"), Adw.ResponseAppearance.SUGGESTED),
        ],
        "autoriser",
        "non",
        rappel,
    )
    logger.debug("demander_accord: affichée")
    return dialogue


def lancer_verification(
    fenetre: Gtk.Window, verification: Callable[[str], maj.MiseAJour | None] = maj.verifier
) -> threading.Thread:
    """Vérifie dans un fil d'exécution ; le résultat revient par ``GLib.idle_add``."""
    logger.debug("lancer_verification: entrée")

    def travail() -> None:
        logger.debug("lancer_verification.travail: entrée")
        try:
            resultat = verification(gcm4.__version__)
        except Exception:  # jamais d'exception hors du fil
            logger.exception("lancer_verification: échec inattendu")
            resultat = None
        GLib.idle_add(afficher, fenetre, resultat)
        logger.debug("lancer_verification.travail: fait")

    fil = threading.Thread(target=travail, name="gcm4-mises-a-jour", daemon=True)
    fil.start()
    logger.debug("lancer_verification: lancée")
    return fil


def afficher(fenetre: Gtk.Window, resultat: maj.MiseAJour | None) -> bool:
    """Toast « version disponible » avec un bouton « Ouvrir » ; rien sinon."""
    logger.debug("afficher: {}", resultat)
    toasts = getattr(fenetre, "toasts", None)
    if resultat is None or toasts is None:
        logger.debug("afficher: rien à afficher")
        return GLib.SOURCE_REMOVE
    toast = Adw.Toast(
        title=_("Version {version} is available").format(version=resultat.version), timeout=0
    )
    toast.set_button_label(_("Open"))
    toast.connect("button-clicked", lambda _t: ouvrir_page(fenetre, resultat.url))
    toasts.add_toast(toast)
    fenetre.toast_mise_a_jour = toast  # tests
    logger.debug("afficher: toast affiché")
    return GLib.SOURCE_REMOVE


def ouvrir_page(fenetre: Gtk.Window, url: str) -> None:
    """Ouvre ``url`` dans le navigateur ; un échec est journalisé."""
    logger.debug("ouvrir_page: {}", url)
    try:
        if hasattr(Gtk, "UriLauncher"):
            Gtk.UriLauncher.new(url).launch(fenetre, None, None)
        else:  # GTK < 4.10
            Gio.AppInfo.launch_default_for_uri(url, None)
    except GLib.Error as exc:
        logger.warning("ouvrir_page: {}", exc.message)
    logger.debug("ouvrir_page: demandé")
