# SPDX-License-Identifier: GPL-3.0-or-later
"""docs/journalisation.md reste fidèle à logging_config.py (issue #172)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import logging_config as lc  # noqa: E402

DOC = (ROOT / "docs" / "journalisation.md").read_text(encoding="utf-8")


class TestDocJournalisation(unittest.TestCase):
    """La documentation cite les vrais noms, niveaux et fichiers."""

    def test_variables_et_option(self):
        """Variables d'environnement et option CLI du module."""
        for nom in (lc.ENV_LEVEL, lc.ENV_DEBUG, lc.ENV_FILE, lc.DEBUG_FLAG):
            self.assertIn(f"`{nom}", DOC, nom)

    def test_fichier_et_niveau_par_defaut(self):
        """Nom du journal permanent et niveau par défaut."""
        self.assertIn(lc.APP_LOG_NAME, DOC)
        self.assertIn(f"`{lc.DEFAULT_LEVEL}`", DOC)

    def test_avertissement_donnees_sensibles(self):
        """L'avertissement sur les variables des piles en debug est présent."""
        self.assertIn("Données sensibles", DOC)
        self.assertIn("valeur des variables", DOC)

    def test_renvoi_aux_tests(self):
        """Les règles renvoient aux tests qui les vérifient, et ces tests existent."""
        for test in ("tests/test_loguru_only.py", "tests/test_secrets_journal.py"):
            self.assertIn(test, DOC)
            self.assertTrue((ROOT / test).is_file(), test)

    def test_lien_depuis_le_readme(self):
        """Critère d'acceptation : lien depuis README.md."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("(docs/journalisation.md)", readme)


if __name__ == "__main__":
    unittest.main()
