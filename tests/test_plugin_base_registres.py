# SPDX-License-Identifier: GPL-3.0-or-later
"""Registres de plugins de ``plugins/plugin_base.py`` (#293, palier 2).

``tests/test_plugin_base.py`` vérifie seulement que le module s'importe sans
GTK. Ici : contrats par défaut de ``ConnectionPlugin`` et ``BatchPlugin``,
instrumentation ``_attach_debug_tracing``, et ``autoload()`` des deux
registres sur des dossiers de plugins fabriqués dans ``tmp_path`` (import en
échec, point d'entrée absent, appel en échec, doublon, liste, ``None``).
Aucun plugin réel n'est importé : le test ne dépend ni de GTK ni des
bibliothèques des protocoles.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest
from loguru import logger

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
)

import plugin_base  # noqa: E402
from plugin_base import (  # noqa: E402
    BatchPlugin,
    BatchPluginRegistry,
    ConnectionPlugin,
    PluginRegistry,
    _attach_debug_tracing,
)


class Protocole(ConnectionPlugin):
    """Plugin de connexion minimal ; les méthodes abstraites délèguent au parent."""

    def __init__(self, protocol_id: str = "essai", ui_order: int = 100, champs=None) -> None:
        """Identifiant, ordre d'affichage et champs Host déclarés."""
        self.protocol_id = protocol_id
        self.ui_order = ui_order
        self._champs = champs or []

    def build_tab(self, host, get_password=None):
        """Délègue à la méthode abstraite."""
        return super().build_tab(host, get_password)

    def build_edit_page(self):
        """Délègue à la méthode abstraite."""
        return super().build_edit_page()

    def load_host_fields(self, host):
        """Délègue à la méthode abstraite."""
        return super().load_host_fields(host)

    def save_host_fields(self, host):
        """Délègue à la méthode abstraite."""
        return super().save_host_fields(host)

    def host_fields(self):
        """Champs fournis, sinon ceux du contrat par défaut."""
        return self._champs or super().host_fields()


class Outil(BatchPlugin):
    """Outil hors session minimal ; activate délègue au parent."""

    def __init__(self, tool_id: str = "outil") -> None:
        """Identifiant de l'outil."""
        self.tool_id = tool_id

    def activate(self):
        """Délègue à la méthode abstraite."""
        return super().activate()


@pytest.fixture
def journal():
    """Capture les messages loguru de niveau DEBUG et plus."""
    messages: list[str] = []
    sink = logger.add(messages.append, level="DEBUG", format="{level} {message}")
    yield messages
    logger.remove(sink)


@pytest.fixture
def dossier_plugins(tmp_path, monkeypatch):
    """Fabrique des modules ``plugin_*.py`` importables, uniques à chaque test."""
    paquet = f"plugins_essai_{tmp_path.name.replace('-', '_')}"
    racine = tmp_path / paquet
    racine.mkdir()
    (racine / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))

    def ecrire(nom: str, code: str) -> None:
        (racine / f"{nom}.py").write_text(textwrap.dedent(code))

    yield racine, paquet, ecrire
    for nom in [m for m in sys.modules if m == paquet or m.startswith(paquet + ".")]:
        del sys.modules[nom]


CORPS_PROTOCOLE = """
    from plugin_base import ConnectionPlugin

    class P(ConnectionPlugin):
        def __init__(self, pid):
            self.protocol_id = pid
        def build_tab(self, host, get_password=None): ...
        def build_edit_page(self): ...
        def load_host_fields(self, host): ...
        def save_host_fields(self, host): ...
"""

CORPS_OUTIL = """
    from plugin_base import BatchPlugin

    class O(BatchPlugin):
        def __init__(self, tid):
            self.tool_id = tid
        def activate(self): ...
"""


