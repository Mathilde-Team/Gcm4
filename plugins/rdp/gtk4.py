# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin rdp de gcm4 (contrat v1, ``docs/plugins.md``) : bureau RDP sur AsyncRdp.

Remplace ``plugins/plugin_rdp.py`` (GTK3, retiré avec ``gtk-frdp/``, ``GtkFrdp.Display`` sur
``Gtk.Socket``/XEmbed, supprimé en GTK4) : #105, #71. La connexion passe
par `AsyncRdp <https://github.com/Mathilde-Team/AsyncRdp4>`_ (FreeRDP 3 en
asyncio) et ses deux intégrations GTK4, qui ne sont pas dans la wheel
``asyncrdp`` :

- ``gcm_gtk4_display_bridge.RdpView`` : affichage (``Gtk.Picture``),
  clavier, souris, redimensionnement ;
- ``gcm_gtk4_rdp_session.RdpSession`` : connexion + vue + presse-papier.

Le dossier ``integrations/gtk4`` d'AsyncRdp4 est cherché dans la clé
``integrations`` de la section ``[plugin rdp]`` de ``gcm4.conf``, puis
dans la variable d'environnement ``ASYNCRDP_GTK4_DIR``, puis dans le
``sys.path`` courant. Les options et leur validation sont dans
:mod:`core` (mêmes clés INI que GTK3).

Écarts avec GTK3, voulus ou en attente :

- la session démarre à l'ouverture de l'hôte ; le menu contextuel GTK3
  (Connecter/Déconnecter, « Touches spéciales ») devient un bouton de
  menu en haut de l'onglet : Déconnecter, Reconnecter et les
  combinaisons de :data:`core.COMBINAISONS`, envoyées par
  ``client.keyboard.combo`` (AsyncRdp 0.x avec ``Keyboard.combo``) ;
  fermer l'onglet déconnecte ;
- le partage d'une image ISO (montée par ``udisksctl`` en GTK3) est refusé
  par la validation ;
- le glisser-déposer de fichiers n'est pas encore repris.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402
from gcm4.core.model import Host  # noqa: E402
from gcm4.core.plugins import ConnectionPlugin  # noqa: E402
from gcm4.ui import boucle  # noqa: E402

from . import core  # noqa: E402

#: Clé de ``[plugin rdp]`` : dossier ``integrations/gtk4`` d'AsyncRdp4.
CLE_INTEGRATIONS = "integrations"
#: Variable d'environnement de repli pour ce dossier.
VARIABLE_INTEGRATIONS = "ASYNCRDP_GTK4_DIR"

#: Libellés des géométries, dans l'ordre de :data:`core.GEOMETRIES`.
LIBELLES_GEOMETRIE = {
    core.GEOMETRIE_PLEIN_ECRAN: _("Fit to tab"),
    "1920x1080": "1920×1080",
    "1280x1024": "1280×1024",
    "1024x768": "1024×768",
    "800x600": "800×600",
    core.GEOMETRIE_PERSONNALISEE: _("Custom…"),
}


class AsyncRdp:
    """Modules AsyncRdp chargés à la demande (``asyncrdp`` et ses intégrations GTK4)."""

    def __init__(self, asyncrdp: Any, session: type, vue: type) -> None:
        """Garde le module ``asyncrdp`` et les classes ``RdpSession``, ``RdpView``."""
        logger.debug("AsyncRdp.__init__: entrée")
        self.asyncrdp, self.session, self.vue = asyncrdp, session, vue
        logger.debug("AsyncRdp.__init__: fait")


