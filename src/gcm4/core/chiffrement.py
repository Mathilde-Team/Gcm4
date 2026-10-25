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

Aucun mot de passe, clair ou chiffré, ni la clé ne sont journalisés (#117).
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
        """
        logger.debug("ChiffrementGcm.preparer")
        self.ancien_format = not brut.has_option(SECTION_OPTIONS, "version")
        self._cle = self._lire_cle()
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
