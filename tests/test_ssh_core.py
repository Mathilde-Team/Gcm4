# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests unitaires pour plugins/ssh/core.py.

Fusion (session-26) de l'ancien ``test_ssh_migrate_gcm.py`` (partie
``ssh_migrate_gcm.py`` — ``TestRenderSshStanzaProxyCommand``) avec une
couverture nouvelle de la partie ``ssh_config_parser.py`` (classes
``SSHOption``/``SSHHost``/``SSHConfig``/``SSHConfigParser``), qui n'avait
jusqu'ici AUCUN test dédié (dette listée dans ``CLAUDE.md``/
``docs/features-backlog.md`` avant cette session — confirmée en
inspectant ``tests/`` : seul ``ssh_config_editor.py``, qui a besoin de
GTK3, l'exerçait indirectement).

Comme tests/test_gcm4_core.py, ce fichier importe le module directement —
aucun stub GTK nécessaire, ``plugins/ssh/core.py`` n'a aucune dépendance
gi/Gtk (voir CONSIGNES-AGENTS-IA.md §5).
"""

import configparser
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import plugins.ssh.core as core  # noqa: E402


def _cp_with_host(**fields):
    """Construit un RawConfigParser minimal avec une seule section hôte.

    Args:
        **fields: Paires clé=valeur à écrire dans la section ``[host test]``.

    Returns:
        tuple[configparser.RawConfigParser, str]: le parser et le nom de
        section, prêts à passer à ``ssh_migrate_gcm._render_ssh_stanza()``.
    """
    cp = configparser.RawConfigParser()
    section = "host test"
    cp.add_section(section)
    for key, value in fields.items():
        cp.set(section, key, value)
    return cp, section


class TestRenderSshStanzaProxyCommand(unittest.TestCase):
    """_render_ssh_stanza() — backlog features.md §4.2, ProxyCommand SSH."""

    def test_proxy_command_emitted_when_set(self):
        """Un proxy_command renseigné produit une ligne ProxyCommand."""
        cp, section = _cp_with_host(proxy_command="ssh -W %h:%p bastion")
        stanza = core._render_ssh_stanza("test", "10.0.0.1", cp, section)
        self.assertIn("ProxyCommand ssh -W %h:%p bastion", stanza)

    def test_proxy_command_absent_when_unset(self):
        """Sans proxy_command, aucune ligne ProxyCommand n'apparaît."""
        cp, section = _cp_with_host()
        stanza = core._render_ssh_stanza("test", "10.0.0.1", cp, section)
        self.assertNotIn("ProxyCommand", stanza)

    def test_proxy_jump_still_emitted_alongside(self):
        """Régression : ProxyJump reste émis (proxy_command inséré juste
        avant lui dans la liste des champs — vérifie qu'il n'a pas été
        décalé/écrasé par erreur).
        """
        cp, section = _cp_with_host(proxy_jump="user@bastion.example.com")
        stanza = core._render_ssh_stanza("test", "10.0.0.1", cp, section)
        self.assertIn("ProxyJump user@bastion.example.com", stanza)

    def test_both_proxy_jump_and_proxy_command_emitted(self):
        """Les deux peuvent coexister dans la sortie (pas de validation
        d'exclusion mutuelle ici, cohérent avec le reste du module qui ne
        fait aucune validation croisée entre champs).
        """
        cp, section = _cp_with_host(
            proxy_jump="user@bastion.example.com",
            proxy_command="nc -X connect -x proxy:1080 %h %p",
        )
        stanza = core._render_ssh_stanza("test", "10.0.0.1", cp, section)
        self.assertIn("ProxyJump user@bastion.example.com", stanza)
        self.assertIn("ProxyCommand nc -X connect -x proxy:1080 %h %p", stanza)


class TestSSHOption(unittest.TestCase):
    """SSHOption.__str__() — rendu d'une directive avec son indentation."""

    def test_str_uses_stored_indentation(self):
        """L'indentation d'origine est reproduite telle quelle."""
        opt = core.SSHOption(key="Port", value="2222", indentation="  ")
        self.assertEqual(str(opt), "  Port 2222")

    def test_str_default_indentation(self):
        """Sans indentation explicite, la valeur par défaut (4 espaces) s'applique."""
        opt = core.SSHOption(key="User", value="root")
        self.assertEqual(str(opt), "    User root")


