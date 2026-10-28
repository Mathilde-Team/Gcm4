#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
vnc_tab.py — Onglet VNC complet pour gnome-connection-manager (fork GTK4)

Mis à jour pour exploiter les nouvelles fonctions de ton fork d'asyncvnc2
(Tight/Hextile/ZlibHex, curseur serveur, resize, Fence/ContinuousUpdates,
JPEG quality / compression level, 8bpp indexé optionnel).

Fichier UNIQUE et autonome : le pinning de clé hôte (anciennement dans un
vnc_host_keys.py séparé) est désormais inclus directement ci-dessous —
un seul fichier à copier dans ton dépôt.

Dépendances :
    pip install "PyGObject>=3.50" numpy cryptography
    + ton asyncvnc2 modifié (asyncvnc2-1.py) sur le PYTHONPATH

*** CORRECTIF IMPORTANT PAR RAPPORT À LA VERSION PRÉCÉDENTE ***
La version précédente de ce fichier ne rappelait jamais
`client.video.refresh()` après le premier `screenshot()`. Dans ton
asyncvnc2, `refresh()` envoie une FramebufferUpdateRequest explicite — sans
ça, le serveur n'a plus aucune raison d'envoyer quoi que ce soit après la
toute première image, et l'affichage se figeait silencieusement. La boucle
de lecture ci-dessous redemande maintenant une mise à jour après chaque
rectangle reçu, sauf quand les ContinuousUpdates sont actives (le serveur
pousse alors les mises à jour de lui-même, sans qu'on ait besoin de
redemander).

Architecture, en miroir de ton onglet SPICE :
- VncDisplay (Gtk.Picture) : logique pure de connexion/rendu/entrées.
- VncTab (Gtk.Box) : l'onglet complet — barre d'outils, placeholder de
  connexion, overlay de curseur serveur, affichage.
- build_vnc_tab_label() : label d'onglet (icône + titre + bouton fermer).

HYPOTHÈSES à ajuster selon ton architecture réelle :
- Conteneur d'onglets = Gtk.Notebook (voir la démo en bas du fichier).
  CONFIRMÉ le 2026-09-05 contre le vrai dépôt gcm4 fourni (encore en
  cours d'écriture, mais l'architecture des onglets est déjà en place) :
  `gnome_connection_manager.py` construit bien `self.nbConsole =
  Gtk.Notebook()` — aucun `Adw.TabView` nulle part dans le dépôt.
- Plein écran : PLUS une hypothèse — voir `_toggle_fullscreen()` /
  `_resolve_fullscreen_action()` ci-dessous. Le vrai dépôt gcm4 ne
  définit AUCUNE action "win.toggle-fullscreen" ; cet onglet bascule
  donc lui-même le plein écran sur sa propre fenêtre racine, sans
  dépendre d'un mécanisme GCM encore instable.
- Le mapping de coordonnées widget <-> framebuffer distant est géré par
  VncDisplay.get_display_transform() / widget_to_remote(), qui tient
  compte du ContentFit courant (CONTAIN avec letterboxing centré, ou
  FILL). Utilisé à la fois pour l'envoi des positions souris et pour le
  positionnement/dimensionnement de l'overlay du curseur serveur.
"""

import asyncio
import enum
from dataclasses import dataclass
from typing import Optional

from loguru import logger

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib, GObject, Pango

import base64
import json
import os
import ssl
import tempfile
from datetime import datetime
from pathlib import Path

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_der_public_key,
)

import asyncvnc2


# ---------------------------------------------------------------------------
# Pinning des clés publiques hôte (auth Apple ARD, type 33)
#
# Ton asyncvnc2 fait déjà tout le travail de sécurité : si on lui fournit un
# host_key connu, il l'utilise directement pour chiffrer la clé AES de
# session au lieu d'aller le récupérer sur le réseau — si la clé stockée ne
# correspond plus à celle du serveur, le serveur ne peut pas déchiffrer et
# l'authentification échoue (PermissionError), plutôt que de se connecter
# silencieusement à un éventuel MITM. Cette classe ne fait que la
# persistance côté client, façon known_hosts SSH.
#
# Ne concerne que les serveurs utilisant l'authentification Apple ARD
# (macOS Partage d'écran). Sans effet sur les serveurs VNC classiques
# (Proxmox, TigerVNC, x11vnc...), qui n'ont pas de clé hôte.
# ---------------------------------------------------------------------------


class HostKeyStore:
    """
    Fichier JSON : {"host:port": "<clé publique DER encodée en base64>"}.
    Par défaut dans ~/.gcm/, à côté de ta clé AES existante (.gcm.key).
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (Path.home() / ".gcm" / "vnc_host_keys.json")
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                logger.warning(
                    "HostKeyStore._load | fichier illisible ou corrompu, réinitialisation | path={path}",
                    path=str(self.path),
                    exception=True,
                )
                return {}
        return {}

    def _save(self):
        # Écriture atomique (fichier temporaire dans le MÊME dossier, puis
        # `os.replace()`) plutôt qu'un `write_text()` direct sur la cible.
        # `write_text()` tronque le fichier avant d'écrire le nouveau
        # contenu -- un crash pile à ce moment-là (coupure de courant, kill
        # -9, disque plein en cours d'écriture...) laisse un fichier
        # partiellement écrit, JSON invalide ou avec une entrée coupée en
        # plein milieu. C'est très exactement le scénario que `get()` et
        # `_load()` savent déjà encaisser (entrée corrompue isolée, ou
        # fichier entier illisible) -- mais mieux vaut ne jamais produire ce
        # fichier corrompu en premier lieu que de compter uniquement sur la
        # récupération après coup. `os.replace()` est atomique au niveau du
        # système de fichiers (POSIX comme Windows) : soit l'ancien
        # contenu reste en place, soit le nouveau y est entièrement --
        # jamais un état intermédiaire. Le fichier temporaire est créé dans
        # le même dossier que la cible pour que `os.replace()` reste un
        # simple renommage (pas de copie inter-partition qui romprait
        # l'atomicité).
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(self._data, indent=2))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _entry_key(host: str, port: int) -> str:
        return f"{host}:{port}"

    def get(self, host: str, port: int) -> Optional[rsa.RSAPublicKey]:
        raw = self._data.get(self._entry_key(host, port))
        if raw is None:
            return None
        try:
            return load_der_public_key(base64.b64decode(raw))
        except (ValueError, UnsupportedAlgorithm):
            # Le fichier JSON dans son ensemble était valide (sinon _load()
            # l'aurait déjà intercepté), mais CETTE entrée précise n'est ni
            # du base64 valide (`base64.b64decode` lève `binascii.Error`,
            # une sous-classe de `ValueError`) ni une clé publique DER valide
            # (`load_der_public_key` lève `ValueError`, ou plus rarement
            # `UnsupportedAlgorithm`) -- édition manuelle du fichier,
            # écriture tronquée par un crash en plein `_save()`, etc.
            #
            # Sans ce garde, l'exception remontait NON INTERCEPTÉE hors de
            # `HostKeyStore.get()` : `VncDisplay._connect_and_run()` appelle
            # `get()` AVANT son bloc try/except (celui-ci ne protège que
            # `asyncvnc2.connect()` et la suite), donc la tâche asyncio
            # plantait silencieusement sans jamais atteindre
            # `_set_status(VncStatus.ERROR, ...)` -- l'onglet restait bloqué
            # indéfiniment sur le spinner "Connexion à ...", sans bouton
            # "Réessayer" ni "Oublier la clé enregistrée" (tous deux masqués
            # tant que le statut reste CONNECTING) -- ironique, puisque ce
            # bouton existe justement pour ce genre de problème de clé hôte.
            # On traite cette entrée comme absente -- même repli que
            # `_load()` pour un fichier JSON entièrement corrompu, mais ici
            # limité à CETTE clé : les autres entrées du store restent
            # intactes (rien n'est effacé sur disque par un simple get()).
            logger.warning(
                "HostKeyStore.get | entrée illisible ou corrompue, traitée comme absente | host={host} port={port}",
                host=host,
                port=port,
                exception=True,
            )
            return None

    def set(self, host: str, port: int, key: rsa.RSAPublicKey):
        der = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        self._data[self._entry_key(host, port)] = base64.b64encode(der).decode("ascii")
        self._save()

    def forget(self, host: str, port: int):
        if self._data.pop(self._entry_key(host, port), None) is not None:
            self._save()


#: Instance partagée par défaut, pratique pour une utilisation simple.
#: Une vraie intégration dans gnome-connection-manager voudra sans doute
#: injecter son propre HostKeyStore (chemin différent, ou stockage
#: partagé avec le reste des profils de connexion) plutôt que d'utiliser
#: ce singleton.
default_host_key_store = HostKeyStore()


# ---------------------------------------------------------------------------
# Modèle de connexion
# ---------------------------------------------------------------------------


@dataclass
class VncConnectionInfo:
    name: str
    host: str
    port: int = 5900
    username: Optional[str] = None
    password: Optional[str] = None

    # --- nouveaux réglages exposés par ton fork d'asyncvnc2 ---
    #: Qualité JPEG pour Tight (0-9), None = pas de préférence envoyée.
    jpeg_quality: Optional[int] = None
    #: Niveau de compression zlib (0-9), None = pas de préférence envoyée.
    compression_level: Optional[int] = None
    #: Accepter le mode 8bpp indexé natif du serveur plutôt que de forcer
    #: le 32bpp truecolour. À laisser à False sauf besoin spécifique — cf.
    #: les mises en garde de CLAUDE.md sur l'ordre SetColourMapEntries.
    allow_indexed_colour: bool = False
    #: Session partagée (True, comportement historique) ou exclusive
    #: (False) — nécessite le patch shared_flag_patch.py côté asyncvnc2.
    shared: bool = True
    #: Override avancé de l'ordre/choix des encodages négociés. None =
    #: laisser asyncvnc2 utiliser son ordre de priorité par défaut.
    encodings: Optional[list] = None
    #: Pinning de la clé hôte (auth Apple ARD uniquement) façon known_hosts
    #: SSH. Sans effet sur les serveurs VNC classiques, qui n'ont pas de
    #: clé hôte.
    pin_host_key: bool = True
    #: Synchronisation bidirectionnelle du presse-papiers avec le serveur.
    sync_clipboard: bool = True
    #: Inverse le sens de la molette ("défilement naturel" façon trackpad
    #: macOS) pour cette connexion. False = convention traditionnelle
    #: (molette vers le bas = défilement vers le bas), déjà le
    #: comportement historique de ce fichier.
    invert_scroll: bool = False
    #: Transport WebSocket (Gcm4 #104, tâche 3) : la connexion RFB passe
    #: dans un tunnel WebSocket (websockify, proxy noVNC, Proxmox…) au lieu
    #: d'une socket TCP brute. Requête HTTP en clair sur ``host:port``.
    websocket: bool = False
    #: Chemin de la requête d'ouverture WebSocket (ex. ``/websockify``).
    #: Ignoré si ``websocket`` est faux.
    ws_path: str = "/"
    #: VeNCrypt (Gcm4 #104, tâche 2) : fichier PEM d'une autorité de
    #: certification privée pour vérifier le certificat X509* du serveur.
    #: None = magasin système (comportement d'asyncvnc2 par défaut). Le nom
    #: d'hôte vérifié est toujours ``host``, renseigné par asyncvnc2.
    tls_ca_file: Optional[str] = None
    #: Reconnexion inversée (Gcm4 #104, tâche 4) : au lieu de composer vers
    #: ``host:port``, l'onglet ÉCOUTE sur ``host:port`` (``asyncvnc2.listen``)
    #: et attend que le serveur VNC vienne à lui (``vncconfig -connect``,
    #: UltraVNC ``-connect``…). Port conventionnel : 5500. Le pinning de clé
    #: hôte est désactivé dans ce mode (cf. ``_resolve_connection_mode``).
    reverse: bool = False


