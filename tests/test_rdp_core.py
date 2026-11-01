# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique sans GTK du plugin rdp (``plugins/rdp/core.py``, #105)."""

from __future__ import annotations

import pytest

import plugins.rdp.core as core
from gcm4.core.model import Host


def _hote(options=None, **champs):
    """Hôte ``rdp`` de test, options ``rdp`` données."""
    valeurs = {
        "name": "bureau",
        "host": "10.0.0.7",
        "port": None,
        "protocol": "rdp",
        "user": "mathilde",
        "password": "s3cret",
    }
    valeurs.update(champs)
    hote = Host(**valeurs)
    hote.protocol_options["rdp"] = dict(options or {})
    return hote


@pytest.mark.parametrize(("port_hote", "attendu"), [(None, 3389), (3390, 3390)])
def test_port(port_hote, attendu):
    """3389 par défaut."""
    assert core.port(_hote(port=port_hote)) == attendu


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [("True", True), ("true", True), ("1", True), ("yes", True), ("False", False), ("", False)],
)
def test_booleen(valeur, attendu):
    """Lecture des booléens comme ``_bool_from_str`` GTK3."""
    assert core.booleen(valeur) is attendu


def test_options_defaut_cles_gtk3():
    """Mêmes clés et défauts que ``host_fields`` GTK3 (presse-papier partagé)."""
    defaut = core.options_defaut()
    assert set(defaut) == {
        "rdp_domain",
        "rdp_geometry",
        "rdp_width",
        "rdp_height",
        "rdp_cert_ignore",
        "rdp_dyn_res",
        "rdp_remote_app",
        "rdp_share_clipboard",
        "rdp_share_folder",
        "rdp_shared_folder_path",
    }
    assert defaut["rdp_geometry"] == "fullscreen"
    assert defaut["rdp_share_clipboard"] == "True"
    assert core.valider(defaut) == []


@pytest.mark.parametrize(
    ("options", "attendu"),
    [
        ({"rdp_geometry": "fullscreen"}, (1920, 1080)),
        ({"rdp_geometry": "1024x768"}, (1024, 768)),
        ({"rdp_geometry": "custom", "rdp_width": "1600", "rdp_height": "900"}, (1600, 900)),
        ({}, (1920, 1080)),
    ],
)
def test_taille(options, attendu):
    """Taille demandée à la connexion."""
    assert core.taille(options) == attendu


def test_taille_invalide():
    """Géométrie inconnue ou taille personnalisée vide : ValueError."""
    with pytest.raises(ValueError):
        core.taille({"rdp_geometry": "640x480"})
    with pytest.raises(ValueError):
        core.taille({"rdp_geometry": "custom"})


def test_valider_taille_personnalisee():
    """``custom`` exige largeur et hauteur entre 200 et 8192."""
    erreurs = core.valider({"rdp_geometry": "custom", "rdp_width": "abc", "rdp_height": "100"})
    assert [e.champ for e in erreurs] == ["rdp_width", "rdp_height"]
    assert core.valider({"rdp_geometry": "custom", "rdp_width": "800", "rdp_height": "600"}) == []


def test_valider_geometrie_inconnue():
    """Une géométrie hors liste est signalée."""
    assert [e.champ for e in core.valider({"rdp_geometry": "4k"})] == ["rdp_geometry"]


def test_valider_dossier(tmp_path):
    """Dossier partagé : chemin exigé, existant, pas d'ISO."""
    base = {"rdp_share_folder": "True"}
    assert [e.champ for e in core.valider(base)] == ["rdp_shared_folder_path"]
    iso = {**base, "rdp_shared_folder_path": str(tmp_path / "w.iso")}
    assert [e.champ for e in core.valider(iso)] == ["rdp_shared_folder_path"]
    absent = {**base, "rdp_shared_folder_path": str(tmp_path / "absent")}
    assert [e.champ for e in core.valider(absent)] == ["rdp_shared_folder_path"]
    assert core.valider({**base, "rdp_shared_folder_path": str(tmp_path)}) == []
    # Case décochée : le chemin n'est pas vérifié (GTK3 l'ignorait).
    assert core.valider({"rdp_shared_folder_path": "/nulle/part"}) == []


@pytest.mark.parametrize(
    ("options", "attendu"),
    [
        ({"rdp_geometry": "fullscreen"}, True),
        ({"rdp_geometry": "1024x768"}, False),
        ({"rdp_geometry": "1024x768", "rdp_dyn_res": "True"}, True),
    ],
)
def test_suit_onglet(options, attendu):
    """La résolution suit l'onglet en ``fullscreen`` ou avec ``rdp_dyn_res``."""
    assert core.suit_onglet(options) is attendu


def test_reglages_complets(tmp_path):
    """Options GTK3 → réglages asyncrdp ; le mot de passe n'y est pas."""
    hote = _hote(
        {
            "rdp_domain": " CORP ",
            "rdp_geometry": "1280x1024",
            "rdp_cert_ignore": "True",
            "rdp_share_clipboard": "False",
            "rdp_share_folder": "True",
            "rdp_shared_folder_path": str(tmp_path),
            "rdp_remote_app": "||notepad",
        },
        port=3390,
    )
    r = core.reglages(hote)
    assert (r.hote, r.port, r.utilisateur, r.domaine) == ("10.0.0.7", 3390, "mathilde", "CORP")
    assert r.options == {
        "width": 1280,
        "height": 1024,
        "ignore_certificate": True,
        "redirect_clipboard": False,
        "redirect_drives": False,
    }
    assert r.dossier == ("gcm4", str(tmp_path))
    assert r.remote_app == "||notepad"
    assert r.suit_onglet is False
    assert "s3cret" not in repr(r)


def test_reglages_defaut():
    """Hôte sans options : plein écran, presse-papier, pas de domaine."""
    r = core.reglages(_hote())
    assert r.domaine is None
    assert r.dossier is None and r.remote_app is None
    assert r.options["redirect_clipboard"] is True
    assert r.options["ignore_certificate"] is False
    assert r.suit_onglet is True


def test_reglages_invalides():
    """Options invalides : ValueError avec le message de validation."""
    with pytest.raises(ValueError):
        core.reglages(_hote({"rdp_geometry": "custom"}))


def test_combinaisons():
    """Menu « Touches spéciales » (#71) : identifiants uniques, touches AsyncRdp."""
    identifiants = [c[0] for c in core.COMBINAISONS]
    assert len(set(identifiants)) == len(identifiants) == 5
    assert core.touches("ctrl-alt-suppr") == ("ctrl", "alt", "delete")
    assert core.touches("impr-ecran") == ("print",)
    with pytest.raises(KeyError):
        core.touches("inconnue")
