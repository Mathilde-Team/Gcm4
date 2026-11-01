# Migration GTK4 — dette structurelle majeure, stratégie

> Chantier prioritaire du projet (voir `../CLAUDE.md`). Contenu déplacé tel
> quel depuis l'ancien `features.md` §3 lors de la réorganisation de la
> documentation — non réédité depuis.

C'est le plus gros chantier à venir. Le portage réel **n'a pas commencé** ; le code
actuel ne fait qu'anticiper certains points de rupture. Cette section sert de base
de décision : elle inventorie les points de rupture connus, pose les deux stratégies
possibles, et sera complétée avec les travaux séparés déjà réalisés sur les
approches VNC/RDP/SPICE en GTK4 dès leur dépôt dans le projet.

## 3.0 Décisions actées (2026-08-29)
- **Stratégie retenue : B — refactoring complet / réécriture** (voir §3.3).
  Le refactor MVC et la migration GTK4 seront donc traités ensemble plutôt
  qu'en portage incrémental module par module.
- **Renommage du projet : GCM → gcm4**, pour marquer la génération GTK4.
  Fait dans cette session : le nom apparaît dans les livrables (nom du zip)
  et dans cette documentation. **Non fait** — chantier à part entière, à ne
  pas mener à l'aveugle en marge d'une autre tâche : renommage effectif dans
  le code (module `gnome_connection_manager.py`, importé par tous les
  plugins et par `tests/test_gcm.py`), domaine i18n `"gcm-lang"`, fichier
  `.desktop`, metainfo AppStream, cibles de paquet (`make deb/rpm/opensuse`),
  et surtout le chemin de configuration utilisateur actuel `~/.gcm/gcm.conf`
  (`CONFIG_FILE`/`CONFIG_DIR` dans `gnome_connection_manager.py`) — le
  renommer sans chemin de migration casserait la configuration des
  utilisateurs existants (cf. `ssh_migrate_gcm.py`, qui gère déjà ce genre
  de transition pour un autre changement de config). Numérotation de version
  après renommage (reprise en 1.x ou redémarrage en 4.0.0 ?) : pas tranchée,
  à décider avec l'auteure.

## 3.0-bis Décisions actées (2026-09-10) — réponses aux questions ouvertes du §9 de `proposition-architecture-plugins-gtk4.md`

- **Principe de découpage** : dossier par plugin avec **`core.py` + `gtk4.py`
  uniquement** — pas de `gtk3.py`. Confirme et durcit la décision déjà actée
  du 2026-08-29 (« GTK3 100 % abandonné, pas de coexistence ») : un plugin
  n'aura jamais qu'un seul fichier d'UI, la génération GTK4.
