# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests unitaires pour gcm4_core.py.

Contrairement à tests/test_gcm.py (qui doit stubber GTK/VTE pour importer
gnome_connection_manager.py), ce fichier importe gcm4_core directement —
aucun stub nécessaire, ce qui est exactement le but de l'extraction du
2026-08-30 (voir gcm4_core.py, docstring de module).

Couvre en priorité le comportement NOUVEAU/MODIFIÉ par l'extraction (pas
seulement un déplacement des tests existants de test_gcm.py, qui restent
valides via les alias de compatibilité dans gnome_connection_manager.py) :
- load_encryption_key/initialise_encyption_key lèvent une exception au lieu
  d'appeler msgbox (impossible sans GTK) — §1 de la demande du 2026-08-30.
- proto_default_port/all_default_ports reçoivent plugin_registry en
  paramètre explicite au lieu de lire le singleton global wMain — §2.
- decrypt() reçoit `version` en paramètre au lieu de lire utils.conf.VERSION.
- color_to_hex/dep_install_hint : régressions de transcription trouvées et
  corrigées en écrivant ce fichier (diff manquant, logique de distribution
  incorrecte) — verrouillées ici pour ne pas revenir.

Les templates série (_SERIAL_TEMPLATES) ne sont PAS couverts ici : après
relecture (2026-08-30), ce sont des données spécifiques au protocole série,
qui n'ont pas leur place dans gcm4_core.py (cœur neutre de protocole) — elles
restent dans gnome_connection_manager.py, couvertes par
tests/test_gcm.py::TestSerialTemplatesSaveLoad.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gcm4_core  # noqa: E402


class FakePlugin:
    """Simule un ConnectionPlugin minimal pour les tests de port par défaut."""

    def __init__(self, default_port):
        """Initialise avec le port par défaut donné."""
        self.default_port = default_port


class FakePluginRegistry:
    """Simule un PluginRegistry minimal (voir plugins/plugin_base.py)."""

    def __init__(self, plugins: dict):
        """Initialise avec le dict de plugins simulés."""
        self._plugins = plugins

    def get(self, proto):
        """Retourne le plugin associé à *proto*, ou None."""
        return self._plugins.get(proto)

    def all(self):
        """Retourne tous les plugins enregistrés."""
        return list(self._plugins.values())


class FakePluginSansAdresse:
    """Plugin dont ``requires_address(protocol)`` (méthode, contrat v1 #297) rend False."""

    def requires_address(self, protocol):
        """Protocole sans hôte distant."""
        return False


class FakePluginAttributAdresse:
    """Plugin avec ``requires_address`` en attribut booléen (plugins GTK3, #297)."""

    def __init__(self, valeur):
        """Valeur de l'attribut ``requires_address``."""
        self.requires_address = valeur


class FakePluginContratV1:
    """Plugin du contrat v1 (#124) : ``default_port(protocol)`` est une méthode."""

    def __init__(self, port, protocoles=()):
        """Port par défaut (ou None) et protocoles gérés."""
        self._port = port
        self.protocols = tuple(protocoles)

    def default_port(self, protocol):
        """Port par défaut du protocole (méthode, contrat v1)."""
        return self._port


class TestResolveConfigDir(unittest.TestCase):
    """Smoke test — couverture complète déjà dans tests/test_gcm.py::TestResolveConfigDir."""

    def test_default_and_override(self):
        """--config explicite vs dossier par défaut (couverture complète : test_gcm.py)."""
        config_dir, remaining = gcm4_core._resolve_config_dir([], "/home/alice")
        self.assertEqual(config_dir, os.path.join("/home/alice", ".gcm"))
        self.assertEqual(remaining, [])
        config_dir, remaining = gcm4_core._resolve_config_dir(
            ["--config", "/tmp/x", "grp/host"], "/home/alice"
        )
        self.assertEqual(config_dir, "/tmp/x")
        self.assertEqual(remaining, ["grp/host"])


class TestDepInstallHint(unittest.TestCase):
    """dep_install_hint() — régression de transcription du 2026-08-30 verrouillée."""

    def _hint_for(self, os_release_content, pkg_arch="pkg-arch"):
        import builtins
        import tempfile
        import unittest.mock as mock

        real_open = builtins.open
        fd, path = tempfile.mkstemp()
        try:
            with os.fdopen(fd, "w") as f:
                f.write(os_release_content)

            def fake_open(p, *a, **k):
                if p == "/etc/os-release":
                    return real_open(path, *a, **k)
                return real_open(p, *a, **k)

            with mock.patch("builtins.open", side_effect=fake_open):
                return gcm4_core.dep_install_hint("pkg-deb", "pkg-fed", pkg_arch)
        finally:
            os.unlink(path)

    def test_debian_family(self):
        """Distributions Debian/Ubuntu/dérivées -> apt."""
        self.assertIn("apt install pkg-deb", self._hint_for("ID=ubuntu\nID_LIKE=debian\n"))

    def test_fedora_family(self):
        """Distributions Fedora/RHEL/dérivées -> dnf."""
        self.assertIn("dnf install pkg-fed", self._hint_for("ID=fedora\n"))

    def test_arch_family(self):
        """Distributions Arch/dérivées -> pacman."""
        self.assertIn("pacman -S pkg-arch", self._hint_for("ID=arch\n"))

    def test_arch_without_pkg_arch_falls_back_to_fedora_name(self):
        """Arch sans pkg_arch retombe sur le nom du paquet Fedora."""
        hint = self._hint_for("ID=manjaro\n", pkg_arch=None)
        self.assertIn("pacman -S pkg-fed", hint)

    def test_unreadable_os_release_gives_generic_hint(self):
        """/etc/os-release illisible -> message générique avec les deux noms de paquet."""
        import unittest.mock as mock

        with mock.patch("builtins.open", side_effect=OSError("no such file")):
            hint = gcm4_core.dep_install_hint("pkg-deb", "pkg-fed")
        self.assertIn("pkg-deb", hint)
        self.assertIn("pkg-fed", hint)