class VncStatus(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


# ---------------------------------------------------------------------------
# GDK_TO_VNC_KEY : table de DÉROGATIONS, pas la table principale.
#
# Pour l'immense majorité des touches -- y compris tout l'AZERTY (lettres
# accentuées é/è/à/ç/ù, symboles &é"'(-è_çà)=, et même les touches mortes
# dead_circumflex/dead_diaeresis) -- Gdk.keyval_name() renvoie déjà le nom
# de keysym X11 standard, et ton asyncvnc2 (via le paquet `keysymdef`, une
# table X11 complète) le reconnaît tel quel. Pas besoin de les lister ici.
#
# Cette table ne sert que pour les quelques cas où on veut *forcer* un nom
# différent de celui que GDK renvoie (ex. traiter KP_Enter comme Return).
# ---------------------------------------------------------------------------

GDK_TO_VNC_KEY = {
    "KP_Enter": "Return",
}


# ---------------------------------------------------------------------------
# Widget d'affichage bas niveau : framebuffer + entrées, pas de chrome UI
# ---------------------------------------------------------------------------


class VncDisplay(Gtk.Picture):
    """Affiche le framebuffer VNC et relaie clavier/souris/molette."""

    __gsignals__ = {
        # (status: valeur de VncStatus, message lisible)
        "vnc-status-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "vnc-title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # message d'erreur complété du nombre de secondes restant avant la
        # prochaine reconnexion automatique, émis chaque seconde (#94)
        "vnc-reconnect-countdown": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # objet asyncvnc2.Cursor, ou None si le serveur a demandé de le cacher
        "vnc-cursor-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # (largeur, hauteur) du framebuffer distant après un resize serveur
        "vnc-size-changed": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        # liste d'asyncvnc2.Screen (multi-écran, ExtendedDesktopSize)
        "vnc-screens-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # émis une fois la connexion pleinement établie (après le premier rendu)
        "vnc-connected": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, info: VncConnectionInfo, host_key_store: Optional[HostKeyStore] = None):
        super().__init__()
        self.info = info
        self.host_key_store = host_key_store or default_host_key_store
        self.set_can_shrink(True)
        self.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.client = None
        self._ctx = None
        self._read_task = None
        self._connect_task = None
        self.status = VncStatus.DISCONNECTED

        self._last_cursor = None
        self._last_size = None
        self._last_screens = []
        self._active_screen_id = None
        self._active_mouse_holds = {}
        self._active_key_holds = {}
        self._last_clipboard_text = ""
        self._clipboard_watched = None
        # Dernière texture réellement poussée à l'écran (déjà recadrée sur
        # l'écran actif le cas échéant) -- attribut d'instance normal, PAS
        # récupéré via self.get_paintable() : dans le stub GTK4 de test
        # (tests/gtk_stub/), tout accès non défini renvoie un _NoOp() y
        # compris get_paintable() avant le tout premier rendu, ce qui
        # rendrait "pas encore d'image" indétectable. Voir save_screenshot()
        # ci-dessous.
        self._last_frame_texture = None

        # Reconnexion automatique après une coupure inattendue (pas un
        # stop()/close() volontaire) -- voir _should_schedule_auto_reconnect
        # ci-dessous pour la condition exacte, et _resolve_reconnect_delay_seconds
        # pour le calcul du délai (backoff exponentiel plafonné).
        self._had_connected_before = False
        self._reconnect_attempt = 0
        self._reconnect_timeout_id = None
        # Décompte affiché seconde par seconde jusqu'à la tentative (#94) :
        # minuteur distinct (GLib.timeout_add, 1000 ms) du minuteur de
        # reconnexion lui-même, annulé en même temps que lui.
        self._countdown_timeout_id = None
        self._countdown_remaining = 0
        self._countdown_base_message = ""

        self._setup_input_controllers()

    # ---- Cycle de vie ----

    def start(self):
        self._connect_task = asyncio.ensure_future(self._connect_and_run())

    def stop(self):
        logger.debug("VncDisplay.stop | host={host} status={status}", host=self.info.host, status=self.status.value)
        self._cancel_pending_auto_reconnect()
        if self._connect_task:
            self._connect_task.cancel()
        if self._read_task:
            self._read_task.cancel()
        self._release_all_mouse_holds()
        self._release_all_key_holds()
        if self._ctx:
            asyncio.ensure_future(self._safe_close())
        self._set_status(VncStatus.DISCONNECTED, "Déconnecté")

    def _release_all_mouse_holds(self):
        for button, hold_cm in self._active_mouse_holds.items():
            try:
                hold_cm.__exit__(None, None, None)
            except Exception:
                logger.trace(
                    "VncDisplay._release_all_mouse_holds | échec (connexion déjà fermée ?) | "
                    "host={host} button={button}",
                    host=self.info.host,
                    button=button,
                )
        self._active_mouse_holds.clear()

    def _release_all_key_holds(self):
        for keycode, hold_cm in self._active_key_holds.items():
            try:
                hold_cm.__exit__(None, None, None)
            except Exception:
                logger.trace(
                    "VncDisplay._release_all_key_holds | échec (connexion déjà fermée ?) | "
                    "host={host} keycode={keycode}",
                    host=self.info.host,
                    keycode=keycode,
                )
        self._active_key_holds.clear()

    async def _safe_close(self):
        try:
            await self._ctx.__aexit__(None, None, None)
        except Exception:
            logger.trace("VncDisplay._safe_close | fermeture déjà en cours | host={host}", host=self.info.host)

    def reconnect(self):
        logger.debug("VncDisplay.reconnect | host={host}", host=self.info.host)
        self.stop()
        self._last_cursor = None
        self._last_size = None
        self.start()

    # ---- Reconnexion automatique ----

    @staticmethod
    def _should_schedule_auto_reconnect(status_value: str, had_connected_before: bool) -> bool:
        """Détermine si une reconnexion automatique doit être planifiée après
        un changement de statut -- méthode statique pure (même style que
        `_resolve_resize_target_size`/`_resolve_fullscreen_action`) pour
        rester testable sans instancier de vrai `VncDisplay` ni dépendre de
        `GLib.timeout_add_seconds`.

        Ne se déclenche QUE sur `VncStatus.ERROR` -- jamais sur
        `VncStatus.DISCONNECTED` (c'est précisément ce que `stop()` pose,
        qu'il s'agisse d'un `close()` utilisateur ou d'un `reconnect()`
        manuel/automatique en cours -- un stop() volontaire ne doit jamais
        se reprogrammer lui-même) -- ET seulement si la connexion avait
        déjà été pleinement établie au moins une fois auparavant
        (`had_connected_before`, mis à jour dans `_set_status` dès que le
        statut passe à CONNECTED). Sans cette deuxième condition, un échec
        d'authentification ou un hôte injoignable DÈS LA PREMIÈRE tentative
        déclencherait des reconnexions automatiques répétées avec le même
        mot de passe/la même adresse -- inutile (l'erreur ne va pas se
        résoudre toute seule) et potentiellement néfaste (verrouillage de
        compte côté serveur pour l'auth Apple ARD). Seule une vraie coupure
        EN COURS DE SESSION (réseau perdu, serveur redémarré) déclenche une
        tentative automatique ; un échec de connexion initiale reste gouverné
        par le bouton "Réessayer" existant, sous contrôle de l'utilisateur.
        """
        return status_value == VncStatus.ERROR.value and had_connected_before

    @staticmethod
    def _resolve_reconnect_delay_seconds(attempt: int) -> int:
        """Délai (en secondes) avant la N-ième tentative de reconnexion
        automatique consécutive (`attempt` démarre à 0 pour la première).

        Backoff exponentiel plafonné à 30 s (2, 4, 8, 16, 30, 30, 30...) --
        assez court pour récupérer vite d'un blip réseau, assez long à
        l'approche du plafond pour ne pas marteler un serveur qui reste
        injoignable plus longtemps. Pas de nombre maximal de tentatives :
        une coupure prolongée continue de retenter indéfiniment à ce
        rythme plafonné -- fermer l'onglet (`close()`/`stop()`, qui annule
        tout minuteur en attente via `_cancel_pending_auto_reconnect()`)
        ou cliquer "Reconnecter" reste le moyen d'interrompre la boucle.
        """
        return min(2 * (2**attempt), 30)

    @staticmethod
    def _append_reconnect_delay_to_message(message: str, delay_seconds: int) -> str:
        """
        Complète le message de statut affiché à l'utilisateur quand une
        reconnexion automatique vient d'être planifiée (`_set_status`
        ci-dessous), pour qu'il sache qu'une tentative EST prévue et
        environ quand -- jusqu'ici cette information n'existait que dans
        les logs (`logger.info` dans `_schedule_auto_reconnect`
        ci-dessous), l'interface affichait juste le message d'erreur brut
        ("Connexion interrompue : ...") sans indiquer que quoi que ce
        soit allait se passer ensuite. Méthode statique pure (même style
        que `_resolve_reconnect_delay_seconds` ci-dessus) pour rester
        testable sans dépendre de `GLib.timeout_add_seconds`.

        `message` vide (n'arrive pas en pratique : tous les appels
        `_set_status(VncStatus.ERROR, ...)` du fichier passent un message
        non vide) renvoie juste le suffixe seul, sans tiret orphelin
        devant.
        """
        suffix = f"nouvelle tentative automatique dans {delay_seconds}s"
        return f"{message} — {suffix}" if message else suffix

    def _schedule_auto_reconnect(self):
        delay_seconds = self._resolve_reconnect_delay_seconds(self._reconnect_attempt)
        self._reconnect_attempt += 1
        logger.info(
            "VncDisplay._schedule_auto_reconnect | nouvelle tentative dans {delay}s | host={host} attempt={attempt}",
            delay=delay_seconds,
            host=self.info.host,
            attempt=self._reconnect_attempt,
        )
        self._reconnect_timeout_id = GLib.timeout_add_seconds(delay_seconds, self._on_auto_reconnect_timeout)
        self._start_reconnect_countdown(delay_seconds)

    @staticmethod
    def _resolve_countdown_tick(remaining: int):
        """Une seconde de décompte (#94) : (secondes restantes, continuer ?).

        Le décompte s'arrête à 1 s affichée : la tentative elle-même
        (minuteur de reconnexion) remplace ensuite le message par
        « Connexion à … ». Jamais négatif, même si un tick arrive en retard.
        """
        logger.debug("VncDisplay._resolve_countdown_tick | entrée remaining={r}", r=remaining)
        restant = max(remaining - 1, 0)
        resultat = (restant, restant > 0)
        logger.debug("VncDisplay._resolve_countdown_tick | sortie {r}", r=resultat)
        return resultat

    def _start_reconnect_countdown(self, delay_seconds: int):
        logger.debug("VncDisplay._start_reconnect_countdown | entrée delay={d}", d=delay_seconds)
        self._cancel_reconnect_countdown()
        self._countdown_remaining = delay_seconds
        self._countdown_timeout_id = GLib.timeout_add(1000, self._on_reconnect_countdown_tick)
        logger.debug("VncDisplay._start_reconnect_countdown | sortie")

    def _on_reconnect_countdown_tick(self):
        logger.debug("VncDisplay._on_reconnect_countdown_tick | entrée remaining={r}", r=self._countdown_remaining)
        self._countdown_remaining, continuer = self._resolve_countdown_tick(self._countdown_remaining)
        if not continuer:
            self._countdown_timeout_id = None
            logger.debug("VncDisplay._on_reconnect_countdown_tick | sortie fin du décompte")
            return False
        self.emit(
            "vnc-reconnect-countdown",
            self._append_reconnect_delay_to_message(self._countdown_base_message, self._countdown_remaining),
        )
        logger.debug("VncDisplay._on_reconnect_countdown_tick | sortie")
        return True

    def _cancel_reconnect_countdown(self):
        logger.debug("VncDisplay._cancel_reconnect_countdown | entrée id={i}", i=self._countdown_timeout_id)
        if self._countdown_timeout_id is not None:
            GLib.source_remove(self._countdown_timeout_id)
            self._countdown_timeout_id = None
        logger.debug("VncDisplay._cancel_reconnect_countdown | sortie")

    def _on_auto_reconnect_timeout(self):
        self._reconnect_timeout_id = None
        self._cancel_reconnect_countdown()
        self.reconnect()
        return False  # source à usage unique -- un nouveau minuteur est replanifié si ça échoue encore

    def _cancel_pending_auto_reconnect(self):
        self._cancel_reconnect_countdown()
        if self._reconnect_timeout_id is not None:
            GLib.source_remove(self._reconnect_timeout_id)
            self._reconnect_timeout_id = None

    def forget_host_key(self):
        """Oublie la clé hôte pinnée pour ce serveur (auth Apple ARD)."""
        if self.info.pin_host_key:
            logger.info(
                "VncDisplay.forget_host_key | host={host} port={port}",
                host=self.info.host,
                port=self.info.port,
            )
            self.host_key_store.forget(self.info.host, self.info.port)

    def _set_status(self, status: VncStatus, message: str = ""):
        self.status = status
        if status == VncStatus.CONNECTED:
            # Une connexion pleinement établie autorise une future
            # reconnexion automatique (cf. _should_schedule_auto_reconnect)
            # et remet le backoff à zéro -- une coupure ultérieure repart
            # du délai le plus court, pas de celui atteint lors d'une série
            # de tentatives précédente.
            self._had_connected_before = True
            self._reconnect_attempt = 0
        will_auto_reconnect = self._should_schedule_auto_reconnect(status.value, self._had_connected_before)
        if will_auto_reconnect:
            self._countdown_base_message = message
            # Complète le message AVANT l'émission ci-dessous, pas après --
            # sinon l'interface afficherait un instant le message brut sans
            # l'info de délai (cf. _append_reconnect_delay_to_message
            # ci-dessus, session 23). Délai recalculé ici plutôt que de
            # changer la signature de _schedule_auto_reconnect() pour le
            # lui faire remonter : self._reconnect_attempt n'a pas encore
            # changé entre les deux appels (il n'est incrémenté que DANS
            # _schedule_auto_reconnect, appelée juste après), donc les deux
            # calculs sont garantis identiques -- calcul pur redondant mais
            # sans risque, qui évite de toucher une signature déjà couverte
            # par tests/test_auto_reconnect.py::
            # test_schedule_auto_reconnect_uses_the_resolved_delay_and_increments_attempt
            # et les tests de câblage qui la monkeypatchent en 0 argument.
            message = self._append_reconnect_delay_to_message(
                message, self._resolve_reconnect_delay_seconds(self._reconnect_attempt)
            )
        self.emit("vnc-status-changed", status.value, message)
        if will_auto_reconnect:
            self._schedule_auto_reconnect()

    @staticmethod
    def _resolve_connection_mode(reverse: bool, host: str, port: int, pin_host_key: bool) -> dict:
        """Sens d'établissement de la connexion (Gcm4 #104, tâche 4).

        Renvoie ``opener`` (``"connect"`` ou ``"listen"``, nom de la
        fonction d'asyncvnc2), ``status`` (texte affiché pendant
        l'attente) et ``pin`` (pinning de clé hôte actif ou non).

        En reconnexion inversée, ``host:port`` est l'adresse LOCALE
        d'écoute, pas l'identité du serveur qui va se présenter : épingler
        une clé sous cette adresse mélangerait les clés de tous les
        serveurs qui s'y connectent, donc pas de pinning.
        """
        logger.debug(
            "VncDisplay._resolve_connection_mode | entrée reverse={r} host={h} port={p}", r=reverse, h=host, p=port
        )
        if reverse:
            resultat = {
                "opener": "listen",
                "status": f"En attente d'une connexion entrante sur {host}:{port}…",
                "pin": False,
            }
        else:
            resultat = {"opener": "connect", "status": f"Connexion à {host}…", "pin": pin_host_key}
        logger.debug("VncDisplay._resolve_connection_mode | sortie {r}", r=resultat)
        return resultat

    @staticmethod
    def _resolve_transport_kwargs(websocket: bool, ws_path: str) -> dict:
        """Arguments de transport à passer à ``asyncvnc2.connect()``.

        Sans WebSocket : dictionnaire vide, pour rester compatible avec un
        asyncvnc2 qui ne connaît pas encore ``websocket=``. Avec : chemin
        normalisé (vide → ``/``, ``/`` ajouté en tête si absent).

        Raises:
            ValueError: le chemin contient un espace ou un caractère de
                contrôle ; il finit tel quel dans la ligne de requête HTTP,
                on refuse donc tout ce qui pourrait y injecter un en-tête.
        """
        logger.debug(
            "VncDisplay._resolve_transport_kwargs | entrée websocket={w} ws_path={p!r}", w=websocket, p=ws_path
        )
        if not websocket:
            logger.debug("VncDisplay._resolve_transport_kwargs | sortie TCP brut")
            return {}
        chemin = (ws_path or "").strip() or "/"
        if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in chemin):
            logger.debug("VncDisplay._resolve_transport_kwargs | sortie chemin refusé")
            raise ValueError(f"chemin WebSocket invalide : {ws_path!r}")
        if not chemin.startswith("/"):
            chemin = "/" + chemin
        resultat = {"websocket": True, "ws_path": chemin}
        logger.debug("VncDisplay._resolve_transport_kwargs | sortie {r}", r=resultat)
        return resultat

    @staticmethod
    def _resolve_tls_kwargs(ca_file: Optional[str]) -> dict:
        """Arguments VeNCrypt à passer à ``asyncvnc2.connect()``.

        Sans CA privée : dictionnaire vide (asyncvnc2 utilise le magasin
        système). Avec : ``ssl_context`` vérifiant certificat ET nom d'hôte
        (``ssl.create_default_context``), qui ne fait confiance qu'à cette
        CA.

        Raises:
            ValueError: fichier absent, illisible ou qui n'est pas un PEM
                de certificat valide.
        """
        logger.debug("VncDisplay._resolve_tls_kwargs | entrée ca_file={f!r}", f=ca_file)
        if not ca_file or not ca_file.strip():
            logger.debug("VncDisplay._resolve_tls_kwargs | sortie magasin système")
            return {}
        chemin = os.path.expanduser(ca_file.strip())
        try:
            contexte = ssl.create_default_context(cafile=chemin)
        except (OSError, ssl.SSLError) as exc:
            logger.debug("VncDisplay._resolve_tls_kwargs | sortie CA illisible : {exc}", exc=exc)
            raise ValueError(f"autorité de certification illisible ({chemin}) : {exc}") from exc
        logger.debug("VncDisplay._resolve_tls_kwargs | sortie CA privée {c}", c=chemin)
        return {"ssl_context": contexte}

    @staticmethod
    def _describe_connection_error(exc: BaseException) -> str:
        """Message d'état d'un échec de connexion ; les erreurs TLS de
        VeNCrypt sont nommées comme telles plutôt que noyées dans
        « Échec de connexion »."""
        logger.debug("VncDisplay._describe_connection_error | entrée {t}", t=type(exc).__name__)
        if isinstance(exc, ssl.SSLCertVerificationError):
            detail = exc.verify_message or str(exc)
            message = f"Certificat du serveur refusé (VeNCrypt) : {detail}"
        elif isinstance(exc, ssl.SSLError):
            message = f"Échec de la négociation TLS (VeNCrypt) : {exc}"
        else:
            message = f"Échec de connexion : {exc}"
        logger.debug("VncDisplay._describe_connection_error | sortie {m}", m=message)
        return message

    async def _connect_and_run(self):
        logger.debug(
            "VncDisplay._connect_and_run | enter | host={host} port={port} shared={shared}",
            host=self.info.host,
            port=self.info.port,
            shared=self.info.shared,
        )
        mode = self._resolve_connection_mode(self.info.reverse, self.info.host, self.info.port, self.info.pin_host_key)
        opener = getattr(asyncvnc2, mode["opener"])
        self._set_status(VncStatus.CONNECTING, mode["status"])

        pinned_key = None
        if mode["pin"]:
            pinned_key = self.host_key_store.get(self.info.host, self.info.port)

        connect_kwargs = {
            "username": self.info.username,
            "password": self.info.password,
            "host_key": pinned_key,
            "encodings": self.info.encodings,
            "jpeg_quality": self.info.jpeg_quality,
            "compression_level": self.info.compression_level,
            "allow_indexed_colour": self.info.allow_indexed_colour,
        }
        try:
            connect_kwargs.update(self._resolve_transport_kwargs(self.info.websocket, self.info.ws_path))
        except ValueError as exc:
            logger.error("VncDisplay._connect_and_run | {exc} | host={host}", exc=exc, host=self.info.host)
            self._set_status(VncStatus.ERROR, f"Chemin WebSocket invalide : {self.info.ws_path!r}")
            logger.debug("VncDisplay._connect_and_run | sortie chemin WebSocket invalide")
            return

        try:
            connect_kwargs.update(self._resolve_tls_kwargs(self.info.tls_ca_file))
        except ValueError as exc:
            logger.error("VncDisplay._connect_and_run | {exc} | host={host}", exc=exc, host=self.info.host)
            self._set_status(VncStatus.ERROR, f"VeNCrypt : {exc}")
            logger.debug("VncDisplay._connect_and_run | sortie CA privée illisible")
            return

        try:
            try:
                self._ctx = opener(self.info.host, self.info.port, shared=self.info.shared, **connect_kwargs)
                self.client = await self._ctx.__aenter__()
            except TypeError:
                # ton asyncvnc2 n'a pas encore shared_flag_patch.py appliqué —
                # on retombe sur le comportement historique (session partagée).
                logger.debug(
                    "VncDisplay._connect_and_run | connect() ne supporte pas shared= | "
                    "host={host} | repli sans ce paramètre",
                    host=self.info.host,
                )
                self._ctx = opener(self.info.host, self.info.port, **connect_kwargs)
                self.client = await self._ctx.__aenter__()
        except PermissionError as exc:
            logger.error(
                "VncDisplay._connect_and_run | authentification refusée | "
                "host={host} port={port} pinned_key={pinned_key}",
                host=self.info.host,
                port=self.info.port,
                pinned_key=pinned_key is not None,
                exception=True,
            )
            if pinned_key is not None:
                self._set_status(
                    VncStatus.ERROR,
                    "Authentification refusée — la clé enregistrée pour ce "
                    "serveur ne correspond peut-être plus à la sienne "
                    "(réinstallation du serveur, ou à vérifier en cas de "
                    "doute avant de l'oublier).",
                )
            else:
                self._set_status(VncStatus.ERROR, f"Authentification refusée : {exc}")
            return
        except Exception as exc:
            logger.error(
                "VncDisplay._connect_and_run | échec de connexion | host={host} port={port}",
                host=self.info.host,
                port=self.info.port,
                exception=True,
            )
            self._set_status(VncStatus.ERROR, self._describe_connection_error(exc))
            return

        # Après un succès, mémoriser/rafraîchir la clé hôte si l'auth Apple
        # ARD en a fourni une (no-op pour les serveurs VNC classiques).
        if mode["pin"] and self.client.host_key is not None:
            self.host_key_store.set(self.info.host, self.info.port, self.client.host_key)

        title = getattr(self.client.video, "name", None) or self.info.name
        self.emit("vnc-title-changed", title)

        try:
            pixels = await self.client.screenshot()
            self._check_screens_changed()
            self._push_frame(self._apply_active_screen_crop(pixels))
            self._check_size_changed()
        except Exception:
            logger.error(
                "VncDisplay._connect_and_run | erreur au premier rendu | host={host}",
                host=self.info.host,
                exception=True,
            )
            self._set_status(VncStatus.ERROR, "Erreur au premier rendu")
            return

        logger.info(
            "VncDisplay._connect_and_run | connecté | host={host} port={port} size={width}x{height} title={title!r}",
            host=self.info.host,
            port=self.info.port,
            width=self.client.video.width,
            height=self.client.video.height,
            title=title,
        )
        self._set_status(VncStatus.CONNECTED, "Connecté")
        self._read_task = asyncio.ensure_future(self._read_loop())
        self.emit("vnc-connected")

    def _handle_video_update(self):
        self._check_cursor_changed()
        self._check_size_changed()
        self._check_screens_changed()
        pixels = self.client.video.as_rgba()
        self._push_frame(self._apply_active_screen_crop(pixels))

        # En mode ContinuousUpdates, le serveur pousse les mises à jour
        # sans qu'on ait à les redemander. Sinon, chaque rafraîchissement
        # doit être explicitement redemandé — c'est ce qui manquait avant.
        if not self.client.continuous_updates_active:
            self.client.video.refresh()

    def _handle_clipboard_update(self):
        if not self.info.sync_clipboard:
            return
        text = self.client.clipboard.text
        self._last_clipboard_text = text
        self._set_system_clipboard(text)

    def _handle_bell_update(self):
        display = self.get_display()
        if display is not None:
            display.beep()

    async def _read_loop(self):
        logger.debug("VncDisplay._read_loop | enter | host={host}", host=self.info.host)
        try:
            # Premier rafraîchissement : screenshot() a déjà consommé la
            # toute première image, il faut explicitement en redemander une
            # ensuite (voir la note en tête de fichier).
            if not self.client.continuous_updates_active:
                self.client.video.refresh()

            while True:
                update = await self.client.read()

                if update == asyncvnc2.UpdateType.VIDEO:
                    self._handle_video_update()
                elif update == asyncvnc2.UpdateType.CLIPBOARD:
                    self._handle_clipboard_update()
                elif update == asyncvnc2.UpdateType.BELL:
                    self._handle_bell_update()

                # SERVER_FENCE et END_OF_CONTINUOUS_UPDATES sont déjà
                # traités en interne par Client.read() (réponse automatique
                # au fence, mise à jour de continuous_updates_active) —
                # rien à faire ici de plus.

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "VncDisplay._read_loop | connexion interrompue | host={host}",
                host=self.info.host,
                exception=True,
            )
            self._set_status(VncStatus.ERROR, f"Connexion interrompue : {exc}")

    # ---- Rendu ----

    def _push_frame(self, rgba_array):
        height, width, _ = rgba_array.shape
        data = rgba_array.tobytes()
        gbytes = GLib.Bytes.new(data)
        texture = Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8A8, gbytes, width * 4)
        self.set_paintable(texture)
        self._last_frame_texture = texture

    def save_screenshot(self, path) -> bool:
        """Enregistre en PNG l'image actuellement affichée (dernier frame
        poussé par _push_frame -- déjà recadré sur l'écran actif si un
        écran spécifique est sélectionné, cf. _apply_active_screen_crop).

        Lecture locale pure : ne touche JAMAIS self.client ni le socket, donc
        pas concerné par la famille de garde "connexion déjà morte" décrite
        dans docs/pieges.md -- rien n'est écrit sur le réseau ici, seulement
        sur disque. Renvoie False (plutôt que de lever) si aucune image n'a
        encore été reçue (avant le tout premier rendu) ou si l'écriture
        disque échoue (permissions, disque plein, chemin invalide) : à
        l'appelant -- un bouton de la barre d'outils -- de prévenir
        l'utilisateur plutôt que de laisser une exception remonter hors d'un
        callback GTK.
        """
        if self._last_frame_texture is None:
            logger.warning(
                "VncDisplay.save_screenshot | aucune image disponible pour l'instant | host={host}",
                host=self.info.host,
            )
            return False
        try:
            self._last_frame_texture.save_to_png(str(path))
        except Exception:
            logger.error(
                "VncDisplay.save_screenshot | échec d'écriture | host={host} path={path}",
                host=self.info.host,
                path=str(path),
                exception=True,
            )
            return False
        logger.info(
            "VncDisplay.save_screenshot | image enregistrée | host={host} path={path}",
            host=self.info.host,
            path=str(path),
        )
        return True

    def copy_screenshot_to_clipboard(self) -> bool:
        """Copie l'image actuellement affichée dans le presse-papiers système
        (`Gdk.Clipboard.set_texture()`), sans passer par un fichier.

        Même source que `save_screenshot()` (`_last_frame_texture`, dernier
        frame poussé par `_push_frame()` -- déjà recadré sur l'écran actif le
        cas échéant) et même contrat défensif : lecture locale pure, ne
        touche JAMAIS `self.client` ni le socket, renvoie `False` (plutôt que
        de lever) si aucune image n'est encore disponible ou si la copie
        échoue, plutôt que de laisser une exception remonter hors d'un
        callback GTK.

        `self.get_clipboard()` (`Gtk.Widget.get_clipboard()`) fonctionne même
        avant que le widget soit réalisé -- pas besoin d'un display GTK déjà
        affiché pour que l'appel soit valide.
        """
        if self._last_frame_texture is None:
            logger.warning(
                "VncDisplay.copy_screenshot_to_clipboard | aucune image disponible pour l'instant | host={host}",
                host=self.info.host,
            )
            return False
        try:
            self.get_clipboard().set_texture(self._last_frame_texture)
        except Exception:
            logger.error(
                "VncDisplay.copy_screenshot_to_clipboard | échec de la copie | host={host}",
                host=self.info.host,
                exception=True,
            )
            return False
        logger.info(
            "VncDisplay.copy_screenshot_to_clipboard | image copiée dans le presse-papiers | host={host}",
            host=self.info.host,
        )
        return True

    def _check_cursor_changed(self):
        cursor = self.client.video.cursor
        if cursor is not self._last_cursor:
            self._last_cursor = cursor
            self.emit("vnc-cursor-changed", cursor)

    def _check_size_changed(self):
        size = (self.client.video.width, self.client.video.height)
        if size != self._last_size:
            self._last_size = size
            self.emit("vnc-size-changed", size[0], size[1])

    def _check_screens_changed(self):
        screens = self.client.video.screens
        if screens != self._last_screens:
            self._last_screens = screens
            self.emit("vnc-screens-changed", screens)

    def _find_active_screen(self):
        for screen in self._last_screens:
            if screen.id == self._active_screen_id:
                return screen
        return None

    def _apply_active_screen_crop(self, rgba_array):
        if self._active_screen_id is None:
            return rgba_array
        screen = self._find_active_screen()
        if screen is None:
            # L'écran choisi a disparu (déconnexion d'un moniteur côté
            # distant) -- on retombe sur le bureau complet plutôt que de
            # planter sur un découpage hors bornes.
            return rgba_array
        ys, xs = screen.slices
        return rgba_array[ys, xs]

    def set_active_screen(self, screen_id: Optional[int]):
        """
        Restreint l'affichage à un écran particulier (parmi ceux annoncés
        par le serveur via ExtendedDesktopSize), ou None pour le bureau
        complet. Les coordonnées souris continuent d'être converties vers
        le référentiel absolu du framebuffer distant (cf. widget_to_remote).
        """
        self._active_screen_id = screen_id
        if self.client:
            pixels = self.client.video.as_rgba()
            self._push_frame(self._apply_active_screen_crop(pixels))

    # ---- Actions spéciales ----

    #: Commandes d'alimentation xvp (#104) : nom → méthode ``send_xvp_<nom>``
    #: d'asyncvnc2.Client.
    POWER_ACTIONS = ("shutdown", "reboot", "reset")

    def xvp_supported(self) -> bool:
        """Vrai si la connexion est établie et que le serveur a annoncé xvp
        (message XVP_INIT, reçu de façon asynchrone après la connexion :
        à relire au moment d'afficher le menu, pas une fois pour toutes)."""
        logger.debug("VncDisplay.xvp_supported | entrée host={host}", host=self.info.host)
        resultat = bool(self.client is not None and getattr(self.client, "xvp_supported", False))
        logger.debug("VncDisplay.xvp_supported | sortie {r}", r=resultat)
        return resultat

    def send_power_action(self, action: str) -> bool:
        """Envoie la commande xvp ``action`` (``shutdown``, ``reboot`` ou
        ``reset``) au serveur, avec ``confirm=True`` : l'appelant DOIT avoir
        obtenu la confirmation de l'utilisateur avant (cf. le dialogue de
        VncTab._show_power_confirmation_dialog). Renvoie False si rien n'est
        parti (pas de connexion).

        Raises:
            ValueError: ``action`` inconnue.
        """
        logger.debug("VncDisplay.send_power_action | entrée action={a} host={host}", a=action, host=self.info.host)
        if action not in self.POWER_ACTIONS:
            logger.debug("VncDisplay.send_power_action | sortie action inconnue")
            raise ValueError(action)
        if not self.client:
            logger.debug("VncDisplay.send_power_action | sortie sans connexion")
            return False
        asyncio.ensure_future(self._send_power_action(action))
        logger.debug("VncDisplay.send_power_action | sortie envoyée")
        return True

    async def _send_power_action(self, action: str):
        logger.debug("VncDisplay._send_power_action | entrée action={a}", a=action)
        try:
            await getattr(self.client, f"send_xvp_{action}")(confirm=True)
        except Exception as exc:
            # Même famille de garde que _send_cad (coroutine fire-and-forget,
            # connexion morte possible) ; journalisé, pas silencieux : une
            # commande d'alimentation perdue doit se retrouver dans le journal.
            logger.warning(
                "VncDisplay._send_power_action | échec action={a} host={host} : {exc}",
                a=action,
                host=self.info.host,
                exc=exc,
            )
        logger.debug("VncDisplay._send_power_action | sortie")

    def send_ctrl_alt_del(self):
        if self.client:
            asyncio.ensure_future(self._send_cad())

    async def _send_cad(self):
        try:
            with self.client.keyboard.hold("Control_L", "Alt_L"):
                self.client.keyboard.press("Delete")
        except Exception:
            # Même famille de garde que _on_motion/_on_button_pressed/
            # _on_button_released/_on_scroll/_on_key_pressed/
            # _on_local_clipboard_read (self.client jamais remis à None
            # après une erreur de lecture, cf. docs/pieges.md) : cette
            # coroutine est passée à asyncio.ensure_future() par
            # send_ctrl_alt_del(), donc fire-and-forget -- sans ce garde,
            # une exception ici devenait un « Task exception was never
            # retrieved » silencieux (avalé par le handler par défaut
            # d'asyncio) plutôt qu'une remontée hors d'un callback GTK. Le
            # bouton "Ctrl+Alt+Suppr" de la barre d'outils reste cliquable
            # dans tous les états (CONNECTING/ERROR/DISCONNECTED compris),
            # contrairement à btn_continuous pour set_continuous_updates
            # ci-dessous -- priorité suggérée en session 14, traité en
            # session 15, cf. docs/sessions/session-15.md et
            # tests/test_send_cad_dead_connection.py.
            pass

    def send_text(self, text: str):
        """
        Envoie `text` au serveur distant comme une suite de frappes
        clavier (une par caractère), en CONTOURNANT le presse-papiers --
        utile quand la synchronisation presse-papiers est désactivée par
        profil (VncConnectionInfo.sync_clipboard) ou pour un caractère que
        le clavier physique local ne produit pas directement.

        Ne fait rien si `text` est vide ou si la connexion n'est pas
        établie -- même garde que send_ctrl_alt_del() ci-dessus.
        """
        if self.client and text:
            asyncio.ensure_future(self._send_text(text))

    async def _send_text(self, text: str):
        for char in text:
            vnc_name = self._resolve_vnc_key_name_for_char(char)
            if vnc_name is None:
                continue
            try:
                self.client.keyboard.press(vnc_name)
            except Exception:
                # Un caractère isolé qui échoue -- touche non reconnue par
                # asyncvnc2 (cas normal, même famille que le `except
                # KeyError` de _on_key_pressed plus bas) OU connexion
                # tombée en cours d'envoi (même famille de garde que
                # _send_cad ci-dessus -- self.client jamais remis à None
                # après une erreur de lecture, cf. docs/pieges.md) -- ne
                # doit pas interrompre l'envoi des caractères suivants du
                # même texte. Pas de distinction KeyError/Exception ici
                # contrairement à _on_key_pressed : dans les deux cas le
                # comportement voulu est identique (passer au caractère
                # suivant), et le comportement d'exception de
                # keyboard.press() pour un nom non reconnu n'est pas
                # confirmé contre le fork réel (pas livré dans ce paquet,
                # cf. docs/features-backlog.md § « Dépendance ») -- un
                # garde générique suffit, sans dépendre de cette
                # hypothèse. Si la connexion est réellement morte, chaque
                # caractère suivant échouera à son tour de la même façon,
                # sans boucle infinie (bornée par len(text)).
                continue

    def toggle_scaling(self):
        current = self.get_content_fit()
        self.set_content_fit(Gtk.ContentFit.FILL if current == Gtk.ContentFit.CONTAIN else Gtk.ContentFit.CONTAIN)

    # ---- Presse-papiers bidirectionnel ----

    def _watch_local_clipboard(self):
        """À appeler une fois le widget rattaché à un Gdk.Display (signal
        'realize'), pour surveiller les changements du presse-papiers
        système et les relayer vers le serveur."""
        if not self.info.sync_clipboard:
            return
        display = self.get_display()
        if display is None or display.get_clipboard() is self._clipboard_watched:
            return
        self._clipboard_watched = display.get_clipboard()
        self._clipboard_watched.connect("changed", self._on_local_clipboard_changed)

    def _on_local_clipboard_changed(self, clipboard):
        clipboard.read_text_async(None, self._on_local_clipboard_read)

    @staticmethod
    def _resolve_clipboard_send_mode(extended_supported: bool, text: str) -> str:
        """Comment envoyer ``text`` au serveur (Gcm4 #104, tâche 1).

        - ``"extended"`` : le serveur a confirmé Extended Clipboard →
          ``send_clipboard_update()`` (Notify puis Provide, UTF-8) ;
        - ``"legacy"`` : sinon, ClientCutText historique si ``text`` tient en
          Latin-1 ;
        - ``"unsupported"`` : ni l'un ni l'autre (caractère hors Latin-1 et
          serveur sans Extended Clipboard) → rien n'est envoyé.
        """
        logger.debug(
            "VncDisplay._resolve_clipboard_send_mode | entrée extended={e} longueur={n}",
            e=extended_supported,
            n=len(text),
        )
        if extended_supported:
            mode = "extended"
        else:
            try:
                text.encode("latin-1")
                mode = "legacy"
            except UnicodeEncodeError:
                mode = "unsupported"
        logger.debug("VncDisplay._resolve_clipboard_send_mode | sortie {m}", m=mode)
        return mode

    async def _send_clipboard_extended(self, text: str):
        """Envoi Extended Clipboard, fire-and-forget : un échec (connexion
        morte, support retiré) est journalisé, pas remonté d'une tâche
        asyncio orpheline (même garde que _send_cad)."""
        logger.debug("VncDisplay._send_clipboard_extended | entrée longueur={n}", n=len(text))
        try:
            await self.client.send_clipboard_update(text)
        except Exception as exc:
            logger.warning(
                "VncDisplay._send_clipboard_extended | échec host={host} : {exc}",
                host=self.info.host,
                exc=exc,
            )
        logger.debug("VncDisplay._send_clipboard_extended | sortie")

    def _on_local_clipboard_read(self, clipboard, result):
        logger.debug("VncDisplay._on_local_clipboard_read | entrée")
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            logger.trace("VncDisplay._on_local_clipboard_read | contenu non textuel ou illisible, ignoré")
            logger.debug("VncDisplay._on_local_clipboard_read | sortie contenu non textuel")
            return
        if not text or text == self._last_clipboard_text or not self.client:
            logger.debug("VncDisplay._on_local_clipboard_read | sortie rien à envoyer")
            return
        self._last_clipboard_text = text
        mode = self._resolve_clipboard_send_mode(bool(getattr(self.client, "clipboard_ext_supported", False)), text)
        if mode == "extended":
            asyncio.ensure_future(self._send_clipboard_extended(text))
            logger.debug("VncDisplay._on_local_clipboard_read | sortie Extended Clipboard")
            return
        if mode == "unsupported":
            logger.warning(
                "VncDisplay._on_local_clipboard_read | texte hors Latin-1 et serveur sans "
                "Extended Clipboard : presse-papiers non transmis | host={host}",
                host=self.info.host,
            )
            logger.debug("VncDisplay._on_local_clipboard_read | sortie non transmis")
            return
        try:
            self.client.clipboard.write(text)
        except Exception as exc:
            # Même famille de garde que _on_motion/_on_button_pressed/
            # _on_button_released/_on_scroll/_on_key_pressed (self.client
            # jamais remis à None après une erreur de lecture, cf.
            # docs/pieges.md) : le garde `if not self.client:` ci-dessus ne
            # protège pas le cas où la connexion tombe juste avant que
            # l'utilisateur copie quelque chose localement -- clipboard.write()
            # écrit réellement sur le socket et peut lever sur une connexion
            # déjà morte. Ce callback est invoqué directement par GLib
            # (read_text_async), donc sans ce garde l'exception remontait
            # telle quelle hors du callback, exactement comme pour les cinq
            # chemins d'entrée déjà protégés -- sixième point de cette
            # famille, trouvé en auditant tous les accès à self.client (cf.
            # docs/sessions/session-14.md et
            # tests/test_clipboard_dead_connection.py).
            logger.debug(
                "VncDisplay._on_local_clipboard_read | écriture impossible, connexion morte ? {t}: {exc}",
                t=type(exc).__name__,
                exc=exc,
            )
        logger.debug("VncDisplay._on_local_clipboard_read | sortie ClientCutText")

    def _set_system_clipboard(self, text: str):
        display = self.get_display()
        if display is None or not text:
            return
        provider = Gdk.ContentProvider.new_for_value(GObject.Value(str, text))
        display.get_clipboard().set_content(provider)

    def get_display_transform(self):
        """
        Calcule (scale_x, scale_y, offset_x, offset_y) pour convertir entre
        coordonnées widget et coordonnées framebuffer distant, compte tenu
        du ContentFit courant (CONTAIN = échelle uniforme + letterboxing
        centré, FILL = échelle indépendante en x/y, pas d'offset) et de
        l'écran actif s'il y en a un (cf. set_active_screen).
        Retourne None tant qu'on n'a pas de taille distante ou de taille
        allouée connues (ex: avant le premier rendu).
        """
        if not self.client:
            return None

        screen = self._find_active_screen() if self._active_screen_id is not None else None
        if screen is not None:
            remote_w, remote_h = screen.width, screen.height
        else:
            # Bureau complet -- soit aucun écran n'est sélectionné, soit
            # l'écran sélectionné a disparu (déconnexion d'un moniteur
            # côté distant). Dans les deux cas, _apply_active_screen_crop
            # affiche déjà le framebuffer complet sans crop : le calcul de
            # transform doit suivre la MÊME bascule, sinon on continue de
            # convertir les coordonnées comme si un crop était encore
            # actif alors que l'image affichée, elle, ne l'est plus --
            # décalage silencieux entre où l'utilisateur clique à l'écran
            # et où le clic atterrit réellement côté serveur. Bug réel
            # trouvé et corrigé le 2026-09-04, cf.
            # tests/test_display_transform.py.
            remote_w = self.client.video.width
            remote_h = self.client.video.height
        if not remote_w or not remote_h:
            return None

        alloc_w = self.get_width()
        alloc_h = self.get_height()
        if alloc_w <= 0 or alloc_h <= 0:
            return None

        if self.get_content_fit() == Gtk.ContentFit.FILL:
            return alloc_w / remote_w, alloc_h / remote_h, 0.0, 0.0

        # CONTAIN : échelle uniforme, image centrée (letterboxing)
        scale = min(alloc_w / remote_w, alloc_h / remote_h)
        offset_x = (alloc_w - remote_w * scale) / 2
        offset_y = (alloc_h - remote_h * scale) / 2
        return scale, scale, offset_x, offset_y

    def widget_to_remote(self, x, y):
        """Convertit des coordonnées widget (ex: EventControllerMotion) en
        coordonnées framebuffer distant ABSOLUES, pour un pointage souris
        précis même quand l'affichage est mis à l'échelle et/ou restreint à
        un seul écran (cf. set_active_screen)."""
        transform = self.get_display_transform()
        if transform is None:
            return int(x), int(y)
        scale_x, scale_y, offset_x, offset_y = transform
        rx = (x - offset_x) / scale_x if scale_x else 0.0
        ry = (y - offset_y) / scale_y if scale_y else 0.0

        if self._active_screen_id is not None:
            screen = self._find_active_screen()
            if screen is not None:
                # Le crop affiché est local à l'écran choisi -- il faut
                # rajouter son décalage pour retrouver des coordonnées
                # absolues dans le framebuffer complet (mouse.move() les
                # attend en absolu, pas en relatif à l'écran affiché).
                rx += screen.x
                ry += screen.y

        return int(rx), int(ry)

    def set_continuous_updates(self, enable: bool):
        """
        Active/désactive ContinuousUpdates. À utiliser avec prudence : au
        moins un serveur testé (TigerVNC 1.13.1) refuse purement et
        simplement la demande — voir CLAUDE.md. En cas de refus silencieux,
        la boucle de lecture continue de fonctionner correctement puisque
        continuous_updates_active reste à False côté client.
        """
        if not self.client:
            return
        asyncio.ensure_future(self._send_enable_continuous_updates(enable))

    async def _send_enable_continuous_updates(self, enable: bool):
        try:
            await self.client.send_enable_continuous_updates(
                enable, 0, 0, self.client.video.width, self.client.video.height
            )
        except Exception:
            # Même famille de garde que _send_cad ci-dessus, deuxième et
            # dernier point fire-and-forget identifié en session 14
            # (docs/features-backlog.md § « Backlog ouvert »). La coroutine
            # renvoyée par client.send_enable_continuous_updates(...) était
            # jusqu'ici passée DIRECTEMENT à asyncio.ensure_future(), sans
            # aucun wrapper à envelopper dans un try/except -- ce wrapper
            # ajoute ce point d'ancrage. Fenêtre plus étroite en pratique
            # que _send_cad : btn_continuous est déjà désactivé hors de
            # l'état CONNECTED (contrairement au bouton Ctrl+Alt+Suppr),
            # mais le trou reste réel si la connexion tombe juste avant
            # l'envoi. Traité en session 15, cf.
            # docs/sessions/session-15.md et
            # tests/test_continuous_updates_dead_connection.py.
            pass

    # ---- Entrées clavier / souris ----

    def _setup_input_controllers(self):
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        key_ctrl.connect("key-released", self._on_key_released)
        self.add_controller(key_ctrl)

        # Filet de sécurité : si le widget perd le focus (ex. Alt-Tab)
        # pendant qu'une touche est physiquement enfoncée, on ne recevra
        # jamais le key-released correspondant -> on relâche tout pour
        # éviter une touche "collée" côté distant.
        focus_ctrl = Gtk.EventControllerFocus()
        focus_ctrl.connect("leave", lambda *_: self._release_all_key_holds())
        self.add_controller(focus_ctrl)

        click = Gtk.GestureClick()
        click.set_button(0)  # tous les boutons
        click.connect("pressed", self._on_button_pressed)
        click.connect("released", self._on_button_released)
        self.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        self.connect("realize", lambda *_: self._watch_local_clipboard())

    def _resolve_vnc_key_name(self, keyval) -> Optional[str]:
        """
        Convertit un keyval GDK en nom de touche compris par asyncvnc2.

        1) dérogation explicite (GDK_TO_VNC_KEY)
        2) nom de keysym X11 tel que renvoyé par GDK (couvre déjà l'AZERTY,
           les accents et les touches mortes, cf. commentaire plus haut)
        3) filet de secours : caractère Unicode produit par la touche (utile
           pour les touches sans nom keysym X11 classique, ex. certains
           claviers étendus) -- key_codes reconnaît aussi les caractères
           bruts, pas seulement les noms de keysyms.
        """
        name = Gdk.keyval_name(keyval)
        if name in GDK_TO_VNC_KEY:
            return GDK_TO_VNC_KEY[name]
        if name:
            return name
        codepoint = Gdk.keyval_to_unicode(keyval)
        if codepoint:
            return chr(codepoint)
        return None

    def _resolve_vnc_key_name_for_char(self, char: str) -> Optional[str]:
        """
        Convertit un caractère Unicode isolé (utilisé par send_text() pour
        envoyer du texte comme une suite de frappes) en nom de touche
        compris par asyncvnc2, en réutilisant _resolve_vnc_key_name()
        ci-dessus plutôt qu'une résolution séparée.

        Gdk.unicode_to_keyval() renvoie soit le vrai keysym X11 nommé
        quand il existe (couvre lettres, chiffres, ponctuation, l'essentiel
        de l'AZERTY -- même mécanisme que la frappe physique réelle, cf.
        _resolve_vnc_key_name ci-dessus), soit -- pour un caractère sans
        keysym X11 classique -- un keysym Unicode synthétique de la forme
        `point_de_code | 0x01000000` (convention X11/GDK, vérifiée le
        2026-09-11 contre docs.gtk.org avant d'écrire cette méthode).
        Dans ce second cas, Gdk.keyval_name() ne renvoie aucun nom pour ce
        keysym synthétique, et _resolve_vnc_key_name() retombe alors sur
        Gdk.keyval_to_unicode(), qui sait retrouver le point de code
        d'origine dans cette même plage -- le caractère est alors transmis
        tel quel, exactement comme le filet de secours déjà en place pour
        une touche physique sans nom keysym classique.
        """
        return self._resolve_vnc_key_name(Gdk.unicode_to_keyval(ord(char)))

    def _on_key_pressed(self, ctrl, keyval, keycode, state):
        if not self.client:
            return True
        # Garde anti-répétition : le système envoie des key-pressed répétés
        # tant que la touche est physiquement enfoncée (auto-repeat). On ne
        # relance pas un appui sur une touche déjà tenue -- ça correspond
        # au comportement réel d'une touche maintenue côté clavier distant.
        if keycode in self._active_key_holds:
            return True
        vnc_name = self._resolve_vnc_key_name(keyval)
        if vnc_name is None:
            return True  # touche sans équivalent connu, on ignore proprement
        try:
            hold_cm = self.client.keyboard.hold(vnc_name)
            hold_cm.__enter__()
        except KeyError:
            logger.warning(
                "VncDisplay._on_key_pressed | touche non reconnue par asyncvnc2 | "
                "host={host} vnc_name={vnc_name!r} keyval={keyval} keycode={keycode}",
                host=self.info.host,
                vnc_name=vnc_name,
                keyval=keyval,
                keycode=keycode,
            )
            return True
        except Exception:
            # Même famille de garde que _on_motion/_on_button_pressed/
            # _on_button_released (self.client jamais remis à None après une
            # erreur de lecture, cf. docs/pieges.md) : contrairement au
            # KeyError ci-dessus (touche non reconnue par asyncvnc2, cas
            # normal), ceci couvre une écriture sur un socket déjà mort --
            # à la fois dans keyboard.hold() lui-même et dans
            # hold_cm.__enter__(), qui écrit sur le socket. Sans ce garde,
            # l'exception remontait telle quelle hors du gestionnaire de
            # signal GTK, contrairement aux quatre chemins souris déjà
            # protégés (session 12).
            return True
        self._active_key_holds[keycode] = hold_cm
        return True

    def _on_key_released(self, ctrl, keyval, keycode, state):
        hold_cm = self._active_key_holds.pop(keycode, None)
        if hold_cm is not None:
            try:
                hold_cm.__exit__(None, None, None)
            except Exception:
                pass
        return True

    def _on_motion(self, ctrl, x, y):
        if not self.client:
            return
        rx, ry = self.widget_to_remote(x, y)
        try:
            self.client.mouse.move(rx, ry)
        except Exception:
            # Même famille de garde que _on_button_pressed/_on_button_released
            # (corrigés le 2026-09-08/09) : self.client n'est jamais remis à
            # None après une erreur de lecture (_read_loop se contente de
            # _set_status(ERROR, ...)), donc le garde `if not self.client:`
            # en tête de cette méthode ne protège pas le cas où la connexion
            # tombe juste avant un déplacement -- un mouse.move() sur un
            # socket déjà mort peut lever, et sans ce garde l'exception
            # remontait telle quelle hors du gestionnaire de signal GTK
            # (appelé à haute fréquence pendant un déplacement de souris,
            # contrairement aux appuis/relâchements ponctuels déjà protégés).
            pass

    def _on_button_pressed(self, gesture, n_press, x, y):
        if not self.client:
            return
        self.grab_focus()
        rx, ry = self.widget_to_remote(x, y)
        button = self._gdk_button_to_vnc(gesture.get_current_button())
        try:
            self.client.mouse.move(rx, ry)
            if button in self._active_mouse_holds:
                # _gdk_button_to_vnc traduit délibérément tout bouton GDK non
                # reconnu (boutons latéraux 8/9, etc.) vers 0 (clic gauche) --
                # deux boutons PHYSIQUES différents peuvent donc se traduire
                # vers le MÊME bouton VNC. Sans ce garde (symétrique de celui
                # déjà présent dans _on_key_pressed), un second appui ainsi
                # traduit écraserait silencieusement la référence au premier
                # hold dans _active_mouse_holds -- celui-ci ne serait alors
                # JAMAIS relâché côté serveur (bouton "collé" au premier
                # relâchement physique reçu, quel qu'il soit). Bug réel trouvé
                # et corrigé le 2026-09-06, cf.
                # tests/test_mouse_button_holds.py.
                return
            # Maintien réel du bouton entre pressed et released, pour que le
            # drag (sélection, glisser-déposer) fonctionne côté serveur.
            hold_cm = self.client.mouse.hold(button)
            hold_cm.__enter__()
        except Exception:
            # Symétrique du garde déjà présent dans _on_button_released
            # (__exit__, corrigé le 2026-09-09) et dans _on_key_released,
            # mais côté __enter__()/écriture initiale. Rien ne remet
            # self.client à None sur une erreur de lecture (_read_loop se
            # contente de _set_status(ERROR, ...)) : le garde `if not
            # self.client:` en tête de cette méthode ne protège donc pas le
            # cas où la connexion tombe juste avant cet appui -- un
            # `mouse.move()`/`mouse.hold().__enter__()` sur un socket déjà
            # mort peut lever, et sans ce garde l'exception remontait telle
            # quelle hors du gestionnaire de signal GTK. Même famille de bug
            # que celui corrigé le 2026-09-09 dans _on_button_released --
            # voir tests/test_mouse_button_holds.py.
            return
        self._active_mouse_holds[button] = hold_cm

    def _on_button_released(self, gesture, n_press, x, y):
        if not self.client:
            return
        button = self._gdk_button_to_vnc(gesture.get_current_button())
        hold_cm = self._active_mouse_holds.pop(button, None)
        if hold_cm is not None:
            try:
                hold_cm.__exit__(None, None, None)
            except Exception:
                # Symétrique du garde déjà présent dans _on_key_released,
                # pour une raison différente du garde anti-collision de
                # _on_button_pressed ci-dessus (2026-09-06). Si la connexion
                # tombe PENDANT qu'un bouton est physiquement maintenu (ex.
                # coupure réseau en plein drag), le hold correspondant reste
                # dans _active_mouse_holds : _read_loop ne fait que
                # _set_status(ERROR, ...) sur exception, sans jamais
                # toucher aux holds actifs (seul stop() les relâche tous,
                # cf. _release_all_mouse_holds), et self.client n'est
                # JAMAIS remis à None après une erreur de lecture -- seule
                # une nouvelle connexion réussie le réaffecte -- donc le
                # garde `if not self.client:` en tête de cette méthode ne
                # protège pas non plus ce chemin. Le relâchement physique
                # du bouton arrive donc normalement, __exit__() tente
                # d'écrire sur un socket déjà fermé et peut lever -- sans ce
                # garde, l'exception remontait telle quelle hors du
                # gestionnaire de signal GTK. _on_key_released a déjà
                # exactement ce garde depuis le début ; _on_button_released
                # ne l'avait pas. Bug réel trouvé et corrigé le 2026-09-09,
                # cf. tests/test_mouse_button_holds.py.
                pass

    @staticmethod
    def _resolve_effective_scroll_delta(dy: float, invert: bool) -> float:
        """
        Applique l'inversion de molette de la connexion
        (`VncConnectionInfo.invert_scroll`) au delta vertical brut
        rapporté par GTK, avant que `_on_scroll()` ci-dessous décide
        quelle méthode appeler. Méthode statique pure (même style que
        `_resolve_reconnect_delay_seconds`) pour rester testable
        indépendamment du contrôleur GTK et du client VNC -- le sens
        conventionnel (`dy > 0` = molette vers le bas dans
        `Gtk.EventControllerScroll`) est facile à inverser deux fois par
        erreur si on ne le vérifie pas explicitement.
        """
        return -dy if invert else dy

    def _on_scroll(self, ctrl, dx, dy):
        if not self.client:
            return True
        dy = self._resolve_effective_scroll_delta(dy, self.info.invert_scroll)
        try:
            if dy > 0:
                self.client.mouse.scroll_down()
            elif dy < 0:
                self.client.mouse.scroll_up()
        except Exception:
            # Même famille de garde que _on_motion (2026-09-11) et
            # _on_button_pressed/_on_button_released (2026-09-08/09) :
            # self.client n'est jamais remis à None après une erreur de
            # lecture (_read_loop se contente de _set_status(ERROR, ...)),
            # donc le garde `if not self.client:` en tête de cette méthode
            # ne protège pas le cas où la connexion tombe pendant un
            # scroll -- scroll_down()/scroll_up() sur un socket déjà mort
            # peut lever, et sans ce garde l'exception remontait telle
            # quelle hors du gestionnaire de signal GTK. Dernier des trois
            # chemins souris de cette famille (avec _on_motion et les deux
            # méthodes bouton) à recevoir ce garde.
            pass
        return True

    @staticmethod
    def _gdk_button_to_vnc(gdk_button):
        # GDK : 1=gauche, 2=milieu, 3=droit -> asyncvnc2 : 0=gauche, 1=milieu, 2=droit
        return {1: 0, 2: 1, 3: 2}.get(gdk_button, 0)


