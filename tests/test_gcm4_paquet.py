# SPDX-License-Identifier: GPL-3.0-or-later
"""Paquet src/gcm4 : point d'entrée et ligne de commande (issue #133).

Sans display ni GTK : ``gcm4 --version`` et ``--help`` sont utilisés par
le test de fumée des paquets ; GTK absent donne un message d'installation
et un code non nul, sans trace Python.
"""

from __future__ import annotations

import ast
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.9 de Rocky 9 (#223) : même API, paquet tomli
    import tomli as tomllib

import gcm4
from gcm4 import app
from gcm4.core import cli

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "gcm4"


def _gcm4(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "GCM_DEBUG")
    }
    env["GCM_LOG_LEVEL"] = "WARNING"
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "gcm4", *args], capture_output=True, text=True, env=env, timeout=60
    )


def test_version_sans_display():
    """``gcm4 --version`` répond sans display, sur stdout, code 0."""
    r = _gcm4("--version")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"gcm4 {gcm4.__version__}"
    assert r.stderr == ""


def test_module_version_en_processus(monkeypatch, capsys):
    """``python -m gcm4 --version`` exécuté en processus : même sortie, code 0.

    Le test de fumée ci-dessus passe par un sous-processus, invisible pour
    la couverture ; celui-ci exécute ``src/gcm4/__main__.py`` en processus
    (argparse termine avant tout import GTK, aucun display nécessaire).
    """
    monkeypatch.setattr(sys, "argv", ["gcm4", "--version"])
    with pytest.raises(SystemExit) as infos:
        runpy.run_module("gcm4", run_name="__main__")
    assert infos.value.code in (0, None)
    assert capsys.readouterr().out.strip() == f"gcm4 {gcm4.__version__}"


def test_version_lue_dans_les_metadonnees():
    """La version est celle de pyproject.toml (importlib.metadata)."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert gcm4.__version__ == data["project"]["version"]


def test_aide_sans_display():
    """``gcm4 --help`` liste les options de #133."""
    r = _gcm4("--help")
    assert r.returncode == 0, r.stderr
    for option in ("--version", "--debug", "--config", "GROUPE/NOM"):
        assert option in r.stdout


def test_gtk_absent_message_d_installation(tmp_path):
    """Sans PyGObject : commande d'installation, code CODE_GTK_ABSENT, aucune trace."""
    faux = tmp_path / "faux" / "gi"
    faux.mkdir(parents=True)
    (faux / "__init__.py").write_text('raise ImportError("gi simulé absent")\n', encoding="utf-8")
    r = _gcm4(
        "-c",
        str(tmp_path / "conf"),
        env_extra={
            "PYTHONPATH": os.pathsep.join([str(faux.parent), str(ROOT / "src"), str(ROOT)])
        },
    )
    assert r.returncode == app.CODE_GTK_ABSENT
    assert "Installez-les avec" in r.stderr
    assert "Traceback" not in r.stderr


def test_config_par_defaut_xdg(tmp_path):
    """Sans ``-c`` : ``~/.config/gcm4``, ancien ``~/.gcm`` à migrer (#99)."""
    args = cli.parse_args([], home=str(tmp_path), environ={})
    assert args.config_dir == str(tmp_path / ".config" / "gcm4")
    assert args.ancien_dossier == str(tmp_path / ".gcm")
    assert args.hotes == [] and args.debug is False


def test_config_xdg_config_home(tmp_path):
    """``XDG_CONFIG_HOME`` absolu respecté ; relatif ignoré (spécification XDG)."""
    args = cli.parse_args([], home=str(tmp_path), environ={"XDG_CONFIG_HOME": "/srv/conf"})
    assert args.config_dir == "/srv/conf/gcm4"
    args = cli.parse_args([], home=str(tmp_path), environ={"XDG_CONFIG_HOME": "relatif"})
    assert args.config_dir == str(tmp_path / ".config" / "gcm4")


def test_config_explicite_jamais_remplie_depuis_gcm(tmp_path):
    """Avec ``-c``, aucune copie depuis ``~/.gcm``."""
    args = cli.parse_args(["-c", str(tmp_path / "x")], home=str(tmp_path))
    assert args.config_dir == str(tmp_path / "x") and args.ancien_dossier is None


def test_config_relative_rendue_absolue(tmp_path, monkeypatch):
    """``-c conf`` relatif devient un chemin absolu."""
    monkeypatch.chdir(tmp_path)
    assert cli.parse_args(["-c", "conf"]).config_dir == str(tmp_path / "conf")


def test_config_relative_au_dossier_donne(tmp_path):
    """``cwd`` : ``-c`` relatif résolu dans le dossier d'un deuxième lancement (#275)."""
    args = cli.parse_args(["-c", "conf"], home="/x", cwd=str(tmp_path))
    assert args.config_dir == str(tmp_path / "conf")
    assert cli.parse_args(["-c", "/abs"], cwd=str(tmp_path)).config_dir == "/abs"


def test_hotes_et_debug():
    """Les arguments ``groupe/nom`` sont conservés, ``--debug`` reconnu."""
    args = cli.parse_args(["--debug", "lab/web", "prod/db"], home="/x")
    assert args.debug is True
    assert args.hotes == ["lab/web", "prod/db"]


