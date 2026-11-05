# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin web de gcm4 (contrat v1, ``docs/plugins.md``) : console web dans un onglet (#397).

Portage de ``plugins/plugin_web.py`` (GTK3, WebKit2 4.x) :

- **WebKitGTK 6.0 présent** (``gir1.2-webkit-6.0``) : la page s'ouvre dans
  l'onglet (``WebKit.WebView``), chargée dès l'ouverture comme en GTK3. Si
  ``web_ignore_cert`` est vrai, un certificat refusé est accepté **pour cet
  hôte seulement** (``NetworkSession.allow_tls_certificate_for_host``) puis
  la page est rechargée ; GTK3 coupait la vérification pour tout le contexte
  WebKit. Chaque acceptation est journalisée en avertissement.
- **WebKitGTK absent** (Debian 12, Rocky 9 sans le paquet) : l'onglet le dit
  et ouvre l'URL dans le navigateur par défaut, comme le repli GTK3 ; le
  plugin se charge quand même (WebKit n'est importé qu'à l'ouverture).

La logique sans GTK (options, URL, validation) est dans :mod:`core`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import gi

gi.require_version("Gtk", "4.0")  # déjà fixé par gcm4 ; déclare le fichier GTK4 (#178)

from gi.repository import Gio, GLib, Gtk  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402
from gcm4.core.model import Host  # noqa: E402
from gcm4.core.plugins import ConnectionPlugin  # noqa: E402
from gcm4.core.validation import FieldError  # noqa: E402

from . import core  # noqa: E402

__all__ = ["PLUGIN", "SessionWeb", "WebPlugin", "charger_webkit"]


def charger_webkit() -> Any | None:
    """Module ``WebKit`` 6.0 (GTK4), ou ``None`` s'il n'est pas installé."""
    logger.debug("charger_webkit: entrée")
    try:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit
    except (ValueError, ImportError) as exc:
        logger.debug("charger_webkit: absent ({})", exc)
        return None
    logger.debug(
        "charger_webkit: WebKitGTK {}.{}", WebKit.get_major_version(), WebKit.get_minor_version()
    )
    return WebKit


class SessionWeb:
    """Onglet web : barre (adresse, état, recharger, navigateur) et page ou message de repli."""

    def __init__(
        self, host: Host, context: Any, adresse: str, ignorer: bool, webkit: Any | None
    ) -> None:
        """Construit l'onglet et charge ``adresse`` (dans l'onglet ou dans le navigateur).

        Args:
            host: hôte ouvert.
            context: contexte du plugin (``log``, ``notify``).
            adresse: URL validée (:func:`core.url`).
            ignorer: accepter un certificat non vérifié pour l'hôte de l'URL.
            webkit: module ``WebKit`` 6.0, ou ``None`` (repli navigateur).
        """
        logger.debug("SessionWeb.__init__: {!r}", host.path)
        self.host = host
        self.context = context
        self.adresse = adresse
        self.ignorer = ignorer
        self.webkit = webkit
        self.webview: Any | None = None
        self.ferme = False
        self.en_erreur = False
        self.exceptions_tls: set[str] = set()
        self._construire()
        self.charger()
        logger.debug("SessionWeb.__init__: fait")

    def _construire(self) -> None:
        """Widgets de l'onglet ; ``self.widget`` est la boîte racine."""
        logger.debug("SessionWeb._construire: entrée")
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outils = Gtk.Box(spacing=6)
        for cote in ("top", "start", "end"):
            getattr(outils, f"set_margin_{cote}")(6)
        self.titre = Gtk.Label(xalign=0, hexpand=True, selectable=True)
        self.titre.set_markup(
            "<b>{}</b> <small>{}</small>".format(
                GLib.markup_escape_text(_("Web")), GLib.markup_escape_text(self.adresse)
            )
        )
        outils.append(self.titre)
        self.etat = Gtk.Label(label=_("Waiting…"), css_classes=["dim-label"])
        outils.append(self.etat)
        self.bouton_recharger = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text=_("Refresh")
        )
        self.bouton_recharger.connect("clicked", lambda _b: self.charger())
        outils.append(self.bouton_recharger)
        self.bouton_navigateur = Gtk.Button(
            icon_name="web-browser-symbolic", tooltip_text=_("Open in the system browser")
        )
        self.bouton_navigateur.connect("clicked", lambda _b: self.ouvrir_navigateur())
        outils.append(self.bouton_navigateur)
        self.widget.append(outils)
        self.widget.append(Gtk.Separator())
        if self.webkit is not None:
            self.webview = self.webkit.WebView(hexpand=True, vexpand=True)
            self.webview.get_settings().set_enable_javascript(True)  # consoles BMC HTML5
            self.webview.connect("load-changed", self._chargement)
            self.webview.connect("notify::title", self._titre_change)
            self.webview.connect("load-failed", self._echec)
            self.webview.connect("load-failed-with-tls-errors", self._erreur_tls)
            self.widget.append(self.webview)
        else:
            self.repli = Gtk.Label(
                label=_(
                    "WebKitGTK 6.0 unavailable (gir1.2-webkit-6.0 package missing).\n"
                    "The web console opens in the system browser."
                ),
                justify=Gtk.Justification.CENTER,
                wrap=True,
                vexpand=True,
            )
            self.widget.append(self.repli)
        logger.debug("SessionWeb._construire: WebKit {}", "oui" if self.webview else "non")

    def _statut(self, texte: str) -> None:
        """Texte de l'état, à droite de l'adresse."""
        logger.debug("SessionWeb._statut: {!r}", texte)
        self.etat.set_text(texte)
        logger.debug("SessionWeb._statut: fait")

    def charger(self) -> None:
        """(Re)charge l'URL dans l'onglet, ou l'ouvre dans le navigateur sans WebKit."""
        logger.debug("SessionWeb.charger: {!r}", self.adresse)
        if self.ferme:
            logger.debug("SessionWeb.charger: onglet fermé")
            return
        if self.webview is None:
            self.ouvrir_navigateur()
            logger.debug("SessionWeb.charger: repli navigateur")
            return
        self._statut(_("Connecting…"))
        self.webview.load_uri(self.adresse)
        logger.debug("SessionWeb.charger: chargement lancé")

    def ouvrir_navigateur(self) -> bool:
        """Ouvre l'URL dans le navigateur par défaut ; vrai si le lancement a réussi."""
        logger.debug("SessionWeb.ouvrir_navigateur: {!r}", self.adresse)
        try:
            Gio.AppInfo.launch_default_for_uri(self.adresse, None)
        except GLib.Error as exc:
            self.context.log.warning(
                "échec d'ouverture de {} dans le navigateur : {}", self.adresse, exc.message
            )
            self._statut(_("Error: could not open the URL"))
            logger.debug("SessionWeb.ouvrir_navigateur: échec")
            return False
        self._statut(_("Opened in the system browser"))
        logger.debug("SessionWeb.ouvrir_navigateur: fait")
        return True

    def _chargement(self, webview: Any, evenement: Any) -> None:
        """``load-changed`` : « Connexion… » au début, titre de la page à la fin."""
        logger.debug("SessionWeb._chargement: {}", evenement)
        if evenement == self.webkit.LoadEvent.STARTED:
            self.en_erreur = False
            self._statut(_("Connecting…"))
        elif evenement == self.webkit.LoadEvent.FINISHED and not self.en_erreur:
            self._statut(webview.get_title() or _("Loaded"))
        logger.debug("SessionWeb._chargement: fait")

    def _titre_change(self, webview: Any, _spec: Any) -> None:
        """Titre de la page connu après la fin du chargement : il remplace « Chargé »."""
        logger.debug("SessionWeb._titre_change: entrée")
        titre = webview.get_title()
        if titre and not self.en_erreur and not webview.is_loading():
            self._statut(titre)
        logger.debug("SessionWeb._titre_change: fait")

    def _echec(self, _webview: Any, _evenement: Any, uri: str, erreur: GLib.Error) -> bool:
        """``load-failed`` : erreur affichée et journalisée ; WebKit affiche sa page d'erreur."""
        logger.debug("SessionWeb._echec: {!r}", uri)
        self.en_erreur = True
        self.context.log.warning("échec de chargement de {} : {}", uri, erreur.message)
        self._statut(_("Error: {msg}").format(msg=erreur.message))
        logger.debug("SessionWeb._echec: fait")
        return False

    def _erreur_tls(self, webview: Any, uri: str, certificat: Any, _erreurs: Any) -> bool:
        """Certificat refusé : accepté pour cet hôte seulement si ``web_ignore_cert``.

        Returns:
            ``True`` si l'erreur est traitée (certificat accepté, page rechargée) ;
            ``False`` laisse WebKit afficher sa page d'erreur.
        """
        logger.debug("SessionWeb._erreur_tls: {!r}", uri)
        nom = core.decouper(uri)[1]
        if not self.ignorer or not nom or nom in self.exceptions_tls:
            self.en_erreur = True
            self._statut(_("Error: {msg}").format(msg=_("Certificate")))
            logger.debug("SessionWeb._erreur_tls: refusé")
            return False
        self.exceptions_tls.add(nom)
        self.context.log.warning(
            "certificat TLS non vérifié accepté pour {} (web_ignore_cert)", nom
        )
        webview.get_network_session().allow_tls_certificate_for_host(certificat, nom)
        webview.load_uri(uri)
        logger.debug("SessionWeb._erreur_tls: accepté, rechargement")
        return True

    def close(self) -> None:
        """Arrête le chargement et le processus web de l'onglet ; appelé une fois."""
        logger.debug("SessionWeb.close: {!r}", self.host.path)
        self.ferme = True
        if self.webview is not None:
            self.webview.stop_loading()
            self.webview.terminate_web_process()
        logger.debug("SessionWeb.close: fait")


