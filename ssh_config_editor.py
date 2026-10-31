# SPDX-License-Identifier: GPL-3.0-or-later
"""Dialogue GTK3 d'édition de ~/.ssh/config intégré à GCM.

Fournit un éditeur visuel (liste d'hôtes + formulaire de champs)
et un onglet Raw (texte brut éditable) avec diff inline.
Utilise ssh_config_parser.py, adapté de SSH-Studio (BuddySirJava, GPLv3).

Corrections v2 :
- Ajout de Gdk dans les imports gi.repository
- Clipboard corrigé : Gdk.Atom.intern("CLIPBOARD", False) au lieu de gdk_atom_intern
- Handler _on_raw_changed réintégré (perdu lors du fix ruff I001)
"""

from __future__ import annotations

import difflib
import os
import subprocess
from gettext import gettext as _
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402
from loguru import logger

from key_picker_dialog import KeyPickerDialog

# Fusion session-26 : ssh_config_parser.py + ssh_migrate_gcm.py -> plugins/ssh/core.py
# (voir docs/gtk4-migration.md §3.0-bis/§3.6). Ce fichier reste à la racine
# (GTK3, pas encore migré vers plugins/ssh/gtk4.py — session séparée).
from plugins.ssh.core import (  # noqa: E402
    SHARED_SSH_CONFIG_PATH,
    SSHConfig,
    SSHConfigParser,
    SSHHost,
    SSHOption,
)
from ssh_key_manager_dialog import SSHKeyManagerDialog
from utils import GCMBase, run_dialog_sync

# ---------------------------------------------------------------------------
# Cibles éditables — point 5/6 : éditeur commun personnel + partagé
# ---------------------------------------------------------------------------
_TARGET_PERSONAL = "personal"
_TARGET_SHARED = "shared"
_TARGET_LABELS: dict[str, str] = {
    _TARGET_PERSONAL: _("Personal (~/.ssh/config)"),
    _TARGET_SHARED: _("Shared — bastion, all users (/etc/ssh/ssh_config.d/config_gcm.conf)"),
}

# ---------------------------------------------------------------------------
# Champs affichés dans le formulaire visuel
# ---------------------------------------------------------------------------
_BASIC_FIELDS: list[tuple[str, str]] = [
    ("Host", _("Alias(es)")),
    ("HostName", _("HostName")),
    ("User", _("User")),
    ("Port", _("Port")),
    ("IdentityFile", _("IdentityFile")),
    ("ProxyJump", _("ProxyJump")),
    ("ProxyCommand", _("ProxyCommand")),
    ("ForwardAgent", _("ForwardAgent")),
    ("ServerAliveInterval", _("ServerAliveInterval")),
    ("ServerAliveCountMax", _("ServerAliveCountMax")),
    ("Compression", _("Compression")),
    ("StrictHostKeyChecking", _("StrictHostKeyChecking")),
    ("UserKnownHostsFile", _("UserKnownHostsFile")),
    ("ConnectTimeout", _("ConnectTimeout")),
    ("RequestTTY", _("RequestTTY")),
    ("RemoteCommand", _("RemoteCommand")),
    ("ControlMaster", _("ControlMaster")),
    ("ControlPersist", _("ControlPersist")),
    ("PubkeyAuthentication", _("PubkeyAuthentication")),
    ("PasswordAuthentication", _("PasswordAuthentication")),
    ("IdentitiesOnly", _("IdentitiesOnly")),
    ("LogLevel", _("LogLevel")),
    ("AddKeysToAgent", _("AddKeysToAgent")),
]

# ---------------------------------------------------------------------------
# Exemples de valeurs affichés en grisé (placeholder) dans chaque champ
# ---------------------------------------------------------------------------
_FIELD_PLACEHOLDERS: dict[str, str] = {
    "Host": _("ex : web1 ou web1 web1.example.com"),
    "HostName": _("ex : 192.168.1.10 ou example.com"),
    "User": _("ex : root"),
    "Port": _("ex : 22"),
    "IdentityFile": _("ex : ~/.ssh/id_ed25519, ~/.ssh/id_rsa"),
    "ProxyJump": _("ex : user@bastion.example.com, user@server1.example.com"),
    "ProxyCommand": _("ex : ssh -W %h:%p bastion, ou corkscrew proxy 8080 %h %p"),
    "ForwardAgent": _("yes ou no"),
    "ServerAliveInterval": _("ex : 60"),
    "ServerAliveCountMax": _("ex : 3"),
    "Compression": _("yes ou no"),
    "StrictHostKeyChecking": _("yes / no / ask / accept-new"),
    "UserKnownHostsFile": _("ex : ~/.ssh/known_hosts"),
    "ConnectTimeout": _("ex : 10"),
    "RequestTTY": _("yes / no / force / auto"),
    "RemoteCommand": _("ex : tmux attach || tmux new"),
    "ControlMaster": _("auto / yes / no"),
    "ControlPersist": _("ex : 10m"),
    "PubkeyAuthentication": _("yes ou no"),
    "PasswordAuthentication": _("yes ou no"),
    "IdentitiesOnly": _("yes ou no"),
    "LogLevel": _("QUIET/ERROR/INFO/VERBOSE/DEBUG"),
    "AddKeysToAgent": _("yes / no / ask / confirm"),
}

# ---------------------------------------------------------------------------
# Directives pouvant apparaître plusieurs fois dans un même bloc Host.
# Le formulaire affiche/accepte leurs valeurs séparées par des virgules dans
# un seul champ, mais chacune est réécrite sur sa propre ligne dans le
# fichier ~/.ssh/config (voir SSHHost.get_options()/set_options()).
# ---------------------------------------------------------------------------
_MULTI_VALUE_FIELDS: frozenset[str] = frozenset({"IdentityFile"})


# ---------------------------------------------------------------------------
# Dialogue principal
# ---------------------------------------------------------------------------


