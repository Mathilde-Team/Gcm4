# SPDX-License-Identifier: GPL-3.0-or-later
"""Aucun secret dans le journal applicatif.

Les vérifications statiques ciblaient les fichiers GTK3 (plugin_vnc,
plugin_spice, gnome_connection_manager), supprimés avec le cœur GTK3
(#182) ; reste le test de la fonction pure ``mask_password`` de
``gcm4.core.journal`` (extrait sans GTK depuis l'issue #130).
"""

import unittest


def _load_mask_password():
    """mask_password partagée, dans logging_config (sans GTK) depuis l'issue #130."""
    from gcm4.core import journal as logging_config

    return logging_config.mask_password


class TestMaskPassword(unittest.TestCase):
    """logging_config.mask_password (masquage des URI et commandes)."""

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
