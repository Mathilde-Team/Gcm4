# SPDX-License-Identifier: GPL-3.0-or-later
"""Contrat « outil » v1 : ToolPlugin, découverte et menu, sans GTK (#73)."""

from __future__ import annotations

import sys

import pytest

from gcm4.core import menu
from gcm4.core.plugins import (
    API_VERSION,
    SECTION_EXPORT,
    SECTION_IMPORT,
    ConnectionPlugin,
    ToolPlugin,
    incompatibilite_outil,
)
from gcm4.core.registre import Candidat, PluginRegistry, decouvrir


class _Contexte:
    """PluginContext minimal."""

    log = None

    def config(self):
        return {}

    def ask_password(self, host, done):
        done(None)

    def notify(self, message):
        pass

    def open_tab(self, title, widget):
        pass


class _BaseOutil(ToolPlugin):
    """Outil complet de test."""

    def activate(self):
        """Déclenche l'outil factice."""


class Export(_BaseOutil):
    """Outil de section export."""

    name = "export-putty"
    version = "1.0"
    menu_label = "Export PuTTY…"


class OutilMenu(_BaseOutil):
    """Outil de sous-menu Tools."""

    name = "inventaire"
    version = "0.1"
    menu_label = "Inventory…"


class _BaseConnexion(ConnectionPlugin):
    """Connexion minimale (le contrat complet est dans test_gcm4_plugins)."""

    def build_host_options_widget(self, host, options, changed):
        return None

    def open(self, host):
        raise NotImplementedError


# --- Contrat ------------------------------------------------------------------


def test_outil_minimal_et_valeurs_par_defaut():
    """Un outil valide se charge, avec les sections et valeurs par défaut."""
    outil = Export(_Contexte())
    assert (outil.name, outil.version) == ("export-putty", "1.0")
    assert outil.menu_section == "outils"
    assert outil.icon_name == ""
    assert outil.api_version == API_VERSION
    assert Export.build_preferences_page(outil) is None


def test_outils_refuses():
    """incompatibilite_outil refuse chaque cas invalide."""
    cas = [
        (_BaseConnexion, "not a tool plugin"),
        (
            type("Vieux", (_BaseOutil,), {"name": "vieux", "menu_label": "V", "api_version": 0}),
            "version",
        ),
        (type("SansNom", (_BaseOutil,), {"menu_label": "V"}), "name"),
        (type("NomEspace", (_BaseOutil,), {"name": "a b", "menu_label": "V"}), "name"),
        (type("SansLibelle", (_BaseOutil,), {"name": "ok"}), "menu label"),
        (
            type(
                "SectionInvalide",
                (_BaseOutil,),
                {"name": "ok", "menu_label": "V", "menu_section": "nulle part"},
            ),
            "menu section",
        ),
        (ToolPlugin, "name"),
    ]
    for classe, indice in cas:
        raison = incompatibilite_outil(classe)
        assert raison is not None and indice in raison, (classe, raison)


def test_contrat_outil_sans_gtk():
    """Le module du contrat s'importe sans GTK (même règle que les connexions)."""
    import subprocess

    code = (
        "import sys; sys.path.insert(0, 'src');"
        "from gcm4.core.plugins import ToolPlugin; print(ToolPlugin.__name__)"
    )
    resultat = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert resultat.stdout.strip() == "ToolPlugin"


# --- Registre -----------------------------------------------------------------


MODULE_OUTILS = """
from gcm4.core.plugins import ConnectionPlugin, ToolPlugin


class BaseOutil(ToolPlugin):
    def activate(self):
        pass


class Inventaire(BaseOutil):
    name = "inventaire"
    version = "3.1"
    menu_label = "Inventory…"
    menu_section = "outils"


class Putty(BaseOutil):
    name = "import-putty"
    version = "1.0"
    menu_label = "Import PuTTY…"
    menu_section = "import"


class MauvaiseSection(BaseOutil):
    name = "mauvaise"
    menu_label = "Mauvaise"
    menu_section = "inconnue"


class Doublon(BaseOutil):
    name = "inventaire"
    menu_label = "Doublon"


class BaseConnexion(ConnectionPlugin):
    def build_host_options_widget(self, host, options, changed):
        return None

    def open(self, host):
        raise NotImplementedError


class MemeNom(BaseConnexion):
    name = "inventaire"
    version = "9.9"
    protocols = ("inv",)
"""


