# SPDX-License-Identifier: GPL-3.0-or-later
"""Logique sans GTK du plugin ipmisol (``plugins/ipmisol/core.py``, #179)."""

from __future__ import annotations

import pytest

import plugins.ipmisol.core as core
from gcm4.core import terminal
from gcm4.core.model import Host


def _hote(**champs):
    """Hôte ``ipmi`` de test."""
    valeurs = {
        "name": "bmc",
        "host": "10.0.0.5",
        "port": None,
        "protocol": "ipmi",
        "user": "admin",
        "password": "s3cret",
        "extra_params": "",
    }
    valeurs.update(champs)
    return Host(**valeurs)


@pytest.mark.parametrize(
    ("port_hote", "attendu"),
    [(623, 623), (None, 623), (6230, 6230)],
)
def test_port(port_hote, attendu):
    """Port de l'hôte, 623 (UDP RMCP) par défaut comme GTK3."""
    assert core.port(_hote(port=port_hote)) == attendu


def test_chemin():
    """``ipmitool`` cherché dans le ``PATH`` donné ; absent : ``None``."""
    assert core.chemin({"PATH": "/usr/bin"}) is None or "ipmitool" in core.chemin(
        {"PATH": "/usr/bin"}
    )
    assert core.chemin({"PATH": ""}) is None


def test_lancement_argv_et_env():
    """``sol activate`` ; mot de passe dans ``IPMI_PASSWORD``, jamais dans ``argv``."""
    lancement = core.lancement("/usr/bin/ipmitool", _hote(extra_params="-C 17"))
    assert lancement.argv == [
        "/usr/bin/ipmitool",
        "-I",
        "lanplus",
        "-H",
        "10.0.0.5",
        "-p",
        "623",
        "-U",
        "admin",
        "-E",
        "-C",
        "17",
        "sol",
        "activate",
    ]
    assert lancement.env == ("IPMI_PASSWORD=s3cret",)
    assert lancement.mot_de_passe == ""
    assert "s3cret" not in repr(lancement)


def test_lancement_sans_mot_de_passe():
    """Sans mot de passe : pas de variable d'environnement, pas de descripteur 3."""
    lancement = core.lancement("/usr/bin/ipmitool", _hote(password=""))
    assert lancement.env == ()
    assert lancement.mot_de_passe == ""


def test_lancement_repr_ne_fuit_pas_le_mot_de_passe():
    """``repr`` de ``Lancement`` : ni le mot de passe ni les valeurs d'``env`` (#145)."""
    lancement = terminal.Lancement(["/usr/bin/ipmitool"], env=("IPMI_PASSWORD=s3cret",))
    assert "s3cret" not in repr(lancement)
    assert "IPMI_PASSWORD" not in repr(lancement)


def test_desactivation(tmp_path, monkeypatch):
    """``sol deactivate`` à la fermeture ; mot de passe dans l'environnement, pas en argument."""
    programme = tmp_path / "ipmitool"
    programme.write_text(
        "#!/bin/sh\n"
        'echo "argv=$@" >> "$FAUX_IPMITOOL_TRACE"\n'
        'if [ -n "$IPMI_PASSWORD" ]; then echo "IPMI_PASSWORD=present" >> "$FAUX_IPMITOOL_TRACE"; fi\n'
        'echo "deactivate" >> "$FAUX_IPMITOOL_TRACE"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    programme.chmod(0o755)
    trace = tmp_path / "trace"
    monkeypatch.setenv("FAUX_IPMITOOL_TRACE", str(trace))
    assert core.desactivation(str(programme), _hote())
    lignes = trace.read_text(encoding="utf-8").splitlines()
    assert "argv=-I lanplus -H 10.0.0.5 -p 623 -U admin -E sol deactivate" in lignes
    assert "IPMI_PASSWORD=present" in lignes
    assert "deactivate" in lignes


def test_desactivation_echec(tmp_path):
    """Échec d'ipmitool (code non nul) ou binaire introuvable : ``False``."""
    programme = tmp_path / "ipmitool"
    programme.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    programme.chmod(0o755)
    assert core.desactivation(str(programme), _hote()) is False
    assert core.desactivation(str(tmp_path / "absent"), _hote()) is False
