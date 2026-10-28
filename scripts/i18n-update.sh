#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# scripts/i18n-update.sh -- maintenance des catalogues gettext (issue #165,
# sur le modèle de Netcross scripts/i18n-update.sh).
#
#   scripts/i18n-update.sh              xgettext -> lang/messages.pot,
#                                       msgmerge lang/*.po (crée les .po
#                                       manquants avec msginit),
#                                       msgfmt --check
#   scripts/i18n-update.sh --check      vérifie sans rien écrire : .pot et .po
#                                       à jour par rapport aux sources (dates
#                                       POT-Creation-Date/PO-Revision-Date
#                                       ignorées), catalogues valides (CI)
#   scripts/i18n-update.sh --compile [DIR]
#                                       msgfmt --check puis compilation en
#                                       DIR/<locale>/LC_MESSAGES/<domaine>.mo
#                                       (défaut : lang, lu par bindtextdomain)
#   scripts/i18n-update.sh --desktop DIR
#                                       msgfmt --desktop / --xml : lanceur et
#                                       métadonnées AppStream de data/ avec
#                                       les traductions, écrits dans DIR
#                                       (paquets .deb/.rpm, #274)
#
# Langues : lang/LINGUAS, nulle part ailleurs. Les nouvelles chaînes arrivent
# avec msgstr "" : aucune traduction automatique n'est insérée.
# Domaine : gcm-lang tant que le renommage (#99) n'est pas tranché ;
# GCM_I18N_DOMAIN permet de compiler sous un autre nom.
# Sources : fichiers Python suivis par git, hors tests/, tools/, scripts/ et
# des sous-projets qui gèrent leurs propres catalogues (SSH-Studio/,
# gtk-frdp/) ; lanceur et métadonnées AppStream de gcm4 dans data/ (#274).
# Codes de langue du lanceur et d'AppStream : la langue seule (fr, de...),
# sauf pt_BR et zh_CN qui ont leur variante ; un « fr » couvre fr_BE, fr_CA...
#
# Prérequis : gettext (apt-get install gettext / dnf install gettext).

set -euo pipefail
export LC_ALL=C.UTF-8

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LANG_DIR="$ROOT/lang"
# Règles ITS AppStream du dépôt (lang/its, #274) : xgettext et msgfmt --xml
# les trouvent dans $GETTEXTDATADIRS/its. Sans elles, le résultat dépendait
# de la machine : gettext 0.21 n'a pas de règle pour *.metainfo.xml (« cannot
# locate ITS rules », sauf si le paquet appstream est installé) et la règle
# intégrée à gettext 0.23 n'extrait pas les notes de version.
export GETTEXTDATADIRS="$LANG_DIR${GETTEXTDATADIRS:+:$GETTEXTDATADIRS}"
POT="$LANG_DIR/messages.pot"
DOMAIN="${GCM_I18N_DOMAIN:-gcm-lang}"
MODE="update"
DEST="$LANG_DIR"

case "${1:-}" in
    "") ;;
    --check) MODE="check" ;;
    --compile) MODE="compile"; DEST="${2:-$DEST}" ;;
    --desktop)
        MODE="desktop"
        [ -n "${2:-}" ] || { echo "--desktop : dossier de sortie requis" >&2; exit 2; }
        DEST="$2" ;;
    -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
esac

for tool in xgettext msgmerge msgfmt msginit msgfilter msgattrib; do
    command -v "$tool" >/dev/null || { echo "gettext requis ($tool introuvable)" >&2; exit 2; }
done

linguas() { sed -e 's/#.*//' -e 's/[[:space:]]//g' "$LANG_DIR/LINGUAS" | grep -v '^$'; }

# Contenu comparable : sans les dates de génération et sans les coupures de
# lignes (deux versions de gettext ne coupent pas au même endroit).
normalize() {
    grep -vE '^"(POT-Creation-Date|PO-Revision-Date):' "$1" | msgcat --no-wrap --sort-by-file - 2>/dev/null || true
}

APP_ID="io.github.MathildeDec.gcm4"
DESKTOP="data/$APP_ID.desktop"
METAINFO="data/$APP_ID.metainfo.xml"

