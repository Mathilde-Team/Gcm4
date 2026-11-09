PKG_NAME    = gnome-connection-manager
PKG_VERSION = 4.0.0
PKG_MAINTAINER = "MathildeDec <mathilde.deuscher@gmail.com>"
PKG_DESCRIPTION = "Multi-protocol tabbed connection manager (SSH/RDP/VNC/SPICE/Serial)"
PKG_VENDOR  = MathildeDec
PKG_URL     = https://github.com/MathildeDec/gnome-connection-manager
PKG_ARCH    = all
PKG_LICENSE = GPLv3

# Modules embarqués : les .py de src/gcm4 (paquet GTK4, #133) et les .py de
# plugins/ hors tests. L'application GTK3 (GTK3.OLD/, cible `install` fpm)
# a été supprimée avec le cœur GTK3 (#182) : les paquets vivants sont
# build-deb/ et build-rpm/ (cibles deb / rpm-gcm4).
GCM4_PY     = $(sort $(shell git ls-files 'src/gcm4/*.py' 'src/gcm4/*.css'))
PLUGINS_PY  = $(sort $(shell find plugins -name '*.py' -not -path '*/tests/*' -not -name 'conftest.py' -not -path '*/__pycache__/*'))

# ── Cibles principales ────────────────────────────────────────────────────────
.PHONY: all run-gtk4 deb deb-gcm4 smoke-deb rpm-gcm4 smoke-rpm translate i18n-update i18n-check i18n-report class-diagram ci coverage-combined check-desktop icons test test-xterm lint validate clean help docs

all:  test lint deb rpm-gcm4 translate validate

help:
	@echo "Cibles disponibles :"
	@echo "  make          → test + lint + deb + rpm-gcm4 (défaut)"
	@echo "  make deb      → paquet .deb de gcm4 (build-deb/dist/gcm4_*.deb)"
	@echo "  make smoke-deb → installe ce paquet (sudo apt) et le vérifie"
	@echo "  make rpm-gcm4 → paquet .rpm de la base GTK4 (build-rpm/dist/gcm4-*.rpm)"
	@echo "  make smoke-rpm → vérifie le contenu de ce paquet (rpm -qpl, sans l'installer)"
	@echo "  make translate → compile tous les .po → .mo"
	@echo "  make i18n-update → extrait les chaînes et met à jour lang/*.po"
	@echo "  make i18n-check  → échoue si lang/messages.pot ou un .po est périmé"
	@echo "  make i18n-report → taux de traduction par langue"
	@echo "  make run-gtk4 → lance gcm4 (GTK4) dans .venv ; venv créé par bootstrap-dev.sh"
	@echo "  make class-diagram → régénère docs/class-diagram.md"
	@echo "  make ci       → rejoue les étapes des jobs Qualité, Types, i18n, bureau et documentation"
	@echo "  make coverage-combined → fusionne donnees-couverture/ et vérifie lignes et branches"
	@echo "  make check-desktop → valide lanceur, AppStream et icônes (data/)"
	@echo "  make icons   → régénère data/icons/hicolor/ depuis data/icon.png"
	@echo "  make docs     → construit la documentation MkDocs (site/, strict)"
	@echo "  make test     → lance la suite pytest"
	@echo "  make test-xterm → teste le pont JavaScript (Node 22.16.0)"
	@echo "  make lint     → ruff"
	@echo "  make validate → valide .po (msgfmt) + .glade/.xml/.json"
	@echo "  make clean    → supprime les paquets et répertoires temporaires"

# ── Compilation des traductions ───────────────────────────────────────────────
translate:
	@echo "Compilation des fichiers .po → .mo (langues de lang/LINGUAS)…"
	@scripts/i18n-update.sh --compile lang

# Catalogues gettext (issue #165) : extraction + fusion, puis vérification
# sans écriture (même contrôle que la CI).
i18n-update:
	@scripts/i18n-update.sh

i18n-check:
	@scripts/i18n-update.sh --check

i18n-report:
	@uv run python scripts/i18n_report.py

# ── Base GTK4 (#119) ──────────────────────────────────────────────────────────
# gi, GTK 4 et libadwaita viennent des paquets système : le .venv doit être
# créé avec --system-site-packages sur le Python système (bootstrap-dev.sh).
run-gtk4:
	@grep -q '^include-system-site-packages = true' .venv/pyvenv.cfg 2>/dev/null || { \
	  echo "make run-gtk4 : .venv absent ou sans --system-site-packages (gi invisible)."; \
	  echo "  rm -rf .venv && uv venv --system-site-packages --python \"$$(command -v python3)\" .venv && uv sync --group dev"; \
	  exit 1; }
	uv run gcm4 $(ARGS)

class-diagram:
	@uv run python scripts/generate_class_diagram.py

# ── Paquet .deb de gcm4 (#156) ───────────────────────────────────────────────
# build-deb/build.sh (dpkg-buildpackage) : paquet gcm4 de la base GTK4, sans
# aucun plugin, version lue dans pyproject.toml. Plus de .deb de l'application
# GTK3 (décision du 2026-10-22, #156). deb-gcm4 : ancien nom, conservé.
# smoke-deb l'installe avec apt (sudo) puis le vérifie, comme le job
# « Paquets » de la CI : à lancer dans un conteneur ou une machine de test.
deb:
	build-deb/build.sh

