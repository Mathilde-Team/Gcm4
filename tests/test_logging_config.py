# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests de logging_config (issue #130, sous-issue 4a), sans GTK."""

from __future__ import annotations

import argparse
import enum
import os
import subprocess
import sys
from pathlib import Path

import pytest
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gcm4_core  # noqa: E402
import logging_config as lc  # noqa: E402


@pytest.fixture(autouse=True)
def _propre(monkeypatch):
    """Environnement neutre et configuration remise à zéro autour de chaque test."""
    for var in (lc.ENV_LEVEL, lc.ENV_DEBUG, lc.ENV_FILE):
        monkeypatch.delenv(var, raising=False)
    lc._reset_for_tests()
    yield
    lc._reset_for_tests()


def _lire(chemin):
    """Contenu d'un journal après vidage des handlers."""
    logger.complete()
    return Path(chemin).read_text(encoding="utf-8")


# -- niveaux -----------------------------------------------------------------


def test_niveau_par_defaut_info():
    """Sans option ni variable : INFO."""
    assert lc.level_from_env() == "INFO"
    lc.configure_logging()
    assert lc.current_level() == "INFO"
    assert not lc.is_debug_enabled()


@pytest.mark.parametrize("valeur", ["1", "true", "yes", "oui", "ON"])
def test_gcm_debug_active_debug(monkeypatch, valeur):
    """GCM_DEBUG vrai : DEBUG."""
    monkeypatch.setenv(lc.ENV_DEBUG, valeur)
    assert lc.level_from_env() == "DEBUG"


def test_gcm_log_level_prioritaire(monkeypatch):
    """GCM_LOG_LEVEL l'emporte sur GCM_DEBUG."""
    monkeypatch.setenv(lc.ENV_DEBUG, "1")
    monkeypatch.setenv(lc.ENV_LEVEL, "warning")
    assert lc.level_from_env() == "WARNING"


def test_niveau_inconnu_repli_sur_info(tmp_path):
    """Niveau inconnu : INFO, avec un avertissement dans le journal."""
    lc.configure_logging("BAVARD", config_dir=str(tmp_path))
    assert lc.current_level() == "INFO"
    assert "niveau de log inconnu 'BAVARD'" in _lire(lc.app_log_path(str(tmp_path)))


def test_is_debug_level():
    """DEBUG et TRACE sont des niveaux debug, pas INFO."""
    assert lc.is_debug_level("debug") and lc.is_debug_level("TRACE")
    assert not lc.is_debug_level("INFO")


# -- handlers ----------------------------------------------------------------


def test_journal_permanent_au_niveau_courant(tmp_path):
    """gcm-app.log suit le niveau : pas de DEBUG par défaut (avant #130 : toujours DEBUG)."""
    lc.configure_logging(config_dir=str(tmp_path))
    logger.debug("trace-invisible")
    logger.info("info-visible")
    contenu = _lire(tmp_path / "log" / "gcm-app.log")
    assert "info-visible" in contenu and "trace-invisible" not in contenu
    assert "\x1b[" not in contenu  # pas de codes couleur dans le fichier


def test_mode_debug_processus_et_thread(tmp_path):
    """En debug, chaque ligne porte processus:thread."""
    lc.configure_logging("DEBUG", config_dir=str(tmp_path))
    logger.debug("trace-visible")
    contenu = _lire(lc.app_log_path(str(tmp_path)))
    assert "trace-visible" in contenu and "MainProcess:MainThread" in contenu


def test_gcm_log_file_copie_supplementaire(tmp_path, monkeypatch):
    """GCM_LOG_FILE ajoute un fichier, en plus du journal permanent."""
    copie = tmp_path / "copie" / "debug.log"
    monkeypatch.setenv(lc.ENV_FILE, str(copie))
    lc.configure_logging(config_dir=str(tmp_path / "conf"))
    logger.warning("dans-les-deux")
    assert "dans-les-deux" in _lire(copie)
    assert "dans-les-deux" in _lire(lc.app_log_path(str(tmp_path / "conf")))
    assert lc.log_files() == [lc.app_log_path(str(tmp_path / "conf")), str(copie)]


def test_journal_inaccessible_ne_bloque_pas(tmp_path):
    """Dossier de journal impossible à créer : avertissement, pas d'exception."""
    bloque = tmp_path / "fichier"
    bloque.write_text("x")
    lc.configure_logging(config_dir=str(bloque))  # bloque/log impossible : bloque est un fichier
    assert lc.log_files() == []


def test_idempotent_sans_force(tmp_path):
    """Un second appel sans force ne change rien ; avec force, il remplace."""
    lc.configure_logging(config_dir=str(tmp_path / "a"))
    lc.configure_logging("DEBUG", config_dir=str(tmp_path / "b"))
    assert lc.current_level() == "INFO"
    assert lc.log_files() == [lc.app_log_path(str(tmp_path / "a"))]
    lc.configure_logging("DEBUG", config_dir=str(tmp_path / "b"), force=True)
    assert lc.current_level() == "DEBUG"
    assert lc.log_files() == [lc.app_log_path(str(tmp_path / "b"))]