def charger_asyncrdp(config: Mapping[str, str], environ: Mapping[str, str]) -> AsyncRdp:
    """Importe ``asyncrdp``, ``RdpSession`` et ``RdpView``.

    Args:
        config: section ``[plugin rdp]`` (clé ``integrations``).
        environ: environnement (variable ``ASYNCRDP_GTK4_DIR``).

    Raises:
        FileNotFoundError: AsyncRdp ou ses intégrations GTK4 sont introuvables
            (message traduit, affiché par la base).
    """
    logger.debug("charger_asyncrdp: entrée")
    dossier = (config.get(CLE_INTEGRATIONS) or environ.get(VARIABLE_INTEGRATIONS) or "").strip()
    if dossier:
        dossier = os.path.expanduser(dossier)
        if dossier not in sys.path:
            sys.path.insert(0, dossier)
    try:
        import asyncrdp
        from gcm_gtk4_display_bridge import RdpView
        from gcm_gtk4_rdp_session import RdpSession
    except ImportError as exc:
        logger.debug("charger_asyncrdp: absent ({})", exc)
        raise FileNotFoundError(
            _(
                "AsyncRdp is not available ({error}). Install asyncrdp and set "
                "'integrations' in the [plugin rdp] section to the integrations/gtk4 "
                "folder of AsyncRdp4."
            ).format(error=exc)
        ) from exc
    resultat = AsyncRdp(asyncrdp, RdpSession, RdpView)
    logger.debug("charger_asyncrdp: chargé depuis {!r}", dossier or "sys.path")
    return resultat


#: Attribut de ``RdpView`` qui garde sa sous-classe : le paquet du plugin peut
#: être rechargé, le type GObject ``Gcm4VueRdp`` ne s'enregistre qu'une fois.
ATTRIBUT_VUE = "_gcm4_vue_rdp"


def classe_vue(base: type) -> type:
    """Sous-classe de ``RdpView`` qui ne suit la taille de l'onglet que si demandé.

    ``RdpView`` demande au serveur la taille de son allocation à chaque
    redimensionnement ; avec une géométrie fixe sans ``rdp_dyn_res``, la
    taille choisie est gardée et l'image est mise à l'échelle.
    """
    logger.debug("classe_vue: entrée")
    if ATTRIBUT_VUE not in vars(base):

        class VueRdp(base):  # type: ignore[misc, valid-type]
            """``RdpView`` de gcm4 : redimensionnement distant optionnel."""

            __gtype_name__ = "Gcm4VueRdp"

            suit_onglet = True

            def _on_widget_resize(self, width: int, height: int) -> None:
                """Redimensionne la session distante seulement si elle suit l'onglet."""
                logger.debug("VueRdp._on_widget_resize: {}x{}", width, height)
                if self.suit_onglet:
                    super()._on_widget_resize(width, height)
                logger.debug("VueRdp._on_widget_resize: fait")

        setattr(base, ATTRIBUT_VUE, VueRdp)
    resultat = vars(base)[ATTRIBUT_VUE]
    logger.debug("classe_vue: {}", resultat.__name__)
    return resultat


def options_asyncrdp(module: Any, reglages: core.Reglages) -> Any:
    """``asyncrdp.RdpOptions`` des réglages (dossier partagé, RemoteApp compris)."""
    logger.debug("options_asyncrdp: entrée")
    champs = dict(reglages.options)
    if reglages.dossier is not None:
        nom, chemin = reglages.dossier
        champs["drives"] = [module.DriveMapping(name=nom, path=chemin)]
    if reglages.remote_app:
        champs["remoteapp"] = module.RemoteAppOptions(program=reglages.remote_app)
    resultat = module.RdpOptions(**champs)
    logger.debug("options_asyncrdp: {} champ(s)", len(champs))
    return resultat


#: Préfixe des actions de l'onglet (``rdp.deconnecter``…).
GROUPE_ACTIONS = "rdp"


def construire_menu() -> Gio.Menu:
    """Menu de l'onglet : Déconnecter, Reconnecter, « Touches spéciales » (#71)."""
    logger.debug("construire_menu: entrée")
    menu = Gio.Menu()
    session = Gio.Menu()
    session.append(_("Disconnect"), f"{GROUPE_ACTIONS}.deconnecter")
    session.append(_("Reconnect"), f"{GROUPE_ACTIONS}.reconnecter")
    menu.append_section(None, session)
    touches = Gio.Menu()
    for identifiant, libelle, _noms in core.COMBINAISONS:
        element = Gio.MenuItem.new(libelle, None)
        element.set_action_and_target_value(
            f"{GROUPE_ACTIONS}.touches", GLib.Variant.new_string(identifiant)
        )
        touches.append_item(element)
    menu.append_submenu(_("Special keys"), touches)
    logger.debug("construire_menu: fait")
    return menu


