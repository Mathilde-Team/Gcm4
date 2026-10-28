# SPDX-License-Identifier: GPL-3.0-or-later
"""Aucun secret dans le journal applicatif : mot de passe VNC, URI SPICE,
fichier .vv Proxmox, mot de passe d'hôte (gnome_connection_manager).

Vérification statique (aucun import GTK) + test de la fonction pure
plugin_spice.mask_password extraite par AST.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    ROOT / "plugins" / "plugin_vnc.py",
    ROOT / "plugins" / "plugin_spice.py",
    ROOT / "gnome_connection_manager.py",
]
# Variables qui portent un secret en clair dans ces fichiers.
SECRET_NAMES = {"pwd", "password", "vv_lines"}


def _log_calls(path):
    """(ligne, noms interpolés) de chaque appel app_logger.*/logger.* en f-string."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        target = node.func.value
        if not (isinstance(target, ast.Name) and target.id in ("app_logger", "logger")):
            continue
        for arg in node.args:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.FormattedValue):
                    yield node.lineno, sub.value


def _load_mask_password():
    """mask_password partagée, dans logging_config (sans GTK) depuis l'issue #130."""
    import logging_config

    return logging_config.mask_password


class TestAucunSecretJournalise(unittest.TestCase):
    """Les f-strings de journalisation n'interpolent aucun secret brut."""

    def test_aucune_variable_secrete_interpolee(self):
        """pwd/password/vv_lines n'apparaissent que via bool()/len()/mask_password()."""
        fautes = []
        for path in FILES:
            for lineno, value in _log_calls(path):
                if isinstance(value, ast.Call):
                    continue  # bool(pwd), len(vv_lines), mask_password(cmd)...
                name = (
                    value.attr if isinstance(value, ast.Attribute) else getattr(value, "id", None)
                )
                if name in SECRET_NAMES:
                    fautes.append(f"{path.name}:{lineno} ({name})")
        self.assertEqual(fautes, [])

    def test_uri_et_commande_spice_masquees(self):
        """Toute trace d'URI ou de commande SPICE passe par mask_password()."""
        src = (ROOT / "plugins" / "plugin_spice.py").read_text(encoding="utf-8")
        for lineno, value in _log_calls(ROOT / "plugins" / "plugin_spice.py"):
            if isinstance(value, ast.Name) and value.id in ("uri", "cmd"):
                self.fail(f"plugin_spice.py:{lineno} journalise {value.id} sans mask_password()")
        self.assertIn("mask_password(", src)

    def test_plus_de_chemin_passwd_fixe(self):
        """Le fichier passwd VNC n'utilise plus de chemin /tmp fixe."""
        src = (ROOT / "plugins" / "plugin_vnc.py").read_text(encoding="utf-8")
        self.assertNotIn("/tmp/klfhghzz", src)
        self.assertIn("tempfile.mkstemp(", src)


class TestMaskPassword(unittest.TestCase):
    """logging_config.mask_password (importée par plugin_spice)."""

    @classmethod
    def setUpClass(cls):
        """Charge la fonction pure."""
        cls.mask = staticmethod(_load_mask_password())

    def test_uri(self):
        """Le mot de passe d'une URI est masqué, le reste conservé."""
        self.assertEqual(
            self.mask("spice://h?port=5930&password=s3cr3t&tls-port=1"),
            "spice://h?port=5930&password=***&tls-port=1",
        )

    def test_liste_d_arguments(self):
        """Une commande (liste) est convertie et masquée."""
        out = self.mask(["remote-viewer", "spice://h?port=5930&password=abc"])
        self.assertNotIn("abc", out)
        self.assertIn("password=***", out)

    def test_identifiants_d_url(self):
        """user:motdepasse@ d'une URL devient user:***@ ; l'utilisateur reste visible."""
        self.assertEqual(
            self.mask("qemu+ssh://root:hunter2@pve1/system"), "qemu+ssh://root:***@pve1/system"
        )
        self.assertEqual(self.mask("qemu+ssh://root@pve1/system"), "qemu+ssh://root@pve1/system")

    def test_sans_mot_de_passe(self):
        """Sans password=, le texte est inchangé."""
        self.assertEqual(self.mask("spice://h?port=5930"), "spice://h?port=5930")