def test_enable_debug(tmp_path):
    """enable_debug reconfigure à chaud en DEBUG."""
    lc.configure_logging()
    lc.enable_debug(config_dir=str(tmp_path))
    assert lc.is_debug_enabled()


def test_get_logger_configure_au_besoin():
    """get_logger configure si nécessaire et lie le nom du module."""
    lie = lc.get_logger("module.test")
    assert lc.current_level() == "INFO"
    assert lie is not None


def test_setup_app_logger_delegue(tmp_path):
    """gcm4_core.setup_app_logger passe par logging_config (debug compris)."""
    gcm4_core.setup_app_logger(str(tmp_path))
    assert lc.current_level() == "INFO"
    gcm4_core.setup_app_logger(str(tmp_path), debug=True)
    assert lc.current_level() == "DEBUG"
    assert lc.log_files() == [lc.app_log_path(str(tmp_path))]


# -- ligne de commande -------------------------------------------------------


def test_extract_debug_flag():
    """--debug est retiré, l'ordre des autres arguments conservé."""
    assert lc.extract_debug_flag(["-c", "/x", "--debug", "g/h"]) == (True, ["-c", "/x", "g/h"])
    assert lc.extract_debug_flag(["g/h"]) == (False, ["g/h"])


def test_add_et_apply_debug_argument():
    """Option argparse --debug commune."""
    parser = argparse.ArgumentParser()
    lc.add_debug_argument(parser)
    lc.apply_debug_argument(parser.parse_args([]))
    assert not lc.is_debug_enabled()
    lc.apply_debug_argument(parser.parse_args(["--debug"]))
    assert lc.is_debug_enabled()


# -- summarize ---------------------------------------------------------------


class _Couleur(enum.Enum):
    """Enum de test."""

    ROUGE = 1


@pytest.mark.parametrize(
    ("valeur", "nom", "attendu"),
    [
        ("secret", "password", "***"),
        ("x", "pwd", "***"),
        ("x", "spice_ticket", "***"),
        (None, "", "None"),
        (3, "port", "3"),
        ("court", "", "'court'"),
        ("ssh://alice:pw@hote/x", "uri", "'ssh://***@hote/x'"),
        ("a" * 81, "", "<str 81 car.>"),
        (b"abc", "", "<bytes 3 octets>"),
        (Path("/tmp/x"), "", "'/tmp/x'"),
        (_Couleur.ROUGE, "", "_Couleur.ROUGE"),
        ([1, 2], "", "<list 2>"),
        (object(), "", "<object>"),
    ],
)
def test_summarize(valeur, nom, attendu):
    """Résumés sûrs : secrets masqués, objets résumés."""
    assert lc.summarize(valeur, nom) == attendu


# -- sous-processus (comportement réel de stderr) ----------------------------


def _executer(env_extra):
    """Lance un petit programme qui configure les journaux et en émet deux."""
    code = (
        "import logging_config, loguru;"
        "logging_config.configure_logging();"
        "loguru.logger.debug('ligne-debug'); loguru.logger.info('ligne-info')"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("GCM_")}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True
    ).stderr


def test_stderr_info_par_defaut():
    """Sans option : stderr affiche INFO mais pas DEBUG."""
    err = _executer({})
    assert "ligne-info" in err and "ligne-debug" not in err


def test_stderr_debug_avec_gcm_debug():
    """GCM_DEBUG=1 : DEBUG visible, avec le thread émetteur."""
    err = _executer({"GCM_DEBUG": "1"})
    assert "ligne-debug" in err and "MainProcess:MainThread" in err


# --- Convention bibliothèque des *_core.py (issue #130) ---------------------


def test_chaque_core_qui_journalise_se_desactive():
    """Tout *_core.py racine qui importe loguru se désactive et figure dans CORE_MODULES."""
    import ast

    attendus = []
    for chemin in sorted(ROOT.glob("*_core.py")):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        if not any(isinstance(n, ast.ImportFrom) and n.module == "loguru" for n in arbre.body):
            continue
        attendus.append(chemin.stem)
        desactive = any(
            isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "attr", "") == "disable"
            and [getattr(a, "id", None) for a in n.value.args] == ["__name__"]
            for n in arbre.body
        )
        assert desactive, f"{chemin.name} : ajouter <logger>.disable(__name__) au niveau module"
    assert sorted(m for m in lc.CORE_MODULES if m != "gcm4") == attendus
    assert "gcm4" in lc.CORE_MODULES  # src/gcm4/__init__.py, même convention


def test_core_muet_avant_configure_logging_puis_reactive():
    """Un module *_core ne journalise rien avant configure_logging(), puis normalement."""
    lc._reset_for_tests()
    logger.disable("gcm4_core")
    recus: list[str] = []
    sink = logger.add(lambda m: recus.append(m.record["name"]), level="DEBUG")
    try:
        gcm4_core.dep_install_hint("a", "b")
        assert "gcm4_core" not in recus
        lc.configure_logging("DEBUG", force=True)
        sink = logger.add(lambda m: recus.append(m.record["name"]), level="DEBUG")
        gcm4_core.dep_install_hint("a", "b")
        assert "gcm4_core" in recus
    finally:
        lc._reset_for_tests()
        try:
            logger.remove(sink)
        except ValueError:
            pass
