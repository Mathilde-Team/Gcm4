"""`make ci` rejoue les étapes de la CI (issue #162).

Chaque commande d'une ligne des jobs Qualité, Types et i18n de
``.github/workflows/ci.yml`` doit figurer dans la recette ``ci`` du
Makefile : ajouter une étape à la CI sans l'ajouter à ``make ci`` fait
échouer ce test. Exceptions : installation de l'environnement et étapes
propres au runner (dossier ``$RUNNER_TEMP``, résumé du job), et test de
fumée du paquet (#156) : il installe le .deb avec apt dans le conteneur,
ce que ``make ci`` ne fait pas sur un poste ; il se rejoue avec
``make smoke-deb``.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
HORS_MAKE_CI = (
    "uv python install",
    "uv sync",
    "$RUNNER_TEMP",
    "i18n_report.py --markdown",
    "smoke-test-packages.sh",
)


def recette(cible: str) -> list[str]:
    """Commandes de la recette d'une cible du Makefile."""
    bloc = re.search(rf"^{cible}:.*\n((?:\t.*\n)+)", MAKEFILE, re.M)
    assert bloc, f"cible {cible} absente du Makefile"
    return [ligne.strip() for ligne in bloc.group(1).splitlines()]


def commandes_ci() -> list[str]:
    """Commandes `run:` d'une ligne de ci.yml, hors installation et runner."""
    return [
        cmd.strip().strip('"')
        for cmd in re.findall(r"^\s+run: (?![|>])(.+)$", CI, re.M)
        if not any(exclu in cmd for exclu in HORS_MAKE_CI)
    ]


class TestMakeCi(unittest.TestCase):
    """Parité entre `make ci` et ci.yml."""

    def test_chaque_etape_de_la_ci_est_rejouee(self):
        """Toute commande d'une ligne de ci.yml figure dans `make ci`."""
        commandes = commandes_ci()
        self.assertGreaterEqual(len(commandes), 6)
        manquantes = [c for c in commandes if c not in recette("ci")]
        self.assertEqual(manquantes, [], "ajouter ces commandes à la cible ci du Makefile")

    def test_tests_avec_couverture(self):
        """La suite tourne avec la couverture et gettext exigé, comme le job Qualité (#160)."""
        self.assertIn("uv run python -m pytest tests/ -q -rs", CI)
        self.assertIn('GCM_TESTS_EXIGES: "gettext"', CI)
        self.assertTrue(
            any(
                c.startswith(
                    "GCM_TESTS_EXIGES=gettext uv run python -m pytest tests/ -q -rs --cov"
                )
                for c in recette("ci")
            )
        )

    def test_cible_declaree_et_documentee(self):
        """Cible .PHONY et ligne d'aide."""
        self.assertRegex(MAKEFILE, r"\.PHONY:.*\bci\b")
        self.assertIn("make ci ", MAKEFILE)


if __name__ == "__main__":
    unittest.main()
