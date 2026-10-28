#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Taux de traduction par langue des catalogues ``lang/*.po`` (issue #166).

Lit ``lang/LINGUAS`` (seule liste des langues, issue #165) et compte, pour
chaque ``lang/<locale>.po``, les entrées traduites, approximatives
(« fuzzy », non utilisées à l'exécution) et vides. L'en-tête (msgid vide)
et les entrées obsolètes (``#~``) sont ignorés.

Usage :

    scripts/i18n_report.py              # tableau texte
    scripts/i18n_report.py --markdown   # tableau Markdown

Dans GitHub Actions, le tableau Markdown est aussi ajouté au résumé du
job (``$GITHUB_STEP_SUMMARY``). Le rapport est informatif : le code de
retour vaut 0 sauf si un catalogue listé manque ou ne peut pas être lu (2).
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
LANG_DIR = ROOT / "lang"


@dataclass(frozen=True)
class Stats:
    """Compteurs d'un catalogue."""

    locale: str
    translated: int
    fuzzy: int
    untranslated: int

    @property
    def total(self) -> int:
        """Nombre d'entrées actives (hors en-tête et obsolètes)."""
        return self.translated + self.fuzzy + self.untranslated

    @property
    def percent(self) -> float:
        """Part des entrées traduites et utilisables, en pourcentage."""
        return 100.0 * self.translated / self.total if self.total else 0.0


def read_linguas(lang_dir: Path = LANG_DIR) -> list[str]:
    """Locales déclarées dans ``lang/LINGUAS`` (commentaires ignorés).

    Args:
        lang_dir: Dossier des catalogues.

    Returns:
        list[str]: Locales, dans l'ordre du fichier.
    """
    logger.debug("read_linguas() called | lang_dir={}", lang_dir)
    locales = []
    for line in (lang_dir / "LINGUAS").read_text(encoding="utf-8").splitlines():
        locales.extend(line.split("#", 1)[0].split())
    logger.debug("read_linguas() returning | {} locale(s)", len(locales))
    return locales


def _unquote(fragment: str) -> str:
    """Décode une chaîne PO entre guillemets (mêmes échappements que C)."""
    return ast.literal_eval(fragment)


def parse_po(text: str) -> list[tuple[str, list[str], bool]]:
    """Entrées actives d'un catalogue : (msgid, msgstr[], fuzzy).

    Args:
        text: Contenu du fichier .po.

    Returns:
        list[tuple[str, list[str], bool]]: Une entrée par msgid, en-tête compris.
    """
    logger.debug("parse_po() called | {} caractères", len(text))
    entries: list[tuple[str, list[str], bool]] = []
    entry: dict | None = None
    pending_fuzzy = False
    current = ""  # dernier mot-clé lu : msgctxt, msgid, msgid_plural, msgstr

    def flush() -> None:
        if entry is not None and entry["msgid"] is not None:
            entries.append((entry["msgid"], entry["msgstr"], entry["fuzzy"]))

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#~"):
            continue
        if line.startswith("#,"):
            pending_fuzzy = "fuzzy" in line[2:].replace(",", " ").split()
            continue
        if line.startswith("#"):
            continue
        if line.startswith('"'):  # suite de la chaîne précédente
            if current == "msgid":
                entry["msgid"] += _unquote(line)
            elif current == "msgstr":
                entry["msgstr"][-1] += _unquote(line)
            continue
        keyword, _, rest = line.partition(" ")
        if keyword == "msgctxt" or (keyword == "msgid" and current != "msgctxt"):
            flush()
            entry = {"msgid": None, "msgstr": [], "fuzzy": pending_fuzzy}
            pending_fuzzy = False
        if keyword == "msgid":
            entry["msgid"] = _unquote(rest)
        elif keyword.startswith("msgstr"):
            entry["msgstr"].append(_unquote(rest))
            keyword = "msgstr"
        current = keyword
    flush()
    logger.debug("parse_po() returning | {} entrée(s)", len(entries))
    return entries


def catalog_stats(locale: str, text: str) -> Stats:
    """Compte les entrées traduites, fuzzy et vides d'un catalogue.

    Args:
        locale: Code de la langue (ex. ``fr_FR``).
        text: Contenu du fichier .po.

    Returns:
        Stats: Compteurs, hors en-tête.
    """
    logger.debug("catalog_stats() called | locale={}", locale)
    translated = fuzzy = untranslated = 0
    for msgid, msgstrs, is_fuzzy in parse_po(text):
        if msgid == "":
            continue
        if is_fuzzy:
            fuzzy += 1
        elif msgstrs and all(msgstrs):
            translated += 1
        else:
            untranslated += 1
    stats = Stats(locale, translated, fuzzy, untranslated)
    logger.debug("catalog_stats() returning | {}", stats)
    return stats


def collect(lang_dir: Path = LANG_DIR) -> tuple[list[Stats], list[str]]:
    """Statistiques de toutes les langues de ``LINGUAS``.

    Args:
        lang_dir: Dossier des catalogues.

    Returns:
        tuple[list[Stats], list[str]]: Statistiques triées par taux
        décroissant, puis messages d'erreur (catalogue manquant ou illisible).
    """
    logger.debug("collect() called | lang_dir={}", lang_dir)
    stats, errors = [], []
    for locale in read_linguas(lang_dir):
        path = lang_dir / f"{locale}.po"
        try:
            stats.append(catalog_stats(locale, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, ValueError, SyntaxError) as exc:
            logger.warning("catalogue {} illisible : {}", path.name, exc)
            errors.append(f"lang/{locale}.po : {exc}")
    stats.sort(key=lambda s: (-s.percent, s.locale))
    logger.debug("collect() returning | {} langue(s), {} erreur(s)", len(stats), len(errors))
    return stats, errors


def render(stats: list[Stats], markdown: bool) -> str:
    """Tableau texte ou Markdown, avec une ligne de synthèse.

    Args:
        stats: Statistiques par langue.
        markdown: ``True`` pour un tableau Markdown.

    Returns:
        str: Le rapport.
    """
    logger.debug("render() called | {} langue(s) markdown={}", len(stats), markdown)
    total = stats[0].total if stats else 0
    complete = sum(1 for s in stats if s.total and s.translated == s.total)
    if markdown:
        lines = [
            "## Traductions",
            "",
            f"{len(stats)} langue(s), {total} chaîne(s) par catalogue, {complete} langue(s) complète(s).",
            "",
            "| Langue | Traduites | Fuzzy | Vides | Taux |",
            "|---|---:|---:|---:|---:|",
        ]
        lines += [
            f"| `{s.locale}` | {s.translated} | {s.fuzzy} | {s.untranslated} | {s.percent:.1f} % |"
            for s in stats
        ]
    else:
        lines = [f"{'Langue':<8} {'Traduites':>9} {'Fuzzy':>6} {'Vides':>6} {'Taux':>7}"]
        lines += [
            f"{s.locale:<8} {s.translated:>9} {s.fuzzy:>6} {s.untranslated:>6} {s.percent:>6.1f}%"
            for s in stats
        ]
        lines.append(f"{len(stats)} langue(s), {total} chaîne(s), {complete} complète(s)")
    report = "\n".join(lines) + "\n"
    logger.debug("render() returning | {} ligne(s)", len(lines))
    return report


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée.

    Args:
        argv: Arguments (``sys.argv[1:]`` par défaut).

    Returns:
        int: 0, ou 2 si un catalogue manque ou est illisible.
    """
    parser = argparse.ArgumentParser(description="Taux de traduction par langue (lang/*.po)")
    parser.add_argument("--markdown", action="store_true", help="tableau Markdown")
    parser.add_argument("--lang-dir", type=Path, default=LANG_DIR, help="dossier des catalogues")
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if os.environ.get("GCM_DEBUG") else "WARNING")
    logger.debug("main() called | markdown={} lang_dir={}", args.markdown, args.lang_dir)

    stats, errors = collect(args.lang_dir)
    sys.stdout.write(render(stats, args.markdown))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(render(stats, markdown=True))
    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    code = 2 if errors else 0
    logger.debug("main() returning | code={}", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