class TestColorToHex(unittest.TestCase):
    """color_to_hex() — régression de transcription du 2026-08-30 (paramètre diff manquant)."""

    class _FakeRGBA:
        def __init__(self, r, g, b):
            self.red, self.green, self.blue = r, g, b

    def test_no_diff(self):
        """Sans diff : conversion directe des composantes."""
        rgba = self._FakeRGBA(0.5, 0.2, 0.9)
        self.assertEqual(gcm4_core.color_to_hex(rgba), "#7f33e5")

    def test_with_negative_diff(self):
        """Avec diff négatif : composantes décalées avant conversion."""
        rgba = self._FakeRGBA(0.5, 0.2, 0.9)
        self.assertEqual(gcm4_core.color_to_hex(rgba, -14), "#7125d7")

    def test_not_zero_padded_like_original(self):
        """Pas de zero-padding (%x, pas %02x) : comportement d'origine conservé."""
        # Comportement d'origine (%x, pas %02x) : une composante à 0 ne
        # produit qu'un seul chiffre, pas deux — verrouillé intentionnellement.
        rgba = self._FakeRGBA(0, 0, 0)
        self.assertEqual(gcm4_core.color_to_hex(rgba), "#000")


class TestEncryption(unittest.TestCase):
    """xor/encrypt_old/decrypt_old/encrypt/decrypt — portage fidèle de test_gcm.py::TestEncryption."""

    def test_xor_roundtrip(self):
        """xor() est son propre inverse."""
        key, msg = "secret", "hello world"
        encrypted = gcm4_core.xor(key, msg)
        decrypted = gcm4_core.xor(key, "".join(encrypted))
        self.assertEqual("".join(decrypted), msg)

    def test_encrypt_decrypt_old_roundtrip(self):
        """encrypt_old/decrypt_old : aller-retour."""
        pw, plain = "mypasskey", "s3cr3t"
        self.assertEqual(gcm4_core.decrypt_old(pw, gcm4_core.encrypt_old(pw, plain)), plain)

    def test_encrypt_decrypt_aes_roundtrip(self):
        """encrypt/decrypt AES (version=1) : aller-retour."""
        pw, plain = "testpassword", "MyP@ssw0rd!"
        enc = gcm4_core.encrypt(pw, plain)
        self.assertEqual(gcm4_core.decrypt(pw, enc, version=1), plain)

    def test_decrypt_version_0_uses_old_xor(self):
        """version=0 (défaut) doit utiliser decrypt_old, pas AES — comportement d'origine."""
        pw, plain = "k", "abc"
        enc = gcm4_core.encrypt_old(pw, plain)
        self.assertEqual(gcm4_core.decrypt(pw, enc), plain)
        self.assertEqual(gcm4_core.decrypt(pw, enc, version=0), plain)


class TestGetUsername(unittest.TestCase):
    """get_username() — couverture minimale (fonction déjà triviale)."""

    def test_reads_user_env(self):
        """USER (ou LOGNAME/USERNAME) est bien lu depuis l'environnement."""
        import unittest.mock as mock

        with mock.patch.dict(os.environ, {"USER": "alice"}, clear=False):
            self.assertEqual(gcm4_core.get_username(), "alice")


class TestGetPassword(unittest.TestCase):
    """get_password() — divergence délibérée du 2026-08-30 (garde défensive)."""

    def test_returns_username_plus_key(self):
        """Nom d'utilisateur concaténé à la clé de chiffrement locale."""
        import unittest.mock as mock

        with mock.patch.dict(os.environ, {"USER": "bob"}, clear=False):
            gcm4_core._enc_passwd = "deadbeef"
            self.assertEqual(gcm4_core.get_password(), "bobdeadbeef")

    def test_no_username_does_not_crash(self):
        """Divergence assumée : ne lève plus TypeError si aucune variable USER/LOGNAME/USERNAME."""
        import unittest.mock as mock

        with mock.patch.dict(os.environ, {}, clear=True):
            gcm4_core._enc_passwd = "xyz"
            self.assertEqual(gcm4_core.get_password(), "xyz")


