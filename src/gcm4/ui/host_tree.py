"""Arbre des groupes et des hôtes du panneau gauche (#139).

``Gtk.TreeListModel`` + ``Gtk.ListView`` + ``Gtk.TreeExpander`` (fin de
``Gtk.TreeView``, déprécié en GTK 4.10). Une seule source de vérité : le
``HostStore`` du lot 2 ; la structure vient de
:func:`gcm4.core.host_tree.construire`.

- Couleurs de groupe : classes ``gcm-couleur-rrggbb`` sur le libellé
  (feuille chargée par ``Gcm4Application.set_group_colors``).
- Rayures : classe ``gcm-rayures`` sur la liste, sauf si
  ``disable-hosts-stripes`` (``style.css``).
- Groupes repliés : ``Group.collapsed`` suit l'état des lignes ; il est
  enregistré au format GTK3 par la fenêtre (:mod:`gcm4.core.replies`).
- Recherche (#140) : ``Gtk.SearchEntry`` (Ctrl+F, Échap efface, frappe
  dans la liste capturée) ; un ``Gtk.FilterListModel`` par niveau, filtre
  commun calculé par :func:`gcm4.core.host_tree.correspondances`. Pendant la
  recherche, tout est déplié sans toucher à ``Group.collapsed`` ; à
  l'effacement, l'arbre est reconstruit avec l'état replié d'origine et la
  sélection est conservée si elle reste visible.
- Glisser-déposer (#141) : ``Gtk.DragSource`` / ``Gtk.DropTarget`` par
  ligne, plus une cible sur la liste pour la zone vide (racine). Règles et
  application dans :mod:`gcm4.core.deplacements` ; enregistrement immédiat
  par ``enregistrer`` (fourni par la fenêtre). En cas d'échec d'écriture, le
  déplacement est annulé et ``notifier`` affiche la raison.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gdk, Gio, GObject, Gtk
from loguru import logger

from gcm4.core import deplacements
from gcm4.core.config import ConfigError
from gcm4.core.host_tree import Noeud, ancetres, construire, correspondances, infobulle
from gcm4.core.i18n import _
from gcm4.core.model import SEPARATEUR, HostStore
from gcm4.ui.menu_arbre import MenuArbre

ICONE_GROUPE = "folder-symbolic"
# network-workstation-symbolic est absente d'adwaita-icon-theme 50 (icône
# cassée) ; les plugins fourniront la leur (lot 6). tests/gtk4/test_arbre.py
# vérifie que ces icônes existent dans le thème.
ICONE_HOTE = "network-server-symbolic"


class ElementArbre(GObject.Object):
    """Élément du modèle : enveloppe GObject d'un :class:`~gcm4.core.host_tree.Noeud`."""

    __gtype_name__ = "Gcm4ElementArbre"

    def __init__(self, noeud: Noeud) -> None:
        """Enveloppe ``noeud``."""
        logger.trace("ElementArbre.__init__: {!r}", noeud.chemin)
        super().__init__()
        self.noeud = noeud
        logger.trace("ElementArbre.__init__: fait")


def _liste(noeuds: list[Noeud]) -> Gio.ListStore:
    """``Gio.ListStore`` d'``ElementArbre``."""
    logger.trace("_liste: {}", len(noeuds))
    liste = Gio.ListStore(item_type=ElementArbre)
    for noeud in noeuds:
        liste.append(ElementArbre(noeud))
    logger.trace("_liste: fait")
    return liste


