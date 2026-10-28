# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests unitaires pour master_password_core.py.

Comme tests/test_gcm4_core.py, ce fichier importe le module directement —
aucun stub GTK nécessaire, le module n'a aucune dépendance gi/Gtk (voir
CONSIGNES-AGENTS-IA.md §5).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import master_password_core as core  # noqa: E402


class TestIsProtected(unittest.TestCase):
    """is_protected() — distinction contenu protégé / legacy en clair."""

    def test_legacy_plain_key_is_not_protected(self):
        """Un KEY_FILE legacy (hex brut, produit par initialise_encyption_key) n'est pas protégé."""
        self.assertFalse(core.is_protected("1a2b3c4d5e6f"))

    def test_empty_content_is_not_protected(self):
        """Un contenu vide (première initialisation) n'est pas protégé."""
        self.assertFalse(core.is_protected(""))

    def test_protected_content_is_detected(self):
        """Le contenu produit par protect_key_file_content() est détecté comme protégé."""
        protected = core.protect_key_file_content("deadbeef", "hunter2")
        self.assertTrue(core.is_protected(protected))


class TestProtectAndUnlock(unittest.TestCase):
    """Round-trip protection/déverrouillage et détection de mot de passe incorrect."""

    def test_round_trip_returns_original_key(self):
        """unlock_key_file_content() renvoie exactement la clé passée à protect_key_file_content()."""
        raw_key = "cafef00ddeadbeef1234567890abcdef"
        protected = core.protect_key_file_content(raw_key, "correct horse battery staple")
        self.assertEqual(
            core.unlock_key_file_content(protected, "correct horse battery staple"), raw_key
        )

    def test_wrong_master_password_raises(self):
        """Un mot de passe maître incorrect lève InvalidMasterPasswordError, sans renvoyer de clé corrompue."""
        protected = core.protect_key_file_content("deadbeef", "right-password")
        with self.assertRaises(core.InvalidMasterPasswordError):
            core.unlock_key_file_content(protected, "wrong-password")

    def test_empty_master_password_rejected_at_protect_time(self):
        """protect_key_file_content() refuse un mot de passe maître vide (fausse sécurité)."""
        with self.assertRaises(ValueError):
            core.protect_key_file_content("deadbeef", "")

    def test_two_protections_of_same_key_produce_different_output(self):
        """Deux appels sur la même clé/mot de passe produisent des sorties différentes (sel aléatoire)."""
        protected_a = core.protect_key_file_content("deadbeef", "hunter2")
        protected_b = core.protect_key_file_content("deadbeef", "hunter2")
        self.assertNotEqual(protected_a, protected_b)
        # mais les deux se déverrouillent bien vers la même clé d'origine
        self.assertEqual(core.unlock_key_file_content(protected_a, "hunter2"), "deadbeef")
        self.assertEqual(core.unlock_key_file_content(protected_b, "hunter2"), "deadbeef")

    def test_unicode_master_password(self):
        """Un mot de passe maître contenant des caractères non-ASCII fonctionne (encodage UTF-8 explicite)."""
        protected = core.protect_key_file_content("deadbeef", "météo-42-éàü")
        self.assertEqual(core.unlock_key_file_content(protected, "météo-42-éàü"), "deadbeef")


class TestCorruptedContent(unittest.TestCase):
    """Détection d'un contenu protégé malformé, sans lever d'exception non gérée."""

    def test_missing_magic_prefix_raises_corrupted(self):
        """Un contenu sans préfixe magique lève CorruptedProtectedContentError, pas InvalidMasterPasswordError."""
        with self.assertRaises(core.CorruptedProtectedContentError):
            core.unlock_key_file_content("not-a-protected-content", "hunter2")

    def test_wrong_number_of_fields_raises_corrupted(self):
        """Un contenu avec le bon préfixe mais un nombre de champs incorrect est rejeté proprement."""
        with self.assertRaises(core.CorruptedProtectedContentError):
            core.unlock_key_file_content(f"{core.MASTER_PASSWORD_MAGIC}onlyonefield", "hunter2")

    def test_invalid_base64_salt_raises_corrupted(self):
        """Un sel qui n'est pas du base64 valide est rejeté proprement (pas de traceback binascii brut)."""
        malformed = f"{core.MASTER_PASSWORD_MAGIC}not-valid-base64!!:verifier:ciphertext"
        with self.assertRaises(core.CorruptedProtectedContentError):
            core.unlock_key_file_content(malformed, "hunter2")


