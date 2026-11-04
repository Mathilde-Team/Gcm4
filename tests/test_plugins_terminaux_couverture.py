# SPDX-License-Identifier: GPL-3.0-or-later
"""Onglets terminal hérités série, Telnet et local (#293, palier 8).

``plugin_serial``, ``plugin_telnet`` et ``plugin_local`` (GTK3) construisent
un ``Vte.Terminal``, une commande, puis programment l'envoi du mot de passe
et des commandes post-connexion (``##D=<ms>``). Ici, VTE, GLib et
``vte_run`` sont remplacés par des doublures qui enregistrent les appels :
aucun terminal réel, aucun processus lancé, aucun signal envoyé (les
doublures ``gi`` de ``tests/test_gcm.py`` sont réutilisées).
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
)

import test_gcm  # noqa: E402  (pose les doublures gi/Gtk avant l'import des plugins)

# isort: split
import plugin_local  # noqa: E402
import plugin_serial  # noqa: E402
import plugin_telnet  # noqa: E402

gcm = test_gcm.gcm


class Combo:
    """Doublure de ``Gtk.ComboBoxText`` : ids, texte actif, signaux."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialise la doublure."""
        self.items: list[tuple[str, str]] = []
        self.active_id: str | None = None
        self.signaux: dict[str, object] = {}

    def append(self, item_id: str, label: str) -> None:
        """Simule ``append``."""
        self.items.append((item_id, label))

    def append_text(self, label: str) -> None:
        """Simule ``append_text``."""
        self.items.append((label, label))

    def set_active_id(self, item_id: str) -> None:
        """Simule ``set_active_id``."""
        self.active_id = item_id

    def get_active_id(self) -> str | None:
        """Simule ``get_active_id``."""
        return self.active_id

    def get_active_text(self) -> str | None:
        """Simule ``get_active_text``."""
        return self.active_id

    def connect(self, signal: str, rappel) -> None:
        """Simule ``connect``."""
        self.signaux[signal] = rappel

    def __getattr__(self, nom: str):
        """Méthode GTK sans effet pour la doublure."""
        # set_margin_start, set_hexpand… : sans effet.
        return lambda *a, **k: None


class Grille:
    """Doublure de ``Gtk.Grid`` : garde les widgets attachés."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialise la doublure."""
        self.attaches: list[tuple] = []

    def attach(self, widget, colonne, ligne, largeur, hauteur) -> None:
        """Simule ``attach``."""
        self.attaches.append((widget, colonne, ligne))

    def __getattr__(self, nom: str):
        """Méthode GTK sans effet pour la doublure."""
        return lambda *a, **k: None


class Etiquette:
    """Doublure de ``Gtk.Label``."""

    def __init__(self, label: str = "", **kwargs) -> None:
        """Initialise la doublure."""
        self.label = label

    def __getattr__(self, nom: str):
        """Méthode GTK sans effet pour la doublure."""
        return lambda *a, **k: None


class Terminal:
    """Doublure de ``Vte.Terminal`` : enregistre les réglages."""

    def __init__(self) -> None:
        """Initialise la doublure."""
        self.reglages: dict[str, tuple] = {}
        self.redessine = 0

    def get_vadjustment(self):
        """Simule ``get_vadjustment``."""
        return None

    def queue_draw(self) -> None:
        """Simule ``queue_draw``."""
        self.redessine += 1

    def __getattr__(self, nom: str):
        """Méthode GTK sans effet pour la doublure."""
        if nom.startswith("set_"):
            return lambda *a: self.reglages.__setitem__(nom, a)
        raise AttributeError(nom)


class Couleur:
    """Doublure de ``Gdk.RGBA`` rendue par ``parse_color_rgba``."""

    def __init__(self, texte: str) -> None:
        """Initialise la doublure."""
        self.texte = texte
        self.alpha = 1.0


