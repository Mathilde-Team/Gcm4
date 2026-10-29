# SPDX-License-Identifier: GPL-3.0-or-later
"""Mots de passe chiffrés de gcm4.conf, compatibles avec l'application GTK3 (#138).

Reprise de ``gcm4_core`` (``encrypt`` / ``decrypt`` / ``decrypt_old`` /
``load_encryption_key`` / ``initialise_encyption_key``) et de leur appel
dans ``gnome_connection_manager.py`` :

- secret = nom d'utilisateur (``USER``, ``LOGNAME``, ``USERNAME``) suivi du
  contenu de ``.gcm4.key`` (``gcm4_core.get_password``) ;
- format lu selon ``[options] version`` : **absente** → ancien XOR
  (``decrypt_old``), **présente** (quelle que soit sa valeur, comme la
  comparaison ``"1" == 0`` de GTK3) → AES-256 OFB de ``pyAES`` ;
- écriture toujours au format courant (AES) et ``version = 1`` ;
- ``.gcm4.key`` créée (``0600``, jamais écrasée) à la première écriture si
  elle manque.

Différence voulue avec GTK3 : une erreur explicite là où GTK3 effaçait en
silence. ``gcm4_core.decrypt`` et ``pyAES.decrypt`` renvoient ``""`` ou du
texte faux avec une mauvaise clé, et l'enregistrement suivant écrasait les
mots de passe. Ici, le bourrage PKCS#7 et l'UTF-8 sont vérifiés, et toute
incohérence lève :class:`ChiffrementError` (une :class:`ConfigError`) :
rien n'est chargé, donc rien ne peut être écrasé.

Mot de passe maître (#98, optionnel) : ``.gcm4.key`` peut contenir la clé
enveloppée par ``master_password_core`` (mot de passe maître et code de
recouvrement). Elle est alors **verrouillée** tant que :meth:`ChiffrementGcm.deverrouiller`
(ou :meth:`~ChiffrementGcm.recuperer`) n'a pas réussi : aucune lecture ni
écriture d'un mot de passe d'hôte n'est possible, et la clé n'est jamais
recréée par-dessus. Sans mot de passe maître, rien ne change.

Aucun mot de passe, clair ou chiffré, ni la clé, ni le mot de passe maître,
ni le code de recouvrement ne sont journalisés (#117).
"""

from __future__ import annotations

import base64
import binascii
import configparser
import os
import re
import secrets

from loguru import logger

import gcm4_core
import master_password_core as mpc
import pyAES
from gcm4.core import chemins
from gcm4.core.config import SECTION_OPTIONS, ConfigError
from gcm4.core.i18n import _

FICHIER_CLE = chemins.FICHIER_CLE
#: Valeur écrite dans ``[options] version`` (format AES de ``app_fileversion``).
FORMAT_COURANT = "1"
_BLOC = 16
_SECTION_HOTE = re.compile(r"^host \d+$")


class ChiffrementError(ConfigError):
    """Clé absente ou illisible, ou mot de passe indéchiffrable (message traduit)."""


class CleVerrouillee(ChiffrementError):
    """``.gcm4.key`` est protégée par un mot de passe maître non encore saisi (#98)."""


class MotDePasseMaitreIncorrect(ChiffrementError):
    """Mot de passe maître incorrect (#98)."""


class CodeRecouvrementIncorrect(ChiffrementError):
    """Code de recouvrement incorrect (#98)."""


