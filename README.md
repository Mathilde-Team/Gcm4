# gcm4 (Gnome Connection Manager) — v4.0.0

> **Le gestionnaire de connexions multi-protocoles le plus complet pour GNOME / GTK4.**

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://python.org)
[![GTK 4](https://img.shields.io/badge/GTK-4.0-green.svg)](https://gtk.org)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-1701-brightgreen.svg)](tests/)
[![Ruff](https://img.shields.io/badge/ruff-pass-brightgreen.svg)](pyproject.toml)

**Fork de [kuthulux/gnome-connection-manager](https://github.com/kuthulux/gnome-connection-manager)**
maintenu par [MathildeDec](https://github.com/Mathilde-Team/Gcm4).

---

## Pourquoi GCM ?

GCM regroupe dans une seule fenêtre à onglets **tous vos accès distants** :
terminal SSH, bureau RDP embarqué, console série RS-232/485, VNC, SPICE,
telnet, session locale, console BMC IPMI SOL et visualiseur web BMC. Plus
besoin de jongler entre dix outils différents. Chaque protocole est un
**plugin** (`plugins/<nom>/` : `core.py` pour la logique sans GTK +
`gtk4.py` pour l'interface), chargé automatiquement au démarrage —
ajouter un protocole n'implique pas de modifier le cœur de l'application.

| Protocole | Implémentation | Description |
|-----------|---------------|-------------|
| **SSH** | xterm.js intégré (WebKitGTK 6) | Terminal complet avec agent, tunnels, cluster mode |
| **RDP** | plugin `plugins/rdp/` sur AsyncRdp (FreeRDP 3) | Bureau Windows **directement dans l'onglet** : audio, redirection USB, multi-écran, presse-papier, dossier partagé, RemoteApp |
| **VNC** | plugin `plugins/pluginvnc2/` sur AsyncVnc (widget natif) | Connexion VNC avec authentification, mode lecture seule, partage de session |
| **SPICE** | remote-viewer / virt-viewer via fichier `.vv` | Console VM SPICE haute performance (tunnel libvirt, ticket Proxmox) |
| **Série** | picocom / minicom / screen | Port série RS-232/RS-485 avec **11 templates constructeurs** |
| **Telnet** | telnet / expect | Sessions Telnet classiques |
| **Local** | shell | Terminal local |
| **SFTP** (gcm4) | paramiko | Navigateur de fichiers distant : navigation, téléchargement, envoi, glisser-déposer (`docs/guide/sftp.md`) |
| **IPMI SOL** | ipmitool | Console BMC Serial-over-LAN (iLO/iDRAC/IMM…) |
| **Web** | vue HTML5 intégrée | Visualiseur console BMC (iLO/iDRAC/IMM) ou n'importe quelle URL |

---

## Fonctionnalités clés

### 🖥️ Multi-protocoles dans un seul outil
- Ouvrez un onglet SSH, un onglet RDP et une console série côte à côte
- Chaque connexion conserve ses propres paramètres (police, couleurs, tunnel…)
- Sauvegarde chiffrée des mots de passe (AES)

### 🔷 RDP intégré (plugin `plugins/rdp/`)
- La session Windows s'affiche **dans l'onglet gcm4**, via le client AsyncRdp
  (binding asyncio sur libfreerdp3, pas d'X11/XEmbed)
- Support **FreeRDP 3**, audio, redirection USB, multi-écran, presse-papier,
  dossier partagé et RemoteApp
- Exige PyGObject ≥ 3.50 (boucle asyncio GLib) ; le répertoire des
  intégrations se configure via `[plugin rdp] integrations` dans
  `gcm4.conf` ou la variable `ASYNCRDP_GTK4_DIR`
- Domaine `CORP\user` géré automatiquement

### 🔌 Console Série RS-232 / RS-485
- Terminal xterm.js **embarqué** (WebKitGTK 6, ressources locales, pas de fenêtre externe)
- Contrôles dédiés : débit, bits de données, parité, bits de stop, contrôle de flux
- **11 templates constructeurs** pré-configurés :

  | Template | Débit | Format |
  |----------|-------|--------|
  | Cisco IOS / IOS-XE / NX-OS | 9600 | 8N1 |
  | HP Comware (H3C) | 9600 | 8N1 |
  | Aruba AOS-S / AOS-CX | 9600 | 8N1 |
  | Juniper JunOS | 9600 | 8N1 |
  | Fortinet FortiOS | 9600 | 8N1 |
  | Palo Alto PAN-OS | 9600 | 8N1 |
  | F5 TMOS | **19200** | 8N1 |
  | Linux / Raspberry Pi | **115200** | 8N1 |
  | Arduino / ESP32 | **115200** | 8N1 |
  | RS-485 Modbus RTU | 9600 | 8N1 |
  | Libre (manuel) | configurable | configurable |

### 📦 Import libvirt — dialogue en 2 phases
- **Phase 1 — Scan** : saisie des URIs libvirt, user SSH, cases à cocher SSH/SPICE/RDP/VNC, log en temps réel, barre de progression
- **Phase 2 — Prévisualisation** : tableau des connexions découvertes (scroll, > 250 lignes) avant import
  — colonnes : Proto | Nom VM | Groupe | IP/Hôte | État | Importé
  — lignes déjà importées grisées / cases décochées, nouvelles cochées par défaut
  — case «Écraser existants», boutons **Tout cocher / Tout décocher**
- **SSH** : jamais direct — ProxyJump (`-J user@hv:port`) si IP connue, sinon shell HV + `-t ssh user@vm`
- **SPICE** : `virt-viewer --connect qemu+ssh://user@hv/system vm` (tunnel SSH natif, pas d'URI `spice://`)
- **RDP** : uniquement si port 3389 confirmé ouvert (sondé depuis l'HV via `nc`/`nmap`)
- **VNC** : uniquement si port 5900 confirmé ouvert

### 🖥️ Import Proxmox — nativement via `qm`
- **Menu Fichier → Importer depuis Proxmox…** — idem libvirt mais inventaire via `qm list` / `qm config`
- **SPICE** : détecte `vga: qxl` → génère un ticket SPICE Proxmox à la connexion via `pvesh` + SSH → fichier `.vv` temporaire → `remote-viewer`
- **Résolution IP améliorée** : pipeline 4 niveaux — QGA → `ipconfig0` → ARP filtré par MAC → nmap ping scan
- **VNC** : sonde le port 5900 depuis l'hyperviseur

### 📥 Autres imports d'infrastructure
- **VirtualBox** : Menu Fichier → Importer depuis VirtualBox… — saisie manuelle d'une URI `vbox+ssh://user@host[:port]/`
- **oVirt** : Menu Fichier → Importer depuis oVirt… — pilotage de l'Engine oVirt (URL + API REST + utilisateur SSH)
- **PuTTY** : Menu Fichier → Importer depuis PuTTY… — import des sessions depuis le registre Windows (`.reg` exporté)

### 🚀 Déploiement de configuration en masse
- **Netmiko** (`plugins/netmiko_push/`) : envoi de commandes CLI en masse sur un inventaire d'équipements réseau (SSH), profils réutilisables, gabarits `#colonne#` alimentés depuis l'inventaire CSV
- **SNMP** (`plugins/snmp_push/`) : dépôt de configuration TFTP/FTP puis déclenchement par `SNMP SET`, exécution parallèle (`ThreadPoolExecutor`) ; constructeurs supportés : Cisco, H3C **et Huawei VRP** (`HUAWEI-CONFIG-MAN-MIB`)

### 🛠️ Outils en ligne de commande (`tools/`)

| Script | Description |
|--------|-------------|
| [tools/libvirt\_inventory.py](tools/libvirt_inventory.py) | Inventaire complet des VMs KVM/QEMU : IP, OS, vCPUs, RAM, ports SPICE/VNC/RDP |
| [tools/ssh\_deploy.py](tools/ssh_deploy.py) | Déploiement de clés SSH (RSA-4096 + Ed25519) sur toutes les VMs depuis l’HV |

**`libvirt_inventory.py`** — exports multiples :
```bash
python3 tools/libvirt_inventory.py \
  --detect-ports \
  --output-json inventory.json \
  --output-csv  inventory.csv \
  --ansible-ini inventory.ini \
  --ansible-yaml inventory.yml
```

Options principales : `--uris`, `--no-os`, `--no-nmap`, `--ssh-password`, `--ssh-timeout`

**`ssh_deploy.py`** :
```bash
# Génère clés RSA/Ed25519 sur l’HV + déploiement via ssh-copy-id vers toutes les VMs
python3 tools/ssh_deploy.py --inventory inventory.json --vm-user ubuntu
```

### 🌐 29 langues supportées
ar · bn · cs · da · de · el · en · fa · fi · fr · he · hi · hu · id · it · ja ·
ko · nb · nl · pl · pt · ro · ru · sv · th · tr · uk · vi · zh

### 🎨 Thème GTK bureau
- GCM lit automatiquement le thème GTK du bureau au démarrage (`gsettings org.gnome.desktop.interface gtk-theme`)
- Mode sombre activé si `color-scheme = prefer-dark`

### 🧙 Qualité du code
- **ruff** (linter + formateur, style Google docstring)
- **pre-commit** : hooks automatisés à chaque commit (`pre-commit install`)
- 1701 tests unitaires pytest dans `tests/` (sans dépendance GTK) et 522
  tests d'interface dans `tests/gtk4/` (GTK4 réels sous Xvfb) —
  l'ancienne suite `tests/test_gcm.py` de l'application GTK3 a été
  supprimée avec le cœur GTK3 (#182)

---

## Installation

### Depuis les sources

```bash
git clone https://github.com/Mathilde-Team/Gcm4
cd Gcm4
uv sync
uv run gcm4
```

### Dossier de configuration alternatif

Par défaut, gcm4 stocke sa configuration dans `~/.config/gcm4/` (migration
automatique depuis `~/.gcm/`, jamais destructive). Pour utiliser un autre
emplacement (poste partagé, tests, profils multiples) :

```bash
uv run gcm4 --config /chemin/vers/dossier
uv run gcm4 -c /chemin/vers/dossier
```

### Dépendances (Debian / Ubuntu)

```bash
# Obligatoires
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 expect python3-pycryptodome

# RDP : FreeRDP 3 côté système (le plugin GTK4 plugins/rdp/ s'en charge,
# plus besoin de la bibliothèque gtk-frdp héritée, supprimée en #182)
# VNC — aucun paquet externe : widget natif AsyncVnc (plugin pluginvnc2)

# SPICE
sudo apt install virt-viewer       # remote-viewer (gcm4 et GTK3)

# Console série
sudo apt install picocom               # ou minicom

# Import libvirt (recommandé : paquet système)
sudo apt install python3-paramiko python3-libvirt
# Alternative pip (si python3-paramiko absent de votre distro) :
# pip3 install --user paramiko
```

### Dépendances (Fedora)

```bash
sudo dnf install python3 python3-gobject gtk4 libadwaita webkitgtk6.0 expect freerdp \
                 tigervnc virt-viewer picocom python3-paramiko python3-pycryptodome
```

`webkitgtk6.0` fournit le backend GTK4 des terminaux sur
[Fedora](https://packages.fedoraproject.org/pkgs/webkitgtk/webkitgtk6.0/index.html).
Cela ne rend pas ces commandes applicables telles quelles à Rocky 9 :
voir [les limites du terminal](docs/guide/terminal-xterm.md).

### Qualité du code

```bash
pip install ruff pre-commit black
pre-commit install          # installe les hooks git
pre-commit run --all-files  # vérification manuelle
```

---

## Utilisation rapide

### Ajouter une connexion SSH
1. Clic droit sur un groupe → **Nouveau hôte**
2. Remplir : nom, adresse, utilisateur, port (22)
3. Protocole : `ssh` (défaut)
4. Double-clic sur le host pour ouvrir l’onglet terminal

### Ajouter une connexion RDP
1. **Nouveau hôte** → protocole `rdp`, port `3389`
2. Renseigner `Utilisateur` et optionnellement `Mot de passe`
3. Le bureau Windows s'ouvre directement dans l'onglet

### Ajouter une connexion série
1. **Nouveau hôte** → protocole `serial`
2. Host = chemin du port (`/dev/ttyUSB0`), Port = débit initial (`9600`)
3. À l'ouverture : sélectionnez un **template** pour pré-remplir tous les paramètres

### Importer des VMs libvirt
1. Menu **Fichier → Importer depuis libvirt…**
2. Les URIs virt-manager sont pré-remplies depuis dconf
3. Cocher les protocoles à importer : SSH / SPICE / RDP / VNC
4. Cliquer **🔍 Scanner les hyperviseurs** — le log s'affiche en temps réel
5. Phase 2 : vérifier le tableau, cocher/décocher les VMs, cliquer **⬇ Importer les sélectionnés**

### Importer des VMs Proxmox
1. Menu **Fichier → Importer depuis Proxmox…**
2. Saisir l'URI de l'hyperviseur : `qemu+ssh://root@192.168.105.41/system`
3. Cocher les protocoles : SSH / SPICE / RDP / VNC
4. Pour SPICE : la VM doit avoir `vga: qxl` (GCM le détecte automatiquement)
5. À la connexion SPICE, GCM génère un ticket via `pvesh` — aucune configuration manuelle nécessaire

---

## Langue

GCM détecte automatiquement la langue du système. Pour forcer :

```bash
LANG=en_US.UTF-8 uv run gcm4
LANG=de_DE.UTF-8 uv run gcm4
```

---

## Packaging (deb / rpm)

### Cibles `make`

| Commande | Description |
|----------|-------------|
| `make` | Teste, lint, puis génère `.deb` + `.rpm` gcm4 + traductions + validation |
| `make deb` | `.deb` Debian / Ubuntu de gcm4 (`build-deb/dist/`) |
| `make smoke-deb` | Installe le `.deb` (sudo apt) et le vérifie |
| `make rpm-gcm4` | `.rpm` de la base GTK4 (`build-rpm/dist/`) |
| `make smoke-rpm` | Vérifie le contenu du `.rpm` (`rpm -qpl`, sans l'installer) |
| `make translate` | Compile les `.po` de `lang/LINGUAS` → `.mo` |
| `make i18n-update` | Extrait les chaînes (`lang/messages.pot`) et met à jour les `.po` |
| `make i18n-check` | Échoue si le `.pot` ou un `.po` est périmé (CI) |
| `make i18n-report` | Taux de traduction par langue |
| `make validate` | Valide `.po` (msgfmt) + `.glade` / `.xml` / `.json` |
| `make test` | Lance la suite pytest |
| `make test-xterm` | Teste le pont JavaScript du terminal (Node 22) |
| `make lint` | ruff |
| `make check` | validate + lint + test + vérification git propre |
| `make ci` | Rejoue les étapes Qualité, Types, i18n, bureau et documentation |
| `make coverage-combined` | Fusionne les données de couverture et vérifie le seuil (95 % sur `src/gcm4/core`) |
| `make check-desktop` | Valide lanceur, AppStream et icônes (`data/`) |
| `make icons` | Régénère `data/icons/hicolor/` depuis `data/icon.png` |
| `make class-diagram` | Régénère `docs/class-diagram.md` |
| `make docs` | Construit la documentation MkDocs (`site/`, strict) |
| `make run-gtk4` | Lance gcm4 (GTK4) dans `.venv` |
| `make clean` | Supprime paquets et répertoires temporaires |
| `make help` | Affiche l'aide |

```bash
git clone https://github.com/Mathilde-Team/Gcm4
cd Gcm4
make deb        # deb de gcm4
make rpm-gcm4   # rpm de gcm4
make validate   # valider .po avant commit
```

### Environnement de développement Python

Dépendances Python gérées par [uv](https://docs.astral.sh/uv/) (`pyproject.toml`
+ `uv.lock`, projet non empaqueté — `[tool.uv] package = false`). Installation
automatisée, paquets système GTK/WebKit inclus :

```bash
./bootstrap-dev.sh              # installe tout, support SNMP inclus
uv run gcm4
```

Voir `bootstrap-dev.sh --help` pour les options (`--without-snmp`, `--no-apt`).

#### Interface GTK4 (`gcm4`)

PyGObject, GTK 4 et libadwaita ne s'installent **pas** avec uv : ils
viennent des paquets système, et le venv doit les voir. `bootstrap-dev.sh`
le fait ; à la main :

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
rm -rf .venv
uv venv --system-site-packages --python "$(command -v python3)" .venv
uv sync --group dev
make run-gtk4            # = uv run gcm4 ; make run-gtk4 ARGS="--debug"
```

- `--system-site-packages` rend visibles les paquets système (`gi`) ;
  `--python` impose l'interpréteur **système** : avec un Python téléchargé
  par uv, les paquets système vus seraient les siens et `import gi`
  échouerait.
- gcm4 exige **Python ≥ 3.12** (Debian 13, Ubuntu 24.04, Fedora 39 ou plus
  récent). Avec un Python système plus ancien, `bootstrap-dev.sh` prévient.
- Sans ce venv, `uv run gcm4` s'arrête avec le code 3 et indique les
  paquets à installer ; `make run-gtk4` refuse un `.venv` créé sans
  `--system-site-packages`.
- Tests de l'interface : `GCM_TESTS_GTK4=1 xvfb-run -a uv run pytest tests/gtk4`.
- Tests sautés (#160) : un test qui a besoin de gettext porte le marqueur
  `requires_gettext`, ceux de `tests/gtk4` reçoivent `requires_gtk4`. Avec
  `GCM_TESTS_EXIGES=gettext` (job Qualité, `make ci`) ou `GCM_TESTS_GTK4=1`
  (job GTK4), un test marqué qui est sauté échoue ; sans ces variables, il
  reste sauté et `pytest -rs` en donne la raison. `pytest -m requires_gettext`
  ne lance que ces tests.

### Qualité du code

```bash
pip install ruff pre-commit
pre-commit install          # installe les hooks git
pre-commit run --all-files  # vérification manuelle
```

**Hooks pre-commit actifs** :

| Hook | Fichiers ciblés | Action |
|------|----------------|--------|
| `ruff` | `.py` | lint + autofix |
| `ruff-format` | `.py` | formatage |
| `trailing-whitespace` | tous | supprime espaces en fin de ligne |
| `end-of-file-fixer` | tous | assure un `\n` final |
| `check-merge-conflict` | tous | bloque les marqueurs de conflit oubliés |
| `check-added-large-files` | tous | bloque les fichiers > 2 Mo ajoutés par erreur |
| `check-circular-imports` | `.py` | refuse les imports circulaires (`tools/check_circular_imports.py`) |
| `class-diagram` | `.py` | régénère et vérifie `docs/class-diagram.md` |

---

## Nouveautés

### v4.0.0 — migration GTK4
- **GTK4 + libadwaita** : refonte complète de l'interface (stratégie MVC),
  application `gcm4` en Python ≥ 3.12, configuration dans
  `~/.config/gcm4/gcm4.conf` (migration automatique depuis `~/.gcm/`)
- **GTK3 supprimé** : l'ancienne application `GTK3.OLD/` a été retirée (#179,
  #182) ; verrou CI « zéro fichier GTK3 »
- **Architecture à plugins `core.py` + `gtk4.py`** pour tous les protocoles
  (contrat v1), cœur neutre vis-à-vis des protocoles
- **RDP en widget intégré** : plugin `plugins/rdp/` sur AsyncRdp/FreeRDP 3 —
  audio, redirection USB, multi-écran, presse-papier, dossier partagé,
  RemoteApp (#71, #105)
- **VNC natif** : plugin `plugins/pluginvnc2/` sur AsyncVnc (#102)
- **Terminal xterm.js** (WebKitGTK 6) pour SSH, série et telnet
- **Import PuTTY** (#436), imports d'hyperviseurs livrés (#439) :
  libvirt, Proxmox, VirtualBox, oVirt
- **Déploiement en masse** : `netmiko_push` (#441) et `snmp_push` (#445)
- 2223 tests (1701 unitaires + 522 interface GTK4)

### v1.3.3
- **Résolution IP Proxmox améliorée** : pipeline 4 niveaux QGA → `ipconfig0` → ARP filtré par MAC → nmap ping scan (identique libvirt)
- **Thème GTK bureau** : lecture `gsettings gtk-theme` + `color-scheme` (mode sombre) au démarrage
- **491 tests unitaires** : +44 tests (SPICE Proxmox mock, icônes protocole, résolution IP, détection QXL, sous-groupes import, validation port, VNC)
- Fix `UnboundLocalError` : variable `_stdin` pour unpacking `exec_command` (conflit avec `gettext._`)
- Fix ARP Proxmox : filtrage par MAC VM (évitait de retourner l'IP d'une autre VM)

### v1.3.2
- **Import libvirt refondu** : dialogue 2 phases (scan → tableau de prévisualisation)
  - SSH via ProxyJump (`-J`), SPICE via tunnel libvirt natif, RDP conditionnel (port 3389)
  - Tableau scrollable (>250 VMs), cases à cocher, case «Écraser existants»
  - Déplacé dans le menu **Fichier** (plus dans Serveurs)
- **`SpiceTab`** : détection `--connect` pour tunnel libvirt natif (bypass URI `spice://`)
- **Outils CLI** `tools/` : `libvirt_inventory.py` v4 + `ssh_deploy.py`
  - Exports JSON / CSV / Ansible INI / Ansible YAML
  - Détection OS (XML, guest-agent, SSH), vCPUs, RAM, ports SPICE/VNC/RDP
  - `--ssh-password`, `--ssh-timeout`, `--detect-ports`, `--no-nmap`
- **Qualité** : `pyproject.toml` + `.pre-commit-config.yaml` (ruff Google style + flake8)
- **Corrections GTK3** : audit complet dépréciations v1.3.2 (`override_font→CssProvider`, `GtkPaned`, `get_color→get_rgba`, etc.)

### v1.3.1
- **SerialTab** : console série RS-232/RS-485 embarquée dans xterm.js : picocom / minicom / screen
  - 11 templates constructeurs (Cisco, HP, Aruba, Juniper, Fortinet, Palo Alto, F5, Linux, Arduino, Modbus, Libre)
  - Combos dédiés : débit, bits de données, parité, bits de stop, contrôle de flux
- **VncTab** : connexion VNC native (vncviewer / vinagre / remmina)
- **SpiceTab** : connexion SPICE (remote-viewer / virt-viewer)
- **16 langues** : ajout uk / ja / ar / tr / nl / cs / sv / nb
- **379 tests unitaires** (pytest)
- Fix `save_host_to_ini` TypeError Python 3 (champs non-str)
- Fix `encrypt_old`/`decrypt_old` Python 3 bytes/str

### v1.3.0
- **RDP XEmbed** : bureau Windows directement dans l’onglet (GtkSocket + `xfreerdp /parent-window`)
- **Import libvirt** : inventaire VMs KVM/QEMU avec détection Windows
- **GCMBase** : remplacement de `SimpleGladeApp` par `Gtk.Builder` natif
- 8 bugs upstream corrigés (#64 #66 #67 #81 #82 #87 #88 #89)
- 188 docstrings Google (#110)
- Compatibilité Python 3.13 / GTK 3.24+

---

## Journaux et mode debug

```bash
uv run gcm4 --debug          # ou GCM_DEBUG=1
GCM_LOG_LEVEL=TRACE uv run gcm4
```

Le journal permanent est `~/.config/gcm4/log/gcm-app.log`. Niveaux, variables
d'environnement, données sensibles à relire avant de joindre un journal à
un bug, règles pour les contributeurs : voir
[docs/journalisation.md](docs/journalisation.md).

## Vérification des mises à jour (gcm4)

Au premier démarrage, gcm4 demande s'il peut interroger GitHub
(`api.github.com`) pour savoir si une version plus récente est publiée ;
rien n'est envoyé sans cet accord. Le choix se change dans les
préférences (Général > Vérifier les mises à jour). `GCM_NO_UPDATE_CHECK=1`
désactive entièrement la vérification (paquets de distribution, postes
sans accès Internet).

---

## Contribuer

Les pull requests sont les bienvenues.
Consultez [CLAUDE.md](CLAUDE.md) pour l'état courant et la prochaine étape, et
[docs/features-backlog.md](docs/features-backlog.md) pour l'état d'avancement
détaillé et les fonctionnalités planifiées.
Pour les changements importants, ouvrez d’abord une issue.

## Licence

Texte complet : [LICENSE](LICENSE) (GNU GPL version 3). Détail des régimes : [NOTICE](NOTICE).

- Code écrit par l'équipe : **GPL-3.0-or-later**.
- Fichiers repris de l'amont gnome-connection-manager (Renzo Bertuzzi) : **GPL-3.0 seule**, comme le prévoit l'avis amont (« version 3 », sans « ou toute version ultérieure »). Le programme distribué, qui combine les deux, est donc diffusé sous GPL-3.0 seule.
- `src/gcm4/core/pyaes.py` : MIT (Brandon Sterne).
- Chaque fichier de code indique son régime par un en-tête `SPDX-License-Identifier` (vérifié par `tests/test_licence.py`).


---

## Écosystème Gcm4 — bibliothèques asynchrones

Gcm4 s'appuie sur deux bibliothèques asynchrones dédiées, développées en parallèle :

### AsyncVnc2 — client VNC asynchrone

- **Dépôt :** [Mathilde-Team/AsyncVnc4](https://github.com/Mathilde-Team/AsyncVnc4)
- **Rôle :** Client RFB/VNC pur Python (23 patches, 50 tests), consommé par le plugin `pluginvnc2`
- **Annonce :** [Gcm4 #102](https://github.com/Mathilde-Team/Gcm4/issues/102)
- **Liaison plugin :** [Gcm4 #104](https://github.com/Mathilde-Team/Gcm4/issues/104)

### AsyncRdp — client RDP asynchrone

- **Dépôt :** [Mathilde-Team/AsyncRdp4](https://github.com/Mathilde-Team/AsyncRdp4)
- **Rôle :** Binding asyncio sur libfreerdp3 via cffi, **intégré** dans gcm4
  via le plugin `plugins/rdp/` (#71, #105) — remplace l'ancien GtkFrdp,
  avec audio, USB, multi-écran, presse-papier, dossier partagé, RemoteApp
- **Annonce :** [Gcm4 #103](https://github.com/Mathilde-Team/Gcm4/issues/103)
- **Liaison plugin :** [Gcm4 #105](https://github.com/Mathilde-Team/Gcm4/issues/105)

### Roadmap d'intégration

La roadmap coordonnée des trois dépôts est dans [Gcm4 #106](https://github.com/Mathilde-Team/Gcm4/issues/106), avec un tableau de bord dans [Gcm4 #107](https://github.com/Mathilde-Team/Gcm4/issues/107).

Le tableau de bord est une synthèse manuelle dont les tableaux historiques
restent datés. Conformément au choix de l'autrice dans #107, le workflow
« Suivi des phases d'intégration » publie le rapport courant uniquement
dans le [résumé de ses exécutions GitHub Actions](https://github.com/Mathilde-Team/Gcm4/actions/workflows/integration-phases.yml) :
il ne réécrit ni le corps de l'issue ni un commentaire et conserve ses
permissions de lecture. Les issues ouvertes ne signifient pas nécessairement
qu'aucun code n'a été livré ; vérifier leurs commentaires et PR associées.

| Phase | Objectif | Issues clés | État |
|-------|----------|-------------|------|
| 1 | Stabilisation bibliothèques | AsyncVnc4 #22, #34 · AsyncRdp4 #36 | livrée |
| 2 | Intégration plugins Gcm4 | Gcm4 #104, #105, #71 | livrée |
| 3 | Tests serveurs réels | AsyncVnc4 #27-#32 · AsyncRdp4 #34, #37 | livrée |
| 4 | Migration GTK4 + nettoyage | Gcm4 #97, #96, #98, #99 | livrée |

Priorités unifiées (P1-P4) et tracks de travail parallèle (VNC / RDP / Gcm4) communs aux trois dépôts.