def _hote(**champs):
    """Hôte minimal pour les onglets terminal."""
    base = {
        "name": "h1",
        "host": "10.0.0.1",
        "port": "23",
        "user": "",
        "password": "",
        "extra_params": "",
        "commands": "",
        "font_color": "",
        "back_color": "",
        "backspace_key": 0,
        "delete_key": 0,
    }
    base.update(champs)
    return types.SimpleNamespace(**base)


@pytest.fixture
def vte(monkeypatch):
    """VTE, GLib, Pango, ``vte_run`` et le module principal en doublures.

    Renvoie l'espace de noms des enregistrements : lancements, minuteries,
    fenêtre principale et configuration.
    """
    enr = types.SimpleNamespace(lancements=[], minuteries=[], terminaux=[])

    def nouveau_terminal():
        """Simule ``nouveau_terminal``."""
        t = Terminal()
        enr.terminaux.append(t)
        return t

    faux_vte = types.SimpleNamespace(Terminal=nouveau_terminal, MAJOR_VERSION=0, MINOR_VERSION=60)
    faux_glib = types.SimpleNamespace(
        timeout_add=lambda ms, f, *a: enr.minuteries.append((ms, f, a))
    )
    faux_pango = types.SimpleNamespace(FontDescription=lambda nom: ("police", nom))
    conf = types.SimpleNamespace(
        WORD_SEPARATORS="-./",
        BUFFER_LINES=5000,
        FONT_COLOR="#FFFFFF",
        BACK_COLOR="#000000",
        FONT="",
        TRANSPARENCY=0,
    )
    wmain = MagicMock()
    wmain.window.transparency = False
    enr.conf, enr.wmain = conf, wmain

    for module in (plugin_local, plugin_telnet):
        monkeypatch.setattr(module, "Vte", faux_vte)
        monkeypatch.setattr(module, "Pango", faux_pango)
        monkeypatch.setattr(
            module, "vte_run", lambda v, cmd, args=None: enr.lancements.append((cmd, args))
        )
        monkeypatch.setattr(module.Gtk, "events_pending", lambda: False, raising=False)
        monkeypatch.setattr(
            module.Gtk, "Scrollbar", types.SimpleNamespace(new=lambda *a: None), raising=False
        )
    monkeypatch.setattr(plugin_telnet, "GLib", faux_glib)
    # D'autres modules de tests retirent ``gi.repository`` de ``sys.modules`` : on le réinstalle le temps du test.
    gi = sys.modules.get("gi") or types.ModuleType("gi")
    depot_gi = (
        sys.modules.get("gi.repository")
        or getattr(gi, "repository", None)
        or types.ModuleType("gi.repository")
    )
    monkeypatch.setattr(gi, "repository", depot_gi, raising=False)
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", depot_gi)
    monkeypatch.setattr(depot_gi, "GLib", faux_glib, raising=False)
    monkeypatch.setattr(gcm, "conf", conf, raising=False)
    monkeypatch.setattr(gcm, "wMain", wmain, raising=False)
    monkeypatch.setattr(gcm, "parse_color_rgba", Couleur, raising=False)
    monkeypatch.setattr(gcm, "DEFAULT_BGCOLOR", "#300A24", raising=False)
    monkeypatch.setattr(gcm, "SHELL", "/bin/bash", raising=False)
    monkeypatch.setattr(gcm, "TEL_BIN", "telnet", raising=False)
    monkeypatch.setattr(gcm, "SSH_COMMAND", "/usr/share/gcm/ssh.expect", raising=False)
    return enr


# ── Construction du terminal (commune à Telnet et Local) ─────────────────────


@pytest.mark.parametrize("onglet", [plugin_telnet.TelnetTab, plugin_local.LocalTab])
def test_terminal_reglages_par_defaut(vte, onglet):
    """Séparateurs, historique, hyperliens, couleurs de la configuration, police par défaut."""
    tab = onglet(_hote())
    r = tab.terminal.reglages
    assert r["set_word_char_exceptions"] == ("-./",)
    assert r["set_scrollback_lines"] == (5000,)
    assert r["set_allow_hyperlink"] == (True,)
    avant, fond, palette = r["set_colors"]
    assert (avant.texte, fond.texte, len(palette)) == ("#FFFFFF", "#000000", 16)
    assert "set_font" not in r
    assert vte.conf.FONT == "monospace"
    assert "set_color_background" not in r
    vte.wmain.registerUrlRegexes.assert_called_once_with(tab.terminal)


