# SPDX-License-Identifier: GPL-3.0-or-later
"""Contrat du lanceur Wayland : erreurs propagées et ressources privées nettoyées."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("statut", [0, 7])
def test_lanceur_propage_statut_et_nettoie(tmp_path, statut):
    """Un compositeur simulé ne transforme jamais un client rouge en succès."""
    binaires = tmp_path / "bin"
    binaires.mkdir()
    weston = binaires / "weston"
    weston.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, socket\n"
        "s = socket.socket(socket.AF_UNIX)\n"
        "s.bind(os.path.join(os.environ['XDG_RUNTIME_DIR'], os.environ['WAYLAND_DISPLAY']))\n"
        "signal.pause()\n"
    )
    weston.chmod(0o755)
    client = binaires / "client"
    trace = tmp_path / "client.json"
    client.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"pathlib.Path({str(trace)!r}).write_text(json.dumps({{\n"
        "    'runtime': os.environ['XDG_RUNTIME_DIR'],\n"
        "    'display': os.environ.get('DISPLAY'),\n"
        "    'socket': os.environ.get('WAYLAND_SOCKET'),\n"
        "    'backend': os.environ['GDK_BACKEND'],\n"
        "    'gtk_exige': os.environ['GCM_TESTS_GTK4'],\n"
        "    'wayland_exige': os.environ['GCM_TESTS_WAYLAND'],\n"
        "    'arguments': sys.argv[1:],\n"
        "}))\n"
        f"raise SystemExit({statut})\n"
    )
    client.chmod(0o755)
    environnement = {
        **os.environ,
        "PATH": f"{binaires}{os.pathsep}{os.environ['PATH']}",
        "GCM_PYTHON": str(client),
        "DISPLAY": ":999",
        "WAYLAND_SOCKET": "999",
        "TMPDIR": str(tmp_path),
    }
    resultat = subprocess.run(
        ["bash", str(RACINE / "scripts" / "test-wayland.sh")],
        env=environnement,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert resultat.returncode == statut, resultat.stderr
    recu = json.loads(trace.read_text())
    assert recu["display"] is None and recu["socket"] is None
    assert recu["backend"] == "wayland"
    assert recu["gtk_exige"] == recu["wayland_exige"] == "1"
    assert recu["arguments"][:2] == ["-m", "pytest"]
    assert "tests/wayland" in recu["arguments"]
    assert "tests/gtk4/test_plugin_local.py" in recu["arguments"]
    assert not Path(recu["runtime"]).exists()
