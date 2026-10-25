"""Règles loguru vérifiées par AST (issue #171, modèle : Netcross
``tests/test_loguru_regles.py``).

Périmètre : fichiers Python suivis par git, hors ``tests/`` et des
sous-projets ``SSH-Studio/`` et ``gtk-frdp/``. Aucun module n'est importé.

1. Chaque ``except`` contient un appel de journal. Les manques existants
   sont listés par fichier dans ``EXCEPT_SANS_JOURNAL`` : le compte doit
   être **exact**, donc il ne peut que diminuer (le mettre à jour quand on
   corrige un ``except``, jamais pour en ajouter).
2. Aucun ``import logging`` (voir aussi ``tests/test_loguru_only.py``) et
   tout module qui définit des fonctions a un logger de module (liste
   ``MODULES_SANS_LOGGER``, qui ne peut que diminuer elle aussi).
3. Placeholders ``{}`` cohérents avec le nombre d'arguments, et jamais de
   ``%s`` (loguru ne l'interprète pas).
4. Aucune variable nommée comme un secret (``pwd``, ``password``,
   ``passwd``, ``passphrase``, ``key``, ``token``, ``secret``...) interpolée
   telle quelle dans un message : ``bool(password)`` ou ``len(pwd)`` sont
   acceptés, ``{password}`` non (généralise ``tests/test_secrets_journal.py``).
5. ``scripts/audit_debug_sorties.py`` (consigne « debug en entrée et à
   chaque sortie ») : total inférieur ou égal à
   ``scripts/audit_debug_sorties.baseline``.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import string
import subprocess
import sys
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = ("SSH-Studio/", "gtk-frdp/", "tests/")
LOGGER_NAMES = {"logger", "app_logger", "_logger", "log", "_log", "_loguru_logger"}
LOG_METHODS = {"trace", "debug", "info", "success", "warning", "error", "exception", "critical"}
SECRET_NAMES = re.compile(
    r"(pwd|passwd|password|passphrase|key|token|secret|api_?key|private_key|credentials?)",
    re.IGNORECASE,
)

# Fichier -> nombre d'``except`` sans appel de journal (état de l'issue #171).
EXCEPT_SANS_JOURNAL = {
    "gnome_connection_manager.py": 38,
    "plugins/plugin_base.py": 2,
    "plugins/plugin_import_libvirt.py": 1,
    "plugins/plugin_import_ovirt.py": 4,
    "plugins/plugin_import_proxmox.py": 2,
    "plugins/plugin_ipmisol.py": 1,
    "plugins/plugin_netmiko_push.py": 6,
    "plugins/plugin_serial.py": 1,
    "plugins/plugin_snmp_push.py": 6,
    "plugins/plugin_spice.py": 8,
    "plugins/plugin_ssh.py": 3,
    "plugins/plugin_vnc.py": 7,
    "plugins/plugin_web.py": 1,
    "plugins/pluginvnc2/vnc_tab.py": 14,
    "plugins/ssh/core.py": 14,
    "snmp_bulk_core.py": 13,
    "snmp_push_core.py": 15,
    "ssh_config_editor.py": 4,
    "ssh_key_manager_dialog.py": 17,
    "tools/libvirt_inventory.py": 3,
    "tools/ssh_deploy.py": 1,
    "tools/validate_xml_json.py": 4,
    "widgets.py": 3,
}

# Modules qui définissent des fonctions sans logger de module.
MODULES_SANS_LOGGER: set[str] = set()


def python_files() -> list[str]:
    """Fichiers du périmètre (suivis par git, hors tests et sous-projets)."""
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [f for f in out.stdout.split() if not f.startswith(EXCLUDED) and "/tests/" not in f]


def parse(path: str) -> ast.Module:
    """AST d'un fichier, sans les SyntaxWarning des docstrings existantes."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse((ROOT / path).read_text(encoding="utf-8"))