class TestEncryptionKeyLifecycle(unittest.TestCase):
    """load_encryption_key/initialise_encyption_key — réécriture exception (2026-08-30, §1).

    L'original affichait un msgbox() en cas d'erreur (impossible ici, module
    sans GTK) ; ces fonctions lèvent désormais RuntimeError.
    """

    def setUp(self):
        """Crée un dossier temporaire pour le fichier de clé."""
        import tempfile

        self._tmpdir = tempfile.mkdtemp()
        self._keyfile = os.path.join(self._tmpdir, ".gcm.key")

    def tearDown(self):
        """Nettoie le dossier temporaire."""
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_initialise_then_load_roundtrip(self):
        """Clé générée puis relue : round-trip identique."""
        gcm4_core.initialise_encyption_key(self._keyfile)
        key_after_init = gcm4_core._enc_passwd
        self.assertTrue(os.path.exists(self._keyfile))
        gcm4_core._enc_passwd = ""
        gcm4_core.load_encryption_key(self._keyfile)
        self.assertEqual(gcm4_core._enc_passwd, key_after_init)

    def test_load_missing_file_sets_empty_key_no_error(self):
        """Fichier absent = clé vide, sans lever d'exception (comportement d'origine)."""
        missing = os.path.join(self._tmpdir, "does-not-exist.key")
        gcm4_core.load_encryption_key(missing)
        self.assertEqual(gcm4_core._enc_passwd, "")

    def test_load_unreadable_file_raises_runtime_error(self):
        """Fichier illisible (contenu non-UTF-8) -> RuntimeError."""
        with open(self._keyfile, "wb") as f:
            f.write(b"\xff\xfe\x00\x01")  # invalide en UTF-8 → force une erreur de lecture
        with self.assertRaises(RuntimeError):
            gcm4_core.load_encryption_key(self._keyfile)

    def test_load_protected_key_raises_and_keeps_key_empty(self):
        """#98 : une clé protégée par mot de passe maître n'est jamais utilisée telle quelle."""
        import master_password_core

        for content in (
            master_password_core.protect_key_file_content("cafe", "pw"),
            master_password_core.protect_key_file_content_with_recovery("cafe", "pw")[0],
        ):
            with open(self._keyfile, "w") as f:
                f.write(content)
            gcm4_core._enc_passwd = "previous"
            with self.assertRaisesRegex(RuntimeError, "master password"):
                gcm4_core.load_encryption_key(self._keyfile)
            self.assertEqual(gcm4_core._enc_passwd, "")

    def test_initialise_unwritable_dir_raises_runtime_error(self):
        """Dossier parent inexistant -> RuntimeError, pas de crash silencieux."""
        bogus_path = os.path.join(self._tmpdir, "no-such-subdir", "key")
        with self.assertRaises(RuntimeError):
            gcm4_core.initialise_encyption_key(bogus_path)


class TestMigrateLegacyConfigDir(unittest.TestCase):
    """migrate_legacy_config_dir() — mécanisme générique, session-30 (renommage gcm4).

    La fonction ne décide d'aucun nom de dossier (voir CONSIGNES-AGENTS-IA.md
    §2) : old_dir/new_dir sont reçus en paramètres explicites, jamais
    supposés. Voir docs/gcm4-rename.md pour le plan complet du renommage
    dont ce mécanisme est un préalable non encore câblé.
    """

    def setUp(self):
        """Crée un dossier temporaire contenant old_dir/new_dir (non créés)."""
        import tempfile

        self._tmpdir = tempfile.mkdtemp()
        self._old_dir = os.path.join(self._tmpdir, "old")
        self._new_dir = os.path.join(self._tmpdir, "new")

    def tearDown(self):
        """Nettoie le dossier temporaire (restaure les permissions au besoin)."""
        import shutil

        os.chmod(self._old_dir, 0o755) if os.path.isdir(self._old_dir) else None
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_migrates_when_old_exists_and_new_missing(self):
        """old_dir présent, new_dir absent -> copie effectuée, True retourné."""
        os.makedirs(self._old_dir)
        with open(os.path.join(self._old_dir, "gcm.conf"), "w") as f:
            f.write("[Section]\nkey=value\n")
        result = gcm4_core.migrate_legacy_config_dir(self._old_dir, self._new_dir)
        self.assertTrue(result)
        with open(os.path.join(self._new_dir, "gcm.conf")) as f:
            self.assertEqual(f.read(), "[Section]\nkey=value\n")

    def test_old_dir_never_deleted_after_migration(self):
        """old_dir doit rester intact après une migration réussie (pas de move)."""
        os.makedirs(self._old_dir)
        with open(os.path.join(self._old_dir, "gcm.conf"), "w") as f:
            f.write("data")
        gcm4_core.migrate_legacy_config_dir(self._old_dir, self._new_dir)
        self.assertTrue(os.path.exists(os.path.join(self._old_dir, "gcm.conf")))

    def test_preserves_key_file_permissions(self):
        """Le fichier de clé (0600) garde sa permission après migration."""
        os.makedirs(self._old_dir)
        key_path = os.path.join(self._old_dir, ".gcm.key")
        with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
            f.write("secret")
        gcm4_core.migrate_legacy_config_dir(self._old_dir, self._new_dir)
        migrated_mode = os.stat(os.path.join(self._new_dir, ".gcm.key")).st_mode & 0o777
        self.assertEqual(migrated_mode, 0o600)

    def test_no_op_when_new_dir_already_exists(self):
        """new_dir déjà présent -> jamais écrasé, False retourné."""
        os.makedirs(self._old_dir)
        os.makedirs(self._new_dir)
        with open(os.path.join(self._old_dir, "gcm.conf"), "w") as f:
            f.write("stale")
        result = gcm4_core.migrate_legacy_config_dir(self._old_dir, self._new_dir)
        self.assertFalse(result)
        self.assertFalse(os.path.exists(os.path.join(self._new_dir, "gcm.conf")))

    def test_no_op_when_old_dir_missing(self):
        """old_dir absent (premier lancement) -> rien créé, False retourné."""
        result = gcm4_core.migrate_legacy_config_dir(self._old_dir, self._new_dir)
        self.assertFalse(result)
        self.assertFalse(os.path.exists(self._new_dir))

    def test_old_dir_is_a_file_not_a_directory_is_treated_as_absent(self):
        """old_dir existe mais est un fichier -> traité comme absent, pas de crash."""
        with open(self._old_dir, "w") as f:
            f.write("not a directory")
        result = gcm4_core.migrate_legacy_config_dir(self._old_dir, self._new_dir)
        self.assertFalse(result)
        self.assertFalse(os.path.exists(self._new_dir))

    def test_idempotent_second_call_does_not_touch_user_edits(self):
        """Un second appel après migration ne doit jamais écraser une édition faite depuis."""
        os.makedirs(self._old_dir)
        with open(os.path.join(self._old_dir, "gcm.conf"), "w") as f:
            f.write("v1")
        gcm4_core.migrate_legacy_config_dir(self._old_dir, self._new_dir)
        with open(os.path.join(self._new_dir, "gcm.conf"), "w") as f:
            f.write("v2-modifie-par-utilisateur")
        result = gcm4_core.migrate_legacy_config_dir(self._old_dir, self._new_dir)
        self.assertFalse(result)
        with open(os.path.join(self._new_dir, "gcm.conf")) as f:
            self.assertEqual(f.read(), "v2-modifie-par-utilisateur")

    def test_partial_failure_cleans_up_new_dir_and_leaves_old_dir_intact(self):
        """Échec en cours de copie -> new_dir nettoyé, old_dir jamais touché, RuntimeError."""
        from unittest import mock

        os.makedirs(self._old_dir)
        with open(os.path.join(self._old_dir, "gcm.conf"), "w") as f:
            f.write("data")

        def _fake_copytree(_src, dst, **_kwargs):
            os.makedirs(dst)
            with open(os.path.join(dst, "partial"), "w") as f:
                f.write("incomplete")
            raise OSError("disque plein (simulé)")

        with mock.patch("gcm4_core.shutil.copytree", side_effect=_fake_copytree):
            with self.assertRaises(RuntimeError):
                gcm4_core.migrate_legacy_config_dir(self._old_dir, self._new_dir)
        self.assertFalse(os.path.exists(self._new_dir))
        with open(os.path.join(self._old_dir, "gcm.conf")) as f:
            self.assertEqual(f.read(), "data")


