#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Même seuil que Python, sans recopier 95 dans les paramètres Node.
set -euo pipefail
cd "$(dirname "$0")/.."
seuil=$(uv run python -c 'import pathlib,tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["tool"]["coverage"]["report"]["fail_under"])')
node --experimental-test-coverage \
  --test-coverage-include=src/gcm4/ui/xterm_assets/terminal.js \
  --test-coverage-lines="$seuil" --test-coverage-branches="$seuil" \
  --test tests/xterm/terminal.test.cjs