class TestSSHHost(unittest.TestCase):
    """SSHHost — parsing depuis des lignes brutes et accesseurs d'options."""

    def test_from_raw_lines_parses_patterns_and_options(self):
        """Un bloc Host simple est reconnu avec ses patterns et directives."""
        lines = [
            "Host myserver *.example.com\n",
            "    HostName 10.0.0.1\n",
            "    User admin\n",
        ]
        host = core.SSHHost.from_raw_lines(lines)
        self.assertEqual(host.patterns, ["myserver", "*.example.com"])
        self.assertEqual(host.get_option("HostName"), "10.0.0.1")
        self.assertEqual(host.get_option("hostname"), "10.0.0.1")  # insensible à la casse

    def test_from_raw_lines_no_host_declaration_raises(self):
        """Un bloc sans ligne Host est rejeté."""
        with self.assertRaises(ValueError):
            core.SSHHost.from_raw_lines(["    User admin\n"])

    def test_from_raw_lines_multiple_host_declarations_raises(self):
        """Deux lignes Host dans le même bloc brut sont rejetées."""
        with self.assertRaises(ValueError):
            core.SSHHost.from_raw_lines(["Host a\n", "Host b\n"])

    def test_set_option_adds_when_missing(self):
        """set_option() ajoute la directive si elle est absente."""
        host = core.SSHHost(patterns=["srv"])
        host.set_option("Port", "22")
        self.assertEqual(host.get_option("Port"), "22")

    def test_set_option_updates_existing(self):
        """set_option() met à jour la valeur si la directive existe déjà."""
        host = core.SSHHost(patterns=["srv"], options=[core.SSHOption(key="Port", value="22")])
        host.set_option("Port", "2222")
        self.assertEqual(host.get_option("Port"), "2222")
        self.assertEqual(len(host.options), 1)

    def test_remove_option_found_and_missing(self):
        """remove_option() renvoie True/False selon que la clé existait."""
        host = core.SSHHost(patterns=["srv"], options=[core.SSHOption(key="Port", value="22")])
        self.assertTrue(host.remove_option("Port"))
        self.assertFalse(host.remove_option("Port"))

    def test_get_options_and_set_options_multivalue(self):
        """get_options()/set_options() gèrent les directives répétées (IdentityFile...)."""
        host = core.SSHHost(patterns=["srv"])
        host.set_options("IdentityFile", ["~/.ssh/id_ed25519", "~/.ssh/id_rsa"])
        self.assertEqual(
            host.get_options("IdentityFile"),
            ["~/.ssh/id_ed25519", "~/.ssh/id_rsa"],
        )
        host.set_options("IdentityFile", ["~/.ssh/id_ed25519"])
        self.assertEqual(host.get_options("IdentityFile"), ["~/.ssh/id_ed25519"])


class TestSSHConfig(unittest.TestCase):
    """SSHConfig — génération de contenu et gestion de la liste des hôtes."""

    def test_generate_content_round_trip(self):
        """Un hôte simple est rendu avec son bloc Host et ses options."""
        cfg = core.SSHConfig(file_path=Path("/tmp/does-not-matter"))
        host = core.SSHHost(
            patterns=["myserver"],
            options=[core.SSHOption(key="HostName", value="10.0.0.1")],
        )
        cfg.add_host(host)
        content = cfg.generate_content()
        self.assertIn("Host myserver", content)
        self.assertIn("HostName 10.0.0.1", content)

    def test_is_dirty_detects_change(self):
        """is_dirty() distingue le contenu généré du contenu d'origine chargé."""
        cfg = core.SSHConfig(file_path=Path("/tmp/does-not-matter"))
        cfg.original_lines = ["Host myserver", "    HostName 10.0.0.1"]
        cfg.add_host(
            core.SSHHost(
                patterns=["myserver"], options=[core.SSHOption(key="HostName", value="10.0.0.1")]
            )
        )
        self.assertFalse(cfg.is_dirty())
        cfg.hosts[0].set_option("HostName", "10.0.0.2")
        self.assertTrue(cfg.is_dirty())

    def test_get_host_add_remove(self):
        """get_host()/add_host()/remove_host() opèrent sur la liste des blocs."""
        cfg = core.SSHConfig(file_path=Path("/tmp/does-not-matter"))
        host = core.SSHHost(patterns=["alpha", "beta"])
        cfg.add_host(host)
        self.assertIs(cfg.get_host("beta"), host)
        self.assertIsNone(cfg.get_host("gamma"))
        self.assertTrue(cfg.remove_host(host))
        self.assertFalse(cfg.remove_host(host))