def test_option_inconnue_refusee():
    """Une option inconnue est refusée par argparse (code 2)."""
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["--inexistante"])
    assert exc.value.code == 2


def test_commande_declaree():
    """``[project.scripts] gcm4 = "gcm4.app:main"`` et Python 3.12 minimum (D4)."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"]["gcm4"] == "gcm4.app:main"
    assert data["project"]["requires-python"].startswith(">=3.12")


def _sous_type_checking(n: ast.AST) -> bool:
    """``if TYPE_CHECKING:`` ou ``if typing.TYPE_CHECKING:``."""
    test = getattr(n, "test", None)
    return isinstance(n, ast.If) and (
        (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
        or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
    )


def _imports(path: Path) -> list[str]:
    """Modules importés à l'exécution (blocs ``if TYPE_CHECKING:`` exclus : annotations seules)."""
    noms = []
    pile: list[ast.AST] = [ast.parse(path.read_text(encoding="utf-8"))]
    while pile:
        n = pile.pop()
        if isinstance(n, ast.Import):
            noms += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            noms.append(n.module)
        if _sous_type_checking(n):
            pile.extend(n.orelse)  # le corps n'est jamais exécuté
        else:
            pile.extend(ast.iter_child_nodes(n))
    return noms


def test_imports_type_checking_ignores(tmp_path):
    """Un import sous ``if TYPE_CHECKING:`` n'est pas compté ; le même import ailleurs l'est."""
    f = tmp_path / "m.py"
    f.write_text(
        "from typing import TYPE_CHECKING\nimport typing\n"
        "if TYPE_CHECKING:\n    from gi.repository import Gtk\n"
        "if typing.TYPE_CHECKING:\n    import gi\nelse:\n    import os\n"
        "def f():\n    import json\n",
        encoding="utf-8",
    )
    assert sorted(_imports(f)) == ["json", "os", "typing", "typing"]


def test_aucun_gi_hors_de_ui():
    """Aucun fichier de src/gcm4 hors ``ui/`` n'importe ``gi`` (cœur et point d'entrée)."""
    fautes = [
        f"{p.relative_to(ROOT)} : {m}"
        for p in sorted(SRC.rglob("*.py"))
        if "ui" not in p.relative_to(SRC).parts
        for m in _imports(p)
        if m == "gi" or m.startswith("gi.")
    ]
    assert fautes == []


def test_require_version_uniquement_dans_ui_init():
    """``gi.require_version`` n'apparaît que dans ``src/gcm4/ui/__init__.py``.

    Seule exception : ``ui/terminal.py`` fixe VTE 3.91, et rien d'autre. VTE
    ne peut pas être exigé dans ``ui/__init__.py`` : la base doit démarrer
    là où VTE GTK4 manque (Rocky 9 et 10, #223) ; seuls les plugins terminal
    importent ``ui/terminal.py`` (#295).
    """
    fichiers = sorted(
        str(p.relative_to(ROOT))
        for p in SRC.rglob("*.py")
        if "require_version" in p.read_text(encoding="utf-8")
    )
    assert fichiers == ["src/gcm4/ui/__init__.py", "src/gcm4/ui/terminal.py"]
    texte = (SRC / "ui" / "terminal.py").read_text(encoding="utf-8")
    assert re.findall(r"require_version\((.*?)\)", texte) == ['"Vte", "3.91"']


def _importe_ui_terminal(texte: str, dans_ui: bool) -> bool:
    """Vrai si ``texte`` importe ``gcm4.ui.terminal`` (absolu, ``from gcm4.ui import terminal`` ou, dans ``ui/``, relatif)."""
    for n in ast.walk(ast.parse(texte)):
        if isinstance(n, ast.Import) and any(a.name == "gcm4.ui.terminal" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom):
            noms = {a.name for a in n.names}
            if n.level == 0 and n.module == "gcm4.ui.terminal":
                return True
            if n.level == 0 and n.module == "gcm4.ui" and "terminal" in noms:
                return True
            if dans_ui and n.level == 1 and (n.module == "terminal" or "terminal" in noms):
                return True
    return False


def test_terminal_vte_reserve_aux_plugins():
    """Aucun module de la base n'importe ``gcm4.ui.terminal`` (VTE GTK4 facultatif, #295)."""
    fautes = [
        str(p.relative_to(ROOT))
        for p in sorted(SRC.rglob("*.py"))
        if _importe_ui_terminal(p.read_text(encoding="utf-8"), "ui" in p.relative_to(SRC).parts)
    ]
    assert fautes == []


@pytest.mark.parametrize(
    ("texte", "dans_ui", "attendu"),
    [
        ("import gcm4.ui.terminal\n", False, True),
        ("from gcm4.ui import terminal\n", False, True),
        ("from gcm4.ui.terminal import SessionTerminal\n", False, True),
        ("from . import terminal\n", True, True),
        ("from .terminal import zone_options\n", True, True),
        ("from . import terminal\n", False, False),
        ("from gcm4.core import terminal\n", False, False),
        ("from . import dialogues\n", True, False),
    ],
)
def test_detecteur_import_ui_terminal(texte, dans_ui, attendu):
    """Le détecteur reconnaît les formes d'import de ``gcm4.ui.terminal``, et seulement elles."""
    assert _importe_ui_terminal(texte, dans_ui) is attendu
