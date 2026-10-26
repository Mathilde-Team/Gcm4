"""Onglet « Push SNMP » (déploiement de configuration en masse par SNMP) pour GCM.

Jumeau de ``plugin_netmiko_push.py`` : même contrat (``BatchPlugin``, pas
``ConnectionPlugin`` — action par lot sur un inventaire, pas une session par
hôte), même structure à 3 phases (Configuration → Suivi → Résultats) en
``Gtk.Stack``, même intégration en onglet singleton dans ``wmain.nbConsole``
plutôt qu'en dialogue modal. Voir le docstring de ``plugin_netmiko_push.py``
pour la justification de cette dérogation à la règle « un onglet = une
connexion » du reste de GCM.

Seule la phase Configuration change substantiellement : au lieu d'un
device_type netmiko + identifiants SSH, un profil ici combine un
constructeur (``snmp_bulk_core.SNMP_VENDORS`` — H3C/Comware, Cisco...) et
des identifiants SNMP (v2c ou v3), et une section supplémentaire configure
le serveur de dépôt (SFTP/FTP/TFTP) sur lequel le fichier rendu est déposé
avant que l'équipement n'aille le chercher via l'opération SNMP du driver.
Tout le reste (inventaire CSV, gabarit, suivi, résultats, journaux) est
strictement le même mécanisme que côté Netmiko.

Intégration dans Wmain : AUCUNE (cf. ``get_batch_plugin()`` en bas de
fichier, découvert automatiquement par ``BatchPluginRegistry.autoload()``).

AVERTISSEMENT — cf. l'avertissement en tête de ``snmp_bulk_core.py`` : la
partie SNMP (ezsnmp) n'a pas pu être testée contre un équipement réel dans
cet environnement de développement. Teste sur un équipement de labo avant
tout usage en production.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402
from loguru import logger
from plugin_base import BatchPlugin  # noqa: E402

from snmp_bulk_core import (  # noqa: E402
    SNMP_VENDORS,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_REJECTED,
    STATUS_SKIPPED,
    InventoryRow,
    PushResult,
    SnmpAuth,
    SnmpProfile,
    SnmpProfileStore,
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
    logger.debug("snmp push | gettext non lié, _() local : {}", exc)

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

# Colonnes du ListStore de résultats (phase Résultats)
RES_LINE, RES_IP, RES_PROFILE, RES_STATUS, RES_ERROR, RES_LOGPATH, RES_IDX = range(7)

# Colonnes du ListStore de profils SNMP (éditeur embarqué)
P_NAME, P_VENDOR, P_VERSION, P_PORT = range(4)


def open_snmp_push_tab(wmain) -> SnmpPushTab:
    """Ouvre l'onglet Push SNMP dans le notebook de sessions de *wmain*, ou
    ramène le focus dessus s'il est déjà ouvert (singleton).

    Args:
        wmain: Instance ``Wmain`` de GCM (fournit ``.nbConsole`` et ``.window``).
    """
    logger.debug(f"open_snmp_push_tab | wmain={wmain}")
    existing = getattr(wmain, "_snmp_push_tab", None)
    notebook = wmain.nbConsole
    if existing is not None:
        page_num = notebook.page_num(existing.get_root_widget())
        if page_num != -1:
            notebook.set_current_page(page_num)
            return existing
        wmain._snmp_push_tab = None

    tab = SnmpPushTab(wmain.window)
    root = tab.get_root_widget()
    root.show_all()
    label_box = _build_closable_tab_label(_("Push SNMP"), notebook, root)
    notebook.append_page(root, label_box)
    notebook.set_tab_reorderable(root, True)
    notebook.set_current_page(notebook.get_n_pages() - 1)
    wmain._snmp_push_tab = tab
    return tab


def _build_closable_tab_label(
    text: str, notebook: Gtk.Notebook, page_widget: Gtk.Widget
) -> Gtk.Box:
    """Étiquette d'onglet minimaliste avec bouton de fermeture — copie du
    helper de ``plugin_netmiko_push.py`` (petite duplication assumée pour
    ne pas faire dépendre ce plugin d'un autre plugin).

    Args:
        text: Libellé affiché sur l'onglet.
        notebook: Notebook GTK parent, pour retrouver le numéro de page à fermer.
        page_widget: Widget racine de la page associée à cet onglet.
    """
    logger.debug(f"_build_closable_tab_label | text={text}")
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    box.pack_start(Gtk.Label(label=text), False, False, 0)
    btn = Gtk.Button()
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.add(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU))

    def _on_close(_b):
        """Ferme l'onglet en retirant sa page du notebook.

        Args:
            _b: Bouton GTK à l'origine du signal (non utilisé).
        """
        logger.debug("_build_closable_tab_label._on_close")
        page_num = notebook.page_num(page_widget)
        if page_num != -1:
            notebook.remove_page(page_num)

    btn.connect("clicked", _on_close)
    box.pack_start(btn, False, False, 0)
    box.show_all()
    return box


class SnmpPushTab:
    """Widget d'onglet unique portant les 3 phases du déploiement SNMP."""

    def __init__(self, parent_window, default_dir: str | None = None):
        """Initialise l'onglet (état vide, aucun inventaire chargé).

        Args:
            parent_window: Fenêtre GTK parente (dialogues modaux éventuels).
            default_dir: Dossier initial proposé dans les sélecteurs de fichiers.
        """
        logger.debug(f"SnmpPushTab.__init__ | default_dir={default_dir}")
        self.parent_window = parent_window
        self.default_dir = default_dir or str(Path.home())

        self.rows: list[InventoryRow] = []
        self.template_text: str = ""
        self.profiles = SnmpProfileStore.__new__(SnmpProfileStore)
        self.profiles.path = Path(self.default_dir) / "snmp_profiles.ini"
        self.profiles._profiles = {}
        self._results_by_line: dict[int, PushResult] = {}
        self._monitor_store_by_line: dict[int, Gtk.TreeIter] = {}
        self._cancel_event = threading.Event()
        self._running = False
        self._log_dir: Path | None = None

        self._build_ui()

    def get_root_widget(self) -> Gtk.Widget:
        """Retourne le widget racine à insérer dans le notebook de sessions."""
        logger.debug("SnmpPushTab.get_root_widget")
        return self._root

    # ══════════════════════════════════════════════════════════════════
    # Construction générale : Gtk.Stack à 3 phases
    # ══════════════════════════════════════════════════════════════════

    def _build_ui(self):
        """Construit le ``Gtk.Stack`` à 3 phases (Configuration/Suivi/Résultats)."""
        logger.debug("SnmpPushTab._build_ui")
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
        """Construit la page de la phase 1 (Configuration).

        Returns:
            Le widget racine de la page.
        """
        logger.debug("SnmpPushTab._build_config_page")
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

        # ── Éditeur de profils SNMP embarqué ───────────────────────────
        expander_p = Gtk.Expander(label=_("Profils SNMP (constructeur, identifiants…)"))
        expander_p.set_expanded(True)
        expander_p.add(self._build_profile_editor())
        page.pack_start(expander_p, False, False, 0)

        # ── Serveur de dépôt (SFTP/FTP/TFTP) ────────────────────────────
        expander_t = Gtk.Expander(label=_("Serveur de dépôt du fichier (SFTP / FTP / TFTP)"))
        expander_t.set_expanded(False)
        expander_t.add(self._build_transfer_editor())
        page.pack_start(expander_t, False, False, 0)

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
        opts.pack_start(Gtk.Label(label=_("Envois en parallèle :")), False, False, 0)
        adj = Gtk.Adjustment(value=10, lower=1, upper=200, step_increment=1)
        self._spin_workers = Gtk.SpinButton(adjustment=adj)
        opts.pack_start(self._spin_workers, False, False, 0)

        btn_validate = Gtk.Button(label=_("Valider (à blanc, sans envoyer)"))
        btn_validate.connect("clicked", self._on_validate)
        opts.pack_start(btn_validate, False, False, 0)

        btn_launch = Gtk.Button(label=_("Lancer le déploiement →"))
        btn_launch.get_style_context().add_class("suggested-action")
        btn_launch.connect("clicked", self._on_launch)
        opts.pack_end(btn_launch, False, False, 0)

        return page

    def _make_chooser_row(self, grid, row_idx, label_text) -> Gtk.FileChooserButton:
        """Ajoute une ligne « label + sélecteur de fichier » à *grid*.

        Args:
            grid: Grille GTK où insérer la ligne.
            row_idx: Index de ligne dans la grille.
            label_text: Libellé affiché devant le sélecteur.

        Returns:
            Le ``Gtk.FileChooserButton`` créé.
        """
        logger.debug(f"SnmpPushTab._make_chooser_row | row_idx={row_idx} label={label_text}")
        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(0)
        grid.attach(lbl, 0, row_idx, 1, 1)
        chooser = Gtk.FileChooserButton(title=label_text, action=Gtk.FileChooserAction.OPEN)
        try:
            chooser.set_current_folder(self.default_dir)
        except Exception as exc:
            logger.debug("snmp push | dossier par défaut {!r} non appliqué : {}", self.default_dir, exc)
        chooser.set_hexpand(True)
        grid.attach(chooser, 1, row_idx, 1, 1)
        return chooser

    # ── Éditeur de profils SNMP ──────────────────────────────────────────

    def _build_profile_editor(self) -> Gtk.Widget:
        """Construit l'éditeur de profils SNMP embarqué (liste + formulaire)."""
        logger.debug("SnmpPushTab._build_profile_editor")
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._profile_store = Gtk.ListStore(str, str, str, int)
        tv = Gtk.TreeView(model=self._profile_store)
        tv.set_size_request(300, 160)
        self._profile_tv = tv
        tv.append_column(Gtk.TreeViewColumn(_("Nom"), Gtk.CellRendererText(), text=P_NAME))
        tv.append_column(
            Gtk.TreeViewColumn(_("Constructeur"), Gtk.CellRendererText(), text=P_VENDOR)
        )
        tv.append_column(Gtk.TreeViewColumn(_("SNMP"), Gtk.CellRendererText(), text=P_VERSION))
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
            title=_("snmp_profiles.ini"), action=Gtk.FileChooserAction.OPEN
        )
        try:
            self._chooser_profiles_file.set_current_folder(self.default_dir)
        except Exception as exc:
            logger.debug("snmp push | dossier par défaut {!r} non appliqué : {}", self.default_dir, exc)
        self._chooser_profiles_file.connect("file-set", self._on_profiles_file_chosen)
        chooser_row.pack_start(self._chooser_profiles_file, True, True, 0)
        left.pack_start(chooser_row, False, False, 0)

        hbox.pack_start(left, False, False, 0)

        form = Gtk.Grid(column_spacing=8, row_spacing=6)
        form.set_hexpand(True)

        def add_field(row, label_text, widget):
            """Ajoute une ligne « label + widget » au formulaire de profil.

            Args:
                row: Index de ligne dans la grille du formulaire.
                label_text: Libellé affiché devant le widget.
                widget: Widget de saisie à insérer.
            """
            logger.debug(f"_build_profile_editor.add_field | row={row} label={label_text}")
            lbl = Gtk.Label(label=label_text)
            lbl.set_xalign(0)
            form.attach(lbl, 0, row, 1, 1)
            widget.set_hexpand(True)
            form.attach(widget, 1, row, 1, 1)

        self._f_name = Gtk.Entry()
        add_field(0, _("Nom du profil (= colonne 'type'/'profile' du CSV) :"), self._f_name)

        self._f_vendor = Gtk.ComboBoxText()
        for vendor_id, display_name in SNMP_VENDORS.items():
            self._f_vendor.append(vendor_id, display_name)
        add_field(1, _("Constructeur (driver de push SNMP) :"), self._f_vendor)

        self._f_version = Gtk.ComboBoxText()
        self._f_version.append("v2c", "SNMPv2c (communauté)")
        self._f_version.append("v3", "SNMPv3 (USM, auth+priv)")
        self._f_version.set_active_id("v2c")
        add_field(2, _("Version SNMP :"), self._f_version)

        self._f_community = Gtk.Entry()
        self._f_community.set_visibility(False)
        add_field(3, _("Communauté (v2c, en écriture — jamais 'public') :"), self._f_community)

        self._f_v3_user = Gtk.Entry()
        add_field(4, _("Utilisateur USM (v3) :"), self._f_v3_user)

        self._f_auth_proto = Gtk.ComboBoxText()
        for proto in ("SHA", "MD5", ""):
            self._f_auth_proto.append(proto or "none", proto or _("(aucune)"))
        self._f_auth_proto.set_active_id("SHA")
        add_field(5, _("Protocole d'authentification (v3) :"), self._f_auth_proto)

        self._f_auth_key = Gtk.Entry()
        self._f_auth_key.set_visibility(False)
        add_field(6, _("Clé d'authentification (v3) :"), self._f_auth_key)

        self._f_priv_proto = Gtk.ComboBoxText()
        for proto in ("AES", "DES", ""):
            self._f_priv_proto.append(proto or "none", proto or _("(aucun)"))
        self._f_priv_proto.set_active_id("AES")
        add_field(7, _("Protocole de confidentialité (v3) :"), self._f_priv_proto)

        self._f_priv_key = Gtk.Entry()
        self._f_priv_key.set_visibility(False)
        add_field(8, _("Clé de confidentialité (v3) :"), self._f_priv_key)

        self._chk_show_pw = Gtk.CheckButton(label=_("Afficher les secrets"))
        self._chk_show_pw.connect("toggled", self._on_toggle_show_pw)
        form.attach(self._chk_show_pw, 1, 9, 1, 1)

        self._f_port = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=161, lower=1, upper=65535, step_increment=1)
        )
        add_field(10, _("Port SNMP :"), self._f_port)

        self._f_timeout = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(value=5, lower=1, upper=120, step_increment=1)
        )
        add_field(11, _("Timeout requête SNMP (s) :"), self._f_timeout)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_apply = Gtk.Button(label=_("Enregistrer ce profil"))
        btn_apply.connect("clicked", self._on_profile_apply)
        btn_row.pack_start(btn_apply, False, False, 0)
        btn_save_file = Gtk.Button(label=_("Écrire snmp_profiles.ini sur disque"))
        btn_save_file.connect("clicked", self._on_profiles_save_file)
        btn_row.pack_start(btn_save_file, False, False, 0)
        form.attach(btn_row, 0, 12, 2, 1)

        hbox.pack_start(form, True, True, 0)
        return hbox

    def _build_transfer_editor(self) -> Gtk.Widget:
        """Éditeur du serveur de dépôt pour SNMP."""
        logger.debug("SnmpPushTab._build_transfer_editor")
        form = Gtk.Grid(column_spacing=8, row_spacing=6)

        def add_field(row, label_text, widget):
            """Ajoute une ligne « label + widget » au formulaire de transfert.

            Args:
                row: Index de ligne dans la grille du formulaire.
                label_text: Libellé affiché devant le widget.
                widget: Widget de saisie à insérer.
            """
            logger.debug(f"_build_transfer_editor.add_field | row={row} label={label_text}")
            lbl = Gtk.Label(label=label_text)
            lbl.set_xalign(0)
            form.attach(lbl, 0, row, 1, 1)
            widget.set_hexpand(True)
            form.attach(widget, 1, row, 1, 1)

        self._t_server_ip = Gtk.Entry()
        self._t_server_ip.set_text("192.168.1.1")
        add_field(0, _("IP du serveur de dépôt (TFTP/FTP/SFTP) :"), self._t_server_ip)

        hint = Gtk.Label(
            label=_(
                "⚠ L'équipement doit pouvoir joindre ce serveur pour aller chercher "
                "le fichier de configuration. Les paramètres de transfert (protocole, credentials) "
                "doivent être configurés à part."
            )
        )
        hint.set_xalign(0)
        hint.set_line_wrap(True)
        form.attach(hint, 0, 1, 2, 1)

        return form

    def _current_server_ip(self) -> str:
        """Récupère l'IP du serveur de dépôt."""
        logger.debug("SnmpPushTab._current_server_ip")
        return self._t_server_ip.get_text().strip() or "192.168.1.1"

    def _on_toggle_show_pw(self, chk):
        """Bascule la visibilité des champs secrets du formulaire de profil.

        Args:
            chk: Case à cocher GTK à l'origine du signal.
        """
        logger.debug(f"SnmpPushTab._on_toggle_show_pw | active={chk.get_active()}")
        show = chk.get_active()
        for entry in (self._f_community, self._f_auth_key, self._f_priv_key):
            entry.set_visibility(show)

    def _refresh_profile_list(self):
        """Recharge la liste des profils SNMP affichée depuis ``self.profiles``."""
        logger.debug("SnmpPushTab._refresh_profile_list")
        self._profile_store.clear()
        for p in self.profiles.all():
            self._profile_store.append([p.name, p.vendor, p.auth.version, p.port])

    def _on_profiles_file_chosen(self, chooser):
        """Recharge le magasin de profils depuis le fichier choisi.

        Args:
            chooser: Sélecteur de fichier GTK à l'origine du signal.
        """
        logger.debug("SnmpPushTab._on_profiles_file_chosen")
        path = chooser.get_filename()
        if not path:
            return
        self.profiles = SnmpProfileStore(path)
        self._refresh_profile_list()
        self._lbl_config_status.set_text(_("Profils SNMP chargés depuis {p}").format(p=path))

    def _on_profile_selected(self, selection):
        """Remplit le formulaire avec le profil sélectionné dans la liste.

        Args:
            selection: Sélection GTK de la liste de profils.
        """
        model, it = selection.get_selected()
        if it is None:
            return
        name = model[it][P_NAME]
        p = self.profiles.get(name)
        if p is None:
            return
        logger.debug(f"SnmpPushTab._on_profile_selected | name={name}")
        self._f_name.set_text(p.name)
        if p.vendor:
            self._f_vendor.set_active_id(p.vendor)
        self._f_version.set_active_id(p.auth.version)
        self._f_community.set_text(p.auth.community or "")
        self._f_v3_user.set_text(p.auth.username or "")
        self._f_auth_proto.set_active_id(p.auth.auth_protocol or "none")
        self._f_auth_key.set_text(p.auth.auth_password or "")
        self._f_priv_proto.set_active_id(p.auth.priv_protocol or "none")
        self._f_priv_key.set_text(p.auth.priv_password or "")
        self._f_port.set_value(p.port)
        self._f_timeout.set_value(p.timeout)

    def _on_profile_new(self, _btn):
        """Réinitialise le formulaire pour la saisie d'un nouveau profil.

        Args:
            _btn: Bouton GTK à l'origine du signal (non utilisé).
        """
        logger.debug("SnmpPushTab._on_profile_new")
        self._f_name.set_text("")
        self._f_vendor.set_active(-1)
        self._f_version.set_active_id("v2c")
        self._f_community.set_text("")
        self._f_v3_user.set_text("")
        self._f_auth_proto.set_active_id("SHA")
        self._f_auth_key.set_text("")
        self._f_priv_proto.set_active_id("AES")
        self._f_priv_key.set_text("")
        self._f_port.set_value(161)
        self._f_timeout.set_value(5)
        self._f_name.grab_focus()

    def _on_profile_delete(self, _btn):
        """Supprime le profil actuellement sélectionné dans la liste.

        Args:
            _btn: Bouton GTK à l'origine du signal (non utilisé).
        """
        selection = self._profile_tv.get_selection()
        model, it = selection.get_selected()
        if it is None:
            return
        name = model[it][P_NAME]
        logger.debug(f"SnmpPushTab._on_profile_delete | name={name}")
        self.profiles.delete(name)
        self._refresh_profile_list()

    def _on_profile_apply(self, _btn):
        """Valide le formulaire et enregistre le profil en mémoire.

        Args:
            _btn: Bouton GTK à l'origine du signal (non utilisé).
        """
        name = self._f_name.get_text().strip()
        if not name:
            self._lbl_config_status.set_text(_("Le profil doit avoir un nom."))
            return
        vendor_id = self._f_vendor.get_active_id() or ""
        if not vendor_id:
            self._lbl_config_status.set_text(_("Choisissez un constructeur."))
            return
        logger.debug(f"SnmpPushTab._on_profile_apply | name={name} vendor={vendor_id}")
        auth_proto = self._f_auth_proto.get_active_id() or ""
        priv_proto = self._f_priv_proto.get_active_id() or ""
        auth = SnmpAuth(
            version=self._f_version.get_active_id() or "v2c",
            community=self._f_community.get_text(),
            username=self._f_v3_user.get_text(),
            auth_protocol="" if auth_proto == "none" else auth_proto,
            auth_password=self._f_auth_key.get_text(),
            priv_protocol="" if priv_proto == "none" else priv_proto,
            priv_password=self._f_priv_key.get_text(),
        )
        profile = SnmpProfile(
            name=name,
            vendor=vendor_id,
            auth=auth,
            port=self._f_port.get_value_as_int(),
            timeout=int(self._f_timeout.get_value()),
        )
        self.profiles.set(name, profile)
        self._refresh_profile_list()
        self._lbl_config_status.set_text(_("Profil '{n}' mis à jour (en mémoire).").format(n=name))

    def _on_profiles_save_file(self, _btn):
        """Écrit le magasin de profils SNMP sur disque.

        Args:
            _btn: Bouton GTK à l'origine du signal (non utilisé).
        """
        if not self.profiles.path or str(self.profiles.path) in ("", "."):
            self._lbl_config_status.set_text(_("Choisissez d'abord un fichier snmp_profiles.ini."))
            return
        logger.debug(f"SnmpPushTab._on_profiles_save_file | path={self.profiles.path}")
        try:
            self.profiles.save()
            self._lbl_config_status.set_text(
                _("Profils écrits dans {p}").format(p=self.profiles.path)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("snmp push | écriture des profils impossible : {}", exc)
            self._lbl_config_status.set_text(_("Erreur d'écriture : {e}").format(e=exc))

    # ── Inventaire ────────────────────────────────────────────────────────

    def _on_load_inventory(self, _btn):
        """Charge l'inventaire CSV et le gabarit choisis, peuple l'aperçu.

        Args:
            _btn: Bouton GTK à l'origine du signal (non utilisé).
        """
        csv_path = self._chooser_csv.get_filename()
        tpl_path = self._chooser_tpl.get_filename()
        if not csv_path or not tpl_path:
            self._lbl_config_status.set_text(_("Choisissez l'inventaire CSV et le gabarit."))
            return
        logger.debug(f"SnmpPushTab._on_load_inventory | csv={csv_path} tpl={tpl_path}")
        try:
            self.rows = load_inventory(csv_path)
            self.template_text = Path(tpl_path).read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("snmp push | chargement de l'inventaire impossible : {}", exc)
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
        """Inverse la case « Envoyer » de la ligne d'inventaire cliquée.

        Args:
            _cell: Cell renderer GTK à l'origine du signal (non utilisé).
            path: Chemin (index) de la ligne basculée dans le ``Gtk.ListStore``.
        """
        logger.debug(f"SnmpPushTab._on_row_toggled | path={path}")
        self._inv_store[path][C_SEL] = not self._inv_store[path][C_SEL]

    def _on_inventory_selection_changed(self, selection):
        """Met à jour l'aperçu de rendu pour la ligne d'inventaire sélectionnée.

        Args:
            selection: Sélection GTK de la liste d'inventaire.
        """
        model, it = selection.get_selected()
        if it is None:
            return
        idx = model[it][C_IDX]
        row = self.rows[idx]
        logger.debug(f"SnmpPushTab._on_inventory_selection_changed | idx={idx} ip={row.ip}")
        context = row.variables
        missing = find_missing_variables(self.template_text, set(context))
        if missing:
            self._lbl_preview_warn.set_text(
                _("⚠ Variables absentes du CSV : {v}").format(v=", ".join(missing))
            )
        else:
            self._lbl_preview_warn.set_text("")
        self._preview_buffer.set_text(render_template(self.template_text, context))

    def _selected_rows(self) -> list[InventoryRow]:
        """Retourne les lignes d'inventaire actuellement cochées.

        Returns:
            Liste des ``InventoryRow`` cochées dans le ``Gtk.ListStore``.
        """
        logger.debug("SnmpPushTab._selected_rows")
        out = []
        for r in self._inv_store:
            if r[C_SEL]:
                out.append(self.rows[r[C_IDX]])
        return out

    def _on_validate(self, _btn):
        """Valide à blanc l'inventaire (profils connus, variables complètes).

        Args:
            _btn: Bouton GTK à l'origine du signal (non utilisé).
        """
        if not self.rows:
            self._lbl_config_status.set_text(_("Chargez d'abord un inventaire."))
            return
        logger.debug("SnmpPushTab._on_validate")
        n_ok = 0
        n_err = 0
        for r in self._inv_store:
            row = self.rows[r[C_IDX]]
            profile = self.profiles.get(row.profile_name)
            if profile is None:
                r[C_VARS] = _("⚠ profil inconnu : {p}").format(p=row.profile_name)
                n_err += 1
                continue
            missing = find_missing_variables(self.template_text, set(row.variables))
            if missing:
                r[C_VARS] = _("⚠ variables manquantes : {v}").format(v=", ".join(missing))
                n_err += 1
                continue
            n_ok += 1
        server_ip = self._current_server_ip()
        if not server_ip:
            self._lbl_config_status.set_text(
                _("⚠ Renseignez le serveur de dépôt avant de lancer.")
            )
            return
        self._lbl_config_status.set_text(
            _("Validation : {ok} prêt(s), {err} en erreur.").format(ok=n_ok, err=n_err)
        )

    # ══════════════════════════════════════════════════════════════════
    # Phase 2 : Suivi
    # ══════════════════════════════════════════════════════════════════

    def _build_monitor_page(self) -> Gtk.Widget:
        """Construit la page de la phase 2 (Suivi en temps réel)."""
        logger.debug("SnmpPushTab._build_monitor_page")
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
        """Lance le déploiement SNMP en arrière-plan pour les lignes cochées.

        Args:
            _btn: Bouton GTK à l'origine du signal (non utilisé).
        """
        if self._running:
            return
        if not self.rows:
            self._lbl_config_status.set_text(_("Chargez d'abord un inventaire."))
            return
        selected = self._selected_rows()
        if not selected:
            self._lbl_config_status.set_text(_("Aucune ligne cochée."))
            return
        server_ip = self._current_server_ip()
        if not server_ip:
            self._lbl_config_status.set_text(
                _("⚠ Renseignez le serveur de dépôt avant de lancer.")
            )
            return

        logger.debug(f"SnmpPushTab._on_launch | n_selected={len(selected)} server_ip={server_ip}")
        self._log_dir = (
            Path.home() / ".local" / "share" / "gcm" / "snmp_push" / time.strftime("%Y%m%d_%H%M%S")
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

        def on_row_start(row: InventoryRow):
            """Marque la ligne comme « en cours » dans le tableau de suivi.

            Args:
                row: Ligne d'inventaire dont l'envoi démarre.
            """
            logger.debug(f"_on_launch.on_row_start | ip={row.ip}")
            GLib.idle_add(self._set_monitor_status, row.line_no, _STATUS_LABELS["running"], "", "")

        def on_row_done(result: PushResult):
            """Reporte le résultat d'un envoi dans le tableau de suivi.

            Args:
                result: Résultat du push pour un équipement.
            """
            logger.debug(f"_on_launch.on_row_done | ip={result.row.ip} status={result.status}")
            GLib.idle_add(self._handle_row_done, result)

        def worker():
            """Exécute le déploiement en masse dans un thread dédié."""
            logger.debug("_on_launch.worker | start")
            try:
                run_bulk_push(
                    selected,
                    self.profiles,
                    self.template_text,
                    log_dir=self._log_dir,
                    max_workers=max_workers,
                    on_row_start=on_row_start,
                    on_row_done=on_row_done,
                    cancel_event=self._cancel_event,
                    server_ip=server_ip,
                )
            finally:
                GLib.idle_add(self._on_run_finished)

        threading.Thread(target=worker, daemon=True).start()

    def _on_cancel_run(self, _btn):
        """Demande l'annulation du déploiement en cours.

        Args:
            _btn: Bouton GTK à l'origine du signal (non utilisé).
        """
        logger.debug("SnmpPushTab._on_cancel_run")
        self._cancel_event.set()
        self._btn_cancel_run.set_sensitive(False)

    def _set_monitor_status(self, line_no, status_text, detail, duration_text):
        """Met à jour la ligne de suivi correspondant à *line_no*.

        Args:
            line_no: Numéro de ligne CSV identifiant l'équipement.
            status_text: Libellé de statut déjà traduit à afficher.
            detail: Message de détail (erreur ou info) à afficher.
            duration_text: Durée écoulée, déjà formatée en texte.

        Returns:
            ``False`` (convention ``GLib.idle_add`` : ne pas répéter l'appel).
        """
        it = self._monitor_store_by_line.get(line_no)
        if it is None:
            return False
        logger.debug(f"SnmpPushTab._set_monitor_status | line_no={line_no} status={status_text}")
        self._monitor_store[it][R_STATUS] = status_text
        self._monitor_store[it][R_DETAIL] = detail
        self._monitor_store[it][R_DURATION] = duration_text
        return False

    def _handle_row_done(self, result: PushResult):
        """Enregistre le résultat d'un envoi et met à jour suivi/compteurs.

        Args:
            result: Résultat du push pour un équipement.

        Returns:
            ``False`` (convention ``GLib.idle_add`` : ne pas répéter l'appel).
        """
        logger.debug(f"SnmpPushTab._handle_row_done | ip={result.row.ip} status={result.status}")
        self._results_by_line[result.row.line_no] = result
        detail = result.error
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
        """Met à jour les compteurs OK/Rejetés/Échecs/En attente affichés."""
        logger.debug("SnmpPushTab._update_counts")
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
        """Termine la phase de suivi et bascule sur la page des résultats.

        Returns:
            ``False`` (convention ``GLib.idle_add`` : ne pas répéter l'appel).
        """
        logger.debug("SnmpPushTab._on_run_finished")
        self._running = False
        self._btn_cancel_run.set_sensitive(False)
        self._btn_goto_results.set_sensitive(True)
        self._populate_results()
        return False

    # ══════════════════════════════════════════════════════════════════
    # Phase 3 : Résultats
    # ══════════════════════════════════════════════════════════════════

    def _build_results_page(self) -> Gtk.Widget:
        """Construit la page de la phase 3 (Résultats et journaux)."""
        logger.debug("SnmpPushTab._build_results_page")
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
        """Remplit la liste des résultats depuis ``self._results_by_line``."""
        logger.debug("SnmpPushTab._populate_results")
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
        """Affiche le journal du résultat sélectionné dans la liste.

        Args:
            selection: Sélection GTK de la liste de résultats.
        """
        model, it = selection.get_selected()
        if it is None:
            return
        log_path = model[it][RES_LOGPATH]
        logger.debug(f"SnmpPushTab._on_result_selected | log_path={log_path}")
        if not log_path:
            self._log_buffer.set_text("")
            return
        try:
            content = Path(log_path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("snmp push | journal {} illisible : {}", log_path, exc)
            content = _("Impossible de lire le journal : {e}").format(e=exc)
        self._log_buffer.set_text(content)

    def _on_open_log_dir(self, _btn):
        """Ouvre le dossier des journaux dans le gestionnaire de fichiers.

        Args:
            _btn: Bouton GTK à l'origine du signal (non utilisé).
        """
        if self._log_dir is None:
            return
        logger.debug(f"SnmpPushTab._on_open_log_dir | log_dir={self._log_dir}")
        try:
            subprocess.Popen(["xdg-open", str(self._log_dir)])
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"xdg-open a échoué : {exc}")


# ══════════════════════════════════════════════════════════════════════
# Point d'entrée BatchPlugin (autoload par BatchPluginRegistry)
# ══════════════════════════════════════════════════════════════════════


class SnmpPushBatchPlugin(BatchPlugin):
    """Adapte ``open_snmp_push_tab`` au contrat ``BatchPlugin``.

    Découvert et enregistré automatiquement par
    ``BatchPluginRegistry.autoload()`` — voir ``plugin_base.py`` et le
    docstring de module ci-dessus.
    """

    tool_id = "snmp-push"
    display_name = _("Déploiement SNMP…")
    icon_name = "network-transmit-receive-symbolic"

    def activate(self) -> None:
        """Ouvre (ou ramène le focus sur) l'onglet Push SNMP."""
        logger.debug("SnmpPushBatchPlugin.activate")
        if self.app is None:
            raise RuntimeError(
                "SnmpPushBatchPlugin.activate() appelé sans app liée "
                "(BatchPluginRegistry.bind_app() n'a pas été appelé)"
            )
        open_snmp_push_tab(self.app)


def get_batch_plugin() -> SnmpPushBatchPlugin:
    """Point d'entrée de découverte pour ``BatchPluginRegistry.autoload()``."""
    logger.debug("get_batch_plugin")
    return SnmpPushBatchPlugin()
