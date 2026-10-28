# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique du plugin SFTP (#75), sans GTK : connexion, navigation, transferts.

Bibliothèque : paramiko (extra ``paramiko`` de ``pyproject.toml``). Tout ce
module est bloquant (réseau) : l'onglet GTK4 (:mod:`gtk4`) l'appelle depuis un
fil de travail, jamais depuis la boucle GTK.

Clé d'hôte : vérifiée contre ``~/.ssh/known_hosts`` (le fichier d'OpenSSH,
partagé avec ``ssh``). Une clé inconnue lève :class:`CleHoteInconnue` avec
son empreinte SHA256 ; l'onglet la montre et, si l'utilisateur l'accepte,
:func:`ajouter_cle_connue` l'ajoute au fichier puis la connexion est relancée.
Une clé **différente** de celle connue lève :class:`CleHoteChangee` et la
connexion est refusée, sans proposition d'accepter (même règle qu'OpenSSH).

Authentification : mot de passe de l'hôte s'il y en a un, sinon fichier de clé
(option ``identity-file``), agent SSH et clés par défaut de ``~/.ssh``. Un
refus lève :class:`AuthentificationRefusee` ; l'onglet demande alors un mot de
passe (``context.ask_password``) et réessaie.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import os
import posixpath
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import paramiko
from loguru import logger

from gcm4.core.i18n import _

PORT_DEFAUT = 22
#: Dossier distant ouvert à la connexion ; vide : dossier d'accueil.
OPT_DOSSIER = "remote-dir"
#: Fichier de clé privée ; vide : agent SSH et clés par défaut de ``~/.ssh``.
OPT_CLE = "identity-file"
#: Clé de ``[plugin sftp]`` : dossier local des téléchargements.
CONF_TELECHARGEMENTS = "download-dir"
DELAI_CONNEXION_S = 15.0
#: Message de paramiko quand ni mot de passe, ni clé, ni agent ne sont
#: disponibles : traité comme un refus, pour que l'onglet demande un mot de passe.
SANS_METHODE = "No authentication methods available"

Progression = Callable[[int, int], None]


@dataclass(frozen=True)
class Entree:
    """Élément d'un dossier distant."""

    nom: str
    chemin: str
    taille: int
    modifie: int
    dossier: bool
    lien: bool
    mode: int

    @property
    def droits(self) -> str:
        """Droits à la manière de ``ls -l`` (``drwxr-xr-x``)."""
        logger.debug("Entree.droits: {!r}", self.nom)
        texte = stat.filemode(self.mode)
        logger.debug("Entree.droits: {}", texte)
        return texte


class ErreurSftp(Exception):
    """Échec de connexion ou d'opération, message lisible par l'utilisateur."""


class AuthentificationRefusee(ErreurSftp):
    """Le serveur a refusé les identifiants proposés."""


class CleHoteInconnue(ErreurSftp):
    """Clé d'hôte absente de ``known_hosts`` : à faire accepter par l'utilisateur."""

    def __init__(self, nom: str, cle: paramiko.PKey) -> None:
        """Garde le nom ``known_hosts`` de l'hôte et sa clé."""
        logger.debug("CleHoteInconnue.__init__: {!r}", nom)
        super().__init__(nom)
        self.nom = nom
        self.cle = cle
        self.empreinte = empreinte(cle)
        logger.debug("CleHoteInconnue.__init__: fait")


class CleHoteChangee(ErreurSftp):
    """Clé d'hôte différente de celle de ``known_hosts`` : connexion refusée."""

    def __init__(self, nom: str, cle: paramiko.PKey) -> None:
        """Garde le nom de l'hôte et l'empreinte de la clé présentée."""
        logger.debug("CleHoteChangee.__init__: {!r}", nom)
        super().__init__(nom)
        self.nom = nom
        self.empreinte = empreinte(cle)
        logger.debug("CleHoteChangee.__init__: fait")


