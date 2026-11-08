# SPDX-License-Identifier: GPL-3.0-or-later
"""Amorce isolée du PTY, sans ``preexec_fn`` dans le processus GTK multithread (#76).

Exécutée par le même interpréteur que gcm4, après ``start_new_session``.
FD0 est l'esclave du PTY ; le seul FD hérité supplémentaire, s'il existe,
est remappé sur FD3 avant exec. Aucun secret dans argv ni dans le journal.
"""

from __future__ import annotations

import fcntl
import os
import sys
import termios

from loguru import logger

# Le journal de cette amorce ne doit pas polluer le flux terminal du programme.
logger.disable(__name__)


def main(arguments: list[str]) -> None:
    """Acquiert le terminal de contrôle puis remplace l'amorce par le programme."""
    logger.debug("pty_child.main: entrée")
    exec_fd = int(arguments[1])
    try:
        os.set_inheritable(exec_fd, False)
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        fd = int(arguments[0])
        if fd >= 0:
            os.dup2(fd, 3, inheritable=True)
            os.set_inheritable(3, True)
            if fd != 3:
                os.close(fd)
        os.execvpe(arguments[2], arguments[2:], os.environ)
    except OSError as exc:
        logger.debug("pty_child.main: exec refusé ({})", exc.errno)
        os.write(exec_fd, str(exc.errno or 5).encode("ascii"))
        # Ni argv ni l'environnement ne sont reproduits dans l'erreur.
        os.write(2, f"exec: {os.strerror(exc.errno or 5)}\r\n".encode())
        logger.debug("pty_child.main: sortie 127")
        raise SystemExit(127) from None
    logger.debug("pty_child.main: remplacé")


if __name__ == "__main__":
    main(sys.argv[1:])
