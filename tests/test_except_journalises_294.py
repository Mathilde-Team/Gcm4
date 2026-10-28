# SPDX-License-Identifier: GPL-3.0-or-later
"""``except`` journalisés de ``plugins/ssh/core.py`` et ``tools/`` (issue #294).

Les replis restent les mêmes (valeur par défaut, Include ignoré, nettoyage
du fichier temporaire) ; ce qui change, c'est qu'ils laissent une trace qui
nomme la cause. On intercepte les méthodes du logger du module plutôt que
d'ajouter un puits loguru (``tests/test_loguru_only.py``).
"""

from __future__ import annotations

import configparser
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import plugins.ssh.core as core  # noqa: E402


@pytest.fixture
def journal(monkeypatch):
    """Messages journalisés par plugins/ssh/core.py : (niveau, texte)."""
    recus = []
    for niveau in ("debug", "warning", "error"):
        monkeypatch.setattr(
            core.logger,
            niveau,
            lambda msg, *args, _n=niveau, **_kw: recus.append((_n, msg.format(*args))),
        )
    return recus


def test_config_defaults_are_logged_without_option_name(journal):
    """Valeurs par défaut de configparser tracées, sans nom d'option."""
    cp = configparser.RawConfigParser()
    cp.add_section("s")
    cp.set("s", "nombre", "pas un entier")
    cp.set("s", "drapeau", "peut-être")
    assert core._cp_get(cp, "absente", "password", "défaut") == "défaut"
    assert core._cp_getbool(cp, "s", "drapeau", True) is True
    assert core._cp_getint(cp, "s", "nombre", 7) == 7
    assert [n for n, _m in journal] == ["debug"] * 3
    assert "NoSectionError" in journal[0][1] and "ValueError" in journal[2][1]
    assert not any("password" in m for _n, m in journal)


def test_remove_absent_host_is_logged(journal):
    """Retirer un hôte absent laisse une trace debug."""
    config = core.SSHConfig(file_path=Path("/nonexistent"))
    assert config.remove_host(core.SSHHost(patterns=["absent"])) is False
    assert journal and "hôte absent" in journal[0][1]


def test_unreadable_include_is_a_warning(tmp_path, journal):
    """Un Include illisible est ignoré avec un avertissement."""
    illisible = tmp_path / "inclus.conf"
    illisible.mkdir()  # un répertoire : open() lève IsADirectoryError (OSError)
    parser = core.SSHConfigParser(config_path=tmp_path / "config")
    parser.config.include_directives = [str(illisible)]
    parser._resolve_includes()
    assert parser.config.includes_resolved == {}
    assert ("warning", f"Include {illisible} ignoré, fichier illisible : ") in [
        (n, m[: len(f"Include {illisible} ignoré, fichier illisible : ")]) for n, m in journal
    ]


def test_failed_atomic_write_logs_and_cleans_up(tmp_path, journal, monkeypatch):
    """Écriture atomique ratée : erreur journalisée, temporaire supprimé."""
    cible = tmp_path / "config"
    parser = core.SSHConfigParser(config_path=cible)

    def echec_replace(_src, _dst):
        raise OSError("disque plein (simulé)")

    monkeypatch.setattr(core.os, "replace", echec_replace)
    with pytest.raises(OSError, match="disque plein"):
        parser._atomic_write("Host x\n")
    assert any(
        n == "error" and "écriture atomique" in m and "disque plein" in m for n, m in journal
    )
    assert list(tmp_path.iterdir()) == []  # fichier temporaire supprimé


def test_validate_xml_json_still_reports_errors(tmp_path):
    """Le script signale toujours les fichiers invalides (code 1)."""
    xml = tmp_path / "f.xml"
    xml.write_text("<a>", encoding="utf-8")
    js = tmp_path / "f.json"
    js.write_text("{", encoding="utf-8")
    ok = tmp_path / "ok.json"
    ok.write_text("{}", encoding="utf-8")
    sortie = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "validate_xml_json.py"), str(xml), str(js), str(ok)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert sortie.returncode == 1
    assert (
        f"[XML] {xml}" in sortie.stderr
        and f"[JSON] {js}" in sortie.stderr
        and str(ok) not in sortie.stderr
    )
