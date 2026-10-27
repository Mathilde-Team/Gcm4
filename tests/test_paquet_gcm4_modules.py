"""Modules de la racine embarqués par le paquet gcm4 (wheel, .deb, .rpm).

Le wheel ne contient que ``src/gcm4`` et les modules de la racine listés dans
``[tool.hatch.build.targets.wheel] only-include`` ; ``build-deb/build.sh`` et
``build-rpm/build.sh`` copient la même liste. Un module de la racine importé
par gcm4 (directement ou par un module embarqué) mais absent de cette liste
fait échouer ``gcm4 --version`` une fois installé (#98 : ``master_password_core``).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.9 de Rocky 9 (#223) : même API, paquet tomli
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def _only_include() -> list[str]:
    """Liste ``only-include`` du wheel."""
    donnees = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return donnees["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"]


def _modules_racine_importes(fichier: Path) -> set[str]:
    """Modules de la racine (``<nom>.py``) importés par ``fichier``, imports différés compris."""
    arbre = ast.parse(fichier.read_text(encoding="utf-8"))
    noms: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            noms.update(alias.name.split(".")[0] for alias in noeud.names)
        elif isinstance(noeud, ast.ImportFrom) and noeud.module and noeud.level == 0:
            noms.add(noeud.module.split(".")[0])
    return {nom for nom in noms if (ROOT / f"{nom}.py").is_file()}


def test_modules_racine_importes_par_gcm4_embarques():
    """Chaque module de la racine atteint depuis src/gcm4 est dans ``only-include``."""
    inclus = set(_only_include())
    a_voir = list((ROOT / "src" / "gcm4").rglob("*.py"))
    vus: set[Path] = set()
    absents: set[str] = set()
    while a_voir:
        fichier = a_voir.pop()
        if fichier in vus:
            continue
        vus.add(fichier)
        for nom in _modules_racine_importes(fichier):
            if f"{nom}.py" not in inclus:
                absents.add(nom)
            a_voir.append(ROOT / f"{nom}.py")
    assert sorted(absents) == []


def test_scripts_de_paquet_meme_liste_que_le_wheel():
    """``build-deb/build.sh`` et ``build-rpm/build.sh`` copient les fichiers de ``only-include``."""
    attendus = _only_include()
    for script in ("build-deb/build.sh", "build-rpm/build.sh"):
        texte = (ROOT / script).read_text(encoding="utf-8")
        ligne = re.search(r"git ls-files -z -- (.*?)\\\n", texte)
        assert ligne is not None, script
        assert ligne.group(1).split() == attendus, script
