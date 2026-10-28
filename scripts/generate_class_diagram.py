#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Génère ``docs/class-diagram.md`` depuis le code (issue #175, modèle Netcross #140).

Analyse statique (module ``ast``) : aucun module de GCM n'est importé, donc ni
GTK ni VTE ne sont nécessaires. La sortie est déterministe (aucune date ni
chemin absolu) : le fichier ne change que si le code change.

Périmètre : modules Python suivis par git à la racine, ``plugins/`` et, dès
qu'il existe, ``src/gcm4/`` (épopée GTK4). ``tests/``, ``tools/``,
``scripts/`` et les sous-projets ``SSH-Studio/``, ``gtk-frdp/`` sont exclus.

Groupes :

- **Cœur** : modules sans GTK (``*_core.py``, ``gcm4_core.py``,
  ``logging_config.py``, ``master_password_core.py``), comme dans
  ``tests/test_architecture_imports.py`` ;
- **Application GTK** : les autres modules de la racine ;
- **Plugins** : ``plugins/`` ;
- ``gcm4.core``, ``gcm4.ui``… : sous-paquets de ``src/gcm4``.

Contenu : graphe des dépendances entre groupes, puis par groupe une table
des modules (première phrase de leur docstring) et un ou plusieurs blocs
mermaid ``classDiagram`` (champs annotés, méthodes publiques, fonctions
publiques de module, héritage). Un bloc est plafonné à ``MAX_BLOCK_CHARS``
caractères (limite de rendu de mermaid : 50 000).

Usage :

    python3 scripts/generate_class_diagram.py          # (ré)écrit le fichier
    python3 scripts/generate_class_diagram.py --check  # code 1 s'il est périmé
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "class-diagram.md"
EXCLUDED = ("tests/", "tools/", "scripts/", "SSH-Studio/", "gtk-frdp/")
CORE_EXTRA = {"gcm4_core.py", "logging_config.py", "master_password_core.py"}
GROUP_CORE = "Cœur (sans GTK)"
GROUP_APP = "Application GTK"
GROUP_PLUGINS = "Plugins"
FIXED_GROUPS = [GROUP_CORE, GROUP_APP, GROUP_PLUGINS]
MAX_BLOCK_CHARS = 40_000
DOC_MAX_CHARS = 160


@dataclass
class ClassInfo:
    """Classe extraite du code."""

    name: str
    bases: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    is_dataclass: bool = False


@dataclass
class ModuleInfo:
    """Module extrait du code."""

    path: str
    name: str
    group: str
    doc: str = ""
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


def safe(text: str) -> str:
    """Rend un texte acceptable dans une ligne de membre mermaid.

    Args:
        text: Texte brut (annotation, base de classe...).

    Returns:
        str: Texte sans guillemets ni accolades, ``[]`` remplacés par ``~~``.
    """
    logger.trace("safe() called")
    text = text.replace("[", "~").replace("]", "~")
    for ch in ('"', "'", "`", "{", "}", "<", ">", "|", "\n", "\r"):
        text = text.replace(ch, " " if ch in "\n\r|" else "")
    result = " ".join(text.split())
    logger.trace("safe() returning")
    return result


def annotation(node: ast.expr | None) -> str:
    """Annotation de type en syntaxe mermaid (``list~str~``).

    Args:
        node: Nœud d'annotation (ou None).

    Returns:
        str: Texte de l'annotation, vide si absente.
    """
    logger.trace("annotation() called")
    if node is None:
        logger.trace("annotation() returning | absente")
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        logger.trace("annotation() returning | chaîne")
        return safe(node.value)
    result = safe(ast.unparse(node))
    logger.trace("annotation() returning")
    return result


def function_line(fn: ast.FunctionDef | ast.AsyncFunctionDef, *, is_method: bool) -> str:
    """Ligne mermaid d'une fonction ou méthode publique.

    Args:
        fn: Définition.
        is_method: Retirer ``self``/``cls``.

    Returns:
        str: ``+nom(params)`` suivi du type de retour.
    """
    logger.trace("function_line() called | {}", fn.name)
    params = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args)]
    if is_method and params and params[0] in ("self", "cls"):
        params = params[1:]
    if fn.args.vararg:
        params.append("*" + fn.args.vararg.arg)
    params.extend(a.arg for a in fn.args.kwonlyargs)
    if fn.args.kwarg:
        params.append("**" + fn.args.kwarg.arg)
    decorators = {ast.unparse(d).split("(")[0].split(".")[-1] for d in fn.decorator_list}
    static = "$" if decorators & {"staticmethod", "classmethod"} else ""
    ret = annotation(fn.returns)
    line = f"+{fn.name}({', '.join(params)}){static}" + (f" {ret}" if ret else "")
    logger.trace("function_line() returning")
    return line


