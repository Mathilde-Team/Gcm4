# Portage des plugins GTK3 vers GTK4 : tableau de décision (#179)

**Décision de l'autrice** (#179, 01/10/2026) : « tous les plugin seront portés
en gtk4 ». Aucun plugin n'est abandonné. Pour chacun, ce tableau indique
**comment** le porter, avec quelle bibliothèque GTK4, et ce qui bloque sur les
distributions cibles. Une sous-issue par plugin est créée au moment de son
portage (deuxième case de #179).

## Méthode

Les versions ont été relevées le 21/10/2026 dans des conteneurs podman, sur les
dépôts officiels :

- **Debian** : `apt-cache policy` sur `debian:12` et `debian:13`.
- **Ubuntu** : même commande sur `ubuntu:24.04` et `ubuntu:26.04`.
- **Fedora et Rocky** : `dnf repoquery --file '*/girepository-1.0/<typelib>'`
  sur `fedora:44` et `rockylinux/rockylinux:9`. Pour Rocky 9, les dépôts
  BaseOS, AppStream, CRB et EPEL étaient actifs.

« GTK3 seulement » veut dire que le typelib dépend de `gir1.2-gtk-3.0`
(`apt-cache depends`). Le widget ne peut donc pas être placé dans une fenêtre
GTK4.

## Bibliothèques GTK4 sur les cibles

| Bibliothèque (typelib) | Debian 12 | Debian 13 | Ubuntu 24.04 | Ubuntu 26.04 | Fedora 44 | Rocky 9 |
|---|---|---|---|---|---|---|
| VTE GTK4 (`Vte-3.91`) | 0.70.6 | 0.80.1 | 0.76.0 | 0.84.0 | 0.84.1 | **absent** (VTE 0.64, GTK3 seulement) |
| WebKitGTK GTK4 (`WebKit-6.0`) | 2.50.6 | 2.54.0 | 2.52.6 | 2.52.6 | 2.54.1 | **absent** |
| gtk-vnc (`GtkVnc-2.0`) | 1.3.1, GTK3 seulement | 1.5.0, GTK3 seulement | 1.3.1, GTK3 seulement | 1.5.0, GTK3 seulement | 1.5.0, GTK3 seulement | 1.3.0, GTK3 seulement |
| spice-gtk (`SpiceClientGtk-3.0`) | 0.42, GTK3 seulement | 0.42, GTK3 seulement | 0.42, GTK3 seulement | 0.42, GTK3 seulement | 0.42, GTK3 seulement | **absent** |
| FreeRDP 3 | **absent** (2.11.7) | 3.15.0 | 3.32.1 | 3.32.1 | 3.32.2 | **absent** (2.11.7) |

Outils et modules Python appelés par les plugins :

| Outil ou module | Debian 12 | Debian 13 | Ubuntu 24.04 | Ubuntu 26.04 | Fedora 44 | Rocky 9 |
|---|---|---|---|---|---|---|
| `ipmitool` | 1.8.19 | 1.8.19 | 1.8.19 | 1.8.19 | 1.8.19 | 1.8.18 |
| `picocom` | 3.1 | 3.1 | 3.1 | 3.1 | 2024.07 | 3.1 (EPEL) |
| `telnet` | 0.17 | 0.17 | 0.17 | 0.17 | 0.17 | 0.17 |
| paramiko | 2.12.0 | 3.5.1 | 2.12.0 | 4.0.0 | 3.5.1 | 2.12.0 (EPEL) |
| libvirt-python | 9.0.0 | 11.3.0 | 10.0.0 | 12.0.0 | 12.0.0 | 11.10.0 |
| pyserial | 3.5 | 3.5 | 3.5 | 3.5 | 3.5 | 3.4 |

**Conséquence pour Rocky 9** (#223) : avec les paquets de la distribution,
aucun plugin terminal ni le mode intégré du plugin Web ne peut y fonctionner
en GTK4. La base gcm4 y démarre (#286), mais l'utilisateur n'aurait ni SSH, ni
Telnet, ni terminal local. Passer à Rocky 10 ne règle pas le problème. Rocky 10.2 fournit Python 3.12,
GTK 4.16 et libadwaita 1.6, mais pas non plus `Vte-3.91` ni `WebKit-6.0`
(BaseOS, AppStream, CRB et EPEL, relevé du même jour). Il reste deux options,
et c'est à l'autrice de trancher, sur #223 :

- garder la famille EL comme cible de la base seule ;
- fournir VTE GTK4 autrement (Flatpak, ou paquet construit par le projet).

## Tableau par plugin

« Lignes » = taille de `plugins/plugin_<nom>.py` sur `dev` (ad32608).

### Plugins de connexion (contrat v1, `ConnectionPlugin`)

| Plugin | Lignes | Technique GTK3 | Portage GTK4 | Bloquant ou décision liée |
|---|---|---|---|---|
| ssh | 1087 (+ `plugins/ssh/core.py`, 1700) | `Vte.Terminal` 2.91 | `plugins/ssh/gtk4.py`, `Vte.Terminal` 3.91. Le cœur `core.py` existe déjà, sans GTK | #97 (portage), #180 (dialogues de clés et de `ssh_config` à la racine) ; Rocky 9 sans VTE GTK4 |
| telnet | 216 | `Vte.Terminal` 2.91, commande `telnet` | **porté** : `plugins/telnet/` (contrat v1, #179) ; `Vte.Terminal` 3.91, session VTE et zone d'options partagées (`gcm4.ui.terminal`, `gcm4.core.terminal`) ; `plugin_telnet.py` (GTK3) retiré le 08/11/2026 | Rocky 9 sans VTE GTK4. **Premier portage conseillé** : le plus petit plugin terminal, il permet de valider le contrat v1 avec un vrai terminal |
| local | 192 | `Vte.Terminal` 2.91, shell local | **porté** : `plugins/local/` (contrat v1, #295) ; `Vte.Terminal` 3.91 ; `plugin_local.py` (GTK3) retiré le 08/11/2026 | Rocky 9 sans VTE GTK4 |
| serial | 689 | `Vte.Terminal` 2.91, `picocom`/`minicom`/`screen` | **porté** : `plugins/serial/` (contrat v1, #179) ; détection des outils et construction de la commande sans GTK (`core.py`), paramètres série dans la zone d'options du protocole (repli du débit sur le champ Port, héritage GTK3) ; `plugin_serial.py` (GTK3) retiré le 08/11/2026 (#179) | Rocky 9 sans VTE GTK4 ; `picocom` vient d'EPEL sur Rocky 9 |
| ipmisol | 246 | `Vte.Terminal` 2.91, `ipmitool sol activate` | **porté** : `plugins/ipmisol/` (contrat v1, #179) ; mot de passe via `IPMI_PASSWORD` (`Lancement.env`), `sol deactivate` à la fermeture ; `plugin_ipmisol.py` (GTK3) retiré le 08/11/2026 (#179) | Rocky 9 sans VTE GTK4 |
| web | 298 | `WebKit2.WebView` 4.1 ou navigateur externe (`Gio.AppInfo`) | **porté** : `plugins/web/` (contrat v1, #397) ; `WebKit.WebView` 6.0 chargé à l'ouverture seulement, certificat refusé accepté pour l'hôte de l'URL seulement (`allow_tls_certificate_for_host`, GTK3 coupait la vérification pour tout le contexte), repli vers le navigateur par défaut par `Gio.AppInfo` (présent dès GTK 4.8, `Gtk.UriLauncher` n'apporterait rien ici) ; mêmes clés `web_url` et `web_ignore_cert` que GTK3 ; `plugin_web.py` (GTK3) retiré le 08/11/2026 (#179) | Rocky 9 : repli externe seulement (tests WebKit écartés, repli testé) |
| vnc | 701 | `GtkVnc.Display` (gtk-vnc, GTK3 seulement partout) | **fusion avec `plugins/pluginvnc2`** (déjà GTK4, client asyncvnc2), comme prévu par #104 | #104 ; aucune version GTK4 de gtk-vnc dans les distributions |
| spice | 1424 | `SpiceClientGtk.Display` 3.0 (GTK3 seulement partout) | aucun widget SPICE GTK4. Deux voies : affichage maison sur `SpiceClientGLib` (canal d'affichage vers `Gdk.MemoryTexture`, comme pluginvnc2), ou visionneuse externe (`remote-viewer`) | **choix à faire** (sous-issue au moment du portage) ; spice-gtk absent de Rocky 9 |
| rdp | 614 | `GtkFrdp.Display` (`Gtk.Socket`/XEmbed, supprimé en GTK4) | **porté** : `plugins/rdp/` sur AsyncRdp (contrat v1, #105) ; mêmes clés INI que GTK3, `RdpSession` et `RdpView` des intégrations GTK4 d'AsyncRdp4, boucle asyncio sur GLib (`gcm4.ui.boucle`). gtk-frdp abandonné (décision du 2026-11-01, #71) ; `plugin_rdp.py` (GTK3) retiré le 08/11/2026 (#179) | #71 (retrait de gtk-frdp, menu contextuel) ; FreeRDP 3 absent de Debian 12 et de Rocky 9 |

### Plugins d'import, d'export et de traitement par lots (`BatchPlugin` GTK3)

Le contrat v1 de gcm4 (`src/gcm4/core/plugins.py`) ne couvre que les plugins
de connexion. Il n'a pas d'équivalent de `BatchPlugin` (entrée de menu
Import/Export/Outils) :

- `context_menu_items` ne vise que les hôtes du plugin ;
- `open_tab` ouvre un onglet, pas une entrée de menu.

**Avant de porter ces plugins, il faut ajouter au contrat un type « outil »**,
avec sa version de contrat et ses tests. C'est un travail à part, prérequis de
toutes les lignes suivantes.

| Plugin | Lignes | Technique GTK3 | Portage GTK4 | Bloquant ou décision liée |
|---|---|---|---|---|
| import_csv, import_json, export_csv, export_json | 103, 97, 100, 98 | `BatchPlugin`, dialogues `Gtk.FileChooserDialog` | **retirés** (31/10/2026) : remplacés par l'import/export CSV et JSON de la base — sans GTK (#152, PR #267), interface (#153, PR #269), ligne de commande (#154). Les quatre fichiers sont supprimés et leur entrée est sortie de l'inventaire GTK3 | remplacé par la base (fusionné) |
| import_putty | 117 | `BatchPlugin`, sessions PuTTY | **porté** : `plugins/import_putty/` (contrat v1 outil, #436) ; onglet de choix du dossier (`fichiers.choisir`, repli GTK 4.8), résumé du scan, stratégie de conflit (les trois de #153), import via le nouveau service du contexte `PluginContext.import_hosts` | premier outil porté : exemple pour les suivants |
| import_libvirt | 1174 | `BatchPlugin`, `Gtk.TreeView` | **porté** : `plugins/import_libvirt/` (contrat v1 outil, #439) ; collecte dans `core.py` sans GTK, prévisualisation en `Gtk.ColumnView` sur l'onglet générique des imports (`gcm4_hv.ui`), cibles dconf ou saisie manuelle | contrat « outil » |
| import_ovirt | 1045 | idem | **porté** : `plugins/import_ovirt/` (contrat v1 outil, #439) ; API REST `urllib` dans `core.py`, résolution de route (direct → rebond moteur → rebond hyperviseur → question manuelle via `dialogues.creer`) | contrat « outil » |
| import_proxmox | 1029 | idem | **porté** : `plugins/import_proxmox/` (contrat v1 outil, #439) ; `qm list/config/guest cmd` dans `core.py`, IP par l'agent QEMU puis ipconfig/ARP/nmap, SPICE si `vga: qxl` | contrat « outil » |
| import_virtualbox | 1039 | idem, `VBoxManage` | **porté** : `plugins/import_virtualbox/` (contrat v1 outil, #439) ; `VBoxManage` dans `core.py`, IP par guestproperty puis ARP/nmap, RDP VRDE prioritaire, VNC invité sondé | contrat « outil » |
| netmiko_push | 932 | `BatchPlugin`, `Gtk.TreeView`, `plugins/netmiko_push/core.py` | **porté** : `plugins/netmiko_push/` (contrat v1 outil, #441) ; onglet « Netmiko » en trois phases (`Gtk.Stack` Configuration / Suivi / Résultats), inventaire en `ColumnView` cochable, éditeur de profils en cascade constructeur → modèle, envoi via `core.run_bulk_push` avec annulation et journaux ouverts par `FileLauncher` | contrat « outil » |
| snmp_push | 1146 | idem, `plugins/snmp_push/core.py` | **porté** : `plugins/snmp_push/` (contrat v1 outil, #445) ; onglet « Push SNMP » en trois phases, profils SNMP (constructeur, v2c/v3, USM), et la nouvelle section « serveur de dépôt » (#74) : protocole, identifiants, dossier, protocole de récupération — `core.FileTransferConfig` transmis à `core.run_bulk_push` ; sans dépôt coché, comportement inchangé (adresse seule) | contrat « outil » |

## Ordre proposé

1. **telnet** : le plus petit plugin terminal. Il pose `Vte.Terminal` 3.91
   derrière le contrat v1, et un test sous Xvfb avec un vrai terminal.
2. **local** (#295), **ipmisol** et **serial** (#179) : portés. Avec local, la
   session VTE et la zone d'options de telnet passent dans `gcm4.ui.terminal`
   et `gcm4.core.terminal` (`docs/plugins.md`), que les plugins suivants
   réutilisent.
3. **ssh** (#97, #180) : le plus gros ; il réutilise le terminal des étapes 1
   et 2.
4. **web** (#397) : porté.
5. ~~**Contrat « outil »**~~ fait (#73) : `ToolPlugin` dans
   `gcm4.core.plugins`, sections de menu `outils`/`import`/`export`,
   validation `incompatibilite_outil()`, sous-menu « Tools » et action
   `win.outil-<name>` (`gcm4.core.menu.menu_avec_outils`,
   `docs/plugins.md`). **import_putty** est porté (#436) — il a ouvert la
   voie avec le service `import_hosts` du contexte. Les quatre imports
   d'hyperviseurs (**libvirt, Proxmox, VirtualBox, oVirt**) sont portés
   (#439) sur un onglet générique partagé (`plugins/import_hyperviseurs/`).
   **netmiko_push** est porté (#441). **snmp_push** est porté (#445), avec
   son formulaire de dépôt (#74) : le lot #179 est terminé — tous les
   plugins `plugin_*.py` encore en GTK3 ont leur équivalent outil.
6. **vnc** (fusion pluginvnc2, #104), **rdp** (#71, #105) et **spice** (choix
   de la voie) : ils dépendent de projets ou de décisions extérieurs.

Les six plugins de connexion déjà portés (telnet, local, ipmisol, serial,
web, rdp) ont vu leur fichier GTK3 retiré le 08/11/2026 (#179). Restent
`plugin_ssh.py` (après #97), `plugin_vnc.py` (fusion pluginvnc2) et
`plugin_spice.py` (choix de la voie).

Chaque portage retire son fichier de `tests/gtk3_inventaire.txt` (#178). La
case « plus aucun plugin GTK3 dans `plugins/` » de #179 est cochée quand
l'inventaire ne contient plus aucun `plugins/plugin_*.py`.
