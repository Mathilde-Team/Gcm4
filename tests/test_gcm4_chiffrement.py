"""Mots de passe chiffrés de gcm4.conf, compatibles GTK3 (#138)."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from loguru import logger

import gcm4_core
from gcm4.core import chemins
from gcm4.core import config as config_mod
from gcm4.core.chiffrement import ChiffrementError, ChiffrementGcm, dechiffrer_aes_strict
from gcm4.core.config import ConfigError, load_config, save_config

DATA = Path(__file__).resolve().parent / "data" / "config"
U = "utilisateur"
CLAIRS = {
    "PROD/eu/api": "Mot2Passe!é",
    "LAB/web": "",
    "LAB/bases/db": "s3cr3t-très-long-pour-plusieurs-blocs-AES-0123456789",
}


@pytest.fixture
def dossier(tmp_path, monkeypatch, request):
    """Fixture GTK3 (``aes`` par défaut) copiée dans tmp_path puis migrée (#99) ; .bak remis à zéro."""
    monkeypatch.setattr(config_mod, "_sauvegardes", set())
    source = DATA / getattr(request, "param", "aes")
    cible = tmp_path / "gcm"
    shutil.copytree(source, cible)
    chemins.preparer(str(cible))  # gcm.conf/.gcm.key (GTK3) -> gcm4.conf/.gcm4.key
    return cible


def _mots_de_passe(cfg):
    return {h.path: h.password for h in cfg.store.hosts}


def test_fixture_aes_relue(dossier):
    """La fixture AES se déchiffre avec sa .gcm4.key, comme dans l'application GTK3."""
    cfg = load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier), U))
    assert _mots_de_passe(cfg) == CLAIRS
    assert cfg.store.groups["PROD/eu"].color == "#00aaff"
    assert cfg.store.find("LAB/web").protocol_options["proto-b"]["resolution"] == "1280x800"


def test_fixture_aes_reecrite_sans_perte(dossier):
    """Relue puis réécrite : mots de passe compris, rien n'est perdu ; version = 1 ; même clé."""
    conf = dossier / "gcm4.conf"
    cle_avant = (dossier / ".gcm4.key").read_text()
    save_config(
        str(conf),
        load_config(str(conf), ChiffrementGcm(str(dossier), U)),
        ChiffrementGcm(str(dossier), U),
    )
    cfg = load_config(str(conf), ChiffrementGcm(str(dossier), U))
    assert _mots_de_passe(cfg) == CLAIRS
    assert cfg.brut.get("options", "version") == "1"
    assert cfg.brut.get("plugin inconnu", "cle") == "valeur"
    assert (
        cfg.store.find("PROD/eu/api").protocol_options["proto-a"]["tunnel"] == "L8080:localhost:80"
    )
    assert (dossier / ".gcm4.key").read_text() == cle_avant


def test_compatible_avec_gcm4_core(dossier):
    """Ce qu'écrit gcm4 se relit avec gcm4_core.decrypt (application GTK3)."""
    conf = dossier / "gcm4.conf"
    save_config(
        str(conf),
        load_config(str(conf), ChiffrementGcm(str(dossier), U)),
        ChiffrementGcm(str(dossier), U),
    )
    cle = (dossier / ".gcm4.key").read_text()
    cp = load_config(str(conf)).store  # SansChiffrement : valeurs stockées
    api = cp.find("PROD/eu/api")
    assert gcm4_core.decrypt(U + cle, api.password, version=1) == "Mot2Passe!é"


@pytest.mark.parametrize("dossier", ["ancien"], indirect=True)
def test_ancien_format_lu_puis_ecrit_en_aes(dossier):
    """Ancien XOR (sans version, sans clé) : lu, puis réécrit en AES avec une .gcm4.key 0600 créée."""
    conf = dossier / "gcm4.conf"
    assert not (dossier / ".gcm4.key").exists()
    cfg = load_config(str(conf), ChiffrementGcm(str(dossier), U))
    assert _mots_de_passe(cfg) == CLAIRS
    save_config(str(conf), cfg, ChiffrementGcm(str(dossier), U))
    cle = dossier / ".gcm4.key"
    assert cle.is_file() and stat.S_IMODE(os.stat(cle).st_mode) == 0o600
    relu = load_config(str(conf), ChiffrementGcm(str(dossier), U))
    assert relu.brut.get("options", "version") == "1" and _mots_de_passe(relu) == CLAIRS


def test_cle_absente_erreur_explicite_sans_perte(dossier):
    """.gcm4.key absente avec des mots de passe AES : ChiffrementError, fichier intact, aucune clé créée."""
    (dossier / ".gcm4.key").unlink()
    avant = (dossier / "gcm4.conf").read_bytes()
    with pytest.raises(ChiffrementError, match="missing"):
        load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier), U))
    assert (dossier / "gcm4.conf").read_bytes() == avant and not (dossier / ".gcm4.key").exists()


def test_mauvaise_cle_erreur_au_lieu_d_effacer(dossier):
    """Clé remplacée : GTK3 renverrait "" ; ici ChiffrementError (aucun mot de passe écrasé)."""
    (dossier / ".gcm4.key").write_text("autre-cle")
    with pytest.raises(ChiffrementError):
        load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier), U))
    assert issubclass(ChiffrementError, ConfigError)


