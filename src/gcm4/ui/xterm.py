# SPDX-License-Identifier: GPL-3.0-or-later
"""Widget xterm.js local dans WebKitGTK 6 ; aucun serveur ni URL distante (#76)."""

from __future__ import annotations

import codecs
import json
import mimetypes
import signal
import time
from collections import deque
from pathlib import Path

import gi

gi.require_version("WebKit", "6.0")

from gi.repository import Gio, GLib, GObject, Gtk, WebKit  # noqa: E402
from loguru import logger  # noqa: E402

from gcm4.core.i18n import _  # noqa: E402
from gcm4.core.pty_process import ProcessusPty  # noqa: E402

ASSETS = Path(__file__).with_name("xterm_assets")
URI = "gcm4-terminal://local/index.html"
RESSOURCES = {
    "index.html",
    "terminal.css",
    "terminal.js",
    "xterm.js",
    "xterm.css",
    "addon-fit.js",
    "addon-search.js",
}
_CONTEXTE = None


def _ressource(requete, *_args) -> None:
    """Ne sert que les sept fichiers publics de l'émulateur, jamais un chemin libre."""
    logger.trace("_ressource: entrée")
    nom = requete.get_path().removeprefix("/")
    if nom not in RESSOURCES or requete.get_uri() != f"gcm4-terminal://local/{nom}":
        requete.finish_error(
            GLib.Error.new_literal(Gio.io_error_quark(), "Not found", Gio.IOErrorEnum.NOT_FOUND)
        )
        logger.debug("_ressource: ressource refusée")
        return
    try:
        contenu = (ASSETS / nom).read_bytes()
    except OSError as exc:
        logger.warning("_ressource: fichier indisponible ({})", exc.errno)
        requete.finish_error(
            GLib.Error.new_literal(Gio.io_error_quark(), "Not found", Gio.IOErrorEnum.NOT_FOUND)
        )
        logger.debug("_ressource: échec signalé")
        return
    flux = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(contenu))
    requete.finish(flux, len(contenu), mimetypes.guess_type(nom)[0] or "text/plain")
    logger.trace("_ressource: {} octet(s)", len(contenu))


def contexte():
    """Contexte partagé des terminaux, sans schéma de fichiers ouvert au renderer."""
    logger.debug("contexte: entrée")
    global _CONTEXTE
    if _CONTEXTE is None:
        _CONTEXTE = WebKit.WebContext.new()
        _CONTEXTE.register_uri_scheme("gcm4-terminal", _ressource)
        _CONTEXTE.get_security_manager().register_uri_scheme_as_local("gcm4-terminal")
    logger.debug("contexte: fait")
    return _CONTEXTE