class TestRewrapAndRemove(unittest.TestCase):
    """Changement de mot de passe maître et retrait de la protection."""

    def test_rewrap_with_correct_old_password(self):
        """rewrap_key_file_content() change le mot de passe maître sans changer la clé locale."""
        raw_key = "deadbeef"
        protected = core.protect_key_file_content(raw_key, "old-password")
        rewrapped = core.rewrap_key_file_content(protected, "old-password", "new-password")
        self.assertTrue(core.is_protected(rewrapped))
        self.assertEqual(core.unlock_key_file_content(rewrapped, "new-password"), raw_key)
        with self.assertRaises(core.InvalidMasterPasswordError):
            core.unlock_key_file_content(rewrapped, "old-password")

    def test_rewrap_with_wrong_old_password_raises(self):
        """rewrap_key_file_content() refuse de changer le mot de passe sans le bon mot de passe actuel."""
        protected = core.protect_key_file_content("deadbeef", "old-password")
        with self.assertRaises(core.InvalidMasterPasswordError):
            core.rewrap_key_file_content(protected, "wrong-password", "new-password")

    def test_remove_protection_returns_plain_raw_key(self):
        """remove_master_password_protection() renvoie la clé en clair, prête à réécrire telle quelle."""
        raw_key = "deadbeef"
        protected = core.protect_key_file_content(raw_key, "hunter2")
        plain = core.remove_master_password_protection(protected, "hunter2")
        self.assertEqual(plain, raw_key)
        self.assertFalse(core.is_protected(plain))

    def test_remove_protection_with_wrong_password_raises(self):
        """remove_master_password_protection() exige le bon mot de passe maître avant de retirer la protection."""
        protected = core.protect_key_file_content("deadbeef", "hunter2")
        with self.assertRaises(core.InvalidMasterPasswordError):
            core.remove_master_password_protection(protected, "wrong")


if __name__ == "__main__":
    unittest.main()


