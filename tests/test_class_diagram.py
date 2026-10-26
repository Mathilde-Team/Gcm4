"""Diagramme de classes généré (issue #175)."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _generator():
    spec = importlib.util.spec_from_file_location(
        "generate_class_diagram", ROOT / "scripts" / "generate_class_diagram.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_class_diagram"] = module
    spec.loader.exec_module(module)
    return module


GEN = _generator()


class TestDiagrammeDeClasses(unittest.TestCase):
    """docs/class-diagram.md suit le code."""

    def test_fichier_a_jour(self):
        """Le fichier committé est celui que produit le générateur (comme --check en CI)."""
        self.assertEqual(
            GEN.main(["--check"]),
            0,
            "lancer `make class-diagram` et committer docs/class-diagram.md",
        )

    def test_fichier_perime_detecte(self):
        """Un diagramme modifié fait échouer --check."""
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "class-diagram.md"
            stale.write_text(
                (ROOT / "docs" / "class-diagram.md").read_text(encoding="utf-8") + "x\n",
                encoding="utf-8",
            )
            self.assertEqual(GEN.main(["--check", "--output", str(stale)]), 1)
            self.assertEqual(GEN.main(["--output", str(stale)]), 0)
            self.assertEqual(GEN.main(["--check", "--output", str(stale)]), 0)

    def test_deterministe(self):
        """Deux générations successives sont identiques."""
        files = GEN.source_files()
        first = GEN.render_document([GEN.extract_module(p) for p in files])
        second = GEN.render_document([GEN.extract_module(p) for p in files])
        self.assertEqual(first, second)
        self.assertNotIn(str(ROOT), first)

    def test_groupes(self):
        """Classement cœur / application / plugins / src/gcm4."""
        self.assertEqual(GEN.group_of("gcm4_core.py"), GEN.GROUP_CORE)
        self.assertEqual(GEN.group_of("snmp_bulk_core.py"), GEN.GROUP_CORE)
        self.assertEqual(GEN.group_of("gnome_connection_manager.py"), GEN.GROUP_APP)
        self.assertEqual(GEN.group_of("plugins/ssh/core.py"), GEN.GROUP_PLUGINS)
        self.assertEqual(GEN.group_of("src/gcm4/core/hosts.py"), "gcm4.core")
        self.assertEqual(GEN.module_name("src/gcm4/core/__init__.py"), "gcm4.core")

    def test_perimetre(self):
        """Ni tests, ni outils, ni sous-projets."""
        files = GEN.source_files()
        self.assertIn("gcm4_core.py", files)
        self.assertIn("plugins/plugin_base.py", files)
        self.assertFalse(
            [
                f
                for f in files
                if f.startswith(("tests/", "tools/", "scripts/", "SSH-Studio/", "gtk-frdp/"))
            ]
        )

    def test_blocs_sous_la_limite_mermaid(self):
        """Chaque bloc mermaid reste sous MAX_BLOCK_CHARS (limite de rendu 50 000)."""
        text = (ROOT / "docs" / "class-diagram.md").read_text(encoding="utf-8")
        blocks = text.split("```mermaid")[1:]
        self.assertTrue(blocks)
        for block in blocks:
            self.assertLess(len(block.split("```")[0]), 50_000)

    def test_rendu_classe(self):
        """Champs annotés, méthode publique, héritage local et dataclass."""
        src = (
            "from dataclasses import dataclass\n"
            "@dataclass\nclass A:\n    nom: list[str]\n    def ouvrir(self, hote) -> bool: ...\n"
            "class B(A):\n    def _prive(self): ...\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m_core.py"
            path.write_text(src, encoding="utf-8")
            old_root = GEN.ROOT
            GEN.ROOT = Path(tmp)
            try:
                module = GEN.extract_module("m_core.py")
            finally:
                GEN.ROOT = old_root
        block = GEN.render_block([module], GEN.class_ids([module]))
        self.assertIn("+list~str~ nom", block)
        self.assertIn("+ouvrir(hote) bool", block)
        self.assertIn("A <|-- B", block)
        self.assertIn("<<dataclass>>", block)
        self.assertNotIn("_prive", block)


if __name__ == "__main__":
    unittest.main()
