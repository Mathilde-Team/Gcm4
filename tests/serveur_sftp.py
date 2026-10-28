# SPDX-License-Identifier: GPL-3.0-or-later
"""Serveur SSH/SFTP de test (#75) : paramiko côté serveur, sur 127.0.0.1, port libre.

Le protocole est le vrai (échange de clés, authentification, sous-système
SFTP de paramiko) ; seul le système de fichiers est restreint à une racine
temporaire, vue par le client comme ``/``. Dossier d'accueil : ``/accueil``.

Comptes : ``UTILISATEUR`` / ``MOT_DE_PASSE`` (mot de passe), et la clé
publique donnée à :class:`ServeurSftp` (clé), pour tester les deux voies.
Inspiré du ``StubSFTPServer`` des tests de paramiko.
"""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path

import paramiko
import pytest

UTILISATEUR = "alice"
MOT_DE_PASSE = "s3cret-de-test"
ACCUEIL = "/accueil"


class _Serveur(paramiko.ServerInterface):
    """Authentification par mot de passe ou par la clé autorisée."""

    def __init__(self, cle_autorisee: paramiko.PKey | None) -> None:
        self.cle_autorisee = cle_autorisee

    def check_channel_request(self, kind, chanid):  # noqa: ANN001 (API paramiko)
        return (
            paramiko.OPEN_SUCCEEDED
            if kind == "session"
            else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        )

    def get_allowed_auths(self, username):  # noqa: ANN001
        return "password,publickey"

    def check_auth_password(self, username, password):  # noqa: ANN001
        ok = username == UTILISATEUR and password == MOT_DE_PASSE
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):  # noqa: ANN001
        ok = (
            username == UTILISATEUR
            and self.cle_autorisee is not None
            and key.asbytes() == self.cle_autorisee.asbytes()
        )
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED


class _Fichier(paramiko.SFTPHandle):
    """Fichier ouvert : lecture et écriture déléguées au fichier local."""

    def stat(self):
        try:
            return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)


class _Sftp(paramiko.SFTPServerInterface):
    """Système de fichiers sous ``racine`` (attribut posé par :class:`ServeurSftp`)."""

    racine = "/"

    def _local(self, chemin: str) -> str:
        """Chemin local d'un chemin distant, sans sortir de la racine."""
        propre = os.path.normpath("/" + self.canonicalize(chemin)).lstrip("/")
        return os.path.join(self.racine, propre)

    def canonicalize(self, path):  # noqa: ANN001
        if path in ("", "."):
            return ACCUEIL
        if not path.startswith("/"):
            path = ACCUEIL + "/" + path
        return os.path.normpath(path).replace("//", "/")

    def list_folder(self, path):  # noqa: ANN001
        local = self._local(path)
        try:
            resultat = []
            for nom in os.listdir(local):
                attr = paramiko.SFTPAttributes.from_stat(os.lstat(os.path.join(local, nom)))
                attr.filename = nom
                resultat.append(attr)
            return resultat
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def stat(self, path):  # noqa: ANN001
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._local(path)))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def lstat(self, path):  # noqa: ANN001
        try:
            return paramiko.SFTPAttributes.from_stat(os.lstat(self._local(path)))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def open(self, path, flags, attr):  # noqa: ANN001
        local = self._local(path)
        try:
            descripteur = os.open(local, flags | getattr(os, "O_BINARY", 0), 0o644)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        if flags & os.O_WRONLY:
            mode = "ab" if flags & os.O_APPEND else "wb"
        elif flags & os.O_RDWR:
            mode = "a+b" if flags & os.O_APPEND else "r+b"
        else:
            mode = "rb"
        flux = os.fdopen(descripteur, mode)
        poignee = _Fichier(flags)
        poignee.filename = local
        poignee.readfile = flux
        poignee.writefile = flux
        return poignee

    def remove(self, path):  # noqa: ANN001
        try:
            os.remove(self._local(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def rename(self, oldpath, newpath):  # noqa: ANN001
        try:
            os.rename(self._local(oldpath), self._local(newpath))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def mkdir(self, path, attr):  # noqa: ANN001
        try:
            os.mkdir(self._local(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def rmdir(self, path):  # noqa: ANN001
        try:
            os.rmdir(self._local(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        return paramiko.SFTP_OK

    def readlink(self, path):  # noqa: ANN001
        try:
            return os.readlink(self._local(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)


class ServeurSftp:
    """Serveur en fil d'arrière-plan ; :meth:`arreter` ferme tout."""

    def __init__(self, racine: Path, cle_hote: paramiko.PKey, cle_autorisee=None) -> None:
        """Écoute sur 127.0.0.1, port libre (``self.port``) ; ``racine/accueil`` est créé."""
        self.racine = racine
        (racine / ACCUEIL.lstrip("/")).mkdir(parents=True, exist_ok=True)
        self.cle_hote = cle_hote
        self.cle_autorisee = cle_autorisee
        self.connexions = 0
        self._transports: list[paramiko.Transport] = []
        self._ecoute = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ecoute.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._ecoute.bind(("127.0.0.1", 0))
        self._ecoute.listen(8)
        self.port = self._ecoute.getsockname()[1]
        self._fil = threading.Thread(target=self._accepter, daemon=True)
        self._fil.start()

    def _accepter(self) -> None:
        """Une connexion après l'autre, jusqu'à la fermeture de la socket d'écoute."""
        while True:
            try:
                connexion, _adresse = self._ecoute.accept()
            except OSError:
                return
            self.connexions += 1
            transport = paramiko.Transport(connexion)
            transport.add_server_key(self.cle_hote)
            racine = str(self.racine)
            classe = type("_SftpRacine", (_Sftp,), {"racine": racine})
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, classe)
            self._transports.append(transport)
            try:
                transport.start_server(server=_Serveur(self.cle_autorisee))
            except (paramiko.SSHException, EOFError, OSError):
                continue

    def arreter(self) -> None:
        """Ferme la socket d'écoute (``shutdown`` réveille ``accept``) et toutes les connexions."""
        try:
            self._ecoute.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._ecoute.close()
        self._fil.join(5)
        for transport in self._transports:
            transport.close()


# ── Fixtures (importées par les modules de test) ────────────────────────


@pytest.fixture(scope="session")
def cles_sftp():
    """Clé d'hôte, clé de l'utilisateur et une autre clé d'hôte (RSA 2048, générées une fois)."""
    return tuple(paramiko.RSAKey.generate(2048) for _ in range(3))


@pytest.fixture
def maison(tmp_path, monkeypatch):
    """``HOME`` temporaire, sans agent SSH : rien de la machine ne se mêle au test."""
    dossier = tmp_path / "maison"
    dossier.mkdir()
    monkeypatch.setenv("HOME", str(dossier))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    return dossier


@pytest.fixture
def serveur(tmp_path, cles_sftp):
    """Serveur démarré sur une racine temporaire ; arrêté après le test."""
    racine = tmp_path / "racine"
    racine.mkdir()
    instance = ServeurSftp(racine, cles_sftp[0], _publique(cles_sftp[1]))
    yield instance
    instance.arreter()


def _publique(cle: paramiko.PKey) -> paramiko.PKey:
    """Partie publique d'une clé (comparée par ``asbytes``)."""
    return paramiko.RSAKey(data=cle.asbytes())
