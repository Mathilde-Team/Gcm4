# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin de connexion série (port local via picocom/minicom/screen).

Troisième plugin implémenté après Web et IPMI SOL.

Deux bugs pré-existants trouvés en portant cette logique depuis
``Whost.on_serialCmbTemplate_changed`` (gnome_connection_manager.py) —
corrigés ici, PAS dans le code historique (qui reste le fallback du flag,
inchangé) :

1. Les ids des templates dans le combo glade (``cisco``, ``h3c``,
   ``juniper``, ``aruba``, ``bmc``, ``custom``) ne correspondent PAS aux
   clés du dict ``_SERIAL_TEMPLATES`` (des noms complets comme
   ``"Cisco IOS / IOS-XE / NX-OS"``) — la recherche ``dict.get(tpl_id)``
   échoue donc toujours et la fonction ne fait jamais rien. Le
   pré-remplissage par template est actuellement une fonctionnalité morte.
2. Même si la clé avait matché, l'ordre de dépaquetage du tuple
   (``baud, db, par, sb, fl = tpl``) ne correspond pas à l'ordre documenté
   du format (``baud, flow, parity, databits, stopbits``) : databits et
   stopbits/flow se retrouveraient permutés.
"""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Callable
from gettext import gettext as _

import gi
from loguru import logger

gi.require_version("Vte", "2.91")
from gi.repository import Gtk, Vte
from plugin_base import ConnectionPlugin

from widgets import app_logger, vte_run

__all__ = ["SerialPlugin", "SerialTab"]

# Détection du binaire serial disponible (picocom > minicom > screen)
SERIAL_BIN = (
    shutil.which("picocom") or shutil.which("minicom") or shutil.which("screen") or "picocom"
)

# Templates série : (débit, flow, parity, databits, stopbits)
#   flow : n=none  x=xon/xoff  h=rts/cts
#   parity : n=none  e=even  o=odd
# Templates série par défaut (immuables — utilisés comme fallback).
_SERIAL_TEMPLATES_DEFAULT = {
    "Cisco IOS / IOS-XE / NX-OS": ("9600", "n", "n", "8", "1"),
    "HP Comware (H3C)": ("9600", "n", "n", "8", "1"),
    "Aruba AOS-S / AOS-CX": ("9600", "n", "n", "8", "1"),
    "Juniper JunOS": ("9600", "n", "n", "8", "1"),
    "Fortinet FortiOS": ("9600", "n", "n", "8", "1"),
    "Palo Alto PAN-OS": ("9600", "n", "n", "8", "1"),
    "F5 TMOS": ("19200", "n", "n", "8", "1"),
    "Linux / Raspberry Pi": ("115200", "n", "n", "8", "1"),
    "Arduino / ESP32": ("115200", "n", "n", "8", "1"),
    "RS-485 Modbus RTU": ("9600", "n", "n", "8", "1"),
    "Libre (manuel)": ("9600", "n", "n", "8", "1"),
}
# Copie mutable chargée au démarrage (peut être étendue / modifiée par l'user).
_SERIAL_TEMPLATES = dict(_SERIAL_TEMPLATES_DEFAULT)


# (baud, databits, parity, stopbits, flow) — valeurs conformes aux libellés
# du combo glade serialCmbTemplate (ex. "Cisco IOS (9600 8N1)").
_TEMPLATES: dict[str, tuple[str, str, str, str, str]] = {
    "cisco": ("9600", "8", "n", "1", "n"),
    "h3c": ("9600", "8", "n", "1", "n"),
    "juniper": ("9600", "8", "n", "1", "n"),
    "aruba": ("9600", "8", "n", "1", "n"),
    "bmc": ("115200", "8", "n", "1", "n"),
}

_BAUD_RATES = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]
_DATABITS = ["5", "6", "7", "8"]
_PARITY = [("n", _("None")), ("e", _("Even")), ("o", _("Odd"))]
_STOPBITS = ["1", "2"]
_FLOW = [
    ("n", _("None")),
    ("x", _("XON/XOFF (software)")),
    ("h", _("RTS/CTS (hardware)")),
    ("d", _("DTR/DSR")),
]
_TOOLS = ["picocom", "minicom", "screen"]
_TEMPLATE_ITEMS = [
    ("custom", _("— custom —")),
    ("cisco", _("Cisco IOS (9600 8N1)")),
    ("h3c", _("H3C/HPE Comware (9600 8N1)")),
    ("juniper", _("Juniper (9600 8N1)")),
    ("aruba", _("Aruba (9600 8N1)")),
    ("bmc", _("BMC/iDRAC (115200 8N1)")),
]


def _cmb_set_id(cmb: Gtk.ComboBoxText | None, item_id: str) -> None:
    """Sélectionne l'item d'un GtkComboBoxText par son id.

    Args:
        cmb: Combo cible (peut être ``None``).
        item_id: Id de l'item à sélectionner.
    """
    if cmb is not None:
        cmb.set_active_id(item_id)


def _cmb_get_id(cmb: Gtk.ComboBoxText | None, default: str = "") -> str:
    """Retourne l'id actif d'un GtkComboBoxText.

    Args:
        cmb: Combo source (peut être ``None``).
        default: Valeur retournée si rien n'est sélectionné.
    """
    if cmb is None:
        return default
    active_id = cmb.get_active_id()
    return active_id if active_id is not None else default


class SerialTab(Gtk.Box):
    """Onglet de connexion série embarquant un terminal VTE.

    Lance picocom (ou minicom / screen) dans le widget VTE intégré.
    Supporte des templates constructeur (Cisco, HP Comware, Aruba…).

    Paramètres série complets exposés dans l'UI :
        - Port série (/dev/ttyUSBx, /dev/ttySx…)
        - Débit (baud rate) : 300 → 921600
        - Bits de données : 5 / 6 / 7 / 8
        - Parité : Aucune / Paire / Impaire
        - Bits de stop : 1 / 2
        - Contrôle de flux : Aucun / XON-XOFF / RTS-CTS / DSR-DTR

    Args:
        host (Host): Hôte avec protocol='serial'.
            - host.host         : chemin du port
            - host.port         : débit en bauds
            - host.extra_params : options libres supplémentaires
            - host.type         : clé template
    """

    # Correspondances valeur interne → label UI
    _FLOW_LABELS = [
        ("n", "None"),
        ("x", "XON/XOFF (soft)"),
        ("h", "RTS/CTS (hard)"),
        ("d", "DSR/DTR"),
    ]
    _PARITY_LABELS = [
        ("n", "Aucune (N)"),
        ("e", "Paire (E)"),
        ("o", "Impaire (O)"),
    ]
    _DATABITS_VALUES = ("5", "6", "7", "8")
    _STOPBITS_VALUES = ("1", "2")

    def __init__(self, host):
        """Initialise le widget SerialTab."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.host = host

        # ── ligne 1 : appareil + débit + template ───────────────────────────
        hb1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hb1.set_margin_start(6)
        hb1.set_margin_end(6)
        hb1.set_margin_top(4)

        # Port série
        lbl_dev = Gtk.Label(label=_("Serial port:"))
        lbl_dev.set_xalign(1.0)
        self._entry_dev = Gtk.Entry()
        self._entry_dev.set_text(getattr(host, "host", "/dev/ttyUSB0") or "/dev/ttyUSB0")
        self._entry_dev.set_tooltip_text(_("Ex : /dev/ttyUSB0, /dev/ttyS0, /dev/ttyACM0"))
        self._entry_dev.set_hexpand(True)

        # Débit (baud rate)
        lbl_baud = Gtk.Label(label=_("Baud rate:"))
        lbl_baud.set_xalign(1.0)
        self._cmb_baud = Gtk.ComboBoxText()
        for b in (
            "300",
            "1200",
            "2400",
            "4800",
            "9600",
            "19200",
            "38400",
            "57600",
            "115200",
            "230400",
            "460800",
            "921600",
        ):
            self._cmb_baud.append_text(b)
        self._cmb_baud.set_tooltip_text(_("Transmission speed in baud"))
        # serial_baud prioritaire sur port (héritage historique)
        baud = str(getattr(host, "serial_baud", "") or getattr(host, "port", "9600") or "9600")
        self._cmb_select(self._cmb_baud, baud, 4)  # défaut 9600 (idx 4)

        # Template constructeur
        lbl_tpl = Gtk.Label(label=_("Template:"))
        lbl_tpl.set_xalign(1.0)
        self._cmb_tpl = Gtk.ComboBoxText()
        for tpl_name in _SERIAL_TEMPLATES:
            self._cmb_tpl.append_text(tpl_name)
        self._cmb_tpl.set_tooltip_text(_("Load vendor-recommended serial settings"))
        tpl_saved = getattr(host, "type", "") or ""
        tpl_list = list(_SERIAL_TEMPLATES.keys())
        tpl_idx = tpl_list.index(tpl_saved) if tpl_saved in tpl_list else 10
        self._cmb_tpl.set_active(tpl_idx)
        self._cmb_tpl.connect("changed", self._on_template_changed)

        for w in (
            lbl_dev,
            self._entry_dev,
            lbl_baud,
            self._cmb_baud,
            lbl_tpl,
            self._cmb_tpl,
        ):
            hb1.pack_start(w, False, False, 0)

        # ── ligne 2 : paramètres série (databits, parity, stopbits, flow) ──
        hb2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hb2.set_margin_start(6)
        hb2.set_margin_end(6)

        # Bits de données
        lbl_data = Gtk.Label(label=_("Data bits:"))
        lbl_data.set_xalign(1.0)
        self._cmb_databits = Gtk.ComboBoxText()
        for d in self._DATABITS_VALUES:
            self._cmb_databits.append_text(d)
        self._cmb_databits.set_tooltip_text(_("Number of data bits per byte (8 = standard)"))
        _db = str(getattr(host, "serial_databits", "8") or "8")
        _db_idx = list(self._DATABITS_VALUES).index(_db) if _db in self._DATABITS_VALUES else 3
        self._cmb_databits.set_active(_db_idx)

        # Parité
        lbl_par = Gtk.Label(label=_("Parity:"))
        lbl_par.set_xalign(1.0)
        self._cmb_parity = Gtk.ComboBoxText()
        for _code, _label in self._PARITY_LABELS:
            self._cmb_parity.append_text(_label)
        self._cmb_parity.set_tooltip_text(_("Parity bit: None (N) = standard, Even (E), Odd (O)"))
        _par = str(getattr(host, "serial_parity", "n") or "n")
        _par_codes = [c for c, _ in self._PARITY_LABELS]
        self._cmb_parity.set_active(_par_codes.index(_par) if _par in _par_codes else 0)

        # Bits de stop
        lbl_stop = Gtk.Label(label=_("Stop bits:"))
        lbl_stop.set_xalign(1.0)
        self._cmb_stopbits = Gtk.ComboBoxText()
        for s in self._STOPBITS_VALUES:
            self._cmb_stopbits.append_text(s)
        self._cmb_stopbits.set_tooltip_text(
            _("Stop bits: 1 = standard, 2 = RS-485 / legacy hardware")
        )
        _sb = str(getattr(host, "serial_stopbits", "1") or "1")
        _sb_idx = list(self._STOPBITS_VALUES).index(_sb) if _sb in self._STOPBITS_VALUES else 0
        self._cmb_stopbits.set_active(_sb_idx)

        # Contrôle de flux
        lbl_flow = Gtk.Label(label=_("Flow control:"))
        lbl_flow.set_xalign(1.0)
        self._cmb_flow = Gtk.ComboBoxText()
        for _code, _label in self._FLOW_LABELS:
            self._cmb_flow.append_text(_label)
        self._cmb_flow.set_tooltip_text(
            _(
                "Flow control:\n"
                "  None = standard console\n"
                "  XON/XOFF = software (Ctrl-Q/Ctrl-S)\n"
                "  RTS/CTS = hardware (full cable)\n"
                "  DSR/DTR = legacy hardware"
            )
        )
        _fl = str(getattr(host, "serial_flow", "n") or "n")
        _fl_codes = [c for c, _ in self._FLOW_LABELS]
        self._cmb_flow.set_active(_fl_codes.index(_fl) if _fl in _fl_codes else 0)

        # Options libres
        lbl_opts = Gtk.Label(label=_("xfreerdp options:"))
        lbl_opts.set_xalign(1.0)
        self._entry_opts = Gtk.Entry()
        self._entry_opts.set_text(getattr(host, "extra_params", "") or "")
        self._entry_opts.set_tooltip_text(
            _(
                "Additional raw options passed to the binary\n"
                "Ex: --logfile /tmp/serial.log  (picocom)\n"
                "     -o -x  (minicom)"
            )
        )

        # Boutons
        self._btn_connect = Gtk.Button(label=_("Connect"))
        self._btn_connect.connect("clicked", self._on_connect)
        self._btn_disconnect = Gtk.Button(label=_("Disconnect"))
        self._btn_disconnect.set_sensitive(False)
        self._btn_disconnect.connect("clicked", self._on_disconnect)

        for w in (
            lbl_data,
            self._cmb_databits,
            lbl_par,
            self._cmb_parity,
            lbl_stop,
            self._cmb_stopbits,
            lbl_flow,
            self._cmb_flow,
            lbl_opts,
            self._entry_opts,
            self._btn_connect,
            self._btn_disconnect,
        ):
            hb2.pack_start(w, False, False, 0)

        # ── infos + terminal ────────────────────────────────────────────────
        lbl_bin = Gtk.Label()
        lbl_bin.set_markup(f"<small><i>Detected binary: {SERIAL_BIN}</i></small>")
        lbl_bin.set_xalign(0.0)
        lbl_bin.set_margin_start(6)

        self._lbl_status = Gtk.Label(label=_("Disconnect"))
        self._lbl_status.set_xalign(0.0)
        self._lbl_status.set_margin_start(6)

        self._terminal = Vte.Terminal()
        self._terminal.set_scrollback_lines(5000)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(self._terminal)
        sw.set_vexpand(True)
        sw.set_hexpand(True)

        self.pack_start(hb1, False, False, 0)
        self.pack_start(hb2, False, False, 0)
        self.pack_start(lbl_bin, False, False, 0)
        self.pack_start(self._lbl_status, False, False, 0)
        self.pack_start(sw, True, True, 0)
        self.show_all()

        # Appliquer le template après construction de l'UI
        if tpl_saved in _SERIAL_TEMPLATES:
            self._apply_template(tpl_saved)

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _cmb_select(cmb, value, default_idx=0):
        """Sélectionne l'entrée dont le texte == value, sinon default_idx."""
        model = cmb.get_model()
        it = model.get_iter_first()
        while it is not None:
            if model[it][0] == value:
                cmb.set_active_iter(it)
                return
            it = model.iter_next(it)
        cmb.set_active(default_idx)

    def _flow_code(self):
        idx = self._cmb_flow.get_active()
        return self._FLOW_LABELS[idx][0] if 0 <= idx < len(self._FLOW_LABELS) else "n"

    def _parity_code(self):
        idx = self._cmb_parity.get_active()
        return self._PARITY_LABELS[idx][0] if 0 <= idx < len(self._PARITY_LABELS) else "n"

    def _databits(self):
        return self._cmb_databits.get_active_text() or "8"

    def _stopbits(self):
        return self._cmb_stopbits.get_active_text() or "1"

    # ── template ────────────────────────────────────────────────────────────

    def _apply_template(self, name):
        """Applique les paramètres d'un template aux combos de l'UI."""
        if name not in _SERIAL_TEMPLATES:
            return
        baud, flow, parity, databits, stopbits = _SERIAL_TEMPLATES[name]
        self._cmb_select(self._cmb_baud, baud, 4)
        # flow
        flow_codes = [c for c, _ in self._FLOW_LABELS]
        self._cmb_flow.set_active(flow_codes.index(flow) if flow in flow_codes else 0)
        # parity
        parity_codes = [c for c, _ in self._PARITY_LABELS]
        self._cmb_parity.set_active(parity_codes.index(parity) if parity in parity_codes else 0)
        # databits / stopbits
        self._cmb_select(self._cmb_databits, databits, 3)
        self._cmb_select(self._cmb_stopbits, stopbits, 0)
        self.host.type = name

    def _on_template_changed(self, cmb):
        """Applique le template sélectionné sur tous les combos."""
        name = cmb.get_active_text()
        self._apply_template(name)

    # ── construction de la commande ─────────────────────────────────────────

    def _build_cmd(self):
        """Construit la liste d'arguments pour le sous-processus série.

        Les paramètres (débit, databits, parity, stopbits, flow) sont lus
        directement depuis les combos dédiés, pas depuis une chaîne brute.

        Returns:
            list[str]: Commande prête pour vte_run().
        """
        device = self._entry_dev.get_text().strip() or "/dev/ttyUSB0"
        baud = self._cmb_baud.get_active_text() or "9600"
        flow = self._flow_code()
        parity = self._parity_code()
        databits = self._databits()
        stopbits = self._stopbits()
        extra = self._entry_opts.get_text().strip()

        # Résolution du binaire : serial_tool prioritaire sur la détection globale
        _TOOL_BINS = {
            "picocom": shutil.which("picocom"),
            "minicom": shutil.which("minicom"),
            "screen": shutil.which("screen"),
        }
        tool_id = (getattr(self.host, "serial_tool", "") or "").strip()
        serial_bin = (_TOOL_BINS.get(tool_id) or SERIAL_BIN) if tool_id else SERIAL_BIN
        bin_name = os.path.basename(serial_bin)

        if bin_name == "picocom":
            cmd = [
                serial_bin,
                "--baud",
                baud,
                "--flow",
                flow,
                "--parity",
                parity,
                "--databits",
                databits,
                "--stopbits",
                stopbits,
            ]
            if extra:
                cmd += shlex.split(extra)
            cmd.append(device)
        elif bin_name == "minicom":
            # minicom : -b baud  -D device  --databits  --stopbits
            # flow via --8bit / -f (xon) / --rtscts (rts/cts)
            cmd = [
                serial_bin,
                "-b",
                baud,
                "-D",
                device,
                "--databits",
                databits,
                "--stopbits",
                stopbits,
            ]
            if flow == "x":
                cmd += ["-f", "on"]
            elif flow == "h":
                cmd += ["--rtscts"]
            if extra:
                cmd += shlex.split(extra)
        else:  # screen : device baud [databits[parity[stopbits]]]
            # screen accepte 8n1, 7e1, etc. comme troisième argument
            parity_map = {"n": "n", "e": "e", "o": "o"}
            combo = f"{databits}{parity_map.get(parity, 'n')}{stopbits}"
            cmd = [serial_bin, device, baud, combo]
            if extra:
                cmd += shlex.split(extra)
        app_logger.debug(f"Serial: command = {cmd}")
        return cmd

    # ── connexion / déconnexion ──────────────────────────────────────────────

    def _on_connect(self, widget):
        """Lance la session série dans le terminal VTE."""
        self.host.extra_params = self._entry_opts.get_text().strip()
        # Persister dans les attributs structurés (serial_baud, etc.)
        baud = self._cmb_baud.get_active_text() or "9600"
        self.host.serial_baud = baud
        self.host.serial_databits = self._databits()
        self.host.serial_parity = self._parity_code()
        self.host.serial_stopbits = self._stopbits()
        self.host.serial_flow = self._flow_code()
        # Compatibilité : port = baud pour les anciens configs
        self.host.port = baud
        self.host.host = self._entry_dev.get_text().strip()
        cmd = self._build_cmd()
        if not cmd:
            return
        self._lbl_status.set_text(_("Connecting…"))
        self._btn_connect.set_sensitive(False)
        self._btn_disconnect.set_sensitive(True)
        vte_run(self._terminal, cmd[0], cmd[1:])

    def _on_disconnect(self, widget):
        """Envoie SIGTERM au processus fils du VTE."""
        try:
            pid = self._terminal.get_pty().get_fd()
            if pid and pid > 0:
                import signal as _signal

                os.kill(pid, _signal.SIGTERM)
        except Exception as exc:
            logger.debug("plugin serial | arrêt du processus série impossible : {}", exc)
        self._lbl_status.set_text(_("Disconnect"))
        self._btn_connect.set_sensitive(True)
        self._btn_disconnect.set_sensitive(False)

    def connect_serial(self):
        """Démarre la session série automatiquement (appelé depuis addTab)."""
        self._on_connect(None)


