# SPDX-License-Identifier: GPL-3.0-or-later
"""Icône de la zone de notification de gcm4 (#83) : StatusNotifierItem sur D-Bus.

GTK 4 n'a plus de ``Gtk.StatusIcon``. L'icône est publiée selon la
spécification freedesktop StatusNotifierItem, que lisent KDE Plasma, XFCE,
Cinnamon, MATE, LXQt et GNOME avec l'extension AppIndicator. Le menu suit le
protocole ``com.canonical.dbusmenu``. Le tout passe par ``Gio.DBusConnection``,
sans aucune nouvelle dépendance (option 1 retenue le 28/10).

- Préférence ``tray-icon`` (``AppConfig.tray_icon``), **désactivée par défaut**.
- Sans hôte SNI (``org.kde.StatusNotifierWatcher`` absent du bus), rien ne
  s'affiche et rien n'échoue ; l'icône est enregistrée dès qu'il apparaît.
- Clic : affiche ou masque la fenêtre principale. Menu : « Afficher la
  fenêtre » / « Masquer la fenêtre », « Quitter ».
"""

from __future__ import annotations

import os
from collections.abc import Callable

from gi.repository import Gio, GLib
from loguru import logger

from gcm4.core.i18n import _

WATCHER = "org.kde.StatusNotifierWatcher"
CHEMIN_WATCHER = "/StatusNotifierWatcher"
CHEMIN_ICONE = "/StatusNotifierItem"
CHEMIN_MENU = "/MenuBar"
INTERFACE_ICONE = "org.kde.StatusNotifierItem"
INTERFACE_MENU = "com.canonical.dbusmenu"

#: Identifiants des entrées du menu (0 : racine dbusmenu).
BASCULER = 1
SEPARATEUR = 2
QUITTER = 3

INTROSPECTION = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate">
      <arg type="i" direction="in"/><arg type="i" direction="in"/>
    </method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in"/><arg type="as" direction="in"/>
      <arg type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/>
      <arg type="v" direction="in"/><arg type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/>
    </method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})"/><arg type="a(ias)"/>
    </signal>
  </interface>