class Terminal(Gtk.Box):
    """Émulateur graphique et adaptation non bloquante du PTY à la boucle GTK.

    Un seul bloc de sortie de 64 Kio est en vol. Sa consommation par xterm
    réarme la lecture : un programme bavard subit la contre-pression du PTY,
    pas une accumulation illimitée de JavaScript. L'entrée est aussi écrite
    partiellement en surveillant IO_OUT. FD3 n'est jamais envoyé à la page.
    """

    __gsignals__ = {"child-exited": (GObject.SignalFlags.RUN_LAST, None, (int,))}

    def __init__(self):
        """Construit la page isolée et la recherche native, sans lancer de programme."""
        logger.debug("Terminal.__init__: entrée")
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self._encoding = "UTF-8"
        self._decoder = codecs.getincrementaldecoder(self._encoding)(errors="replace")
        self._backspace = self._delete = 0
        self._theme = {"foreground": "#ffffff", "background": "#000000"}
        self._ready = self._en_vol = False
        self._sortie = deque()
        self._entree = bytearray()
        self._lecture = self._ecriture = self._secret_watch = 0
        self._spawn_watch = 0
        self._spawn_complete = self._spawn_failed = False
        self._processus = None
        self._fermeture = None
        self._escalade = 0
        self._erreur = False
        self.recherche = Gtk.SearchBar(show_close_button=True)
        ligne = Gtk.Box(spacing=6)
        self.champ = Gtk.SearchEntry(hexpand=True)
        self.champ.connect("activate", self._chercher)
        ligne.append(self.champ)
        for precedent, icone in ((True, "go-up-symbolic"), (False, "go-down-symbolic")):
            bouton = Gtk.Button(icon_name=icone, tooltip_text=_("Search"))
            bouton.connect("clicked", self._chercher, precedent)
            ligne.append(bouton)
        self.recherche.set_child(ligne)
        self.recherche.connect_entry(self.champ)
        self.append(self.recherche)
        gestionnaire = WebKit.UserContentManager()
        gestionnaire.register_script_message_handler("terminal", None)
        gestionnaire.connect("script-message-received::terminal", self._message)
        self.view = WebKit.WebView(
            web_context=contexte(),
            network_session=WebKit.NetworkSession.new_ephemeral(),
            user_content_manager=gestionnaire,
            hexpand=True,
            vexpand=True,
        )
        reglages = self.view.get_settings()
        reglages.set_enable_javascript(True)
        reglages.set_allow_file_access_from_file_urls(False)
        reglages.set_allow_universal_access_from_file_urls(False)
        self.view.connect("decide-policy", self._politique)
        self.view.connect("permission-request", self._permission)
        self.view.connect("load-failed", self._echec)
        self.view.connect("web-process-terminated", self._echec)
        self.view.connect("notify::has-focus", self._focus)
        self.append(self.view)
        self.view.load_uri(URI)
        logger.debug("Terminal.__init__: fait")

    def _focus(self, view, *_args):
        """Transmet le focus natif à la zone de saisie xterm, sans voler celui de la recherche."""
        logger.debug("Terminal._focus: entrée")
        if self._ready and view.has_focus():
            self._eval("gcmTerminal.focus()")
        logger.debug("Terminal._focus: fait")

    def _politique(self, _view, decision, genre):
        """Interdit les navigations et fenêtres étrangères, même depuis le terminal."""
        logger.trace("Terminal._politique: entrée")
        if genre in (
            WebKit.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit.PolicyDecisionType.NEW_WINDOW_ACTION,
        ):
            if (
                genre == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION
                or decision.get_navigation_action().get_request().get_uri() != URI
            ):
                decision.ignore()
                logger.debug("Terminal._politique: navigation refusée")
                return True
        logger.trace("Terminal._politique: ressource locale")
        return False

    def _permission(self, _view, demande):
        """Aucune caméra, géolocalisation ou permission web dans un terminal local."""
        logger.debug("Terminal._permission: refus")
        demande.deny()
        logger.debug("Terminal._permission: fait")
        return True

    def _echec(self, *_args):
        """Un échec du renderer est visible et arrête le processus, sans page blanche muette."""
        logger.debug("Terminal._echec: entrée")
        if len(_args) == 4 and (
            _args[2] != URI
            or _args[3].matches(WebKit.network_error_quark(), WebKit.NetworkError.CANCELLED)
        ):
            logger.debug("Terminal._echec: chargement annulé ou navigation étrangère refusée")
            return True
        logger.warning("Terminal._echec: rendu xterm indisponible")
        if not self._erreur:
            self._erreur = True
            self.append(Gtk.Label(label=_("Error") + ": xterm.js", wrap=True))
            self.stop()
        logger.debug("Terminal._echec: fait")
        return True

    def _eval(self, expression, callback=None):
        """Évalue uniquement du code interne ; toute donnée dynamique est sérialisée en JSON."""
        logger.trace("Terminal._eval: entrée")
        self.view.evaluate_javascript(expression, -1, None, None, None, self._evalue, callback)
        logger.trace("Terminal._eval: demandé")

    def _evalue(self, view, resultat, callback):
        """Récolte les erreurs JavaScript, même si l'appelant n'attend aucune valeur."""
        logger.trace("Terminal._evalue: entrée")
        try:
            valeur = view.evaluate_javascript_finish(resultat)
        except GLib.Error as exc:
            logger.warning("Terminal._evalue: JavaScript refusé ({})", exc.code)
            self._echec()
        else:
            if callback is not None:
                callback(valeur)
        logger.trace("Terminal._evalue: fait")

    def _message(self, _gestionnaire, valeur):
        """Décode un protocole fermé ; ne journalise jamais les entrées du terminal."""
        logger.trace("Terminal._message: entrée")
        try:
            brut = valeur.to_string()
            if len(brut) > 131072:
                logger.debug("Terminal._message: message trop long")
                return
            message = json.loads(brut)
            genre, contenu = message["type"], message.get("value")
            if genre == "ready" and not self._ready:
                self._ready = True
                self._configurer()
                self._focus(self.view)
                self._transmettre()
                self._demarrer_lecture()
            elif genre == "ack" and self._en_vol:
                self._en_vol = False
                self._transmettre()
                self._demarrer_lecture()
            elif (
                genre in ("input", "binary") and isinstance(contenu, str) and len(contenu) <= 65536
            ):
                self._ajouter_entree(
                    contenu.encode(
                        "latin1" if genre == "binary" else self._encoding, errors="replace"
                    )
                )
            elif (
                genre == "resize"
                and isinstance(contenu, list)
                and len(contenu) == 2
                and all(type(n) is int and 2 <= n <= 1000 for n in contenu)
            ):
                if self._processus is not None and self._processus.fd >= 0:
                    self._processus.resize(*contenu)
            elif genre == "copy":
                self._eval("gcmTerminal.selection()", self._copier)
            elif genre == "paste":
                self.get_clipboard().read_text_async(None, self._coller)
            elif genre == "search":
                self.recherche.set_search_mode(True)
                self.champ.grab_focus()
            elif genre == "erase":
                if self._processus is not None and self._processus.fd >= 0:
                    self._ajouter_entree(self._processus.erase())
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.debug("Terminal._message: format refusé ({})", type(exc).__name__)
        logger.trace("Terminal._message: fait")

    def _copier(self, valeur):
        """Copie uniquement la sélection demandée par le clavier."""
        logger.debug("Terminal._copier: entrée")
        self.get_clipboard().set(valeur.to_string())
        logger.debug("Terminal._copier: fait")

    def _coller(self, clipboard, resultat):
        """Collage explicite par xterm, qui respecte le mode bracketed paste du programme."""
        logger.debug("Terminal._coller: entrée")
        try:
            texte = clipboard.read_text_finish(resultat)
        except GLib.Error as exc:
            logger.warning("Terminal._coller: lecture refusée ({})", exc.code)
        else:
            if texte:
                self._eval(f"gcmTerminal.paste({json.dumps(texte)})")
        logger.debug("Terminal._coller: fait")

    def _chercher(self, _widget, precedent=False):
        """Recherche littérale dans l'historique local, sans regex distante."""
        logger.debug("Terminal._chercher: entrée")
        self._eval(
            f"gcmTerminal.search({json.dumps(self.champ.get_text())},{json.dumps(precedent)})"
        )
        logger.debug("Terminal._chercher: fait")

    def texte(self, callback):
        """Lecture asynchrone du vrai buffer xterm, notamment pour les tests natifs."""
        logger.trace("Terminal.texte: entrée")
        if self._ready:
            self._eval("gcmTerminal.text()", lambda valeur: callback(valeur.to_string()))
        else:
            callback("")
        logger.trace("Terminal.texte: demandé")

    def set_encoding(self, nom):
        """Transcodage incrémental Python, indépendant des options ICU de WebKit."""
        logger.debug("Terminal.set_encoding: {}", nom)
        try:
            decodeur = codecs.getincrementaldecoder(nom)(errors="replace")
        except LookupError:
            logger.debug("Terminal.set_encoding: encodage refusé")
            raise GLib.Error(f"Unknown encoding: {nom}") from None
        self._encoding, self._decoder = nom, decodeur
        logger.debug("Terminal.set_encoding: fait")

    def get_encoding(self):
        """Nom de l'encodage effectif de la session."""
        return self._encoding

    def set_backspace_binding(self, valeur):
        """Conserve les cinq valeurs INI historiques."""
        logger.debug("Terminal.set_backspace_binding: {}", valeur)
        self._backspace = valeur
        self._configurer()
        logger.debug("Terminal.set_backspace_binding: fait")

    def set_delete_binding(self, valeur):
        """Conserve les cinq valeurs INI historiques."""
        logger.debug("Terminal.set_delete_binding: {}", valeur)
        self._delete = valeur
        self._configurer()
        logger.debug("Terminal.set_delete_binding: fait")

    def set_colors(self, avant, fond, palette):
        """Couleurs d'hôte et palette ANSI communes aux anciens terminaux."""
        logger.debug("Terminal.set_colors: entrée")
        noms = (
            "black",
            "red",
            "green",
            "yellow",
            "blue",
            "magenta",
            "cyan",
            "white",
            "brightBlack",
            "brightRed",
            "brightGreen",
            "brightYellow",
            "brightBlue",
            "brightMagenta",
            "brightCyan",
            "brightWhite",
        )
        self._theme = dict(zip(noms, (c.to_string() for c in palette), strict=True))
        self._theme.update(foreground=avant.to_string(), background=fond.to_string())
        self._configurer()
        logger.debug("Terminal.set_colors: fait")

    def _configurer(self):
        """Applique les options avant ou après le chargement de la page."""
        logger.trace("Terminal._configurer: entrée")
        if self._ready:
            options = {"backspace": self._backspace, "delete": self._delete, "theme": self._theme}
            self._eval(f"gcmTerminal.configure({json.dumps(options)})")
        logger.trace("Terminal._configurer: fait")

    def feed(self, donnees):
        """Décode une sortie dans l'encodage configuré, y compris entre deux blocs."""
        logger.trace("Terminal.feed: {} octet(s)", len(donnees))
        self._sortie.append(self._decoder.decode(donnees))
        self._transmettre()
        logger.trace("Terminal.feed: fait")

    def _transmettre(self):
        """Un bloc en vol seulement, acquitté après analyse ANSI par xterm."""
        logger.trace("Terminal._transmettre: entrée")
        if self._ready and not self._en_vol and self._sortie:
            self._en_vol = True
            texte = "".join(self._sortie)
            self._sortie.clear()
            self._eval(f"gcmTerminal.write({json.dumps(texte)})")
        logger.trace("Terminal._transmettre: fait")

    def feed_child(self, donnees):
        """Entrées de l'application en UTF-8, transcodées comme la saisie utilisateur."""
        logger.trace("Terminal.feed_child: {} octet(s)", len(donnees))
        self._ajouter_entree(
            donnees.decode("utf-8", errors="replace").encode(self._encoding, errors="replace")
        )
        logger.trace("Terminal.feed_child: fait")

    def _ajouter_entree(self, donnees):
        """Met les octets à écrire en file, sans bloquer la boucle GTK."""
        logger.trace("Terminal._ajouter_entree: {} octet(s)", len(donnees))
        if self._processus is not None and self._processus.fd >= 0:
            self._entree.extend(donnees)
            if not self._ecriture:
                self._ecriture = GLib.io_add_watch(
                    self._processus.fd,
                    GLib.PRIORITY_DEFAULT,
                    GLib.IO_OUT | GLib.IO_ERR | GLib.IO_HUP,
                    self._ecrire,
                )
        logger.trace("Terminal._ajouter_entree: fait")

    def _ecrire(self, _fd, _condition):
        """Écrit partiellement ; l'erreur d'un PTY terminé n'est pas une exception GTK."""
        logger.trace("Terminal._ecrire: entrée")
        try:
            n = self._processus.write(self._entree)
            del self._entree[:n]
        except OSError as exc:
            logger.debug("Terminal._ecrire: PTY indisponible ({})", exc.errno)
            self._entree.clear()
        if not self._entree:
            self._ecriture = 0
            logger.trace("Terminal._ecrire: terminé")
            return GLib.SOURCE_REMOVE
        logger.trace("Terminal._ecrire: suite")
        return GLib.SOURCE_CONTINUE

    def spawn(self, lancement, envv, callback):
        """Lance le PTY ; callback différé, compatible avec une fermeture immédiate."""
        logger.debug("Terminal.spawn: entrée")
        try:
            self._processus = ProcessusPty(lancement, envv)
        except OSError as exc:
            logger.warning("Terminal.spawn: lancement refusé ({})", exc.errno)
            GLib.idle_add(callback, self, -1, GLib.Error(str(exc)))
        else:
            self._spawn_watch = GLib.io_add_watch(
                self._processus.exec_fd,
                GLib.PRIORITY_DEFAULT,
                GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                self._lancement_fini,
                callback,
            )
            if self._processus.secret_fd >= 0:
                self._secret_watch = GLib.io_add_watch(
                    self._processus.secret_fd,
                    GLib.PRIORITY_DEFAULT,
                    GLib.IO_OUT | GLib.IO_ERR | GLib.IO_HUP,
                    self._envoyer_secret,
                )
            self._demarrer_lecture()
            GLib.timeout_add(25, self._poller)
        logger.debug("Terminal.spawn: fait")

    def _lancement_fini(self, _fd, _condition, callback):
        """Le PID n'est annoncé qu'après la confirmation d'exec, pas celui de l'amorce."""
        logger.debug("Terminal._lancement_fini: entrée")
        try:
            pret = self._processus.pret()
        except OSError as exc:
            logger.warning("Terminal._lancement_fini: exec refusé ({})", exc.errno)
            self._spawn_failed = True
            callback(self, -1, GLib.Error(str(exc)))
        else:
            if not pret:
                logger.debug("Terminal._lancement_fini: amorce en cours")
                return GLib.SOURCE_CONTINUE
            callback(self, self._processus.process.pid, None)
        self._spawn_complete = True
        self._spawn_watch = 0
        logger.debug("Terminal._lancement_fini: fait")
        return GLib.SOURCE_REMOVE

    def _envoyer_secret(self, *_args):
        """FD3 n'est écrit que par Python, jamais par JavaScript."""
        logger.trace("Terminal._envoyer_secret: entrée")
        fini = self._processus.envoyer_secret()
        if fini:
            self._secret_watch = 0
        logger.trace("Terminal._envoyer_secret: terminé={}", fini)
        return not fini

    def _demarrer_lecture(self):
        """Réarme après l'acquittement du bloc précédent."""
        logger.trace("Terminal._demarrer_lecture: entrée")
        if (
            self._ready
            and not self._en_vol
            and not self._sortie
            and not self._lecture
            and self._processus is not None
            and self._processus.fd >= 0
            and not self._processus.eof
        ):
            self._lecture = GLib.io_add_watch(
                self._processus.fd,
                GLib.PRIORITY_DEFAULT,
                GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                self._lire,
            )
        logger.trace("Terminal._demarrer_lecture: fait")

    def _lire(self, *_args):
        """Un bloc puis attente d'acquittement ; aucune sortie perdue à la fin du programme."""
        logger.trace("Terminal._lire: entrée")
        self._lecture = 0
        try:
            donnees = self._processus.read()
        except OSError as exc:
            logger.warning("Terminal._lire: PTY refusé ({})", exc.errno)
            self._processus.eof = True
            self.stop()
        else:
            if donnees:
                self.feed(donnees)
            else:
                self._demarrer_lecture()
        logger.trace("Terminal._lire: fait")
        return GLib.SOURCE_REMOVE

    def _poller(self):
        """Récolte sans bloquer ; fermeture résistante aux enfants qui ignorent SIGHUP."""
        logger.trace("Terminal._poller: entrée")
        statut = self._processus.poll()
        if (
            self._spawn_complete
            and statut is not None
            and (self._processus.eof or self._fermeture is not None)
        ):
            for attribut in ("_lecture", "_ecriture", "_secret_watch", "_spawn_watch"):
                source = getattr(self, attribut)
                if source:
                    GLib.source_remove(source)
                    setattr(self, attribut, 0)
            self._processus.close()
            self._entree.clear()
            if not self._spawn_failed:
                self.emit("child-exited", statut)
            logger.trace("Terminal._poller: récolté")
            return GLib.SOURCE_REMOVE
        if self._fermeture is not None:
            ecoule = time.monotonic() - self._fermeture
            if ecoule > 2 and self._escalade < 2:
                self._processus.signaler(signal.SIGKILL)
                self._escalade = 2
            elif ecoule > 0.5 and self._escalade < 1:
                self._processus.signaler(signal.SIGTERM)
                self._escalade = 1
        logger.trace("Terminal._poller: suite")
        return GLib.SOURCE_CONTINUE

    def stop(self):
        """Fermeture idempotente de la session et de son premier plan."""
        logger.debug("Terminal.stop: entrée")
        if self._fermeture is None:
            self._fermeture = time.monotonic()
            if self._processus is not None and self._processus.poll() is None:
                self._processus.signaler()
        logger.debug("Terminal.stop: fait")