@pytest.mark.parametrize("onglet", [plugin_telnet.TelnetTab, plugin_local.LocalTab])
def test_terminal_police_transparence_et_couleurs_d_hote(vte, onglet, monkeypatch):
    """Police configurée, transparence (alpha), couleurs propres à l'hôte ; VTE ancien sans hyperliens."""
    vte.conf.FONT = "Monospace 11"
    vte.conf.TRANSPARENCY = 25
    vte.wmain.window.transparency = True
    monkeypatch.setattr(
        onglet.__module__ and sys.modules[onglet.__module__].Vte, "MINOR_VERSION", 40
    )
    tab = onglet(_hote(font_color="#111111", back_color="#222222", backspace_key=2, delete_key=3))
    r = tab.terminal.reglages
    assert "set_allow_hyperlink" not in r
    assert r["set_font"] == (("police", "Monospace 11"),)
    (fond,) = r["set_color_background"]
    assert (fond.texte, fond.alpha) == ("#222222", 0.75)
    assert r["set_colors"][0].texte == "#111111"
    assert r["set_backspace_binding"] == (2,)
    assert r["set_delete_binding"] == (3,)


@pytest.mark.parametrize("onglet", [plugin_telnet.TelnetTab, plugin_local.LocalTab])
def test_terminal_transparence_sans_couleur_de_fond(vte, onglet):
    """Sans couleur de fond (hôte ni configuration) : fond par défaut transparent, pas de set_colors."""
    vte.conf.BACK_COLOR = ""
    vte.conf.TRANSPARENCY = 50
    vte.wmain.window.transparency = True
    tab = onglet(_hote())
    r = tab.terminal.reglages
    assert "set_colors" not in r
    (fond,) = r["set_color_background"]
    assert (fond.texte, fond.alpha) == ("#300A24", 0.5)


# ── Telnet ───────────────────────────────────────────────────────────────────


def test_telnet_direct_sans_identifiants(vte):
    """Sans utilisateur ni mot de passe : telnet direct, aucun envoi programmé."""
    tab = plugin_telnet.TelnetPlugin().build_tab(_hote(user="admin"))
    tab.connect_telnet()
    assert vte.lancements == [("telnet", ["telnet", "10.0.0.1", "23"])]
    assert tab.terminal.command == ("telnet", ["telnet", "10.0.0.1", "23"], "")
    assert vte.minuteries == []
    assert tab.terminal.redessine == 1


def test_telnet_expect_mot_de_passe_options_et_commandes(vte):
    """Identifiants : ssh.expect, options supplémentaires, mot de passe à 2 s, commandes ##D=."""
    hote = _hote(
        user="admin",
        password="secret",
        extra_params="-e ^]",
        commands="enable\nterm len 0\n##D=500\nshow ver\n##D=x\nfin",
    )
    tab = plugin_telnet.TelnetTab(hote)
    tab.connect_telnet()
    expect = "/usr/share/gcm/ssh.expect"
    args = [expect, "telnet", "-l", "admin", "-e", "^]", "10.0.0.1", "23"]
    assert vte.lancements == [(expect, args)]
    assert tab.terminal.host is hote
    envoi = vte.wmain.send_data
    assert vte.minuteries == [
        (2000, envoi, (tab.terminal, "secret")),
        (3000, envoi, (tab.terminal, "enable\rterm len 0")),
        (3500, envoi, (tab.terminal, "show ver\r##D=x\rfin")),
    ]


def test_telnet_delai_en_tete_sans_lignes(vte):
    """Un ``##D=`` avant toute commande décale seulement le premier envoi."""
    tab = plugin_telnet.TelnetTab(_hote(commands="##D=1000\nls"))
    tab.connect_telnet()
    assert [m[0] for m in vte.minuteries] == [4000]


