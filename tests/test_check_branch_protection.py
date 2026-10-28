# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests de scripts/check_branch_protection.py (protection de dev, issue #161, repris de Netcross #334).

Reseau exclu, comme pour scripts/pr_coverage_comment.py : l'etat de la
protection est fourni sous forme de reponses d'API figees. Le test
d'integration hors ligne (regle versionnee <-> vrais workflows du depot)
tourne sur chaque PR : il empeche de renommer un job requis sans mettre la
regle a jour, ce qui bloquerait toutes les PR vers dev.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _charger_module():
    chemin = REPO_ROOT / "scripts" / "check_branch_protection.py"
    spec = importlib.util.spec_from_file_location("check_branch_protection", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_branch_protection"] = module
    spec.loader.exec_module(module)
    return module


cbp = _charger_module()

REGLE_DEV = REPO_ROOT / ".github" / "branch-protection" / "dev.json"


def _regle(**surcharges):
    regle = {
        "required_status_checks": {
            "strict": True,
            "checks": [{"context": "Qualité"}, {"context": "Paquets"}],
        },
        "enforce_admins": True,
        "required_linear_history": False,
    }
    regle.update(surcharges)
    return regle


def _branche(protegee=True, contexts=("Qualité", "Paquets"), niveau="everyone"):
    return {
        "name": "dev",
        "protected": protegee,
        "protection": {
            "enabled": protegee,
            "required_status_checks": {"contexts": list(contexts), "enforcement_level": niveau},
        },
    }


def _protection(strict=True, lineaire=False):
    return {
        "required_status_checks": {"strict": strict},
        "required_linear_history": {"enabled": lineaire},
    }


# ---------------------------------------------------------------- regle


def test_regle_versionnee_valide():
    """Regle versionnee valide."""
    regle = cbp.charger_regle(REGLE_DEV)
    assert "Qualité (lint, tests)" in cbp.contextes_requis(regle)
    # critere d'acceptation de #334 : branche a jour exigee
    assert regle["required_status_checks"]["strict"] is True
    # les releases dev -> main et les reconciliations (#373, #461) sont des
    # merge commits : un historique lineaire exige les refuserait
    assert regle["required_linear_history"] is False


def test_regle_versionnee_est_une_charge_utile_complete():
    """PUT /branches/{b}/protection exige ces quatre cles, meme a null."""
    regle = json.loads(REGLE_DEV.read_text(encoding="utf-8"))
    for cle in (
        "required_status_checks",
        "enforce_admins",
        "required_pull_request_reviews",
        "restrictions",
    ):
        assert cle in regle


@pytest.mark.parametrize(
    ("contenu", "message"),
    [
        ("{", "illisible"),
        ("{}", "required_status_checks.checks absent"),
        (
            '{"required_status_checks": {"strict": true, "checks": []}, "enforce_admins": true}',
            "aucun check",
        ),
        (
            '{"required_status_checks": {"strict": true, "checks": [{}]}, "enforce_admins": true}',
            "sans 'context'",
        ),
        (
            '{"required_status_checks": {"checks": [{"context": "Q"}]}, "enforce_admins": true}',
            "strict",
        ),
        (
            '{"required_status_checks": {"strict": true, "checks": [{"context": "Q"}]}}',
            "enforce_admins",
        ),
    ],
)
def test_regle_invalide(tmp_path, contenu, message):
    """Regle invalide."""
    chemin = tmp_path / "dev.json"
    chemin.write_text(contenu, encoding="utf-8")
    with pytest.raises(cbp.RegleInvalideError, match=message):
        cbp.charger_regle(chemin)


def test_regle_absente(tmp_path):
    """Regle absente."""
    with pytest.raises(cbp.RegleInvalideError, match="introuvable"):
        cbp.charger_regle(tmp_path / "absent.json")


# ------------------------------------------------------- hors ligne


def test_regle_coherente_avec_les_vrais_workflows():
    """Chaque check requis correspond a un job qui tourne sur les PR vers dev."""
    regle = cbp.charger_regle(REGLE_DEV)
    workflows = cbp.lire_workflows(REPO_ROOT / ".github" / "workflows")
    assert cbp.verifier_hors_ligne(regle, workflows, "dev") == []


def test_job_renomme_detecte():
    """Job renomme detecte."""
    workflows = [
        {"on": {"pull_request": {"branches": ["dev"]}}, "jobs": {"q": {"name": "Qualité v2"}}}
    ]
    ecarts = cbp.verifier_hors_ligne(_regle(), workflows, "dev")
    assert any("« Qualité »" in e and "bloquees" in e for e in ecarts)


@pytest.mark.parametrize(
    ("declencheur", "attendu"),
    [
        ("pull_request", True),
        (["push", "pull_request"], True),
        ({"pull_request": None}, True),
        ({"pull_request": {"branches": ["main", "dev"]}}, True),
        ({"pull_request": {"branches": ["main"]}}, False),
        ({"pull_request": {"branches-ignore": ["dev"]}}, False),
        ({"push": {"branches": ["dev"]}}, False),
        ("push", False),
    ],
)
def test_tourne_sur_pr(declencheur, attendu):
    """Tourne sur pr."""
    assert cbp.tourne_sur_pr({"on": declencheur}, "dev") is attendu


def test_cle_on_lue_comme_booleen_par_yaml():
    """YAML 1.1 lit une cle `on` non quotee comme True."""
    import yaml

    workflow = yaml.safe_load("on:\n  pull_request:\njobs:\n  a:\n    name: A\n")
    assert True in workflow
    assert cbp.jobs_de_pr([workflow], "dev") == {"A"}


def test_job_conditionne_exclu_et_nom_par_defaut():
    """Job conditionne exclu et nom par defaut."""
    workflow = {
        "on": "pull_request",
        "jobs": {
            "sans_nom": {},
            "conditionne": {"name": "Publication", "if": "github.ref == 'x'"},
        },
    }
    assert cbp.jobs_de_pr([workflow], "dev") == {"sans_nom"}


# ------------------------------------------------------- etat reel


def test_conforme():
    """Conforme."""
    assert cbp.comparer(_regle(), _branche(), _protection()) == ([], [])


def test_branche_non_protegee():
    """Etat constate le 26/09/2026 : dev.protected == false."""
    ecarts, _ = cbp.comparer(_regle(), _branche(protegee=False, contexts=(), niveau="off"), None)
    assert ecarts == ["la branche dev n'est pas protegee"]


def test_check_manquant_et_check_en_trop():
    """Check manquant et check en trop."""
    ecarts, _ = cbp.comparer(_regle(), _branche(contexts=("Qualité", "Autre")), _protection())
    assert "check requis absent de la protection : « Paquets »" in ecarts
    assert "check requis en plus de la regle versionnee : « Autre »" in ecarts


def test_administrateurs_exemptes():
    """L'incident de #334 (PR fusionnees CI rouge) venait d'un administrateur."""
    ecarts, _ = cbp.comparer(_regle(), _branche(niveau="non_admins"), _protection())
    assert any("non_admins" in e and "everyone" in e for e in ecarts)


def test_enforce_admins_false_attend_non_admins():
    """Enforce admins false attend non admins."""
    assert cbp.comparer(
        _regle(enforce_admins=False), _branche(niveau="non_admins"), _protection()
    ) == ([], [])


def test_strict_non_verifiable_sans_droit_admin():
    """Strict non verifiable sans droit admin."""
    ecarts, non_verifiables = cbp.comparer(_regle(), _branche(), None)
    assert ecarts == []
    assert len(non_verifiables) == 1 and "strict" in non_verifiables[0]


def test_strict_desactive():
    """Strict desactive."""
    ecarts, _ = cbp.comparer(_regle(), _branche(), _protection(strict=False))
    assert ecarts == ["branche a jour exigee (strict) : False au lieu de True"]


def test_historique_lineaire_refuse():
    """Historique lineaire refuse."""
    ecarts, _ = cbp.comparer(_regle(), _branche(), _protection(lineaire=True))
    assert len(ecarts) == 1 and "merge commits" in ecarts[0]


# ------------------------------------------------------------- main


def test_main_hors_ligne_conforme(capsys):
    """Main hors ligne conforme."""
    assert cbp.main(["--hors-ligne"]) == cbp.CONFORME
    assert "conforme" in capsys.readouterr().out


def test_main_ecart_affiche_la_commande_a_appliquer(monkeypatch, capsys):
    """Main ecart affiche la commande a appliquer."""
    monkeypatch.setattr(
        cbp, "verifier_en_ligne", lambda *a: (["la branche dev n'est pas protegee"], [])
    )
    assert cbp.main(["--depot", "o/r"]) == cbp.ECART
    sortie = capsys.readouterr().out
    assert "::error::la branche dev n'est pas protegee" in sortie
    assert (
        "gh api -X PUT repos/o/r/branches/dev/protection --input .github/branch-protection/dev.json"
        in sortie
    )


def test_main_avertit_sans_echouer_sur_le_non_verifiable(monkeypatch, capsys):
    """Main avertit sans echouer sur le non verifiable."""
    monkeypatch.setattr(cbp, "verifier_en_ligne", lambda *a: ([], ["strict non lisible"]))
    assert cbp.main([]) == cbp.CONFORME
    assert "::warning::strict non lisible" in capsys.readouterr().out


def test_main_erreur_api(monkeypatch, capsys):
    """Main erreur api."""

    def boom(*_a):
        raise RuntimeError("branche dev introuvable")

    monkeypatch.setattr(cbp, "verifier_en_ligne", boom)
    assert cbp.main([]) == cbp.ERREUR
    assert "::error::branche dev introuvable" in capsys.readouterr().out


def test_main_regle_invalide(tmp_path):
    """Main regle invalide."""
    chemin = tmp_path / "dev.json"
    chemin.write_text("[]", encoding="utf-8")
    assert cbp.main(["--regle", str(chemin), "--hors-ligne"]) == cbp.ERREUR


@pytest.mark.parametrize("code", [401, 403, 404])
def test_get_renvoie_none_sans_acces(monkeypatch, code):
    """401 : constate sans jeton sur /protection (26/09/2026)."""
    import urllib.error

    def refuse(requete, timeout):
        raise urllib.error.HTTPError(requete.full_url, code, "refus", {}, None)

    monkeypatch.setattr(cbp.urllib.request, "urlopen", refuse)
    assert cbp._get("https://api.github.com/x", "jeton") is None


def test_get_propage_les_autres_erreurs(monkeypatch):
    """Get propage les autres erreurs."""
    import urllib.error

    def panne(requete, timeout):
        raise urllib.error.HTTPError(requete.full_url, 500, "panne", {}, None)

    monkeypatch.setattr(cbp.urllib.request, "urlopen", panne)
    with pytest.raises(urllib.error.HTTPError):
        cbp._get("https://api.github.com/x", None)


def test_verifier_en_ligne_sans_acces_admin(monkeypatch):
    """Verifier en ligne sans acces admin."""
    reponses = {
        "https://api.github.com/repos/o/r/branches/dev": _branche(),
        "https://api.github.com/repos/o/r/branches/dev/protection": None,
    }
    monkeypatch.setattr(cbp, "_get", lambda url, jeton: copy.deepcopy(reponses[url]))
    ecarts, non_verifiables = cbp.verifier_en_ligne(_regle(), "o/r", "dev", None)
    assert ecarts == [] and non_verifiables


def test_verifier_en_ligne_branche_introuvable(monkeypatch):
    """Verifier en ligne branche introuvable."""
    monkeypatch.setattr(cbp, "_get", lambda url, jeton: None)
    with pytest.raises(RuntimeError, match="introuvable"):
        cbp.verifier_en_ligne(_regle(), "o/r", "dev", None)