def is_log_call(node: ast.AST) -> bool:
    """``logger.info(...)``, ``self.logger.debug(...)``, ``app_logger.exception(...)``..."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    if node.func.attr not in LOG_METHODS | {"log"}:
        return False
    target = node.func.value
    return (isinstance(target, ast.Name) and target.id in LOGGER_NAMES) or (
        isinstance(target, ast.Attribute) and target.attr in LOGGER_NAMES
    )


def except_sans_journal(tree: ast.Module) -> int:
    """Nombre d'``except`` qui ne contiennent aucun appel de journal."""
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and not any(is_log_call(n) for n in ast.walk(node))
    )


def a_un_logger_de_module(tree: ast.Module) -> bool:
    """Logger défini au niveau module (y compris dans un try/if de premier niveau)."""
    statements = list(tree.body)
    for node in tree.body:
        if isinstance(node, (ast.Try, ast.If)):
            statements += (
                node.body
                + node.orelse
                + [s for h in getattr(node, "handlers", []) for s in h.body]
            )
    for node in statements:
        if isinstance(node, ast.ImportFrom) and node.module == "loguru":
            if any((a.asname or a.name) in LOGGER_NAMES for a in node.names):
                return True
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) in LOGGER_NAMES for t in node.targets
        ):
            return True
    return False


def placeholders(fmt: str) -> int | None:
    """Nombre de champs ``{}`` d'un format, None s'il est invalide."""
    try:
        return sum(1 for _, field, _, _ in string.Formatter().parse(fmt) if field is not None)
    except ValueError:
        return None


def erreurs_de_format(tree: ast.Module) -> list[str]:
    """Appels de journal dont le format ne correspond pas aux arguments."""
    fautes = []
    for node in ast.walk(tree):
        if not is_log_call(node) or not node.args:
            continue
        first = node.args[0]
        if node.func.attr == "log":  # logger.log(niveau, message, ...)
            if len(node.args) < 2:
                continue
            first, extra = node.args[1], len(node.args) - 2
        else:
            extra = len(node.args) - 1
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        if re.search(r"%[sdrifx]", first.value) and extra:
            fautes.append(f"{node.lineno}: %-format (loguru attend {{}})")
            continue
        if extra or node.keywords:
            count = placeholders(first.value)
            if count is not None and not node.keywords and count != extra:
                fautes.append(f"{node.lineno}: {count} champ(s) {{}} pour {extra} argument(s)")
    return fautes


