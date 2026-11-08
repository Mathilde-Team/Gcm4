# SPDX-License-Identifier: GPL-3.0-or-later
"""Replis de traduction et entrées vides du cœur, sans catalogue externe (#293)."""

import builtins
import gettext
import locale
from types import SimpleNamespace

import pytest

from gcm4.core import metier


@pytest.mark.parametrize("langue", ["", ".UTF-8:fr_FR:fr_FR", "en_US:en_US"])
def test_traduction_candidates_et_replis(monkeypatch, langue):
    """Les variantes dupliquées/vide sont filtrées et les replis anglais conservés."""
    monkeypatch.setitem(builtins.__dict__, "_", lambda texte: texte)
    monkeypatch.setenv("LANGUAGE", langue)
    for cle in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(cle, raising=False)
    monkeypatch.setattr(locale, "setlocale", lambda *_a: "C")
    monkeypatch.setattr(locale, "bindtextdomain", lambda *_a: None)
    monkeypatch.setattr(gettext, "bindtextdomain", lambda *_a: None)
    monkeypatch.setattr(gettext, "textdomain", lambda *_a: None)
    monkeypatch.setattr(metier, "_gcm_app_name", metier._gcm_app_name)
    langues = []

    def traduire(_domaine, **options):
        """Enregistre les langues sans lire le disque ni installer de catalogue."""
        langues.extend(options["languages"])
        return SimpleNamespace(gettext=lambda texte: f"traduit:{texte}")

    monkeypatch.setattr(gettext, "translation", traduire)
    metier.bindtextdomain("test", "/catalogues-inexistants")
    assert builtins._("texte") == "traduit:texte"
    assert len(langues) == len(set(langues))
    assert "en_US" in langues and "en" in langues
    assert "" not in langues
    if "fr_FR" in langue:
        assert langues[:3] == [".UTF-8", "fr_FR", "fr"]


def test_entrees_vides_ignorees_sans_hote_invente():
    """Spécifications vides et couleurs sans paire complète sont ignorées."""
    assert metier.resolve_host_specs(["", "groupe/", "/hote"], {}) == []
    assert metier.parse_group_colors("=rouge,groupe=,valide=#abcdef") == {"valide": "#abcdef"}
