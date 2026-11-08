# SPDX-License-Identifier: GPL-3.0-or-later
"""La suite native VNC reste obligatoire, mesurée et publiée avant le seuil combiné."""

from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parents[1]


def test_suite_vnc_native_et_widget_dans_job_requis():
    """Aucun test natif oublié ; la couverture exclut seulement le code de test lui-même."""
    workflow = yaml.safe_load((RACINE / ".github/workflows/ci.yml").read_text())
    etapes = workflow["jobs"]["gtk4"]["steps"]
    indice_native = next(
        i for i, e in enumerate(etapes) if "pytest tests/gtk4 tests/vnc " in e.get("run", "")
    )
    native = etapes[indice_native]
    assert native["env"]["GCM_TESTS_VNC"] == "1"
    assert native["env"]["GCM_TESTS_GTK4"] == "1"
    assert native["env"]["COVERAGE_FILE"] == ".coverage.gtk4"
    indice_widget = next(
        i for i, e in enumerate(etapes) if "plugins/pluginvnc2/tests " in e.get("run", "")
    )
    widget = etapes[indice_widget]
    assert widget["env"]["COVERAGE_FILE"] == ".coverage.gtk4"
    assert "--cov=vnc_tab --cov-config=pyproject.toml --cov-append" in widget["run"]
    assert "sautes" in native["run"] and "sautes" in widget["run"]
    indice_artifact = next(
        i for i, e in enumerate(etapes) if e.get("with", {}).get("name") == "coverage-data-gtk4"
    )
    assert indice_native < indice_widget < indice_artifact


def test_suite_native_ne_masque_pas_une_dependance_absente():
    """Le mode obligatoire ne saute aucun test : les imports réels échouent normalement."""
    conftest = (RACINE / "tests/vnc/conftest.py").read_text()
    obligatoire = {"os": __import__("os")}
    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {"GCM_TESTS_VNC": "1"}):
        exec(compile(conftest, "conftest_vnc", "exec"), obligatoire)
    assert "collect_ignore_glob" not in obligatoire
    assert "skip" not in conftest
