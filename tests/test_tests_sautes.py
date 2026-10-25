"""Un test sauté alors que sa dépendance est installée échoue (issue #160).

Le mécanisme est dans ``tests/exigences.py`` : marqueurs ``requires_gettext``
et ``requires_gtk4``, variable ``GCM_TESTS_EXIGES``. Les cas de bout en bout
lancent un vrai pytest dans un sous-processus, sur un module de test écrit
dans un dossier temporaire, avec ce module chargé comme greffon et les
marqueurs de ``pyproject.toml``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from exigences import MARQUEURS_DEPENDANCES, dependances_exigees

ROOT = Path(__file__).resolve().parent.parent
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

MODULE = textwrap.dedent(
    """
    import pytest

    @pytest.mark.requires_gettext
    def test_gettext_saute():
        pytest.skip("gettext non installé")

    @pytest.mark.requires_gtk4
    def test_gtk4_saute():
        pytest.skip("GTK4 absent")

    def test_sans_marqueur_saute():
        pytest.skip("autre raison")

    @pytest.mark.requires_gettext
    @pytest.mark.xfail(reason="attendu")
    def test_xfail_marque():
        raise AssertionError

    @pytest.mark.requires_gettext
    def test_gettext_passe():
        pass
    """
)


def _lancer(tmp_path: Path, **env: str) -> dict[str, str]:
    """Lance pytest sur ``MODULE`` ; renvoie l'issue de chaque test (sortie ``-v``)."""
    (tmp_path / "test_module.py").write_text(MODULE, encoding="utf-8")
    environ = {
        k: v for k, v in os.environ.items() if k not in ("GCM_TESTS_EXIGES", "GCM_TESTS_GTK4")
    }
    environ.update(env)
    environ["PYTHONPATH"] = str(ROOT / "tests")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "exigences",
            "-c",
            str(ROOT / "pyproject.toml"),
            "--rootdir",
            str(tmp_path),
            "-p",
            "no:cacheprovider",
            "-v",
            str(tmp_path / "test_module.py"),
        ],
        capture_output=True,
        text=True,
        env=environ,
        cwd=tmp_path,
        check=False,
    )
    issues = dict(re.findall(r"::(test_\w+) (PASSED|FAILED|SKIPPED|XFAIL)", proc.stdout))
    assert issues, proc.stdout + proc.stderr
    return issues


def test_sans_declaration_les_sauts_restent_des_sauts(tmp_path):
    """Hors job déclaré, un test marqué peut être sauté (poste de développement)."""
    issues = _lancer(tmp_path)
    assert issues["test_gettext_saute"] == "SKIPPED"
    assert issues["test_gtk4_saute"] == "SKIPPED"
    assert issues["test_sans_marqueur_saute"] == "SKIPPED"
    assert issues["test_gettext_passe"] == "PASSED"


def test_gettext_declare_fait_echouer_le_saut_marque(tmp_path):
    """``GCM_TESTS_EXIGES=gettext`` : seul le test ``requires_gettext`` sauté échoue."""
    issues = _lancer(tmp_path, GCM_TESTS_EXIGES="gettext")
    assert issues["test_gettext_saute"] == "FAILED"
    assert issues["test_gtk4_saute"] == "SKIPPED"
    assert issues["test_sans_marqueur_saute"] == "SKIPPED"
    assert issues["test_xfail_marque"] == "XFAIL"
    assert issues["test_gettext_passe"] == "PASSED"


def test_gcm_tests_gtk4_vaut_gtk4(tmp_path):
    """``GCM_TESTS_GTK4=1`` (job GTK4) suffit à exiger GTK4."""
    issues = _lancer(tmp_path, GCM_TESTS_GTK4="1")
    assert issues["test_gtk4_saute"] == "FAILED"
    assert issues["test_gettext_saute"] == "SKIPPED"


