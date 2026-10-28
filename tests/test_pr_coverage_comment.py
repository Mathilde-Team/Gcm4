# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests de scripts/pr_coverage_comment.py (issue #128), sans réseau."""

from __future__ import annotations

import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "pr_coverage_comment", ROOT / "scripts" / "pr_coverage_comment.py"
)
pcc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pcc)


def _rapport(pct, lignes=(50, 100), branches=(5, 10), fichiers=None):
    """Rapport coverage.py JSON minimal."""
    return {
        "totals": {
            "percent_covered": pct,
            "covered_lines": lignes[0],
            "num_statements": lignes[1],
            "covered_branches": branches[0],
            "num_branches": branches[1],
        },
        "files": {nom: {"summary": {"percent_covered": v}} for nom, v in (fichiers or {}).items()},
    }


def test_seuil_lu_dans_le_pyproject_du_depot():
    """Le fail_under du dépôt est lu et plausible."""
    seuil = pcc.lire_seuil()
    assert seuil is not None and 0 < seuil <= 100


def test_seuil_absent_ou_fichier_illisible(tmp_path):
    """Section absente ou fichier introuvable : aucun seuil."""
    vide = tmp_path / "pyproject.toml"
    vide.write_text("[tool.ruff]\nline-length = 99\n", encoding="utf-8")
    assert pcc.lire_seuil(str(vide)) is None
    assert pcc.lire_seuil(str(tmp_path / "absent.toml")) is None


def test_seuil_hors_section_ignore(tmp_path):
    """Un fail_under hors de [tool.coverage.report] est ignoré."""
    f = tmp_path / "pyproject.toml"
    f.write_text("[autre]\nfail_under = 99\n[tool.coverage.report]\nprecision = 1\n")
    assert pcc.lire_seuil(str(f)) is None


def test_commentaire_totaux_et_delta():
    """Totaux, delta et marge au seuil dans le tableau."""
    corps = pcc.construire_commentaire(
        _rapport(21.04, (60, 100), (6, 10)), _rapport(19.7, (55, 100), (5, 10)), 18.0
    )
    assert corps.startswith(pcc.MARQUEUR)
    assert "| Couverture | 19.7 % | 21.0 % | +1.3 pt |" in corps
    assert "| Instructions couvertes | 55/100 | 60/100 | +5 |" in corps
    assert "| Branches couvertes | 5/10 | 6/10 | +1 |" in corps
    assert "Seuil bloquant : 18.0 % (`fail_under`) sur le **total combiné**" in corps


def test_commentaire_total_partiel_jamais_compare_au_seuil():
    """Issue #278 : ce total exclut src/gcm4/ui/ ; sous le seuil, aucun faux échec annoncé."""
    corps = pcc.construire_commentaire(_rapport(28.0), _rapport(28.2), 41.0)
    assert "Sous le seuil bloquant**" not in corps
    assert "job Qualité de la CI échouera" not in corps
    assert "« Couverture combinée »" in corps


def test_commentaire_explique_le_perimetre():
    """Le commentaire ne montre plus src/gcm4/ui/ à 0 % sans explication."""
    corps = pcc.construire_commentaire(_rapport(28.0), _rapport(28.2), 41.0)
    assert pcc.PERIMETRE in corps
    assert "`src/gcm4/ui/`" in corps and "0 %" in corps


def test_commentaire_sans_seuil():
    """Sans seuil configuré, le commentaire le dit."""
    assert "Aucun seuil bloquant" in pcc.construire_commentaire(_rapport(1), _rapport(1), None)


def _fichier_core(couvertes, instructions, branches_couvertes, branches, pct=None):
    """Entrée `files` d'un rapport JSON avec le détail lignes + branches."""
    return {
        "summary": {
            "covered_lines": couvertes,
            "num_statements": instructions,
            "covered_branches": branches_couvertes,
            "num_branches": branches,
            "percent_covered": pct if pct is not None else 100.0,
        }
    }


def _totaux(pct, lignes=(60, 100), branches=(6, 10)):
    """`totals` complet d'un rapport JSON."""
    return {
        "percent_covered": pct,
        "covered_lines": lignes[0],
        "num_statements": lignes[1],
        "covered_branches": branches[0],
        "num_branches": branches[1],
    }


def test_couverture_coeur_agregee():
    """Lignes et branches du cœur sont agrégées en un pourcentage."""
    rapport = {
        "totals": _totaux(20.0),
        "files": {
            "src/gcm4/core/config.py": _fichier_core(290, 294, 84, 86),
            "src/gcm4/core/menu.py": _fichier_core(19, 19, 8, 8),
            "src/gcm4/window.py": _fichier_core(10, 100, 0, 10, pct=10.0),
        },
    }
    # (290+84+19+8) / (294+86+19+8) = 401/407 = 98,5 %
    assert pcc.couverture_coeur(rapport) == pytest.approx(100 * 401 / 407)


def test_couverture_coeur_absente():
    """Sans fichier du cœur : None, la ligne est omise du commentaire."""
    rapport = {
        "totals": _totaux(20.0),
        "files": {"src/gcm4_core.py": _fichier_core(50, 100, 0, 0, pct=50.0)},
    }
    assert pcc.couverture_coeur(rapport) is None
    corps = pcc.construire_commentaire(rapport, rapport, 18.0)
    assert "src/gcm4/core/" not in corps
    assert "seuil bloquant du cœur" not in corps


