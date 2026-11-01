# Gnome Connection Manager (GCM) — v1.3.3

> **Le gestionnaire de connexions multi-protocoles le plus complet pour GNOME / GTK3.**

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://python.org)
[![GTK 3](https://img.shields.io/badge/GTK-3.24-green.svg)](https://gtk.org)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-780-brightgreen.svg)](tests/)
[![Ruff](https://img.shields.io/badge/ruff-pass-brightgreen.svg)](pyproject.toml)

**Fork de [kuthulux/gnome-connection-manager](https://github.com/kuthulux/gnome-connection-manager)**
maintenu par [MathildeDec](https://github.com/MathildeDec/gnome-connection-manager).

---

## Pourquoi GCM ?

GCM regroupe dans une seule fenêtre à onglets **tous vos accès distants** :
terminal SSH, bureau RDP embarqué, console série RS-232/485, VNC, SPICE,
telnet, session locale, console BMC IPMI SOL et visualiseur web BMC. Plus
besoin de jongler entre dix outils différents. Chaque protocole est un
**plugin** (`plugins/plugin_*.py`), chargé automatiquement au démarrage —
ajouter un protocole n'implique pas de modifier le cœur de l'application.

| Protocole | Implémentation | Description |
|-----------|---------------|-------------|
| **SSH** | VTE intégré | Terminal complet avec agent, tunnels, cluster mode |
| **RDP** | XEmbed + xfreerdp | Bureau Windows **directement dans l'onglet** GCM (XEmbed X11) ou fenêtre externe (Wayland) |
| **VNC** | vncviewer / vinagre / remmina (ou widget natif `GtkVnc.Display` si dispo) | Connexion VNC avec authentification |
| **SPICE** | remote-viewer / virt-viewer (ou widget natif `SpiceClientGtk.Display` si dispo) | Console VM SPICE haute performance (mode URI, tunnel libvirt, ticket Proxmox) |
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

### 🔷 RDP intégré via XEmbed
- La session Windows s'affiche **dans l'onglet GCM** (pas une fenêtre séparée)
- Bascule automatique sur fenêtre externe si Wayland pur est détecté
- Support `xfreerdp2` et `xfreerdp3`, `/cert:ignore`, `/dynamic-resolution`
- Domaine `CORP\user` géré automatiquement (`/d:` + `/u:`)

### 🔌 Console Série RS-232 / RS-485
- Terminal VTE **embarqué** (pas de fenêtre externe)
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
- **CSV / JSON** : import et export d'hôtes (l'export ne contient jamais les mots de passe)

### 🚀 Déploiement de configuration en masse
- **Netmiko** (`plugin_netmiko_push.py`) : envoi de commandes CLI en masse sur un inventaire d'équipements réseau (SSH), profils réutilisables, gabarits `#colonne#` alimentés depuis l'inventaire CSV
- **SNMP** (`plugin_snmp_push.py`) : dépôt de configuration TFTP/FTP puis déclenchement par `SNMP SET`, exécution parallèle (`ThreadPoolExecutor`) ; constructeurs supportés : Cisco, H3C **et Huawei VRP** (`HUAWEI-CONFIG-MAN-MIB`)

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
- Fallback : `~/.config/gtk-3.0/settings.ini`
- Mode sombre activé si `color-scheme = prefer-dark`

### 🧙 Qualité du code
- **ruff** (linter + formateur, style Google docstring) + **flake8**
- **pre-commit** : hooks automatisés à chaque commit (`pre-commit install`)
- 780 tests unitaires pytest — 506 dans `tests/test_gcm.py` (nécessitent
  GTK3/VTE réels), 274 répartis sur 19 autres fichiers sans dépendance GTK
  (chiffrement, Host, HostUtils, Serial, RDP, VNC, SPICE, libvirt, Proxmox,
  proto-icons, validation port, gestes/menus contextuels GTK4, éditeur
  inline, audit `plugins/ssh/gtk4.py` — voir `docs/architecture.md` §2.8 ;
  chiffres réels vérifiés session-45, `docs/sessions/session-45.md`)

---

## Installation

### Depuis les sources

```bash
git clone https://github.com/MathildeDec/gnome-connection-manager
cd gnome-connection-manager
./gnome_connection_manager.py
```

### Dossier de configuration alternatif

Par défaut, GCM stocke sa configuration dans `~/.gcm/`. Pour utiliser un autre
emplacement (poste partagé, tests, profils multiples) :

```bash
./gnome_connection_manager.py --config /chemin/vers/dossier
./gnome_connection_manager.py -c /chemin/vers/dossier
```

### Dépendances (Debian / Ubuntu)

```bash
# Obligatoires
sudo apt install python3 python3-gi gir1.2-vte-2.91 gir1.2-gtk-3.0 expect python3-pycryptodome

# RDP (plugin rdp GTK4, AsyncRdp sur FreeRDP 3 ; gtk-frdp est abandonné, #71)
sudo apt install freerdp3-dev python3-dev build-essential
git clone https://github.com/Mathilde-Team/AsyncRdp4.git
pip install --break-system-packages -e AsyncRdp4
export ASYNCRDP_GTK4_DIR="$PWD/AsyncRdp4/integrations/gtk4"
# VNC
sudo apt install tigervnc-viewer       # ou vinagre, remmina

# SPICE
sudo apt install gir1.2-spiceclientgtk-3.0       # fournit remote-viewer

# Console série
sudo apt install picocom               # ou minicom

# Import libvirt (recommandé : paquet système)
sudo apt install python3-paramiko python3-libvirt
# Alternative pip (si python3-paramiko absent de votre distro) :
# pip3 install --user paramiko
```

### Dépendances (Fedora / RHEL)

```bash
sudo dnf install python3 python3-gobject vte291 expect freerdp \
                 tigervnc virt-viewer picocom python3-paramiko python3-pycryptodome
```

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
LANG=en_US.UTF-8 ./gnome_connection_manager.py
LANG=de_DE.UTF-8 ./gnome_connection_manager.py
```

---

## Packaging (deb / rpm / opensuse)

### Prérequis

```bash
# Debian/Ubuntu
sudo apt install git ruby ruby-dev build-essential gettext python3-paramiko
sudo gem install fpm

# Fedora/RHEL
sudo dnf install git ruby ruby-devel make gettext python3-paramiko
sudo gem install fpm
```

### Cibles `make`

| Commande | Description |
|----------|-------------|
| `make` | Génère `.deb` + `.rpm` Fedora |
| `make deb` | `.deb` Debian / Ubuntu de gcm4 (`build-deb/dist/`) |
| `make rpm` | `.rpm` Fedora / RHEL / CentOS |
| `make opensuse` | `.rpm` **openSUSE / SLES** (dépendances zypper : `typelib-1_0-Vte-2.91`) |
| `make translate` | Compile les `.po` de `lang/LINGUAS` → `.mo` |
| `make i18n-update` | Extrait les chaînes (`lang/messages.pot`) et met à jour les `.po` |
| `make i18n-check` | Échoue si le `.pot` ou un `.po` est périmé (CI) |
| `make validate` | Valide `.po` (msgfmt) + `.glade` / `.xml` / `.json` |
| `make test` | Lance la suite pytest (777 tests) |
| `make lint` | ruff + flake8 |
| `make check` | validate + lint + test + vérification git propre |
| `make clean` | Supprime paquets et `__pycache__` |
| `make help` | Affiche l'aide |

```bash
git clone https://github.com/MathildeDec/gnome-connection-manager
cd gnome-connection-manager
make            # deb + rpm Fedora
make deb        # deb de gcm4 seulement
make opensuse   # rpm openSUSE
make validate   # valider .po + .glade avant commit
```

### Environnement de développement Python

Dépendances Python gérées par [uv](https://docs.astral.sh/uv/) (`pyproject.toml`
+ `uv.lock`, projet non empaqueté — `[tool.uv] package = false`). Installation
automatisée, paquets système GTK/VTE inclus :

```bash
./bootstrap-dev.sh              # installe tout, support RDP+SNMP inclus
uv run python3 gnome_connection_manager.py
```

Voir `bootstrap-dev.sh --help` pour les options (`--without-rdp`,
`--without-snmp`, `--no-apt`).

#### Nouvelle interface GTK4 (`gcm4`, en cours de migration)

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
| `trailing-whitespace` | tous | supprime espaces en fin de ligne |
| `end-of-file-fixer` | tous | assure un `\n` final |
| `check-yaml` / `check-toml` / `check-json` | `.yaml` / `.toml` / `.json` | syntaxe |
| `ruff` | `.py` | lint + autofix |
| `ruff-format` | `.py` | formatage |
| `flake8` | `.py` | lint complémentaire |
| `validate-po` | `.po` | `msgfmt --check` — bloque si traduction invalide |
| `validate-xml-glade-json` | `.glade` / `.xml` / `.json` | parser XML/JSON Python — bloque si syntaxe cassée |

---

## Nouveautés

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
- **SerialTab** : console série RS-232/RS-485 embarquée dans VTE — picocom / minicom / screen
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
./gnome_connection_manager.py --debug          # ou GCM_DEBUG=1
GCM_LOG_LEVEL=TRACE ./gnome_connection_manager.py
```

Le journal permanent est `~/.gcm/log/gcm-app.log`. Niveaux, variables
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
- `pyAES.py` : MIT (Brandon Sterne).
- Chaque fichier de code indique son régime par un en-tête `SPDX-License-Identifier` (vérifié par `tests/test_licence.py`).


---

## Écosystème Gcm4 — bibliothèques asynchrones

Gcm4 s'appuie sur deux bibliothèques asynchrones dédiées, développées en parallèle :

### AsyncVnc2 — client VNC asynchrone

- **Dépôt :** [MathildeDec/AsyncVnc4](https://github.com/MathildeDec/AsyncVnc4)
- **Rôle :** Client RFB/VNC pur Python (23 patches, 50 tests), consommé par le plugin `pluginvnc2`
- **Annonce :** [Gcm4 #102](https://github.com/MathildeDec/Gcm4/issues/102)
- **Liaison plugin :** [Gcm4 #104](https://github.com/MathildeDec/Gcm4/issues/104)

### AsyncRdp — client RDP asynchrone

- **Dépôt :** [MathildeDec/AsyncRdp4](https://github.com/MathildeDec/AsyncRdp4)
- **Rôle :** Binding asyncio sur libfreerdp3 via cffi (122 tests), qui remplace GtkFrdp (plugin rdp GTK4, #105/#71)
- **Annonce :** [Gcm4 #103](https://github.com/MathildeDec/Gcm4/issues/103)
- **Liaison plugin :** [Gcm4 #105](https://github.com/MathildeDec/Gcm4/issues/105)

### Roadmap d'intégration

La roadmap coordonnée des trois dépôts est dans [Gcm4 #106](https://github.com/MathildeDec/Gcm4/issues/106), avec un tableau de bord dans [Gcm4 #107](https://github.com/MathildeDec/Gcm4/issues/107).

| Phase | Objectif | Issues clés |
|-------|----------|-------------|
| 1 | Stabilisation bibliothèques | AsyncVnc4 #22, #34 · AsyncRdp4 #36 |
| 2 | Intégration plugins Gcm4 | Gcm4 #104, #105, #71 |
| 3 | Tests serveurs réels | AsyncVnc4 #27-#32 · AsyncRdp4 #34, #37 |
| 4 | Migration GTK4 + nettoyage | Gcm4 #97, #96, #98, #99 |

Priorités unifiées (P1-P4) et tracks de travail parallèle (VNC / RDP / Gcm4) communs aux trois dépôts.