- **Mécanisme de sélection GTK3/GTK4 au chargement (§5, options a/b)** :
  **sans objet** — conséquence directe du point précédent. Puisqu'aucun
  `gtk3.py` n'existera jamais à côté d'un `gtk4.py` dans un même plugin, il
  n'y a rien à sélectionner au runtime ; le flag `plugin_base.GTK_MAJOR`
  évoqué en option (a) n'a plus de raison d'être. Nuance importante pour la
  suite : cela ne veut pas dire que l'app tourne déjà en GTK4 — tant que
  `gnome_connection_manager.py` (le cœur GTK, §3.2 ci-dessous) n'est pas
  lui-même porté, un plugin restructuré en `core.py`/`gtk4.py` ne sera pas
  chargeable dans l'app réelle avant que ce portage du cœur soit fait. Le
  découpage en dossiers peut néanmoins commencer dès maintenant côté
  structure/`core.py` (logique métier, testable sans GTK dès aujourd'hui),
  le `gtk4.py` de chaque plugin restant non exécutable tant que le cœur n'a
  pas basculé.
- **Fichiers SSH (§6)** : **fusion**. `ssh_config_editor.py`,
  `ssh_config_parser.py`, `ssh_key_manager_dialog.py`, `ssh_migrate_gcm.py`
  et `plugins/plugin_ssh.py` (aujourd'hui éclatés à la racine + `plugins/`)
  seront regroupés en exactement deux fichiers dans `plugins/ssh/` :
  `core.py` (logique métier, zéro GTK — parsing `~/.ssh/config`, gestion des
  clés, migration de configuration) et `gtk4.py` (dialogues et widgets).
- **Plugin pilote (§8.3)** : confirmé, **SSH**.

Conséquence sur l'ordre de mise en œuvre (§3.6 ci-dessous, point 2 —
« valider avec l'autrice » — est donc levé) : le prochain travail de codage
sur ce chantier peut commencer par l'extraction de `plugins/ssh/core.py`
(partie testable sans GTK dès maintenant, sur le modèle de l'extraction de
`gcm4_core.py` en session-05), une session distincte étant réservée ensuite
pour `plugins/ssh/gtk4.py` (portage effectif des widgets — dépend lui-même
de l'avancement des points de rupture du cœur en §3.2, en particulier
`Gtk.Dialog.run()`, avant de pouvoir tourner réellement).

## 3.1 Contexte — décision initiale à reconsidérer
`fork/ROADMAP-GCM-v1.3.md` actait explicitement, au démarrage du fork
(2026-06-09) : **« GTK3 (pas GTK4) — GTK4 non installé sur l'environnement de
dev, VTE/GTK4 peu mature, migration sans bénéfice visible perçu »**. Cette
décision est **à considérer comme caduque** : c'est désormais l'inverse qui est
demandé. Elle reste consignée ici pour mémoire (elle explique pourquoi plusieurs
API dépréciées n'ont jamais été traitées) et parce que les raisons alors invoquées
(maturité des bindings VTE/GtkVnc/SpiceClientGtk sous GTK4) sont exactement les
points à revérifier avant de trancher la stratégie.

## 3.2 Points de rupture déjà identifiés dans le code actuel

| Point | Où | Nature du problème |
|---|---|---|
| ~~`Gtk.Dialog.run()`~~ | `utils.run_dialog_sync()` | **Résolu, confirmé session-28 (2026-09-10)** — supprimé en GTK4, contourné par une boucle `GLib.MainLoop` (mécanisme documenté par la doc de migration GTK elle-même, valable en GTK3 comme en GTK4). Audit exhaustif : **tous** les appelants de `gnome_connection_manager.py` passent déjà par `run_dialog_sync()`, aucun `.run()` direct sur un `Gtk.Dialog` ne subsiste — verrouillé par `tests/test_gtk4_core_rupture_points.py::TestDialogRunRuptureResolved` |
| ~~`GtkMenuBar` / `GtkMenu`~~ (menu principal) | `Wmain._build_primary_menu()` | **Résolu, découvert non documenté en session-28** — le menu hamburger (`Gtk.MenuButton` + `Gio.Menu`/`Gio.SimpleAction`, commentaire interne « palier 2 ») est déjà écrit et câblé (`self._build_primary_menu()` appelé à l'init, `btnPrimaryMenu.set_menu_model(menu)`) ; aucune trace de ce travail n'apparaissait dans cette documentation ni dans `features-backlog.md`/`CLAUDE.md` avant cette session — écart doc/code corrigé ici, verrouillé par `TestMenuBarRuptureResolved`. Reste néanmoins un `Gtk.Menu` classique pour « Custom commands » (assumé et documenté dans le code, cf. §3.6-bis) et pour les menus contextuels (clic droit sur l'arbre/les onglets/la console) — non couverts par ce point, voir §3.6-bis |
| ~~`Gtk.Widget.reparent()`~~ | *(n'existe plus)* | **Faux positif d'audit, corrigé session-28** — l'unique occurrence vivait dans `_inject_spice_frame_LEGACY`, une méthode jamais appelée nulle part dans le dépôt (le vrai remplacement, `_inject_spice_frame`, est un no-op documenté « géré par le glade » depuis longtemps). Ce point de rupture n'était donc déjà plus vivant avant même cette session ; la méthode morte a été retirée pour ne plus fausser l'inventaire — verrouillé par `TestReparentRuptureResolved` |
| `Gtk.Socket` / XEmbed (RDP) | `plugin_rdp.py` | XEmbed est un mécanisme X11 ; **`Gtk.Socket` n'existe plus en GTK4**, y compris sous XWayland. Toujours le point le plus critique : l'embarquement RDP natif devra changer de mécanisme (voir §3.4) — **non concerné par la session-28**, aucun changement |
| `GtkVnc.Display` (VNC) | `plugin_vnc.py` | Widget GTK3 de `gtk-vnc` — statut de la version GTK4 de la bibliothèque à vérifier avant de porter le plugin — **non concerné par la session-28** |
| `SpiceClientGtk.Display` (SPICE) | `plugin_spice.py` | Idem, à vérifier côté `spice-gtk` — **non concerné par la session-28** (le correctif STOCK_* de cette session touche `plugin_spice.py` mais un autre point isolé, voir ligne suivante) |
| `SimpleGladeApp` → `Gtk.Builder` | déjà fait (étape 3 du fork) | Le code utilise déjà `Gtk.Builder` natif — bonne base pour GTK4, mais le fichier `.glade` (format GtkBuilder XML) devra être audité (attributs `use-stock`, `stock-id`, `GtkImageMenuItem` déjà en partie nettoyés en étape 2 GTK3) |
| VTE | Terminal SSH/Telnet/Local/IPMI SOL | Vérifier la disponibilité et l'API de `Vte-3.0`/GTK4 (paquet `vte4`) sur les distributions cibles avant de s'engager — **non concerné par la session-28** |
| ~~Icônes `Gtk.STOCK_*` restantes~~ | `plugins/plugin_spice.py::_ask_password_dialog` | **Trouvé et corrigé en session-28** — contrairement à ce que cette ligne affirmait (« nettoyage déjà traité »), `Gtk.STOCK_CANCEL`/`Gtk.STOCK_OK` subsistaient bel et bien dans `plugin_spice.py` ; remplacés par des libellés traduits littéraux (`_("Cancel")`/`_("OK")`), sur le modèle déjà en place dans `plugins/plugin_ssh.py`. Vérifié : plus aucune occurrence de `Gtk.STOCK_` dans le cœur ni dans `plugins/*.py` — verrouillé par `TestNoStockConstantsRemain` |

## 3.3 Deux stratégies possibles — décision à prendre

**A — Portage incrémental module par module**
- Geler l'API interne (`ConnectionPlugin`/`BatchPlugin`) comme couche
  d'abstraction déjà en place, et porter les widgets GTK3 vers GTK4 plugin par
  plugin derrière cette même interface.
- Avantage : risque maîtrisé, l'app reste utilisable à chaque étape, cohérent
  avec le refactor plugins déjà terminé (§2.4).
- Risque : deux code paths GTK3/GTK4 à maintenir en parallèle pendant la
  transition si on veut livrer des versions intermédiaires.

**B — Refactoring complet / réécriture**
- Profiter de la bascule pour traiter en même temps le refactor MVC jamais fait
  (séparation logique métier / UI, item historique « post v1.3 » toujours ouvert,
  voir §4.2) et la taille de `gnome_connection_manager.py` (~7500 lignes).
- Avantage : évite de porter puis re-porter la même dette technique deux fois.
- Risque : chantier plus long avant toute version livrable, effort de test plus
  élevé (492 tests actuels à adapter aux nouveaux points d'entrée).

**Stratégie B retenue (2026-08-29, voir §3.0).** Le point bloquant commun aux
deux stratégies restait l'embarquement RDP (§3.4) ; les recherches de §3.5 ne
lèvent pas totalement l'incertitude mais donnent des références externes
concrètes pour instruire l'architecture d'embarquement RDP/VNC/SPICE du futur
gcm4.

## 3.4 Cas particulier — RDP embarqué (le point le plus incertain)
`GtkFrdp.Display` (sous-projet vendorisé `gtk-frdp/`) et le fallback
`Gtk.Socket`/XEmbed reposent tous deux, à des degrés divers, sur des mécanismes
liés à X11. Sous GTK4 (Wayland-first), il faudra très probablement soit :
- confirmer que `GtkFrdp` (s'il est activement maintenu côté GNOME) expose un
  widget natif GTK4 sans dépendre de XEmbed, soit
- basculer entièrement sur le mode « fenêtre externe » déjà utilisé aujourd'hui
  en repli Wayland pur (perte de l'intégration en onglet native), soit
- explorer une intégration par portail Wayland/`libei`/capture de sous-surface,
  qui n'a pas d'équivalent direct à XEmbed.

**Précision de l'auteure (2026-09-10, session-30)** : RDP restera dans un
plugin — le portage GTK4 de l'embarquement (quelle que soit l'option retenue
ci-dessus) se fera dans une restructuration `plugins/rdp/{core.py,gtk4.py}`
suivant le même schéma que le pilote SSH (§3.6), pas comme un traitement
spécial au niveau du cœur. Cela ne tranche pas l'incertitude technique
elle-même (quel mécanisme remplace `Gtk.Socket`/XEmbed, toujours à
vérifier — §3.5), seulement son organisation : `plugin_rdp.py` reste un
plugin comme les autres au sens de `CONSIGNES-AGENTS-IA.md` §1 (neutralité de
protocole du cœur), et sa session de portage, quand elle viendra, suivra la
même mécanique d'extraction que `plugins/ssh/`.

**Mise à jour (session-46, 2026-09-19)** : l'audit du code réellement
chargé montre que la prémisse de ce paragraphe — un repli `Gtk.Socket`/
XEmbed encore actif à côté de `GtkFrdp.Display` — ne tient plus. Ce
mécanisme a disparu de `plugins/plugin_rdp.py`/`gnome_connection_manager.py`
(probablement au moment de la bascule vers l'architecture à plugins,
avant même cette section) ; il n'en reste que des tests orphelins. La
question réellement ouverte a changé de nature — voir §3.8, qui corrige
et complète ce paragraphe sans en changer l'esprit (RDP reste « le point
le plus incertain », mais pas pour la raison ici décrite).

## 3.5 État de l'écosystème GTK4 pour VTE/VNC/RDP/SPICE (recherche web, 2026-08-29)
Recherche ponctuelle pour instruire la décision de §3.0 — à reconfirmer sur les
distributions cibles avant de s'engager, ces informations évoluent vite :

- **VTE** : `vte4`/`vte291-gtk4` est un paquet réel et actif (Arch, Fedora,
  Debian `libvte-2.91-gtk4-dev`, Alpine, RHEL, Homebrew `vte3`), construit et
  consommé aujourd'hui par de vrais projets GTK4 (RustConn ci-dessous exige
  VTE4 comme dépendance de build). Ne devrait plus être le point bloquant.
- **gtk-vnc** : le paquet distribué le plus récent trouvé (CachyOS, build
  juin 2025) est encore lié à `gtk3` ; aucun portage GTK4 publié localisé.
- **spice-gtk** : idem — toujours `libspice-client-gtk-3.0` au suivi Debian
  sid (jusqu'à fin 2025), aucune variante `-4.0` répertoriée nulle part.
  Signe convergent : RustConn (ci-dessous), pourtant 100 % GTK4 et
  activement développé, n'essaie pas d'embarquer SPICE nativement et
  bascule systématiquement sur un visualiseur externe
  (`remote-viewer`/`virt-viewer`) — exactement le mode de repli que gcm
  utilise déjà en Wayland pur (§2.1). Probablement la voie la plus sûre pour
  gcm4 côté SPICE plutôt que de chercher un widget natif GTK4.
- **GtkFrdp** : statut GTK4 natif toujours **non confirmé**. Son
  consommateur de référence, **GNOME Connections**, est lui-même encore
  répertorié GTK3 aujourd'hui — pas de validation indirecte possible par ce
  biais, contrairement à ce qu'on aurait pu espérer.
- **Prior art directement comparable à gcm4**, apparu depuis la rédaction
  initiale de cette section : **RustConn** (Rust, GTK4/libadwaita ≥4.14/1.5,
  SSH+RDP+VNC+SPICE+Telnet+Série+Kubernetes+Zero Trust) et **Field Monitor**
  (Rust/GTK4/libadwaita, RDP/VNC/SPICE ciblé Proxmox/QEMU-KVM — très proche
  de l'usage import libvirt/Proxmox de gcm) résolvent aujourd'hui exactement
  le même problème que la migration gcm4. Leur choix : implémentation
  RDP/VNC embarquée maison en premier recours, repli client externe
  (FreeRDP/TigerVNC) si l'embarqué échoue — aucune dépendance à
  `gtk-frdp`/`gtk-vnc`. Un troisième projet, **qemu-display** (crate Rust
  `rdw`, « Remote Display Widget »), vise spécifiquement des widgets GTK4
  pour VNC/RDP/SPICE mais reste expérimental/démo.
  Sources : dépôt GitHub `totoshko88/RustConn` (`INSTALL.md`/`USER_GUIDE.md`),
  `rustdesk/qemu-display`, fiche Flathub Field Monitor, This Week in GNOME
  #236/#247/#259 (2026), traqueurs de paquets Arch/Debian/CachyOS pour
  vte4/gtk-vnc/spice-gtk.
- **Implication pour §3.3** : ces trois projets étant en Rust (pas
  réutilisables comme bibliothèque Python par gcm4), mais leur choix
  architectural — widgets embarqués maison plutôt que
  gtk-vnc/spice-gtk/gtk-frdp vieillissants, repli externe assumé pour SPICE
  — est un signal de marché à prendre en compte pour l'architecture
  RDP/VNC/SPICE de gcm4, indépendamment du langage d'implémentation.

## 3.6 Premier pas concret du portage (session-25, 2026-09-10)

Jusqu'ici, **aucune ligne de portage GTK4 réel n'avait été écrite** — seul du
travail préparatoire (séparation `gcm4_core.py`, suppression du vestige
`patch_edit_host_dialog`). Cette session lève **l'étape 1** de l'ordre de
mise en œuvre proposé (`proposition-architecture-plugins-gtk4.md` §8) :

- `plugins/plugin_base.py` ne verrouille plus `gi.require_version("Gtk",
  "3.0")` ni n'importe plus `Gtk` réellement au niveau module — remplacé par
  un bloc `if TYPE_CHECKING: from gi.repository import Gtk`, l'unique usage
  restant étant des annotations de type déjà paresseuses (`from __future__
  import annotations`). Vérifié en important le module dans un environnement
  où `gi`/PyGObject n'est **pas installé du tout** : import réussi, `gi`
  absent de `sys.modules` après coup (`tests/test_plugin_base.py`, 4 tests
  nouveaux). Le contrat public (`ConnectionPlugin`, `PluginRegistry`,
  `BatchPlugin`, `BatchPluginRegistry`) est inchangé.
- Portée volontairement limitée à ce point isolé (§4 de la proposition,
  « peut être faite seule, en premier, sans attendre la réorganisation en
  dossiers ») — aucun découpage en `plugins/<nom>/core.py`/`gtk4.py` fait
  cette session, aucun plugin réellement porté vers des widgets GTK4.

**Ce qui reste avant que le *cœur* (pas les plugins un par un) soit
« prêt » GTK4**, dans l'ordre du §8 de la proposition puis du §3.2 ci-dessus :

1. ~~`plugin_base.py` : lever le verrou GTK3~~ — **fait cette session.**
2. ~~Valider avec l'autrice les points ouverts du §9~~ — **fait le
   2026-09-10** (voir §3.0-bis : dossier `core.py`+`gtk4.py` uniquement,
   pas de mécanisme de sélection à écrire, fusion SSH en 2 fichiers, pilote
   SSH confirmé).
3. Réorganiser le plugin pilote SSH en `plugins/ssh/{core.py,gtk4.py}` —
   **`core.py` (logique métier, testable sans GTK) peut être extrait dès la
   prochaine session** ; `gtk4.py` (widgets réels) reste tributaire du point 4
   ci-dessous pour être exécutable dans l'app, même s'il peut être écrit en
   parallèle.
4. Traiter dans `gnome_connection_manager.py` lui-même (le futur `gtk4.py`
   central, ~7610 lignes) chacun des points de rupture du §3.2 :
   `Gtk.Dialog.run()`, `GtkMenuBar`/`GtkMenu` → `Gio.Menu`, et surtout
   `Gtk.Widget.reparent()` — trois chantiers indépendants, chacun
   vraisemblablement une session à lui seul vu la règle du projet (un plugin
   ou un point de rupture isolé par session, pas tout d'un coup).
5. Cas le plus incertain : l'embarquement RDP (§3.4) — dépend d'abord d'une
   vérification externe (statut GTK4 réel de `GtkFrdp`/repli fenêtre
   externe) avant de pouvoir être chiffré en sessions.
6. Seulement à ce stade le *cœur* GTK4 (fenêtre principale, dialogues,
   menus) serait fonctionnellement portable, indépendamment des plugins
   VNC/SPICE (bibliothèques tierces encore GTK3 seules, §3.5) qui resteraient
   à traiter plugin par plugin ensuite.

**Estimation de sessions : toujours non chiffrable avec fiabilité**, même
la validation du point 2 obtenue — la principale inconnue restante est
désormais externe au projet (statut réel de `GtkFrdp`/`gtk-vnc`/`spice-gtk`
en GTK4, point 5) et non plus une question de méthode interne. À
titre de repère de vitesse historique **uniquement** (aucune garantie de
transposition, la difficulté d'un point de rupture GTK4 n'a rien à voir avec
celle d'une fonctionnalité applicative déjà connue) : 24 sessions ont livré
24 items fonctionnels bornés depuis le 2026-08-29 (`features-backlog.md`),
soit en moyenne un item par session — les points 3 à 5 ci-dessus comptent
au moins 5 chantiers de taille comparable ou supérieure (dont un, le RDP,
explicitement qualifié de plus incertain de tout le projet), plus la
session de décision du point 2 elle-même. **Ordre de grandeur brut, à ne pas
citer comme un engagement : pas moins d'une demi-douzaine de sessions
supplémentaires avant un cœur GTK4 fonctionnellement complet**, sans compter
ensuite le portage plugin par plugin (§8.4 de la proposition) qui reste hors
du périmètre « cœur ». Le point 2 (validation avec l'autrice) est le
prochain à traiter et conditionne tout raffinement sérieux de cette
estimation.

*(Note post-session-28 : le point 4 ci-dessus listait trois chantiers
« indépendants, chacun vraisemblablement une session à lui seul ». Deux se
sont révélés déjà faits et un troisième déjà mort code — voir §3.6-bis.
L'estimation ci-dessus, écrite avant cet audit, comptait donc ces trois
chantiers en trop ; elle n'a pas été rechiffrée pour autant, la principale
inconnue restante (RDP, point 5) étant inchangée.)*

## 3.6-bis Audit des points de rupture du cœur (session-28, 2026-09-10)

**Constat de départ** : `CLAUDE.md`/§3.6 ci-dessus affirmaient qu'« aucune
ligne de portage GTK4 réel n'avait été écrite » avant la session-25, et que
les trois points de rupture du §3.2 concernant `gnome_connection_manager.py`
(`Gtk.Dialog.run()`, `GtkMenuBar`/`GtkMenu`, `reparent()`) restaient à
traiter, chacun « vraisemblablement une session à lui seul ». Avant de
choisir lequel traiter en premier, vérification systématique de l'état réel
du code (même discipline que les audits des sessions-07/20/23/24, qui
avaient déjà trouvé plusieurs items du backlog « déjà faits ») :

- **`Gtk.Dialog.run()`** : déjà intégralement couvert par
  `utils.run_dialog_sync()`, utilisé par tous les appelants du fichier — un
  `grep` exhaustif de `.run(` ne remonte que ce mécanisme, `subprocess.run`
  (détection de thème desktop) et `w_main.run()` (boucle GTK principale,
  sans rapport avec un dialogue). Rien à coder : déjà résolu, seule
  l'absence de test de non-régression manquait.
- **`GtkMenuBar`/`GtkMenu`** : en réalité déjà remplacé par un menu
  hamburger (`Gtk.MenuButton` + `Gio.Menu`/`Gio.SimpleAction`,
  `Wmain._build_primary_menu()`, commentaire interne « palier 2 »), câblé
  et fonctionnel (`self._build_primary_menu()` appelé à l'initialisation,
  `btnPrimaryMenu.set_menu_model(menu)`). **Aucune trace de ce travail dans
  la documentation de suivi** (`gtk4-migration.md`, `features-backlog.md`,
  `CLAUDE.md`, `CHANGELOG.md`, aucun fichier de session) avant cette
  session — écart doc/code non détecté jusqu'ici, du même type que ceux
  déjà corrigés par les audits antérieurs, mais dans l'autre sens (du
  travail fait et non documenté, plutôt qu'un backlog obsolète). Nuance
  importante : ceci ne couvre que le *menu principal*. Les menus
  contextuels (clic droit sur `treeServers`, les onglets, la console —
  `popupMenu`/`popupMenuFolder`/`popupMenuTab`) et le sous-menu « Custom
  commands » restent des `Gtk.Menu` classiques, assumés comme tels dans le
  code (docstring de `_build_primary_menu`) — **également supprimé en
  GTK4**, donc un point de rupture réel et non résolu, mais distinct de
  celui du §3.2 (qui visait spécifiquement le menu bar principal). Non
  traité cette session, à re-signaler pour une session dédiée si le
  chantier cœur progresse.
- **`Gtk.Widget.reparent()`** : la seule occurrence du fichier
  (`grid.reparent(vbox)`) vivait dans `_inject_spice_frame_LEGACY`, une
  méthode explicitement documentée « Ne pas appeler » et, vérification
  faite, **jamais appelée nulle part dans le dépôt** — le vrai chemin actif
  est `_inject_spice_frame` (no-op, « widgets SPICE maintenant dans le
  glade »). Ce point de rupture n'était donc déjà plus vivant avant cette
  session, juste mal classé dans l'inventaire. Décision : retirer la
  méthode morte plutôt que la garder en l'état — le commentaire
  au-dessus affirmait déjà qu'elle était « conservée sous forme de
  commentaire pour référence », ce qui n'était pas exact (c'était du code
  Python actif, pas un commentaire) ; l'historique complet reste
  disponible dans git, un commentaire d'une ligne suffit dans le fichier.
- **Bonus trouvé en cours d'audit, hors des trois points ci-dessus** : la
  ligne « Icônes `Gtk.STOCK_*` restantes » de §3.2 affirmait le nettoyage
  déjà fait ; un `grep` sur `plugins/*.py` a trouvé `Gtk.STOCK_CANCEL`/
  `Gtk.STOCK_OK` toujours présents dans `plugins/plugin_spice.py`
  (`_ask_password_dialog`). Corrigé au même modèle que le reste du projet
  (libellés traduits littéraux plutôt que constantes stock).

**Livré cette session** : suppression de `_inject_spice_frame_LEGACY`
(code mort, ~108 lignes) dans `gnome_connection_manager.py` ; correctif
`Gtk.STOCK_*` → libellés traduits dans `plugins/plugin_spice.py` ; nouveau
fichier `tests/test_gtk4_core_rupture_points.py` (7 tests, scan textuel du
code source, aucune dépendance GTK requise) verrouillant ces quatre
constats pour éviter toute régression future ; mise à jour du tableau §3.2
et de l'estimation §3.6. Détail complet : `docs/sessions/session-28.md`.

**Conséquence sur l'ordre de mise en œuvre** : des trois chantiers du point
4 de §3.6, il ne reste donc plus, au niveau du *cœur*, que la question des
menus contextuels (`Gtk.Menu` classique, nouvellement isolée ci-dessus) —
un chantier bien plus petit que ce que laissait supposer l'estimation
précédente. Le point 5 (RDP, §3.4) reste inchangé et demeure la principale
inconnue du chantier GTK4.

## 3.6-ter Audit approfondi des menus contextuels (session-29, 2026-09-10)

**Objectif de la session** : avant de choisir entre les trois chantiers
restants (`plugins/ssh/gtk4.py`, menus contextuels, RDP — §3.6 point 4/5),
inventaire exhaustif du point « menus contextuels » isolé mais non détaillé
par la session-28, pour évaluer sa taille réelle. Conformément à
`CONSIGNES-AGENTS-IA.md` (« proposer un plan avant de coder un gros
morceau », « ne pas trancher seul une ambiguïté ») : **audit et plan
seulement cette session, pas de portage** — le chantier s'est révélé plus
large qu'espéré par la note de clôture de session-28 (voir inventaire
ci-dessous), et surtout entrelacé à des choix de conception (comment
représenter un `Gtk.CheckMenuItem` en `Gio.SimpleAction`, comment garder les
menus « Custom commands »/protocole dynamiques avec un `GMenuModel`
normalement statique) qui méritent d'être posés avant de coder, pas
découverts en cours de route sur un fichier aussi central.

**Inventaire complet** (recherche exhaustive `Gtk.Menu(`,
`.popup(None, None, None, None,`, `Gtk.MenuItem`/`CheckMenuItem`/
`SeparatorMenuItem`, `populate-popup`, hors dossiers vendorisés
`SSH-Studio`/`gtk-frdp` et environnements de test `rdp`/`vnc`) :

| Menu | Fichier | Items | Particularités |
|---|---|---|---|
| `popupMenu` (clic droit sur la console) | `gnome_connection_manager.py::createMenu` | 12 items + 2 séparateurs + 1 sous-menu | Contient `mnuLog` (`Gtk.CheckMenuItem`, état coché/décoché) et le sous-menu dynamique « Custom commands » (`mnuCommands`, repeuplé au runtime par `populateCommandsMenu()` via `foreach(...).remove(...)`) |
| `popupMenuFolder` (clic droit sur `treeServers`) | idem | 13 items + 2 séparateurs | Items **injectés dynamiquement par plugin** (`plugin.build_folder_context_menu_items()`, boucle sur `plugin_registry.all_sorted()`) — un `GMenuModel` doit rester mutable à l'exécution pour ce cas, pas seulement construit une fois |
| `popupMenuTab` (clic droit sur un onglet) | idem | 9 items | `mnuLog` idem (`CheckMenuItem`) ; visibilité conditionnelle de plusieurs items selon l'état (`mnuReopen`, `mnuSplitH`/`mnuSplitV` selon le nombre d'onglets) — en `Gio.Menu`, la visibilité conditionnelle passe par `action.set_enabled()`/`action.set_state()`, pas par `.show()`/`.hide()` sur l'item |
| `build_remote_desktop_context_menu()` (RDP/VNC/SPICE, clic droit sur l'onglet bureau distant) | `widgets.py` | 2 items fixes + N items `extra_items` (variable, ex. périphériques USB SPICE) + 1 sous-menu « Special keys » (5 entrées) | Fonction générique partagée par 3 plugins — un seul point à porter plutôt que trois, si le futur remplacement garde la même signature |
| Menu contextuel natif de `Gtk.Entry` (édition du nom d'hôte/groupe) | `widgets.py::MultilineCellRenderer._on_editor_populate_popup` | — | Signal `populate-popup` de `Gtk.Entry`, **remplacé en GTK4** par `Gtk.Text.set_extra_menu()`/`set_extra_menu(Gio.MenuModel)` — mécanisme différent, pas juste un renommage de signal |

Total : **6 instanciations `Gtk.Menu()`**, **5 appels** à la signature
dépréciée à 6 arguments positionnels `.popup(None, None, None, None,
event.button, event.time)` (supprimée en GTK4, `Gtk.Menu` n'existant plus)
plus **1 appel-pont** distinct (`get_menu().popup(None, None, None, None, 0,
Gtk.get_current_event_time())`, qui ouvre `mnuCommands` en pop-up depuis une
action du menu hamburger — voir `_build_primary_menu`), et **48**
instanciations `Gtk.MenuItem`/`Gtk.CheckMenuItem`/`Gtk.SeparatorMenuItem`.
Chiffres verrouillés par `tests/test_gtk4_context_menus_baseline.py`
(nouveau, session-29) pour détecter toute dérive avant le portage réel.

**Difficultés identifiées qui dépassent un simple remplacement d'API** :

1. **États cochés** (`mnuLog` dans deux menus) : `Gtk.CheckMenuItem` n'a pas
   d'équivalent direct dans `Gio.Menu` — il faut une `Gio.SimpleAction` à
   état booléen (`Gio.SimpleAction.new_stateful()`) et un item de menu
   `Gio.MenuItem` construit avec une action ciblant ce state, changement
   de modèle plus que de simple appel.
2. **Contenu dynamique** (`populateCommandsMenu()`, items par plugin dans
   `popupMenuFolder`, visibilité conditionnelle dans `popupMenuTab`) :
   `Gio.Menu` reste mutable au runtime (`insert()`/`remove()`/
   `remove_all()`), donc faisable, mais chaque site d'appel qui fait
   aujourd'hui `.append(menuItem)`/`.show()`/`.hide()` doit être réécrit
   individuellement — ce n'est pas un remplacement mécanique en une passe.
3. **Déclenchement** : les 5+1 sites `.popup(None, None, None, None, ...)`
   sont tous armés depuis un gestionnaire d'évènement bouton
   (`Gdk.EventButton`, `event.button`/`event.time`) — GTK4 n'a plus
   d'évènements de ce type sur les widgets (modèle `Gtk.EventController`) ;
   chaque site doit migrer vers un `Gtk.GestureClick` dédié en amont, pas
   seulement changer la ligne du `.popup(`.
4. **`populate-popup` de `Gtk.Entry`** (item 5 du tableau) est un mécanisme
   à part, propre au widget d'édition inline des noms d'hôtes/groupes — à
   traiter séparément du reste, pas dans le même lot que les 4 menus
   applicatifs.

**Décision de cette session** : ne pas coder ce portage à l'aveugle vu son
entrelacement avec des choix de conception non triviaux (point 1 et 2
ci-dessus) — proposer d'abord un plan détaillé (probablement un menu par
session, `popupMenuTab` en premier candidat car le plus simple des quatre :
pas d'injection dynamique par plugin, seulement de la visibilité
conditionnelle) à valider avec l'auteure avant d'écrire du code, plutôt que
de découvrir ces difficultés en cours de portage sur un fichier aussi
central. Livré cette session : cet inventaire, et
`tests/test_gtk4_context_menus_baseline.py` (nouveau, 3 tests, verrouille
les comptages ci-dessus par scan textuel, même principe que
`test_gtk4_core_rupture_points.py`).

## 3.6-quater Portage effectif de `popupMenuTab` (session-31, 2026-09-11)

**Contexte** : premier des quatre menus contextuels identifiés par l'audit
session-29 à être réellement porté, conformément à l'ordre proposé (le plus
simple des quatre — pas d'injection dynamique par plugin, seulement de la
visibilité conditionnelle de `mnuReopen`/`mnuSplitH`/`mnuSplitV`).

**Ce qui a été porté** : `self.popupMenuTab` (`Gtk.Menu` classique avant
cette session, construit dans `createMenu()`) devient une instance de
`widgets.TabContextMenu`, nouvelle classe qui encapsule un trio
`Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.Popover` — même principe déjà en
place pour le menu principal (`Wmain._build_primary_menu`), donc du code
qui fonctionne dès maintenant sous GTK3 (`Gio.Menu`/`Gio.SimpleAction`
existent depuis GTK 3.4, `Gtk.Popover.new_from_model()` depuis GTK 3.12) et
sera repris tel quel lors du passage effectif à GTK4.

Réponses apportées aux deux premières difficultés de l'audit session-29 :

1. **États cochés** (point 1) : la case "Enable logging" devient une
   `Gio.SimpleAction.new_stateful()` booléenne. Le gestionnaire métier
   existant (`Wmain.on_popupmenu`, item `"L"`, inchangé) continue de lire/
   écrire l'état via `get_active()`/`set_active()` grâce à un petit adaptateur,
   `widgets._LogActionShim`, qui traduit ces appels vers
   `action.get_state()`/`action.set_state()`.
2. **Contenu dynamique / visibilité conditionnelle** (point 2, sous-cas
   `popupMenuTab` : pas d'injection par plugin ici, seulement du show/hide) :
   `Gio.Menu` restant mutable à l'exécution, `TabContextMenu.rebuild()`
   reconstruit entièrement le modèle à chaque ouverture (`remove_all()` puis
   réinsertion des sections pertinentes) plutôt que de cacher/afficher des
   `Gtk.MenuItem` persistants, qui n'existent plus. La disposition
   elle-même (quelles entrées apparaissent selon `is_active`/`multi_pane`)
   est calculée par une fonction pure, `build_tab_context_menu_layout()`
   dans le nouveau module `tab_context_menu_core.py` — zéro import
   `Gtk`/`Gio`, pour rester testable sans stub GTK (`CONSIGNES-AGENTS-IA.md`
   section 5), couverte par `tests/test_tab_context_menu_core.py` (10 tests).

**Ce qui n'a volontairement pas été touché** : le point 3 de l'audit
session-29 (déclenchement — migration du `button-press-event` sur
`Gtk.EventBox` vers `Gtk.GestureClick`) reste hors périmètre de cette
session. `Gtk.Popover.set_pointing_to()`/`popup()` sont appelés depuis le
même gestionnaire `NotebookTabLabel.popupmenu()` qu'avant, toujours câblé
sur le `Gdk.EventButton` existant — un chantier commun aux quatre menus
contextuels, à traiter séparément (probablement en une seule fois pour les
quatre, plutôt que quatre fois). Les trois autres menus (`popupMenu`,
`popupMenu.mnuCommands`, `popupMenuFolder`) restent en `Gtk.Menu` classique,
inchangés.

**Baseline mise à jour** : `tests/test_gtk4_context_menus_baseline.py`
ajusté (`CORE_FILE` : 4→3 `Gtk.Menu()`, `WIDGETS_FILE` : 2→1 appel
`.popup(None, None, None, None, ...)` — le second appel, `self.popup.popup(
...)`, devient `self.popup.popup_at(...)`), avec la même discipline de
verrouillage volontaire qu'avant pour les trois menus non encore portés.

**Reste à faire pour ce chantier** : `popupMenu` (12 items + sous-menu
"Custom commands" dynamique) et `popupMenuFolder` (items injectés par
plugin) restent les deux prochains candidats, dans un ordre à confirmer
avec l'auteure — `popupMenuFolder` a l'avantage d'un mécanisme d'injection
par plugin déjà standardisé ailleurs (`menu_actions()`, voir
`_build_primary_menu`), `popupMenu` porte en plus le sous-menu dynamique
"Custom commands" (`populateCommandsMenu()`, `foreach(...).remove(...)`).
Le point 3 (déclenchement `Gtk.GestureClick`) et le point 4 (`Gtk.Entry`
`populate-popup` → `set_extra_menu()`) restent également ouverts pour les
quatre menus.

## 3.6-quinquies Portage effectif de `popupMenuFolder` (session-32, 2026-09-11)

**Contexte** : deuxième des quatre menus contextuels identifiés par l'audit
session-29 à être réellement porté, après `popupMenuTab` (session-31,
§3.6-quater). Choisi comme candidat suivant car son mécanisme d'injection
dynamique par plugin (`plugin.build_folder_context_menu_items()`) se ramène
au même motif `add_action()`/section reconstruite déjà utilisé par
`Wmain._build_primary_menu` et par `TabContextMenu`, contrairement à
`popupMenu` qui porte en plus le sous-menu "Custom commands"
(`populateCommandsMenu()`, `foreach(...).remove(...)`) — un mécanisme
distinct non traité ici.

**Ce qui a été porté** : `self.popupMenuFolder` (`Gtk.Menu` classique avant
cette session, construit dans `createMenu()`) devient une instance de
`widgets.FolderContextMenu`, nouvelle classe qui encapsule le même trio
`Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.Popover` que `TabContextMenu`.

1. **Entrées statiques** (connect, add, new-group, rename-group,
   group-color, group-color-reset, edit, delete, duplicate, expand,
   collapse) : chacune devient une `Gio.SimpleAction` sans état, câblée
   soit directement sur la méthode `Wmain` existante (ex. `lambda:
   self.on_btnConnect_clicked(None)`, même motif que
   `_build_primary_menu`), soit relayée vers `Wmain.on_popupmenu` pour les
   deux entrées qui passaient déjà par ce dispatcher avant ce portage
   (`"H"` = Copy Address, `"D"` = Duplicate Host).
2. **Visibilité conditionnelle selon la cible du clic droit** (vide/
   dossier/hôte, ancien `pthinfo is None` / `iter_has_child()` de
   `on_tvServers_button_press_event`) : `Gio.Menu` restant mutable à
   l'exécution, `FolderContextMenu.rebuild()` reconstruit entièrement le
   modèle à chaque ouverture plutôt que de cacher/afficher des
   `Gtk.MenuItem` persistants. La disposition elle-même est calculée par
   une fonction pure, `build_folder_context_menu_layout()` dans le nouveau
   module `folder_context_menu_core.py` — zéro import `Gtk`/`Gio`, couverte
   par `tests/test_folder_context_menu_core.py` (8 tests).
3. **Contenu dynamique injecté par plugin** (`protocol_extra_items`,
   ancienne liste de `Gtk.MenuItem` construite une fois à la création du
   menu) : remplacé par un `protocol_items_provider` (callable fixé par
   `Wmain.createMenu`, évalué à chaque `rebuild()`) qui interroge
   `plugin_registry.all_sorted()` au moment de l'ouverture — le nombre et
   les libellés de ces items restent variables selon les plugins chargés,
   sans liste à tenir à jour séparément côté `Wmain`. Leur visibilité
   (section présente uniquement pour un nœud hôte) est calculée par
   `folder_context_menu_core.should_show_protocol_items()`, même principe
   que le reste de la disposition.

**Ce qui n'a volontairement pas été touché** : le point 3 de l'audit
session-29 (déclenchement — migration du `button-press-event` sur
`treeServers` vers `Gtk.GestureClick`) reste hors périmètre de cette
session, commun aux quatre menus contextuels. `popupMenu` (et son sous-menu
dynamique `mnuCommands`) reste en `Gtk.Menu` classique, inchangé.

**Baseline mise à jour** : `tests/test_gtk4_context_menus_baseline.py`
ajusté (`CORE_FILE` : 3→2 `Gtk.Menu()`, appels `.popup(None, None, None,
None, ...)` : 2→1 — `self.popupMenuFolder.popup(...)` devient
`self.popupMenuFolder.popup_at(...)`), avec la même discipline de
verrouillage volontaire qu'avant pour `popupMenu`, seul restant.

**Reste à faire pour ce chantier** : `popupMenu` (12 items + sous-menu
"Custom commands" dynamique) reste le seul candidat restant parmi les
quatre menus applicatifs. Le point 3 (déclenchement `Gtk.GestureClick`) et
le point 4 (`Gtk.Entry` `populate-popup` → `set_extra_menu()`) restent
également ouverts, communs aux quatre menus.

## 3.6-sexies Portage effectif de `popupMenu` (session-33, 2026-09-11)

**Contexte** : troisième et dernier des quatre menus contextuels
applicatifs identifiés par l'audit session-29 à être réellement porté,
après `popupMenuTab` (session-31, §3.6-quater) et `popupMenuFolder`
(session-32, §3.6-quinquies) — candidat naturel puisque seul restant de ce
sous-ensemble (voir §3.6-quinquies « Reste à faire »), sans nouvel
arbitrage d'ordre nécessaire.

**Ce qui a été porté** : `self.popupMenu` (`Gtk.Menu` classique avant
cette session, construit dans `createMenu()`) devient une instance de
`widgets.PopupMenu`, nouvelle classe qui encapsule le même trio
`Gio.Menu`/`Gio.SimpleActionGroup`/`Gtk.Popover` que `TabContextMenu`/
`FolderContextMenu`.

1. **Entrées statiques** (copy, paste, copy-paste, select-all, copy-all,
   save-buffer, split-h, split-v, unsplit, reset, clear, clone, close) :
   chacune devient une `Gio.SimpleAction` sans état, relayée vers l'unique
   `Wmain.on_popupmenu` avec son code d'action historique inchangé (`"C"`,
   `"V"`, …, `"RS2"`/`"RC2"`/`"CC2"` — suffixés `"2"` de longue date pour
   rester distinguables des codes `"RS"`/`"RC"`/`"CC"` de `popupMenuTab`
   côté ce même dispatcher). Recompte exact fait à cette occasion : 14
   entrées directes (dont la case "Enable logging" ci-dessous), pas 12
   comme l'indiquait l'audit session-29 (§3.6-ter) — écart mineur
   d'inventaire sans conséquence sur le portage, la cause la plus probable
   étant la réutilisation de l'attribut `self.popupMenu.mnuSelect` pour
   deux libellés différents ("Select all" puis "Save buffer to file") dans
   l'ancien code, un attribut jamais relu ailleurs (dispatch entièrement
   piloté par les codes, pas par ces attributs) et donc sans équivalent à
   reproduire ici — chaque action porte désormais son propre nom
   (`select-all`/`save-buffer`).
2. **Sensibilité variable de quatre actions** (copy, split-h, split-v,
   unsplit — ancien `.set_sensitive()` de `on_terminal_click`) :
   différence structurelle avec les deux menus précédents, dont c'est la
   *disposition* (présence/absence d'un item) qui variait. Ici la
   disposition (`popup_menu_core.POPUP_MENU_SECTIONS`) est **constante** ;
   seule la sensibilité change, calculée par la fonction pure
   `compute_enabled_actions()` (`popup_menu_core.py`, zéro `Gtk`/`Gio`,
   couverte par `tests/test_popup_menu_core.py`, 22 tests) et appliquée via
   `Gio.SimpleAction.set_enabled()` — écouté automatiquement par le
   `Gtk.Popover` construit via `new_from_model()`, sans reconstruire le
   modèle à chaque ouverture (`PopupMenu.rebuild()`, contrairement à
   `TabContextMenu.rebuild()`/`FolderContextMenu.rebuild()`, ne touche pas
   `self.model`).
3. **Case "Enable logging"** (ancien `Gtk.CheckMenuItem`, code `"L2"`) :
   même traitement que `popupMenuTab` (§3.6-quater) — `Gio.SimpleAction` à
   état booléen, `widgets._LogActionShim` **réutilisé tel quel** (même
   classe, pas de duplication) pour préserver l'interface
   `get_active()`/`set_active()` attendue par `Wmain.on_popupmenu`.
4. **Sous-menu dynamique "Custom commands"** (ancien
   `self.popupMenu.mnuCommands`, `Gtk.Menu` distinct attaché via
   `set_submenu()`, peuplé par `populateCommandsMenu()`) : seul mécanisme
   des quatre menus contextuels audités à être un véritable sous-menu
   imbriqué plutôt qu'une liste à plat ou une section — devient un
   `Gio.Menu` imbriqué via `append_submenu()` (mécanisme déjà utilisé sans
   difficulté par `Wmain._build_primary_menu` pour "Imports"/"Exports"/
   etc., donc pas une inconnue pour ce dépôt). `PopupMenu.populate_commands()`
   reproduit le `foreach(...).remove(...)` + reconstruction complète de
   l'ancien code, avec une différence volontaire : les actions
   `"command-N"` au-delà du nouveau compte sont explicitement retirées
   (`Gio.SimpleActionGroup.remove_action()`) plutôt que simplement
   laissées orphelines comme le fait la section "protocol" de
   `FolderContextMenu` — nécessaire ici car le nombre de commandes
   personnalisées peut réellement diminuer d'un appel à l'autre (édition
   des raccourcis dans les Préférences), alors que le nombre de plugins
   chargés ne varie pas en cours de session. Différence de rendu assumée
   et documentée : le libellé (`popup_menu_core.format_command_label()`)
   reste un texte brut "[raccourci] commande", sans la couleur bleue ni la
   taille réduite que portait l'ancien `Gtk.MenuItem.get_child().
   set_attributes()` en Pango — `Gio.MenuItem` n'a qu'un libellé texte, pas
   d'attributs par item.
5. **Pont "Custom commands" du menu hamburger** (ancien
   `add_popup_action("custom-commands", lambda: self.popupMenu.mnuCommands)`
   de `_build_primary_menu`, qui appelait `Gtk.Menu.popup()` directement) :
   `mnuCommands` n'existant plus comme widget autonome, remplacé par
   `PopupMenu.popup_commands_at()`, un second `Gtk.Popover` construit sur
   le même modèle `Gio.Menu` partagé (`self.commands_model`) — les deux
   présentations (sous-menu imbriqué et pop-up autonome) restent
   synchronisées puisqu'elles partagent modèle et groupe d'actions.
   `add_popup_action()`, devenu sans appelant, est retiré.
6. **Nettoyage** : `createMenuItem()` et `populateCommandsMenu()` (logique
   entièrement absorbée par `widgets.PopupMenu`) sont supprimés de
   `gnome_connection_manager.py` et consignés dans le bloc « Méthodes
   supprimées » existant, même discipline que pour
   `_inject_spice_frame_LEGACY` (session-28).

**Ce qui n'a volontairement pas été touché** : le point 3 de l'audit
session-29 (déclenchement — migration du `button-press-event` de
`on_terminal_click` vers `Gtk.GestureClick`) reste hors périmètre de cette
session, commun aux quatre menus contextuels — seule une réorganisation
mineure sans effet observable a eu lieu (calcul de `multi_pane`/
`has_split_notebook` remonté avant le `if`/`else` pour être disponible au
seul appel à `rebuild()`, voir la docstring de `on_terminal_click`). Le
point 4 (`Gtk.Entry`/`populate-popup`), le constructeur générique
RDP/VNC/SPICE de `widgets.py` (`build_remote_desktop_context_menu()`),
`plugins/ssh/gtk4.py` et RDP restent également non traités.

**Baseline mise à jour** : `tests/test_gtk4_context_menus_baseline.py`
ajusté — les trois compteurs de `CORE_FILE` tombent à **zéro**
(`Gtk.Menu()` : 2→0 ; appels `.popup(None, None, None, None, ...)` : 1→0,
`self.popupMenu.popup(...)` devenant `self.popupMenu.popup_at(...)` ;
pont "Custom commands" `get_menu().popup(...)` : 1→0, devenu
`popup_commands_at(...)`, vérifié par un nouveau test positif). Les quatre
menus contextuels applicatifs de l'audit session-29 sont désormais tous
portés vers `Gio.Menu` ; `WIDGETS_FILE` (constructeur générique RDP/VNC/
SPICE) reste inchangé, hors périmètre.

**Reste à faire pour ce chantier** : le déclenchement `Gtk.GestureClick`
(point 3, commun aux quatre menus contextuels désormais tous portés) et le
cas `Gtk.Entry`/`populate-popup` (point 4) restent ouverts. Au-delà des
menus contextuels proprement dits, `plugins/ssh/gtk4.py` (portage effectif
des widgets SSH) et RDP (plugin `plugins/rdp/{core.py,gtk4.py}`) restent
les autres chantiers GTK4 non commencés — ordre entre ces quatre éléments
toujours à confirmer avec l'auteure, voir `docs/features-backlog.md`.

## 3.6-septies Audit du déclenchement `Gtk.GestureClick` (point 3, session-38, 2026-09-16)

**Contexte** : le point 3 de l'audit session-29 (§3.6-ter) — migration du
déclenchement `button-press-event`/`button_press_event` sur
`Gdk.EventButton` vers `Gtk.GestureClick` — restait ouvert depuis, signalé
« hors périmètre » par chacune des trois sessions de portage effectif
(session-31 §3.6-quater, session-32 §3.6-quinquies, session-33 §3.6-sexies).
Contrairement à ces trois sessions, ce point n'avait encore jamais été
inventorié précisément ni ordonné dans le backlog par rapport aux trois
autres chantiers GTK4 restants (`plugins/ssh/gtk4.py`, RDP, ce point) —
`docs/features-backlog.md` le signale explicitement comme « ordre … à
confirmer avec l'auteure ». Conformément à `CONSIGNES-AGENTS-IA.md`
(« proposer un plan avant de coder un gros morceau ») et au même principe
que l'audit session-29 pour les menus eux-mêmes : **inventaire et plan
seulement cette session, aucun portage de code**, ce point touchant le
déclenchement d'entrée central de trois widgets différents (terminal VTE,
`treeServers`, étiquette d'onglet) sans qu'aucun environnement GTK3/VTE
réel ne soit disponible ici pour vérifier empiriquement le résultat.

**Inventaire exhaustif** (recherche `button.press.event`, hors
`SSH-Studio`/`gtk-frdp`) — 3 familles de sites réellement liées au
déclenchement d'un menu contextuel applicatif, sur les 7 occurrences
totales trouvées dans `gnome_connection_manager.py`/`widgets.py` :

| Site | Fichier:ligne(s) | Widget | Menu déclenché | Particularité |
|---|---|---|---|---|
| `v.connect("button_press_event", self.on_terminal_click)` | `gnome_connection_manager.py:2281` (onglet simple, `addTab`) et `:3201` (volet issu d'un split) | `Vte.Terminal` | `popupMenu` (clic droit, bouton 3) | Même gestionnaire pour les deux sites — un seul point de logique, deux points de connexion à faire évoluer ensemble. Gère aussi, dans le même `if`/`elif`, l'ouverture d'URL au Ctrl+clic gauche (bouton 1) via `widget.match_check_event(event)`/`hyperlink_check_event(event)`, sans rapport avec le menu mais sur le même signal |
| `treeServers.connect("button-press-event", self.on_tvServers_button_press_event)` | `gnome_connection_manager.py:1159` | `Gtk.TreeView` | `popupMenuFolder` (clic droit, bouton 3) | Le même gestionnaire distingue aussi le double/triple-clic (boutons gauches) pour laisser l'événement se propager (édition inline) — logique la plus simple des trois candidats, aucune branche dépendant d'un modificateur clavier |
| `self.eb.connect("button-press-event", self.popupmenu, label)` | `widgets.py:1172` (`NotebookTabLabel.__init__`) | `Gtk.EventBox` (étiquette d'onglet) | `popupMenuTab` **ou** le menu `Gtk.Menu` classique encore non porté de `build_remote_desktop_context_menu()` (`widgets.py::NotebookTabLabel.popupmenu`, selon que l'onglet est un terminal ou un bureau distant RDP/VNC/SPICE) | Seul des trois sites à alimenter un menu déjà porté (`TabContextMenu`) **et** un menu qui ne l'est pas encore (RDP/VNC/SPICE) selon une branche `if remote_widget is not None` — migrer ce déclencheur sans casser la branche legacy demande de continuer à fournir un `Gdk.Event`/bouton/temps compatible à `remote_menu.popup(...)`, pas seulement à l'appel déjà porté `popup_at()` |

**Sites textuellement similaires mais hors périmètre de ce point**
(exclus de l'inventaire ci-dessus, à ne pas confondre lors d'un futur
comptage) :
- `hpMain.connect("button-press-event", self.on_hpMain_button_press_event)`
  (`:1152`) : bascule d'affichage du panneau au double-clic sur le
  séparateur — ne déclenche aucun menu.
- `nbConsole.connect("button-press-event", self.on_double_click)` (`:1168`)
  et `nb.connect("button_press_event", self.on_double_click, None)`
  (`:4039`) : ouverture d'un onglet local au double/triple-clic dans la
  zone vide du notebook — ne déclenche aucun menu.
- `editor.connect("button-press-event", self._on_editor_pressed)`
  (`widgets.py:1689`) : contournement d'un bug GTK (`_on_editor_pressed`
  retourne `True` sans condition pour éviter une assertion), sans rapport
  avec `populate-popup`/le point 4 ni avec un menu contextuel.

**`GtkGestureMultiPress` (GTK3) → `GtkGestureClick` (GTK4) — vérifié
auprès de la documentation officielle GTK (`docs.gtk.org`,
`blog.gtk.org`)** : il s'agit majoritairement d'un renommage plutôt que
d'une API nouvelle — `GtkGestureMultiPress` existe déjà en GTK3 (depuis la
3.14) avec le même signal `pressed(gesture, n_press, x, y)`, et la
documentation de migration officielle 3→4 ne liste que deux différences :
le renommage lui-même et la suppression de la propriété `area`
(restriction de zone de clic, non utilisée dans ce dépôt). Ceci ouvre une
voie de migration **compatible dès aujourd'hui en GTK3**, sans attendre le
portage du cœur : remplacer chaque `widget.connect("button[-_]press-event",
handler)` par un `Gtk.GestureMultiPress.new(widget)` + `gesture.connect
("pressed", handler)`, ce qui produira un code quasi identique à sa forme
GTK4 finale (renommage de classe uniquement lors du basculement réel).

**Difficulté non triviale identifiée, au-delà du renommage de classe** :
le signal `pressed` ne transmet que `(gesture, n_press, x, y)` — ni objet
`Gdk.EventButton`, ni numéro de bouton, ni état des modificateurs
clavier. Les trois gestionnaires actuels lisent pourtant `event.button`
(distinguer clic droit/gauche), et `on_terminal_click` lit en plus
`event.get_state() & Gdk.ModifierType.CONTROL_MASK` (Ctrl+clic) et passe
`event` tel quel à `widget.match_check_event(event)`/
`hyperlink_check_event(event)` (API VTE attendant un objet événement). La
migration doit donc, pour chaque site : (a) fixer le bouton écouté via
`Gtk.GestureSingle.set_button()` sur le geste plutôt que de le tester dans
le handler (un geste par bouton, ou lecture de
`gesture.get_current_button()`) ; (b) récupérer l'état des modificateurs
via `gesture.get_current_event_state()` (disponible sur
`Gtk.EventController` depuis GTK3) pour le test Ctrl+clic ; (c) obtenir
l'objet `Gdk.Event` d'origine via `gesture.get_current_event()`
(également disponible en GTK3) pour les deux appels VTE et pour
`remote_menu.popup(None, None, None, None, event.button, event.time)`
côté `widgets.py` — aucun de ces trois points n'est un simple
remplacement mécanique de signature, contrairement au renommage de
classe lui-même.

**Proposition d'ordre** (à confirmer avec l'auteure, même discipline que
l'ordre proposé par la session-29 pour les quatre menus eux-mêmes,
§3.6-ter) : `treeServers`/`popupMenuFolder` en premier — seul des trois
sites sans branche dépendant d'un modificateur clavier (b, ci-dessus) ni
de menu non porté (le cas mixte de `widgets.py:1172`) — puis
`on_terminal_click` (branche Ctrl+clic à porter, mais menu déjà
entièrement porté), et l'étiquette d'onglet (`widgets.py:1172`) en
dernier puisqu'elle est seule à devoir continuer à alimenter le menu
`Gtk.Menu` legacy des bureaux distants tant que celui-ci n'est pas
lui-même porté.

**Livré cette session** : cet inventaire, la vérification de la relation
`GtkGestureMultiPress`/`GtkGestureClick` auprès de la documentation
officielle, et un verrou de comptage (`tests/test_gtk4_context_menus_baseline.py`,
nouvelle classe `TestGestureClickTriggerBaseline`) sur les 3 familles de
sites recensées ci-dessus, pour détecter toute dérive avant le portage
réel — même principe que les compteurs déjà en place dans ce fichier pour
`Gtk.Menu()`/`.popup(...)`. Aucun portage de code cette session.

## 3.6-octies Portage effectif de `treeServers`/`popupMenuFolder` (session-39, 2026-09-17)

**Contexte** : premier des trois sites inventoriés par l'audit session-38
(§3.6-septies) à être effectivement porté, conformément à la proposition
d'ordre posée par cette même session — `treeServers`/`popupMenuFolder`
retenu en premier car seul des trois sans branche dépendant d'un
modificateur clavier. Même discipline que le passage de l'audit session-29
au premier portage effectif (session-31, `popupMenuTab`).

**⚠️ Correction apportée à l'audit session-38** : la vérification
indépendante de cette session (`docs.gtk.org`/`gtk4-rs`, méthode par
méthode) montre que `gtk_event_controller_get_current_event()` et
`gtk_event_controller_get_current_event_state()`, présentées par l'audit
session-38 comme « disponibles sur `Gtk.EventController` depuis GTK3 »,
sont en réalité des ajouts **GTK4 uniquement** (absentes des listes de
méthodes de `Gtk.EventController`/`Gtk.Gesture` en GTK3 sur
`docs.gtk.org`/pgi-docs, présentes uniquement sur les pages GTK4
correspondantes). L'équivalent GTK3 pour retrouver l'évènement
`Gdk.Event` d'origine pendant l'interprétation d'un geste est
`Gtk.Gesture.get_last_event(sequence)` (hérité de `Gtk.Gesture`, disponible
depuis 3.14), qui prend en argument une `Gdk.EventSequence` — ou `None`
pour les évènements pointeur/souris sans identifiant de séquence propre.
Cette correction ne change rien au portage de `treeServers` ci-dessous
(qui n'a besoin ni de l'évènement complet ni de l'état des modificateurs,
voir plus bas), mais **doit être appliquée** lors du portage des deux
sites restants (`on_terminal_click`, qui lit l'état Ctrl+clic et repasse
l'évènement à VTE, et `NotebookTabLabel.popupmenu`, qui repasse l'évènement
à `remote_menu.popup()`) : utiliser `gesture.get_last_event(None)`, jamais
`get_current_event()`/`get_current_event_state()`.

**Conception retenue pour ce site** :

- **Bouton** : `Gtk.GestureSingle:button` vaut `1` (`GDK_BUTTON_PRIMARY`)
  par défaut — vérifié sur `docs.gtk.org`/le source `gtkgesturesingle.c`
  (`priv->button = GDK_BUTTON_PRIMARY` dans `gtk_gesture_single_init`),
  et non 0 comme on pourrait le supposer par analogie avec la sémantique
  « tous boutons » de `set_button(0)`. Comme l'ancien gestionnaire traite
  *tous* les boutons dans une seule fonction (clic droit pour le menu,
  tout bouton pour le double/triple-clic à avaler), le geste est configuré
  avec `set_button(0)` puis le bouton réel est lu à chaque pression via
  `gesture.get_current_button()` (`Gtk.GestureSingle`, disponible depuis
  3.14) plutôt que restreint à la construction — seconde des deux options
  posées par l'audit session-38.
- **Décision pure extraite** : `gesture_trigger_core.classify_tree_servers_press(button, n_press)`
  reproduit les deux branches de l'ancien `if`/`else`
  (`button == 3 and n_press == 1` → ouvrir le menu ; `n_press >= 2` → avaler ;
  sinon → laisser propager), testée sans stub GTK
  (`tests/test_gesture_trigger_core.py`, 7 tests).
- **Phase de propagation** : `Gtk.PropagationPhase.TARGET` fixée
  explicitement (`set_propagation_phase`), et non la valeur par défaut
  `BUBBLE`. D'après `docs.gtk.org` (`class.Gesture.html`) : après la phase
  de capture, GTK émet les signaux traditionnels
  (`button-press-event`…) et *seuls* les gestes en phase `TARGET` sont
  nourris depuis les gestionnaires par défaut du widget à ce même point —
  les gestes en phase `BUBBLE` (le défaut) ne reçoivent les évènements
  **qu'après**, une fois remontés depuis le widget cible. La phase
  `TARGET` est donc le point d'entrée le plus proche de l'ancien
  `.connect("button-press-event", …)` non-`_after`, qui s'exécutait avant
  le gestionnaire de classe par défaut de `Gtk.TreeView`.
- **Équivalent de l'ancien `return True`/`False`** : le signal `pressed`
  ne retourne rien (`void`, contrairement à `gboolean` pour
  `button-press-event`) — la seule façon d'obtenir un effet équivalent
  est `gesture.set_state(Gtk.EventSequenceState.CLAIMED)`, appelé pour les
  issues `"open-menu"` et `"swallow"` (anciennement `return True`) ; rien
  n'est appelé pour `"propagate"` (état par défaut `NONE`, anciennement
  `return False`).
- **`FolderContextMenu.popup_at()`** (`widgets.py`) : signature changée de
  `(relative_to, event)` à `(relative_to, x, y)` — le signal `pressed`
  fournit déjà `x`/`y` séparément, sans objet évènement associé, et cette
  méthode n'utilisait de toute façon que `event.x`/`event.y`. Seul
  appelant dans tout le dépôt (`Wmain.on_tvServers_pressed`), donc
  changement sans impact ailleurs.
- **Référence du geste conservée** : `self._tree_servers_press_gesture`
  sur `Wmain`. PyGObject ne maintient pas la `Gtk.GestureMultiPress` en vie
  au-delà de la portée de la méthode qui la construit (contrairement à
  `gtk_widget_add_controller()` en GTK4, qui prend possession du
  contrôleur — voir l'exemple du blog GTK cité par l'audit session-38) ;
  sans référence Python conservée, le geste serait probablement collecté
  peu après la construction de la fenêtre et cesserait silencieusement de
  fonctionner.

**⚠️ Non vérifié empiriquement dans cet environnement de travail** (GTK3/VTE
absents, voir la mise en garde standard de `CLAUDE.md`) : l'interaction
précise entre `Gtk.PropagationPhase.TARGET` + `Gtk.EventSequenceState.CLAIMED`
sur un geste attaché directement à `treeServers`, et le gestionnaire de
classe par défaut de `Gtk.TreeView` (sélection de ligne au clic simple,
activation de ligne — `row-activated` — au double-clic). La documentation
officielle confirme que les gestes en phase `TARGET` sont nourris « depuis
les gestionnaires par défaut du widget », mais ne précise pas si `CLAIMED`
empêche ensuite le traitement interne propre à `Gtk.TreeView` de s'exécuter,
ni si la séquence ainsi revendiquée à la pression affecte le relâchement
correspondant (`button-release-event`) — dont `row-activated` pourrait bien
dépendre, ce qui expliquerait pourquoi l'ancien code fonctionnait déjà
malgré son `return True` sur les évènements `_2BUTTON_PRESS`/`_3BUTTON_PRESS`
(hypothèse plausible mais non confirmée par le code source de GTK, non
consulté en détail cette session). **À vérifier manuellement avant mise en
production** : clic droit ouvre bien le menu (avec sélection/focus corrects) ;
clic gauche simple sélectionne bien une ligne normalement ; double-clic
gauche connecte bien via `row-activated` comme avant ce portage.

**Livré cette session** : `gesture_trigger_core.py` (nouveau module,
destiné à accueillir aussi la classification des deux sites restants lors
de leur portage), `tests/test_gesture_trigger_core.py` (7 tests),
`Wmain.on_tvServers_pressed` (remplace `on_tvServers_button_press_event`),
`FolderContextMenu.popup_at()` adapté, compteur
`EXPECTED_GESTURECLICK_TRIGGER_SITES` de
`tests/test_gtk4_context_menus_baseline.py` mis à jour (site treeServers
tombé à zéro) et complété d'un test positif
(`test_tree_servers_trigger_uses_gesture`). **Reste** : `on_terminal_click`
(2 sites) et `NotebookTabLabel.popupmenu` — utiliser
`gesture.get_last_event(None)` pour ces deux sites (voir correction
ci-dessus), pas `get_current_event()`/`get_current_event_state()`.

## 3.6-nonies Portage effectif du déclenchement `on_terminal_click` (session-40, 2026-09-17)

**Contexte** : deuxième des trois sites inventoriés par l'audit
session-38 (§3.6-septies) à être effectivement porté, conformément à la
proposition d'ordre posée par cette même session et reprise sans la
retrancher — `on_terminal_click` après `treeServers`/`popupMenuFolder`
(session-39, §3.6-octies), avant l'étiquette d'onglet (seule à alimenter
aussi le menu RDP/VNC/SPICE encore non porté, donc laissée pour la fin).

**Différences structurelles avec `treeServers`**, qui ont conditionné la
conception ci-dessous :

1. **Un troisième cas, absent de `treeServers`** : Ctrl+clic gauche simple
   (`button == 1 and event.get_state() & Gdk.ModifierType.CONTROL_MASK`)
   déclenche une détection d'URL (`widget.match_check_event(event)` /
   `widget.hyperlink_check_event(event)`) sans rapport avec un menu
   contextuel. La fonction pure extraite,
   `gesture_trigger_core.classify_terminal_click()`, prend donc un
   paramètre supplémentaire `ctrl_pressed` (booléen, calculé par
   l'appelant à partir de l'évènement — voir point 2) en plus de
   `button`/`n_press`, et un paramètre `paste_on_right_click` (valeur de
   `conf.PASTE_ON_RIGHT_CLICK` au moment du clic, passée explicitement
   plutôt que lue sur le global `conf`, conformément à
   `CONSIGNES-AGENTS-IA.md` section 2) pour choisir entre `"paste"` et
   `"open-menu"` sur clic droit.
2. **Correction session-39 appliquée dès la conception** (pas découverte
   en cours de route comme pour `treeServers`) : l'état des modificateurs
   clavier et l'évènement complet (nécessaire à
   `match_check_event()`/`hyperlink_check_event()`, qui attendent un
   `Gdk.Event` réel) sont obtenus via `gesture.get_last_event(None)`, pas
   `get_current_event()`/`get_current_event_state()` (absents de GTK3,
   voir §3.6-octies). Si l'évènement renvoyé est `None` (cas limite non
   observé mais possible d'après la documentation PyGObject — aucune
   séquence active), la branche `"check-url"` est ignorée sans lever
   d'exception plutôt que de planter sur un `None.get_state()`.
3. **Aucune issue `"swallow"`** : contrairement à `treeServers`, l'ancien
   `on_terminal_click` ne testait que `event.type ==
   Gdk.EventType.BUTTON_PRESS`, jamais `_2BUTTON_PRESS`/`_3BUTTON_PRESS` —
   les pressions multiples n'étaient donc jamais interceptées et
   tombaient déjà, avant ce portage, dans le traitement par défaut de
   `Vte.Terminal` (sélection native d'un mot au double-clic, d'une ligne
   au triple-clic). Reproduire un `"swallow"` ici casserait cette
   sélection native — `classify_terminal_click()` renvoie donc
   `"propagate"` pour tout `n_press >= 2`, quel que soit le bouton,
   **sans** appeler `gesture.set_state(CLAIMED)` dans ce cas côté
   appelant, à la différence de `on_tvServers_pressed`.
4. **Deux sites de connexion identiques, widget créé dynamiquement** :
   contrairement à `treeServers` (un seul widget, statique, référence de
   geste conservée sur `self`), chaque terminal est créé à la volée (un
   par onglet, aux deux endroits historiques listés par l'audit
   session-38, `gnome_connection_manager.py:2281` et `:3201`). Une
   `Gtk.GestureMultiPress` par widget global ne conviendrait pas ; une
   méthode d'attache partagée,
   `Wmain.attach_terminal_click_gesture(widget)`, construit et configure
   le geste (mêmes réglages que `treeServers` :
   `set_button(0)`/`set_propagation_phase(TARGET)`) pour chaque terminal,
   et conserve la référence **sur le widget lui-même**
   (`widget._terminal_click_gesture`) plutôt que sur `self` : sa durée de
   vie suit ainsi celle du terminal (collectée à la fermeture de
   l'onglet) au lieu de s'accumuler sans limite sur `Wmain` au fil des
   connexions successives. Les deux sites historiques appellent
   maintenant `self.attach_terminal_click_gesture(v)` au lieu de
   `v.connect("button_press_event", self.on_terminal_click)`.
5. **Widget explicite dans la connexion du signal** : `Gtk.GestureSingle`
   n'expose pas de méthode pour retrouver son widget surveillé depuis
   l'intérieur d'un gestionnaire GTK3 (à la différence de
   `self._tree_servers_press_gesture`, qui n'a de toute façon besoin que
   d'un seul widget connu à l'avance, `treeServers`) — le terminal est
   donc passé explicitement en argument supplémentaire de `connect()`
   (`gesture.connect("pressed", self.on_terminal_pressed, widget)`),
   récupéré tel quel par `Wmain.on_terminal_pressed(gesture, n_press, x,
   y, widget)`.

**`widgets.PopupMenu.popup_at()`** adapté comme
`FolderContextMenu.popup_at()` en session-39 : signature changée de
`(relative_to, event)` à `(relative_to, x, y)`, le signal `pressed`
fournissant déjà ces coordonnées séparément et cette méthode n'utilisant
de toute façon que `event.x`/`event.y`. Seul appelant dans tout le dépôt
(`Wmain.on_terminal_pressed`), donc changement sans impact ailleurs —
vérifié par recherche textuelle avant modification.

**Équivalent de l'ancien `return True`** (qui empêchait le code de mise
au focus en fin de fonction de s'exécuter après clic droit) : un `return`
explicite après les issues `"paste"`/`"open-menu"` dans
`on_terminal_pressed`, avant d'atteindre le bloc de mise au focus toujours
exécuté en fin de fonction — même structure que l'ancien `if`/`elif` avec
`return True` sur la première branche seulement.

**⚠️ Non vérifié empiriquement dans cet environnement de travail** (GTK3/VTE
absents, voir la mise en garde standard de `CLAUDE.md`), dans la continuité
de la réserve déjà posée pour `treeServers` (§3.6-octies) : l'interaction
entre `Gtk.PropagationPhase.TARGET` + le geste attaché directement au
terminal, et la gestion interne de `Vte.Terminal` (sélection de texte au
double/triple-clic, dont on affirme ci-dessus qu'elle continue de
fonctionner nativement puisque `classify_terminal_click()` ne revendique
jamais la séquence dans ce cas). À confirmer manuellement avant mise en
production : clic droit ouvre bien le menu (ou colle, selon
`PASTE_ON_RIGHT_CLICK`) ; Ctrl+clic gauche sur une URL/adresse l'ouvre
toujours dans le navigateur ; double-clic sélectionne bien un mot,
triple-clic une ligne, comme avant ce portage ; le focus du terminal se
fait toujours correctement après un clic simple sans modificateur.

**Livré cette session** : `gesture_trigger_core.classify_terminal_click()`
(nouvelle fonction dans le module créé en session-39), 8 nouveaux tests
dans `tests/test_gesture_trigger_core.py` ; `Wmain.on_terminal_click`
remplacé par `Wmain.attach_terminal_click_gesture()` +
`Wmain.on_terminal_pressed()` (2 sites d'appel mis à jour) ;
`widgets.PopupMenu.popup_at()` adapté ; compteur
`EXPECTED_GESTURECLICK_TRIGGER_SITES` de
`tests/test_gtk4_context_menus_baseline.py` mis à jour (site
`on_terminal_click` tombé à zéro) et complété d'un test positif
(`test_terminal_click_trigger_uses_gesture`). **Reste** :
`NotebookTabLabel.popupmenu` (étiquette d'onglet, seul des trois sites de
l'audit session-38 non encore porté) et le cas `Gtk.Entry`/`populate-popup`,
hors périmètre de l'audit session-38 (menu natif, pas un menu applicatif).

## 3.6-decies Portage effectif du déclenchement de l'étiquette d'onglet (session-41, 2026-09-17)

**Contexte** : troisième et dernier des trois sites inventoriés par
l'audit session-38 (§3.6-septies) à être effectivement porté —
`NotebookTabLabel.popupmenu` (`widgets.py`), après `treeServers`/
`popupMenuFolder` (session-39, §3.6-octies) et `on_terminal_click`
(session-40, §3.6-nonies). Laissé pour la fin conformément à la
proposition d'ordre posée dès la session-38 : c'est le seul des trois
sites à alimenter, sur la même issue de clic droit, deux menus
distincts selon le contenu de l'onglet.

**Différences structurelles avec les deux sites précédents**, qui ont
conditionné la conception ci-dessous :

1. **Vit dans `widgets.py`, pas dans `Wmain`** : à la différence de
   `treeServers` et `on_terminal_click` (déclencheur et gestionnaire tous
   deux méthodes de `Wmain`, dans `gnome_connection_manager.py`), le
   déclencheur ET le gestionnaire de ce site sont des méthodes de
   `NotebookTabLabel` elle-même. Conséquence pratique : pas besoin d'une
   méthode d'attache partagée comme
   `Wmain.attach_terminal_click_gesture()` (session-40) — le geste est
   construit une seule fois, directement dans `NotebookTabLabel.__init__`,
   et la référence conservée sur `self` (`self._label_click_gesture`) y
   suffit déjà : `NotebookTabLabel` est déjà une instance créée une fois
   par onglet (comme un `Vte.Terminal`), donc `self` y joue exactement le
   rôle que jouait le widget terminal lui-même en session-40
   (`widget._terminal_click_gesture`), sans détour par un widget externe
   passé en argument.
2. **Aucune branche modificateur clavier** : comme `treeServers`, aucun
   test de `Gdk.ModifierType` dans l'ancien code —
   `gesture_trigger_core.classify_tab_label_click()` n'a donc besoin que
   de `button`/`n_press`, sans le paramètre `ctrl_pressed` qu'a dû
   ajouter `classify_terminal_click()`.
3. **Aucune issue `"swallow"`** : comme `on_terminal_click`, l'ancien
   `popupmenu` ne testait que `event.type == Gdk.EventType.BUTTON_PRESS`,
   jamais `_2BUTTON_PRESS`/`_3BUTTON_PRESS` — `classify_tab_label_click()`
   renvoie donc `"propagate"` pour tout `n_press >= 2`, quel que soit le
   bouton, comme pour le terminal et contrairement à `treeServers`.
4. **Une même issue `"open-menu"` recouvre deux menus distincts** :
   l'ancien code choisissait, sur clic droit, entre le menu `Gtk.Menu`
   legacy du bureau distant (`remote_widget.build_context_menu()`, si
   l'onglet est un RDP/VNC/SPICE) et `TabContextMenu` (sinon). Ce choix
   dépend d'un état d'instance
   (`NotebookTabLabel._get_remote_desktop_widget()`) inaccessible à une
   fonction pure — `classify_tab_label_click()` ne distingue donc que le
   *déclenchement* (`"open-menu"`), laissant la *destination* à
   `on_label_pressed()`, le nouveau gestionnaire.
5. **Le menu du bureau distant reste un `Gtk.Menu` legacy, non porté** :
   sa méthode `.popup()` attend toujours la signature à six arguments
   positionnels historique
   (`popup(parent_menu_shell, parent_menu_item, func, data, button,
   activate_time)`), qui a besoin d'un numéro de bouton et d'un
   horodatage — deux informations qu'un `Gdk.EventButton` fournissait
   directement (`event.button`, `event.time`) mais qu'un signal `pressed`
   de `Gtk.GestureMultiPress` ne transmet pas sous cette forme. Plutôt que
   de dépendre de `gesture.get_last_event(None)` (dont la disponibilité et
   la forme exacte du champ temps ne sont pas garanties pour un usage qui
   n'a par ailleurs besoin d'aucun autre champ de l'évènement — pas de
   modificateur clavier, pas de coordonnées), le bouton est obtenu via
   `gesture.get_current_button()` (déjà calculé pour la classification) et
   l'horodatage via `Gtk.get_current_event_time()` — idiome déjà présent
   ailleurs dans ce dépôt (`Wmain.on_terminal_pressed`, pour
   `Gtk.show_uri()`) et qui évite cette dépendance. Ce menu du bureau
   distant reste, comme avant cette session, hors du périmètre du portage
   `Gio.Menu`/`Gtk.Popover` engagé en session-31 (§3.6-quater) : seul son
   *déclenchement* est porté ici, pas son *contenu*.
6. **Asymétrie de `Gtk.EventSequenceState.CLAIMED` reproduite fidèlement**,
   à la différence du choix plus prudent (jamais de `CLAIMED`) fait pour
   le terminal en session-40 : l'ancien code retournait `True` sur clic
   droit (les deux sous-branches, menu distant et `TabContextMenu`) et sur
   un clic milieu annulé par confirmation
   (`conf.CONFIRM_ON_CLOSE_TAB_MIDDLE` + `msgconfirm()` différent de
   `Gtk.ResponseType.OK`), mais **pas** quand l'onglet était effectivement
   fermé (chute en fin de fonction après `self.close_tab(...)`, sans
   `return`). `on_label_pressed()` appelle donc
   `gesture.set_state(Gtk.EventSequenceState.CLAIMED)` dans les deux
   premiers cas et pas dans le troisième — voir la docstring de
   `classify_tab_label_click()` pour le détail, cette asymétrie ne pouvant
   pas être repliée dans la fonction pure puisqu'elle dépend du résultat
   de `msgconfirm()`, connu seulement à l'exécution.

**`widgets.TabContextMenu.popup_at()`** adaptée comme
`FolderContextMenu.popup_at()`/`PopupMenu.popup_at()` en sessions 39/40 :
signature changée de `(relative_to, event)` à `(relative_to, x, y)`. Seul
appelant dans tout le dépôt (`NotebookTabLabel.on_label_pressed`), donc
changement sans impact ailleurs — vérifié par recherche textuelle avant
modification.

**⚠️ Non vérifié empiriquement dans cet environnement de travail** (GTK3
absent, voir la mise en garde standard de `CLAUDE.md`), dans la continuité
de la réserve déjà posée pour `treeServers` (§3.6-octies) et le terminal
(§3.6-nonies) : à confirmer manuellement avant mise en production — clic
droit sur un onglet SSH/série ouvre bien `TabContextMenu` au point de
clic ; clic droit sur un onglet RDP/VNC/SPICE ouvre bien le menu du
bureau distant (pas `TabContextMenu`) ; clic milieu ferme bien l'onglet,
avec confirmation si `CONFIRM_ON_CLOSE_TAB_MIDDLE` est actif et annulation
possible ; le changement d'onglet au clic gauche continue de fonctionner
normalement (mécanisme propre de `Gtk.Notebook`, non affecté par ce
portage puisque `classify_tab_label_click()` renvoie `"propagate"` pour
le bouton 1).

**Livré cette session** :
`gesture_trigger_core.classify_tab_label_click()` (nouvelle fonction dans
le module créé en session-39, avec la constante
`TAB_LABEL_CLICK_OUTCOMES`), 6 nouveaux tests dans
`tests/test_gesture_trigger_core.py` ; `NotebookTabLabel.popupmenu`
remplacé par `NotebookTabLabel.on_label_pressed()`, geste construit
directement dans `__init__` ; `widgets.TabContextMenu.popup_at()`
adaptée ; compteur `EXPECTED_GESTURECLICK_TRIGGER_SITES` de
`tests/test_gtk4_context_menus_baseline.py` mis à jour (site de
l'étiquette d'onglet tombé à zéro) et complété d'un test positif
(`test_tab_label_trigger_uses_gesture`). **Les trois sites de l'audit
session-38 (point 3) sont désormais tous portés.** Reste, hors périmètre
de cet audit (menu natif, pas un menu applicatif) : le cas
`Gtk.Entry`/`populate-popup` — **terminologie corrigée en session-42, voir
§3.6-undecies ci-dessous : ce n'est pas un `Gtk.Entry`.**

## 3.6-undecies Audit du point 4 — éditeur inline `CellTextView` (session-42, 2026-09-18)

**Contexte** : dernier point resté ouvert de l'audit historique des menus
contextuels (session-29, §3.6-ter, item 5 du tableau), désigné dans toute
la documentation jusqu'ici comme « le cas `Gtk.Entry`/`populate-popup` ».
Avant de le porter, vérification du code réel (comme pratiqué
systématiquement dans ce dépôt avant tout portage, cf. audits sessions
28/38) : le widget concerné, `widgets.py::MultilineCellRenderer.
_on_editor_populate_popup`, ne s'applique **pas** à un `Gtk.Entry` mais à
`CellTextView`, une sous-classe de `Gtk.TextView` (+ `Gtk.CellEditable`) —
l'éditeur inline utilisé pour renommer un hôte/dossier par double-clic
dans `treeServers`. Écart de documentation corrigé ici (le nom du widget
dans le tableau §3.6-ter était donc inexact depuis la session-29 ;
`Gtk.Entry`/`GtkText` n'apparaît nulle part dans ce chemin de code).

**Périmètre réel plus large que documenté** : `CellTextView.
do_start_editing()` (appelée au double-clic sur une cellule éditable de
`treeServers`) connecte non pas un seul signal GTK3-only mais quatre, sur
quatre lignes consécutives (`widgets.py:1819-1822`) :

| Signal (GTK3) | Gestionnaire | Rôle | Équivalent GTK4 |
|---|---|---|---|
| `focus-out-event` | `_on_editor_focus_out_event` | Annule l'édition si le focus est perdu — sauf si `_in_editor_menu` (menu contextuel natif ouvert) | `Gtk.EventControllerFocus` (signal `leave`) |
| `key-press-event` | `_on_editor_key_press_event` | Valide (Entrée) ou annule (Échap) l'édition | `Gtk.EventControllerKey` (signal `key-pressed`) |
| `populate-popup` | `_on_editor_populate_popup` | Positionne `_in_editor_menu = True` avant l'ouverture du menu contextuel natif (couper/copier/coller), remis à `False` à sa fermeture (`unmap`) — **n'ajoute aucun item**, contrairement à l'usage habituel de ce signal | **Supprimé en GTK4** pour `Gtk.TextView`/`Gtk.Text` — remplacé par un mécanisme différent, `set_extra_menu(Gio.MenuModel)`, qui sert à *ajouter* des items, pas à observer l'ouverture/fermeture du menu natif existant (vérifié sur `docs.gtk.org`/`valadoc.org`) |
| `button-press-event` | `_on_editor_pressed` | Retourne toujours `True` pour éviter un bug connu (`gtk_text_mark_get_buffer: assertion 'GTK_IS_TEXT_MARK (mark)' failed`) | `Gtk.GestureClick` — déjà catalogué comme site « décoy » du point 3 dans `tests/test_gtk4_context_menus_baseline.py::EXPECTED_GESTURECLICK_DECOY_SITES` (décoy vis-à-vis du *déclenchement de menu contextuel applicatif*, pas vis-à-vis de la migration GTK4 de ce signal lui-même, qui reste entière) |

**Difficulté non résolue par la recherche de cette session** :
`populate-popup` n'a, en GTK4, aucun signal de remplacement direct pour
son usage ici — observer l'ouverture/fermeture du menu contextuel
*natif*, généré automatiquement par le widget, sans y ajouter d'item.
`set_extra_menu()` répond à un besoin différent. Question de fond non
tranchable ici sans GTK4 réel dans cet environnement de travail : le
mécanisme GTK4 du menu contextuel natif (un `Gtk.Popover` interne attaché
au widget via `set_parent()`, plutôt qu'un `Gtk.Menu` top-level séparé
comme en GTK3) déclenche-t-il, à son ouverture, un `leave` sur
`Gtk.EventControllerFocus` du widget d'édition, comme le faisait l'ancien
`Gtk.Menu` avec `focus-out-event` ? Plusieurs sources trouvées en
recherchant ce point suggèrent qu'un `Gtk.Popover` attaché ne vole pas
nécessairement le focus clavier de son widget porteur de la même façon
qu'un ancien menu top-level — si c'est le cas ici, le contournement
`_in_editor_menu` pourrait devenir inutile en GTK4 (simplification
bienvenue), mais cela ne peut être confirmé que manuellement, sur un GTK4
réel, pas dans cet environnement de travail.

**Décision de cette session** : ne pas coder ce portage à l'aveugle, pour
deux raisons cumulées avec celles déjà invoquées en session-29 pour les
menus applicatifs — (1) le périmètre réel (4 signaux, 3 mécanismes de
remplacement distincts) dépasse largement ce que documentait §3.6-ter
(une seule ligne de tableau, présentée comme un simple renommage de
signal) ; (2) le point le plus délicat (`populate-popup`) dépend d'un
comportement d'exécution invérifiable empiriquement ici, et une réponse
incorrecte casserait silencieusement le renommage inline — fonctionnalité
de base très utilisée (double-clic sur un hôte/dossier dans l'arbre).
Comme pour `popupMenuTab` et consorts en session-29, livré cette session :
cet inventaire corrigé, et un nouveau verrou textuel
(`tests/test_gtk4_inline_editor_baseline.py`, 2 tests) sur les quatre
signaux actuels, pour détecter toute dérive avant un futur portage et
servir de check-list vivante — aucun portage de code cette session.

**Reste** : porter les quatre signaux (probablement en plusieurs passes ;
le couple `focus-out-event`/`populate-popup` est le plus risqué et
mériterait d'être vérifié manuellement sur un GTK4 réel avant d'écrire le
code, pas après) — ordre non tranché, à proposer et confirmer avec
l'auteure, comme pour `plugins/ssh/gtk4.py`/RDP. Toute mention future de
« `Gtk.Entry`/`populate-popup` » comme seul point restant ailleurs dans ce
dépôt (`docs/features-backlog.md`, `CLAUDE.md`) doit être lue à la lumière
de cette correction : le widget concerné est `CellTextView`/`Gtk.TextView`,
pas `Gtk.Entry`, et le périmètre couvre 4 signaux, pas 1.

## 3.6-duodecies Portage effectif de `key-press-event` (session-43, 2026-09-18)

**Contexte** : premier des quatre signaux inventoriés par l'audit
session-42 ci-dessus (§3.6-undecies) à être effectivement porté.
Proposition d'ordre pour les quatre (à confirmer avec l'auteure, comme
`plugins/ssh/gtk4.py`/RDP) posée cette session, sans la trancher pour les
trois signaux restants : `key-press-event` d'abord (fait cette session),
`button-press-event` ensuite, le couple `focus-out-event`/
`populate-popup` en dernier.

**Pourquoi `key-press-event` en premier** : c'est le seul des quatre signaux
totalement indépendant des trois autres — il ne lit ni n'écrit
`_in_editor_menu` (le drapeau partagé par `focus-out-event` et
`populate-popup`) — et son équivalence GTK4 est directe, sans aucun
comportement d'exécution à vérifier empiriquement. Vérification faite
cette session sur `docs.gtk.org` (gtk3 et gtk4) et `gtk-rs` :
`Gtk.EventControllerKey` est la **même classe** des deux côtés (ajoutée en
GTK3 3.24, inchangée en GTK4), avec le même signal `key-pressed(keyval,
keycode, state)` renvoyant un booléen — contrairement à
`Gtk.GestureMultiPress`/`Gtk.GestureClick` (`gesture_trigger_core.py`, deux
classes distinctes ne partageant qu'un signal). Seule différence trouvée :
la construction. En GTK3, `Gtk.EventControllerKey.new(widget)` attache
directement le contrôleur au widget passé en paramètre ; en GTK4, le
constructeur ne prend aucun argument et l'attache se fait séparément via
`widget.add_controller(controller)`. **À ajuster lors du futur passage réel
à GTK4** — même type de différence de construction déjà relevé pour les
gestes dans `gesture_trigger_core.py`.

**Portage fait** : `MultilineCellRenderer._on_editor_key_press_event`
renommé `_on_editor_key_pressed`, reçoit désormais `(controller, keyval,
keycode, state)` au lieu de `(editor, event)` — le widget éditeur est
récupéré via `controller.get_widget()`. Logique de classification extraite
en fonction pure testable, `inline_editor_core.classify_editor_key_press()`
(zéro import Gtk, 9 tests/5 sous-tests dans
`tests/test_inline_editor_core.py`), reproduisant à l'identique les trois
branches de l'ancien code (Shift/Ctrl enfoncé → ignorer ; Entrée/Entrée du
pavé numérique → valider ; Échap → annuler ; tout le reste → ignorer).
**Comportement inchangé** : l'ancien gestionnaire ne retournait jamais
`True` (aucun `return` explicite après validation/annulation) — le nouveau
gestionnaire renvoie donc systématiquement `False`, quelle que soit
l'issue, pour laisser l'évènement se propager exactement comme avant (pas
de changement de comportement à documenter au sens de
`CONSIGNES-AGENTS-IA.md` section 4). Dans `do_start_editing()`, la ligne
`editor.connect("key-press-event", ...)` est remplacée par la construction
du contrôleur et la connexion du signal ; la référence du contrôleur est
gardée sur l'éditeur (`editor._key_controller`), même précaution que pour
les `Gtk.Gesture*` conservés sur le widget porteur en session-39/40/41
(éviter que PyGObject ne libère le wrapper Python prématurément).

**Nuance ajoutée pour `button-press-event`** (deuxième de la proposition
d'ordre, pas encore fait) : son portage vers `Gtk.GestureClick`/
`Gtk.GestureMultiPress` est mécaniquement aussi simple que celui-ci (classes
déjà utilisées ailleurs dans ce dépôt), mais son utilité réelle — il
contourne un bug GTK3 précis (`gtk_text_mark_get_buffer: assertion
'GTK_IS_TEXT_MARK (mark)' failed`), voir le commentaire de
`_on_editor_pressed` dans `widgets.py` — sur un GTK4 réel n'est pas
vérifiable dans cet environnement de travail : le bug pourrait ne plus
exister, rendant le contournement inutile, ou au contraire nécessiter un
mécanisme différent (`Gtk.GestureClick` ne renvoie pas de booléen de la
même façon qu'un ancien `button-press-event` pour arrêter la propagation).
Cette distinction entre les deux signaux restants les plus simples
(`key-press-event`, sans risque caché ; `button-press-event`, mécaniquement
simple mais d'utilité incertaine en GTK4) n'était pas faite par l'audit
initial de la session-42, qui les traitait comme équivalents en risque.

**Vérification** : `ruff check`/`ruff format --check` propres sur les
fichiers touchés/créés (`widgets.py`, `inline_editor_core.py`,
`tests/test_inline_editor_core.py`,
`tests/test_gtk4_inline_editor_baseline.py`) ; `tools/check_circular_imports.py`
sans régression (42 modules) ; suite de tests hors `test_gcm.py` (GTK3
absent de cet environnement) : 265 tests/53 sous-tests, tous passants.
**Non vérifié empiriquement** (GTK3 réel absent de cet environnement de
travail) : le comportement à l'exécution du contrôleur — à confirmer
manuellement avant mise en production (double-clic sur un hôte/dossier
pour lancer l'édition, puis Entrée pour valider, Échap pour annuler, et
frappe de texte normal pendant l'édition).

**Reste** : `button-press-event` et le couple `focus-out-event`/
`populate-popup` (voir la proposition d'ordre ci-dessus, ordre entre ces
deux points toujours à confirmer avec l'auteure comme les chantiers
`plugins/ssh/gtk4.py`/RDP). Détail complet de session : voir
`docs/sessions/session-43.md`.

## 3.6-terdecies Portage effectif de `button-press-event` (session-44, 2026-09-18)

**Contexte** : deuxième des quatre signaux de l'audit session-42
(§3.6-undecies) à être effectivement porté, conformément à la proposition
d'ordre posée en session-43 (§3.6-duodecies ci-dessus).

**Différence avec `key-press-event`** : contrairement à
`classify_editor_key_press()`, la classification de ce signal ne branche
sur **aucun** de ses paramètres — l'ancien gestionnaire
(`MultilineCellRenderer._on_editor_pressed`) retournait
inconditionnellement `True` pour tout clic reçu, quel que soit le bouton
ou le nombre de pressions, en commentaire de contournement d'un bug GTK3
précis (`gtk_text_mark_get_buffer: assertion 'GTK_IS_TEXT_MARK (mark)'
failed`). La nouvelle fonction pure,
`inline_editor_core.classify_editor_button_press(n_press=...)`, formalise
ce comportement inconditionnel (elle renvoie toujours `"claim"`) plutôt
que de le laisser implicite dans `widgets.py` — extraite malgré l'absence
de branchement réel, conformément à la discipline de tests de
`CONSIGNES-AGENTS-IA.md` section 5 (4 tests dans
`tests/test_inline_editor_core.py`, dont un qui vérifie explicitement que
le résultat ne varie pas selon `n_press`).

**Portage fait** : `_on_editor_pressed(self, editor, menu)` remplacé par
`_on_editor_button_pressed(self, gesture, n_press, x, y)`, connecté au
signal `pressed` d'un `Gtk.GestureMultiPress` (GTK3) — même classe utilisée
pour les trois sites du point 3 (`gesture_trigger_core.py`,
sessions 39-41). `set_button(0)` (tous boutons) reproduit l'absence de
distinction de bouton de l'ancien signal. Revendiquer la séquence
(`gesture.set_state(Gtk.EventSequenceState.CLAIMED)`) est l'équivalent le
plus proche disponible du `return True` de l'ancien `button-press-event`
pour empêcher la propagation vers le gestionnaire par défaut de
`Gtk.TextView`. Référence du geste gardée sur l'éditeur
(`editor._button_gesture`), même précaution que pour `_key_controller`
(session-43) et les `Gtk.Gesture*` des sessions 39-41.

**Nuance non résolue par ce portage** (déjà posée par
`inline_editor_core.py` et §3.6-duodecies ci-dessus, reproduite ici sans
y répondre) : contrairement à `key-press-event`, l'**utilité réelle** de
ce contournement sur un GTK4 réel n'est pas vérifiable dans cet
environnement de travail (GTK4 absent). Le bug d'origine peut avoir
disparu avec GTK4, auquel cas revendiquer systématiquement la séquence
reste sans effet néfaste mais devient un contournement inutile plutôt
qu'une régression — cette distinction (forme du portage vérifiable,
utilité du comportement porté non vérifiable) est ce qui permet de coder
ce signal maintenant sans enfreindre la règle de
`CONSIGNES-AGENTS-IA.md`/`claude.md` de ne pas trancher à l'aveugle un
comportement d'exécution non vérifiable : la forme (classe/signal/API) est
vérifiée et documentée, seule l'utilité du comportement métier reste
ouverte, et reste identique qu'on la revendique ou non côté propagation
d'évènement.

**`test_gtk4_context_menus_baseline.py` mis à jour** : le site
`_on_editor_pressed` y était compté depuis la session-38 comme un « décoy »
(site partageant le nom du signal `button-press-event` mais hors périmètre
du point 3, qui ne concerne que le déclenchement des quatre menus
contextuels applicatifs). Son compteur dans
`EXPECTED_GESTURECLICK_DECOY_SITES` tombe à zéro ; le verrou positif du
nouveau câblage vit dans `tests/test_gtk4_inline_editor_baseline.py`
(fichier dédié à l'éditeur inline depuis la session-42), pas dans ce
fichier-ci.

**Vérification** : `ruff check`/`ruff format --check` propres sur les
fichiers touchés (`widgets.py`, `inline_editor_core.py`,
`tests/test_inline_editor_core.py`,
`tests/test_gtk4_inline_editor_baseline.py`,
`tests/test_gtk4_context_menus_baseline.py`) ;
`tools/check_circular_imports.py` sans régression (42 modules) ; suite de
tests hors `test_gcm.py` (GTK3 absent de cet environnement) : 271
tests/59 sous-tests, tous passants (271 = 265 de la session-43 + 6
nouveaux : 4 dans `test_inline_editor_core.py`, 2 dans
`test_gtk4_inline_editor_baseline.py`). **Non vérifié empiriquement**
(GTK3 réel absent de cet environnement de travail) : le comportement à
l'exécution du geste — à confirmer manuellement avant mise en production
(clic simple/double/triple dans l'éditeur inline pendant un renommage
d'hôte/dossier ; en particulier vérifier que l'assertion GTK3 d'origine
ne réapparaît pas, et que la sélection/le positionnement du curseur dans
l'éditeur restent utilisables normalement au clic).

**Reste** : le couple `focus-out-event`/`populate-popup` (dernier des
quatre signaux, le plus risqué — voir §3.6-undecies pour la question non
tranchée sur le focus du `Gtk.Popover` interne du menu contextuel natif
GTK4), `plugins/ssh/gtk4.py` et RDP — ordre entre ces chantiers toujours à
confirmer avec l'auteure. Détail complet de session :
`docs/sessions/session-44.md`.

## 3.6-quaterdecies Vérification empirique des portages GestureClick et inline editor (issue #72, 2026-10-26)

**Contexte** : les portages des sessions 39-44 (§3.6-octies à
§3.6-terdecies) ont tous été écrits « en aveugle » — GTK3 était absent de
l'environnement de développement, chaque section le notait explicitement
(« non vérifiable empiriquement dans cet environnement de travail »).
Cette section documente la vérification manuelle demandée par l'issue #72
avant mise en production, menée sur l'application GTK3 **réelle**.

**Harnais** : application lancée sous Xvfb (`:99`) avec openbox, clics
clavier/souris injectés au niveau serveur X (XTEST via python-xlib) —
donc les vrais chemins d'événements GTK (synthèse 2BUTTON/3BUTTON,
focus, grabs), pas des `widget.event()` synthétiques. Terminal réel via
`addTab("local")` (VTE + shell), arbre peuplé par une configuration de
test (3 hôtes dans un groupe). Le harnais n'est pas commité : la règle
« aucun nouveau code GTK3 » (tests/test_gtk3_retrait.py,
gtk3_inventaire.txt) l'interdit — il vit hors du dépôt.

**Résultats : 14/14 PASS.**

- Terminal (GestureMultiPress porté session-40) : double-clic sélectionne
  le mot (run de 60 « P » sélectionné exactement), triple-clic la ligne
  entière, clic simple donne le focus au VTE — la sélection native VTE
  n'est pas avalée par le geste (la mise en garde de §3.6-nonies était
  fondée).
- Étiquette d'onglet (session-41) : clic droit ouvre le popover
  `TabContextMenu` (Gio.Menu, 3 entrées), clic milieu ferme l'onglet.
- Arbre `treeServers` (session-39) : clic simple sélectionne la ligne,
  clic droit sur un hôte ouvre `popupMenuFolder` avec les sections
  attendues (4 pour « host »), clic droit dans le vide ouvre le menu
  racine (2 sections) — le contenu s'adapte bien à la cible.
- Éditeur inline (sessions 42-44) : sur l'arbre « Command » de la fenêtre
  Paramètres (seul site réellement éditable — le renderer de
  `treeServers` n'est PAS éditable, son renommage passe par le menu
  contextuel), le double-clic démarre bien l'édition `CellTextView` avec
  le texte actuel, et Entrée valide la nouvelle valeur dans le modèle.

**Bug réel trouvé et corrigé** (l'objet même de cette vérification) :
`Wmain.on_terminal_pressed` plantait en `TypeError: unsupported operand
type(s) for &` sur PyGObject récent — `Gdk.EventButton.get_state()` y
renvoie un tuple résultat `(succès, état)` (`_ResultTuple`, vérifié sur
PyGObject 3.56 / Ubuntu 26.04) et non plus les flags seuls, cassant le
Ctrl+clic gauche « ouvrir le lien » à chaque pression (plantage journalisé
puis avalé, donc invisible en développement sans GTK3). Correctif :
`gesture_trigger_core.masque_actif_dans_etat()` (fonction pure, zéro
import Gtk, testée à 100 % lignes et branches) normalise les deux formats
et remplace le `&` direct à l'unique site appelant. Vérifié empiriquement
après correctif : plus aucun TypeError sur la même série de clics.

**Observations annexes** (faites pendant la session, sans comparaison
avant/après sur le code inchangé — à confirmer avec l'auteure si besoin) :

- La page « Shortcuts » de la fenêtre Paramètres déborde de la hauteur
  d'écran par défaut (mesuré : `treeCustom` positionné à 846 px dans un
  dialogue de 748 px) : l'arbre « Custom Commands », siège de l'éditeur
  inline, n'est pas cliquable tant que l'arbre « Standard Commands »
  n'est pas réduit. Le code de cette fenêtre n'est pas touché par cette
  session ; à rapprocher éventuellement d'une amélioration de
  disposition séparée.
- Pièges rencontrés pour tout futur harnais empirique (aucun n'est un
  bug des portages) : `widget.get_window().get_origin()` peut renvoyer
  l'origine d'une fenêtre ancêtre (décalage de 38 px mesuré sur le VTE)
  — préférer `translate_coordinates` vers le toplevel ; `get_cell_area`
  est en coordonnées bin window (conversion nécessaire) ; la sortie du
  shell n'arrive qu'après plusieurs secondes (attendre la position du
  curseur, pas un délai fixe) ; trois clics XTEST consécutifs sans
  release sont coalescés par le serveur X (alterner press/release).

**Reste ouvert** : rien sur le périmètre GestureClick/inline editor — les
sites encore non portés (couple `focus-out-event`/`populate-popup`,
`plugins/ssh/gtk4.py`, RDP) restent ceux listés en fin de §3.6-terdecies.

## 3.7 Audit de `plugins/ssh/gtk4.py` (session-45, 2026-09-18)

Avant de choisir lequel des trois chantiers restants traiter (le couple
`focus-out-event`/`populate-popup` ci-dessus, `plugins/ssh/gtk4.py`, RDP —
ordre non tranché depuis la session-44), même discipline que pour les
audits précédents (sessions-28/29/38/42) : inventaire du périmètre réel de
`plugins/ssh/gtk4.py` avant tout portage, ce point n'ayant jamais été
détaillé au-delà de sa mention dans §3.0-bis.

**Rappel du périmètre décidé (§3.0-bis)** : `plugins/ssh/gtk4.py` doit
regrouper les dialogues et widgets aujourd'hui éclatés dans trois fichiers
GTK3 à la racine du dépôt — `plugins/ssh/core.py` (logique métier, extrait
en session-26) ne contient lui aucun import GTK et n'est pas concerné :

| Fichier | Lignes | Rôle |
|---|---|---|
| `ssh_config_editor.py` | 1379 | Éditeur `~/.ssh/config` (liste des hôtes, formulaire d'options, aperçu de la commande `ssh`) |
| `ssh_key_manager_dialog.py` | 2678 | Gestionnaire de clés SSH (génération, import, `known_hosts`, `authorized_keys`, CA de signature) |
| `key_picker_dialog.py` | 378 | Dialogue de sélection d'une clé privée depuis `~/.ssh/` |

Soit **4435 lignes** de code GTK3 non encore auditées ni portées — un
périmètre nettement plus large que celui de n'importe quel audit
précédent de ce chantier (les quatre signaux de l'éditeur inline,
session-42, totalisaient quelques dizaines de lignes).

**Ce qui est déjà bon signe** : les trois fichiers utilisent déjà
partout `utils.run_dialog_sync()` plutôt qu'un appel direct `.run()` sur
leurs `Gtk.Dialog` — le point de rupture le plus structurant du cœur
(§3.2, résolu partout ailleurs en session-28) est donc **déjà résolu ici
aussi**, sans travail supplémentaire (verrouillé par
`tests/test_ssh_gtk4_baseline.py::test_dialog_run_rupture_already_resolved_here_too`).
`KeyPickerDialog` documente même explicitement ce choix dans sa propre
docstring d'exemple.

**Ruptures d'API distinctes recensées** (hors boilerplate
`pack_start()`/`pack_end()`/`.add()`, voir plus bas) :

| Rupture | Où | Remplacement GTK4 |
|---|---|---|
| `show_all()` (15 occurrences : 6+8+1) | les 3 fichiers | Supprimé en GTK4 — confirmé sur le guide de migration officiel GNOME (« Mention gtk_widget_show_all in the migration guide » : la fonction, la propriété `no-show-all` et ses accesseurs ont disparu). Les widgets sont visibles par défaut ; la ligne peut le plus souvent être simplement retirée |
| `set_border_width()` (4 occurrences) | `ssh_key_manager_dialog.py` | `Gtk.Container` (et sa propriété `border-width`) est supprimé en GTK4 (confirmé — commit « avahi-ui: Avoid removed (in GTK+4) gtk_container_set_border_width() », pratique documentée aussi côté Inkscape) ; remplacement par des marges (`set_margin_*()`) posées sur chaque enfant plutôt que sur le conteneur |
| `Gtk.Clipboard.get(...)` (2 occurrences) | `ssh_config_editor.py`, `ssh_key_manager_dialog.py` | Classe entièrement retirée en GTK4 — section dédiée du guide de migration officiel (« Replace GtkClipboard with GdkClipboard ») ; remplacement par `widget.get_clipboard()` (`Gdk.Clipboard`), API asynchrone |
| `dlg.get_filename()` (3 occurrences) | `ssh_key_manager_dialog.py` | N'existe plus sur l'interface `Gtk.FileChooser` en GTK4 (confirmé absent de la liste des méthodes, `api.pygobject.gnome.org/Gtk-4.0/interface-FileChooser.html`) ; remplacé par `get_file()` → `Gio.File` → `.get_path()` |
| `set_current_folder(str(...))` (2 occurrences) | `ssh_key_manager_dialog.py` | Signature changée en GTK4 : `set_current_folder(file: Gio.File)` au lieu d'une chaîne (confirmé sur les bindings `gtk4-rs`/`valadoc.org`, générés depuis les headers GTK4 officiels) |
| `Gtk.FileChooserDialog(...)` (3 occurrences) | `ssh_key_manager_dialog.py` | La classe existe toujours en GTK4 (pas une rupture immédiate), mais l'ensemble de ses méthodes ci-dessus est déprécié depuis GTK 4.10 au profit de `Gtk.FileDialog` (API asynchrone à callback) |

Comptes exacts verrouillés par le nouveau
`tests/test_ssh_gtk4_baseline.py`.

**Question ouverte, volontairement non tranchée ici** : pour le trio
`Gtk.FileChooserDialog`/`get_filename()`/`set_current_folder()`, deux
lectures raisonnables coexistent — (a) adapter a minima l'existant
(`get_file().get_path()`, `set_current_folder(Gio.File)`, en gardant
`Gtk.FileChooserDialog`, toujours utilisable en GTK4 malgré sa
dépréciation) ou (b) réécrire directement vers `Gtk.FileDialog`,
l'API recommandée, mais asynchrone (callback), ce qui change la forme de
l'appelant (`_on_designate_ca()`/`_on_import_ca()` ne pourraient plus
enchaîner synchroniquement sur `run_dialog_sync()` + traitement du
résultat comme aujourd'hui). Deviner reviendrait à trancher seul un choix
d'architecture — à confirmer avec l'auteure, comme pour l'ordre des trois
chantiers restants.

**Ce qui n'a délibérément pas été détaillé point par point** : les
appels `pack_start()`/`pack_end()`/`.add()` de construction de layout
(36+39+6 = 81 pour `pack_start()`, 2+1+0 pour `pack_end()`, 6+8+1 pour
`.add()` selon les trois fichiers) — supprimés eux aussi en GTK4
(`GtkBox.pack_start()`/`pack_end()` et `GtkContainer.add()` disparaissent
avec la classe `GtkContainer`, confirmée retirée par la documentation
officielle du portage GTK4 de plusieurs projets tiers, Inkscape et Siril
notamment). Contrairement aux quatre signaux de l'éditeur inline
(session-42, chacun remplaçable indépendamment par un mécanisme distinct),
cette centaine d'appels sera de toute façon retraitée en bloc par la
Stratégie B actée (réécriture complète du fichier, §3.0/§3.3) : les
verrouiller un par un n'offrirait aucune valeur de check-list
supplémentaire, seulement du bruit dans la baseline. Leurs décomptes
globaux ci-dessus servent uniquement à chiffrer l'ampleur du chantier
(4435 lignes, trois fichiers, aucun encore touché), pas de check-list
ligne à ligne.

**Pas codé cette session** (audit + inventaire seulement, conformément à
`CONSIGNES-AGENTS-IA.md`) : le volume (4435 lignes) et la question
d'architecture ouverte sur les `FileChooserDialog` rendent ce chantier
au moins aussi engageant que celui des menus contextuels (session-29),
qui avait pris quatre sessions de portage effectif une fois audité.
Nouveau verrou `tests/test_ssh_gtk4_baseline.py` (3 tests / 18
sous-tests) : baseline des marqueurs de rupture ci-dessus, confirmation
que le verrou `gi.require_version("Gtk", "3.0")` est toujours en place
sur les trois fichiers (portage non commencé), et confirmation que
`Gtk.Dialog.run()` n'y est déjà plus appelé directement. Détail complet
de session : `docs/sessions/session-45.md`.

## 3.8 Audit RDP — correction du diagnostic de §3.4 (session-46, 2026-09-19)

Dernier des trois chantiers dont l'ordre restait à confirmer avec
l'auteure (couple `focus-out-event`/`populate-popup` — §3.6-undecies —,
`plugins/ssh/gtk4.py` — §3.7 —, RDP) à ne pas encore avoir été audité en
détail. Même discipline que pour `plugins/ssh/gtk4.py` (session-45) et
les audits précédents (sessions-28/29/38/42) : inventaire avant tout
portage de code, conformément à `CONSIGNES-AGENTS-IA.md`.

### 3.8.1 Ce que le code réellement chargé montre (et qui contredit §3.4)

§3.4 décrivait `GtkFrdp.Display` et un repli `Gtk.Socket`/XEmbed comme deux
mécanismes coexistants, tous deux dépendants de X11. Vérification directe
sur le dépôt tel qu'il est aujourd'hui :

| Question | Constat |
|---|---|
| Le plugin RDP chargé (`plugins/plugin_rdp.py`) utilise-t-il `Gtk.Socket`/XEmbed ? | Non. Sa propre docstring de classe le dit explicitement : « Seule solution RDP de GCM (pas de fallback) ». Zéro occurrence de `Gtk.Socket`/`GtkSocket`/`/parent-window:` dans le fichier |
| L'ancien mécanisme XEmbed a-t-il existé ? | Oui — `tests/test_gcm.py` le prouve (`TestRdpEmbeddedTabBuildCmd`, teste `RdpEmbeddedTab._build_cmd()` et son argument `/parent-window:<XID>`) |
| Existe-t-il encore dans `gnome_connection_manager.py` ? | Non — recherche textuelle exhaustive : `class RdpEmbeddedTab` et `_rdp_socket_available` n'apparaissent plus nulle part hors `tests/test_gcm.py` |

Cette dernière ligne referme une question restée ouverte depuis la
session-36 : `_vm_name_split`/`_rdp_socket_available` y avaient été
reconfirmés orphelins (`docs/architecture.md` §5 point 5) sans qu'on sache
à quoi `_rdp_socket_available` avait servi. Cet audit établit que
c'était le détecteur X11 du chemin XEmbed — sa disparition n'est donc pas
un accident isolé, mais la trace du passage à l'architecture à plugins
(`plugin_rdp.py` se décrit lui-même comme le « quatrième plugin implémenté
après Web, IPMI SOL et Serial »). Le sort des tests orphelins de
`tests/test_gcm.py` (suppression vs réintroduction des fonctions) reste
néanmoins à confirmer avec l'auteure, exactement comme documenté en
session-36 — cet audit ne le tranche pas, il en précise seulement
l'origine. **Tranché depuis (#87, 2026-10-12)** : tests supprimés ;
les tests `_vm_name_split` visent `hypervisor_import_common.vm_name_split()`.

**Conséquence pour la question posée par §3.4** : elle n'est plus « quel
mécanisme remplace `Gtk.Socket`/XEmbed » (ce chemin n'existe déjà plus
dans le code réellement chargé) mais « `GtkFrdp.Display` a-t-elle un
chemin GTK4 ? ».

### 3.8.2 Inspection de la bibliothèque vendorisée `gtk-frdp/`

`plugins/plugin_rdp.py` consomme `GtkFrdp.Display` par introspection GObject
(`gi.require_version("GtkFrdp", "0.2")`, `GtkFrdp.Display.new()`), rendu
par la bibliothèque C vendorisée dans `gtk-frdp/` (projet GNOME, Felipe
Borges/Marek Kašík, `gtk-frdp.doap`, écrit à l'origine pour GNOME Boxes).
Lecture de sa source :

- `frdp-display.h`/`.c` : `FrdpDisplay` est une sous-classe directe de
  `GtkDrawingArea`
  (`G_DEFINE_TYPE_WITH_PRIVATE (FrdpDisplay, frdp_display, GTK_TYPE_DRAWING_AREA)`),
  qui peint via Cairo et invalide via `gtk_widget_queue_draw_area()` —
  le schéma classique d'un widget de dessin GTK3, pas un mécanisme
  d'embarquement de fenêtre externe.
- Recherche exhaustive dans tout `gtk-frdp/src/` : **aucune** occurrence de
  `X11`/`Xlib`/`gdk_x11`/`GDK_WINDOWING_X11`/`xcb_`. Une seule mention de
  `GdkWindow` (type générique multi-backend en GTK3, **supprimé en GTK4**
  au profit de `GdkSurface` — `frdp-session.c`), qui est une rupture d'API
  réelle mais d'une classe déjà bien connue de ce dépôt, pas un blocage
  architectural. §3.4 avait donc tort d'imputer une dépendance X11 à
  `GtkFrdp.Display` lui-même — seul l'ancien repli XEmbed (aujourd'hui
  disparu, §3.8.1) en avait une.
- **Mais** `gtk-frdp/src/meson.build` épingle explicitement
  `dependency('gtk+-3.0')` : tel que vendorisé aujourd'hui dans ce dépôt,
  `gtk-frdp` ne se construit pas contre GTK4.

Rappel : `gtk-frdp/` est un sous-dossier vendorisé, exclu de tous les
hooks qualité (`CONSIGNES-AGENTS-IA.md` §7) — cette inspection est une
lecture ponctuelle pour l'audit, pas un chantier de lint/tests sur du code
C.

### 3.8.3 Recherche amont (web, 2026-09-19 — à reconfirmer périodiquement, comme le recommandait déjà §3.5)

`gtk-frdp` est un petit projet GNOME (~25 issues, 3 MR sur
`gitlab.gnome.org/GNOME/gtk-frdp` au moment de cette recherche) sans
branche ni ticket « port GTK4 » identifiable. Signal le plus significatif
trouvé : **GNOME Boxes** — dont Felipe Borges (co-auteur de `gtk-frdp`)
est le mainteneur — vient de terminer sa propre migration GTK4/libadwaita
(bêta annoncée le 2026-08-03 par F. Borges lui-même, environ sept
semaines avant cette recherche). L'annonce et sa couverture (Phoronix, UbuntuHandbook,
itsfoss) décrivent en détail le remplacement du widget SPICE — GTK3 vers
**Libmks**, une nouvelle bibliothèque dédiée — mais aucune ne mentionne
RDP ni `gtk-frdp`. Le billet de F. Borges précise que cette nouvelle
version « couvre déjà la plupart de ce que l'ancien Boxes savait faire »,
laissant ouvert que certaines fonctionnalités (dont RDP, non citée)
restent à rattraper. Signal réel mais non concluant, dans la même veine
que le constat de §3.5 (« GNOME Connections listé GTK3 ») : le statut
GTK4 de `gtk-frdp` reste **non confirmé** — et apparemment non résolu
même du côté de son propre mainteneur, sept semaines après une migration
qui aurait été l'occasion naturelle de le clarifier.

### 3.8.4 Découverte annexe : le menu contextuel RDP/VNC/SPICE jamais porté

En vérifiant `plugin_rdp.py`, `widgets.build_remote_desktop_context_menu()`
(le « constructeur générique RDP/VNC/SPICE » cité par l'audit initial des
menus contextuels, session-29, comme un des quatre menus applicatifs) se
révèle **toujours** construit avec deux `Gtk.Menu()` classiques (menu
principal + sous-menu « Special keys »), consommé tel quel par les trois
plugins RDP, VNC et SPICE. `tests/test_gtk4_context_menus_baseline.py`
le documentait déjà correctement en creux depuis la session-33
(« Restent hors périmètre de ce fichier : le constructeur générique
RDP/VNC/SPICE... », compteur `WIDGETS_FILE` inchangé) — mais le
résumé de `CLAUDE.md` pour cette même session affirme que « les quatre
menus contextuels applicatifs... sont désormais tous portés », ce qui est
inexact pour celui-ci : seuls trois des quatre l'ont été
(`popupMenu`/`popupMenuFolder`/`popupMenuTab`). `CLAUDE.md` (entrée
1-quindecies) a été mis à jour pour le signaler ; le paragraphe d'origine
de la session-33 n'a volontairement pas été réécrit. Nouveau verrou dédié
au périmètre RDP/VNC/SPICE : `tests/test_gtk4_rdp_baseline.py` (en
complément de, sans dupliquer, `tests/test_gtk4_context_menus_baseline.py`).

### 3.8.5 Bilan et ce qui reste ouvert

| Point | État après cet audit |
|---|---|
| Repli `Gtk.Socket`/XEmbed pour RDP | Confirmé disparu du code réellement chargé — non-sujet pour le portage GTK4 |
| `_rdp_socket_available`/`RdpEmbeddedTab` (orphelins, session-36) | Origine désormais expliquée (vestiges du repli XEmbed) ; tests supprimés, décision de l'autrice du 2026-10-12 (#87) |
| `GtkFrdp.Display` — dépendance X11 propre | Écartée — widget `GtkDrawingArea` standard, rendu Cairo |
| `GtkFrdp.Display` — build GTK4 | Non disponible tel que vendorisé (`gtk+-3.0` épinglé) ; statut amont non confirmé (recherche 2026-09-18) |
| `GdkWindow` dans `frdp-session.c` | Rupture d'API réelle mais mineure/connue (→ `GdkSurface`) |
| `build_remote_desktop_context_menu()` (RDP/VNC/SPICE) | Confirmé non porté (`Gtk.Menu` classique), contrairement au résumé session-33 |

**Question ouverte, volontairement non tranchée ici** : si `gtk-frdp` ne
gagne pas de chemin GTK4 en amont, au moins trois lectures possibles
existent pour `plugins/rdp/gtk4.py` (à construire, §3.4, précision de
l'auteure session-30) — (a) forker/patcher localement `gtk-frdp` vers
GTK4 (`GtkDrawingArea` existe toujours, le portage semble borné à
`GdkWindow`→`GdkSurface` et au meson.build, mais reste un chantier C hors
compétence Python de ce dépôt), (b) attendre un portage amont, ou (c)
explorer une alternative (le crate Rust `rdw`/`qemu-display` cité par
§3.5, ou un widget maison). Trancher entre ces lectures serait décider
seul un choix d'architecture — à confirmer avec l'auteure, comme l'ordre
des trois chantiers, désormais tous audités (§3.6-undecies, §3.7, §3.8).

**Pas codé cette session** (audit + inventaire seulement, conformément à
`CONSIGNES-AGENTS-IA.md`). Nouveau verrou `tests/test_gtk4_rdp_baseline.py`
(3 classes, 6 tests / 23 sous-tests) : disparition confirmée des marqueurs
XEmbed du code réellement chargé, dépendance actuelle à `GtkFrdp`/GTK3,
état non porté de `build_remote_desktop_context_menu()`. `docs/architecture.md`
§2.1 (tableau des protocoles) et §5 (écarts doc/code) mis à jour en
conséquence. Détail complet de session : `docs/sessions/session-46.md`.

## 3.6-quaterdecies Couple `focus-out-event`/`populate-popup` de `CellTextView` (#96, 01/11/2026)

Décision de Mathilde du 12/10/2026, précisée par l'option A du 28/10/2026 :

- le menu contextuel natif de l'éditeur inline est remplacé par un popover
  maîtrisé : `Gtk.PopoverMenu.new_from_model()` en GTK4,
  `Gtk.Popover.new_from_model()` en GTK3. Il propose quatre actions standard
  (couper, copier, coller, tout sélectionner) ;
- il s'ouvre au clic droit (`is_editor_menu_button`) et, en GTK3, avec la
  touche Menu ou Maj+F10 (signal `popup-menu`) ; le menu natif n'est plus
  ouvert, `populate-popup` est supprimé ;
- la perte de focus **valide** l'édition (`edited`), sauf pendant que le
  popover est ouvert ou si l'édition est déjà terminée par Entrée ou Échap
  (`classify_editor_focus_leave`) ;
- GTK4 utilise `Gtk.EventControllerFocus` (`leave`). En GTK3, qui n'a pas ce
  contrôleur, `focus-out-event` reste en repli.

Changement visible : sous GTK3, la perte de focus annulait l'édition ; elle
la valide désormais.