def test_commentaire_ligne_coeur_et_marge():
    """Ligne dédiée au cœur, delta, et rappel du seuil 95 % (issue #164)."""
    pr = {
        "totals": _totaux(21.0),
        "files": {"src/gcm4/core/config.py": _fichier_core(290, 294, 84, 86, pct=98.4)},
    }
    base = {
        "totals": _totaux(19.7, (55, 100), (5, 10)),
        "files": {"src/gcm4/core/config.py": _fichier_core(288, 294, 80, 86, pct=98.0)},
    }
    corps = pcc.construire_commentaire(pr, base, 18.0)
    assert "| Cœur `src/gcm4/core/` | 96.8 % | 98.4 % | +1.6 pt |" in corps
    assert "Seuil bloquant du cœur : 95.0 % (issue #164), marge 3.4 pt." in corps


def test_commentaire_coeur_sous_le_seuil():
    """Un cœur sous 95 % est signalé en gras."""
    pr = {
        "totals": _totaux(21.0),
        "files": {"src/gcm4/core/config.py": _fichier_core(100, 294, 0, 86, pct=26.3)},
    }
    base = json.loads(json.dumps(pr))
    corps = pcc.construire_commentaire(pr, base, 18.0)
    assert "**Sous le seuil bloquant du cœur** de 95.0 % (issue #164)" in corps


def test_fichiers_en_baisse_tries_et_nouveaux_ignores():
    """Baisses triées ; fichiers nouveaux ou stables absents."""
    pr = _rapport(10, fichiers={"a.py": 50.0, "b.py": 10.0, "c.py": 90.0, "neuf.py": 0.0})
    base = _rapport(10, fichiers={"a.py": 60.0, "b.py": 40.0, "c.py": 90.02})
    assert pcc.fichiers_en_baisse(pr, base) == [("b.py", 40.0, 10.0), ("a.py", 60.0, 50.0)]
    corps = pcc.construire_commentaire(pr, base, None)
    assert "### Fichiers dont la couverture baisse" in corps
    assert "| `b.py` | 40.0 % | 10.0 % |" in corps
    assert "neuf.py" not in corps and "c.py" not in corps


def test_liste_des_baisses_tronquee():
    """Au-delà de MAX_FICHIERS, la liste est tronquée."""
    noms = {f"f{i}.py": 10.0 for i in range(pcc.MAX_FICHIERS + 3)}
    pr = _rapport(1, fichiers=noms)
    base = _rapport(1, fichiers=dict.fromkeys(noms, 20.0))
    assert "… et 3 autre(s)" in pcc.construire_commentaire(pr, base, None)


def test_publier_met_a_jour_le_commentaire_existant(monkeypatch):
    """Le commentaire portant le marqueur est modifié (PATCH)."""
    appels = []

    def faux(chemin, token, methode="GET", corps=None):
        """Remplace l'appel réseau."""
        appels.append((methode, chemin))
        if methode == "GET":
            return [{"id": 1, "body": "autre"}, {"id": 7, "body": pcc.MARQUEUR, "html_url": "u7"}]
        return {"html_url": "x"}

    monkeypatch.setattr(pcc, "_appel_api", faux)
    assert "mis à jour (u7)" in pcc.publier("o/r", 5, "corps", "t")
    assert appels[-1] == ("PATCH", "/repos/o/r/issues/comments/7")


def test_publier_cree_sinon(monkeypatch):
    """Sans commentaire existant, un commentaire est créé."""

    def faux(chemin, token, methode="GET", corps=None):
        """Remplace l'appel réseau."""
        return [] if methode == "GET" else {"html_url": "neuf"}

    monkeypatch.setattr(pcc, "_appel_api", faux)
    assert pcc.publier("o/r", 5, "corps", "t") == "commentaire créé (neuf)"


@pytest.fixture
def rapports(tmp_path):
    """Deux rapports JSON minimaux et les arguments de main()."""
    pr, base = tmp_path / "pr.json", tmp_path / "base.json"
    pr.write_text(json.dumps(_rapport(20.0)))
    base.write_text(json.dumps(_rapport(19.0)))
    return ["--pr-json", str(pr), "--base-json", str(base), "--pr-number", "3"]


def test_main_dry_run(rapports, capsys):
    """--dry-run affiche le commentaire sans appel réseau."""
    assert pcc.main(rapports + ["--dry-run"]) == 0
    assert "+1.0 pt" in capsys.readouterr().out


def test_main_sans_environnement_github(rapports, monkeypatch):
    """Sans GITHUB_TOKEN/GITHUB_REPOSITORY : code 2."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert pcc.main(rapports) == 2


def _refus(code):
    def faux(*_a, **_k):
        raise urllib.error.HTTPError("https://api.github.com/x", code, "refus", {}, None)

    return faux


def test_main_pr_de_fork_403_resume_du_job(rapports, monkeypatch, tmp_path, capsys):
    """403 (jeton en lecture seule, PR de fork) : rapport dans le résumé, code 0."""
    resume = tmp_path / "resume.md"
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(resume))
    monkeypatch.setattr(pcc, "_appel_api", _refus(403))
    assert pcc.main(rapports) == 0
    assert "+1.0 pt" in resume.read_text(encoding="utf-8")
    assert "::warning::" in capsys.readouterr().out


def test_main_403_sans_resume_affiche(rapports, monkeypatch, capsys):
    """Hors GitHub Actions (pas de GITHUB_STEP_SUMMARY) : rapport sur la sortie."""
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(pcc, "_appel_api", _refus(403))
    assert pcc.main(rapports) == 0
    assert "+1.0 pt" in capsys.readouterr().out


def test_main_autre_erreur_http_remonte(rapports, monkeypatch):
    """Une autre erreur HTTP (500…) reste une erreur."""
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(pcc, "_appel_api", _refus(500))
    with pytest.raises(urllib.error.HTTPError):
        pcc.main(rapports)