class RdpOnglet:
    """Onglet RDP : message d'état pendant la connexion, puis le bureau distant."""

    def __init__(
        self,
        host: Host,
        modules: AsyncRdp,
        reglages: core.Reglages,
        log: Any,
        notifier: Callable[[str], None] | None = None,
    ) -> None:
        """Construit l'onglet ; la connexion est lancée par :meth:`connecter`.

        Args:
            host: hôte ouvert.
            modules: AsyncRdp chargé (:func:`charger_asyncrdp`).
            reglages: réglages de connexion (:func:`core.reglages`).
            log: journal du plugin.
            notifier: toast d'échec (``context.notify``).
        """
        logger.debug("RdpOnglet.__init__: {!r}", host.path)
        self._host, self._modules, self._reglages = host, modules, reglages
        self._log, self._notifier = log, notifier
        self._tache: asyncio.Future | None = None
        self._fermeture: asyncio.Future | None = None
        self.ferme = False
        self.vue = classe_vue(modules.vue)()
        self.vue.suit_onglet = reglages.suit_onglet
        self.vue.set_hexpand(True)
        self.vue.set_vexpand(True)
        self.vue.set_focusable(True)
        self.etat = Gtk.Label(label=_("Connecting to {host}…").format(host=host.host), wrap=True)
        self._mot_de_passe = ""
        self.pile = Gtk.Stack(hexpand=True, vexpand=True)
        self.pile.add_named(self.etat, "etat")
        self.pile.add_named(self.vue, "bureau")
        self.pile.set_visible_child_name("etat")
        self.actions = self._actions()
        self.menu = Gtk.MenuButton(
            icon_name="input-keyboard-symbolic",
            menu_model=construire_menu(),
            tooltip_text=_("Remote desktop"),
            halign=Gtk.Align.END,
        )
        self.menu.add_css_class("flat")
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.widget.insert_action_group(GROUPE_ACTIONS, self.actions)
        self.widget.append(self.menu)
        self.widget.append(self.pile)
        self._session = modules.session(
            view=self.vue,
            gdk_display=Gdk.Display.get_default(),
            connect_fn=modules.asyncrdp.connect,
        )
        logger.debug("RdpOnglet.__init__: fait")

    def _actions(self) -> Gio.SimpleActionGroup:
        """Actions du menu de l'onglet (préfixe :data:`GROUPE_ACTIONS`)."""
        logger.debug("RdpOnglet._actions: entrée")
        groupe = Gio.SimpleActionGroup()
        for nom, rappel in (("deconnecter", self.deconnecter), ("reconnecter", self.reconnecter)):
            action = Gio.SimpleAction.new(nom, None)
            action.connect("activate", lambda _a, _p, f=rappel: f())
            groupe.add_action(action)
        touches = Gio.SimpleAction.new("touches", GLib.VariantType.new("s"))
        touches.connect("activate", lambda _a, p: self.envoyer_touches(p.get_string()))
        groupe.add_action(touches)
        self._groupe = groupe
        self._sensibilite(False)
        logger.debug("RdpOnglet._actions: fait")
        return groupe

    def _sensibilite(self, connecte: bool) -> None:
        """Déconnecter et les touches n'ont de sens qu'une fois connecté."""
        logger.debug("RdpOnglet._sensibilite: {}", connecte)
        self._groupe.lookup_action("deconnecter").set_enabled(connecte)
        self._groupe.lookup_action("touches").set_enabled(connecte)
        logger.debug("RdpOnglet._sensibilite: fait")

    def connecter(self, mot_de_passe: str) -> asyncio.Future:
        """Lance la connexion (tâche asyncio sur la boucle GLib)."""
        logger.debug("RdpOnglet.connecter: entrée")
        self._mot_de_passe = mot_de_passe
        self._tache = asyncio.ensure_future(self._connecter(mot_de_passe))
        logger.debug("RdpOnglet.connecter: tâche lancée")
        return self._tache

    async def _connecter(self, mot_de_passe: str) -> bool:
        """Connexion, puis affichage du bureau ; un échec s'affiche dans l'onglet."""
        logger.debug("RdpOnglet._connecter: entrée")
        r = self._reglages
        try:
            await self._session.connect(
                r.hote,
                r.port,
                r.utilisateur or None,
                mot_de_passe or None,
                domain=r.domaine,
                options=options_asyncrdp(self._modules.asyncrdp, r),
            )
        except asyncio.CancelledError:
            logger.debug("RdpOnglet._connecter: annulée")
            raise
        except Exception as exc:  # asyncrdp lève ConnectionError, RuntimeError…
            message = _("RDP connection to {host} failed: {error}").format(host=r.hote, error=exc)
            self._log.warning("connexion RDP à {} : {}", r.hote, exc)
            self.echec(message)
            logger.debug("RdpOnglet._connecter: échec")
            return False
        if self.ferme:  # onglet fermé pendant la connexion
            await self._session.disconnect()
            logger.debug("RdpOnglet._connecter: onglet déjà fermé")
            return False
        self.pile.set_visible_child_name("bureau")
        self._sensibilite(True)
        self.vue.grab_focus()
        logger.debug("RdpOnglet._connecter: connectée")
        return True

    def echec(self, message: str) -> None:
        """Affiche ``message`` dans l'onglet et en toast."""
        logger.debug("RdpOnglet.echec: entrée")
        self.etat.set_label(message)
        self.pile.set_visible_child_name("etat")
        self._sensibilite(False)
        if self._notifier is not None:
            self._notifier(message)
        logger.debug("RdpOnglet.echec: fait")

    def deconnecter(self) -> asyncio.Future | None:
        """Déconnecte sans fermer l'onglet ; Reconnecter relance la session."""
        logger.debug("RdpOnglet.deconnecter: entrée")
        if self.ferme:
            logger.debug("RdpOnglet.deconnecter: onglet fermé")
            return None
        if self._tache is not None and not self._tache.done():
            self._tache.cancel()
        self._sensibilite(False)
        self.etat.set_label(_("Disconnected from {host}").format(host=self._reglages.hote))
        self.pile.set_visible_child_name("etat")
        self._fermeture = asyncio.ensure_future(self._session.disconnect())
        logger.debug("RdpOnglet.deconnecter: déconnexion lancée")
        return self._fermeture

    def reconnecter(self) -> asyncio.Future | None:
        """Déconnecte puis reconnecte avec le dernier mot de passe saisi."""
        logger.debug("RdpOnglet.reconnecter: entrée")
        if self.ferme:
            logger.debug("RdpOnglet.reconnecter: onglet fermé")
            return None
        if self._tache is not None and not self._tache.done():
            self._tache.cancel()
        self._sensibilite(False)
        self.etat.set_label(_("Connecting to {host}…").format(host=self._reglages.hote))
        self.pile.set_visible_child_name("etat")
        self._tache = asyncio.ensure_future(self._reconnecter())
        logger.debug("RdpOnglet.reconnecter: tâche lancée")
        return self._tache

    async def _reconnecter(self) -> bool:
        """Attend la déconnexion éventuelle, puis :meth:`_connecter`."""
        logger.debug("RdpOnglet._reconnecter: entrée")
        await self._session.disconnect()
        resultat = await self._connecter(self._mot_de_passe)
        logger.debug("RdpOnglet._reconnecter: {}", resultat)
        return resultat

    def envoyer_touches(self, identifiant: str) -> bool:
        """Envoie la combinaison ``identifiant`` de :data:`core.COMBINAISONS`.

        Rend faux, avec un toast, sans session ou si AsyncRdp ne connaît pas
        ``Keyboard.combo`` (version antérieure).
        """
        logger.debug("RdpOnglet.envoyer_touches: {!r}", identifiant)
        noms = core.touches(identifiant)
        client = getattr(self._session, "client", None)
        clavier = getattr(client, "keyboard", None)
        combo = getattr(clavier, "combo", None)
        if combo is None:
            message = (
                _("Not connected")
                if client is None
                else _("This AsyncRdp version cannot send key combinations")
            )
            self._log.warning("touches {} non envoyées : {}", identifiant, message)
            if self._notifier is not None:
                self._notifier(message)
            logger.debug("RdpOnglet.envoyer_touches: indisponible")
            return False
        try:
            combo(*noms)
        except (ValueError, RuntimeError, OSError) as exc:
            self._log.warning("touches {} : {}", identifiant, exc)
            if self._notifier is not None:
                self._notifier(_("Key combination failed: {error}").format(error=exc))
            logger.debug("RdpOnglet.envoyer_touches: échec")
            return False
        self.vue.grab_focus()
        logger.debug("RdpOnglet.envoyer_touches: envoyé")
        return True

    def close(self) -> None:
        """Annule une connexion en cours et déconnecte (asynchrone, rien ne bloque)."""
        logger.debug("RdpOnglet.close: entrée")
        if self.ferme:
            logger.debug("RdpOnglet.close: déjà fermé")
            return
        self.ferme = True
        if self._tache is not None and not self._tache.done():
            self._tache.cancel()
        self._fermeture = asyncio.ensure_future(self._session.disconnect())
        logger.debug("RdpOnglet.close: déconnexion lancée")


