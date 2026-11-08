# SPDX-License-Identifier: GPL-3.0-or-later
"""Le seuil doit porter sur chaque mesure, sans se laisser tromper par le total."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verifier_couverture", RACINE / "scripts" / "verifier_couverture.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def rapport(lignes=95, branches=95, total_lignes=100, total_branches=100):
    """Un total combiné flatteur n'a volontairement aucun effet sur le contrôle."""
    return {
        "meta": {"branch_coverage": True},
        "totals": {
            "covered_lines": lignes,
            "num_statements": total_lignes,
            "covered_branches": branches,
            "num_branches": total_branches,
            "percent_covered": 99.9,
        },
    }


@pytest.mark.parametrize(
    "donnees,statut",
    [
        (rapport(), 0),
        (rapport(100, 100), 0),
        (rapport(94, 100), 1),
        (rapport(100, 94), 1),
        (rapport(94, 94), 1),
        (rapport(100, 94_999, total_branches=100_000), 1),
        (rapport(94_999, 100, total_lignes=100_000), 1),
        (rapport(95, 0, total_branches=0), 0),
    ],
)
def test_seuil_independant_sans_arrondi(donnees, statut, capsys):
    """95 exact passe, 94,999 échoue même si la moyenne annoncée est à 99,9."""
    assert module.verifier(donnees, 95) == statut
    sortie = capsys.readouterr().out
    assert "Lignes :" in sortie and "Branches :" in sortie
    assert ("ÉCHEC" in sortie) is bool(statut)


@pytest.mark.parametrize(
    "donnees",
    [
        {"meta": {"branch_coverage": False}},
        rapport(0, 0, 0, 0),
        rapport(-1, 100),
        rapport(101, 100),
        rapport(100, -1),
        rapport(100, 101),
    ],
)
def test_rapport_invalide_refuse(donnees):
    """Absence de branches mesurées ou compteurs incohérents ne valent pas succès."""
    with pytest.raises(ValueError):
        module.verifier(donnees, 95)


@pytest.mark.parametrize("seuil,statut", [(81, 0), (95, 1), (100, 1), (0, 2)])
def test_main_lit_le_seuil_du_pyproject(tmp_path, monkeypatch, seuil, statut):
    """Une seule configuration pilote la commande, sans constante 95 dupliquée."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.coverage.report]\nfail_under = {seuil}\n", encoding="utf-8"
    )
    fichier = tmp_path / "rapport.json"
    fichier.write_text(json.dumps(rapport(100, 94)), encoding="utf-8")
    assert module.main([str(fichier)]) == statut


@pytest.mark.parametrize(
    "contenu", [None, "pas du JSON", "{}", '{"meta":{"branch_coverage":false}}']
)
def test_main_erreur_de_donnees(tmp_path, monkeypatch, contenu):
    """Un artefact absent ou mal formé échoue avec le statut explicite 2."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.coverage.report]\nfail_under = 95\n", encoding="utf-8"
    )
    fichier = tmp_path / "rapport.json"
    if contenu is not None:
        fichier.write_text(contenu, encoding="utf-8")
    assert module.main([str(fichier)]) == 2


def test_ci_verifie_les_deux_mesures_apres_fusion():
    """Le job requis conserve son rapport combiné et y ajoute le contrôle séparé."""
    ci = (RACINE / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "coverage json -o coverage-combinee.json --fail-under=0" in ci
    assert "python scripts/verifier_couverture.py coverage-combinee.json" in ci
    assert "coverage report --skip-covered" in ci
    assert ci.index("coverage combine --keep") < ci.index("verifier_couverture.py")
    assert "set -o pipefail" in ci.split("Total combiné, seuil bloquant")[1]
    makefile = (RACINE / "Makefile").read_text(encoding="utf-8")
    assert "coverage-combined:" in makefile
    assert "python scripts/verifier_couverture.py coverage-combinee.json" in makefile


def test_seuil_du_depot_ne_redescend_pas():
    """Le palier global atteint ne peut pas être ramené sous 95 %."""
    config = module.coverage.Coverage(config_file=str(RACINE / "pyproject.toml"))
    seuil = config.get_option("report:fail_under")
    assert isinstance(seuil, (int, float)) and 95 <= seuil <= 100
