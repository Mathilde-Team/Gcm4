#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# ─────────────────────────────────────────────────────────────────────────────
# bootstrap-dev.sh — Installeur d'environnement de développement (uv)
#
# Remplace Poetry par uv depuis cette session : plus simple sur les deux
# points qui nécessitaient un contournement documenté avec Poetry (bug de
# détection d'interpréteur derrière un shim pyenv, erreur DBus/secretstorage
# du backend keyring) — uv n'a présenté ni l'un ni l'autre en usage normal
# lors de cette migration ; à re-signaler si un cas reproductible apparaît.
#
# Automatise :
#   1. Paquets système (typelibs GTK 3 et 4, libadwaita, VTE/VNC/SPICE) via apt
#   2. Création du venv avec accès aux paquets système (`uv venv
#      --system-site-packages`, obligatoire pour `import gi`)
#   3. `uv sync` avec tous les extras (netmiko, paramiko, libvirt, vnc) et
#      le groupe de dépendances de développement (ruff, black, pytest)
#   4. (optionnel, --with-rdp) AsyncRdp (FreeRDP 3) pour le plugin rdp GTK4 :
#      clone d'AsyncRdp4 à côté de Gcm4 s'il manque, installation dans le venv
#   5. (optionnel, --with-snmp) libsnmp-dev + ezsnmp (plugin_snmp_push.py)
#
# Usage :
#   ./bootstrap-dev.sh              # installe tout, support RDP+SNMP inclus
#   ./bootstrap-dev.sh --without-rdp # n'installe pas le support RDP
#   ./bootstrap-dev.sh --without-snmp # n'installe pas le support SNMP (ezsnmp)
#   ./bootstrap-dev.sh --no-apt     # ne touche pas aux paquets système (sudo)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

WITH_RDP=1
WITH_SNMP=1
NO_APT=0
for arg in "$@"; do
    case "$arg" in
        --with-rdp) WITH_RDP=1 ;;
        --without-rdp) WITH_RDP=0 ;;
        --with-snmp) WITH_SNMP=1 ;;
        --without-snmp) WITH_SNMP=0 ;;
        --no-apt) NO_APT=1 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Argument inconnu : $arg" >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

info()  { printf '\033[94m[bootstrap]\033[0m %s\n' "$1"; }
ok()    { printf '\033[92m[ OK ]\033[0m %s\n' "$1"; }
warn()  { printf '\033[93m[WARN]\033[0m %s\n' "$1"; }
fail()  { printf '\033[91m[FAIL]\033[0m %s\n' "$1" >&2; }

# ── 1. Paquets système ───────────────────────────────────────────────────────
if [[ "$NO_APT" -eq 0 ]]; then
    if ! command -v apt-get >/dev/null 2>&1; then
        warn "apt-get introuvable (distribution non-Debian/Ubuntu) : passez --no-apt et installez manuellement les paquets listés dans le Makefile / README."
    else
        info "Installation des dépendances système (sudo apt-get install ...)"
        APT_PKGS=(
            python3-gi
            gir1.2-gtk-3.0
            gir1.2-gtk-4.0
            gir1.2-adw-1
            gir1.2-vte-2.91
            gir1.2-gtk-vnc-2.0
            gir1.2-spiceclientgtk-3.0
            python3-venv
            expect
            python3-paramiko
            build-essential
            pkg-config
            libvirt-dev
        )
        # Paquet venv versionné (ex: python3.13-venv) si disponible, en plus du générique
        PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        if apt-cache show "python${PYVER}-venv" >/dev/null 2>&1; then
            APT_PKGS+=("python${PYVER}-venv")
        fi
        if [[ "$WITH_RDP" -eq 1 ]]; then
            APT_PKGS+=(
                freerdp3-dev
                python3-dev
                build-essential
            )
        fi
        if [[ "$WITH_SNMP" -eq 1 ]]; then
            APT_PKGS+=(
                libsnmp-dev
                snmp-mibs-downloader
            )
        fi
        sudo apt-get update -qq
        if ! sudo apt-get install -y "${APT_PKGS[@]}"; then
            fail "Échec de l'installation de certains paquets apt — vérifiez le nom des paquets pour votre distribution."
            exit 1
        fi
        ok "Paquets système installés"
    fi
else
    info "--no-apt : étape d'installation système ignorée"
fi

# ── 2. uv disponible ? ────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    fail "uv n'est pas installé. Installez-le avec : curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# ── 3. Créer le venv avec accès aux paquets système (obligatoire pour `gi`) ──
# `import gi` (PyGObject) résout les bindings GTK/VTE via les typelibs
# système installés à l'étape 1, jamais via pip — le venv doit donc voir les
# paquets système (équivalent du `virtualenvs.options.system-site-packages`
# de Poetry).
# Interpréteur **système** imposé : avec un Python téléchargé par uv, les
# paquets système vus seraient les siens, pas /usr/lib/python3/dist-packages,
# et `import gi` échouerait. gcm4 (GTK4) exige Python >= 3.12 (D4, #118).
PYTHON_SYSTEME="$(command -v python3)"
if ! "$PYTHON_SYSTEME" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    warn "Python système $("$PYTHON_SYSTEME" -V 2>&1 | cut -d' ' -f2) < 3.12 : gcm4 (GTK4) exige 3.12 (Debian 13, Ubuntu 24.04, Fedora 39 ou plus récent)."
fi
if [[ ! -d .venv ]]; then
    info "Création du venv (uv venv --system-site-packages --python $PYTHON_SYSTEME)"
    uv venv --system-site-packages --python "$PYTHON_SYSTEME" .venv
    ok ".venv créé"