class TestProtoRequiresAddress(unittest.TestCase):
    """proto_requires_address — adresse exigée selon le plugin du protocole (#297)."""

    def test_plugin_absent_requiert_l_adresse(self):
        """Plugin absent : l'adresse reste requise (vrai)."""
        registry = FakePluginRegistry({})
        self.assertTrue(gcm4_core.proto_requires_address("inexistant", registry))

    def test_plugin_sans_declaration_requiert_l_adresse(self):
        """Plugin sans déclaration : défaut du contrat, l'adresse est requise."""
        registry = FakePluginRegistry({"ssh": FakePlugin(22)})
        self.assertTrue(gcm4_core.proto_requires_address("ssh", registry))

    def test_methode_contrat_v1_appelee(self):
        """Contrat v1 : ``requires_address(protocol)`` méthode est appelée et rend False."""
        registry = FakePluginRegistry({"local": FakePluginSansAdresse()})
        self.assertFalse(gcm4_core.proto_requires_address("local", registry))

    def test_attribut_booleen_faux(self):
        """Attribut booléen False (plugin GTK3) : l'adresse n'est pas requise."""
        registry = FakePluginRegistry({"serie": FakePluginAttributAdresse(False)})
        self.assertFalse(gcm4_core.proto_requires_address("serie", registry))

    def test_attribut_booleen_vrai(self):
        """Attribut booléen True : l'adresse est requise."""
        registry = FakePluginRegistry({"rdp": FakePluginAttributAdresse(True)})
        self.assertTrue(gcm4_core.proto_requires_address("rdp", registry))


class TestProtoDefaultPort(unittest.TestCase):
    """proto_default_port/all_default_ports — réécriture paramètre explicite (2026-08-30, §2)."""

    def test_known_proto_returns_port(self):
        """Un proto connu retourne son port par défaut en str."""
        registry = FakePluginRegistry({"ssh": FakePlugin(22), "rdp": FakePlugin(3389)})
        self.assertEqual(gcm4_core.proto_default_port("ssh", registry), "22")
        self.assertEqual(gcm4_core.proto_default_port("rdp", registry), "3389")

    def test_unknown_proto_returns_empty_string(self):
        """Un proto inconnu (plugin absent) retourne une chaîne vide."""
        registry = FakePluginRegistry({})
        self.assertEqual(gcm4_core.proto_default_port("inexistant", registry), "")

    def test_plugin_without_default_port_returns_empty_string(self):
        """Un plugin sans port par défaut (None) retourne une chaîne vide."""
        registry = FakePluginRegistry({"local": FakePlugin(None)})
        self.assertEqual(gcm4_core.proto_default_port("local", registry), "")

    def test_all_default_ports_includes_empty_and_known(self):
        """L'ensemble agrège tous les ports connus, plus la chaîne vide."""
        registry = FakePluginRegistry(
            {"ssh": FakePlugin(22), "rdp": FakePlugin(3389), "local": FakePlugin(None)}
        )
        result = gcm4_core.all_default_ports(registry)
        self.assertEqual(result, {"22", "3389", ""})

    def test_methode_du_contrat_v1_est_appelee(self):
        """Contrat v1 : ``default_port(protocol)`` méthode est appelée avec le protocole (#146)."""
        registry = FakePluginRegistry({"demo": FakePluginContratV1(4242, protocoles=("demo",))})
        self.assertEqual(gcm4_core.proto_default_port("demo", registry), "4242")

    def test_all_default_ports_avec_methodes(self):
        """Les ports des méthodes du contrat v1 alimentent l'ensemble (via ``protocols``)."""
        registry = FakePluginRegistry(
            {
                "demo": FakePluginContratV1(4242, protocoles=("demo",)),
                "local": FakePluginContratV1(None, protocoles=("local",)),
            }
        )
        self.assertEqual(gcm4_core.all_default_ports(registry), {"4242", ""})


class TestComputeZoomSize(unittest.TestCase):
    """Smoke test — couverture complète déjà dans tests/test_gcm.py::TestComputeZoomSize."""

    def test_default_bounds(self):
        """Bornes par défaut (couverture complète : test_gcm.py)."""
        self.assertEqual(gcm4_core.compute_zoom_size(10, 5), 15)
        self.assertEqual(gcm4_core.compute_zoom_size(10, 1000), 72)
        self.assertEqual(gcm4_core.compute_zoom_size(10, -1000), 4)

    def test_custom_bounds(self):
        """Bornes personnalisées respectées."""
        self.assertEqual(gcm4_core.compute_zoom_size(10, 5, minimum=0, maximum=12), 12)


