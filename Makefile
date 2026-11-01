PKG_NAME    = gnome-connection-manager
PKG_VERSION = 4.0.0
PKG_MAINTAINER = "MathildeDec <mathilde.deuscher@gmail.com>"
PKG_DESCRIPTION = "Multi-protocol tabbed connection manager (SSH/RDP/VNC/SPICE/Serial)"
PKG_VENDOR  = MathildeDec
PKG_URL     = https://github.com/MathildeDec/gnome-connection-manager
PKG_ARCH    = all
PKG_ARCH_RPM = noarch
PKG_LICENSE = GPLv3
PKG_RPM     = $(PKG_NAME)-$(PKG_VERSION).$(PKG_ARCH_RPM).rpm
PKG_OPENSUSE = $(PKG_NAME)-opensuse-$(PKG_VERSION).$(PKG_ARCH_RPM).rpm

TMPINSTALLDIR = /tmp/$(PKG_NAME)-fpm-install

# Modules embarqués (issue #162) : TOUS les .py de la racine et les .py de
# plugins/ hors tests. Avant, seuls gnome_connection_manager.py, pyAES.py et
# urlregex.py étaient copiés : le paquet installé échouait à l'import de
# gcm4_core, logging_config, models, utils, widgets... et n'avait aucun
# plugin. tests/test_paquet_contenu.py vérifie que chaque module importé est
# bien installé.
RUNTIME_PY  = $(sort $(wildcard *.py))
PLUGINS_PY  = $(sort $(shell find plugins -name '*.py' -not -path '*/tests/*' -not -name 'conftest.py' -not -path '*/__pycache__/*'))

FPM_COMMON = -s dir -n $(PKG_NAME) -v $(PKG_VERSION) -C $(TMPINSTALLDIR) \
	--maintainer $(PKG_MAINTAINER) \
	--description "$$(printf '$(PKG_DESCRIPTION)')" \
	--license $(PKG_LICENSE) --vendor $(PKG_VENDOR) \
	--category net --url $(PKG_URL)

# ── Cibles principales ────────────────────────────────────────────────────────
.PHONY: all run-gtk4 deb deb-gcm4 smoke-deb rpm rpm-gcm4 smoke-rpm opensuse install translate i18n-update i18n-check i18n-report class-diagram ci check-desktop icons test lint validate clean help docs

all:  test lint deb rpm opensuse translate validate

help:
	@echo "Cibles disponibles :"
	@echo "  make          → deb + rpm (défaut)"
	@echo "  make deb      → paquet .deb de gcm4 (build-deb/dist/gcm4_*.deb)"
	@echo "  make smoke-deb → installe ce paquet (sudo apt) et le vérifie"
	@echo "  make rpm-gcm4 → paquet .rpm de la base GTK4 (build-rpm/dist/gcm4-*.rpm)"
	@echo "  make smoke-rpm → vérifie le contenu de ce paquet (rpm -qpl, sans l'installer)"
	@echo "  make rpm      → paquet .rpm (Fedora/RHEL/CentOS)"
	@echo "  make opensuse → paquet .rpm (openSUSE — dépendances zypper)"
	@echo "  make translate → compile tous les .po → .mo"
	@echo "  make i18n-update → extrait les chaînes et met à jour lang/*.po"
	@echo "  make i18n-check  → échoue si lang/messages.pot ou un .po est périmé"
	@echo "  make i18n-report → taux de traduction par langue"
	@echo "  make run-gtk4 → lance gcm4 (GTK4) dans .venv ; venv créé par bootstrap-dev.sh"
	@echo "  make class-diagram → régénère docs/class-diagram.md"
	@echo "  make ci       → rejoue les étapes des jobs Qualité, Types, i18n, bureau et documentation"
	@echo "  make check-desktop → valide lanceur, AppStream et icônes (data/)"
	@echo "  make icons   → régénère data/icons/hicolor/ depuis icon.png"
	@echo "  make docs     → construit la documentation MkDocs (site/, strict)"
	@echo "  make test     → lance la suite pytest"
	@echo "  make lint     → ruff + flake8"
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

# ── Installation dans un répertoire temporaire ────────────────────────────────
install: translate
	mkdir -p $(DESTDIR)/usr/share/$(PKG_NAME)
	mkdir -p $(DESTDIR)/usr/share/applications
	mkdir -p $(DESTDIR)/usr/share/doc/$(PKG_NAME)
	echo "$(PKG_NAME) ($(PKG_VERSION)) all; urgency=low" \
		> $(DESTDIR)/usr/share/doc/$(PKG_NAME)/changelog
	git log --no-merges --format="* %s" \
		>> $(DESTDIR)/usr/share/doc/$(PKG_NAME)/changelog
	gzip -9 $(DESTDIR)/usr/share/doc/$(PKG_NAME)/changelog
	cp gnome-connection-manager.desktop $(DESTDIR)/usr/share/applications/
	cat NOTICE LICENSE > $(DESTDIR)/usr/share/doc/$(PKG_NAME)/copyright
	cp -r lang icon.png ssh.expect style.css $(RUNTIME_PY) \
		$(DESTDIR)/usr/share/$(PKG_NAME)/
	for f in $(PLUGINS_PY); do \
		install -D -m 644 "$$f" "$(DESTDIR)/usr/share/$(PKG_NAME)/$$f"; \
	done
	chmod 755 $(DESTDIR)/usr/share/$(PKG_NAME)/gnome_connection_manager.py

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

