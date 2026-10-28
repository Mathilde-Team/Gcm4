# SPDX-License-Identifier: GPL-3.0-or-later
"""Contrat plugin v1 sans GTK (#149)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from gcm4.core.model import Host
from gcm4.core.plugins import API_VERSION, ConnectionPlugin, MenuItem, Session, incompatibilite
from gcm4.core.validation import FieldError


class _Contexte:
    """PluginContext minimal."""

    log = None

    def __init__(self):
        self.messages = []

    def config(self):
        return {"cle": "valeur"}

    def ask_password(self, host, done):
        done("secret")

    def notify(self, message):
        self.messages.append(message)

    def open_tab(self, title, widget):
        pass


class _Session:
    def __init__(self):
        self.widget = object()
        self.fermee = False

    def close(self):
        self.fermee = True


class Demo(ConnectionPlugin):
    """Plugin complet écrit d'après docs/plugins.md."""

    name = "demo"
    version = "1.0"
    protocols = ("demo",)

    def build_host_options_widget(self, host, options, changed):
        """Zone d'options factice : écrit une option et la signale."""
        options["vu"] = "oui"
        changed()
        return object()

    def open(self, host):
        """Session factice, annoncée par le contexte."""
        self.context.notify(f"ouverture {host.name}")
        return _Session()


def test_plugin_minimal_et_valeurs_par_defaut():
    """Seules build_host_options_widget et open sont obligatoires ; le reste a un défaut neutre."""
    ctx = _Contexte()
    p = Demo(ctx)
    assert incompatibilite(Demo) is None
    assert p.context is ctx and p.api_version == API_VERSION == 1
    assert p.default_port("demo") is None and p.default_options("demo") == {}
    assert p.requires_address("demo") is True  # défaut du contrat v1 (#297)
    assert p.validate_options("demo", {"x": "1"}) == []
    assert p.build_preferences_page() is None and p.context_menu_items(Host("h")) == []
    session = p.open(Host("h"))
    assert isinstance(session, Session) and ctx.messages == ["ouverture h"]
    session.close()
    assert session.fermee


def test_zone_d_options_modifie_une_copie():
    """Le plugin écrit dans la copie fournie et signale le changement."""
    appels = []
    options = {}
    Demo(_Contexte()).build_host_options_widget(Host("h"), options, lambda: appels.append(1))
    assert options == {"vu": "oui"} and appels == [1]


def test_surcharges_facultatives():
    """Port, options, validation, menu : surchargés par le plugin."""

    class Complet(Demo):
        def default_port(self, protocol):
            return 4242

        def requires_address(self, protocol):
            return False

        def default_options(self, protocol):
            return {"mode": "rapide"}

        def validate_options(self, protocol, options):
            return [FieldError("mode", "invalide")] if options.get("mode") == "?" else []

        def context_menu_items(self, host):
            return [MenuItem("Ping", lambda: None)]

    p = Complet(_Contexte())
    assert p.default_port("demo") == 4242 and p.default_options("demo") == {"mode": "rapide"}
    assert p.requires_address("demo") is False  # surcharge facultative (#297)
    assert p.validate_options("demo", {"mode": "?"})[0].champ == "mode"
    assert p.context_menu_items(Host("h"))[0].label == "Ping"


def _variante(**attributs):
    return type("Variante", (Demo,), attributs)


@pytest.mark.parametrize(
    ("classe", "indice"),
    [
        (object, "connection plugin"),
        (42, "connection plugin"),
        (_variante(api_version=2), "version 2"),
        (_variante(api_version=0), "version 0"),
        (_variante(name=""), "lowercase"),
        (_variante(name="Demo"), "lowercase"),
        (_variante(name="mon demo"), "lowercase"),
        (_variante(protocols=()), "protocol"),
        (_variante(protocols=("",)), "protocol"),
    ],
)
def test_plugins_refuses(classe, indice):
    """Héritage, version du contrat, nom, protocoles : refus avec une raison lisible."""
    raison = incompatibilite(classe)
    assert raison and indice in raison


def test_methodes_abstraites_manquantes():
    """Une méthode obligatoire absente est nommée dans la raison."""

    class Incomplet(ConnectionPlugin):
        name = "incomplet"
        protocols = ("x",)

        def open(self, host):
            return _Session()

    assert "build_host_options_widget" in incompatibilite(Incomplet)


def test_contrat_sans_gtk():
    """Importer le contrat ne charge pas GTK (annotations sous TYPE_CHECKING)."""
    code = "import sys, gcm4.core.plugins; sys.exit('gi' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
