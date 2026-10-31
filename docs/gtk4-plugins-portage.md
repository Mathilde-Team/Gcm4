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
| telnet | 216 | `Vte.Terminal` 2.91, commande `telnet` | `Vte.Terminal` 3.91 ; même schéma que ssh, en plus court | Rocky 9 sans VTE GTK4. **Premier portage conseillé** : le plus petit plugin terminal, il permet de valider le contrat v1 avec un vrai terminal |
| local | 192 | `Vte.Terminal` 2.91, shell local | `Vte.Terminal` 3.91 | Rocky 9 sans VTE GTK4 |
| serial | 689 | `Vte.Terminal` 2.91, `picocom`/`minicom`/`screen` | **porté** : `plugins/serial/` (contrat v1, #179) ; détection des outils et construction de la commande sans GTK (`core.py`), paramètres série dans la zone d'options du protocole (repli du débit sur le champ Port, héritage GTK3) | Rocky 9 sans VTE GTK4 ; `picocom` vient d'EPEL sur Rocky 9 |
| ipmisol | 246 | `Vte.Terminal` 2.91, `ipmitool sol activate` | **porté** : `plugins/ipmisol/` (contrat v1, #179) ; mot de passe via `IPMI_PASSWORD` (`Lancement.env`), `sol deactivate` à la fermeture | Rocky 9 sans VTE GTK4 |
| web | 298 | `WebKit2.WebView` 4.1 ou navigateur externe (`Gio.AppInfo`) | `WebKit.WebView` 6.0 ; repli vers le navigateur externe (`Gtk.UriLauncher` à partir de GTK 4.10, sinon `Gio.AppInfo`) | Rocky 9 : repli externe seulement |
| vnc | 701 | `GtkVnc.Display` (gtk-vnc, GTK3 seulement partout) | **fusion avec `plugins/pluginvnc2`** (déjà GTK4, client asyncvnc2), comme prévu par #104 | #104 ; aucune version GTK4 de gtk-vnc dans les distributions |
| spice | 1424 | `SpiceClientGtk.Display` 3.0 (GTK3 seulement partout) | aucun widget SPICE GTK4. Deux voies : affichage maison sur `SpiceClientGLib` (canal d'affichage vers `Gdk.MemoryTexture`, comme pluginvnc2), ou visionneuse externe (`remote-viewer`) | **choix à faire** (sous-issue au moment du portage) ; spice-gtk absent de Rocky 9 |
| rdp | 614 | `GtkFrdp.Display` (`Gtk.Socket`/XEmbed, supprimé en GTK4) | `plugins/rdp/gtk4.py` sur AsyncRdp | #71, #105 ; FreeRDP 3 absent de Debian 12 et de Rocky 9 |

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
| import_putty | 117 | `BatchPlugin`, sessions PuTTY | type « outil », dialogue GTK4 de choix de fichier | contrat « outil » |
| import_libvirt | 1174 | `BatchPlugin`, `Gtk.TreeView` | type « outil » ; liste en `Gtk.ColumnView` ; la logique libvirt passe dans `core.py` | contrat « outil » |
| import_ovirt | 1045 | idem | idem (API REST, JSON) | contrat « outil » |
| import_proxmox | 1029 | idem | idem | contrat « outil » |
| import_virtualbox | 1039 | idem, `VBoxManage` | idem | contrat « outil » |
| netmiko_push | 932 | `BatchPlugin`, `Gtk.TreeView`, `netmiko_bulk_core.py` | type « outil » ; le cœur existe déjà sans GTK | contrat « outil » |
| snmp_push | 1146 | idem, `snmp_bulk_core.py` | type « outil » ; le cœur existe sans GTK, dépôt SFTP/FTP/TFTP compris (#74) | contrat « outil » ; formulaire de dépôt (protocole, identifiants, dossier) à ajouter |

## Ordre proposé

1. **telnet** : le plus petit plugin terminal. Il pose `Vte.Terminal` 3.91
   derrière le contrat v1, et un test sous Xvfb avec un vrai terminal.
2. **local** (#295), **ipmisol** et **serial** (#179) : portés. Avec local, la
   session VTE et la zone d'options de telnet passent dans `gcm4.ui.terminal`
   et `gcm4.core.terminal` (`docs/plugins.md`), que les plugins suivants
   réutilisent.
3. **ssh** (#97, #180) : le plus gros ; il réutilise le terminal des étapes 1
   et 2.
4. **web**.
5. **Contrat « outil »**, puis **import_putty**, les imports d'hyperviseurs,
   **netmiko_push**, et **snmp_push** après #74.
6. **vnc** (fusion pluginvnc2, #104), **rdp** (#71, #105) et **spice** (choix
   de la voie) : ils dépendent de projets ou de décisions extérieurs.

Chaque portage retire son fichier de `tests/gtk3_inventaire.txt` (#178). La
case « plus aucun plugin GTK3 dans `plugins/` » de #179 est cochée quand
l'inventaire ne contient plus aucun `plugins/plugin_*.py`.
