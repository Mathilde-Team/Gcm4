# SPDX-License-Identifier: GPL-3.0-or-later
"""Application libadwaita de gcm4 : instance unique, actions, À propos (#133, #134)."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable

from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from loguru import logger

import logging_config
from gcm4 import __version__
from gcm4.core import chemins, cli, theme
from gcm4.core import preferences as prefs_core
from gcm4.core import raccourcis as raccourcis_core
from gcm4.core.config import ConfigError
from gcm4.core.i18n import _
from gcm4.core.registre import Candidat, PluginRegistry, decouvrir
from gcm4.ui import aide, boucle, mises_a_jour
from gcm4.ui import raccourcis as raccourcis_ui
from gcm4.ui.contexte import ContexteApplication
from gcm4.ui.main_window import MainWindow
from gcm4.ui.preferences import PreferencesWindow, css_police
from gcm4.ui.zone_notification import IconeNotification

APPLICATION_ID = "io.github.MathildeDec.gcm4"
SITE = "https://github.com/Mathilde-Team/Gcm4"
#: Nom affiché (titre, À propos, lanceur ``data/io.github.MathildeDec.gcm4.desktop``).
NOM_AFFICHE = "Gnome Connection Manager"
#: Règles CSS encore utiles de l'ancienne interface (audit dans le fichier).
STYLE_CSS = os.path.join(os.path.dirname(__file__), "style.css")
#: Instance unique ; un deuxième lancement transmet sa ligne de commande, son
#: dossier courant et son environnement à l'instance en cours (#275).
DRAPEAUX = Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.SEND_ENVIRONMENT
#: Codes de sortie d'un deuxième lancement refusé par l'instance en cours :
#: ligne de commande invalide, autre dossier de configuration (#275).
CODE_REFUS = 2
CODE_AUTRE_CONFIG = 5
#: Icônes du dépôt (#155), utilisées quand gcm4 tourne depuis les sources ;
#: une fois installées (#156, #157), le thème hicolor du système les fournit.
ICONES_DEPOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "icons")
DEVELOPPEURS = ["Renzo Bertuzzi L. <kuthulu@gmail.com>", "MathildeDec"]


class Gcm4Application(Adw.Application):
    """Application unique : une deuxième exécution active la fenêtre existante."""

    def __init__(
        self,
        args: argparse.Namespace,
        flags: Gio.ApplicationFlags | None = None,
        candidats: Iterable[Candidat] | None = None,
    ) -> None:
        """Garde les arguments déjà analysés par ``gcm4.core.cli``.

        Args:
            args: options de ``gcm4.core.cli`` (``config_dir``, ``debug``, ``hotes``).
            flags: drapeaux ``Gio`` ; par défaut ``HANDLES_COMMAND_LINE`` et
                ``SEND_ENVIRONMENT`` (environnement d'un deuxième lancement, #275).
            candidats: plugins à charger ; par défaut, points d'entrée puis
                dossier source (``gcm4.core.registre``). Les tests y passent
                le plugin démo.
        """
        logger.debug("Gcm4Application.__init__: config_dir={}", args.config_dir)
        # Avant l'ouverture de l'écran : classe X11 des fenêtres (WM_CLASS) = nom
        # du fichier .desktop, sinon « python3 » et le bureau ne relie pas la
        # fenêtre à son lanceur (#155). Sous Wayland, c'est l'application_id.
        GLib.set_prgname(APPLICATION_ID)
        GLib.set_application_name(NOM_AFFICHE)
        super().__init__(
            application_id=APPLICATION_ID,
            flags=DRAPEAUX if flags is None else flags,
        )
        self.args = args
        self._candidats = None if candidats is None else list(candidats)
        #: Plugins chargés au démarrage ; vide jusque-là (mode zéro plugin).
        self.registre = PluginRegistry()
        self.config_file = chemins.fichier_conf(args.config_dir)
        self.css_principal: Gtk.CssProvider | None = None
        self.css_couleurs: Gtk.CssProvider | None = None
        self.css_police: Gtk.CssProvider | None = None
        self.preferences: Gtk.Window | None = None  # fenêtre des préférences ouverte (#147)
        self.zone_notification: IconeNotification | None = None  # icône SNI (#83)
        self.fenetre_raccourcis: Gtk.Widget | None = None  # Aide > Raccourcis clavier (#86)
        self.connect("startup", self._on_startup)
        self.connect("activate", self._on_activate)
        self.connect("command-line", self._on_command_line)
        self.connect("shutdown", lambda *_a: self.appliquer_zone_notification(False))
        logger.debug("Gcm4Application.__init__: prête")

    def _on_startup(self, _app: Adw.Application) -> None:
        """Actions de l'application et raccourcis clavier."""
        logger.debug("_on_startup: entrée")
        for nom, rappel in (
            ("quit", self._on_quit),
            ("about", self._on_about),
            ("help", self._on_help),
            ("shortcuts", self._on_shortcuts),
            ("preferences", self._on_preferences),
        ):
            action = Gio.SimpleAction.new(nom, None)
            action.connect("activate", rappel)
            self.add_action(action)
        self.appliquer_raccourcis()
        mode = theme.load_theme(self.config_file)
        action_theme = Gio.SimpleAction.new_stateful(
            "theme", GLib.VariantType.new("s"), GLib.Variant("s", mode)
        )
        action_theme.connect("change-state", self._on_theme)
        self.add_action(action_theme)
        self.appliquer_theme(mode)
        self._charger_css()
        # Aucun plugin ne peut empêcher le démarrage : ses erreurs restent dans le registre.
        self.registre = decouvrir(lambda nom: ContexteApplication(self, nom), self._candidats)
        logger.debug("_on_startup: fin")

    def raccourcis_effectifs(self) -> dict[str, tuple[str, ...]]:
        """Accélérateurs de la fenêtre lus dans ``[shortcuts]`` (#247) ; défaut si absent."""
        logger.debug("raccourcis_effectifs: entrée")
        resultat = raccourcis_core.effectifs(
            raccourcis_core.lire(self.config_file), raccourcis_ui.resoudre
        )
        logger.debug("raccourcis_effectifs: {} action(s)", len(resultat))
        return resultat

    def appliquer_raccourcis(self) -> None:
        """Pose les accélérateurs effectifs : au démarrage et après chaque modification."""
        logger.debug("appliquer_raccourcis: entrée")
        for action, touches in self.raccourcis_effectifs().items():
            self.set_accels_for_action(action, list(touches))
        logger.debug("appliquer_raccourcis: fait")

    def appliquer_theme(self, mode: str) -> None:
        """Applique ``mode`` (``system`` / ``light`` / ``dark``) à chaud avec ``Adw.StyleManager``."""
        logger.debug("appliquer_theme: {}", mode)
        schema = getattr(Adw.ColorScheme, theme.SCHEMAS[mode])
        Adw.StyleManager.get_default().set_color_scheme(schema)
        logger.debug("appliquer_theme: fait")

    def _on_theme(self, action: Gio.SimpleAction, valeur: GLib.Variant) -> None:
        """Menu Affichage > thème : applique, puis enregistre dans gcm4.conf."""
        logger.debug("_on_theme: entrée")
        mode = valeur.get_string()
        logger.debug("_on_theme: {}", mode)
        if mode not in theme.MODES:
            logger.warning("_on_theme: mode inconnu {!r}", mode)
            return
        action.set_state(valeur)
        self.appliquer_theme(mode)
        try:
            theme.save_theme(self.config_file, mode)
        except ConfigError as exc:
            logger.warning("_on_theme: thème non enregistré ({})", exc)
        logger.debug("_on_theme: fait")

    def _charger_css(self) -> None:
        """Feuille de style de la base et feuille des couleurs de groupe (vide au départ)."""
        logger.debug("_charger_css: {}", STYLE_CSS)
        ecran = Gdk.Display.get_default()
        if ecran is None:
            logger.warning("_charger_css: aucun écran, CSS non chargé")
            return
        self.css_principal = Gtk.CssProvider()
        self.css_principal.load_from_path(STYLE_CSS)
        self.css_couleurs = Gtk.CssProvider()
        self.css_police = Gtk.CssProvider()
        for fournisseur in (self.css_principal, self.css_couleurs, self.css_police):
            Gtk.StyleContext.add_provider_for_display(
                ecran, fournisseur, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._declarer_icone(ecran)
        self.appliquer_police_arbre(prefs_core.load_preferences(self.config_file).tree_font)
        logger.debug("_charger_css: fait")

    @staticmethod
    def _declarer_icone(ecran: Gdk.Display) -> None:
        """Icône des fenêtres : celle du lanceur, ``APPLICATION_ID`` (#155).

        Depuis les sources, le dossier ``data/icons`` du dépôt est ajouté au
        thème d'icônes pour que l'icône soit trouvée sans installation.
        """
        logger.debug("_declarer_icone: entrée")
        depot = os.path.normpath(ICONES_DEPOT)
        if os.path.isdir(depot):
            Gtk.IconTheme.get_for_display(ecran).add_search_path(depot)
            logger.debug("_declarer_icone: icônes du dépôt {}", depot)
        Gtk.Window.set_default_icon_name(APPLICATION_ID)
        logger.debug("_declarer_icone: fait")

    @staticmethod
    def _charger(fournisseur: Gtk.CssProvider, css: str) -> None:
        """Remplace le contenu d'une feuille de style (GTK 4.8 à 4.12+)."""
        logger.trace("_charger: {} octet(s)", len(css))
        if hasattr(fournisseur, "load_from_string"):  # GTK ≥ 4.12
            fournisseur.load_from_string(css)
        else:  # GTK 4.8 (Debian 12) : un seul argument, des octets (#223)
            fournisseur.load_from_data(css.encode("utf-8"))
        logger.trace("_charger: fait")

    def appliquer_police_arbre(self, description: str) -> None:
        """Police de l'arbre des hôtes (#147) ; vide : police du système."""
        logger.debug("appliquer_police_arbre: {!r}", description)
        if self.css_police is None:
            logger.debug("appliquer_police_arbre: CSS non chargé")
            return
        self._charger(self.css_police, css_police(description))
        logger.debug("appliquer_police_arbre: fait")

    def set_group_colors(self, couleurs: Iterable[str]) -> None:
        """Recharge les classes ``gcm-couleur-rrggbb`` (arbre des hôtes, #121)."""
        logger.debug("set_group_colors")
        if self.css_couleurs is None:
            logger.debug("set_group_colors: CSS non chargé")
            return
        self._charger(self.css_couleurs, theme.colors_css(couleurs))
        logger.debug("set_group_colors: fait")

    def _on_command_line(self, _app: Adw.Application, ligne: Gio.ApplicationCommandLine) -> int:
        """Ligne de commande : premier lancement ou deuxième lancement transmis (#275).

        Au premier lancement, ``self.args`` contient déjà les options
        analysées par ``gcm4.app.main`` : la fenêtre est seulement activée.
        Un deuxième lancement est analysé dans le contexte de son processus
        (dossier courant, environnement) et appliqué à l'instance en cours :
        ``--debug`` passe le journal au niveau DEBUG, ``groupe/nom``
        sélectionne les hôtes. Un autre dossier de configuration est refusé
        (``CODE_AUTRE_CONFIG``, message affiché par :func:`run`) plutôt
        qu'ignoré.

        Args:
            _app (Adw.Application): L'application (``self``).
            ligne (Gio.ApplicationCommandLine): Ligne de commande reçue.

        Returns:
            int: Code de sortie du lancement qui a transmis la ligne.
        """
        logger.debug("_on_command_line: entrée (distante={})", ligne.get_is_remote())
        if not ligne.get_is_remote():
            self.activate()
            logger.debug("_on_command_line: sortie 0 (premier lancement)")
            return 0
        demande = self._analyser_ligne_distante(ligne)
        if demande is None:
            logger.debug("_on_command_line: sortie {} (ligne invalide)", CODE_REFUS)
            return CODE_REFUS
        if demande.config_dir != self.args.config_dir:
            logger.warning(
                "_on_command_line: dossier {} refusé, instance lancée avec {}",
                demande.config_dir,
                self.args.config_dir,
            )
            # Message écrit par le deuxième lancement (run) : printerr n'est
            # pas accessible par introspection et printerr_literal date de GLib 2.80.
            logger.debug("_on_command_line: sortie {} (autre configuration)", CODE_AUTRE_CONFIG)
            return CODE_AUTRE_CONFIG
        if demande.debug and not self.args.debug:
            self.args.debug = True
            logging_config.enable_debug(config_dir=self.args.config_dir)
            logger.info("_on_command_line: mode debug activé par un deuxième lancement")
        self.activate()
        principale = next((f for f in self.get_windows() if isinstance(f, MainWindow)), None)
        if principale is not None:
            principale.ouvrir_hotes(demande.hotes)
        logger.debug("_on_command_line: sortie 0 ({} hôte(s))", len(demande.hotes))
        return 0

    @staticmethod
    def _analyser_ligne_distante(ligne: Gio.ApplicationCommandLine) -> argparse.Namespace | None:
        """Options d'un deuxième lancement, analysées avec son dossier et son environnement.

        Args:
            ligne (Gio.ApplicationCommandLine): Ligne de commande transmise.

        Returns:
            argparse.Namespace | None: Options de ``gcm4.core.cli``, ``None``
            si la ligne est refusée par l'analyseur (le deuxième lancement
            l'a déjà validée : seul un appel D-Bus forgé y arrive).
        """
        logger.debug("_analyser_ligne_distante: entrée")
        arguments = list(ligne.get_arguments() or [])[1:]
        logger.debug("_analyser_ligne_distante: {} argument(s)", len(arguments))
        environ = dict(v.partition("=")[::2] for v in (ligne.get_environ() or []))
        try:
            demande = cli.parse_args(
                arguments, home=environ.get("HOME"), environ=environ, cwd=ligne.get_cwd()
            )
        except SystemExit:
            logger.warning("_analyser_ligne_distante: ligne refusée {}", arguments)
            return None
        logger.debug("_analyser_ligne_distante: config_dir={}", demande.config_dir)
        return demande

    def _on_activate(self, _app: Adw.Application) -> None:
        """Présente la fenêtre principale, créée au premier appel."""
        logger.debug("_on_activate: entrée")
        fenetre = self.get_active_window()
        if fenetre is None:
            fenetre = MainWindow(self, self.args.config_dir)
            fenetre.present()
            fenetre.restaurer_onglets()  # une fois, à la création (#246)
            fenetre.ouvrir_hotes(getattr(self.args, "hotes", []))  # gcm4 groupe/nom (#154)
            mises_a_jour.au_demarrage(fenetre)  # jamais sans accord (#245)
            if prefs_core.load_preferences(self.config_file).tray_icon:
                self.appliquer_zone_notification(True)
        else:
            fenetre.present()
        logger.debug("_on_activate: fenêtre présentée")

    def _fenetre_principale(self) -> Gtk.Window | None:
        """Fenêtre principale, même masquée par l'icône de notification (#83)."""
        logger.trace("_fenetre_principale")
        resultat = next((f for f in self.get_windows() if isinstance(f, MainWindow)), None)
        logger.trace("_fenetre_principale: {}", resultat is not None)
        return resultat

    def _principale_visible(self) -> bool:
        """La fenêtre principale est-elle affichée ? (libellé du menu de l'icône)."""
        logger.trace("_principale_visible")
        fenetre = self._fenetre_principale()
        resultat = fenetre is not None and fenetre.get_visible()
        logger.trace("_principale_visible: {}", resultat)
        return resultat

    def basculer_fenetre(self) -> None:
        """Clic sur l'icône de notification : masque la fenêtre affichée, sinon la présente."""
        logger.debug("basculer_fenetre: entrée")
        fenetre = self._fenetre_principale()
        if fenetre is not None and fenetre.get_visible():
            fenetre.set_visible(False)
            logger.debug("basculer_fenetre: masquée")
            return
        if fenetre is None:
            self.activate()  # aucune fenêtre (fermée) : la crée
        else:
            fenetre.present()
        logger.debug("basculer_fenetre: présentée")

    def appliquer_zone_notification(self, active: bool) -> None:
        """Affiche ou retire l'icône de la zone de notification (préférence ``tray-icon``, #83).

        Sans bus de session, l'option reste sans effet (journalisé).
        """
        logger.debug("appliquer_zone_notification: {}", active)
        if not active:
            if self.zone_notification is not None:
                self.zone_notification.arreter()
                self.zone_notification = None
            logger.debug("appliquer_zone_notification: retirée")
            return
        if self.zone_notification is not None:
            logger.debug("appliquer_zone_notification: déjà affichée")
            return
        connexion = self.get_dbus_connection()
        if connexion is None:
            logger.warning("zone de notification : aucun bus de session D-Bus, icône non affichée")
            return
        depot = os.path.normpath(ICONES_DEPOT)
        self.zone_notification = IconeNotification(
            connexion,
            APPLICATION_ID,
            depot if os.path.isdir(depot) else "",
            self._principale_visible,
            self.basculer_fenetre,
            lambda: self.activate_action("quit", None),
        )
        self.zone_notification.demarrer()
        logger.debug("appliquer_zone_notification: publiée")

    def _on_quit(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Ctrl+Q : ferme les fenêtres (leur état est enregistré) puis quitte.

        Une fenêtre qui demande confirmation (connexions ouvertes, #147)
        garde l'application ouverte : elle quittera après la réponse.
        """
        logger.debug("_on_quit: entrée")
        for fenetre in list(self.get_windows()):
            fenetre.close()
        if any(getattr(f, "confirmation_fermeture", None) for f in self.get_windows()):
            logger.debug("_on_quit: confirmation demandée, pas encore quitté")
            return
        self.quit()
        logger.debug("_on_quit: fin")

    def boite_a_propos(self) -> Gtk.Widget:
        """Boîte « À propos » (``Adw.AboutDialog`` si libadwaita ≥ 1.5, sinon ``AboutWindow``)."""
        logger.debug("boite_a_propos: entrée")
        classe = getattr(Adw, "AboutDialog", None) or Adw.AboutWindow
        boite = classe(
            application_name=NOM_AFFICHE,
            application_icon=APPLICATION_ID,
            version=__version__,
            developers=DEVELOPPEURS,
            copyright="© 2012 Renzo Bertuzzi L.",
            license_type=Gtk.License.GPL_3_0,
            website=SITE,
            comments=_("A simple ssh connection manager for gnome"),
        )
        logger.debug("boite_a_propos: {}", classe.__name__)
        return boite

    def _on_about(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Aide > À propos."""
        logger.debug("_on_about: entrée")
        boite = self.boite_a_propos()
        fenetre = self.get_active_window()
        if hasattr(boite, "set_transient_for"):  # Adw.AboutWindow (libadwaita < 1.5)
            boite.set_transient_for(fenetre)
            boite.present()
        else:  # Adw.AboutDialog
            boite.present(fenetre)
        logger.debug("_on_about: présentée")

    def _on_help(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Aide > Guide utilisateur (F1, #86) : guide ouvert dans le navigateur."""
        logger.debug("_on_help: entrée")
        aide.ouvrir_guide(self.get_active_window())
        logger.debug("_on_help: fait")

    def _on_shortcuts(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Aide > Raccourcis clavier (Ctrl+?, #86) : raccourcis effectifs."""
        logger.debug("_on_shortcuts: entrée")
        self.fenetre_raccourcis = aide.presenter_raccourcis(
            self.get_active_window(), self.raccourcis_effectifs()
        )
        logger.debug("_on_shortcuts: fait")

    def _on_preferences(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Édition > Préférences (Ctrl+,) : pages Général et Apparence (#147)."""
        logger.debug("_on_preferences: entrée")
        fenetre = self.get_active_window()
        if isinstance(fenetre, PreferencesWindow):
            fenetre.present()
            logger.debug("_on_preferences: déjà ouverte")
            return
        principale = next((f for f in self.get_windows() if isinstance(f, MainWindow)), None)
        self.preferences = PreferencesWindow(self, principale)
        self.preferences.present()
        logger.debug("_on_preferences: présentée")


def run(args: argparse.Namespace, argv: list[str] | None = None) -> int:
    """Exécute la boucle GTK.

    Args:
        args (argparse.Namespace): Options déjà analysées (``gcm4.core.cli``).
        argv (list[str] | None): Arguments bruts (sans ``argv[0]``), transmis
            à l'instance en cours si gcm4 est déjà lancé (#275).

    Returns:
        int: Code de sortie du processus.
    """
    logger.debug("run: entrée")
    application = Gcm4Application(args)
    # asyncio sur GLib avant run() : plugins rdp, vnc (#105).
    code = boucle.executer(lambda: application.run(["gcm4", *(argv or [])]))
    # Seul un deuxième lancement reçoit ce code (celui de l'instance en cours).
    if code == CODE_AUTRE_CONFIG:
        print(
            _(
                "gcm4 is already running with another configuration folder: "
                "close it to use {requested}."
            ).format(requested=args.config_dir),
            file=sys.stderr,
        )
    logger.debug("run: sortie {}", code)
    return int(code)
