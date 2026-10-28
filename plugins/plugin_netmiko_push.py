# SPDX-License-Identifier: GPL-3.0-or-later
"""Onglet « Netmiko » (déploiement de configuration en masse) pour GCM.

Ce plugin n'est volontairement PAS un ``ConnectionPlugin`` (cf.
``plugin_base.py``) : ce contrat modélise une session interactive *par
hôte* (un onglet = une connexion). Ici, il s'agit d'une action *par lot*
sur un inventaire entier — GCM n'ouvre aucune console, il pilote netmiko en
arrière-plan et journalise sur disque. C'est donc un unique onglet
singleton nommé « Netmiko », contenant lui-même 3 phases :

    Configuration → Suivi → Résultats

présentées comme les pages d'un ``Gtk.Stack`` (bascule visuelle, aucune
fenêtre modale). Cela déroge sciemment à la règle « un onglet = une
connexion » du reste de GCM — décision assumée pour cet outil.

Intégration dans Wmain : AUCUNE, et c'est le but. Ce module expose
``get_batch_plugin()`` (cf. tout en bas du fichier), le point d'entrée que
``BatchPluginRegistry.autoload()`` (voir ``plugin_base.py``) découvre tout
seul au démarrage — exactement comme les ``plugin_xxx.py`` de protocole
(SSH, VNC...) sont découverts via ``get_plugin()`` par ``PluginRegistry``.
``gnome_connection_manager.py`` n'a donc besoin que du câblage générique
(déjà en place) qui construit une entrée de menu « Outils » pour chaque
``BatchPlugin`` enregistré — rien de spécifique à Netmiko à y ajouter.

Si ce fichier ne se charge pas (dépendance manquante, erreur de syntaxe...),
``BatchPluginRegistry.autoload()`` journalise la cause précise en warning
et Wmain affiche un avertissement au démarrage — pas de plantage silencieux.

``open_netmiko_tab`` utilise ``wmain.nbConsole`` — le ``Gtk.Notebook`` où
``addTab()`` ouvre normalement les sessions (cf. ``self.get_widget("nbConsole")``
dans ``gnome_connection_manager.py``) — et est un singleton : un second appel
ramène simplement le focus sur l'onglet déjà ouvert plutôt que d'en créer un
second.

Aucune modification du fichier ``.glade`` n'est nécessaire : comme les
dialogues d'import libvirt/Proxmox, l'UI est construite entièrement en
code — mais ici comme widget de notebook, pas comme ``Gtk.Dialog``.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import gi
from loguru import logger

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402
from plugin_base import BatchPlugin  # noqa: E402

from netmiko_bulk_core import (  # noqa: E402
    STATUS_FAILED,
    STATUS_OK,
    STATUS_REJECTED,
    STATUS_SKIPPED,
    VENDOR_DEVICE_TYPES,
    DeviceProfile,
    InventoryRow,
    ProfileStore,
    PushResult,
    find_missing_variables,
    load_inventory,
    render_template,
    run_bulk_push,
    summarize_results,
)

# Utilise le _() global injecté par bindtextdomain() si présent, sinon no-op.
try:
    _  # type: ignore[name-defined]  # noqa: B018 -- sonde volontaire de NameError, pas une expression morte
except NameError as exc:
    logger.debug("netmiko push | gettext non lié, _() local : {}", exc)

    def _(s: str) -> str:  # noqa: D401
        return s


_STATUS_LABELS = {
    STATUS_OK: _("OK"),
    STATUS_REJECTED: _("Rejeté"),
    STATUS_FAILED: _("Échec"),
    STATUS_SKIPPED: _("Ignoré"),
    "pending": _("En attente"),
    "running": _("En cours"),
}

# Colonnes du ListStore d'inventaire (phase Configuration)
C_SEL, C_LINE, C_IP, C_PROFILE, C_VARS, C_IDX = range(6)

# Colonnes du ListStore de suivi (phase Suivi)
R_LINE, R_IP, R_PROFILE, R_STATUS, R_DETAIL, R_DURATION, R_IDX = range(7)

# Colonnes du ListStore de résultats (phase Résultats) — modèle distinct,
# la 6e colonne porte le chemin du fichier de log (et non une durée).
RES_LINE, RES_IP, RES_PROFILE, RES_STATUS, RES_ERROR, RES_LOGPATH, RES_IDX = range(7)

# Colonnes du ListStore de profils (éditeur embarqué)
P_NAME, P_VENDOR, P_DEVICE_TYPE, P_USERNAME, P_PORT = range(5)


def open_netmiko_tab(wmain) -> NetmikoTab:
    """Ouvre l'onglet Netmiko dans le notebook de sessions de *wmain*, ou
    ramène le focus dessus s'il est déjà ouvert (singleton).

    Args:
        wmain: Instance ``Wmain`` de GCM (fournit ``.nbConsole`` — le
            ``Gtk.Notebook`` où ``addTab()`` ouvre normalement les sessions —
            et ``.window``).
    """
    existing = getattr(wmain, "_netmiko_tab", None)
    notebook = wmain.nbConsole
    if existing is not None:
        page_num = notebook.page_num(existing.get_root_widget())
        if page_num != -1:
            notebook.set_current_page(page_num)
            return existing
        # L'onglet a été fermé entretemps : on oublie la référence périmée.
        wmain._netmiko_tab = None

    tab = NetmikoTab(wmain.window)
    root = tab.get_root_widget()
    root.show_all()
    label_box = _build_closable_tab_label(_("Netmiko"), notebook, root)
    notebook.append_page(root, label_box)
    notebook.set_tab_reorderable(root, True)
    notebook.set_current_page(notebook.get_n_pages() - 1)
    wmain._netmiko_tab = tab
    return tab


def _build_closable_tab_label(
    text: str, notebook: Gtk.Notebook, page_widget: Gtk.Widget
) -> Gtk.Box:
    """Étiquette d'onglet minimaliste avec bouton de fermeture.

    GCM dispose probablement déjà d'une ``NotebookTabLabel`` réutilisable
    (cf. les autres ``notebook.append_page(...)`` de ``gnome_connection_manager.py``) ;
    sa signature exacte n'étant pas visible dans les fichiers fournis, ce
    petit label autonome est utilisé ici. À remplacer par ``NotebookTabLabel``
    si vous préférez l'habillage standard (glisser-déposer, menu contextuel...).
    """
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    box.pack_start(Gtk.Label(label=text), False, False, 0)
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.add(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU))

    def _on_close(_b):
        page_num = notebook.page_num(page_widget)
        if page_num != -1:
            notebook.remove_page(page_num)

    btn.connect("clicked", _on_close)
    box.pack_start(btn, False, False, 0)
    box.show_all()
    return box


class NetmikoTab:
    """Widget d'onglet unique portant les 3 phases du déploiement Netmiko."""

    def __init__(self, parent_window, default_dir: str | None = None):
        """Initialise l'onglet et construit son UI (phases Inventaire/Configuration/Suivi).

        Args:
            parent_window: Fenêtre principale hôte (utilisée pour les
                dialogues modaux de sélection de fichiers).
            default_dir (str, optional): Répertoire par défaut pour
                l'inventaire CSV et le magasin de profils
                (``profiles.ini``). Le dossier personnel de l'utilisateur
                si omis.
        """
        self.parent_window = parent_window
        self.default_dir = default_dir or str(Path.home())

        # État
        self.rows: list[InventoryRow] = []
        self.template_text: str = ""
        self.profiles = ProfileStore.__new__(ProfileStore)
        self.profiles.path = Path(self.default_dir) / "profiles.ini"
        self.profiles._profiles = {}
        self._results_by_line: dict[int, PushResult] = {}
        self._monitor_store_by_line: dict[int, Gtk.TreeIter] = {}
        self._cancel_event = threading.Event()
        self._running = False
        self._log_dir: Path | None = None
        self._current_vendor_for_editor: str | None = None

        self._build_ui()

    def get_root_widget(self) -> Gtk.Widget:
        """Retourne le widget racine de l'onglet, à insérer dans le notebook hôte.

        Returns:
            Gtk.Widget: Le ``Gtk.Box`` vertical construit par :meth:`_build_ui`.
        """
        return self._root

    # ══════════════════════════════════════════════════════════════════
    # Construction générale : Gtk.Stack à 3 phases
    # ══════════════════════════════════════════════════════════════════

    def _build_ui(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_start(10)
        outer.set_margin_end(10)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        self._root = outer

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        switcher = Gtk.StackSwitcher(stack=self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        outer.pack_start(switcher, False, False, 0)
        outer.pack_start(self.stack, True, True, 0)

        self.stack.add_titled(self._build_config_page(), "config", _("1. Configuration"))
        self.stack.add_titled(self._build_monitor_page(), "monitor", _("2. Suivi"))
        self.stack.add_titled(self._build_results_page(), "results", _("3. Résultats"))

    # ══════════════════════════════════════════════════════════════════
    # Phase 1 : Configuration
    # ══════════════════════════════════════════════════════════════════

    def _build_config_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # ── Fichiers d'entrée ────────────────────────────────────────────
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        page.pack_start(grid, False, False, 0)
        self._chooser_csv = self._make_chooser_row(grid, 0, _("Inventaire (CSV) :"))
        self._chooser_tpl = self._make_chooser_row(grid, 1, _("Gabarit de configuration :"))
        btn_load = Gtk.Button(label=_("Charger l'inventaire"))
        btn_load.get_style_context().add_class("suggested-action")
        btn_load.connect("clicked", self._on_load_inventory)
        grid.attach(btn_load, 2, 0, 1, 2)

        self._lbl_config_status = Gtk.Label(label="")
        self._lbl_config_status.set_xalign(0)
        page.pack_start(self._lbl_config_status, False, False, 0)

        # ── Éditeur de profils embarqué ───────────────────────────────────
        expander = Gtk.Expander(label=_("Profils de connexion (constructeur, identifiants…)"))
        expander.set_expanded(True)
        expander.add(self._build_profile_editor())
        page.pack_start(expander, False, False, 0)

        # ── Aperçu de l'inventaire + rendu ────────────────────────────────
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_vexpand(True)
        page.pack_start(paned, True, True, 0)

        self._inv_store = Gtk.ListStore(bool, int, str, str, str, int)
        tv = Gtk.TreeView(model=self._inv_store)
        tv.set_vexpand(True)
        self._inv_tv = tv
        cr_toggle = Gtk.CellRendererToggle()
        cr_toggle.connect("toggled", self._on_row_toggled)
        tv.append_column(Gtk.TreeViewColumn(_("Envoyer"), cr_toggle, active=C_SEL))
        tv.append_column(Gtk.TreeViewColumn(_("Ligne"), Gtk.CellRendererText(), text=C_LINE))
        tv.append_column(Gtk.TreeViewColumn(_("IP / hôte"), Gtk.CellRendererText(), text=C_IP))
        tv.append_column(Gtk.TreeViewColumn(_("Profil"), Gtk.CellRendererText(), text=C_PROFILE))
        col_vars = Gtk.TreeViewColumn(_("Variables"), Gtk.CellRendererText(), text=C_VARS)
        col_vars.set_expand(True)
        tv.append_column(col_vars)
        sel = tv.get_selection()
        sel.connect("changed", self._on_inventory_selection_changed)
        scroll_tv = Gtk.ScrolledWindow()
        scroll_tv.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_tv.add(tv)
        paned.pack1(scroll_tv, resize=True, shrink=False)

        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._lbl_preview_warn = Gtk.Label(label="")
        self._lbl_preview_warn.set_xalign(0)
        preview_box.pack_start(self._lbl_preview_warn, False, False, 0)
        self._preview_buffer = Gtk.TextBuffer()
        preview_tv = Gtk.TextView(buffer=self._preview_buffer)
        preview_tv.set_editable(False)
        preview_tv.set_monospace(True)
        scroll_preview = Gtk.ScrolledWindow()
        scroll_preview.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_preview.set_size_request(-1, 140)
        scroll_preview.add(preview_tv)
        preview_box.pack_start(scroll_preview, True, True, 0)
        paned.pack2(preview_box, resize=False, shrink=False)

        # ── Options + lancement ────────────────────────────────────────────
        opts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        page.pack_start(opts, False, False, 0)
        opts.pack_start(Gtk.Label(label=_("Connexions en parallèle :")), False, False, 0)
        adj = Gtk.Adjustment(value=5, lower=1, upper=50, step_increment=1)
        self._spin_workers = Gtk.SpinButton(adjustment=adj)
        opts.pack_start(self._spin_workers, False, False, 0)
        self._chk_save = Gtk.CheckButton(label=_("Sauvegarder la configuration après envoi"))
        opts.pack_start(self._chk_save, False, False, 0)

        btn_validate = Gtk.Button(label=_("Valider (à blanc, sans se connecter)"))
        btn_validate.connect("clicked", self._on_validate)
        opts.pack_start(btn_validate, False, False, 0)

        btn_launch = Gtk.Button(label=_("Lancer le déploiement →"))
        btn_launch.get_style_context().add_class("suggested-action")
        btn_launch.connect("clicked", self._on_launch)
        opts.pack_end(btn_launch, False, False, 0)

        return page

    def _make_chooser_row(self, grid, row_idx, label_text) -> Gtk.FileChooserButton:
        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(0)
        grid.attach(lbl, 0, row_idx, 1, 1)
        chooser = Gtk.FileChooserButton(title=label_text, action=Gtk.FileChooserAction.OPEN)
        try:
            chooser.set_current_folder(self.default_dir)
        except Exception as exc:
            logger.debug(
                "netmiko push | dossier par défaut {!r} non appliqué : {}", self.default_dir, exc
            )
        chooser.set_hexpand(True)
        grid.attach(chooser, 1, row_idx, 1, 1)
        return chooser

    # ── Éditeur de profils (constructeur → device_type en cascade) ──────

    def _build_profile_editor(self) -> Gtk.Widget:
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        # Liste des profils existants
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._profile_store = Gtk.ListStore(str, str, str, str, int)
        tv = Gtk.TreeView(model=self._profile_store)
        tv.set_size_request(320, 160)
        self._profile_tv = tv
        tv.append_column(Gtk.TreeViewColumn(_("Nom"), Gtk.CellRendererText(), text=P_NAME))
        tv.append_column(
            Gtk.TreeViewColumn(_("Constructeur"), Gtk.CellRendererText(), text=P_VENDOR)
        )
        tv.append_column(
            Gtk.TreeViewColumn(_("device_type"), Gtk.CellRendererText(), text=P_DEVICE_TYPE)
        )
        tv.append_column(
            Gtk.TreeViewColumn(_("Utilisateur"), Gtk.CellRendererText(), text=P_USERNAME)
        )
        tv.get_selection().connect("changed", self._on_profile_selected)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tv)
        left.pack_start(scroll, True, True, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        btn_new = Gtk.Button(label=_("Nouveau"))
        btn_new.connect("clicked", self._on_profile_new)
        btn_del = Gtk.Button(label=_("Supprimer"))
        btn_del.connect("clicked", self._on_profile_delete)
        btns.pack_start(btn_new, False, False, 0)
        btns.pack_start(btn_del, False, False, 0)
        left.pack_start(btns, False, False, 0)

        chooser_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chooser_row.pack_start(Gtk.Label(label=_("Fichier :")), False, False, 0)
        self._chooser_profiles_file = Gtk.FileChooserButton(
            title=_("profiles.ini"), action=Gtk.FileChooserAction.OPEN
        )
        try:
            self._chooser_profiles_file.set_current_folder(self.default_dir)
        except Exception as exc:
            logger.debug(
                "netmiko push | dossier par défaut {!r} non appliqué : {}", self.default_dir, exc
            )
        self._chooser_profiles_file.connect("file-set", self._on_profiles_file_chosen)
        chooser_row.pack_start(self._chooser_profiles_file, True, True, 0)
        left.pack_start(chooser_row, False, False, 0)

        hbox.pack_start(left, False, False, 0)

        # Formulaire d'édition
        form = Gtk.Grid(column_spacing=8, row_spacing=6)
        form.set_hexpand(True)

        def add_field(row, label_text, widget):
            lbl = Gtk.Label(label=label_text)
            lbl.set_xalign(0)
            form.attach(lbl, 0, row, 1, 1)
            widget.set_hexpand(True)
            form.attach(widget, 1, row, 1, 1)

        self._f_name = Gtk.Entry()
        add_field(0, _("Nom du profil (= colonne 'type'/'profile' du CSV) :"), self._f_name)

        self._f_vendor = Gtk.ComboBoxText()
        for vendor in VENDOR_DEVICE_TYPES:
            self._f_vendor.append(vendor, vendor)
        self._f_vendor.connect("changed", self._on_vendor_changed)
        add_field(1, _("Constructeur :"), self._f_vendor)

        self._f_device_type = Gtk.ComboBoxText()
        add_field(2, _("Modèle / device_type netmiko :"), self._f_device_type)

        self._f_username = Gtk.Entry()
        add_field(3, _("Utilisateur :"), self._f_username)

        self._f_password = Gtk.Entry()
        self._f_password.set_visibility(False)
        add_field(4, _("Mot de passe :"), self._f_password)

        self._f_secret = Gtk.Entry()
        self._f_secret.set_visibility(False)
        add_field(5, _("Mot de passe enable (si applicable) :"), self._f_secret)

        self._chk_show_pw = Gtk.CheckButton(label=_("Afficher les mots de passe"))
        self._chk_show_pw.connect("toggled", self._on_toggle_show_pw)
        form.attach(self._chk_show_pw, 1, 6, 1, 1)

        self._f_port = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=22, lower=1, upper=65535, step_increment=1)
        )
        add_field(7, _("Port :"), self._f_port)

        self._f_timeout = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=10, lower=1, upper=300, step_increment=1)
        )
        add_field(8, _("Timeout (s) :"), self._f_timeout)

        self._f_save_cmd = Gtk.Entry()
        add_field(
            9,
            _("Commande de sauvegarde (optionnel, ex. 'save force') :"),
            self._f_save_cmd,
        )

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_apply = Gtk.Button(label=_("Enregistrer ce profil"))
        btn_apply.connect("clicked", self._on_profile_apply)
        btn_row.pack_start(btn_apply, False, False, 0)
        btn_save_file = Gtk.Button(label=_("Écrire profiles.ini sur disque"))
        btn_save_file.connect("clicked", self._on_profiles_save_file)
        btn_row.pack_start(btn_save_file, False, False, 0)
        form.attach(btn_row, 0, 10, 2, 1)

        hbox.pack_start(form, True, True, 0)
        return hbox

    def _on_toggle_show_pw(self, chk):
        show = chk.get_active()
        self._f_password.set_visibility(show)
        self._f_secret.set_visibility(show)

    def _on_vendor_changed(self, combo):
        vendor = combo.get_active_id()
        self._f_device_type.remove_all()
        if not vendor:
            return
        for device_type, label in VENDOR_DEVICE_TYPES.get(vendor, []):
            self._f_device_type.append(device_type, f"{device_type} — {label}")
        if VENDOR_DEVICE_TYPES.get(vendor):
            self._f_device_type.set_active(0)

    def _refresh_profile_list(self):
        self._profile_store.clear()
        for p in self.profiles.all():
            self._profile_store.append([p.name, p.vendor, p.device_type, p.username, 0])

    def _on_profiles_file_chosen(self, chooser):
        path = chooser.get_filename()
        if not path:
            return
        if not Path(path).exists():
            ProfileStore.write_template(path)
        self.profiles = ProfileStore(path)
        self._refresh_profile_list()
        self._lbl_config_status.set_text(_("Profils chargés depuis {p}").format(p=path))

    def _on_profile_selected(self, selection):
        model, it = selection.get_selected()
        if it is None:
            return
        name = model[it][P_NAME]
        p = self.profiles.get(name)
        if p is None:
            return
        self._f_name.set_text(p.name)
        if p.vendor:
            self._f_vendor.set_active_id(p.vendor)
        self._f_device_type.set_active_id(p.device_type)
        self._f_username.set_text(p.username)
        self._f_password.set_text(p.password)
        self._f_secret.set_text(p.secret)
        self._f_port.set_value(p.port)
        self._f_timeout.set_value(p.timeout)
        self._f_save_cmd.set_text(p.save_command)

    def _on_profile_new(self, _btn):
        self._f_name.set_text("")
        self._f_vendor.set_active(-1)
        self._f_device_type.remove_all()
        self._f_username.set_text("")
        self._f_password.set_text("")
        self._f_secret.set_text("")
        self._f_port.set_value(22)
        self._f_timeout.set_value(10)
        self._f_save_cmd.set_text("")
        self._f_name.grab_focus()

    def _on_profile_delete(self, _btn):
        selection = self._profile_tv.get_selection()
        model, it = selection.get_selected()
        if it is None:
            return
        name = model[it][P_NAME]
        self.profiles.delete(name)
        self._refresh_profile_list()

    def _on_profile_apply(self, _btn):
        name = self._f_name.get_text().strip()
        if not name:
            self._lbl_config_status.set_text(_("Le profil doit avoir un nom."))
            return
        device_type = self._f_device_type.get_active_id() or ""
        if not device_type:
            self._lbl_config_status.set_text(_("Choisissez un constructeur et un modèle."))
            return
        profile = DeviceProfile(
            name=name,
            vendor=self._f_vendor.get_active_id() or "",
            device_type=device_type,
            username=self._f_username.get_text(),
            password=self._f_password.get_text(),
            secret=self._f_secret.get_text(),
            port=self._f_port.get_value_as_int(),
            timeout=self._f_timeout.get_value_as_int(),
            save_command=self._f_save_cmd.get_text().strip(),
        )
        self.profiles.set(profile)
        self._refresh_profile_list()
        self._lbl_config_status.set_text(_("Profil '{n}' mis à jour (en mémoire).").format(n=name))

    def _on_profiles_save_file(self, _btn):
        if not self.profiles.path or str(self.profiles.path) in ("", "."):
            self._lbl_config_status.set_text(_("Choisissez d'abord un fichier profiles.ini."))
            return
        try:
            self.profiles.save()
            self._lbl_config_status.set_text(
                _("Profils écrits dans {p}").format(p=self.profiles.path)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("netmiko push | écriture des profils impossible : {}", exc)
            self._lbl_config_status.set_text(_("Erreur d'écriture : {e}").format(e=exc))

    # ── Inventaire ────────────────────────────────────────────────────────

    def _on_load_inventory(self, _btn):
        csv_path = self._chooser_csv.get_filename()
        tpl_path = self._chooser_tpl.get_filename()
        if not csv_path or not tpl_path:
            self._lbl_config_status.set_text(_("Choisissez l'inventaire CSV et le gabarit."))
            return
        try:
            self.rows = load_inventory(csv_path)
            self.template_text = Path(tpl_path).read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("netmiko push | chargement de l'inventaire impossible : {}", exc)
            self._lbl_config_status.set_text(_("Erreur de chargement : {e}").format(e=exc))
            return

        self._inv_store.clear()
        for idx, row in enumerate(self.rows):
            vars_summary = ", ".join(f"{k}={v}" for k, v in row.variables.items())
            self._inv_store.append(
                [True, row.line_no, row.ip, row.profile_name, vars_summary, idx]
            )
        self._lbl_config_status.set_text(
            _("{n} hôte(s) chargé(s) depuis {csv}.").format(n=len(self.rows), csv=csv_path)
        )

    def _on_row_toggled(self, _cell, path):
        self._inv_store[path][C_SEL] = not self._inv_store[path][C_SEL]

    def _on_inventory_selection_changed(self, selection):
        model, it = selection.get_selected()
        if it is None:
            return
        idx = model[it][C_IDX]
        row = self.rows[idx]
        context = row.render_context()
        missing = find_missing_variables(self.template_text, context)
        if missing:
            self._lbl_preview_warn.set_text(
                _("⚠ Variables absentes du CSV : {v}").format(v=", ".join(missing))
            )
        else:
            self._lbl_preview_warn.set_text("")
        self._preview_buffer.set_text(render_template(self.template_text, context))

    def _selected_rows(self) -> list[InventoryRow]:
        out = []
        for r in self._inv_store:
            if r[C_SEL]:
                out.append(self.rows[r[C_IDX]])
        return out

    def _on_validate(self, _btn):
        if not self.rows:
            self._lbl_config_status.set_text(_("Chargez d'abord un inventaire."))
            return
        n_ok = 0
        n_err = 0
        for r in self._inv_store:
            row = self.rows[r[C_IDX]]
            profile = self.profiles.get(row.profile_name)
            if profile is None:
                r[C_VARS] = _("⚠ profil inconnu : {p}").format(p=row.profile_name)
                n_err += 1
                continue
            missing = find_missing_variables(self.template_text, row.render_context())
            if missing:
                r[C_VARS] = _("⚠ variables manquantes : {v}").format(v=", ".join(missing))
                n_err += 1
                continue
            n_ok += 1
        self._lbl_config_status.set_text(
            _("Validation : {ok} prêt(s), {err} en erreur.").format(ok=n_ok, err=n_err)
        )

    # ══════════════════════════════════════════════════════════════════
    # Phase 2 : Suivi
    # ══════════════════════════════════════════════════════════════════

    def _build_monitor_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self._progress = Gtk.ProgressBar()
        self._progress.set_show_text(True)
        page.pack_start(self._progress, False, False, 0)

        counts_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self._lbl_count_ok = Gtk.Label(label=_("OK : 0"))
        self._lbl_count_rejected = Gtk.Label(label=_("Rejetés : 0"))
        self._lbl_count_failed = Gtk.Label(label=_("Échecs : 0"))
        self._lbl_count_pending = Gtk.Label(label=_("En attente : 0"))
        for lbl in (
            self._lbl_count_ok,
            self._lbl_count_rejected,
            self._lbl_count_failed,
            self._lbl_count_pending,
        ):
            counts_box.pack_start(lbl, False, False, 0)
        page.pack_start(counts_box, False, False, 0)

        self._monitor_store = Gtk.ListStore(int, str, str, str, str, str, int)
        tv = Gtk.TreeView(model=self._monitor_store)
        tv.set_vexpand(True)
        tv.append_column(Gtk.TreeViewColumn(_("Ligne"), Gtk.CellRendererText(), text=R_LINE))
        tv.append_column(Gtk.TreeViewColumn(_("IP / hôte"), Gtk.CellRendererText(), text=R_IP))
        tv.append_column(Gtk.TreeViewColumn(_("Profil"), Gtk.CellRendererText(), text=R_PROFILE))
        tv.append_column(Gtk.TreeViewColumn(_("Statut"), Gtk.CellRendererText(), text=R_STATUS))
        col_detail = Gtk.TreeViewColumn(_("Détail"), Gtk.CellRendererText(), text=R_DETAIL)
        col_detail.set_expand(True)
        tv.append_column(col_detail)
        tv.append_column(
            Gtk.TreeViewColumn(_("Durée (s)"), Gtk.CellRendererText(), text=R_DURATION)
        )
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tv)
        page.pack_start(scroll, True, True, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._btn_cancel_run = Gtk.Button(label=_("Annuler l'envoi"))
        self._btn_cancel_run.connect("clicked", self._on_cancel_run)
        self._btn_cancel_run.set_sensitive(False)
        btn_row.pack_start(self._btn_cancel_run, False, False, 0)
        self._btn_goto_results = Gtk.Button(label=_("Voir les résultats →"))
        self._btn_goto_results.connect(
            "clicked", lambda _b: self.stack.set_visible_child_name("results")
        )
        self._btn_goto_results.set_sensitive(False)
        btn_row.pack_end(self._btn_goto_results, False, False, 0)
        page.pack_start(btn_row, False, False, 0)

        return page

    def _on_launch(self, _btn):
        if self._running:
            return
        if not self.rows:
            self._lbl_config_status.set_text(_("Chargez d'abord un inventaire."))
            return
        selected = self._selected_rows()
        if not selected:
            self._lbl_config_status.set_text(_("Aucune ligne cochée."))
            return

        self._log_dir = (
            Path.home()
            / ".local"
            / "share"
            / "gcm"
            / "netmiko_push"
            / time.strftime("%Y%m%d_%H%M%S")
        )
        self._cancel_event = threading.Event()
        self._running = True
        self._results_by_line.clear()
        self._monitor_store.clear()
        self._monitor_store_by_line.clear()

        for row in selected:
            it = self._monitor_store.append(
                [
                    row.line_no,
                    row.ip,
                    row.profile_name,
                    _STATUS_LABELS["pending"],
                    "",
                    "",
                    row.line_no,
                ]
            )
            self._monitor_store_by_line[row.line_no] = it

        self._total_to_run = len(selected)
        self._done_count = 0
        self._progress.set_fraction(0.0)
        self._progress.set_text(_("0 / {n}").format(n=self._total_to_run))
        self._btn_cancel_run.set_sensitive(True)
        self._btn_goto_results.set_sensitive(False)
        self._update_counts()

        self.stack.set_visible_child_name("monitor")

        max_workers = self._spin_workers.get_value_as_int()
        save = self._chk_save.get_active()

        def on_row_start(row: InventoryRow):
            GLib.idle_add(self._set_monitor_status, row.line_no, _STATUS_LABELS["running"], "", "")

        def on_row_done(result: PushResult):
            GLib.idle_add(self._handle_row_done, result)

        def worker():
            try:
                run_bulk_push(
                    selected,
                    self.profiles,
                    self.template_text,
                    log_dir=self._log_dir,
                    save=save,
                    max_workers=max_workers,
                    on_row_start=on_row_start,
                    on_row_done=on_row_done,
                    cancel_event=self._cancel_event,
                )
            finally:
                GLib.idle_add(self._on_run_finished)

        threading.Thread(target=worker, daemon=True).start()

    def _on_cancel_run(self, _btn):
        self._cancel_event.set()
        self._btn_cancel_run.set_sensitive(False)

    def _set_monitor_status(self, line_no, status_text, detail, duration_text):
        it = self._monitor_store_by_line.get(line_no)
        if it is None:
            return False
        self._monitor_store[it][R_STATUS] = status_text
        self._monitor_store[it][R_DETAIL] = detail
        self._monitor_store[it][R_DURATION] = duration_text
        return False

    def _handle_row_done(self, result: PushResult):
        self._results_by_line[result.row.line_no] = result
        detail = result.error or ("; ".join(result.config_errors) if result.config_errors else "")
        self._set_monitor_status(
            result.row.line_no,
            _STATUS_LABELS.get(result.status, result.status),
            detail,
            f"{result.duration_s:.1f}",
        )
        self._done_count += 1
        self._progress.set_fraction(self._done_count / max(1, self._total_to_run))
        self._progress.set_text(_("{d} / {n}").format(d=self._done_count, n=self._total_to_run))
        self._update_counts()
        return False

    def _update_counts(self):
        counts = {STATUS_OK: 0, STATUS_REJECTED: 0, STATUS_FAILED: 0}
        for r in self._results_by_line.values():
            if r.status in counts:
                counts[r.status] += 1
        pending = self._total_to_run - len(self._results_by_line)
        self._lbl_count_ok.set_text(_("OK : {n}").format(n=counts[STATUS_OK]))
        self._lbl_count_rejected.set_text(_("Rejetés : {n}").format(n=counts[STATUS_REJECTED]))
        self._lbl_count_failed.set_text(_("Échecs : {n}").format(n=counts[STATUS_FAILED]))
        self._lbl_count_pending.set_text(_("En attente : {n}").format(n=pending))

    def _on_run_finished(self):
        self._running = False
        self._btn_cancel_run.set_sensitive(False)
        self._btn_goto_results.set_sensitive(True)
        self._populate_results()
        return False

    # ══════════════════════════════════════════════════════════════════
    # Phase 3 : Résultats
    # ══════════════════════════════════════════════════════════════════

    def _build_results_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self._lbl_summary = Gtk.Label(label=_("Aucune exécution pour le moment."))
        self._lbl_summary.set_xalign(0)
        page.pack_start(self._lbl_summary, False, False, 0)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        page.pack_start(paned, True, True, 0)

        self._results_store = Gtk.ListStore(int, str, str, str, str, str, int)
        tv = Gtk.TreeView(model=self._results_store)
        tv.append_column(Gtk.TreeViewColumn(_("Ligne"), Gtk.CellRendererText(), text=RES_LINE))
        tv.append_column(Gtk.TreeViewColumn(_("IP / hôte"), Gtk.CellRendererText(), text=RES_IP))
        tv.append_column(Gtk.TreeViewColumn(_("Profil"), Gtk.CellRendererText(), text=RES_PROFILE))
        tv.append_column(Gtk.TreeViewColumn(_("Statut"), Gtk.CellRendererText(), text=RES_STATUS))
        tv.get_selection().connect("changed", self._on_result_selected)
        self._results_tv = tv
        scroll_left = Gtk.ScrolledWindow()
        scroll_left.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_left.set_size_request(380, -1)
        scroll_left.add(tv)
        paned.pack1(scroll_left, resize=False, shrink=False)

        self._log_buffer = Gtk.TextBuffer()
        log_tv = Gtk.TextView(buffer=self._log_buffer)
        log_tv.set_editable(False)
        log_tv.set_monospace(True)
        scroll_right = Gtk.ScrolledWindow()
        scroll_right.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll_right.add(log_tv)
        paned.pack2(scroll_right, resize=True, shrink=False)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_open_dir = Gtk.Button(label=_("Ouvrir le dossier des journaux"))
        btn_open_dir.connect("clicked", self._on_open_log_dir)
        btn_row.pack_start(btn_open_dir, False, False, 0)
        btn_new = Gtk.Button(label=_("← Nouveau déploiement"))
        btn_new.connect("clicked", lambda _b: self.stack.set_visible_child_name("config"))
        btn_row.pack_end(btn_new, False, False, 0)
        page.pack_start(btn_row, False, False, 0)

        return page

    def _populate_results(self):
        self._results_store.clear()
        results = sorted(self._results_by_line.values(), key=lambda r: r.row.line_no)
        summary = summarize_results(results)
        self._lbl_summary.set_text(
            _(
                "Total : {total}  |  OK : {ok}  |  Rejetés : {rejected}  |  Échecs : {failed}  |  Ignorés : {skipped}"
            ).format(**summary)
        )
        for r in results:
            self._results_store.append(
                [
                    r.row.line_no,
                    r.row.ip,
                    r.row.profile_name,
                    _STATUS_LABELS.get(r.status, r.status),
                    r.error,
                    r.log_path,
                    r.row.line_no,
                ]
            )

    def _on_result_selected(self, selection):
        model, it = selection.get_selected()
        if it is None:
            return
        log_path = model[it][RES_LOGPATH]
        if not log_path:
            self._log_buffer.set_text("")
            return
        try:
            content = Path(log_path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("netmiko push | journal {} illisible : {}", log_path, exc)
            content = _("Impossible de lire le journal : {e}").format(e=exc)
        self._log_buffer.set_text(content)

    def _on_open_log_dir(self, _btn):
        if self._log_dir is None:
            return
        try:
            subprocess.Popen(["xdg-open", str(self._log_dir)])
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"xdg-open a échoué : {exc}")


# ══════════════════════════════════════════════════════════════════════
# Point d'entrée BatchPlugin (autoload par BatchPluginRegistry)
# ══════════════════════════════════════════════════════════════════════


class NetmikoPushBatchPlugin(BatchPlugin):
    """Adapte ``open_netmiko_tab`` au contrat ``BatchPlugin``.

    Découvert et enregistré automatiquement par
    ``BatchPluginRegistry.autoload()`` — voir ``plugin_base.py`` et le
    docstring de module ci-dessus.
    """

    tool_id = "netmiko-push"
    display_name = _("Déploiement Netmiko…")
    icon_name = "network-transmit-receive-symbolic"

    def activate(self) -> None:
        """Ouvre (ou ramène au premier plan) l'onglet Netmiko de ``self.app``.

        ``self.app`` est l'instance ``Wmain``, injectée par
        ``BatchPluginRegistry.bind_app()`` juste après l'autoload. Si elle
        n'a pas été injectée (mauvais ordre d'appel côté Wmain), on échoue
        explicitement plutôt que de planter plus loin avec un
        ``AttributeError`` peu clair.
        """
        if self.app is None:
            raise RuntimeError(
                "NetmikoPushBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        open_netmiko_tab(self.app)


def get_batch_plugin() -> NetmikoPushBatchPlugin:
    """Point d'entrée de découverte pour ``BatchPluginRegistry.autoload()``."""
    return NetmikoPushBatchPlugin()
