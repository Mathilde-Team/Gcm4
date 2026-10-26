"""Mot de passe maître et code de recouvrement dans ChiffrementGcm (#98)."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from loguru import logger

import master_password_core as mpc
from gcm4.core import chemins
from gcm4.core import config as config_mod
from gcm4.core.chiffrement import (
    ChiffrementError,
    ChiffrementGcm,
    CleVerrouillee,
    CodeRecouvrementIncorrect,
    MotDePasseMaitreIncorrect,
)
from gcm4.core.config import load_config, save_config
from gcm4.core.model import Host

DATA = Path(__file__).resolve().parent / "data" / "config"
U = "utilisateur"
CLAIRS = {
    "PROD/eu/api": "Mot2Passe!é",
    "LAB/web": "",
    "LAB/bases/db": "s3cr3t-très-long-pour-plusieurs-blocs-AES-0123456789",
}


@pytest.fixture
def dossier(tmp_path, monkeypatch):
    """Fixture GTK3 AES copiée puis migrée (gcm4.conf/.gcm4.key)."""
    monkeypatch.setattr(config_mod, "_sauvegardes", set())
    cible = tmp_path / "gcm"
    shutil.copytree(DATA / "aes", cible)
    chemins.preparer(str(cible))
    return cible


def _charger(dossier, chiffrement=None):
    """Configuration relue avec ``chiffrement`` (nouveau par défaut)."""
    chiffrement = chiffrement or ChiffrementGcm(str(dossier), U)
    return load_config(str(dossier / "gcm4.conf"), chiffrement)


def _mots_de_passe(cfg):
    """Chemin d'hôte -> mot de passe en clair."""
    return {h.path: h.password for h in cfg.store.hosts}


def _activer(dossier, mot_de_passe="maître"):
    """Active la protection ; renvoie (contenu brut d'avant, code)."""
    cle = (dossier / ".gcm4.key").read_text()
    code = ChiffrementGcm(str(dossier), U).activer_protection(mot_de_passe)
    return cle, code


def test_sans_mot_de_passe_maitre_rien_ne_change(dossier):
    """Option jamais forcée : clé en clair, ni protégée ni verrouillée."""
    chiffrement = ChiffrementGcm(str(dossier), U)
    assert not chiffrement.est_protegee()
    assert not chiffrement.est_verrouillee()
    assert not chiffrement.a_code_recouvrement()
    assert _mots_de_passe(_charger(dossier, chiffrement)) == CLAIRS


def test_activation_protege_la_cle_et_rend_un_code(dossier):
    """Activation : fichier v2 en 0600, sans la clé ni le code en clair."""
    cle, code = _activer(dossier)
    contenu = (dossier / ".gcm4.key").read_text()
    assert contenu.startswith(mpc.MASTER_PASSWORD_MAGIC_V2)
    assert cle not in contenu and code not in contenu
    assert stat.S_IMODE(os.stat(dossier / ".gcm4.key").st_mode) == 0o600
    assert not (dossier / ".gcm4.key.tmp").exists()
    assert mpc.unlock_with_recovery_code(contenu, code) == cle


def test_cle_verrouillee_refuse_lecture_et_ecriture(dossier):
    """Verrouillée : chargement refusé, et la clé n'est jamais recréée par-dessus."""
    _activer(dossier)
    contenu = (dossier / ".gcm4.key").read_text()
    chiffrement = ChiffrementGcm(str(dossier), U)
    assert chiffrement.est_protegee() and chiffrement.est_verrouillee()
    assert chiffrement.a_code_recouvrement()
    with pytest.raises(CleVerrouillee, match="master password"):
        _charger(dossier, chiffrement)
    with pytest.raises(CleVerrouillee):
        chiffrement.chiffrer("x")
    assert (dossier / ".gcm4.key").read_text() == contenu


