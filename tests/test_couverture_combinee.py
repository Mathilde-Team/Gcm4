# SPDX-License-Identifier: GPL-3.0-or-later
"""scripts/couverture_combinee.py : détail de la couverture fusionnée (issue #163)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import coverage
import pytest
from coverage.data import CoverageData

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "couverture_combinee", ROOT / "scripts" / "couverture_combinee.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture
def depot(tmp_path, monkeypatch):
    """Mini-dépôt : un module ui (4 lignes) et un module core (2 lignes).

    Données « qualite » : core entièrement exécuté, ui jamais importé
    (coverage.py enregistre alors les fichiers de ``source`` non exécutés
    sans aucune ligne : ils comptent à 0 %). Données « gtk4 » : ui à moitié,
    core absent des données.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nsource = ["src/gcm4"]\nrelative_files = true\n', encoding="utf-8"
    )
    ui = tmp_path / "src" / "gcm4" / "ui"
    core = tmp_path / "src" / "gcm4" / "core"
    for dossier in (tmp_path / "src" / "gcm4", ui, core):
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "__init__.py").write_text("", encoding="utf-8")
    (ui / "fenetre.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n", encoding="utf-8")
    (core / "modele.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    donnees = tmp_path / "donnees"
    donnees.mkdir()
    qualite = CoverageData(str(donnees / ".coverage.qualite"))
    qualite.add_lines(
        {
            "src/gcm4/__init__.py": [],
            "src/gcm4/core/__init__.py": [],
            "src/gcm4/core/modele.py": [1, 2],
            "src/gcm4/ui/__init__.py": [],
            "src/gcm4/ui/fenetre.py": [],
        }
    )
    qualite.write()
    gtk4 = CoverageData(str(donnees / ".coverage.gtk4"))
    gtk4.add_lines({"src/gcm4/ui/__init__.py": [], "src/gcm4/ui/fenetre.py": [1, 2]})
    gtk4.write()
    combinee = coverage.Coverage(data_file=str(tmp_path / ".coverage"))
    combinee.combine(
        [str(donnees / ".coverage.qualite"), str(donnees / ".coverage.gtk4")], keep=True
    )
    combinee.save()
    return donnees


def test_total_par_perimetre(depot):
    """Chaque jeu de données donne sa couverture ; ui absent du job Qualité = 0 %."""
    qualite = str(depot / ".coverage.qualite")
    gtk4 = str(depot / ".coverage.gtk4")
    assert module.total(qualite, "src/gcm4/core/*") == pytest.approx(100.0)
    assert module.total(qualite, "src/gcm4/ui/*") == pytest.approx(0.0)
    assert module.total(gtk4, "src/gcm4/ui/*") == pytest.approx(50.0)
    assert module.total(".coverage", "src/gcm4/*") == pytest.approx(400 / 6)


def test_perimetre_sans_fichier(depot):
    """Aucun fichier du périmètre : ``None`` (« non exécuté »), pas 0 %."""
    assert module.total(".coverage", "src/inexistant/*") is None
    assert module.cellule(None) == "non exécuté"
    assert module.cellule(92.24) == "92.2 %"


def test_tableau_markdown(depot):
    """Le résumé montre ce que GTK4 ajoute : ui passe de 0 % à 50 %."""
    texte = module.tableau(
        str(depot / ".coverage.qualite"), str(depot / ".coverage.gtk4"), ".coverage"
    )
    assert "Total combiné : 66.7 %" in texte
    assert "| `src/gcm4/ui/*` | 0.0 % | 50.0 % | 50.0 % |" in texte
    assert "| `src/gcm4/core/*` | 100.0 % | non exécuté | 100.0 % |" in texte


def test_donnees_par_job_conservees(depot, capsys):
    """Le script ne fusionne ni ne supprime les fichiers de chaque job."""
    assert (
        module.main(
            [
                "--qualite",
                str(depot / ".coverage.qualite"),
                "--gtk4",
                str(depot / ".coverage.gtk4"),
                "--combinee",
                ".coverage",
            ]
        )
        == 0
    )
    assert "Couverture combinée" in capsys.readouterr().out
    assert (depot / ".coverage.qualite").exists()
    assert (depot / ".coverage.gtk4").exists()


def test_workflow_fusionne_les_deux_jobs():
    """ci.yml : données nommées par job, publiées, fusionnées hors du dossier courant."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "COVERAGE_FILE: .coverage.qualite" in ci
    assert "COVERAGE_FILE: .coverage.gtk4" in ci
    assert ci.count("include-hidden-files: true") >= 2
    assert "name: Couverture combinée" in ci
    assert "needs: [quality, gtk4]" in ci
    assert "path: donnees-couverture" in ci
    assert "uv run coverage report --skip-covered" in ci


def test_chemins_relatifs():
    """relative_files : les chemins du conteneur GTK4 et du runner se fusionnent."""
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    run = config["tool"]["coverage"]["run"]
    assert run["relative_files"] is True
    assert "src/gcm4" in run["source"]
