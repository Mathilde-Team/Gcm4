# SPDX-License-Identifier: GPL-3.0-or-later
"""Workflow .github/workflows/integration-phases.yml : rapport d'avancement (issue #107)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "integration-phases.yml").read_text(encoding="utf-8")

# Libellés définis dans le dépôt Mathilde-Team/Gcm4 (gh label list, 05/11/2026).
PRIORITES = ["P0-critique", "P1-haute", "P2-moyenne", "P3-faible", "P4-bloquée"]
LIBELLES_INEXISTANTS = [
    "P0-fondations",
    "P1-différenciation",
    "P2-exploitation",
    "P3-applicatif",
    "P4-admin",
    "diff-1-faible",
]


class TestWorkflowIntegrationPhases(unittest.TestCase):
    """Pagination, exclusion des PR, libellés existants, erreurs signalées."""

    def test_toutes_les_pages_sont_lues(self):
        """Plus de 100 issues closes : github.paginate, jamais un seul appel paginé."""
        self.assertIn("github.paginate(github.rest.issues.listForRepo", WORKFLOW)
        self.assertIn("github.paginate(github.rest.pulls.list", WORKFLOW)
        self.assertNotRegex(WORKFLOW, r"await github\.rest\.(issues|pulls)\.list")

    def test_les_pr_ne_sont_pas_comptees_comme_des_issues(self):
        """L'API issues renvoie aussi les PR (champ pull_request)."""
        self.assertIn("!i.pull_request", WORKFLOW)
        self.assertRegex(WORKFLOW, r"openIssues = sansPR\(")
        self.assertRegex(WORKFLOW, r"closedIssues = sansPR\(")

    def test_libelles_de_priorite_du_depot(self):
        """Les priorités comptées sont celles du dépôt, pas d'anciens noms."""
        liste = re.search(r"\['Priorité', \[(.*?)\]\]", WORKFLOW)
        self.assertIsNotNone(liste)
        self.assertEqual(re.findall(r"'([^']+)'", liste.group(1)), PRIORITES)
        for nom in LIBELLES_INEXISTANTS:
            self.assertNotIn(nom, WORKFLOW)

    def test_fichiers_de_suivi_existants(self):
        """Seuls des fichiers présents dans le dépôt sont vérifiés."""
        fichiers = re.search(r"for f in (.*?); do", WORKFLOW).group(1).split()
        self.assertTrue(fichiers)
        for f in fichiers:
            self.assertTrue((ROOT / f).is_file(), f)

    def test_echec_de_la_liste_des_pr_signale(self):
        """Aucune erreur ignorée en silence."""
        self.assertNotIn("// ignore", WORKFLOW)
        self.assertIn("core.warning(", WORKFLOW)

    def test_droits_en_lecture_seule(self):
        """Le rapport ne modifie rien : droits en lecture uniquement."""
        permissions = re.search(r"^permissions:\n((?:\s+.*\n)+?)\n", WORKFLOW, re.M).group(1)
        self.assertNotIn("write", permissions)