class TestResolveClusterCommand(unittest.TestCase):
    """resolve_cluster_command() — features.md §4.2, masquage mdp cluster."""

    def test_no_token_returns_text_unchanged(self):
        """Un texte sans marqueur #P= est retourné tel quel."""
        self.assertEqual(gcm4_core.resolve_cluster_command("show version"), "show version")

    def test_single_token_replaced_by_value(self):
        """#P=<valeur> est remplacé par <valeur> seule."""
        self.assertEqual(
            gcm4_core.resolve_cluster_command("mysql -u root -p#P=hunter2"),
            "mysql -u root -phunter2",
        )

    def test_multiple_tokens_all_replaced(self):
        """Plusieurs marqueurs dans la même commande sont tous résolus."""
        self.assertEqual(
            gcm4_core.resolve_cluster_command("#P=alpha then #P=beta"),
            "alpha then beta",
        )

    def test_empty_value_token(self):
        """#P= sans valeur (fin de ligne) devient une chaîne vide."""
        self.assertEqual(gcm4_core.resolve_cluster_command("echo #P="), "echo ")

    def test_value_stops_at_whitespace(self):
        """La valeur du marqueur s'arrête au premier espace."""
        self.assertEqual(
            gcm4_core.resolve_cluster_command("#P=secret --verbose"),
            "secret --verbose",
        )


class TestMaskClusterCommand(unittest.TestCase):
    """mask_cluster_command() — features.md §4.2, masquage mdp cluster."""

    def test_no_token_returns_text_unchanged(self):
        """Un texte sans marqueur #P= est retourné tel quel."""
        self.assertEqual(gcm4_core.mask_cluster_command("show version"), "show version")

    def test_value_replaced_by_same_length_asterisks(self):
        """La valeur est masquée par autant d'astérisques, marqueur #P= conservé."""
        self.assertEqual(
            gcm4_core.mask_cluster_command("mysql -u root -p#P=hunter2"),
            "mysql -u root -p#P=*******",
        )

    def test_multiple_tokens_all_masked(self):
        """Plusieurs marqueurs sont tous masqués indépendamment."""
        self.assertEqual(
            gcm4_core.mask_cluster_command("#P=ab then #P=cdef"),
            "#P=** then #P=****",
        )

    def test_empty_value_token_stays_empty(self):
        """#P= sans valeur reste #P= (zéro astérisque)."""
        self.assertEqual(gcm4_core.mask_cluster_command("echo #P="), "echo #P=")

    def test_masked_output_never_contains_original_value(self):
        """Le mot de passe en clair n'apparaît jamais dans le résultat masqué."""
        masked = gcm4_core.mask_cluster_command("login #P=SuperSecret123")
        self.assertNotIn("SuperSecret123", masked)


class TestResolveAutoCloseTab(unittest.TestCase):
    """resolve_auto_close_tab() — features.md §4.2 #77, option par host."""

    def test_empty_override_falls_back_to_global(self):
        """Surcharge vide ("") : le réglage global est utilisé tel quel."""
        self.assertEqual(gcm4_core.resolve_auto_close_tab("", 0), 0)
        self.assertEqual(gcm4_core.resolve_auto_close_tab("", 1), 1)
        self.assertEqual(gcm4_core.resolve_auto_close_tab("", 2), 2)

    def test_valid_override_wins_over_global(self):
        """Une surcharge valide ("0"/"1"/"2") prime sur le réglage global."""
        self.assertEqual(gcm4_core.resolve_auto_close_tab("0", 1), 0)
        self.assertEqual(gcm4_core.resolve_auto_close_tab("1", 0), 1)
        self.assertEqual(gcm4_core.resolve_auto_close_tab("2", 1), 2)

    def test_override_result_is_always_int(self):
        """La surcharge stockée en str est bien convertie en int au retour."""
        result = gcm4_core.resolve_auto_close_tab("1", 0)
        self.assertIsInstance(result, int)

    def test_invalid_override_falls_back_to_global(self):
        """Une valeur ni ""/"0"/"1"/"2" replie silencieusement sur le réglage
        global (INI corrompu, ancien format) plutôt que de lever.
        """
        self.assertEqual(gcm4_core.resolve_auto_close_tab("garbage", 2), 2)
        self.assertEqual(gcm4_core.resolve_auto_close_tab(None, 1), 1)


class FakeHost:
    """Simule un Host minimal pour les tests de workspace_hosts()."""

    def __init__(self, name, workspace=False):
        """Initialise avec un nom et l'appartenance à l'espace de travail."""
        self.name = name
        self.workspace = workspace


class TestWorkspaceHosts(unittest.TestCase):
    """workspace_hosts() — features.md §4.2, "Espace de travail (workspace)"."""

    def test_filters_across_groups(self):
        """Ne retient que les hôtes workspace=True, tous groupes confondus."""
        groups = {
            "prod": [FakeHost("web1", workspace=True), FakeHost("web2")],
            "dev": [FakeHost("test1", workspace=True)],
        }
        result = gcm4_core.workspace_hosts(groups)
        self.assertEqual({h.name for h in result}, {"web1", "test1"})

    def test_empty_groups_returns_empty_list(self):
        """Aucun groupe -> liste vide, pas d'exception."""
        self.assertEqual(gcm4_core.workspace_hosts({}), [])

    def test_no_workspace_host_returns_empty_list(self):
        """Aucun hôte marqué -> liste vide."""
        groups = {"prod": [FakeHost("web1"), FakeHost("web2")]}
        self.assertEqual(gcm4_core.workspace_hosts(groups), [])

    def test_missing_attribute_treated_as_false(self):
        """Un objet sans attribut workspace (duck-typing) est ignoré plutôt
        que de lever une AttributeError.
        """

        class BareObject:
            pass

        groups = {"g": [BareObject(), FakeHost("web1", workspace=True)]}
        result = gcm4_core.workspace_hosts(groups)
        self.assertEqual([h.name for h in result], ["web1"])


