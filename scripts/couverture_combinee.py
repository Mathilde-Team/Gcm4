#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
r"""couverture_combinee.py -- tableau de la couverture fusionnée (issue #163).

Le job CI « Couverture combinée » fusionne (``coverage combine``) les
données du job Qualité (``.coverage.qualite``, suite ``tests/`` sans GTK4)
et du job GTK4 (``.coverage.gtk4``, ``tests/gtk4`` sous Xvfb). Ce script
écrit, en Markdown, la couverture de chaque périmètre pour chacune des trois
données : on voit ce que le job GTK4 ajoute, en particulier pour
``src/gcm4/ui/`` que seul ``tests/gtk4`` exécute.

Le seuil bloquant reste celui de ``coverage report`` (``fail_under`` de
pyproject.toml), appliqué par le workflow au total combiné.

Appel :
    uv run python scripts/couverture_combinee.py \
        --qualite .coverage.qualite --gtk4 .coverage.gtk4 --combinee .coverage
"""

from __future__ import annotations

import argparse
import io
import sys

import coverage
from coverage.exceptions import NoDataError
from loguru import logger

#: Périmètres détaillés, du plus étroit au plus large.
PERIMETRES = ("src/gcm4/ui/*", "src/gcm4/core/*", "src/gcm4/*")


def total(fichier: str, include: str | None = None) -> float | None:
    """Pourcentage couvert d'un fichier de données, ``None`` si rien n'est mesuré.

    Args:
        fichier (str): Fichier de données coverage.py.
        include (str | None): Motif de fichiers (``--include``), tout si ``None``.

    Returns:
        float | None: Couverture en pour cent (lignes et branches), ``None``
        si aucun fichier du périmètre n'a été exécuté par ces tests.
    """
    logger.debug("total: {} {}", fichier, include)
    mesure = coverage.Coverage(data_file=fichier)
    mesure.load()
    try:
        valeur = mesure.report(include=include, file=io.StringIO())
    except NoDataError:
        logger.debug("total: aucune donnée pour {}", include)
        return None
    logger.debug("total: {:.1f}", valeur)
    return valeur


def cellule(valeur: float | None) -> str:
    """Cellule du tableau : pourcentage à une décimale, ou « non exécuté »."""
    logger.debug("cellule: {}", valeur)
    texte = "non exécuté" if valeur is None else f"{valeur:.1f} %"
    logger.debug("cellule: {}", texte)
    return texte


def tableau(qualite: str, gtk4: str, combinee: str) -> str:
    """Résumé Markdown : total combiné puis détail par périmètre et par job.

    Args:
        qualite (str): Données du job Qualité.
        gtk4 (str): Données du job GTK4.
        combinee (str): Données fusionnées.

    Returns:
        str: Texte Markdown pour ``$GITHUB_STEP_SUMMARY``.
    """
    logger.debug("tableau: entrée")
    lignes = [
        "### Couverture combinée (Qualité + GTK4, issue #163)",
        "",
        f"Total combiné : {cellule(total(combinee))}",
        "",
        "| Périmètre | Qualité seul | GTK4 seul | Combiné |",
        "|---|---|---|---|",
    ]
    for motif in PERIMETRES:
        valeurs = (total(f, motif) for f in (qualite, gtk4, combinee))
        lignes.append(f"| `{motif}` | " + " | ".join(cellule(v) for v in valeurs) + " |")
    logger.debug("tableau: {} périmètre(s)", len(PERIMETRES))
    return "\n".join(lignes) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : écrit le tableau sur la sortie standard."""
    logger.debug("main: entrée")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qualite", required=True, help="données du job Qualité")
    parser.add_argument("--gtk4", required=True, help="données du job GTK4")
    parser.add_argument("--combinee", required=True, help="données fusionnées")
    args = parser.parse_args(argv)
    sys.stdout.write(tableau(args.qualite, args.gtk4, args.combinee))
    logger.debug("main: sortie 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