# ---------------------------------------------------------------------------
# Onglet complet : barre d'outils + placeholder + affichage + curseur serveur
# ---------------------------------------------------------------------------


class VncTab(Gtk.Box):
    """
    Onglet VNC prêt à empaqueter dans un conteneur d'onglets.

    Signaux :
      - "tab-title-changed" (str)   : nom du bureau distant
      - "tab-status-changed" (str)  : valeur de VncStatus
      - "close-requested" ()        : fermeture demandée par l'utilisateur
    """

    __gsignals__ = {
        "tab-title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "tab-status-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "close-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, info: VncConnectionInfo):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.info = info
        self._last_mouse_xy = (0, 0)
        self._current_cursor = None
        self._last_remote_size = None
        self._continuous_updates_on = False
        self._screens = []
        self._updating_screen_dropdown = False
        self._is_fullscreen = False
        # Case « Garder la fenêtre ouverte après l'envoi » du dialogue
        # d'envoi de texte (#93) : mémorisée pour la durée de vie de
        # l'onglet, décochée par défaut (comportement historique).
        self._keep_send_text_dialog_open = False

        self.toolbar = self._build_toolbar()
        self.append(self.toolbar)

        self.overlay = Gtk.Overlay()
        self.overlay.set_vexpand(True)
        self.append(self.overlay)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_vexpand(True)
        self.scroller.set_hexpand(True)
        self.display = VncDisplay(info)
        self.display.connect("vnc-status-changed", self._on_status_changed)
        self.display.connect("vnc-reconnect-countdown", self._on_reconnect_countdown)
        self.display.connect("vnc-title-changed", self._on_title_changed)
        self.display.connect("vnc-cursor-changed", self._on_cursor_changed)
        self.display.connect("vnc-size-changed", self._on_size_changed)
        self.display.connect("vnc-screens-changed", self._on_screens_changed)
        self.display.connect("vnc-connected", self._on_connected)
        self.scroller.set_child(self.display)
        self.overlay.set_child(self.scroller)

        # Overlay de curseur serveur (Rich Cursor / X Cursor / Cursor With Alpha)
        self.cursor_picture = Gtk.Picture()
        self.cursor_picture.set_can_shrink(False)
        self.cursor_picture.set_halign(Gtk.Align.START)
        self.cursor_picture.set_valign(Gtk.Align.START)
        self.cursor_picture.set_visible(False)
        self.cursor_picture.set_can_target(False)  # ne doit jamais intercepter les clics
        self.cursor_picture.set_content_fit(Gtk.ContentFit.FILL)
        self.overlay.add_overlay(self.cursor_picture)

        # Suit la souris au-dessus du display pour repositionner le curseur
        # serveur (indépendant des contrôleurs d'entrée internes à VncDisplay).
        cursor_motion = Gtk.EventControllerMotion()
        cursor_motion.connect("motion", self._on_display_motion)
        self.display.add_controller(cursor_motion)

        self.placeholder = self._build_placeholder()
        self.overlay.add_overlay(self.placeholder)

        self._setup_global_shortcuts()

        self.display.start()

    # ---- Barre d'outils ----

    def _build_toolbar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)
        bar.set_margin_start(6)
        bar.set_margin_end(6)

        self.status_label = Gtk.Label(label=self.info.name)
        self.status_label.set_xalign(0)
        self.status_label.set_hexpand(True)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        bar.append(self.status_label)

        btn_cad = Gtk.Button(label="Ctrl+Alt+Suppr")
        btn_cad.set_tooltip_text("Envoyer Ctrl+Alt+Suppr à la machine distante")
        btn_cad.connect("clicked", lambda *_: self.display.send_ctrl_alt_del())
        bar.append(btn_cad)

        # Bouton texte (pas une icône) comme btn_cad ci-dessus : action peu
        # courante, un libellé explicite évite le pari sur un nom d'icône
        # ambigu (clavier virtuel ? réglages clavier ?) plutôt qu'une vraie
        # icône symbolique standard vérifiée.
        self.btn_send_text = Gtk.Button(label="Envoyer du texte…")
        self.btn_send_text.set_tooltip_text(
            "Envoyer du texte au serveur distant comme une suite de frappes clavier, sans passer par le presse-papiers (Alt+F12)"
        )
        # Même cycle de sensibilité que btn_screenshot/btn_copy_screenshot
        # ci-dessous : désactivé tant qu'aucune connexion n'est établie
        # (cf. _on_status_changed / _on_connected). send_text() se garde
        # déjà lui-même (if self.client and text) ; ce bouton évite en
        # plus d'ouvrir un dialogue pour un envoi qui ne partirait nulle
        # part.
        self.btn_send_text.set_sensitive(False)
        self.btn_send_text.connect("clicked", lambda *_: self._on_send_text_clicked())
        bar.append(self.btn_send_text)

        self.btn_continuous = Gtk.ToggleButton(icon_name="media-playlist-repeat-symbolic")
        self.btn_continuous.set_tooltip_text("Mises à jour continues (à tester : certains serveurs les refusent)")
        self.btn_continuous.set_sensitive(False)
        self.btn_continuous.connect("toggled", self._on_continuous_toggled)
        bar.append(self.btn_continuous)

        btn_resize = Gtk.Button(icon_name="view-restore-symbolic")
        btn_resize.set_tooltip_text("Ajuster la fenêtre à la taille distante actuelle")
        btn_resize.connect("clicked", lambda *_: self._resize_window_to_remote())
        bar.append(btn_resize)

        btn_scale = Gtk.Button(icon_name="zoom-fit-best-symbolic")
        btn_scale.set_tooltip_text("Basculer ajusté / taille réelle")
        btn_scale.connect("clicked", lambda *_: self.display.toggle_scaling())
        bar.append(btn_scale)

        # Menu « Alimentation » (xvp, #104) : le serveur annonce xvp après la
        # connexion, de façon asynchrone ; l'état des entrées est donc relu à
        # chaque ouverture du menu (_refresh_power_popover) plutôt que figé
        # au changement de statut.
        self.btn_power = Gtk.MenuButton(label="Alimentation")
        self.btn_power.set_tooltip_text(
            "Arrêter, redémarrer ou réinitialiser la machine distante (extension xvp du serveur)"
        )
        self.power_popover = Gtk.Popover()
        power_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        power_box.set_margin_top(6)
        power_box.set_margin_bottom(6)
        power_box.set_margin_start(6)
        power_box.set_margin_end(6)
        self.power_info_label = Gtk.Label()
        self.power_info_label.set_wrap(True)
        self.power_info_label.set_max_width_chars(36)
        self.power_info_label.set_xalign(0)
        self.power_info_label.add_css_class("dim-label")
        power_box.append(self.power_info_label)
        self.power_buttons = {}
        for action in VncDisplay.POWER_ACTIONS:
            bouton = Gtk.Button(label=self._resolve_power_action_texts(action, self.info.host)["menu"])
            bouton.add_css_class("flat")
            bouton.connect("clicked", self._on_power_action_clicked, action)
            power_box.append(bouton)
            self.power_buttons[action] = bouton
        self.power_popover.set_child(power_box)
        self.power_popover.connect("show", lambda *_a: self._refresh_power_popover())
        self.btn_power.set_popover(self.power_popover)
        bar.append(self.btn_power)

        btn_reconnect = Gtk.Button(icon_name="view-refresh-symbolic")
        btn_reconnect.set_tooltip_text("Reconnecter")
        btn_reconnect.connect("clicked", lambda *_: self.display.reconnect())
        bar.append(btn_reconnect)

        # "process-stop-symbolic" vérifiée le 2026-09-12 (icône standard,
        # remplaçante documentée de l'ancien GTK_STOCK_STOP depuis GTK 3.10,
        # cf. docs.gtk.org) -- cohérente avec btn_reconnect juste au-dessus
        # (les deux actions de cycle de vie de connexion vont ensemble).
        self.btn_disconnect = Gtk.Button(icon_name="process-stop-symbolic")
        self.btn_disconnect.set_tooltip_text(
            "Déconnecter -- ferme la connexion sans fermer l'onglet (libère une "
            "session exclusive côté serveur, ou annule une tentative de "
            "connexion/reconnexion en cours)"
        )
        # PAS le même cycle de sensibilité que btn_screenshot/btn_send_text
        # (sensibles seulement une fois CONNECTED) : "Déconnecter" a un vrai
        # sens dans CONNECTING (annuler la tentative) et ERROR (interrompre
        # une boucle de reconnexion automatique en cours de backoff) en plus
        # de CONNECTED -- cf. _resolve_disconnect_button_sensitivity()
        # ci-dessous. Valeur initiale False alignée sur self.status =
        # VncStatus.DISCONNECTED posé par VncDisplay.__init__ (rien à
        # déconnecter avant le tout premier _on_status_changed).
        self.btn_disconnect.set_sensitive(False)
        self.btn_disconnect.connect("clicked", lambda *_: self.display.stop())
        bar.append(self.btn_disconnect)

        btn_fullscreen = Gtk.Button(icon_name="view-fullscreen-symbolic")
        btn_fullscreen.set_tooltip_text("Plein écran")
        # Le vrai dépôt gcm4 (vérifié le 2026-09-05) ne définit aucune action
        # "win.toggle-fullscreen" : le plein écran y est géré par un raccourci
        # clavier (F11) câblé uniquement sur les widgets VTE, qui bascule
        # Gtk.Window.fullscreen()/unfullscreen() directement sur la fenêtre
        # principale. Rien d'exploitable tel quel depuis un onglet non-
        # terminal — cet onglet gère donc lui-même son plein écran, cf.
        # _toggle_fullscreen() / _resolve_fullscreen_action() plus bas.
        btn_fullscreen.connect("clicked", lambda *_: self._toggle_fullscreen())
        bar.append(btn_fullscreen)

        self.btn_screenshot = Gtk.Button(icon_name="camera-photo-symbolic")
        self.btn_screenshot.set_tooltip_text("Capturer l'affichage distant dans un fichier PNG")
        # Désactivé tant qu'aucune image n'a encore été reçue (avant
        # vnc-connected) -- même cycle de sensibilité que btn_continuous
        # (cf. _on_status_changed / _on_connected).
        self.btn_screenshot.set_sensitive(False)
        self.btn_screenshot.connect("clicked", lambda *_: self._on_screenshot_clicked())
        bar.append(self.btn_screenshot)

        self.btn_copy_screenshot = Gtk.Button(icon_name="edit-copy-symbolic")
        self.btn_copy_screenshot.set_tooltip_text("Copier l'affichage distant dans le presse-papiers")
        # Même cycle de sensibilité que btn_screenshot (même source de
        # données -- _last_frame_texture -- désactivé tant qu'aucune image
        # n'a encore été reçue).
        self.btn_copy_screenshot.set_sensitive(False)
        self.btn_copy_screenshot.connect("clicked", lambda *_: self._on_copy_screenshot_clicked())
        bar.append(self.btn_copy_screenshot)

        # Sélecteur d'écran (multi-moniteur, ExtendedDesktopSize) -- masqué
        # tant qu'un seul écran (ou aucune info d'écran) n'est annoncé.
        self.screen_dropdown = Gtk.DropDown()
        self.screen_dropdown.set_tooltip_text("Choisir un écran distant")
        self.screen_dropdown.set_visible(False)
        self.screen_dropdown.connect("notify::selected", self._on_screen_selected)
        bar.append(self.screen_dropdown)

        return bar

    def _on_continuous_toggled(self, button):
        enable = button.get_active()
        self._continuous_updates_on = enable
        self.display.set_continuous_updates(enable)

    def _resize_window_to_remote(self):
        size = self._resolve_resize_target_size(self._screens, self.display._active_screen_id, self._last_remote_size)
        if not size:
            return
        root = self.get_root()
        if isinstance(root, Gtk.Window):
            width, height = size
            root.set_default_size(width, height)

    @staticmethod
    def _resolve_resize_target_size(screens, active_screen_id, last_remote_size):
        """Détermine la taille (largeur, hauteur) à viser pour "Ajuster la
        fenêtre à la taille distante actuelle".

        Priorité à la taille de l'écran distant actuellement sélectionné
        (multi-écran, cf. VncDisplay.set_active_screen) plutôt qu'au
        framebuffer complet -- sans ça, cliquer sur ce bouton alors qu'un
        seul écran est affiché (recadré) redimensionnait la fenêtre à la
        taille du bureau entier, plus grande que ce qui est réellement
        montré. Si l'écran sélectionné a disparu de la liste courante
        (déconnexion d'un moniteur côté serveur, cf. le même repli dans
        _find_active_screen), retombe sur le framebuffer complet plutôt
        que de planter. Renvoie None si aucune taille n'est encore connue
        (avant le premier `vnc-size-changed`), pour laisser l'appelant
        décider de ne rien faire plutôt que de déballer un None.
        """
        if active_screen_id is not None:
            for screen in screens:
                if screen.id == active_screen_id:
                    return screen.width, screen.height
        return last_remote_size

    def _toggle_fullscreen(self):
        """Bascule le plein écran de la fenêtre racine contenant cet onglet.

        Ne dépend d'AUCUN mécanisme côté gcm4 (voir _resolve_fullscreen_action
        pour le pourquoi) : utilise directement `Gtk.Widget.get_root()`, comme
        `_resize_window_to_remote()` le fait déjà pour le redimensionnement.
        Ignore silencieusement (avec un log) si l'onglet n'est pas encore
        attaché à une vraie `Gtk.Window` — ex. onglet construit mais pas
        encore inséré dans le Notebook.
        """
        root = self.get_root()
        if not isinstance(root, Gtk.Window):
            logger.warning(
                "VncTab: bouton plein écran ignoré, pas de Gtk.Window racine "
                "(onglet pas encore attaché à une fenêtre ?)"
            )
            return
        self._is_fullscreen, method_name = self._resolve_fullscreen_action(self._is_fullscreen)
        getattr(root, method_name)()

    @staticmethod
    def _resolve_fullscreen_action(currently_fullscreen):
        """Détermine le prochain état plein écran et la méthode Gtk.Window
        à appeler pour y basculer.

        Logique extraite en méthode statique pure (même style que
        `_resolve_resize_target_size` / `_resolve_screen_dropdown_index`)
        pour rester testable sans instancier `VncTab` ni un vrai
        `Gtk.Window`.

        Historique : ce bouton appelait auparavant `self.activate_action(
        "win.toggle-fullscreen", None)`, sur la seule FOI d'une hypothèse
        jamais vérifiée (cf. CLAUDE.md). Vérifié le 2026-09-05 contre le
        vrai dépôt gcm4 : aucune action de ce nom n'existe. Le plein écran y
        est géré par un raccourci clavier (F11 par défaut, configurable) qui
        bascule `Gtk.Window.fullscreen()`/`unfullscreen()` directement sur
        la fenêtre principale (`self.wMainWindow`/`self.hpMainWindow`) —
        mais ce raccourci n'est câblé que sur les widgets VTE
        (`on_terminal_keypress`), jamais sur les onglets non-terminal
        (VNC/SPICE). Rien à réutiliser tel quel, et rien de garanti stable
        tant que gcm4 est en cours d'écriture. Cet onglet garde donc son
        propre état local (`self._is_fullscreen`) et bascule sa PROPRE
        fenêtre racine, indépendamment de ce que fait gcm4 en interne.
        """
        next_state = not currently_fullscreen
        return next_state, ("fullscreen" if next_state else "unfullscreen")

    # ---- Capture d'écran ----

    def _on_screenshot_clicked(self):
        """Ouvre un Gtk.FileDialog pour choisir où enregistrer une capture
        PNG de l'affichage distant courant (VncDisplay.save_screenshot).

        Le nom de fichier par défaut est calculé par
        _default_screenshot_filename() (méthode statique pure, testable
        sans dialogue réel). Le résultat de l'enregistrement (succès ou
        échec) est affiché brièvement dans status_label via
        _flash_status_message() -- pas de popup bloquante pour un geste
        aussi fréquent.
        """
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(self._default_screenshot_filename(self.info.host))
        root = self.get_root()
        dialog.save(root if isinstance(root, Gtk.Window) else None, None, self._on_screenshot_dialog_finished)

    def _on_screenshot_dialog_finished(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            # Dialogue annulé par l'utilisateur -- rien à faire, pas une erreur.
            return
        path = gfile.get_path()
        success = self.display.save_screenshot(path)
        self._flash_status_message(self._resolve_screenshot_feedback_message(success, path))

    @staticmethod
    def _default_screenshot_filename(host: str, when: Optional[datetime] = None) -> str:
        """Nom de fichier PNG par défaut proposé dans le dialogue
        d'enregistrement : "vnc-<host>-<horodatage>.png".

        `host` peut être une adresse IPv6 littérale (ex. "fe80::1"), dont
        les deux-points ne sont pas de bons caractères de nom de fichier
        (ambigus avec un flux alternatif NTFS, gênants sur certains
        systèmes de fichiers partagés) -- remplacés par des tirets, comme
        le reste du nom. `when` est injectable pour les tests ; par défaut
        l'horodatage courant.
        """
        when = when or datetime.now()
        safe_host = host.replace(":", "-").replace("/", "-")
        return f"vnc-{safe_host}-{when:%Y%m%d-%H%M%S}.png"

    @staticmethod
    def _resolve_screenshot_feedback_message(success: bool, path) -> str:
        """Message affiché brièvement dans status_label après une tentative
        de capture -- extrait en méthode statique pure (même style que
        _resolve_resize_target_size / _resolve_fullscreen_action) pour
        rester testable sans dialogue ni GLib.timeout_add réels."""
        if success:
            return f"Capture enregistrée : {path}"
        return "Échec de la capture d'écran (voir le journal)"

    def _on_copy_screenshot_clicked(self):
        """Copie directement l'affichage distant courant dans le
        presse-papiers (VncDisplay.copy_screenshot_to_clipboard) -- pas de
        dialogue, contrairement à _on_screenshot_clicked : il n'y a rien à
        choisir (ni nom, ni emplacement) pour une copie presse-papiers.
        """
        success = self.display.copy_screenshot_to_clipboard()
        self._flash_status_message(self._resolve_copy_screenshot_feedback_message(success))

    @staticmethod
    def _resolve_copy_screenshot_feedback_message(success: bool) -> str:
        """Message affiché brièvement dans status_label après une tentative
        de copie presse-papiers -- même style que
        _resolve_screenshot_feedback_message, mais sans chemin de fichier à
        mentionner (rien n'est écrit sur disque ici)."""
        if success:
            return "Capture copiée dans le presse-papiers"
        return "Échec de la copie dans le presse-papiers (voir le journal)"

    def _flash_status_message(self, text: str, duration_ms: int = 3000):
        """Affiche `text` dans status_label pendant `duration_ms`, puis
        restaure le libellé précédent -- réutilisé pour le retour visuel
        après une capture d'écran, sans dépendre d'une infrastructure de
        toast/notification qui n'existe pas encore dans cet onglet."""
        previous_text = self.status_label.get_label()
        self.status_label.set_label(text)
        GLib.timeout_add(duration_ms, lambda: self.status_label.set_label(previous_text))

    @staticmethod
    def _resolve_pasted_clipboard_text(clipboard_text: Optional[str]) -> Optional[str]:
        """
        Décide ce qu'il faut effectivement coller dans le champ du
        dialogue "Envoyer du texte" à partir du contenu lu dans le
        presse-papiers local par le bouton "Coller" (icône secondaire de
        l'entrée, cf. _show_send_text_dialog ci-dessous) : uniquement s'il
        y a réellement du texte -- un presse-papiers vide, ou illisible
        (`None`, cf. le `except GLib.Error` dans _show_send_text_dialog,
        par exemple une image copiée) laisse le champ inchangé plutôt que
        d'écraser ce que l'utilisateur avait déjà tapé.
        """
        return clipboard_text or None

    @staticmethod
    def _resolve_send_text_action(text: str, keep_open: bool) -> Optional[str]:
        """
        Décide ce que fait le dialogue "Envoyer du texte" quand
        l'utilisateur valide (Entrée ou bouton "Envoyer", #93) :

        - ``None`` : champ vide, rien à envoyer, le dialogue reste tel quel ;
        - ``"keep"`` : envoyer, puis vider le champ et garder le dialogue
          ouvert pour un envoi répété (case cochée) ;
        - ``"close"`` : envoyer, puis fermer le dialogue (case décochée,
          comportement historique).
        """
        logger.debug(
            "VncTab._resolve_send_text_action | entrée longueur={n} keep_open={k}",
            n=len(text),
            k=keep_open,
        )
        if not text:
            action = None
        elif keep_open:
            action = "keep"
        else:
            action = "close"
        logger.debug("VncTab._resolve_send_text_action | sortie action={a}", a=action)
        return action

    def _on_send_text_clicked(self, *_args):
        """Ouvre une petite fenêtre pour taper du texte à envoyer au
        serveur distant sous forme de frappes clavier (une par caractère),
        cf. VncDisplay.send_text()."""
        self._show_send_text_dialog()

    def _show_send_text_dialog(self):
        # Même structure que _show_forget_key_confirmation_dialog plus bas
        # (Gtk.Window transitoire + modal, pas de nouvelle infrastructure
        # de dialogue) -- pas de logique de confirmation ici, juste un
        # champ de saisie et un bouton d'envoi. Pas de _flash_status_message
        # après l'envoi (contrairement à la capture d'écran/copie
        # presse-papiers ci-dessus) : send_text() est fire-and-forget sans
        # signal de réussite, exactement comme send_ctrl_alt_del() dont le
        # bouton n'affiche non plus aucune confirmation -- afficher un
        # message fixe "envoyé" laisserait croire à une confirmation de
        # remise qu'on n'a pas réellement.
        root = self.get_root()
        dialog = Gtk.Window()
        if isinstance(root, Gtk.Window):
            dialog.set_transient_for(root)
        dialog.set_modal(True)
        dialog.set_title("Envoyer du texte")
        dialog.set_default_size(420, -1)
        dialog.set_resizable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        dialog.set_child(box)

        label = Gtk.Label(
            label=(
                "Le texte est envoyé au serveur distant comme une suite de "
                "frappes clavier, sans passer par le presse-papiers. Les "
                "caractères sans équivalent clavier reconnu sont ignorés."
            )
        )
        label.set_wrap(True)
        label.set_xalign(0)
        box.append(label)

        entry = Gtk.Entry()
        entry.set_placeholder_text("Texte à envoyer")
        # "edit-paste-symbolic" -- même icône que celle déjà utilisée pour
        # la copie de capture d'écran ci-dessus, déjà confirmée présente
        # dans ce thème. set_icon_from_icon_name()/le signal "icon-press"/
        # set_icon_tooltip_text() sont vérifiés le 2026-09-13 contre
        # docs.gtk.org et api.pygobject.gnome.org (API GtkEntry présente
        # depuis GTK 2.16, toujours d'actualité en GTK4).
        entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "edit-paste-symbolic")
        entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Coller le presse-papiers local")
        box.append(entry)

        def _on_paste_read(clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
            except GLib.Error:
                # Même garde que _on_local_clipboard_read plus haut
                # (contenu non textuel ou illisible, ex. une image copiée)
                # -- champ laissé inchangé plutôt qu'une exception qui
                # remonterait telle quelle hors de ce callback GLib.
                logger.trace("VncTab._show_send_text_dialog | presse-papiers local non textuel ou illisible, ignoré")
                return
            pasted = self._resolve_pasted_clipboard_text(text)
            if pasted is not None:
                # Remplace tout le champ plutôt qu'une insertion au
                # curseur : ce champ sert à composer UN texte à envoyer,
                # pas à éditer un texte existant -- cf.
                # docs/sessions/session-22.md. select_region() sélectionne
                # le texte collé, prêt à être envoyé tel quel (Entrée) ou
                # retapé par-dessus si besoin.
                entry.set_text(pasted)
                entry.select_region(0, -1)

        def _on_paste_icon_pressed(entry_widget, icon_pos):
            entry_widget.get_clipboard().read_text_async(None, _on_paste_read)

        entry.connect("icon-press", _on_paste_icon_pressed)

        # Envoi répété (#93) : case cochée, le dialogue reste ouvert après
        # l'envoi et le champ est vidé pour le texte suivant. L'état est
        # mémorisé sur l'onglet pour la prochaine ouverture du dialogue.
        keep_open_check = Gtk.CheckButton(label="Garder la fenêtre ouverte après l'envoi")
        keep_open_check.set_active(self._keep_send_text_dialog_open)

        def _on_keep_open_toggled(check):
            logger.debug("VncTab._on_keep_open_toggled | entrée actif={a}", a=check.get_active())
            self._keep_send_text_dialog_open = check.get_active()
            logger.debug(
                "VncTab._on_keep_open_toggled | sortie garder ouvert={k}",
                k=self._keep_send_text_dialog_open,
            )

        keep_open_check.connect("toggled", _on_keep_open_toggled)
        box.append(keep_open_check)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_row.set_halign(Gtk.Align.END)
        box.append(button_row)

        cancel_button = Gtk.Button(label="Fermer")
        cancel_button.connect("clicked", lambda *_a: dialog.close())
        button_row.append(cancel_button)

        send_button = Gtk.Button(label="Envoyer")
        send_button.add_css_class("suggested-action")
        button_row.append(send_button)

        def _do_send(*_a):
            # Pas de _resolve_xxx() pur extrait ici, contrairement à
            # _resolve_forget_key_confirmation plus bas : ce garde ne fait
            # que reprendre celui déjà fait par send_text() lui-même (if
            # self.client and text), sans règle métier propre à vérifier
            # séparément -- juste éviter de fermer le dialogue sur une
            # validation vide (Entrée dans un champ non rempli).
            text = entry.get_text()
            action = self._resolve_send_text_action(text, self._keep_send_text_dialog_open)
            if action is None:
                return
            self.display.send_text(text)
            if action == "keep":
                entry.set_text("")
                entry.grab_focus()
            else:
                dialog.close()

        entry.connect("activate", _do_send)
        send_button.connect("clicked", _do_send)

        dialog.present()
        entry.grab_focus()

    # ---- Raccourcis clavier globaux ----

    # (accélérateur GTK, nom de la méthode d'action) -- donnée de classe
    # pure pour rester vérifiable (table + résolution des handlers) sans
    # instancier de vrai Gtk.ShortcutController. Combinaisons choisies
    # pour limiter les collisions avec un usage normal côté invité
    # distant : <Control><Alt>End pour Ctrl+Alt+Suppr (convention déjà
    # répandue chez les clients VNC -- la vraie combinaison Ctrl+Alt+Suppr
    # est de toute façon interceptée par le système D'HÔTE local avant
    # d'atteindre quelque application que ce soit, ce repli est donc la
    # seule façon réaliste de la déclencher). F11 pour le plein écran
    # (attente utilisateur standard, et cf. docs/pieges.md : le vrai dépôt
    # gcm4 ne câble ce raccourci que sur les widgets VTE, jamais sur cet
    # onglet). <Shift>F12 pour la capture d'écran (combinaison rarement
    # utilisée par un système invité). <Control>F12 pour la copie
    # presse-papiers (session 19) -- même famille que <Shift>F12,
    # modificateur différent pour rester distinct et facile à retenir
    # (Maj = fichier, Ctrl = presse-papiers). <Alt>F12 pour le dialogue
    # d'envoi de texte (Gcm4 #92), même famille : Alt = texte.
    _GLOBAL_SHORTCUTS = (
        ("<Shift>F12", "_shortcut_action_screenshot"),
        ("<Control>F12", "_shortcut_action_copy_screenshot"),
        ("<Alt>F12", "_shortcut_action_send_text"),
        ("F11", "_shortcut_action_toggle_fullscreen"),
        ("<Control><Alt>End", "_shortcut_action_send_ctrl_alt_del"),
    )

    def _setup_global_shortcuts(self):
        """Raccourcis clavier locaux à l'onglet (capture d'écran, copie,
        envoi de texte, plein écran, Ctrl+Alt+Suppr), utilisables sans passer par la souris/la
        barre d'outils.

        Phase CAPTURE, posée sur VncTab -- ancêtre de VncDisplay dans
        l'arbre de widgets (VncTab > Overlay > ScrolledWindow >
        VncDisplay). En GTK4, les contrôleurs en phase CAPTURE d'un
        ancêtre s'exécutent AVANT la phase TARGET du widget effectivement
        ciblé par l'événement. C'est nécessaire ici :
        VncDisplay._on_key_pressed() retourne TOUJOURS True, y compris
        pour une touche sans équivalent VNC connu (cf. docs/pieges.md) --
        sans la phase CAPTURE sur un ancêtre, ces raccourcis ne se
        déclencheraient jamais quand VncDisplay a le focus (le cas normal
        en usage : c'est lui qui reçoit et relaie les frappes clavier au
        serveur distant), l'appui serait systématiquement avalé d'abord et
        transmis tel quel au serveur distant.
        """
        self.shortcuts = Gtk.ShortcutController()
        self.shortcuts.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        for accelerator, handler_name in self._GLOBAL_SHORTCUTS:
            handler = getattr(self, handler_name)
            action = Gtk.CallbackAction.new(lambda widget, args, data, _h=handler: _h())
            self.shortcuts.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accelerator), action))
        self.add_controller(self.shortcuts)

    def _shortcut_action_screenshot(self) -> bool:
        self._on_screenshot_clicked()
        return True

    def _shortcut_action_copy_screenshot(self) -> bool:
        self._on_copy_screenshot_clicked()
        return True

    def _shortcut_action_send_text(self) -> bool:
        """Ouvre le dialogue d'envoi de texte (Gcm4 #92), comme le bouton.

        Même garde que le bouton : tant qu'aucune connexion n'est établie
        (``btn_send_text`` désactivé, cf. _on_status_changed/_on_connected),
        rien ne s'ouvre et False laisse l'événement poursuivre son chemin
        au lieu de l'avaler sans effet visible.
        """
        logger.debug("VncTab._shortcut_action_send_text | entrée")
        if not self.btn_send_text.get_sensitive():
            logger.debug("VncTab._shortcut_action_send_text | sortie False (pas connecté)")
            return False
        self._on_send_text_clicked()
        logger.debug("VncTab._shortcut_action_send_text | sortie True")
        return True

    def _shortcut_action_toggle_fullscreen(self) -> bool:
        self._toggle_fullscreen()
        return True

    def _shortcut_action_send_ctrl_alt_del(self) -> bool:
        self.display.send_ctrl_alt_del()
        return True

    # ---- Placeholder de connexion (spinner / erreur / réessayer) ----

    def _build_placeholder(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.set_hexpand(True)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(32, 32)
        self.spinner.start()
        box.append(self.spinner)

        self.placeholder_label = Gtk.Label(label=f"Connexion à {self.info.host}…")
        box.append(self.placeholder_label)

        self.retry_button = Gtk.Button(label="Réessayer")
        self.retry_button.set_visible(False)
        self.retry_button.connect("clicked", lambda *_: self.display.reconnect())
        box.append(self.retry_button)

        self.forget_key_button = Gtk.Button(label="Oublier la clé enregistrée et réessayer")
        self.forget_key_button.set_visible(False)
        self.forget_key_button.connect("clicked", self._on_forget_key_clicked)
        box.append(self.forget_key_button)

        return box

    @staticmethod
    def _resolve_power_menu_state(connected: bool, xvp_supported: bool):
        """État du menu « Alimentation » (#104) : (entrées actives, message).

        Pure, testable sans GTK : non connecté ou serveur sans xvp → entrées
        inactives et explication ; sinon entrées actives, aucun message.
        """
        logger.debug("VncTab._resolve_power_menu_state | entrée connected={c} xvp={x}", c=connected, x=xvp_supported)
        if not connected:
            resultat = (False, "Disponible une fois la connexion établie.")
        elif not xvp_supported:
            resultat = (
                False,
                "Ce serveur n'annonce pas l'extension xvp : arrêt, redémarrage et réinitialisation sont indisponibles.",
            )
        else:
            resultat = (True, None)
        logger.debug("VncTab._resolve_power_menu_state | sortie {r}", r=resultat)
        return resultat

    @staticmethod
    def _resolve_power_action_texts(action: str, host: str) -> dict:
        """Libellés de la commande xvp ``action`` : entrée de menu, titre et
        texte du dialogue de confirmation, bouton de confirmation.

        Raises:
            KeyError: ``action`` inconnue.
        """
        logger.debug("VncTab._resolve_power_action_texts | entrée action={a}", a=action)
        textes = {
            "shutdown": {
                "menu": "Arrêter…",
                "title": "Arrêter la machine distante ?",
                "body": f"Le serveur VNC demandera un arrêt propre du système de « {host} ».",
                "confirm": "Arrêter",
            },
            "reboot": {
                "menu": "Redémarrer…",
                "title": "Redémarrer la machine distante ?",
                "body": f"Le serveur VNC demandera un redémarrage propre du système de « {host} ».",
                "confirm": "Redémarrer",
            },
            "reset": {
                "menu": "Réinitialiser…",
                "title": "Réinitialiser la machine distante ?",
                "body": (
                    f"Le serveur VNC réinitialisera brutalement « {host} », comme un "
                    "bouton reset : les données non enregistrées seront perdues."
                ),
                "confirm": "Réinitialiser",
            },
        }
        resultat = textes[action]
        logger.debug("VncTab._resolve_power_action_texts | sortie {t}", t=resultat["title"])
        return resultat

    def _refresh_power_popover(self):
        """Relit l'état xvp à l'ouverture du menu « Alimentation »."""
        logger.debug("VncTab._refresh_power_popover | entrée")
        actif, message = self._resolve_power_menu_state(
            self.display.status == VncStatus.CONNECTED, self.display.xvp_supported()
        )
        for bouton in self.power_buttons.values():
            bouton.set_sensitive(actif)
        self.power_info_label.set_label(message or "")
        self.power_info_label.set_visible(message is not None)
        logger.debug("VncTab._refresh_power_popover | sortie actif={a}", a=actif)

    def _on_power_action_clicked(self, _button, action: str):
        """Entrée du menu « Alimentation » : ferme le menu, demande confirmation."""
        logger.debug("VncTab._on_power_action_clicked | entrée action={a}", a=action)
        self.power_popover.popdown()
        self._show_power_confirmation_dialog(action)
        logger.debug("VncTab._on_power_action_clicked | sortie")

    def _show_power_confirmation_dialog(self, action: str):
        """Dialogue de confirmation d'une commande xvp ; Annuler a le focus."""
        logger.debug("VncTab._show_power_confirmation_dialog | entrée action={a}", a=action)
        textes = self._resolve_power_action_texts(action, self.info.host)
        root = self.get_root()
        dialog = Gtk.Window()
        if isinstance(root, Gtk.Window):
            dialog.set_transient_for(root)
        dialog.set_modal(True)
        dialog.set_title(textes["title"])
        dialog.set_default_size(420, -1)
        dialog.set_resizable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        dialog.set_child(box)

        label = Gtk.Label(label=textes["body"])
        label.set_wrap(True)
        label.set_xalign(0)
        box.append(label)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_row.set_halign(Gtk.Align.END)
        box.append(button_row)

        cancel_button = Gtk.Button(label="Annuler")
        cancel_button.connect("clicked", lambda *_a: dialog.close())
        button_row.append(cancel_button)

        confirm_button = Gtk.Button(label=textes["confirm"])
        confirm_button.add_css_class("destructive-action")
        button_row.append(confirm_button)

        def _do_confirm(*_a):
            logger.debug("VncTab._show_power_confirmation_dialog._do_confirm | entrée action={a}", a=action)
            dialog.close()
            envoye = self.display.send_power_action(action)
            logger.debug("VncTab._show_power_confirmation_dialog._do_confirm | sortie envoyée={e}", e=envoye)

        confirm_button.connect("clicked", _do_confirm)
        self.power_dialog = dialog
        dialog.present()
        cancel_button.grab_focus()
        logger.debug("VncTab._show_power_confirmation_dialog | sortie")

    def _on_forget_key_clicked(self, *_args):
        """Ouvre une confirmation par retype du nom d'hôte avant d'exécuter
        « Oublier la clé enregistrée et réessayer ».

        Avant ce correctif, ce bouton appelait directement
        `forget_host_key()` + `reconnect()` sur un simple clic — oublier une
        clé pinnée par erreur (mauvais onglet, double-clic accidentel) rouvre
        une fenêtre d'exposition à un MITM le temps de la reconnexion
        suivante, sans aucun filet. Le motif « retaper le nom d'hôte pour
        confirmer une action destructrice » suit `PATTERNS.md` (cf.
        `docs/sessions/session-05.md`).
        """
        self._show_forget_key_confirmation_dialog()

    def _show_forget_key_confirmation_dialog(self):
        root = self.get_root()
        dialog = Gtk.Window()
        if isinstance(root, Gtk.Window):
            dialog.set_transient_for(root)
        dialog.set_modal(True)
        dialog.set_title("Oublier la clé enregistrée ?")
        dialog.set_default_size(420, -1)
        dialog.set_resizable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        dialog.set_child(box)

        label = Gtk.Label(
            label=(
                "Oublier la clé hôte enregistrée supprime la protection contre "
                f"un serveur usurpé (MITM) jusqu'à la prochaine connexion. "
                f"Pour confirmer, retapez le nom d'hôte exact : « {self.info.host} »."
            )
        )
        label.set_wrap(True)
        label.set_xalign(0)
        box.append(label)

        entry = Gtk.Entry()
        entry.set_placeholder_text(self.info.host)
        box.append(entry)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_row.set_halign(Gtk.Align.END)
        box.append(button_row)

        cancel_button = Gtk.Button(label="Annuler")
        cancel_button.connect("clicked", lambda *_a: dialog.close())
        button_row.append(cancel_button)

        confirm_button = Gtk.Button(label="Oublier la clé")
        confirm_button.add_css_class("destructive-action")
        confirm_button.set_sensitive(False)
        button_row.append(confirm_button)

        def _update_confirm_sensitivity(*_a):
            confirm_button.set_sensitive(self._resolve_forget_key_confirmation(entry.get_text(), self.info.host))

        def _do_confirm(*_a):
            if not self._resolve_forget_key_confirmation(entry.get_text(), self.info.host):
                return
            dialog.close()
            self.display.forget_host_key()
            self.display.reconnect()

        entry.connect("changed", _update_confirm_sensitivity)
        entry.connect("activate", _do_confirm)
        confirm_button.connect("clicked", _do_confirm)

        dialog.present()
        entry.grab_focus()

    @staticmethod
    def _resolve_forget_key_confirmation(typed_text: str, expected_host: str) -> bool:
        """Détermine si le texte retapé autorise à exécuter « Oublier la clé
        enregistrée et réessayer ».

        Extrait en méthode statique pure (même style que
        `_resolve_resize_target_size` / `_resolve_screen_dropdown_index` /
        `_resolve_fullscreen_action`) pour rester testable sans instancier
        `VncTab`, `Gtk.Window` ni `Gtk.Entry`.

        Comparaison stricte (pas de `.strip()`, pas de casse insensible) :
        le but est de vérifier que l'utilisateur a bien lu et retapé le nom
        d'hôte affiché, pas qu'il a tapé quelque chose qui y ressemble.
        Un `expected_host` vide (ne devrait pas arriver en pratique) ne peut
        jamais être confirmé par une entrée vide.
        """
        return bool(expected_host) and typed_text == expected_host

    # ---- Réactions aux signaux de VncDisplay ----

    @staticmethod
    def _resolve_disconnect_button_sensitivity(status_value: str) -> bool:
        """
        Détermine si le bouton "Déconnecter" doit être cliquable pour un
        statut donné -- méthode statique pure (même style que
        `_resolve_resize_target_size`/`_resolve_fullscreen_action`
        ci-dessus) pour rester testable sans instancier de vrai `VncTab`.

        Sensible dans TOUS les états sauf DISCONNECTED (déjà déconnecté,
        rien à arrêter) -- contrairement à `btn_screenshot`/
        `btn_copy_screenshot`/`btn_send_text`, qui ne redeviennent
        cliquables qu'une fois pleinement CONNECTED (cf. `_on_connected`).
        "Déconnecter" appelle `VncDisplay.stop()`, qui se comporte
        proprement dans n'importe quel état : annule une tentative de
        connexion en cours (CONNECTING), ferme une session active
        (CONNECTED), ou interrompt une boucle de reconnexion automatique en
        cours de backoff (ERROR) -- sans jamais redéclencher cette boucle
        lui-même (`stop()` pose DISCONNECTED, jamais ERROR, cf.
        `_should_schedule_auto_reconnect` dans `VncDisplay`).
        """
        return status_value != VncStatus.DISCONNECTED.value

    def _on_status_changed(self, display, status_value, message):
        self.emit("tab-status-changed", status_value)
        self.status_label.set_label(f"{self.info.name} — {message}")
        self.btn_disconnect.set_sensitive(self._resolve_disconnect_button_sensitivity(status_value))

        if status_value == VncStatus.CONNECTED.value:
            self.placeholder.set_visible(False)
        elif status_value == VncStatus.CONNECTING.value:
            self.placeholder.set_visible(True)
            self.spinner.start()
            self.placeholder_label.set_label(message)
            self.retry_button.set_visible(False)
            self.forget_key_button.set_visible(False)
            self.cursor_picture.set_visible(False)
            self.btn_continuous.set_sensitive(False)
            self.btn_screenshot.set_sensitive(False)
            self.btn_copy_screenshot.set_sensitive(False)
            self.btn_send_text.set_sensitive(False)
        elif status_value == VncStatus.ERROR.value:
            self.placeholder.set_visible(True)
            self.spinner.stop()
            self.placeholder_label.set_label(message)
            self.retry_button.set_visible(True)
            self.forget_key_button.set_visible(self.info.pin_host_key)
            self.cursor_picture.set_visible(False)
            self.btn_continuous.set_sensitive(False)
            self.btn_screenshot.set_sensitive(False)
            self.btn_copy_screenshot.set_sensitive(False)
            self.btn_send_text.set_sensitive(False)
        elif status_value == VncStatus.DISCONNECTED.value:
            self.placeholder.set_visible(True)
            self.spinner.stop()
            self.placeholder_label.set_label("Déconnecté")
            self.retry_button.set_visible(True)
            self.forget_key_button.set_visible(False)
            self.cursor_picture.set_visible(False)
            self.btn_continuous.set_sensitive(False)
            self.btn_screenshot.set_sensitive(False)
            self.btn_copy_screenshot.set_sensitive(False)
            self.btn_send_text.set_sensitive(False)

    def _on_reconnect_countdown(self, _display, message):
        """Met à jour le message d'erreur affiché, seconde par seconde (#94),
        sans repasser par tout _on_status_changed (statut inchangé)."""
        logger.debug("VncTab._on_reconnect_countdown | entrée {m}", m=message)
        self.status_label.set_label(f"{self.info.name} — {message}")
        self.placeholder_label.set_label(message)
        logger.debug("VncTab._on_reconnect_countdown | sortie")

    def _on_title_changed(self, display, title):
        self.emit("tab-title-changed", title)

    def _on_size_changed(self, display, width, height):
        self._last_remote_size = (width, height)

    def _on_screens_changed(self, display, screens):
        self._screens = list(screens)

        # Calculée AVANT de décider si le dropdown est visible : que
        # l'écran actif soit toujours valable ou non doit être tranché de
        # la même façon qu'il y ait 0, 1 ou plusieurs écrans dans cette
        # annonce -- cf. le correctif du 2026-09-05 ci-dessous.
        selected_index = self._resolve_screen_dropdown_index(self._screens, self.display._active_screen_id)

        if len(self._screens) < 2:
            # Rien à choisir : un seul écran (ou pas d'info ExtendedDesktopSize)
            self.screen_dropdown.set_visible(False)
        else:
            labels = ["Bureau complet (tous les écrans)"] + [
                f"Écran {s.id} — {s.width}×{s.height}" for s in self._screens
            ]
            self._updating_screen_dropdown = True
            self.screen_dropdown.set_model(Gtk.StringList.new(labels))
            self.screen_dropdown.set_selected(selected_index)
            self._updating_screen_dropdown = False
            self.screen_dropdown.set_visible(True)

        if selected_index == 0 and self.display._active_screen_id is not None:
            # L'écran précédemment sélectionné a disparu de cette nouvelle
            # annonce ExtendedDesktopSize (ou il ne reste carrément plus
            # assez d'écrans pour qu'un choix ait un sens -- cf. le bug
            # corrigé le 2026-09-05 : cette ligne ne s'exécutait avant que
            # dans la branche >= 2 écrans, jamais quand on repassait sous
            # 2 -- un `_active_screen_id` resté périmé après un simple
            # `return` anticipé) -- on aligne l'état réel du display sur
            # ce que montre désormais l'UI ("Bureau complet"), plutôt que
            # de laisser _active_screen_id pointer vers un écran absent ou
            # inaccessible (silencieusement toléré par
            # _find_active_screen/get_display_transform, mais qui
            # reviendrait cropper dessus sans action de l'utilisateur si
            # cet écran réapparaît plus tard avec le même id).
            self.display.set_active_screen(None)

    @staticmethod
    def _resolve_screen_dropdown_index(screens, active_screen_id):
        """Index à sélectionner dans le dropdown juste après avoir
        reconstruit son modèle (`vnc-screens-changed`).

        Sans ça, toute ré-annonce ExtendedDesktopSize (le serveur peut la
        renvoyer pour bien d'autres raisons qu'un vrai changement de
        moniteur) remettait silencieusement le dropdown sur "Bureau
        complet" (index 0) SANS toucher à `VncDisplay._active_screen_id`
        (`_updating_screen_dropdown` bloque justement `_on_screen_selected`
        pendant `set_model()`/`set_selected()`) -- l'UI affichait "Bureau
        complet" alors que le crop réellement montré restait celui de
        l'écran précédemment choisi. Ici on cherche d'abord si l'écran
        actif est toujours présent dans la nouvelle liste (et si oui, on
        garde sa sélection) ; seulement s'il a disparu on retombe sur 0 --
        et l'appelant se charge alors d'aligner `_active_screen_id` sur ce
        choix (cf. `_on_screens_changed`).
        Index 0 = "Bureau complet" ; sinon 1-based (index i+1 pour
        `screens[i]`), même correspondance que `_on_screen_selected`.
        """
        if active_screen_id is not None:
            for i, screen in enumerate(screens):
                if screen.id == active_screen_id:
                    return i + 1
        return 0

    def _on_screen_selected(self, dropdown, *_args):
        if self._updating_screen_dropdown:
            return  # évite de redéclencher set_active_screen() pendant set_model()/set_selected()
        idx = dropdown.get_selected()
        if idx <= 0 or idx - 1 >= len(self._screens):
            self.display.set_active_screen(None)
        else:
            self.display.set_active_screen(self._screens[idx - 1].id)

    def _on_connected(self, display):
        self.btn_continuous.set_sensitive(True)
        self.btn_screenshot.set_sensitive(True)
        self.btn_copy_screenshot.set_sensitive(True)
        self.btn_send_text.set_sensitive(True)
        # Réapplique le mode ContinuousUpdates si l'utilisateur l'avait
        # activé avant une reconnexion — sinon il resterait affiché comme
        # actif dans la barre d'outils sans l'être réellement.
        if self._continuous_updates_on:
            self.display.set_continuous_updates(True)

    def _on_cursor_changed(self, display, cursor):
        self._current_cursor = cursor

        if cursor is None or cursor.width == 0 or cursor.height == 0:
            # Le serveur demande explicitement de cacher le curseur —
            # on rend la main au pointeur système par défaut.
            self.cursor_picture.set_visible(False)
            self.display.set_cursor_from_name("default")
            return

        height, width = cursor.data.shape[0], cursor.data.shape[1]
        data = cursor.data.tobytes()
        texture = Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(data), width * 4)
        self.cursor_picture.set_paintable(texture)

        # La texture est à la résolution distante native ; on redimensionne
        # l'overlay selon l'échelle d'affichage courante pour qu'il reste
        # cohérent visuellement avec le reste du bureau distant.
        transform = self.display.get_display_transform()
        scale_x, scale_y = (transform[0], transform[1]) if transform else (1.0, 1.0)
        self.cursor_picture.set_size_request(max(1, round(width * scale_x)), max(1, round(height * scale_y)))
        self.cursor_picture.set_visible(True)

        # Le serveur gère désormais le rendu du curseur lui-même : on cache
        # le pointeur système pour éviter d'en voir deux superposés.
        self.display.set_cursor_from_name("none")

        self._position_cursor(*self._last_mouse_xy)

    def _on_display_motion(self, ctrl, x, y):
        self._last_mouse_xy = (x, y)
        if self.cursor_picture.get_visible():
            self._position_cursor(x, y)

    def _position_cursor(self, x, y):
        # x, y sont déjà en coordonnées widget (là où se trouve réellement
        # le pointeur à l'écran) ; seul le hotspot — exprimé en pixels
        # distants — doit être converti à l'échelle d'affichage courante.
        transform = self.display.get_display_transform()
        scale_x, scale_y = (transform[0], transform[1]) if transform else (1.0, 1.0)
        hotspot_x = (self._current_cursor.x if self._current_cursor else 0) * scale_x
        hotspot_y = (self._current_cursor.y if self._current_cursor else 0) * scale_y
        self.cursor_picture.set_margin_start(max(0, int(x - hotspot_x)))
        self.cursor_picture.set_margin_top(max(0, int(y - hotspot_y)))

    # ---- API publique pour la fenêtre principale ----

    def close(self):
        self.display.stop()
        self.emit("close-requested")


