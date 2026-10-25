"""Onglet voisin et titre de la fenêtre, sans GTK (#247)."""

from __future__ import annotations

import pytest

from gcm4.core.comportement_onglets import (
    NOM_APPLICATION,
    PRECEDENT,
    SUIVANT,
    titre_application,
    titre_fenetre,
    voisin,
)


@pytest.mark.parametrize(
    ("courant", "total", "sens", "boucler", "attendu"),
    [
        (0, 3, SUIVANT, True, 1),
        (2, 3, SUIVANT, True, 0),  # bouclage
        (2, 3, SUIVANT, False, 2),  # arrêt à la dernière page
        (0, 3, PRECEDENT, True, 2),
        (0, 3, PRECEDENT, False, 0),
        (1, 3, PRECEDENT, False, 0),
        (0, 1, SUIVANT, True, 0),  # une seule page
        (-1, 3, SUIVANT, False, 0),  # aucune page courante
        (-1, 3, PRECEDENT, False, 2),
        (0, 0, SUIVANT, True, -1),  # aucune page
    ],
)
def test_voisin(courant, total, sens, boucler, attendu):
    """Comme GTK3 (_CONSOLE_NEXT / _CONSOLE_PREV, on_tab_scroll)."""
    assert voisin(courant, total, sens, boucler) == attendu


def test_voisin_sens_invalide():
    """Sens autre que 1 ou -1 : erreur explicite."""
    with pytest.raises(ValueError):
        voisin(0, 3, 2, True)


def test_titre_application():
    """Vide ou blanc : nom de l'application ; sinon le titre sans espaces superflus."""
    assert titre_application("") == NOM_APPLICATION == titre_application("   ")
    assert titre_application("  Production ") == "Production"


@pytest.mark.parametrize(
    ("app_title", "dynamique", "onglet", "attendu"),
    [
        ("", False, "web", NOM_APPLICATION),
        ("", True, "web", f"{NOM_APPLICATION} - web"),
        ("Prod", True, "  web ", "Prod - web"),
        ("Prod", True, None, "Prod"),  # page d'accueil
        ("Prod", True, "  ", "Prod"),
        ("Prod", False, "web", "Prod"),
    ],
)
def test_titre_fenetre(app_title, dynamique, onglet, attendu):
    """Format GTK3 (on_tab_focus) : « %s - %s »."""
    assert titre_fenetre(app_title, dynamique, onglet) == attendu