deb-gcm4: deb

smoke-deb:
	scripts/smoke-test-packages.sh deb $(wildcard build-deb/dist/gcm4_*_all.deb)

# ── Paquet .rpm de la base GTK4 (#157) ───────────────────────────────────────
# build-rpm/build.sh (rpmbuild, Fedora ou construction croisée avec le paquet
# rpm de Debian/Ubuntu). smoke-rpm vérifie le contenu sans installer ; sur
# Fedora, « scripts/smoke-test-packages.sh rpm-dnf » l'installe et le lance.
rpm-gcm4:
	build-rpm/build.sh

smoke-rpm:
	scripts/smoke-test-packages.sh rpm $(wildcard build-rpm/dist/gcm4-*.noarch.rpm)

# ── make ci (issue #162) : rejoue localement les étapes du job Qualité ───────
# Même ordre et mêmes commandes que .github/workflows/ci.yml (jobs Qualité,
# Types, i18n et documentation) ; tests/test_make_ci.py vérifie que chaque commande `run:`
# de ces jobs figure ici.
ci:
	bash scripts/test-xterm.sh
	uv run ruff check .
	uv run python scripts/generate_class_diagram.py --check
	uv run ruff format --check .
	uvx pre-commit run --all-files --show-diff-on-failure
	uv run python tools/check_circular_imports.py
	GCM_TESTS_EXIGES=gettext uv run python -m pytest tests/ -q -rs --cov --cov-report=term-missing:skip-covered --cov-fail-under=0
	uv run coverage report --include='src/gcm4/core/*' --fail-under=95
	uv run mypy
	bash scripts/test-wayland.sh
	uv run python tools/validate_po.py lang/*.po
	scripts/i18n-update.sh --check
	scripts/check-desktop.sh
	uvx --with mkdocs-material --with pymdown-extensions --from "mkdocs<2" mkdocs build --strict

# Données des deux jobs de CI, ou reproduction locale documentée (#293).
# Le rapport historique est conservé, mais ne suffit pas à lui seul :
# une moyenne >= 95 % peut masquer des branches sous le seuil.
coverage-combined:
	uv run coverage combine --keep donnees-couverture/.coverage.qualite donnees-couverture/.coverage.gtk4
	uv run coverage xml -o coverage-combinee.xml --fail-under=0
	uv run coverage json -o coverage-combinee.json --fail-under=0
	uv run python scripts/verifier_couverture.py coverage-combinee.json
	uv run coverage report --skip-covered

# ── Intégration au bureau (issue #155) ──
# check-desktop : lanceur, AppStream et icônes (desktop-file-utils, appstream).
# icons : régénère data/icons/hicolor/ depuis data/icon.png (ImageMagick).
check-desktop:
	scripts/check-desktop.sh

icons:
	scripts/generer-icones.sh

# ── Documentation (MkDocs, issue #127) ──
docs:
	uvx --with mkdocs-material --with pymdown-extensions --from "mkdocs<2" mkdocs build --strict

# ── Tests unitaires ───────────────────────────────────────────────────────────
test:
	python3 -m pytest tests/ -v

# Node 22.16.0 est figé dans la CI ; aucun Node requis à l'exécution de gcm4.
test-xterm:
	bash scripts/test-xterm.sh

# ── Lint (ruff) ───────────────────────────────────────────────────────────────
# Historique : flake8 ne couvrait que le fichier principal de l'application
# GTK3 (supprimée en #182) ; ruff couvre désormais tout le dépôt (hors
# sous-projets vendorisés, voir l'exclude de pyproject.toml).
lint:
	ruff check .

# ── Validation .po (+ .xml/.glade/.json si le dépôt en contient un jour) ─────
# session-30 (2026-09-10) : cette cible appelait tools/validate_xml_json.py
# sur gnome-connection-manager.glade, fichier absent du dépôt depuis la
# suppression de tout .glade/.ui (session-08, cf. CLAUDE.md) — `make validate`
# et donc `make check` échouaient silencieusement à chaque exécution
# (`[Errno 2] No such file or directory`), sans rapport avec les 423 erreurs
# ruff pré-existantes déjà documentées pour `make lint`. Corrigé en retirant
# l'appel obsolète ; tools/validate_xml_json.py reste disponible tel quel
# (générique .xml/.glade/.json) pour un futur fichier de ce type, il suffira
# de l'invoquer de nouveau ici. Voir docs/gcm4-rename.md.
validate:
	@echo "Validation des fichiers .po…"
	@python3 tools/validate_po.py lang/*.po && echo "\033[92m  .po OK\033[0m"

# ── Nettoyage ─────────────────────────────────────────────────────────────────
clean:
	rm -rf build-deb/_build build-deb/dist build-rpm/dist
	find . -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

# ── Aide au développeur ───────────────────────────────────────────────────────
check-gitignore:
	@if [ -n "$$(git status -uno -s)" ]; then \
		echo "ERROR: des fichiers trackés ont des modifications non commitées" >&2; \
		git status -uno -s; \
		exit 1; \
	fi

check: validate lint test check-gitignore
