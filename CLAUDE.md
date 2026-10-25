# CLAUDE.md — Gnome Connection Manager (GCM → gcm4)

> Mémo de démarrage : ce qu'il faut avoir lu avant d'intervenir sur ce
> dépôt. Le reste de la documentation vit dans `docs/` — à rouvrir au
> besoin, pas à reparcourir à chaque session (carte en bas de ce fichier).

## État courant

GCM est un gestionnaire de connexions distantes multi-protocoles (SSH, RDP,
VNC, SPICE, série, telnet, local, IPMI SOL, Web/BMC) pour GNOME/GTK3, en
onglets dans une fenêtre unique, avec import d'infrastructure virtualisée
(libvirt, Proxmox, VirtualBox, oVirt) et outils de déploiement de
configuration en masse (Netmiko, SNMP). Fork de kuthulux/gnome-connection-manager,
maintenu par MathildeDec. Version courante : **4.0.0** (redémarrage de la
numérotation aligné sur le nom gcm4, décision du 2026-10-22, #299).

Renommage en cours vers **gcm4** avec le passage à GTK4 : fait dans les
livrables et la documentation, **pas fait dans le code** (module
`gnome_connection_manager.py`, domaine i18n `gcm-lang`, `.desktop`,
packaging, et surtout `~/.gcm/gcm.conf` qui casserait la config des
utilisateurs existants sans migration). Audit exhaustif + mécanisme
générique de migration de config livrés et testés (session-30,
`docs/gcm4-rename.md`) ; **exécution du renommage toujours pas commencée**
(six choix de noms à trancher avec l'auteure).

**Décisions actées, toujours en vigueur :**
- Migration GTK4 en **stratégie B** (refactoring complet + MVC, pas de
  portage incrémental) — détail et points de rupture dans
  `docs/gtk4-migration.md`.
- Architecture à plugins : un sous-dossier par plugin (`plugins/<nom>/`),
  **SSH en plugin pilote** (premier réorganisé), **GTK3 100 % abandonné**
  (pas de coexistence — chaque plugin aura `core.py` + `gtk4.py`, jamais de
  `gtk3.py`). Voir `CONSIGNES-AGENTS-IA.md` pour les règles imposées à tout
  agent travaillant sur ce chantier (logging, docstrings, tests, ruff,
  imports circulaires — strictes).
  **Points §9 de `proposition-architecture-plugins-gtk4.md` validés avec
  l'autrice le 2026-09-10** : pas de mécanisme de sélection GTK3/GTK4 à
  écrire (conséquence du "GTK3 100% abandonné"), fichiers SSH fusionnés en
  2 (`core.py`+`gtk4.py`, pas plus) — détail `docs/gtk4-migration.md`
  §3.0-bis.
- Neutralité de protocole du cœur (`gcm4_core.py`) : aucune logique
  spécifique à un protocole ne doit y rester à terme — vestiges déjà
  identifiés dans `docs/architecture.md` §2.8-bis.

**Décisions du 12/10/2026 (base GTK4, épopée #118) :**
- D1, D2, D4 et D5 validées : GTK 4 + libadwaita, commande `gcm4`,
  **Python ≥ 3.12**, paquet `src/gcm4/` (détail dans #118).
- **D3 remplacée (#99)** : configuration dans `~/.config/gcm4/gcm4.conf`
  (`XDG_CONFIG_HOME` respecté), migration automatique depuis `~/.gcm/` par
  copie (0600, ancien dossier jamais supprimé tant que GTK3 existe, #177).
- Dialogues de fichiers : `Gtk.FileDialog`, avec un repli pour GTK < 4.10
  (#97, comme dans SwitchCapture).
- **Distributions cibles** : Debian 12 (Python 3.11) et Rocky 9 (Python
  3.9) **restent dans les cibles**. D4 impose donc d'y livrer un Python
  ≥ 3.12 (ou de rendre le code compatible) et de vérifier libadwaita sur
  Rocky 9 : travail suivi dans une issue dédiée, jamais en abandonnant ces
  distributions.
- **PyGObject, GTK 4 et libadwaita viennent des paquets système** : venv
  `uv venv --system-site-packages` sur le Python système (README,
  `bootstrap-dev.sh`, `make run-gtk4`). Ne pas ajouter PyGObject aux
  dépendances uv.
- **Traductions** : chaque texte ajouté dans `src/gcm4/` est traduit dans
  les 29 langues dans la même PR (ou une PR dédiée avant la release),
  jamais laissé vide ni `fuzzy` (`tests/test_gcm4_traductions.py`).
  L'outillage (`scripts/i18n-update.sh`, workflow i18n) n'insère toujours
  aucune traduction de lui-même.
- **Onglet sélectionné** : surlignage jaune `#EEEB00` gardé, texte foncé
  (`src/gcm4/ui/style.css`).
- **Protections** : dev exige une branche à jour, pas main (seul dev y
  fusionne) ; détail dans `docs/release.md`.
- **Questions à l'autrice** : posées en commentaire de l'issue concernée,
  avec le label `question` (rouge). Une réponse donnée dans le chat est
  reportée dans ce fichier ou dans l'issue, puis le label `question` est
  retiré.
- **Exigence de qualité** : entre une solution propre et un raccourci, on
  choisit la solution propre, sans poser la question.
- **Issues d'amélioration** : une issue marquée « seulement si un besoin
  réel se confirme » ou de faible priorité reste ouverte et sera réalisée ;
  ne jamais la fermer « not planned » sans l'accord de l'autrice (#92, #93
  et #94, rouvertes le 12/10/2026).

⚠️ **Avant de répondre à « qu'est-ce qui existe dans GCM » : vérifier dans
`plugins/`, `tools/`, `lang/`, pas sur la seule foi du README.** La doc
utilisateur a longtemps décrit une version sans architecture à plugins,
sans IPMI SOL/Web, sans VirtualBox/oVirt, sans Netmiko/SNMP push, et
n'annonçait que 16 langues alors que 29 existent — README, CHANGELOG et
désormais `Documentation-fr.md` (session-24, 2026-09-09) sont à jour.

## 🎯 Chantier prioritaire : migration GTK4

Le portage GTK3 → GTK4 est **le plus gros chantier à venir** et n'a
toujours pas commencé concrètement. Points de rupture déjà identifiés
(`Gtk.Dialog.run()`, `GtkMenuBar`/`GtkMenu`, `Gtk.Widget.reparent()`, et
surtout `GtkFrdp.Display` pour le RDP embarqué — widget natif sans
dépendance X11, mais vendorisé (`gtk-frdp/`) toujours épinglé GTK3,
statut GTK4 en amont non confirmé ; **pas** de dépendance `Gtk.Socket`/
XEmbed dans le code réellement chargé, contrairement à ce qui était
compris jusqu'à la session-45 — voir §3.4/§3.8 de
`docs/gtk4-migration.md`, corrigés en session-46), stratégie retenue et
recherche sur l'écosystème GTK4 : tout est dans `docs/gtk4-migration.md`.

## Prochaine étape

0. Point isolé traité (session-25, 2026-09-10) : verrou GTK3
   (`gi.require_version("Gtk", "3.0")`) de `plugins/plugin_base.py` levé —
   voir `gtk4-migration.md` §3.6 pour le détail et pour la réponse (fourchette
   grossière, volontairement non chiffrée précisément) à la question de
   l'échéance du cœur GTK4. Questions §9 de
   `proposition-architecture-plugins-gtk4.md` validées le 2026-09-10 (voir
   §3.0-bis).
1. **Plugin pilote SSH — `plugins/ssh/core.py` extrait (session-26,
   2026-09-10)** : fusion de `ssh_config_parser.py`+`ssh_migrate_gcm.py`
   (zéro GTK, testable dès maintenant), `ssh_config_editor.py`/
   `plugins/plugin_ssh.py` importent désormais ce module. Les deux
   anciens fichiers racine sont supprimés. **Reste** : `plugins/ssh/gtk4.py`
   (portage effectif des widgets — éditeur `~/.ssh/config`, gestion des
   clés — aujourd'hui encore dans `ssh_config_editor.py`/
   `ssh_key_manager_dialog.py`/`key_picker_dialog.py` à la racine, GTK3),
   tributaire de l'avancement des points de rupture du cœur (§3.2 ci-dessous)
   pour être exécutable dans l'app — voir `docs/sessions/session-26.md`.
   Une fois ce plugin validé, généraliser le découpage `core.py`/`gtk4.py`
   aux autres plugins un par un (§8.4 de la proposition) — pas commencé.
1-bis. **Audit des points de rupture du cœur GTK4 (session-28, 2026-09-10)** :
   avant de choisir lequel des trois points restants de §3.2 traiter, vérification
   systématique de leur état réel — deux se sont révélés **déjà résolus, mais
   jamais documentés** (`Gtk.Dialog.run()` : tous les appelants passent déjà
   par `run_dialog_sync()` ; `GtkMenuBar`/`GtkMenu` du menu principal : déjà
   remplacé par un menu hamburger `Gio.Menu`, câblé et fonctionnel), et un
   troisième s'est révélé **du code mort** (`Gtk.Widget.reparent()`, unique
   occurrence dans une méthode jamais appelée, retirée cette session). Un
   `Gtk.STOCK_*` résiduel non détecté par l'audit précédent a aussi été
   corrigé dans `plugins/plugin_spice.py`. Quatre tests de non-régression
   ajoutés (`tests/test_gtk4_core_rupture_points.py`). Détail complet et
   nuances (les menus **contextuels**, eux, restent un point de rupture réel
   et non résolu) : `gtk4-migration.md` §3.6-bis, `docs/sessions/session-28.md`.
1-ter. **Audit approfondi des menus contextuels (session-29, 2026-09-10)** :
   avant de choisir entre les trois chantiers GTK4 restants (`plugins/ssh/gtk4.py`,
   menus contextuels, RDP), inventaire exhaustif du point « menus contextuels »
   isolé mais non détaillé par la session-28 : 4 menus applicatifs distincts
   (`popupMenu`, `popupMenuFolder`, `popupMenuTab`, constructeur générique
   RDP/VNC/SPICE de `widgets.py`) + le menu natif `Gtk.Entry`
   (`populate-popup`), soit 6 `Gtk.Menu()`, 48 items, 6 sites `.popup()`
   dépréciés. Deux difficultés structurelles trouvées (items à état coché
   sans équivalent direct `Gio.Menu`, contenu dynamique par plugin/onglet) —
   **chantier plus large qu'espéré, pas codé cette session** (audit + plan
   seulement, conformément à `CONSIGNES-AGENTS-IA.md`). Nouveau fichier de
   verrouillage `tests/test_gtk4_context_menus_baseline.py`. Détail complet :
   `gtk4-migration.md` §3.6-ter, `docs/sessions/session-29.md`. **⚠️ Ordre
   des trois chantiers restants à confirmer avec l'auteure** (proposition :
   `popupMenuTab` seul en premier, le plus simple des quatre menus).
1-quater. **Portage effectif de `popupMenuTab` (session-31, 2026-09-11)** :
   suite de la proposition ci-dessus — premier des quatre menus contextuels
   réellement porté vers `Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.Popover`
   (nouvelle classe `widgets.TabContextMenu` ; disposition du menu calculée
   par une fonction pure testable sans stub GTK, `tab_context_menu_core.py`
   ; case "Enable logging" adaptée en `Gio.SimpleAction` à état via
   `widgets._LogActionShim`). **Reste** : `popupMenu`/`popupMenuFolder` (2
   des 4 menus, ordre entre eux non tranché), le déclenchement
   `Gtk.GestureClick` (commun aux 4, hors périmètre de cette session), et le
   cas `Gtk.Entry`/`populate-popup`. Détail : `gtk4-migration.md`
   §3.6-quater, `docs/sessions/session-31.md`.
1-quinquies. **Portage effectif de `popupMenuFolder` (session-32,
   2026-09-11)** : deuxième des quatre menus contextuels réellement porté,
   même trio `Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.Popover` (nouvelle
   classe `widgets.FolderContextMenu` ; disposition calculée par
   `folder_context_menu_core.py`, testable sans stub GTK ; items
   spécifiques à un protocole gardés dynamiques via un
   `protocol_items_provider` évalué à chaque ouverture plutôt qu'une liste
   `Gtk.MenuItem` figée). **Reste** : `popupMenu` (seul des 4 menus non
   encore porté, avec son sous-menu dynamique "Custom commands"), le
   déclenchement `Gtk.GestureClick` (commun aux 4), et le cas
   `Gtk.Entry`/`populate-popup`. Détail : `gtk4-migration.md`
   §3.6-quinquies, `docs/sessions/session-32.md`.
1-sexies. **Portage effectif de `popupMenu` (session-33, 2026-09-11)** :
   troisième et dernier des quatre menus contextuels applicatifs réellement
   porté, même trio `Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.Popover`
   (nouvelle classe `widgets.PopupMenu` ; disposition **constante** — à la
   différence des deux précédents, aucune visibilité conditionnelle
   d'item : seule la sensibilité de quatre actions varie à l'ouverture,
   calculée par `popup_menu_core.py`, testable sans stub GTK ; sous-menu
   dynamique "Custom commands" devenu un véritable sous-menu `Gio.Menu`
   imbriqué — libellé texte brut, sans la mise en forme Pango du raccourci
   que portait l'ancien `Gtk.MenuItem`, différence assumée et documentée ;
   pont du menu hamburger mis à jour en conséquence,
   `PopupMenu.popup_commands_at()`). Les quatre menus contextuels
   applicatifs identifiés par l'audit session-29 sont désormais tous
   portés. **Reste** : le déclenchement `Gtk.GestureClick` (commun aux 4),
   le cas `Gtk.Entry`/`populate-popup`, `plugins/ssh/gtk4.py` et RDP —
   ordre entre ces chantiers toujours à confirmer avec l'auteure. Détail :
   `gtk4-migration.md` §3.6-sexies, `docs/sessions/session-33.md`.
1-septies. **Audit du déclenchement `Gtk.GestureClick` (session-38,
   2026-09-16)** : avant de choisir lequel des quatre chantiers GTK4
   restants traiter, inventaire exhaustif du point 3 (déclenchement)
   resté ouvert depuis les trois portages de menus — 3 familles de sites
   réelles (`on_terminal_click` × 2 sites, `on_tvServers_button_press_event`,
   `NotebookTabLabel.popupmenu`), distinguées de 4 sites textuellement
   voisins mais sans rapport avec un menu. Vérification auprès de la
   documentation GTK officielle : `GtkGestureMultiPress` (GTK3, dispo
   depuis 3.14) et `GtkGestureClick` (GTK4) partagent le même signal
   `pressed(gesture, n_press, x, y)` — renommage plus perte de la
   propriété `area`, migration donc possible dès GTK3. Difficulté réelle
   identifiée : le signal `pressed` ne transmet ni bouton, ni
   modificateurs clavier, ni `Gdk.Event` — les trois gestionnaires actuels
   en ont besoin (bouton droit/gauche, Ctrl+clic, appels VTE
   `match_check_event`/`hyperlink_check_event`), nécessitant
   `get_current_event_state()`/`get_current_event()` sur le geste.
   **Pas codé cette session** (audit + plan seulement, conformément à
   `CONSIGNES-AGENTS-IA.md`) — proposition d'ordre posée sans la trancher :
   `treeServers`/`popupMenuFolder` en premier (seul sans branche
   modificateur), `on_terminal_click` ensuite, l'étiquette d'onglet en
   dernier (seule à alimenter aussi le menu RDP/VNC/SPICE encore non
   porté). Nouvelle classe de verrouillage
   `tests/test_gtk4_context_menus_baseline.py::TestGestureClickTriggerBaseline`.
   Détail : `gtk4-migration.md` §3.6-septies, `docs/sessions/session-38.md`.
1-octies. **Portage effectif de `treeServers`/`popupMenuFolder` (session-39,
   2026-09-17)** : premier des trois sites du point 3 effectivement porté
   vers `Gtk.GestureMultiPress` (`Wmain.on_tvServers_pressed`, remplace
   `on_tvServers_button_press_event`), conformément à la proposition
   d'ordre de la session-38. Nouveau module `gesture_trigger_core.py`
   (logique pure de classification du clic, destiné aussi aux deux sites
   restants) ; `FolderContextMenu.popup_at()` adapté (reçoit `x`/`y` au
   lieu d'un `Gdk.EventButton`). **Correction apportée à l'audit
   session-38** : `get_current_event()`/`get_current_event_state()`
   n'existent pas en GTK3 sur `Gtk.EventController` (ajouts GTK4
   uniquement, vérifié sur `docs.gtk.org`) — utiliser
   `Gtk.Gesture.get_last_event(None)` à la place pour les deux sites
   restants. **Reste** : `on_terminal_click` (2 sites, branche Ctrl+clic)
   et l'étiquette d'onglet (menu mixte porté/non porté) — ⚠️ interaction
   précise entre `Gtk.EventSequenceState.CLAIMED` et le gestionnaire par
   défaut de `Gtk.TreeView` non vérifiable empiriquement ici, à confirmer
   manuellement avant mise en production (clic droit, clic simple,
   double-clic sur treeServers). Détail : `gtk4-migration.md`
   §3.6-octies, `docs/sessions/session-39.md`.
1-nonies. **Portage effectif du déclenchement `on_terminal_click`
   (session-40, 2026-09-17)** : deuxième des trois sites du point 3
   effectivement porté vers `Gtk.GestureMultiPress`
   (`Wmain.on_terminal_pressed`, remplace `on_terminal_click` ; 2 sites
   d'appel identiques attachés via la nouvelle méthode partagée
   `Wmain.attach_terminal_click_gesture()`, référence du geste conservée
   sur le widget terminal lui-même plutôt que sur `self`, puisque chaque
   terminal est créé dynamiquement contrairement à `treeServers`).
   Nouvelle fonction `gesture_trigger_core.classify_terminal_click()`
   (8 tests) — troisième paramètre `ctrl_pressed` par rapport à
   `classify_tree_servers_press()`, pour la branche Ctrl+clic gauche
   (détection d'URL) absente de `treeServers` ; aucune issue `"swallow"`
   ici, contrairement à `treeServers` : le double/triple-clic reste géré
   nativement par VTE (sélection de mot/ligne), jamais avalé.
   `widgets.PopupMenu.popup_at()` adapté (reçoit `x`/`y` au lieu d'un
   `Gdk.EventButton`), même principe que `FolderContextMenu.popup_at()`
   en session-39. Correction session-39 (`get_last_event(None)`, pas
   `get_current_event()`/`get_current_event_state()`) appliquée dès la
   conception. **Reste** : l'étiquette d'onglet
   (`NotebookTabLabel.popupmenu`, seul des trois sites du point 3 non
   encore porté) — ⚠️ interaction précise entre
   `Gtk.PropagationPhase.TARGET` et la sélection de texte native de
   `Vte.Terminal` non vérifiable empiriquement ici, à confirmer
   manuellement avant mise en production (clic droit ouvre le menu/colle,
   Ctrl+clic gauche ouvre une URL, double/triple-clic sélectionne
   mot/ligne, focus après clic simple). Détail : `gtk4-migration.md`
   §3.6-nonies, `docs/sessions/session-40.md`.
1-decies. **Portage effectif du déclenchement de l'étiquette d'onglet
   (session-41, 2026-09-17)** : troisième et dernier des trois sites du
   point 3 effectivement porté vers `Gtk.GestureMultiPress`
   (`NotebookTabLabel.on_label_pressed`, remplace
   `NotebookTabLabel.popupmenu` ; geste construit directement dans
   `__init__`, référence conservée sur `self` — pas de méthode d'attache
   partagée nécessaire, `NotebookTabLabel` étant déjà créée une fois par
   onglet). Nouvelle fonction `gesture_trigger_core.classify_tab_label_click()`
   (6 tests) — comme `treeServers`, aucune branche modificateur clavier ;
   comme le terminal, aucune issue `"swallow"`. Particularité propre à ce
   site : une même issue `"open-menu"` recouvre deux menus distincts
   (bureau distant RDP/VNC/SPICE en `Gtk.Menu` legacy, ou
   `TabContextMenu`) — choix laissé à l'appelant, hors de portée de la
   fonction pure ; le menu distant reste `Gtk.Menu` legacy, son
   `.popup()` à six arguments adapté avec `gesture.get_current_button()`
   + `Gtk.get_current_event_time()` (pas de dépendance à
   `gesture.get_last_event(None)`, superflu ici). Asymétrie de
   `Gtk.EventSequenceState.CLAIMED` de l'ancien code reproduite
   fidèlement (revendiquée sur clic droit et sur clic milieu annulé, pas
   sur fermeture d'onglet réussie) — contrairement au choix plus prudent
   (jamais de `CLAIMED`) fait pour le terminal en session-40.
   `widgets.TabContextMenu.popup_at()` adapté, même principe que les deux
   sites précédents. **Les trois sites du point 3 (audit session-38) sont
   désormais tous portés** — ⚠️ non vérifié empiriquement ici (GTK3
   absent), à confirmer manuellement avant mise en production (clic droit
   ouvre le bon menu selon le protocole de l'onglet, clic milieu ferme
   l'onglet avec confirmation le cas échéant, clic gauche change toujours
   d'onglet normalement). Détail : `gtk4-migration.md` §3.6-decies,
   `docs/sessions/session-41.md`.
1-undecies. **Audit du point 4 — éditeur inline `CellTextView`, ex-« cas
   `Gtk.Entry`/`populate-popup` » (session-42, 2026-09-18)** : avant de
   porter ce dernier point ouvert de l'audit session-29 (§3.6-ter, item 5),
   vérification du code réel — le widget concerné est en fait une
   sous-classe de `Gtk.TextView`+`Gtk.CellEditable` (`CellTextView`), pas
   un `Gtk.Entry` (terminologie inexacte reprise dans toute la
   documentation depuis la session-29, corrigée ici). Périmètre réel
   également plus large que documenté : `CellTextView.do_start_editing()`
   connecte 4 signaux GTK3-only, pas 1 (`focus-out-event`,
   `key-press-event`, `populate-popup`, `button-press-event`), avec 3
   mécanismes de remplacement GTK4 distincts. Le point le plus délicat,
   `populate-popup` — qui ici sert uniquement à observer l'ouverture/
   fermeture du menu contextuel natif de l'éditeur (pas à y ajouter des
   items) — n'a aucun équivalent GTK4 direct pour cet usage ; la question
   dont dépend la solution (le `Gtk.Popover` interne du menu natif GTK4
   vole-t-il le focus du widget porteur comme le faisait l'ancien
   `Gtk.Menu` ?) n'est pas vérifiable empiriquement dans cet environnement
   de travail. **Pas de portage de code cette session**, conformément à
   `CONSIGNES-AGENTS-IA.md` (ne pas coder à l'aveugle un comportement
   d'exécution non vérifiable, surtout sur une fonctionnalité de base
   aussi utilisée que le renommage inline d'un hôte/dossier). Livré :
   l'inventaire corrigé et un nouveau verrou textuel
   (`tests/test_gtk4_inline_editor_baseline.py`, 2 tests) verrouillant les
   4 signaux actuels et la correction de terminologie. **Reste** : ordre
   de portage des 4 signaux à proposer et confirmer avec l'auteure, comme
   pour `plugins/ssh/gtk4.py`/RDP. Détail : `gtk4-migration.md`
   §3.6-undecies, `docs/sessions/session-42.md`.
1-duodecies. **Portage effectif de `key-press-event` (session-43,
   2026-09-18)** : premier des quatre signaux du point 4 (audit
   session-42 ci-dessus) effectivement porté, vers
   `Gtk.EventControllerKey`/`key-pressed` — vérifié sur `docs.gtk.org` :
   même classe et même signal en GTK3 (≥ 3.24) et GTK4, seule la
   construction diffère (`.new(widget)` attache directement en GTK3 ;
   `.new()` puis `widget.add_controller()` séparément en GTK4, à ajuster
   au futur passage réel). Choisi en premier car seul des quatre signaux
   totalement indépendant des trois autres (ne touche pas
   `_in_editor_menu`) et sans aucun comportement d'exécution à vérifier
   empiriquement — proposition d'ordre posée cette session pour les trois
   signaux restants : `button-press-event` ensuite, le couple
   `focus-out-event`/`populate-popup` en dernier (le plus risqué). Logique
   extraite en fonction pure testable (nouveau module
   `inline_editor_core.classify_editor_key_press()`, 9 tests/5 sous-tests) ;
   comportement inchangé (l'ancien gestionnaire ne retournait jamais
   `True`, le nouveau renvoie donc toujours `False`). Référence du
   contrôleur gardée sur l'éditeur (`editor._key_controller`), même
   précaution que pour les `Gtk.Gesture*` des sessions 39-41. **Nuance
   ajoutée** par rapport à l'audit initial de la session-42 : parmi les
   trois signaux restants, `button-press-event` est mécaniquement simple
   mais son utilité réelle (contournement d'un bug GTK3 précis) sur un
   GTK4 réel n'est pas vérifiable ici — à distinguer du couple
   `focus-out-event`/`populate-popup`, seul réellement bloqué sur une
   question de comportement d'exécution GTK4. `tests/
   test_gtk4_inline_editor_baseline.py` mis à jour (verrou positif sur le
   nouveau câblage, verrou négatif sur l'ancien signal, baseline des
   trois signaux restants inchangée). **Reste** : `button-press-event` et
   le couple `focus-out-event`/`populate-popup` (ordre entre les deux non
   tranché), `plugins/ssh/gtk4.py`, et RDP — ⚠️ ordre entre ces chantiers
   toujours à confirmer avec l'auteure. Détail : `gtk4-migration.md`
   §3.6-duodecies, `docs/sessions/session-43.md`.
1-terdecies. **Portage effectif de `button-press-event` (session-44,
   2026-09-18)** : deuxième des quatre signaux du point 4 effectivement
   porté, vers `Gtk.GestureMultiPress`/`pressed` (même classe que les
   trois sites du point 3, sessions 39-41 ; `set_button(0)`, tous
   boutons, comme l'ancien signal). Nouvelle fonction
   `inline_editor_core.classify_editor_button_press()` (4 tests) —
   contrairement à `classify_editor_key_press()`, elle ne branche sur
   aucun paramètre : l'ancien gestionnaire retournait inconditionnellement
   `True` pour tout clic (contournement d'un bug GTK3 précis), et la
   nouvelle fonction reproduit ce comportement en renvoyant toujours
   `"claim"`, revendiqué via
   `gesture.set_state(Gtk.EventSequenceState.CLAIMED)`, équivalent le plus
   proche du `return True` d'origine. Référence du geste gardée sur
   l'éditeur (`editor._button_gesture`), même précaution que pour
   `_key_controller` (session-43). **Nuance non résolue** (déjà posée en
   session-43) : contrairement à `key-press-event`, l'utilité réelle de ce
   contournement sur un GTK4 réel n'est pas vérifiable ici (le bug GTK3
   d'origine peut avoir disparu) — seule la forme du portage
   (classe/signal/API) est vérifiée, pas l'utilité du comportement métier
   porté ; distinction qui permet de coder ce signal sans trancher à
   l'aveugle un comportement d'exécution. `tests/
   test_gtk4_context_menus_baseline.py` mis à jour (le décoy
   `_on_editor_pressed`, compté hors périmètre du point 3 depuis la
   session-38, tombe à zéro ; verrou positif dans
   `tests/test_gtk4_inline_editor_baseline.py`). **Reste** : le couple
   `focus-out-event`/`populate-popup` (le plus risqué des quatre),
   `plugins/ssh/gtk4.py`, et RDP — ⚠️ ordre entre ces chantiers toujours à
   confirmer avec l'auteure. Détail : `gtk4-migration.md` §3.6-terdecies,
   `docs/sessions/session-44.md`.
1-quaterdecies. **Audit de `plugins/ssh/gtk4.py` (session-45, 2026-09-18)** :
   avant de choisir lequel des trois chantiers restants traiter, inventaire
   du périmètre réel de `plugins/ssh/gtk4.py` (jamais détaillé au-delà de sa
   mention en §3.0-bis) — trois fichiers GTK3 à fusionner, **4435 lignes au
   total, aucune encore auditée ni portée** : `ssh_config_editor.py` (1379
   lignes), `ssh_key_manager_dialog.py` (2678 lignes), `key_picker_dialog.py`
   (378 lignes) ; `plugins/ssh/core.py` (session-26, zéro GTK) non concerné.
   Bonne surprise : les trois fichiers passent déjà partout par
   `utils.run_dialog_sync()`, aucun `.run()` direct sur un `Gtk.Dialog` —
   le point de rupture le plus structurant du cœur (§3.2) est donc déjà
   résolu ici aussi. Six ruptures d'API distinctes recensées et vérifiées
   auprès de sources officielles (guide de migration GNOME, doc de classe
   GTK4, bindings générés depuis les headers C) : `show_all()` (15 occ.,
   supprimé), `set_border_width()` (4 occ., `Gtk.Container` supprimé),
   `Gtk.Clipboard.get()` (2 occ., classe retirée, remplacée par
   `Gdk.Clipboard`), `dlg.get_filename()` (3 occ., n'existe plus sur
   `Gtk.FileChooser` en GTK4), `set_current_folder(str(...))` (2 occ.,
   signature changée pour un `Gio.File`), `Gtk.FileChooserDialog(...)`
   (3 occ., encore présent mais déprécié depuis 4.10 au profit de
   `Gtk.FileDialog`). Les ~90 occurrences cumulées de
   `pack_start()`/`pack_end()`/`.add()` ne sont volontairement pas
   verrouillées ligne à ligne (seront de toute façon réécrites en bloc par
   la Stratégie B) — seul leur total est mentionné pour chiffrer l'ampleur
   du chantier. **Question d'architecture identifiée, non tranchée** :
   pour le trio `FileChooserDialog`, adapter a minima l'existant ou
   réécrire vers `Gtk.FileDialog` (API recommandée mais asynchrone,
   changerait la forme de deux appelants) — à confirmer avec l'auteure,
   comme l'ordre des trois chantiers restants. **Pas codé cette session**
   (audit seulement, conformément à `CONSIGNES-AGENTS-IA.md` : chantier au
   moins aussi engageant que celui des menus contextuels, qui avait pris
   quatre sessions de portage une fois audité). Nouveau verrou
   `tests/test_ssh_gtk4_baseline.py` (3 tests/18 sous-tests). Détail :
   `gtk4-migration.md` §3.7, `docs/sessions/session-45.md`.
1-quindecies. **Audit du chantier RDP (session-46, 2026-09-19)** :
   dernier des trois chantiers restants encore non audité. Corrige §3.4
   (rédigé le 2026-08-29) : le code réellement chargé
   (`plugins/plugin_rdp.py`) n'a plus de repli `Gtk.Socket`/XEmbed — sa
   propre docstring le dit (« Seule solution RDP de GCM (pas de
   fallback) »), et l'ancien mécanisme (classe `RdpEmbeddedTab`,
   sous-processus `xfreerdp` + `/parent-window:<XID>`) a disparu de
   `gnome_connection_manager.py` ; seuls des tests orphelins en
   subsistent (`tests/test_gcm.py`), confirmant que
   `_rdp_socket_available` (déjà noté orphelin, session-36,
   `docs/architecture.md` §5 point 5) était précisément son détecteur
   X11. Le point ouvert n'est donc plus « quel remplaçant pour XEmbed »
   mais « `GtkFrdp.Display` (bibliothèque C vendorisée `gtk-frdp/`,
   sous-classe `GtkDrawingArea`, aucune API X11 dans sa source) a-t-elle
   un chemin GTK4 ? » — non confirmé en amont malgré la migration
   GTK4/libadwaita tout juste terminée de GNOME Boxes (bêta 2026-08-03,
   même mainteneur que `gtk-frdp`), qui ne documente que le remplacement
   du widget SPICE (par **Libmks**), jamais RDP/gtk-frdp (recherche web
   du 2026-09-19, à reconfirmer périodiquement). Découverte annexe : le
   « quatrième menu contextuel » de l'audit session-29
   (`build_remote_desktop_context_menu()` de `widgets.py`, partagé par
   RDP/VNC/SPICE) n'a en réalité jamais été porté, contrairement à ce que
   dit le résumé de l'entrée 1-sexies ci-dessus (seuls les trois autres
   l'ont été — `tests/test_gtk4_context_menus_baseline.py` le documentait
   déjà correctement en creux). **Pas codé cette session** (audit
   seulement, même discipline que pour `plugins/ssh/gtk4.py`). Nouveau
   verrou `tests/test_gtk4_rdp_baseline.py` (6 tests/23 sous-tests).
   Détail : `gtk4-migration.md` §3.8, `docs/sessions/session-46.md`.
   **Reste** : les trois chantiers (couple `focus-out-event`/
   `populate-popup`, `plugins/ssh/gtk4.py`, RDP) sont désormais tous
   audités — ⚠️ leur ordre de traitement reste à confirmer avec
   l'auteure.
2. **Audit exhaustif + mécanisme de migration pour le renommage GCM → gcm4
   (session-30, 2026-09-10)** : inventaire complet de ce qui doit changer
   (12 sites d'import réel du module principal, 29 occurrences du domaine
   i18n `gcm-lang`, `.desktop`, packaging, config utilisateur) et nouvelle
   fonction générique testée `gcm4_core.migrate_legacy_config_dir()`
   (copie, jamais destructrice, idempotente, sans nom de dossier en dur —
   8 tests). **Pas d'exécution du renommage cette session** : six choix de
   noms non tranchés (module, dossier de config, fichier de conf/clé,
   domaine i18n, id `.desktop`, `PKG_NAME`) — voir le tableau `à confirmer
   avec l'auteure` de `docs/gcm4-rename.md` §6. Bonus trouvé en auditant le
   packaging : `make validate`/`make check` échouaient silencieusement
   depuis la session-08 (référence à un `.glade` supprimé) — corrigé et
   vérifié. Nouveau verrou `tests/test_gcm4_rename_inventory.py`. Détail :
   `docs/gcm4-rename.md`, `docs/sessions/session-30.md`.
3. Reste de l'urgence haute (SFTP, master password, arbitrage
   `snmp_push_core.py`) :
   **à confirmer avec l'auteure avant d'implémenter**, ne pas trancher seul —
   détail de chaque point dans `docs/features-backlog.md`.
   **Confirmation du nombre de tests réel et des tests orphelins — retiré de
   cette liste (session-36, 2026-09-16)** : ce point-ci était un audit, pas
   une décision produit — traité. `tests/test_gcm.py` compte **506 méthodes
   de test / 58 classes** (comptage statique, contre 492/56 documentés le
   2026-08-30 — dérive réelle, pas une erreur de comptage) ; total dépôt
   **732 tests** (506 + 226 des 15 autres fichiers `tests/test_*.py`, sans
   dépendance GTK, collectés et exécutés avec succès dans cet environnement
   de travail). `_vm_name_split`/`_rdp_socket_available` reconfirmés
   orphelins (aucune trace hors `tests/test_gcm.py`, recherche répétée sur
   tout le dépôt) — **ce point précis reste, lui, à confirmer avec
   l'auteure** avant de trancher entre suppression des tests et
   renommage/réintroduction des fonctions manquantes (voir
   `docs/architecture.md` §2.8). README/CHANGELOG (« 491 tests ») corrigés
   avec les chiffres réels. Détail : `docs/sessions/session-36.md`.
   Master password : la logique de chiffrement (`master_password_core.py`,
   session-27, 2026-09-10) est faite et testée isolément (PBKDF2 + enveloppe
   `pyAES`, rétrocompatible avec un `KEY_FILE` legacy non protégé) ; le
   câblage réel (prompt GTK au démarrage, politique en cas de mot de passe
   oublié) reste bloqué sur une décision produit à confirmer avec l'auteure,
   pas codé à l'aveugle.
   `Documentation-fr.md` (dernier point de doc utilisateur en retard) a été
   mis à jour (session-24, 2026-09-09) : architecture plugins, IPMI SOL/Web,
   VirtualBox/oVirt, Netmiko/SNMP push, 29 langues — n'attendait aucune
   décision produit, seulement un rattrapage factuel.
   **`ssh_config_editor.py` — retiré de cette liste (session-34,
   2026-09-14)** : recherche textuelle plus poussée que celle d'origine
   (`architecture.md` §2.5, restée au seul nom de fichier) — `SshPlugin.
   menu_actions()` (`plugins/plugin_ssh.py`) déclare bien l'action
   `"edit-ssh-config"` vers `SshPlugin.edit_ssh_config()`, consommée par
   `Wmain._build_primary_menu()` (`gnome_connection_manager.py`, section
   `edit_section3` du menu « Edit » du hamburger). Le module était câblé
   avant cette session ; seule la documentation ne le disait pas. Rien à
   arbitrer avec l'auteure sur ce point, contrairement à
   `snmp_push_core.py` : ce n'était pas un choix produit ouvert, juste un
   retard de documentation. Nouveau verrou textuel
   `tests/test_ssh_config_editor_wiring.py`. Détail :
   `docs/sessions/session-34.md`.

## Commandes de qualité

```bash
ruff check <fichier> && ruff format <fichier>   # sur tout fichier neuf ou touché
make test      # python3 -m pytest tests/ -v
make lint      # ruff check . + flake8 gnome_connection_manager.py
make validate  # .po (+ .xml/.glade/.json si le dépôt en contient un jour)
make check     # validate + lint + test + check-gitignore
```

⚠️ **`ruff check .` passe désormais sans aucune erreur (corrigé session-36,
2026-09-16)** — `make lint` dans son ensemble (`ruff check .` **+**
`flake8 gnome_connection_manager.py`) ne passe toujours pas : en
corrigeant les 423 erreurs `tests/test_gcm.py` ci-dessous, un second écart
distinct a été découvert sur `flake8`, jamais configuré pour ce dépôt (ni
`.flake8` ni `setup.cfg`) — tournait donc avec sa limite par défaut de 79
caractères au lieu des 99 déclarés partout ailleurs (`pyproject.toml`,
`ruff`/`black`), remontant 435 faux positifs `E501` rien que sur
`gnome_connection_manager.py`. Fichier `.flake8` ajouté (`max-line-length
= 99`), ramenant le compte à 182 (97 lignes réellement >99 caractères +
85 constats de code légués déjà connus et volontairement ignorés par
`ruff` pour ce fichier — `E711`/`E721`/`E722`/`F841`, voir la liste
`ignore` de `[tool.ruff.lint]` — mais que `flake8`, non configuré de la
même façon, continue de signaler). Non corrigés cette session : hors
périmètre de la demande (« correction des erreurs *ruff* »), et un
correctif à l'aveugle sur 182 lignes d'un fichier de 7600 lignes
risquerait des effets de bord non vérifiables sans GTK3/VTE réel dans cet
environnement de travail. Les 423 erreurs `tests/test_gcm.py`
(docstrings manquantes, seul reste connu depuis la session-35) ont été comblées par génération mécanique d'une docstring
d'une ligne à partir du nom de chaque test/classe sans docstring — honnête
(reflet littéral du nom existant, aucun contenu inventé) mais mécanique,
une relecture humaine reste utile sur les noms de test peu clairs. `ruff
check .` : **0 erreur** sur tout le dépôt. Bug latent corrigé au passage :
`[tool.ruff] exclude` (personnalisé) ne couvrait pas `.venv` — une fois
l'environnement uv installé, `ruff check .`/`ruff format --check .`
scannaient aussi ses paquets tiers (21419 erreurs et 1122 fichiers de
formatage en trop, tous dans `.venv/`) ; `.venv` ajouté à l'exclude.
`ruff format --check .` (formatage, distinct du lint) : 26 fichiers non
conformes restants (27 avant cette session, `tests/test_gcm.py` reformaté
au passage puisque massivement modifié) — non traités, hors périmètre de
cette session (`CONSIGNES-AGENTS-IA.md` §6). Détail :
`docs/sessions/session-36.md`.

Historique (corrigé ci-dessus, gardé pour mémoire) : `make lint` échouait
depuis la session-08 environ, 423 erreurs dans `tests/test_gcm.py`
uniquement, docstrings manquantes — pas une régression introduite par une
session donnée ; voir `docs/architecture.md` §2.8. Point affiné session-35
(2026-09-14) : `pyproject.toml` (`[tool.ruff] exclude`) n'excluait pas les
répertoires vendorisés `SSH-Studio/`/`gtk-frdp/` (contrairement à
`.pre-commit-config.yaml`, correct depuis l'origine) — corrigé, et les 73
erreurs réelles restantes hors `test_gcm.py` (17 fichiers : `models.py`,
`netmiko_bulk_core.py`, `plugin_base.py` et 8 autres plugins, `utils.py`,
`widgets.py`) ont été corrigées. `Makefile` (`lint:`) élargi en
conséquence à `ruff check .` (couvrait seulement 3 chemins auparavant).

ℹ️ `make validate` échouait silencieusement depuis la session-08
(référence à un `gnome-connection-manager.glade` supprimé du dépôt) —
corrigé session-30, voir `docs/gcm4-rename.md` §4.

⚠️ **GTK3/VTE non installés dans cet environnement de travail** :
`tests/test_gcm.py` ne s'importe pas ici. Tout code métier sans GTK a ses
propres fichiers de test indépendants et importables directement
(`test_gcm4_core.py`, `test_snmp_bulk_core.py`, etc.) — c'est là qu'ajouter
les tests de tout nouveau code cœur. `test_gtk4_core_rupture_points.py`
(session-28) suit un principe différent pour les rares cas où même ça n'est
pas possible : au lieu d'importer le module GTK, il scanne son texte source
(comme `test_plugin_base.py::test_no_require_version_call_in_source`) —
utile pour verrouiller l'absence d'un point de rupture GTK4 précis sans
attendre que tout `gnome_connection_manager.py` soit importable ici.

Pre-commit (une fois) : `pip install pre-commit --break-system-packages &&
pre-commit install` — imports circulaires interdits
(`tools/check_circular_imports.py`). Règles complètes de logging/docstrings/
tests pour le code des plugins : `CONSIGNES-AGENTS-IA.md`.

ℹ️ **Context7 disponible** (session-37, 2026-09-16) : outil MCP
consultable pour de la documentation à jour sur une bibliothèque/API tierce
(PyGObject/GTK, Vte, Netmiko, paramiko, cryptography, etc.) quand un doute
porte sur une signature, un comportement récent ou une dépréciation — utile
en particulier ici où l'environnement de travail n'a pas GTK3/VTE réels
pour vérifier empiriquement. Ne remplace pas la lecture du code source du
dépôt lui-même ni les décisions déjà actées dans cette documentation.

## Jobs CI (`.github/workflows/ci.yml`, issue #159)

| Job | Runner | Contenu |
|---|---|---|
| Qualité (lint, tests) | maison (self-hosted) | ruff, flake8, format, pre-commit, imports circulaires, import-linter, pytest + couverture, aucun test sauté hors `src/gcm4` absent (#160) |
| Types (mypy) | maison | mypy (#131) |
| i18n (catalogues à jour) | maison | validation, fraîcheur et compilation des `.po` (#129) |
| Tests GTK4 (Xvfb) | ubuntu-latest, conteneur `ubuntu:26.04` | `tests/gtk4` (interface gcm4, #134) et `plugins/pluginvnc2/tests` contre un vrai GTK4 + libadwaita, échec si un test est sauté (#160) |

Paquets : workflow `release.yml` (#162). `concurrency` : un nouveau push
annule le run obsolète de la même branche. Runners surchargeables par les
variables `RUNS_ON_LINUX` et `RUNS_ON_GTK4`. `make ci` rejoue les trois
premiers jobs.

## Carte de la documentation

- **`docs/features-backlog.md`** — fait / pas encore fait, par urgence (importé ci-dessous)
- `docs/architecture.md` — état détaillé du code module par module, écarts doc/code
- `docs/gtk4-migration.md` — stratégie complète de la migration GTK4
- `docs/gcm4-rename.md` — audit exhaustif + plan du renommage GCM → gcm4 (module, i18n, `.desktop`, packaging, config utilisateur), distinct de la migration GTK4
- `docs/sessions/session-NN.md` — historique détaillé, un fichier par session ;
  à rouvrir seulement pour retrouver le raisonnement d'une décision passée,
  pas à chaque démarrage
- `CONSIGNES-AGENTS-IA.md` — règles strictes pour le code des plugins
- `proposition-architecture-plugins-gtk4.md`, `PATTERNS-COMPARISON.md` — inchangés

@docs/features-backlog.md

## Documentation MkDocs (issue #127)

- `mkdocs.yml` à la racine : thème material, `language: fr`, `docs_dir: docs`, nav explicite, pas de `site_url`. `docs/sessions/` est exclu du site.
- README.md, Documentation-fr.md et CHANGELOG.md restent à la racine. Ils sont inclus par `pymdownx.snippets` (`docs/guide/utilisateur.md`, `docs/guide/changements.md`), sans copie.
- Toute nouvelle page va dans `docs/` **et** dans la `nav`. Job CI « Construction (mkdocs build --strict) », qui échoue sur un lien mort et publie le site en artefact.
- En local : `uvx --with mkdocs-material --with pymdown-extensions --from "mkdocs<2" mkdocs serve`.

## Instructions reçues dans le chat (issue #237)

Toute instruction reçue dans le chat (Perplexity Computer, Claude Code ou autre) devient une issue. Une phrase ne reste pas en l'air.

- Avant de commencer le travail, créer l'issue dans le dépôt concerné, en citant l'instruction mot pour mot avec sa date.
- Une demande qui touche plusieurs dépôts donne une issue par dépôt, chacune liée aux autres.
- Une question posée dans le chat sans réponse immédiate devient elle aussi une issue, ou un commentaire « Question pour Mathilde » sur l'issue existante.
- La PR ferme ou cite l'issue (`Closes #n`). Une instruction abandonnée est fermée avec sa raison (« not planned »).
- En fin de session, vérifier que chaque instruction du chat a son issue, ouverte ou fermée.