def test_telnet_page_edition_vide(vte):
    """Aucune page ni champ propre à Telnet."""
    plugin = plugin_telnet.TelnetPlugin()
    assert plugin.build_edit_page() is not None
    plugin.load_host_fields(_hote())
    plugin.save_host_fields(_hote())


# ── Local ────────────────────────────────────────────────────────────────────


def test_local_shell_seul(vte):
    """``$SHELL`` lancé sans argument ni commande."""
    tab = plugin_local.LocalPlugin().build_tab(_hote(name="local"))
    tab.connect_local()
    assert vte.lancements == [("/bin/bash", None)]
    assert vte.minuteries == []
    assert tab.terminal.redessine == 1


def test_local_commandes_a_700_ms(vte):
    """Commandes post-connexion : premier envoi à 700 ms puis délais cumulés."""
    tab = plugin_local.LocalTab(_hote(commands="cd /srv\n##D=300\nls\n##D=200"))
    tab.connect_local()
    envoi = vte.wmain.send_data
    assert vte.minuteries == [
        (700, envoi, (tab.terminal, "cd /srv")),
        (1000, envoi, (tab.terminal, "ls")),
    ]


def test_local_page_edition_vide(vte):
    """Aucune page ni champ propre au shell local."""
    plugin = plugin_local.LocalPlugin()
    assert plugin.build_edit_page() is not None
    plugin.load_host_fields(_hote())
    plugin.save_host_fields(_hote())


# ── Série : formulaire d'édition ─────────────────────────────────────────────


@pytest.fixture
def formulaire(monkeypatch):
    """``SerialPlugin`` avec combos, grille et étiquettes en doublures."""
    monkeypatch.setattr(plugin_serial.Gtk, "ComboBoxText", Combo, raising=False)
    monkeypatch.setattr(plugin_serial.Gtk, "Grid", Grille, raising=False)
    monkeypatch.setattr(plugin_serial.Gtk, "Label", Etiquette, raising=False)
    monkeypatch.setattr(plugin_serial.Gtk, "Align", types.SimpleNamespace(START=1), raising=False)
    plugin = plugin_serial.SerialPlugin()
    grille = plugin.build_edit_page()
    return plugin, grille


def test_cmb_aides_sans_combo():
    """Combo absent : sélection ignorée, valeur par défaut rendue."""
    plugin_serial._cmb_set_id(None, "x")
    assert plugin_serial._cmb_get_id(None, "defaut") == "defaut"
    vide = Combo()
    assert plugin_serial._cmb_get_id(vide, "defaut") == "defaut"
    vide.set_active_id("9600")
    assert plugin_serial._cmb_get_id(vide) == "9600"


def test_formulaire_sept_lignes_et_valeurs_initiales(formulaire):
    """Sept combos (modèle, débit, bits, parité, stop, contrôle de flux, outil), valeurs par défaut."""
    plugin, grille = formulaire
    assert len(grille.attaches) == 14
    assert [ligne for _, colonne, ligne in grille.attaches if colonne == 1] == list(range(7))
    assert plugin._cmb_template.get_active_id() == "custom"
    assert "changed" in plugin._cmb_template.signaux
    valeurs = [
        c.get_active_id()
        for c in (
            plugin._cmb_baud,
            plugin._cmb_databits,
            plugin._cmb_parity,
            plugin._cmb_stopbits,
            plugin._cmb_flow,
            plugin._cmb_tool,
        )
    ]
    assert valeurs == ["9600", "8", "n", "1", "n", "picocom"]


