# SPDX-License-Identifier: GPL-3.0-or-later
"""Import et export des hôtes en ligne de commande, sans fenêtre (#154)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import logging_config
from gcm4 import app
from gcm4.core import cli, echange_cli
from gcm4.core.chiffrement import ChiffrementGcm
from gcm4.core.config import Config, load_config, save_config
from gcm4.core.model import Host


def _gcm4(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    """``python -m gcm4`` sans display (ni X11 ni Wayland), comme un script cron."""
    env = {
        k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "GCM_DEBUG")
    }
    env.update(GCM_LOG_LEVEL="WARNING", HOME=str(home), LANGUAGE="en_US")
    return subprocess.run(
        [sys.executable, "-m", "gcm4", *args], capture_output=True, text=True, env=env, timeout=60
    )


def _config(dossier: Path, *hotes: Host) -> Path:
    """Crée ``gcm4.conf`` (mots de passe chiffrés avec ``.gcm4.key``) dans ``dossier``."""
    dossier.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    for h in hotes:
        cfg.store.add(h)
    save_config(str(dossier / "gcm4.conf"), cfg, ChiffrementGcm(str(dossier)))
    return dossier


def _relire(dossier: Path) -> Config:
    return load_config(str(dossier / "gcm4.conf"), ChiffrementGcm(str(dossier)))


# --- sous-processus : scriptable sans display -------------------------------


def test_export_json_sans_display(tmp_path):
    """Export : code 0, bilan sur stdout, aucun mot de passe, aucune fenêtre."""
    conf = _config(tmp_path / "c", Host(name="api", group="PROD", host="h", password="s3cret"))
    r = _gcm4("-c", str(conf), "--export-json", str(tmp_path / "hotes"), home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"Hosts exported: 1 ({tmp_path / 'hotes.json'})"
    doc = json.loads((tmp_path / "hotes.json").read_text(encoding="utf-8"))
    assert [h["name"] for h in doc["hosts"]] == ["api"]
    assert "s3cret" not in (tmp_path / "hotes.json").read_text(encoding="utf-8")


def test_export_avec_mots_de_passe_dechiffres(tmp_path):
    """``--include-passwords`` : mot de passe déchiffré avec ``.gcm4.key``, en clair."""
    conf = _config(tmp_path / "c", Host(name="api", password="s3cret"))
    assert "s3cret" not in (conf / "gcm4.conf").read_text(encoding="utf-8")
    r = _gcm4(
        "-c", str(conf), "--export-csv", str(tmp_path / "h.csv"), "--include-passwords",
        home=tmp_path,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert "s3cret" in (tmp_path / "h.csv").read_text(encoding="utf-8")


def test_import_json_puis_conflits(tmp_path):
    """Import : configuration enregistrée (chiffrée), bilan, politique de conflit."""
    conf = _config(tmp_path / "c", Host(name="api", group="PROD", host="ancien"))
    source = tmp_path / "in.json"
    source.write_text(
        json.dumps([{"group": "PROD", "name": "api", "host": "nouveau", "password": "pw"}]),
        encoding="utf-8",
    )
    r = _gcm4("-c", str(conf), "--import-json", str(source), home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "added 0, replaced 0, renamed 0, ignored 1, new groups 0" in r.stdout
    r = _gcm4("-c", str(conf), "--import-json", str(source), "--on-conflict", "replace",
              home=tmp_path)  # fmt: skip
    assert r.returncode == 0 and "replaced 1" in r.stdout, r.stderr
    hote = _relire(conf).store.find("PROD/api")
    assert hote is not None and (hote.host, hote.password) == ("nouveau", "pw")
    assert "pass = pw\n" not in (conf / "gcm4.conf").read_text(encoding="utf-8")  # chiffré


def test_import_mal_forme_code_non_nul_configuration_intacte(tmp_path):
    """Fichier invalide : code 5, message sur stderr, gcm4.conf inchangé."""
    conf = _config(tmp_path / "c", Host(name="garde"))
    avant = (conf / "gcm4.conf").read_bytes()
    mauvais = tmp_path / "in.csv"
    mauvais.write_text("group,name\nA,un\nB,\n", encoding="utf-8")
    r = _gcm4("-c", str(conf), "--import-csv", str(mauvais), home=tmp_path)
    assert r.returncode == echange_cli.CODE_ECHANGE_IMPOSSIBLE
    assert r.stdout == ""
    assert "is not a valid host export: line 3: the host name is missing" in r.stderr
    assert (conf / "gcm4.conf").read_bytes() == avant


def test_import_dans_une_configuration_neuve(tmp_path):
    """Dossier vide : gcm4.conf créé avec les hôtes importés."""
    source = tmp_path / "in.csv"
    source.write_text("name,host,group\nweb,10.0.0.1,LAB\n", encoding="utf-8")
    r = _gcm4("-c", str(tmp_path / "neuf"), "--import-csv", str(source), home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert _relire(tmp_path / "neuf").store.find("LAB/web") is not None


def test_options_exclusives(tmp_path):
    """Un seul import ou export à la fois (erreur argparse, code 2)."""
    r = _gcm4("--export-json", "a", "--import-json", "b", home=tmp_path)
    assert r.returncode == 2 and "not allowed with argument" in r.stderr


# --- en processus (couverture) ------------------------------------------------


def _args(*argv: str, conf: Path):
    return cli.parse_args(["-c", str(conf), *argv])


def test_sans_option_d_echange_rien_a_faire(tmp_path):
    """Sans option : ``None``, l'interface démarre normalement."""
    assert echange_cli.executer(_args(conf=tmp_path)) is None