</node>
"""


class IconeNotification:
    """Icône StatusNotifierItem et son menu dbusmenu, publiés sur ``connexion``.

    Args:
        connexion (Gio.DBusConnection): Bus de session.
        nom_icone (str): Nom d'icône du thème (``APPLICATION_ID``).
        dossier_icones (str): Dossier ajouté au thème par l'hôte SNI (icônes
            du dépôt depuis les sources) ; vide : thème du système seul.
        visible (Callable[[], bool]): La fenêtre principale est-elle affichée ?
        basculer (Callable[[], None]): Affiche ou masque la fenêtre.
        quitter (Callable[[], None]): Quitte l'application (``app.quit``).
    """

    def __init__(
        self,
        connexion: Gio.DBusConnection,
        nom_icone: str,
        dossier_icones: str,
        visible: Callable[[], bool],
        basculer: Callable[[], None],
        quitter: Callable[[], None],
    ) -> None:
        """Prépare l'icône ; rien n'est publié avant :meth:`demarrer`."""
        logger.debug("IconeNotification.__init__: entrée ({})", nom_icone)
        self.connexion = connexion
        self.nom_icone = nom_icone
        self.dossier_icones = dossier_icones
        self.visible = visible
        self.basculer = basculer
        self.quitter = quitter
        self.nom_bus = f"org.kde.StatusNotifierItem-{os.getpid()}-{id(self) & 0xFFFF}"
        self.revision = 1
        self.enregistree = False  # acceptée par le StatusNotifierWatcher
        self._objets: list[int] = []
        self._nom: int = 0
        self._veille: int = 0
        logger.debug("IconeNotification.__init__: prête ({})", self.nom_bus)

    # Cycle de vie -------------------------------------------------------------

    def demarrer(self) -> None:
        """Publie les objets, prend le nom de bus et guette le StatusNotifierWatcher."""
        logger.debug("demarrer: entrée")
        if self._objets:
            logger.debug("demarrer: déjà démarrée")
            return
        noeud = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        for chemin, interface in ((CHEMIN_ICONE, INTERFACE_ICONE), (CHEMIN_MENU, INTERFACE_MENU)):
            self._objets.append(
                self.connexion.register_object(
                    chemin,
                    noeud.lookup_interface(interface),
                    self._on_appel,
                    self._on_propriete,
                    None,
                )
            )
        self._nom = Gio.bus_own_name_on_connection(
            self.connexion, self.nom_bus, Gio.BusNameOwnerFlags.NONE, None, None
        )
        self._veille = Gio.bus_watch_name_on_connection(
            self.connexion,
            WATCHER,
            Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_present,
            self._on_watcher_absent,
        )
        logger.debug("demarrer: publiée sous {}", self.nom_bus)

    def arreter(self) -> None:
        """Retire l'icône : objets, nom de bus et veille du StatusNotifierWatcher."""
        logger.debug("arreter: entrée")
        for objet in self._objets:
            self.connexion.unregister_object(objet)
        self._objets = []
        if self._veille:
            Gio.bus_unwatch_name(self._veille)
            self._veille = 0
        if self._nom:
            Gio.bus_unown_name(self._nom)
            self._nom = 0
        self.enregistree = False
        logger.debug("arreter: fait")

    def _on_watcher_present(
        self, connexion: Gio.DBusConnection, nom: str, proprietaire: str
    ) -> None:
        """Hôte SNI présent (ou revenu) : demande l'enregistrement de l'icône."""
        logger.debug("_on_watcher_present: {} ({})", nom, proprietaire)
        connexion.call(
            WATCHER,
            CHEMIN_WATCHER,
            WATCHER,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self.nom_bus,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_enregistrement,
        )
        logger.debug("_on_watcher_present: enregistrement demandé")

    def _on_enregistrement(self, connexion: Gio.DBusConnection, resultat: Gio.AsyncResult) -> None:
        """Réponse du StatusNotifierWatcher ; un refus est journalisé, sans plus."""
        logger.debug("_on_enregistrement: entrée")
        try:
            connexion.call_finish(resultat)
        except GLib.Error as exc:
            logger.warning("zone de notification : icône refusée par l'hôte SNI ({})", exc.message)
            self.enregistree = False
            logger.debug("_on_enregistrement: refusée")
            return
        self.enregistree = True
        logger.debug("_on_enregistrement: icône enregistrée")

    def _on_watcher_absent(self, _connexion: Gio.DBusConnection, nom: str) -> None:
        """Aucun hôte SNI : l'icône n'est affichée nulle part (rien d'autre à faire)."""
        logger.debug("_on_watcher_absent: {}", nom)
        self.enregistree = False
        logger.debug("_on_watcher_absent: en attente d'un hôte SNI")

    # Menu ----------------------------------------------------------------------

    def entrees(self) -> list[tuple[int, dict[str, GLib.Variant]]]:
        """Entrées du menu (identifiant, propriétés dbusmenu), libellés traduits."""
        logger.trace("entrees: entrée")
        libelle = _("Hide window") if self.visible() else _("Show window")
        resultat = [
            (BASCULER, {"label": GLib.Variant("s", libelle)}),
            (SEPARATEUR, {"type": GLib.Variant("s", "separator")}),
            (
                QUITTER,
                {
                    "label": GLib.Variant("s", _("Quit")),
                    "icon-name": GLib.Variant("s", "application-exit"),
                },
            ),
        ]
        logger.trace("entrees: {} entrée(s)", len(resultat))
        return resultat

    def disposition(self) -> GLib.Variant:
        """Arbre ``(ia{sv}av)`` de ``GetLayout`` : racine et entrées du menu."""
        logger.trace("disposition: révision {}", self.revision)
        enfants = [
            GLib.Variant("(ia{sv}av)", (ident, props, [])) for ident, props in self.entrees()
        ]
        racine = (0, {"children-display": GLib.Variant("s", "submenu")}, enfants)
        resultat = GLib.Variant("(u(ia{sv}av))", (self.revision, racine))
        logger.trace("disposition: fait")
        return resultat

    def rafraichir(self) -> None:
        """Libellé Afficher/Masquer changé : nouvelle révision signalée à l'hôte SNI."""
        logger.debug("rafraichir: entrée")
        self.revision += 1
        if self._objets:
            self.connexion.emit_signal(
                None,
                CHEMIN_MENU,
                INTERFACE_MENU,
                "LayoutUpdated",
                GLib.Variant("(ui)", (self.revision, 0)),
            )
        logger.debug("rafraichir: révision {}", self.revision)

    def activer(self, ident: int) -> None:
        """Entrée ``ident`` du menu choisie (ou clic sur l'icône pour ``BASCULER``)."""
        logger.debug("activer: {}", ident)
        if ident == BASCULER:
            self.basculer()
            self.rafraichir()
        elif ident == QUITTER:
            self.quitter()
        else:
            logger.debug("activer: entrée {} sans action", ident)
        logger.debug("activer: fait")

    # D-Bus ---------------------------------------------------------------------

    def _on_appel(
        self,
        _connexion: Gio.DBusConnection,
        _expediteur: str,
        _chemin: str,
        interface: str,
        methode: str,
        parametres: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        """Méthodes de ``org.kde.StatusNotifierItem`` et de ``com.canonical.dbusmenu``."""
        logger.debug("_on_appel: {}.{}", interface, methode)
        args = parametres.unpack()
        reponse: GLib.Variant | None = None
        if methode == "Activate":
            self.activer(BASCULER)
        elif methode == "GetLayout":
            reponse = self.disposition()
        elif methode == "GetGroupProperties":
            demandes = set(args[0])
            props = [(i, p) for i, p in self.entrees() if not demandes or i in demandes]
            reponse = GLib.Variant("(a(ia{sv}))", (props,))
        elif methode == "GetProperty":
            props = dict(self.entrees()).get(args[0], {})
            if args[1] not in props:
                invocation.return_dbus_error(
                    "com.canonical.dbusmenu.Error.UnknownProperty", f"{args[0]}:{args[1]}"
                )
                logger.debug("_on_appel: propriété inconnue {}", args)
                return
            reponse = GLib.Variant("(v)", (props[args[1]],))
        elif methode == "Event":
            if args[1] == "clicked":
                GLib.idle_add(self._activer_plus_tard, args[0])
        elif methode == "EventGroup":
            for ident, evenement, _donnees, _instant in args[0]:
                if evenement == "clicked":
                    GLib.idle_add(self._activer_plus_tard, ident)
            reponse = GLib.Variant("(ai)", ([],))
        elif methode == "AboutToShow":
            reponse = GLib.Variant("(b)", (False,))
        elif methode == "AboutToShowGroup":
            reponse = GLib.Variant("(aiai)", ([], []))
        # SecondaryActivate, ContextMenu, Scroll : sans effet (menu affiché par l'hôte).
        invocation.return_value(reponse)
        logger.debug("_on_appel: répondu")

    def _activer_plus_tard(self, ident: int) -> bool:
        """``Event`` reçu : l'action est faite après la réponse D-Bus (boucle GTK)."""
        logger.debug("_activer_plus_tard: {}", ident)
        self.activer(ident)
        logger.debug("_activer_plus_tard: fait")
        return GLib.SOURCE_REMOVE

    def _on_propriete(
        self,
        _connexion: Gio.DBusConnection,
        _expediteur: str,
        _chemin: str,
        interface: str,
        nom: str,
    ) -> GLib.Variant | None:
        """Propriétés des deux interfaces ; ``None`` pour une propriété inconnue."""
        logger.trace("_on_propriete: {}.{}", interface, nom)
        if interface == INTERFACE_MENU:
            valeurs = {
                "Version": GLib.Variant("u", 3),
                "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"),
                "IconThemePath": GLib.Variant(
                    "as", [self.dossier_icones] if self.dossier_icones else []
                ),
            }
        else:
            valeurs = {
                "Category": GLib.Variant("s", "ApplicationStatus"),
                "Id": GLib.Variant("s", "gcm4"),
                "Title": GLib.Variant("s", "gcm4"),
                "Status": GLib.Variant("s", "Active"),
                "WindowId": GLib.Variant("i", 0),
                "IconName": GLib.Variant("s", self.nom_icone),
                "IconThemePath": GLib.Variant("s", self.dossier_icones),
                "ItemIsMenu": GLib.Variant("b", False),
                "Menu": GLib.Variant("o", CHEMIN_MENU),
                "ToolTip": GLib.Variant("(sa(iiay)ss)", (self.nom_icone, [], "gcm4", "")),
            }
        resultat = valeurs.get(nom)
        logger.trace("_on_propriete: {}", "trouvée" if resultat is not None else "inconnue")
        return resultat
