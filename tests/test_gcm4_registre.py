# SPDX-License-Identifier: GPL-3.0-or-later
"""Découverte des plugins, tri et isolation des erreurs, sans GTK (#150)."""

from __future__ import annotations

import sys
from importlib import metadata

import pytest

from gcm4.core import registre
from gcm4.core.plugins import ConnectionPlugin
from gcm4.core.registre import Candidat, PluginRegistry, decouvrir

MODULE = '''
from gcm4.core.plugins import ConnectionPlugin


class Base(ConnectionPlugin):
    """Plugin de test."""

    def build_host_options_widget(self, host, options, changed):
        """Aucune zone."""
        return None

    def open(self, host):
        """Pas de session."""
        raise NotImplementedError


class Alpha(Base):
    name = "alpha"
    version = "1.0"
    protocols = ("a",)


class Beta(Base):
    name = "beta"
    version = "2.0"
    protocols = ("b", "b2")


class Gourmand(Base):
    name = "gourmand"
    protocols = ("a",)


class Vieux(Base):
    name = "vieux"
    protocols = ("v",)
    api_version = 0


class Fragile(Base):
    name = "fragile"
    protocols = ("f",)

    def __init__(self, context):
        raise RuntimeError("pas de matériel")


class Sortie(Base):
    name = "sortie"
    protocols = ("s",)

    def __init__(self, context):
        raise SystemExit(3)
'''