@pytest.mark.parametrize(
    ("argv", "attendu"),
    [
        (["--export-json", "f"], ("export", "json", "f")),
        (["--export-csv", "f"], ("export", "csv", "f")),
        (["--import-json", "f"], ("import", "json", "f")),
        (["--import-csv", "f"], ("import", "csv", "f")),
        ([], None),
    ],
)
def test_demande(argv, attendu, tmp_path):
    """Chaque option donne son action."""
    assert echange_cli.demande(_args(*argv, conf=tmp_path)) == attendu


@pytest.mark.parametrize(
    ("fichier", "fmt", "attendu"),
    [("h", "json", "h.json"), ("h.JSON", "json", "h.JSON"), ("h.json", "csv", "h.json.csv")],
)
def test_extension_imposee_par_le_format(fichier, fmt, attendu):
    """``--export-csv x.json`` reste du CSV : extension ajoutée."""
    assert echange_cli._cible(fichier, fmt) == attendu


def test_executer_en_processus(tmp_path):
    """Export puis import en processus, flux fournis."""
    conf = _config(tmp_path / "c", Host(name="a", group="G"))
    sortie, erreur = io.StringIO(), io.StringIO()
    code = echange_cli.executer(
        _args("--export-csv", str(tmp_path / "x"), conf=conf), sortie, erreur
    )
    assert code == 0 and "Hosts exported: 1" in sortie.getvalue() and erreur.getvalue() == ""
    code = echange_cli.executer(
        _args("--import-csv", str(tmp_path / "x.csv"), "--on-conflict", "rename", conf=conf),
        sortie,
        erreur,
    )
    assert code == 0 and "renamed 1" in sortie.getvalue()
    assert _relire(conf).store.find("G/a (2)") is not None


def test_configuration_corrompue_rien_n_est_ecrit(tmp_path, capsys):
    """gcm4.conf illisible : code 5, flux par défaut (stderr), aucun export."""
    conf = tmp_path / "c"
    conf.mkdir()
    (conf / "gcm4.conf").write_text("[host 1\nname = x\n", encoding="utf-8")
    code = echange_cli.executer(_args("--export-json", str(tmp_path / "x"), conf=conf))
    assert code == echange_cli.CODE_ECHANGE_IMPOSSIBLE
    assert "gcm4: " in capsys.readouterr().err
    assert not (tmp_path / "x.json").exists()


def test_main_s_arrete_avant_gtk(tmp_path, monkeypatch):
    """``app.main`` rend le code de l'échange sans importer l'interface."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Journalisation globale non touchée (les autres tests gardent leurs puits).
    monkeypatch.setattr(logging_config, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr(logging_config, "enable_debug", lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "gcm4.ui", None)  # import impossible si tenté
    code = app.main(["-c", str(tmp_path / "c"), "--export-json", str(tmp_path / "x")])
    assert code == 0 and (tmp_path / "x.json").exists()
