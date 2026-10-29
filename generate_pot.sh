#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# generate_pot.sh — conservé pour compatibilité : la maintenance des
# catalogues est faite par scripts/i18n-update.sh (issue #165), qui écrit
# lang/messages.pot (l'ancien chemin po/gcm.pot n'existait plus) et met à
# jour les lang/*.po listés dans lang/LINGUAS.
#
#   ./generate_pot.sh            -> scripts/i18n-update.sh
#   ./generate_pot.sh --check    -> scripts/i18n-update.sh --check
#   ./generate_pot.sh --update-po (ancien nom) -> scripts/i18n-update.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
case "${1:-}" in
    --update-po|"") exec scripts/i18n-update.sh ;;
    *) exec scripts/i18n-update.sh "$@" ;;
esac