class FakeGroupedHost:
    """Simule un Host minimal (groupe + nom) pour les tests de sauvegarde/
    restauration des onglets ouverts (serialize_open_tabs/resolve_host_specs).
    """

    def __init__(self, group, name):
        """Initialise avec un groupe et un nom."""
        self.group = group
        self.name = name


class TestSerializeOpenTabs(unittest.TestCase):
    """serialize_open_tabs() — features.md §4.2, "Sauvegarde/restauration
    des onglets ouverts".
    """

    def test_builds_group_slash_name_specs(self):
        """Un hôte groupe+nom devient "groupe/nom"."""
        hosts = [FakeGroupedHost("prod", "web1"), FakeGroupedHost("dev", "test1")]
        self.assertEqual(gcm4_core.serialize_open_tabs(hosts), ["prod/web1", "dev/test1"])

    def test_empty_list_returns_empty_list(self):
        """Aucun onglet ouvert -> liste vide."""
        self.assertEqual(gcm4_core.serialize_open_tabs([]), [])

    def test_skips_host_without_group_or_name(self):
        """Un hôte sans groupe (ex. l'onglet "local") est ignoré."""
        hosts = [FakeGroupedHost("", "local"), FakeGroupedHost("prod", "web1")]
        self.assertEqual(gcm4_core.serialize_open_tabs(hosts), ["prod/web1"])

    def test_deduplicates_same_host_opened_twice(self):
        """Un même hôte ouvert dans deux onglets n'apparaît qu'une fois."""
        hosts = [FakeGroupedHost("prod", "web1"), FakeGroupedHost("prod", "web1")]
        self.assertEqual(gcm4_core.serialize_open_tabs(hosts), ["prod/web1"])


class TestParseOpenTabs(unittest.TestCase):
    """parse_open_tabs() — lecture symétrique de serialize_open_tabs()."""

    def test_splits_comma_separated_specs(self):
        """Chaîne bien formée -> liste des identifiants."""
        self.assertEqual(
            gcm4_core.parse_open_tabs("prod/web1,dev/test1"), ["prod/web1", "dev/test1"]
        )

    def test_empty_string_returns_empty_list(self):
        """Chaîne vide (premier démarrage) -> liste vide."""
        self.assertEqual(gcm4_core.parse_open_tabs(""), [])

    def test_none_returns_empty_list(self):
        """None -> liste vide, pas d'exception."""
        self.assertEqual(gcm4_core.parse_open_tabs(None), [])

    def test_ignores_stray_commas_and_whitespace(self):
        """Virgules superflues/espaces ne produisent pas d'entrées vides."""
        self.assertEqual(
            gcm4_core.parse_open_tabs(" prod/web1 , ,dev/test1,"), ["prod/web1", "dev/test1"]
        )


class TestResolveHostSpecs(unittest.TestCase):
    """resolve_host_specs() — résolution "groupe/nom" -> objets hôte,
    logique reprise de la boucle historique de sys.argv[1:] (Wmain.__init__).
    """

    def setUp(self):
        """Prépare un dictionnaire groups minimal partagé par les tests."""
        self.groups = {
            "prod": [FakeGroupedHost("prod", "web1"), FakeGroupedHost("prod", "web2")],
            "dev": [FakeGroupedHost("dev", "test1")],
        }

    def test_resolves_known_specs_in_order(self):
        """Les specs valides sont résolues dans leur ordre d'origine."""
        result = gcm4_core.resolve_host_specs(["dev/test1", "prod/web1"], self.groups)
        self.assertEqual([h.name for h in result], ["test1", "web1"])

    def test_unknown_group_is_skipped(self):
        """Un groupe absent de groups est ignoré, pas d'exception."""
        result = gcm4_core.resolve_host_specs(["ghost/web1"], self.groups)
        self.assertEqual(result, [])

    def test_unknown_name_in_known_group_is_skipped(self):
        """Un nom d'hôte introuvable dans un groupe existant est ignoré."""
        result = gcm4_core.resolve_host_specs(["prod/missing"], self.groups)
        self.assertEqual(result, [])

    def test_malformed_spec_without_slash_is_skipped(self):
        """Une spec sans "/" est ignorée plutôt que de lever une exception."""
        result = gcm4_core.resolve_host_specs(["no-slash-here"], self.groups)
        self.assertEqual(result, [])

    def test_empty_groups_returns_empty_list(self):
        """Aucun groupe connu -> liste vide pour n'importe quelle spec."""
        self.assertEqual(gcm4_core.resolve_host_specs(["prod/web1"], {}), [])


class TestSerializeGroupColors(unittest.TestCase):
    """serialize_group_colors() — sérialisation "groupe=couleur,..." pour
    la persistance INI (features.md §4.2, "Couleurs par groupe").
    """

    def test_serializes_entries_as_group_equals_color(self):
        """Chaque entrée devient "groupe=couleur", séparée par une virgule."""
        result = gcm4_core.serialize_group_colors({"Prod": "#ff8800", "Dev": "#00aaff"})
        self.assertEqual(result, "Prod=#ff8800,Dev=#00aaff")

    def test_empty_dict_returns_empty_string(self):
        """Un dictionnaire vide sérialise en chaîne vide."""
        self.assertEqual(gcm4_core.serialize_group_colors({}), "")

    def test_none_returns_empty_string(self):
        """None est traité comme un dictionnaire vide."""
        self.assertEqual(gcm4_core.serialize_group_colors(None), "")

    def test_entries_with_empty_group_or_color_are_skipped(self):
        """Une entrée sans groupe ou sans couleur n'est pas sérialisée."""
        result = gcm4_core.serialize_group_colors({"": "#ff8800", "Dev": "", "Prod": "#00aaff"})
        self.assertEqual(result, "Prod=#00aaff")