class SerialPlugin(ConnectionPlugin):
    """Plugin pour le protocole ``serial`` (port série local)."""

    protocol_id = "serial"
    display_name = _("Serial")
    default_port = None
    icon_name = "utilities-terminal-symbolic"
    ui_order = 5

    def __init__(self) -> None:
        """Initialise les références de widgets du formulaire série (peuplées par ``build_edit_page()``)."""
        self._cmb_template: Gtk.ComboBoxText | None = None
        self._cmb_baud: Gtk.ComboBoxText | None = None
        self._cmb_databits: Gtk.ComboBoxText | None = None
        self._cmb_parity: Gtk.ComboBoxText | None = None
        self._cmb_stopbits: Gtk.ComboBoxText | None = None
        self._cmb_flow: Gtk.ComboBoxText | None = None
        self._cmb_tool: Gtk.ComboBoxText | None = None

    def build_tab(self, host, get_password: Callable[[], str] | None = None) -> Gtk.Widget:
        """Construit l'onglet de session série (délègue à ``SerialTab``).

        Args:
            host: Instance ``Host`` avec ``protocol == "serial"``.
            get_password: Non utilisé (pas d'authentification série).

        Returns:
            Une instance de ``SerialTab`` (inchangée).
        """
        logger.debug(f"SerialPlugin.build_tab | host={host.name}")
        return SerialTab(host)

    def build_edit_page(self) -> Gtk.Widget:
        """Construit la page de champs Edit Host pour le protocole série.

        Reproduit ``gridSerialProps`` (7 combos) en Python pur, avec un
        pré-remplissage par template fonctionnel (cf. bugs documentés en
        tête de module).

        Returns:
            Un ``Gtk.Grid`` autonome.
        """
        grid = Gtk.Grid()
        grid.set_row_homogeneous(True)
        grid.set_row_spacing(4)
        grid.set_column_spacing(6)
        grid.set_margin_start(8)
        grid.set_margin_end(8)
        grid.set_margin_top(8)

        rows: list[tuple[str, Gtk.ComboBoxText, list]] = []

        self._cmb_template = Gtk.ComboBoxText()
        for item_id, label in _TEMPLATE_ITEMS:
            self._cmb_template.append(item_id, label)
        self._cmb_template.set_active_id("custom")
        self._cmb_template.connect("changed", self._on_template_changed)
        rows.append((_("Template"), self._cmb_template, None))

        self._cmb_baud = Gtk.ComboBoxText()
        for v in _BAUD_RATES:
            self._cmb_baud.append(v, v)
        self._cmb_baud.set_active_id("9600")
        rows.append((_("Baud rate"), self._cmb_baud, None))

        self._cmb_databits = Gtk.ComboBoxText()
        for v in _DATABITS:
            self._cmb_databits.append(v, v)
        self._cmb_databits.set_active_id("8")
        rows.append((_("Data bits"), self._cmb_databits, None))

        self._cmb_parity = Gtk.ComboBoxText()
        for item_id, label in _PARITY:
            self._cmb_parity.append(item_id, label)
        self._cmb_parity.set_active_id("n")
        rows.append((_("Parity"), self._cmb_parity, None))

        self._cmb_stopbits = Gtk.ComboBoxText()
        for v in _STOPBITS:
            self._cmb_stopbits.append(v, v)
        self._cmb_stopbits.set_active_id("1")
        rows.append((_("Stop bits"), self._cmb_stopbits, None))

        self._cmb_flow = Gtk.ComboBoxText()
        for item_id, label in _FLOW:
            self._cmb_flow.append(item_id, label)
        self._cmb_flow.set_active_id("n")
        rows.append((_("Flow control"), self._cmb_flow, None))

        self._cmb_tool = Gtk.ComboBoxText()
        for v in _TOOLS:
            self._cmb_tool.append(v, v)
        self._cmb_tool.set_active_id("picocom")
        rows.append((_("Tool"), self._cmb_tool, None))

        for row_idx, (label_text, widget, _unused) in enumerate(rows):
            lbl = Gtk.Label(label=label_text)
            lbl.set_halign(Gtk.Align.START)
            grid.attach(lbl, 0, row_idx, 1, 1)
            widget.set_margin_start(10)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row_idx, 1, 1)

        grid.show_all()
        return grid

    def _on_template_changed(self, widget: Gtk.ComboBoxText) -> None:
        """Pré-remplit les combos selon le template constructeur choisi.

        Args:
            widget: Combo ``serialCmbTemplate`` (celui qui a changé).
        """
        tpl_id = _cmb_get_id(widget, "custom")
        tpl = _TEMPLATES.get(tpl_id)
        if tpl is None:
            return  # "custom" (ou id inconnu) : on ne touche à rien
        baud, databits, parity, stopbits, flow = tpl
        _cmb_set_id(self._cmb_baud, baud)
        _cmb_set_id(self._cmb_databits, databits)
        _cmb_set_id(self._cmb_parity, parity)
        _cmb_set_id(self._cmb_stopbits, stopbits)
        _cmb_set_id(self._cmb_flow, flow)

    def load_host_fields(self, host) -> None:
        """Recharge les 6 combos depuis *host* (le template reste "custom").

        Args:
            host: Instance ``Host`` source.
        """
        _cmb_set_id(self._cmb_baud, getattr(host, "serial_baud", "9600") or "9600")
        _cmb_set_id(self._cmb_databits, getattr(host, "serial_databits", "8") or "8")
        _cmb_set_id(self._cmb_parity, getattr(host, "serial_parity", "n") or "n")
        _cmb_set_id(self._cmb_stopbits, getattr(host, "serial_stopbits", "1") or "1")
        _cmb_set_id(self._cmb_flow, getattr(host, "serial_flow", "n") or "n")
        _cmb_set_id(self._cmb_tool, getattr(host, "serial_tool", "picocom") or "picocom")
        _cmb_set_id(self._cmb_template, "custom")
        logger.debug(f"SerialPlugin.load_host_fields | host={getattr(host, 'name', '?')}")

    def save_host_fields(self, host) -> None:
        """Reporte les 6 combos dans *host* (le template n'est pas persisté).

        Args:
            host: Instance ``Host`` à mettre à jour.
        """
        host.serial_baud = _cmb_get_id(self._cmb_baud, "9600")
        host.serial_databits = _cmb_get_id(self._cmb_databits, "8")
        host.serial_parity = _cmb_get_id(self._cmb_parity, "n")
        host.serial_stopbits = _cmb_get_id(self._cmb_stopbits, "1")
        host.serial_flow = _cmb_get_id(self._cmb_flow, "n")
        host.serial_tool = _cmb_get_id(self._cmb_tool, "picocom")
        logger.debug(f"SerialPlugin.save_host_fields | host={getattr(host, 'name', '?')}")

    def host_fields(self) -> list[tuple[str, object]]:
        """Champs ``Host`` propres au protocole série."""
        return [
            ("serial_databits", "8"),
            ("serial_baud", "9600"),
            ("serial_parity", "n"),
            ("serial_stopbits", "1"),
            ("serial_flow", "n"),
            ("serial_tool", "picocom"),
        ]


def get_plugin() -> SerialPlugin:
    """Point d'entree standard utilise par PluginRegistry.autoload().

    Returns:
        Une instance de SerialPlugin, prete a etre enregistree.
    """
    return SerialPlugin()
