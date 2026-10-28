#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Icônes du lanceur gcm4 (#155) aux tailles standard du thème hicolor, à partir
# de icon.png (128×128) : réduction seulement, jamais d'agrandissement (une
# taille 256 ou 512 demanderait une source vectorielle).
#
# Usage : scripts/generer-icones.sh            régénère data/icons/hicolor/
#         scripts/generer-icones.sh --check    vérifie présence et dimensions
#
# Les PNG produits sont versionnés : la CI ne les régénère pas (le rendu varie
# d'une version d'ImageMagick à l'autre), elle vérifie leurs dimensions
# (scripts/check-desktop.sh et tests/test_integration_bureau.py).
set -euo pipefail

RACINE="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$RACINE/icon.png"
APP_ID="io.github.MathildeDec.gcm4"
TAILLES=(16 22 24 32 48 64 128)
DEST="$RACINE/data/icons/hicolor"

dimensions() {
    # Largeur×hauteur lues dans l'en-tête PNG (octets 16 à 23), sans outil externe.
    od -An -tu1 -j16 -N8 "$1" | awk '{printf "%dx%d\n", $1*16777216+$2*65536+$3*256+$4, $5*16777216+$6*65536+$7*256+$8}'
}

if [ "${1:-}" = "--check" ]; then
    erreurs=0
    for t in "${TAILLES[@]}"; do
        f="$DEST/${t}x${t}/apps/$APP_ID.png"
        if [ ! -f "$f" ]; then
            echo "icône absente : $f" >&2
            erreurs=1
        elif [ "$(dimensions "$f")" != "${t}x${t}" ]; then
            echo "dimensions inattendues pour $f : $(dimensions "$f")" >&2
            erreurs=1
        fi
    done
    [ "$erreurs" = 0 ] && echo "icônes : ${#TAILLES[@]} tailles conformes"
    exit "$erreurs"
fi

command -v magick >/dev/null || command -v convert >/dev/null || {
    echo "ImageMagick requis (sudo apt-get install imagemagick)" >&2
    exit 1
}
IM=$(command -v magick || command -v convert)
for t in "${TAILLES[@]}"; do
    mkdir -p "$DEST/${t}x${t}/apps"
    # -strip et exclusion des dates : fichier identique d'une génération à l'autre.
    "$IM" "$SOURCE" -filter Lanczos -resize "${t}x${t}" -strip \
        -define png:exclude-chunks=date,time "$DEST/${t}x${t}/apps/$APP_ID.png"
done
echo "icônes générées dans $DEST"