class TestRecoveryCode(unittest.TestCase):
    """Format v2 : double enveloppe mot de passe / code de recouvrement (#98)."""

    RAW = "cafef00ddeadbeef1234567890abcdef"

    def test_generated_code_shape_and_randomness(self):
        """24 caractères de l'alphabet sans ambiguïté, groupés par 4, tous différents."""
        codes = {core.generate_recovery_code() for _ in range(20)}
        self.assertEqual(len(codes), 20)
        for code in codes:
            groups = code.split("-")
            self.assertEqual([len(g) for g in groups], [4] * 6)
            self.assertTrue(set("".join(groups)) <= set(core.RECOVERY_CODE_ALPHABET))

    def test_normalize_recovery_code(self):
        """Casse, tirets et blancs indifférents."""
        self.assertEqual(core.normalize_recovery_code(" abcd-EFGH \n2345 "), "ABCDEFGH2345")

    def test_both_envelopes_unlock_same_key(self):
        """Le mot de passe et le code déverrouillent la même clé."""
        content, code = core.protect_key_file_content_with_recovery(self.RAW, "s3cret")
        self.assertTrue(content.startswith(core.MASTER_PASSWORD_MAGIC_V2))
        self.assertTrue(core.is_protected(content))
        self.assertTrue(core.has_recovery_code(content))
        self.assertEqual(core.unlock_key_file_content(content, "s3cret"), self.RAW)
        self.assertEqual(core.unlock_with_recovery_code(content, code), self.RAW)
        self.assertEqual(
            core.unlock_with_recovery_code(content, code.lower().replace("-", " ")), self.RAW
        )

    def test_code_is_not_stored(self):
        """Ni le code ni sa forme normalisée n'apparaissent dans le contenu."""
        content, code = core.protect_key_file_content_with_recovery(self.RAW, "s3cret")
        self.assertNotIn(code, content)
        self.assertNotIn(core.normalize_recovery_code(code), content)
        self.assertNotIn(self.RAW, content)

    def test_wrong_code_and_wrong_password(self):
        """Code faux : InvalidRecoveryCodeError ; mot de passe faux : InvalidMasterPasswordError."""
        content, code = core.protect_key_file_content_with_recovery(self.RAW, "s3cret")
        with self.assertRaises(core.InvalidRecoveryCodeError):
            core.unlock_with_recovery_code(content, "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA")
        with self.assertRaises(core.InvalidMasterPasswordError):
            core.unlock_key_file_content(content, "nope")
        # le mot de passe ne sert pas de code, ni l'inverse
        with self.assertRaises(core.InvalidRecoveryCodeError):
            core.unlock_with_recovery_code(content, "s3cret")
        with self.assertRaises(core.InvalidMasterPasswordError):
            core.unlock_key_file_content(content, code)

    def test_empty_password_rejected(self):
        """Mot de passe maître vide refusé, en protection comme en recouvrement."""
        with self.assertRaises(ValueError):
            core.protect_key_file_content_with_recovery(self.RAW, "")
        content, code = core.protect_key_file_content_with_recovery(self.RAW, "s3cret")
        with self.assertRaises(ValueError):
            core.recover_with_code(content, code, "")

    def test_change_password_keeps_recovery_code(self):
        """Changer le mot de passe garde le v2 et le même code valide."""
        content, code = core.protect_key_file_content_with_recovery(self.RAW, "old")
        changed = core.rewrap_key_file_content(content, "old", "new")
        self.assertTrue(core.has_recovery_code(changed))
        self.assertEqual(core.unlock_key_file_content(changed, "new"), self.RAW)
        with self.assertRaises(core.InvalidMasterPasswordError):
            core.unlock_key_file_content(changed, "old")
        self.assertEqual(core.unlock_with_recovery_code(changed, code), self.RAW)

    def test_change_password_v1_stays_v1(self):
        """Un contenu v1 reste v1 après changement de mot de passe."""
        changed = core.rewrap_key_file_content(
            core.protect_key_file_content(self.RAW, "old"), "old", "new"
        )
        self.assertFalse(core.has_recovery_code(changed))
        self.assertEqual(core.unlock_key_file_content(changed, "new"), self.RAW)

    def test_regenerate_code_invalidates_old_one(self):
        """Régénération : nouveau code valide, ancien refusé, mot de passe inchangé."""
        content, old_code = core.protect_key_file_content_with_recovery(self.RAW, "s3cret")
        regenerated, new_code = core.regenerate_recovery_code(content, "s3cret")
        self.assertNotEqual(old_code, new_code)
        self.assertEqual(core.unlock_with_recovery_code(regenerated, new_code), self.RAW)
        self.assertEqual(core.unlock_key_file_content(regenerated, "s3cret"), self.RAW)
        with self.assertRaises(core.InvalidRecoveryCodeError):
            core.unlock_with_recovery_code(regenerated, old_code)
        with self.assertRaises(core.InvalidMasterPasswordError):
            core.regenerate_recovery_code(content, "nope")

    def test_regenerate_upgrades_v1(self):
        """Régénérer le code d'un contenu v1 le fait passer en v2."""
        upgraded, code = core.regenerate_recovery_code(
            core.protect_key_file_content(self.RAW, "pw"), "pw"
        )
        self.assertTrue(core.has_recovery_code(upgraded))
        self.assertEqual(core.unlock_with_recovery_code(upgraded, code), self.RAW)

    def test_recover_with_code_imposes_new_password_and_code(self):
        """Mot de passe oublié : nouveau mot de passe, nouveau code, l'ancien code consommé."""
        content, code = core.protect_key_file_content_with_recovery(self.RAW, "forgotten")
        recovered, new_code = core.recover_with_code(content, code, "fresh")
        self.assertEqual(core.unlock_key_file_content(recovered, "fresh"), self.RAW)
        self.assertEqual(core.unlock_with_recovery_code(recovered, new_code), self.RAW)
        with self.assertRaises(core.InvalidMasterPasswordError):
            core.unlock_key_file_content(recovered, "forgotten")
        with self.assertRaises(core.InvalidRecoveryCodeError):
            core.unlock_with_recovery_code(recovered, code)
        with self.assertRaises(core.InvalidRecoveryCodeError):
            core.recover_with_code(content, "BAD", "fresh")

    def test_v1_has_no_recovery_code(self):
        """Un contenu v1 n'a pas de code : RecoveryCodeUnavailableError."""
        content = core.protect_key_file_content(self.RAW, "pw")
        self.assertFalse(core.has_recovery_code(content))
        with self.assertRaises(core.RecoveryCodeUnavailableError):
            core.unlock_with_recovery_code(content, "ABCD")

    def test_remove_protection_v2(self):
        """Retirer la protection d'un v2 rend la clé en clair."""
        content, _code = core.protect_key_file_content_with_recovery(self.RAW, "pw")
        self.assertEqual(core.remove_master_password_protection(content, "pw"), self.RAW)

    def test_corrupted_v2(self):
        """v2 tronqué ou au sel invalide : CorruptedProtectedContentError."""
        with self.assertRaises(core.CorruptedProtectedContentError):
            core.unlock_key_file_content(f"{core.MASTER_PASSWORD_MAGIC_V2}a:b:c", "pw")
        with self.assertRaises(core.CorruptedProtectedContentError):
            core.unlock_with_recovery_code(f"{core.MASTER_PASSWORD_MAGIC_V2}AAAA:v:c:!!:v:c", "pw")

    def test_secrets_never_logged(self):
        """Ni le mot de passe, ni le code, ni la clé n'apparaissent dans les journaux."""
        from loguru import logger

        lines = []
        sink = logger.add(lines.append, level="DEBUG", format="{message}")
        try:
            content, code = core.protect_key_file_content_with_recovery(self.RAW, "Pa55-word")
            core.unlock_with_recovery_code(content, code)
            core.recover_with_code(content, code, "N3w-word")
        finally:
            logger.remove(sink)
        journal = "".join(lines)
        self.assertTrue(journal)
        for secret in (
            self.RAW,
            "Pa55-word",
            "N3w-word",
            code,
            core.normalize_recovery_code(code),
        ):
            self.assertNotIn(secret, journal)