class SshConfigEditorDialog(GCMBase, Gtk.Dialog):
    """Éditeur visuel de ~/.ssh/config avec onglet Raw/diff.

    Onglets :
    - **Visual** : liste d'hôtes à gauche, formulaire à droite
    - **Raw** : texte brut du fichier avec diff des modifications

    Écran 100% Python (pas de Glade) — hérite directement de ``Gtk.Dialog``,
    en plus de ``GCMBase`` (mode ``path=None``, cf. ``GCMBase.__init__``) qui
    lui fournit ``is_tab``/``tab_key``/``request_close()``/
    ``on_tab_will_close()``, nécessaires pour être ouvert comme onglet
    épinglé via ``Wmain.open_management_tab`` (propriété du plugin SSH, cf.
    ``SshPlugin.edit_ssh_config``).

    Deux modes d'usage coexistent tant que le mode legacy (sans plugin)
    n'a pas disparu :

    Mode fenêtre autonome (legacy, ``is_tab`` reste ``False``) ::

        dlg = SshConfigEditorDialog(parent=self.window)
        if run_dialog_sync(dlg) == Gtk.ResponseType.OK:
            dlg.save()
        dlg.destroy()

    Mode onglet épinglé (``Wmain.open_management_tab`` positionne
    ``is_tab``/``tab_key`` après construction, cf. ``SshPlugin.edit_ssh_config``) ::

        wMain.open_management_tab(
            "ssh-config",
            _("Edit SSH config"),
            lambda: SshConfigEditorDialog(parent=wMain.window, show=False),
        )
    """

    def __init__(self, parent: Gtk.Window, show: bool = True) -> None:
        """Initialise le dialogue et charge ~/.ssh/config.

        Args:
            parent: Fenêtre parente GCM (pour centrage modal en mode fenêtre
                autonome ; sans effet en mode onglet, cf. ``_top_window()``).
            show (bool): Sans effet direct ici (ce dialogue n'a jamais été
                affiché automatiquement par son propre ``__init__`` — c'est
                l'appelant, via ``run_dialog_sync`` ou ``embed_dialog_content``,
                qui décide). Conservé pour cohérence d'API avec les autres
                écrans de gestion (``Whost``, ``Wcluster``, ``Wconfig``).
        """
        logger.debug("SshConfigEditorDialog.__init__: entrée | show={}", show)
        Gtk.Dialog.__init__(
            self,
            title=_("Edit SSH config (personal + shared)"),
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        # Mode 100% Python (pas de Glade) : initialise juste is_tab/tab_key,
        # ne touche pas a la construction GTK deja faite ci-dessus (cf.
        # GCMBase.__init__, branche path=None).
        GCMBase.__init__(self, path=None, parent=parent, show=show)
        # L'instance EST deja le Gtk.Dialog (heritage direct) : main_widget
        # pointe donc vers self, exactement comme pour les ecrans Glade ou
        # main_widget designe la racine Gtk.Dialog chargee par le builder.
        self.main_widget = self
        # GNOME Shell/Mutter "attache" les Gtk.Dialog modaux transient_for un
        # parent (attach-modal-dialogs) : la fenêtre reste liée à la position
        # de la fenêtre principale (décalage fixe, impossible à déplacer plus
        # haut que son point d'apparition). Passer le type hint à NORMAL lui
        # redonne une décoration indépendante et un placement libre, tout en
        # restant modal/transient (bloque toujours la fenêtre principale).
        self.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        self.set_default_size(940, 620)

        self._parsers: dict[str, SSHConfigParser] = {
            _TARGET_PERSONAL: SSHConfigParser(),
            _TARGET_SHARED: SSHConfigParser(config_path=SHARED_SSH_CONFIG_PATH),
        }
        for parser in self._parsers.values():
            parser.parse()
        self._configs: dict[str, SSHConfig] = {k: p.config for k, p in self._parsers.items()}

        # Cible actuellement affichée dans l'onglet Raw/Diff (point 6). Les
        # attributs `_parser`/`_config` restent des ALIAS vers
        # self._parsers[self._raw_target] / self._configs[self._raw_target] :
        # tout le code Raw/Diff historique (_sync_raw_from_model,
        # _sync_model_from_raw, _refresh_diff, save(), _run_validation) peut
        # rester quasi inchangé, il opère juste sur la cible sélectionnée.
        self._raw_target: str = _TARGET_PERSONAL
        self._parser: SSHConfigParser = self._parsers[self._raw_target]
        self._config: SSHConfig = self._configs[self._raw_target]

        # Association host (par identité d'objet) -> fichier d'origine
        # ("personal"/"shared"), reconstruite à chaque _populate_list().
        self._host_source: dict[int, str] = {}
        self._current_host: SSHHost | None = None
        self._current_host_source: str | None = None
        self._suspend_file_combo_signal: bool = False
        self._dirty = False
        # Doit rester False tant que l'UI n'est pas entièrement construite et le
        # modèle synchronisé une première fois : Gtk.Notebook peut émettre un
        # signal "switch-page" initial (page 0) pendant _build_ui()/show_all(),
        # AVANT que self._raw_buffer ne soit rempli. Sans ce garde-fou,
        # _on_tab_switch déclenche _sync_model_from_raw() sur un buffer Raw
        # encore vide, ce qui écrase les hôtes fraîchement parsés par une liste
        # vide (bug constaté : n_hosts=12 au parsing mais 0 hôte affiché).
        self._initialized = False

        logger.debug(
            "SshConfigEditorDialog.__init__ | personal={} (n={}) shared={} (n={})",
            self._parsers[_TARGET_PERSONAL].config_path,
            len(self._configs[_TARGET_PERSONAL].hosts),
            self._parsers[_TARGET_SHARED].config_path,
            len(self._configs[_TARGET_SHARED].hosts),
        )

        self._build_ui()
        self._populate_list()
        self._sync_raw_from_model()
        self._update_save_button()
        self._initialized = True

        # Le fichier n'existe pas encore (première utilisation) : la liste et
        # l'onglet Raw sont donc vides, ce qui peut sembler être un bug de
        # chargement — un message explicite évite la confusion.
        if not self._parsers[_TARGET_PERSONAL].config_path.exists():
            GLib.idle_add(
                _show_toast,
                self,
                _("{path} does not exist yet — it will be created when saving.").format(
                    path=self._parsers[_TARGET_PERSONAL].config_path
                ),
            )
        logger.debug("SshConfigEditorDialog.__init__: fin")

    # ------------------------------------------------------------------
    # Construction de l'UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Construit l'ensemble de l'interface (notebook + panneaux)."""
        logger.debug("_build_ui: entrée")
        content = self.get_content_area()
        content.set_spacing(0)

        self._error_bar = Gtk.InfoBar()
        self._error_bar.set_message_type(Gtk.MessageType.ERROR)
        self._error_bar.set_show_close_button(True)
        self._error_label = Gtk.Label(label="")
        self._error_label.set_line_wrap(True)
        self._error_bar.get_content_area().pack_start(self._error_label, True, True, 0)
        self._error_bar.connect("response", lambda bar, _rid: bar.hide())
        self._error_bar.hide()
        content.pack_start(self._error_bar, False, False, 0)

        self._notebook = Gtk.Notebook()
        self._notebook.set_margin_start(8)
        self._notebook.set_margin_end(8)
        self._notebook.set_margin_top(8)
        self._notebook.set_margin_bottom(4)
        content.pack_start(self._notebook, True, True, 0)

        self._notebook.append_page(self._build_visual_tab(), Gtk.Label(label=_("Visual")))
        self._notebook.append_page(self._build_raw_tab(), Gtk.Label(label=_("Raw / Diff")))
        self._notebook.connect("switch-page", self._on_tab_switch)

        content.show_all()
        self._error_bar.hide()
        logger.debug("_build_ui: fin")

    def _make_action_buttons(self) -> Gtk.Box:
        """Crée une nouvelle paire de boutons Save/Cancel.

        Insérée directement dans le contenu (onglets Visual et Raw) plutôt
        que dans la zone d'actions standard du Gtk.Dialog, qui pouvait ne pas
        s'afficher correctement selon le thème/gestionnaire de fenêtres (bug
        rapporté : boutons OK/Annuler invisibles). Chaque appel crée de
        nouvelles instances de widgets (un widget GTK ne peut avoir qu'un seul
        parent).

        Returns:
            Box horizontal contenant les deux boutons.
        """
        logger.debug("_make_action_buttons: entrée")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        btn_cancel = Gtk.Button(label=_("✕ Cancel"))
        btn_cancel.connect("clicked", self._on_dialog_cancel_clicked)
        btn_save = Gtk.Button(label=_("💾 Save"))
        btn_save.get_style_context().add_class("suggested-action")
        btn_save.connect("clicked", self._on_dialog_save_clicked)
        box.pack_start(btn_cancel, False, False, 0)
        box.pack_start(btn_save, False, False, 0)
        logger.debug("_make_action_buttons: fin")
        return box

    def _on_dialog_save_clicked(self, _widget: Gtk.Widget) -> None:
        """Gestionnaire du bouton Save.

        Mode fenêtre autonome (legacy) : déclenche la réponse OK, encore
        récupérée par l'appelant via ``run_dialog_sync`` (qui appelle alors
        ``dlg.save()``). Mode onglet épinglé : plus d'appelant synchrone à
        prévenir — la sauvegarde doit donc se faire ici, avant la fermeture.
        """
        logger.debug("_on_dialog_save_clicked: entrée")
        if self.is_tab:
            self.save()
            self.request_close()
        else:
            self.response(Gtk.ResponseType.OK)
        logger.debug("_on_dialog_save_clicked: fin")

    def _on_dialog_cancel_clicked(self, _widget: Gtk.Widget) -> None:
        """Gestionnaire du bouton Annuler (cf. ``_on_dialog_save_clicked``)."""
        logger.debug("_on_dialog_cancel_clicked: entrée")
        if self.is_tab:
            self.request_close()
        else:
            self.response(Gtk.ResponseType.CANCEL)
        logger.debug("_on_dialog_cancel_clicked: fin")

    def _top_window(self) -> Gtk.Window:
        """Retourne la fenêtre de haut niveau à utiliser comme parent pour
        les dialogues enfants (KeyPickerDialog, SSHKeyManagerDialog,
        Gtk.MessageDialog de confirmation, _SshTestDialog).

        Piège identique à celui rencontré sur ``Whost`` (cf.
        ``Whost._top_window``) : en mode onglet épinglé, ``self`` (ce
        dialogue) n'est jamais affiché/réalisé — l'utiliser comme
        ``transient_for``/parent donnerait un rattachement à une fenêtre
        invisible. On utilise alors la fenêtre principale, dont l'onglet
        fait désormais partie. En mode fenêtre autonome, ``self`` est bien
        affiché (par ``run_dialog_sync``) et reste la fenêtre parente
        appropriée.

        Returns:
            Gtk.Window: Fenêtre parente appropriée.
        """
        logger.debug("_top_window: entrée | is_tab={}", self.is_tab)
        if self.is_tab:
            from gnome_connection_manager import wMain  # noqa: PLC0415

            logger.debug("_top_window: retour fenêtre principale (mode onglet)")
            return wMain.window
        logger.debug("_top_window: retour self (mode fenêtre autonome)")
        return self

    def _build_visual_tab(self) -> Gtk.Widget:
        """Construit le panneau Visual (liste + formulaire).

        Returns:
            Widget racine de l'onglet.
        """
        logger.debug("_build_visual_tab: entrée")
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(230)

        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        left_box.set_margin_start(4)
        left_box.set_margin_end(4)
        left_box.set_margin_top(4)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text(_("Filter hosts…"))
        self._search_entry.connect("search-changed", self._on_search_changed)
        left_box.pack_start(self._search_entry, False, False, 0)

        sw_left = Gtk.ScrolledWindow()
        sw_left.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw_left.set_shadow_type(Gtk.ShadowType.IN)
        sw_left.set_min_content_width(210)

        # Colonnes : [0] alias affiché (avec badge fichier), [1] tooltip,
        # [2] alias brut (pour lookup), [3] source ("personal"/"shared").
        # Vue fusionnée des deux fichiers (point 5) : chaque hôte, quel que
        # soit son fichier d'origine, apparaît dans la même liste.
        self._host_store = Gtk.ListStore(str, str, str, str)
        self._host_filter = self._host_store.filter_new()
        self._host_filter.set_visible_func(self._host_filter_func)

        self._tree = Gtk.TreeView(model=self._host_filter)
        self._tree.set_headers_visible(False)
        self._tree.set_tooltip_column(1)
        col = Gtk.TreeViewColumn("", Gtk.CellRendererText(), text=0)
        self._tree.append_column(col)
        self._tree.get_selection().connect("changed", self._on_host_selected)
        sw_left.add(self._tree)
        left_box.pack_start(sw_left, True, True, 0)

        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        btn_add = Gtk.Button(label=_("＋ Add"))
        btn_add.set_tooltip_text(_("Add a new empty host"))
        btn_add.connect("clicked", self._on_add_host)
        self._btn_remove = Gtk.Button(label=_("✕ Remove"))
        self._btn_remove.set_tooltip_text(_("Remove selected host"))
        self._btn_remove.set_sensitive(False)
        self._btn_remove.connect("clicked", self._on_remove_host)
        self._btn_test = Gtk.Button(label=_("⚡ Test"))
        self._btn_test.set_tooltip_text(_("Test SSH connection to selected host"))
        self._btn_test.set_sensitive(False)
        self._btn_test.connect("clicked", self._on_test_connection)
        btn_bar.pack_start(btn_add, True, True, 0)
        btn_bar.pack_start(self._btn_remove, True, True, 0)
        btn_bar.pack_start(self._btn_test, True, True, 0)
        btn_bar.pack_end(self._make_action_buttons(), False, False, 0)
        left_box.pack_start(btn_bar, False, False, 0)

        paned.pack1(left_box, False, False)

        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right_box.set_margin_start(8)
        right_box.set_margin_end(4)
        right_box.set_margin_top(4)

        # Sélecteur "Fichier" par hôte (point 5) : place/déplace l'hôte
        # actuellement chargé dans le formulaire entre ~/.ssh/config
        # (personnel) et /etc/ssh/ssh_config.d/config_gcm.conf (partagé).
        file_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        file_row.pack_start(Gtk.Label(label=_("File:")), False, False, 0)
        self._host_file_combo = Gtk.ComboBoxText()
        for key in (_TARGET_PERSONAL, _TARGET_SHARED):
            self._host_file_combo.append(key, _TARGET_LABELS[key])
        self._host_file_combo.connect("changed", self._on_host_file_changed)
        file_row.pack_start(self._host_file_combo, False, False, 0)
        right_box.pack_start(file_row, False, False, 4)

        self._form_grid = Gtk.Grid()
        self._form_grid.set_column_spacing(10)
        self._form_grid.set_row_spacing(6)
        self._form_grid.set_margin_top(8)

        self._entries: dict[str, Gtk.Entry] = {}
        self._error_labels: dict[str, Gtk.Label] = {}

        for row, (key, label_text) in enumerate(_BASIC_FIELDS):
            lbl = Gtk.Label(label=label_text + ":", xalign=1.0)
            lbl.set_size_request(140, -1)
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_placeholder_text(_FIELD_PLACEHOLDERS.get(key, key))
            if key in _MULTI_VALUE_FIELDS:
                entry.set_tooltip_text(
                    _(
                        "Multiple values possible: separate them with commas (one line per value in the file)"
                    )
                )
            entry.connect("changed", self._on_field_changed, key)
            err_lbl = Gtk.Label(label="")
            err_lbl.set_xalign(0.0)
            err_lbl.get_style_context().add_class("error")
            err_lbl.hide()
            self._entries[key] = entry
            self._error_labels[key] = err_lbl

            if key == "IdentityFile":
                # Boîte horizontale : entry + bouton sélecteur de clé + bouton gestionnaire
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                hbox.set_hexpand(True)
                hbox.pack_start(entry, True, True, 0)
                btn_pick = Gtk.Button(label=_("🔑 Pick…"))
                btn_pick.set_tooltip_text(_("Browse existing SSH keys in ~/.ssh/"))
                btn_pick.connect("clicked", self._on_pick_identity_file)
                btn_mgr = Gtk.Button(label=_("⚙"))
                btn_mgr.set_tooltip_text(_("Open SSH Key Manager (generate / import / delete)"))
                btn_mgr.connect("clicked", self._on_open_key_manager)
                hbox.pack_start(btn_pick, False, False, 0)
                hbox.pack_start(btn_mgr, False, False, 0)
                self._form_grid.attach(lbl, 0, row * 2, 1, 1)
                self._form_grid.attach(hbox, 1, row * 2, 1, 1)
            else:
                self._form_grid.attach(lbl, 0, row * 2, 1, 1)
                self._form_grid.attach(entry, 1, row * 2, 1, 1)
            self._form_grid.attach(err_lbl, 1, row * 2 + 1, 1, 1)

        copy_btn = Gtk.Button(label=_("Copy ssh cmd"))
        copy_btn.set_tooltip_text(_("Copy full ssh command to clipboard"))
        copy_btn.connect("clicked", self._on_copy_ssh_cmd)
        copy_btn.set_hexpand(False)
        self._form_grid.attach(copy_btn, 1, len(_BASIC_FIELDS) * 2, 1, 1)

        self._placeholder_label = Gtk.Label(label=_("← Select or create a host"))
        self._placeholder_label.set_sensitive(False)
        self._placeholder_label.set_valign(Gtk.Align.CENTER)
        self._placeholder_label.set_halign(Gtk.Align.CENTER)

        form_scroller = Gtk.ScrolledWindow()
        form_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        form_scroller.add(self._form_grid)

        # Sous-onglets du formulaire : "Redirection de port" (copié/adapté de
        # l'onglet "Port forwarding" du dialogue Edit host de GCM, remplace les
        # anciens champs texte LocalForward/RemoteForward/DynamicForward par le
        # sélecteur Dynamic/Local/Remote) placé avant "Propriétés", qui contient
        # HostName et le reste du formulaire général.
        sub_notebook = Gtk.Notebook()
        sub_notebook.append_page(
            self._build_port_forwarding_tab(), Gtk.Label(label=_("Redirection de port"))
        )
        sub_notebook.append_page(form_scroller, Gtk.Label(label=_("Properties")))

        self._form_stack = Gtk.Stack()
        self._form_stack.add_named(self._placeholder_label, "placeholder")
        self._form_stack.add_named(sub_notebook, "form")
        self._form_stack.set_visible_child_name("placeholder")

        right_box.pack_start(self._form_stack, True, True, 0)
        paned.pack2(right_box, True, False)
        logger.debug("_build_visual_tab: fin")
        return paned

    def _build_port_forwarding_tab(self) -> Gtk.Widget:
        """Construit l'onglet "Redirection de port" (Dynamic/Local/Remote).

        Repris et adapté du dialogue "Edit host" de GCM (sélecteur de type +
        Local Port/Remote Host/Remote Port + liste), mais mappé sur les
        directives ``LocalForward``/``RemoteForward``/``DynamicForward`` de
        ~/.ssh/config au lieu du format interne "host.tunnel" de GCM.

        Returns:
            Widget racine de l'onglet.
        """
        logger.debug("_build_port_forwarding_tab: entrée")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        fields_grid = Gtk.Grid()
        fields_grid.set_column_spacing(10)
        fields_grid.set_row_spacing(6)

        self._pf_combo = Gtk.ComboBoxText()
        self._pf_combo.append("dynamic", _("Dynamic (SOCKS)"))
        self._pf_combo.append("local", _("Local (-L)"))
        self._pf_combo.append("remote", _("Remote (-R)"))
        self._pf_combo.set_active_id("local")
        self._pf_combo.connect("changed", self._on_pf_type_changed)
        fields_grid.attach(self._pf_combo, 0, 0, 1, 1)

        self._pf_lbl_local = Gtk.Label(label=_("Local Port"), xalign=0.0)
        fields_grid.attach(self._pf_lbl_local, 0, 1, 1, 1)
        self._pf_lbl_host = Gtk.Label(label=_("Remote Host"), xalign=0.0)
        fields_grid.attach(self._pf_lbl_host, 0, 2, 1, 1)
        self._pf_lbl_remote = Gtk.Label(label=_("Remote Port"), xalign=0.0)
        fields_grid.attach(self._pf_lbl_remote, 0, 3, 1, 1)

        self._pf_txt_local = Gtk.SpinButton()
        self._pf_txt_local.set_adjustment(
            Gtk.Adjustment(value=8080, lower=1, upper=65535, step_increment=1, page_increment=10)
        )
        self._pf_txt_local.set_hexpand(True)
        fields_grid.attach(self._pf_txt_local, 1, 1, 1, 1)

        self._pf_txt_host = Gtk.Entry()
        self._pf_txt_host.set_text("localhost")
        self._pf_txt_host.set_hexpand(True)
        fields_grid.attach(self._pf_txt_host, 1, 2, 1, 1)

        self._pf_txt_remote = Gtk.SpinButton()
        self._pf_txt_remote.set_adjustment(
            Gtk.Adjustment(value=8080, lower=1, upper=65535, step_increment=1, page_increment=10)
        )
        self._pf_txt_remote.set_hexpand(True)
        fields_grid.attach(self._pf_txt_remote, 1, 3, 1, 1)

        box.pack_start(fields_grid, False, False, 0)

        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        btn_add = Gtk.Button(label=_("＋ Add"))
        btn_add.connect("clicked", self._on_pf_add)
        btn_remove = Gtk.Button(label=_("✕ Remove"))
        btn_remove.connect("clicked", self._on_pf_remove)
        btn_bar.pack_start(btn_add, False, False, 0)
        btn_bar.pack_start(btn_remove, False, False, 0)
        box.pack_start(btn_bar, False, False, 0)

        self._pf_error_label = Gtk.Label(label="")
        self._pf_error_label.set_xalign(0.0)
        self._pf_error_label.get_style_context().add_class("error")
        self._pf_error_label.hide()
        box.pack_start(self._pf_error_label, False, False, 0)

        # Colonnes : Local, Host, Remote, valeur brute ssh_config (cachée), Type
        self._pf_store = Gtk.ListStore(str, str, str, str, str)
        self._pf_tree = Gtk.TreeView(model=self._pf_store)
        for col_index, title in (
            (0, _("Local")),
            (1, _("Host")),
            (2, _("Remote")),
            (4, _("Type")),
        ):
            self._pf_tree.append_column(
                Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=col_index)
            )

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_shadow_type(Gtk.ShadowType.IN)
        sw.set_vexpand(True)
        sw.add(self._pf_tree)
        box.pack_start(sw, True, True, 0)

        self._on_pf_type_changed(self._pf_combo)
        logger.debug("_build_port_forwarding_tab: fin")
        return box

    def _on_pf_type_changed(self, widget: Gtk.ComboBoxText) -> None:
        """Adapte la sensibilité et les libellés selon le type choisi.

        Args:
            widget: Sélecteur Dynamic/Local/Remote.
        """
        logger.debug("_on_pf_type_changed: entrée")
        tunnel_type = widget.get_active_id() or "local"
        is_dynamic = tunnel_type == "dynamic"
        self._pf_txt_host.set_sensitive(not is_dynamic)
        self._pf_txt_remote.set_sensitive(not is_dynamic)
        if tunnel_type == "remote":
            self._pf_lbl_local.set_text(_("Remote listen port"))
            self._pf_lbl_host.set_text(_("Local target host"))
            self._pf_lbl_remote.set_text(_("Local target port"))
        else:
            self._pf_lbl_local.set_text(_("Local Port"))
            self._pf_lbl_host.set_text(_("Remote Host"))
            self._pf_lbl_remote.set_text(_("Remote Port"))
        logger.debug("_on_pf_type_changed: fin | type={}", tunnel_type)

    def _on_pf_add(self, _widget: Gtk.Widget) -> None:
        """Ajoute une redirection de port à la liste, selon le type sélectionné."""
        logger.debug("_on_pf_add: entrée")
        tunnel_type = self._pf_combo.get_active_id() or "local"
        local = str(self._pf_txt_local.get_value_as_int())
        host = self._pf_txt_host.get_text().strip()
        remote = str(self._pf_txt_remote.get_value_as_int())

        if tunnel_type == "dynamic":
            host = ""
            remote = ""
            raw = local
        else:
            if not host:
                self._pf_error_label.set_text(_("Enter remote host"))
                self._pf_error_label.show()
                logger.debug("_on_pf_add: hôte distant manquant -> refus")
                return
            raw = f"{local} {host}:{remote}"

        self._pf_error_label.hide()
        type_label = {"dynamic": _("Dynamic"), "remote": _("Remote")}.get(tunnel_type, _("Local"))
        self._pf_store.append([local, host, remote, raw, type_label])
        self._dirty = True
        logger.debug("_on_pf_add: redirection ajoutée | type={} raw={}", tunnel_type, raw)

    def _on_pf_remove(self, _widget: Gtk.Widget) -> None:
        """Supprime la redirection de port sélectionnée dans la liste."""
        logger.debug("_on_pf_remove: entrée")
        model, it = self._pf_tree.get_selection().get_selected()
        if it is not None:
            model.remove(it)
            self._dirty = True
        logger.debug("_on_pf_remove: fin")

    def _build_raw_tab(self) -> Gtk.Widget:
        """Construit le panneau Raw/Diff.

        Returns:
            Widget racine de l'onglet.
        """
        logger.debug("_build_raw_tab: entrée")
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_margin_start(8)
        vbox.set_margin_end(8)
        vbox.set_margin_top(8)
        vbox.set_margin_bottom(4)

        info = Gtk.Label(
            label=_("Edit raw ssh_config — changes here override the Visual tab on Save.")
        )
        info.set_xalign(0.0)
        info.set_line_wrap(True)
        vbox.pack_start(info, False, False, 0)

        # Sélecteur de fichier (point 6) : bascule tout l'onglet Raw/Diff
        # (texte + diff) entre le fichier personnel et le fichier partagé.
        # Le formulaire/la liste de l'onglet Visual restent, eux, une vue
        # fusionnée des deux fichiers (voir _populate_list).
        target_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        target_row.pack_start(Gtk.Label(label=_("File:")), False, False, 0)
        self._raw_target_combo = Gtk.ComboBoxText()
        for key in (_TARGET_PERSONAL, _TARGET_SHARED):
            self._raw_target_combo.append(key, _TARGET_LABELS[key])
        self._raw_target_combo.set_active_id(self._raw_target)
        self._raw_target_combo.connect("changed", self._on_raw_target_changed)
        target_row.pack_start(self._raw_target_combo, False, False, 0)
        vbox.pack_start(target_row, False, False, 0)

        paned_raw = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned_raw.set_position(340)

        sw_raw = Gtk.ScrolledWindow()
        sw_raw.set_shadow_type(Gtk.ShadowType.IN)
        self._raw_buffer = Gtk.TextBuffer()
        self._raw_view = Gtk.TextView(buffer=self._raw_buffer)
        self._raw_view.set_monospace(True)
        self._raw_view.set_left_margin(6)
        self._raw_view.set_top_margin(4)
        sw_raw.add(self._raw_view)
        paned_raw.pack1(sw_raw, True, False)

        sw_diff = Gtk.ScrolledWindow()
        sw_diff.set_shadow_type(Gtk.ShadowType.IN)
        sw_diff.set_min_content_height(120)
        self._diff_buffer = Gtk.TextBuffer()
        self._diff_buffer.create_tag("added", foreground="#2d9e44")
        self._diff_buffer.create_tag("removed", foreground="#e01b24")
        self._diff_buffer.create_tag("header", foreground="#888888", weight=Pango.Weight.BOLD)
        self._diff_view = Gtk.TextView(buffer=self._diff_buffer)
        self._diff_view.set_monospace(True)
        self._diff_view.set_editable(False)
        self._diff_view.set_left_margin(6)
        self._diff_view.set_top_margin(4)
        sw_diff.add(self._diff_view)

        diff_label = Gtk.Label(label=_("Diff (original → current):"), xalign=0.0)
        diff_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        diff_box.pack_start(diff_label, False, False, 0)
        diff_box.pack_start(sw_diff, True, True, 0)

        paned_raw.pack2(diff_box, False, False)
        vbox.pack_start(paned_raw, True, True, 0)

        btn_diff = Gtk.Button(label=_("⟳ Refresh diff"))
        btn_diff.connect("clicked", lambda _w: self._refresh_diff())
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        btn_row.pack_start(btn_diff, False, False, 0)
        btn_row.pack_end(self._make_action_buttons(), False, False, 0)
        vbox.pack_start(btn_row, False, False, 0)

        # IMPORTANT : connecter le signal APRÈS avoir créé self._raw_buffer
        self._raw_buffer.connect("changed", self._on_raw_changed)
        logger.debug("_build_raw_tab: fin")
        return vbox

    def _populate_list(self) -> None:
        """Remplit le ListStore depuis les deux fichiers (personnel + partagé).

        Reconstruit également ``self._host_source`` (identité d'objet hôte →
        fichier d'origine), utilisé par le formulaire (point 5) pour savoir
        où déplacer/enregistrer un hôte.
        """
        logger.debug("_populate_list: entrée")
        self._host_store.clear()
        self._host_source = {}
        _BADGE = {_TARGET_PERSONAL: "", _TARGET_SHARED: "🌐 "}
        for target in (_TARGET_PERSONAL, _TARGET_SHARED):
            for host in self._configs[target].hosts:
                self._host_source[id(host)] = target
                alias = " ".join(host.patterns)
                hostname = host.get_option("HostName") or ""
                display = f"{_BADGE[target]}{alias}"
                tooltip = f"{alias}\n{hostname}" if hostname else alias
                tooltip = f"{tooltip}\n[{_TARGET_LABELS[target]}]"
                self._host_store.append([display, tooltip, alias, target])
        logger.debug(
            "_populate_list | personal={} shared={} -> {} ligne(s)",
            len(self._configs[_TARGET_PERSONAL].hosts),
            len(self._configs[_TARGET_SHARED].hosts),
            len(self._host_store),
        )

    def _host_filter_func(self, model: Gtk.TreeModel, it: Gtk.TreeIter, _data: object) -> bool:
        """Filtre les hôtes selon le texte de recherche.

        Args:
            model: Modèle de données.
            it: Itérateur sur la ligne candidate.
            _data: Données utilisateur (non utilisé).

        Returns:
            True si la ligne doit être visible.
        """
        logger.debug("_host_filter_func: entrée")
        text = self._search_entry.get_text().strip().lower()
        if not text:
            logger.debug("_host_filter_func: filtre vide -> tout visible")
            return True
        visible = text in model[it][0].lower() or text in model[it][1].lower()
        logger.debug("_host_filter_func: retour | visible={}", visible)
        return visible

    def _load_host_in_form(self, host: SSHHost, source: str) -> None:
        """Remplit le formulaire avec les valeurs d'un hôte.

        Args:
            host: Bloc SSH à afficher.
            source: Fichier d'origine de l'hôte (:data:`_TARGET_PERSONAL` ou
                :data:`_TARGET_SHARED`), pour le sélecteur "Fichier" (point 5).
        """
        logger.debug("_load_host_in_form: entrée | source={}", source)
        self._current_host = host
        self._current_host_source = source
        self._suspend_file_combo_signal = True
        self._host_file_combo.set_active_id(source)
        self._suspend_file_combo_signal = False
        self._form_stack.set_visible_child_name("form")
        self._entries["Host"].set_text(" ".join(host.patterns))
        for key in list(_BASIC_FIELDS)[1:]:
            field_key = key[0]
            if field_key in _MULTI_VALUE_FIELDS:
                val = ", ".join(host.get_options(field_key))
            else:
                val = host.get_option(field_key) or ""
            self._entries[field_key].set_text(val)
            self._error_labels[field_key].hide()
        self._load_port_forwarding_in_form(host)
        self._btn_remove.set_sensitive(True)
        self._btn_test.set_sensitive(bool(host.get_option("HostName") or host.patterns))
        logger.debug("_load_host_in_form: fin")

    def _load_port_forwarding_in_form(self, host: SSHHost) -> None:
        """Peuple la liste "Redirection de port" depuis LocalForward/RemoteForward/DynamicForward.

        Args:
            host: Bloc SSH à afficher.
        """
        logger.debug("_load_port_forwarding_in_form: entrée")
        self._pf_store.clear()
        for raw in host.get_options("DynamicForward"):
            local = raw.split(None, 1)[0] if raw.split() else raw
            self._pf_store.append([local, "", "", raw, _("Dynamic")])
        for raw, type_label in (
            *((v, _("Local")) for v in host.get_options("LocalForward")),
            *((v, _("Remote")) for v in host.get_options("RemoteForward")),
        ):
            parts = raw.split(None, 1)
            local = parts[0] if parts else raw
            host_port = parts[1] if len(parts) > 1 else ""
            if ":" in host_port:
                target_host, target_port = host_port.rsplit(":", 1)
            else:
                target_host, target_port = host_port, ""
            self._pf_store.append([local, target_host, target_port, raw, type_label])
        logger.debug("_load_port_forwarding_in_form: fin")

    def _save_form_to_host(self) -> bool:
        """Applique les valeurs du formulaire dans self._current_host.

        Returns:
            True si tous les champs sont valides, False sinon.
        """
        logger.debug("_save_form_to_host: entrée")
        if self._current_host is None:
            logger.debug("_save_form_to_host: aucun hôte courant -> rien à faire")
            return True

        valid = True

        raw_alias = self._entries["Host"].get_text().strip()
        if not raw_alias:
            self._show_field_error("Host", _("At least one alias is required"))
            valid = False
        else:
            self._current_host.patterns = raw_alias.split()
            self._error_labels["Host"].hide()

        port_str = self._entries["Port"].get_text().strip()
        if port_str:
            try:
                p = int(port_str)
                if p < 1 or p > 65535:
                    raise ValueError
                self._error_labels["Port"].hide()
            except ValueError:
                logger.debug("_save_form_to_host: port invalide | valeur={!r}", port_str)
                self._show_field_error("Port", _("Port must be an integer between 1 and 65535"))
                valid = False

        for key, _lbl in _BASIC_FIELDS:
            if key in ("Host", "Port"):
                continue
            val = self._entries[key].get_text().strip()
            if key in _MULTI_VALUE_FIELDS:
                values = [v.strip() for v in val.split(",") if v.strip()]
                self._current_host.set_options(key, values)
            elif val:
                self._current_host.set_option(key, val)
            else:
                self._current_host.remove_option(key)

        self._save_port_forwarding_to_host()

        logger.debug("_save_form_to_host: retour | valid={}", valid)
        return valid

    def _save_port_forwarding_to_host(self) -> None:
        """Réécrit LocalForward/RemoteForward/DynamicForward depuis la liste "Redirection de port"."""
        logger.debug("_save_port_forwarding_to_host: entrée")
        if self._current_host is None:
            logger.debug("_save_port_forwarding_to_host: aucun hôte courant -> retour")
            return
        dynamic_values: list[str] = []
        local_values: list[str] = []
        remote_values: list[str] = []
        for row in self._pf_store:
            raw, type_label = row[3], row[4]
            if type_label == _("Dynamic"):
                dynamic_values.append(raw)
            elif type_label == _("Remote"):
                remote_values.append(raw)
            else:
                local_values.append(raw)
        self._current_host.set_options("DynamicForward", dynamic_values)
        self._current_host.set_options("LocalForward", local_values)
        self._current_host.set_options("RemoteForward", remote_values)
        logger.debug(
            "_save_port_forwarding_to_host: fin | dyn={} local={} remote={}",
            len(dynamic_values),
            len(local_values),
            len(remote_values),
        )

    def _show_field_error(self, key: str, msg: str) -> None:
        """Affiche un message d'erreur sous un champ.

        Args:
            key: Clé du champ concerné.
            msg: Texte de l'erreur.
        """
        logger.debug("_show_field_error: entrée")
        lbl = self._error_labels.get(key)
        if lbl:
            lbl.set_text(msg)
            lbl.show()
        logger.debug("_show_field_error: fin")

    def _on_raw_target_changed(self, combo: Gtk.ComboBoxText) -> None:
        """Change le fichier affiché dans l'onglet Raw/Diff (point 6).

        Args:
            combo: Sélecteur de fichier Raw/Diff.
        """
        logger.debug("_on_raw_target_changed: entrée")
        target = combo.get_active_id()
        if target is None or target == self._raw_target:
            logger.debug("_on_raw_target_changed: cible inchangée -> ignoré")
            return
        self._switch_raw_target(target)
        logger.debug("_on_raw_target_changed: fin | cible={}", target)

    def _switch_raw_target(self, target: str) -> None:
        """Repointe l'onglet Raw/Diff (``self._parser``/``self._config``) vers
        *target* ("personal" ou "shared") et rafraîchit texte + diff.

        Args:
            target: Clé de la cible (:data:`_TARGET_PERSONAL` ou
                :data:`_TARGET_SHARED`).
        """
        logger.debug("_switch_raw_target: entrée | target={}", target)
        self._raw_target = target
        self._parser = self._parsers[target]
        self._config = self._configs[target]
        self._sync_raw_from_model()
        logger.debug(
            "_switch_raw_target: cible basculée | target={} path={}",
            target,
            self._parser.config_path,
        )

    def _sync_raw_from_model(self) -> None:
        """Copie le contenu généré par le modèle dans la zone Raw."""
        logger.debug("_sync_raw_from_model: entrée")
        content = self._config.generate_content()
        logger.debug("_sync_raw_from_model: contenu généré | {} caractères", len(content))
        self._raw_buffer.handler_block_by_func(self._on_raw_changed)
        self._raw_buffer.set_text(content)
        self._raw_buffer.handler_unblock_by_func(self._on_raw_changed)
        self._refresh_diff()
        logger.debug("_sync_raw_from_model: fin")

    def _sync_model_from_raw(self) -> bool:
        """Parse le contenu de la zone Raw et recharge le modèle.

        Le fichier temporaire de parsing est toujours écrit dans un
        répertoire utilisateur inscriptible (``tempfile.gettempdir()``),
        jamais dans ``self._config.file_path.parent`` : pour la cible
        partagée ce répertoire (``/etc/ssh/ssh_config.d/``) est root-owned et
        l'écriture y échouerait (PermissionError) — point 4.a.

        Returns:
            True si le parsing a réussi.
        """
        logger.debug("_sync_model_from_raw: entrée")
        import tempfile as _tempfile  # noqa: PLC0415

        start, end = self._raw_buffer.get_bounds()
        text = self._raw_buffer.get_text(start, end, False)
        tmp_path = Path(_tempfile.gettempdir()) / f".gcm_raw_tmp_{self._raw_target}_{os.getpid()}"
        try:
            tmp_path.write_text(text, encoding="utf-8")
            tmp_parser = SSHConfigParser(config_path=tmp_path)
            tmp_parser.parse()
            self._config.hosts = tmp_parser.config.hosts
            self._config.global_options = tmp_parser.config.global_options
            self._config.include_directives = tmp_parser.config.include_directives
            logger.debug("_sync_model_from_raw: parsing réussi")
            return True
        except Exception as exc:
            logger.warning(
                "_sync_model_from_raw: échec parsing Raw | {} | {}", type(exc).__name__, exc
            )
            return False
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug(
                    "_sync_model_from_raw: nettoyage tmp impossible | {} | {}",
                    type(exc).__name__,
                    exc,
                )

    def _refresh_diff(self) -> None:
        """Met à jour la zone diff entre le fichier original et la version courante."""
        logger.debug("_refresh_diff: entrée")
        original = "\n".join(self._config.original_lines) + "\n"
        start, end = self._raw_buffer.get_bounds()
        current = self._raw_buffer.get_text(start, end, False)

        diff = list(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                current.splitlines(keepends=True),
                fromfile="original",
                tofile="current",
                n=2,
            )
        )

        self._diff_buffer.set_text("")
        it = self._diff_buffer.get_end_iter()
        if not diff:
            self._diff_buffer.insert_with_tags_by_name(it, _("No changes."), "header")
            logger.debug("_refresh_diff: aucun changement")
            return

        for line in diff:
            if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
                self._diff_buffer.insert_with_tags_by_name(it, line, "header")
            elif line.startswith("+"):
                self._diff_buffer.insert_with_tags_by_name(it, line, "added")
            elif line.startswith("-"):
                self._diff_buffer.insert_with_tags_by_name(it, line, "removed")
            else:
                self._diff_buffer.insert(it, line)
        logger.debug("_refresh_diff: fin | {} ligne(s) de diff", len(diff))

    def save(self) -> None:
        """Sauvegarde la configuration sur disque (personnel ET partagé).

        Lit depuis la zone Raw si l'onglet Raw est actif (pour le fichier
        actuellement sélectionné dans le sélecteur Raw/Diff, point 6), sinon
        applique d'abord le formulaire à l'hôte courant (onglet Visual) avant
        d'utiliser le modèle en mémoire. Les DEUX fichiers sont ensuite
        écrits (point 5) : ``SSHConfigParser.write()`` est un no-op si le
        contenu généré est identique au contenu sur disque, donc écrire les
        deux systématiquement est sûr même si un seul a changé.
        """
        logger.debug("save: entrée | onglet courant={}", self._notebook.get_current_page())
        if self._notebook.get_current_page() == 1:
            if not self._sync_model_from_raw():
                logger.error("save | raw parse failed, aborting write")
                return
        elif self._current_host is not None:
            # Sans ceci, les modifications du formulaire non encore
            # synchronisées (aucun changement d'hôte/onglet déclenché depuis
            # la dernière frappe) étaient silencieusement perdues à
            # l'enregistrement — bug rapporté : "Save ne sauvegarde rien".
            if not self._save_form_to_host():
                self._run_validation()
                logger.error("save | form validation failed, aborting write")
                return
            self._populate_list()

        errors: list[str] = []
        for target, parser in self._parsers.items():
            try:
                parser.write()
                logger.info(
                    "save: écrit | cible={} path={}", _TARGET_LABELS[target], parser.config_path
                )
            except OSError as exc:
                msg = f"{_TARGET_LABELS[target]} : {exc}"
                logger.error(
                    "save: échec d'écriture | cible={} exc={}",
                    _TARGET_LABELS[target],
                    exc,
                )
                errors.append(msg)

        if errors:
            self._error_label.set_text("\n".join(errors))
            self._error_bar.show()
        logger.debug("save: fin | erreurs={}", len(errors))

    def _run_validation(self) -> list[str]:
        """Lance la validation des DEUX fichiers et affiche les erreurs dans l'InfoBar.

        Returns:
            Liste des messages d'erreur (vide si OK), préfixés par le fichier
            concerné quand les deux ont des erreurs.
        """
        logger.debug("_run_validation: entrée")
        errors: list[str] = []
        for target, parser in self._parsers.items():
            for err in parser.validate():
                errors.append(f"[{_TARGET_LABELS[target]}] {err}")
        if errors:
            self._error_label.set_text("\n".join(errors))
            self._error_bar.show()
        else:
            self._error_bar.hide()
        logger.debug("_run_validation: retour | {} erreur(s)", len(errors))
        return errors

    def _update_save_button(self) -> None:
        """Active ou désactive le bouton Save selon la validité."""
        logger.debug("_update_save_button: entrée")
        self._run_validation()
        logger.debug("_update_save_button: fin")

    def _on_pick_identity_file(self, _widget: Gtk.Widget) -> None:
        """Ouvre le dialogue de sélection de clé SSH.

        Args:
            _widget: Widget qui a déclenché l'événement.
        """
        logger.debug("_on_pick_identity_file: entrée")
        dlg = KeyPickerDialog(parent=self._top_window())
        dlg.connect("key-selected", self._on_key_selected)
        run_dialog_sync(dlg)
        dlg.destroy()
        logger.debug("_on_pick_identity_file: fin")

    def _on_key_selected(self, _dlg, key_path: str) -> None:
        """Callback du signal key-selected de KeyPickerDialog.

        Args:
            _dlg: Dialog source (non utilisé).
            key_path: Chemin absolu de la clé privée sélectionnée.
        """
        logger.debug("_on_key_selected: entrée")
        self._entries["IdentityFile"].set_text(key_path)
        self._dirty = True
        logger.debug("_on_key_selected: fin")

    def _on_open_key_manager(self, _widget: Gtk.Widget) -> None:
        """Ouvre le dialogue de gestion des clés SSH.

        Args:
            _widget: Widget qui a déclenché l'événement.
        """
        # SSHKeyManagerDialog est une Gtk.Window NON modale. Si on l'ouvre
        # pendant que CE dialogue (self) est encore modal, le grab GTK modal
        # reste actif et intercepte tous les événements destinés à la fenêtre
        # Key Manager : elle s'affiche mais aucun bouton/onglet ne répond
        # (bug rapporté — fonctionne normalement quand ouverte depuis le menu
        # principal, dont la fenêtre parente n'est pas modale). On désactive
        # temporairement la modalité, restaurée à la fermeture du gestionnaire.
        # En mode onglet épinglé (self.is_tab), self n'est jamais affiché ni
        # modal (cf. _top_window()) : rien à basculer, comme pour
        # Whost.on_btnKeyManager_clicked.
        logger.debug("_on_open_key_manager: entrée")
        top_window = self._top_window()
        if not self.is_tab:
            self.set_modal(False)
        dlg = SSHKeyManagerDialog(parent=top_window)
        if not self.is_tab:
            dlg.connect("destroy", lambda _w: self.set_modal(True))
        dlg.show_all()
        logger.debug("_on_open_key_manager: fin")

    def _on_host_selected(self, selection: Gtk.TreeSelection) -> None:
        """Charge l'hôte sélectionné dans le formulaire.

        Args:
            selection: Sélection courante du TreeView.
        """
        logger.debug("_on_host_selected: entrée")
        if self._current_host is not None:
            self._save_form_to_host()
            self._dirty = True
            self._update_save_button()

        model, it = selection.get_selected()
        if it is None:
            self._current_host = None
            self._current_host_source = None
            self._form_stack.set_visible_child_name("placeholder")
            self._btn_remove.set_sensitive(False)
            self._btn_test.set_sensitive(False)
            logger.debug("_on_host_selected: aucune sélection -> formulaire vide")
            return

        raw_alias, source = model[it][2], model[it][3]
        host = self._configs[source].get_host(raw_alias)
        if host:
            self._load_host_in_form(host, source)
        logger.debug("_on_host_selected: fin")

    def _on_field_changed(self, _entry: Gtk.Entry, _key: str) -> None:
        """Marque le formulaire comme modifié.

        Args:
            entry: Champ modifié.
            key: Clé SSH correspondante.
        """
        logger.debug("_on_field_changed: entrée")
        self._dirty = True
        logger.debug("_on_field_changed: fin")

    def _on_raw_changed(self, _buf: Gtk.TextBuffer) -> None:
        """Marque l'onglet Raw comme modifié.

        Args:
            _buf: Buffer texte (non utilisé directement).
        """
        logger.debug("_on_raw_changed: entrée")
        self._dirty = True
        logger.debug("_on_raw_changed: fin")

    def _on_add_host(self, _widget: Gtk.Widget) -> None:
        """Crée un nouvel hôte vide (fichier personnel par défaut) et le sélectionne.

        Le nouvel hôte est toujours créé côté personnel : l'utilisateur peut
        ensuite le basculer vers le fichier partagé via le sélecteur
        "Fichier" du formulaire (point 5) une fois ses champs renseignés.

        Args:
            _widget: Widget qui a déclenché l'événement.
        """
        logger.debug("_on_add_host: entrée")
        new_host = SSHHost(patterns=["new-host"])
        new_host.options.append(SSHOption(key="HostName", value=""))
        new_host.options.append(SSHOption(key="User", value=""))
        self._configs[_TARGET_PERSONAL].add_host(new_host)
        self._populate_list()
        last = len(self._host_store) - 1
        if last >= 0:
            self._tree.set_cursor(Gtk.TreePath(last), None, False)
        self._dirty = True
        logger.debug("_on_add_host: fin")

    def _on_remove_host(self, _widget: Gtk.Widget) -> None:
        """Supprime l'hôte actuellement sélectionné après confirmation.

        Args:
            _widget: Widget qui a déclenché l'événement.
        """
        logger.debug("_on_remove_host: entrée")
        if self._current_host is None or self._current_host_source is None:
            logger.debug("_on_remove_host: aucun hôte courant -> ignoré")
            return
        alias = " ".join(self._current_host.patterns)
        dlg = Gtk.MessageDialog(
            transient_for=self._top_window(),
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=_("Remove host?"),
        )
        target_path = self._parsers[self._current_host_source].config_path
        dlg.format_secondary_text(
            _("Remove «{alias}» from {path}?").format(alias=alias, path=target_path)
        )
        if run_dialog_sync(dlg) == Gtk.ResponseType.YES:
            self._configs[self._current_host_source].remove_host(self._current_host)
            self._current_host = None
            self._current_host_source = None
            self._form_stack.set_visible_child_name("placeholder")
            self._populate_list()
            self._dirty = True
        dlg.destroy()
        logger.debug("_on_remove_host: fin")

    def _on_host_file_changed(self, combo: Gtk.ComboBoxText) -> None:
        """Déplace l'hôte courant entre fichier personnel et partagé (point 5).

        Args:
            combo: Sélecteur "Fichier" du formulaire.
        """
        logger.debug("_on_host_file_changed: entrée")
        if self._suspend_file_combo_signal or self._current_host is None:
            logger.debug("_on_host_file_changed: signal suspendu ou sans hôte -> ignoré")
            return
        new_target = combo.get_active_id()
        if new_target is None or new_target == self._current_host_source:
            logger.debug("_on_host_file_changed: cible identique -> ignoré")
            return

        old_target = self._current_host_source
        self._configs[old_target].remove_host(self._current_host)
        self._configs[new_target].add_host(self._current_host)
        self._current_host_source = new_target
        self._dirty = True
        self._populate_list()

        # Ré-sélectionne le même hôte (maintenant dans l'autre fichier) dans
        # l'arbre, pour ne pas perdre le focus utilisateur.
        alias = " ".join(self._current_host.patterns)
        for row in self._host_store:
            if row[2] == alias and row[3] == new_target:
                self._tree.set_cursor(row.path, None, False)
                break

        logger.info(
            "_on_host_file_changed: hôte déplacé | alias={} {} -> {}",
            alias,
            _TARGET_LABELS[old_target],
            _TARGET_LABELS[new_target],
        )

    def _on_test_connection(self, _widget: Gtk.Widget) -> None:
        """Lance un test de connexion SSH vers l'hôte sélectionné.

        Args:
            _widget: Widget qui a déclenché l'événement.
        """
        logger.debug("_on_test_connection: entrée")
        if self._current_host is None:
            logger.debug("_on_test_connection: aucun hôte courant -> ignoré")
            return
        alias = self._current_host.patterns[0] if self._current_host.patterns else ""
        if not alias:
            logger.debug("_on_test_connection: alias vide -> ignoré")
            return
        dlg = _SshTestDialog(parent=self._top_window(), alias=alias)
        run_dialog_sync(dlg)
        dlg.destroy()
        logger.debug("_on_test_connection: fin | alias={}", alias)

    def _on_copy_ssh_cmd(self, _widget: Gtk.Widget) -> None:
        """Copie la commande SSH complète dans le presse-papiers.

        Args:
            _widget: Widget qui a déclenché l'événement.
        """
        logger.debug("_on_copy_ssh_cmd: entrée")
        if self._current_host is None:
            logger.debug("_on_copy_ssh_cmd: aucun hôte courant -> ignoré")
            return
        alias = self._current_host.patterns[0] if self._current_host.patterns else ""
        user = self._current_host.get_option("User") or ""
        port = self._current_host.get_option("Port") or "22"
        hostname = self._current_host.get_option("HostName") or alias

        cmd = f"ssh -p {port} {user}@{hostname}" if user else f"ssh -p {port} {hostname}"

        # API Clipboard correcte pour GTK3 : Gdk.Atom.intern
        clip = Gtk.Clipboard.get(Gdk.Atom.intern("CLIPBOARD", False))
        clip.set_text(cmd, -1)
        _show_toast(self, _("Copied: {cmd}").format(cmd=cmd))
        logger.debug("_on_copy_ssh_cmd: commande copiée | alias={}", alias)

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        """Rafraîchit le filtre de la liste.

        Args:
            _entry: Champ de recherche (non utilisé directement).
        """
        logger.debug("_on_search_changed: entrée")
        self._host_filter.refilter()
        logger.debug("_on_search_changed: fin")

    def _on_tab_switch(self, _nb: Gtk.Notebook, _page: Gtk.Widget, page_num: int) -> None:
        """Synchronise modèle ↔ Raw lors du changement d'onglet.

        Args:
            _nb: Notebook (non utilisé).
            _page: Page destination (non utilisé).
            page_num: Index de la page destination.
        """
        logger.debug("_on_tab_switch: entrée | page={}", page_num)
        if not self._initialized:
            # Ignore le(s) signal(aux) "switch-page" émis par GTK pendant la
            # construction initiale du Notebook (avant que self._raw_buffer ne
            # soit synchronisé) — voir commentaire dans __init__.
            logger.debug("_on_tab_switch: pas encore initialisé -> ignoré")
            return
        if page_num == 1:
            if self._current_host is not None:
                self._save_form_to_host()
            self._sync_raw_from_model()
        else:
            if self._sync_model_from_raw():
                self._populate_list()
                self._refresh_diff()
        logger.debug("_on_tab_switch: fin")


