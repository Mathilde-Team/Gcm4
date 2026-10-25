#!/usr/bin/env bash
# build-rpm/build.sh -- construit le paquet .rpm de la base GTK4 gcm4 (#157).
#
# Sur le modèle de Netcross : une archive gcm4-<version>.tar.gz réunit ce
# que gcm4.spec installe (wheel de pyproject.toml, data/, catalogues
# compilés, lanceur commun avec le .deb), puis rpmbuild construit le paquet
# dans une arborescence locale (_build/rpmbuild), déposé dans build-rpm/dist/.
#
# Version : celle de pyproject.toml, seule source, passée au spec.
#
# Prérequis :
#   Fedora : dnf install rpm-build gettext git python3-build python3-hatchling
#            python3-rpm-macros
#   Debian/Ubuntu (construction croisée) : apt-get install rpm gettext git
#            python3-build python3-hatchling
#
# Usage : build-rpm/build.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION="$(sed -n 's/^version *= *"\(.*\)"/\1/p' "$REPO_ROOT/pyproject.toml" | head -n 1)"
[ -n "$VERSION" ] || { echo "version introuvable dans pyproject.toml" >&2; exit 1; }
BUILD_DIR="$SCRIPT_DIR/_build"
SRC_DIR="$BUILD_DIR/src"
STAGE_DIR="$BUILD_DIR/gcm4-$VERSION"
TOPDIR="$BUILD_DIR/rpmbuild"
DIST_DIR="$SCRIPT_DIR/dist"
# Python de la distribution (un python3 de /usr/local n'aurait ni build ni
# hatchling).
PYTHON=/usr/bin/python3

echo "=== gcm4 $VERSION : construction du paquet .rpm ==="

rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$SRC_DIR" "$STAGE_DIR" "$DIST_DIR" "$TOPDIR"/{SOURCES,SPECS,BUILD,RPMS,SRPMS,BUILDROOT}

# Wheel : mêmes sources que le .deb (fichiers suivis par git seulement).
(cd "$REPO_ROOT" && git ls-files -z -- src/gcm4 gcm4_core.py logging_config.py pyAES.py \
    pyproject.toml README.md LICENSE \
    | xargs -0 cp --parents -t "$SRC_DIR")
"$PYTHON" -m build --wheel --no-isolation --outdir "$STAGE_DIR" "$SRC_DIR"
# Nom de fichier imposé par le spec, indépendant du nom de distribution.
mv "$STAGE_DIR"/*-"$VERSION"-py3-none-any.whl "$STAGE_DIR/gcm4-$VERSION-py3-none-any.whl"

(cd "$REPO_ROOT" && git ls-files -z -- data LICENSE | xargs -0 cp --parents -t "$STAGE_DIR")
cp "$REPO_ROOT/build-deb/wrappers/gcm4" "$STAGE_DIR/gcm4"
"$REPO_ROOT/scripts/i18n-update.sh" --compile "$STAGE_DIR/locale"
# Lanceur et métadonnées AppStream avec leurs traductions (#274) : remplacent
# les fichiers anglais de data/ copiés plus haut.
"$REPO_ROOT/scripts/i18n-update.sh" --desktop "$STAGE_DIR/data"

tar czf "$TOPDIR/SOURCES/gcm4-$VERSION.tar.gz" -C "$BUILD_DIR" "gcm4-$VERSION"
cp "$SCRIPT_DIR/gcm4.spec" "$TOPDIR/SPECS/"

rpmbuild --define "_topdir $TOPDIR" --define "gcm4_version $VERSION" \
    -bb "$TOPDIR/SPECS/gcm4.spec"

find "$TOPDIR/RPMS" -name "gcm4-$VERSION-*.noarch.rpm" -exec cp {} "$DIST_DIR/" \;
ls "$DIST_DIR"/gcm4-"$VERSION"-*.noarch.rpm >/dev/null

echo ""
echo "=== Paquet généré dans build-rpm/dist/ ==="
ls -l "$DIST_DIR"
echo "Installation : sudo dnf install ./build-rpm/dist/gcm4-${VERSION}-1.noarch.rpm"
