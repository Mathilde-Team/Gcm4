#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# build-deb/build.sh -- construit le paquet .deb de la base GTK4 gcm4 (#156).
#
# Sur le modèle de Netcross : une arborescence temporaire (_build/) réunit ce
# que debian/rules installe (sources du wheel, data/, catalogues compilés,
# lanceur), dpkg-buildpackage y construit le paquet, qui est déposé dans
# build-deb/dist/.
#
# Version : celle de pyproject.toml, seule source. Le paquet et
# « gcm4 --version » (lue dans le .dist-info du wheel) affichent donc la même.
#
# Paquet sans aucun plugin : voir debian/control.
#
# Prérequis (Debian 13, Ubuntu 24.04 ou plus récent) :
#   apt-get install build-essential debhelper dh-python dpkg-dev gettext git \
#     python3-build python3-hatchling
# (build-essential : exigé par dpkg-buildpackage, même pour un paquet « all »)
#
# Usage : build-deb/build.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/_build"
DIST_DIR="$SCRIPT_DIR/dist"

VERSION="$(sed -n 's/^version *= *"\(.*\)"/\1/p' "$REPO_ROOT/pyproject.toml" | head -n 1)"
[ -n "$VERSION" ] || { echo "version introuvable dans pyproject.toml" >&2; exit 1; }

echo "=== gcm4 $VERSION : construction du paquet .deb ==="

rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# Sources du wheel ([tool.hatch.build.targets.wheel] de pyproject.toml) :
# les fichiers suivis par git seulement, sans __pycache__ ni fichier local.
# README.md et LICENSE sont lus par hatchling (métadonnées du wheel).
(cd "$REPO_ROOT" && git ls-files -z -- src/gcm4 gcm4_core.py logging_config.py master_password_core.py pyAES.py \
    pyproject.toml README.md LICENSE NOTICE data \
    | xargs -0 cp --parents -t "$BUILD_DIR")
cp -r "$SCRIPT_DIR/debian" "$SCRIPT_DIR/wrappers" "$BUILD_DIR/"

# Catalogues des langues de lang/LINGUAS (#129), installés dans
# /usr/share/locale/<langue>/LC_MESSAGES/gcm-lang.mo : gcm4.core.i18n les y
# cherche (sys.prefix/share/locale).
"$REPO_ROOT/scripts/i18n-update.sh" --compile "$BUILD_DIR/locale"
# Lanceur et métadonnées AppStream avec leurs traductions (#274) : remplacent
# les fichiers anglais de data/ copiés plus haut.
"$REPO_ROOT/scripts/i18n-update.sh" --desktop "$BUILD_DIR/data"

# debian/changelog : une entrée, la version de pyproject.toml.
cat > "$BUILD_DIR/debian/changelog" <<CHANGELOG
gcm4 ($VERSION) unstable; urgency=medium

  * Paquet de la base GTK4 gcm4 $VERSION, sans plugin.

 -- MathildeDec <mathilde.deuscher@gmail.com>  $(LC_ALL=C date -R)
CHANGELOG

(cd "$BUILD_DIR" && dpkg-buildpackage -us -uc -b)

# dpkg-buildpackage dépose le .deb à côté de l'arborescence source.
mv "$SCRIPT_DIR"/gcm4_"$VERSION"_all.deb "$DIST_DIR/"
rm -f "$SCRIPT_DIR"/gcm4_"$VERSION"_*.buildinfo "$SCRIPT_DIR"/gcm4_"$VERSION"_*.changes

echo ""
echo "=== Paquet généré : build-deb/dist/gcm4_${VERSION}_all.deb ==="
echo "Installation : sudo apt install ./build-deb/dist/gcm4_${VERSION}_all.deb"