def test_modele_constructeur_pre_remplit(formulaire):
    """Choisir un modèle constructeur recopie ses cinq réglages ; « custom » ne touche à rien."""
    plugin, _ = formulaire
    tpl_id, (baud, databits, parity, stopbits, flow) = next(iter(plugin_serial._TEMPLATES.items()))
    plugin._cmb_template.set_active_id(tpl_id)
    plugin._on_template_changed(plugin._cmb_template)
    assert (
        plugin._cmb_baud.get_active_id(),
        plugin._cmb_databits.get_active_id(),
        plugin._cmb_parity.get_active_id(),
        plugin._cmb_stopbits.get_active_id(),
        plugin._cmb_flow.get_active_id(),
    ) == (baud, databits, parity, stopbits, flow)
    plugin._cmb_baud.set_active_id("57600")
    plugin._cmb_template.set_active_id("custom")
    plugin._on_template_changed(plugin._cmb_template)
    assert plugin._cmb_baud.get_active_id() == "57600"


def test_charger_puis_enregistrer_l_hote(formulaire):
    """Aller-retour hôte → combos → hôte ; champs absents : valeurs par défaut."""
    plugin, _ = formulaire
    source = types.SimpleNamespace(
        name="console",
        serial_baud="115200",
        serial_databits="7",
        serial_parity="e",
        serial_stopbits="2",
        serial_flow="h",
        serial_tool="minicom",
    )
    plugin._cmb_template.set_active_id("bmc")
    plugin.load_host_fields(source)
    assert plugin._cmb_template.get_active_id() == "custom"
    cible = types.SimpleNamespace()
    plugin.save_host_fields(cible)
    assert vars(cible) == {
        "serial_baud": "115200",
        "serial_databits": "7",
        "serial_parity": "e",
        "serial_stopbits": "2",
        "serial_flow": "h",
        "serial_tool": "minicom",
    }
    plugin.load_host_fields(types.SimpleNamespace(serial_baud=""))
    plugin.save_host_fields(cible)
    assert (cible.serial_baud, cible.serial_tool) == ("9600", "picocom")


# ── Série : connexion et déconnexion ─────────────────────────────────────────


def _onglet_serie(monkeypatch, lancements, **champs):
    """``SerialTab`` sur ``/dev/ttyUSB0`` avec ``vte_run`` enregistré."""
    monkeypatch.setattr(plugin_serial, "SERIAL_BIN", "picocom")
    monkeypatch.setattr(
        plugin_serial, "vte_run", lambda t, cmd, args: lancements.append((cmd, args))
    )
    hote = test_gcm._make_host(protocol="serial", host="/dev/ttyUSB0", port="9600", **champs)
    return plugin_serial.SerialTab(hote)


def test_connexion_persiste_les_reglages_et_lance(monkeypatch):
    """Connexion : réglages recopiés dans l'hôte, boutons basculés, picocom lancé."""
    lancements: list = []
    tab = _onglet_serie(monkeypatch, lancements)
    tab._entry_opts.set_text("  --omap crlf ")
    tab.connect_serial()
    assert tab.host.extra_params == "--omap crlf"
    assert tab.host.port == tab.host.serial_baud
    assert tab.host.host == tab._entry_dev.get_text().strip()
    assert len(lancements) == 1
    cmd, args = lancements[0]
    assert os.path.basename(cmd) == "picocom"
    assert "--omap" in args
    assert tab._lbl_status.get_text() == plugin_serial._("Connecting…")


def test_connexion_sans_commande_ne_lance_rien(monkeypatch):
    """Commande vide (outil introuvable, périphérique vide) : rien n'est lancé."""
    lancements: list = []
    tab = _onglet_serie(monkeypatch, lancements)
    monkeypatch.setattr(tab, "_build_cmd", lambda: [])
    tab._on_connect(None)
    assert lancements == []


def test_deconnexion_envoie_sigterm(monkeypatch):
    """Déconnexion : SIGTERM au descripteur rendu par le PTY, boutons rebasculés."""
    lancements: list = []
    tab = _onglet_serie(monkeypatch, lancements)
    tab._terminal = MagicMock()
    tab._terminal.get_pty.return_value.get_fd.return_value = 4242
    tues: list = []
    monkeypatch.setattr(plugin_serial.os, "kill", lambda pid, sig: tues.append((pid, sig)))
    tab._on_disconnect(None)
    import signal

    assert tues == [(4242, signal.SIGTERM)]
    assert tab._lbl_status.get_text() == plugin_serial._("Disconnect")


