"""Fenêtre principale de gcm4 (#134).

``Adw.HeaderBar`` avec menu principal, ``Gtk.Paned`` : panneau gauche
(arbre des hôtes du lot 3, #139) et ``Gtk.Notebook`` à droite avec une page
d'accueil. Taille et largeur du panneau restaurées depuis ``gcm4.conf`` et
enregistrées à la fermeture.

Menu Fichier (#153) : import/export des hôtes et copie de la configuration,
dans :mod:`gcm4.ui.echange`.

Connexions (#151) : ``win.connect-host`` demande au registre des plugins
le plugin du protocole de l'hôte. Sans plugin, un message le dit ; sinon
sa session devient un onglet, fermée par le bouton de l'onglet ou à la
fermeture de la fenêtre.

Onglets (#247, options de base de ``Wconfig``) : confirmation avant de
fermer un onglet (bouton, clic du milieu sur l'onglet), onglet suivant /
précédent (raccourcis, molette sur les onglets) avec bouclage optionnel,
titre de la fenêtre personnalisable et suivant l'onglet courant. La
fermeture de la fenêtre ne redemande rien onglet par onglet : elle a sa
propre confirmation (#147).
"""

from __future__ import annotations

from collections.abc import Sequence

from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from loguru import logger

from gcm4.core import chemins, comportement_onglets, menu, onglets, window_state
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import AppConfig, ConfigError, load_config, save_config, update_keys
from gcm4.core.i18n import _
from gcm4.core.plugins import Session
from gcm4.core.registre import PluginRegistry
from gcm4.core.replies import ecrire_replies
from gcm4.ui import dialogues
from gcm4.ui.echange import Echange
from gcm4.ui.host_tree import HostTree

LARGEUR_DEFAUT = 1000
HAUTEUR_DEFAUT = 650


def construire_menu() -> Gio.Menu:
    """``Gio.Menu`` du menu principal, depuis ``gcm4.core.menu``."""
    logger.debug("construire_menu: entrée")
    racine = Gio.Menu()
    for sous_menu in menu.MENU_PRINCIPAL:
        contenu = Gio.Menu()
        for section in sous_menu.sections:
            bloc = Gio.Menu()
            for entree in section:
                ligne = Gio.MenuItem.new(_(entree.libelle), None)
                if entree.cible is None:
                    ligne.set_detailed_action(entree.action)
                else:
                    ligne.set_action_and_target_value(
                        entree.action, GLib.Variant("s", entree.cible)
                    )
                bloc.append_item(ligne)
            contenu.append_section(None, bloc)
        racine.append_submenu(_(sous_menu.titre), contenu)
    logger.debug("construire_menu: {} sous-menu(s)", racine.get_n_items())
    return racine


