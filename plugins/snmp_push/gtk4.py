# SPDX-License-Identifier: GPL-3.0-or-later
"""Outil « Déploiement SNMP » (contrat v1, #445, portage #179, après #74).

Portage GTK4 de ``plugins/plugin_snmp_push.py`` (``BatchPlugin`` GTK3,
supprimé) : un plugin outil ouvre un onglet « Push SNMP » à trois phases
(``Gtk.Stack``) — Configuration, Suivi, Résultats. Jumeau du portage
netmiko (#441) : même mécanique (inventaire CSV, gabarits ``#variable#``,
profils, validation à blanc, envoi parallèle annulable, journaux), seul
le modèle de profil change — un profil SNMP combine un constructeur
(:data:`core.SNMP_VENDORS`) et des identifiants SNMP v2c/v3 — et la
nouvelle section « serveur de dépôt » (#74) expose enfin ce que le cœur
sait faire depuis le 2026-10-24 : déposer le fichier rendu par
SFTP/FTP/TFTP (:class:`core.FileTransferConfig` transmis à
:func:`core.run_bulk_push`) avant que l'équipement n'aille le chercher.
Sans dépôt coché, comportement inchangé : le ``SNMP SET`` pointe juste
l'adresse du serveur de récupération (``server_ip``).

- les ``Gtk.TreeView``/``Gtk.ListStore`` deviennent des
  :class:`VueListe` (``Gtk.ColumnView`` sur ``Gio.ListStore``), avec
  cases à cocher pour l'inventaire et sélection pour les aperçus ;
- les ``Gtk.FileChooserButton`` (supprimés en GTK4) deviennent des
  boutons passant par :func:`gcm4.ui.fichiers.choisir` (asynchrone,
  repli GTK 4.8) ;
- les rappels des threads ouvriers rebondissent vers le thread
  principal par ``GLib.idle_add`` ; l'annulation passe par un
  ``threading.Event`` lu par le cœur ;
- l'ouverture du dossier des journaux passe par ``Gtk.FileLauncher``
  (repli ``Gio.AppInfo``).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Gio, GLib, GObject, Gtk, Pango  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import N_, _  # noqa: E402
from gcm4.core.plugins import SECTION_OUTILS, ToolPlugin  # noqa: E402
from gcm4.ui import fichiers  # noqa: E402

from . import core  # noqa: E402

#: Dossier des journaux d'exécution : un sous-dossier horodaté par lancement.
DOSSIER_JOURNAUX = Path.home() / ".local" / "share" / "gcm" / "snmp_push"


def _libelle_statut(statut: str) -> str:
    """Libellé traduit d'un statut d'envoi (ou le code brut par défaut)."""
    logger.trace("_libelle_statut: {!r}", statut)
    libelles = {
        core.STATUS_OK: _("OK"),
        core.STATUS_REJECTED: _("Rejeté"),
        core.STATUS_FAILED: _("Échec"),
        core.STATUS_SKIPPED: _("Ignoré"),
        "pending": _("En attente"),
        "running": _("En cours"),
    }
    logger.trace("_libelle_statut: fait")
    return libelles.get(statut, statut)


def _magasin_vide(chemin: Path) -> core.SnmpProfileStore:
    """Magasin de profils SNMP vide sur ``chemin`` (comme le GTK3 : rien
    n'est chargé tant que l'utilisateur n'a pas choisi son fichier).

    Passer par ``__new__`` évite ``SnmpProfileStore.__init__`` qui lirait
    ``chemin`` s'il existait déjà : ici l'onglet part toujours de rien.
    """
    logger.debug("_magasin_vide: {!r}", chemin)
    magasin = core.SnmpProfileStore.__new__(core.SnmpProfileStore)  # noqa: SLF001 — cf. docstring
    magasin.path = chemin
    magasin._profiles = {}  # noqa: SLF001 — idem
    logger.debug("_magasin_vide: fait")
    return magasin


class LigneSuivi:
    """Une ligne du suivi d'exécution (objet mutable, rafraîchi en place)."""

    def __init__(self, ligne: int, ip: str, profil: str) -> None:
        """Construit la ligne « En attente », sans détail ni durée."""
        logger.trace("LigneSuivi.__init__: {}", ligne)
        self.ligne = ligne
        self.ip = ip
        self.profil = profil
        self.statut = "pending"
        self.detail = ""
        self.duree = ""
        logger.trace("LigneSuivi.__init__: fait")


class Element(GObject.Object):
    """Valeur Python quelconque rangée dans un ``Gio.ListStore``, cochable."""

    def __init__(self, valeur: object, coche: bool = True) -> None:
        """Enveloppe ``valeur`` ; ``coche`` est l'état de la case à cocher."""
        logger.trace("Element.__init__")
        super().__init__()
        self.valeur = valeur
        self.coche = coche
        logger.trace("Element.__init__: sortie")


class VueListe(Gtk.ScrolledWindow):
    """``Gtk.ColumnView`` de lignes : cases à cocher et/ou sélection.

    Remplace les ``Gtk.TreeView`` + ``Gtk.ListStore`` de la version GTK3 :
    les lignes sont des :class:`Element` (valeur Python arbitraire). La
    colonne de cases à cocher est facultative (``cocher``), de même que
    la sélection simple (``sur_selection``, rappel avec la valeur).
    """

    def __init__(
        self,
        colonnes: list[tuple[str, Callable[[object], str], bool]],
        hauteur: int = 160,
        cocher: bool = False,
        sur_selection: Callable[[Any], None] | None = None,
    ) -> None:
        """Construit la vue.

        Args:
            colonnes: ``(titre, texte de la cellule, extensible)`` par colonne.
            hauteur: Hauteur minimale du défilement, en pixels.
            cocher: Ajoute une première colonne de cases à cocher.
            sur_selection: Rappel recevant la valeur de la ligne sélectionnée
                (clic) ; aucune sélection si ``None``.
        """
        logger.debug("VueListe.__init__: {} colonne(s)", len(colonnes))
        super().__init__(vexpand=True, has_frame=True)
        self.set_min_content_height(hauteur)
        self._sur_selection = sur_selection
        self.modele = Gio.ListStore.new(Element)
        self.vue = Gtk.ColumnView(vexpand=True)
        if sur_selection is not None:
            selection = Gtk.SingleSelection(model=self.modele, autoselect=False)
            selection.connect("selection-changed", self._selection_changee)
            self.selection = selection
            self.vue.set_model(selection)
        else:
            self.selection = None
            self.vue.set_model(Gtk.NoSelection(model=self.modele))
        if cocher:
            self.vue.append_column(self._colonne_coche())
        for titre, texte, extensible in colonnes:
            self.vue.append_column(self._colonne_texte(titre, texte, extensible))
        self.set_child(self.vue)
        logger.debug("VueListe.__init__: sortie")

    def _colonne_texte(
        self, titre: str, texte: Callable[[object], str], extensible: bool
    ) -> Gtk.ColumnViewColumn:
        """Colonne d'étiquettes affichant ``texte(valeur)``."""
        logger.trace("_colonne_texte: {}", titre)
        fabrique = Gtk.SignalListItemFactory()

        def preparer(_fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
            logger.trace("preparer: entrée")
            item.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
            logger.trace("preparer: sortie")

        def lier(_fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
            logger.trace("lier: entrée")
            item.get_child().set_text(texte(item.get_item().valeur))
            logger.trace("lier: sortie")

        fabrique.connect("setup", preparer)
        fabrique.connect("bind", lier)
        logger.trace("_colonne_texte: fait")
        return Gtk.ColumnViewColumn(title=titre, factory=fabrique, expand=extensible)

    def _colonne_coche(self) -> Gtk.ColumnViewColumn:
        """Colonne de cases à cocher liées à :attr:`Element.coche`."""
        logger.trace("_colonne_coche: entrée")
        fabrique = Gtk.SignalListItemFactory()

        def basculee(case: Gtk.CheckButton) -> None:
            logger.trace("basculee: entrée")
            if getattr(case, "liaison", False):
                logger.trace("basculee: liaison en cours, ignoré")
                return
            case.liaison = True
            case.set_active(not case.get_active())  # le toggle a déjà eu lieu
            if case.valeur is not None:
                case.valeur.coche = case.get_active()
            case.liaison = False
            logger.trace("basculee: sortie")

        def preparer(_fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
            logger.trace("preparer: entrée")
            case = Gtk.CheckButton(halign=Gtk.Align.CENTER)
            case.liaison = False
            case.valeur = None
            case.connect("toggled", basculee)
            item.set_child(case)
            logger.trace("preparer: sortie")

        def lier(_fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
            logger.trace("lier: entrée")
            case = item.get_child()
            case.valeur = item.get_item()
            case.liaison = True
            case.set_active(case.valeur.coche)
            case.liaison = False
            logger.trace("lier: sortie")

        fabrique.connect("setup", preparer)
        fabrique.connect("bind", lier)
        colonne = Gtk.ColumnViewColumn(title="", factory=fabrique)
        logger.trace("_colonne_coche: fait")
        return colonne

    def _selection_changee(self, selection: Gtk.SingleSelection, _position: int, _n: int) -> None:
        """Ligne cliquée : le rappel reçoit sa valeur (rien si aucune)."""
        logger.trace("_selection_changee")
        if self._sur_selection is None:
            logger.trace("_selection_changee: sortie (sans rappel)")
            return
        element = selection.get_selected_item()
        if element is not None:
            self._sur_selection(element.valeur)
        logger.trace("_selection_changee: sortie")

    def remplir(self, valeurs: list[object]) -> None:
        """Remplace le contenu par ``valeurs`` (toutes cochées)."""
        logger.debug("remplir: {} valeur(s)", len(valeurs))
        elements = [Element(v) for v in valeurs]
        self.modele.splice(0, self.modele.get_n_items(), elements)
        logger.debug("remplir: fait")

    def rafraichir(self) -> None:
        """Réaffiche les lignes existantes (valeurs mutées en place)."""
        logger.trace("rafraichir")
        elements = self.elements()
        self.modele.splice(0, len(elements), elements)
        logger.trace("rafraichir: fait")

    def elements(self) -> list[Element]:
        """Toutes les lignes du modèle, dans l'ordre."""
        logger.trace("elements")
        return [self.modele.get_item(i) for i in range(self.modele.get_n_items())]

    def selectionnees(self) -> list[Any]:
        """Valeurs des lignes cochées, dans l'ordre."""
        logger.trace("selectionnees")
        return [e.valeur for e in self.elements() if e.coche]


class OngletSnmp(Gtk.Box):
    """Onglet « Push SNMP » : Configuration → Suivi → Résultats.

    L'onglet déroge sciemment à la règle « un onglet = une connexion »
    du reste de gcm4 — décision assumée dès la version GTK3 : c'est une
    action par lot, sans console ouverte.
    """

    def __init__(self, context: Any) -> None:
        """Construit l'onglet vide : rien n'est chargé avant le choix des fichiers."""
        logger.debug("OngletSnmp.__init__: entrée")
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.context = context
        #: Lignes d'inventaire chargées (core.InventoryRow).
        self.rows: list[Any] = []
        #: Gabarit de configuration (texte brut).
        self.template_text: str = ""
        #: Magasin de profils (vide tant qu'aucun fichier n'est choisi).
        self.profiles = _magasin_vide(Path.home() / "snmp_profiles.ini")
        #: Chemins choisis : inventaire CSV, gabarit, profils.
        self._chemin_csv: Path | None = None
        self._chemin_gabarit: Path | None = None
        self._chemin_profils: Path | None = None
        #: Résultats du dernier envoi, par numéro de ligne.
        self._resultats: dict[int, Any] = {}
        #: Lignes du suivi de l'exécution en cours.
        self._suivi: dict[int, LigneSuivi] = {}
        #: Ordre des lignes du suivi (affichage).
        self._ordre_suivi: list[int] = []
        self._annuler = threading.Event()
        self._en_cours = False
        self._log_dir: Path | None = None
        self._total = 0
        self._faits = 0

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        aiguillage = Gtk.StackSwitcher(stack=self._stack)
        aiguillage.set_halign(Gtk.Align.CENTER)
        self.append(aiguillage)
        self._stack.set_vexpand(True)
        self.append(self._stack)

        self._stack.add_titled(self._construire_configuration(), "config", _("1. Configuration"))
        self._stack.add_titled(self._construire_suivi(), "monitor", _("2. Suivi"))
        self._stack.add_titled(self._construire_resultats(), "results", _("3. Résultats"))
        self._stack.set_visible_child_name("config")
        logger.debug("OngletSnmp.__init__: sortie")

    # ══════════════════════════════════════════════════════════════════
    # Phase 1 : Configuration
    # ══════════════════════════════════════════════════════════════════

    def _construire_configuration(self) -> Gtk.Widget:
        """Page de configuration : fichiers, profils, dépôt, inventaire, options."""
        logger.debug("_construire_configuration: entrée")
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        grille = Gtk.Grid(column_spacing=8, row_spacing=6)
        self._etiquette_csv = Gtk.Label(xalign=0)
        self._bouton_csv = self._ligne_fichier(
            grille, 0, _("Inventaire (CSV) :"), self._sur_csv_choisi, True
        )
        self._etiquette_gabarit = Gtk.Label(xalign=0)
        self._bouton_gabarit = self._ligne_fichier(
            grille, 1, _("Gabarit de configuration :"), self._sur_gabarit_choisi, False
        )
        bouton_charger = Gtk.Button(label=_("Charger l'inventaire"))
        bouton_charger.add_css_class("suggested-action")
        bouton_charger.connect("clicked", self._on_charger_inventaire)
        grille.attach(bouton_charger, 3, 0, 1, 2)
        page.append(grille)

        self._etiquette_statut = Gtk.Label(xalign=0, wrap=True)
        page.append(self._etiquette_statut)

        expander_profils = Gtk.Expander(label=_("Profils SNMP (constructeur, identifiants…)"))
        expander_profils.set_expanded(True)
        expander_profils.set_child(self._construire_editeur_profils())
        page.append(expander_profils)

        # Serveur de dépôt du fichier rendu (#74) : absent du GTK3.
        expander_depot = Gtk.Expander(label=_("Serveur de dépôt du fichier (SFTP / FTP / TFTP)"))
        expander_depot.set_expanded(False)
        expander_depot.set_child(self._construire_editeur_depot())
        page.append(expander_depot)

        # Inventaire à cocher + aperçu du gabarit rendu pour la ligne choisie.
        self._vue_inventaire = VueListe(
            [
                (_("Ligne"), lambda r: str(r.line_no), False),
                (_("IP / hôte"), lambda r: r.ip, False),
                (_("Profil"), lambda r: r.profile_name, False),
                (_("Variables"), lambda r: _resume_variables(r), True),
            ],
            hauteur=180,
            cocher=True,
            sur_selection=self._on_ligne_inventaire_choisie,
        )
        page.append(self._vue_inventaire)

        self._etiquette_apercu = Gtk.Label(xalign=0, wrap=True)
        page.append(self._etiquette_apercu)
        self._tampon_apercu = Gtk.TextBuffer()
        vue_apercu = Gtk.TextView(buffer=self._tampon_apercu, editable=False, monospace=True)
        vue_apercu.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        defilement = Gtk.ScrolledWindow(vexpand=False, has_frame=True)
        defilement.set_min_content_height(120)
        defilement.set_child(vue_apercu)
        page.append(defilement)

        options = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        options.append(Gtk.Label(label=_("Envois en parallèle :")))
        ajustement = Gtk.Adjustment(value=10, lower=1, upper=200, step_increment=1)
        self._choix_workers = Gtk.SpinButton(adjustment=ajustement)
        options.append(self._choix_workers)

        bouton_valider = Gtk.Button(label=_("Valider (à blanc, sans envoyer)"))
        bouton_valider.connect("clicked", self._on_valider)
        options.append(bouton_valider)
        self._bouton_lancer = Gtk.Button(label=_("Lancer le déploiement →"))
        self._bouton_lancer.add_css_class("suggested-action")
        self._bouton_lancer.connect("clicked", self._on_lancer)
        options.append(self._bouton_lancer)
        page.append(options)

        logger.debug("_construire_configuration: sortie")
        return page

    def _ligne_fichier(
        self,
        grille: Gtk.Grid,
        ligne: int,
        libelle: str,
        rappel: Callable[[str | None], None],
        filtrer: bool,
    ) -> Gtk.Button:
        """Une ligne « libellé — chemin choisi — bouton Choisir » de la grille."""
        logger.debug("_ligne_fichier: {}", libelle)
        texte = Gtk.Label(label=libelle, xalign=0)
        grille.attach(texte, 0, ligne, 1, 1)
        etiquette = self._etiquette_csv if ligne == 0 else self._etiquette_gabarit
        etiquette.set_hexpand(True)
        etiquette.set_ellipsize(Pango.EllipsizeMode.END)
        grille.attach(etiquette, 1, ligne, 1, 1)
        bouton = Gtk.Button(label=_("Choisir un fichier…"))
        bouton.connect("clicked", lambda _b: self._ouvrir_selecteur(libelle, rappel, filtrer))
        grille.attach(bouton, 2, ligne, 1, 1)
        logger.debug("_ligne_fichier: fait")
        return bouton

    def _ouvrir_selecteur(
        self, titre: str, rappel: Callable[[str | None], None], filtrer: bool
    ) -> None:
        """Ouvre le sélecteur de fichier (asynchrone, jamais bloquant)."""
        logger.debug("_ouvrir_selecteur: {!r}", titre)
        parent = self.get_root()
        if not isinstance(parent, Gtk.Window):
            logger.debug("_ouvrir_selecteur: pas de fenêtre parente, ignoré")
            return
        fichiers.choisir(
            parent,
            fichiers.OUVRIR,
            titre,
            rappel,
            filtrer=filtrer,
        )
        logger.debug("_ouvrir_selecteur: sélecteur ouvert")

    def _sur_csv_choisi(self, chemin: str | None) -> None:
        """Inventaire choisi (ou sélecteur annulé : rien ne change)."""
        logger.debug("_sur_csv_choisi: {!r}", chemin)
        if chemin:
            self._chemin_csv = Path(chemin)
            self._etiquette_csv.set_label(str(self._chemin_csv))
        logger.debug("_sur_csv_choisi: fait")

    def _sur_gabarit_choisi(self, chemin: str | None) -> None:
        """Gabarit choisi (ou sélecteur annulé : rien ne change)."""
        logger.debug("_sur_gabarit_choisi: {!r}", chemin)
        if chemin:
            self._chemin_gabarit = Path(chemin)
            self._etiquette_gabarit.set_label(str(self._chemin_gabarit))
        logger.debug("_sur_gabarit_choisi: fait")

    def _construire_editeur_profils(self) -> Gtk.Widget:
        """Éditeur de profils SNMP : liste à gauche, formulaire à droite."""
        logger.debug("_construire_editeur_profils: entrée")
        ensemble = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        gauche = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._vue_profils = VueListe(
            [
                (_("Nom"), lambda p: p.name, False),
                (_("Constructeur"), lambda p: p.vendor, False),
                (_("SNMP"), lambda p: p.auth.version, False),
                (_("Utilisateur"), lambda p: p.auth.username or "", False),
            ],
            hauteur=160,
            sur_selection=self._on_profil_choisi,
        )
        gauche.append(self._vue_profils)

        boutons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bouton_nouveau = Gtk.Button(label=_("Nouveau"))
        bouton_nouveau.connect("clicked", self._on_profil_nouveau)
        bouton_supprimer = Gtk.Button(label=_("Supprimer"))
        bouton_supprimer.connect("clicked", self._on_profil_supprimer)
        boutons.append(bouton_nouveau)
        boutons.append(bouton_supprimer)
        gauche.append(boutons)

        ligne_fichier = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        ligne_fichier.append(Gtk.Label(label=_("Fichier :")))
        self._etiquette_profils = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self._etiquette_profils.set_hexpand(True)
        ligne_fichier.append(self._etiquette_profils)
        bouton_fichier = Gtk.Button(label=_("Choisir un fichier…"))
        bouton_fichier.connect("clicked", self._on_choisir_profils)
        ligne_fichier.append(bouton_fichier)
        gauche.append(ligne_fichier)
        ensemble.append(gauche)

        formulaire = Gtk.Grid(column_spacing=8, row_spacing=6)
        formulaire.set_hexpand(True)

        def champ(ligne: int, libelle: str, widget: Gtk.Widget) -> None:
            logger.trace("champ: {}", libelle)
            texte = Gtk.Label(label=libelle, xalign=0)
            widget.set_hexpand(True)
            formulaire.attach(texte, 0, ligne, 1, 1)
            formulaire.attach(widget, 1, ligne, 1, 1)
            logger.trace("champ: sortie")

        self._ch_nom = Gtk.Entry()
        champ(0, _("Nom du profil (= colonne 'type'/'profile' du CSV) :"), self._ch_nom)

        self._liste_constructeurs = Gtk.StringList.new(
            [libelle for _clef, libelle in core.SNMP_VENDORS.items()]
        )
        self._choix_constructeur = Gtk.DropDown(model=self._liste_constructeurs, hexpand=True)
        champ(1, _("Constructeur (driver de push SNMP) :"), self._choix_constructeur)

        self._liste_versions = Gtk.StringList.new(
            [_("SNMPv2c (communauté)"), _("SNMPv3 (USM, auth+priv)")]
        )
        self._choix_version = Gtk.DropDown(model=self._liste_versions, hexpand=True)
        self._choix_version.set_selected(0)
        champ(2, _("Version SNMP :"), self._choix_version)

        self._ch_communaute = Gtk.Entry()
        self._ch_communaute.set_visibility(False)
        champ(3, _("Communauté (v2c, en écriture — jamais 'public') :"), self._ch_communaute)

        self._ch_utilisateur = Gtk.Entry()
        champ(4, _("Utilisateur USM (v3) :"), self._ch_utilisateur)

        self._liste_auth = Gtk.StringList.new(["SHA", "MD5", _("(aucune)")])
        self._choix_auth = Gtk.DropDown(model=self._liste_auth, hexpand=True)
        self._choix_auth.set_selected(0)
        champ(5, _("Protocole d'authentification (v3) :"), self._choix_auth)

        self._ch_cle_auth = Gtk.Entry()
        self._ch_cle_auth.set_visibility(False)
        champ(6, _("Clé d'authentification (v3) :"), self._ch_cle_auth)

        self._liste_priv = Gtk.StringList.new(["AES", "DES", _("(aucun)")])
        self._choix_priv = Gtk.DropDown(model=self._liste_priv, hexpand=True)
        self._choix_priv.set_selected(0)
        champ(7, _("Protocole de confidentialité (v3) :"), self._choix_priv)

        self._ch_cle_priv = Gtk.Entry()
        self._ch_cle_priv.set_visibility(False)
        champ(8, _("Clé de confidentialité (v3) :"), self._ch_cle_priv)

        self._case_voir = Gtk.CheckButton(label=_("Afficher les secrets"))
        self._case_voir.connect("toggled", self._on_voir_secrets)
        formulaire.attach(self._case_voir, 1, 9, 1, 1)

        self._ch_port = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=161, lower=1, upper=65535, step_increment=1)
        )
        champ(10, _("Port SNMP :"), self._ch_port)

        self._ch_timeout = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=5, lower=1, upper=120, step_increment=1)
        )
        champ(11, _("Timeout requête SNMP (s) :"), self._ch_timeout)

        ligne_boutons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bouton_enregistrer = Gtk.Button(label=_("Enregistrer ce profil"))
        bouton_enregistrer.connect("clicked", self._on_profil_enregistrer)
        bouton_ecrire = Gtk.Button(label=_("Écrire snmp_profiles.ini sur disque"))
        bouton_ecrire.connect("clicked", self._on_ecrire_profils)
        ligne_boutons.append(bouton_enregistrer)
        ligne_boutons.append(bouton_ecrire)
        formulaire.attach(ligne_boutons, 0, 12, 2, 1)
        ensemble.append(formulaire)

        logger.debug("_construire_editeur_profils: sortie")
        return ensemble

    def _construire_editeur_depot(self) -> Gtk.Widget:
        """Éditeur du serveur de dépôt (#74) : protocole, identifiants, dossier.

        Nouveau par rapport au GTK3, qui n'exposait que l'adresse : le
        cœur sait déposer le fichier rendu depuis #74 — le formulaire
        transmet simplement :class:`core.FileTransferConfig` à l'envoi.
        Décochée, la case laisse le comportement GTK3 (aucun dépôt, le
        ``SNMP SET`` ne cite que l'adresse du serveur).
        """
        logger.debug("_construire_editeur_depot: entrée")
        formulaire = Gtk.Grid(column_spacing=8, row_spacing=6)

        def champ(ligne: int, libelle: str, widget: Gtk.Widget) -> None:
            logger.trace("champ: {}", libelle)
            texte = Gtk.Label(label=libelle, xalign=0)
            widget.set_hexpand(True)
            formulaire.attach(texte, 0, ligne, 1, 1)
            formulaire.attach(widget, 1, ligne, 1, 1)
            logger.trace("champ: sortie")

        self._case_depot = Gtk.CheckButton(
            label=_("Déposer le fichier rendu sur ce serveur avant l'envoi SNMP")
        )
        self._case_depot.connect("toggled", self._on_depot_change)
        formulaire.attach(self._case_depot, 0, 0, 2, 1)

        self._liste_protocoles = Gtk.StringList.new(["SFTP", "FTP", "TFTP"])
        self._choix_protocole = Gtk.DropDown(model=self._liste_protocoles, hexpand=True)
        self._choix_protocole.set_selected(0)
        champ(1, _("Protocole de dépôt :"), self._choix_protocole)

        self._ch_serveur = Gtk.Entry()
        self._ch_serveur.set_text("192.168.1.1")
        champ(2, _("Adresse du serveur de dépôt :"), self._ch_serveur)

        self._ch_port_depot = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=0, lower=0, upper=65535, step_increment=1)
        )
        champ(3, _("Port (0 = défaut du protocole) :"), self._ch_port_depot)

        self._ch_utilisateur_depot = Gtk.Entry()
        champ(4, _("Utilisateur (SFTP/FTP) :"), self._ch_utilisateur_depot)

        self._ch_mot_de_passe_depot = Gtk.Entry()
        self._ch_mot_de_passe_depot.set_visibility(False)
        champ(5, _("Mot de passe (SFTP/FTP) :"), self._ch_mot_de_passe_depot)

        self._ch_dossier = Gtk.Entry()
        self._ch_dossier.set_text("/")
        champ(6, _("Dossier distant du dépôt :"), self._ch_dossier)

        self._liste_recuperation = Gtk.StringList.new([_("(automatique)"), "TFTP", "FTP"])
        self._choix_recuperation = Gtk.DropDown(model=self._liste_recuperation, hexpand=True)
        self._choix_recuperation.set_selected(0)
        champ(7, _("Protocole de récupération par l'équipement :"), self._choix_recuperation)

        indice = Gtk.Label(
            xalign=0,
            wrap=True,
            label=_(
                "⚠ L'équipement doit pouvoir joindre ce serveur pour aller chercher "
                "le fichier de configuration. Les paramètres de transfert (protocole, credentials) "
                "doivent être configurés à part."
            ),
        )
        formulaire.attach(indice, 0, 8, 2, 1)

        self._champs_depot = [
            self._choix_protocole,
            self._ch_serveur,
            self._ch_port_depot,
            self._ch_utilisateur_depot,
            self._ch_mot_de_passe_depot,
            self._ch_dossier,
            self._choix_recuperation,
        ]
        self._maj_sensibilite_depot()
        logger.debug("_construire_editeur_depot: sortie")
        return formulaire

    def _on_depot_change(self, case: Gtk.CheckButton) -> None:
        """Case « déposer » basculée : les champs suivent."""
        logger.debug("_on_depot_change: {}", case.get_active())
        self._maj_sensibilite_depot()
        logger.debug("_on_depot_change: fait")

    def _maj_sensibilite_depot(self) -> None:
        """Active ou grise les champs du dépôt selon la case."""
        logger.trace("_maj_sensibilite_depot")
        actif = self._case_depot.get_active()
        for champ in self._champs_depot:
            champ.set_sensitive(actif)
        logger.trace("_maj_sensibilite_depot: fait")

    def _config_depot(self) -> core.FileTransferConfig | None:
        """Configuration de dépôt du formulaire, ou ``None`` si décochée."""
        logger.debug("_config_depot: actif={}", self._case_depot.get_active())
        if not self._case_depot.get_active():
            logger.debug("_config_depot: pas de dépôt")
            return None
        protocoles = ["sftp", "ftp", "tftp"]
        recuperations = ["", "tftp", "ftp"]
        config = core.FileTransferConfig(
            protocol=protocoles[self._choix_protocole.get_selected()],
            host=self._ch_serveur.get_text().strip(),
            port=self._ch_port_depot.get_value_as_int(),
            username=self._ch_utilisateur_depot.get_text(),
            password=self._ch_mot_de_passe_depot.get_text(),
            remote_dir=self._ch_dossier.get_text().strip() or "/",
            fetch_protocol=recuperations[self._choix_recuperation.get_selected()],
        )
        logger.debug("_config_depot: {} sur {}", config.protocol, config.host)
        return config

    def _maj_liste_profils(self) -> None:
        """Réaffiche la liste des profils du magasin."""
        logger.debug("_maj_liste_profils: {} profil(s)", len(self.profiles.all()))
        self._vue_profils.remplir(list(self.profiles.all()))
        logger.debug("_maj_liste_profils: sortie")

    def _on_choisir_profils(self, _bouton: Gtk.Button) -> None:
        """Ouvre le sélecteur du fichier de profils."""
        logger.debug("_on_choisir_profils: entrée")
        parent = self.get_root()
        if not isinstance(parent, Gtk.Window):
            logger.debug("_on_choisir_profils: pas de fenêtre parente, ignoré")
            return
        fichiers.choisir(
            parent,
            fichiers.OUVRIR,
            "snmp_profiles.ini",
            self._sur_profils_choisis,
            filtrer=False,
        )
        logger.debug("_on_choisir_profils: sélecteur ouvert")

    def _sur_profils_choisis(self, chemin: str | None) -> None:
        """Fichier de profils choisi : le magasin est rechargé depuis lui."""
        logger.debug("_sur_profils_choisis: {!r}", chemin)
        if not chemin:
            logger.debug("_sur_profils_choisis: annulé")
            return
        try:
            self.profiles = core.SnmpProfileStore(chemin)
        except Exception as exc:  # noqa: BLE001
            logger.exception("snmp push : chargement des profils impossible")
            self._etiquette_statut.set_label(_("Erreur de chargement : {e}").format(e=exc))
            logger.debug("_sur_profils_choisis: échec notifié")
            return
        self._chemin_profils = Path(chemin)
        self._etiquette_profils.set_label(chemin)
        self._maj_liste_profils()
        self._etiquette_statut.set_label(_("Profils SNMP chargés depuis {p}").format(p=chemin))
        logger.debug("_sur_profils_choisis: fait")

    def _on_profil_choisi(self, profil: Any) -> None:
        """Profil sélectionné dans la liste : le formulaire se remplit."""
        logger.debug("_on_profil_choisi: {!r}", getattr(profil, "name", None))
        self._ch_nom.set_text(profil.name)
        vendeurs = list(core.SNMP_VENDORS)
        if profil.vendor in vendeurs:
            self._choix_constructeur.set_selected(vendeurs.index(profil.vendor))
        self._choix_version.set_selected(0 if profil.auth.version == "v2c" else 1)
        self._ch_communaute.set_text(profil.auth.community or "")
        self._ch_utilisateur.set_text(profil.auth.username or "")
        self._choix_auth.set_selected(
            ["SHA", "MD5", ""].index(profil.auth.auth_protocol or "")
            if (profil.auth.auth_protocol or "") in ("SHA", "MD5", "")
            else 0
        )
        self._ch_cle_auth.set_text(profil.auth.auth_password or "")
        self._choix_priv.set_selected(
            ["AES", "DES", ""].index(profil.auth.priv_protocol or "")
            if (profil.auth.priv_protocol or "") in ("AES", "DES", "")
            else 0
        )
        self._ch_cle_priv.set_text(profil.auth.priv_password or "")
        self._ch_port.set_value(profil.port)
        self._ch_timeout.set_value(profil.timeout)
        logger.debug("_on_profil_choisi: fait")

    def _on_profil_nouveau(self, _bouton: Gtk.Button) -> None:
        """Vide le formulaire pour saisir un nouveau profil."""
        logger.debug("_on_profil_nouveau: entrée")
        self._ch_nom.set_text("")
        self._choix_constructeur.set_selected(Gtk.INVALID_LIST_POSITION)
        self._choix_version.set_selected(0)
        self._ch_communaute.set_text("")
        self._ch_utilisateur.set_text("")
        self._choix_auth.set_selected(0)
        self._ch_cle_auth.set_text("")
        self._choix_priv.set_selected(0)
        self._ch_cle_priv.set_text("")
        self._ch_port.set_value(161)
        self._ch_timeout.set_value(5)
        self._ch_nom.grab_focus()
        logger.debug("_on_profil_nouveau: fait")

    def _on_profil_supprimer(self, _bouton: Gtk.Button) -> None:
        """Supprime le profil sélectionné dans la liste."""
        logger.debug("_on_profil_supprimer: entrée")
        element = None
        if self._vue_profils.selection is not None:
            element = self._vue_profils.selection.get_selected_item()
        if element is None:
            logger.debug("_on_profil_supprimer: aucun sélectionné")
            return
        self.profiles.delete(element.valeur.name)
        self._maj_liste_profils()
        logger.debug("_on_profil_supprimer: fait")

    def _on_profil_enregistrer(self, _bouton: Gtk.Button) -> None:
        """Enregistre le profil du formulaire dans le magasin (et sur disque)."""
        logger.debug("_on_profil_enregistrer: entrée")
        nom = self._ch_nom.get_text().strip()
        if not nom:
            self._etiquette_statut.set_label(_("Le profil doit avoir un nom."))
            logger.debug("_on_profil_enregistrer: sans nom, refusé")
            return
        index_vendeur = self._choix_constructeur.get_selected()
        if index_vendeur == Gtk.INVALID_LIST_POSITION:
            self._etiquette_statut.set_label(_("Choisissez un constructeur."))
            logger.debug("_on_profil_enregistrer: sans constructeur, refusé")
            return
        vendeurs = list(core.SNMP_VENDORS)
        protocoles_auth = ["SHA", "MD5", ""]
        protocoles_priv = ["AES", "DES", ""]
        auth = core.SnmpAuth(
            version="v2c" if self._choix_version.get_selected() == 0 else "v3",
            community=self._ch_communaute.get_text(),
            username=self._ch_utilisateur.get_text() or None,
            auth_password=self._ch_cle_auth.get_text() or None,
            auth_protocol=protocoles_auth[self._choix_auth.get_selected()],
            priv_password=self._ch_cle_priv.get_text() or None,
            priv_protocol=protocoles_priv[self._choix_priv.get_selected()],
        )
        profil = core.SnmpProfile(
            name=nom,
            vendor=vendeurs[index_vendeur],
            auth=auth,
            port=self._ch_port.get_value_as_int(),
            timeout=self._ch_timeout.get_value_as_int(),
        )
        self.profiles.set(nom, profil)
        self._maj_liste_profils()
        self._etiquette_statut.set_label(_("Profil '{n}' mis à jour (en mémoire).").format(n=nom))
        logger.debug("_on_profil_enregistrer: fait")

    def _on_ecrire_profils(self, _bouton: Gtk.Button) -> None:
        """Écrit le magasin de profils sur le disque."""
        logger.debug("_on_ecrire_profils: entrée")
        if not self._chemin_profils:
            self._etiquette_statut.set_label(_("Choisissez d'abord un fichier snmp_profiles.ini."))
            logger.debug("_on_ecrire_profils: sans fichier, refusé")
            return
        try:
            self.profiles.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("snmp push : écriture des profils impossible : {}", exc)
            self._etiquette_statut.set_label(_("Erreur d'écriture : {e}").format(e=exc))
            logger.debug("_on_ecrire_profils: échec notifié")
            return
        self._etiquette_statut.set_label(_("Profils écrits dans {p}").format(p=self.profiles.path))
        logger.debug("_on_ecrire_profils: fait")

    def _on_voir_secrets(self, case: Gtk.CheckButton) -> None:
        """Bascule la visibilité des secrets (communauté et clés v3)."""
        logger.debug("_on_voir_secrets: {}", case.get_active())
        voir = case.get_active()
        self._ch_communaute.set_visibility(voir)
        self._ch_cle_auth.set_visibility(voir)
        self._ch_cle_priv.set_visibility(voir)
        logger.debug("_on_voir_secrets: fait")

    # ── Inventaire ────────────────────────────────────────────────────────

    def _on_charger_inventaire(self, _bouton: Gtk.Button) -> None:
        """Charge l'inventaire CSV et le gabarit choisis."""
        logger.debug("_on_charger_inventaire: entrée")
        if not self._chemin_csv or not self._chemin_gabarit:
            self._etiquette_statut.set_label(_("Choisissez l'inventaire CSV et le gabarit."))
            logger.debug("_on_charger_inventaire: fichiers manquants")
            return
        try:
            self.rows = core.load_inventory(self._chemin_csv)
            self.template_text = self._chemin_gabarit.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("snmp push : chargement de l'inventaire impossible : {}", exc)
            self._etiquette_statut.set_label(_("Erreur de chargement : {e}").format(e=exc))
            logger.debug("_on_charger_inventaire: échec notifié")
            return
        self._vue_inventaire.remplir(list(self.rows))
        self._etiquette_statut.set_label(
            _("{n} hôte(s) chargé(s) depuis {csv}.").format(n=len(self.rows), csv=self._chemin_csv)
        )
        logger.debug("_on_charger_inventaire: {} ligne(s)", len(self.rows))

    def _on_ligne_inventaire_choisie(self, ligne: Any) -> None:
        """Ligne d'inventaire cliquée : aperçu du gabarit rendu pour elle."""
        logger.debug("_on_ligne_inventaire_choisie: ligne {}", ligne.line_no)
        contexte = ligne.variables
        manquantes = core.find_missing_variables(self.template_text, set(contexte))
        if manquantes:
            self._etiquette_apercu.set_label(
                _("⚠ Variables absentes du CSV : {v}").format(v=", ".join(manquantes))
            )
        else:
            self._etiquette_apercu.set_label("")
        self._tampon_apercu.set_text(core.render_template(self.template_text, contexte))
        logger.debug("_on_ligne_inventaire_choisie: fait")

    def _lignes_cochees(self) -> list[Any]:
        """Lignes d'inventaire cochées, dans l'ordre."""
        logger.trace("_lignes_cochees")
        return list(self._vue_inventaire.selectionnees())

    def _on_valider(self, _bouton: Gtk.Button) -> None:
        """Validation à blanc : profils connus et variables complètes."""
        logger.debug("_on_valider: entrée")
        if not self.rows:
            self._etiquette_statut.set_label(_("Chargez d'abord un inventaire."))
            logger.debug("_on_valider: sans inventaire")
            return
        pret = 0
        erreur = 0
        for element in self._vue_inventaire.elements():
            ligne = element.valeur
            if self.profiles.get(ligne.profile_name) is None:
                erreur += 1
                continue
            manquantes = core.find_missing_variables(self.template_text, set(ligne.variables))
            if manquantes:
                erreur += 1
                continue
            pret += 1
        self._etiquette_statut.set_label(
            _("Validation : {ok} prêt(s), {err} en erreur.").format(ok=pret, err=erreur)
        )
        logger.debug("_on_valider: {} prêt(s), {} en erreur", pret, erreur)

    # ══════════════════════════════════════════════════════════════════
    # Phase 2 : Suivi
    # ══════════════════════════════════════════════════════════════════

    def _construire_suivi(self) -> Gtk.Widget:
        """Page de suivi : progression, compteurs, une ligne par hôte."""
        logger.debug("_construire_suivi: entrée")
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self._progression = Gtk.ProgressBar(show_text=True)
        page.append(self._progression)

        compteurs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self._etiquette_ok = Gtk.Label()
        self._etiquette_rejetes = Gtk.Label()
        self._etiquette_echecs = Gtk.Label()
        self._etiquette_attente = Gtk.Label()
        for etiquette in (
            self._etiquette_ok,
            self._etiquette_rejetes,
            self._etiquette_echecs,
            self._etiquette_attente,
        ):
            compteurs.append(etiquette)
        page.append(compteurs)

        self._vue_suivi = VueListe(
            [
                (_("Ligne"), lambda s: str(s.ligne), False),
                (_("IP / hôte"), lambda s: s.ip, False),
                (_("Profil"), lambda s: s.profil, False),
                (_("Statut"), lambda s: _libelle_statut(s.statut), False),
                (_("Détail"), lambda s: s.detail, True),
                (_("Durée (s)"), lambda s: s.duree, False),
            ],
            hauteur=240,
        )
        page.append(self._vue_suivi)

        ligne_boutons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._bouton_annuler = Gtk.Button(label=_("Annuler l'envoi"))
        self._bouton_annuler.connect("clicked", self._on_annuler)
        self._bouton_annuler.set_sensitive(False)
        ligne_boutons.append(self._bouton_annuler)
        self._bouton_voir_resultats = Gtk.Button(label=_("Voir les résultats →"))
        self._bouton_voir_resultats.connect(
            "clicked", lambda _b: self._stack.set_visible_child_name("results")
        )
        self._bouton_voir_resultats.set_sensitive(False)
        ligne_boutons.append(self._bouton_voir_resultats)
        page.append(ligne_boutons)

        self._maj_compteurs()
        logger.debug("_construire_suivi: sortie")
        return page

    def _on_lancer(self, _bouton: Gtk.Button) -> None:
        """Lance le déploiement des lignes cochées dans un thread."""
        logger.debug("_on_lancer: entrée")
        if self._en_cours:
            logger.debug("_on_lancer: déjà en cours, ignoré")
            return
        if not self.rows:
            self._etiquette_statut.set_label(_("Chargez d'abord un inventaire."))
            logger.debug("_on_lancer: sans inventaire")
            return
        selection = self._lignes_cochees()
        if not selection:
            self._etiquette_statut.set_label(_("Aucune ligne cochée."))
            logger.debug("_on_lancer: rien de coché")
            return
        depot = self._config_depot()
        if depot is not None and not depot.host:
            self._etiquette_statut.set_label(
                _("⚠ Renseignez le serveur de dépôt avant de lancer.")
            )
            logger.debug("_on_lancer: sans serveur de dépôt, refusé")
            return

        self._log_dir = DOSSIER_JOURNAUX / time.strftime("%Y%m%d_%H%M%S")
        self._annuler = threading.Event()
        self._en_cours = True
        self._resultats.clear()
        self._suivi.clear()
        self._ordre_suivi = [ligne.line_no for ligne in selection]
        lignes = [LigneSuivi(ligne.line_no, ligne.ip, ligne.profile_name) for ligne in selection]
        for suivi in lignes:
            self._suivi[suivi.ligne] = suivi
        self._vue_suivi.remplir(list(lignes))

        self._total = len(selection)
        self._faits = 0
        self._progression.set_fraction(0.0)
        self._progression.set_text(_("0 / {n}").format(n=self._total))
        self._bouton_annuler.set_sensitive(True)
        self._bouton_voir_resultats.set_sensitive(False)
        self._maj_compteurs()
        self._stack.set_visible_child_name("monitor")

        workers = self._choix_workers.get_value_as_int()
        serveur = depot.host if depot is not None else self._ch_serveur.get_text().strip()

        def sur_debut(ligne: Any) -> None:
            logger.debug("sur_debut: ligne {}", ligne.line_no)
            GLib.idle_add(self._statut_suivi, ligne.line_no, "running", "", "")
            logger.debug("sur_debut: sortie")

        def sur_fini(resultat: Any) -> None:
            logger.debug("sur_fini: ligne {}", resultat.row.line_no)
            GLib.idle_add(self._gerer_resultat, resultat)
            logger.debug("sur_fini: sortie")

        def travailler() -> None:
            logger.debug("travailler: entrée")
            try:
                core.run_bulk_push(
                    selection,
                    self.profiles,
                    self.template_text,
                    log_dir=self._log_dir,
                    max_workers=workers,
                    on_row_start=sur_debut,
                    on_row_done=sur_fini,
                    cancel_event=self._annuler,
                    server_ip=serveur,
                    transfer=depot,
                )
            finally:
                GLib.idle_add(self._fin_execution)
            logger.debug("travailler: sortie")

        threading.Thread(target=travailler, daemon=True).start()
        logger.debug("_on_lancer: parti")

    def _on_annuler(self, _bouton: Gtk.Button) -> None:
        """Demande l'annulation : les hôtes restants seront ignorés."""
        logger.debug("_on_annuler: entrée")
        self._annuler.set()
        self._bouton_annuler.set_sensitive(False)
        logger.debug("_on_annuler: fait")

    def _statut_suivi(self, ligne: int, statut: str, detail: str, duree: str) -> bool:
        """Met à jour une ligne du suivi (appelé dans le thread principal)."""
        logger.debug("_statut_suivi: ligne {} → {}", ligne, statut)
        suivi = self._suivi.get(ligne)
        if suivi is not None:
            suivi.statut = statut
            suivi.detail = detail
            suivi.duree = duree
            self._vue_suivi.rafraichir()
        logger.debug("_statut_suivi: fait")
        return False

    def _gerer_resultat(self, resultat: Any) -> bool:
        """Un hôte est terminé : ligne, compteurs et progression suivent."""
        logger.debug("_gerer_resultat: ligne {}", resultat.row.line_no)
        self._resultats[resultat.row.line_no] = resultat
        self._statut_suivi(
            resultat.row.line_no,
            resultat.status,
            resultat.error,
            f"{resultat.duration_s:.1f}",
        )
        self._faits += 1
        self._progression.set_fraction(self._faits / max(1, self._total))
        self._progression.set_text(_("{d} / {n}").format(d=self._faits, n=self._total))
        self._maj_compteurs()
        logger.debug("_gerer_resultat: fait")
        return False

    def _maj_compteurs(self) -> None:
        """Actualise les quatre compteurs de la page de suivi."""
        logger.trace("_maj_compteurs")
        compte = {
            core.STATUS_OK: 0,
            core.STATUS_REJECTED: 0,
            core.STATUS_FAILED: 0,
        }
        for resultat in self._resultats.values():
            if resultat.status in compte:
                compte[resultat.status] += 1
        attente = self._total - len(self._resultats)
        self._etiquette_ok.set_label(_("OK : {n}").format(n=compte[core.STATUS_OK]))
        self._etiquette_rejetes.set_label(
            _("Rejetés : {n}").format(n=compte[core.STATUS_REJECTED])
        )
        self._etiquette_echecs.set_label(_("Échecs : {n}").format(n=compte[core.STATUS_FAILED]))
        self._etiquette_attente.set_label(_("En attente : {n}").format(n=attente))
        logger.trace("_maj_compteurs: fait")

    def _fin_execution(self) -> bool:
        """Fin du déploiement : boutons, résultats peuplés."""
        logger.debug("_fin_execution: entrée")
        self._en_cours = False
        self._bouton_annuler.set_sensitive(False)
        self._bouton_voir_resultats.set_sensitive(True)
        self._peupler_resultats()
        logger.debug("_fin_execution: fait")
        return False

    # ══════════════════════════════════════════════════════════════════
    # Phase 3 : Résultats
    # ══════════════════════════════════════════════════════════════════

    def _construire_resultats(self) -> Gtk.Widget:
        """Page de résultats : résumé, détail par hôte, journal affiché."""
        logger.debug("_construire_resultats: entrée")
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self._etiquette_resume = Gtk.Label(xalign=0, wrap=True)
        page.append(self._etiquette_resume)

        ensemble = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._vue_resultats = VueListe(
            [
                (_("Ligne"), lambda r: str(r.row.line_no), False),
                (_("IP / hôte"), lambda r: r.row.ip, False),
                (_("Profil"), lambda r: r.row.profile_name, False),
                (_("Statut"), lambda r: _libelle_statut(r.status), False),
                (_("Détail"), lambda r: r.error, True),
            ],
            hauteur=220,
            sur_selection=self._on_resultat_choisi,
        )
        ensemble.append(self._vue_resultats)

        self._tampon_journal = Gtk.TextBuffer()
        vue_journal = Gtk.TextView(buffer=self._tampon_journal, editable=False, monospace=True)
        vue_journal.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        defilement = Gtk.ScrolledWindow(vexpand=True, has_frame=True)
        defilement.set_child(vue_journal)
        ensemble.append(defilement)
        page.append(ensemble)

        ligne_boutons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bouton_dossier = Gtk.Button(label=_("Ouvrir le dossier des journaux"))
        bouton_dossier.connect("clicked", self._on_ouvrir_journaux)
        ligne_boutons.append(bouton_dossier)
        bouton_nouveau = Gtk.Button(label=_("← Nouveau déploiement"))
        bouton_nouveau.connect("clicked", lambda _b: self._stack.set_visible_child_name("config"))
        ligne_boutons.append(bouton_nouveau)
        page.append(ligne_boutons)

        logger.debug("_construire_resultats: sortie")
        return page

    def _peupler_resultats(self) -> None:
        """Remplit la page de résultats depuis le dernier envoi."""
        logger.debug("_peupler_resultats: {} résultat(s)", len(self._resultats))
        resultats = sorted(self._resultats.values(), key=lambda r: r.row.line_no)
        resume = core.summarize_results(resultats)
        self._etiquette_resume.set_label(
            _(
                "Total : {total}  |  OK : {ok}  |  Rejetés : {rejected}"
                "  |  Échecs : {failed}  |  Ignorés : {skipped}"
            ).format(**resume)
        )
        self._vue_resultats.remplir(list(resultats))
        logger.debug("_peupler_resultats: fait")

    def _on_resultat_choisi(self, resultat: Any) -> None:
        """Résultat cliqué : son fichier de journal s'affiche à droite."""
        logger.debug("_on_resultat_choisi: ligne {}", resultat.row.line_no)
        chemin = getattr(resultat, "log_path", None)
        if not chemin:
            self._tampon_journal.set_text("")
            logger.debug("_on_resultat_choisi: sans journal")
            return
        try:
            contenu = Path(chemin).read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("snmp push : journal {} illisible : {}", chemin, exc)
            contenu = _("Impossible de lire le journal : {e}").format(e=exc)
        self._tampon_journal.set_text(contenu)
        logger.debug("_on_resultat_choisi: fait")

    def _on_ouvrir_journaux(self, _bouton: Gtk.Button) -> None:
        """Ouvre le dossier des journaux dans le gestionnaire de fichiers."""
        logger.debug("_on_ouvrir_journaux: entrée")
        if self._log_dir is None:
            logger.debug("_on_ouvrir_journaux: aucun dossier, ignoré")
            return
        parent = self.get_root()
        fenetre = parent if isinstance(parent, Gtk.Window) else None
        _ouvrir_dossier(fenetre, str(self._log_dir))
        logger.debug("_on_ouvrir_journaux: fait")