def _ligne(grille: Gtk.Grid, rang: int, libelle: str, widget: Gtk.Widget) -> None:
    """Ajoute « libellé : widget » à la ligne ``rang`` de ``grille``."""
    logger.debug("_ligne: {!r}", libelle)
    etiquette = Gtk.Label(label=libelle, xalign=0)
    widget.set_hexpand(True)
    grille.attach(etiquette, 0, rang, 1, 1)
    grille.attach(widget, 1, rang, 1, 1)
    logger.debug("_ligne: fait")


def zone_options(options: dict[str, str], changed: Callable[[], None]) -> Gtk.Widget:
    """Zone d'options RDP du dialogue d'hôte (onglet « RDP » de GTK3).

    Args:
        options: copie des options ``rdp``, modifiée à chaque saisie.
        changed: appelé après chaque modification.
    """
    logger.debug("zone_options: entrée")
    for cle, valeur in core.options_defaut().items():
        options.setdefault(cle, valeur)
    grille = Gtk.Grid(row_spacing=6, column_spacing=12)

    def entree(cle: str) -> Gtk.Entry:
        """Champ texte lié à l'option ``cle``."""
        logger.debug("zone_options.entree: {}", cle)
        champ = Gtk.Entry(text=options.get(cle, ""))

        def modifie(widget: Gtk.Entry) -> None:
            """Saisie : met l'option à jour."""
            logger.debug("zone_options.modifie: {}", cle)
            options[cle] = widget.get_text().strip()
            changed()
            logger.debug("zone_options.modifie: fait")

        champ.connect("changed", modifie)
        logger.debug("zone_options.entree: fait")
        return champ

    def case(cle: str, libelle: str) -> Gtk.CheckButton:
        """Case à cocher liée à l'option booléenne ``cle``."""
        logger.debug("zone_options.case: {}", cle)
        bouton = Gtk.CheckButton(label=libelle, active=core.booleen(options.get(cle, "False")))

        def bascule(widget: Gtk.CheckButton) -> None:
            """Case cochée ou décochée : ``True``/``False`` comme GTK3."""
            logger.debug("zone_options.bascule: {}", cle)
            options[cle] = core.texte_booleen(widget.get_active())
            changed()
            logger.debug("zone_options.bascule: fait")

        bouton.connect("toggled", bascule)
        logger.debug("zone_options.case: fait")
        return bouton

    _ligne(grille, 0, _("Domain"), entree(core.OPT_DOMAINE))
    geometries = list(core.GEOMETRIES)
    liste = Gtk.DropDown.new_from_strings([LIBELLES_GEOMETRIE[g] for g in geometries])
    actuelle = options.get(core.OPT_GEOMETRIE) or core.GEOMETRIE_PLEIN_ECRAN
    if actuelle in geometries:
        liste.set_selected(geometries.index(actuelle))
    _ligne(grille, 1, _("Geometry"), liste)
    taille = Gtk.Box(spacing=6)
    taille.append(entree(core.OPT_LARGEUR))
    taille.append(Gtk.Label(label="×"))
    taille.append(entree(core.OPT_HAUTEUR))
    taille.set_visible(actuelle == core.GEOMETRIE_PERSONNALISEE)
    _ligne(grille, 2, _("Size"), taille)

    def geometrie(widget: Gtk.DropDown, _pspec: Any) -> None:
        """Géométrie choisie ; la taille n'est visible qu'en « personnalisée »."""
        logger.debug("zone_options.geometrie: entrée")
        choix = geometries[widget.get_selected()]
        options[core.OPT_GEOMETRIE] = choix
        taille.set_visible(choix == core.GEOMETRIE_PERSONNALISEE)
        changed()
        logger.debug("zone_options.geometrie: {}", choix)

    liste.connect("notify::selected", geometrie)
    _ligne(grille, 3, _("RemoteApp"), entree(core.OPT_REMOTE_APP))
    rang = 4
    for cle, libelle in (
        (core.OPT_CERTIFICAT, _("Ignore certificate errors")),
        (core.OPT_RESOLUTION_DYNAMIQUE, _("Dynamic resolution")),
        (core.OPT_PRESSE_PAPIER, _("Share clipboard")),
        (core.OPT_PARTAGE_DOSSIER, _("Share folder")),
    ):
        grille.attach(case(cle, libelle), 1, rang, 1, 1)
        rang += 1
    _ligne(grille, rang, _("Shared folder path"), entree(core.OPT_CHEMIN_DOSSIER))
    logger.debug("zone_options: fait")
    return grille


