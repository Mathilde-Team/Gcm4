#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# install.sh — installe les dépendances de pluginvnc2, vérifie ruff et
# lance les tests. Pensé pour préparer un premier push GitHub propre :
# rien n'est poussé ici, ce script prépare juste l'environnement local et
# confirme que tout est vert avant de committer.
#
# Usage :
#   ./install.sh            installation + vérifications complètes
#   ./install.sh --no-venv  installe dans l'interpréteur courant (pas de venv)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

USE_VENV=1
for arg in "$@"; do
    if [[ "$arg" == "--no-venv" ]]; then
        USE_VENV=0
    fi
done

echo "== pluginvnc2 : installation =="

if ! command -v python3 >/dev/null 2>&1; then
    echo "Erreur : python3 introuvable." >&2
    exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "Python détecté : $PYTHON_VERSION"

echo
echo "== Vérification GTK4/PyGObject (niveau système) =="
# PyGObject doit venir du gestionnaire de paquets système, pas de pip :
# il compile depuis les sources sous pip et a besoin des en-têtes
# girepository, absents par défaut. C'est pour ça que le venv ci-dessous
# est créé avec --system-site-packages, pour voir cette installation
# système au lieu d'essayer de la reconstruire.
if python3 -c "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk" 2>/dev/null; then
    HAVE_GTK4=1
    echo "GTK4 disponible au niveau système."
else
    HAVE_GTK4=0
    echo "GTK4 non disponible au niveau système."
    echo "Sur Debian/Ubuntu : sudo apt install python3-gi gir1.2-gtk-4.0"
    echo "Les tests de logique pure tourneront quand même via le stub tests/gtk_stub/."
fi

if [[ "$USE_VENV" -eq 1 ]]; then
    if [[ ! -d .venv ]]; then
        echo
        echo "Création de l'environnement virtuel (.venv, --system-site-packages"
        echo "pour voir le PyGObject installé au niveau système)…"
        python3 -m venv --system-site-packages .venv
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    echo "Environnement virtuel activé : $(which python3)"
fi

echo
echo "== Installation des dépendances Python (numpy, cryptography, loguru, ruff, pytest) =="
pip install --upgrade pip --quiet
pip install -r requirements-dev.txt --quiet
echo "Dépendances installées."

echo
echo "== ruff check =="
ruff check vnc_tab.py

echo
echo "== ruff format --check =="
ruff format --check vnc_tab.py

echo
echo "== Tests =="
if [[ "$HAVE_GTK4" -eq 1 ]]; then
    pytest tests/ -v
else
    PYTHONPATH="tests/gtk_stub" pytest tests/ -v --ignore=tests/gtk_stub
fi

echo
echo "== Tout est vert =="
echo "Prochaines étapes pour le push GitHub :"
echo "  1. git init  (si ce n'est pas déjà un dépôt)"
echo "  2. git add ."
echo "  3. git commit -m \"pluginvnc2 : onglet VNC GTK4 pour gnome-connection-manager\""
echo "  4. git remote add origin <url-du-depot>"
echo "  5. git push -u origin main"
