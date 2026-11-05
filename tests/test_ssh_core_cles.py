# SPDX-License-Identifier: GPL-3.0-or-later
"""Clés de ``~/.ssh/`` pour le sélecteur de clé GTK4 (``plugins/ssh/core.py``, #97).

Logique sans GTK : parcours du dossier, empreinte via ``ssh-keygen -lf``,
permissions. Les vraies clés sont produites par ``ssh-keygen`` (présent
partout où gcm tourne) ; les échecs de ``ssh-keygen`` sont simulés.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from plugins.ssh import core


def _generer(dossier: Path, nom: str, mode: int = 0o600) -> Path:
    """Clé ed25519 sans phrase de passe ``dossier/nom`` (+ ``.pub``)."""
    chemin = dossier / nom
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", nom, "-f", str(chemin)], check=True
    )
    chemin.chmod(mode)
    return chemin


@pytest.fixture
def ssh(tmp_path: Path) -> Path:
    """Dossier ``.ssh`` vide."""
    dossier = tmp_path / ".ssh"
    dossier.mkdir(mode=0o700)
    return dossier


def test_dossier_absent_liste_vide(tmp_path: Path) -> None:
    """Dossier absent liste vide."""
    assert core.lister_cles(tmp_path / "absent") == []


def test_dossier_par_defaut_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ssh: Path
) -> None:
    """Dossier par defaut home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _generer(ssh, "id_ed25519")
    assert [c.nom for c in core.lister_cles()] == ["id_ed25519"]


def test_cle_privee_avec_publique(ssh: Path) -> None:
    """Cle privee avec publique."""
    prive = _generer(ssh, "id_ed25519")
    (cle,) = core.lister_cles(ssh)
    assert cle.chemin == prive and cle.publique == prive.with_name("id_ed25519.pub")
    assert cle.empreinte.startswith("SHA256:") and len(cle.empreinte) <= 32
    assert (cle.permissions, cle.permissions_ok, cle.est_publique) == ("600", True, False)
    assert core.chemin_prive(cle) == str(prive)


def test_fichiers_ignores_et_tri(ssh: Path) -> None:
    """Fichiers ignores et tri."""
    for nom in ("config", "known_hosts", "authorized_keys", "environment", ".cache"):
        (ssh / nom).write_text("x")
    (ssh / "sous-dossier").mkdir()
    _generer(ssh, "b_cle")
    _generer(ssh, "a_cle")
    assert [c.nom for c in core.lister_cles(ssh)] == ["a_cle", "b_cle"]


def test_publique_orpheline(ssh: Path) -> None:
    """Publique orpheline."""
    prive = _generer(ssh, "orpheline")
    prive.unlink()
    (cle,) = core.lister_cles(ssh)
    assert cle.nom == "orpheline.pub" and cle.publique is None and cle.est_publique
    assert cle.empreinte.startswith("SHA256:")
    assert core.chemin_prive(cle) == str(prive)


def test_privee_sans_publique_permissions_trop_larges(ssh: Path) -> None:
    """Privee sans publique permissions trop larges."""
    prive = _generer(ssh, "large", mode=0o644)
    prive.with_name("large.pub").unlink()
    (cle,) = core.lister_cles(ssh)
    assert cle.publique is None
    assert (cle.permissions, cle.permissions_ok) == ("644", False)


def test_permissions_illisibles(tmp_path: Path) -> None:
    """Permissions illisibles."""
    assert core.permissions_cle(tmp_path / "absente") == ("?", False)


def test_empreinte_ssh_keygen_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empreinte ssh keygen absent."""

    def absent(*_a, **_k):
        """Absent."""
        raise FileNotFoundError("ssh-keygen")

    monkeypatch.setattr(core.subprocess, "run", absent)
    assert core.empreinte_cle(tmp_path / "cle") == ""


def test_empreinte_fichier_qui_n_est_pas_une_cle(tmp_path: Path) -> None:
    """Empreinte fichier qui n est pas une cle."""
    faux = tmp_path / "pas_une_cle"
    faux.write_text("bonjour\n")
    assert core.empreinte_cle(faux) == ""


def test_empreinte_sortie_inattendue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empreinte sortie inattendue."""
    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout="256\n"),
    )
    assert core.empreinte_cle(tmp_path / "cle") == ""