def _nom(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def secrets_journalises(tree: ast.Module) -> list[str]:
    """Variable de secret passée telle quelle (f-string ou argument) à un appel de journal."""
    fautes = []
    for node in ast.walk(tree):
        if not is_log_call(node):
            continue
        valeurs = [
            v.value for a in node.args for v in ast.walk(a) if isinstance(v, ast.FormattedValue)
        ]
        valeurs += node.args[1:] + [k.value for k in node.keywords]
        for valeur in valeurs:
            nom = _nom(valeur)
            if nom and SECRET_NAMES.fullmatch(nom):
                fautes.append(f"{node.lineno}: {nom}")
    return fautes


def _audit():
    spec = importlib.util.spec_from_file_location(
        "audit_debug_sorties", ROOT / "scripts" / "audit_debug_sorties.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_debug_sorties"] = module
    spec.loader.exec_module(module)
    return module


class TestReglesLoguru(unittest.TestCase):
    """Règles bloquantes du job Qualité."""

    @classmethod
    def setUpClass(cls):
        """Analyse chaque fichier une seule fois."""
        cls.trees = {f: parse(f) for f in python_files()}

    def test_except_sans_journal_ne_peut_que_diminuer(self):
        """Compte exact par fichier : un nouvel except muet fait échouer."""
        actuel = {f: n for f, t in self.trees.items() if (n := except_sans_journal(t))}
        self.assertEqual(
            actuel,
            EXCEPT_SANS_JOURNAL,
            "chaque except doit journaliser (logger.exception / logger.warning) ; "
            "si vous en avez corrigé, baissez EXCEPT_SANS_JOURNAL",
        )

    def test_logger_de_module(self):
        """Tout module qui définit des fonctions a un logger loguru."""
        actuel = {
            f
            for f, t in self.trees.items()
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(t))
            and not a_un_logger_de_module(t)
        }
        self.assertEqual(actuel, MODULES_SANS_LOGGER, "ajouter `from loguru import logger`")

    def test_aucun_import_logging(self):
        """Pas de module logging standard."""
        fautes = [
            f"{f}:{n.lineno}"
            for f, t in self.trees.items()
            for n in ast.walk(t)
            if (
                isinstance(n, ast.Import)
                and any(a.name.split(".")[0] == "logging" for a in n.names)
            )
            or (
                isinstance(n, ast.ImportFrom)
                and n.level == 0
                and (n.module or "").split(".")[0] == "logging"
            )
        ]
        self.assertEqual(fautes, [])

    def test_placeholders(self):
        """{} cohérents avec les arguments, jamais de %s."""
        fautes = {f: e for f, t in self.trees.items() if (e := erreurs_de_format(t))}
        self.assertEqual(fautes, {})

    def test_aucun_secret_interpole(self):
        """Ni {password} ni logger.debug("...", token) ; bool()/len() acceptés."""
        fautes = {f: e for f, t in self.trees.items() if (e := secrets_journalises(t))}
        self.assertEqual(
            fautes, {}, "journaliser bool(x), len(x) ou logging_config.summarize(x, nom)"
        )

    def test_audit_debug_sorties_ne_doit_pas_augmenter(self):
        """Compteur de la consigne « debug en entrée et à chaque sortie »."""
        audit = _audit()
        total = sum(len(v) for v in audit.audit_repository().values())
        reference = audit.read_baseline()
        self.assertLessEqual(
            total,
            reference,
            "nouvelles fonctions sans debug() en entrée/sortie : scripts/audit_debug_sorties.py --details",
        )


class TestDetecteurs(unittest.TestCase):
    """Les détecteurs eux-mêmes."""

    def test_except(self):
        """Un except muet compte, un except journalisé non."""
        tree = ast.parse(
            "try:\n    x()\nexcept A:\n    pass\nexcept B:\n    logger.warning('b')\n"
        )
        self.assertEqual(except_sans_journal(tree), 1)

    def test_format(self):
        """Champ manquant et %s détectés ; f-string et format correct acceptés."""
        tree = ast.parse(
            'logger.info("a {} b {}", x)\nlogger.info("a %s", x)\nlogger.info("ok {}", x)\nlogger.info(f"{x}")\n'
        )
        self.assertEqual([e.split(":")[0] for e in erreurs_de_format(tree)], ["1", "2"])

    def test_secrets(self):
        """Secret interpolé ou passé en argument détecté ; bool() et sous-clé acceptés."""
        tree = ast.parse(
            'logger.debug(f"p={password}")\nlogger.debug(f"p={bool(password)}")\n'
            'logger.debug("t={}", self.token)\nlogger.debug(f"n={key[\'name\']}")\n'
        )
        self.assertEqual([e.split(":")[0] for e in secrets_journalises(tree)], ["1", "3"])

    def test_audit(self):
        """Fonction tracée en entrée et à chaque sortie conforme, l'autre non."""
        audit = _audit()
        source = (
            "def ok(x):\n    logger.debug('in')\n    if x:\n        logger.debug('out')\n        return 1\n"
            "    logger.debug('out')\n    return 2\n"
            "def ko(x):\n    if x:\n        return 1\n    return 2\n"
        )
        self.assertEqual(
            [(n, p) for _, n, p in audit.audit_source(source)], [("ko", ["entrée", "sortie"])]
        )


if __name__ == "__main__":
    unittest.main()