def _ouvrir_dossier(parent: Gtk.Window | None, dossier: str) -> None:
    """Ouvre ``dossier`` dans le gestionnaire de fichiers (asynchrone).

    Même schéma que netmiko_push : ``Gtk.FileLauncher`` (GTK ≥ 4.10),
    sinon repli ``Gio.AppInfo.launch_default_for_uri`` (l'ancien
    ``subprocess.Popen(['xdg-open', …])`` du GTK3, qui dépendait d'un
    binaire externe).
    """
    logger.debug("_ouvrir_dossier: {}", dossier)
    fichier = Gio.File.new_for_path(dossier)
    if hasattr(Gtk, "FileLauncher"):

        def fin(lanceur: Gtk.FileLauncher, resultat: Gio.AsyncResult) -> None:
            logger.debug("_ouvrir_dossier.fin: entrée")
            try:
                lanceur.launch_finish(resultat)
            except GLib.Error as exc:
                logger.warning("_ouvrir_dossier: {} ({})", dossier, exc.message)
            logger.debug("_ouvrir_dossier.fin: fait")

        Gtk.FileLauncher.new(fichier).launch(parent, None, fin)
    else:  # GTK < 4.10
        Gio.AppInfo.launch_default_for_uri(fichier.get_uri(), None)
    logger.debug("_ouvrir_dossier: demandé")


class SnmpPushPlugin(ToolPlugin):
    """« Déploiement SNMP » : onglet à trois phases de déploiement en masse."""

    name = "snmp-push"
    version = "1.0"
    menu_label = N_("Déploiement SNMP…")
    menu_section = SECTION_OUTILS
    icon_name = "network-wired-symbolic"

    def activate(self) -> None:
        """Ouvre l'onglet Push SNMP (fichiers et profils à choisir)."""
        logger.debug("SnmpPushPlugin.activate: entrée")
        self.context.open_tab(_("Push SNMP"), OngletSnmp(self.context))
        logger.debug("SnmpPushPlugin.activate: fait")


PLUGIN = SnmpPushPlugin


def _resume_variables(ligne: Any) -> str:
    """Résumé des variables supplémentaires d'une ligne (« clef=valeur, … »)."""
    logger.trace("_resume_variables")
    return ", ".join(f"{clef}={valeur}" for clef, valeur in ligne.variables.items())
