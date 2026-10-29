# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests unitaires pour putty_import_core.py.

Comme tests/test_gcm4_core.py, ce fichier importe putty_import_core
directement — aucun stub GTK nécessaire, puisque le module n'a aucune
dépendance gi/Gtk (voir CONSIGNES-AGENTS-IA.md §5).
"""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import putty_import_core as core  # noqa: E402


class TestDecodePuttySessionName(unittest.TestCase):
    """Décodage du schéma d'échappement %XX des noms de fichiers PuTTY."""

    def test_plain_name_unchanged(self):
        """Un nom sans caractère spécial reste inchangé."""
        self.assertEqual(core.decode_putty_session_name("myserver"), "myserver")

    def test_space_decoded(self):
        """%20 est décodé en espace."""
        self.assertEqual(core.decode_putty_session_name("Default%20Settings"), "Default Settings")

    def test_slash_decoded(self):
        """%2F est décodé en slash (nom de session pouvant contenir un /)."""
        self.assertEqual(core.decode_putty_session_name("prod%2Fweb01"), "prod/web01")


class TestParsePuttySessionFile(unittest.TestCase):
    """Lecture brute d'un fichier de session PuTTY (Clé=Valeur)."""

    def test_parses_key_value_lines(self):
        """Les lignes Clé=Valeur sont lues telles quelles."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session"
            path.write_text(
                "HostName=192.168.1.10\nPortNumber=22\nProtocol=ssh\n", encoding="utf-8"
            )
            raw = core.parse_putty_session_file(path)
        self.assertEqual(raw["HostName"], "192.168.1.10")
        self.assertEqual(raw["PortNumber"], "22")
        self.assertEqual(raw["Protocol"], "ssh")

    def test_ignores_blank_and_malformed_lines(self):
        """Les lignes vides ou sans '=' n'apparaissent pas dans le résultat."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session"
            path.write_text(
                "HostName=host1\n\nno-equals-sign-here\nUserName=root\n", encoding="utf-8"
            )
            raw = core.parse_putty_session_file(path)
        self.assertEqual(raw, {"HostName": "host1", "UserName": "root"})

    def test_value_with_equals_sign_kept_whole(self):
        """Seul le premier '=' sépare clé et valeur (valeur pouvant en contenir)."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session"
            path.write_text("PortForwardings=L3389=10.0.0.5:3389\n", encoding="utf-8")
            raw = core.parse_putty_session_file(path)
        self.assertEqual(raw["PortForwardings"], "L3389=10.0.0.5:3389")


class TestPuttySessionToRow(unittest.TestCase):
    """Conversion session PuTTY brute -> row au format ``_dict_to_host``."""

    def test_ssh_session_converted(self):
        """Une session SSH complète est convertie avec le port explicite."""
        row, reason = core.putty_session_to_row(
            "myserver",
            {"HostName": "10.0.0.1", "PortNumber": "2222", "Protocol": "ssh", "UserName": "alice"},
        )
        self.assertIsNone(reason)
        self.assertEqual(
            row,
            {
                "name": "myserver",
                "host": "10.0.0.1",
                "user": "alice",
                "port": "2222",
                "type": "ssh",
                "protocol": "ssh",
                "description": "Importé de PuTTY (ssh)",
            },
        )

    def test_missing_protocol_defaults_to_ssh(self):
        """PuTTY omet parfois Protocol=ssh (c'est la valeur par défaut du client)."""
        row, reason = core.putty_session_to_row("myserver", {"HostName": "10.0.0.1"})
        self.assertIsNone(reason)
        self.assertEqual(row["type"], "ssh")
        self.assertEqual(row["port"], "22")

    def test_missing_port_uses_protocol_default(self):
        """Port absent -> port par défaut du protocole (23 pour telnet)."""
        row, _reason = core.putty_session_to_row(
            "myserver", {"HostName": "10.0.0.1", "Protocol": "telnet"}
        )
        self.assertEqual(row["port"], "23")

    def test_unsupported_protocol_skipped(self):
        """Rlogin/Raw n'ont pas d'équivalent GCM -> session ignorée avec raison."""
        row, reason = core.putty_session_to_row(
            "legacy", {"HostName": "10.0.0.1", "Protocol": "rlogin"}
        )
        self.assertIsNone(row)
        self.assertIn("rlogin", reason)
        self.assertIn("legacy", reason)

    def test_missing_hostname_skipped(self):
        """Session sans HostName (ex. profil de préréglages) -> ignorée avec raison."""
        row, reason = core.putty_session_to_row("empty", {"Protocol": "ssh"})
        self.assertIsNone(row)
        self.assertIn("HostName", reason)


class TestScanPuttySessions(unittest.TestCase):
    """Scan complet d'un dossier de sessions PuTTY."""

    def test_missing_directory_returns_empty(self):
        """Dossier absent -> ([], []) sans lever d'exception."""
        rows, skipped = core.scan_putty_sessions(Path("/nonexistent/putty/sessions/dir"))
        self.assertEqual(rows, [])
        self.assertEqual(skipped, [])

    def test_scans_mixed_sessions(self):
        """Sessions importables, ignorées, et Default Settings dans un même dossier."""
        with TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp)
            (sessions_dir / "server1").write_text(
                "HostName=10.0.0.1\nPortNumber=22\nProtocol=ssh\nUserName=root\n", encoding="utf-8"
            )
            (sessions_dir / "old%2Drlogin").write_text(
                "HostName=10.0.0.2\nProtocol=rlogin\n", encoding="utf-8"
            )
            (sessions_dir / "Default%20Settings").write_text("Protocol=ssh\n", encoding="utf-8")

            rows, skipped = core.scan_putty_sessions(sessions_dir)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "server1")
        self.assertEqual(rows[0]["host"], "10.0.0.1")
        self.assertEqual(len(skipped), 1)
        self.assertIn("old-rlogin", skipped[0])


if __name__ == "__main__":
    unittest.main()