def empreinte(cle: paramiko.PKey) -> str:
    """Empreinte au format d'OpenSSH : ``SHA256:`` + base64 sans ``=``."""
    logger.debug("empreinte: {}", cle.get_name())
    condense = hashlib.sha256(cle.asbytes()).digest()
    texte = "SHA256:" + base64.b64encode(condense).decode("ascii").rstrip("=")
    logger.debug("empreinte: {}", texte)
    return texte


def nom_known_hosts(hote: str, port: int) -> str:
    """Nom de l'hôte dans ``known_hosts`` : ``hote``, ou ``[hote]:port`` hors port 22."""
    logger.debug("nom_known_hosts: {!r} {}", hote, port)
    nom = hote if port == PORT_DEFAUT else f"[{hote}]:{port}"
    logger.debug("nom_known_hosts: {!r}", nom)
    return nom


def known_hosts_defaut() -> Path:
    """``~/.ssh/known_hosts``, le fichier d'OpenSSH."""
    logger.debug("known_hosts_defaut: entrée")
    chemin = Path.home() / ".ssh" / "known_hosts"
    logger.debug("known_hosts_defaut: {}", chemin)
    return chemin


def ajouter_cle_connue(fichier: Path, nom: str, cle: paramiko.PKey) -> None:
    """Ajoute ``nom type base64`` à la fin de ``fichier``, sans réécrire le reste.

    Le dossier est créé en 0700 et le fichier en 0600 s'ils n'existent pas.
    """
    logger.debug("ajouter_cle_connue: {} {!r} {}", fichier, nom, cle.get_name())
    fichier.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    nouveau = not fichier.exists()
    ligne = f"{nom} {cle.get_name()} {cle.get_base64()}\n"
    with fichier.open("a", encoding="ascii") as flux:
        if not nouveau and fichier.stat().st_size:
            with fichier.open("rb") as lecture:
                lecture.seek(-1, os.SEEK_END)
                if lecture.read(1) != b"\n":
                    flux.write("\n")
        flux.write(ligne)
    if nouveau:
        fichier.chmod(0o600)
    logger.debug("ajouter_cle_connue: fait (nouveau fichier : {})", nouveau)


def parent(chemin: str) -> str:
    """Dossier parent d'un chemin distant absolu ; ``/`` reste ``/``."""
    logger.debug("parent: {!r}", chemin)
    resultat = posixpath.dirname(chemin.rstrip("/")) or "/"
    logger.debug("parent: {!r}", resultat)
    return resultat


def joindre(dossier: str, nom: str) -> str:
    """``dossier/nom`` en POSIX, quel que soit le système local."""
    logger.debug("joindre: {!r} {!r}", dossier, nom)
    chemin = posixpath.join(dossier, nom)
    logger.debug("joindre: {!r}", chemin)
    return chemin


def nom_valide(nom: str) -> bool:
    """Nom de fichier ou de dossier utilisable : non vide, sans ``/``, ni ``.`` ni ``..``."""
    logger.debug("nom_valide: {!r}", nom)
    valide = bool(nom) and "/" not in nom and "\0" not in nom and nom not in (".", "..")
    logger.debug("nom_valide: {}", valide)
    return valide


def taille_lisible(octets: int) -> str:
    """``512 B``, ``1.5 KiB``, ``3.0 MiB``... (unités binaires, traduites)."""
    logger.debug("taille_lisible: {}", octets)
    unites = (_("B"), _("KiB"), _("MiB"), _("GiB"))
    valeur, rang = float(octets), 0
    while valeur >= 1024 and rang < len(unites) - 1:
        valeur /= 1024
        rang += 1
    texte = f"{octets} {unites[0]}" if rang == 0 else f"{valeur:.1f} {unites[rang]}"
    logger.debug("taille_lisible: {}", texte)
    return texte


