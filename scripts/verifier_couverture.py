#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Vérifie séparément les lignes et branches du rapport JSON combiné (#293)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import coverage
from loguru import logger


def verifier(rapport: dict, seuil: float) -> int:
    """Retourne 0 si les deux mesures atteignent le seuil, sans arrondi préalable."""
    logger.debug("verifier: entrée, seuil={}", seuil)
    if not rapport["meta"]["branch_coverage"]:
        raise ValueError("La mesure des branches est obligatoire.")
    totaux = rapport["totals"]
    statut = 0
    for nom, cle_total, cle_couvert in (
        ("Lignes", "num_statements", "covered_lines"),
        ("Branches", "num_branches", "covered_branches"),
    ):
        total, couvert = totaux[cle_total], totaux[cle_couvert]
        if total < 0 or not 0 <= couvert <= total or (nom == "Lignes" and total == 0):
            raise ValueError(f"Compteurs de couverture invalides : {nom}.")
        pourcentage = 100 * couvert / total if total else 100.0
        accepte = 100 * couvert >= seuil * total
        print(
            f"- {nom} : {couvert}/{total}, {pourcentage:.6f} %, "
            f"seuil {seuil:g} % : {'OK' if accepte else 'ÉCHEC'}"
        )
        if not accepte:
            statut = 1
    logger.debug("verifier: sortie {}", statut)
    return statut


def main(argv: list[str] | None = None) -> int:
    """Charge le rapport et le même fail_under que les commandes coverage de CI."""
    logger.debug("main: entrée")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rapport", type=Path, help="rapport JSON de la couverture combinée")
    args = parser.parse_args(argv)
    try:
        seuil_config = coverage.Coverage().get_option("report:fail_under")
        if not isinstance(seuil_config, (int, float)) or not 0 < seuil_config <= 100:
            raise ValueError("Un fail_under explicite entre 0 et 100 est obligatoire.")
        seuil = float(seuil_config)
        rapport = json.loads(args.rapport.read_text(encoding="utf-8"))
        statut = verifier(rapport, seuil)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("Vérification de couverture impossible : {}", exc)
        statut = 2
    logger.debug("main: sortie {}", statut)
    return statut


if __name__ == "__main__":
    sys.exit(main())
