# Architecture — état réel du code (référence détaillée)

> Document de référence, pas un mémo de démarrage : à consulter au besoin
> (nom d'un module, statut d'une fonctionnalité, écart doc/code), pas à
> relire intégralement à chaque session. Voir `../CLAUDE.md` pour l'essentiel
> à connaître avant d'intervenir, et `features-backlog.md` pour le suivi
> fait/pas fait. Contenu repris de l'ancien `claude.md`/`features.md` lors de
> la réorganisation de la documentation (2026-09-08) — non réédité depuis
> sauf mention contraire.

## 1. Vue d'ensemble des modules

| Module | Rôle | Dépendances externes |
|---|---|---|
| `gnome_connection_manager.py` | Cœur applicatif (~7500 lignes) : `Wmain`, `Whost`, `Wconfig`, `Wabout`, `Wcluster` | `PyGObject`, `GTK3`, `VTE` |
| `plugins/plugin_*.py` (`ConnectionPlugin`) | Un plugin par protocole de connexion, autoload par `PluginRegistry` | selon protocole (voir §2.1) |
| `plugins/plugin_*.py` (`BatchPlugin`) | Outils de traitement en masse (import/export/push config), autoload par `BatchPluginRegistry` | selon outil (voir §2.2) |
| `models.py` | `Host` — champs communs + champs par protocole appris dynamiquement (`all_host_fields()`) | — |
| `widgets.py` / `utils.py` | Widgets GTK custom, `run_dialog_sync()` (palliatif `Gtk.Dialog.run()`, absent en GTK4) | `PyGObject` |
| `ssh_config_editor.py`, `ssh_key_manager_dialog.py`, `key_picker_dialog.py` | Gestion SSH avancée — UI GTK3, portage de SSH-Studio (GTK4/Adw → GTK3) | `paramiko` (clés) |
| `plugins/ssh/core.py` | Logique métier SSH sans GTK (parsing `~/.ssh/config`, migration/import gcm.conf) — fusion `ssh_config_parser.py`+`ssh_migrate_gcm.py` (session-26, plugin pilote de la migration GTK4) | — |
| `netmiko_bulk_core.py`, `snmp_bulk_core.py` | Cœurs métier sans GTK des outils de déploiement en masse (`snmp_push_core.py` supprimé, #74) | `netmiko`, `ezsnmp`/`easysnmp`, `paramiko` (dépôt SFTP) |
| `hypervisor_import_common.py` | Helpers partagés libvirt/Proxmox (jamais préfixé `plugin_`) | `paramiko`, `nmap` (sonde) |
| `master_password_core.py` | Protection par mot de passe maître du fichier de clé locale (`KEY_FILE`) — logique de chiffrement/déchiffrement seule, zéro GTK (session-27) | `pyAES` |
| `tools/` | Scripts CLI autonomes : `libvirt_inventory.py` (v4), `ssh_deploy.py`, `validate_po.py`, `validate_xml_json.py` | `virsh`, `paramiko` |
| `pyAES.py` / `urlregex.py` | AES-256/OFB pur Python (mots de passe) / regex PCRE2 (URL/e-mail terminal) | — |
| `tests/test_gcm.py` | 492 fonctions de test (56 classes), `unittest` + `mock` | `pytest` |
| Dépendances vendorisées | `gtk-frdp/` (RDP embarqué natif, meson), `SSH-Studio/` (référence portage SSH), `vnc/`, `rdp/` (cibles Docker de test) | `meson`/`ninja` |

## 2. Fonctionnalités par module

Légende : ✅ terminé et câblé dans l'interface — ⚠️ terminé mais intégration UI
incertaine/partielle — 🔲 non commencé.

### 2.1 Protocoles de connexion (`plugins/plugin_*.py`, `ConnectionPlugin`)
Chaque protocole est découvert automatiquement par `PluginRegistry.autoload()`.

| Protocole | Plugin | État |
|---|---|---|
| SSH | `plugin_ssh.py` | ✅ Terminal VTE, agent, tunnels, cluster ; encapsule `SSHConfigParser`/`SSHKeyManagerDialog`/`KeyPickerDialog` |
| RDP | `plugin_rdp.py` | ✅ `GtkFrdp.Display` natif (widget `GtkDrawingArea`, aucun repli `Gtk.Socket`/XEmbed dans le code chargé — corrigé session-46, voir §5 point 8) |
| VNC | `plugin_vnc.py` | ✅ Widget natif `GtkVnc.Display` si `gtk-vnc` dispo, sinon vncviewer/vinagre/remmina |
| SPICE | `plugin_spice.py` | ✅ Widget natif `SpiceClientGtk.Display`, mode libvirt natif si `libvirt-python` dispo, sinon remote-viewer/virt-viewer |
| Série RS-232/485 | `plugin_serial.py` | ✅ picocom/minicom/screen, 11 templates constructeurs |
| Telnet | `plugin_telnet.py` | ✅ |
| Local | `plugin_local.py` | ✅ Terminal shell local |
| IPMI SOL | `plugin_ipmisol.py` | ✅ Console BMC Serial-over-LAN (iLO/iDRAC/IMM…) — non documenté dans README/CHANGELOG |
| Web | `plugin_web.py` | ✅ Visualiseur console BMC HTML5 (iLO/iDRAC/IMM ou URL quelconque) — premier plugin implémenté (preuve de concept), non documenté |

### 2.2 Outils de traitement en masse (`BatchPlugin`, menu Fichier/Outils)

| Outil | Plugin | État |
|---|---|---|
| Import libvirt/KVM | `plugin_import_libvirt.py` | ✅ Dialogue 2 phases (scan → prévisualisation), SSH ProxyJump, SPICE via tunnel libvirt natif |
| Import Proxmox | `plugin_import_proxmox.py` | ✅ `qm list`/`qm config`, ticket SPICE via `pvesh`, résolution IP 4 niveaux |
| Import VirtualBox | `plugin_import_virtualbox.py` | ✅ URI `vbox+ssh://user@host[:port]/` saisie manuelle — non documenté |
| Import oVirt | `plugin_import_ovirt.py` | ✅ Pilotage Engine oVirt (URL + API REST + user SSH) — non documenté |
| Import CSV / JSON | `plugin_import_csv.py` / `plugin_import_json.py` | ❌ **Supprimés le 31/10/2026 (#179)** : remplacés par l'import de la base GTK4 (`gcm4.core.exchange`, #152/#153) |
| Export CSV / JSON | `plugin_export_csv.py` / `plugin_export_json.py` | ❌ **Supprimés le 31/10/2026 (#179)** : remplacés par l'export de la base GTK4 (#152/#153), sans mot de passe |
| Push config masse — Netmiko | `plugin_netmiko_push.py` + `netmiko_bulk_core.py` | ✅ Session CLI SSH/équipement, profils, inventaire CSV, gabarits `#colonne#` — non documenté |
| Push config masse — SNMP | `plugin_snmp_push.py` + `snmp_bulk_core.py` | ✅ `SNMP SET` déclencheur (H3C/Cisco/**Huawei ajouté le 2026-08-30**), `ThreadPoolExecutor`. **#74 (2026-10-24)** : dépôt du fichier rendu par SFTP/FTP/TFTP (`FileTransferConfig`, `upload_config_file`) et protocole de récupération TFTP/FTP/SFTP transmis au driver, repris de `snmp_push_core.py`, supprimé ; le formulaire du plugin GTK3 ne propose pas encore ces réglages (§2.2-bis) |
| Import PuTTY (`~/.putty/sessions/`) | `plugin_import_putty.py` + `putty_import_core.py` | ✅ **2026-09-01.** Scanne un dossier de sessions PuTTY Unix (`Clé=Valeur` par fichier, noms de fichier décodés du schéma `%XX`) ; SSH/Telnet uniquement (Rlogin/Raw sans équivalent GCM, Serial hors périmètre — champs PuTTY trop différents du plugin série, voir docstring de `putty_import_core.py`), sessions ignorées listées dans le message de résumé |

> Le plugin Netmiko n'est **pas cassé** : le log « `plugin_netmiko_push` ignore : pas
> de `get_plugin()` » est normal (module `BatchPlugin`, chargé séparément par
> `BatchPluginRegistry`, voir `Wmain._warn_if_batch_plugin_failed()`).

#### 2.2-bis Constat (2026-08-30) — tranché (#74, 2026-10-12)
> **Décision de l'auteure (#74, 2026-10-12)** : compléter `snmp_bulk_core.py`
> avec les transports SFTP/FTP/TFTP, puis supprimer `snmp_push_core.py`.
> **Fait le 2026-10-24** : `FileTransferConfig`, `upload_config_file()`
> (SFTP paramiko avec clé d'hôte vérifiée dans `known_hosts`, FTP `ftplib`,
> client TFTP RFC 1350), paramètre `transfer` de `push_to_device()`/
> `run_bulk_push()` et `FETCH_PROTOCOLS` par driver ; `snmp_push_core.py`
> supprimé. Sans `transfer`, comportement inchangé (rien déposé, TFTP sur
> `server_ip`) : le formulaire de `plugin_snmp_push.py` (GTK3) n'expose pas
> encore protocole et identifiants, à faire au portage GTK4 du plugin.
> L'historique ci-dessous est conservé tel quel.

En vérifiant le câblage du push SNMP (voir §4.1, ajout du driver Huawei),
recherche textuelle complète : **`plugin_snmp_push.py` importe exclusivement
depuis `snmp_bulk_core.py`** (`from snmp_bulk_core import SNMP_VENDORS,
STATUS_*, InventoryRow, ...`). `snmp_push_core.py` — module bien plus complet
(1247 lignes, dépôt SFTP **et** FTP/TFTP, `asyncio`, sa propre hiérarchie de
drivers `SnmpPushDriver`/`Hh3cComwareDriver`/`CiscoConfigCopyDriver`) —
**n'est importé nulle part dans le dépôt**, malgré une description antérieure
de ce document (ligne SNMP de §2.2) qui lui correspondait en réalité. Deux
implémentations parallèles de la même fonctionnalité coexistent donc :
`snmp_bulk_core.py` (effectivement câblée, synchrone, dépôt TFTP/FTP
seulement) et `snmp_push_core.py` (orpheline, plus riche). Même traitement
que `ssh_config_editor.py` (§2.5) : ne pas décider seul(e) lequel des deux
garder/caller/supprimer — à trancher avec l'auteure, l'un ou l'autre pouvant
être un travail en cours plutôt qu'un abandon.
- **Précision de l'auteure (2026-08-30) :** `ezsnmp` est la bibliothèque
  destinée à remplacer `easysnmp` pour le **futur plugin SNMP en GTK4**. Ceci
  confirme que la logique « `ezsnmp` en priorité, repli sur `easysnmp` »
  déjà présente dans `snmp_bulk_core.py` (et portée à l'identique dans
  `HuaweiVrpDriver`, §4.1) va dans le bon sens pour la transition. Indice
  supplémentaire pour trancher entre les deux modules candidats : en
  vérifiant les imports, **`snmp_push_core.py` n'a aucun repli sur
  `easysnmp`** — il n'utilise qu'`ezsnmp` (import paresseux
  `_require_ezsnmp()`, aucune mention d'`easysnmp` en dehors des
  commentaires explicatifs), alors que `snmp_bulk_core.py` conserve le repli
  `easysnmp` pour compatibilité. `snmp_push_core.py` est donc le plus
  probablement le candidat pensé pour ce futur plugin GTK4 tout-`ezsnmp` —
  hypothèse à confirmer avec l'auteure, pas une décision prise ici.

### 2.3 Import d'infrastructure virtualisée — outils CLI (`tools/`)
- ✅ `tools/libvirt_inventory.py` (v4) : inventaire complet (IP, OS, vCPUs, RAM,
  ports SPICE/VNC/RDP), exports JSON/CSV/Ansible INI/YAML.
- ✅ `tools/ssh_deploy.py` : clés RSA-4096 + Ed25519, déploiement `ssh-copy-id`.
- ✅ `tools/validate_po.py`, `tools/validate_xml_json.py` : utilisés par `make validate`.
- ✅ `hypervisor_import_common.py` : bibliothèque partagée (SSH par clé, lecture
  URIs dconf, scan nmap, sonde de port) — volontairement non préfixée `plugin_`.

### 2.4 Architecture à plugins — refactor `Host` générique
`gcm_plugin_architecture.html` documentait ce découpage comme une simple
proposition ; il est **entièrement mis en œuvre** :
- ✅ `models.py` : plus aucun champ spécifique à un protocole codé en dur dans
  `Host` — appris dynamiquement via `_protocol_field_defaults()` /
  `wMain.plugin_registry.all_host_fields()`.
- ✅ `Wmain` instancie `PluginRegistry`/`BatchPluginRegistry`, `autoload()` puis
  `bind_app()` (référence directe à l'app, plus d'import circulaire).
- ✅ Menu contextuel, items par protocole et section « Outils » peuplés
  dynamiquement à partir des plugins enregistrés.

### 2.5 Gestion SSH avancée (portage de SSH-Studio, GTK4/Adw → GTK3, GPLv3)
- ✅ `plugins/ssh/core.py` (logique pure, zéro GTK — session-26, fusion des
  anciens `ssh_config_parser.py`+`ssh_migrate_gcm.py`, premier plugin
  réorganisé en dossier `plugins/<nom>/` pour la migration GTK4, voir
  `docs/gtk4-migration.md` §3.0-bis/§3.6). `plugins/ssh/gtk4.py` (widgets
  réels) n'existe pas encore — session séparée, tributaire du portage du
  cœur GTK4.
- ✅ `ssh_key_manager_dialog.py` (clés orphelines, known_hosts,
  authorized_keys, CA de signature), `key_picker_dialog.py` — câblés via
  `plugin_ssh.py::manage_ssh_keys()`, inchangés par la fusion (restent
  GTK3, à la racine, pas encore réorganisés).
- ✅ `plugins.ssh.core.migrate_ssh_hosts()` : migration `gcm.conf` →
  `~/.ssh/config` (backup horodaté, bloc sentinelle, notice badge rouge) —
  câblé (`patch_edit_host_dialog`).
- ✅ `ssh_config_editor.py` (éditeur visuel + onglet Raw avec diff) : **câblage
  confirmé (session-34, 2026-09-14)**, l'affirmation précédente de cette
  ligne (« aucun appel retrouvé ») était fausse — recherche textuelle plus
  poussée que celle d'origine (qui s'était arrêtée au nom de fichier seul) :
  `SshPlugin.menu_actions()` (`plugins/plugin_ssh.py`) déclare l'action
  `"edit-ssh-config"` vers `SshPlugin.edit_ssh_config()`, qui importe
  `SshConfigEditorDialog` et l'ouvre en onglet épinglé
  (`Wmain.open_management_tab()`) ; `Wmain._build_primary_menu()`
  (`gnome_connection_manager.py`) itère `plugin.menu_actions()` pour chaque
  plugin enregistré et ajoute l'entrée résultante (« Edit ~/.ssh/config… »)
  à la section `edit_section3` du menu « Edit » du hamburger. Nouveau verrou
  textuel `tests/test_ssh_config_editor_wiring.py`, détail
  `docs/sessions/session-34.md`. Importe désormais `plugins.ssh.core`
  (session-26) au lieu des anciens `ssh_config_parser.py`/
  `ssh_migrate_gcm.py`, mais reste lui-même un fichier GTK3 à la racine du
  dépôt, pas encore déplacé dans `plugins/ssh/`.

### 2.6 Sécurité / configuration
- ✅ Chiffrement AES des mots de passe (`pyAES.py`, AES-256/OFB pur Python).
- ⚠️ **Master password (session-27, 2026-09-10)** : `master_password_core.py`
  fournit la logique de protection/déverrouillage du contenu de `KEY_FILE`
  par un mot de passe maître (PBKDF2-HMAC-SHA256, enveloppe via `pyAES`,
  vérificateur d'intégrité, rétrocompatibilité totale avec un `KEY_FILE`
  legacy non protégé) — **testée et fonctionnelle en isolation, pas encore
  câblée** dans `gcm4_core.load_encryption_key()`/`initialise_encyption_key()`
  ni dans le flux de démarrage GTK (prompt du mot de passe, gestion de
  l'oubli). Voir `docs/sessions/session-27.md` pour le détail du périmètre
  et des points laissés à la validation de l'auteure.
- ✅ Détection automatique du thème GTK du bureau (`gsettings` + fallback
  `settings.ini`, mode sombre système).
- ✅ Détection d'URLs/e-mails dans le terminal (`urlregex.py`, PCRE2).
- ✅ **2026-09-01.** Masquage des mots de passe dans l'historique des
  commandes cluster (`Wcluster`, zone de commandes envoyée à tous les hôtes
  actifs) : marqueur `#P=<valeur>` saisi dans la commande — envoyé en clair
  aux terminaux (`gcm4_core.resolve_cluster_command()`), mais remplacé par
  des astérisques dans l'historique rappelable au CTRL+UP/CTRL+DOWN
  (`gcm4_core.mask_cluster_command()`), pour qu'un mot de passe tapé une
  fois ne reste jamais en clair en mémoire au-delà de l'envoi. Convention
  introduite cette session (aucune trace antérieure du marqueur dans le
  dépôt ni dans une doc historique) ; compromis assumé et documenté dans
  la docstring : un marqueur rappelé depuis l'historique n'est plus
  réutilisable tel quel, l'utilisateur doit resaisir la valeur. Logique
  pure dans `gcm4_core.py` (zéro GTK, testée dans
  `tests/test_gcm4_core.py::TestResolveClusterCommand`/`TestMaskClusterCommand`,
  10 tests), câblage mince dans `Wcluster.send_cluster_commands()` ;
  info-bulle ajoutée sur la zone de saisie pour la découvrabilité.
- ⚠️ **Migration de config pour le renommage gcm4 (session-30, 2026-09-10)** :
  `gcm4_core.migrate_legacy_config_dir(old_dir, new_dir)` — copie générique
  (jamais destructrice, idempotente, permissions préservées) d'un dossier
  de config vers un nouveau, sans nom en dur — **testée et fonctionnelle en
  isolation (8 tests), pas encore câblée** : ni `old_dir`/`new_dir`
  définitifs, ni l'appel au démarrage de `gnome_connection_manager.py`.
  Voir `docs/gcm4-rename.md` pour l'inventaire complet du renommage dont ce
  mécanisme est un préalable, et le paragraphe `widgets.py` ci-dessous pour
  la duplication de `CONFIG_DIR`/`CONFIG_FILE`/`KEY_FILE` à traiter au même
  moment.

### 2.7 Internationalisation
✅ **29 langues** ont un fichier `.po` dans `lang/`, **toutes les 29 compilées en
`.mo`** (`bn_BD` compilé le 2026-08-30, 251/253 chaînes traduites) : ar, bn, cs, da, de, el, en, fa, fi, fr, he, hi, hu, id, it, ja,
ko, nb, nl, pl, pt, ro, ru, sv, th, tr, uk, vi, zh. README/CHANGELOG n'en annoncent
que **16** — 13 langues existent sans être documentées (bn, da, el, fa, fi, he, hi,
hu, id, ro, th, vi, zh).

### 2.8 Tests et qualité
- ✅ **780 tests** au total (**506 fonctions de test / 58 classes** dans
  `tests/test_gcm.py`, comptage statique au 2026-09-16, inchangé depuis ;
  **274** répartis sur les 19 autres fichiers `tests/test_*.py` — chiffre
  vérifié en session-45 (2026-09-18), contre 271 comptés en session-44
  (2026-09-18) : `tests/test_ssh_gtk4_baseline.py` (3 tests/18 sous-tests,
  audit de `plugins/ssh/gtk4.py`) ajouté cette session-là. Avant, les
  sessions 38 à 44 avaient ajouté (`test_gtk4_context_menus_baseline.py`,
  `test_gcm4_rename_inventory.py`, `test_ssh_config_editor_wiring.py`,
  `test_gtk4_core_rupture_points.py`, `test_gesture_trigger_core.py`,
  `test_gtk4_inline_editor_baseline.py`, `test_inline_editor_core.py`)
  sans que cette section ne soit mise à jour entre-temps — même type de
  dérive de documentation que celle déjà corrigée ici en session-36 pour
  `test_gcm.py`, sans dépendance GTK, collectés et exécutés avec succès
  dans cet environnement de travail).
  Chiffre affiné cette session : la précédente entrée de cette section
  (2026-08-30, « 492 fonctions de test », 56 classes) a dérivé depuis —
  README/CHANGELOG citaient encore « 491 tests », corrigés en
  conséquence. `test_gcm.py` reste non collectable ici faute de GTK3/VTE
  réels (voir plus bas) ; les 506 n'ont donc pas été **exécutés** en
  conditions réelles cette session, seulement comptés statiquement — à
  confirmer via `make test` chez l'auteure.
- ✅ `ruff` (docstrings Google, durcissement D1xx/D417 sur objets publics) +
  `flake8` + `black` + `pre-commit`. **`ruff check .` passe désormais sans
  aucune erreur sur tout le dépôt (2026-09-16)** — voir plus bas, le point
  « `make lint` échoue » ci-dessous est corrigé et laissé en place à titre
  d'historique.
- ✅ Packaging `.deb`/`.rpm`/openSUSE via `fpm` (`make deb/rpm/opensuse`).
- ✅ **Gestion des dépendances Python migrée de Poetry vers uv (2026-09-16)** :
  `pyproject.toml` (`[tool.uv] package = false` remplace `[tool.poetry]
  package-mode = false`, dépendances dev déplacées de
  `[tool.poetry.group.dev.dependencies]` vers `[dependency-groups] dev`),
  `poetry.lock`/`poetry.toml` supprimés, `uv.lock` généré et vérifié
  (`uv sync` + `uv run pytest` passent). `bootstrap-dev.sh` réécrit en
  conséquence (`uv venv --system-site-packages` + `uv sync --extra ...
  --group dev`) — les deux contournements documentés pour Poetry depuis
  l'origine du script (détection d'interpréteur derrière un shim pyenv,
  erreur DBus/secretstorage du backend keyring) n'ont pas d'équivalent
  connu avec uv et ont été retirés plutôt que reformulés à l'aveugle ; à
  re-signaler si un cas reproductible apparaît chez l'auteure. Détail :
  `docs/sessions/session-36.md`.
- ✅ **Tranché (#87)** : l'autrice a décidé le 2026-10-12 de supprimer les
  tests orphelins. Les tests `_rdp_socket_available` sont retirés de
  `tests/test_gcm.py` et les tests `_vm_name_split` visent désormais
  `hypervisor_import_common.vm_name_split()` (commit 3e485e9, #116) ; plus
  aucune classe de `tests/test_gcm.py` n'appelle de fonction absente.
  Historique du constat initial : (même traitement que
  `ssh_config_editor.py` en §2.5, pas de correction à l'aveugle) : plusieurs
  classes de `tests/test_gcm.py` (`TestVmNameSplit...`/`TestRdpSocketAvailable...`
  et leurs variantes « supplémentaires ») appellent `gcm._vm_name_split(...)`
  et `gcm._rdp_socket_available(...)`. Recherche textuelle complète sur tout
  le dépôt : ces deux noms n'existent nulle part dans
  `gnome_connection_manager.py`, sans alias d'import. La fonction
  équivalente réellement présente est `hypervisor_import_common.vm_name_split()`
  (sans underscore, autre module) ; aucune trace de `_rdp_socket_available`
  ailleurs. Sens du renommage/déplacement non déterminé sans contexte —
  possible refactor où les tests n'ont pas suivi. Ces tests échoueraient
  donc à l'exécution (non vérifié en conditions réelles ici faute de
  GTK3/VTE installés dans cet environnement de travail ; à confirmer via
  `make test` chez l'auteure). **Reconfirmé sans changement le 2026-09-16**
  (recherche répétée sur tout le dépôt après la migration uv et la
  correction ruff de cette session : toujours aucune trace des deux
  fonctions hors `tests/test_gcm.py`). Second symptôme du même ordre repéré au
  passage : un bloc `if __name__ == "__main__":` orphelin (ligne ~1801),
  juste avant la bannière « BLOC 3 — Tests supplémentaires (triplement de
  la suite) », signe d'une concaténation de générations de tests jamais
  entièrement réconciliée — sans conséquence sous `pytest` (découverte de
  classes, ignore les gardes `__main__`), mais `python3 tests/test_gcm.py`
  exécuté directement s'arrêterait avant les blocs suivants.
- ⚠️ **Constat complémentaire (2026-08-30), à confirmer avec l'auteure avant
  correction** — même logique que les deux points ci-dessus : le stub GTK/VTE de
  `tests/test_gcm.py` (fonctions `_make_gi_stub`/`_make_gtk_stub`, en tête de
  fichier) ne suit plus exactement les besoins réels du module dans cet
  environnement de travail (pas de GTK3/VTE installés) : `gi.repository.GObject`
  stubbé n'expose pas `SignalFlags` (nécessaire à `key_picker_dialog.py`) et
  `Vte` n'est pas résolu par le chemin d'import dynamique emprunté par
  `gnome_connection_manager.py` (message d'erreur applicatif « dépendances GTK3
  / VTE manquantes » levé dès l'import du module, avant même la collecte des
  tests). Non vérifié en conditions réelles chez l'auteure (où GTK3/VTE sont
  installés, donc potentiellement sans impact réel) ; les nouveaux tests ajoutés
  cette session (`TestResolveConfigDir`, voir §4.1) suivent scrupuleusement le
  même patron que `TestComputeZoomSize` et ont été vérifiés isolément (fonction
  copiée hors module, sans stub) plutôt que via `pytest tests/test_gcm.py`,
  faute de pouvoir lever ce blocage sans toucher au stub à l'aveugle.
- ⚠️ **`ruff check .` passe désormais sans aucune erreur (2026-09-16)**,
  mais **`make lint` dans son ensemble (`ruff check .` + `flake8
  gnome_connection_manager.py`) ne passe toujours pas** — second écart
  distinct découvert cette session sur `flake8` : jamais configuré pour ce
  dépôt (ni `.flake8` ni `setup.cfg`), il tournait avec sa limite par
  défaut de 79 caractères au lieu des 99 déclarés partout ailleurs
  (`pyproject.toml` : `ruff`/`black` `line-length = 99`), remontant 435
  faux positifs `E501` rien que sur `gnome_connection_manager.py`.
  `.flake8` ajouté (`max-line-length = 99`), ramenant le compte à 182 (97
  lignes réellement >99 caractères + 85 constats de code légué déjà connus
  et volontairement ignorés par `ruff` pour ce fichier —
  `E711`/`E721`/`E722`/`F841`, cf. `[tool.ruff.lint] ignore` — que
  `flake8`, non aligné sur la même liste, continue de signaler). Non
  corrigés cette session : hors périmètre de la demande (« correction des
  erreurs *ruff* »), et un correctif à l'aveugle sur 182 lignes d'un
  fichier de 7600 lignes risquerait des effets de bord non vérifiables
  sans GTK3/VTE réel dans cet environnement de travail — à traiter en
  session dédiée. Côté `ruff` proprement dit : anciennement (2026-08-30,
  affiné 2026-09-14) `ruff check gnome_connection_manager.py tests/`
  remontait 423 erreurs, toutes des docstrings manquantes sur des
  méthodes/classes de test dans `tests/test_gcm.py` (415 `D102` + 8
  `D101`). Corrigé cette session par génération mécanique d'une docstring
  d'une ligne à partir du nom de chaque test/classe sans docstring (ex.
  `test_with_negative_diff` → `"""Test with negative diff."""`,
  `TestColorToHex` conservait déjà la sienne, non touchée) — script
  ponctuel (`ast`, non conservé dans le dépôt), puis `ruff format
  tests/test_gcm.py` pour rester cohérent avec le reste du fichier
  massivement modifié. Choix délibéré de la génération mécanique plutôt
  que de docstrings rédigées une à une (666 méthodes au total dans ce
  fichier) : honnête (aucun contenu inventé, reflet littéral du nom
  existant) mais mécanique — une relecture humaine reste utile sur les cas
  où le nom du test est lui-même peu clair. `ruff check .` : **0 erreur**
  sur tout le dépôt. `ruff format --check .` (mise en forme, distinct du
  lint) : 26 fichiers non conformes restants (27 avant cette session,
  `tests/test_gcm.py` reformaté au passage) — non traités, hors périmètre
  de cette session (`CONSIGNES-AGENTS-IA.md` §6, ne pas reformater un
  fichier hérité non par ailleurs modifié). `Makefile` (`lint:`) inchangé
  (`ruff check .` couvrait déjà tout le dépôt depuis la session-35).
- ✅ **Nouveaux outils de qualité (2026-08-30)** : `CONSIGNES-AGENTS-IA.md`
  (règles strictes pour tout agent IA travaillant sur les plugins — logging
  Loguru, docstrings Google, tests, ruff, imports circulaires) +
  `.pre-commit-config.yaml` (ruff lint/format, hygiène de base, détecteur
  d'imports circulaires local) + `tools/check_circular_imports.py` (analyse
  uniquement les imports exécutés au chargement du module — ignore
  délibérément les imports différés en corps de fonction et les blocs
  `if TYPE_CHECKING:`, pour ne pas remonter de faux positifs sur des
  patterns déjà utilisés intentionnellement dans ce dépôt). Les trois
  validés réellement (dépôt git temporaire, `pre-commit run --all-files`,
  détection positive et négative de cycle testée) — pas seulement rédigés.
  Répertoires vendorisés (`SSH-Studio/`, `gtk-frdp/`) et environnements de
  test (`rdp/`, `vnc/`) exclus de tous les hooks depuis l'origine ;
  `pyproject.toml` (`[tool.ruff] exclude`, utilisé par `ruff check .`/`make
  lint` hors pre-commit) ne l'était pas et a été aligné en session-35
  (2026-09-14, voir plus haut) — les deux configurations excluent désormais
  les mêmes répertoires.
- ⚠️ **`widgets.py` duplique indépendamment `CONFIG_DIR`/`CONFIG_FILE`/
  `KEY_FILE` et son propre logger Loguru (2026-08-30, trouvé en écrivant
  `gcm4_core.py`)** : `widgets.py` (ligne ~49) recalcule son propre
  `USERHOME_DIR`/`CONFIG_DIR`/`CONFIG_FILE`/`KEY_FILE` en dur
  (`USERHOME_DIR + "/.gcm"`) au lieu d'utiliser `gcm4_core._resolve_config_dir()`,
  et définit sa propre `_setup_app_logger()` (ligne ~62, avec repli stdlib
  si Loguru absent) indépendante de celle de `gcm4_core.py`. Conséquences
  concrètes : l'option CLI `--config` (#80) est invisible pour tout code
  passant par `widgets.CONFIG_FILE` (ex. `plugin_spice.py`, pairage
  d'hôtes SPICE, qui lit `Path(CONFIG_FILE)` importé depuis `widgets.py`,
  pas depuis le cœur) ; et deux configurations Loguru distinctes coexistent
  selon l'ordre d'import (laquelle des deux `.add()`/`.remove()` s'exécute
  en dernier l'emporte). Non corrigé (risque de double init du logger non
  vérifiable sans GTK/VTE réel dans cet environnement de travail) — à
  confirmer avec l'auteure avant d'unifier les deux (`widgets.py`
  importerait `CONFIG_DIR`/`CONFIG_FILE`/`KEY_FILE`/`setup_app_logger`
  depuis `gcm4_core.py` au lieu de les recalculer). À traiter en même temps
  que l'exécution réelle du renommage GCM → gcm4 (`docs/gcm4-rename.md` §5)
  pour ne pas laisser les deux définitions diverger sur le nouveau nom.

### 2.8-bis Vestiges de protocole dans le cœur (constat 2026-08-30)

**2026-09-09 (session-23)** : le vestige `patch_edit_host_dialog` (import
direct par le cœur, depuis `ssh_migrate_gcm.py`, dans `Whost.init`) a été
corrigé — logique GTK rapatriée dans `SshPlugin.patch_edit_host_dialog`,
exposée via un nouveau hook générique `ConnectionPlugin.patch_edit_host_dialog`
(no-op par défaut, cf. `plugin_base.py`). Le cœur itère désormais
`plugin_registry.all()` sans nommer aucun protocole sur ce chemin. Il ne
figurait pas dans la liste ci-dessous (trouvé indépendamment) ; les trois
vestiges suivants, eux, restent ouverts.

En travaillant sur `gcm4_core.py` (§4.1), plusieurs traces de protocole
trouvées dans `gnome_connection_manager.py` qui ne devraient plus y être
puisque ces protocoles sont déjà des plugins — non corrigées ici, portée
plus large qu'une extraction (touchent potentiellement le contrat
`ConnectionPlugin` de `plugin_base.py`) :

- `TEL_BIN = "telnet"` (ligne ~168) : constante nommant le binaire telnet,
  au niveau module du cœur.
- `vte_protocols = {"ssh", "telnet", "local", "serial", "ipmi"}` et
  `is_vte = proto in ("ssh", "telnet", "local", "serial", "ipmi")` (deux
  occurrences) : le cœur énumère en dur quels protocoles utilisent un
  terminal VTE plutôt qu'un autre type de widget (RDP/VNC/SPICE). Ce
  savoir devrait venir d'un attribut déclaratif du plugin lui-même (ex.
  `ConnectionPlugin.uses_vte_terminal: bool`), pas d'une liste en dur côté
  cœur — sans quoi ajouter un futur plugin terminal (ou en retirer un)
  oblige à modifier le cœur, contrairement à l'objectif de découverte
  automatique sans connaissance de protocole.

### 2.9 Bugs historiques upstream — tous corrigés
Issues kuthulux/gnome-connection-manager, traitées en étape « 1b » du fork (voir
§4 pour le suivi complet) :

| Issue | Symptôme | Correction |
|---|---|---|
| [#81](https://github.com/kuthulux/gnome-connection-manager/issues/81) | Crash démarrage, `style.css` manquant | `try/except` non-bloquant sur `load_from_path` |
| [#82](https://github.com/kuthulux/gnome-connection-manager/issues/82) | Clone impossible avec mot de passe | `sendPassword()` réordonné dans `addTab()` |
| [#88](https://github.com/kuthulux/gnome-connection-manager/issues/88) | Logging cassé Ubuntu 24.04 / VTE 2.91+ | Migré vers le signal `output-written` |
| [#89](https://github.com/kuthulux/gnome-connection-manager/issues/89) | Passphrase SSH redemandée à chaque onglet | `SSH_AUTH_SOCK` injecté dans l'env VTE |
| [#87](https://github.com/kuthulux/gnome-connection-manager/issues/87) | Freeze SSH MikroTik (`window-change` flood) | Debounce 200 ms sur `on_terminal_size_allocate` |
| [#64](https://github.com/kuthulux/gnome-connection-manager/issues/64) | Double-clic ouvre un onglet dans Midnight Commander | Vérification `posY < tab_bar_height` |
| [#67](https://github.com/kuthulux/gnome-connection-manager/issues/67) | Surlignage jaune cluster persiste | `queue_draw()` forcé sur tous les onglets |
| [#66](https://github.com/kuthulux/gnome-connection-manager/issues/66) | Port tunnel SSH perdu à la sauvegarde | Sérialisation `host`/`port` séparée |

Deux bugs additionnels observés dans d'anciens journaux de développement
(`ImportError: wMain`, `AttributeError` sur « Éditer » sans sélection) sont
également **déjà corrigés** dans le code actuel.

### 2.10 Champs du dialogue d'hôte (gcm4, #144)

Inventaire de `Whost` (`gnome_connection_manager.py`, l. ~5448 à 6700,
`models.Host._CORE_FIELDS`) pour le dialogue GTK4 (`gcm4.ui.host_dialog`,
#145). **Commun** : attribut de `gcm4.core.model.Host`, édité par le dialogue
et validé par `gcm4.core.validation.validate_host`. **Protocole** : clé de
`host.protocol_options[protocole]`, éditée par la zone du plugin (#146) et
conservée telle quelle sans plugin. La correspondance avec les clés INI est
`gcm4.core.config.CHAMPS_HOTE` : toute clé absente de ce tableau y est une
option de protocole.

| Widget `Whost` | Champ GTK3 | Clé INI | Classement | Dialogue gcm4 |
|---|---|---|---|---|
| `txtName` | `name` | `name` | commun | nom (obligatoire, sans `/`, unique dans le groupe) |
| `cmbGroup` (avec saisie) | `group` | `group` | commun | groupe : liste des groupes et saisie libre (racine permise) |
| `txtHost` | `host` | `host` | commun | adresse (obligatoire sauf protocole sans adresse) |
| `txtPort` | `port` | `port` | commun | port : vide (défaut du protocole, #146) ou 1 à 65535 |
| `txtUser` | `user` | `user` | commun | utilisateur |
| `txtPass` / `txtPassword` | `password` | `pass` (chiffré) | commun | `Gtk.PasswordEntry`, jamais journalisé |
| `txtDescription` | `description` | `description` | commun | description |
| `btnFColor`, `btnBColor`, `chkDefaultColors` | `font_color`, `back_color` | `font-color`, `back-color` | commun | `Gtk.ColorDialogButton` ×2 et interrupteur « couleurs par défaut » |
| `cmbProtocol` | `protocol` | `protocol` | commun | `Gtk.DropDown` : protocoles du registre (#124), plus le protocole actuel s'il est inconnu |
| `chkWorkspace` | `workspace` | `workspace` | commun | interrupteur « espace de travail » |
| `cmbAutoCloseTab` | `auto_close_tab` | `auto-close-tab` | commun | hérité / jamais / toujours / sortie propre |
| `txtPreConnectCommand` | `pre_connect_command` | `pre-connect-command` | commun | stocké, exécuté par les plugins seulement |
| `txtPostConnectCommand` | `post_connect_command` | `post-connect-command` | commun | stocké, exécuté par les plugins seulement |
| — (page du plugin) | `extra_params` | `extra_params` | commun | paramètres supplémentaires (texte libre transmis au client) |
| `cmbType` | `type` | `type` | protocole | remplacé par `protocol` ; conservé tel quel |
| `txtPrivateKey`, `btnBrowse`, `btnPickKey`, `btnKeyManager` | `private_key` | `private_key` | protocole | zone du plugin (SSH) |
| `chkCommands`, `txtCommands` | `commands` | `commands` | protocole | zone du plugin (terminal) |
| `chkLogging` | `log` | `log` | protocole | zone du plugin (terminal) |
| `cmbBackspace`, `cmbDelete` | `backspace_key`, `delete_key` | `backspace_key`, `delete_key` | protocole | zone du plugin (terminal VTE) |
| `txtTerm` | `term` | `term` | protocole | zone du plugin (terminal) |
| page tunnels | `tunnel` | `tunnel` | protocole | zone du plugin (SSH) |
| options SSH | `keep_alive`, `x11`, `agent`, `compression`, `compressionLevel` | idem | protocole | zone du plugin (SSH) |
| pages `nbHost` des plugins (`save_host_fields`) | champs de `_protocol_field_defaults()` | idem | protocole | zone du plugin du protocole |

### 2.11 Préférences : inventaire de `Wconfig` (gcm4, #147)

Chaque option du dialogue GTK3 `Wconfig` est classée dans
`gcm4.core.preferences.INVENTAIRE` ; `tests/test_gcm4_preferences.py` relit
`Wconfig` et échoue si une option n'y figure pas. Les pages Général et
Apparence (`gcm4.ui.preferences`) appliquent chaque changement tout de suite
et l'écrivent aussitôt (`update_keys`, écriture atomique). Nouvelle clé gcm4 :
`[window] tree-font` (description Pango, vide = police du système).

Restauration des onglets (#246, `gcm4.core.onglets`) : à la fermeture, les
chemins `groupe/nom` des onglets de connexion sont écrits dans l'ordre
affiché dans `[window] open-tabs` (format GTK3), même option désactivée.
Au démarrage, si `[options] restore-open-tabs` est actif, chaque chemin est
rouvert par `win.connect-host` ; un hôte disparu ou un protocole sans
plugin donne un seul message par cas. Limite du format : un chemin
contenant une virgule n'est pas enregistré.

Vérification des mises à jour (#245, `gcm4.core.mises_a_jour`,
`gcm4.ui.mises_a_jour`) : jamais d'accès réseau au démarrage sans accord.
`[options] check-updates` (GTK3) dit si l'on vérifie ; `[window]
check-updates-consent` garde l'accord explicite (absent : une question,
une seule fois ; « Non » désactive l'option ; activer l'option dans les
préférences vaut accord). `GCM_NO_UPDATE_CHECK=1` désactive tout (paquets
de distribution, tests). Source : API GitHub `releases/latest` du dépôt
(brouillons et pré-versions exclus), délai 5 s, proxy de l'environnement ;
comparaison numérique des versions (GTK3 comparait des chaînes). Requête
dans un fil d'exécution, résultat par `GLib.idle_add` ; hors ligne : une
ligne de journal, rien à l'écran.

Options de base des onglets et de la fenêtre (#247,
`gcm4.core.comportement_onglets`) : `[options] confirm-close-tab` et
`confirm-close-tab-middle` (défaut vrai, comme GTK3) demandent « Close
console » avant de fermer un onglet par son bouton ou par un clic du milieu
sur l'onglet ; la fermeture de la fenêtre garde sa propre confirmation.
`cycle-tabs` fait boucler `win.next-tab` / `win.previous-tab` (menu
Affichage, Ctrl+Tab / Ctrl+Maj+Tab comme GTK3, et Ctrl+Page suivante /
précédente) et la molette sur les onglets (vers le haut : précédent ; pas sur
le contenu des pages). `disable-hosts-stripes` retire l'alternance de fond
de l'arbre ; `update-title` ajoute le titre de l'onglet courant au titre de
la fenêtre (« titre - onglet ») ; `app-title` remplace le nom de
l'application (vide, ou égal au nom que GTK3 y écrit par défaut : nom de
l'application). Ces options sont appliquées tout de suite.

Raccourcis modifiables (#247, `gcm4.core.raccourcis`, `gcm4.ui.raccourcis`) :
les six raccourcis de la fenêtre (`gcm4.core.menu.RACCOURCIS`) se changent
dans la page Raccourcis. Un clic sur la ligne ouvre « Set Shortcut » : la
combinaison pressée est lue en phase de capture (accélérateurs de
l'application suspendus pendant la saisie, Ctrl+Q n'y quitte pas) ; Échap
annule, Entrée valide, Retour arrière désactive. Une combinaison déjà prise
par une autre action ou par l'arbre des hôtes est refusée, ainsi qu'une
touche ordinaire sans Ctrl, Alt ni Super (F1–F35 et touches multimédia
acceptées seules). Stockage : `[shortcuts]`, format de GTK3
(`CTRL+SHIFT+TAB`, nom GDK 3 en majuscules) ; `console_previous` et
`console_next` sont partagées avec GTK3, et leur valeur par défaut GTK3 vaut
« par défaut » (les deux raccourcis de gcm4) ; les autres actions ont des
clés `gcm4_…` que GTK3 ignore. Valeur vide : désactivé ; clé absente
(« Reset to default ») : défauts ; valeur illisible : défauts et
avertissement. Les autres clés de la section (terminal, commandes
personnalisées) ne sont ni lues ni modifiées, et `save_config` reprend la
section du fichier telle quelle. Les raccourcis de l'arbre restent fixes
(posés par l'arbre, pas par l'application).

Lot 5b (#148) : pages Raccourcis (lecture seule à l'origine, depuis
`gcm4.core.preferences.raccourcis()`, même source que les raccourcis posés
par l'application et l'arbre), Journalisation et Extensions. Nouvelle clé
`[options] log-level` (`TRACE`…`ERROR`, défaut `INFO`) ; priorité : `--debug`,
puis `GCM_LOG_LEVEL` / `GCM_DEBUG`, puis la préférence. `save_config` reprend
désormais du fichier les clés de `[options]` sans champ `AppConfig`, comme
celles de `[window]` : une préférence écrite en cours de session n'est pas
effacée par un enregistrement du modèle.

| Option `Wconfig` | Clé INI | Classement | Destination dans gcm4 |
|---|---|---|---|
| `WORD_SEPARATORS` | `options/word-separators` | terminal | plugin terminal |
| `BUFFER_LINES` | `options/buffer-lines` | terminal | plugin terminal |
| `TRANSPARENCY` | `options/transparency` | terminal | plugin terminal |
| `TERM` | `options/term` | terminal | plugin terminal |
| `LOG_PATH` | `options/log-path` | terminal | plugin terminal (journal des sessions) |
| `CONTINUOUS_TAB_LOG` | `options/continuous-tab-log` | terminal | plugin terminal |
| `STARTUP_LOCAL` | `options/startup-local` | protocole | plugin local |
| `LOG_LOCAL` | `options/log-local` | protocole | plugin local |
| `PASTE_ON_RIGHT_CLICK` | `options/paste-right-click` | terminal | plugin terminal |
| `AUTO_COPY_SELECTION` | `options/auto-copy-selection` | terminal | plugin terminal |
| `CONFIRM_ON_CLOSE_TAB` | `options/confirm-close-tab` | base | page Général (#247) |
| `CONFIRM_ON_CLOSE_TAB_MIDDLE` | `options/confirm-close-tab-middle` | base | page Général (#247) |
| `CYCLE_TABS` | `options/cycle-tabs` | base | page Général (#247) |
| `DISABLE_SHORTCUTS` | `options/disable-shortcuts` | terminal | plugin terminal |
| `DISABLE_NOTIFICATIONS` | `options/disable-notifications` | terminal | plugin terminal |
| `RESTORE_OPEN_TABS` | `options/restore-open-tabs` | base | page Général (#147), #246 |
| `AUTO_CLOSE_TAB` | `options/auto-close-tab` | terminal | plugin terminal |
| `CONFIRM_ON_EXIT` | `options/confirm-exit` | base | page Général (#147) |
| `CHECK_UPDATES` | `options/check-updates` | base | page Général (#147), #245 |
| `DISABLE_HOSTS_STRIPES` | `options/disable-hosts-stripes` | base | page Apparence (#247) |
| `UPDATE_TITLE` | `options/update-title` | base | page Apparence (#247) |
| `APP_TITLE` | `options/app-title` | base | page Apparence (#247) |
| `FONT_COLOR` | `options/font-color` | terminal | plugin terminal |
| `BACK_COLOR` | `options/back-color` | terminal | plugin terminal |
| `FONT` | `options/font` | terminal | plugin terminal (police VTE) |
| `THEME_MODE` | `window/theme-mode` | base | page Apparence (#147) |
| `DARK_MODE` | `window/dark-mode` | base | écrite avec theme-mode (#135) |
| `shortcuts` | `shortcuts/*` | base | page Raccourcis (#148) ; fenêtre modifiable (#247) ; terminal : plugin terminal |
| `custom commands` | `shortcuts/*` | terminal | plugin terminal |
| `build_preferences_tab` | — | protocole | plugin.build_preferences_page (#148) |
| `SerialTemplatesTab` | — | protocole | plugin série |

### 2.12 Instance unique et deuxième lancement (gcm4, #275)

`Gcm4Application` est à instance unique (`DRAPEAUX` :
`HANDLES_COMMAND_LINE | SEND_ENVIRONMENT`). `gcm4.ui.application.run`
transmet les arguments bruts à `Gio.Application.run` : si gcm4 tourne déjà,
le deuxième processus envoie sa ligne de commande, son dossier courant et
son environnement à l'instance en cours, qui l'analyse avec
`gcm4.core.cli.parse_args(..., home, environ, cwd)` (`-c` relatif et
`$XDG_CONFIG_HOME` pris dans le contexte du deuxième lancement) :

| Deuxième lancement | Effet dans l'instance en cours | Code de sortie |
|---|---|---|
| `gcm4 --debug` (action « debug mode » du lanceur) | niveau DEBUG à chaud (`logging_config.enable_debug`), `args.debug` vrai pour Préférences > Journalisation | 0 |
| `gcm4 groupe/nom…` | fenêtre présentée, hôtes sélectionnés (`MainWindow.ouvrir_hotes`, #154) | 0 |
| autre dossier de configuration (`-c` ou `$XDG_CONFIG_HOME`) | rien ; message sur la sortie d'erreur du deuxième lancement | 5 (`CODE_AUTRE_CONFIG`) |
| ligne refusée par l'analyseur (appel D-Bus forgé seulement) | rien | 2 (`CODE_REFUS`) |

Le message du refus est écrit par le deuxième processus :
`Gio.ApplicationCommandLine.printerr` n'est pas accessible par
introspection et `printerr_literal` demande GLib 2.80. Tests :
`tests/gtk4/test_deuxieme_lancement.py` (ligne simulée, puis vrais
processus sur un `dbus-daemon` privé).

## 3. Arborescence détaillée (fichiers, classes, dépendances)

```
gcm4_core.py                  ~1085 lignes — cœur MÉTIER, zéro import gi/Gtk/Vte,
                                extrait de gnome_connection_manager.py le 2026-08-30
                                (préalable au découpage des plugins en dossiers,
                                voir décisions en tête de ce fichier). Neutre de
                                protocole par construction (§2.8-bis de features.md
                                pour les vestiges encore ailleurs). Importé par
                                gnome_connection_manager.py ET models.py.
                                2026-09-01 : resolve_cluster_command()/
                                mask_cluster_command() (marqueur #P= du mode
                                cluster, voir plus bas) y ajoutés.
                                2026-09-02 : resolve_auto_close_tab() y ajoutée
                                (fermeture auto d'onglet #77, surcharge par host,
                                voir Whost/NotebookTabLabel ci-dessous).
                                2026-09-07 : resolve_connection_hook_command() y
                                ajoutée (scripting #116, volet "avant" seulement
                                — voir gnome_connection_manager.run_pre_connect_hook()
                                et Host.pre_connect_command dans models.py).
                                2026-09-07 (plus tard) : resolve_theme_mode() y
                                ajoutée (mode sombre dédié #113, validation
                                centralisée du THEME_MODE persisté — voir
                                loadConfig()/Wconfig ci-dessous).
                                2026-09-10 (session-30) : migrate_legacy_config_dir()
                                y ajoutée (mécanisme générique de migration de
                                dossier de config, préparatoire au renommage
                                GCM→gcm4 — voir docs/gcm4-rename.md, pas encore
                                câblée).

gnome_connection_manager.py   ~7610 lignes — cœur GTK (le "gtk4.py" central à
                                terme, portage GTK4 pas encore commencé)
├── Wmain (GCMBase, Gtk.Window)
│   ├── self.plugin_registry       PluginRegistry (protocoles, get_plugin())
│   ├── self.batch_plugin_registry BatchPluginRegistry (outils, get_batch_plugin())
│   └── global wMain = self        alias historique, assigné à l'instanciation ;
│                                   utilisé par les plugins au lieu d'importer Wmain
├── Whost (GCMBase, Gtk.Dialog)     formulaire d'édition d'hôte (coquille commune ;
│                                   chaque plugin fournit sa page via build_edit_page() ;
│                                   cmbAutoCloseTab pour la surcharge par host de
│                                   conf.AUTO_CLOSE_TAB, visible seulement en VTE)
├── Wconfig / Wabout / Wcluster        (Wcluster.send_cluster_commands() : marqueur
│                                   #P=<valeur> masqué à l'historique depuis le
│                                   2026-09-01, voir gcm4_core.py ci-dessus)
├── SerialTemplatesTab              vestige de protocole assumé, voir features.md §2.8-bis
└── CheckUpdates (Thread)

plugins/                         découverts par autoload() (cf. plugin_base.py)
├── plugin_base.py                ConnectionPlugin (ABC), PluginRegistry,
│                                  BatchPlugin (ABC), BatchPluginRegistry.
│                                  2026-09-10 (session-25) : verrou
│                                  gi.require_version("Gtk", "3.0")/import Gtk
│                                  réel levé — seul usage de Gtk restant est en
│                                  annotation de type, sous `if TYPE_CHECKING:`.
│                                  S'importe désormais sans gi du tout (vérifié
│                                  dans un environnement sans PyGObject
│                                  installé). Chaque plugin reste libre de sa
│                                  propre version GTK — voir gtk4-migration.md §3.6.
├── plugin_ssh.py                  → SSHConfigParser/SSHKeyManagerDialog/
│                                    KeyPickerDialog ; manage_ssh_keys()
├── plugin_vnc.py / plugin_spice.py / plugin_rdp.py / plugin_serial.py
├── plugin_telnet.py / plugin_local.py
├── plugin_ipmisol.py              console BMC SOL (pas de page d'édition dédiée,
│                                   utilise seulement les champs communs de Whost)
├── plugin_web.py                  visualiseur console BMC HTML5 / URL quelconque
│                                   (1er plugin implémenté, preuve de concept)
├── plugin_import_libvirt.py / plugin_import_proxmox.py
├── plugin_import_virtualbox.py    URI vbox+ssh:// saisie manuelle
├── plugin_import_ovirt.py         Engine oVirt (URL + API REST + user SSH)
├── plugin_import_putty.py         ✅ 2026-09-01 — sessions PuTTY Unix
│                                   (~/.putty/sessions/), SSH/Telnet
│                                   uniquement ; scan/parsing dans
│                                   putty_import_core.py (racine, zéro GTK)
│                                   (import/export CSV/JSON supprimés le
│                                   31/10/2026, #179 : remplacés par la base
│                                   GTK4 — gcm4.core.exchange)
├── plugin_netmiko_push.py         BatchPlugin — get_batch_plugin() (PAS get_plugin(),
│                                   c'est normal : PluginRegistry l'ignore à raison,
│                                   BatchPluginRegistry le charge séparément)
└── plugin_snmp_push.py            BatchPlugin — idem, asyncio + ezsnmp

models.py         Host — _CORE_FIELDS communs + champs par protocole appris
                  dynamiquement via _protocol_field_defaults() →
                  wMain.plugin_registry.all_host_fields() (PAS de champs codés en
                  dur par protocole : le refactor annoncé dans
                  gcm_plugin_architecture.html est terminé)
widgets.py        widgets GTK custom (dont WebTab, la classe la plus simple, 104
                  lignes, utilisée comme base de plugin_web.py)
utils.py          run_dialog_sync() — palliatif documenté pour Gtk.Dialog.run()
                  (supprimé en GTK4) ; classe de base GCMBase
pyAES.py          AES-256/OFB pur Python (mots de passe)
urlregex.py       regex PCRE2 (URL/e-mail dans le terminal)

hypervisor_import_common.py   helpers partagés libvirt/Proxmox — jamais préfixé
                               plugin_ (pour ne pas être scanné par les autoload())

ssh_config_editor.py / ssh_key_manager_dialog.py / key_picker_dialog.py
    UI GTK3, portage de SSH-Studio (BuddySirJava/SSH-Studio, GPLv3, GTK4/Adw → GTK3).
    ssh_key_manager_dialog.py est câblé via plugin_ssh.py::manage_ssh_keys().
    ssh_config_editor.py : AUCUN appel retrouvé ailleurs dans le dépôt — intégration
    UI non confirmée, à vérifier (cf. features-backlog.md).
    Importent plugins.ssh.core (ci-dessous) au lieu des anciens
    ssh_config_parser.py/ssh_migrate_gcm.py depuis la session-26 ; restent
    eux-mêmes GTK3 à la racine du dépôt (gtk4.py pas encore écrit).

plugins/ssh/core.py
    Logique métier SSH sans GTK (session-26, plugin pilote de la migration
    GTK4, docs/gtk4-migration.md §3.0-bis/§3.6). Fusion des anciens
    ssh_config_parser.py + ssh_migrate_gcm.py : parsing ~/.ssh/config,
    migration/import gcm.conf. plugins/ssh/__init__.py existe (marqueur de
    package) mais n'expose pas encore get_plugin() — plugins/ssh/gtk4.py
    (widgets réels) reste à écrire, session séparée.

netmiko_bulk_core.py / snmp_bulk_core.py
    cœurs métier sans GTK des outils de déploiement en masse.
    #74 (2026-10-24) : snmp_bulk_core.py dépose aussi le fichier rendu
    (SFTP/FTP/TFTP, FileTransferConfig) ; snmp_push_core.py, décrit
    ci-dessous, est supprimé.
    plugin_netmiko_push.py -> netmiko_bulk_core.py.
    plugin_snmp_push.py -> snmp_bulk_core.py (drivers H3C/Cisco/Huawei, ce
    dernier ajouté le 2026-08-30, voir décisions en tête de ce fichier ;
    ezsnmp en priorité avec repli easysnmp pour compatibilité).
    snmp_push_core.py (SFTP+FTP/TFTP, asyncio, sa propre hiérarchie de
    drivers, ezsnmp UNIQUEMENT — aucun repli easysnmp) N'EST IMPORTÉ NULLE
    PART — deux implémentations parallèles de la même fonctionnalité
    coexistent dans le dépôt. Précision de l'auteure (2026-08-30) : ezsnmp
    remplace easysnmp pour le futur plugin SNMP en GTK4 — ce qui fait de
    snmp_push_core.py (tout-ezsnmp) le candidat le plus probable pour ce
    futur plugin, mais à confirmer avec l'auteure avant de trancher (même
    logique que ssh_config_editor.py ci-dessus, cf. features.md §2.2-bis).
    snmp_bulk_core_easysnmp.py (ancienne variante EasySNMP, non importée
    non plus) a été supprimée le 2026-08-30 après portage de son unique
    capacité non dupliquée (driver Huawei) dans snmp_bulk_core.py.

tools/            scripts CLI autonomes : libvirt_inventory.py (v4), ssh_deploy.py,
                  validate_po.py, validate_xml_json.py (ces deux derniers utilisés
                  par `make validate`), check_circular_imports.py (2026-08-30, hook
                  pre-commit local — voir CONSIGNES-AGENTS-IA.md)

CONSIGNES-AGENTS-IA.md   nouveau (2026-08-30) — règles strictes pour tout agent IA
                  travaillant sur les plugins (logging Loguru, docstrings Google,
                  tests, ruff, imports circulaires). À lire avant de commencer le
                  découpage en dossiers par plugin (SSH en premier).

.pre-commit-config.yaml   nouveau (2026-08-30) — ruff (lint+format), hygiène de
                  base, détecteur d'imports circulaires. Validé réellement (dépôt
                  git temporaire, pas seulement rédigé). Répertoires vendorisés
                  (SSH-Studio/, gtk-frdp/) et environnements de test (rdp/, vnc/)
                  exclus de tous les hooks — ne pas les en retirer.

tests/test_gcm.py   492 fonctions de test (56 classes), unittest + mock — cohérent
                     avec les « 491 tests » annoncés en doc. ⚠️ Des classes
                     référencent gcm._vm_name_split/_rdp_socket_available, deux
                     noms absents de gnome_connection_manager.py — à confirmer
                     avant correction, voir features.md §2.8.
tests/test_snmp_bulk_core.py   nouveau (2026-08-30), 4 tests — couvre snmp_bulk_core.py
                     (jamais testé jusqu'ici) : verrouille l'invariant « tout
                     vendor de SNMP_VENDORS a un driver dans DRIVERS » (régression
                     du bug Huawei corrigé cette session). Stub minimal d'ezsnmp
                     (l'extension C réelle n'est pas installable dans cet
                     environnement de travail) ; module sans dépendance GTK, donc
                     pas besoin des stubs GTK/VTE de test_gcm.py.
tests/test_gcm4_core.py   nouveau (2026-08-30), 93 tests (26 d'origine +
                     ajouts de sessions ultérieures, dont 8 pour
                     migrate_legacy_config_dir() en session-30), tous exécutés
                     réellement et verts — couvre gcm4_core.py (zéro GTK, aucun
                     stub requis). Priorité au comportement nouveau/changé
                     (exceptions au lieu de msgbox, plugin_registry en
                     paramètre) et aux 3 régressions de transcription trouvées
                     en écrivant le fichier (color_to_hex, dep_install_hint) —
                     verrouillées pour ne pas revenir. Ne couvre PAS les
                     templates série (restés dans gnome_connection_manager.py,
                     voir features.md §2.8-bis) : couverts par
                     tests/test_gcm.py::TestSerialTemplatesSaveLoad comme avant.
tests/test_gcm4_rename_inventory.py   nouveau (session-30, 2026-09-10), 7
                     tests — pas un test de comportement runtime : verrou
                     textuel sur l'état actuel avant le renommage GCM → gcm4
                     (12 sites d'import réel du module principal, occurrences
                     du domaine i18n, contenu du .desktop, non-régression sur
                     le fix Makefile/.glade) — voir docs/gcm4-rename.md.

Dépendances vendorisées en local (code source seulement, pas d'historique git ni
de build compilé après ménage) :
  gtk-frdp/    sous-projet meson GNOME, fournit le typelib GtkFrdp pour le RDP
               embarqué natif (voir README § Installation pour la procédure de
               build : meson setup / ninja / ninja install)
  SSH-Studio/  code source de référence ayant servi de base au portage GTK3
  vnc/, rdp/   environnements Docker de test (cibles VNC/RDP factices)
```

## 4. Conventions du projet

- `ruff` (docstrings Google, règles E/W/F/I/D/UP/B/C4/SIM, durcissement récent sur
  D1xx/D417 — docstrings Args/Returns/Raises obligatoires sur objets publics) +
  `flake8` + `black` (line-length 120) + `pre-commit`.
- Logging : `loguru` en priorité, fallback `logging` stdlib
  (`try: from loguru import logger except ImportError:`), répété dans plusieurs
  fichiers — respecter ce pattern dans tout nouveau module.
- Les modules « cœur métier sans GTK » (`netmiko_bulk_core.py`, `snmp_*.py`,
  `hypervisor_import_common.py`) restent volontairement découplés de GTK et des
  plugins pour rester testables hors environnement graphique — ne pas y introduire
  d'import GTK/plugin au niveau module.
- Deux familles de plugins bien distinctes, à ne pas confondre :
  - `ConnectionPlugin` → fonction de niveau module `get_plugin()`, chargé par
    `PluginRegistry.autoload()`.
  - `BatchPlugin` → fonction de niveau module `get_batch_plugin()`, chargé par
    `BatchPluginRegistry.autoload()`.
  Un module qui n'expose que l'une des deux fonctions est **normalement** ignoré
  (pas en échec) par l'autre registre — ne pas interpréter ce log comme un bug.
- `hypervisor_import_common.py` ne doit jamais être renommé `plugin_*.py` (sinon
  scanné à tort par les deux `autoload()`).
- Build/qualité : voir `Makefile` (`make test`, `make lint`, `make validate`,
  `make check`, `make deb/rpm/opensuse`, `make translate`).

## 5. Écarts doc/code à garder en tête

1. README/CHANGELOG/Documentation-fr ne mentionnent ni l'architecture à plugins, ni
   IPMI SOL, ni Web, ni VirtualBox/oVirt, ni Netmiko/SNMP push.
2. README annonce 16 langues ; 29 existent (13 non documentées : bn, da, el, fa, fi,
   he, hi, hu, id, ro, th, vi, zh). Les 29 ont désormais leur `.mo` compilé
   (`bn_BD` corrigé le 2026-08-30, voir « Livré cette session » ci-dessous).
3. L'ancienne `fork/ROADMAP-GCM-v1.3.md` contenait des sections dupliquées et de
   nombreux items marqués « 🔲 post v1.3 » déjà réalisés depuis (architecture
   plugins #107, gestion clés SSH #108, import/export CSV/JSON #74) — fusionnée et
   dédoublonnée dans `features.md` §4, le fichier d'origine n'existe plus.
4. Deux bugs documentés dans d'anciens journaux de développement (`ImportError:
   wMain`, crash sur « Éditer » sans sélection) sont **déjà corrigés** dans le code
   actuel — ne pas les re-corriger par erreur en pensant reproduire un rapport de
   bug obsolète.
5. `tests/test_gcm.py` contient des classes de test référençant
   `gcm._vm_name_split`/`gcm._rdp_socket_available`, deux noms introuvables dans
   `gnome_connection_manager.py` (probable renommage/déplacement jamais répercuté
   côté tests) — à confirmer avec l'auteure avant correction, même logique que
   `ssh_config_editor.py` ci-dessus. Détail dans `features.md` §2.8.
6. Le stub GTK/VTE de `tests/test_gcm.py` ne suffit plus à importer
   `gnome_connection_manager.py` dans un environnement sans GTK3/VTE installés
   (`GObject.SignalFlags` manquant, `Vte` non résolu) — potentiellement sans
   impact chez l'auteure si GTK3/VTE y sont installés ; à confirmer avant de
   toucher au stub. Détail dans `features.md` §2.8.
7. Deux implémentations parallèles du push SNMP coexistent dans le dépôt :
   `snmp_bulk_core.py` (effectivement câblée par `plugin_snmp_push.py`) et
   `snmp_push_core.py` (plus complète — SFTP+FTP/TFTP, asyncio — mais
   **non importée nulle part**). La documentation précédente de ce dépôt
   décrivait par erreur la fonctionnalité du second comme si elle appartenait
   au premier. **Tranché (#74)** : transports portés dans
   `snmp_bulk_core.py`, `snmp_push_core.py` supprimé — §2.2-bis.
8. Le tableau du §2.1 ci-dessus décrivait RDP avec un repli `Gtk.Socket`/
   XEmbed encore actif à côté de `GtkFrdp.Display` — inexact : ce repli
   (ancienne classe `RdpEmbeddedTab`, sous-processus `xfreerdp` avec
   `/parent-window:<XID>`) a disparu de `gnome_connection_manager.py` et
   de `plugins/plugin_rdp.py` (probablement lors du passage à
   l'architecture à plugins), corrigé dans le tableau. Ceci explique
   l'orphelin `_rdp_socket_available` du point 5 ci-dessus : c'était le
   détecteur X11 de ce repli disparu — son détail complet (inspection de
   la bibliothèque vendorisée `gtk-frdp/`, statut GTK4 en amont non
   confirmé, et un menu contextuel RDP/VNC/SPICE jamais porté malgré le
   résumé session-33 de `CLAUDE.md`) est dans `docs/gtk4-migration.md`
   §3.8 (audit session-46, 2026-09-19).