class _SshTestDialog(Gtk.Dialog):
    """Dialogue de test de connexion SSH (ssh -o ConnectTimeout=5 -v alias).

    Args:
        parent: Fenêtre parente.
        alias: Alias SSH à tester.
    """

    def __init__(self, parent: Gtk.Window, alias: str) -> None:
        """Initialise le dialogue de test SSH.

        Args:
            parent: Fenêtre parente.
            alias: Alias SSH du bloc Host à tester.
        """
        logger.debug("_SshTestDialog.__init__: entrée | alias={}", alias)
        super().__init__(
            title=_("Test connection — {alias}").format(alias=alias),
            transient_for=parent,
            modal=True,
        )
        self.set_default_size(560, 300)
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        self._alias = alias
        self._build_ui()
        self.show_all()
        GLib.idle_add(self._run_test)
        logger.debug("_SshTestDialog.__init__: fin")

    def _build_ui(self) -> None:
        """Construit la zone d'affichage de sortie SSH."""
        logger.debug("_SshTestDialog._build_ui: entrée")
        sw = Gtk.ScrolledWindow()
        sw.set_shadow_type(Gtk.ShadowType.IN)
        sw.set_margin_start(8)
        sw.set_margin_end(8)
        sw.set_margin_top(8)
        sw.set_margin_bottom(4)
        self._buf = Gtk.TextBuffer()
        tv = Gtk.TextView(buffer=self._buf)
        tv.set_monospace(True)
        tv.set_editable(False)
        tv.set_left_margin(4)
        sw.add(tv)
        self.get_content_area().pack_start(sw, True, True, 0)
        self.get_content_area().show_all()
        logger.debug("_SshTestDialog._build_ui: fin")

    def _run_test(self) -> bool:
        """Lance ssh -o ConnectTimeout=5 -v alias et affiche la sortie.

        Returns:
            False (fin du GLib.idle_add).
        """
        logger.debug("_run_test: entrée | alias={}", self._alias)
        cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-v", self._alias]
        self._append(_("Running: {cmd}\n\n").format(cmd=" ".join(cmd)))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            output = result.stdout + result.stderr
            self._append(output or _("(no output)\n"))
            self._append(_("\nExit code: {rc}").format(rc=result.returncode))
        except subprocess.TimeoutExpired:
            logger.debug("_run_test: timeout après 10 s | alias={}", self._alias)
            self._append(_("\n[Timeout after 10 s]"))
        except Exception as exc:
            logger.debug("_run_test: erreur | {} | {}", type(exc).__name__, exc)
            self._append(_("\n[Error: {exc}]").format(exc=exc))
        logger.debug("_run_test: fin | code={}", result.returncode)
        return False

    def _append(self, text: str) -> None:
        """Ajoute du texte à la fin du buffer d'affichage.

        Args:
            text: Texte à ajouter.
        """
        logger.debug("_append: entrée | {} caractère(s)", len(text))
        self._buf.insert(self._buf.get_end_iter(), text)
        logger.debug("_append: fin")


def _show_toast(parent: Gtk.Window, msg: str, duration_ms: int = 2500) -> None:
    """Affiche une notification temporaire dans une InfoBar flottante.

    Args:
        parent: Fenêtre parente (pour positionnement).
        msg: Message à afficher.
        duration_ms: Durée d'affichage en millisecondes.
    """
    logger.debug("_show_toast: entrée | durée_ms={}", duration_ms)
    bar = Gtk.InfoBar()
    bar.set_message_type(Gtk.MessageType.INFO)
    bar.get_content_area().pack_start(Gtk.Label(label=msg), True, True, 0)
    bar.show_all()
    content = parent.get_content_area() if isinstance(parent, Gtk.Dialog) else None
    if content:
        content.pack_start(bar, False, False, 0)
        GLib.timeout_add(duration_ms, lambda: bar.destroy() or False)
    logger.debug("_show_toast: fin")
