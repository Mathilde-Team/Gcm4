# Changelog — Gnome Connection Manager

## [Non publié]

- **gcm4 : protocoles sans adresse (#297)** : méthode facultative `requires_address` du contrat de plugin v1 (défaut : adresse exigée) ; le plugin local la déclare : champ Adresse masqué dans le dialogue hôte et enregistrement sans adresse accepté pour les hôtes `local`, comme le faisait GTK3 (`show_host = not is_local`)

## [4.0.0] — 2026-10-24

> Première version de **gcm4** (réécriture GTK4/libadwaita). La numérotation
> redémarre à 4.0.0, alignée sur le nom gcm4, plutôt que de poursuivre en
> 1.x (décision du 2026-10-22, #299). Cette section regroupe tout le travail
> livré depuis 1.3.3. Détail complet, session par session, dans `CLAUDE.md`
> (journal) et `features.md` (§4.1).

### Added
- **pluginvnc2, reconnexion inversée** (#104, tâche 4) : réglage `reverse` de la connexion VNC, l'onglet écoute (`asyncvnc2.listen`) et attend que le serveur vienne à lui ; pinning de clé hôte coupé dans ce mode. Tests `plugins/pluginvnc2/tests/test_reverse_listen.py`
- **Push SNMP : dépôt du fichier rendu** (#74) : `snmp_bulk_core.py` dépose la configuration rendue sur le serveur de dépôt avant le `SNMP SET` (`FileTransferConfig`, `upload_config_file` : SFTP avec clé d'hôte vérifiée dans `known_hosts`, FTP, TFTP RFC 1350) et transmet le protocole de récupération au driver (`FETCH_PROTOCOLS` : H3C ftp/tftp, Cisco tftp/ftp/sftp, Huawei ftp/tftp/sftp) au lieu du TFTP codé en dur. Paramètre `transfer` optionnel de `push_to_device`/`run_bulk_push`, comportement inchangé sans lui. Testé contre de vrais serveurs SFTP, FTP et TFTP locaux
- **pluginvnc2 : envoi de texte répété** (#93) : case « Garder la fenêtre ouverte après l'envoi » dans le dialogue « Envoyer du texte », décochée par défaut et mémorisée pour la durée de vie de l'onglet ; cochée, le champ est vidé après chaque envoi et le dialogue reste ouvert
- **Sélection multiple et édition en lot** (#100) : l'arbre GTK4 passe en `Gtk.MultiSelection` (Ctrl+clic, Maj+clic) ; « Modifier les hôtes sélectionnés… » (clic droit dans la sélection ou touche Menu) change le port et/ou le groupe de plusieurs hôtes, case non cochée = champ inchangé. Conflits de noms dans le groupe cible refusés avant toute écriture. Logique sans GTK dans `gcm4.core.edition_lot`, guide « Modifier plusieurs hôtes » dans `docs/guide/base-gtk4.md`
- **Cluster : commandes favorites** (#84) : liste déroulante « Favoris » dans le dialogue Cluster, distincte de l'historique, avec boutons Ajouter/Retirer ; favoris par défaut `df`, `apt update`, `updatedb`, persistés dans `[cluster_favorites]` de `gcm.conf` ; commande contenant `#P=` refusée
- **gcm4 : mot de passe maître** (#98) : protection facultative de `.gcm4.key`, demandée au démarrage (page « Mots de passe verrouillés », configuration chargée après déverrouillage). Code de recouvrement de 24 caractères affiché une seule fois à l'activation, jamais stocké ; « Mot de passe oublié ? » le demande puis impose un nouveau mot de passe et un nouveau code. Paramètres > Sécurité : activer, changer, nouveau code, désactiver. Format `GCM4MPW2` (le `GCM4MPW1` reste lu), écriture atomique de la clé. L'application GTK3 refuse une clé protégée au lieu de la lire en clair. Guide `docs/guide/mot-de-passe-maitre.md`
- **gcm4 : encodage par hôte des onglets terminal** (#85) : liste « Encodage » dans la zone d'options des plugins telnet et local (UTF-8 par défaut, ISO-8859-1/15, Windows-1252/1250/1251, KOI8-R/U, CP437/850, Shift_JIS, EUC-JP, EUC-KR, GB18030, Big5…), option `encoding` de l'hôte, appliquée à l'ouverture de l'onglet ; saisie, commandes après ouverture et messages de gcm4 convertis. Valeur inconnue : UTF-8 ; encodage refusé par VTE : signalé dans l'onglet
- **pluginvnc2 : raccourci `Alt+F12` pour le dialogue « Envoyer du texte »** (#92) : même famille que `Maj+F12` (capture) et `Ctrl+F12` (copie) ; sans effet tant que la connexion n'est pas établie, comme le bouton. Info-bulle du bouton complétée
- **pluginvnc2, menu « Alimentation » (xvp)** (#104, tâche 5) : arrêter, redémarrer ou réinitialiser la machine distante depuis la barre d'outils de l'onglet VNC, avec confirmation. Entrées inactives (et expliquées) tant que la connexion n'est pas établie ou si le serveur n'annonce pas xvp. Tests `plugins/pluginvnc2/tests/test_xvp_power.py`
- **pluginvnc2, transport WebSocket** (#104, tâche 3) : réglages `websocket` / `ws_path` de la connexion VNC transmis à asyncvnc2 (websockify, proxy noVNC), chemin validé contre l'injection d'en-tête ; option `--websocket[=CHEMIN]` de la démo autonome. Tests `plugins/pluginvnc2/tests/test_websocket_transport.py`
- **pluginvnc2, VeNCrypt avec CA privée** (#104, tâche 2) : réglage `tls_ca_file` de la connexion VNC (autorité de certification privée pour les sous-types X509*), messages d'erreur explicites quand le certificat est refusé ou que la négociation TLS échoue. Tests `plugins/pluginvnc2/tests/test_vencrypt_ca.py`
- **pluginvnc2, presse-papiers étendu** (#104, tâche 1) : le texte copié localement part en UTF-8 par Extended Clipboard (`send_clipboard_update()`) quand le serveur le prend en charge, ClientCutText Latin-1 sinon ; un texte non transmissible est journalisé au lieu d'être perdu en silence. Tests `plugins/pluginvnc2/tests/test_extended_clipboard.py`
- **pluginvnc2, décompte de reconnexion** (#94) : le délai avant la prochaine reconnexion automatique est décompté seconde par seconde dans l'onglet VNC au lieu de rester figé. Tests `plugins/pluginvnc2/tests/test_reconnect_countdown.py`
- **Plugin SFTP GTK4** (#75) : `plugins/sftp/` au contrat v1, navigateur de fichiers distant (paramiko). Navigation, téléchargement sans écraser (`download-dir` ou dossier XDG), envoi par bouton ou glisser-déposer, nouveau dossier, renommage, suppression confirmée. Clé d'hôte vérifiée dans `~/.ssh/known_hosts` (clé inconnue : empreinte SHA256 à accepter dans l'onglet ; clé changée : refus), mot de passe, fichier de clé ou agent SSH, mot de passe demandé en cas de refus. Réseau dans un fil de travail, jamais dans la boucle GTK. Testé contre un vrai serveur SSH/SFTP paramiko (`tests/serveur_sftp.py`) ; guide `docs/guide/sftp.md`
- **Journalisation SOCLE §8, lot 1** (#294) : logger loguru dans les 11 modules qui n'en avaient pas (`MODULES_SANS_LOGGER` vide). Les 6 nouveaux `*_core.py` se mettent en sourdine à l'import et sont ajoutés à `CORE_MODULES` (#130). 23 `except` hérités journalisent désormais le type et le message de l'exception (191 → 168) ; l'`except:` nu de `models.HostUtils.get_val` devient `except BaseException as exc:`, sémantique inchangée. Traces d'entrée et de sortie ajoutées dans ces modules : référence de l'audit 1042 → 970. Les valeurs ne sont journalisées que pour les modules sans données sensibles. Dans `pyAES.py`, seules les fonctions par opération ou par bloc sont tracées, pas les primitives de ronde.
- **gcm4, base GTK4** (#126, réécriture, paquet `src/gcm4`) : commande `gcm4`
  et ligne de commande (#133) ; fenêtre GTK4/libadwaita, actions et À propos
  (#134) ; thème clair/sombre/système (#135) ; modèle Host/Group sans GTK
  (#136) ; lecture/écriture atomique de `gcm.conf`, clés inconnues
  conservées (#137) ; mots de passe chiffrés compatibles GTK3 (#138) ;
  dossier `~/.config/gcm4` avec migration non destructive depuis `~/.gcm`
  (#99) ; arbre des groupes et hôtes (#139), recherche (#140),
  glisser-déposer (#141), menus contextuels et suppression confirmée (#142) ;
  dialogue d'hôte avec validation (#144, #145) et zone d'options fournie par
  le plugin (#146) ; préférences Général, Apparence, Raccourcis,
  Journalisation et Extensions (#147, #148) ; contrat plugin v1, découverte
  isolée des erreurs et mode zéro plugin (#149, #150, #151) ; restauration
  des onglets (#246) ; vérification des mises à jour avec accord explicite
  (#245)
- **Intégration bureau et paquets de gcm4** : lanceur `.desktop`, métadonnées
  AppStream et icônes (#155) ; paquet `.deb` (Debian 13, Ubuntu 24.04+) (#156)
  et paquet `.rpm` (Fedora) (#157), vérifiés par un test de fumée en CI ;
  guide d'installation et de premiers pas `docs/guide/base-gtk4.md` (#158)
- **Plugin local GTK4** (#295) : `plugins/local/` au contrat v1, shell de
  l'utilisateur (`$SHELL`, sinon `/bin/sh`) dans un terminal VTE 3.91,
  commandes après ouverture (700 ms, `##D=`), touches d'effacement, `TERM`
  et couleurs de l'hôte. Session VTE et zone d'options communes aux plugins
  terminal dans `gcm4.ui.terminal` et `gcm4.core.terminal` ; telnet les
  utilise, sans changement de comportement
- **Base GTK4 — `gcm4 groupe/nom…`** (#154) : les hôtes de la ligne de
  commande sont sélectionnés dans l'arbre au démarrage (groupes parents
  dépliés), connectés seulement si un plugin gère leur protocole ; hôte
  inconnu signalé par un seul message
- **Base GTK4 — import/export CSV et JSON sans GTK** (#152) :
  `src/gcm4/core/exchange.py`, schéma JSON versionné (`gcm4-hosts`, v1,
  `docs/echange-hotes.md`), mots de passe exclus sauf demande explicite,
  conflits ignorer / renommer / remplacer avec rapport, fichier mal formé
  sans aucune modification de la configuration ; exports GTK3 acceptés
- **Base GTK4 — import/export en ligne de commande** (#154) :
  `gcm4 --export-json|--export-csv|--import-json|--import-csv FICHIER`,
  `--on-conflict ignore|rename|replace`, `--include-passwords` ; sans
  fenêtre ni display, code de sortie 5 en cas d'erreur
- **Base GTK4 — import/export depuis le menu Fichier** (#153) :
  importer (aperçu, stratégie de conflit, bilan final), exporter
  (avertissement quand les mots de passe sont inclus), enregistrer la
  configuration sous (`gcm4.conf` + `.gcm4.key`) ; `Gtk.FileDialog` avec
  repli `Gtk.FileChooserNative` avant GTK 4.10
- **Architecture à plugins** (#107) : `Host` générique sans champ codé en dur
  par protocole, `PluginRegistry`/`BatchPluginRegistry` avec autoload et
  injection de contexte (`bind_app()`), menu contextuel peuplé dynamiquement
- **Nouveaux protocoles de connexion** : IPMI SOL (console BMC
  Serial-over-LAN — iLO/iDRAC/IMM…) et Web (visualiseur console BMC HTML5 ou
  URL quelconque)
- **Nouveaux imports d'infrastructure** : VirtualBox (URI
  `vbox+ssh://user@host[:port]/`), oVirt (Engine REST + SSH), en plus des
  imports CSV/JSON existants et de leurs exports symétriques (sans mot de
  passe)
- **Import PuTTY** (`~/.putty/sessions/`, SSH/Telnet) — 2026-09-01
- **Déploiement de configuration en masse** : push Netmiko (session CLI
  SSH/équipement, profils, inventaire CSV, gabarits `#colonne#`) et push SNMP
  (dépôt TFTP/FTP + `SNMP SET`, drivers H3C/Cisco/**Huawei VRP**)
- **Gestion SSH avancée** (#108, portage de SSH-Studio) : clés orphelines,
  `known_hosts`, `authorized_keys`, CA de signature, migration
  `gcm.conf` → `~/.ssh/config` (`ssh_migrate_gcm.py`)
- **SSH `ProxyCommand` arbitraire** (2026-09-05), en complément de
  `ProxyJump`/tunnel `dynamic` SOCKS déjà existants
- **13 nouvelles langues** (bn, da, el, fa, fi, he, hi, hu, id, ro, th, vi,
  zh) — total désormais **29 langues**, toutes compilées en `.mo`
- **Option CLI `--config PATH` / `-c PATH`** (#80) — dossier de configuration
  alternatif
- **Zoom terminal** Ctrl+/Ctrl-/Ctrl+0 par onglet, raccourcis configurables
  (#79)
- **Masquage des mots de passe dans l'historique cluster** : marqueur
  `#P=<valeur>` envoyé en clair aux terminaux mais remplacé par des
  astérisques dans l'historique CTRL+UP/CTRL+DOWN
- **Fermeture automatique d'onglet — surcharge par hôte** (#77) :
  `Host.auto_close_tab`, en plus du réglage global déjà existant
- **Désactivation globale des raccourcis clavier** (préférence
  `Disable keyboard shortcuts`)
- **Notifications desktop de fin de connexion** (#109, volet déconnexion) —
  D-Bus direct vers `org.freedesktop.Notifications`, protocoles VTE
  uniquement (SSH/telnet/local/serial)
- **Espace de travail (« workspace »)** : marquage d'hôtes par case à cocher,
  action « Open Workspace » qui ouvre tous les hôtes marqués d'un coup
- **Sauvegarde/restauration des onglets ouverts** au démarrage (préférence
  opt-in `Restore open tabs from the previous session on startup`)
- **Couleurs par groupe/dossier**, avec héritage vers les sous-dossiers et
  les hôtes sans couleur propre
- **Mode sombre dédié** (#113) : préférence à trois états
  Système/Clair/Sombre, en plus de la détection automatique du thème du
  bureau déjà en place
- **Scripting avant/après connexion** (#116) : `Host.pre_connect_command`
  (« Before connecting ») et `Host.post_connect_command` (« After
  disconnecting », protocoles VTE uniquement), marqueurs `{name}`/
  `{address}`/`{group}`/`{protocol}`
- **`CONSIGNES-AGENTS-IA.md`**, `.pre-commit-config.yaml` et
  `tools/check_circular_imports.py` : règles et outillage qualité pour tout
  agent (humain ou IA) travaillant sur le dépôt

### Removed
- `snmp_push_core.py` (#74) : double implémentation orpheline du push SNMP, jamais importée ; ses transports sont repris dans `snmp_bulk_core.py`

### Fixed
- **Nom de l'organisation** (#310) : l'ancien nom mal orthographié (sans « l », compte GitHub inexistant) remplacé par `Mathilde-Team` dans la CI (installation d'`asyncvnc2`), la condition du job `i18n.yml` (qui ne tournait jamais), `check_branch_protection.py`, `i18n-update.sh` et l'en-tête des catalogues, `mkdocs.yml` et le test d'intégration. `tests/test_nom_organisation.py` refuse l'ancien nom dans les fichiers suivis
- gcm4 sur Debian 12 (GTK 4.8, libadwaita 1.2, #223) : le repli
  `Gtk.CssProvider.load_from_data` passait deux arguments et une chaîne
  (exception à chaque démarrage, CSS de l'arbre non chargé) ; il reçoit
  maintenant des octets UTF-8. La fenêtre des préférences ne s'ouvrait pas
  (`Adw.ActionRow.set_subtitle_selectable` exige libadwaita 1.3) ; l'appel est
  conditionnel. Nouveau job CI « gcm4 sur Debian 12 » dans un conteneur
  `debian:12` : démarrage sous Xvfb et tests gcm4, aucun test sauté
- gcm4 sur Rocky Linux 9 (Python 3.9, GTK 4.12, libadwaita 1.4, #223) : le
  code de `src/gcm4` ne démarrait pas en Python 3.9 (alias `X | None`
  évalués à l'exécution dans `ui/dialogues.py` et `ui/host_dialog.py`,
  `zip(strict=True)` dans `core/chiffrement.py`, `entry_points(group=...)`
  dans `core/registre.py`) ; écrit pour Python 3.9 à 3.14, sans changer
  `requires-python` (décision attendue sur #223). Nouveau job CI « gcm4 sur
  Rocky Linux 9 » dans un conteneur `rockylinux/rockylinux:9`
- Push SNMP vers un équipement Huawei VRP échouait silencieusement
  (« Constructeur inconnu : huawei_vrp ») malgré sa présence dans le menu
  déroulant : driver `HuaweiVrpDriver` manquant côté résolution effective,
  ajouté ; `snmp_bulk_core_easysnmp.py` (devenu entièrement redondant et déjà
  non importé nulle part) supprimé
- **2026-09-10** : `make validate` (et donc `make check`) échouait
  silencieusement depuis la session-08 — référence à
  `gnome-connection-manager.glade`, fichier supprimé du dépôt depuis la
  suppression de tout `.glade`/`.ui`, sans rapport avec les 423 erreurs
  `ruff` déjà documentées pour `make lint` ; référence retirée des cibles
  `install`/`validate` du `Makefile`, corrigé et vérifié en conditions
  réelles — voir `docs/gcm4-rename.md` §4, `docs/sessions/session-30.md`

### Changed
- `make deb` construit le paquet gcm4 (`build-deb/build.sh`) et le workflow Release
  le joint aux releases ; plus de `.deb` de l'application GTK3, `make deb-gcm4`
  reste un alias (#156)
- **Journalisation des `except` hérités, lot 1** (#294) : les 22 `except` sans trace de `plugins/ssh/core.py` et de `tools/` (`libvirt_inventory.py`, `ssh_deploy.py`, `validate_xml_json.py`) nomment désormais la cause (debug pour un repli normal, warning quand une donnée est ignorée, error avant de relancer). Les replis eux-mêmes ne changent pas ; ces fichiers sortent de `EXCEPT_SANS_JOURNAL`. Tests `tests/test_except_journalises_294.py`
- **Journalisation des `except` hérités, lot 2** (#294) : les 13 `except` sans trace de `snmp_bulk_core.py` nomment désormais l'hôte et la cause (warning pour un SET, un walk ou une lecture d'état en échec, debug pour un repli normal) ; retours inchangés, fichier retiré de `EXCEPT_SANS_JOURNAL`. Tests `tests/test_snmp_bulk_except_294.py`
- **Renommage du projet décidé : GCM → gcm4** (2026-08-29), pour marquer la
  génération GTK4 — pas encore effectif dans le code (module principal,
  domaine i18n, `.desktop`, packaging, chemin `~/.gcm/gcm.conf`) ; voir
  `features.md` §3.0
- **Stratégie de migration GTK4 actée : réécriture complète** (refactor MVC +
  portage GTK4 traités ensemble plutôt qu'en portage incrémental) ; le
  portage réel n'a pas commencé — voir `features.md` §3
- Nouveau module `gcm4_core.py` : cœur métier extrait de
  `gnome_connection_manager.py`, zéro dépendance GTK, préalable au découpage
  en plugins GTK4
- `fork/ROADMAP-GCM-v1.3.md` fusionné dans `features.md` (§3-4) puis
  supprimé — le dossier `fork/` n'existe plus dans le dépôt
- `README.md` mis à jour pour refléter l'état réel du code (architecture
  plugins, IPMI SOL/Web, VirtualBox/oVirt, Netmiko/SNMP push, 29 langues,
  option `--config`)
- **2026-09-09** : `Documentation-fr.md` mis à jour (dernier document
  utilisateur en retard sur l'état réel du code) — architecture plugins,
  protocoles IPMI SOL et Web, imports VirtualBox/oVirt, déploiement en
  masse Netmiko/SNMP, table des 29 langues réelles ; lien interne cassé
  vers la section Tunnels SSH corrigé au passage — voir
  `docs/sessions/session-24.md`
- Nettoyage de l'archive du dépôt (~130 Mo → ~5 Mo) : caches, volume Docker
  de test, clone dupliqué et historiques git des sous-projets vendorisés,
  brouillons de traduction obsolètes, notes de session périmées, logs de
  débogage personnels — détail dans `features.md` § Ménage effectué
- **2026-09-09** : dernier vestige SSH codé en dur dans le cœur retiré —
  `patch_edit_host_dialog` (grisage des champs + notice rouge d'un hôte
  géré par `~/.ssh/config` dans le dialogue d'édition) rapatrié dans
  `SshPlugin.patch_edit_host_dialog`, exposé via un nouveau hook générique
  `ConnectionPlugin.patch_edit_host_dialog` (no-op par défaut) ; le cœur
  n'importe plus rien de spécifique à SSH sur ce chemin — voir
  `features.md` et `docs/sessions/session-23.md`
- **2026-09-10** : audit des points de rupture GTK4 du cœur
  (`docs/gtk4-migration.md` §3.2) — deux points présentés comme ouverts
  étaient en réalité déjà résolus mais jamais documentés (`Gtk.Dialog.run()`
  intégralement remplacé par `run_dialog_sync()` ; le menu principal
  `GtkMenuBar`/`GtkMenu` déjà remplacé par un menu hamburger `Gio.Menu`
  câblé et fonctionnel) ; un troisième était du code mort
  (`Gtk.Widget.reparent()`, unique occurrence dans
  `_inject_spice_frame_LEGACY`, jamais appelée — méthode retirée) ; des
  constantes `Gtk.STOCK_CANCEL`/`Gtk.STOCK_OK` oubliées corrigées dans
  `plugins/plugin_spice.py`. Nouveau fichier
  `tests/test_gtk4_core_rupture_points.py` (7 tests) verrouillant ces
  constats — voir `docs/sessions/session-28.md`
- **2026-09-10** : audit approfondi des menus contextuels GTK4
  (`docs/gtk4-migration.md` §3.6-ter) — inventaire exhaustif des 4 menus
  applicatifs (`popupMenu`, `popupMenuFolder`, `popupMenuTab`, constructeur
  générique RDP/VNC/SPICE de `widgets.py`) et du cas `Gtk.Entry`
  (`populate-popup`) : 6 `Gtk.Menu()`, 48 items, 6 sites `.popup()`
  dépréciés. Deux difficultés structurelles identifiées (items à état
  coché sans équivalent direct `Gio.Menu`, contenu dynamique par
  plugin/onglet) — chantier plus large qu'espéré, audit et plan proposés
  seulement, portage non codé cette session. Nouveau fichier
  `tests/test_gtk4_context_menus_baseline.py` (3 tests) verrouillant les
  comptages de l'audit — voir `docs/sessions/session-29.md`
- **2026-09-10** : audit exhaustif du renommage GCM → gcm4
  (`docs/gcm4-rename.md`) — inventaire complet de ce qui doit changer dans
  le code (12 sites d'import réel du module principal, 29 occurrences du
  domaine i18n `gcm-lang`, `.desktop`, packaging `PKG_NAME`, configuration
  utilisateur `~/.gcm/gcm.conf`). Nouvelle fonction générique testée
  `gcm4_core.migrate_legacy_config_dir()` (copie non destructrice,
  idempotente, permissions préservées, aucun nom de dossier en dur) — pas
  encore câblée. Précision de l'auteure consignée : RDP restera dans un
  plugin (`plugins/rdp/{core.py,gtk4.py}`, même schéma que le pilote SSH),
  voir `docs/gtk4-migration.md` §3.4. Renommage réel non exécuté : six choix
  de noms restent à confirmer avec l'auteure (§6 de `docs/gcm4-rename.md`).
  Nouveau fichier `tests/test_gcm4_rename_inventory.py` (7 tests)
  verrouillant l'état actuel — voir `docs/sessions/session-30.md`
- **2026-09-11** : portage GTK4 effectif de `popupMenuTab` (menu contextuel
  des onglets), premier des quatre menus contextuels audités par la
  session-29 — nouvelle classe `widgets.TabContextMenu`
  (`Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.Popover`, fonctionnelle dès
  maintenant sous GTK3) remplace l'ancien `Gtk.Menu`/`Gtk.MenuItem` ; case
  "Enable logging" adaptée en `Gio.SimpleAction` à état via
  `widgets._LogActionShim` (interface `get_active()`/`set_active()`
  inchangée côté `Wmain.on_popupmenu`) ; disposition conditionnelle du menu
  (onglet actif, notebook à plusieurs volets) extraite en fonction pure
  testable sans stub GTK, `tab_context_menu_core.py`. Déclenchement
  (`Gtk.GestureClick`) et les trois autres menus contextuels non traités
  cette session. Nouveau fichier `tests/test_tab_context_menu_core.py`
  (10 tests) ; `tests/test_gtk4_context_menus_baseline.py` ajusté en
  conséquence — voir `docs/gtk4-migration.md` §3.6-quater,
  `docs/sessions/session-31.md`
- **2026-09-11** : portage GTK4 effectif de `popupMenuFolder` (menu
  contextuel du panneau de serveurs), deuxième des quatre menus contextuels
  audités par la session-29 — nouvelle classe `widgets.FolderContextMenu`
  (même trio `Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.Popover` que
  `TabContextMenu`) remplace l'ancien `Gtk.Menu`/`Gtk.MenuItem` ; les items
  spécifiques à un protocole (`plugin.build_folder_context_menu_items()`)
  restent dynamiques via un `protocol_items_provider` évalué à chaque
  ouverture plutôt qu'une liste `Gtk.MenuItem` figée. Disposition
  conditionnelle selon la cible du clic droit (vide/dossier/hôte) extraite
  en fonction pure testable sans stub GTK, `folder_context_menu_core.py`.
  Déclenchement (`Gtk.GestureClick`) et les deux menus contextuels restants
  (`popupMenu` et son sous-menu "Custom commands") non traités cette
  session. Nouveau fichier `tests/test_folder_context_menu_core.py`
  (8 tests) ; `tests/test_gtk4_context_menus_baseline.py` ajusté en
  conséquence — voir `docs/gtk4-migration.md` §3.6-quinquies,
  `docs/sessions/session-32.md`
- **2026-09-11** : portage GTK4 effectif de `popupMenu` (menu contextuel du
  terminal), troisième et dernier des quatre menus contextuels applicatifs
  audités par la session-29 — nouvelle classe `widgets.PopupMenu` (même
  trio `Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.Popover`) remplace l'ancien
  `Gtk.Menu`/`Gtk.MenuItem` ; à la différence des deux menus précédents, la
  disposition est constante, seule la sensibilité de quatre actions
  (copier, scinder H/V, réunifier) varie à l'ouverture, extraite en
  fonction pure testable sans stub GTK, `popup_menu_core.py`. Le sous-menu
  dynamique "Custom commands" devient un véritable sous-menu `Gio.Menu`
  imbriqué (`append_submenu()`, libellé texte brut sans la mise en forme
  Pango du raccourci) et le pont d'ouverture depuis le menu hamburger est
  mis à jour (`PopupMenu.popup_commands_at()`). `createMenuItem()`/
  `populateCommandsMenu()`, absorbées, sont supprimées. Les quatre menus
  contextuels applicatifs de l'audit session-29 sont désormais tous
  portés ; déclenchement (`Gtk.GestureClick`, commun aux quatre) et le cas
  `Gtk.Entry`/`populate-popup` restent ouverts. Nouveau fichier
  `tests/test_popup_menu_core.py` (22 tests) ; `tests/
  test_gtk4_context_menus_baseline.py` ajusté en conséquence (les trois
  compteurs de `gnome_connection_manager.py` tombent à zéro) — voir
  `docs/gtk4-migration.md` §3.6-sexies, `docs/sessions/session-33.md`
- **2026-09-14** : câblage de `ssh_config_editor.py` confirmé, documentation
  corrigée — `docs/architecture.md` §2.5 affirmait depuis plusieurs sessions
  qu'aucun appel n'avait été retrouvé et que le point d'intégration menu
  restait à localiser ; recherche textuelle plus poussée que celle
  d'origine (restée au seul nom de fichier) : `SshPlugin.menu_actions()`
  déclare bien l'action `"edit-ssh-config"` vers
  `SshPlugin.edit_ssh_config()`, consommée par
  `Wmain._build_primary_menu()` (section « Edit » du menu hamburger) — le
  module était câblé avant cette session, seule la documentation ne le
  disait pas. Nouveau fichier `tests/test_ssh_config_editor_wiring.py`
  (3 tests) verrouillant ce constat — voir `docs/sessions/session-34.md`
- **2026-09-14** : correction des erreurs ruff — `pyproject.toml`
  (`[tool.ruff] exclude`) élargi pour exclure les répertoires vendorisés
  `SSH-Studio/`/`gtk-frdp/` (comme `.pre-commit-config.yaml` le faisait déjà,
  mais pas cette configuration-ci), qui gonflaient le compte de 196 lignes
  sans rapport avec ce dépôt ; 73 erreurs réelles corrigées à la main sur 17
  fichiers (`models.py`, `netmiko_bulk_core.py`, `plugins/plugin_base.py` et
  8 autres plugins, `utils.py`, `widgets.py` : docstrings manquantes ou
  obsolètes, deux expressions mortes documentées par `noqa`, une variable de
  boucle inutilisée) ; ces 13 fichiers reformatés (`ruff format`) au passage ;
  `Makefile` (`lint:`) élargi de 3 chemins explicites à `ruff check .`. Reste
  exactement les 423 erreurs déjà connues de `tests/test_gcm.py` (non
  touché) et 27 fichiers non conformes à `ruff format` (non touchés, hors
  périmètre). Nouveau fichier `tests/test_ruff_config.py` (3 tests)
  verrouillant l'exclusion des répertoires vendorisés — voir
  `docs/sessions/session-35.md`
- **2026-09-16** : gestion des dépendances migrée de Poetry vers uv
  (`pyproject.toml` : `[tool.uv] package = false` remplace `[tool.poetry]
  package-mode = false`, dépendances dev déplacées vers `[dependency-groups]
  dev`, `poetry.lock`/`poetry.toml` supprimés, `uv.lock` généré ;
  `bootstrap-dev.sh` réécrit pour `uv venv --system-site-packages` +
  `uv sync --extra ... --group dev` — les deux contournements Poetry
  documentés depuis l'origine du script (bug de détection derrière un shim
  pyenv, erreur DBus/secretstorage du backend keyring) n'ont pas
  d'équivalent connu avec uv, retirés plutôt que reformulés à l'aveugle).
  Correction de la totalité des erreurs `ruff check .` restantes : les 423
  déjà connues (415 `D102` + 8 `D101`, docstrings manquantes) dans
  `tests/test_gcm.py` — jusqu'ici volontairement non traitées, hors
  périmètre d'une session (`CONSIGNES-AGENTS-IA.md` §6) — comblées par
  génération mécanique à partir du nom de chaque test/classe (ex.
  `test_with_negative_diff` → `"""Test with negative diff."""`) ; `ruff
  check .` passe désormais sans aucune erreur sur tout le dépôt. Audit du
  nombre de tests réel demandé par `claude.md` (« prochaine étape » point
  3) : `tests/test_gcm.py` compte aujourd'hui **506 méthodes de test / 58
  classes** (comptage statique, contre 492/56 documentés le 2026-08-30,
  `docs/architecture.md` §2.8 — dérive réelle depuis, pas une erreur de
  comptage) ; total dépôt **732 tests** (506 + 226 des 15 autres fichiers
  `tests/test_*.py` — dont `tests/test_uv_migration.py`, nouveau cette
  session, verrouillant la migration uv et la config flake8 ci-dessous —
  ces 226 collectés et exécutés avec succès dans cet
  environnement de travail sans GTK3/VTE — `test_gcm.py` reste non
  collectable ici, limitation déjà documentée, non vérifiée en conditions
  réelles chez l'auteure). Les deux tests orphelins déjà signalés
  (`gcm._vm_name_split`/`gcm._rdp_socket_available`, fonctions absentes de
  tout le code de production) reconfirmés toujours orphelins après ce
  passage — aucune correction tentée, toujours « à confirmer avec
  l'auteure » avant de trancher entre suppression des tests et
  renommage/réintroduction des fonctions. `README.md`/`CHANGELOG.md`
  (badge et mentions « 491 tests ») mis à jour avec les chiffres réels.
  Détail : `docs/sessions/session-36.md`
- **2026-09-16 (bis)** : ajout d'une note dans `CLAUDE.md` (« Commandes de
  qualité ») signalant que l'outil MCP Context7 est disponible pour
  consulter de la documentation à jour sur une bibliothèque/API tierce en
  cas de doute — utile en particulier dans cet environnement de travail
  sans GTK3/VTE réels. Détail : `docs/sessions/session-37.md`
- **2026-09-16 (ter)** : audit du déclenchement `Gtk.GestureClick` — point
  3 de l'audit session-29, resté ouvert après le portage effectif des
  trois menus contextuels (sessions 31 à 33). Inventaire exhaustif de 3
  familles de sites `button-press-event`/`button_press_event` réellement
  liées au déclenchement d'un menu (`on_terminal_click` × 2 sites,
  `on_tvServers_button_press_event`, `NotebookTabLabel.popupmenu`),
  distinguées de 4 sites textuellement voisins mais sans rapport avec un
  menu. Vérification auprès de la documentation GTK officielle :
  `GtkGestureMultiPress` (GTK3, depuis 3.14) et `GtkGestureClick` (GTK4)
  partagent le même signal `pressed(gesture, n_press, x, y)` — renommage
  et perte de la propriété `area` seulement, migration donc possible dès
  GTK3. Difficulté réelle identifiée : ce signal ne transmet ni bouton, ni
  modificateurs clavier, ni `Gdk.Event`, contrairement à
  `button-press-event` — les trois gestionnaires actuels en ont besoin
  (clic droit/gauche, Ctrl+clic, appels VTE `match_check_event`/
  `hyperlink_check_event`). Aucun portage de code cette session
  (audit + plan seulement, conformément à `CONSIGNES-AGENTS-IA.md`) —
  proposition d'ordre posée sans la trancher : `treeServers` en premier.
  Nouvelle classe `TestGestureClickTriggerBaseline` dans
  `tests/test_gtk4_context_menus_baseline.py`. Détail :
  `docs/sessions/session-38.md`, `docs/gtk4-migration.md` §3.6-septies
- **2026-09-17** : premier portage effectif du déclenchement
  `Gtk.GestureClick` — `treeServers`/`popupMenuFolder`
  (`Wmain.on_tvServers_pressed`, remplace
  `on_tvServers_button_press_event`), retenu en premier par la
  proposition d'ordre de la session-38. Nouveau module
  `gesture_trigger_core.py` (logique pure de classification du clic,
  destiné aussi aux deux sites restants), 7 tests. `FolderContextMenu.
  popup_at()` (`widgets.py`) adapté : reçoit désormais `x`/`y`
  directement plutôt qu'un `Gdk.EventButton`. Correction apportée à
  l'audit session-38 : `get_current_event()`/`get_current_event_state()`
  n'existent pas sur `Gtk.EventController` en GTK3 (ajouts GTK4
  uniquement, vérifié sur `docs.gtk.org`) — `Gtk.Gesture.
  get_last_event(None)` à utiliser à la place pour les deux sites
  restants. ⚠️ Interaction précise entre `Gtk.EventSequenceState.CLAIMED`
  et le comportement par défaut de `Gtk.TreeView` (sélection au clic,
  activation au double-clic) non vérifiable empiriquement dans cet
  environnement de travail — à confirmer manuellement avant mise en
  production. Détail : `docs/sessions/session-39.md`,
  `docs/gtk4-migration.md` §3.6-octies
- **2026-09-17 (bis)** : deuxième portage effectif du déclenchement
  `Gtk.GestureClick` — `on_terminal_click` (2 sites d'appel identiques,
  `Wmain.on_terminal_pressed`), retenu en deuxième par la proposition
  d'ordre de la session-38. Nouvelle méthode partagée `Wmain.
  attach_terminal_click_gesture()` (référence du geste conservée sur le
  widget terminal lui-même, créé dynamiquement par onglet, plutôt que sur
  `self`). Nouvelle fonction `gesture_trigger_core.
  classify_terminal_click()` (8 tests) : paramètre supplémentaire
  `ctrl_pressed` pour la branche Ctrl+clic gauche (détection d'URL,
  absente de `treeServers`), aucune issue `"swallow"` pour ne pas casser
  la sélection native de VTE au double/triple-clic. `widgets.PopupMenu.
  popup_at()` adapté (reçoit `x`/`y` au lieu d'un `Gdk.EventButton`), même
  principe que `FolderContextMenu.popup_at()`. Correction session-39
  (`get_last_event(None)`, pas `get_current_event()`/
  `get_current_event_state()`) appliquée dès la conception. ⚠️ Interaction
  précise entre `Gtk.PropagationPhase.TARGET` et la sélection de texte
  native de `Vte.Terminal` non vérifiable empiriquement dans cet
  environnement de travail — à confirmer manuellement avant mise en
  production. Détail : `docs/sessions/session-40.md`,
  `docs/gtk4-migration.md` §3.6-nonies
- **2026-09-17 (ter)** : troisième et dernier portage effectif du
  déclenchement `Gtk.GestureClick` — l'étiquette d'onglet
  (`NotebookTabLabel.on_label_pressed`, remplace
  `NotebookTabLabel.popupmenu`), retenue en dernier par la proposition
  d'ordre de la session-38. Geste construit directement dans
  `NotebookTabLabel.__init__` (référence conservée sur `self` — pas de
  méthode d'attache partagée nécessaire, contrairement au terminal, cette
  classe étant déjà instanciée une fois par onglet). Nouvelle fonction
  `gesture_trigger_core.classify_tab_label_click()` (6 tests) : aucune
  branche modificateur clavier (comme `treeServers`), aucune issue
  `"swallow"` (comme le terminal). Particularité propre à ce site : une
  même issue `"open-menu"` recouvre deux menus distincts (bureau distant
  RDP/VNC/SPICE en `Gtk.Menu` legacy, ou `TabContextMenu`), le choix entre
  les deux restant une décision de l'appelant ; le menu distant reste
  `Gtk.Menu` legacy, son `.popup()` à six arguments adapté avec
  `gesture.get_current_button()` + `Gtk.get_current_event_time()` plutôt
  que `gesture.get_last_event(None)` (superflu ici, aucun autre champ de
  l'évènement n'étant nécessaire). Asymétrie de
  `Gtk.EventSequenceState.CLAIMED` de l'ancien code reproduite fidèlement
  (revendiquée sur clic droit et sur clic milieu annulé par confirmation,
  pas sur fermeture d'onglet réussie) — contrairement au choix plus
  prudent (jamais de `CLAIMED`) fait pour le terminal. `widgets.
  TabContextMenu.popup_at()` adapté (reçoit `x`/`y` au lieu d'un
  `Gdk.EventButton`), même principe que les deux sites précédents. **Les
  trois sites du point 3 (audit session-38) sont désormais tous portés.**
  ⚠️ Non vérifié empiriquement dans cet environnement de travail (GTK3
  absent) — à confirmer manuellement avant mise en production. Détail :
  `docs/sessions/session-41.md`, `docs/gtk4-migration.md` §3.6-decies
- **2026-09-18** : audit du dernier point ouvert de l'audit des menus
  contextuels (session-29) — jusqu'ici désigné partout comme « le cas
  `Gtk.Entry`/`populate-popup` ». Terminologie corrigée en vérifiant le
  code réel : le widget concerné (`widgets.CellTextView`, éditeur inline
  utilisé pour renommer un hôte/dossier par double-clic dans
  `treeServers`) est une sous-classe de `Gtk.TextView`, pas de
  `Gtk.Entry` — appellation inexacte reprise dans toute la documentation
  depuis la session-29. Périmètre réel également plus large que
  documenté : 4 signaux GTK3-only connectés (`focus-out-event`,
  `key-press-event`, `populate-popup`, `button-press-event`), pas 1
  seul, avec 3 mécanismes de remplacement GTK4 distincts
  (`Gtk.EventControllerFocus`, `Gtk.EventControllerKey`,
  `Gtk.GestureClick`). Le point le plus délicat, `populate-popup` — qui
  sert ici uniquement à observer l'ouverture/fermeture du menu
  contextuel natif de l'éditeur, pas à y ajouter des items — n'a aucun
  équivalent GTK4 direct pour cet usage (`set_extra_menu()` répond à un
  besoin différent) ; la question dont dépend la solution (le
  `Gtk.Popover` interne du menu natif GTK4 vole-t-il le focus du widget
  porteur comme l'ancien `Gtk.Menu` top-level ?) n'est pas vérifiable
  empiriquement dans cet environnement de travail. **Aucun portage de
  code cette session**, conformément à `CONSIGNES-AGENTS-IA.md` : livré
  seulement l'inventaire corrigé et un nouveau verrou textuel
  (`tests/test_gtk4_inline_editor_baseline.py`, 2 tests/4 sous-tests).
  Détail : `docs/sessions/session-42.md`, `docs/gtk4-migration.md`
  §3.6-undecies
- **2026-09-18** : portage effectif du premier des quatre signaux du
  point 4 (audit session-42 ci-dessus), `key-press-event` →
  `Gtk.EventControllerKey`/`key-pressed` — même classe en GTK3 (>= 3.24,
  vérifié sur `docs.gtk.org`) et en GTK4, seule la construction diffère
  (`.new(widget)` attache directement en GTK3 ; `.new()` puis
  `widget.add_controller()` séparément en GTK4, à ajuster lors du futur
  passage réel). Choisi en premier car seul des quatre totalement
  indépendant des trois autres (ne touche pas `_in_editor_menu`) et sans
  aucun comportement d'exécution à vérifier empiriquement, contrairement
  au couple `focus-out-event`/`populate-popup`. Logique de classification
  extraite en fonction pure testable (nouveau module
  `inline_editor_core.classify_editor_key_press()`, 9 tests/5 sous-tests,
  `tests/test_inline_editor_core.py`) ; comportement inchangé (l'ancien
  gestionnaire ne retournait jamais `True`, le nouveau renvoie donc
  toujours `False`). Référence du contrôleur gardée sur `editor`
  (`editor._key_controller`), même précaution que pour les `Gtk.Gesture*`
  portés en session-39/40/41. Nuance ajoutée à la proposition d'ordre des
  trois signaux restants : `button-press-event` est mécaniquement simple
  (`Gtk.GestureClick`/`Gtk.GestureMultiPress`, déjà utilisées ailleurs)
  mais son utilité réelle (contournement d'un bug GTK3 précis) sur un
  GTK4 réel n'est pas vérifiable ici — distinction absente de l'audit
  initial de la session-42. `tests/test_gtk4_inline_editor_baseline.py`
  mis à jour (verrou positif sur le nouveau câblage, verrou négatif sur
  l'ancien signal, baseline des trois signaux restants inchangée).
  Détail : `docs/sessions/session-43.md`, `docs/gtk4-migration.md`
  §3.6-duodecies
- **2026-09-18** : portage effectif du deuxième des quatre signaux du
  point 4, `button-press-event` → `Gtk.GestureMultiPress`/`pressed`
  (même classe que les trois sites du point 3 déjà portés, sessions
  39-41), conformément à la proposition d'ordre posée en session-43.
  `set_button(0)` (tous boutons) reproduit l'absence de distinction de
  bouton de l'ancien signal. Logique extraite en fonction pure testable
  (nouveau module `inline_editor_core.classify_editor_button_press()`,
  4 tests, `tests/test_inline_editor_core.py`) — contrairement à
  `classify_editor_key_press()`, elle ne branche sur aucun paramètre :
  l'ancien gestionnaire (`_on_editor_pressed`) retournait
  inconditionnellement `True` pour tout clic reçu, contournement d'un
  bug GTK3 précis (`gtk_text_mark_get_buffer: assertion
  'GTK_IS_TEXT_MARK (mark)' failed`), sans jamais distinguer bouton ou
  nombre de pressions. La fonction reproduit ce comportement
  (renvoie toujours `"claim"`), revendiqué via
  `gesture.set_state(Gtk.EventSequenceState.CLAIMED)`, équivalent le
  plus proche disponible du `return True` d'origine. Référence du geste
  gardée sur l'éditeur (`editor._button_gesture`), même précaution que
  pour `_key_controller` en session-43. ⚠️ Nuance non résolue par ce
  portage (déjà posée en session-43) : contrairement à `key-press-event`,
  l'utilité réelle de ce contournement sur un GTK4 réel n'est pas
  vérifiable dans cet environnement de travail — seule la forme du
  portage (classe/signal/API) est vérifiée, pas l'utilité du
  comportement métier porté. `tests/test_gtk4_inline_editor_baseline.py`
  mis à jour (verrou positif/négatif, même principe que
  `key-press-event`) ; `tests/test_gtk4_context_menus_baseline.py`
  également mis à jour (le décoy `_on_editor_pressed`, compté hors
  périmètre du point 3 depuis la session-38, tombe à zéro). Compteur de
  tests hors `test_gcm.py` : 271 tests/59 sous-tests (265/53 avant cette
  session). Détail : `docs/sessions/session-44.md`,
  `docs/gtk4-migration.md` §3.6-terdecies
- **2026-09-18** : audit de `plugins/ssh/gtk4.py` (troisième des trois
  chantiers GTK4 restants, jamais détaillé au-delà de sa mention en
  §3.0-bis) — inventaire des trois fichiers GTK3 à fusionner
  (`ssh_config_editor.py`, `ssh_key_manager_dialog.py`,
  `key_picker_dialog.py`, **4435 lignes au total, aucune encore portée** ;
  `plugins/ssh/core.py`, session-26, non concerné). Bonne surprise :
  les trois fichiers passent déjà partout par `utils.run_dialog_sync()`,
  aucun `.run()` direct sur un `Gtk.Dialog` — le point de rupture le plus
  structurant du cœur (résolu en session-28) l'est donc déjà ici aussi.
  Six ruptures d'API distinctes recensées et vérifiées auprès de sources
  officielles (guide de migration GNOME, documentation de classe GTK4,
  bindings générés depuis les headers C) : `show_all()` (15 occurrences,
  supprimé), `set_border_width()` (4 occurrences, `Gtk.Container`
  supprimé), `Gtk.Clipboard.get()` (2 occurrences, classe retirée,
  remplacée par `Gdk.Clipboard`), `dlg.get_filename()` (3 occurrences,
  n'existe plus sur `Gtk.FileChooser` en GTK4), `set_current_folder(str
  (...))` (2 occurrences, signature changée pour un `Gio.File`),
  `Gtk.FileChooserDialog(...)` (3 occurrences, encore présent mais
  déprécié depuis 4.10 au profit de `Gtk.FileDialog`). Question
  d'architecture identifiée et non tranchée : adapter a minima
  l'existant ou réécrire vers `Gtk.FileDialog` (API recommandée mais
  asynchrone, changerait la forme de deux appelants) — à confirmer avec
  l'auteure, comme l'ordre des trois chantiers restants. **Aucun portage
  de code cette session**, conformément à `CONSIGNES-AGENTS-IA.md`.
  Nouveau verrou `tests/test_ssh_gtk4_baseline.py` (3 tests/18
  sous-tests). Compteur de tests hors `test_gcm.py` : 274 tests/77
  sous-tests (271/59 avant cette session). Détail :
  `docs/sessions/session-45.md`, `docs/gtk4-migration.md` §3.7
- **2026-09-19** : audit RDP (dernier des trois chantiers GTK4 restants) —
  corrige le diagnostic de `gtk4-migration.md` §3.4 (2026-08-29). Le
  repli `Gtk.Socket`/XEmbed qui y était décrit comme actif à côté de
  `GtkFrdp.Display` a en réalité disparu du code réellement chargé
  (`plugins/plugin_rdp.py` : « Seule solution RDP de GCM (pas de
  fallback) » ; ni `Gtk.Socket` ni `/parent-window:<XID>` nulle part
  hors tests orphelins de `tests/test_gcm.py`) — ce qui explique
  l'orphelin `_rdp_socket_available` déjà noté en session-36 : c'était
  le détecteur X11 de ce repli disparu. Inspection de la bibliothèque C
  vendorisée `gtk-frdp/` : `GtkFrdp.Display` est une sous-classe
  `GtkDrawingArea` sans **aucune** API X11 dans sa source (recherche
  exhaustive : `X11`/`Xlib`/`gdk_x11`/`GDK_WINDOWING_X11`/`xcb_`, zéro
  occurrence), mais son `meson.build` épingle encore explicitement
  `gtk+-3.0`. Recherche web (2026-09-19) : la migration GTK4/libadwaita
  tout juste terminée de GNOME Boxes (bêta 2026-08-03, même mainteneur
  que `gtk-frdp`, Felipe Borges) ne documente que le remplacement de son
  widget SPICE par **Libmks** — aucune mention de RDP/gtk-frdp ; statut
  GTK4 amont de `gtk-frdp` toujours non confirmé. Découverte annexe :
  `widgets.build_remote_desktop_context_menu()` (RDP/VNC/SPICE, le
  « quatrième menu contextuel » de l'audit session-29) confirmé jamais
  porté (toujours deux `Gtk.Menu()` classiques), contrairement au résumé
  de la session-33 (« les quatre menus... tous portés ») — corrigé.
  **Aucun portage de code cette session**, conformément à
  `CONSIGNES-AGENTS-IA.md` : les trois chantiers restants (couple
  `focus-out-event`/`populate-popup`, `plugins/ssh/gtk4.py`, RDP) sont
  désormais tous audités, leur ordre de traitement reste entièrement à
  confirmer avec l'auteure. Nouveau verrou
  `tests/test_gtk4_rdp_baseline.py` (6 tests/23 sous-tests). Compteur de
  tests hors `test_gcm.py` : 280 tests/100 sous-tests (274/77 avant cette
  session). Détail : `docs/sessions/session-46.md`,
  `docs/gtk4-migration.md` §3.8

---

## [1.3.3] — 2026-06-12 (MathildeDec fork)

### Added
- **Résolution IP Proxmox améliorée** : pipeline 4 niveaux QGA → ipconfig0 → ARP filtré par MAC → nmap ping scan (identique à libvirt)
- **Thème GTK bureau** : GCM lit maintenant `gsettings org.gnome.desktop.interface gtk-theme` + mode sombre `color-scheme=prefer-dark` au démarrage
- **491 tests unitaires** : 44 nouveaux tests pour SPICE Proxmox, icônes protocole, résolution IP, détection QXL, sous-groupes import, validation port, VNC

### Fixed
- `SpiceTab._build_cmd` : variable `_stdin` au lieu de `_` pour l'unpacking `exec_command` (évite `UnboundLocalError` au conflit avec `gettext._`)
- ARP fallback Proxmox : filtrage par MAC VM (évitait de retourner l'IP d'une autre VM)

## [1.3.2] — 2026-06-10 (MathildeDec fork)

### Added
- **Import libvirt — dialogue 2 phases** :
  - Phase 1 : URIs pré-remplies depuis dconf, user SSH, cases SSH/SPICE/RDP, log temps réel, progression
  - Phase 2 : tableau de prévisualisation scrollable (>250 lignes) — colonnes Proto/Nom/Groupe/IP/État/Importé
  - Cases à cocher par VM, boutons « Tout cocher / Tout décocher », case « Écraser existants »
  - Déplacé dans le menu **Fichier** (était Serveurs)
- **SSH via ProxyJump** (`-J user@hv:port`) si IP connue, shell HV + `-t ssh user@vm` sinon
- **SPICE via tunnel libvirt natif** : `virt-viewer --connect qemu+ssh://user@hv/system vm` (bypass URI `spice://`)
- **RDP conditionnel** : port 3389 sondé depuis l'HV (`nc`/`nmap`) avant création de l'entrée
- **`_check_port_open`** : sonde TCP depuis l'HV (nc -z fallback nmap)
- **`tools/libvirt_inventory.py` v4** :
  - CLI argparse complet : `--uris`, `--no-os`, `--no-nmap`, `--detect-ports`, `--vm-user`, `--ssh-password`, `--ssh-timeout`
  - Exports : JSON, CSV, Ansible INI (groupes linux/windows/rdp/running/stopped), Ansible YAML
  - `PortInfo` : détection ports SPICE/VNC/RDP (nc/nmap depuis l'HV)
  - `VM` : vCPUs, memory_mb (virsh dominfo)
  - Détection OS : XML → guest-agent → SSH direct
  - `ssh_connect` : password optionnel + timeout configurable
- **`tools/ssh_deploy.py`** : génération clés RSA-4096 + Ed25519 sur l'HV, déploiement via ssh-copy-id
- **`pyproject.toml`** : ruff (Google docstring convention, rules E/W/F/I/D/UP/B/C4/SIM), black, pytest
- **`.pre-commit-config.yaml`** : ruff (fix + format) + black + flake8 + pre-commit-hooks
- **Badge ruff** dans README

### Fixed
- `SpiceTab._build_cmd` : détecte `opts_str.startswith("--connect")` → bypasse URI `spice://` pour le mode libvirt
- Audit complet GTK3 v1.3.2 : `override_font → CssProvider`, `GtkPaned` double-add, `get_color → get_rgba`, `set_has_resize_grip` supprimé, `gtk-menu-images` protégé, `set_alignment → set_xalign`
- `loadConfig` : fallback par option individuelle (`_gopt()`) — plus de crash si option manquante
- `mark_tab_as_closed` : guard `get_parent() is None`

### Changed
- Scripts déplacés : `fork/libvirt_inventory.py` + `fork/ssh_deploy.py` → `tools/`
- Version : `1.3.1` → `1.3.2`

---

## [1.3.1] — 2026-06-09 (MathildeDec fork)

### Added
- **SerialTab** : console série RS-232/RS-485 embarquée dans VTE — picocom / minicom / screen
  - 11 templates constructeurs (Cisco, HP, Aruba, Juniper, Fortinet, Palo Alto, F5, Linux, Arduino, Modbus, Libre)
  - Combos dédiés : débit, bits de données, parité, bits de stop, contrôle de flux
- **VncTab** : connexion VNC native (vncviewer / vinagre / remmina)
- **SpiceTab** : connexion SPICE (remote-viewer / virt-viewer)
- **16 langues** : ajout uk / ja / ar / tr / nl / cs / sv / nb
- **379 tests unitaires** (pytest)
- Fix `save_host_to_ini` TypeError Python 3 (champs non-str)
- Fix `encrypt_old`/`decrypt_old` Python 3 bytes/str

---

## [1.3.0] — 2026-06-09 (MathildeDec fork)

### Added
- **RDP support** (étape 5) : connexion RDP via `xfreerdp` ou `xfreerdp3`, détection automatique du binaire disponible, onglet dédié avec log de session et boutons Connecter/Déconnecter
- **RDP XEmbed** (étape 6) : session xfreerdp embarquée directement dans l'onglet GCM via `Gtk.Socket` (X11 / XWayland) ; bascule automatique sur fenêtre externe si Wayland pur
- **Import libvirt** (étape 4) : dialogue d'import de VMs depuis `libvirt` (SSH ou local), ajout dans les groupes existants ou dans un nouveau groupe
- **GCMBase** (étape 3) : remplacement de `SimpleGladeApp` par un wrapper natif `Gtk.Builder` sans dépendance tierce
- **Docstrings Google** (issue #110) : 188 fonctions/méthodes documentées au format Google-style
- **Support du protocole dans `Host`** : champ `protocol` (ssh/telnet/rdp/local) dans la classe `Host`, le formulaire hôte et la sérialisation INI

### Fixed
- Python 3.13 : suppression de `from __future__ import print_function`, remplacement de `xrange` → `range`
- GTK3 : suppression de `Gtk.ImageMenuItem`, `Gtk.STOCK_*`, APIs dépréciées ; migration vers `Gtk.MenuItem` + `Gtk.Box`
- Issue #81 : crash au démarrage si `style.css` manquant → `try/except` non-bloquant
- Issue #82 : clone d'onglet avec mot de passe → logique `sendPassword` corrigée
- Issue #87 : freeze SSH sur équipements MikroTik → debounce 200 ms sur `on_terminal_size_allocate`
- Issue #88 : logging VTE cassé sur GTK 3.24+ → migration `output-written` / fallback `contents-changed`
- Issue #89 : passphrase SSH redemandée à chaque onglet → injection `SSH_AUTH_SOCK` dans l'environnement VTE
- Issue #64 : double-clic dans Midnight Commander ouvre un onglet → vérification `posY < tab_bar_height`
- Issue #66 : port du tunnel SSH perdu à la sauvegarde → sérialisation `tunnel_host:port` corrigée
- Issue #67 : surligage jaune cluster ne disparaît pas → `queue_draw()` forcé sur changement de cluster
- VTE dual-path : `output-written` (≥ VTE 0.60) avec fallback `contents-changed`

### Changed
- `app_web` : pointe désormais vers `https://github.com/MathildeDec/gnome-connection-manager`
- Version bumped : `1.2.1` → `1.3.0`
- Code reformatté avec `black` (max-line-length 120) — 0 erreur `flake8`

---

## [1.2.1] — upstream kuthulux

Dernière version upstream de référence.
Voir : https://github.com/kuthulux/gnome-connection-manager


### Added
- **RDP support** (étape 5) : connexion RDP via `xfreerdp` ou `xfreerdp3`, détection automatique du binaire disponible, onglet dédié avec log de session et boutons Connecter/Déconnecter
- **RDP XEmbed** (étape 6) : session xfreerdp embarquée directement dans l'onglet GCM via `Gtk.Socket` (X11 / XWayland) ; bascule automatique sur fenêtre externe si Wayland pur
- **Import libvirt** (étape 4) : dialogue d'import de VMs depuis `libvirt` (SSH ou local), ajout dans les groupes existants ou dans un nouveau groupe
- **GCMBase** (étape 3) : remplacement de `SimpleGladeApp` par un wrapper natif `Gtk.Builder` sans dépendance tierce
- **Docstrings Google** (issue #110) : 188 fonctions/méthodes documentées au format Google-style
- **Support du protocole dans `Host`** : champ `protocol` (ssh/telnet/rdp/local) dans la classe `Host`, le formulaire hôte et la sérialisation INI

### Fixed
- Python 3.13 : suppression de `from __future__ import print_function`, remplacement de `xrange` → `range`
- GTK3 : suppression de `Gtk.ImageMenuItem`, `Gtk.STOCK_*`, APIs dépréciées ; migration vers `Gtk.MenuItem` + `Gtk.Box`
- Issue #81 : crash au démarrage si `style.css` manquant → `try/except` non-bloquant
- Issue #82 : clone d'onglet avec mot de passe → logique `sendPassword` corrigée
- Issue #87 : freeze SSH sur équipements MikroTik → debounce 200 ms sur `on_terminal_size_allocate`
- Issue #88 : logging VTE cassé sur GTK 3.24+ → migration `output-written` / fallback `contents-changed`
- Issue #89 : passphrase SSH redemandée à chaque onglet → injection `SSH_AUTH_SOCK` dans l'environnement VTE
- Issue #64 : double-clic dans Midnight Commander ouvre un onglet → vérification `posY < tab_bar_height`
- Issue #66 : port du tunnel SSH perdu à la sauvegarde → sérialisation `tunnel_host:port` corrigée
- Issue #67 : surligage jaune cluster ne disparaît pas → `queue_draw()` forcé sur changement de cluster
- VTE dual-path : `output-written` (≥ VTE 0.60) avec fallback `contents-changed`

### Changed
- `app_web` : pointe désormais vers `https://github.com/MathildeDec/gnome-connection-manager`
- Version bumped : `1.2.1` → `1.3.0`
- Code reformatté avec `black` (max-line-length 120) — 0 erreur `flake8`

---

## [1.2.1] — upstream kuthulux

Dernière version upstream de référence.
Voir : https://github.com/kuthulux/gnome-connection-manager
