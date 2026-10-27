#!/usr/bin/env python3
r"""pr_coverage_comment.py -- publie le commentaire de couverture d'une PR.

Issue #128 (repris de Netcross, scripts/pr_coverage_comment.py, #224) : le
workflow .github/workflows/pr-coverage.yml exécute pytest --cov deux fois
(ref de fusion de la PR, puis SHA de base de `dev`) et passe les deux
rapports JSON de coverage.py à ce script. Celui-ci publie, ou met à jour,
un commentaire unique sur la PR : couverture totale, delta par rapport à la
base, ligne dédiée au cœur ``src/gcm4/core/`` (seuil 95 %, issue #164) et
fichiers dont la couverture baisse.

Appel :
    GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/repo \\
    python3 scripts/pr_coverage_comment.py \\
        --pr-json coverage-pr.json --base-json coverage-base.json \\
        --pr-number 123

Bibliothèque standard (urllib) plus loguru, dépendance du projet : le
workflow le lance par ``uv run``. Le seuil bloquant (``fail_under`` de pyproject.toml) est
appliqué par ci.yml ; ce script le rappelle et signale une PR qui passe
dessous.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

from loguru import logger

# Marqueur HTML invisible : identifie le commentaire à mettre à jour plutôt
# qu'à dupliquer d'un push à l'autre.
MARQUEUR = "<!-- gcm4:couverture-pr -->"

API = "https://api.github.com"

PYPROJECT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pyproject.toml"
)

# Au-delà, la liste des fichiers en baisse est tronquée (commentaire lisible).
MAX_FICHIERS = 15

# Seuil séparé du cœur de la base GTK4 (issue #164) : la base doit naître
# très couverte. Le job Qualité applique la même valeur via
# ``coverage report --include='src/gcm4/core/*' --fail-under=95`` ; la garder
# synchronisée avec docs/quality/seuil-couverture.md.
COEUR = "src/gcm4/core/"
SEUIL_COEUR = 95.0


def lire_seuil(chemin: str = PYPROJECT) -> float | None:
    """``fail_under`` de la section [tool.coverage.report], ou None.

    Lecture par expression régulière plutôt que tomllib (Python >= 3.11) :
    le projet accepte encore Python 3.10.
    """
    logger.debug("lire_seuil() | chemin={}", chemin)
    try:
        with open(chemin, encoding="utf-8") as f:
            texte = f.read()
    except OSError as exc:
        logger.warning("pyproject.toml illisible ({}) : aucun seuil", exc)
        return None
    section = re.search(r"^\[tool\.coverage\.report\]\s*$(.*?)(?=^\[|\Z)", texte, re.M | re.S)
    if section is None:
        logger.debug("lire_seuil() -> None (section absente)")
        return None
    valeur = re.search(r"^fail_under\s*=\s*([0-9.]+)\s*$", section.group(1), re.M)
    seuil = float(valeur.group(1)) if valeur else None
    logger.debug("lire_seuil() -> {}", seuil)
    return seuil


def _lire_rapport(chemin: str) -> dict:
    """Charge un rapport JSON de coverage.py."""
    logger.debug("_lire_rapport() | chemin={}", chemin)
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def _pct(valeur: float) -> str:
    """76.7421... -> '76.7 %'."""
    return f"{valeur:.1f} %"


def _delta(pr: float, base: float) -> str:
    """Différence en points de pourcentage, signée, 1 décimale."""
    return f"{pr - base:+.1f} pt"


# Ce commentaire mesure la suite hors GTK4 (job Qualité) : src/gcm4/ui/ n'y
# est pas exécuté. Le seuil global s'applique au total combiné (issue #278) ;
# comparer ce total partiel au seuil annoncerait un faux échec.
PERIMETRE = (
    "Périmètre de ce commentaire : suite hors GTK4 (job Qualité). "
    "`src/gcm4/ui/`, exécuté seulement par `tests/gtk4` sous Xvfb, y compte à 0 %."
)


def _ligne_seuil(seuil: float | None) -> str:
    """Phrase de rappel du seuil bloquant (appliqué au total combiné, pas à ce total)."""
    if seuil is None:
        return "Aucun seuil bloquant configuré (`fail_under` absent de pyproject.toml)."
    return (
        f"Seuil bloquant : {_pct(seuil)} (`fail_under`) sur le **total combiné** "
        "Qualité + GTK4, contrôlé par le job « Couverture combinée » (détail par "
        "périmètre dans le résumé de ce job, issue #278)."
    )


def couverture_coeur(rapport: dict, prefixe: str = COEUR) -> float | None:
    """Pourcentage (lignes + branches) des fichiers sous `src/gcm4/core/`.

    Issue #164 : la base doit naître très couverte ; le commentaire de PR
    affiche une ligne dédiée au cœur, en plus du total. ``None`` si le
    rapport ne contient aucun fichier du cœur (base antérieure à l'épopée
    GTK4 #118) : la ligne est alors omise.
    """
    logger.debug("couverture_coeur() | prefixe={}", prefixe)
    couvertes = instructions = 0
    for nom, donnees in rapport.get("files", {}).items():
        if not nom.startswith(prefixe):
            continue
        resume = donnees["summary"]
        couvertes += resume["covered_lines"] + resume.get("covered_branches", 0)
        instructions += resume["num_statements"] + resume.get("num_branches", 0)
    if not instructions:
        logger.debug("couverture_coeur() -> None (aucun fichier du cœur)")
        return None
    pourcentage = 100.0 * couvertes / instructions
    logger.debug("couverture_coeur() -> {}", pourcentage)
    return pourcentage


def fichiers_en_baisse(rapport_pr: dict, rapport_base: dict, tolerance: float = 0.05) -> list:
    """Fichiers présents des deux côtés dont la couverture baisse.

    Returns:
        list[tuple[str, float, float]]: (fichier, base %, PR %), triés par
        baisse décroissante. Un fichier nouveau ou supprimé n'y figure pas :
        le total le reflète déjà.
    """
    logger.debug("fichiers_en_baisse() | tolerance={}", tolerance)
    base = rapport_base.get("files", {})
    resultat = []
    for nom, donnees in rapport_pr.get("files", {}).items():
        if nom not in base:
            continue
        avant = base[nom]["summary"]["percent_covered"]
        apres = donnees["summary"]["percent_covered"]
        if apres < avant - tolerance:
            resultat.append((nom, avant, apres))
    resultat.sort(key=lambda x: x[2] - x[1])
    logger.debug("fichiers_en_baisse() -> {} fichier(s)", len(resultat))
    return resultat


def construire_commentaire(
    rapport_pr: dict, rapport_base: dict, seuil: float | None = None
) -> str:
    """Corps Markdown du commentaire de couverture."""
    logger.debug("construire_commentaire() | seuil={}", seuil)
    pr = rapport_pr["totals"]
    base = rapport_base["totals"]
    b_pr, b_base = pr.get("covered_branches", 0), base.get("covered_branches", 0)
    lignes = [
        MARQUEUR,
        "## Couverture de tests (delta vs `dev`)",
        "",
        "| Métrique | Base (`dev`) | PR | Δ |",
        "|---|---|---|---|",
        f"| Couverture | {_pct(base['percent_covered'])} | {_pct(pr['percent_covered'])} | "
        f"{_delta(pr['percent_covered'], base['percent_covered'])} |",
        f"| Instructions couvertes | {base['covered_lines']}/{base['num_statements']} | "
        f"{pr['covered_lines']}/{pr['num_statements']} | "
        f"{pr['covered_lines'] - base['covered_lines']:+d} |",
        f"| Branches couvertes | {b_base}/{base.get('num_branches', 0)} | "
        f"{b_pr}/{pr.get('num_branches', 0)} | {b_pr - b_base:+d} |",
    ]
    c_pr, c_base = couverture_coeur(rapport_pr), couverture_coeur(rapport_base)
    if c_pr is not None and c_base is not None:
        lignes.append(
            f"| Cœur `src/gcm4/core/` | {_pct(c_base)} | {_pct(c_pr)} | {_delta(c_pr, c_base)} |"
        )
    lignes += [
        "",
        PERIMETRE,
        _ligne_seuil(seuil),
    ]
    if c_pr is not None:
        if c_pr < SEUIL_COEUR:
            lignes.append(
                f"**Sous le seuil bloquant du cœur** de {_pct(SEUIL_COEUR)} "
                "(issue #164) : le job Qualité de la CI échouera."
            )
        else:
            lignes.append(
                f"Seuil bloquant du cœur : {_pct(SEUIL_COEUR)} (issue #164), "
                f"marge {c_pr - SEUIL_COEUR:.1f} pt."
            )
    baisses = fichiers_en_baisse(rapport_pr, rapport_base)
    if baisses:
        lignes += ["", "### Fichiers dont la couverture baisse", "", "| Fichier | Base | PR |"]
        lignes.append("|---|---|---|")
        for nom, avant, apres in baisses[:MAX_FICHIERS]:
            lignes.append(f"| `{nom}` | {_pct(avant)} | {_pct(apres)} |")
        if len(baisses) > MAX_FICHIERS:
            lignes.append(f"| … et {len(baisses) - MAX_FICHIERS} autre(s) | | |")
    logger.debug("construire_commentaire() -> {} ligne(s)", len(lignes))
    return "\n".join(lignes)


def _appel_api(chemin: str, token: str, methode: str = "GET", corps: str | None = None):
    """Appel REST GitHub minimal (urllib, pas de dépendance tierce)."""
    logger.debug("_appel_api() | {} {}", methode, chemin)
    requete = urllib.request.Request(
        f"{API}{chemin}",
        data=corps.encode() if corps else None,
        method=methode,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(requete) as reponse:  # noqa: S310 (URL fixe api.github.com)
        return json.loads(reponse.read())


def publier(repo: str, pr: int, corps: str, token: str) -> str:
    """Met à jour le commentaire existant (marqueur) ou en crée un."""
    logger.debug("publier() | repo={} pr={}", repo, pr)
    commentaires = _appel_api(f"/repos/{repo}/issues/{pr}/comments?per_page=100", token)
    for c in commentaires:
        if MARQUEUR in c.get("body", ""):
            _appel_api(
                f"/repos/{repo}/issues/comments/{c['id']}",
                token,
                methode="PATCH",
                corps=json.dumps({"body": corps}),
            )
            return f"commentaire existant mis à jour ({c['html_url']})"
    cree = _appel_api(
        f"/repos/{repo}/issues/{pr}/comments",
        token,
        methode="POST",
        corps=json.dumps({"body": corps}),
    )
    return f"commentaire créé ({cree['html_url']})"


def main(argv: list[str]) -> int:
    """Point d'entrée : 0 si publié, 2 si l'environnement GitHub manque."""
    parseur = argparse.ArgumentParser(description="Commentaire de couverture d'une PR")
    parseur.add_argument("--pr-json", required=True, help="rapport coverage.py de la PR")
    parseur.add_argument("--base-json", required=True, help="rapport coverage.py de la base")
    parseur.add_argument("--pr-number", required=True, type=int)
    parseur.add_argument(
        "--dry-run", action="store_true", help="affiche le commentaire sans le publier"
    )
    arguments = parseur.parse_args(argv)
    # Traces DEBUG seulement sur demande (même variable que l'application,
    # issue #130) : les logs CI restent lisibles.
    niveau = "DEBUG" if os.environ.get("GCM_DEBUG", "") in {"1", "true", "yes", "on"} else "INFO"
    logger.remove()
    logger.add(sys.stderr, level=niveau)
    logger.debug("main() | pr={} dry_run={}", arguments.pr_number, arguments.dry_run)

    corps = construire_commentaire(
        _lire_rapport(arguments.pr_json), _lire_rapport(arguments.base_json), lire_seuil()
    )
    if arguments.dry_run:
        print(corps)
        return 0
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        logger.error("GITHUB_TOKEN et GITHUB_REPOSITORY sont requis")
        return 2
    try:
        print(publier(repo, arguments.pr_number, corps, token))
    except urllib.error.HTTPError as exc:
        if exc.code != 403:
            raise
        # PR depuis un fork : GITHUB_TOKEN est en lecture seule, GitHub refuse
        # le commentaire (403). Le rapport reste lisible dans le résumé du job ;
        # ce job n'applique aucun seuil, il ne doit donc pas échouer.
        logger.warning("publication refusée (403, PR depuis un fork) : {}", exc)
        ecrire_resume(corps)
        print(
            "::warning::Commentaire de couverture refusé (403, PR depuis un fork) : "
            "rapport dans le résumé du job."
        )
    return 0


def ecrire_resume(corps: str) -> None:
    """Ajoute ``corps`` au résumé du job (``GITHUB_STEP_SUMMARY``), sinon l'affiche."""
    logger.debug("ecrire_resume() | {} caractère(s)", len(corps))
    chemin = os.environ.get("GITHUB_STEP_SUMMARY")
    if chemin:
        with open(chemin, "a", encoding="utf-8") as resume:
            resume.write(corps + "\n")
    else:
        print(corps)
    logger.debug("ecrire_resume() -> {}", "résumé" if chemin else "sortie standard")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
