#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# scripts/smoke-test-packages.sh -- test de fumée des paquets gcm4 (#156, #157).
#
# Sur le modèle de Netcross : le paquet est vérifié tel que l'utilisateur le
# reçoit, dans un système propre (conteneur du job « Paquets »).
#
#   deb : scripts/smoke-test-packages.sh deb build-deb/dist/gcm4_*.deb
#         installe le .deb avec apt (dépendances résolues), puis vérifie :
#         1. aucune dépendance de plugin (VTE, gtk-vnc, spice, freerdp) ;
#         2. le lanceur /usr/bin/gcm4 (exécutable, /usr/bin/python3) ;
#         3. « gcm4 --version » donne la version du paquet ;
#         4. chaque module de /usr/share/gcm4 s'importe avec /usr/bin/python3 ;
#         5. le lanceur .desktop, le metainfo (traduits) et les icônes sont
#            installés ;
#         6. un catalogue .mo par langue de lang/LINGUAS, réellement chargé
#            par gcm4.core.i18n depuis /usr/share/locale ;
#         7. gcm4 démarre sous Xvfb, présente sa fenêtre, et son journal
#            debug ne contient ni erreur ni trace d'exception.
#   rpm : scripts/smoke-test-packages.sh rpm build-rpm/dist/gcm4-*.noarch.rpm
#         contenu du paquet sans l'installer (rpm -qpl, rpm -qpR) : lanceur,
#         modules, .dist-info, bureau, icônes, un .mo par langue, dépendances
#         (aucune de plugin). Possible sur tout système qui a rpm.
#   rpm-dnf : même vérification du contenu, puis installation avec dnf
#         (Fedora, EL) et vérifications 2 à 7 du .deb.
#
# Prérequis du système de test (deb, rpm-dnf) : xvfb (xvfb-run), xauth, dbus
# (dbus-run-session), et toutes les langues installées : aucune règle dpkg
# path-exclude sur /usr/share/locale, %_install_langs non restreint (images
# minimales de conteneur).
# Sortie non nulle au premier écart.

set -euo pipefail

MODE="${1:?usage: $0 deb|rpm|rpm-dnf <paquet>}"
PKG="${2:?usage: $0 deb|rpm|rpm-dnf <paquet>}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="io.github.MathildeDec.gcm4"
PREFIX="/usr/share/gcm4"
DOMAIN="gcm-lang"
ICON_SIZES=(16x16 22x22 24x24 32x32 48x48 64x64 128x128)
# Dépendances propres aux plugins de connexion : jamais dans la base.
PLUGIN_DEPS_RE='vte|gtk-?vnc|spice|freerdp|expect|paramiko'
# Délai laissé à gcm4 pour démarrer sous Xvfb avant d'être arrêté.
LAUNCH_SECONDS="${GCM4_SMOKE_LAUNCH_SECONDS:-20}"

fail() { echo "ECHEC : $*" >&2; exit 1; }

linguas() { sed -e 's/#.*//' -e 's/[[:space:]]//g' "$ROOT/lang/LINGUAS" | grep -v '^$'; }

# Dépendances déclarées (une par ligne, sans version) : aucune de plugin,
# et celles de la base, sous le nom de la distribution.
check_depends() {
    local liste="$1"; shift
    if grep -Eiq "$PLUGIN_DEPS_RE" <<<"$liste"; then
        fail "dépendance de plugin dans le paquet de la base : $(tr '\n' ' ' <<<"$liste")"
    fi
    for d in "$@"; do
        grep -qx "$d" <<<"$liste" || fail "dépendance $d absente : $(tr '\n' ' ' <<<"$liste")"
    done
    echo "dépendances : $(tr '\n' ' ' <<<"$liste")"
}

# Vérifications 2 à 7, communes au .deb et au .rpm installés.
check_installed() {
    local version="$1"

    # 2. lanceur
    [ -x /usr/bin/gcm4 ] || fail "lanceur /usr/bin/gcm4 absent ou non exécutable"
    head -n 1 /usr/bin/gcm4 | grep -qx '#!/usr/bin/python3' || fail "/usr/bin/gcm4 n'utilise pas /usr/bin/python3"
    command -v gcm4 >/dev/null || fail "gcm4 introuvable dans le PATH"

    # 3. version : celle du paquet, lue dans le .dist-info installé
    local attendu="gcm4 $version" home
    home="$(mktemp -d)"
    [ "$(HOME="$home" gcm4 --version)" = "$attendu" ] \
        || fail "gcm4 --version : « $(HOME="$home" gcm4 --version) » au lieu de « $attendu »"
    echo "--version : $attendu"

    # 4. imports (interface comprise : ses dépendances sont celles du paquet)
    /usr/bin/python3 - "$PREFIX" <<'PY'
import importlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))