def test_deverrouiller_puis_lire_et_ecrire(dossier):
    """Bon mot de passe : mots de passe relus, réécriture sans perte, clé toujours protégée."""
    _activer(dossier)
    chiffrement = ChiffrementGcm(str(dossier), U)
    with pytest.raises(MotDePasseMaitreIncorrect, match="Incorrect master password"):
        chiffrement.deverrouiller("faux")
    assert chiffrement.est_verrouillee()
    chiffrement.deverrouiller("maître")
    assert not chiffrement.est_verrouillee()
    cfg = _charger(dossier, chiffrement)
    assert _mots_de_passe(cfg) == CLAIRS
    cfg.store.add(Host(name="neuf", host="h", group="LAB", password="pw-neuf"))
    save_config(str(dossier / "gcm4.conf"), cfg, chiffrement)
    assert (dossier / ".gcm4.key").read_text().startswith(mpc.MASTER_PASSWORD_MAGIC_V2)
    relu = ChiffrementGcm(str(dossier), U)
    relu.deverrouiller("maître")
    assert _mots_de_passe(_charger(dossier, relu))["LAB/neuf"] == "pw-neuf"


def test_recuperer_impose_nouveau_mot_de_passe_et_code(dossier):
    """Mot de passe oublié : code faux refusé ; bon code -> nouveau mot de passe et nouveau code."""
    _cle, code = _activer(dossier, "oublié")
    chiffrement = ChiffrementGcm(str(dossier), U)
    with pytest.raises(CodeRecouvrementIncorrect, match="Incorrect recovery code"):
        chiffrement.recuperer("AAAA-AAAA", "neuf")
    with pytest.raises(ChiffrementError, match="must not be empty"):
        chiffrement.recuperer(code, "")
    nouveau_code = chiffrement.recuperer(code.lower(), "neuf")
    assert nouveau_code != code
    assert _mots_de_passe(_charger(dossier, chiffrement)) == CLAIRS
    autre = ChiffrementGcm(str(dossier), U)
    with pytest.raises(MotDePasseMaitreIncorrect):
        autre.deverrouiller("oublié")
    with pytest.raises(CodeRecouvrementIncorrect):
        autre.recuperer(code, "x")
    autre.deverrouiller("neuf")
    assert ChiffrementGcm(str(dossier), U).recuperer(nouveau_code, "encore") != nouveau_code


def test_changer_mot_de_passe_garde_le_code(dossier):
    """Changement : ancien refusé, nouveau accepté, code toujours valide."""
    _cle, code = _activer(dossier, "ancien")
    chiffrement = ChiffrementGcm(str(dossier), U)
    with pytest.raises(MotDePasseMaitreIncorrect):
        chiffrement.changer_mot_de_passe("faux", "nouveau")
    with pytest.raises(ChiffrementError, match="must not be empty"):
        chiffrement.changer_mot_de_passe("ancien", "")
    chiffrement.changer_mot_de_passe("ancien", "nouveau")
    contenu = (dossier / ".gcm4.key").read_text()
    with pytest.raises(mpc.InvalidMasterPasswordError):
        mpc.unlock_key_file_content(contenu, "ancien")
    assert mpc.unlock_key_file_content(contenu, "nouveau") == mpc.unlock_with_recovery_code(
        contenu, code
    )


def test_regenerer_code(dossier):
    """Régénération : ancien code invalide, nouveau valide, mot de passe faux refusé."""
    _cle, code = _activer(dossier)
    chiffrement = ChiffrementGcm(str(dossier), U)
    with pytest.raises(MotDePasseMaitreIncorrect):
        chiffrement.regenerer_code("faux")
    nouveau = chiffrement.regenerer_code("maître")
    contenu = (dossier / ".gcm4.key").read_text()
    with pytest.raises(mpc.InvalidRecoveryCodeError):
        mpc.unlock_with_recovery_code(contenu, code)
    assert mpc.unlock_with_recovery_code(contenu, nouveau)


def test_desactiver_protection(dossier):
    """Désactivation : la clé d'origine revient en clair, lecture sans mot de passe."""
    cle, _code = _activer(dossier)
    chiffrement = ChiffrementGcm(str(dossier), U)
    with pytest.raises(MotDePasseMaitreIncorrect):
        chiffrement.desactiver_protection("faux")
    chiffrement.desactiver_protection("maître")
    assert (dossier / ".gcm4.key").read_text() == cle
    assert not chiffrement.est_verrouillee()
    assert _mots_de_passe(_charger(dossier)) == CLAIRS


