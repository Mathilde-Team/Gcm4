# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique sans GTK du plugin web (``plugins/web/core.py``, #179, #397)."""

from __future__ import annotations

import pytest

import plugins.web.core as core


def test_options_defaut_comme_gtk3():
    """URL vide, certificats non vérifiés acceptés, écrits ``True`` comme GTK3."""
    assert core.options_defaut() == {"web_url": "", "web_ignore_cert": "True"}


@pytest.mark.parametrize("valeur, attendu", [(True, "True"), (False, "False")])
def test_ecrire_booleen(valeur, attendu):
    """``str(bool)`` de GTK3."""
    assert core.ecrire_booleen(valeur) == attendu


@pytest.mark.parametrize(
    "brut, attendu",
    [
        ("True", True),
        ("False", False),
        (" no ", False),
        ("0", False),
        ("off", False),
        ("1", True),
        ("yes", True),
        ("", True),  # absente : défaut GTK3
        ("peut-être", True),  # illisible : défaut GTK3
    ],
)
def test_ignorer_certificat(brut, attendu):
    """Relu comme ``configparser`` ; absent ou illisible : vrai (GTK3)."""
    assert core.ignorer_certificat({"web_ignore_cert": brut}) is attendu


def test_ignorer_certificat_option_absente():
    """Configuration GTK3 sans la clé : vrai."""
    assert core.ignorer_certificat({}) is True


@pytest.mark.parametrize(
    "brut, attendu",
    [
        ("", ""),
        ("   ", ""),
        (" https://ilo.example.org/ ", "https://ilo.example.org/"),
        ("http://10.0.0.5:8080/ui", "http://10.0.0.5:8080/ui"),
        ("ilo.example.org", "https://ilo.example.org"),  # schéma ajouté
        ("ilo:8443/console", "https://ilo:8443/console"),  # « hôte:port » n'est pas un schéma
        ("javascript:alert(1)", "javascript:alert(1)"),  # schéma explicite : gardé, puis refusé
        ("FILE:/etc/passwd", "FILE:/etc/passwd"),
    ],
)
def test_url(brut, attendu):
    """Espaces retirés, ``https://`` ajouté s'il n'y a pas de schéma."""
    assert core.url({"web_url": brut}) == attendu


def test_url_option_absente():
    """Clé absente : chaîne vide."""
    assert core.url({}) == ""


@pytest.mark.parametrize(
    "adresse, attendu",
    [
        ("HTTPS://BMC.example.org:443/x", ("https", "bmc.example.org")),
        ("http://[::1]:8080/", ("http", "::1")),
        ("http://[::1", ("", "")),  # IPv6 mal fermée : illisible
        ("https://", ("https", "")),
    ],
)
def test_decouper(adresse, attendu):
    """Schéma en minuscules et nom d'hôte ; ``("", "")`` si ``urlsplit`` échoue."""
    assert core.decouper(adresse) == attendu


def test_valider_url_absente():
    """URL obligatoire (message de GTK3)."""
    erreurs = core.valider({})
    assert [(e.champ, e.message) for e in erreurs] == [("web_url", "URL is required.")]


@pytest.mark.parametrize(
    "brut",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ftp://depot",
        "https://",
        "http://[::1",
        "mailto:a@b",
    ],
)
def test_valider_refuse_hors_http(brut):
    """Seuls ``http`` et ``https`` avec un nom d'hôte sont acceptés."""
    erreurs = core.valider({"web_url": brut})
    assert [e.champ for e in erreurs] == ["web_url"]
    assert erreurs[0].message == "The URL must start with http:// or https://"


@pytest.mark.parametrize(
    "brut", ["https://ilo/", "HTTP://10.0.0.5", "idrac.example.org", "ilo:8443"]
)
def test_valider_accepte(brut):
    """URL ``http``/``https`` (schéma ajouté au besoin) : aucune erreur."""
    assert core.valider({"web_url": brut}) == []