class TestParseGroupColors(unittest.TestCase):
    """parse_group_colors() — inverse de serialize_group_colors()."""

    def test_parses_valid_entries(self):
        """Des entrées "groupe=couleur" valides sont toutes résolues."""
        result = gcm4_core.parse_group_colors("Prod=#ff8800,Dev=#00aaff")
        self.assertEqual(result, {"Prod": "#ff8800", "Dev": "#00aaff"})

    def test_empty_or_none_returns_empty_dict(self):
        """Chaîne vide ou None -> dictionnaire vide, pas d'exception."""
        self.assertEqual(gcm4_core.parse_group_colors(""), {})
        self.assertEqual(gcm4_core.parse_group_colors(None), {})

    def test_malformed_entry_without_equals_is_skipped(self):
        """Une entrée sans "=" est ignorée plutôt que de lever une exception."""
        result = gcm4_core.parse_group_colors("Prod=#ff8800,not-an-entry,Dev=#00aaff")
        self.assertEqual(result, {"Prod": "#ff8800", "Dev": "#00aaff"})

    def test_roundtrip_with_serialize(self):
        """parse(serialize(x)) restitue le dictionnaire d'origine."""
        original = {"Prod/Web": "#ff8800", "Dev": "#00aaff"}
        self.assertEqual(
            gcm4_core.parse_group_colors(gcm4_core.serialize_group_colors(original)), original
        )


class TestResolveGroupColor(unittest.TestCase):
    """resolve_group_color() — héritage de couleur depuis les dossiers
    ancêtres, jusqu'au groupe/hôte demandé.
    """

    def test_exact_match_wins(self):
        """Un groupe avec sa propre couleur ne remonte pas aux ancêtres."""
        result = gcm4_core.resolve_group_color(
            "Prod/Web", {"Prod": "#111111", "Prod/Web": "#222222"}
        )
        self.assertEqual(result, "#222222")

    def test_inherits_from_ancestor_folder(self):
        """Un sous-groupe sans couleur propre hérite de son ancêtre coloré."""
        result = gcm4_core.resolve_group_color("Prod/Web/Front", {"Prod": "#111111"})
        self.assertEqual(result, "#111111")

    def test_no_match_returns_default(self):
        """Aucun ancêtre coloré -> valeur par défaut (chaîne vide)."""
        self.assertEqual(gcm4_core.resolve_group_color("Dev", {"Prod": "#111111"}), "")

    def test_custom_default_is_returned(self):
        """La valeur par défaut personnalisée est bien celle retournée."""
        result = gcm4_core.resolve_group_color("Dev", {}, default="#fff")
        self.assertEqual(result, "#fff")

    def test_empty_group_returns_default(self):
        """Un groupe vide/None (ex. hôte "local") retourne toujours default."""
        self.assertEqual(gcm4_core.resolve_group_color("", {"Prod": "#111111"}), "")
        self.assertEqual(gcm4_core.resolve_group_color(None, {"Prod": "#111111"}), "")

    def test_empty_color_map_returns_default(self):
        """Aucune couleur définie nulle part -> valeur par défaut."""
        self.assertEqual(gcm4_core.resolve_group_color("Prod/Web", {}), "")


class TestResolveConnectionHookCommand(unittest.TestCase):
    """resolve_connection_hook_command() — backlog features.md §4.2 #116
    ("Scripting avant/après connexion"), volet "avant" de cette session.
    """

    def test_empty_template_returns_none(self):
        """Chaîne vide -> None (aucun hook à exécuter)."""
        self.assertIsNone(gcm4_core.resolve_connection_hook_command(""))

    def test_none_template_returns_none(self):
        """None -> None, sans lever d'exception."""
        self.assertIsNone(gcm4_core.resolve_connection_hook_command(None))

    def test_whitespace_only_template_returns_none(self):
        """Un modèle composé uniquement d'espaces compte comme vide."""
        self.assertIsNone(gcm4_core.resolve_connection_hook_command("   "))

    def test_all_placeholders_substituted(self):
        """Les quatre marqueurs connus sont tous remplacés correctement."""
        result = gcm4_core.resolve_connection_hook_command(
            "echo {name} {address} {group} {protocol}",
            host_name="srv1",
            host_address="10.0.0.1",
            host_group="Prod/Web",
            protocol="ssh",
        )
        self.assertEqual(result, "echo srv1 10.0.0.1 Prod/Web ssh")

    def test_template_without_placeholders_returned_as_is(self):
        """Un modèle sans marqueur est retourné tel quel."""
        result = gcm4_core.resolve_connection_hook_command("notify-send hello", host_name="srv1")
        self.assertEqual(result, "notify-send hello")

    def test_partial_placeholders_others_default_to_empty(self):
        """Les marqueurs non fournis par l'appelant sont substitués par ""."""
        result = gcm4_core.resolve_connection_hook_command("echo {name}-{group}", host_name="srv1")
        self.assertEqual(result, "echo srv1-")

    def test_unknown_placeholder_returns_template_unchanged(self):
        """Un marqueur inconnu (faute de frappe) ne lève pas d'exception."""
        result = gcm4_core.resolve_connection_hook_command("echo {nope}", host_name="srv1")
        self.assertEqual(result, "echo {nope}")