def test_les_deux_dependances(tmp_path):
    """Liste séparée par des virgules, espaces tolérés."""
    issues = _lancer(tmp_path, GCM_TESTS_EXIGES=" gettext , gtk4 ")
    assert issues["test_gettext_saute"] == "FAILED"
    assert issues["test_gtk4_saute"] == "FAILED"
    assert issues["test_sans_marqueur_saute"] == "SKIPPED"


def test_raison_d_origine_dans_l_echec(tmp_path):
    """Le message d'échec donne la dépendance déclarée et la raison du saut."""
    (tmp_path / "test_module.py").write_text(MODULE, encoding="utf-8")
    environ = {**os.environ, "GCM_TESTS_EXIGES": "gettext", "PYTHONPATH": str(ROOT / "tests")}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "exigences", "-c", str(ROOT / "pyproject.toml")]
        + ["--rootdir", str(tmp_path), "-p", "no:cacheprovider", "-q"]
        + [str(tmp_path / "test_module.py") + "::test_gettext_saute"],
        capture_output=True,
        text=True,
        env=environ,
        cwd=tmp_path,
        check=False,
    )
    assert proc.returncode == 1, proc.stdout
    assert "GCM_TESTS_EXIGES déclare gettext installé" in proc.stdout
    assert "gettext non installé" in proc.stdout


def test_marqueur_inconnu_refuse(tmp_path):
    """``--strict-markers`` : un marqueur mal orthographié n'est jamais ignoré en silence."""
    (tmp_path / "test_module.py").write_text(
        "import pytest\n\n@pytest.mark.requires_getext\ndef test_x():\n    pass\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(ROOT / "pyproject.toml")]
        + ["--rootdir", str(tmp_path), "-p", "no:cacheprovider", "-q", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert proc.returncode != 0
    assert "requires_getext" in proc.stdout + proc.stderr


@pytest.mark.parametrize(
    ("environ", "attendu"),
    [
        ({}, set()),
        ({"GCM_TESTS_EXIGES": ""}, set()),
        ({"GCM_TESTS_EXIGES": "gettext"}, {"gettext"}),
        ({"GCM_TESTS_EXIGES": "gettext,gtk4"}, {"gettext", "gtk4"}),
        ({"GCM_TESTS_GTK4": "1"}, {"gtk4"}),
        ({"GCM_TESTS_GTK4": "0"}, set()),
        ({"GCM_TESTS_EXIGES": "gettext", "GCM_TESTS_GTK4": "1"}, {"gettext", "gtk4"}),
    ],
)
def test_dependances_exigees(environ, attendu):
    """Lecture de ``GCM_TESTS_EXIGES`` et ``GCM_TESTS_GTK4``."""
    assert dependances_exigees(environ) == attendu


def test_dependance_inconnue_refusee():
    """Une faute de frappe dans ``GCM_TESTS_EXIGES`` arrête pytest au lieu de ne rien exiger."""
    with pytest.raises(pytest.UsageError, match="gtk"):
        dependances_exigees({"GCM_TESTS_EXIGES": "gtk"})


def test_marqueurs_declares_dans_pyproject():
    """Chaque marqueur du conftest est déclaré dans ``[tool.pytest.ini_options]``."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for marqueur in MARQUEURS_DEPENDANCES:
        assert f'"{marqueur}:' in pyproject


def test_la_ci_declare_ses_dependances():
    """Le job Qualité (gettext installé) exige gettext ; le job GTK4 exige GTK4."""
    assert re.search(r'GCM_TESTS_EXIGES: "gettext"', CI)
    assert re.search(r'GCM_TESTS_GTK4: "1"', CI)


def test_plus_aucun_saut_tolere_dans_la_ci():
    """src/gcm4 existe : la tolérance « src/gcm4 pas encore créé » a disparu (#160)."""
    assert "pas encore créé" not in CI
    assert "pas encore créé" not in (ROOT / "tests" / "test_architecture_imports.py").read_text(
        encoding="utf-8"
    )