def test_activation_sans_cle_la_cree(tmp_path):
    """Pas encore de .gcm4.key : elle est créée puis protégée."""
    chiffrement = ChiffrementGcm(str(tmp_path), U)
    code = chiffrement.activer_protection("pw")
    contenu = (tmp_path / chemins.FICHIER_CLE).read_text()
    assert mpc.unlock_key_file_content(contenu, "pw") == mpc.unlock_with_recovery_code(
        contenu, code
    )
    assert chiffrement.dechiffrer(chiffrement.chiffrer("secret")) == "secret"


def test_activation_refusee(dossier):
    """Déjà protégée, ou mot de passe vide : refus, clé intacte."""
    cle = (dossier / ".gcm4.key").read_text()
    with pytest.raises(ChiffrementError, match="must not be empty"):
        ChiffrementGcm(str(dossier), U).activer_protection("")
    assert (dossier / ".gcm4.key").read_text() == cle
    _activer(dossier)
    with pytest.raises(ChiffrementError, match="already protected"):
        ChiffrementGcm(str(dossier), U).activer_protection("autre")


def test_operations_sur_cle_non_protegee(dossier, tmp_path):
    """Déverrouiller, changer, régénérer, désactiver, récupérer : refusés sans protection."""
    chiffrement = ChiffrementGcm(str(dossier), U)
    for appel in (
        lambda: chiffrement.deverrouiller("x"),
        lambda: chiffrement.changer_mot_de_passe("x", "y"),
        lambda: chiffrement.regenerer_code("x"),
        lambda: chiffrement.desactiver_protection("x"),
        lambda: chiffrement.recuperer("x", "y"),
        lambda: ChiffrementGcm(str(tmp_path / "vide"), U).deverrouiller("x"),
    ):
        with pytest.raises(ChiffrementError, match="not protected"):
            appel()


def test_format_v1_et_cle_corrompue(dossier):
    """v1 (sans code) : déverrouillable, récupération refusée ; contenu corrompu signalé."""
    cle = (dossier / ".gcm4.key").read_text()
    (dossier / ".gcm4.key").write_text(mpc.protect_key_file_content(cle, "pw"))
    chiffrement = ChiffrementGcm(str(dossier), U)
    assert chiffrement.est_protegee() and not chiffrement.a_code_recouvrement()
    with pytest.raises(ChiffrementError, match="no recovery code"):
        chiffrement.recuperer("ABCD", "neuf")
    chiffrement.deverrouiller("pw")
    assert _mots_de_passe(_charger(dossier, chiffrement)) == CLAIRS
    (dossier / ".gcm4.key").write_text(f"{mpc.MASTER_PASSWORD_MAGIC_V2}a:b")
    with pytest.raises(ChiffrementError, match="corrupted"):
        ChiffrementGcm(str(dossier), U).deverrouiller("pw")


def test_ecriture_impossible_laisse_la_cle_intacte(dossier, monkeypatch):
    """os.replace en échec : ChiffrementError, ancienne clé intacte."""
    cle = (dossier / ".gcm4.key").read_text()
    (dossier / ".gcm4.key.tmp").write_text("reste d'un essai précédent")

    def echec(*_args):
        raise OSError("disque plein")

    monkeypatch.setattr(os, "replace", echec)
    with pytest.raises(ChiffrementError, match="Unable to write"):
        ChiffrementGcm(str(dossier), U).activer_protection("pw")
    assert (dossier / ".gcm4.key").read_text() == cle


def test_aucun_secret_journalise(dossier):
    """Ni mot de passe maître, ni code, ni clé dans les journaux, même en TRACE."""
    lignes: list[str] = []
    puits = logger.add(lignes.append, level="TRACE", format="{message}")
    try:
        cle, code = _activer(dossier, "Ma1tre-Secret")
        chiffrement = ChiffrementGcm(str(dossier), U)
        chiffrement.deverrouiller("Ma1tre-Secret")
        nouveau = chiffrement.recuperer(code, "N0uveau-Secret")
        chiffrement.regenerer_code("N0uveau-Secret")
    finally:
        logger.remove(puits)
    journal = "".join(lignes)
    for secret in (cle, code, nouveau, "Ma1tre-Secret", "N0uveau-Secret"):
        assert secret not in journal