def dechiffrer_aes_strict(stocke: str, secret: str) -> str:
    """Inverse exact de ``pyAES.encrypt`` ; bourrage et UTF-8 vérifiés.

    Raises:
        ChiffrementError: base64 invalide, taille incorrecte, bourrage PKCS#7
            ou UTF-8 invalide (mauvaise clé, valeur corrompue).
    """
    logger.trace("dechiffrer_aes_strict")
    try:
        donnees = base64.b64decode(stocke, validate=True)
    except (binascii.Error, ValueError) as exc:
        logger.warning("dechiffrer_aes_strict: base64 invalide")
        raise ChiffrementError(_("A stored password is corrupted.")) from exc
    if len(donnees) < 2 * _BLOC or len(donnees) % _BLOC:
        raise ChiffrementError(_("A stored password is corrupted."))
    vecteur, corps = donnees[:_BLOC], donnees[_BLOC:]
    cle_aes = pyAES.passwordToKey(secret)
    flux: list[int] = list(vecteur)
    clair = bytearray()
    for debut in range(0, len(corps), _BLOC):
        flux = pyAES.aesEncrypt(flux, cle_aes)
        bloc = corps[debut : debut + _BLOC]
        # Contrôle de zip(strict=True), absent de Python 3.9 (Rocky 9, #223).
        if len(bloc) != len(flux):
            raise ValueError(f"bloc de {len(bloc)} octets, flux de {len(flux)}")
        clair += bytes(o ^ f for o, f in zip(bloc, flux))  # noqa: B905 (contrôlé ci-dessus)
    bourrage = clair[-1]
    if not 1 <= bourrage <= _BLOC or clair[-bourrage:] != bytes([bourrage]) * bourrage:
        raise ChiffrementError(
            _("Unable to decrypt the stored passwords: wrong or replaced .gcm4.key.")
        )
    try:
        resultat = clair[:-bourrage].decode("utf-8")
    except UnicodeDecodeError as exc:
        logger.warning("dechiffrer_aes_strict: UTF-8 invalide")
        raise ChiffrementError(
            _("Unable to decrypt the stored passwords: wrong or replaced .gcm4.key.")
        ) from exc
    logger.trace("dechiffrer_aes_strict: fait (longueur masquée)")
    return resultat


