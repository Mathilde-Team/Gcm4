# SPDX-License-Identifier: GPL-3.0-or-later
"""Import/export des hôtes et copie de la configuration depuis le menu Fichier (#153).

Actions de la fenêtre principale :

- ``win.import-hosts`` : choix d'un fichier CSV/JSON, aperçu (nombre
  d'hôtes, déjà présents, mots de passe en clair), choix de la stratégie de
  conflit avec le bilan prévu mis à jour, puis bilan final ;
- ``win.export-hosts`` : option « inclure les mots de passe » avec un
  avertissement visible quand elle est cochée, puis choix du fichier ;
- ``win.save-config-as`` : copie de ``gcm4.conf`` et ``.gcm4.key`` dans un
  dossier choisi.

Le travail est fait par :mod:`gcm4.core.exchange` et
:mod:`gcm4.core.sauvegarde` ; ce module n'affiche que les dialogues. Les
sélecteurs passent par :attr:`Echange.choisir` (remplaçable dans les
tests, qui n'ouvrent pas de vrai sélecteur).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, Gtk
from loguru import logger

from gcm4.core import sauvegarde
from gcm4.core.config import ConfigError
from gcm4.core.exchange import Conflit, ExchangeError, exporter_fichier, importer, lire_fichier
from gcm4.core.i18n import _
from gcm4.core.model import Host
from gcm4.ui import dialogues, fichiers

if TYPE_CHECKING:
    from gcm4.ui.main_window import MainWindow

#: Nom proposé à l'export.
NOM_EXPORT = "gcm4-hosts.json"

#: Stratégies proposées à l'import, dans l'ordre affiché (la première par défaut).
STRATEGIES: tuple[Conflit, ...] = (Conflit.IGNORER, Conflit.RENOMMER, Conflit.REMPLACER)


def _libelle(conflit: Conflit) -> str:
    """Libellé traduit d'une stratégie de conflit."""
    logger.trace("_libelle: {}", conflit.value)
    libelles = {
        Conflit.IGNORER: _("Keep the existing host"),
        Conflit.RENOMMER: _("Rename the imported host"),
        Conflit.REMPLACER: _("Replace the existing host"),
    }
    logger.trace("_libelle: fait")
    return libelles[conflit]


def _avertissement(texte: str) -> Gtk.Label:
    """Libellé d'avertissement (classe CSS ``warning``), retour à la ligne."""
    logger.trace("_avertissement")
    etiquette = Gtk.Label(label=texte, wrap=True, xalign=0)
    etiquette.add_css_class("warning")
    logger.trace("_avertissement: fait")
    return etiquette


def _une_fois(rappel: Callable[[str], None]) -> Callable[[str], None]:
    """``rappel`` limité à la première réponse du dialogue.

    Fermer un dialogue déjà répondu peut émettre une seconde réponse (celle
    de fermeture) : elle est ignorée.
    """
    logger.trace("_une_fois")
    deja: list[str] = []  # première réponse reçue

    def enveloppe(reponse: str) -> None:
        logger.debug("_une_fois.enveloppe: {!r}", reponse)
        if deja:
            logger.debug("_une_fois.enveloppe: réponse ignorée")
            return
        deja.append(reponse)
        rappel(reponse)
        logger.debug("_une_fois.enveloppe: fait")

    logger.trace("_une_fois: fait")
    return enveloppe