# Code de langue d'un .po dans le lanceur et AppStream (voir en-tête).
desktop_locale() {
    case "$1" in
        pt_BR|zh_CN) echo "$1" ;;
        *) echo "${1%%_*}" ;;
    esac
}

sources() {
    (cd "$ROOT" && git ls-files '*.py' \
        | grep -vE '^(tests|tools|scripts|SSH-Studio|gtk-frdp)/' | sort -u)
}

generate_pot() {
    local out="$1"
    (cd "$ROOT" && sources | xgettext --files-from=- --output="$out" \
        --language=Python --from-code=UTF-8 \
        --keyword=_ --keyword=N_ --keyword=ngettext:1,2 --keyword=pgettext:1c,2 \
        --add-comments=TRANSLATORS: --add-location=file --sort-by-file --no-wrap \
        --package-name="GNOME Connection Manager" \
        --msgid-bugs-address="https://github.com/Mathilde-Team/Gcm4/issues" \
        --copyright-holder="Mathilde Deuscher" 2>"$TMP/xgettext.log")
    # Lanceur (clés traduisibles de la spécification desktop) et AppStream
    # (règles ITS de lang/its, voir GETTEXTDATADIRS plus haut).
    # Ajoutées au même modèle (--join-existing), à la suite des chaînes
    # Python : une chaîne déjà présente (« Gnome Connection Manager »)
    # garde sa place et gagne la référence data/. Mêmes options d'en-tête,
    # sinon xgettext remet l'en-tête par défaut.
    local entete=(--package-name="GNOME Connection Manager"
        --msgid-bugs-address="https://github.com/Mathilde-Team/Gcm4/issues"
        --copyright-holder="Mathilde Deuscher")
    (cd "$ROOT" && xgettext --join-existing --output="$out" --language=Desktop \
        --keyword= --keyword=Name --keyword=GenericName --keyword=Comment --keyword=Keywords \
        --add-location=file --no-wrap "${entete[@]}" "$DESKTOP" 2>>"$TMP/xgettext.log")
    (cd "$ROOT" && xgettext --join-existing --output="$out" --from-code=UTF-8 \
        --add-location=file --no-wrap "${entete[@]}" "$METAINFO" 2>>"$TMP/xgettext.log")
    # xgettext laisse CHARSET dans l'en-tête du modèle ; les .po héritent d'UTF-8.
    sed -i 's/charset=CHARSET/charset=UTF-8/' "$out"
}

merge_po() {  # $1 = .po existant, $2 = .pot, $3 = sortie
    msgmerge --quiet --add-location=file --sort-by-file --no-wrap --previous --output-file="$3" "$1" "$2"
}

same() { diff -q <(normalize "$1") <(normalize "$2") >/dev/null; }

# Clés d'un catalogue : msgctxt/msgid/msgid_plural des entrées actives.
# Pour le contrôle --check d'un .po, c'est la seule chose comparée : le
# rapprochement approximatif (« fuzzy ») de msgmerge et les msgstr qu'il
# propose varient d'une version de gettext à l'autre (0.21 sur les runners,
# 0.23 en local), alors que l'ensemble des chaînes à traduire, lui, est
# déterminé par messages.pot.
keys() {
    msgattrib --no-obsolete --no-wrap "$1" 2>/dev/null | grep -E '^(msgctxt|msgid|msgid_plural) ' | LC_ALL=C sort
}

