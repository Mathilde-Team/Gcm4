# SPDX-License-Identifier: GPL-3.0-or-later
"""Interface GTK4 commune aux imports d'hyperviseurs (contrat v1, #439).

Partagée par ``plugins/import_libvirt``, ``import_proxmox``,
``import_virtualbox`` et ``import_ovirt`` : chaque plugin fournit sa page
de configuration (paramètres propres à l'hyperviseur), cette classe
fournit le reste du flux :

- le suivi du scan (journal + progression, thread-sûr via
  ``GLib.idle_add``) ;
- la prévisualisation des connexions découvertes en ``Gtk.ColumnView``
  avec cases à cocher (remplacement des ``Gtk.TreeView`` GTK3) ;
- la stratégie de conflit (les trois de l'import de fichiers, #153) et
  l'import via le service du contexte ``PluginContext.import_hosts``
  (#436), avec le bilan en toast.

L'ancien marquage « existe déjà » des versions GTK3 (couleur de ligne
déduite du magasin global) disparaît : le plugin n'a pas accès au
magasin, et la stratégie de conflit rend la même information (les
doublons sont ignorés, renommés ou remplacés, au choix).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Gio, GLib, GObject, Gtk, Pango  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.exchange import Conflit, dict_vers_hote
from gcm4.core.i18n import _  # noqa: E402
from gcm4.core.sauvegarde import bilan

#: Stratégies proposées, dans l'ordre affiché (la première par défaut) —
#: les mêmes que l'import de fichiers et l'import PuTTY (#153, #436).
STRATEGIES: tuple[Conflit, ...] = (Conflit.IGNORER, Conflit.RENOMMER, Conflit.REMPLACER)

#: État de la VM entre crochets dans la description (« [running] vm — … »).
_RE_ETAT = re.compile(r"\[([^\]]+)\]")


def _libelle(conflit: Conflit) -> str:
    """Libellé traduit d'une stratégie de conflit (mêmes msgid que #153)."""
    logger.trace("_libelle: {}", conflit.value)
    libelles = {
        Conflit.IGNORER: _("Keep the existing host"),
        Conflit.RENOMMER: _("Rename the imported host"),
        Conflit.REMPLACER: _("Replace the existing host"),
    }
    logger.trace("_libelle: fait")
    return libelles[conflit]


class Element(GObject.Object):
    """Valeur Python quelconque rangée dans un ``Gio.ListStore``, cochable."""

    def __init__(self, valeur: object, coche: bool = True) -> None:
        """Enveloppe ``valeur`` ; ``coche`` est l'état de la case à cocher."""
        logger.trace("Element.__init__")
        super().__init__()
        self.valeur = valeur
        self.coche = coche
        logger.trace("Element.__init__: sortie")


class VueCochable(Gtk.ScrolledWindow):
    """``Gtk.ColumnView`` à cases à cocher en première colonne.

    Remplace les ``Gtk.TreeView`` + ``Gtk.ListStore`` des versions GTK3 :
    les lignes sont des :class:`Element` (valeur Python arbitraire), la
    première colonne est une case à cocher, les suivantes du texte.
    """

    def __init__(
        self,
        colonnes: list[tuple[str, Callable[[object], str], bool]],
        hauteur: int = 160,
    ) -> None:
        """Construit la vue.

        Args:
            colonnes: ``(titre, texte de la cellule, extensible)`` par colonne
                texte (la colonne de cases à cocher est ajoutée devant).
            hauteur: Hauteur minimale du défilement, en pixels.
        """
        logger.debug("VueCochable.__init__: {} colonne(s) texte", len(colonnes))
        super().__init__(vexpand=True, has_frame=True)
        self.set_min_content_height(hauteur)
        self.modele = Gio.ListStore.new(Element)
        self.vue = Gtk.ColumnView(vexpand=True)
        self.vue.set_model(Gtk.NoSelection(model=self.modele))
        self.vue.append_column(self._colonne_coche())
        for titre, texte, extensible in colonnes:
            self.vue.append_column(self._colonne_texte(titre, texte, extensible))
        self.set_child(self.vue)
        logger.debug("VueCochable.__init__: sortie")

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

    def remplir(self, valeurs: list[object]) -> None:
        """Remplace le contenu par ``valeurs`` (toutes cochées)."""
        logger.debug("remplir: {} valeur(s)", len(valeurs))
        elements = [Element(v) for v in valeurs]
        self.modele.splice(0, self.modele.get_n_items(), elements)
        logger.debug("remplir: fait")

    def _elements(self) -> list[Element]:
        """Toutes les lignes du modèle, dans l'ordre."""
        return [self.modele.get_item(i) for i in range(self.modele.get_n_items())]

    def selectionnees(self) -> list[Any]:
        """Valeurs des lignes cochées, dans l'ordre."""
        logger.trace("selectionnees")
        return [e.valeur for e in self._elements() if e.coche]

    def tout_cocher(self, valeur: bool) -> None:
        """Coche ou décoche toutes les lignes (réaffiche les cases)."""
        logger.debug("tout_cocher: {}", valeur)
        elements = self._elements()
        for element in elements:
            element.coche = valeur
        # re-splice les mêmes éléments : « bind » refait, cases rafraîchies
        self.modele.splice(0, len(elements), elements)
        logger.debug("tout_cocher: fait")