class ChiffrementGcm:
    """Chiffrement de ``gcm4.conf`` avec ``.gcm4.key`` (format de l'application GTK3).

    S'utilise avec :func:`gcm4.core.config.load_config` /
    :func:`~gcm4.core.config.save_config`, qui appellent :meth:`preparer`
    avant de lire les hôtes et écrivent :attr:`version_format`.
    """

    version_format = FORMAT_COURANT

    def __init__(self, config_dir: str, utilisateur: str | None = None) -> None:
        """``utilisateur`` : ``None`` → ``gcm4_core.get_username()`` (variables d'environnement)."""
        logger.debug("ChiffrementGcm.__init__: {}", config_dir)
        self.fichier_cle = os.path.join(config_dir, FICHIER_CLE)
        self._utilisateur = gcm4_core.get_username() if utilisateur is None else utilisateur
        self._cle: str | None = None
        self.ancien_format = False
        logger.debug("ChiffrementGcm.__init__: prêt")

    @property
    def _secret(self) -> str:
        """Nom d'utilisateur + clé, comme ``gcm4_core.get_password``."""
        logger.trace("ChiffrementGcm._secret")
        resultat = (self._utilisateur or "") + (self._cle or "")
        logger.trace("ChiffrementGcm._secret: (masqué)")
        return resultat

    def _lire_cle(self) -> str | None:
        """Contenu de ``.gcm4.key``, ``None`` si absente."""
        logger.debug("ChiffrementGcm._lire_cle: {}", self.fichier_cle)
        try:
            with open(self.fichier_cle, encoding="utf-8") as fichier:
                cle = fichier.read()
        except FileNotFoundError:
            logger.debug("ChiffrementGcm._lire_cle: absente")
            return None
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("ChiffrementGcm._lire_cle: illisible : {}", exc)
            raise ChiffrementError(
                _("Unable to read the encryption key %s: %s") % (self.fichier_cle, exc)
            ) from exc
        logger.debug("ChiffrementGcm._lire_cle: lue (masquée)")
        return cle

    def preparer(self, brut: configparser.RawConfigParser) -> None:
        """Format d'après ``[options] version`` et chargement de la clé, avant la lecture des hôtes.

        Raises:
            ChiffrementError: ``.gcm4.key`` absente alors que des mots de passe
                AES sont enregistrés, ou illisible.
            CleVerrouillee: ``.gcm4.key`` protégée et pas encore déverrouillée.
        """
        logger.debug("ChiffrementGcm.preparer")
        self.ancien_format = not brut.has_option(SECTION_OPTIONS, "version")
        contenu = self._lire_cle()
        if contenu is not None and mpc.is_protected(contenu):
            if self._cle is None:
                logger.debug("ChiffrementGcm.preparer: clé verrouillée")
                raise CleVerrouillee(self._message_verrouillee())
        else:
            self._cle = contenu
        chiffres = any(
            brut.get(s, "pass", fallback="") for s in brut.sections() if _SECTION_HOTE.match(s)
        )
        if self._cle is None and chiffres and not self.ancien_format:
            raise ChiffrementError(
                _("The encryption key %s is missing: the stored passwords cannot be decrypted.")
                % self.fichier_cle
            )
        logger.debug("ChiffrementGcm.preparer: ancien format={}", self.ancien_format)

    def dechiffrer(self, stocke: str) -> str:
        """Valeur de ``pass`` → mot de passe (vide → vide)."""
        logger.trace("ChiffrementGcm.dechiffrer")
        if not stocke:
            logger.trace("ChiffrementGcm.dechiffrer: vide")
            return ""
        if self.ancien_format:
            resultat = gcm4_core.decrypt_old(self._secret, stocke)
        else:
            resultat = dechiffrer_aes_strict(stocke, self._secret)
        logger.trace("ChiffrementGcm.dechiffrer: fait (masqué)")
        return resultat

    def _assurer_cle(self) -> None:
        """Crée ``.gcm4.key`` (0600, ``O_EXCL`` : jamais écrasée) si aucune clé n'est chargée."""
        logger.debug("ChiffrementGcm._assurer_cle")
        if self._cle is not None:
            logger.debug("ChiffrementGcm._assurer_cle: déjà chargée")
            return
        existante = self._lire_cle()
        if existante is not None and mpc.is_protected(existante):
            logger.debug("ChiffrementGcm._assurer_cle: clé verrouillée")
            raise CleVerrouillee(self._message_verrouillee())
        if existante is not None:
            self._cle = existante
            logger.debug("ChiffrementGcm._assurer_cle: existante")
            return
        cle = f"{secrets.randbits(128):x}"  # même alphabet que initialise_encyption_key
        try:
            os.makedirs(os.path.dirname(self.fichier_cle) or ".", exist_ok=True)
            descripteur = os.open(self.fichier_cle, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descripteur, "w", encoding="utf-8") as fichier:
                fichier.write(cle)
                fichier.flush()
                os.fsync(fichier.fileno())
        except OSError as exc:
            logger.warning("ChiffrementGcm._assurer_cle: création impossible : {}", exc)
            raise ChiffrementError(
                _("Unable to create the encryption key %s: %s") % (self.fichier_cle, exc)
            ) from exc
        self._cle = cle
        logger.debug("ChiffrementGcm._assurer_cle: créée")

    def chiffrer(self, clair: str) -> str:
        """Mot de passe → valeur de ``pass`` au format courant (AES)."""
        logger.trace("ChiffrementGcm.chiffrer")
        self._assurer_cle()
        resultat: str = pyAES.encrypt(clair, self._secret)
        logger.trace("ChiffrementGcm.chiffrer: fait (masqué)")
        return resultat

    # --- mot de passe maître (#98) ----------------------------------------------

    def _message_verrouillee(self) -> str:
        """Message traduit : clé verrouillée."""
        logger.trace("ChiffrementGcm._message_verrouillee")
        message: str = _("The encryption key %s is protected by a master password.") % (
            self.fichier_cle
        )
        logger.trace("ChiffrementGcm._message_verrouillee: fait")
        return message

    def est_protegee(self) -> bool:
        """``.gcm4.key`` existe et est protégée par un mot de passe maître."""
        logger.debug("ChiffrementGcm.est_protegee")
        contenu = self._lire_cle()
        resultat = contenu is not None and mpc.is_protected(contenu)
        logger.debug("ChiffrementGcm.est_protegee: {}", resultat)
        return resultat

    def est_verrouillee(self) -> bool:
        """Protégée et pas encore déverrouillée dans cette session."""
        logger.debug("ChiffrementGcm.est_verrouillee")
        resultat = self._cle is None and self.est_protegee()
        logger.debug("ChiffrementGcm.est_verrouillee: {}", resultat)
        return resultat

    def a_code_recouvrement(self) -> bool:
        """La clé protégée porte un code de recouvrement (format v2)."""
        logger.debug("ChiffrementGcm.a_code_recouvrement")
        contenu = self._lire_cle()
        resultat = contenu is not None and mpc.has_recovery_code(contenu)
        logger.debug("ChiffrementGcm.a_code_recouvrement: {}", resultat)
        return resultat

    def _contenu_protege(self) -> str:
        """Contenu protégé de ``.gcm4.key``.

        Raises:
            ChiffrementError: clé absente, illisible ou non protégée.
        """
        logger.debug("ChiffrementGcm._contenu_protege")
        contenu = self._lire_cle()
        if contenu is None or not mpc.is_protected(contenu):
            logger.debug("ChiffrementGcm._contenu_protege: non protégée")
            raise ChiffrementError(_("The encryption key is not protected by a master password."))
        logger.debug("ChiffrementGcm._contenu_protege: lu (masqué)")
        return contenu

    def _ecrire_cle(self, contenu: str) -> None:
        """Remplace ``.gcm4.key`` de façon atomique (fichier ``0600`` puis ``os.replace``).

        Raises:
            ChiffrementError: écriture impossible ; l'ancienne clé est intacte.
        """
        logger.debug("ChiffrementGcm._ecrire_cle: {}", self.fichier_cle)
        temporaire = f"{self.fichier_cle}.tmp"
        try:
            os.makedirs(os.path.dirname(self.fichier_cle) or ".", exist_ok=True)
            if os.path.exists(temporaire):
                os.unlink(temporaire)
            descripteur = os.open(temporaire, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descripteur, "w", encoding="utf-8") as fichier:
                fichier.write(contenu)
                fichier.flush()
                os.fsync(fichier.fileno())
            os.replace(temporaire, self.fichier_cle)
        except OSError as exc:
            logger.warning("ChiffrementGcm._ecrire_cle: écriture impossible : {}", exc)
            raise ChiffrementError(
                _("Unable to write the encryption key %s: %s") % (self.fichier_cle, exc)
            ) from exc
        logger.debug("ChiffrementGcm._ecrire_cle: fait (masqué)")

    def _traduire(self, exc: Exception) -> ChiffrementError:
        """Exception de ``master_password_core`` → :class:`ChiffrementError` traduite."""
        logger.debug("ChiffrementGcm._traduire: {}", type(exc).__name__)
        erreur: ChiffrementError
        if isinstance(exc, mpc.InvalidMasterPasswordError):
            erreur = MotDePasseMaitreIncorrect(_("Incorrect master password."))
        elif isinstance(exc, mpc.InvalidRecoveryCodeError):
            erreur = CodeRecouvrementIncorrect(_("Incorrect recovery code."))
        elif isinstance(exc, mpc.RecoveryCodeUnavailableError):
            erreur = ChiffrementError(_("This encryption key has no recovery code."))
        elif isinstance(exc, mpc.CorruptedProtectedContentError):
            erreur = ChiffrementError(_("The encryption key %s is corrupted.") % self.fichier_cle)
        else:
            erreur = ChiffrementError(_("The master password must not be empty."))
        logger.debug("ChiffrementGcm._traduire: fait")
        return erreur

    def deverrouiller(self, mot_de_passe: str) -> None:
        """Déverrouille la clé pour cette session (rien n'est écrit).

        Raises:
            MotDePasseMaitreIncorrect: mot de passe incorrect.
            ChiffrementError: clé absente, non protégée ou corrompue.
        """
        logger.debug("ChiffrementGcm.deverrouiller (mot de passe masqué)")
        contenu = self._contenu_protege()
        try:
            self._cle = mpc.unlock_key_file_content(contenu, mot_de_passe)
        except (mpc.InvalidMasterPasswordError, mpc.CorruptedProtectedContentError) as exc:
            logger.warning("ChiffrementGcm.deverrouiller: refusé ({})", type(exc).__name__)
            raise self._traduire(exc) from exc
        logger.debug("ChiffrementGcm.deverrouiller: fait")

    def recuperer(self, code: str, nouveau_mot_de_passe: str) -> str:
        """Mot de passe oublié : déverrouille par le code et impose un nouveau mot de passe.

        Returns:
            str: Nouveau code de recouvrement, à afficher une seule fois.

        Raises:
            CodeRecouvrementIncorrect: code incorrect.
            ChiffrementError: clé sans code, corrompue, ou mot de passe vide.
        """
        logger.debug("ChiffrementGcm.recuperer (code et mot de passe masqués)")
        contenu = self._contenu_protege()
        try:
            nouveau, nouveau_code = mpc.recover_with_code(contenu, code, nouveau_mot_de_passe)
            cle = mpc.unlock_key_file_content(nouveau, nouveau_mot_de_passe)
        except (
            mpc.InvalidRecoveryCodeError,
            mpc.RecoveryCodeUnavailableError,
            mpc.CorruptedProtectedContentError,
            ValueError,
        ) as exc:
            logger.warning("ChiffrementGcm.recuperer: refusé ({})", type(exc).__name__)
            raise self._traduire(exc) from exc
        self._ecrire_cle(nouveau)
        self._cle = cle
        logger.debug("ChiffrementGcm.recuperer: fait (nouveau code masqué)")
        return nouveau_code

    def activer_protection(self, mot_de_passe: str) -> str:
        """Protège ``.gcm4.key`` (créée si besoin) par un mot de passe maître.

        Returns:
            str: Code de recouvrement, à afficher une seule fois.

        Raises:
            ChiffrementError: déjà protégée, mot de passe vide, écriture impossible.
        """
        logger.debug("ChiffrementGcm.activer_protection (mot de passe masqué)")
        if self.est_protegee():
            logger.debug("ChiffrementGcm.activer_protection: déjà protégée")
            raise ChiffrementError(
                _("The encryption key is already protected by a master password.")
            )
        self._assurer_cle()
        try:
            contenu, code = mpc.protect_key_file_content_with_recovery(
                self._cle or "", mot_de_passe
            )
        except ValueError as exc:
            logger.warning("ChiffrementGcm.activer_protection: mot de passe vide")
            raise self._traduire(exc) from exc
        self._ecrire_cle(contenu)
        logger.debug("ChiffrementGcm.activer_protection: fait (code masqué)")
        return code

    def changer_mot_de_passe(self, ancien: str, nouveau: str) -> None:
        """Change le mot de passe maître ; le code de recouvrement reste valide.

        Raises:
            MotDePasseMaitreIncorrect: ancien mot de passe incorrect.
            ChiffrementError: non protégée, nouveau mot de passe vide, écriture.
        """
        logger.debug("ChiffrementGcm.changer_mot_de_passe (mots de passe masqués)")
        contenu = self._contenu_protege()
        try:
            nouveau_contenu = mpc.rewrap_key_file_content(contenu, ancien, nouveau)
        except (
            mpc.InvalidMasterPasswordError,
            mpc.CorruptedProtectedContentError,
            ValueError,
        ) as exc:
            logger.warning("ChiffrementGcm.changer_mot_de_passe: refusé ({})", type(exc).__name__)
            raise self._traduire(exc) from exc
        self._ecrire_cle(nouveau_contenu)
        logger.debug("ChiffrementGcm.changer_mot_de_passe: fait")

    def regenerer_code(self, mot_de_passe: str) -> str:
        """Remplace le code de recouvrement (l'ancien cesse d'être valide).

        Returns:
            str: Nouveau code, à afficher une seule fois.

        Raises:
            MotDePasseMaitreIncorrect: mot de passe incorrect.
            ChiffrementError: non protégée, corrompue, écriture impossible.
        """
        logger.debug("ChiffrementGcm.regenerer_code (mot de passe masqué)")
        contenu = self._contenu_protege()
        try:
            nouveau_contenu, code = mpc.regenerate_recovery_code(contenu, mot_de_passe)
        except (mpc.InvalidMasterPasswordError, mpc.CorruptedProtectedContentError) as exc:
            logger.warning("ChiffrementGcm.regenerer_code: refusé ({})", type(exc).__name__)
            raise self._traduire(exc) from exc
        self._ecrire_cle(nouveau_contenu)
        logger.debug("ChiffrementGcm.regenerer_code: fait (code masqué)")
        return code

    def desactiver_protection(self, mot_de_passe: str) -> None:
        """Retire le mot de passe maître : ``.gcm4.key`` redevient la clé en clair.

        Raises:
            MotDePasseMaitreIncorrect: mot de passe incorrect.
            ChiffrementError: non protégée, corrompue, écriture impossible.
        """
        logger.debug("ChiffrementGcm.desactiver_protection (mot de passe masqué)")
        contenu = self._contenu_protege()
        try:
            cle = mpc.remove_master_password_protection(contenu, mot_de_passe)
        except (mpc.InvalidMasterPasswordError, mpc.CorruptedProtectedContentError) as exc:
            logger.warning("ChiffrementGcm.desactiver_protection: refusé ({})", type(exc).__name__)
            raise self._traduire(exc) from exc
        self._ecrire_cle(cle)
        self._cle = cle
        logger.debug("ChiffrementGcm.desactiver_protection: fait")
