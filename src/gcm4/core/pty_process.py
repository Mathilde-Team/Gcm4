# SPDX-License-Identifier: GPL-3.0-or-later
"""Processus POSIX sous PTY, indépendant de GTK et de l'émulateur (#76)."""

from __future__ import annotations

import errno
import fcntl
import os
import shutil
import signal
import struct
import subprocess
import sys
import termios
from pathlib import Path

from loguru import logger

from gcm4.core.terminal import Lancement


class ProcessusPty:
    """PTY non bloquant ; FD3 privé, groupe de session distinct, statut waitpid.

    Le lecteur de FD3 est transmis à l'amorce puis immédiatement fermé
    dans le parent. Son écriture est non bloquante : même un secret long
    ne peut pas figer GTK. L'appelant surveille ``secret_fd`` en écriture,
    ``fd`` en lecture, et appelle ``poll`` pour récolter le processus.
    """

    def __init__(self, lancement: Lancement, envv: list[str]) -> None:
        """Lance sans shell et sans preexec_fn ; aucun secret n'est journalisé."""
        logger.debug("ProcessusPty.__init__: entrée")
        env = dict(os.environ)
        for item in [*envv, *lancement.env]:
            cle, valeur = item.split("=", 1)
            env[cle] = valeur
        if not lancement.argv or shutil.which(lancement.argv[0], path=env.get("PATH")) is None:
            logger.debug("ProcessusPty.__init__: programme absent")
            raise FileNotFoundError(
                errno.ENOENT,
                os.strerror(errno.ENOENT),
                lancement.argv[0] if lancement.argv else None,
            )
        self.fd = -1
        self.secret_fd = -1
        self.exec_fd = -1
        self._exec_error = bytearray()
        self.programme = lancement.argv[0]
        self._secret = b""
        self.eof = False
        self._ferme = False
        maitre, esclave = os.openpty()
        lecture = -1
        exec_ecriture = -1
        try:
            self.fd = maitre
            self.resize(80, 24)
            if lancement.mot_de_passe:
                lecture, self.secret_fd = os.pipe()
                os.set_blocking(self.secret_fd, False)
                self._secret = lancement.mot_de_passe.encode("utf-8") + b"\n"
            self.exec_fd, exec_ecriture = os.pipe()
            os.set_blocking(self.exec_fd, False)
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("pty_child.py")),
                    str(lecture),
                    str(exec_ecriture),
                    *lancement.argv,
                ],
                stdin=esclave,
                stdout=esclave,
                stderr=esclave,
                env=env,
                start_new_session=True,
                pass_fds=(lecture, exec_ecriture) if lecture >= 0 else (exec_ecriture,),
            )
            os.set_blocking(maitre, False)
        except BaseException:
            logger.debug("ProcessusPty.__init__: lancement refusé")
            self.close()
            logger.debug("ProcessusPty.__init__: exception propagée")
            raise
        finally:
            os.close(esclave)
            if lecture >= 0:
                os.close(lecture)
            if exec_ecriture >= 0:
                os.close(exec_ecriture)
        logger.debug("ProcessusPty.__init__: pid={}", self.process.pid)

    def pret(self) -> bool:
        """EOF du tube close-on-exec : le programme a remplacé l'amorce avec succès."""
        logger.trace("ProcessusPty.pret: entrée")
        if self.exec_fd >= 0:
            try:
                donnees = os.read(self.exec_fd, 64)
            except BlockingIOError:
                logger.trace("ProcessusPty.pret: amorce en cours")
                return False
            self._exec_error.extend(donnees)
            if donnees:
                logger.trace("ProcessusPty.pret: erreur à récolter")
                return False
            os.close(self.exec_fd)
            self.exec_fd = -1
            if self._exec_error:
                numero = int(self._exec_error)
                logger.debug("ProcessusPty.pret: exec refusé ({})", numero)
                raise OSError(numero, os.strerror(numero), self.programme)
        logger.trace("ProcessusPty.pret: prêt")
        return True

    def envoyer_secret(self) -> bool:
        """Écrit ce qui tient dans le tube ; vrai quand FD3 a reçu tout le secret."""
        logger.trace("ProcessusPty.envoyer_secret: entrée")
        if self.secret_fd >= 0:
            try:
                n = os.write(self.secret_fd, self._secret)
                self._secret = self._secret[n:]
            except BlockingIOError:
                logger.trace("ProcessusPty.envoyer_secret: tube plein")
            except BrokenPipeError:
                logger.debug("ProcessusPty.envoyer_secret: destinataire fermé")
                self._secret = b""
            if not self._secret:
                os.close(self.secret_fd)
                self.secret_fd = -1
        logger.trace("ProcessusPty.envoyer_secret: terminé={}", self.secret_fd < 0)
        return self.secret_fd < 0

    def read(self) -> bytes:
        """Lit au plus 64 Kio ; EOF et EIO de l'esclave fermé sont équivalents."""
        logger.trace("ProcessusPty.read: entrée")
        try:
            resultat = os.read(self.fd, 65536)
            self.eof = not resultat
        except BlockingIOError:
            logger.trace("ProcessusPty.read: rien de disponible")
            resultat = b""
        except OSError as exc:
            logger.debug("ProcessusPty.read: erreur ({})", exc.errno)
            if exc.errno != errno.EIO:
                logger.debug("ProcessusPty.read: exception propagée")
                raise
            self.eof = True
            resultat = b""
        logger.trace("ProcessusPty.read: {} octet(s), eof={}", len(resultat), self.eof)
        return resultat

    def write(self, donnees: bytes) -> int:
        """Écriture partielle non bloquante, sans journaliser le contenu."""
        logger.trace("ProcessusPty.write: {} octet(s)", len(donnees))
        try:
            n = os.write(self.fd, donnees)
        except BlockingIOError:
            logger.trace("ProcessusPty.write: file pleine")
            n = 0
        logger.trace("ProcessusPty.write: {} octet(s) écrits", n)
        return n

    def resize(self, colonnes: int, lignes: int) -> None:
        """Transmet la taille et le SIGWINCH du noyau au groupe au premier plan."""
        logger.trace("ProcessusPty.resize: {} × {}", colonnes, lignes)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", lignes, colonnes, 0, 0))
        logger.trace("ProcessusPty.resize: fait")

    def erase(self) -> bytes:
        """Caractère VERASE effectif, y compris après un changement par stty."""
        logger.trace("ProcessusPty.erase: entrée")
        valeur = termios.tcgetattr(self.fd)[6][termios.VERASE]
        resultat = valeur if isinstance(valeur, bytes) else bytes([valeur])
        logger.trace("ProcessusPty.erase: fait")
        return resultat

    def poll(self) -> int | None:
        """Récolte l'enfant et restitue le statut brut attendu par les sessions."""
        logger.trace("ProcessusPty.poll: entrée")
        code = self.process.poll()
        statut = None if code is None else (-code if code < 0 else code << 8)
        logger.trace("ProcessusPty.poll: statut={}", statut)
        return statut

    def signaler(self, numero: int = signal.SIGHUP) -> None:
        """Signale le groupe du programme et celui du premier plan s'il diffère."""
        logger.debug("ProcessusPty.signaler: signal={}", numero)
        groupes = {self.process.pid}
        try:
            groupes.add(os.tcgetpgrp(self.fd))
        except OSError as exc:
            logger.debug("ProcessusPty.signaler: premier plan indisponible ({})", exc.errno)
        for groupe in groupes:
            if groupe > 0 and groupe != os.getpgrp():
                try:
                    os.killpg(groupe, numero)
                except ProcessLookupError:
                    logger.debug("ProcessusPty.signaler: groupe déjà terminé")
        logger.debug("ProcessusPty.signaler: fait")

    def close(self) -> None:
        """Libère les FD, même après un lancement partiel ; ne récolte pas l'enfant."""
        logger.debug("ProcessusPty.close: entrée")
        if not self._ferme:
            self._ferme = True
            for fd in (self.fd, self.secret_fd, self.exec_fd):
                if fd >= 0:
                    os.close(fd)
            self.fd = self.secret_fd = self.exec_fd = -1
            self._secret = b""
        logger.debug("ProcessusPty.close: fait")