# ── Paquet .rpm (Fedora / RHEL / CentOS) ─────────────────────────────────────
rpm:
	rm -rf $(TMPINSTALLDIR) $(PKG_RPM)
	chmod -R g-w *
	$(MAKE) install DESTDIR=$(TMPINSTALLDIR)
	fpm -t rpm -p $(PKG_RPM) $(FPM_COMMON) \
		-a $(PKG_ARCH_RPM) \
		-d python3 \
		-d python3-gobject \
		-d vte291 \
		-d gtk-vnc2 \
		-d spice-gtk3 \
		-d freerdp \
		-d expect \
		-d python3-paramiko \
		-d python3-loguru \
		--after-install postinst \
		usr
	@echo "\033[92mOK: $(PKG_RPM)\033[0m"

# ── Paquet .rpm (openSUSE / SLES) ────────────────────────────────────────────
# Les noms de paquets openSUSE diffèrent de Fedora :
#   python3-gobject → python3-gobject (identique)
#   vte291          → typelib-1_0-Vte-2.91 + libvte-2_91-0
#   expect          → expect (identique)
opensuse:
	rm -rf $(TMPINSTALLDIR) $(PKG_OPENSUSE)
	chmod -R g-w *
	$(MAKE) install DESTDIR=$(TMPINSTALLDIR)
	fpm -t rpm -p $(PKG_OPENSUSE) $(FPM_COMMON) \
		-a $(PKG_ARCH_RPM) \
		--rpm-dist "opensuse" \
		-d python3 \
		-d python3-gobject \
		-d "typelib-1_0-Vte-2.91" \
		-d "typelib-1_0-GtkVnc-2.0" \
		-d "typelib-1_0-SpiceClientGtk-3.0" \
		-d freerdp \
		-d expect \
		-d python3-paramiko \
		-d python3-loguru \
		--after-install postinst \
		usr
	@echo "\033[92mOK: $(PKG_OPENSUSE) (openSUSE)\033[0m"

# ── make ci (issue #162) : rejoue localement les étapes du job Qualité ───────
# Même ordre et mêmes commandes que .github/workflows/ci.yml (jobs Qualité,
# Types, i18n et documentation) ; tests/test_make_ci.py vérifie que chaque commande `run:`
# de ces jobs figure ici.
ci:
	uv run ruff check .
	uv run --with flake8 flake8 gnome_connection_manager.py
	uv run python scripts/generate_class_diagram.py --check
	uv run ruff format --check .
	uvx pre-commit run --all-files --show-diff-on-failure
	uv run python tools/check_circular_imports.py
	GCM_TESTS_EXIGES=gettext uv run python -m pytest tests/ -q -rs --cov --cov-report=term-missing:skip-covered --cov-fail-under=0
	uv run coverage report --include='src/gcm4/core/*' --fail-under=95
	uv run mypy
	uv run python tools/validate_po.py lang/*.po
	scripts/i18n-update.sh --check
	scripts/check-desktop.sh
	uvx --with mkdocs-material --with pymdown-extensions --from "mkdocs<2" mkdocs build --strict

# ── Intégration au bureau (issue #155) ──
# check-desktop : lanceur, AppStream et icônes (desktop-file-utils, appstream).
# icons : régénère data/icons/hicolor/ depuis icon.png (ImageMagick).
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

# ── Lint (ruff + flake8) ──────────────────────────────────────────────────────
# session-35 (2026-09-14) : la cible ne couvrait que gnome_connection_manager.py,
# gcm4_core.py et tests/ — les plugins/, models.py, netmiko_bulk_core.py,
# utils.py et widgets.py n'étaient jamais vérifiés. Élargi à tout le dépôt
# (respecte l'exclude de pyproject.toml, dont SSH-Studio, vendorisé
# et hors périmètre GCM).
# session-36 (2026-09-16) : `ruff check .` passe désormais sans aucune erreur
# (les 423 erreurs pré-existantes de tests/test_gcm.py, docstrings manquantes,
# ont été comblées). `make lint` échoue néanmoins toujours aujourd'hui, pour
# une raison distincte nouvellement découverte : flake8 n'a jamais été
# configuré pour ce dépôt (pas de .flake8/setup.cfg avant cette session) et
# tournait donc avec sa limite par défaut de 79 caractères au lieu des 99
# déclarés partout ailleurs — corrigé (.flake8 ajouté), mais 182 erreurs
# flake8 réelles restent sur gnome_connection_manager.py (97 lignes >99
# caractères + 85 constats de code légué déjà ignorés par ruff pour ce
# fichier), non corrigées cette session (hors périmètre de la demande
# « erreurs ruff », voir docs/sessions/session-36.md).
lint:
	ruff check .
	flake8 gnome_connection_manager.py

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
	rm -rf $(TMPINSTALLDIR)
	rm -rf build-deb/_build build-deb/dist
	rm -f $(PKG_RPM)
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
