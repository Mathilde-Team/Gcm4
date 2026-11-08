# SPDX-License-Identifier: GPL-3.0-or-later
"""Vrais PTY : terminal de contrôle, FD3, encodage, taille, statut et fermeture."""

from __future__ import annotations

import errno
import os
import signal
import sys
import termios
import time

import pytest

from gcm4.core import pty_child, pty_process
from gcm4.core.pty_process import ProcessusPty
from gcm4.core.terminal import Lancement


def attendre(processus, condition, delai=10):
    """Draine le vrai PTY et attend une condition bornée dans le temps."""
    fin = time.monotonic() + delai
    sortie = b""
    while time.monotonic() < fin:
        processus.pret()
        processus.envoyer_secret()
        if not processus.eof:
            sortie += processus.read()
        statut = processus.poll()
        if condition(sortie, statut):
            return sortie, statut
        time.sleep(0.005)
    pytest.fail(f"PTY : délai dépassé, {len(sortie)} octets, statut={statut}")


@pytest.fixture
def creer():
    """Récolte tous les enfants, même en cas d'assertion échouée."""
    processus = []

    def nouveau(code, secret="", env=(), envv=None):
        p = ProcessusPty(
            Lancement([sys.executable, "-c", code], secret, env), envv or ["TERM=xterm"]
        )
        processus.append(p)
        return p

    yield nouveau
    for p in processus:
        if p.poll() is None:
            p.signaler(signal.SIGKILL)
            p.process.wait(timeout=5)
        p.close()


def test_vrai_terminal_et_statut(creer):
    """TTY de contrôle, environnement hérité et statut waitpid exact."""
    p = creer(
        "import os; print('tty', os.isatty(0), os.tcgetpgrp(0)==os.getpgrp()); "
        "print('fd3', os.path.exists('/proc/self/fd/3')); "
        "print(os.environ['TERM'], os.environ['GCM_PTY_TEST']); raise SystemExit(3)",
        env=("GCM_PTY_TEST=valeur",),
        envv=["TERM=vt220"],
    )
    sortie, statut = attendre(p, lambda _s, r: r is not None and p.eof)
    assert b"tty True True" in sortie
    assert b"fd3 False" in sortie
    assert b"vt220 valeur" in sortie
    assert statut == 3 << 8
    p.close()
    p.close()
    assert p.fd == p.secret_fd == -1


def test_secret_long_prive_hors_argv_et_env(creer, capsys):
    """Un FD3 supérieur à la capacité du tube reste non bloquant et privé."""
    p = creer(
        "import os; s=os.fdopen(3).read(); print('secret-long', len(s)); "
        "print('aucun-secret-argv', all('confidentiel' not in a for a in __import__('sys').argv)); "
        "print('aucun-secret-env', all('confidentiel' not in v for v in os.environ.values()))",
        secret="confidentiel" * 20000,
    )
    sortie, statut = attendre(p, lambda _s, r: r is not None and p.eof)
    assert b"secret-long 240001" in sortie
    assert b"aucun-secret-argv True" in sortie and b"aucun-secret-env True" in sortie
    assert statut == 0
    assert p.secret_fd == -1 and not p._secret
    assert "confidentiel" not in capsys.readouterr().err


def test_entree_et_redimensionnement(creer):
    """Unicode et dimensions de l'esclave passent par le noyau."""
    p = creer(
        "import os; print('pret', flush=True); s=input(); "
        "print('recu', repr(s), os.get_terminal_size(0), flush=True)"
    )
    attendre(p, lambda s, _r: b"pret" in s)
    assert p.erase() == b"\x7f"
    reglages = termios.tcgetattr(p.fd)
    reglages[6][termios.VERASE] = b"\x08"
    termios.tcsetattr(p.fd, termios.TCSANOW, reglages)
    assert p.erase() == b"\x08"
    assert p.read() == b"" and not p.eof
    p.resize(101, 37)
    assert p.write("été\n".encode()) > 0
    sortie, statut = attendre(p, lambda _s, r: r is not None and p.eof)
    assert "recu 'été'".encode() in sortie
    assert b"columns=101, lines=37" in sortie and statut == 0


def test_sighup_et_statut_signal(creer):
    """La fermeture signale la session et restitue le signal brut."""
    p = creer("import time; print('pret', flush=True); time.sleep(60)")
    attendre(p, lambda s, _r: b"pret" in s)
    p.signaler()
    _, statut = attendre(p, lambda _s, r: r is not None and p.eof)
    assert statut == signal.SIGHUP
    p.signaler()


@pytest.mark.parametrize("argv", [[], ["/programme/gcm4-absent"]])
def test_programme_absent(argv):
    """L'échec de résolution n'alloue pas de PTY."""
    with pytest.raises(FileNotFoundError):
        ProcessusPty(Lancement(argv), [])


def test_echec_spawn_libere_les_fd(monkeypatch):
    """Un échec de création ne laisse aucun descripteur ouvert."""
    avant = set(os.listdir("/proc/self/fd"))

    def refuser(*_a, **_kw):
        raise OSError(errno.EAGAIN, "spawn refusé")

    monkeypatch.setattr(pty_process.subprocess, "Popen", refuser)
    with pytest.raises(OSError, match="spawn refusé"):
        ProcessusPty(Lancement([sys.executable], "secret"), [])
    assert set(os.listdir("/proc/self/fd")) == avant