def test_mauvais_utilisateur_erreur(dossier):
    """Le secret inclut le nom d'utilisateur : un autre compte ne déchiffre pas."""
    with pytest.raises(ChiffrementError):
        load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier), "autre"))


def test_cle_illisible(dossier):
    """.gcm4.key remplacée par un dossier : ChiffrementError."""
    (dossier / ".gcm4.key").unlink()
    (dossier / ".gcm4.key").mkdir()
    with pytest.raises(ChiffrementError, match="Unable to read the encryption key"):
        load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier), U))


def test_creation_de_cle_impossible(tmp_path, monkeypatch):
    """Écriture d'une nouvelle clé impossible : ChiffrementError, gcm4.conf non créé."""
    monkeypatch.setattr(config_mod, "_sauvegardes", set())
    ch = ChiffrementGcm(str(tmp_path / "x"), U)
    cfg = load_config(str(tmp_path / "x" / "gcm4.conf"), ch)
    from gcm4.core.model import Host

    cfg.store.add(Host("h", password="p"))

    def refus(*_a, **_k):
        raise PermissionError(13, "refusé")

    monkeypatch.setattr("gcm4.core.chiffrement.os.open", refus)
    with pytest.raises(ChiffrementError, match="Unable to create the encryption key"):
        save_config(str(tmp_path / "x" / "gcm4.conf"), cfg, ch)
    assert not (tmp_path / "x" / "gcm4.conf").exists()


def test_cle_creee_par_ailleurs_reutilisee(tmp_path):
    """Si .gcm4.key apparaît entre lecture et écriture, elle est lue, jamais écrasée."""
    ch = ChiffrementGcm(str(tmp_path), U)
    load_config(str(tmp_path / "gcm4.conf"), ch)
    (tmp_path / ".gcm4.key").write_text("cle-existante")
    chiffre = ch.chiffrer("p")
    assert (tmp_path / ".gcm4.key").read_text() == "cle-existante"
    assert dechiffrer_aes_strict(chiffre, U + "cle-existante") == "p"


@pytest.mark.parametrize("valeur", ["pas du base64 !", "QUJD", "A" * 44])
def test_valeur_corrompue(valeur):
    """Base64 invalide, trop court ou bourrage faux : ChiffrementError."""
    with pytest.raises(ChiffrementError):
        dechiffrer_aes_strict(valeur, "s")


def test_utf8_invalide(monkeypatch):
    """Bourrage correct mais octets non UTF-8 : ChiffrementError."""
    import pyAES

    chiffre = pyAES.encrypt(b"\xff\xfe", "s")
    with pytest.raises(ChiffrementError):
        dechiffrer_aes_strict(chiffre, "s")


def test_aller_retour_strict_toutes_longueurs():
    """dechiffrer_aes_strict inverse pyAES.encrypt pour 0 à 40 octets (frontières de blocs)."""
    import pyAES

    for n in range(41):
        texte = "é" * (n // 2) + "x" * (n % 2)
        assert dechiffrer_aes_strict(pyAES.encrypt(texte, "secret"), "secret") == texte


def test_utilisateur_par_defaut(monkeypatch, tmp_path):
    """Sans utilisateur explicite : gcm4_core.get_username (USER)."""
    monkeypatch.setenv("USER", "quelquun")
    ch = ChiffrementGcm(str(tmp_path))
    assert ch._secret.startswith("quelquun")


def test_aucun_secret_dans_le_journal(dossier):
    """Au niveau TRACE, ni mot de passe, ni valeur chiffrée, ni clé dans le journal (#117)."""
    lignes: list[str] = []
    ident = logger.add(lignes.append, level="TRACE", format="{message}")
    logger.enable("gcm4")
    try:
        conf = dossier / "gcm4.conf"
        cfg = load_config(str(conf), ChiffrementGcm(str(dossier), U))
        save_config(str(conf), cfg, ChiffrementGcm(str(dossier), U))
    finally:
        logger.remove(ident)
        logger.disable("gcm4")
    journal = "".join(lignes)
    assert journal
    cle = (dossier / ".gcm4.key").read_text()
    chiffres = [h.password for h in load_config(str(conf)).store.hosts if h.password]
    for secret in [*filter(None, CLAIRS.values()), cle, *chiffres]:
        assert secret not in journal


def test_flux_aes_de_mauvaise_longueur_refuse(monkeypatch):
    """Bloc et flux AES de longueurs différentes : ``ValueError``, pas de XOR tronqué.

    Remplace le contrôle de ``zip(strict=True)``, absent de Python 3.9 (#223).
    """
    import pyAES

    stocke = gcm4_core.encrypt("cle-de-test", "mot de passe")
    monkeypatch.setattr(pyAES, "aesEncrypt", lambda flux, cle: list(range(15)))
    with pytest.raises(ValueError, match="bloc de 16 octets, flux de 15"):
        dechiffrer_aes_strict(stocke, "cle-de-test")