def _peupler(ecrire) -> None:
    """Un module de chaque cas traité par les deux registres."""
    ecrire("plugin_a_casse", "raise ImportError('module absent')\n")
    ecrire("plugin_b_sans_entree", "VALEUR = 1\n")
    ecrire(
        "plugin_c_proto",
        CORPS_PROTOCOLE + "\n    def get_plugin():\n        return [P('c1'), None, P('c2')]\n",
    )
    ecrire("plugin_d_proto_echec", "def get_plugin():\n    raise RuntimeError('boum')\n")
    ecrire(
        "plugin_e_proto_doublon",
        CORPS_PROTOCOLE + "\n    def get_plugin():\n        return (P('c1'), P(''))\n",
    )
    ecrire(
        "plugin_f_outil",
        CORPS_OUTIL + "\n    def get_batch_plugin():\n        return [O('o1'), None]\n",
    )
    ecrire("plugin_g_outil_echec", "def get_batch_plugin():\n    raise RuntimeError('boum')\n")
    ecrire(
        "plugin_h_outil_doublon",
        CORPS_OUTIL + "\n    def get_batch_plugin():\n        return O('o1')\n",
    )
    ecrire("autre_module", "raise AssertionError('ne doit pas être importé')\n")
    # Homonyme du module de base : jamais re-scanné par les registres.
    ecrire("plugin_base", "raise AssertionError('ne doit pas être importé')\n")


class TestContratsParDefaut:
    """Méthodes abstraites et valeurs par défaut des contrats."""

    def test_methodes_abstraites_levent(self) -> None:
        """Les corps abstraits lèvent NotImplementedError."""
        plugin = Protocole()
        for appel in (
            lambda: plugin.build_tab(object()),
            plugin.build_edit_page,
            lambda: plugin.load_host_fields(object()),
            lambda: plugin.save_host_fields(object()),
            Outil().activate,
        ):
            with pytest.raises(NotImplementedError):
                appel()

    def test_valeurs_par_defaut(self) -> None:
        """Les points d'extension optionnels ne déclarent rien."""
        plugin = Protocole()
        assert plugin.host_fields() == []
        assert plugin.build_folder_context_menu_items() == []
        assert plugin.validate() == []
        assert plugin.menu_actions() == []
        assert plugin.patch_edit_host_dialog(object(), "host 1", object()) is None
        assert Outil().menu_section == "tools"


class TestTraçage:
    """Instrumentation _attach_debug_tracing."""

    def test_methodes_enveloppees_une_seule_fois(self, journal) -> None:
        """Appel journalisé, instrumentation idempotente."""
        plugin = Protocole()
        _attach_debug_tracing(plugin, "ConnectionPlugin")
        enveloppe = plugin.validate
        _attach_debug_tracing(plugin, "ConnectionPlugin")
        assert plugin.validate is enveloppe
        assert plugin.validate() == []
        assert any("ConnectionPlugin.Protocole.validate | args=0 kwargs=[]" in m for m in journal)

    def test_attributs_illisibles_ou_figes_ignores(self) -> None:
        """Propriété en échec, propriété sans setter et app laissées telles quelles."""

        class Capricieux(Protocole):
            app = staticmethod(lambda: "jamais enveloppé")

            @property
            def illisible(self):
                raise RuntimeError("propriété en échec")

            @property
            def figee(self):
                return lambda: "lecture seule"

        plugin = Capricieux()
        _attach_debug_tracing(plugin, "ConnectionPlugin")
        assert plugin._gcm_debug_tracing_attached is True
        assert plugin.figee() == "lecture seule"
        assert plugin.app is Capricieux.app