@pytest.mark.parametrize("fd", [0, None])
def test_deconnexion_sans_processus(monkeypatch, fd):
    """Aucun processus (descripteur nul) : aucun signal."""
    tab = _onglet_serie(monkeypatch, [])
    tab._terminal = MagicMock()
    tab._terminal.get_pty.return_value.get_fd.return_value = fd
    tues: list = []
    monkeypatch.setattr(plugin_serial.os, "kill", lambda pid, sig: tues.append(pid))
    tab._on_disconnect(None)
    assert tues == []


def test_deconnexion_erreur_journalisee(monkeypatch):
    """PTY indisponible : l'erreur est journalisée, l'onglet repasse à l'état déconnecté."""
    tab = _onglet_serie(monkeypatch, [])
    tab._terminal = MagicMock()
    tab._terminal.get_pty.side_effect = RuntimeError("pas de pty")
    messages: list[str] = []
    identifiant = plugin_serial.logger.add(messages.append, level="DEBUG", format="{message}")
    try:
        tab._on_disconnect(None)
    finally:
        plugin_serial.logger.remove(identifiant)
    assert any("pas de pty" in m for m in messages)
    assert tab._lbl_status.get_text() == plugin_serial._("Disconnect")


def test_local_dernier_lot_et_boucle_d_evenements(vte, monkeypatch):
    """Lignes après le dernier ``##D=`` envoyées ; événements GTK en attente traités avant l'envoi."""
    en_attente = iter([True, True, False])
    iterations: list[int] = []
    monkeypatch.setattr(
        plugin_local.Gtk, "events_pending", lambda: next(en_attente), raising=False
    )
    monkeypatch.setattr(
        plugin_local.Gtk, "main_iteration", lambda: iterations.append(1), raising=False
    )
    tab = plugin_local.LocalTab(_hote(commands="##D=100\nuptime"))
    tab.connect_local()
    assert iterations == [1, 1]
    assert [(ms, a[1]) for ms, _, a in vte.minuteries] == [(800, "uptime")]


def test_telnet_boucle_d_evenements(vte, monkeypatch):
    """Telnet : la boucle GTK est vidée avant la programmation des envois."""
    en_attente = iter([True, False])
    iterations: list[int] = []
    monkeypatch.setattr(
        plugin_telnet.Gtk, "events_pending", lambda: next(en_attente), raising=False
    )
    monkeypatch.setattr(
        plugin_telnet.Gtk, "main_iteration", lambda: iterations.append(1), raising=False
    )
    plugin_telnet.TelnetTab(_hote(commands="##D=10\n##D=20")).connect_telnet()
    assert iterations == [1]
    assert vte.minuteries == []


@pytest.mark.parametrize(
    ("module", "classe"),
    [
        (plugin_local, plugin_local.LocalPlugin),
        (plugin_telnet, plugin_telnet.TelnetPlugin),
        (plugin_serial, plugin_serial.SerialPlugin),
    ],
)
def test_point_d_entree_get_plugin(module, classe):
    """``get_plugin()`` (PluginRegistry.autoload) rend une instance neuve du plugin."""
    assert isinstance(module.get_plugin(), classe)


def test_serie_champs_hote_et_onglet(monkeypatch):
    """Champs ``Host`` du protocole série (valeurs par défaut) ; ``build_tab`` rend un ``SerialTab``."""
    assert dict(plugin_serial.SerialPlugin().host_fields()) == {
        "serial_databits": "8",
        "serial_baud": "9600",
        "serial_parity": "n",
        "serial_stopbits": "1",
        "serial_flow": "n",
        "serial_tool": "picocom",
    }
    monkeypatch.setattr(plugin_serial, "SERIAL_BIN", "picocom")
    hote = test_gcm._make_host(protocol="serial", host="/dev/ttyS0", port="9600")
    assert isinstance(plugin_serial.SerialPlugin().build_tab(hote), plugin_serial.SerialTab)