# Modules à dépendance optionnelle : leur import peut légitimement échouer
# si la dépendance n'est pas installée. D1 de #118 : le paquet de base
# n'embarque pas les dépendances des plugins ; #223 : VTE GTK4 (Vte-3.91)
# est absent de Rocky Linux 9 et 10 — l'import de gcm4.ui.terminal échoue
# alors dans le plugin, signalé en erreur dans Préférences > Extensions,
# sans empêcher gcm4 de démarrer (la base ne l'importe jamais). La
# fonctionnalité est couverte ailleurs : job GTK4 (tests/gtk4/
# test_plugin_local.py, VTE installé).
OPTIONNELS = {"gcm4.ui.terminal": "VTE GTK4 (Vte-3.91), absent du paquet de base (D1 #118, #223)"}

mods, errors, optionnels = [], [], []
for py in sorted(root.rglob("*.py")):
    rel = py.relative_to(root).with_suffix("")
    name = ".".join(rel.parts[:-1] if rel.name == "__init__" else rel.parts)
    if name.endswith("__main__"):
        continue  # lancerait l'application
    mods.append(name)
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 -- on veut tout lister
        if name in OPTIONNELS:
            optionnels.append(f"{name}: {OPTIONNELS[name]}")
            continue
        errors.append(f"{name}: {exc!r}")
        continue
    if not str(getattr(module, "__file__", "")).startswith(str(root)):
        errors.append(f"{name}: importé depuis {module.__file__}, hors de {root}")
if optionnels:
    print("modules optionnels non importés (attendu sans leur dépendance) :")
    print("  " + "\n  ".join(optionnels))
if errors:
    print("\n".join(errors), file=sys.stderr)
    sys.exit(f"imports : {len(errors)} module(s) en échec sur {len(mods)}")
print(f"imports : {len(mods)} modules OK")
PY

    # 5. intégration au bureau
    local desktop="/usr/share/applications/$APP_ID.desktop"
    [ -f "$desktop" ] || fail "$desktop absent"
    grep -qx 'Exec=gcm4' "$desktop" || fail "$desktop : Exec=gcm4 absent"
    grep -qx "Icon=$APP_ID" "$desktop" || fail "$desktop : Icon=$APP_ID absent"
    [ -f "/usr/share/metainfo/$APP_ID.metainfo.xml" ] || fail "metainfo absent"
    # Traductions fusionnées par build.sh (#274) : langue seule (fr) et
    # variante (zh_CN) dans le lanceur ; AppStream (étiquettes BCP 47 selon
    # la version de gettext) vérifié sur fr.
    for code in fr zh_CN; do
        grep -q "^Comment\[$code\]=" "$desktop" || fail "$desktop : Comment[$code] absent"
    done
    grep -q '<summary xml:lang="fr">' "/usr/share/metainfo/$APP_ID.metainfo.xml" \
        || fail "metainfo : summary xml:lang=fr absent"
    for s in "${ICON_SIZES[@]}"; do
        [ -f "/usr/share/icons/hicolor/$s/apps/$APP_ID.png" ] || fail "icône $s absente"
    done
    echo "bureau : lanceur et metainfo traduits, ${#ICON_SIZES[@]} icônes installés"

    # 6. traductions
    local n=0
    for loc in $(linguas); do
        [ -f "/usr/share/locale/$loc/LC_MESSAGES/$DOMAIN.mo" ] || fail "catalogue $loc absent"
        n=$((n + 1))
    done
    /usr/bin/python3 - "$PREFIX" "$DOMAIN" $(linguas) <<'PY'
import gettext, os, sys
sys.path.insert(0, sys.argv[1])
domain, locales = sys.argv[2], sys.argv[3:]
from gcm4.core import i18n
for loc in locales:
    os.environ["LANGUAGE"] = loc
    attendu = gettext.translation(domain, "/usr/share/locale", [loc])
    obtenu = i18n.install()
    if type(obtenu) is gettext.NullTranslations or obtenu._catalog != attendu._catalog:
        sys.exit(f"traductions : {loc} non chargé par gcm4.core.i18n depuis /usr/share/locale")
os.environ["LANGUAGE"] = "fr_FR"
i18n.install()
if i18n._("Cancel") != "Annuler":
    sys.exit(f"traductions : « Cancel » donne « {i18n._('Cancel')} » en fr_FR")
