#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# #77 : vrai compositeur Wayland headless, sans serveur X ni XWayland.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

python="${GCM_PYTHON:-python3}"
command -v weston >/dev/null
command -v "$python" >/dev/null
umask 077
runtime="$(mktemp -d "${TMPDIR:-/tmp}/gcm4-wayland.XXXXXX")"
pid=""

nettoyer() {
    if [[ -n "$pid" ]]; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
    rm -rf -- "$runtime"
}
trap nettoyer EXIT

export XDG_RUNTIME_DIR="$runtime"
export WAYLAND_DISPLAY=gcm4-wayland
export GDK_BACKEND=wayland
export GSK_RENDERER=cairo
export GCM_TESTS_GTK4=1
export GCM_TESTS_WAYLAND=1
unset DISPLAY WAYLAND_SOCKET

weston --backend=headless-backend.so --renderer=pixman \
    --socket="$WAYLAND_DISPLAY" --idle-time=0 --no-config \
    --log="$runtime/weston.log" >"$runtime/weston-sortie.log" 2>&1 &
pid=$!
pret=0
for _ in {1..100}; do
    if [[ -S "$runtime/$WAYLAND_DISPLAY" ]]; then
        pret=1
        break
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        break
    fi
    # Attente courte uniquement pour la création du socket du compositeur.
    sleep 0.05
done
if [[ "$pret" != 1 ]]; then
    echo "Le compositeur Wayland n'a pas créé son socket dans le délai." >&2
    tail -80 "$runtime"/weston*.log >&2 || true
    exit 1
fi

# Ne pas utiliser « weston -- python ... » : Weston 14 retourne zéro même
# si son client échoue. Le statut de pytest doit être celui de ce script.
statut=0
"$python" -m pytest -q -rs --no-cov -p no:cacheprovider \
    tests/wayland tests/gtk4/test_app_demarrage.py \
    tests/gtk4/test_plugin_local.py tests/gtk4/test_raccourcis.py "$@" || statut=$?
if [[ "$statut" != 0 ]]; then
    tail -80 "$runtime"/weston*.log >&2 || true
fi
exit "$statut"