class ZoneCibles(Gtk.Box):
    """Liste de cibles à cocher + saisie manuelle + bouton « Add ».

    Factorise la saisie des hyperviseurs des versions GTK3 : liste
    initiale (dconf pour libvirt, vide pour les autres), cases à cocher
    pour choisir lesquels scanner, ajout manuel d'une cible.
    """

    def __init__(
        self,
        log_fn: Callable[[str], None],
        normaliser: Callable[[str], str],
        message_invalide: str,
        titre_colonne: str = "",
        libelle_saisie: str = "",
        indice_saisie: str = "",
        message_ajoutee: str | None = None,
        message_deja_presente: str | None = None,
    ) -> None:
        """Construit la zone.

        Args:
            log_fn: Journal de l'onglet (messages d'ajout).
            normaliser: Saisie brute → cible normalisée ("" si invalide).
            message_invalide: Message quand la saisie est refusée.
            titre_colonne: Titre de la colonne des cibles.
            libelle_saisie: Libellé devant la saisie manuelle.
            indice_saisie: Texte indicatif de la saisie manuelle.
            message_ajoutee: Message d'ajout (défaut : celui de libvirt).
            message_deja_presente: Message de doublon (défaut : idem).
        """
        logger.debug("ZoneCibles.__init__: entrée")
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.log_fn = log_fn
        self.normaliser = normaliser
        self.message_invalide = message_invalide
        self.message_ajoutee = message_ajoutee or _("Target added: {uri}")
        self.message_deja_presente = message_deja_presente or _("Target already present: {uri}")

        self.vue = VueCochable([(titre_colonne, lambda v: str(v), True)], hauteur=140)

        ligne_saisie = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        if libelle_saisie:
            ligne_saisie.append(Gtk.Label(label=libelle_saisie))
        self.saisie = Gtk.Entry(hexpand=True)
        if indice_saisie:
            self.saisie.set_placeholder_text(indice_saisie)
        self.saisie.connect("activate", lambda *a: self.ajouter())
        bouton_ajout = Gtk.Button(label=_("Add"))
        bouton_ajout.connect("clicked", lambda *a: self.ajouter())
        ligne_saisie.append(self.saisie)
        ligne_saisie.append(bouton_ajout)

        self.append(self.vue)
        self.append(ligne_saisie)
        logger.debug("ZoneCibles.__init__: sortie")

    def ajouter(self) -> None:
        """Normalise la saisie et l'ajoute à la liste (doublons refusés)."""
        logger.debug("ZoneCibles.ajouter: entrée")
        cible = self.normaliser(self.saisie.get_text())
        if not cible:
            self.log_fn(self.message_invalide)
            logger.debug("ZoneCibles.ajouter: saisie invalide")
            return
        presentes = [str(e.valeur) for e in self.vue._elements()]
        if cible in presentes:
            self.log_fn(self.message_deja_presente.format(uri=cible))
            self.saisie.set_text("")
            logger.debug("ZoneCibles.ajouter: déjà présente")
            return
        self.vue.modele.append(Element(cible))
        self.log_fn(self.message_ajoutee.format(uri=cible))
        self.saisie.set_text("")
        logger.debug("ZoneCibles.ajouter: sortie")

    def cibles(self) -> list[str]:
        """Cibles cochées, dans l'ordre de la liste."""
        logger.trace("cibles")
        return [str(v) for v in self.vue.selectionnees()]

    def definir(self, cibles: list[str]) -> None:
        """Remplace la liste des cibles (toutes cochées)."""
        logger.debug("ZoneCibles.definir: {} cible(s)", len(cibles))
        self.vue.remplir(list(cibles))
        logger.debug("ZoneCibles.definir: fait")