def destination_libre(dossier: Path, nom: str) -> Path:
    """``dossier/nom``, ou ``nom (1).ext``, ``nom (2).ext``... si le fichier existe."""
    logger.debug("destination_libre: {} {!r}", dossier, nom)
    candidat = dossier / nom
    racine, extension = os.path.splitext(nom)
    numero = 1
    while candidat.exists():
        candidat = dossier / f"{racine} ({numero}){extension}"
        numero += 1
    logger.debug("destination_libre: {}", candidat)
    return candidat


class _Refuser(paramiko.MissingHostKeyPolicy):
    """Clé inconnue : lève :class:`CleHoteInconnue` au lieu de l'accepter."""

    def missing_host_key(self, client, hostname, key) -> None:  # noqa: ANN001 (API paramiko)
        """Toujours refuser ; l'onglet demande à l'utilisateur."""
        logger.debug("_Refuser.missing_host_key: {!r}", hostname)
        raise CleHoteInconnue(hostname, key)


class ConnexionSftp:
    """Session SFTP sur un hôte : à ouvrir par :meth:`ouvrir`, puis à fermer."""

    def __init__(self, hote: str, port: int | None, utilisateur: str, known_hosts: Path) -> None:
        """Ne se connecte pas encore.

        Args:
            hote: adresse du serveur.
            port: port SSH ; ``None`` : 22.
            utilisateur: nom de connexion ; vide : celui de la session locale.
            known_hosts: fichier des clés d'hôte connues.
        """
        logger.debug("ConnexionSftp.__init__: {!r} {} {!r}", hote, port, utilisateur)
        self.hote = hote
        self.port = port or PORT_DEFAUT
        self.utilisateur = utilisateur or _utilisateur_local()
        self.known_hosts = known_hosts
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None
        logger.debug("ConnexionSftp.__init__: fait")

    @property
    def nom(self) -> str:
        """Nom de l'hôte dans ``known_hosts``."""
        logger.debug("ConnexionSftp.nom: entrée")
        nom = nom_known_hosts(self.hote, self.port)
        logger.debug("ConnexionSftp.nom: {!r}", nom)
        return nom

    @property
    def ouverte(self) -> bool:
        """Vrai entre :meth:`ouvrir` réussi et :meth:`fermer`."""
        logger.debug("ConnexionSftp.ouverte: entrée")
        etat = self._sftp is not None
        logger.debug("ConnexionSftp.ouverte: {}", etat)
        return etat

    def ouvrir(
        self,
        mot_de_passe: str | None = None,
        fichier_cle: str | None = None,
        delai: float = DELAI_CONNEXION_S,
    ) -> str:
        """Connexion SSH puis sous-système SFTP.

        Returns:
            Le dossier courant distant (dossier d'accueil, chemin absolu).

        Raises:
            CleHoteInconnue: clé absente de ``known_hosts``.
            CleHoteChangee: clé différente de celle de ``known_hosts``.
            AuthentificationRefusee: identifiants refusés.
            ErreurSftp: serveur injoignable, SFTP indisponible...
        """
        logger.debug(
            "ConnexionSftp.ouvrir: {!r} mot de passe={} clé={!r}",
            self.nom,
            bool(mot_de_passe),
            fichier_cle,
        )
        client = paramiko.SSHClient()
        if self.known_hosts.is_file():
            client.load_host_keys(str(self.known_hosts))
        client.set_missing_host_key_policy(_Refuser())
        try:
            client.connect(
                self.hote,
                port=self.port,
                username=self.utilisateur,
                password=mot_de_passe or None,
                key_filename=fichier_cle or None,
                timeout=delai,
                banner_timeout=delai,
                auth_timeout=delai,
                allow_agent=not mot_de_passe,
                look_for_keys=not mot_de_passe,
            )
            sftp = client.open_sftp()
            dossier = sftp.normalize(".")
        except CleHoteInconnue:
            logger.debug("ConnexionSftp.ouvrir: clé d'hôte inconnue")
            client.close()
            raise
        except paramiko.BadHostKeyException as exc:
            logger.warning("SFTP {!r} : clé d'hôte différente de known_hosts", self.nom)
            client.close()
            raise CleHoteChangee(self.nom, exc.key) from None
        except paramiko.AuthenticationException as exc:
            logger.debug("ConnexionSftp.ouvrir: authentification refusée ({})", exc)
            client.close()
            raise AuthentificationRefusee(str(exc) or "authentication failed") from None
        except paramiko.SSHException as exc:
            logger.debug("ConnexionSftp.ouvrir: échec SSH {!r}", exc)
            client.close()
            if str(exc) == SANS_METHODE:
                raise AuthentificationRefusee(str(exc)) from None
            raise ErreurSftp(str(exc) or type(exc).__name__) from None
        except (OSError, EOFError) as exc:
            logger.debug("ConnexionSftp.ouvrir: échec {!r}", exc)
            client.close()
            raise ErreurSftp(str(exc) or type(exc).__name__) from None
        self._client = client
        self._sftp = sftp
        logger.debug("ConnexionSftp.ouvrir: ouverte, dossier {!r}", dossier)
        return dossier

    def _canal(self) -> paramiko.SFTPClient:
        """Client SFTP ouvert, ou :class:`ErreurSftp`."""
        logger.debug("ConnexionSftp._canal: entrée")
        if self._sftp is None:
            logger.debug("ConnexionSftp._canal: fermée")
            raise ErreurSftp("not connected")
        logger.debug("ConnexionSftp._canal: fait")
        return self._sftp

    def normaliser(self, chemin: str) -> str:
        """Chemin absolu côté serveur (``~``, ``..`` et liens résolus par le serveur)."""
        logger.debug("ConnexionSftp.normaliser: {!r}", chemin)
        sftp = self._canal()
        try:
            resultat = sftp.normalize(chemin or ".")
        except OSError as exc:
            logger.debug("ConnexionSftp.normaliser: échec {!r}", exc)
            raise ErreurSftp(_message(exc, chemin)) from None
        logger.debug("ConnexionSftp.normaliser: {!r}", resultat)
        return resultat

    def lister(self, dossier: str) -> list[Entree]:
        """Contenu de ``dossier`` : dossiers d'abord, puis par nom sans casse.

        Un lien vers un dossier compte comme un dossier ; un lien cassé comme
        un fichier.
        """
        logger.debug("ConnexionSftp.lister: {!r}", dossier)
        sftp = self._canal()
        try:
            attributs = sftp.listdir_attr(dossier)
        except OSError as exc:
            logger.debug("ConnexionSftp.lister: échec {!r}", exc)
            raise ErreurSftp(_message(exc, dossier)) from None
        entrees = []
        for attr in attributs:
            chemin = joindre(dossier, attr.filename)
            mode = attr.st_mode or 0
            lien = stat.S_ISLNK(mode)
            est_dossier = stat.S_ISDIR(mode)
            if lien:
                try:
                    est_dossier = stat.S_ISDIR(sftp.stat(chemin).st_mode or 0)
                except OSError as exc:
                    logger.debug("ConnexionSftp.lister: lien cassé {!r} ({!r})", chemin, exc)
            entrees.append(
                Entree(
                    nom=attr.filename,
                    chemin=chemin,
                    taille=attr.st_size or 0,
                    modifie=attr.st_mtime or 0,
                    dossier=est_dossier,
                    lien=lien,
                    mode=mode,
                )
            )
        entrees.sort(key=lambda e: (not e.dossier, e.nom.casefold(), e.nom))
        logger.debug("ConnexionSftp.lister: {} entrées", len(entrees))
        return entrees

    def telecharger(
        self, distant: str, local: Path, progression: Progression | None = None
    ) -> Path:
        """Copie le fichier ``distant`` vers ``local`` ; un fichier partiel est supprimé."""
        logger.debug("ConnexionSftp.telecharger: {!r} -> {}", distant, local)
        sftp = self._canal()
        try:
            sftp.get(distant, str(local), callback=progression)
        except OSError as exc:
            logger.debug("ConnexionSftp.telecharger: échec {!r}", exc)
            local.unlink(missing_ok=True)
            raise ErreurSftp(_message(exc, distant)) from None
        logger.debug("ConnexionSftp.telecharger: fait")
        return local

    def envoyer(self, local: Path, dossier: str, progression: Progression | None = None) -> str:
        """Copie le fichier ``local`` dans ``dossier`` (même nom) ; rend le chemin distant."""
        logger.debug("ConnexionSftp.envoyer: {} -> {!r}", local, dossier)
        sftp = self._canal()
        distant = joindre(dossier, local.name)
        try:
            sftp.put(str(local), distant, callback=progression)
        except OSError as exc:
            logger.debug("ConnexionSftp.envoyer: échec {!r}", exc)
            raise ErreurSftp(_message(exc, distant)) from None
        logger.debug("ConnexionSftp.envoyer: {!r}", distant)
        return distant

    def creer_dossier(self, dossier: str, nom: str) -> str:
        """Crée ``dossier/nom`` ; rend son chemin."""
        logger.debug("ConnexionSftp.creer_dossier: {!r} {!r}", dossier, nom)
        sftp = self._canal()
        chemin = joindre(dossier, nom)
        try:
            sftp.mkdir(chemin)
        except OSError as exc:
            logger.debug("ConnexionSftp.creer_dossier: échec {!r}", exc)
            raise ErreurSftp(_message(exc, chemin)) from None
        logger.debug("ConnexionSftp.creer_dossier: fait")
        return chemin

    def renommer(self, entree: Entree, nom: str) -> str:
        """Renomme ``entree`` dans son dossier ; rend le nouveau chemin."""
        logger.debug("ConnexionSftp.renommer: {!r} -> {!r}", entree.chemin, nom)
        sftp = self._canal()
        chemin = joindre(parent(entree.chemin), nom)
        try:
            sftp.rename(entree.chemin, chemin)
        except OSError as exc:
            logger.debug("ConnexionSftp.renommer: échec {!r}", exc)
            raise ErreurSftp(_message(exc, entree.chemin)) from None
        logger.debug("ConnexionSftp.renommer: fait")
        return chemin

    def supprimer(self, entree: Entree) -> None:
        """Supprime un fichier, un lien, ou un dossier **vide** (pas de suppression récursive)."""
        logger.debug("ConnexionSftp.supprimer: {!r}", entree.chemin)
        sftp = self._canal()
        try:
            if entree.dossier and not entree.lien:
                sftp.rmdir(entree.chemin)
            else:
                sftp.remove(entree.chemin)
        except OSError as exc:
            logger.debug("ConnexionSftp.supprimer: échec {!r}", exc)
            raise ErreurSftp(_message(exc, entree.chemin)) from None
        logger.debug("ConnexionSftp.supprimer: fait")

    def fermer(self) -> None:
        """Ferme SFTP puis SSH ; sans effet si déjà fermée."""
        logger.debug("ConnexionSftp.fermer: {!r}", self.nom)
        sftp, client = self._sftp, self._client
        self._sftp = self._client = None
        if sftp is not None:
            sftp.close()
        if client is not None:
            client.close()
        logger.debug("ConnexionSftp.fermer: fait")


def _message(exc: OSError, chemin: str) -> str:
    """``chemin: raison`` (raison du serveur, ou nom de l'exception)."""
    logger.debug("_message: {!r} {!r}", chemin, exc)
    raison = exc.strerror or (str(exc.args[0]) if exc.args else "") or type(exc).__name__
    texte = f"{chemin}: {raison}"
    logger.debug("_message: {!r}", texte)
    return texte


def _utilisateur_local() -> str:
    """Nom de la session locale (comme ``ssh`` sans ``-l``)."""
    logger.debug("_utilisateur_local: entrée")
    try:
        nom = getpass.getuser()
    except (KeyError, OSError) as exc:
        logger.debug("_utilisateur_local: inconnu ({!r})", exc)
        nom = ""
    logger.debug("_utilisateur_local: {!r}", nom)
    return nom