class MainWindow(Adw.ApplicationWindow):
    """Fenêtre principale : en-tête, panneau gauche, onglets."""

    def __init__(self, application: Adw.Application, config_dir: str) -> None:
        """Construit la fenêtre et restaure son état depuis ``config_dir/gcm4.conf``."""
        logger.debug("MainWindow.__init__: config_dir={}", config_dir)
        super().__init__(application=application, title=comportement_onglets.NOM_APPLICATION)
        self.config_file = chemins.fichier_conf(config_dir)
        self.etat = window_state.load_window_state(self.config_file)
        registre = getattr(application, "registre", None)
        #: Plugins de l'application ; vide : mode zéro plugin.
        self.registre = registre if isinstance(registre, PluginRegistry) else PluginRegistry()
        #: Contenu d'onglet -> session du plugin à fermer avec l'onglet.
        self.sessions: dict[Gtk.Widget, Session] = {}
        #: Contenu d'onglet -> chemin ``groupe/nom`` de l'hôte (restauration, #246).
        self.chemins_sessions: dict[Gtk.Widget, str] = {}
        #: Dialogue « Quitter quand même ? » affiché (#147), sinon ``None``.
        self.confirmation_fermeture: Adw.Dialog | Adw.MessageDialog | None = None
        self._fermeture_confirmee = False
        #: Contenu d'onglet -> titre affiché (titre dynamique, confirmation, #247).
        self.titres_onglets: dict[Gtk.Widget, str] = {}
        #: Contenu d'onglet -> dialogue « Fermer la console » affiché (#247).
        self.confirmations_onglets: dict[Gtk.Widget, Adw.Dialog | Adw.MessageDialog] = {}

        entete = Adw.HeaderBar()
        self.bouton_menu = Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=construire_menu()
        )
        self.bouton_menu.set_tooltip_text(_("Menu"))
        entete.pack_end(self.bouton_menu)

        self.panneau = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.panneau.set_size_request(120, -1)
        self.config = None
        self.arbre: HostTree | None = None
        try:
            self.chiffrement = ChiffrementGcm(config_dir)
            self.config = load_config(self.config_file, self.chiffrement)
        except ConfigError as exc:
            # Rien n'est chargé : aucune écriture ne pourra écraser le fichier.
            logger.warning("MainWindow.__init__: configuration non chargée ({})", exc)
            self.erreur_config = Adw.StatusPage(
                icon_name="dialog-warning-symbolic",
                title=_("Configuration not loaded"),
                description=str(exc),
            )
            self.erreur_config.set_vexpand(True)
            self.panneau.append(self.erreur_config)
        else:
            self.arbre = HostTree(
                self.config.store,
                rayures=not self.config.options.disable_hosts_stripes,
                enregistrer=self.enregistrer_config,
                notifier=self.notifier,
            )
            self.panneau.append(self.arbre)
            couleurs = [g.color for g in self.config.store.groups.values() if g.color]
            if hasattr(application, "set_group_colors"):
                application.set_group_colors(couleurs)
        self.onglets = Gtk.Notebook(scrollable=True)
        self.page_accueil = Adw.StatusPage(
            icon_name="network-server-symbolic",
            title=_("Welcome to Gnome Connection Manager"),
            description=_("Hosts and groups will appear in the left panel."),
        )
        etiquette_accueil = Gtk.Label(label=_("Home"))
        self._brancher_defilement(etiquette_accueil)
        self.onglets.append_page(self.page_accueil, etiquette_accueil)
        self.onglets.connect("switch-page", self._on_switch_page)

        self.separateur = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.separateur.set_start_child(self.panneau)
        self.separateur.set_end_child(self.onglets)
        self.separateur.set_resize_start_child(False)
        self.separateur.set_position(self.etat.panel_width)

        corps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        corps.append(entete)
        corps.append(self.separateur)
        self.separateur.set_vexpand(True)
        self.toasts = Adw.ToastOverlay(child=corps)
        self.set_content(self.toasts)

        self.set_default_size(
            self.etat.width if self.etat.width > 0 else LARGEUR_DEFAUT,
            self.etat.height if self.etat.height > 0 else HAUTEUR_DEFAUT,
        )
        action = Gio.SimpleAction.new_stateful(
            "toggle-panel", None, GLib.Variant.new_boolean(self.etat.show_panel)
        )
        action.connect("change-state", self._on_toggle_panel)
        self.add_action(action)
        self.panneau.set_visible(self.etat.show_panel)
        connexion = Gio.SimpleAction.new("connect-host", GLib.VariantType.new("s"))
        connexion.connect("activate", self._on_connect_host)
        self.add_action(connexion)
        recherche = Gio.SimpleAction.new("search", None)
        recherche.connect("activate", self._on_search)
        recherche.set_enabled(self.arbre is not None)  # configuration illisible : rien à chercher
        self.add_action(recherche)
        #: Fichier > importer, exporter, enregistrer la configuration sous (#153).
        self.echange = Echange(self)
        for nom, sens in (
            ("previous-tab", comportement_onglets.PRECEDENT),
            ("next-tab", comportement_onglets.SUIVANT),
        ):
            voisin = Gio.SimpleAction.new(nom, None)
            voisin.connect("activate", self._on_onglet_voisin, sens)
            self.add_action(voisin)
        self.maj_titre()
        self.connect("close-request", self._on_close_request)
        logger.debug("MainWindow.__init__: prête ({})", self.etat)

    def enregistrer_config(self) -> None:
        """Écrit toute la configuration (après une modification de l'arbre).

        Raises:
            ConfigError: écriture impossible ; le fichier est intact.
        """
        logger.debug("enregistrer_config: entrée")
        if self.config is None:  # configuration illisible : jamais d'écriture
            logger.debug("enregistrer_config: aucune configuration chargée")
            return
        save_config(self.config_file, self.config, self.chiffrement)
        logger.debug("enregistrer_config: fait")

    def notifier(self, message: str) -> None:
        """Toast de quelques secondes en bas de la fenêtre."""
        logger.debug("notifier: entrée")
        self.toasts.add_toast(Adw.Toast(title=message, timeout=5))
        logger.debug("notifier: fait")

    def _on_connect_host(self, _action: Gio.SimpleAction, param: GLib.Variant) -> None:
        """Double-clic / Entrée / « Connecter » : le plugin du protocole ouvre un onglet.

        Sans plugin pour ce protocole (mode zéro plugin compris), un message
        le dit et l'hôte reste intact. Un échec du plugin est affiché et
        journalisé, sans onglet.
        """
        logger.debug("_on_connect_host: {!r}", param.get_string())
        chemin = param.get_string()
        hote = self.config.store.find(chemin) if self.config is not None else None
        plugin = self.registre.get(hote.protocol) if hote is not None else None
        if hote is None:
            logger.debug("_on_connect_host: hôte introuvable")
            return
        if plugin is None:
            self.notifier(
                _(
                    "No extension handles the protocol «{protocol}». "
                    "Install it to open this connection."
                ).format(protocol=hote.protocol)
            )
            logger.debug("_on_connect_host: aucun plugin")
            return
        try:
            session = plugin.open(hote)
        except Exception as exc:  # un plugin ne doit pas arrêter gcm4
            logger.warning(
                "_on_connect_host: {!r} n'a pas ouvert {!r} : {}", plugin.name, chemin, exc
            )
            self.notifier(_("Unable to open «{host}»: {error}").format(host=hote.name, error=exc))
            logger.debug("_on_connect_host: échec du plugin")
            return
        self.ajouter_onglet(hote.name, session.widget, session)
        self.chemins_sessions[session.widget] = chemin
        logger.debug("_on_connect_host: onglet ouvert")

    def ajouter_onglet(
        self, titre: str, contenu: Gtk.Widget, session: Session | None = None
    ) -> int:
        """Onglet ``titre`` avec un bouton de fermeture ; rend son numéro (devenu courant)."""
        logger.debug("ajouter_onglet: {!r} session={}", titre, session is not None)
        entete = Gtk.Box(spacing=6)
        entete.append(Gtk.Label(label=titre))
        fermer = Gtk.Button(icon_name="window-close-symbolic", has_frame=False)
        fermer.set_tooltip_text(_("Close tab"))
        fermer.connect("clicked", lambda _b: self.demander_fermeture(contenu))
        entete.append(fermer)
        milieu = Gtk.GestureClick(button=Gdk.BUTTON_MIDDLE)
        milieu.connect("pressed", self._on_clic_milieu, contenu)
        entete.add_controller(milieu)
        self._brancher_defilement(entete)
        self.titres_onglets[contenu] = titre
        if session is not None:
            self.sessions[contenu] = session
        numero = self.onglets.append_page(contenu, entete)
        self.onglets.set_tab_reorderable(contenu, True)
        self.onglets.set_current_page(numero)
        self.maj_titre()  # première page ajoutée : switch-page peut précéder le titre
        logger.debug("ajouter_onglet: page {}", numero)
        return numero

    def _options(self) -> AppConfig:
        """Options de ``[options]`` ; configuration illisible : valeurs par défaut."""
        logger.trace("_options")
        resultat = self.config.options if self.config is not None else AppConfig()
        logger.trace("_options: fait")
        return resultat

    def demander_fermeture(self, contenu: Gtk.Widget, milieu: bool = False) -> None:
        """Ferme l'onglet, après confirmation si l'option correspondante est active.

        Args:
            contenu (Gtk.Widget): Contenu de l'onglet.
            milieu (bool): Clic du milieu sur l'onglet (option
                ``confirm-close-tab-middle``) ; sinon bouton de fermeture
                (``confirm-close-tab``).
        """
        logger.debug("demander_fermeture: milieu={}", milieu)
        options = self._options()
        confirmer = options.confirm_close_tab_middle if milieu else options.confirm_close_tab
        if not confirmer:
            self.fermer_onglet(contenu)
            logger.debug("demander_fermeture: fermé sans confirmation")
            return
        if contenu in self.confirmations_onglets:
            logger.debug("demander_fermeture: déjà demandée")
            return

        def rappel(reponse: str) -> None:
            logger.debug("demander_fermeture.rappel: {}", reponse)
            if self.confirmations_onglets.get(contenu) is not dialogue:
                logger.debug("demander_fermeture.rappel: réponse déjà traitée")
                return
            del self.confirmations_onglets[contenu]
            if reponse == "fermer" and self.onglets.page_num(contenu) >= 0:
                self.fermer_onglet(contenu)
            logger.debug("demander_fermeture.rappel: fait")

        dialogue = dialogues.creer(
            self,
            _("Close console"),
            self.titres_onglets.get(contenu, ""),
            [
                ("annuler", _("Cancel"), None),
                ("fermer", _("Close"), Adw.ResponseAppearance.DESTRUCTIVE),
            ],
            "fermer",
            "annuler",
            rappel,
        )
        self.confirmations_onglets[contenu] = dialogue
        logger.debug("demander_fermeture: confirmation affichée")

    def _on_clic_milieu(
        self, geste: Gtk.GestureClick, _n: int, _x: float, _y: float, contenu: Gtk.Widget
    ) -> None:
        """Clic du milieu sur un onglet : le ferme (confirmation selon l'option)."""
        logger.debug("_on_clic_milieu: entrée")
        geste.set_state(Gtk.EventSequenceState.CLAIMED)
        self.demander_fermeture(contenu, milieu=True)
        logger.debug("_on_clic_milieu: fait")

    def _brancher_defilement(self, etiquette: Gtk.Widget) -> None:
        """Molette sur l'onglet ``etiquette`` : onglet précédent (haut) ou suivant (bas).

        Comme GTK3 (``on_tab_scroll``), sur les onglets seulement : le contenu
        des pages garde sa molette. ``DISCRETE`` : un geste du pavé tactile
        avance d'un onglet, pas de dix.
        """
        logger.trace("_brancher_defilement")
        defilement = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE
        )
        defilement.connect("scroll", self._on_defilement)
        etiquette.add_controller(defilement)
        logger.trace("_brancher_defilement: fait")

    def _on_defilement(
        self, _controleur: Gtk.EventControllerScroll, _dx: float, dy: float
    ) -> bool:
        """Défilement vertical sur un onglet ; ``dy`` nul (horizontal) : ignoré."""
        logger.debug("_on_defilement: dy={}", dy)
        if dy == 0:
            logger.debug("_on_defilement: ignoré")
            return False
        sens = comportement_onglets.PRECEDENT if dy < 0 else comportement_onglets.SUIVANT
        self.aller_onglet_voisin(sens)
        logger.debug("_on_defilement: fait")
        return True

    def aller_onglet_voisin(self, sens: int) -> None:
        """Affiche l'onglet suivant (``1``) ou précédent (``-1``), bouclage selon l'option."""
        logger.debug("aller_onglet_voisin: {}", sens)
        cible = comportement_onglets.voisin(
            self.onglets.get_current_page(),
            self.onglets.get_n_pages(),
            sens,
            self._options().cycle_tabs,
        )
        if cible >= 0:
            self.onglets.set_current_page(cible)
        logger.debug("aller_onglet_voisin: page {}", cible)

    def _on_onglet_voisin(
        self, _action: Gio.SimpleAction, _param: GLib.Variant | None, sens: int
    ) -> None:
        """``win.next-tab`` / ``win.previous-tab``."""
        logger.debug("_on_onglet_voisin: {}", sens)
        self.aller_onglet_voisin(sens)
        logger.debug("_on_onglet_voisin: fait")

    def _on_switch_page(self, _onglets: Gtk.Notebook, page: Gtk.Widget, _numero: int) -> None:
        """Changement d'onglet : titre de la fenêtre (``update-title``)."""
        logger.debug("_on_switch_page: entrée")
        self.maj_titre(page)
        logger.debug("_on_switch_page: fait")

    def maj_titre(self, page: Gtk.Widget | None = None) -> None:
        """Titre de la fenêtre selon ``app-title``, ``update-title`` et l'onglet ``page``.

        Args:
            page (Gtk.Widget | None): Page affichée ; ``None`` : page courante.
        """
        logger.debug("maj_titre: entrée")
        if page is None:
            numero = self.onglets.get_current_page()
            page = self.onglets.get_nth_page(numero) if numero >= 0 else None
        options = self._options()
        titre = comportement_onglets.titre_fenetre(
            options.app_title,
            options.update_title,
            self.titres_onglets.get(page) if page is not None else None,
        )
        self.set_title(titre)
        logger.debug("maj_titre: {!r}", titre)

    def appliquer_options(self) -> None:
        """Applique tout de suite les options d'affichage modifiées (Préférences, #247)."""
        logger.debug("appliquer_options: entrée")
        options = self._options()
        if self.arbre is not None:
            self.arbre.set_rayures(not options.disable_hosts_stripes)
        self.maj_titre()
        logger.debug("appliquer_options: fait")

    def fermer_onglet(self, contenu: Gtk.Widget) -> None:
        """Ferme la session de l'onglet (une seule fois) puis retire l'onglet."""
        logger.debug("fermer_onglet: entrée")
        session = self.sessions.pop(contenu, None)
        self.chemins_sessions.pop(contenu, None)
        self.titres_onglets.pop(contenu, None)
        dialogue = self.confirmations_onglets.pop(contenu, None)
        if dialogue is not None:  # fenêtre fermée pendant la confirmation
            # Adw.AlertDialog (1.5+) : force_close() ; Adw.MessageDialog : close().
            getattr(dialogue, "force_close", dialogue.close)()
        if session is not None:
            try:
                session.close()
            except Exception as exc:  # l'onglet se ferme quand même
                logger.warning("fermer_onglet: close() a échoué : {}", exc)
        numero = self.onglets.page_num(contenu)
        if numero >= 0:
            self.onglets.remove_page(numero)
        logger.debug("fermer_onglet: page {} retirée", numero)

    def onglets_ouverts(self) -> list[str]:
        """Chemins ``groupe/nom`` des onglets de connexion, dans l'ordre affiché."""
        logger.debug("onglets_ouverts: entrée")
        resultat = []
        for numero in range(self.onglets.get_n_pages()):
            chemin = self.chemins_sessions.get(self.onglets.get_nth_page(numero))
            if chemin is not None:
                resultat.append(chemin)
        logger.debug("onglets_ouverts: {}", len(resultat))
        return resultat

    def restaurer_onglets(self) -> None:
        """Rouvre les onglets de la dernière session si l'option est active (#246).

        Hôte disparu ou protocole sans plugin : un seul message par cas, pas
        d'erreur ; l'échec d'un plugin est signalé par ``win.connect-host``.
        """
        logger.debug("restaurer_onglets: entrée")
        if self.config is None or not self.config.options.restore_open_tabs:
            logger.debug("restaurer_onglets: option inactive ou configuration absente")
            return
        absents: list[str] = []
        sans_plugin: list[str] = []
        for chemin in onglets.lire(self.config_file):
            hote = self.config.store.find(chemin)
            if hote is None:
                absents.append(chemin)
            elif self.registre.get(hote.protocol) is None:
                sans_plugin.append(chemin)
            else:
                self.activate_action("win.connect-host", GLib.Variant.new_string(chemin))
        if absents:
            self.notifier(
                _("Not restored, host not found: {hosts}").format(hosts=", ".join(absents))
            )
        if sans_plugin:
            self.notifier(
                _("Not restored, no extension for: {hosts}").format(hosts=", ".join(sans_plugin))
            )
        logger.debug(
            "restaurer_onglets: {} absent(s), {} sans plugin", len(absents), len(sans_plugin)
        )

    def ouvrir_hotes(self, specs: Sequence[str]) -> None:
        """Hôtes de la ligne de commande (``gcm4 groupe/nom…``, #154).

        Chaque hôte trouvé est sélectionné dans l'arbre (groupes dépliés) ;
        il est connecté seulement si un plugin gère son protocole. Hôte
        inconnu : un seul message pour tous, pas d'erreur.

        Args:
            specs (Sequence[str]): Chemins ``groupe/sous-groupe/nom`` (``/``
                en début ou en fin toléré).
        """
        logger.debug("ouvrir_hotes: {} hôte(s)", len(specs))
        if self.config is None or not specs:
            logger.debug("ouvrir_hotes: rien à faire")
            return
        absents: list[str] = []
        for spec in specs:
            hote = self.config.store.find(spec.strip("/"))
            if hote is None:
                absents.append(spec)
                continue
            if self.arbre is not None:
                self.arbre.reveler(hote.path)
            if self.registre.get(hote.protocol) is not None:
                self.activate_action("win.connect-host", GLib.Variant.new_string(hote.path))
        if absents:
            self.notifier(_("Host not found: {hosts}").format(hosts=", ".join(absents)))
        logger.debug("ouvrir_hotes: {} absent(s)", len(absents))

    def _on_search(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        """Ctrl+F / Affichage > Rechercher : panneau affiché, curseur dans la recherche (#140)."""
        logger.debug("_on_search: entrée")
        if self.arbre is not None:
            if not self.panneau.get_visible():
                self.activate_action("win.toggle-panel", None)
            self.arbre.focus_recherche()
        logger.debug("_on_search: fait")

    def _on_toggle_panel(self, action: Gio.SimpleAction, valeur: GLib.Variant) -> None:
        """F9 / Affichage > Panneau : montre ou cache le panneau gauche."""
        logger.debug("_on_toggle_panel: {}", valeur.get_boolean())
        action.set_state(valeur)
        self.panneau.set_visible(valeur.get_boolean())
        self.etat.show_panel = valeur.get_boolean()
        logger.debug("_on_toggle_panel: fait")

    def etat_courant(self) -> window_state.WindowState:
        """État actuel (taille, panneau) à enregistrer."""
        logger.debug("etat_courant: entrée")
        largeur, hauteur = self.get_default_size()
        etat = window_state.WindowState(
            width=self.get_width() or largeur,
            height=self.get_height() or hauteur,
            panel_width=self.separateur.get_position(),
            show_panel=self.panneau.get_visible(),
        )
        logger.debug("etat_courant: {}", etat)
        return etat

    def _confirmation_requise(self) -> bool:
        """Connexions ouvertes et « Confirmer à la fermeture » actif (#147)."""
        logger.trace("_confirmation_requise")
        resultat = bool(
            self.sessions
            and self.config is not None
            and self.config.options.confirm_exit
            and not self._fermeture_confirmee
        )
        logger.trace("_confirmation_requise: {}", resultat)
        return resultat

    def _demander_confirmation(self) -> None:
        """Dialogue « Quitter quand même ? » ; « Quitter » ferme la fenêtre."""
        logger.debug("_demander_confirmation: {} connexion(s)", len(self.sessions))
        if self.confirmation_fermeture is not None:
            logger.debug("_demander_confirmation: déjà affichée")
            return

        def rappel(reponse: str) -> None:
            logger.debug("_demander_confirmation.rappel: {}", reponse)
            self.confirmation_fermeture = None
            if reponse == "quitter":
                self._fermeture_confirmee = True
                # Après la fermeture du dialogue : un close() immédiat fermerait
                # le dialogue encore attaché, pas la fenêtre.
                GLib.idle_add(lambda: self.close() and False)
            logger.debug("_demander_confirmation.rappel: fait")

        self.confirmation_fermeture = dialogues.creer(
            self,
            _("Quit anyway?"),
            _("Open connections will be closed."),
            [
                ("annuler", _("Cancel"), None),
                ("quitter", _("Quit"), Adw.ResponseAppearance.DESTRUCTIVE),
            ],
            "annuler",
            "annuler",
            rappel,
        )
        logger.debug("_demander_confirmation: affichée")

    def _on_close_request(self, _fenetre: Gtk.Window) -> bool:
        """Enregistre l'état ; une erreur d'écriture n'empêche pas la fermeture.

        Connexions ouvertes et « Confirmer à la fermeture » actif : la
        fermeture attend la confirmation (#147).
        """
        logger.debug("_on_close_request: entrée")
        if self._confirmation_requise():
            self._demander_confirmation()
            logger.debug("_on_close_request: confirmation demandée")
            return True
        ouverts = onglets.serialiser(self.onglets_ouverts())  # avant la fermeture
        for contenu in list(self.sessions):
            self.fermer_onglet(contenu)
        try:
            window_state.save_window_state(self.config_file, self.etat_courant())
            if self.config is not None:
                groupes = self.config.store.groups
                replies = ecrire_replies((p for p, g in groupes.items() if g.collapsed), groupes)
                # open-tabs écrit même option désactivée, comme GTK3 (#246).
                update_keys(
                    self.config_file,
                    "window",
                    {"collapsed-folders": replies, onglets.CLE: ouverts},
                )
        except ConfigError as exc:
            # gcm4.conf corrompu ou disque plein : le fichier reste intact,
            # seule la géométrie de cette session est perdue.
            logger.warning("_on_close_request: état non enregistré ({})", exc)
        logger.debug("_on_close_request: fermeture autorisée")
        return False