PY
    echo "traductions : $n catalogues installés et chargés"

    # 7. démarrage réel, sans plugin
    local journal="$home/.config/gcm4/log/gcm-app.log" code=0
    HOME="$home" timeout -k 5 "$LAUNCH_SECONDS" dbus-run-session -- xvfb-run -a gcm4 --debug \
        >"$home/sortie.txt" 2>&1 || code=$?
    # 124 : toujours en marche à l'échéance, arrêté par timeout (attendu).
    [ "$code" -eq 124 ] || { cat "$home/sortie.txt" >&2; fail "gcm4 s'est arrêté seul (code $code)"; }
    [ -f "$journal" ] || { cat "$home/sortie.txt" >&2; fail "journal debug $journal absent"; }
    grep -q "_on_activate: fenêtre présentée" "$journal" \
        || { tail -n 30 "$journal" >&2; fail "fenêtre principale non présentée"; }
    if grep -Eq '\| (ERROR|CRITICAL) +\||Traceback' "$journal" "$home/sortie.txt"; then
        grep -E '\| (ERROR|CRITICAL) +\||Traceback' -A5 "$journal" "$home/sortie.txt" >&2
        fail "erreur ou exception au démarrage"
    fi
    echo "démarrage : fenêtre présentée en moins de ${LAUNCH_SECONDS} s, journal sans erreur"
    rm -rf "$home"
}

check_deb() {
    local sudo="${SUDO-sudo}"
    [ "$(id -u)" -eq 0 ] && sudo=""
    # Images minimales (conteneurs Ubuntu) : dpkg y ignore les .mo, un poste
    # de travail non. Le test 6 serait faux : on refuse de commencer.
    if grep -hsE '^path-exclude=/usr/share/locale/' /etc/dpkg/dpkg.cfg.d/* | grep -q .; then
        fail "dpkg exclut /usr/share/locale (système minimal) : retirer la règle path-exclude de /etc/dpkg/dpkg.cfg.d/ avant le test"
    fi
    $sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$(readlink -f "$PKG")" >/dev/null

    # 1. dépendances
    check_depends "$(dpkg-query -W -f='${Depends}' gcm4 | sed -e 's/ *([^)]*)//g' -e 's/, */\n/g')" \
        python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-loguru
    check_installed "$(dpkg-query -W -f='${Version}' gcm4)"
}

# Contenu du .rpm, sans l'installer (possible sur tout système avec rpm).
check_rpm_content() {
    local listing
    listing="$(rpm -qpl "$PKG")"
    for f in /usr/bin/gcm4 "$PREFIX/gcm4/app.py" "$PREFIX/gcm4/ui/style.css" \
        "$PREFIX/gcm4_core.py" "$PREFIX/logging_config.py" "$PREFIX/master_password_core.py" "$PREFIX/pyAES.py" \
        "/usr/share/applications/$APP_ID.desktop" "/usr/share/metainfo/$APP_ID.metainfo.xml"; do
        grep -qx "$f" <<<"$listing" || fail "rpm : $f absent"
    done
    grep -Eq "^$PREFIX/[^/]+\.dist-info/METADATA$" <<<"$listing" || fail "rpm : .dist-info absent (version de gcm4 --version)"
    for s in "${ICON_SIZES[@]}"; do
        grep -qx "/usr/share/icons/hicolor/$s/apps/$APP_ID.png" <<<"$listing" || fail "rpm : icône $s absente"
    done
    for loc in $(linguas); do
        grep -qx "/usr/share/locale/$loc/LC_MESSAGES/$DOMAIN.mo" <<<"$listing" || fail "rpm : catalogue $loc absent"
    done
    check_depends "$(rpm -qpR "$PKG" | sed -e 's/ *[<>=].*//' | grep -v '^rpmlib(' | grep -v '^/')" \
        python3 python3-gobject gobject-introspection gtk4 libadwaita python3-loguru
    echo "rpm : contenu conforme ($(wc -l <<<"$listing") fichiers)"
}

# Installation avec dnf (Fedora, EL) puis mêmes vérifications que le .deb.
check_rpm_dnf() {
    check_rpm_content
    local sudo="${SUDO-sudo}"
    [ "$(id -u)" -eq 0 ] && sudo=""
    # Images de conteneur : rpm n'y installe parfois que certaines langues
    # (%_install_langs). Un poste de travail les installe toutes.
    local langs
    langs="$(rpm --eval '%{_install_langs}')"
    case "$langs" in
        all|"%{_install_langs}") ;;
        *) fail "rpm n'installe que les langues « $langs » (système minimal) : retirer la définition de %_install_langs (/etc/rpm/) avant le test" ;;
    esac
    $sudo dnf install -y -q "$(readlink -f "$PKG")" >/dev/null
    check_installed "$(rpm -q --qf '%{VERSION}' gcm4)"
}


case "$MODE" in
    deb) check_deb ;;
    rpm) check_rpm_content ;;
    rpm-dnf) check_rpm_dnf ;;
    *) fail "mode inconnu : $MODE" ;;
esac
echo "test de fumée $MODE : OK"