class TestPluginRegistry:
    """Registre des plugins de connexion."""

    def test_enregistrement_et_consultation(self) -> None:
        """Consultation, tri, champs agrégés et injection de l'application."""
        registre = PluginRegistry()
        b, a = (
            Protocole("b", ui_order=1, champs=[("b_x", 0)]),
            Protocole("a", ui_order=1, champs=[("a_x", "")]),
        )
        registre.register(b)
        registre.register(a)
        assert registre.get("a") is a and registre.get("z") is None
        assert registre.all() == [b, a]
        assert [p.protocol_id for p in registre.all_sorted()] == ["a", "b"]
        assert registre.protocol_ids() == ["b", "a"]
        assert registre.all_host_fields() == [("a_x", ""), ("b_x", 0)]
        assert "a" in registre and "z" not in registre and len(registre) == 2
        application = object()
        registre.bind_app(application)
        assert a.app is application and b.app is application

    @pytest.mark.parametrize(("pid", "message"), [("", "manquant"), ("a", "déjà enregistré")])
    def test_enregistrement_refuse(self, pid, message) -> None:
        """Un protocol_id vide ou en double refusé."""
        registre = PluginRegistry()
        registre.register(Protocole("a"))
        with pytest.raises(ValueError, match=message):
            registre.register(Protocole(pid))

    def test_autoload(self, dossier_plugins, journal) -> None:
        """Modules cassés, sans entrée, en échec ou en double journalisés et sautés."""
        racine, paquet, ecrire = dossier_plugins
        _peupler(ecrire)
        registre = PluginRegistry()
        assert registre.autoload(str(racine), paquet) == ["c1", "c2"]
        texte = "\n".join(journal)
        assert f"echec import {paquet}.plugin_a_casse" in texte
        assert f"{paquet}.plugin_b_sans_entree ignore" in texte
        assert f"echec {paquet}.plugin_d_proto_echec.get_plugin()" in texte
        assert f"enregistrement refuse pour {paquet}.plugin_e_proto_doublon" in texte
        assert "plugin_f_outil ignore" not in texte

    def test_autoload_dossier_par_defaut(self, monkeypatch) -> None:
        """Sans dossier, les deux registres parcourent celui de plugin_base.py."""
        vus = []

        def decouverte(directory, package, self_module_name):
            vus.append((directory, package, self_module_name))
            return iter(())

        monkeypatch.setattr(plugin_base, "_discover_plugin_modules", decouverte)
        assert PluginRegistry().autoload() == []
        assert BatchPluginRegistry().autoload() == []
        attendu = (Path(plugin_base.__file__).resolve().parent, None, "plugin_base")
        assert vus == [attendu, attendu]


class TestBatchPluginRegistry:
    """Registre des outils hors session."""

    def test_enregistrement_et_consultation(self) -> None:
        """Consultation et injection de l'application."""
        registre = BatchPluginRegistry()
        outil = Outil("o")
        registre.register(outil)
        assert registre.get("o") is outil and registre.get("z") is None
        assert registre.all() == [outil] and registre.tool_ids() == ["o"]
        assert "o" in registre and len(registre) == 1
        application = object()
        registre.bind_app(application)
        assert outil.app is application

    @pytest.mark.parametrize(("tid", "message"), [("", "manquant"), ("o", "déjà enregistré")])
    def test_enregistrement_refuse(self, tid, message) -> None:
        """Un tool_id vide ou en double refusé."""
        registre = BatchPluginRegistry()
        registre.register(Outil("o"))
        with pytest.raises(ValueError, match=message):
            registre.register(Outil(tid))

    def test_autoload(self, dossier_plugins, journal) -> None:
        """Chaque échec réel est consigné dans load_errors avec sa cause."""
        racine, paquet, ecrire = dossier_plugins
        _peupler(ecrire)
        registre = BatchPluginRegistry()
        assert registre.autoload(racine, paquet) == ["o1"]
        erreurs = registre.load_errors
        assert set(erreurs) == {
            "plugin_a_casse",
            "plugin_b_sans_entree",
            "plugin_g_outil_echec",
            "plugin_h_outil_doublon",
        }
        assert erreurs["plugin_a_casse"] == "echec import : module absent"
        assert (
            erreurs["plugin_b_sans_entree"]
            == "pas de fonction 'get_batch_plugin()' de niveau module"
        )
        assert erreurs["plugin_g_outil_echec"] == "echec de get_batch_plugin() : boum"
        assert erreurs["plugin_h_outil_doublon"].startswith(
            "enregistrement refuse : tool_id déjà enregistré"
        )
