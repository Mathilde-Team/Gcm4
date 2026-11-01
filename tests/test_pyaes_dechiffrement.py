# SPDX-License-Identifier: GPL-3.0-or-later
"""Chemin de déchiffrement de pyAES.py (primitives inverses).

L'application ne chiffre qu'avec pyAES (mots de passe de gcm.conf) ; le
chemin inverse (aesDecrypt et ses primitives *_Inv) n'était jamais exécuté
par les tests. Ces tests le verrouillent par aller-retour et par les
propriétés d'inversibilité des primitives.

Remarque : le pyAES vendé ne reproduit pas le vecteur FIPS-197 C.3
(aesEncrypt rend un autre bloc que l'AES-256 normalisé) — écart préexistant
sur dev, hors périmètre de la journalisation ; l'invariant garanti ici est
la cohérence interne (déchiffrer ce qu'on a chiffré).
"""

from __future__ import annotations

import pyAES

CLE = list(range(32))  # 000102...1f (32 octets, AES-256)


def test_chiffrement_deterministe():
    """Deux chiffrements du même bloc avec la même clé : identiques."""
    clair = list(range(16))
    assert pyAES.aesEncrypt(clair, CLE) == pyAES.aesEncrypt(clair, CLE)


def test_aller_retour_blocs():
    """Chiffrement puis déchiffrement : identité sur plusieurs blocs."""
    blocs = [
        list(b"voici-un-bloc!xy")[:16],
        [0x00] * 16,
        list(range(16)),
    ]
    for clair in blocs:
        chiffre = pyAES.aesEncrypt(clair, CLE)
        assert pyAES.aesDecrypt(chiffre, CLE) == clair


# ── Primitives inverses ──────────────────────────────────────────────────


def test_shiftrows_est_inverse_de_shiftrowsinv():
    """ShiftRows suivi de shiftRowsInv redonne l'état initial."""
    etat = list(range(16))
    direct = etat[:]
    pyAES.shiftRows(direct)
    inverse = direct[:]
    pyAES.shiftRowsInv(inverse)
    assert inverse == etat


def test_subbytes_est_inverse_de_subbytesinv():
    """SubBytes suivi de subBytesInv redonne l'état initial."""
    etat = list(range(16))
    direct = etat[:]
    pyAES.subBytes(direct)
    inverse = direct[:]
    pyAES.subBytesInv(inverse)
    assert inverse == etat


def test_mixcolumn_est_inverse_de_mixcolumninv():
    """MixColumn suivi de mixColumnInv redonne la colonne initiale."""
    colonne = [0x01, 0x02, 0x03, 0x04]
    direct = colonne[:]
    pyAES.mixColumn(direct)
    inverse = direct[:]
    pyAES.mixColumnInv(inverse)
    assert inverse == colonne


def test_mixcolumns_est_inverse_de_mixcolumnsinv():
    """MixColumns suivi de mixColumnsInv redonne l'état initial."""
    etat = list(range(16))
    direct = etat[:]
    pyAES.mixColumns(direct)
    inverse = direct[:]
    pyAES.mixColumnsInv(inverse)
    assert inverse == etat


def test_aesround_est_inverse_de_aesroundinv():
    """AesRound suivi de aesRoundInv redonne l'état initial."""
    cle_etendue = pyAES.expandKey(CLE)
    etat = list(range(16))
    direct = etat[:]
    pyAES.aesRound(direct, cle_etendue)
    inverse = direct[:]
    pyAES.aesRoundInv(inverse, cle_etendue)
    assert inverse == etat


def test_galois_mult_identite_et_double():
    """GaloisMult : x*1 = x et x*2 = x<<1 (mod 0x100)."""
    assert pyAES.galoisMult(0x57, 1) == 0x57
    assert pyAES.galoisMult(0x57, 2) == 0xAE


def test_password_to_key_longueur_32():
    """PasswordToKey produit une clé de 32 octets, déterministe."""
    cle = pyAES.passwordToKey("mot de passe de test")
    assert isinstance(cle, (list, bytes))
    assert len(list(cle)) == 32
    assert list(pyAES.passwordToKey("mot de passe de test")) == list(cle)


def test_getblock_bourre_les_blocs_courts():
    """GetBlock : bloc de moins de 16 octets complété par la valeur manquante."""
    import io

    fp = io.BytesIO(b"court")
    bloc = pyAES.getBlock(fp)
    assert len(bloc) == 16
    assert bloc[:5] == list(b"court")
    assert bloc[5:] == [11] * 11  # 16 - 5


def test_getblock_fichier_vide():
    """GetBlock en fin de flux : bloc vide, pas d'erreur."""
    import io

    assert pyAES.getBlock(io.BytesIO(b"")) == b""
