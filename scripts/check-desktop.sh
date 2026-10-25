#!/usr/bin/env bash
# Intégration au bureau de gcm4 (#155) : valide le lanceur, les métadonnées
# AppStream et les icônes de data/. Utilisé par le job CI « Intégration au
# bureau » et par `make ci` / `make check-desktop`.
#
# Les fichiers de data/ sont validés tels quels, puis après fusion des
# traductions (scripts/i18n-update.sh --desktop, #274) : c'est la version
# fusionnée que les paquets installent.
#
# Outils requis : desktop-file-validate (paquet desktop-file-utils),
# appstreamcli (paquet appstream) et msgfmt (paquet gettext). Un outil absent
# est une erreur, pas un saut.
set -euo pipefail

RACINE="$(cd "$(dirname "$0")/.." && pwd)"
APP_ID="io.github.MathildeDec.gcm4"
DESKTOP="$RACINE/data/$APP_ID.desktop"
METAINFO="$RACINE/data/$APP_ID.metainfo.xml"

manquants=()
command -v desktop-file-validate >/dev/null || manquants+=(desktop-file-utils)
command -v appstreamcli >/dev/null || manquants+=(appstream)
command -v msgfmt >/dev/null || manquants+=(gettext)
if [ "${#manquants[@]}" -gt 0 ]; then
    echo "outils absents, installer : sudo apt-get install ${manquants[*]}" >&2
    exit 1
fi

valider() {  # $1 = lanceur, $2 = métadonnées AppStream
    echo "== desktop-file-validate $1"
    # desktop-file-validate renvoie 0 sur un simple avertissement : toute ligne
    # affichée (erreur, avertissement ou conseil) fait échouer la vérification.
    sortie=$(desktop-file-validate "$1" 2>&1) || { echo "$sortie" >&2; exit 1; }
    if [ -n "$sortie" ]; then
        echo "$sortie" >&2
        exit 1
    fi
    echo "lanceur conforme"

    echo "== appstreamcli validate $2"
    # --no-net : pas d'accès réseau (URL et captures ne sont pas téléchargées).
    # --explain : constat détaillé ; avertissements et erreurs font échouer.
    appstreamcli validate --no-net --explain "$2"
}

valider "$DESKTOP" "$METAINFO"

FUSION="$(mktemp -d)"
trap 'rm -rf "$FUSION"' EXIT
"$RACINE/scripts/i18n-update.sh" --desktop "$FUSION"
valider "$FUSION/$APP_ID.desktop" "$FUSION/$APP_ID.metainfo.xml"

echo "== icônes"
"$RACINE/scripts/generer-icones.sh" --check
