#!/usr/bin/python3
# -*- coding: UTF-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Protection par mot de passe maître du fichier de clé locale (``KEY_FILE``).

Zéro dépendance GTK — voir ``CONSIGNES-AGENTS-IA.md``. Traite l'item
« Master password au démarrage » de `docs/features-backlog.md`
(urgence haute, sécurité), non commencé jusqu'à cette session.

## Constat sur l'existant

``gcm4_core.initialise_encyption_key()``/``load_encryption_key()`` génèrent
et lisent ``KEY_FILE`` (``~/.gcm/.gcm.key``) en clair sur disque (permissions
``0o600``, donc protégé des autres utilisateurs du système, mais lisible par
quiconque a accès au compte — ex. une sauvegarde non chiffrée, un accès
physique à la machine déverrouillée). C'est cette clé locale qui sert
ensuite (via ``pyAES``/``gcm4_core.encrypt``/``decrypt``) à chiffrer les mots
de passe stockés dans ``gcm.conf``. Un « master password » a pour but de ne
plus jamais écrire cette clé en clair : elle est enveloppée
(chiffrée) par une clé dérivée du mot de passe maître, que l'utilisatrice
retape à chaque démarrage — sans ce mot de passe, ``KEY_FILE`` seul ne
suffit plus à déchiffrer les mots de passe stockés.

## Ce que ce module fait (et ne fait pas)

Ce module fournit uniquement la **logique de protection/déverrouillage du
contenu de ``KEY_FILE``** — dérivation de clé (PBKDF2-HMAC-SHA256, stdlib
``hashlib``, pas de nouvelle dépendance), enveloppe/désenveloppe via
``pyAES`` (déjà utilisé par le reste du projet pour les mots de passe,
cohérence délibérée plutôt qu'un deuxième algorithme), vérificateur
d'intégrité (détecte un mauvais mot de passe au lieu de renvoyer une clé
corrompue silencieusement), et **rétrocompatibilité totale** : un
``KEY_FILE`` existant, non protégé, reste lisible tel quel
(``is_protected()`` renvoie ``False``) — aucune migration forcée, aucun
risque de casser la configuration d'une utilisatrice existante (contrainte
explicite de `CLAUDE.md`).