def test_io_partielles_et_erreurs(creer, monkeypatch):
    """Saturation, fin de tube et erreurs ne perdent pas leurs distinctions."""
    p = creer("import time; time.sleep(60)", secret="secret")
    ecrire = os.write
    lire = os.read

    def plein(*_args):
        raise BlockingIOError()

    monkeypatch.setattr(pty_process.os, "write", plein)
    assert not p.envoyer_secret() and p._secret
    assert p.write(b"x") == 0
    monkeypatch.setattr(pty_process.os, "read", plein)
    assert p.read() == b"" and not p.eof

    def brise(*_args):
        raise BrokenPipeError()

    monkeypatch.setattr(pty_process.os, "write", brise)
    assert p.envoyer_secret() and not p._secret
    monkeypatch.setattr(pty_process.os, "write", ecrire)

    def erreur(*_args):
        raise OSError(errno.EBADF, "fd invalide")

    monkeypatch.setattr(pty_process.os, "read", erreur)
    with pytest.raises(OSError, match="fd invalide"):
        p.read()
    monkeypatch.setattr(pty_process.os, "read", lambda *_a: b"")
    assert p.read() == b"" and p.eof
    monkeypatch.setattr(pty_process.os, "read", lire)
    monkeypatch.setattr(pty_process.os, "tcgetpgrp", erreur)
    p.signaler()


@pytest.mark.parametrize("fd", [-1, 3, 5])
def test_amorce_sans_exec_reel(monkeypatch, fd):
    """Vérifie l'acquisition du TTY et le remappage exact sur FD3."""
    appels = []
    monkeypatch.setattr(pty_child.fcntl, "ioctl", lambda *a: appels.append(("ioctl", a)))
    monkeypatch.setattr(pty_child.os, "dup2", lambda *a, **kw: appels.append(("dup", a, kw)))
    monkeypatch.setattr(pty_child.os, "set_inheritable", lambda *a: appels.append(("herit", a)))
    monkeypatch.setattr(pty_child.os, "close", lambda *a: appels.append(("close", a)))
    monkeypatch.setattr(pty_child.os, "execvpe", lambda *a: appels.append(("exec", a)))
    pty_child.main([str(fd), "9", "/bin/programme", "argument"])
    assert appels[0] == ("herit", (9, False))
    assert appels[1][0] == "ioctl"
    assert appels[-1][0] == "exec"
    assert any(a[0] == "dup" for a in appels) == (fd >= 0)
    assert any(a[0] == "close" for a in appels) == (fd == 5)


def test_amorce_exec_refuse(monkeypatch):
    """Un programme disparu se termine en 127 sans exposer ses arguments."""

    def absent(*_args):
        raise FileNotFoundError(errno.ENOENT, "absent")

    messages = []
    monkeypatch.setattr(pty_child.fcntl, "ioctl", lambda *_a: None)
    monkeypatch.setattr(pty_child.os, "execvpe", absent)
    monkeypatch.setattr(pty_child.os, "set_inheritable", lambda *_a: None)
    monkeypatch.setattr(pty_child.os, "write", lambda _fd, texte: messages.append(texte))
    with pytest.raises(SystemExit) as exc:
        pty_child.main(["-1", "9", "/absent"])
    assert exc.value.code == 127 and b"No such file" in messages[1]


def test_amorce_tty_refuse_signale_avant_exec(monkeypatch):
    """Un échec d'acquisition du TTY ne ressemble jamais à une confirmation d'exec."""
    messages = []

    def refuser(*_args):
        raise OSError(errno.ENOTTY, os.strerror(errno.ENOTTY))

    monkeypatch.setattr(pty_child.fcntl, "ioctl", refuser)
    monkeypatch.setattr(pty_child.os, "set_inheritable", lambda *_a: None)
    monkeypatch.setattr(
        pty_child.os, "execvpe", lambda *_a: pytest.fail("exec ne doit pas être appelé")
    )
    monkeypatch.setattr(pty_child.os, "write", lambda fd, texte: messages.append((fd, texte)))
    with pytest.raises(SystemExit) as exc:
        pty_child.main(["-1", "9", "/programme"])
    assert exc.value.code == 127
    assert messages[0] == (9, str(errno.ENOTTY).encode("ascii"))
    assert os.strerror(errno.ENOTTY).encode() in messages[1][1]


def test_confirmation_exec_erreur_partielle(creer, monkeypatch):
    """Le tube distingue une amorce active, un errno partiel et son EOF."""
    p = creer("import time; time.sleep(60)")
    valeurs = iter([None, b"2", b""])

    def lire(*_args):
        valeur = next(valeurs)
        if valeur is None:
            raise BlockingIOError()
        return valeur

    with monkeypatch.context() as m:
        m.setattr(pty_process.os, "read", lire)
        assert not p.pret()
        assert not p.pret()
        with pytest.raises(FileNotFoundError):
            p.pret()
    assert p.exec_fd == -1


def test_vrai_exec_invalide(tmp_path):
    """Un fichier exécutable sans format valide est signalé, puis l'enfant récolté."""
    fichier = tmp_path / "executable-invalide"
    fichier.write_text("ceci n'est pas un executable")
    fichier.chmod(0o700)
    p = ProcessusPty(Lancement([str(fichier)]), [])
    try:
        with pytest.raises(OSError) as exc:
            attendre(p, lambda _s, _r: False)
        assert exc.value.errno == errno.ENOEXEC
        p.process.wait(timeout=5)
        assert p.poll() == 127 << 8
    finally:
        p.close()