class WebPlugin(ConnectionPlugin):
    """Protocole ``web`` : console HTML5 dans l'onglet (WebKitGTK 6.0) ou navigateur système."""

    name = "web"
    version = "1.0"
    protocols = ("web",)

    def default_port(self, protocol: str) -> int | None:
        """Port 443, comme GTK3 (indicatif : l'URL fait foi)."""
        logger.debug("WebPlugin.default_port: {!r}", protocol)
        logger.debug("WebPlugin.default_port: {}", core.PORT_DEFAUT)
        return core.PORT_DEFAUT

    def requires_address(self, protocol: str) -> bool:
        """Faux : l'adresse est dans l'URL (option ``web_url``), le champ Hôte est masqué."""
        logger.debug("WebPlugin.requires_address: {!r}", protocol)
        logger.debug("WebPlugin.requires_address: False")
        return False

    def default_options(self, protocol: str) -> dict[str, str]:
        """URL vide, certificats non vérifiés acceptés (défaut GTK3)."""
        logger.debug("WebPlugin.default_options: {!r}", protocol)
        options = core.options_defaut()
        logger.debug("WebPlugin.default_options: fait")
        return options

    def validate_options(self, protocol: str, options: Mapping[str, str]) -> list[FieldError]:
        """URL obligatoire, ``http`` ou ``https`` (:func:`core.valider`)."""
        logger.debug("WebPlugin.validate_options: {!r}", protocol)
        erreurs = core.valider(options)
        logger.debug("WebPlugin.validate_options: {} erreur(s)", len(erreurs))
        return erreurs

    def build_host_options_widget(
        self, host: Host, options: dict[str, str], changed: Callable[[], None]
    ) -> Gtk.Widget:
        """URL et case « Ignorer les erreurs de certificat » (mêmes libellés que GTK3)."""
        logger.debug("WebPlugin.build_host_options_widget: {!r}", host.path)
        grille = Gtk.Grid(column_spacing=12, row_spacing=6)
        titre_url = Gtk.Label(
            label=_("URL"),
            xalign=0,
            tooltip_text=_(
                "BMC web console (iLO, iDRAC, IMM…) or any web interface (Proxmox, switch, TrueNAS…)"
            ),
        )
        grille.attach(titre_url, 0, 0, 1, 1)
        saisie = Gtk.Entry(
            hexpand=True,
            text=options.get(core.OPT_URL, ""),
            placeholder_text="https://ilo-host.example.org/",
        )

        def url_modifiee(entree: Gtk.Entry) -> None:
            """Saisie de l'URL modifiée : option mise à jour."""
            logger.debug("WebPlugin.build_host_options_widget.url_modifiee: entrée")
            options[core.OPT_URL] = entree.get_text().strip()
            changed()
            logger.debug("WebPlugin.build_host_options_widget.url_modifiee: fait")

        saisie.connect("changed", url_modifiee)
        grille.attach(saisie, 1, 0, 1, 1)
        case = Gtk.CheckButton(
            label=_("Ignore certificate errors"),
            active=core.ignorer_certificat(options),
            tooltip_text=_("Useful for self-signed certificates on BMC interfaces"),
        )

        def case_basculee(bouton: Gtk.CheckButton) -> None:
            """Case cochée ou décochée : option mise à jour."""
            logger.debug("WebPlugin.build_host_options_widget.case_basculee: entrée")
            options[core.OPT_IGNORER_CERTIFICAT] = core.ecrire_booleen(bouton.get_active())
            changed()
            logger.debug("WebPlugin.build_host_options_widget.case_basculee: fait")

        case.connect("toggled", case_basculee)
        grille.attach(case, 1, 1, 1, 1)
        logger.debug("WebPlugin.build_host_options_widget: fait")
        return grille

    def open(self, host: Host) -> SessionWeb:
        """Onglet web ; la page se charge dans l'onglet, ou dans le navigateur sans WebKit.

        Raises:
            ValueError: URL absente ou refusée (:func:`core.valider`).
        """
        logger.debug("WebPlugin.open: {!r}", host.path)
        options = host.protocol_options.get("web", {})
        erreurs = core.valider(options)
        if erreurs:
            logger.debug("WebPlugin.open: URL refusée")
            raise ValueError(erreurs[0].message)
        session = SessionWeb(
            host,
            self.context,
            core.url(options),
            core.ignorer_certificat(options),
            charger_webkit(),
        )
        logger.debug("WebPlugin.open: session ouverte")
        return session


PLUGIN = WebPlugin