Le branchement est fait côté gcm4 (#98) : ``gcm4.core.chiffrement`` lit et
écrit ``KEY_FILE`` via ce module, ``gcm4.ui.mot_de_passe_maitre`` demande le
mot de passe au démarrage (page de verrouillage) et gère l'activation, le
changement, le code de recouvrement et la désactivation dans les
préférences. ``gcm4_core.load_encryption_key()`` (application GTK3) refuse
un ``KEY_FILE`` protégé au lieu de le lire comme une clé en clair.

## Format du contenu protégé

``GCM4MPW1:<salt base64>:<vérificateur hex>:<clé locale chiffrée base64>``
(``GCM4MPW1`` — préfixe magique versionné, permet de faire évoluer le format
plus tard sans ambiguïté avec un ``KEY_FILE`` non protégé, qui ne commence
jamais par ce préfixe puisqu'il ne contient que le résultat de
``"%x" % (x * y)`` de ``initialise_encyption_key()``, uniquement des
chiffres hexadécimaux).

Format v2 (#98, décisions de l'auteure du 12/10/2026) :
``GCM4MPW2:<enveloppe mot de passe>:<enveloppe code de recouvrement>``, chaque
enveloppe ayant la forme v1 ci-dessus (sans préfixe). Le code de
recouvrement est généré à l'activation, affiché une seule fois, jamais
stocké ni journalisé ; il déverrouille la clé si le mot de passe est oublié
puis impose un nouveau mot de passe et un nouveau code
(:func:`recover_with_code`). Le v1 reste lu et accepté partout. La protection
reste optionnelle : sans mot de passe maître, ``KEY_FILE`` garde la clé en
clair, comme avant.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets

from loguru import logger as app_logger

# Convention bibliothèque (#130) : muet à l'import, réactivé par
# logging_config.configure_logging() (CORE_MODULES).
app_logger.disable(__name__)

import pyAES

__all__ = [
    "MASTER_PASSWORD_MAGIC",
    "PBKDF2_ITERATIONS",
    "InvalidMasterPasswordError",
    "CorruptedProtectedContentError",
    "is_protected",
    "protect_key_file_content",
    "unlock_key_file_content",
    "rewrap_key_file_content",
    "remove_master_password_protection",
    "MASTER_PASSWORD_MAGIC_V2",
    "RECOVERY_CODE_ALPHABET",
    "InvalidRecoveryCodeError",
    "RecoveryCodeUnavailableError",
    "generate_recovery_code",
    "normalize_recovery_code",
    "has_recovery_code",
    "protect_key_file_content_with_recovery",
    "unlock_with_recovery_code",
    "regenerate_recovery_code",
    "recover_with_code",
]

MASTER_PASSWORD_MAGIC = "GCM4MPW1:"

# Format v2 (#98, décision de l'auteure du 12/10/2026) : la clé locale est
# enveloppée deux fois, sous le mot de passe maître et sous un code de
# recouvrement. ``GCM4MPW2:<enveloppe mot de passe>:<enveloppe code>``, chaque
# enveloppe valant ``<sel base64>:<vérificateur hex>:<clé chiffrée base64>``
# comme en v1. Le code n'est jamais stocké : seule son enveloppe l'est.
MASTER_PASSWORD_MAGIC_V2 = "GCM4MPW2:"

# Code de recouvrement : 24 caractères tirés de 31 symboles sans ambiguïté
# visuelle (ni 0/O, ni 1/I/L), soit environ 119 bits, affichés par groupes
# de 4 (``ABCD-EFGH-...``) pour être recopiés à la main.
RECOVERY_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
RECOVERY_CODE_LENGTH = 24
_RECOVERY_CODE_GROUP = 4

# 200 000 itérations : recommandation OWASP 2023 pour PBKDF2-HMAC-SHA256
# (>= 600 000 recommandé pour un stockage de mots de passe utilisateur côté
# serveur ; ici la menace est différente — dérivation locale, à chaque
# démarrage de l'app sur le poste de l'utilisatrice — un compromis a été
# choisi entre coût de calcul au démarrage et résistance au brute-force
# hors-ligne si le KEY_FILE protégé est exfiltré. Non mesuré sur une machine
# réelle dans cet environnement de travail (pas de GTK/matériel cible
# disponible ici) — à valider empiriquement (temps de démarrage perçu)
# avant de considérer ce chiffre définitif.
PBKDF2_ITERATIONS = 200_000

_VERIFIER_CONTEXT = "gcm4-master-password-check"


class InvalidMasterPasswordError(Exception):
    """Levée quand le mot de passe maître fourni ne correspond pas."""


class CorruptedProtectedContentError(Exception):
    """Levée quand le contenu protégé est malformé (format inattendu)."""


class InvalidRecoveryCodeError(Exception):
    """Levée quand le code de recouvrement fourni ne correspond pas."""


class RecoveryCodeUnavailableError(Exception):
    """Levée quand le contenu protégé (format v1) n'a pas de code de recouvrement."""


def is_protected(key_file_content: str) -> bool:
    """Indique si ``key_file_content`` est protégé par un mot de passe maître.

    Args:
        key_file_content (str): Contenu brut lu depuis ``KEY_FILE``.

    Returns:
        bool: ``True`` si le contenu porte le préfixe magique
            :data:`MASTER_PASSWORD_MAGIC` ou :data:`MASTER_PASSWORD_MAGIC_V2` (donc protégé), ``False`` sinon
            (y compris pour un ``KEY_FILE`` legacy en clair ou un contenu
            vide).
    """
    app_logger.debug(f"is_protected() called | len={len(key_file_content)}")
    result = key_file_content.startswith((MASTER_PASSWORD_MAGIC, MASTER_PASSWORD_MAGIC_V2))
    app_logger.debug(f"is_protected() returning | result={result}")
    return result


def _derive_key_hex(master_password: str, salt: bytes) -> str:
    """Dérive une clé AES-256 (en hexadécimal) depuis un mot de passe maître.

    Args:
        master_password (str): Mot de passe maître saisi par l'utilisatrice.
        salt (bytes): Sel aléatoire propre à ce ``KEY_FILE`` protégé.

    Returns:
        str: Clé dérivée de 32 octets, représentée en hexadécimal (64
            caractères) — format accepté par ``pyAES.encrypt``/``decrypt``
            comme mot de passe.
    """
    app_logger.debug(f"_derive_key_hex() called | len(salt)={len(salt)} (mot de passe masqué)")
    derived = hashlib.pbkdf2_hmac(
        "sha256", master_password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=32
    )
    result = derived.hex()
    app_logger.debug("_derive_key_hex() returning | clé dérivée (masquée)")
    return result


def _verifier_for(derived_key_hex: str) -> str:
    """Calcule le vérificateur d'intégrité associé à une clé dérivée.

    Le vérificateur permet de détecter un mot de passe maître incorrect
    *sans* déchiffrer la clé locale au préalable : il ne dépend que de la
    clé dérivée, jamais du contenu chiffré.

    Args:
        derived_key_hex (str): Clé dérivée (hex) issue de
            :func:`_derive_key_hex`.

    Returns:
        str: Empreinte SHA-256 (hex) de la clé dérivée concaténée à un
            contexte fixe.
    """
    app_logger.debug("_verifier_for() called | clé dérivée (masquée)")
    result = hashlib.sha256(f"{derived_key_hex}:{_VERIFIER_CONTEXT}".encode()).hexdigest()
    app_logger.debug(f"_verifier_for() returning | verifier={result[:8]}...")
    return result


def _wrap_envelope(raw_key: str, secret: str) -> str:
    """Enveloppe ``raw_key`` sous ``secret`` : ``<sel>:<vérificateur>:<chiffré>``.

    Args:
        raw_key (str): Clé locale en clair.
        secret (str): Mot de passe maître ou code de recouvrement normalisé.

    Returns:
        str: Enveloppe sans préfixe magique (sel aléatoire de 16 octets).
    """
    app_logger.debug("_wrap_envelope() called | secret masqué")
    salt = os.urandom(16)
    derived_key_hex = _derive_key_hex(secret, salt)
    verifier = _verifier_for(derived_key_hex)
    ciphertext = pyAES.encrypt(raw_key, derived_key_hex)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    result = f"{salt_b64}:{verifier}:{ciphertext}"
    app_logger.debug(f"_wrap_envelope() returning | len(result)={len(result)}")
    return result


def _unwrap_envelope(envelope: tuple[bytes, str, str], secret: str) -> str | None:
    """Ouvre une enveloppe ; ``None`` si ``secret`` ne correspond pas.

    Args:
        envelope (tuple[bytes, str, str]): Sel, vérificateur et texte chiffré.
        secret (str): Mot de passe maître ou code de recouvrement normalisé.

    Returns:
        str | None: Clé locale en clair, ou ``None`` (vérificateur invalide,
            rien n'est déchiffré).
    """
    app_logger.debug("_unwrap_envelope() called | secret masqué")
    salt, expected_verifier, ciphertext = envelope
    derived_key_hex = _derive_key_hex(secret, salt)
    if _verifier_for(derived_key_hex) != expected_verifier:
        app_logger.debug("_unwrap_envelope() returning | vérificateur invalide")
        return None
    raw_key = pyAES.decrypt(ciphertext, derived_key_hex)
    app_logger.debug("_unwrap_envelope() returning | clé déchiffrée (masquée)")
    return raw_key


def protect_key_file_content(raw_key: str, master_password: str) -> str:
    """Enveloppe une clé locale en clair sous un mot de passe maître.

    Args:
        raw_key (str): Contenu actuel de ``KEY_FILE`` (clé locale en clair,
            telle que produite par ``gcm4_core.initialise_encyption_key()``).
        master_password (str): Mot de passe maître choisi par l'utilisatrice.

    Returns:
        str: Nouveau contenu à écrire dans ``KEY_FILE``, au format décrit
            dans la docstring de module (préfixé
            :data:`MASTER_PASSWORD_MAGIC`).

    Raises:
        ValueError: Si ``master_password`` est vide — un mot de passe maître
            vide n'apporterait aucune protection réelle tout en donnant une
            fausse impression de sécurité.
    """
    app_logger.debug(
        f"protect_key_file_content() called | len(raw_key)={len(raw_key)} (clés masquées)"
    )
    if not master_password:
        app_logger.debug("protect_key_file_content() raising | mot de passe maître vide")
        raise ValueError("master_password must not be empty")
    result = f"{MASTER_PASSWORD_MAGIC}{_wrap_envelope(raw_key, master_password)}"
    app_logger.debug(f"protect_key_file_content() returning | len(result)={len(result)}")
    return result


def _decode_envelope(salt_b64: str, verifier: str, ciphertext: str) -> tuple[bytes, str, str]:
    """Décode le sel d'une enveloppe.

    Args:
        salt_b64 (str): Sel encodé en base64.
        verifier (str): Vérificateur (hex).
        ciphertext (str): Texte chiffré (base64).

    Returns:
        tuple[bytes, str, str]: Sel décodé, vérificateur, texte chiffré.

    Raises:
        CorruptedProtectedContentError: Si le sel n'est pas du base64 valide.
    """
    app_logger.debug("_decode_envelope() called")
    try:
        salt = base64.b64decode(salt_b64, validate=True)
    except Exception as exc:
        app_logger.debug(f"_decode_envelope() raising | salt invalide: {exc}")
        raise CorruptedProtectedContentError("Salt is not valid base64") from exc
    app_logger.debug("_decode_envelope() returning")
    return salt, verifier, ciphertext


def _parse_envelopes(protected_content: str) -> list[tuple[bytes, str, str]]:
    """Décompose un contenu protégé en ses enveloppes (v1 : 1, v2 : 2).

    La première enveloppe est toujours celle du mot de passe maître ; la
    seconde (v2 seulement) celle du code de recouvrement.

    Args:
        protected_content (str): Contenu de ``KEY_FILE``.

    Returns:
        list[tuple[bytes, str, str]]: Enveloppes (sel, vérificateur, chiffré).

    Raises:
        CorruptedProtectedContentError: Préfixe magique absent, nombre de
            champs inattendu ou sel invalide.
    """
    app_logger.debug(f"_parse_envelopes() called | len={len(protected_content)}")
    if protected_content.startswith(MASTER_PASSWORD_MAGIC_V2):
        parts = protected_content[len(MASTER_PASSWORD_MAGIC_V2) :].split(":")
        if len(parts) != 6:
            app_logger.debug(f"_parse_envelopes() raising | v2, nombre de parties={len(parts)}")
            raise CorruptedProtectedContentError(
                f"Expected 6 ':'-separated fields, got {len(parts)}"
            )
        result = [_decode_envelope(*parts[0:3]), _decode_envelope(*parts[3:6])]
        app_logger.debug("_parse_envelopes() returning | v2, 2 enveloppes")
        return result
    result = [_parse_protected_content(protected_content)]
    app_logger.debug("_parse_envelopes() returning | v1, 1 enveloppe")
    return result


def _parse_protected_content(protected_content: str) -> tuple[bytes, str, str]:
    """Décompose un contenu protégé v1 en (salt, vérificateur, texte chiffré).

    Args:
        protected_content (str): Contenu de ``KEY_FILE``, tel que produit
            par :func:`protect_key_file_content`.

    Returns:
        tuple[bytes, str, str]: Le sel décodé, le vérificateur (hex), et le
            texte chiffré (base64, tel quel, à passer à ``pyAES.decrypt``).

    Raises:
        CorruptedProtectedContentError: Si ``protected_content`` ne porte
            pas le préfixe magique, ou n'a pas le nombre de parties attendu,
            ou si le sel n'est pas du base64 valide.
    """
    app_logger.debug(f"_parse_protected_content() called | len={len(protected_content)}")
    if not protected_content.startswith(MASTER_PASSWORD_MAGIC):
        app_logger.debug("_parse_protected_content() raising | préfixe magique absent")
        raise CorruptedProtectedContentError(
            "Content is not master-password-protected (missing magic prefix)"
        )
    body = protected_content[len(MASTER_PASSWORD_MAGIC) :]
    parts = body.split(":", 2)
    if len(parts) != 3:
        app_logger.debug(f"_parse_protected_content() raising | nombre de parties={len(parts)}")
        raise CorruptedProtectedContentError(f"Expected 3 ':'-separated fields, got {len(parts)}")
    result = _decode_envelope(*parts)
    app_logger.debug("_parse_protected_content() returning | contenu décomposé")
    return result


def unlock_key_file_content(protected_content: str, master_password: str) -> str:
    """Déchiffre une clé locale protégée (v1 ou v2) à l'aide du mot de passe maître.

    Args:
        protected_content (str): Contenu de ``KEY_FILE``, tel que produit
            par :func:`protect_key_file_content` ou
            :func:`protect_key_file_content_with_recovery`.
        master_password (str): Mot de passe maître saisi par l'utilisatrice.

    Returns:
        str: La clé locale en clair (même valeur que ``raw_key`` passé lors
            de la protection).

    Raises:
        CorruptedProtectedContentError: Si ``protected_content`` est
            malformé (voir :func:`_parse_envelopes`).
        InvalidMasterPasswordError: Si ``master_password`` ne correspond pas
            au mot de passe utilisé lors de la protection (vérificateur
            invalide) — la clé locale n'est **pas** déchiffrée dans ce cas.
    """
    app_logger.debug(
        f"unlock_key_file_content() called | len={len(protected_content)} (mot de passe masqué)"
    )
    raw_key = _unwrap_envelope(_parse_envelopes(protected_content)[0], master_password)
    if raw_key is None:
        app_logger.debug("unlock_key_file_content() raising | mot de passe maître incorrect")
        raise InvalidMasterPasswordError("Incorrect master password")
    app_logger.debug(f"unlock_key_file_content() returning | len(raw_key)={len(raw_key)} (masqué)")
    return raw_key


def rewrap_key_file_content(
    protected_content: str, old_master_password: str, new_master_password: str
) -> str:
    """Change le mot de passe maître protégeant une clé locale.

    En v2, l'enveloppe du code de recouvrement est conservée telle quelle :
    le code noté par l'utilisatrice reste valide (#98).

    Args:
        protected_content (str): Contenu actuellement protégé de
            ``KEY_FILE``.
        old_master_password (str): Mot de passe maître actuel.
        new_master_password (str): Nouveau mot de passe maître souhaité.

    Returns:
        str: Nouveau contenu à écrire dans ``KEY_FILE``, protégé par
            ``new_master_password`` (nouveau sel, nouveau vérificateur),
            dans le même format (v1 ou v2) que ``protected_content``.

    Raises:
        CorruptedProtectedContentError: Voir :func:`unlock_key_file_content`.
        InvalidMasterPasswordError: Si ``old_master_password`` est incorrect.
        ValueError: Si ``new_master_password`` est vide (voir
            :func:`protect_key_file_content`).
    """
    app_logger.debug(
        f"rewrap_key_file_content() called | len={len(protected_content)} (mots de passe masqués)"
    )
    raw_key = unlock_key_file_content(protected_content, old_master_password)
    result = protect_key_file_content(raw_key, new_master_password)
    if has_recovery_code(protected_content):
        recovery_envelope = protected_content[len(MASTER_PASSWORD_MAGIC_V2) :].split(":", 3)[3]
        new_envelope = result[len(MASTER_PASSWORD_MAGIC) :]
        result = f"{MASTER_PASSWORD_MAGIC_V2}{new_envelope}:{recovery_envelope}"
    app_logger.debug(f"rewrap_key_file_content() returning | len(result)={len(result)}")
    return result


def generate_recovery_code() -> str:
    """Tire un code de recouvrement aléatoire (``secrets``), groupé par 4.

    Le code n'est jamais journalisé : seule sa longueur l'est.

    Returns:
        str: Code de :data:`RECOVERY_CODE_LENGTH` caractères de
            :data:`RECOVERY_CODE_ALPHABET`, sous la forme ``ABCD-EFGH-...``.
    """
    app_logger.debug("generate_recovery_code() called")
    raw = "".join(secrets.choice(RECOVERY_CODE_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))
    groups = [raw[i : i + _RECOVERY_CODE_GROUP] for i in range(0, len(raw), _RECOVERY_CODE_GROUP)]
    result = "-".join(groups)
    app_logger.debug(f"generate_recovery_code() returning | len={len(result)} (code masqué)")
    return result


def normalize_recovery_code(code: str) -> str:
    """Normalise un code saisi : majuscules, sans tirets ni blancs.

    Args:
        code (str): Code tel que recopié par l'utilisatrice.

    Returns:
        str: Code comparable à celui enveloppé (sans séparateurs).
    """
    app_logger.debug(f"normalize_recovery_code() called | len={len(code)} (code masqué)")
    result = "".join(ch for ch in code.upper() if ch.isalnum())
    app_logger.debug(f"normalize_recovery_code() returning | len={len(result)}")
    return result


def has_recovery_code(protected_content: str) -> bool:
    """Indique si un contenu protégé porte une enveloppe de recouvrement (v2).

    Args:
        protected_content (str): Contenu de ``KEY_FILE``.

    Returns:
        bool: ``True`` pour le format v2.
    """
    app_logger.debug(f"has_recovery_code() called | len={len(protected_content)}")
    result = protected_content.startswith(MASTER_PASSWORD_MAGIC_V2)
    app_logger.debug(f"has_recovery_code() returning | result={result}")
    return result


def protect_key_file_content_with_recovery(raw_key: str, master_password: str) -> tuple[str, str]:
    """Enveloppe une clé locale sous le mot de passe maître et un nouveau code.

    Args:
        raw_key (str): Clé locale en clair.
        master_password (str): Mot de passe maître choisi.

    Returns:
        tuple[str, str]: Contenu v2 à écrire dans ``KEY_FILE`` et code de
            recouvrement à afficher **une seule fois** (jamais stocké).

    Raises:
        ValueError: Si ``master_password`` est vide.
    """
    app_logger.debug(
        f"protect_key_file_content_with_recovery() called | len(raw_key)={len(raw_key)} (masqué)"
    )
    if not master_password:
        app_logger.debug("protect_key_file_content_with_recovery() raising | mot de passe vide")
        raise ValueError("master_password must not be empty")
    code = generate_recovery_code()
    password_envelope = _wrap_envelope(raw_key, master_password)
    recovery_envelope = _wrap_envelope(raw_key, normalize_recovery_code(code))
    content = f"{MASTER_PASSWORD_MAGIC_V2}{password_envelope}:{recovery_envelope}"
    app_logger.debug(
        f"protect_key_file_content_with_recovery() returning | len(content)={len(content)}"
    )
    return content, code


def unlock_with_recovery_code(protected_content: str, recovery_code: str) -> str:
    """Déchiffre la clé locale à l'aide du code de recouvrement (v2).

    Args:
        protected_content (str): Contenu v2 de ``KEY_FILE``.
        recovery_code (str): Code saisi (tirets et casse indifférents).

    Returns:
        str: La clé locale en clair.

    Raises:
        CorruptedProtectedContentError: Contenu malformé.
        RecoveryCodeUnavailableError: Contenu v1, sans code de recouvrement.
        InvalidRecoveryCodeError: Code incorrect (rien n'est déchiffré).
    """
    app_logger.debug(f"unlock_with_recovery_code() called | len={len(protected_content)} (masqué)")
    envelopes = _parse_envelopes(protected_content)
    if len(envelopes) < 2:
        app_logger.debug("unlock_with_recovery_code() raising | format v1 sans code")
        raise RecoveryCodeUnavailableError("This key file has no recovery code")
    raw_key = _unwrap_envelope(envelopes[1], normalize_recovery_code(recovery_code))
    if raw_key is None:
        app_logger.debug("unlock_with_recovery_code() raising | code incorrect")
        raise InvalidRecoveryCodeError("Incorrect recovery code")
    app_logger.debug("unlock_with_recovery_code() returning | clé déchiffrée (masquée)")
    return raw_key


def regenerate_recovery_code(protected_content: str, master_password: str) -> tuple[str, str]:
    """Remplace le code de recouvrement (l'ancien cesse d'être valide).

    Accepte aussi un contenu v1, qui passe alors en v2.

    Args:
        protected_content (str): Contenu protégé actuel.
        master_password (str): Mot de passe maître actuel.

    Returns:
        tuple[str, str]: Nouveau contenu v2 et nouveau code à afficher.

    Raises:
        CorruptedProtectedContentError: Contenu malformé.
        InvalidMasterPasswordError: Mot de passe incorrect.
    """
    app_logger.debug(f"regenerate_recovery_code() called | len={len(protected_content)} (masqué)")
    raw_key = unlock_key_file_content(protected_content, master_password)
    result = protect_key_file_content_with_recovery(raw_key, master_password)
    app_logger.debug("regenerate_recovery_code() returning | nouveau code (masqué)")
    return result


def recover_with_code(
    protected_content: str, recovery_code: str, new_master_password: str
) -> tuple[str, str]:
    """Mot de passe oublié : déverrouille par le code, impose un nouveau mot de passe.

    Le code utilisé est remplacé par un nouveau (à afficher une fois), pour
    qu'un code noté puis retrouvé par un tiers ne serve qu'une fois.

    Args:
        protected_content (str): Contenu v2 de ``KEY_FILE``.
        recovery_code (str): Code de recouvrement saisi.
        new_master_password (str): Nouveau mot de passe maître (non vide).

    Returns:
        tuple[str, str]: Nouveau contenu v2 et nouveau code de recouvrement.

    Raises:
        CorruptedProtectedContentError: Contenu malformé.
        RecoveryCodeUnavailableError: Contenu v1.
        InvalidRecoveryCodeError: Code incorrect.
        ValueError: Nouveau mot de passe vide.
    """
    app_logger.debug(f"recover_with_code() called | len={len(protected_content)} (masqué)")
    if not new_master_password:
        app_logger.debug("recover_with_code() raising | nouveau mot de passe vide")
        raise ValueError("new_master_password must not be empty")
    raw_key = unlock_with_recovery_code(protected_content, recovery_code)
    result = protect_key_file_content_with_recovery(raw_key, new_master_password)
    app_logger.debug("recover_with_code() returning | nouveau contenu et nouveau code (masqués)")
    return result


def remove_master_password_protection(protected_content: str, master_password: str) -> str:
    """Retire la protection par mot de passe maître d'une clé locale.

    Renvoie la clé locale en clair, prête à être réécrite telle quelle dans
    ``KEY_FILE`` (revient au comportement legacy — voir docstring de
    module). Le choix de désactiver la protection reste une décision GTK
    (bouton dans les préférences) hors périmètre de ce module.

    Args:
        protected_content (str): Contenu actuellement protégé de
            ``KEY_FILE``.
        master_password (str): Mot de passe maître actuel, requis pour
            confirmer le retrait de la protection.

    Returns:
        str: La clé locale en clair (contenu legacy, non protégé).

    Raises:
        CorruptedProtectedContentError: Voir :func:`unlock_key_file_content`.
        InvalidMasterPasswordError: Voir :func:`unlock_key_file_content`.
    """
    app_logger.debug(
        f"remove_master_password_protection() called | len={len(protected_content)} (masqué)"
    )
    raw_key = unlock_key_file_content(protected_content, master_password)
    app_logger.debug(
        f"remove_master_password_protection() returning | len(raw_key)={len(raw_key)} (masqué)"
    )
    return raw_key
