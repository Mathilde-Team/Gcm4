# SPDX-License-Identifier: GPL-3.0-or-later
"""Chemins d'erreur et replis de gcm4_core.py.

Complète les tests existants sur les branches restées sans couverture :
détection d'OS inconnue, replis de traduction (locale indisponible) et
échecs de chiffrement/déchiffrement AES.
"""

from __future__ import annotations

import builtins
import gettext
import locale

import gcm4_core
import pyAES

# ── dep_install_hint : OS inconnu ──────────────────────────────────────────


def _fake_open_os_release(contenu: str):
    """Remplace open() pour ne servir qu'un /etc/os-release fictif."""
    open_orig = builtins.open

    def _open(file, *args, **kwargs):
        if str(file) == "/etc/os-release":
            import io

            return io.StringIO(contenu)
        return open_orig(file, *args, **kwargs)

    return _open


def test_dep_install_hint_os_inconnu(monkeypatch):
    """OS non reconnu : résumé Debian+Fedora proposé."""
    monkeypatch.setattr(builtins, "open", _fake_open_os_release("NAME=Factux\nVERSION=1.0\n"))
    resultat = gcm4_core.dep_install_hint("pkg-deb", "pkg-dnf")
    assert "sudo apt install pkg-deb" in resultat
    assert "sudo dnf install pkg-dnf" in resultat


def test_dep_install_hint_os_release_illisible(monkeypatch):
    """/etc/os-release illisible : même repli Debian+Fedora."""
    open_orig = builtins.open

    def _open(file, *args, **kwargs):
        if str(file) == "/etc/os-release":
            raise OSError("interdit")
        return open_orig(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    resultat = gcm4_core.dep_install_hint("pkg-deb", "pkg-dnf")
    assert "sudo apt install pkg-deb" in resultat


def test_dep_install_hint_arch(monkeypatch):
    """Arch : pacman avec le paquet dédié, ou repli sur le paquet Fedora."""
    monkeypatch.setattr(builtins, "open", _fake_open_os_release("NAME=Arch Linux\nID=arch\n"))
    assert "pacman -S pkg-arch" in gcm4_core.dep_install_hint("d", "f", "pkg-arch")
    assert "pacman -S pkg-fedora" in gcm4_core.dep_install_hint("d", "pkg-fedora")


def test_dep_install_hint_opensuse(monkeypatch):
    """OpenSUSE : zypper avec le paquet Debian."""
    monkeypatch.setattr(builtins, "open", _fake_open_os_release("NAME=openSUSE\nID=opensuse\n"))
    assert "zypper install pkg-deb" in gcm4_core.dep_install_hint("pkg-deb", "f")


# ── bindtextdomain : replis de traduction ─────────────────────────────────


def test_bindtextdomain_locale_indisponible(monkeypatch):
    """Setlocale en échec même après repli : _() devient l'identité."""

    def setlocale_rate(category, locale_str=None):
        raise locale.Error("locale indisponible")

    monkeypatch.setattr(locale, "setlocale", setlocale_rate)
    gcm4_core.bindtextdomain("gcm4", "/tmp/locale-inexistant")
    assert builtins.__dict__["_"]("bonjour") == "bonjour"  # identité


def test_bindtextdomain_traduction_en_echec(monkeypatch):
    """Catalogue illisible : _() devient l'identité, pas de crash."""

    def setlocale_ok(category, locale_str=None):
        return "C"

    monkeypatch.setattr(locale, "setlocale", setlocale_ok)

    def translation_rate(*args, **kwargs):
        raise OSError("catalogue illisible")

    monkeypatch.setattr(gettext, "translation", translation_rate)
    gcm4_core.bindtextdomain("gcm4", "/tmp/locale-inexistant")
    assert builtins.__dict__["_"]("bonjour") == "bonjour"


# ── Chiffrement : chemins d'échec ─────────────────────────────────────────


def test_encrypt_erreur_aes_rendu_vide(monkeypatch):
    """pyAES.encrypt qui lève : chaîne vide, pas de crash."""

    def encrypt_rate(text, password):
        raise ValueError("AES cassé")

    monkeypatch.setattr(pyAES, "encrypt", encrypt_rate)
    assert gcm4_core.encrypt("secret", "donnee") == ""


def test_decrypt_erreur_aes_rendu_vide(monkeypatch):
    """pyAES.decrypt qui lève : chaîne vide, pas de crash."""

    def decrypt_rate(text, password):
        raise ValueError("AES cassé")

    monkeypatch.setattr(pyAES, "decrypt", decrypt_rate)
    assert gcm4_core.decrypt("secret", "donnee", version=1) == ""


def test_decrypt_ancien_format_invalide():
    """Ancien format sur données incohérentes : chaîne vide, pas de crash."""
    assert gcm4_core.decrypt("secret", "pas-du-tout-base64-valide!!!", version=0) == ""


def test_encrypt_ancien_format(monkeypatch):
    """encrypt_old sur échec de chiffrement : chaîne vide."""

    def xor_rate(*args, **kwargs):
        raise ValueError("xor cassé")

    monkeypatch.setattr(gcm4_core, "xor", xor_rate)
    assert gcm4_core.encrypt_old("secret", "donnee") == ""
