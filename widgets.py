#!/usr/bin/python3
# -*- coding: UTF-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
"""Widgets GTK personnalisés pour l'interface GCM."""

import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Vte

from folder_context_menu_core import build_folder_context_menu_layout, should_show_protocol_items
from gesture_trigger_core import classify_tab_label_click
from inline_editor_core import classify_editor_button_press, classify_editor_key_press
from popup_menu_core import (
    POPUP_MENU_ACTIONS,
    POPUP_MENU_SECTIONS,
    compute_enabled_actions,
    format_command_label,
)
from tab_context_menu_core import build_tab_context_menu_layout
from utils import conf, run_dialog_sync

if TYPE_CHECKING:
    # `_` est injecté dans les builtins à l'exécution par bindtextdomain().
    # Cette déclaration sert uniquement au type-checker (Pylance) : elle évite
    # les faux positifs "_ is not defined" sans affecter le runtime.
    def _(message: str) -> str: ...


from loguru import logger as _loguru_logger

try:
    USERHOME_DIR = os.getenv("HOME")
except Exception as exc:
    _loguru_logger.debug("widgets | variable HOME illisible : {}", exc)
    USERHOME_DIR = ""
if USERHOME_DIR is None or USERHOME_DIR == "":
    try:
        USERHOME_DIR = os.path.expanduser("~")
    except Exception as exc:
        _loguru_logger.debug("widgets | répertoire personnel introuvable : {}", exc)
        USERHOME_DIR = ""

assert (USERHOME_DIR is not None) and (USERHOME_DIR != ""), (
    "FATAL: Could not determine home directory for the current user"
)

assert os.path.isdir(USERHOME_DIR), (
    "FATAL: Could not locate home directory '%s' for the current user" % (USERHOME_DIR)
)

CONFIG_DIR = USERHOME_DIR + "/.gcm"
CONFIG_FILE = CONFIG_DIR + "/gcm.conf"
KEY_FILE = CONFIG_DIR + "/.gcm.key"

if not os.path.exists(CONFIG_DIR):
    os.makedirs(CONFIG_DIR)
app_name = "Gnome Connection Manager"
# Initialiser les valeurs de conf dépendantes du contexte d'exécution
conf.LOG_PATH = CONFIG_DIR + "/logs"
conf.CONTINUOUS_LOG_PATH = CONFIG_DIR + "/log"
conf.APP_TITLE = app_name


def _setup_app_logger():
    """Initialise le logger applicatif (Loguru).

    Issue #130/#170 : configuration unique (``logging_config``), plus de
    repli sur le module ``logging`` standard (loguru est une dépendance
    obligatoire). Idempotent : si le point d'entrée a déjà configuré les
    journaux, rien ne change ; sinon niveau INFO par défaut. Le point
    d'entrée reconfigure ensuite avec le vrai dossier (--config) et --debug
    (force=True) : pas de fichier ici, ~/.gcm n'est peut-être pas le dossier
    demandé.

    Returns:
        loguru.Logger: Le logger configuré.
    """
    _loguru_logger.debug("_setup_app_logger() called")
    import logging_config

    logging_config.configure_logging()
    _loguru_logger.debug(
        "_setup_app_logger() returning | niveau={}", logging_config.current_level()
    )
    return _loguru_logger


app_logger = _setup_app_logger()

SHELL = os.environ.get("SHELL") or "/bin/sh"  # absent sous cron, systemd, CI
# check Terminal version
TERMINAL_V048 = "spawn_async" in Vte.Terminal.__dict__
# Vérification runtime du signal 'output-written' (VTE >= 0.60)
# GObject.signal_lookup retourne 0 si le signal n'existe pas.

# Détection du binaire serial disponible (picocom > minicom > screen)
RDP_BIN = shutil.which("xfreerdp") or shutil.which("xfreerdp3") or "xfreerdp"

# Combinaisons de touches usuellement interceptées par le gestionnaire de
# fenêtres local avant d'atteindre un widget d'affichage distant embarqué
# (RDP/VNC/SPICE). Noms de touches Gdk (cf. gdk_keyval_from_name).
_SPECIAL_KEY_COMBOS = [
    ("Ctrl+Alt+Suppr", ("Control_L", "Alt_L", "Delete")),
    ("Ctrl+Échap", ("Control_L", "Escape")),
    ("Alt+Tab", ("Alt_L", "Tab")),
    ("Alt+F4", ("Alt_L", "F4")),
    ("Impr. écran", ("Print",)),
]


def build_remote_desktop_context_menu(
    btn_connect, btn_disconnect, send_special_keys_fn, extra_items=None
):
    """Construit le menu contextuel Connect/Disconnect/Touches speciales d'un onglet bureau distant.

    Reutilise pour le clic droit sur l'onglet/titre des onglets RDP/VNC/SPICE,
    a la place des boutons auparavant affiches dans la barre d'outils de
    l'onglet, pour s'harmoniser avec le menu contextuel des autres onglets.

    Args:
        btn_connect (Gtk.Button): bouton "Connect" existant (conserve pour
            son etat de sensibilite et son gestionnaire "clicked", mais non
            empaquete dans la barre d'outils).
        btn_disconnect (Gtk.Button): bouton "Disconnect" existant (idem).
        send_special_keys_fn (callable): fonction(list[str]) d'envoi d'une
            combinaison de touches a la session distante.
        extra_items (list[tuple[str, callable]], optional): entrees
            supplementaires (libelle, callback(widget)) inserees apres
            Connect/Disconnect (ex: peripheriques USB pour SPICE).

    Returns:
        Gtk.Menu: menu pret a afficher via popup().
    """
    menu = Gtk.Menu()

    item_connect = Gtk.MenuItem(label=_("Connect"))
    item_connect.set_sensitive(btn_connect.get_sensitive())
    item_connect.connect("activate", lambda _w: btn_connect.clicked())
    menu.append(item_connect)

    item_disconnect = Gtk.MenuItem(label=_("Disconnect"))
    item_disconnect.set_sensitive(btn_disconnect.get_sensitive())
    item_disconnect.connect("activate", lambda _w: btn_disconnect.clicked())
    menu.append(item_disconnect)

    for label, callback in extra_items or []:
        item = Gtk.MenuItem(label=label)
        item.connect("activate", callback)
        menu.append(item)

    menu.append(Gtk.SeparatorMenuItem())

    keys_item = Gtk.MenuItem(label=_("Special keys"))
    keys_submenu = Gtk.Menu()
    for key_label, keys in _SPECIAL_KEY_COMBOS:
        key_item = Gtk.MenuItem(label=key_label)
        key_item.connect("activate", lambda _w, k=keys: send_special_keys_fn(list(k)))
        keys_submenu.append(key_item)
    keys_item.set_submenu(keys_submenu)
    menu.append(keys_item)

    menu.show_all()
    return menu