class TestSSHConfigParser(unittest.TestCase):
    """SSHConfigParser — lecture/écriture/validation sur fichiers réels."""

    def setUp(self):
        """Prépare un répertoire temporaire isolé pour chaque test."""
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.config_path = Path(self._tmpdir.name) / "config"

    def test_parse_missing_file_returns_empty_config(self):
        """parse() sur un fichier absent renvoie une config vide, sans lever."""
        parser = core.SSHConfigParser(config_path=self.config_path)
        cfg = parser.parse()
        self.assertEqual(cfg.hosts, [])

    def test_write_then_parse_round_trip(self):
        """Un hôte écrit puis reparsé conserve son alias et ses options."""
        parser = core.SSHConfigParser(config_path=self.config_path)
        host = core.SSHHost(
            patterns=["myserver"],
            options=[
                core.SSHOption(key="HostName", value="10.0.0.1"),
                core.SSHOption(key="Port", value="2222"),
            ],
        )
        parser.config.add_host(host)
        parser.write()

        reparsed = core.SSHConfigParser(config_path=self.config_path).parse()
        self.assertEqual(len(reparsed.hosts), 1)
        self.assertEqual(reparsed.hosts[0].patterns, ["myserver"])
        self.assertEqual(reparsed.hosts[0].get_option("Port"), "2222")

    def test_write_creates_backup_on_second_write(self):
        """Le premier write() sur un fichier déjà existant crée un backup horodaté."""
        self.config_path.write_text("Host old\n    HostName 1.2.3.4\n", encoding="utf-8")
        parser = core.SSHConfigParser(config_path=self.config_path)
        parser.parse()
        parser.config.add_host(
            core.SSHHost(
                patterns=["new"], options=[core.SSHOption(key="HostName", value="5.6.7.8")]
            )
        )
        parser.write()

        backups = list(self.config_path.parent.glob(f"{self.config_path.name}.*.bak"))
        self.assertEqual(len(backups), 1)

    def test_write_noop_when_content_unchanged(self):
        """write() n'écrit rien (donc pas de backup) si le contenu est identique."""
        self.config_path.write_text("Host same\n    HostName 1.1.1.1\n", encoding="utf-8")
        parser = core.SSHConfigParser(config_path=self.config_path)
        parser.parse()
        parser.write()  # contenu généré == contenu sur disque
        backups = list(self.config_path.parent.glob(f"{self.config_path.name}.*.bak"))
        self.assertEqual(len(backups), 0)

    def test_validate_detects_duplicate_alias(self):
        """validate() signale un alias utilisé par deux blocs Host distincts."""
        parser = core.SSHConfigParser(config_path=self.config_path)
        parser.config.add_host(core.SSHHost(patterns=["dup"]))
        parser.config.add_host(core.SSHHost(patterns=["dup"]))
        errors = parser.validate()
        self.assertTrue(any("Duplicate host alias" in e for e in errors))

    def test_validate_detects_invalid_port(self):
        """validate() signale un port hors de la plage 1-65535."""
        parser = core.SSHConfigParser(config_path=self.config_path)
        parser.config.add_host(
            core.SSHHost(patterns=["srv"], options=[core.SSHOption(key="Port", value="99999")])
        )
        errors = parser.validate()
        self.assertTrue(any("Invalid port" in e for e in errors))

    def test_validate_detects_missing_identity_file(self):
        """validate() signale un IdentityFile référencé mais absent du disque."""
        parser = core.SSHConfigParser(config_path=self.config_path)
        parser.config.add_host(
            core.SSHHost(
                patterns=["srv"],
                options=[core.SSHOption(key="IdentityFile", value="/does/not/exist/id_rsa")],
            )
        )
        errors = parser.validate()
        self.assertTrue(any("IdentityFile not found" in e for e in errors))

    def test_validate_clean_config_has_no_errors(self):
        """Une configuration valide (alias unique, port correct, pas d'IdentityFile) ne lève aucune erreur."""
        parser = core.SSHConfigParser(config_path=self.config_path)
        parser.config.add_host(
            core.SSHHost(patterns=["srv"], options=[core.SSHOption(key="Port", value="22")])
        )
        self.assertEqual(parser.validate(), [])

    def test_is_shared_target_true_for_shared_path(self):
        """_is_shared_target() reconnaît le chemin sous SHARED_SSH_CONFIG_PATH.parent."""
        shared_path = core.SHARED_SSH_CONFIG_PATH
        parser = core.SSHConfigParser(config_path=shared_path)
        self.assertTrue(parser._is_shared_target())

    def test_is_shared_target_false_for_personal_path(self):
        """_is_shared_target() renvoie False pour un ~/.ssh/config personnel."""
        parser = core.SSHConfigParser(config_path=self.config_path)
        self.assertFalse(parser._is_shared_target())


if __name__ == "__main__":
    unittest.main()
