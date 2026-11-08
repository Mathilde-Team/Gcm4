# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests sautés alors que leur dépendance est installée (issue #160).

Greffon pytest chargé par ``tests/conftest.py`` (et par ``-p exigences``
dans ``tests/test_tests_sautes.py``). Module à part : ``tests/conftest.py``
et ``tests/gtk4/conftest.py`` portent le même nom de module, un test ne
peut pas importer l'un sans risquer d'obtenir l'autre.

- ``requires_gettext`` : le test a besoin de ``msgfmt`` ;
- ``requires_gtk4`` : ajouté à chaque test de ``tests/gtk4`` ;
- ``requires_vte`` : le test a besoin de la typelib VTE (Rocky 9 ne
  l'offre pas, #72).

``GCM_TESTS_EXIGES`` (``gettext``, ``gtk4``, séparés par des virgules ;
``GCM_TESTS_GTK4=1`` vaut ``gtk4``) déclare les dépendances installées par
le job : un test marqué qui y est sauté devient un échec.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import pytest

DOSSIER_GTK4 = Path(__file__).resolve().parent / "gtk4"
MARQUEURS_DEPENDANCES = {
    "requires_gettext": "gettext",
    "requires_gtk4": "gtk4",
    "requires_vte": "vte",
}


def dependances_exigees(environ: Mapping[str, str] = os.environ) -> set[str]:
    """Dépendances déclarées installées par le job (``GCM_TESTS_EXIGES``, ``GCM_TESTS_GTK4``)."""
    exigees = {
        nom.strip() for nom in environ.get("GCM_TESTS_EXIGES", "").split(",") if nom.strip()
    }
    if environ.get("GCM_TESTS_GTK4") == "1":
        exigees.add("gtk4")
    inconnues = exigees - set(MARQUEURS_DEPENDANCES.values())
    if inconnues:
        raise pytest.UsageError(f"GCM_TESTS_EXIGES : dépendance inconnue {sorted(inconnues)}")
    return exigees


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Marque ``requires_gtk4`` chaque test de ``tests/gtk4``."""
    for item in items:
        if DOSSIER_GTK4 in Path(item.path).resolve().parents:
            item.add_marker(pytest.mark.requires_gtk4)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> pytest.TestReport:
    """Un test sauté alors que toutes ses dépendances sont déclarées échoue (#160).

    Un test peut marquer plusieurs dépendances (``requires_gtk4`` et
    ``requires_vte`` par exemple) : tant que le job n'en déclare pas
    toutes, le saut reste légitime — la dépendance manquante peut en
    être la cause.
    """
    rapport: pytest.TestReport = yield
    if not rapport.skipped or hasattr(rapport, "wasxfail"):
        return rapport
    exigees = dependances_exigees()
    marquees = sorted(
        dependance
        for marqueur, dependance in MARQUEURS_DEPENDANCES.items()
        if item.get_closest_marker(marqueur) is not None
    )
    if marquees and set(marquees) <= exigees:
        raison = (
            rapport.longrepr[2] if isinstance(rapport.longrepr, tuple) else str(rapport.longrepr)
        )
        rapport.outcome = "failed"
        rapport.longrepr = f"test sauté alors que GCM_TESTS_EXIGES déclare {', '.join(marquees)} installé : {raison}"
    return rapport
