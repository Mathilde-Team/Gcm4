# SPDX-License-Identifier: GPL-3.0-or-later
"""Vérification des mises à jour sans réseau : versions, réponse, accord (#245)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from gcm4.core import mises_a_jour as maj


@pytest.mark.parametrize(
    ("installee", "publiee", "attendu"),
    [
        ("1.3.3", "v1.3.4", True),
        ("1.3.4rc1", "v1.3.4", True),
        ("1.3.4", "v1.3.4-rc1", False),
        ("1.4", "v1.4.0", False),
        ("1.9", "1.10", True),
        ("1.3.4b2", "1.3.4rc1", True),
        ("2.0", "1.99", False),
        ("0+inconnue", "v9.9", False),
        ("1.3.3", "nightly", False),
    ],
)
def test_plus_recente(installee, publiee, attendu):
    """Comparaison numérique (pas de chaînes, contrairement à GTK3), pré-versions avant."""
    assert maj.plus_recente(installee, publiee) is attendu


def _reponse(**champs):
    base = {"tag_name": "v1.4.0", "html_url": "https://github.com/x/releases/tag/v1.4.0"}
    base.update(champs)
    return json.dumps(base)


def test_lire_reponse():
    """Version publiée seulement ; URL https sinon page des versions."""
    assert maj.lire_reponse(_reponse()) == maj.MiseAJour(
        "v1.4.0", "https://github.com/x/releases/tag/v1.4.0"
    )
    assert maj.lire_reponse(_reponse(html_url="javascript:x")).url == maj.PAGE_VERSIONS
    assert maj.lire_reponse(_reponse(prerelease=True)) is None
    assert maj.lire_reponse(_reponse(draft=True)) is None
    assert maj.lire_reponse(_reponse(tag_name=" ")) is None
    assert maj.lire_reponse("[1, 2]") is None
    assert maj.lire_reponse("pas du json") is None


def test_verifier_sans_reseau():
    """Récupération injectée : URL de l'API, plus récente ou non, hors ligne."""
    urls = []

    def faux(url):
        urls.append(url)
        return _reponse()

    assert maj.verifier("1.3.3", faux) == maj.MiseAJour(
        "v1.4.0", "https://github.com/x/releases/tag/v1.4.0"
    )
    assert urls == [maj.URL_DERNIERE_VERSION]
    assert maj.verifier("1.4.0", faux) is None
    assert maj.verifier("1.3.3", lambda _u: None) is None


def test_recuperer(monkeypatch):
    """GET avec en-têtes et délai ; erreur réseau → None, jamais d'exception."""
    vus = {}

    class Reponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def urlopen(requete, timeout):
        vus["accept"] = requete.get_header("Accept")
        vus["agent"] = requete.get_header("User-agent")
        vus["delai"] = timeout
        return Reponse(b'{"tag_name": "v2"}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert maj.recuperer(maj.URL_DERNIERE_VERSION) == '{"tag_name": "v2"}'
    assert vus == {"accept": "application/vnd.github+json", "agent": "gcm4", "delai": maj.DELAI}

    def hors_ligne(*_a, **_k):
        raise urllib.error.URLError("réseau inaccessible")

    monkeypatch.setattr(urllib.request, "urlopen", hors_ligne)
    assert maj.recuperer(maj.URL_DERNIERE_VERSION) is None


def test_lire_accord(tmp_path):
    """True / False / jamais demandé (absent ou illisible)."""
    conf = tmp_path / "gcm4.conf"
    assert maj.lire_accord(str(conf)) is None
    for brut, attendu in (("True", True), ("false", False), ("peut-être", None)):
        conf.write_text(f"[window]\ncheck-updates-consent = {brut}\n", encoding="utf-8")
        assert maj.lire_accord(str(conf)) is attendu


@pytest.mark.parametrize(
    ("option", "accord", "environ", "attendu"),
    [
        (True, True, {}, maj.VERIFIER),
        (True, None, {}, maj.DEMANDER),
        (True, False, {}, maj.NON),
        (False, True, {}, maj.NON),
        (False, None, {}, maj.NON),
        (True, True, {"GCM_NO_UPDATE_CHECK": "1"}, maj.NON),
        (True, None, {"GCM_NO_UPDATE_CHECK": "0"}, maj.DEMANDER),
    ],
)
def test_decision(option, accord, environ, attendu):
    """Jamais de réseau sans accord ; GCM_NO_UPDATE_CHECK désactive tout."""
    assert maj.decision(option, accord, environ) == attendu