check_all() {
    local rc=0 po loc
    for loc in $(linguas); do
        po="$LANG_DIR/$loc.po"
        if [ ! -f "$po" ]; then echo "manquant : lang/$loc.po (lancer scripts/i18n-update.sh)" >&2; rc=1; continue; fi
        # Les en-têtes Last-Translator/Language-Team laissés à leur valeur
        # par défaut ne sont que des avertissements : on ne les affiche pas.
        msgfmt --check --output-file=/dev/null "$po" 2> >(grep -v 'still has the initial default value' >&2) \
            || { echo "invalide : lang/$loc.po" >&2; rc=1; }
    done
    for po in "$LANG_DIR"/*.po; do
        [ -e "$po" ] || continue
        loc="$(basename "$po" .po)"
        linguas | grep -qx "$loc" || { echo "lang/$loc.po absent de lang/LINGUAS" >&2; rc=1; }
    done
    return $rc
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

case "$MODE" in
update)
    generate_pot "$TMP/messages.pot"
    if [ -f "$POT" ] && same "$POT" "$TMP/messages.pot"; then
        echo "lang/messages.pot inchangé"
    else
        cp "$TMP/messages.pot" "$POT"; echo "lang/messages.pot mis à jour ($(grep -c '^msgid ' "$POT") entrées)"
    fi
    for loc in $(linguas); do
        po="$LANG_DIR/$loc.po"
        if [ ! -f "$po" ]; then
            msginit --no-translator --no-wrap --locale="$loc" --input="$POT" --output-file="$po" 2>/dev/null
            sed -i 's/charset=ASCII/charset=UTF-8/' "$po"
            # msginit recopie les msgid pour l'anglais : ce seraient de fausses
            # traductions. Tout repart vide.
            msgfilter --keep-header --no-wrap --input="$po" --output-file="$po" true
            if grep -q 'nplurals=INTEGER' "$po"; then
                echo "lang/$loc.po créé : msginit ne connaît pas les pluriels de '$loc'," \
                     "renseigner l'en-tête Plural-Forms avant de relancer" >&2
                exit 1
            fi
            echo "lang/$loc.po créé"
        fi
        merge_po "$po" "$POT" "$TMP/$loc.po"
        if ! same "$po" "$TMP/$loc.po"; then
            cp "$TMP/$loc.po" "$po"; echo "lang/$loc.po mis à jour"
        fi
    done
    check_all
    ;;
check)
    rc=0
    generate_pot "$TMP/messages.pot"
    if [ ! -f "$POT" ] || ! diff -u <(normalize "$POT") <(normalize "$TMP/messages.pot") >"$TMP/pot.diff"; then
        echo "::error::lang/messages.pot n'est plus à jour avec les sources : lancer scripts/i18n-update.sh" >&2
        head -60 "$TMP/pot.diff" >&2 || true
        rc=1
    fi
    for loc in $(linguas); do
        po="$LANG_DIR/$loc.po"
        [ -f "$po" ] || continue
        if ! diff -u <(keys "$POT") <(keys "$po") >"$TMP/$loc.diff"; then
            echo "::error::lang/$loc.po n'est plus synchronisé avec messages.pot : lancer scripts/i18n-update.sh" >&2
            head -20 "$TMP/$loc.diff" >&2 || true
            rc=1
        fi
    done
    check_all || rc=1
    [ $rc -eq 0 ] && echo "catalogues à jour : $(linguas | wc -l) langue(s)"
    exit $rc
    ;;
compile)
    check_all
    for loc in $(linguas); do
        mkdir -p "$DEST/$loc/LC_MESSAGES"
        msgfmt --check --output-file="$DEST/$loc/LC_MESSAGES/$DOMAIN.mo" "$LANG_DIR/$loc.po" \
            2> >(grep -v 'still has the initial default value' >&2)
    done
    echo "$(linguas | wc -l) catalogue(s) compilé(s) dans $DEST"
    ;;
desktop)
    check_all
    # msgfmt prend le nom de chaque .po comme code de langue : un dossier
    # temporaire les expose sous leur code de lanceur (desktop_locale).
    mkdir -p "$TMP/desktop" "$DEST"
    for loc in $(linguas); do
        code="$(desktop_locale "$loc")"
        cp "$LANG_DIR/$loc.po" "$TMP/desktop/$code.po"
        echo "$code" >> "$TMP/desktop/LINGUAS"
    done
    msgfmt --desktop --template="$ROOT/$DESKTOP" -d "$TMP/desktop" \
        --output-file="$DEST/$APP_ID.desktop"
    msgfmt --xml --template="$ROOT/$METAINFO" -d "$TMP/desktop" \
        --output-file="$DEST/$APP_ID.metainfo.xml"
    echo "lanceur et AppStream traduits ($(linguas | wc -l) langue(s)) dans $DEST"
    ;;
esac