class OngletImportHyperviseurs(Gtk.Box):
    """Onglet générique des imports d'hyperviseurs : config → suivi → résultats.

    La sous-classe construit sa page de configuration
    (:meth:`_construire_parametres`) et lance son scan via
    :meth:`lancer_scan` ; cette classe gère le thread de scan, le
    journal, la progression, la prévisualisation à cases à cocher, la
    stratégie de conflit et l'import final.
    """

    def __init__(self, context: Any, titre: str) -> None:
        """Construit l'onglet.

        Args:
            context: Contexte du plugin (``PluginContext``).
            titre: Nom de la source, pour le bilan d'import (ex. ``libvirt``).
        """
        logger.debug("OngletImportHyperviseurs.__init__: entrée | {}", titre)
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.context = context
        self.titre = titre
        #: Connexions découvertes par le dernier scan.
        self.decouvertes: list[dict] = []
        #: Bouton de scan de la sous-classe (réactivé à la fin du scan).
        self.bouton_scan: Gtk.Button | None = None

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        aiguillage = Gtk.StackSwitcher(stack=self._stack)
        aiguillage.set_halign(Gtk.Align.CENTER)
        self.append(aiguillage)
        self._stack.set_vexpand(True)
        self.append(self._stack)

        # ── Page 1 : configuration (fournie par la sous-classe) ────────────
        self._stack.add_titled(self._construire_parametres(), "config", _("1. Configuration"))

        # ── Page 2 : suivi (journal + progression) ─────────────────────────
        suivi = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._journal_tampon = Gtk.TextBuffer()
        vue_journal = Gtk.TextView(buffer=self._journal_tampon, editable=False, monospace=True)
        vue_journal.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        vue_journal.set_vexpand(True)
        self._progression = Gtk.ProgressBar(show_text=True)
        self._progression.set_text(_("Waiting…"))
        suivi.append(vue_journal)
        suivi.append(self._progression)
        self._stack.add_titled(suivi, "monitor", _("2. Suivi"))

        # ── Page 3 : résultats (prévisualisation + import) ────────────────
        resultats = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.etiquette_resume = Gtk.Label(xalign=0)
        resultats.append(self.etiquette_resume)

        self.vue = VueCochable(
            [
                (_("Proto"), lambda d: str(d.get("protocol", "")).upper(), False),
                (_("VM name"), lambda d: str(d.get("name", "")), True),
                (_("Group"), lambda d: str(d.get("group", "")), False),
                (_("IP / Host"), lambda d: str(d.get("host", "")), False),
                (_("State"), lambda d: str(d.get("state", "")), False),
            ],
            hauteur=260,
        )
        resultats.append(self.vue)

        ligne_boutons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bouton_tout = Gtk.Button(label=_("✓ Check all"))
        bouton_tout.connect("clicked", lambda *a: self.vue.tout_cocher(True))
        bouton_rien = Gtk.Button(label=_("✗ Uncheck all"))
        bouton_rien.connect("clicked", lambda *a: self.vue.tout_cocher(False))
        ligne_boutons.append(bouton_tout)
        ligne_boutons.append(bouton_rien)
        resultats.append(ligne_boutons)

        resultats.append(Gtk.Label(label=_("If a host already exists:"), xalign=0))
        self.boutons_conflit: dict[Conflit, Gtk.CheckButton] = {}
        premier: Gtk.CheckButton | None = None
        for conflit in STRATEGIES:
            bouton = Gtk.CheckButton(label=_libelle(conflit))
            if premier is None:
                premier = bouton
                bouton.set_active(True)
            else:
                bouton.set_group(premier)
            self.boutons_conflit[conflit] = bouton
            resultats.append(bouton)

        self.bouton_importer = Gtk.Button(label=_("⬇  Import selected"))
        self.bouton_importer.add_css_class("suggested-action")
        self.bouton_importer.set_sensitive(False)
        self.bouton_importer.connect("clicked", self._on_importer)
        resultats.append(self.bouton_importer)
        self._stack.add_titled(resultats, "results", _("3. Résultats"))

        self._stack.set_visible_child_name("config")
        logger.debug("OngletImportHyperviseurs.__init__: sortie")

    def _construire_parametres(self) -> Gtk.Widget:
        """Page de configuration — fournie par la sous-classe."""
        raise NotImplementedError

    def conflit_choisi(self) -> Conflit:
        """Stratégie cochée (``IGNORER`` par défaut)."""
        logger.trace("conflit_choisi")
        resultat = next(
            (c for c, b in self.boutons_conflit.items() if b.get_active()), Conflit.IGNORER
        )
        logger.trace("conflit_choisi: {}", resultat.value)
        return resultat

    def log(self, msg: str) -> None:
        """Ajoute ``msg`` au journal (thread-sûr : via ``GLib.idle_add``)."""
        logger.trace("log: {!r}", msg)

        def _ajouter() -> bool:
            logger.trace("_ajouter: entrée")
            tampon = self._journal_tampon
            tampon.insert(tampon.get_end_iter(), msg + "\n")
            logger.trace("_ajouter: sortie")
            return False

        GLib.idle_add(_ajouter)
        logger.trace("log: sortie")

    def progression(self, fraction: float, texte: str) -> None:
        """Met à jour la barre de progression (thread-sûr)."""
        logger.trace("progression: {}", fraction)

        def _maj() -> bool:
            logger.trace("_maj: entrée")
            self._progression.set_fraction(fraction)
            self._progression.set_text(texte)
            logger.trace("_maj: sortie")
            return False

        GLib.idle_add(_maj)
        logger.trace("progression: sortie")

    def lancer_scan(self, travail: Callable[[], list[dict]]) -> None:
        """Exécute ``travail`` dans un thread et affiche ses résultats.

        ``travail`` collecte les connexions (logique du ``core.py`` du
        plugin) ; le journal et la progression passent par
        :meth:`log`/:meth:`progression`, déjà thread-sûrs. Une exception
        est journalisée et notifiée, sans casser l'onglet.
        """
        logger.debug("lancer_scan: entrée")

        def _travailler() -> None:
            logger.debug("lancer_scan: travail démarré")
            try:
                resultats = travail()
            except Exception as exc:  # noqa: BLE001
                logger.exception("import {} : échec du scan", self.titre)
                self.log(_("Scan failed: {exc}").format(exc=exc))
                resultats = []
            GLib.idle_add(self.afficher_resultats, resultats)
            logger.debug("lancer_scan: travail terminé")

        threading.Thread(target=_travailler, daemon=True).start()
        logger.debug("lancer_scan: sortie")

    def afficher_resultats(self, decouvertes: list[dict]) -> None:
        """Affiche les connexions découvertes dans la prévisualisation."""
        logger.debug("afficher_resultats: {} connexion(s)", len(decouvertes))
        self.decouvertes = decouvertes
        for entree in decouvertes:
            m = _RE_ETAT.search(str(entree.get("description", "")))
            entree["state"] = m.group(1) if m else ""
        self.vue.remplir(list(decouvertes))
        self.etiquette_resume.set_label(
            _("Connections found: {count}.").format(count=len(decouvertes))
        )
        self.bouton_importer.set_sensitive(bool(decouvertes))
        if self.bouton_scan is not None:
            self.bouton_scan.set_sensitive(True)
            self.bouton_scan.set_label(_("🔄  Rescan"))
        self._stack.set_visible_child_name("results")
        logger.debug("afficher_resultats: fait")

    def _on_importer(self, _bouton: Gtk.Button) -> None:
        """Importe les connexions cochées selon la stratégie choisie."""
        logger.debug("_on_importer: entrée")
        choisies = self.vue.selectionnees()
        if not choisies:
            self.context.notify(_("No connection to import (all already exist or none selected)."))
            logger.debug("_on_importer: rien à importer")
            return
        try:
            hotes = [dict_vers_hote(d, ou=str(d.get("name", ""))) for d in choisies]
            rapport = self.context.import_hosts(hotes, self.conflit_choisi())
        except Exception as exc:  # noqa: BLE001
            logger.exception("import {} : échec de l'import", self.titre)
            self.context.notify(_("Scan failed: {exc}").format(exc=exc))
            logger.debug("_on_importer: sortie (échec)")
            return
        self.context.notify(bilan(self.titre, rapport))
        self.bouton_importer.set_sensitive(False)
        logger.debug("_on_importer: {} importée(s)", len(rapport.ajoutes))
        logger.debug("_on_importer: sortie")