def extract_class(node: ast.ClassDef) -> ClassInfo:
    """Champs annotés, méthodes publiques et bases d'une classe.

    Args:
        node: Définition de classe.

    Returns:
        ClassInfo: Classe extraite.
    """
    logger.trace("extract_class() called | {}", node.name)
    info = ClassInfo(
        name=node.name,
        bases=[b for b in (safe(ast.unparse(b)) for b in node.bases) if b != "object"],
    )
    info.is_dataclass = any(
        ast.unparse(d).split("(")[0].endswith("dataclass") for d in node.decorator_list
    )
    seen: set[str] = set()
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            visibility = "-" if item.target.id.startswith("_") else "+"
            info.fields.append(f"{visibility}{annotation(item.annotation)} {item.target.id}")
        elif isinstance(
            item, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and not item.name.startswith("_"):
            if item.name not in seen:
                seen.add(item.name)
                info.methods.append(function_line(item, is_method=True))
    logger.trace("extract_class() returning | {} méthode(s)", len(info.methods))
    return info


def first_sentence(doc: str | None) -> str:
    """Première phrase d'une docstring, tronquée, échappée pour une table Markdown.

    Args:
        doc: Docstring (ou None).

    Returns:
        str: Phrase courte, vide sans docstring.
    """
    logger.trace("first_sentence() called")
    if not doc:
        logger.trace("first_sentence() returning | vide")
        return ""
    paragraph = " ".join(doc.strip().split("\n\n")[0].split())
    cut = paragraph.find(". ")
    if 0 < cut < DOC_MAX_CHARS:
        paragraph = paragraph[: cut + 1]
    elif len(paragraph) > DOC_MAX_CHARS:
        paragraph = paragraph[:DOC_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    result = paragraph.replace("|", "\\|")
    logger.trace("first_sentence() returning")
    return result


def group_of(path: str) -> str:
    """Groupe de rendu d'un fichier.

    Args:
        path: Chemin relatif à la racine.

    Returns:
        str: Groupe (cœur, application, plugins, ``gcm4.<sous-paquet>``).
    """
    logger.trace("group_of() called | {}", path)
    parts = Path(path).parts
    if parts[0] == "src":
        group = ".".join(parts[1:3]) if len(parts) > 3 else parts[1]
    elif parts[0] == "plugins":
        group = GROUP_PLUGINS
    elif path.endswith("_core.py") or path in CORE_EXTRA:
        group = GROUP_CORE
    else:
        group = GROUP_APP
    logger.trace("group_of() returning | {}", group)
    return group


def module_name(path: str) -> str:
    """Nom pointé d'un module (``src/`` retiré, ``__init__`` absorbé).

    Args:
        path: Chemin relatif à la racine.

    Returns:
        str: Nom du module.
    """
    logger.trace("module_name() called | {}", path)
    parts = list(Path(path).with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__" and len(parts) > 1:
        parts = parts[:-1]
    name = ".".join(parts)
    logger.trace("module_name() returning | {}", name)
    return name


def extract_module(path: str) -> ModuleInfo:
    """Classes, fonctions publiques et imports d'un module.

    Args:
        path: Chemin relatif à la racine.

    Returns:
        ModuleInfo: Module extrait.
    """
    logger.debug("extract_module() called | {}", path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)
    info = ModuleInfo(path=path, name=module_name(path), group=group_of(path))
    info.doc = first_sentence(ast.get_docstring(tree))
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            info.classes.append(extract_class(node))
        elif isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and not node.name.startswith("_"):
            info.functions.append(function_line(node, is_method=False))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            info.imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            info.imports.append(node.module)
    logger.debug("extract_module() returning | {} classe(s)", len(info.classes))
    return info


def source_files() -> list[str]:
    """Fichiers du périmètre, suivis par git, triés.

    Returns:
        list[str]: Chemins relatifs à la racine.
    """
    logger.debug("source_files() called")
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    files = sorted(
        f
        for f in out.stdout.split()
        if not f.startswith(EXCLUDED)
        and "/tests/" not in f
        and (("/" not in f) or f.startswith(("plugins/", "src/gcm4/")))
    )
    logger.debug("source_files() returning | {} fichier(s)", len(files))
    return files


def class_ids(modules: list[ModuleInfo]) -> dict[tuple[str, str], str]:
    """Identifiant mermaid de chaque classe : son nom, préfixé du module s'il n'est pas unique.

    Args:
        modules: Modules extraits.

    Returns:
        dict[tuple[str, str], str]: (module, classe) -> identifiant.
    """
    logger.debug("class_ids() called")
    counts: dict[str, int] = {}
    for m in modules:
        for c in m.classes:
            counts[c.name] = counts.get(c.name, 0) + 1
    ids = {
        (m.name, c.name): c.name if counts[c.name] == 1 else f"{m.name.replace('.', '_')}_{c.name}"
        for m in modules
        for c in m.classes
    }
    logger.debug("class_ids() returning | {} classe(s)", len(ids))
    return ids


def render_block(modules: list[ModuleInfo], ids: dict[tuple[str, str], str]) -> str:
    """Bloc mermaid ``classDiagram`` pour un ensemble de modules.

    Args:
        modules: Modules du bloc.
        ids: Identifiants des classes.

    Returns:
        str: Bloc Markdown complet (clôtures comprises).
    """
    logger.debug("render_block() called | {} module(s)", len(modules))
    lines = ["```mermaid", "classDiagram"]
    local = {c.name: ids[(m.name, c.name)] for m in modules for c in m.classes}
    for m in modules:
        if m.functions:
            mid = "mod_" + m.name.replace(".", "_")
            lines.append(f"    class {mid} {{")
            lines.append("        <<module>>")
            lines.extend(f"        {f}" for f in m.functions)
            lines.append("    }")
        for c in m.classes:
            cid = ids[(m.name, c.name)]
            body = c.fields + c.methods
            external = [b for b in c.bases if b.split(".")[-1] not in local]
            stereotypes = (["dataclass"] if c.is_dataclass else []) + external
            if body or stereotypes:
                lines.append(f"    class {cid} {{")
                if stereotypes:
                    lines.append(f"        <<{', '.join(stereotypes)}>>")
                lines.extend(f"        {member}" for member in body)
                lines.append("    }")
            else:
                lines.append(f"    class {cid}")
            for base in c.bases:
                if base.split(".")[-1] in local:
                    lines.append(f"    {local[base.split('.')[-1]]} <|-- {cid}")
    lines.append("```")
    block = "\n".join(lines)
    logger.debug("render_block() returning | {} caractère(s)", len(block))
    return block


def chunk_modules(modules: list[ModuleInfo], ids: dict[tuple[str, str], str]) -> list[str]:
    """Coupe un groupe en blocs de moins de ``MAX_BLOCK_CHARS`` (par modules entiers).

    Args:
        modules: Modules du groupe (au moins une classe ou fonction).
        ids: Identifiants des classes.

    Returns:
        list[str]: Blocs mermaid.
    """
    logger.debug("chunk_modules() called | {} module(s)", len(modules))
    blocks: list[str] = []
    current: list[ModuleInfo] = []
    for m in modules:
        if current and len(render_block([*current, m], ids)) > MAX_BLOCK_CHARS:
            blocks.append(render_block(current, ids))
            current = []
        current.append(m)
    if current:
        blocks.append(render_block(current, ids))
    logger.debug("chunk_modules() returning | {} bloc(s)", len(blocks))
    return blocks


def ordered_groups(modules: list[ModuleInfo]) -> list[str]:
    """Groupes présents, dans l'ordre cœur, application, plugins, puis ``gcm4.*``.

    Args:
        modules: Modules extraits.

    Returns:
        list[str]: Groupes.
    """
    logger.debug("ordered_groups() called")
    present = {m.group for m in modules}
    groups = [g for g in FIXED_GROUPS if g in present] + sorted(present - set(FIXED_GROUPS))
    logger.debug("ordered_groups() returning | {}", groups)
    return groups


def render_dependencies(modules: list[ModuleInfo]) -> str:
    """Graphe mermaid des imports entre groupes (nombre d'imports sur chaque flèche).

    Args:
        modules: Modules extraits.

    Returns:
        str: Bloc Markdown ``flowchart``.
    """
    logger.debug("render_dependencies() called")
    owner = {m.name: m.group for m in modules}
    groups = ordered_groups(modules)
    node = {g: f"g{i}" for i, g in enumerate(groups)}
    edges: dict[tuple[str, str], int] = {}
    for m in modules:
        for imported in m.imports:
            target = owner.get(imported) or owner.get(imported.split(".")[0])
            if target and target != m.group:
                edges[(m.group, target)] = edges.get((m.group, target), 0) + 1
    lines = ["```mermaid", "flowchart TD"]
    lines.extend(f'    {node[g]}["{g}"]' for g in groups)
    lines.extend(f"    {node[a]} -->|{n}| {node[b]}" for (a, b), n in sorted(edges.items()))
    lines.append("```")
    logger.debug("render_dependencies() returning | {} arête(s)", len(edges))
    return "\n".join(lines)


def render_document(modules: list[ModuleInfo]) -> str:
    """Document Markdown complet.

    Args:
        modules: Modules extraits.

    Returns:
        str: Contenu de ``docs/class-diagram.md``.
    """
    logger.debug("render_document() called | {} module(s)", len(modules))
    ids = class_ids(modules)
    n_classes = sum(len(m.classes) for m in modules)
    n_functions = sum(len(m.functions) for m in modules)
    out = [
        "<!-- FICHIER GÉNÉRÉ par scripts/generate_class_diagram.py -- NE PAS ÉDITER À LA MAIN. -->",
        "",
        "# GCM — diagramme de classes",
        "",
        "> **Fichier généré depuis le code** par `scripts/generate_class_diagram.py` (issue #175) :",
        "> toute modification manuelle sera écrasée. Le régénérer avec `make class-diagram` ;",
        "> le job Qualité échoue s'il est périmé (`--check`).",
        "",
        f"{len(modules)} modules · {n_classes} classes · {n_functions} fonctions publiques de module.",
        "",
        "Conventions : `+` public, `-` privé (préfixe `_`) ; `list~str~` = `list[str]` ; `<<module>>`",
        "regroupe les fonctions publiques d'un module ; `<<…>>` sur une classe liste `dataclass` et les",
        "bases définies hors du bloc. Les méthodes privées et les classes imbriquées ne sont pas",
        "représentées. Périmètre : racine, `plugins/` et `src/gcm4/` (hors tests, outils et sous-projets).",
        "",
        "## Dépendances entre groupes",
        "",
        "Nombre d'instructions `import` d'un groupe vers un autre. Le cœur n'importe ni GTK ni plugin",
        "(`tests/test_architecture_imports.py`).",
        "",
        render_dependencies(modules),
    ]
    for group in ordered_groups(modules):
        members = [m for m in modules if m.group == group]
        out += ["", f"## {group}", "", "| Module | Fichier | Rôle |", "|---|---|---|"]
        out += [f"| `{m.name}` | `{m.path}` | {m.doc} |" for m in members]
        drawable = [m for m in members if m.classes or m.functions]
        for block in chunk_modules(drawable, ids) if drawable else []:
            out += ["", block]
    document = "\n".join(out) + "\n"
    logger.debug("render_document() returning | {} caractère(s)", len(document))
    return document


def main(argv: list[str] | None = None) -> int:
    """Écrit le diagramme, ou vérifie qu'il est à jour.

    Args:
        argv: Arguments (``sys.argv[1:]`` par défaut).

    Returns:
        int: 0 si écrit ou à jour, 1 si ``--check`` le trouve périmé.
    """
    logger.trace("main() called (avant configuration des puits : niveau TRACE, muet)")
    parser = argparse.ArgumentParser(description="Génère docs/class-diagram.md depuis le code.")
    parser.add_argument("--check", action="store_true", help="code 1 si le fichier est périmé")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="fichier de sortie")
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if os.environ.get("GCM_DEBUG") else "WARNING")
    logger.debug("main() called | check={}", args.check)
    document = render_document([extract_module(p) for p in source_files()])
    current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
    if args.check:
        if current != document:
            print(
                f"{args.output} est périmé : lancer `make class-diagram` et committer le résultat."
            )
            logger.debug("main() returning | 1 (périmé)")
            return 1
        print(f"{args.output} est à jour.")
        logger.debug("main() returning | 0 (à jour)")
        return 0
    if current != document:
        args.output.write_text(document, encoding="utf-8")
        print(f"{args.output} régénéré.")
    logger.debug("main() returning | 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