# ---------------------------------------------------------------------------
# Fabrique de label d'onglet (icône + titre + bouton fermer)
# ---------------------------------------------------------------------------


def build_vnc_tab_label(tab: VncTab) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

    icon = Gtk.Image.new_from_icon_name("video-display-symbolic")
    box.append(icon)

    label = Gtk.Label(label=tab.info.name)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    box.append(label)

    close_btn = Gtk.Button(icon_name="window-close-symbolic")
    close_btn.set_has_frame(False)
    close_btn.connect("clicked", lambda *_: tab.close())
    box.append(close_btn)

    tab.connect("tab-title-changed", lambda _t, title: label.set_label(title))

    return box


# ---------------------------------------------------------------------------
# Exemple minimal d'intégration dans un Gtk.Notebook (pour tester le module
# isolément ; à retirer une fois intégré à gnome-connection-manager)
# ---------------------------------------------------------------------------


def parse_demo_args(args):
    """Arguments de la démo autonome → ``VncConnectionInfo`` (None si HOST
    manque). ``--websocket`` active le transport WebSocket (chemin ``/``),
    ``--websocket=/websockify`` précise le chemin (Gcm4 #104, tâche 3)."""
    logger.debug("parse_demo_args | entrée {n} argument(s)", n=len(args))
    websocket = False
    ws_path = "/"
    positionnels = []
    for arg in args:
        if arg == "--websocket":
            websocket = True
        elif arg.startswith("--websocket="):
            websocket = True
            ws_path = arg.split("=", 1)[1]
        else:
            positionnels.append(arg)
    if not positionnels:
        logger.debug("parse_demo_args | sortie HOST manquant")
        return None
    info = VncConnectionInfo(
        name=positionnels[0],
        host=positionnels[0],
        port=int(positionnels[1]) if len(positionnels) > 1 else 5900,
        username=positionnels[2] if len(positionnels) > 2 else None,
        password=positionnels[3] if len(positionnels) > 3 else None,
        websocket=websocket,
        ws_path=ws_path,
    )
    logger.debug("parse_demo_args | sortie host={h} websocket={w}", h=info.host, w=info.websocket)
    return info