@pytest.fixture
def module(tmp_path, monkeypatch):
    """Module importable ``plugins_test`` contenant les classes de MODULE."""
    (tmp_path / "plugins_test.py").write_text(MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield "plugins_test"
    sys.modules.pop("plugins_test", None)


def _points(*paires):
    return [metadata.EntryPoint(name=n, value=v, group=registre.GROUPE) for n, v in paires]


def _contextes():
    vus = []

    def fabrique(nom):
        vus.append(nom)
        return {"nom": nom}

    return fabrique, vus


def test_points_d_entree_tries_et_charges(module):
    """Points d'entrée triés par nom ; contexte propre à chaque plugin ; index des protocoles."""
    fabrique, vus = _contextes()
    candidats = registre.candidats_points_d_entree(
        _points(("zbeta", f"{module}:Beta"), ("alpha", f"{module}:Alpha"))
    )
    assert [c.nom for c in candidats] == ["alpha", "zbeta"]
    reg = decouvrir(fabrique, candidats)
    assert [p.name for p in reg.plugins()] == ["alpha", "beta"] and vus == ["alpha", "beta"]
    assert reg.protocols() == ["a", "b", "b2"] and reg.get("b2").name == "beta"
    assert "a" in reg and "x" not in reg and reg.get("x") is None
    assert reg.get("a").context == {"nom": "alpha"} and reg.erreurs() == []
    etat = reg.etats[1]
    assert etat.charge and (etat.version, etat.protocols) == ("2.0", ("b", "b2"))


@pytest.mark.parametrize(
    ("valeur", "indice"),
    [
        ("module_absent:X", "Import failed"),
        ("{m}:Inexistante", "Import failed"),
        ("{m}:Vieux", "version 0"),
        ("{m}:Fragile", "pas de matériel"),
        ("{m}:Sortie", "3"),
    ],
)
def test_plugin_casse_isole(module, valeur, indice):
    """Import, version, __init__ (même SystemExit) : en erreur avec raison ; alpha se charge quand même."""
    fabrique, _ = _contextes()
    reg = decouvrir(
        fabrique,
        registre.candidats_points_d_entree(
            _points(("casse", valeur.format(m=module)), ("alpha", f"{module}:Alpha"))
        ),
    )
    assert [p.name for p in reg.plugins()] == ["alpha"]
    (erreur,) = reg.erreurs()
    assert indice in erreur.erreur and not erreur.charge


def test_warning_au_journal(module):
    """Un plugin refusé est journalisé en warning avec sa raison."""
    from loguru import logger

    lignes = []
    sink = logger.add(
        lambda m: lignes.append((m.record["level"].name, m.record["message"])), level="WARNING"
    )
    logger.enable("gcm4")
    try:
        decouvrir(
            _contextes()[0],
            registre.candidats_points_d_entree(_points(("x", f"{module}:Fragile"))),
        )
    finally:
        logger.disable("gcm4")
        logger.remove(sink)
    assert any(
        niveau == "WARNING" and "fragile" in msg and "pas de matériel" in msg
        for niveau, msg in lignes
    )


def test_doublons_de_nom_et_de_protocole(module):
    """Même name : le premier gagne ; protocole déjà pris : le suivant est refusé."""
    fabrique, _ = _contextes()
    reg = decouvrir(
        fabrique,
        registre.candidats_points_d_entree(
            _points(
                ("a1", f"{module}:Alpha"), ("a2", f"{module}:Alpha"), ("g", f"{module}:Gourmand")
            )
        ),
    )
    assert [p.name for p in reg.plugins()] == ["alpha"]
    raisons = sorted(e.erreur for e in reg.erreurs())
    assert any("same name" in r for r in raisons) and any(
        "«a»" in r and "alpha" in r for r in raisons
    )


def test_dossier_source(tmp_path, module):
    """plugins/<nom>/gtk4.py : PLUGIN chargé ; sans PLUGIN, illisible ou sans gtk4.py : erreur ou ignoré."""
    racine = tmp_path / "plugins"
    for nom, code in (
        ("zeta", f"from {module} import Beta as PLUGIN\n"),
        ("alpha", f"from {module} import Alpha as PLUGIN\n"),
        ("vide", "X = 1\n"),
        ("syntaxe", "def (\n"),
    ):
        (racine / nom).mkdir(parents=True)
        (racine / nom / "gtk4.py").write_text(code, encoding="utf-8")
    (racine / "gtk3_seulement").mkdir()
    (racine / "gtk3_seulement" / "core.py").write_text("", encoding="utf-8")
    (racine / "fichier.py").write_text("", encoding="utf-8")
    candidats = registre.candidats_dossier(racine)
    assert [c.nom for c in candidats] == ["alpha", "syntaxe", "vide", "zeta"]
    reg = decouvrir(_contextes()[0], candidats)
    assert [p.name for p in reg.plugins()] == ["alpha", "beta"]
    erreurs = {e.name: e.erreur for e in reg.erreurs()}
    assert set(erreurs) == {"syntaxe", "vide"} and "PLUGIN" in erreurs["vide"]
    assert "gcm4_plugin_syntaxe" not in sys.modules
    assert (
        registre.candidats_dossier(None) == []
        and registre.candidats_dossier(tmp_path / "absent") == []
    )


def test_ordre_deterministe(module):
    """Le registre range par name, quel que soit l'ordre des candidats."""
    fabrique, _ = _contextes()
    a = registre.candidats_points_d_entree(
        _points(("1", f"{module}:Beta"), ("2", f"{module}:Alpha"))
    )
    b = list(reversed(a))
    assert [e.name for e in decouvrir(fabrique, a).etats] == [
        e.name for e in decouvrir(fabrique, b).etats
    ]


def test_zero_plugin(monkeypatch):
    """Aucun point d'entrée ni dossier source : registre vide, rien ne lève."""
    monkeypatch.setattr(registre.metadata, "entry_points", lambda: {})
    monkeypatch.setattr(registre, "dossier_source", lambda: None)
    reg = decouvrir(_contextes()[0])
    assert reg.plugins() == [] and reg.protocols() == [] and reg.get("ssh") is None
    assert PluginRegistry().etats == []


def test_dossier_source_du_depot():
    """Depuis les sources, plugins/ du dépôt est trouvé ; telnet y est candidat."""
    dossier = registre.dossier_source()
    assert dossier is not None and dossier.name == "plugins"
    candidats = registre.candidats_dossier(dossier)
    assert "telnet" in [c.nom for c in candidats]
    assert all(c.nom and c.source.endswith("gtk4.py") for c in candidats)


def test_candidat_qui_n_est_pas_une_classe():
    """Un point d'entrée qui désigne autre chose qu'une classe de plugin est refusé."""
    reg = decouvrir(_contextes()[0], [Candidat("objet", "x:y", lambda: 42)])
    assert reg.erreurs()[0].name == "objet" and "connection plugin" in reg.erreurs()[0].erreur
    assert issubclass(ConnectionPlugin, object)


def test_importer_fichier_sans_spec(tmp_path, monkeypatch):
    """Spec introuvable : ImportError transformée en erreur du plugin."""
    monkeypatch.setattr(registre.importlib.util, "spec_from_file_location", lambda *a, **k: None)
    with pytest.raises(ImportError):
        registre._importer_fichier("x", tmp_path / "gtk4.py")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(ImportError):
        registre._importer_fichier("x", tmp_path / "gtk4.py")
    assert "gcm4_plugin_x" not in sys.modules


@pytest.mark.parametrize("avec_init", [False, True])
def test_gtk4_importe_son_core(tmp_path, module, avec_init):
    """Le dossier est un paquet : ``from . import core`` marche, avec ou sans ``__init__.py``."""
    dossier = tmp_path / "plugins" / "voisin"
    dossier.mkdir(parents=True)
    (dossier / "core.py").write_text("VALEUR = 42\n", encoding="utf-8")
    (dossier / "gtk4.py").write_text(
        f"from . import core\nfrom {module} import Alpha as PLUGIN\nLU = core.VALEUR\n",
        encoding="utf-8",
    )
    if avec_init:
        (dossier / "__init__.py").write_text("INIT = True\n", encoding="utf-8")
    try:
        assert registre._importer_fichier("voisin", dossier / "gtk4.py").name == "alpha"
        assert sys.modules["gcm4_plugin_voisin.gtk4"].LU == 42
        assert sys.modules["gcm4_plugin_voisin.core"].VALEUR == 42
        assert sys.modules["gcm4_plugin_voisin"].__path__ == [str(dossier)]
        assert getattr(sys.modules["gcm4_plugin_voisin"], "INIT", False) is avec_init
    finally:
        registre._oublier_paquet("gcm4_plugin_voisin")


@pytest.mark.parametrize(
    ("init", "gtk4"),
    [
        ("", "from . import core\nraise RuntimeError('gtk4')\n"),
        ("raise RuntimeError('init')\n", "X = 1\n"),
    ],
)
def test_echec_retire_tout_le_paquet(tmp_path, init, gtk4):
    """Import manqué (dans gtk4.py ou __init__.py) : ni le paquet ni core ne restent chargés."""
    dossier = tmp_path / "casse"
    dossier.mkdir()
    (dossier / "__init__.py").write_text(init, encoding="utf-8")
    (dossier / "core.py").write_text("", encoding="utf-8")
    (dossier / "gtk4.py").write_text(gtk4, encoding="utf-8")
    with pytest.raises(RuntimeError):
        registre._importer_fichier("casse", dossier / "gtk4.py")
    assert not [n for n in sys.modules if n.startswith("gcm4_plugin_casse")]
    sys.modules["gcm4_plugin_casse_autre"] = sys
    try:
        registre._oublier_paquet("gcm4_plugin_casse")
        assert sys.modules["gcm4_plugin_casse_autre"] is sys
    finally:
        del sys.modules["gcm4_plugin_casse_autre"]


class _EntryPoints310:
    """Imite ``metadata.entry_points()`` de Python ≥ 3.10 (méthode ``select``)."""

    def __init__(self, points):
        self._points = points

    def select(self, *, group):
        """Points du groupe demandé."""
        return [p for p in self._points if p.group == group]


def _point_de_groupe(nom, groupe):
    """Point d'entrée ``nom`` du groupe ``groupe`` (valeur factice)."""
    return metadata.EntryPoint(nom, f"module_{nom}:Plugin", groupe)


def test_points_d_entree_api_python_310(monkeypatch):
    """Python ≥ 3.10 : ``select(group=...)`` ; les autres groupes sont ignorés."""
    points = [_point_de_groupe("b", registre.GROUPE), _point_de_groupe("x", "autre.groupe")]
    points.append(_point_de_groupe("a", registre.GROUPE))
    monkeypatch.setattr(registre.metadata, "entry_points", lambda: _EntryPoints310(points))
    assert [c.nom for c in registre.candidats_points_d_entree()] == ["a", "b"]


def test_points_d_entree_api_python_39(monkeypatch):
    """Python 3.9 (Rocky 9, #223) : ``entry_points()`` rend un dict groupe → points."""
    par_groupe = {
        registre.GROUPE: (
            _point_de_groupe("b", registre.GROUPE),
            _point_de_groupe("a", registre.GROUPE),
        ),
        "autre.groupe": (_point_de_groupe("x", "autre.groupe"),),
    }
    monkeypatch.setattr(registre.metadata, "entry_points", lambda: par_groupe)
    assert [c.nom for c in registre.candidats_points_d_entree()] == ["a", "b"]
    monkeypatch.setattr(registre.metadata, "entry_points", lambda: {})
    assert registre.candidats_points_d_entree() == []