class HostTree(Gtk.Box):
    """Panneau gauche : arbre des groupes et des hôtes."""

    __gtype_name__ = "Gcm4HostTree"

    def __init__(
        self,
        store: HostStore,
        rayures: bool = True,
        enregistrer: Callable[[], None] | None = None,
        notifier: Callable[[str], None] | None = None,
    ) -> None:
        """Arbre de ``store``.

        Args:
            store: hôtes et groupes (lot 2).
            rayures: alternance de fond des lignes.
            enregistrer: écrit la configuration après une modification ;
                lève ``ConfigError`` en cas d'échec.
            notifier: affiche un message court à l'utilisateur.
        """
        logger.debug("HostTree.__init__: rayures={}", rayures)
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.store = store
        self.enregistrer = enregistrer
        self.notifier = notifier
        self.garder: set[str] | None = None  # None : pas de recherche en cours
        self.filtre = Gtk.CustomFilter.new(self._garder)
        self.recherche = Gtk.SearchEntry(
            placeholder_text=_("Search hosts"), margin_start=6, margin_end=6
        )
        self.recherche.set_margin_top(6)
        self.recherche.set_margin_bottom(6)
        self.recherche.update_property([Gtk.AccessibleProperty.LABEL], [_("Search hosts")])
        self.recherche.connect("search-changed", self._on_recherche)
        self.recherche.connect("stop-search", self._on_stop)
        self.append(self.recherche)
        self.aucun_resultat = Gtk.Label(label=_("No matching hosts"), vexpand=True, visible=False)
        self.aucun_resultat.add_css_class("dim-label")
        fabrique = Gtk.SignalListItemFactory()
        fabrique.connect("setup", self._on_setup)
        fabrique.connect("bind", self._on_bind)
        fabrique.connect("unbind", self._on_unbind)
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.liste = Gtk.ListView(model=self.selection, factory=fabrique)
        self.liste.add_css_class("gcm-arbre")
        self.liste.add_css_class("navigation-sidebar")
        self.set_rayures(rayures)
        self.liste.update_property([Gtk.AccessibleProperty.LABEL], [_("Hosts")])
        self.defilement = Gtk.ScrolledWindow(
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.liste
        )
        self.append(self.defilement)
        self.append(self.aucun_resultat)
        self.recherche.set_key_capture_widget(self.liste)
        # Zone vide sous les lignes : dépôt à la racine.
        cible_vide = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        cible_vide.set_preload(True)
        cible_vide.connect("motion", self._on_drop_motion, None)
        cible_vide.connect("drop", self._on_drop, None)
        self.liste.add_controller(cible_vide)
        self.modele: Gtk.TreeListModel | None = None
        self._connexions: list[tuple[Gtk.TreeListRow, int]] = []
        self._selection_memo: str | None = None
        self.recharger()
        self.menu = MenuArbre(self)
        logger.debug("HostTree.__init__: prêt")

    # --- modèle ------------------------------------------------------------

    def recharger(self) -> None:
        """Reconstruit l'arbre depuis le ``HostStore`` et restaure les groupes repliés."""
        logger.debug("recharger: entrée")
        # Les lignes de l'ancien modèle, détruites, notifieraient « replié » :
        # leurs signaux sont coupés avant le remplacement.
        for ancienne, identifiant in self._connexions:
            ancienne.disconnect(identifiant)
        self._connexions = []
        racine = Gtk.FilterListModel(model=_liste(construire(self.store)), filter=self.filtre)
        self.modele = Gtk.TreeListModel.new(racine, False, False, self._enfants)
        self.selection.set_model(self.modele)
        self._deplier(None)
        if self.garder is not None:
            # Recherche en cours : chemins recalculés sur le store modifié.
            self.garder = correspondances(self.store, self.recherche.get_text())
            self.filtre.changed(Gtk.FilterChange.DIFFERENT)
            self._tout_deplier()
        logger.debug("recharger: {} ligne(s) visibles", self.modele.get_n_items())

    def _enfants(self, element: ElementArbre) -> Gio.ListModel | None:
        """Enfants d'un groupe (``None`` pour un hôte ou un groupe vide : pas de flèche)."""
        logger.trace("_enfants: {!r}", element.noeud.chemin)
        noeud = element.noeud
        resultat = (
            Gtk.FilterListModel(model=_liste(noeud.enfants), filter=self.filtre)
            if noeud.est_groupe and noeud.enfants
            else None
        )
        logger.trace("_enfants: {}", resultat is not None)
        return resultat

    def _deplier(self, ligne: Gtk.TreeListRow | None) -> None:
        """Déplie les groupes non repliés sous ``ligne`` (``None`` : racine)."""
        logger.trace("_deplier: entrée")
        assert self.modele is not None
        if ligne is None:
            enfants = [
                self.modele.get_child_row(i) for i in range(self.modele.get_model().get_n_items())
            ]
        else:
            enfants = [ligne.get_child_row(i) for i in range(ligne.get_children().get_n_items())]
        for enfant in enfants:
            if enfant is None:
                continue
            self._connexions.append(
                (enfant, enfant.connect("notify::expanded", self._on_expanded))
            )
            noeud = enfant.get_item().noeud
            groupe = self.store.groups.get(noeud.chemin) if noeud.est_groupe else None
            if groupe is not None and enfant.is_expandable() and not groupe.collapsed:
                enfant.set_expanded(True)
        logger.trace("_deplier: {} enfant(s)", len(enfants))

    def _on_expanded(self, ligne: Gtk.TreeListRow, _pspec: GObject.ParamSpec) -> None:
        """Ligne dépliée ou repliée : ``Group.collapsed`` mis à jour ; enfants restaurés."""
        logger.debug("_on_expanded: entrée")
        noeud = ligne.get_item().noeud
        if self.garder is not None:
            # Recherche : dépliage temporaire (_tout_deplier), Group.collapsed intact.
            logger.debug("_on_expanded: recherche en cours, état conservé")
            return
        parent = ligne.get_parent()
        # Ligne détachée : GTK remet profondeur et parent à zéro avant de
        # notifier ; un sous-groupe (« a/b ») sans parent est donc en cours de
        # destruction.
        detachee = SEPARATEUR in noeud.chemin and parent is None
        if not ligne.get_expanded() and (
            detachee or (parent is not None and not parent.get_expanded())
        ):
            # Ligne détruite parce que son parent se replie : l'état propre du
            # sous-groupe est conservé et sera restauré au prochain dépliage.
            logger.debug("_on_expanded: {!r} masqué par son parent, état conservé", noeud.chemin)
            return
        groupe = self.store.groups.get(noeud.chemin)
        if groupe is not None:
            groupe.collapsed = not ligne.get_expanded()
        if ligne.get_expanded():
            self._deplier(ligne)
        logger.debug("_on_expanded: {!r} replié={}", noeud.chemin, not ligne.get_expanded())

    # --- recherche (#140) ---------------------------------------------------

    def _garder(self, element: ElementArbre) -> bool:
        """Filtre commun : tout sans recherche, sinon les chemins de ``correspondances``."""
        logger.trace("_garder: {!r}", element.noeud.chemin)
        resultat = self.garder is None or element.noeud.chemin in self.garder
        logger.trace("_garder: {}", resultat)
        return resultat

    def chercher(self, texte: str) -> None:
        """Filtre l'arbre sur ``texte`` (vide : arbre complet, état replié restauré)."""
        logger.debug("chercher: {!r}", texte)
        avant = self.garder
        # Dernière sélection connue : un filtre peut la masquer un temps.
        self._selection_memo = self.chemin_selectionne() or self._selection_memo
        self.garder = correspondances(self.store, texte)
        if self.garder is None:
            if avant is not None:
                self.recharger()
                self.selectionner(self._selection_memo)
            self._selection_memo = None
        else:
            self.filtre.changed(Gtk.FilterChange.DIFFERENT)
            self._tout_deplier()
        vide = self.garder is not None and not self.garder
        self.aucun_resultat.set_visible(vide)
        self.defilement.set_visible(not vide)
        logger.debug(
            "chercher: {} chemin(s) gardé(s)", "tous" if self.garder is None else len(self.garder)
        )

    def _tout_deplier(self) -> None:
        """Déplie chaque groupe visible (recherche en cours)."""
        logger.debug("_tout_deplier: entrée")
        assert self.modele is not None
        i = 0
        while i < self.modele.get_n_items():  # le dépliage insère les enfants après i
            ligne = self.modele.get_item(i)
            if ligne.is_expandable() and not ligne.get_expanded():
                ligne.set_expanded(True)
            i += 1
        logger.debug("_tout_deplier: {} ligne(s)", i)

    def _on_recherche(self, entree: Gtk.SearchEntry) -> None:
        """Texte modifié (signal temporisé de ``Gtk.SearchEntry``)."""
        logger.debug("_on_recherche: entrée")
        self.chercher(entree.get_text())
        logger.debug("_on_recherche: fait")

    def _on_stop(self, entree: Gtk.SearchEntry) -> None:
        """Échap : efface la recherche et rend la main à la liste."""
        logger.debug("_on_stop: entrée")
        entree.set_text("")
        self.chercher("")
        self.liste.grab_focus()
        logger.debug("_on_stop: fait")

    def focus_recherche(self) -> None:
        """Ctrl+F : place le curseur dans la recherche, texte sélectionné."""
        logger.debug("focus_recherche: entrée")
        self.recherche.grab_focus()
        self.recherche.select_region(0, -1)
        logger.debug("focus_recherche: fait")

    def set_rayures(self, rayures: bool) -> None:
        """Active ou retire l'alternance de fond des lignes (option immédiate, #247)."""
        logger.debug("set_rayures: {}", rayures)
        if rayures:
            self.liste.add_css_class("gcm-rayures")
        else:
            self.liste.remove_css_class("gcm-rayures")
        logger.debug("set_rayures: fait")

    # --- sélection -----------------------------------------------------------

    def chemin_selectionne(self) -> str | None:
        """Chemin de la ligne sélectionnée, ou ``None``."""
        logger.debug("chemin_selectionne: entrée")
        ligne = self.selection.get_selected_item()
        resultat = ligne.get_item().noeud.chemin if ligne is not None else None
        logger.debug("chemin_selectionne: {!r}", resultat)
        return resultat

    def selectionner(self, chemin: str | None) -> bool:
        """Sélectionne la ligne visible ``chemin`` ; ``False`` si elle n'est pas visible."""
        logger.debug("selectionner: {!r}", chemin)
        assert self.modele is not None
        for i in range(self.modele.get_n_items()):
            if chemin is not None and self.modele.get_item(i).get_item().noeud.chemin == chemin:
                self.selection.set_selected(i)
                logger.debug("selectionner: ligne {}", i)
                return True
        logger.debug("selectionner: non visible")
        return False

    def reveler(self, chemin: str) -> bool:
        """Déplie les groupes parents de ``chemin`` puis le sélectionne (``gcm4 groupe/nom``, #154).

        Les groupes dépliés le restent (``Group.collapsed`` mis à jour comme
        pour un clic sur la flèche).

        Returns:
            bool: ``True`` si la ligne est sélectionnée.
        """
        logger.debug("reveler: {!r}", chemin)
        for groupe in ancetres(chemin):
            ligne = self.ligne(groupe)
            if ligne is not None and ligne.is_expandable() and not ligne.get_expanded():
                ligne.set_expanded(True)
        resultat = self.selectionner(chemin)
        logger.debug("reveler: {}", resultat)
        return resultat

    # --- glisser-déposer (#141) ---------------------------------------------

    @staticmethod
    def _cle_ligne(item: Gtk.ListItem | None) -> str | None:
        """Clé ``h:``/``g:`` de la ligne affichée par ``item`` (``None`` : zone vide ou ligne vide)."""
        logger.trace("_cle_ligne")
        ligne = item.get_item() if item is not None else None
        if ligne is None:
            logger.trace("_cle_ligne: aucune")
            return None
        noeud = ligne.get_item().noeud
        resultat = deplacements.cle(noeud.chemin, noeud.est_groupe)
        logger.trace("_cle_ligne: {!r}", resultat)
        return resultat

    def _on_drag_prepare(
        self, _source: Gtk.DragSource, _x: float, _y: float, item: Gtk.ListItem
    ) -> Gdk.ContentProvider | None:
        """Début de glissement : la clé de la ligne est le contenu transporté."""
        logger.debug("_on_drag_prepare: entrée")
        cle = self._cle_ligne(item)
        resultat = Gdk.ContentProvider.new_for_value(cle) if cle is not None else None
        logger.debug("_on_drag_prepare: {!r}", cle)
        return resultat

    def _on_drag_begin(self, source: Gtk.DragSource, _drag: Gdk.Drag, item: Gtk.ListItem) -> None:
        """Icône de glissement : la ligne elle-même."""
        logger.debug("_on_drag_begin: entrée")
        source.set_icon(Gtk.WidgetPaintable.new(item.get_child()), 0, 0)
        logger.debug("_on_drag_begin: fait")

    def _on_drop_motion(
        self, cible: Gtk.DropTarget, _x: float, _y: float, item: Gtk.ListItem | None
    ) -> Gdk.DragAction:
        """Survol : MOVE si le dépôt est valide, sinon aucune action (curseur de refus)."""
        logger.trace("_on_drop_motion")
        valeur = cible.get_value()
        source = valeur if isinstance(valeur, str) else None
        ok = source is not None and isinstance(
            deplacements.valider(self.store, source, self._cle_ligne(item)),
            deplacements.Deplacement,
        )
        logger.trace("_on_drop_motion: {}", ok)
        return Gdk.DragAction.MOVE if ok else Gdk.DragAction(0)

    def _on_drop(
        self,
        _cible: Gtk.DropTarget,
        valeur: object,
        _x: float,
        _y: float,
        item: Gtk.ListItem | None,
    ) -> bool:
        """Dépôt sur une ligne (``item``) ou sur la zone vide (``None``)."""
        logger.debug("_on_drop: entrée")
        resultat = isinstance(valeur, str) and self.deposer(valeur, self._cle_ligne(item))
        logger.debug("_on_drop: {}", resultat)
        return resultat

    def deposer(self, source: str, cible: str | None) -> bool:
        """Dépose ``source`` (clé ``h:``/``g:``) sur ``cible`` (clé, ``None`` : racine).

        Valide, applique, enregistre ; annule si l'écriture échoue. La
        destination est dépliée et l'élément déplacé sélectionné.
        """
        logger.debug("deposer: {!r} -> {!r}", source, cible)
        deplacement = deplacements.valider(self.store, source, cible)
        if isinstance(deplacement, deplacements.Refus):
            if deplacement is deplacements.Refus.NOM_PRIS:
                self._notifier(
                    _("An item with this name already exists in '{group}'.").format(
                        group=deplacements.groupe_cible(self.store, cible) or "/"
                    )
                )
            logger.debug("deposer: refus ({})", deplacement.value)
            return False
        inverse = deplacements.appliquer(self.store, deplacement)
        try:
            if self.enregistrer is not None:
                self.enregistrer()
        except ConfigError as exc:
            # Rien n'a été écrit : le modèle revient à l'état du fichier.
            logger.warning("deposer: enregistrement impossible ({}), déplacement annulé", exc)
            deplacements.appliquer(self.store, inverse)
            self.recharger()
            self._notifier(_("Move not saved: {error}").format(error=exc))
            logger.debug("deposer: annulé")
            return False
        for chemin in [deplacement.destination, *ancetres(deplacement.destination)]:
            groupe = self.store.groups.get(chemin)
            if groupe is not None:
                groupe.collapsed = False
        self.recharger()
        self.selectionner(deplacement.nouveau_chemin)
        logger.debug("deposer: {!r} -> {!r}", deplacement.chemin, deplacement.nouveau_chemin)
        return True

    def appliquer_couleurs(self) -> None:
        """Recharge la feuille des couleurs de groupe de l'application (après un changement)."""
        logger.debug("appliquer_couleurs: entrée")
        racine = self.get_root()
        application = racine.get_application() if isinstance(racine, Gtk.Window) else None
        if application is not None and hasattr(application, "set_group_colors"):
            application.set_group_colors([g.color for g in self.store.groups.values() if g.color])
        logger.debug("appliquer_couleurs: fait")

    def _notifier(self, message: str) -> None:
        """Message court (toast de la fenêtre), journalisé dans tous les cas."""
        logger.debug("_notifier: {}", message)
        if self.notifier is not None:
            self.notifier(message)
        logger.debug("_notifier: fait")

    # --- lignes ------------------------------------------------------------

    def _on_setup(self, _fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        """Widgets d'une ligne : flèche, icône, libellé."""
        logger.trace("_on_setup")
        boite = Gtk.Box(spacing=6)
        boite.append(Gtk.Image())
        boite.append(Gtk.Label(xalign=0, ellipsize=3))  # Pango.EllipsizeMode.END
        fleche = Gtk.TreeExpander(child=boite)
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, item)
        source.connect("drag-begin", self._on_drag_begin, item)
        fleche.add_controller(source)
        cible = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        cible.set_preload(True)  # valeur disponible dès « motion » : refus visible avant le dépôt
        cible.connect("motion", self._on_drop_motion, item)
        cible.connect("drop", self._on_drop, item)
        fleche.add_controller(cible)
        item.set_child(fleche)
        logger.trace("_on_setup: fait")

    def _on_bind(self, _fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        """Remplit une ligne avec son nœud (texte, icône, couleur, info-bulle)."""
        logger.trace("_on_bind")
        ligne: Gtk.TreeListRow = item.get_item()
        noeud: Noeud = ligne.get_item().noeud
        fleche: Gtk.TreeExpander = item.get_child()
        fleche.set_list_row(ligne)
        boite = fleche.get_child()
        icone, libelle = boite.get_first_child(), boite.get_last_child()
        icone.set_from_icon_name(ICONE_GROUPE if noeud.est_groupe else ICONE_HOTE)
        libelle.set_text(noeud.nom)
        classe = noeud.classe_css
        if classe:
            libelle.add_css_class(classe)
        libelle.gcm_classe = classe  # retirée à l'unbind (widgets recyclés)
        if noeud.host is not None:
            boite.set_tooltip_markup(infobulle(noeud.host))
        else:
            boite.set_tooltip_markup(None)
        logger.trace("_on_bind: {!r}", noeud.chemin)

    def _on_unbind(self, _fabrique: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        """Retire la classe de couleur avant recyclage du widget."""
        logger.trace("_on_unbind")
        libelle = item.get_child().get_child().get_last_child()
        classe = getattr(libelle, "gcm_classe", None)
        if classe:
            libelle.remove_css_class(classe)
        libelle.gcm_classe = None
        logger.trace("_on_unbind: fait")

    # --- consultation (tests, lots suivants) --------------------------------

    def lignes_visibles(self) -> list[tuple[int, str]]:
        """``(profondeur, chemin)`` des lignes visibles, dans l'ordre d'affichage."""
        logger.debug("lignes_visibles: entrée")
        assert self.modele is not None
        resultat = []
        for i in range(self.modele.get_n_items()):
            ligne = self.modele.get_item(i)
            resultat.append((ligne.get_depth(), ligne.get_item().noeud.chemin))
        logger.debug("lignes_visibles: {}", len(resultat))
        return resultat

    def ligne(self, chemin: str) -> Gtk.TreeListRow | None:
        """Ligne visible de ``chemin``, ou ``None``."""
        logger.debug("ligne: {!r}", chemin)
        assert self.modele is not None
        for i in range(self.modele.get_n_items()):
            ligne = self.modele.get_item(i)
            if ligne.get_item().noeud.chemin == chemin:
                logger.debug("ligne: trouvée")
                return ligne
        logger.debug("ligne: absente")
        return None