def mount_iso_temp(iso_path):
    """Monte un fichier ISO localement (lecture seule) via udisksctl.

    Utilise udisksctl loop-setup + mount (mécanisme udisks2/polkit standard
    sur un bureau Linux, sans nécessiter root ni sudo). Le point de montage
    résultant peut ensuite être partagé comme un dossier classique (RDPDR
    pour RDP, webdav pour SPICE).

    Args:
        iso_path (str): Chemin absolu du fichier .iso à monter.

    Returns:
        tuple[str, str] | None: (mountpoint, loop_device) si succès, sinon None.
    """
    if not shutil.which("udisksctl"):
        app_logger.warning(
            f"mount_iso_temp | udisksctl introuvable, impossible de monter {iso_path}"
        )
        return None
    try:
        out = subprocess.run(
            ["udisksctl", "loop-setup", "-r", "-f", iso_path, "--no-user-interaction"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        # Sortie typique : "Mapped file /path/to.iso as /dev/loop0."
        loop_device = out.split(" as ")[-1].rstrip(".").strip()
        out = subprocess.run(
            ["udisksctl", "mount", "-b", loop_device, "--no-user-interaction"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        # Sortie typique : "Mounted /dev/loop0 at /run/media/user/LABEL."
        mountpoint = out.split(" at ")[-1].rstrip(".").strip()
        app_logger.debug(f"mount_iso_temp | {iso_path} monte sur {mountpoint} ({loop_device})")
        return mountpoint, loop_device
    except (subprocess.CalledProcessError, IndexError) as exc:
        app_logger.error(f"mount_iso_temp | echec montage {iso_path} : {exc}")
        return None


def umount_iso_temp(loop_device):
    """Démonte et libère un périphérique loop monté par mount_iso_temp().

    Args:
        loop_device (str): Chemin du périphérique loop (ex: /dev/loop0).
    """
    if not loop_device:
        return
    try:
        subprocess.run(
            ["udisksctl", "unmount", "-b", loop_device, "--no-user-interaction"],
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["udisksctl", "loop-delete", "-b", loop_device, "--no-user-interaction"],
            capture_output=True,
            text=True,
        )
        app_logger.debug(f"umount_iso_temp | {loop_device} demonte et libere")
    except Exception as exc:
        app_logger.warning(f"umount_iso_temp | erreur lors du demontage de {loop_device} : {exc}")


def vte_run(terminal, command, arg=None, extra_env=None):
    """Lance une commande dans un terminal VTE.

    Construit l'environnement complet (PATH, TERM, SSH_AUTH_SOCK, DISPLAY, etc.)
    et lance la commande via spawn_async (VTE >= 0.48) ou spawn_sync.

    Args:
        terminal (Vte.Terminal): Terminal VTE dans lequel lancer la commande.
        command (str): Chemin de l'executable a lancer.
        arg (list, optional): Arguments supplementaires. Defaults to None.
        extra_env (dict, optional): Variables d'environnement additionnelles
            (ex: mot de passe transmis via env plutot que ligne de commande,
            pour ne pas apparaitre dans `ps aux`). Defaults to None.
    """
    term_type = getattr(getattr(terminal, "host", None), "term", None) or "xterm"
    # fix #89: transmettre SSH_AUTH_SOCK et vars essentielles au processus VTE
    envv = ["PATH=%s" % os.getenv("PATH"), "TERM=%s" % term_type]
    for _ekey in (
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "GNOME_KEYRING_CONTROL",
    ):
        _eval = os.getenv(_ekey)
        if _eval:
            envv.append("%s=%s" % (_ekey, _eval))
    if extra_env:
        for _k, _v in extra_env.items():
            envv.append("%s=%s" % (_k, _v))
    args = []
    args.append(command)
    if arg:
        args += arg
    flag_spawn = (
        GLib.SpawnFlags.DEFAULT if command == SHELL else GLib.SpawnFlags.FILE_AND_ARGV_ZERO
    )
    if TERMINAL_V048:
        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.getenv("HOME"),
            args,
            envv,
            flag_spawn | GLib.SpawnFlags.SEARCH_PATH,
            None,
            None,
            -1,
            None,
            lambda term, pid, err, user_data: None,
            None,
        )
    else:
        terminal.spawn_sync(
            Vte.PtyFlags.DEFAULT,
            os.getenv("HOME"),
            args,
            envv,
            flag_spawn | GLib.SpawnFlags.DO_NOT_REAP_CHILD | GLib.SpawnFlags.SEARCH_PATH,
            None,
            None,
            None,
        )


class ManagementTabLabel(Gtk.Box):
    """Etiquette d'onglet pour les ecrans de gestion epingles (Parametres,
    editeur d'hote, editeur ssh/config, import...).

    Contrairement a ``NotebookTabLabel`` (onglets de session : menu
    contextuel splits/log/reconnexion, etc.), cette etiquette se limite a un
    titre et un bouton de fermeture : ces ecrans n'ont pas de notion de
    session, de split ou de reconnexion.
    """

    def __init__(self, title, on_close):
        """Initialise l'etiquette.

        Args:
            title (str): Texte affiche dans l'onglet.
            on_close (callable): Appele (sans argument) quand l'utilisateur
                clique sur le bouton de fermeture.
        """
        Gtk.Box.__init__(
            self, orientation=Gtk.Orientation.HORIZONTAL, homogeneous=False, spacing=4
        )

        self.label = Gtk.Label(label=title)
        self.pack_start(self.label, True, True, 0)
        self.label.show()

        close_image = Gtk.Image.new_from_icon_name("window-close", Gtk.IconSize.MENU)
        _, image_w, image_h = Gtk.icon_size_lookup(Gtk.IconSize.MENU)
        close_btn = Gtk.Button()
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.set_size_request(image_w + 7, image_h + 6)
        close_btn.add(close_image)
        close_btn.connect("clicked", lambda *_args: on_close())
        self.pack_start(close_btn, False, False, 0)
        close_btn.show_all()

        self.show()

    def get_text(self):
        """Retourne le texte de l'etiquette de l'onglet.

        Returns:
            str: Texte de l'onglet.
        """
        return self.label.get_text()


class _LogActionShim:
    """Adapte une ``Gio.SimpleAction`` a etat booleen a l'interface ``Gtk.CheckMenuItem``.

    Permet a ``Wmain.on_popupmenu`` (``gnome_connection_manager.py``, item
    ``"L"``) de continuer a lire/ecrire l'etat de la case a cocher "Enable
    logging" via ``get_active()``/``set_active()`` sans etre modifie, alors
    que le widget reel est desormais une ``Gio.SimpleAction`` portee par
    :class:`TabContextMenu` plutot qu'un ``Gtk.CheckMenuItem`` (portage
    GTK4-ready session-31, voir ``docs/gtk4-migration.md`` section 3.6-ter).
    """

    def __init__(self, action):
        """Initialise l'instance.

        Args:
            action (Gio.SimpleAction): Action a etat booleen ("log") portee
                par le ``Gio.SimpleActionGroup`` de :class:`TabContextMenu`.
        """
        self._action = action

    def get_active(self):
        """Retourne l'etat courant de la case a cocher simulee.

        Returns:
            bool: True si la case est cochee (action a l'etat ``True``).
        """
        return self._action.get_state().get_boolean()

    def set_active(self, value):
        """Definit l'etat de la case a cocher simulee.

        Args:
            value (bool): Nouvel etat a appliquer a l'action.
        """
        self._action.set_state(GLib.Variant.new_boolean(bool(value)))


class TabContextMenu:
    """Menu contextuel d'un onglet ("clic droit sur un onglet"), sur ``Gio.Menu``.

    Remplace l'ancien attribut ``popupMenuTab`` (un ``Gtk.Menu`` classique
    avant la session-31, voir ``gnome_connection_manager.py::createMenu``)
    par un trio ``Gio.Menu``/``Gio.SimpleActionGroup``/``Gtk.Popover``, sur le meme
    principe deja adopte pour le menu principal
    (``Wmain._build_primary_menu``) : ``Gio.Menu``/``Gio.SimpleAction``
    existent depuis GTK 3.4 et ``Gtk.Popover.new_from_model()`` depuis
    GTK 3.12, donc ce portage fonctionne des maintenant sous GTK3 et sera
    repris tel quel lors du passage effectif a GTK4 (voir
    ``docs/gtk4-migration.md`` section 3.6-ter, choisi comme premier des
    quatre menus contextuels a porter car le plus simple : pas d'injection
    dynamique par plugin, seulement de la visibilite conditionnelle).

    Portee initialement limitee a cette session (session-31) : seul le
    *contenu* du menu changeait de mecanisme. Le *declenchement*
    (``Gtk.EventBox`` + signal ``button-press-event`` sur
    ``Gdk.EventButton``, dans ``NotebookTabLabel.popupmenu``) restait
    inchange — sa migration vers ``Gtk.GestureClick`` (point 3 de l'audit
    session-29) etait alors un chantier distinct, commun aux quatre menus
    contextuels. **Porte depuis la session-41**
    (``NotebookTabLabel.on_label_pressed``, voir
    ``docs/gtk4-migration.md`` §3.6-decies) : :meth:`popup_at` prend
    desormais ``x``/``y`` directement (fournis par le signal ``pressed``
    de ``Gtk.GestureMultiPress``) plutot qu'un ``Gdk.EventButton`` complet,
    meme principe que :meth:`FolderContextMenu.popup_at`/
    :meth:`PopupMenu.popup_at` (sessions 39/40).

    Difference de comportement assumee et documentee (point 1 de l'audit
    session-29) : la case "Enable logging" n'est plus un ``Gtk.CheckMenuItem``
    mais une ``Gio.SimpleAction`` a etat booleen (``Gio.SimpleAction.
    new_stateful``) — voir :class:`_LogActionShim` pour l'adaptation cote
    gestionnaire existant.
    """

    #: Prefixe des actions dans le Gio.SimpleActionGroup, cf. references
    #: "tabmenu.<nom>" dans :meth:`rebuild`.
    ACTION_GROUP_PREFIX = "tabmenu"

    def __init__(self, dispatch):
        """Initialise l'instance.

        Args:
            dispatch (Callable[[object, str], None]): Gestionnaire
                d'activation existant (``Wmain.on_popupmenu``), reutilise
                tel quel pour ne pas dupliquer la logique metier des items
                du menu (renommer l'onglet, reset console, etc.) —
                l'activation d'une action appelle ``dispatch(widget, code)``
                exactement comme le faisait ``menuItem.connect("activate",
                self.on_popupmenu, code)`` avant ce portage.
        """
        app_logger.debug("TabContextMenu.__init__() called")
        self._dispatch = dispatch
        # Gtk.Label de l'onglet cible par le prochain popup_at() — fixe par
        # l'appelant (NotebookTabLabel.on_label_pressed) avant chaque
        # affichage, exactement comme l'ancien `self.popup.label = self.label`.
        self.label = None
        self.model = Gio.Menu()
        self.actions = Gio.SimpleActionGroup()

        self._log_action = Gio.SimpleAction.new_stateful(
            "log", None, GLib.Variant.new_boolean(False)
        )
        self._log_action.connect("change-state", self._on_log_change_state)
        self.actions.add_action(self._log_action)

        for name, item_code in (
            ("rename", "R"),
            ("reset", "RS"),
            ("clear", "RC"),
            ("reopen", "RO"),
            ("clone", "CC"),
            ("split-h", "SPH"),
            ("split-v", "SPV"),
            ("unsplit", "USP"),
        ):
            self._add_simple_action(name, item_code)

        self.popover = Gtk.Popover.new_from_model(None, self.model)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover.insert_action_group(self.ACTION_GROUP_PREFIX, self.actions)
        app_logger.debug("TabContextMenu.__init__() returning")

    def _add_simple_action(self, name, item_code):
        """Enregistre une action simple qui relaie l'activation vers ``dispatch``.

        Args:
            name (str): Nom de l'action dans le ``Gio.SimpleActionGroup``
                (sans le prefixe ``tabmenu.``).
            item_code (str): Code d'action historique attendu par
                ``Wmain.on_popupmenu`` (ex. ``"R"`` pour "Rename tab").
        """
        app_logger.debug(
            f"TabContextMenu._add_simple_action() called | name={name!r} item_code={item_code!r}"
        )
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", lambda a, p, code=item_code: self._dispatch(None, code))
        self.actions.add_action(action)
        app_logger.debug("TabContextMenu._add_simple_action() returning")

    def _on_log_change_state(self, action, value):
        """Applique le changement d'etat de la case "Enable logging" puis relaie.

        Reproduit l'ordre de l'ancien ``Gtk.CheckMenuItem`` : GTK bascule
        l'etat affiche avant d'emettre l'evenement d'activation, et le
        gestionnaire metier (``Wmain.on_popupmenu``, item ``"L"``) peut
        ensuite revenir dessus (ex. mode de log continu actif — voir
        ``gnome_connection_manager.py``) via ``set_active(False)`` sur le
        shim, qui rappelle ``action.set_state()``.

        Args:
            action (Gio.SimpleAction): Action a etat booleen ("log").
            value (GLib.Variant): Nouvel etat demande.
        """
        app_logger.debug(
            f"TabContextMenu._on_log_change_state() called | value={value.get_boolean()!r}"
        )
        action.set_state(value)
        self._dispatch(_LogActionShim(action), "L")
        app_logger.debug("TabContextMenu._on_log_change_state() returning")

    def rebuild(self, *, is_active, multi_pane, log_active):
        """Reconstruit le modele ``Gio.Menu`` selon l'etat courant de l'onglet.

        Reproduit a l'identique la visibilite conditionnelle de l'ancien
        ``Gtk.Menu`` (``mnuReopen``, ``mnuSplitH``/``mnuSplitV`` — voir
        ``docs/gtk4-migration.md`` section 3.6-ter, point 2) : ``Gio.Menu``
        restant mutable a l'execution (il notifie ``items-changed``, que le
        ``Gtk.Popover`` cree par ``new_from_model()`` ecoute pour se
        redessiner automatiquement), chaque ouverture reconstruit
        entierement le modele plutot que de cacher/afficher des
        ``Gtk.MenuItem`` persistants — qui n'existent plus dans ce
        mecanisme.

        Args:
            is_active (bool): Etat "actif" de l'onglet
                (``NotebookTabLabel.is_active``). Quand ``False``, l'entree
                "Reconnect to host" est proposee (ancien
                ``mnuReopen.show()``).
            multi_pane (bool): ``True`` si le notebook contient plusieurs
                onglets (``nb.get_n_pages() > 1`` calcule par l'appelant) —
                conditionne les entrees "Split H"/"Split V" (ancien
                ``mnuSplitH``/``mnuSplitV`` show/hide).
            log_active (bool): Etat courant du log du terminal cible, pour
                initialiser la case a cocher "Enable logging" (ancien
                ``mnuLog.set_active()``).
        """
        app_logger.debug(
            f"TabContextMenu.rebuild() called | is_active={is_active!r} "
            f"multi_pane={multi_pane!r} log_active={log_active!r}"
        )
        self._log_action.set_state(GLib.Variant.new_boolean(bool(log_active)))

        self.model.remove_all()
        prefix = self.ACTION_GROUP_PREFIX
        labels = {
            "rename": _("Rename tab"),
            "reset": _("Reset console"),
            "clear": _("Reset and Clear console"),
            "reopen": _("Reconnect to host"),
            "clone": _("Clone console"),
            "log": _("Enable logging"),
            "split-h": _("Split H"),
            "split-v": _("Split V"),
            "unsplit": _("Unsplit all consoles"),
        }
        # Disposition (quelles entrees, dans quelles sections) calculee par
        # une fonction pure sans dependance Gtk/Gio — voir
        # tab_context_menu_core.py, extrait pour rester testable.
        for section_names in build_tab_context_menu_layout(
            is_active=is_active, multi_pane=multi_pane
        ):
            section = Gio.Menu()
            for name in section_names:
                section.append(labels[name], f"{prefix}.{name}")
            self.model.append_section(None, section)
        app_logger.debug("TabContextMenu.rebuild() returning")

    def popup_at(self, relative_to, x, y):
        """Affiche le popover positionne sur le point de clic.

        Remplace l'ancien appel a la signature ``Gtk.Menu.popup()`` a six
        arguments positionnels (deja depreciee en GTK3 et purement et
        simplement supprimee en GTK4 avec ``Gtk.Menu`` lui-meme — voir
        ``docs/gtk4-migration.md`` section 3.6-ter point 3) par
        ``Gtk.Popover.set_pointing_to()`` + ``popup()``, disponibles a
        l'identique en GTK3 comme en GTK4.

        Signature changee en session-41 : recoit desormais ``x``/``y``
        directement (coordonnees relatives a ``relative_to``) plutot qu'un
        ``Gdk.EventButton`` complet — consequence du portage du
        declencheur (``NotebookTabLabel.on_label_pressed``) vers
        ``Gtk.GestureMultiPress``, dont le signal ``pressed`` fournit deja
        ces coordonnees a part, sans objet evenement associe (voir
        ``docs/gtk4-migration.md`` section 3.6-decies) — meme changement
        que :meth:`FolderContextMenu.popup_at`/:meth:`PopupMenu.popup_at`
        (sessions 39/40). Seul appelant de cette methode dans tout le
        depot, donc changement de signature sans impact ailleurs.

        Args:
            relative_to (Gtk.Widget): Widget sur lequel positionner le
                popover (le ``Gtk.EventBox`` de l'onglet clique,
                ``self.eb``).
            x (float): Coordonnee X du clic, relative a ``relative_to``.
            y (float): Coordonnee Y du clic, relative a ``relative_to``.
        """
        app_logger.debug(
            f"TabContextMenu.popup_at() called | relative_to={relative_to!r} x={x!r} y={y!r}"
        )
        self.popover.set_relative_to(relative_to)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()
        app_logger.debug("TabContextMenu.popup_at() returning")


class FolderContextMenu:
    """Menu contextuel du panneau de serveurs ("clic droit sur treeServers"), sur ``Gio.Menu``.

    Remplace l'ancien attribut ``popupMenuFolder`` (un ``Gtk.Menu`` classique
    avant la session-32, voir ``gnome_connection_manager.py::createMenu``)
    par un trio ``Gio.Menu``/``Gio.SimpleActionGroup``/``Gtk.Popover``, meme
    principe que :class:`TabContextMenu` (session-31) et que le menu
    principal (``Wmain._build_primary_menu``) : ``Gio.Menu``/
    ``Gio.SimpleAction`` existent depuis GTK 3.4 et
    ``Gtk.Popover.new_from_model()`` depuis GTK 3.12, donc ce portage
    fonctionne des maintenant sous GTK3 et sera repris tel quel lors du
    passage effectif a GTK4 (voir ``docs/gtk4-migration.md`` section
    3.6-quinquies — deuxieme des quatre menus contextuels de l'audit
    session-29 a etre porte, choisi apres ``popupMenuTab`` car son mecanisme
    d'injection dynamique par plugin se ramene au meme motif ``add_action()``
    deja utilise par ``Wmain._build_primary_menu``).

    Portée volontairement limitée lors de sa création en session-32, comme
    pour :class:`TabContextMenu` : seul le *contenu* du menu changeait alors
    de mécanisme, le *déclenchement* (``button-press-event`` sur
    ``Gdk.EventButton``, dans ``Wmain.on_tvServers_button_press_event``)
    restant inchangé — sa migration vers ``Gtk.GestureClick`` (point 3 de
    l'audit session-29) était un chantier distinct, commun aux quatre menus
    contextuels.

    **Mise à jour session-39** : ce déclenchement est désormais porté lui
    aussi, vers ``Gtk.GestureMultiPress`` (``Wmain.on_tvServers_pressed``,
    premier des trois sites identifiés par l'audit session-38 — voir
    ``docs/gtk4-migration.md`` section 3.6-octies). Conséquence directe sur
    cette classe : :meth:`popup_at` reçoit désormais des coordonnées
    ``x``/``y`` directement (fournies telles quelles par le signal
    ``pressed``) plutôt qu'un ``Gdk.EventButton`` complet, ce signal n'en
    transmettant plus.

    Difference de mecanisme assumee et documentee (comme pour la case
    "Enable logging" de :class:`TabContextMenu`) : les items specifiques a
    un protocole (``plugin.build_folder_context_menu_items()``) ne sont plus
    des ``Gtk.MenuItem`` ajoutes une fois pour toutes a la construction, mais
    des actions reconstruites a chaque ouverture par :meth:`rebuild`, sur le
    meme principe que le reste du menu (``Gio.Menu`` mutable, cf.
    ``TabContextMenu.rebuild``) — pas de liste ``protocol_extra_items`` a
    tenir a jour separement.
    """

    #: Prefixe des actions dans le Gio.SimpleActionGroup, cf. references
    #: "foldermenu.<nom>" dans :meth:`rebuild`.
    ACTION_GROUP_PREFIX = "foldermenu"

    def __init__(self, callbacks, dispatch):
        """Initialise l'instance.

        Args:
            callbacks (dict[str, Callable[[], None]]): Gestionnaires
                zero-argument pour les entrees statiques cablees directement
                sur une methode existante de ``Wmain`` plutot que sur
                ``on_popupmenu`` (cles attendues : ``"connect"``, ``"add"``,
                ``"new-group"``, ``"rename-group"``, ``"group-color"``,
                ``"group-color-reset"``, ``"edit"``, ``"delete"``,
                ``"expand"``, ``"collapse"`` — memes methodes qu'avant ce
                portage, ex. ``lambda: self.on_btnConnect_clicked(None)``,
                deja le motif utilise par ``Wmain._build_primary_menu``).
            dispatch (Callable[[object, str], None]): Gestionnaire
                d'activation existant (``Wmain.on_popupmenu``), reutilise
                pour les deux entrees qui passaient deja par lui avant ce
                portage (``"H"`` = Copy Address, ``"D"`` = Duplicate Host —
                voir ``folder_context_menu_core.FOLDER_CONTEXT_MENU_DISPATCH_ACTIONS``).
        """
        app_logger.debug("FolderContextMenu.__init__() called")
        self._callbacks = callbacks
        self._dispatch = dispatch
        # Callback fournissant les items specifiques au protocole a inserer
        # dans le menu (label, callback zero-argument), evalue a chaque
        # rebuild() plutot que fige a la construction — fixe par l'appelant
        # (Wmain.createMenu) avant le premier popup, meme principe que
        # ``self.label`` sur TabContextMenu.
        self.protocol_items_provider = None
        self.model = Gio.Menu()
        self.actions = Gio.SimpleActionGroup()

        for name, handler in callbacks.items():
            self._add_simple_action(name, lambda a, p, h=handler: h())

        for name, item_code in (("copy-address", "H"), ("duplicate", "D")):
            self._add_simple_action(name, lambda a, p, code=item_code: self._dispatch(None, code))

        self.popover = Gtk.Popover.new_from_model(None, self.model)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover.insert_action_group(self.ACTION_GROUP_PREFIX, self.actions)
        app_logger.debug("FolderContextMenu.__init__() returning")

    def _add_simple_action(self, name, activate_handler):
        """Enregistre une action simple sur le Gio.SimpleActionGroup.

        Args:
            name (str): Nom de l'action (sans le prefixe ``foldermenu.``).
            activate_handler (Callable[[Gio.SimpleAction, GLib.Variant], None]):
                Gestionnaire connecte au signal ``activate`` de l'action.
        """
        app_logger.debug(f"FolderContextMenu._add_simple_action() called | name={name!r}")
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", activate_handler)
        self.actions.add_action(action)
        app_logger.debug("FolderContextMenu._add_simple_action() returning")

    def rebuild(self, *, click_target):
        """Reconstruit le modele ``Gio.Menu`` selon la cible du clic droit.

        Reproduit a l'identique la visibilite conditionnelle de l'ancien
        ``Gtk.Menu`` (``Wmain.on_tvServers_button_press_event``, voir
        ``docs/gtk4-migration.md`` section 3.6-quinquies) : ``Gio.Menu``
        restant mutable a l'execution, chaque ouverture reconstruit
        entierement le modele plutot que de cacher/afficher des
        ``Gtk.MenuItem`` persistants — meme principe que
        ``TabContextMenu.rebuild``.

        Args:
            click_target (str): Cible du clic droit sur ``treeServers`` —
                ``"empty"``, ``"folder"`` ou ``"host"``, voir
                ``folder_context_menu_core.CLICK_TARGETS``.
        """
        app_logger.debug(f"FolderContextMenu.rebuild() called | click_target={click_target!r}")
        prefix = self.ACTION_GROUP_PREFIX
        labels = {
            "connect": _("Connect"),
            "copy-address": _("Copy Address"),
            "add": _("Add Host"),
            "new-group": _("New Group…"),
            "rename-group": _("Rename Group…"),
            "group-color": _("Choose Color…"),
            "group-color-reset": _("Reset Color"),
            "edit": _("Edit"),
            "delete": _("Remove"),
            "duplicate": _("Duplicate Host"),
            "expand": _("Expand all"),
            "collapse": _("Collapse all"),
        }
        self.model.remove_all()
        # Disposition (quelles entrees, dans quelles sections) calculee par
        # une fonction pure sans dependance Gtk/Gio — voir
        # folder_context_menu_core.py, extrait pour rester testable.
        for section_names in build_folder_context_menu_layout(click_target=click_target):
            section = Gio.Menu()
            for name in section_names:
                section.append(labels[name], f"{prefix}.{name}")
            self.model.append_section(None, section)

        if (
            should_show_protocol_items(click_target=click_target)
            and self.protocol_items_provider is not None
        ):
            protocol_section = Gio.Menu()
            for index, (label, callback) in enumerate(self.protocol_items_provider()):
                action_name = f"protocol-{index}"
                action = Gio.SimpleAction.new(action_name, None)
                action.connect("activate", lambda a, p, cb=callback: cb())
                # Remplace toute action du meme nom laissee par un rebuild()
                # precedent (nombre d'items variable selon les plugins) :
                # Gio.SimpleActionGroup n'a pas d'equivalent a foreach/remove
                # sur les Gtk.Menu, on ecrase simplement l'action existante.
                self.actions.add_action(action)
                protocol_section.append(label, f"{prefix}.{action_name}")
            self.model.append_section(None, protocol_section)
        app_logger.debug("FolderContextMenu.rebuild() returning")

    def popup_at(self, relative_to, x, y):
        """Affiche le popover positionné sur le point de clic.

        Remplace l'ancien appel à ``Gtk.Menu.popup()`` à six arguments
        positionnels (déjà dépréciée en GTK3 et purement et simplement
        supprimée en GTK4 avec ``Gtk.Menu`` lui-même) par
        ``Gtk.Popover.set_pointing_to()`` + ``popup()``, disponibles à
        l'identique en GTK3 comme en GTK4 — même principe que
        ``TabContextMenu.popup_at``.

        Signature changée en session-39 : reçoit désormais ``x``/``y``
        directement (coordonnées relatives à ``relative_to``) plutôt qu'un
        ``Gdk.EventButton`` complet — conséquence du portage du déclencheur
        (``Wmain.on_tvServers_pressed``) vers ``Gtk.GestureMultiPress``,
        dont le signal ``pressed`` fournit déjà ces coordonnées à part, sans
        objet évènement associé (voir ``docs/gtk4-migration.md`` section
        3.6-octies). Seul appelant de cette méthode dans tout le dépôt,
        donc changement de signature sans impact ailleurs.

        Args:
            relative_to (Gtk.Widget): Widget sur lequel positionner le
                popover (``treeServers``).
            x (float): Coordonnée X du clic, relative à ``relative_to``.
            y (float): Coordonnée Y du clic, relative à ``relative_to``.
        """
        app_logger.debug(
            f"FolderContextMenu.popup_at() called | relative_to={relative_to!r} x={x!r} y={y!r}"
        )
        self.popover.set_relative_to(relative_to)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()
        app_logger.debug("FolderContextMenu.popup_at() returning")


class PopupMenu:
    """Menu contextuel du terminal ("clic droit dans un terminal"), sur ``Gio.Menu``.

    Remplace l'ancien attribut ``popupMenu`` (un ``Gtk.Menu`` classique avant
    la session-33, voir ``gnome_connection_manager.py::createMenu``) par un
    trio ``Gio.Menu``/``Gio.SimpleActionGroup``/``Gtk.Popover``, meme
    principe que :class:`TabContextMenu` (session-31) et
    :class:`FolderContextMenu` (session-32) : ``Gio.Menu``/``Gio.SimpleAction``
    existent depuis GTK 3.4 et ``Gtk.Popover.new_from_model()`` depuis
    GTK 3.12, donc ce portage fonctionne des maintenant sous GTK3 et sera
    repris tel quel lors du passage effectif a GTK4 (voir
    ``docs/gtk4-migration.md`` section 3.6-sexies — troisieme et dernier des
    quatre menus contextuels applicatifs de l'audit session-29 a etre porte).

    Portee initialement limitee a cette session (session-33), comme pour
    les deux precedents : seul le *contenu* du menu changeait de mecanisme,
    le *declenchement* (``button-press-event`` sur ``Gdk.EventButton``,
    dans ``Wmain.on_terminal_click``) restant inchange — sa migration vers
    ``Gtk.GestureClick`` (point 3 de l'audit session-29) etait alors un
    chantier distinct, commun aux quatre menus contextuels. **Porte depuis
    la session-40** (``Wmain.on_terminal_pressed``, voir
    ``docs/gtk4-migration.md`` §3.6-nonies) : :meth:`popup_at` prend
    desormais ``x``/``y`` directement (fournis par le signal ``pressed`` de
    ``Gtk.GestureMultiPress``) plutot qu'un ``Gdk.EventButton`` complet,
    meme principe que :meth:`FolderContextMenu.popup_at`.

    Deux differences de mecanisme assumees et documentees :

    - La case "Enable logging" n'est plus un ``Gtk.CheckMenuItem`` mais une
      ``Gio.SimpleAction`` a etat booleen, comme pour :class:`TabContextMenu`
      — voir :class:`_LogActionShim`, reutilise tel quel (dispatch sur le
      code historique ``"L2"`` plutot que ``"L"``, pour rester distinguable
      cote ``Wmain.on_popupmenu``).
    - Contrairement a :class:`TabContextMenu`/:class:`FolderContextMenu`, la
      disposition du menu (``popup_menu_core.POPUP_MENU_SECTIONS``) est
      CONSTANTE : ce qui varie a l'ouverture est la sensibilite de quatre
      actions (ancien ``.set_sensitive()``), pas la presence d'un item —
      voir :meth:`rebuild`, qui n'a donc pas besoin de reconstruire
      ``self.model`` a chaque appel (``Gio.SimpleAction`` emet
      ``notify::enabled``, ecoute automatiquement par le ``Gtk.Popover``
      construit via ``new_from_model()``).

    Le sous-menu dynamique "Custom commands" (ancien
    ``self.popupMenu.mnuCommands``/``populateCommandsMenu()``) devient un
    veritable sous-menu ``Gio.Menu`` imbrique (``append_submenu()``, deja le
    mecanisme utilise par ``Wmain._build_primary_menu`` pour "Imports"/
    "Exports"/etc.) plutot qu'un ``Gtk.Menu`` attache via ``set_submenu()``
    — voir :meth:`populate_commands`. Le pont depuis le menu hamburger
    (ancien ``add_popup_action("custom-commands", ...)`` de
    ``_build_primary_menu``, qui appelait ``.popup()`` directement sur le
    ``Gtk.Menu``) est remplace par :meth:`popup_commands_at`, un second
    ``Gtk.Popover`` construit sur le meme modele partage.
    """

    #: Prefixe des actions dans le Gio.SimpleActionGroup, cf. references
    #: "popupmenu.<nom>" dans :meth:`__init__`/:meth:`populate_commands`.
    ACTION_GROUP_PREFIX = "popupmenu"

    def __init__(self, dispatch):
        """Initialise l'instance.

        Args:
            dispatch (Callable[[object, str], None]): Gestionnaire
                d'activation existant (``Wmain.on_popupmenu``), reutilise
                tel quel pour ne pas dupliquer la logique metier des items
                du menu (copier/coller, reset console, etc.) — l'activation
                d'une action appelle ``dispatch(widget, code)`` exactement
                comme le faisait ``menuItem.connect("activate",
                self.on_popupmenu, code)`` avant ce portage.
        """
        app_logger.debug("PopupMenu.__init__() called")
        self._dispatch = dispatch
        # Terminal VTE cible du prochain popup_at() — fixe par l'appelant
        # (Wmain.on_terminal_click) avant chaque affichage, exactement comme
        # l'ancien `self.popupMenu.terminal = widget`.
        self.terminal = None
        self.model = Gio.Menu()
        self.actions = Gio.SimpleActionGroup()

        self._log_action = Gio.SimpleAction.new_stateful(
            "log", None, GLib.Variant.new_boolean(False)
        )
        self._log_action.connect("change-state", self._on_log_change_state)
        self.actions.add_action(self._log_action)

        for name, item_code in POPUP_MENU_ACTIONS:
            self._add_simple_action(name, item_code)

        prefix = self.ACTION_GROUP_PREFIX
        labels = {
            "copy": _("Copy"),
            "paste": _("Paste"),
            "copy-paste": _("Copy and Paste"),
            "select-all": _("Select all"),
            "copy-all": _("Copy all"),
            "save-buffer": _("Save buffer to file"),
            "split-h": _("Split H"),
            "split-v": _("Split V"),
            "unsplit": _("Unsplit all consoles"),
            "reset": _("Reset console"),
            "clear": _("Reset and Clear console"),
            "clone": _("Clone console"),
            "log": _("Enable logging"),
            "close": _("Close console"),
        }
        # Disposition constante (pas de rebuild du modele a l'ouverture, a
        # la difference de TabContextMenu/FolderContextMenu) — voir
        # popup_menu_core.POPUP_MENU_SECTIONS et la docstring de classe.
        for section_names in POPUP_MENU_SECTIONS:
            section = Gio.Menu()
            for name in section_names:
                section.append(labels[name], f"{prefix}.{name}")
            self.model.append_section(None, section)

        # Sous-menu dynamique "Custom commands" (3e section, non modelisee
        # dans POPUP_MENU_SECTIONS — voir populate_commands()) : modele
        # mutable repeuple par l'appelant, jamais reconstruit ici.
        self.commands_model = Gio.Menu()
        self._last_command_count = 0
        commands_section = Gio.Menu()
        commands_section.append_submenu(_("Custom commands"), self.commands_model)
        self.model.append_section(None, commands_section)

        self.popover = Gtk.Popover.new_from_model(None, self.model)
        self.popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover.insert_action_group(prefix, self.actions)

        # Popover autonome pour l'action-pont du menu hamburger (fusionne
        # l'ancien add_popup_action("custom-commands", lambda: self.popupMenu
        # .mnuCommands) de _build_primary_menu, qui appelait .popup()
        # directement sur le Gtk.Menu) — meme modele et meme groupe
        # d'actions que ci-dessus, donc toujours synchronise.
        self.commands_popover = Gtk.Popover.new_from_model(None, self.commands_model)
        self.commands_popover.set_position(Gtk.PositionType.BOTTOM)
        self.commands_popover.insert_action_group(prefix, self.actions)
        app_logger.debug("PopupMenu.__init__() returning")

    def _add_simple_action(self, name, item_code):
        """Enregistre une action simple qui relaie l'activation vers ``dispatch``.

        Args:
            name (str): Nom de l'action dans le ``Gio.SimpleActionGroup``
                (sans le prefixe ``popupmenu.``).
            item_code (str): Code d'action historique attendu par
                ``Wmain.on_popupmenu`` (ex. ``"C"`` pour "Copy").
        """
        app_logger.debug(
            f"PopupMenu._add_simple_action() called | name={name!r} item_code={item_code!r}"
        )
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", lambda a, p, code=item_code: self._dispatch(None, code))
        self.actions.add_action(action)
        app_logger.debug("PopupMenu._add_simple_action() returning")

    def _on_log_change_state(self, action, value):
        """Applique le changement d'etat de la case "Enable logging" puis relaie.

        Reproduit l'ordre de l'ancien ``Gtk.CheckMenuItem`` : GTK bascule
        l'etat affiche avant d'emettre l'evenement d'activation, et le
        gestionnaire metier (``Wmain.on_popupmenu``, item ``"L2"``) peut
        ensuite revenir dessus (ex. mode de log continu actif — voir
        ``gnome_connection_manager.py``) via ``set_active(False)`` sur le
        shim, qui rappelle ``action.set_state()`` — meme principe que
        ``TabContextMenu._on_log_change_state`` (code ``"L2"`` plutot que
        ``"L"`` pour rester distinguable cote ``Wmain.on_popupmenu``).

        Args:
            action (Gio.SimpleAction): Action a etat booleen ("log").
            value (GLib.Variant): Nouvel etat demande.
        """
        app_logger.debug(
            f"PopupMenu._on_log_change_state() called | value={value.get_boolean()!r}"
        )
        action.set_state(value)
        self._dispatch(_LogActionShim(action), "L2")
        app_logger.debug("PopupMenu._on_log_change_state() returning")

    def rebuild(self, *, has_selection, multi_pane, has_split_notebook, log_active):
        """Met a jour la sensibilite des actions variables et la case "Enable logging".

        Contrairement a ``TabContextMenu.rebuild()``/
        ``FolderContextMenu.rebuild()``, ne touche PAS ``self.model`` : la
        disposition de popupMenu (``popup_menu_core.POPUP_MENU_SECTIONS``)
        est constante, seule la SENSIBILITE de quatre actions varie selon le
        contexte du clic droit (ancien ``mnuCopy``/``mnuSplitH``/
        ``mnuSplitV``/``mnuUnsplit.set_sensitive()``, voir
        ``docs/gtk4-migration.md`` section 3.6-sexies) — ``Gio.SimpleAction``
        emet ``notify::enabled``, ecoute automatiquement par le
        ``Gtk.Popover`` construit via ``new_from_model()``, donc
        ``set_enabled()`` suffit sans reconstruire le modele.

        Args:
            has_selection (bool): Le terminal cible a-t-il une selection en
                cours (ancien ``widget.get_has_selection()``) — conditionne
                la sensibilite de "Copy".
            multi_pane (bool): ``True`` si le notebook contient plusieurs
                onglets (ancien ``nb.get_n_pages() > 1``) — conditionne la
                sensibilite de "Split H"/"Split V".
            has_split_notebook (bool): ``True`` si un notebook scinde existe
                (ancien ``Wmain.find_notebook(...) is not None``) —
                conditionne la sensibilite de "Unsplit all consoles".
            log_active (bool): Etat courant du log du terminal cible, pour
                initialiser la case a cocher "Enable logging" (ancien
                ``mnuLog.set_active()``).
        """
        app_logger.debug(
            f"PopupMenu.rebuild() called | has_selection={has_selection!r} "
            f"multi_pane={multi_pane!r} has_split_notebook={has_split_notebook!r} "
            f"log_active={log_active!r}"
        )
        self._log_action.set_state(GLib.Variant.new_boolean(bool(log_active)))
        enabled = compute_enabled_actions(
            has_selection=has_selection,
            multi_pane=multi_pane,
            has_split_notebook=has_split_notebook,
        )
        for name, is_enabled in enabled.items():
            self.actions.lookup_action(name).set_enabled(is_enabled)
        app_logger.debug("PopupMenu.rebuild() returning")

    def populate_commands(self, commands):
        """Repeuple le sous-menu dynamique "Custom commands".

        Equivalent a l'ancien ``populateCommandsMenu()``
        (``foreach(...).remove(...)`` puis reconstruction complete) :
        ``Gio.Menu`` restant mutable a l'execution, ``remove_all()`` puis
        reinsertion, meme principe que
        ``TabContextMenu.rebuild()``/``FolderContextMenu.rebuild()``. A la
        difference de la section "protocol" de ``FolderContextMenu`` (dont
        le nombre d'items est stable une fois les plugins charges), le
        nombre de commandes personnalisees peut diminuer d'un appel a
        l'autre (edition des raccourcis dans les Preferences, voir l'ancien
        second site d'appel de ``populateCommandsMenu()``) : les actions
        ``"command-N"`` au-dela du nouveau compte sont explicitement
        retirees plutot que simplement laissees orphelines, pour eviter une
        fuite lente sur une session longue.

        Args:
            commands (list[tuple[str, str]]): Paires ``(raccourci,
                commande)``, deja filtrees et ordonnees par
                ``popup_menu_core.select_command_shortcuts()``.
        """
        app_logger.debug(f"PopupMenu.populate_commands() called | count={len(commands)!r}")
        prefix = self.ACTION_GROUP_PREFIX
        self.commands_model.remove_all()
        for index in range(len(commands), self._last_command_count):
            self.actions.remove_action(f"command-{index}")
        for index, (shortcut, command) in enumerate(commands):
            action_name = f"command-{index}"
            action = Gio.SimpleAction.new(action_name, None)
            action.connect("activate", lambda a, p, cmd=command: self._dispatch(None, "CP", cmd))
            # Remplace toute action du meme nom laissee par un
            # populate_commands() precedent (nombre de commandes variable)
            # — meme motif que la section "protocol" de
            # FolderContextMenu.rebuild().
            self.actions.add_action(action)
            self.commands_model.append(
                format_command_label(shortcut, command), f"{prefix}.{action_name}"
            )
        self._last_command_count = len(commands)
        app_logger.debug("PopupMenu.populate_commands() returning")

    def popup_at(self, relative_to, x, y):
        """Affiche le popover positionne sur le point de clic.

        Remplace l'ancien appel a la signature ``Gtk.Menu.popup()`` a six
        arguments positionnels (deja depreciee en GTK3 et purement et
        simplement supprimee en GTK4 avec ``Gtk.Menu`` lui-meme — voir
        ``docs/gtk4-migration.md`` section 3.6-ter point 3) par
        ``Gtk.Popover.set_pointing_to()`` + ``popup()``, disponibles a
        l'identique en GTK3 comme en GTK4 — meme principe que
        ``TabContextMenu.popup_at``/``FolderContextMenu.popup_at``.

        Signature changee de ``(relative_to, event)`` a ``(relative_to, x,
        y)`` en session-40 (``docs/gtk4-migration.md`` §3.6-nonies), en
        meme temps que le declencheur (``Wmain.on_terminal_click``) est
        passe de ``button-press-event``/``Gdk.EventButton`` a
        ``Gtk.GestureMultiPress`` (signal ``pressed``, qui fournit deja
        ``x``/``y`` separement, sans objet evenement associe) — cette
        methode n'utilisait de toute facon que ``event.x``/``event.y``.
        Seul appelant dans tout le depot (``Wmain.on_terminal_pressed``),
        donc changement sans impact ailleurs — verifie par recherche
        textuelle avant modification, meme demarche que pour
        ``FolderContextMenu.popup_at`` (session-39).

        Args:
            relative_to (Gtk.Widget): Widget sur lequel positionner le
                popover (le terminal VTE clique).
            x (float): Coordonnee X du clic, relative a ``relative_to``.
            y (float): Coordonnee Y du clic, relative a ``relative_to``.
        """
        app_logger.debug(
            f"PopupMenu.popup_at() called | relative_to={relative_to!r} x={x!r} y={y!r}"
        )
        self.popover.set_relative_to(relative_to)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()
        app_logger.debug("PopupMenu.popup_at() returning")

    def popup_commands_at(self, relative_to):
        """Affiche uniquement le sous-menu "Custom commands" en pop-up autonome.

        Remplace l'ancienne action-pont de ``Wmain._build_primary_menu``
        (``add_popup_action("custom-commands", lambda:
        self.popupMenu.mnuCommands)``, qui appelait ``Gtk.Menu.popup()``
        directement sur ``mnuCommands`` depuis une action du menu hamburger)
        — ce dernier n'existe plus en tant que widget autonome depuis ce
        portage (``Gio.Menu`` n'est qu'un modele, pas un widget qu'on peut
        "popup"). Reutilise le meme modele (``self.commands_model``) que le
        sous-menu imbrique dans ``self.model``, via un second
        ``Gtk.Popover`` construit sur ce meme modele — les deux
        presentations restent synchronisees puisqu'elles partagent le meme
        modele mutable et le meme groupe d'actions.

        Args:
            relative_to (Gtk.Widget): Widget sur lequel positionner le
                popover (ancien bouton du menu hamburger,
                ``self.btnPrimaryMenu``).
        """
        app_logger.debug(f"PopupMenu.popup_commands_at() called | relative_to={relative_to!r}")
        self.commands_popover.set_relative_to(relative_to)
        self.commands_popover.popup()
        app_logger.debug("PopupMenu.popup_commands_at() returning")


class NotebookTabLabel(Gtk.Box):
    """Notebook tab label with close button."""

    def __init__(
        self,
        title,
        owner_,
        widget_,
        popup_,
        host_auto_close_tab="",
        host_post_connect_command=None,
    ):
        """Initialise l'instance.

        Args:
            title: Parametre title.
            owner_: Parametre owner_.
            widget_: Parametre widget_.
            popup_: Parametre popup_.
            host_auto_close_tab (str): Surcharge par hôte de
                conf.AUTO_CLOSE_TAB (Host.auto_close_tab, voir models.py) :
                "" = hérite du réglage global, "0"/"1"/"2" =
                Jamais/Toujours/Seulement en sortie propre. Défaut "" pour
                les appelants qui ne connaissent pas d'hôte (division de
                notebook, qui reporte la valeur de l'onglet d'origine au
                lieu de la fixer elle-même — voir gnome_connection_manager.py).
            host_post_connect_command (str | None): Commande "après
                connexion" de l'hôte (Host.post_connect_command, voir
                models.py), déjà résolue (marqueurs {name}/{address}/
                {group}/{protocol} substitués) par
                gnome_connection_manager.resolve_post_connect_command() au
                moment de l'ouverture de l'onglet — ``None`` si aucun hook
                n'est configuré. Lancée par :meth:`mark_tab_as_closed` (donc
                uniquement pour les onglets terminal VTE, seul point
                d'accroche "connexion terminée" disponible aujourd'hui).
                Défaut ``None`` pour les appelants qui ne connaissent pas
                d'hôte (division de notebook, qui reporte la valeur de
                l'onglet d'origine, comme pour host_auto_close_tab).
        """
        Gtk.Box.__init__(
            self, orientation=Gtk.Orientation.HORIZONTAL, homogeneous=False, spacing=0
        )

        self.title = title
        self.owner = owner_
        self.host_auto_close_tab = host_auto_close_tab
        self.host_post_connect_command = host_post_connect_command
        self.eb = Gtk.EventBox()
        label = self.label = Gtk.Label()
        # Declencheur du menu contextuel de l'onglet (TabContextMenu ou, si
        # l'onglet est un bureau distant RDP/VNC/SPICE, le menu Gtk.Menu
        # legacy de build_remote_desktop_context_menu()), porte vers
        # Gtk.GestureMultiPress en session-41 — troisieme et dernier des
        # trois sites identifies par l'audit session-38 point 3 (voir
        # docs/gtk4-migration.md section 3.6-decies), apres treeServers
        # (session-39) et les terminaux (session-40). Remplace l'ancien
        # connect("button-press-event", ...) sur Gdk.EventButton.
        # Reference conservee sur self (self._label_click_gesture) :
        # NotebookTabLabel est deja une instance creee une fois par onglet
        # (comme un Vte.Terminal), donc self joue ici le role que jouait le
        # widget terminal lui-meme en session-40
        # (widget._terminal_click_gesture) — pas besoin d'une methode
        # d'attache partagee, ce constructeur ne s'execute qu'une fois par
        # onglet.
        self._label_click_gesture = Gtk.GestureMultiPress.new(self.eb)
        # Bouton 0 = ecoute tous les boutons (defaut GTK : bouton 1 seul) —
        # necessaire ici car classify_tab_label_click() distingue elle-meme
        # clic droit (menu) et clic milieu (fermeture), comme le faisait
        # l'ancien gestionnaire unique sur button-press-event. Meme
        # raisonnement que self._tree_servers_press_gesture (session-39).
        self._label_click_gesture.set_button(0)
        # Phase TARGET (et non BUBBLE, la valeur par defaut) : meme point
        # d'entree que l'ancien "button-press-event" connecte directement
        # sur self.eb, avant le traitement par defaut du widget.
        self._label_click_gesture.set_propagation_phase(Gtk.PropagationPhase.TARGET)
        self._label_click_gesture.connect("pressed", self.on_label_pressed)
        label.halign = 0
        label.valign = 0.5
        label.set_text(title)
        self.eb.add(label)
        self.pack_start(self.eb, True, True, 0)
        label.show()
        self.eb.show()
        close_image = Gtk.Image.new_from_icon_name("window-close", Gtk.IconSize.MENU)
        _, image_w, image_h = Gtk.icon_size_lookup(Gtk.IconSize.MENU)
        self.widget_ = widget_
        self.popup = popup_
        close_btn = Gtk.Button()
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.connect("clicked", self.on_close_tab, owner_)
        close_btn.set_size_request(image_w + 7, image_h + 6)
        close_btn.add(close_image)
        self.eb2 = Gtk.EventBox()
        self.eb2.add(close_btn)
        self.pack_start(self.eb2, False, False, 0)
        self.eb2.show()
        close_btn.show_all()
        self.is_active = True
        self.eb.add_events(
            Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )  # let the scroll-event pass through
        self.eb2.add_events(
            Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )  # let the scroll-event pass through
        self.show()

    def set_selected(self, sel):
        """Marque ou demarque l'onglet comme selectionne visuellement.

        Args:
            sel (bool): True pour marquer comme selectionne.
        """
        if sel:
            self.get_style_context().add_class("selected")
        else:
            self.get_style_context().remove_class("selected")
        self.queue_draw()  # fix #67: forcer le redraw immédiat du label d'onglet

    def on_close_tab(self, widget, notebook, *args):
        """Gestionnaire du bouton fermeture d'onglet.

        Args:
            widget (Gtk.Button): Bouton clique.
            notebook (Gtk.Notebook): Notebook contenant l'onglet a fermer
                (transmis via ``close_btn.connect("clicked", ..., owner_)``).
            *args: Arguments additionnels ignores, tolerance de signature
                pour l'appel direct (hors signal ``clicked``).
        """
        from gnome_connection_manager import _, conf, msgconfirm

        if (
            conf.CONFIRM_ON_CLOSE_TAB
            and msgconfirm("%s [%s]?" % (_("Close console"), self.label.get_text().strip()))
            != Gtk.ResponseType.OK
        ):
            return True

        self.close_tab(widget)

    def close_tab(self, widget):
        """Ferme l'onglet associe au terminal.

        Args:
            widget: Parametre widget.
        """
        notebook = self.widget_.get_parent()
        page = notebook.page_num(self.widget_)
        if page >= 0:
            notebook.is_closed = True
            notebook.remove_page(page)
            notebook.is_closed = False
            self.widget_.destroy()

    def mark_tab_as_closed(self):
        """Marque visuellement l'onglet comme connexion fermee.

        La fermeture automatique effective vient de la surcharge par hôte
        (``self.host_auto_close_tab``, alimentée par ``Host.auto_close_tab``
        via les sites d'appel de ``NotebookTabLabel``) si elle est définie,
        sinon du réglage global ``conf.AUTO_CLOSE_TAB`` (voir
        :meth:`effective_auto_close_tab`). Envoie aussi une notification
        desktop (backlog features.md §4.2 #109, voir
        ``gnome_connection_manager.send_desktop_notification()``) et lance,
        s'il y en a une, la commande "après connexion" de l'hôte
        (``self.host_post_connect_command``, déjà résolue — backlog
        features.md §4.2 #116 volet "après", voir
        ``gnome_connection_manager.resolve_post_connect_command()``/
        ``_launch_hook_command()``) — seul point d'appel de cette méthode
        dans tout le projet, câblé sur le signal VTE ``child-exited`` (voir
        ``Wmain._on_terminal_child_exited()``), donc un point de repère
        fiable pour « une connexion terminal vient de se terminer », qu'elle
        se soit fermée proprement ou non. Couvre donc les protocoles VTE
        (SSH/telnet/local/serial/ipmi) uniquement — pas RDP/VNC/SPICE, même
        limitation que la notification de déconnexion.
        """
        from gnome_connection_manager import (
            _launch_hook_command,
            app_logger,
            send_desktop_notification,
        )

        app_logger.debug(
            f"NotebookTabLabel.mark_tab_as_closed() called | "
            f"title={self.label.get_text()!r} host_override={self.host_auto_close_tab!r} "
            f"post_connect_command={self.host_post_connect_command!r}"
        )
        host_name = self.label.get_text().strip() or "?"
        send_desktop_notification(_("Connection closed"), _("%s has disconnected.") % host_name)
        _launch_hook_command(
            self.host_post_connect_command,
            "NotebookTabLabel.mark_tab_as_closed (post_connect)",
        )
        self.label.set_markup(
            "<span color='darkgray' strikethrough='true'>%s</span>" % (self.label.get_text())
        )
        self.is_active = False
        mode = self.effective_auto_close_tab()
        if mode != 0:
            if mode == 2:
                parent_nb = self.widget_.get_parent()
                if parent_nb is None:
                    app_logger.debug(
                        "NotebookTabLabel.mark_tab_as_closed() returning | pas de notebook parent"
                    )
                    return
                terminal = parent_nb.get_nth_page(parent_nb.page_num(self.widget_)).get_child()
                if terminal.get_child_exit_status() != 0:
                    app_logger.debug(
                        "NotebookTabLabel.mark_tab_as_closed() returning | sortie non propre, mode=2"
                    )
                    return
            app_logger.debug(
                f"NotebookTabLabel.mark_tab_as_closed() returning | fermeture de l'onglet, mode={mode}"
            )
            self.close_tab(self.widget_)
        else:
            app_logger.debug(
                "NotebookTabLabel.mark_tab_as_closed() returning | mode=0 (jamais), onglet conservé"
            )

    def effective_auto_close_tab(self):
        """Résout le mode de fermeture automatique applicable à cet onglet.

        Délègue à la fonction pure ``gcm4_core.resolve_auto_close_tab()``
        (aucun accès GTK/VTE, testable sans stub) — même pattern que
        ``send_cluster_commands()`` dans ``gnome_connection_manager.py``
        pour ``resolve_cluster_command()``/``mask_cluster_command()``.

        Returns:
            int: 0 (jamais), 1 (toujours) ou 2 (seulement en sortie propre).
        """
        from gcm4_core import resolve_auto_close_tab
        from gnome_connection_manager import conf

        return resolve_auto_close_tab(self.host_auto_close_tab, conf.AUTO_CLOSE_TAB)

    def mark_tab_as_active(self):
        """Marque visuellement l'onglet comme connexion active."""
        self.label.set_markup("%s" % (self.label.get_text()))
        self.is_active = True

    def get_text(self):
        """Retourne le texte de l'etiquette de l'onglet.

        Returns:
            str: Texte de l'onglet.
        """
        return self.label.get_text()

    def _get_remote_desktop_widget(self):
        """Retourne le widget FreeRdpTab/VncTab/SpiceTab porte par cet onglet, si applicable.

        RDP est empaquete directement dans le notebook, tandis que VNC/SPICE
        sont empaquetes dans un Gtk.ScrolledWindow intermediaire (voir
        _open_rdp_tab/_open_vnc_tab/_open_spice_tab dans gnome_connection_manager.py).

        Returns:
            Gtk.Widget | None: le widget d'onglet bureau distant, ou None si
                cet onglet n'en est pas un (terminal SSH classique, etc.).
        """
        widget_ = self.widget_
        if isinstance(widget_, Gtk.ScrolledWindow):
            widget_ = widget_.get_child()
        if hasattr(widget_, "build_context_menu"):
            return widget_
        return None

    def on_label_pressed(self, gesture, n_press, x, y):
        """Gestionnaire de clic sur l'etiquette d'onglet (Gtk.GestureMultiPress).

        Remplace, depuis la session-41, l'ancien gestionnaire ``popupmenu``
        connecte au signal ``button-press-event`` sur ``Gdk.EventButton`` —
        voir ``docs/gtk4-migration.md`` section 3.6-decies pour le detail
        du portage et la correspondance exacte avec l'ancien comportement
        (:func:`gesture_trigger_core.classify_tab_label_click`). Troisieme
        et dernier des trois sites identifies par l'audit session-38
        (point 3), apres ``treeServers``/``popupMenuFolder`` (session-39)
        et ``on_terminal_click`` (session-40).

        Depuis la session-31 (portage GTK4-ready du *contenu*, voir
        ``docs/gtk4-migration.md`` section 3.6-ter), ``self.popup`` est un
        :class:`TabContextMenu` (``Gio.Menu``/``Gtk.Popover``) et non plus
        un ``Gtk.Menu`` : l'etat conditionnel (onglet actif, plusieurs
        volets, log en cours) est passe a ``rebuild()`` plutot que lu/ecrit
        sur des ``Gtk.MenuItem`` individuels.

        Note:
            Contrairement a ``Wmain.on_terminal_pressed`` (session-40), qui
            n'appelle jamais ``gesture.set_state(CLAIMED)`` par prudence
            vis-a-vis de la selection native de VTE, ce site revendique
            explicitement la sequence (``Gtk.EventSequenceState.CLAIMED``)
            partout ou l'ancien code retournait ``True`` — clic droit
            (ouverture d'un menu, distant ou ``TabContextMenu``) et clic
            milieu annule par confirmation — mais **pas** quand l'onglet
            est effectivement ferme (l'ancien code tombait alors en fin de
            fonction sans ``return``). Cette asymetrie de l'ancien code est
            reproduite fidelement ici plutot que lissee.

        Args:
            gesture (Gtk.GestureMultiPress): Geste a l'origine du signal
                ``pressed``, attache a ``self.eb`` avec ``set_button(0)``
                (tous boutons).
            n_press (int): Nombre de pressions consecutives (1 = clic
                simple, 2 = double clic, 3 = triple clic).
            x (float): Coordonnee X du clic, relative a ``self.eb``.
            y (float): Coordonnee Y du clic, relative a ``self.eb``.
        """
        from gnome_connection_manager import conf

        button = gesture.get_current_button()
        app_logger.debug(
            f"NotebookTabLabel.on_label_pressed() called | button={button!r} "
            f"n_press={n_press!r} x={x!r} y={y!r}"
        )
        outcome = classify_tab_label_click(button=button, n_press=n_press)
        if outcome == "propagate":
            app_logger.debug("NotebookTabLabel.on_label_pressed() returning | outcome='propagate'")
            return

        if outcome == "open-menu":
            remote_widget = self._get_remote_desktop_widget()
            if remote_widget is not None:
                remote_menu = remote_widget.build_context_menu()
                if remote_menu is not None:
                    # Menu Gtk.Menu legacy (bureau distant RDP/VNC/SPICE),
                    # pas encore porte vers Gio.Menu : sa signature
                    # .popup() a six arguments positionnels attend encore
                    # un numero de bouton et un horodatage. Le bouton vient
                    # de gesture.get_current_button() (deja calcule
                    # ci-dessus) ; pour l'horodatage,
                    # Gtk.get_current_event_time() (idiome deja utilise
                    # ailleurs dans ce depot, voir Wmain.on_terminal_pressed
                    # pour Gtk.show_uri()) evite de dependre de la
                    # disponibilite/forme exacte de
                    # gesture.get_last_event(None) alors qu'aucun des deux
                    # champs necessaires ici (bouton, temps) ne le requiert.
                    remote_menu.popup(None, None, None, None, button, Gtk.get_current_event_time())
                    gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                    app_logger.debug(
                        "NotebookTabLabel.on_label_pressed() returning | "
                        "outcome='open-menu' (bureau distant)"
                    )
                    return
            self.popup.label = self.label

            nb = self.widget_.get_parent()
            log_active = (
                hasattr(self.widget_.get_children()[0], "log_handler_id")
                and self.widget_.get_children()[0].log_handler_id != 0
            )
            self.popup.rebuild(
                is_active=self.is_active,
                multi_pane=nb.get_n_pages() > 1,
                log_active=log_active,
            )
            self.popup.popup_at(self.eb, x, y)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            app_logger.debug("NotebookTabLabel.on_label_pressed() returning | outcome='open-menu'")
            return

        # outcome == "close-tab"
        from gnome_connection_manager import _, msgconfirm

        if (
            conf.CONFIRM_ON_CLOSE_TAB_MIDDLE
            and msgconfirm("%s [%s]?" % (_("Close console"), self.label.get_text().strip()))
            != Gtk.ResponseType.OK
        ):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            app_logger.debug(
                "NotebookTabLabel.on_label_pressed() returning | outcome='close-tab' (annule)"
            )
            return
        self.close_tab(self.widget_)
        app_logger.debug("NotebookTabLabel.on_label_pressed() returning | outcome='close-tab'")


class EntryDialog(Gtk.Dialog):
    """Dialogue simple de saisie de texte."""

    def __init__(self, title, message, default_text="", modal=True, mask=False):
        """Initialise l'instance.

        Args:
            title: Parametre title.
            message: Parametre message.
            default_text: Parametre default_text.
            modal: Parametre modal.
            mask: Parametre mask.
        """
        Gtk.Dialog.__init__(self)
        self.set_title(title)
        self.connect("destroy", self.quit)
        self.connect("delete_event", self.quit)
        if modal:
            self.set_modal(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(10)
        self.vbox.pack_start(box, True, True, 0)
        box.show()
        if message:
            label = Gtk.Label(label=message)
            box.pack_start(label, True, True, 0)
            label.show()
        self.entry = Gtk.Entry()
        self.entry.set_text(default_text)
        self.entry.set_visibility(not mask)
        box.pack_start(self.entry, True, True, 0)
        self.entry.show()
        self.entry.grab_focus()
        button = Gtk.Button(label=_("OK"))
        button.connect("clicked", self.click)
        button.get_style_context().add_class("suggested-action")
        self.entry.connect("activate", self.click)
        button.set_can_default(True)
        self.action_area.pack_start(button, True, True, 0)
        button.show()
        button.grab_default()
        button = Gtk.Button(label=_("Cancel"))
        button.connect("clicked", self.quit)
        button.set_can_default(True)
        self.action_area.pack_start(button, True, True, 0)
        button.show()
        self.ret = None

    def quit(self, w=None, event=None):
        """Ferme la boite de dialogue.

        Args:
            w (Gtk.Widget, optional): Widget declencheur (bouton Cancel,
                ou ``None`` si appele directement).
            event (Gdk.Event, optional): Evenement associe si connecte a un
                signal en fournissant un (ex. ``delete-event``), ``None``
                sinon.
        """
        self.hide()
        self.destroy()

    def click(self, button):
        """Valide la saisie et ferme la boite de dialogue.

        Args:
            button (Gtk.Widget): Widget declencheur (bouton OK ou
                ``Gtk.Entry`` sur activation).
        """
        self.value = self.entry.get_text()
        self.response(Gtk.ResponseType.OK)


def inputbox(title, text, default="", password=False, parent=None, icon_path=None):
    """Affiche une boîte de saisie de texte.

    Utilitaire générique partagé par le cœur (``gnome_connection_manager.py``)
    et tout plugin ayant besoin de demander une saisie libre à l'utilisateur
    (ex. l'import oVirt, pour une route de secours manuelle). Vit ici plutôt
    que dans ``utils.py`` : elle dépend d'``EntryDialog`` défini dans ce
    module, et ``utils.py`` est importé PAR ``widgets.py`` — l'y déplacer
    créerait un cycle d'import.

    Args:
        title (str): Titre de la boite de dialogue.
        text (str): Label/description affiché au-dessus du champ de saisie.
        default (str, optional): Valeur par defaut. Defaults to ''.
        password (bool, optional): Masquer le texte saisi. Defaults to False.
        parent (Gtk.Window, optional): Fenêtre parente (transient-for
            implicite via ``EntryDialog`` non géré ici ; réservé pour un
            usage futur, sans effet actuellement).
        icon_path (str, optional): Chemin d'une icône à afficher sur la
            fenêtre de dialogue (ex. ``ICON_PATH`` côté cœur). Ignoré si
            absent — les appels depuis un plugin l'omettent simplement.

    Returns:
        str or None: Texte saisi, ou None si annulé.
    """
    dlg = EntryDialog(title, text, default, mask=password)
    if icon_path:
        try:
            dlg.set_icon_from_file(icon_path)
        except GLib.Error as exc:
            _loguru_logger.debug("widgets | icône {} non appliquée : {}", icon_path, exc)
    if run_dialog_sync(dlg) == Gtk.ResponseType.OK:
        response = dlg.value
    else:
        response = None
    dlg.destroy()
    return response


class CellTextView(Gtk.TextView, Gtk.CellEditable):
    """TextView pour edition en ligne dans une cellule."""

    __gtype_name__ = "CellTextView"

    __gproperties__ = {
        "editing-canceled": (
            bool,
            "Editing cancelled",
            "Editing was cancelled",
            False,
            GObject.ParamFlags.READWRITE,
        ),
    }

    def do_editing_done(self, *args):
        """Signale la fin de l'edition en ligne."""
        self.remove_widget()

    def do_remove_widget(self, *args):
        """Supprime le widget d'edition en ligne."""
        pass

    def do_start_editing(self, *args):
        """Demarre l'edition en ligne (implementation vide, interface ``Gtk.CellEditable``).

        Args:
            *args: Arguments transmis par GTK (typiquement l'``Gdk.Event``
                declencheur) ; non utilises, cette implementation ne fait
                rien (le widget est deja pret a l'edition des sa creation).
        """
        pass

    def get_text(self):
        """Retourne le texte de l'etiquette de l'onglet.

        Returns:
            str: Texte de l'onglet.
        """
        text_buffer = self.get_buffer()
        bounds = text_buffer.get_bounds()
        return text_buffer.get_text(*bounds, include_hidden_chars=True)

    def set_text(self, text):
        """Definit le texte de l'editeur.

        Args:
            text (str): Texte a afficher.
        """
        self.get_buffer().set_text(text)


class MultilineCellRenderer(Gtk.CellRendererText):
    """Renderer pour édition multiligne dans une cellule."""

    __gtype_name__ = "MultilineCellRenderer"

    def __init__(self):
        """Initialise l'instance."""
        Gtk.CellRendererText.__init__(self)
        self._in_editor_menu = False

    def _on_editor_focus_out_event(self, editor, *args):
        """Gestionnaire de perte de focus de l'editeur.

        Args:
            editor (CellTextView): Widget d'edition source.
            *args: Arguments additionnels transmis par le signal
                ``focus-out-event`` (l'``Gdk.EventFocus``), non utilises.
        """
        if self._in_editor_menu:
            return
        editor.remove_widget()
        self.emit("editing-canceled")

    def _on_editor_key_pressed(self, controller, keyval, keycode, state):
        """Gestionnaire de touche dans l'editeur inline (``Gtk.EventControllerKey``).

        Portage de l'ancien gestionnaire ``key-press-event``
        (``_on_editor_key_press_event``) vers le signal ``key-pressed`` de
        ``Gtk.EventControllerKey`` — voir ``docs/gtk4-migration.md``
        §3.6-undecies. Classification déléguée à
        ``inline_editor_core.classify_editor_key_press()`` (zéro import
        Gtk, testable sans stub). Comportement inchangé : l'ancien
        gestionnaire ne retournait jamais ``True`` (aucun ``return`` après
        validation/annulation), ce portage renvoie donc toujours ``False``
        pour laisser l'evenement se propager a l'identique.

        Args:
            controller (Gtk.EventControllerKey): Controleur source du signal.
            keyval (int): Code de la touche pressee (``Gdk.KEY_*``).
            keycode (int): Code materiel brut de la touche, non utilise ici.
            state (Gdk.ModifierType): Masque des modificateurs actifs.

        Returns:
            bool: Toujours ``False`` (evenement non arrete), voir ci-dessus.
        """
        app_logger.debug(
            f"MultilineCellRenderer._on_editor_key_pressed() called | keyval={keyval!r}"
        )
        editor = controller.get_widget()
        outcome = classify_editor_key_press(
            keyval=keyval,
            state=state,
            shift_mask=Gdk.ModifierType.SHIFT_MASK,
            control_mask=Gdk.ModifierType.CONTROL_MASK,
            return_keyval=Gdk.KEY_Return,
            kp_enter_keyval=Gdk.KEY_KP_Enter,
            escape_keyval=Gdk.KEY_Escape,
        )
        if outcome == "commit":
            editor.remove_widget()
            self.emit("edited", editor.path, editor.get_text())
        elif outcome == "cancel":
            editor.remove_widget()
            self.emit("editing-canceled")
        app_logger.debug(f"MultilineCellRenderer._on_editor_key_pressed() returning | {outcome=}")
        return False

    def _on_editor_populate_popup(self, editor, menu):
        """Gestionnaire de remplissage du menu contextuel de l'editeur.

        Args:
            editor (CellTextView): Widget d'edition source.
            menu (Gtk.Menu): Menu contextuel a peupler.
        """
        self._in_editor_menu = True

        def on_menu_unmap(menu, self):
            """Gestionnaire de fermeture du menu contextuel.

            Args:
                menu (Gtk.Menu): Menu ferme (source du signal ``unmap``).
                self (MultilineCellRenderer): Instance du renderer, passee
                    en ``user_data`` par ``menu.connect("unmap", ...,
                    self)`` — masque volontairement le ``self`` de la
                    methode englobante, non utilise ici.
            """
            self._in_editor_menu = False

        menu.connect("unmap", on_menu_unmap, self)

    def _on_editor_button_pressed(self, gesture, n_press, x, y):
        """Gestionnaire de clic souris dans l'editeur inline (``Gtk.GestureMultiPress``).

        Portage de l'ancien gestionnaire ``button-press-event``
        (``_on_editor_pressed``, qui retournait inconditionnellement
        ``True``) vers le signal ``pressed`` de ``Gtk.GestureMultiPress``
        (GTK3) / ``Gtk.GestureClick`` (GTK4) — voir
        ``docs/gtk4-migration.md`` §3.6-terdecies. Classification deleguee
        a ``inline_editor_core.classify_editor_button_press()`` (zero
        import Gtk, testable sans stub) : celle-ci renvoie toujours
        ``"claim"``, reproduisant a l'identique le comportement
        inconditionnel de l'ancien gestionnaire (contournement d'un bug
        GTK3 precis, voir sa docstring pour le detail et la reserve sur
        son utilite reelle en GTK4, non verifiable empiriquement ici).
        Revendiquer la sequence (``Gtk.EventSequenceState.CLAIMED``) est
        l'equivalent le plus proche disponible sur ``Gtk.GestureMultiPress``/
        ``Gtk.GestureClick`` du ``return True`` de l'ancien
        ``button-press-event``.

        Args:
            gesture (Gtk.GestureMultiPress): Geste a l'origine du signal
                ``pressed``, connecte sur l'editeur dans
                :meth:`do_start_editing`.
            n_press (int): Nombre de pressions consecutives, transmis a
                :func:`inline_editor_core.classify_editor_button_press`
                sans effet sur son resultat (voir sa docstring).
            x (float): Coordonnee x du clic dans l'editeur, non utilisee.
            y (float): Coordonnee y du clic dans l'editeur, non utilisee.
        """
        app_logger.debug(
            f"MultilineCellRenderer._on_editor_button_pressed() called | n_press={n_press!r}"
        )
        outcome = classify_editor_button_press(n_press=n_press)
        if outcome == "claim":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        app_logger.debug(
            f"MultilineCellRenderer._on_editor_button_pressed() returning | {outcome=}"
        )

    def do_start_editing(self, event, widget, path, bg_area, cell_area, flags):
        """Demarre l'edition en ligne : construit et retourne le ``CellTextView``.

        Implementation de l'interface ``Gtk.CellRenderer.do_start_editing``
        (GTK appelle cette methode au demarrage d'une edition de cellule,
        ex. double-clic sur un nom d'hote/dossier dans l'arborescence).

        Args:
            event (Gdk.Event): Evenement declencheur (double-clic ou touche
                d'activation).
            widget (Gtk.Widget): ``Gtk.TreeView`` proprietaire de la cellule.
            path (str): Chemin (``TreePath`` sous forme de chaine) de la
                ligne en cours d'edition.
            bg_area (Gdk.Rectangle): Zone d'arriere-plan de la cellule.
            cell_area (Gdk.Rectangle): Zone de la cellule elle-meme, utilisee
                pour dimensionner l'editeur (``editor.set_size_request()``).
            flags (Gtk.CellRendererState): Indicateurs d'etat de rendu
                (selectionne, survole...), non utilises directement ici.

        Returns:
            CellTextView: L'editeur en ligne pret a recevoir le focus,
            deja connecte a ses gestionnaires de fin d'edition/annulation.
        """
        from gnome_connection_manager import set_widget_font

        editor = CellTextView()
        set_widget_font(editor, self.props.font_desc)
        editor.set_text(self.props.text)
        editor.set_size_request(cell_area.width, cell_area.height)
        editor.set_border_width(min(self.props.xpad, self.props.ypad))
        editor.path = path
        editor.connect("focus-out-event", self._on_editor_focus_out_event)
        # key-press-event -> Gtk.EventControllerKey/key-pressed, porte cette
        # session (docs/gtk4-migration.md §3.6-undecies). Gtk.EventControllerKey
        # est la meme classe en GTK3 (>= 3.24) et en GTK4 ; seule sa
        # construction differe (GTK3 : .new(widget) attache directement ;
        # GTK4 : .new() puis widget.add_controller() separement - a ajuster
        # lors du futur passage reel a GTK4). Reference gardee sur `editor`
        # (meme precaution que pour les Gtk.Gesture* portes en session-39/40/41)
        # pour eviter que PyGObject ne libere le wrapper Python prematurement.
        editor._key_controller = Gtk.EventControllerKey.new(editor)
        editor._key_controller.connect("key-pressed", self._on_editor_key_pressed)
        editor.connect("populate-popup", self._on_editor_populate_popup)
        # button-press-event -> Gtk.GestureMultiPress/pressed, porte cette
        # session (docs/gtk4-migration.md §3.6-terdecies), meme principe que
        # key-press-event en session-43. set_button(0) : tous boutons, comme
        # pour treeServers/terminal/etiquette d'onglet (sessions 39-41),
        # puisque l'ancien button-press-event ne distinguait pas non plus le
        # bouton. Reference gardee sur `editor` pour eviter une liberation
        # prematuree du wrapper Python (meme precaution que pour
        # `_key_controller` ci-dessus et les Gtk.Gesture* des sessions 39-41).
        editor._button_gesture = Gtk.GestureMultiPress.new(editor)
        editor._button_gesture.set_button(0)
        editor._button_gesture.connect("pressed", self._on_editor_button_pressed)
        editor.show()
        return editor