elif ! grep -q '^include-system-site-packages = true' .venv/pyvenv.cfg 2>/dev/null; then
    warn ".venv existe sans accès aux paquets système : gi y est invisible. Recréez-le : rm -rf .venv && $0"
else
    info ".venv existe déjà, réutilisation"
fi

# ── 4. Installation des dépendances (tous les extras + groupe dev) ───────────
info "uv sync --extra netmiko --extra paramiko --extra libvirt --extra vnc --group dev"
if ! uv sync --extra netmiko --extra paramiko --extra libvirt --extra vnc --group dev; then
    fail "uv sync a échoué — voir le journal ci-dessus."
    exit 1
fi
ok "Dépendances Python installées"

# ── 5. Vérification rapide des imports critiques ─────────────────────────────
info "Vérification des imports (gi, Cryptodome)"
if uv run python3 -c "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk, Vte" 2>/dev/null; then
    ok "gi / Gtk / Vte OK"
else
    warn "gi/Gtk/Vte indisponible — vérifiez python3-gi, gir1.2-gtk-3.0, gir1.2-vte-2.91"
fi
if uv run python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); from gi.repository import Adw, Gtk" 2>/dev/null; then
    ok "gi / Gtk 4 / libadwaita OK (uv run gcm4)"
else
    warn "Gtk 4 / libadwaita indisponible — vérifiez gir1.2-gtk-4.0, gir1.2-adw-1 (gcm4 ne démarrera pas)"
fi
if uv run python3 -c "import Cryptodome" 2>/dev/null; then
    ok "Cryptodome (VNC) OK"
else
    warn "Cryptodome indisponible — relancez : uv sync --extra vnc"
fi

# ── 8. Support RDP optionnel (AsyncRdp, plugin rdp GTK4) ────────────────────
# gtk-frdp (GTK3) est abandonné au profit d'AsyncRdp (#71, #105). Le dépôt
# AsyncRdp4 est cherché à côté de Gcm4 (ASYNCRDP_DIR pour un autre chemin) ;
# son dossier integrations/gtk4 est donné au plugin par ASYNCRDP_GTK4_DIR
# ou la clé « integrations » de [plugin rdp] dans gcm4.conf.
if [[ "$WITH_RDP" -eq 1 ]]; then
    ASYNCRDP_DIR="${ASYNCRDP_DIR:-$(cd .. && pwd)/AsyncRdp4}"
    if [[ ! -d "$ASYNCRDP_DIR" ]]; then
        info "git clone https://github.com/Mathilde-Team/AsyncRdp4 $ASYNCRDP_DIR"
        git clone https://github.com/Mathilde-Team/AsyncRdp4 "$ASYNCRDP_DIR" \
            || warn "clone d'AsyncRdp4 impossible — plugin rdp indisponible."
    fi
    if [[ -d "$ASYNCRDP_DIR" ]]; then
        info "uv pip install -e $ASYNCRDP_DIR (compile le shim FreeRDP 3)"
        if uv pip install -e "$ASYNCRDP_DIR"; then
            ok "AsyncRdp installé — exportez ASYNCRDP_GTK4_DIR=$ASYNCRDP_DIR/integrations/gtk4"
        else
            warn "installation d'AsyncRdp échouée (freerdp3-dev présent ?) — plugin rdp indisponible."
        fi
    fi
else
    warn "Support RDP (plugin rdp / AsyncRdp) non installé — relancez sans --without-rdp pour l'activer."
fi

# ── 9. Support SNMP optionnel (ezsnmp, plugin_snmp_push.py) ─────────────────
if [[ "$WITH_SNMP" -eq 1 ]]; then
    info "apt install libsnmp-dev snmp-mibs-downloader && pip install ezsnmp loguru --break-system-packages"
    if [[ "$NO_APT" -eq 1 ]] && ! command -v snmpget >/dev/null 2>&1; then
        warn "libsnmp-dev absent et --no-apt actif : installez-le manuellement avant ezsnmp."
    fi
    if uv run pip install ezsnmp loguru --break-system-packages; then
        ok "ezsnmp installé (relancez l'app pour activer plugin_snmp_push)"
    else
        fail "échec de l'installation d'ezsnmp — vérifiez que libsnmp-dev est bien installé."
    fi
else
    warn "Support SNMP (plugin_snmp_push / ezsnmp) non installé — relancez avec --with-snmp pour l'activer."
fi

echo
ok "Environnement prêt. Lancez l'application avec :"
echo "    uv run python3 gnome_connection_manager.py"