if __name__ == "__main__":
    import sys
    from gi.events import GLibEventLoopPolicy

    class DemoWindow(Gtk.ApplicationWindow):
        def __init__(self, app, info: VncConnectionInfo):
            super().__init__(application=app, title="Démo VncTab")
            self.set_default_size(1024, 768)

            self.notebook = Gtk.Notebook()
            self.set_child(self.notebook)

            self.add_tab(info)

        def add_tab(self, info: VncConnectionInfo):
            tab = VncTab(info)
            tab.connect("close-requested", self._on_tab_close)
            label = build_vnc_tab_label(tab)
            self.notebook.append_page(tab, label)

        def _on_tab_close(self, tab):
            page_num = self.notebook.page_num(tab)
            if page_num != -1:
                self.notebook.remove_page(page_num)

    class DemoApp(Gtk.Application):
        def __init__(self, info):
            super().__init__(application_id="fr.transcende.vnctab.demo")
            self.info = info

        def do_activate(self):
            win = DemoWindow(self, self.info)
            win.present()

    demo_info = parse_demo_args(sys.argv[1:])
    if demo_info is None:
        logger.error("Usage: vnc_tab.py [--websocket[=CHEMIN]] HOST [PORT] [USERNAME] [PASSWORD]")
        sys.exit(1)

    asyncio.set_event_loop_policy(GLibEventLoopPolicy())
    DemoApp(demo_info).run(None)
