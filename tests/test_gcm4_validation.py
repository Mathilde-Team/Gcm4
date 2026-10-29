# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation des champs communs d'un hôte (#144)."""

from __future__ import annotations

import pytest

from gcm4.core.model import Host, HostStore
from gcm4.core.validation import (
    ADRESSE,
    NOM,
    PORT,
    FieldError,
    erreur_de_port,
    lire_port,
    validate_host,
)


@pytest.fixture
def store():
    """LAB/web et LAB/Web (casse différente), racine/local."""
    s = HostStore()
    for nom, groupe in (("web", "LAB"), ("Web", "LAB"), ("local", "")):
        s.add(Host(nom, group=groupe, host="10.0.0.1", protocol="p"))
    return s


def _champs(erreurs):
    return [e.champ for e in erreurs]


def test_hote_valide(store):
    """Nouvel hôte complet, port vide (défaut du protocole) ou dans les bornes."""
    assert validate_host(Host("api", group="LAB", host="h"), store) == []
    for port in (1, 22, 65535):
        assert validate_host(Host("api", group="LAB", host="h", port=port), store) == []


@pytest.mark.parametrize("nom", ["", "   "])
def test_nom_obligatoire(store, nom):
    """Nom vide ou blanc : erreur sur le nom."""
    assert _champs(validate_host(Host(nom, host="h"), store)) == [NOM]


def test_nom_sans_separateur(store):
    """« / » sépare les groupes : interdit dans un nom d'hôte."""
    erreurs = validate_host(Host("a/b", host="h"), store)
    assert _champs(erreurs) == [NOM] and "/" in erreurs[0].message


def test_nom_unique_dans_le_groupe(store):
    """Doublon exact refusé (espaces ignorés) ; même nom ailleurs ou casse différente acceptés."""
    erreurs = validate_host(Host(" web ", group="LAB", host="h"), store)
    assert _champs(erreurs) == [NOM] and "web" in erreurs[0].message
    assert validate_host(Host("web", group="PROD", host="h"), store) == []
    assert validate_host(Host("WEB", group="LAB", host="h"), store) == []
    assert _champs(validate_host(Host("local", group=" ", host="h"), store)) == [NOM]


def test_modification_ne_se_compte_pas_comme_doublon(store):
    """Mode modification : l'original garde son nom ; prendre le nom d'un autre reste refusé."""
    original = store.find("LAB/web")
    copie = original.clone()
    assert validate_host(copie, store, original=original) == []
    copie.name = "Web"
    assert _champs(validate_host(copie, store, original=original)) == [NOM]


def test_adresse(store):
    """Adresse vide refusée, sauf protocole sans adresse (indiqué par le plugin)."""
    assert _champs(validate_host(Host("api", host="  "), store)) == [ADRESSE]
    assert validate_host(Host("api", host=""), store, adresse_requise=False) == []


@pytest.mark.parametrize("port", [0, -1, 65536, 100000])
def test_port_hors_bornes(store, port):
    """Port hors 1–65535 : erreur sur le port."""
    erreurs = validate_host(Host("api", host="h", port=port), store)
    assert _champs(erreurs) == [PORT] and "65535" in erreurs[0].message


def test_plusieurs_erreurs(store):
    """Toutes les erreurs sont rendues, dans l'ordre des champs."""
    assert _champs(validate_host(Host("", host="", port=0), store)) == [NOM, ADRESSE, PORT]


def test_lire_port():
    """Vide → None ; chiffres → entier ; le reste → ValueError."""
    assert lire_port("") is None and lire_port("  ") is None
    assert lire_port(" 22 ") == 22
    for texte in ("22a", "-1", "2.5", "٢٢", "+22"):
        with pytest.raises(ValueError):
            lire_port(texte)


@pytest.mark.parametrize(
    ("texte", "erreur"),
    [("", False), ("22", False), ("65535", False), ("0", True), ("65536", True), ("ssh", True)],
)
def test_erreur_de_port(texte, erreur):
    """Saisie du port : vide ou 1–65535 valides ; le reste en erreur sur le port."""
    resultat = erreur_de_port(texte)
    assert (resultat is not None) == erreur
    if erreur:
        assert isinstance(resultat, FieldError) and resultat.champ == PORT
