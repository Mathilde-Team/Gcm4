# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin web GTK4 (``plugins/web/gtk4.py``, #397) : formulaire et repli sans WebKit.

Ces tests ne demandent pas WebKitGTK : ils passent aussi sur Debian 12 et
Rocky 9. L'onglet avec un vrai ``WebKit.WebView`` (serveurs HTTP et HTTPS
locaux, certificat auto-signé) est testé dans ``test_plugin_web_webkit.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi
import pytest
from gi.repository import Gio, GLib, Gtk

from gcm4.core import registre
from gcm4.core.model import Host

RACINE = Path(__file__).resolve().parents[2]


class Journal:
    """Journal du contexte : garde les avertissements."""

    def __init__(self):
        """Aucun avertissement."""
        self.avertissements = []

    def warning(self, message, *args):
        """Avertissement formaté comme loguru."""
        self.avertissements.append(message.format(*args))

    def debug(self, *_args):
        """Ignoré."""


class Contexte:
    """Contexte minimal du contrat v1 (``docs/plugins.md``)."""

    def __init__(self):
        """Journal et notifications vides."""
        self.log = Journal()
        self.notifications = []

    def config(self):
        """Section ``[plugin web]`` vide."""
        return {}

    def notify(self, message):
        """Notification notée."""
        self.notifications.append(message)


@pytest.fixture
def plugin():
    """Plugin web chargé depuis ``plugins/`` du dépôt, comme en mode source."""
    candidats = registre.candidats_dossier(RACINE / "plugins")
    etats = {e.name: e for e in registre.decouvrir(lambda _nom: Contexte(), candidats).etats}
    assert etats["web"].erreur is None
    yield etats["web"].plugin
    registre._oublier_paquet("gcm4_plugin_web")


@pytest.fixture
def module(plugin):
    """Module ``gcm4_plugin_web.gtk4`` du plugin chargé."""
    return sys.modules[type(plugin).__module__]


@pytest.fixture
def navigateur(monkeypatch):
    """``Gio.AppInfo.launch_default_for_uri`` remplacé : note les URL ouvertes."""
    ouvertes = []
    monkeypatch.setattr(
        Gio.AppInfo, "launch_default_for_uri", lambda uri, _ctx: ouvertes.append(uri)
    )
    return ouvertes


def _hote(**options):
    """Hôte ``web`` de test (sans adresse : l'URL fait foi)."""
    return Host(name="ilo", protocol="web", protocol_options={"web": dict(options)})


def test_identite(plugin):
    """Nom, protocole, port 443 comme GTK3, pas d'adresse exigée."""
    assert (plugin.name, plugin.protocols) == ("web", ("web",))
    assert plugin.default_port("web") == 443
    assert plugin.requires_address("web") is False
    assert plugin.default_options("web") == {"web_url": "", "web_ignore_cert": "True"}


def test_validate_options(plugin):
    """URL obligatoire et ``http``/``https`` (``core.valider``)."""
    assert [e.champ for e in plugin.validate_options("web", {})] == ["web_url"]
    assert [e.champ for e in plugin.validate_options("web", {"web_url": "file:///x"})] == [
        "web_url"
    ]
    assert plugin.validate_options("web", {"web_url": "https://ilo/"}) == []


def test_formulaire(plugin):
    """URL et case modifient la copie des options et appellent ``changed``."""
    options = {"web_url": "https://ilo/", "web_ignore_cert": "False"}
    appels = []
    grille = plugin.build_host_options_widget(_hote(), options, lambda: appels.append(1))
    assert isinstance(grille, Gtk.Grid)
    saisie = grille.get_child_at(1, 0)
    case = grille.get_child_at(1, 1)
    assert saisie.get_text() == "https://ilo/"
    assert case.get_active() is False  # relu depuis les options (GTK3 : « False »)
    saisie.set_text("  https://idrac.example.org/  ")
    assert options["web_url"] == "https://idrac.example.org/"
    case.set_active(True)
    assert options["web_ignore_cert"] == "True"
    assert len(appels) >= 2  # set_text : effacement puis insertion, puis la case


def test_formulaire_nouvel_hote(plugin):
    """Hôte sans option : case cochée (défaut GTK3), URL vide."""
    grille = plugin.build_host_options_widget(_hote(), {}, lambda: None)
    assert grille.get_child_at(1, 0).get_text() == ""
    assert grille.get_child_at(1, 1).get_active() is True


@pytest.mark.parametrize("options", [{}, {"web_url": "javascript:alert(1)"}])
def test_open_refuse_url_invalide(plugin, options):
    """URL absente ou refusée : exception, la base l'affiche sans onglet."""
    with pytest.raises(ValueError):
        plugin.open(_hote(**options))


def test_open_sans_webkit_ouvre_le_navigateur(plugin, module, navigateur, monkeypatch):
    """Sans WebKitGTK : message dans l'onglet, URL ouverte dans le navigateur."""
    monkeypatch.setattr(module, "charger_webkit", lambda: None)
    session = plugin.open(_hote(web_url="idrac.example.org"))
    assert session.webview is None
    assert "WebKitGTK 6.0" in session.repli.get_text()
    assert navigateur == ["https://idrac.example.org"]
    assert session.etat.get_text() == "Opened in the system browser"
    session.bouton_recharger.emit("clicked")
    session.bouton_navigateur.emit("clicked")
    assert navigateur == ["https://idrac.example.org"] * 3
    session.close()
    session.charger()  # onglet fermé : plus rien n'est ouvert
    assert len(navigateur) == 3


def test_navigateur_en_echec(plugin, module, monkeypatch):
    """Lancement du navigateur impossible : état d'erreur et avertissement."""

    def refuse(_uri, _ctx):
        raise GLib.Error("aucun navigateur")

    monkeypatch.setattr(Gio.AppInfo, "launch_default_for_uri", refuse)
    monkeypatch.setattr(module, "charger_webkit", lambda: None)
    session = plugin.open(_hote(web_url="https://ilo/"))
    assert session.etat.get_text() == "Error: could not open the URL"
    assert session.ouvrir_navigateur() is False
    assert "aucun navigateur" in session.context.log.avertissements[0]


def test_titre_echappe(plugin, module, navigateur, monkeypatch):
    """L'URL est échappée dans le balisage Pango du titre."""
    monkeypatch.setattr(module, "charger_webkit", lambda: None)
    session = plugin.open(_hote(web_url="https://ilo/?a=1&b=<2>"))
    assert session.titre.get_text() == "Web https://ilo/?a=1&b=<2>"


def test_charger_webkit_absent(module, monkeypatch):
    """``gi.require_version`` refuse WebKit 6.0 : ``None`` (repli)."""

    def refuse(nom, version):
        raise ValueError(f"Namespace {nom} not available for version {version}")

    monkeypatch.setattr(gi, "require_version", refuse)
    assert module.charger_webkit() is None