class Echange:
    """Actions d'import/export de la fenêtre principale et leurs dialogues."""

    def __init__(self, fenetre: MainWindow) -> None:
        """Ajoute ``win.import-hosts``, ``win.export-hosts`` et ``win.save-config-as``.

        Import et export sont désactivés si la configuration n'a pas pu être
        chargée ; la copie reste possible (sauvegarde du fichier illisible).
        """
        logger.debug("Echange.__init__: entrée")
        self.fenetre = fenetre
        #: Sélecteur de fichiers (``fichiers.choisir`` ; remplacé dans les tests).
        self.choisir: Callable[..., object] = fichiers.choisir
        #: Dernier dialogue affiché (les tests y répondent).
        self.dialogue: Adw.Dialog | Adw.MessageDialog | None = None
        #: Case « inclure les mots de passe » du dialogue d'export ouvert.
        self.case_mots_de_passe: Gtk.CheckButton | None = None
        #: Avertissement « mots de passe en clair » du dialogue ouvert.
        self.alerte_mots_de_passe: Gtk.Label | None = None
        #: Boutons radio des stratégies du dialogue d'import ouvert.
        self.boutons_conflit: dict[Conflit, Gtk.CheckButton] = {}
        #: Bilan prévu du dialogue d'import ouvert.
        self.etiquette_apercu: Gtk.Label | None = None
        charge = fenetre.config is not None
        for nom, rappel, actif in (
            ("import-hosts", self._on_importer, charge),
            ("export-hosts", self._on_exporter, charge),
            ("save-config-as", self._on_enregistrer_sous, True),
        ):
            action = Gio.SimpleAction.new(nom, None)
            action.connect("activate", rappel)
            action.set_enabled(actif)
            fenetre.add_action(action)
        logger.debug("Echange.__init__: prêt (configuration chargée : {})", charge)

    @property
    def config_dir(self) -> str:
        """Dossier de ``gcm4.conf``."""
        return os.path.dirname(self.fenetre.config_file)

    # --- export ------------------------------------------------------------

    def _on_exporter(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Fichier > Exporter : options (mots de passe), puis choix du fichier."""
        logger.debug("_on_exporter: entrée")
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.case_mots_de_passe = Gtk.CheckButton(label=_("Include passwords (in clear text)"))
        self.alerte_mots_de_passe = _avertissement(
            _("Passwords will be written in clear text: keep this file safe.")
        )
        self.alerte_mots_de_passe.set_visible(False)
        self.case_mots_de_passe.connect("toggled", lambda case: self._alerte(case.get_active()))
        boite.append(self.case_mots_de_passe)
        boite.append(self.alerte_mots_de_passe)

        @_une_fois
        def rappel(reponse: str) -> None:
            logger.debug("_on_exporter.rappel: {}", reponse)
            mots_de_passe = bool(self.case_mots_de_passe and self.case_mots_de_passe.get_active())
            self.dialogue = None
            if reponse == "exporter":
                self.choisir(
                    self.fenetre,
                    fichiers.ENREGISTRER,
                    _("Export servers"),
                    lambda chemin: chemin and self.exporter_vers(chemin, mots_de_passe),
                    NOM_EXPORT,
                )
            logger.debug("_on_exporter.rappel: fait")

        self.dialogue = dialogues.creer(
            self.fenetre,
            _("Export servers"),
            _("JSON or CSV file, chosen by its extension."),
            [("annuler", _("Cancel"), None), ("exporter", _("Export"), None)],
            "exporter",
            "annuler",
            rappel,
            extra=boite,
        )
        logger.debug("_on_exporter: options affichées")

    def _alerte(self, visible: bool) -> None:
        """Montre ou cache l'avertissement « mots de passe en clair »."""
        logger.debug("_alerte: {}", visible)
        if self.alerte_mots_de_passe is not None:
            self.alerte_mots_de_passe.set_visible(visible)
        logger.debug("_alerte: fait")

    def exporter_vers(self, chemin: str, mots_de_passe: bool) -> bool:
        """Écrit tous les hôtes dans ``chemin`` (``.json`` ajouté sans extension connue).

        Args:
            chemin (str): Fichier choisi.
            mots_de_passe (bool): Inclure les mots de passe en clair.

        Returns:
            bool: ``True`` si le fichier est écrit (message affiché dans les
            deux cas).
        """
        logger.debug("exporter_vers: {} mots_de_passe={}", chemin, mots_de_passe)
        if Path(chemin).suffix.lower() not in (".json", ".csv"):
            chemin += ".json"
        assert self.fenetre.config is not None  # action désactivée sinon
        hotes = self.fenetre.config.store.hosts_in("", recursive=True)
        try:
            n = exporter_fichier(chemin, hotes, mots_de_passe=mots_de_passe)
        except ExchangeError as exc:
            self.fenetre.notifier(str(exc))
            logger.debug("exporter_vers: échec")
            return False
        self.fenetre.notifier(_("Hosts exported: {count} ({file})").format(count=n, file=chemin))
        logger.debug("exporter_vers: {} hôte(s)", n)
        return True

    # --- import ------------------------------------------------------------

    def _on_importer(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Fichier > Importer : choix du fichier, puis aperçu."""
        logger.debug("_on_importer: entrée")
        self.choisir(
            self.fenetre,
            fichiers.OUVRIR,
            _("Import servers"),
            lambda chemin: chemin and self.ouvrir_apercu(chemin),
        )
        logger.debug("_on_importer: sélecteur ouvert")

    def ouvrir_apercu(self, chemin: str) -> bool:
        """Lit ``chemin`` et affiche l'aperçu ; fichier invalide : message, rien d'importé.

        Args:
            chemin (str): Fichier CSV ou JSON choisi.

        Returns:
            bool: ``True`` si l'aperçu est affiché.
        """
        logger.debug("ouvrir_apercu: {}", chemin)
        assert self.fenetre.config is not None  # action désactivée sinon
        try:
            hotes = lire_fichier(chemin)
        except ExchangeError as exc:
            self.fenetre.notifier(str(exc))
            logger.debug("ouvrir_apercu: fichier refusé")
            return False
        store = self.fenetre.config.store
        deja = len(sauvegarde.apercu(store, hotes, Conflit.IGNORER).ignores)
        boite = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if sauvegarde.avec_mots_de_passe(hotes):
            self.alerte_mots_de_passe = _avertissement(
                _("This file contains passwords in clear text.")
            )
            boite.append(self.alerte_mots_de_passe)
        else:
            self.alerte_mots_de_passe = None
        boite.append(Gtk.Label(label=_("If a host already exists:"), xalign=0))
        self.boutons_conflit = {}
        premier: Gtk.CheckButton | None = None
        for conflit in STRATEGIES:
            bouton = Gtk.CheckButton(label=_libelle(conflit))
            if premier is None:
                premier = bouton
                bouton.set_active(True)
            else:
                bouton.set_group(premier)
            bouton.connect("toggled", lambda b: b.get_active() and self._maj_apercu(hotes, chemin))
            self.boutons_conflit[conflit] = bouton
            boite.append(bouton)
        self.etiquette_apercu = Gtk.Label(wrap=True, xalign=0)
        self.etiquette_apercu.add_css_class("dim-label")
        boite.append(self.etiquette_apercu)
        self._maj_apercu(hotes, chemin)

        @_une_fois
        def rappel(reponse: str) -> None:
            logger.debug("ouvrir_apercu.rappel: {}", reponse)
            conflit = self.conflit_choisi()
            self.dialogue = None
            if reponse == "importer":
                self.appliquer_import(chemin, hotes, conflit)
            logger.debug("ouvrir_apercu.rappel: fait")

        self.dialogue = dialogues.creer(
            self.fenetre,
            _("Import servers"),
            _("Hosts in {file}: {count}, already present: {existing}").format(
                file=os.path.basename(chemin), count=len(hotes), existing=deja
            ),
            [("annuler", _("Cancel"), None), ("importer", _("Import"), None)],
            "importer",
            "annuler",
            rappel,
            extra=boite,
        )
        logger.debug("ouvrir_apercu: {} hôte(s), {} déjà présent(s)", len(hotes), deja)
        return True

    def conflit_choisi(self) -> Conflit:
        """Stratégie cochée dans l'aperçu (``IGNORER`` par défaut)."""
        logger.trace("conflit_choisi")
        resultat = next(
            (c for c, b in self.boutons_conflit.items() if b.get_active()), Conflit.IGNORER
        )
        logger.trace("conflit_choisi: {}", resultat.value)
        return resultat

    def _maj_apercu(self, hotes: list[Host], chemin: str) -> None:
        """Bilan prévu avec la stratégie cochée."""
        logger.debug("_maj_apercu: entrée")
        assert self.fenetre.config is not None
        if self.etiquette_apercu is not None:
            rapport = sauvegarde.apercu(self.fenetre.config.store, hotes, self.conflit_choisi())
            self.etiquette_apercu.set_label(sauvegarde.bilan(os.path.basename(chemin), rapport))
        logger.debug("_maj_apercu: fait")

    def appliquer_import(self, chemin: str, hotes: list[Host], conflit: Conflit) -> bool:
        """Importe, enregistre ``gcm4.conf``, recharge l'arbre et affiche le bilan.

        Écriture impossible : le magasin reprend son état d'avant l'import.

        Args:
            chemin (str): Fichier importé (pour le bilan).
            hotes (list[Host]): Hôtes lus par :meth:`ouvrir_apercu`.
            conflit (Conflit): Stratégie choisie.

        Returns:
            bool: ``True`` si l'import est enregistré.
        """
        logger.debug("appliquer_import: {} conflit={}", chemin, conflit.value)
        config = self.fenetre.config
        assert config is not None
        avant = (list(config.store.hosts), dict(config.store.groups))
        rapport = importer(config.store, hotes, conflit)
        try:
            self.fenetre.enregistrer_config()
        except ConfigError as exc:
            config.store.hosts, config.store.groups = avant
            self.fenetre.notifier(str(exc))
            logger.debug("appliquer_import: non enregistré, import annulé")
            return False
        if self.fenetre.arbre is not None:
            self.fenetre.arbre.recharger()
        self.dialogue = dialogues.creer(
            self.fenetre,
            _("Import servers"),
            sauvegarde.bilan(os.path.basename(chemin), rapport),
            [("fermer", _("Close"), None)],
            "fermer",
            "fermer",
            _une_fois(lambda _reponse: setattr(self, "dialogue", None)),
        )
        logger.debug("appliquer_import: {} ajouté(s)", len(rapport.ajoutes))
        return True

    # --- copie de la configuration -----------------------------------------

    def _on_enregistrer_sous(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Fichier > Enregistrer la configuration sous : choix du dossier."""
        logger.debug("_on_enregistrer_sous: entrée")
        self.choisir(
            self.fenetre,
            fichiers.DOSSIER,
            _("Save configuration as…"),
            lambda dossier: dossier and self.enregistrer_sous(dossier),
        )
        logger.debug("_on_enregistrer_sous: sélecteur ouvert")

    def enregistrer_sous(self, dossier: str) -> bool:
        """Copie ``gcm4.conf`` et ``.gcm4.key`` dans ``dossier`` ; message dans tous les cas.

        Args:
            dossier (str): Dossier choisi.

        Returns:
            bool: ``True`` si la copie est faite.
        """
        logger.debug("enregistrer_sous: {}", dossier)
        try:
            sauvegarde.enregistrer_sous(self.config_dir, dossier)
        except ExchangeError as exc:
            self.fenetre.notifier(str(exc))
            logger.debug("enregistrer_sous: échec")
            return False
        self.fenetre.notifier(_("Configuration saved to {folder}").format(folder=dossier))
        logger.debug("enregistrer_sous: fait")
        return True