class TestResolveThemeMode(unittest.TestCase):
    """resolve_theme_mode() — backlog features.md §4.2 #113 ("Mode sombre
    dédié"), centralisation de la validation entre le chargement de
    gcm.ini et la présélection du combo Système/Clair/Sombre des
    Préférences.
    """

    def test_system_is_valid(self):
        """La valeur "system" est retournée inchangée."""
        self.assertEqual(gcm4_core.resolve_theme_mode("system"), "system")

    def test_light_is_valid(self):
        """La valeur "light" est retournée inchangée."""
        self.assertEqual(gcm4_core.resolve_theme_mode("light"), "light")

    def test_dark_is_valid(self):
        """La valeur "dark" est retournée inchangée."""
        self.assertEqual(gcm4_core.resolve_theme_mode("dark"), "dark")

    def test_unknown_string_falls_back_to_system(self):
        """Une valeur inconnue (fichier .ini corrompu) replie sur "system"."""
        self.assertEqual(gcm4_core.resolve_theme_mode("blue"), "system")

    def test_empty_string_falls_back_to_system(self):
        """Une chaîne vide replie sur "system"."""
        self.assertEqual(gcm4_core.resolve_theme_mode(""), "system")

    def test_none_falls_back_to_system(self):
        """None replie sur "system", sans lever d'exception."""
        self.assertEqual(gcm4_core.resolve_theme_mode(None), "system")

    def test_case_sensitive(self):
        """La comparaison est sensible à la casse : "Dark" n'est pas "dark"."""
        self.assertEqual(gcm4_core.resolve_theme_mode("Dark"), "system")


if __name__ == "__main__":
    unittest.main()


class TestClusterFavorites(unittest.TestCase):
    """Commandes favorites du dialogue Cluster (#84)."""

    def _cp(self, text=None):
        """ConfigParser lu depuis ``text`` (vide si None)."""
        import configparser

        cp = configparser.RawConfigParser()
        if text is not None:
            cp.read_string(text)
        return cp

    def test_defaults_when_section_missing(self):
        """Section absente : df, apt update, updatedb (décision de l'auteure)."""
        self.assertEqual(
            gcm4_core.load_cluster_favorites(self._cp()), ["df", "apt update", "updatedb"]
        )
        self.assertEqual(gcm4_core.CLUSTER_FAVORITES_DEFAULT, ("df", "apt update", "updatedb"))

    def test_empty_section_stays_empty(self):
        """Section présente mais vide : l'utilisateur a tout retiré."""
        self.assertEqual(gcm4_core.load_cluster_favorites(self._cp("[cluster_favorites]\n")), [])

    def test_load_orders_by_index_and_filters(self):
        """Tri numérique, clés invalides, valeurs vides et doublons ignorés."""
        cp = self._cp(
            "[cluster_favorites]\n"
            "command10 = uptime\n"
            "command2 = ls -l\n"
            "command1 = df -h\n"
            "other = ignored\n"
            "commandx = ignored\n"
            "command3 =\n"
            "command4 = ls -l\n"
            "command5 = echo a\\nb\n"
        )
        self.assertEqual(
            gcm4_core.load_cluster_favorites(cp), ["df -h", "ls -l", "echo a\nb", "uptime"]
        )

    def test_save_then_load_round_trip(self):
        """Écriture puis relecture, retours à la ligne compris, section remplacée."""
        cp = self._cp("[cluster_favorites]\ncommand1 = old\ncommand2 = older\n")
        gcm4_core.save_cluster_favorites(cp, ["df", "printf 'a\nb'"])
        self.assertEqual(cp.get("cluster_favorites", "command2"), "printf 'a\\nb'")
        self.assertFalse(cp.has_option("cluster_favorites", "command3"))
        self.assertEqual(gcm4_core.load_cluster_favorites(cp), ["df", "printf 'a\nb'"])

    def test_save_empty_list_keeps_section(self):
        """Une liste vide écrit une section vide (pas de retour aux défauts)."""
        cp = self._cp()
        gcm4_core.save_cluster_favorites(cp, [])
        self.assertTrue(cp.has_section("cluster_favorites"))
        self.assertEqual(gcm4_core.load_cluster_favorites(cp), [])

    def test_add_appends_stripped_command_without_mutating(self):
        """Ajout en fin de liste, blancs de fin retirés, entrée non modifiée."""
        favorites = ["df"]
        self.assertEqual(
            gcm4_core.add_cluster_favorite(favorites, "free -m \n"), ["df", "free -m"]
        )
        self.assertEqual(favorites, ["df"])

    def test_add_existing_command_is_noop(self):
        """Une commande déjà présente n'est pas dupliquée."""
        self.assertEqual(gcm4_core.add_cluster_favorite(["df"], "df"), ["df"])

    def test_add_rejects_empty_command(self):
        """Commande vide ou faite de blancs refusée."""
        with self.assertRaisesRegex(ValueError, "empty command"):
            gcm4_core.add_cluster_favorite([], " \n ")

    def test_add_rejects_password_token(self):
        """Un marqueur #P= fuirait en clair dans gcm.conf : refusé."""
        with self.assertRaisesRegex(ValueError, "password token"):
            gcm4_core.add_cluster_favorite([], "mysql -p#P=secret")

    def test_remove(self):
        """Retrait d'une commande ; absente, liste inchangée ; entrée non modifiée."""
        favorites = ["df", "apt update"]
        self.assertEqual(gcm4_core.remove_cluster_favorite(favorites, "df"), ["apt update"])
        self.assertEqual(gcm4_core.remove_cluster_favorite(favorites, "nope"), favorites)
        self.assertEqual(favorites, ["df", "apt update"])
