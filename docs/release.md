# Publication et vérification locale

Issue #162.

## `make ci`

Rejoue localement les étapes des jobs Qualité, Types, i18n, Intégration au
bureau et documentation de `.github/workflows/ci.yml`, dans le même ordre :
ruff, flake8, format, imports circulaires, pytest avec couverture, mypy,
validation et fraîcheur des catalogues, lanceur et AppStream, MkDocs. `tests/test_make_ci.py` échoue si une étape est
ajoutée à la CI sans l'être à `make ci`.

Le hook pre-commit (`.pre-commit-config.yaml`, #90) reste plus léger
(ruff, format, imports circulaires) : `make ci` est la vérification
complète avant de pousser.

## Intégration au bureau

Issue #155. Fichiers de `data/`, nommés d'après l'identifiant de
l'application (`APPLICATION_ID`, `io.github.MathildeDec.gcm4`) :

- `io.github.MathildeDec.gcm4.desktop` : lanceur (`Exec=gcm4`) et action
  « gcm4 (debug mode) » (`gcm4 --debug`). gcm4 est une application à
  instance unique : tant que #275 (PR #277) n'est pas fusionnée, l'action
  lancée alors que gcm4 tourne déjà présente la fenêtre existante sans
  activer le mode debug ; ensuite, l'instance en cours passe en DEBUG.
- `io.github.MathildeDec.gcm4.metainfo.xml` : métadonnées AppStream
  (logithèques). La balise `<release>` suit la version de `pyproject.toml`.
- `icons/hicolor/<taille>/apps/io.github.MathildeDec.gcm4.png` : 16 à 128
  pixels, produites par `make icons` (`scripts/generer-icones.sh`) depuis
  `icon.png` (128×128, jamais agrandie) et versionnées.

`GLib.set_prgname(APPLICATION_ID)` (`Gcm4Application.__init__`) donne à la
fenêtre la classe `io.github.MathildeDec.gcm4` (`WM_CLASS` sous X11,
`app_id` sous Wayland) : c'est ce qui rattache la fenêtre à son lanceur
(`StartupWMClass`). Sans cet appel, la classe était `python3`.

`make check-desktop` (`scripts/check-desktop.sh`, job CI « Intégration au
bureau ») valide le lanceur (`desktop-file-validate`, tout constat fait
échouer), les métadonnées (`appstreamcli validate --no-net`) et les tailles
des icônes. `tests/test_integration_bureau.py` vérifie sans ces outils la
cohérence entre fichiers, identifiant, nom affiché et version.

Les paquets .deb et .rpm de la base les installent (#156, #157, sections
suivantes) ; reste la traduction du lanceur et des métadonnées (#274).

## Paquet .deb de la base GTK4

Issue #156, sur le modèle de Netcross.

- `make deb` (`build-deb/build.sh`, dpkg-buildpackage ; `make deb-gcm4`, ancien
  nom, reste un alias) produit
  `build-deb/dist/gcm4_<version>_all.deb`. Prérequis : `build-essential debhelper dh-python
  dpkg-dev gettext git python3-build python3-hatchling`.
- Version : celle de `pyproject.toml`, seule source ; `gcm4 --version` la lit
  dans le `.dist-info` du wheel installé et affiche donc la même.
- Contenu : le wheel de `pyproject.toml` (`src/gcm4` et les modules racine
  `gcm4_core`, `logging_config`, `pyAES`) décompressé dans `/usr/share/gcm4`,
  dossier privé (ces noms génériques n'ont rien à faire dans
  `dist-packages`) ; le lanceur `/usr/bin/gcm4` ; le `.desktop`, le metainfo
  et les icônes de `data/` ; un `.mo` par langue de `lang/LINGUAS` dans
  `/usr/share/locale/<langue>/LC_MESSAGES/gcm-lang.mo`. Les `.pyc` sont
  produits à l'installation (`py3compile`, dh_python3).
- Dépendances : `python3 (>= 3.12)`, `python3-gi`, `gir1.2-gtk-4.0`,
  `gir1.2-adw-1`, `python3-loguru`. **Aucune dépendance de plugin** (VTE,
  gtk-vnc, spice, freerdp, expect, paramiko) : `tests/test_paquet_deb.py` et
  le test de fumée le vérifient. `python3-loguru` n'est pas versionné :
  Ubuntu 24.04 fournit la 0.7.2, avec laquelle la suite de tests passe et
  gcm4 démarre (job « Paquets »).
- `make smoke-deb` (`scripts/smoke-test-packages.sh deb`) installe le paquet
  avec apt puis vérifie : dépendances, lanceur, `gcm4 --version`, import de
  chaque module installé, fichiers du bureau, chargement réel de chaque
  catalogue par `gcm4.core.i18n`, et démarrage sous Xvfb jusqu'à la fenêtre
  principale, journal debug sans erreur. Il installe un paquet système : à
  lancer dans un conteneur ou une machine de test. Sur une image minimale
  (conteneurs Ubuntu), dpkg ignore les `.mo` : le script refuse de tester
  tant que la règle `path-exclude` de `/etc/dpkg/dpkg.cfg.d/` est présente.
- CI : « Paquet .deb (construction) » construit le paquet sur Ubuntu 26.04 ;
  « Paquets (.deb, …) » l'installe dans des conteneurs propres Debian 13,
  Ubuntu 24.04 et Ubuntu 26.04 et lance le test de fumée.

## Paquet .rpm de la base GTK4

Issue #157, même contenu que le .deb.

- `make rpm-gcm4` (`build-rpm/build.sh`, `build-rpm/gcm4.spec`, rpmbuild)
  produit `build-rpm/dist/gcm4-<version>-1.noarch.rpm`. Prérequis sur
  Fedora : `rpm-build python3-rpm-macros gettext git python3-build
  python3-hatchling` ; construction croisée possible sur Debian/Ubuntu avec
  le paquet `rpm` (sans `.pyc` : les macros Python de Fedora manquent).
- Version de `pyproject.toml`, passée au spec (`--define gcm4_version`).
  `Release: 1` sans `%{?dist}` : un seul paquet noarch.
- Même contenu que le .deb, lanceur commun (`build-deb/wrappers/gcm4`) ;
  catalogues marqués par langue (`%find_lang gcm-lang`) ; `.pyc` produits à
  la construction sur Fedora (`%py_byte_compile`). Les modules privés de
  `/usr/share/gcm4` ne génèrent aucun `python3dist(...)`.
- Dépendances : `python3 >= 3.12`, `python3-gobject`, `gobject-introspection`,
  `gtk4`, `libadwaita`, `python3-loguru` ; aucune de plugin.
  `gobject-introspection` fournit le typelib `cairo-1.0` qu'exige celui de
  GTK 4 : sans lui, `import Gtk 4.0` échoue sur Fedora 44 (ni `gtk4` ni
  `python3-gobject` ne l'installent).
- `make smoke-rpm` (`scripts/smoke-test-packages.sh rpm`) vérifie le contenu
  sans installer (`rpm -qpl`, `rpm -qpR`) ; `smoke-test-packages.sh rpm-dnf`
  vérifie le contenu puis installe avec dnf et reprend les vérifications du
  .deb, démarrage sous Xvfb compris. Les images Fedora n'installent que
  l'anglais (`%_install_langs`) : le script refuse de tester tant que c'est
  le cas.
- CI : « Paquet .rpm (construction) » sur Fedora 44 ; « Paquets (.rpm, …) »
  l'installe dans un conteneur Fedora 44 propre.

## Paquets de l'application GTK3

- Plus de `.deb` de l'application GTK3 (décision du 2026-10-22, #156) :
  `make deb` construit le paquet gcm4 ci-dessus, et le workflow Release le
  joint aux releases à la place de l'ancien.
- `make rpm`, `make opensuse` (fpm) emballent l'arborescence
  produite par `make install` : tous les modules `.py` de la racine,
  `plugins/` hors tests, les traductions compilées, l'icône et le
  `.desktop`.
- `tests/test_paquet_contenu.py` lance `make install` et vérifie que chaque
  module local importé, même dans une fonction, est installé. Avant cette
  issue, seuls trois modules étaient copiés et l'application installée ne
  démarrait pas.
- `python3-loguru` est une dépendance de chaque paquet. Les versions des
  distributions peuvent être plus anciennes que la 0.7.3 de
  `pyproject.toml` (Debian bookworm, Ubuntu jammy) : à vérifier au premier
  test d'installation réel (#156, #157).

## Release

`.github/workflows/release.yml` :

- sur tag `v*` (ex. `v1.3.4`) : construit les trois paquets avec
  `PKG_VERSION` tiré du tag, vérifie leur contenu, les publie en artefact
  et les attache à la release du tag ;
- `workflow_dispatch` : même construction sans tag (artefacts seulement),
  version optionnelle.

`guard-main.yml` est conservé : les PR vers `main` viennent de `dev`.

## Protections de main et de dev

- **dev** : contrôles requis Qualité, Types, i18n et Tests GTK4 (Xvfb), et
  branche **à jour exigée** (`.github/branch-protection/dev.json`) : une PR
  en retard se met à jour avec `gh pr update-branch`.
- **main** : on n'y fusionne que dev (contrôle « Garde : PR vers main doit
  venir de dev »), avec Qualité. La branche à jour n'y est **pas** exigée,
  depuis le 12/10/2026. Chaque release crée sur main un commit de fusion
  absent de dev ; avec cette exigence, la release suivante était déclarée
  en retard, et il fallait une PR main → dev (#215, #220) que la
  protection de dev refusait de faire automatiquement.

## Tag de pré-version

`vX.Y.Z-rcN` donne la version de paquet `X.Y.Z~rcN`, qui reste inférieure à
`X.Y.Z` pour dpkg et rpm, et une release marquée pré-version (#211, essai
`v1.3.4-rc1`). GitHub remplace `~` par `.` dans le **nom** des fichiers
attachés, pas dans les métadonnées des paquets.