class RdpPlugin(ConnectionPlugin):
    """Protocole ``rdp`` : bureau à distance sur AsyncRdp (FreeRDP 3)."""

    name = "rdp"
    version = "1.0"
    protocols = ("rdp",)  # protocol_id GTK3 : les configurations existantes

    def default_port(self, protocol: str) -> int | None:
        """Port TCP 3389."""
        logger.debug("RdpPlugin.default_port: {!r}", protocol)
        logger.debug("RdpPlugin.default_port: {}", core.PORT_DEFAUT)
        return core.PORT_DEFAUT

    def default_options(self, protocol: str) -> dict[str, str]:
        """Options d'un nouvel hôte (:func:`core.options_defaut`)."""
        logger.debug("RdpPlugin.default_options: {!r}", protocol)
        resultat = core.options_defaut()
        logger.debug("RdpPlugin.default_options: fait")
        return resultat

    def validate_options(self, protocol: str, options: Mapping[str, str]) -> list:
        """Erreurs des options (:func:`core.valider`)."""
        logger.debug("RdpPlugin.validate_options: {!r}", protocol)
        resultat = core.valider(options)
        logger.debug("RdpPlugin.validate_options: {} erreur(s)", len(resultat))
        return resultat

    def build_host_options_widget(
        self, host: Host, options: dict[str, str], changed: Callable[[], None]
    ) -> Gtk.Widget:
        """Domaine, géométrie, RemoteApp, certificat, presse-papier, dossier partagé."""
        logger.debug("RdpPlugin.build_host_options_widget: {!r}", host.path)
        zone = zone_options(options, changed)
        logger.debug("RdpPlugin.build_host_options_widget: fait")
        return zone

    def open(self, host: Host) -> RdpOnglet:
        """Ouvre l'onglet et lance la connexion ; demande le mot de passe s'il manque.

        Raises:
            FileNotFoundError: AsyncRdp ou ses intégrations GTK4 sont introuvables.
            RuntimeError: aucune boucle asyncio dans la boucle GTK.
            ValueError: options invalides.
        """
        logger.debug("RdpPlugin.open: {!r}", host.path)
        reglages = core.reglages(host)
        if not boucle.asyncio_disponible():
            logger.debug("RdpPlugin.open: pas d'asyncio")
            raise RuntimeError(
                _("RDP needs asyncio in the GTK main loop (PyGObject 3.50 or later)")
            )
        modules = charger_asyncrdp(self.context.config(), os.environ)
        onglet = RdpOnglet(host, modules, reglages, self.context.log, notifier=self.context.notify)
        if host.password:
            onglet.connecter(host.password)
        else:

            def recu(mot_de_passe: str | None) -> None:
                """Mot de passe saisi, ou annulation : l'onglet l'affiche."""
                logger.debug("RdpPlugin.open.recu: entrée")
                if onglet.ferme:
                    logger.debug("RdpPlugin.open.recu: onglet fermé")
                    return
                if mot_de_passe is None:
                    onglet.etat.set_label(_("Connection cancelled"))
                    logger.debug("RdpPlugin.open.recu: annulé")
                    return
                onglet.connecter(mot_de_passe)
                logger.debug("RdpPlugin.open.recu: connexion lancée")

            self.context.ask_password(host, recu)
        logger.debug("RdpPlugin.open: onglet ouvert")
        return onglet


PLUGIN = RdpPlugin
