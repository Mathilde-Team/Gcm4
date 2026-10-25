"""Nom de l'organisation GitHub dans les fichiers suivis (issue #310).

L'ancien nom mal orthographié de l'organisation ne correspond à aucun compte
GitHub : quiconque l'enregistrerait recevrait les installations de la CI, les
liens de la documentation et les rapports de traduction. Aucun fichier suivi
ne doit donc le contenir.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Assemblé en deux morceaux pour que ce fichier ne contienne pas le nom fautif.
NOM_FAUTIF = "Mathide" + "-Team"
NOM_CORRECT = "Mathilde-Team"


def _fichiers_suivis() -> list[Path]:
    """Liste les fichiers suivis par git, ou saute le test hors dépôt."""
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("dépôt git indisponible")
    sortie = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [ROOT / nom.decode() for nom in sortie.split(b"\0") if nom]


def test_aucun_fichier_suivi_ne_contient_le_nom_fautif() -> None:
    """Le nom fautif n'apparaît ni dans le code, ni dans les catalogues."""
    fautif = NOM_FAUTIF.encode()
    fautifs = [
        str(chemin.relative_to(ROOT))
        for chemin in _fichiers_suivis()
        if chemin.is_file() and fautif in chemin.read_bytes()
    ]
    assert fautifs == []


def test_la_ci_installe_asyncvnc2_depuis_l_organisation() -> None:
    """La dépendance asyncvnc2 de la CI vient bien de l'organisation."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"github.com/{NOM_CORRECT}/AsyncVnc4@" in ci


def test_le_job_i18n_cible_le_bon_depot() -> None:
    """La condition du job de mise à jour des catalogues peut être vraie."""
    i18n = (ROOT / ".github" / "workflows" / "i18n.yml").read_text(encoding="utf-8")
    assert f"github.repository == '{NOM_CORRECT}/Gcm4'" in i18n