@pytest.fixture
def module_outils(tmp_path, monkeypatch):
    """Module importable ``outils_test`` avec l'outil et la connexion de test."""
    (tmp_path / "outils_test.py").write_text(MODULE_OUTILS, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "outils_test"
    sys.modules.pop("outils_test", None)


def _candidat(module, classe):
    import outils_test  # noqa: F401

    return Candidat(
        nom=classe, source=f"{module}.{classe}", charger=lambda: getattr(outils_test, classe)
    )


def test_decouverte_et_sections(module_outils):
    """L'outil valide est chargé, classé outil, sans protocole ; la mauvaise section est refusée."""
    registre = decouvrir(
        lambda _nom: _Contexte(),
        [
            _candidat(module_outils, "Inventaire"),
            _candidat(module_outils, "Putty"),
            _candidat(module_outils, "MauvaiseSection"),
        ],
    )
    outils = registre.outils()
    assert [o.name for o in outils] == ["import-putty", "inventaire"]
    assert registre.outil("inventaire").menu_section == "outils"
    assert registre.outil("absent") is None
    erreurs = {e.name: e.erreur for e in registre.erreurs()}
    assert "menu section" in erreurs["mauvaise"]
    # Les connexions et les outils restent séparés.
    assert registre.plugins() == []
    assert [e.type for e in registre.etats if e.charge] == ["outil", "outil"]


def test_doublon_de_nom_entre_familles(module_outils):
    """Un outil et une connexion de même nom ne font qu'un chargé : le premier gagne."""
    registre = decouvrir(
        lambda _nom: _Contexte(),
        [_candidat(module_outils, "Inventaire"), _candidat(module_outils, "MemeNom")],
    )
    assert len(registre.outils()) == 1
    assert [e.name for e in registre.erreurs()] == ["inventaire"]
    assert "already loaded" in registre.erreurs()[0].erreur


def test_outil_refuse_en_erreur_avec_sa_raison(module_outils):
    """Un outil refusé reste affiché en erreur avec sa raison."""
    registre = decouvrir(lambda _nom: _Contexte(), [_candidat(module_outils, "MauvaiseSection")])
    assert registre.outils() == []
    (erreur,) = registre.erreurs()
    assert erreur.name == "mauvaise"
    assert "menu section" in erreur.erreur


# --- Menu ---------------------------------------------------------------------


class _OutilFactice:
    """Shape minimal d'un outil pour le menu (tests sans registre)."""

    def __init__(self, name, menu_label, menu_section="outils"):
        self.name = name
        self.menu_label = menu_label
        self.menu_section = menu_section


def test_action_outil_ne_garde_que_les_caracteres_surs():
    """Le nom d'action est toujours un identifiant GTK valide."""
    assert menu.action_outil("inventaire") == "win.outil-inventaire"
    assert menu.action_outil("Netmiko Push!") == "win.outil-netmiko-push-"


def test_menu_sans_outil_inchange():
    """Sans outil, le menu est exactement MENU_PRINCIPAL."""
    assert menu.menu_avec_outils(menu.MENU_PRINCIPAL, []) is menu.MENU_PRINCIPAL
    assert menu.menu_avec_outils(menu.MENU_PRINCIPAL) is menu.MENU_PRINCIPAL


def test_menu_avec_outils_et_sections():
    """Les outils complètent le menu Fichier et ajoutent le sous-menu Tools."""
    complet = menu.menu_avec_outils(
        menu.MENU_PRINCIPAL,
        [
            _OutilFactice("inventaire", "Inventory…"),
            _OutilFactice("import-putty", "Import PuTTY…", SECTION_IMPORT),
            _OutilFactice("export-csv", "Export CSV…", SECTION_EXPORT),
        ],
    )
    # Sous-menu Tools ajouté en fin.
    assert [sm.titre for sm in complet][-1] == "Tools"
    outillage = [e.action for e in complet[-1].sections[0]]
    assert outillage == ["win.outil-inventaire"]
    # Le menu Fichier gagne une section Import/Export.
    fichier = complet[0]
    assert [e.action for e in fichier.sections[-1]] == [
        "win.outil-import-putty",
        "win.outil-export-csv",
    ]
    # Les autres sous-menus et la base restent intacts.
    assert complet[1:-1] == menu.MENU_PRINCIPAL[1:]
    assert "win.outil-import-putty" in menu.actions_du_menu(complet)


def test_menu_import_seul_sans_sous_menu_tools():
    """Un outil d'import seul n'ajoute pas de sous-menu Tools vide."""
    complet = menu.menu_avec_outils(
        menu.MENU_PRINCIPAL, [_OutilFactice("import-putty", "Import PuTTY…", SECTION_IMPORT)]
    )
    assert [sm.titre for sm in complet] == [sm.titre for sm in menu.MENU_PRINCIPAL]
    assert len(complet[0].sections) == len(menu.MENU_PRINCIPAL[0].sections) + 1


def test_pluginregistry_vide_n_expose_aucun_outil():
    """Le registre vide reste sans outil."""
    assert PluginRegistry().outils() == []
