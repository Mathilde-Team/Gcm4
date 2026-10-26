# pluginvnc2 — fonctionnalités et backlog

Importé depuis `CLAUDE.md`. Onglet VNC pour gnome-connection-manager
(fork GTK4), basé sur un fork maison d'`asyncvnc2` (Tight/Hextile/
ZlibHex, curseur serveur, resize, Fence/ContinuousUpdates — cf.
`CLAUDE.md` pour le détail de ce fork). Fichier unique (`vnc_tab.py`) —
un seul fichier à copier dans le dépôt, cf. `CLAUDE.md` § « Pourquoi un
fichier unique ».

Le détail de chaque correctif (bug trouvé, cause, test, contre-épreuve)
vit dans `docs/sessions/`, un fichier par session (01 à 24 à ce jour).
Ce document-ci ne garde que l'état courant des fonctionnalités et ce
qui reste réellement à faire.

## Affichage

- Rendu du framebuffer via `Gdk.MemoryTexture` dans un `Gtk.Picture`.
- Mise à l'échelle CONTAIN (letterboxing centré) ou FILL, basculable.
- Coordonnées souris et curseur serveur convertis correctement dans les
  deux modes d'échelle (pas de dérive même quand l'affichage est
  redimensionné), y compris si l'écran actif sélectionné disparaît en
  cours de session (repli sur le calcul plein-bureau, cohérent avec ce
  qui est réellement affiché — pas de décalage entre le point cliqué à
  l'écran et où le clic atterrit côté serveur).
- Curseur serveur (Rich Cursor / X Cursor / Cursor With Alpha) rendu en
  overlay, à la bonne échelle, avec masquage du pointeur système pendant
  que le serveur gère son propre rendu.
- Multi-écran (`ExtendedDesktopSize`) : sélecteur d'écran dans la barre
  d'outils, masqué automatiquement s'il n'y a qu'un seul écran. Le crop
  est purement côté client ; les coordonnées souris restent toujours
  absolues (référentiel du framebuffer complet), pas relatives à l'écran
  affiché. Le bouton « ajuster la fenêtre à la taille distante » vise la
  taille de l'écran sélectionné (pas le bureau complet) quand un écran
  spécifique est affiché. La sélection d'écran survit à une ré-annonce
  ExtendedDesktopSize (le dropdown ne revient plus silencieusement sur
  « Bureau complet » tant que l'écran choisi est toujours dans la
  nouvelle liste — et si vraiment il a disparu, l'état interne
  (`VncDisplay._active_screen_id`) est explicitement aligné sur ce que
  montre le dropdown plutôt que de rester périmé).
- Détection de resize serveur, avec un bouton pour ajuster manuellement
  la fenêtre à la taille distante courante.
- Capture d'écran : bouton dans la barre d'outils pour enregistrer en PNG
  l'image actuellement affichée (`VncDisplay.save_screenshot()`, dernière
  texture déjà poussée par `_push_frame()` — déjà recadrée sur l'écran
  actif le cas échéant, cf. multi-écran ci-dessus). Lecture locale pure,
  aucune écriture sur `self.client`/le socket. Désactivé tant qu'aucune
  image n'a encore été reçue (même cycle de sensibilité que le bouton
  ContinuousUpdates). Nom de fichier par défaut horodaté, sanitisé pour
  les adresses IPv6 littérales (deux-points remplacés par des tirets).
  Résultat (succès avec chemin, ou échec renvoyant au journal) affiché
  brièvement dans la barre d'outils.
- Copie de la capture d'écran dans le presse-papiers système, en
  complément de l'enregistrement PNG ci-dessus
  (`VncDisplay.copy_screenshot_to_clipboard()`, bouton dédié
  « edit-copy-symbolic », même source — dernière texture poussée par
  `_push_frame()` — et même cycle de sensibilité que le bouton de capture
  PNG). Copie immédiate au clic, sans dialogue (rien à choisir pour une
  copie presse-papiers). Lecture locale pure, aucune écriture sur
  `self.client`/le socket.

## Entrées

- Clavier : AZERTY (accents directs et touches mortes) fonctionne nativement
  via la correspondance de noms keysym X11 entre GDK et `asyncvnc2`. Vrai
  maintien de touche (`key-pressed`/`key-released`, indexé par keycode
  physique — stable même si Maj change le keyval). Garde anti-répétition
  auto (pas de spam de down/up pendant l'auto-repeat système). Filet
  anti-touches-collées : relâchement de toutes les touches maintenues si
  le widget perd le focus (ex. Alt-Tab en pleine frappe).
- Souris : maintien réel des boutons (`mouse.hold()`) pour le drag et la
  sélection, pas juste un clic simple. Garde anti-collision symétrique de
  celle du clavier : deux boutons physiques traduits vers le même bouton
  VNC (ex. bouton latéral non mappé → clic gauche par défaut, cf.
  `_gdk_button_to_vnc`) ne peuvent pas s'écraser mutuellement. Tous les
  chemins d'entrée à haute fréquence (souris : déplacement, appui,
  relâchement, scroll ; clavier : appui) sont protégés contre une
  exception sur socket déjà mort. Molette : `VncConnectionInfo.invert_scroll`
  (défaut `False`) permet le « défilement naturel » façon trackpad macOS
  pour une connexion donnée — résolution pure dans
  `VncDisplay._resolve_effective_scroll_delta()`, réglage de connexion au
  même titre que `sync_clipboard`/`shared`/etc., pas un bouton de barre
  d'outils.
- Ctrl+Alt+Suppr dédié dans la barre d'outils.
- Menu « Alimentation » dans la barre d'outils (Gcm4 #104, tâche 5) :
  arrêter, redémarrer ou réinitialiser la machine distante via
  l'extension xvp d'asyncvnc2 (`send_xvp_shutdown/reboot/reset`, toujours
  avec `confirm=True`). Chaque commande passe par un dialogue de
  confirmation (Annuler a le focus, bouton destructif). Le serveur
  annonce xvp après la connexion : l'état des entrées est relu à chaque
  ouverture du menu (`VncTab._resolve_power_menu_state`) ; sans
  connexion ou sans xvp, entrées inactives et explication dans le menu.
  Un échec d'envoi est journalisé (`logger.warning`), jamais remonté
  d'une tâche asyncio orpheline. Tests : `tests/test_xvp_power.py`.
- Envoyer du texte au serveur distant comme une suite de frappes clavier
  (bouton dédié dans la barre d'outils, ouvrant une petite fenêtre de
  saisie), en CONTOURNANT le presse-papiers — utile quand la
  synchronisation presse-papiers ci-dessous est désactivée par profil, ou
  pour un caractère que le clavier physique local ne produit pas
  directement (`VncDisplay.send_text()`). Réutilise `_resolve_vnc_key_name()`
  via `Gdk.unicode_to_keyval()` (nouveau point d'entrée, vérifié le
  2026-09-11 contre docs.gtk.org) — même mécanisme de résolution que la
  frappe physique, pas de logique de conversion séparée. Un caractère isolé
  sans équivalent clavier reconnu est ignoré sans interrompre l'envoi des
  suivants. Même cycle de sensibilité que les boutons de capture d'écran
  (désactivé tant qu'aucune connexion n'est établie). Le champ de saisie a
  une icône « Coller » (`edit-paste-symbolic`, position secondaire) qui
  relit le presse-papiers LOCAL de manière asynchrone
  (`Gtk.Entry.read_text_async`/`read_text_finish`, même mécanisme que la
  surveillance du presse-papiers ci-dessous) et préremplit le champ —
  sans envoyer directement, pour que l'utilisateur puisse relire/corriger
  avant de cliquer « Envoyer ». Un presse-papiers vide ou illisible (ex.
  une image copiée) laisse le champ inchangé
  (`VncTab._resolve_pasted_clipboard_text()`). Une case « Garder la
  fenêtre ouverte après l'envoi » (#93, décochée par défaut, mémorisée pour
  la durée de vie de l'onglet) permet un envoi répété : après l'envoi, le
  champ est vidé et le dialogue reste ouvert ; décochée, le dialogue se
  ferme comme avant (`VncTab._resolve_send_text_action()`).
- Presse-papiers bidirectionnel (texte), désactivable par profil. Écriture
  vers le serveur protégée contre une exception sur socket déjà mort (même
  famille de garde que les chemins d'entrée haute fréquence ci-dessus).
  Extended Clipboard (Gcm4 #104, tâche 1) : si le serveur l'a confirmé
  (`client.clipboard_ext_supported`), le texte local part en UTF-8 par
  `send_clipboard_update()` (Notify puis Provide, l'ordre que TigerVNC et
  QEMU exigent) ; sinon ClientCutText historique (Latin-1). Un texte hors
  Latin-1 vers un serveur sans Extended Clipboard n'est pas transmis et
  c'est journalisé (avant : exception avalée sans trace). Décision pure
  `VncDisplay._resolve_clipboard_send_mode`, tests
  `tests/test_extended_clipboard.py`.
- Bip serveur (bell) relayé via `Gdk.Display.beep()`.
- Raccourcis clavier globaux, utilisables sans passer par la souris/la
  barre d'outils : `<Shift>F12` (capture d'écran), `<Control>F12` (copie
  de la capture dans le presse-papiers), `<Alt>F12` (dialogue d'envoi de
  texte, Gcm4 #92 ; sans effet tant que la connexion n'est pas établie),
  `F11` (plein écran),
  `<Control><Alt>End` (Ctrl+Alt+Suppr — convention répandue chez les
  clients VNC, la vraie combinaison étant interceptée par le système
  d'hôte local avant d'atteindre l'application). Posés en phase CAPTURE
  sur `VncTab` (ancêtre de `VncDisplay`) pour ne pas se faire avaler par
  `_on_key_pressed`, qui retourne toujours `True` — cf. `docs/pieges.md`.

## Connexion

- Transport WebSocket (Gcm4 #104, tâche 3) : `VncConnectionInfo.websocket`
  et `ws_path` (ex. `/websockify` pour websockify ou un proxy noVNC),
  transmis à `asyncvnc2.connect(websocket=True, ws_path=…)`. Chemin
  normalisé (vide → `/`, `/` ajouté en tête) et refusé s'il contient un
  espace ou un caractère de contrôle (il finit dans la ligne de requête
  HTTP) ; sans WebSocket, aucun argument de plus n'est passé à `connect()`.
  Démo autonome : `python vnc_tab.py --websocket=/websockify HOTE PORT`.
  Pas encore de champ de formulaire : le plugin n'a pas de formulaire de
  connexion à lui, il viendra avec l'intégration côté gcm4. Tests :
  `tests/test_websocket_transport.py`.
- VeNCrypt (Gcm4 #104, tâche 2) : négocié par asyncvnc2 sans réglage
  (magasin système de CA, nom d'hôte vérifié = `host`).
  `VncConnectionInfo.tls_ca_file` désigne une autorité de certification
  privée (PEM, `~` accepté) : `ssl_context` qui ne fait confiance qu'à
  elle et vérifie le nom d'hôte. Fichier absent ou invalide : l'onglet
  passe en erreur avant toute connexion. Certificat refusé ou poignée de
  main TLS ratée : message d'état qui nomme VeNCrypt et la raison
  (`VncDisplay._describe_connection_error`). Pas encore de champ de
  formulaire (même raison que le WebSocket : pas de formulaire de
  connexion dans le plugin). Tests : `tests/test_vencrypt_ca.py`, avec de
  vrais certificats et une vraie poignée de main TLS en boucle locale.
- Pinning de clé hôte (auth Apple ARD uniquement) façon `known_hosts` SSH
  — stocké dans `~/.gcm/vnc_host_keys.json`, écriture atomique, avec un
  message d'erreur explicite et un bouton de récupération en cas de clé
  changée. Oublier une clé enregistrée exige une confirmation par
  retype du nom d'hôte (pas un simple clic).
- Session partagée ou exclusive (`shared=`), override des encodages
  négociés, qualité JPEG / niveau de compression — tous exposés comme
  champs de `VncConnectionInfo`, transmis à `asyncvnc2.connect()`. Le
  paramètre `shared` a un repli automatique (`TypeError`) si le patch
  côté `asyncvnc2` n'est pas encore appliqué.
- Mode ContinuousUpdates activable par bascule dans la barre d'outils
  (désactivé par défaut — certains serveurs le refusent, cf. `CLAUDE.md`).
- Reconnexion propre : relâche les touches/boutons maintenus, réapplique
  ContinuousUpdates si c'était actif avant la coupure.
- Reconnexion automatique après une coupure inattendue (perte réseau,
  redémarrage serveur) : uniquement si la session avait déjà été
  pleinement établie au moins une fois (jamais sur un échec
  d'authentification ou un hôte injoignable dès la première tentative —
  ça ne se réparerait pas tout seul, et ça risquerait un verrouillage de
  compte côté auth Apple ARD). Backoff exponentiel plafonné à 30 s (2, 4,
  8, 16, 30…), tentatives illimitées tant que la coupure persiste ; fermer
  l'onglet ou cliquer « Reconnecter » interrompt la boucle à tout moment.
  Jamais déclenchée par un `stop()`/`close()` volontaire (statut
  DISCONNECTED, pas ERROR). Le message affiché pendant l'attente inclut
  désormais le délai avant la prochaine tentative (« … — nouvelle
  tentative automatique dans Ns », `VncDisplay._append_reconnect_delay_to_message()`,
  câblée dans `_set_status()`) — jusque-là cette info n'existait que
  dans les logs, l'utilisateur ne voyait qu'un message d'erreur brut sans
  savoir qu'autre chose était prévu.
- Bouton « Déconnecter » dédié dans la barre d'outils (icône
  `process-stop-symbolic`), à côté de « Reconnecter » : ferme la
  connexion SANS fermer l'onglet, via `VncDisplay.stop()` (déjà
  existante, déjà testée pour son annulation de la reconnexion
  automatique en attente, cf. `tests/test_auto_reconnect.py`) — distinct
  de « Reconnecter », qui referme puis rouvre aussitôt. Sensible dans
  tous les états sauf DISCONNECTED (rien à arrêter,
  `VncTab._resolve_disconnect_button_sensitivity()`) : permet aussi bien
  de libérer une session active que d'annuler une tentative de connexion
  en cours (CONNECTING) ou d'interrompre une boucle de reconnexion
  automatique en cours de backoff (ERROR) sans attendre la prochaine
  tentative ni fermer l'onglet.
- Plein écran géré localement par l'onglet (sa propre fenêtre racine),
  indépendant de tout mécanisme exposé par l'app hôte.

## Ce qui n'est PAS dans ce plugin

Tout ce qui touche au protocole RFB lui-même (VeNCrypt, transport
WebSockets, ZYWRLE, SASL, QEMU Extended Key Event, `SetDesktopSize`
côté client...) vit dans le fork `asyncvnc2`, pas ici. Voir les patches
séparés déjà rédigés pour ce fork (`shared_flag_patch.py`,
`auth_edge_cases_patch.py`, `robustness_caps_patch.py`,
`rre_corre_patch.py`, `client_requests_patch.py`,
`ROADMAP_completeness.py`) — pas inclus dans ce paquet, qui ne concerne
que l'intégration GTK4 côté `gnome-connection-manager`.

## Hypothèses vérifiées lors de l'intégration

Les deux hypothèses d'intégration ouvertes ont été tranchées contre le
dépôt `gcm4` réel (session 05, 2026-09-05) : conteneur d'onglets =
`Gtk.Notebook` (confirmé, rien à changer), action `win.toggle-fullscreen`
= inexistante côté `gcm4` (invalidée, résolu en local — voir
`docs/sessions/session-05.md`). Aucune hypothèse d'intégration ouverte
à ce jour.

## Dépendance : `asyncvnc2_patched`

Ce plugin s'appuie sur le fork `asyncvnc2` livré séparément (paquet
`asyncvnc2_patched.zip`, 5 patches appliqués — flag `shared`, mots de passe
non-ASCII, plafonds anti-DoS, RRE/CoRRE, `SetDesktopSize`/QEMU Extended Key
Event). État à connaître avant d'enrichir ce plugin :

- Les 5 patches sont vérifiés (exécution réelle ou RFC), **sauf**
  `send_qemu_extended_key_event()` — format écrit de mémoire, jamais
  confirmé contre une source QEMU fraîche. `vnc_tab.py` ne l'utilise pas
  aujourd'hui (aucune trace de `qemu` dans ce fichier) ; si un futur travail
  câble cette extension côté GTK4, tester contre un vrai Proxmox/QEMU en
  priorité plutôt que de faire confiance au format tel quel.
- VeNCrypt existe désormais côté `asyncvnc2` (sous-types TLS* et X509*,
  cf. § « Connexion » pour le réglage `tls_ca_file`, Gcm4 #104). Le
  pinning de clé hôte (`HostKeyStore`) de ce plugin ne couvre toujours que
  l'auth Apple ARD : les sous-types TLS* anonymes ne vérifient aucune
  identité de serveur, et rien ici ne les épingle.
- `VncDisplay.send_text()` (session 20) suppose que `keyboard.press()` se
  comporte comme `keyboard.hold()` pour un nom de touche non reconnu
  (lève une exception, logiquement un `KeyError` vu leur mécanisme de
  résolution partagé) — jamais confirmé contre le fork réel pour
  `press()` spécifiquement (pas livré dans ce paquet). Sans conséquence
  pratique aujourd'hui : `_send_text()` avale déjà toute exception, quel
  qu'en soit le type, pour passer au caractère suivant (cf.
  `docs/sessions/session-20.md`) — à revisiter seulement si un futur test
  contre un vrai serveur révèle un comportement différent de `press()`
  qui invaliderait cette hypothèse.

## Backlog ouvert

Rien d'identifié à ce jour. L'audit « tous les accès à `self.client` qui
écrivent réellement sur le socket » (démarré session 14) est clos depuis
la session 15 : les huit points trouvés (six callbacks GTK/GLib
synchrones — `_on_motion`, `_on_button_pressed`, `_on_button_released`,
`_on_scroll`, `_on_key_pressed`, `_on_local_clipboard_read`, sessions 09 à
14 — et deux actions fire-and-forget — `_send_cad`,
`set_continuous_updates`, session 15) sont tous protégés par un
`try/except Exception: pass`, même style, même cause racine
(`self.client` jamais remis à `None` après une erreur de lecture, cf.
`docs/pieges.md`). Détail complet dans `docs/sessions/session-15.md` et
`tests/test_send_cad_dead_connection.py` /
`tests/test_continuous_updates_dead_connection.py`.

Session 16 (2026-09-10) a ajouté la capture d'écran (§ « Affichage »
ci-dessus) en repartant d'un besoin utilisateur réel plutôt que d'un
nouveau point de l'audit — cf. `docs/sessions/session-16.md`. Idées non
retenues à ce stade, pour rester au périmètre exprimé à l'époque :
raccourci clavier dédié pour la capture, indicateur visuel temporaire sur
l'affichage lui-même plutôt que dans la barre d'outils.

Session 17 (2026-09-11) a repris la première de ces deux idées non
retenues — raccourcis clavier — en l'élargissant au plein écran et à
Ctrl+Alt+Suppr (§ « Entrées » ci-dessus), et a ajouté la reconnexion
automatique après coupure (§ « Connexion » ci-dessus). Les deux ont été
proposées à l'utilisateur (recherche assistée par le connecteur MCP
Context7 pour vérifier les API GTK4 réellement disponibles avant de les
proposer) puis confirmées avant implémentation, plutôt que décidées
unilatéralement sous « ajoute toutes les fonctions manquantes » — cf.
`docs/sessions/session-17.md`. Idée écartée à cette occasion (dépend du
fork `asyncvnc2_patched`, pas livré dans ce paquet, cf. § « Dépendance »
ci-dessus) : presse-papiers image en plus du texte.

Session 18 (2026-09-11) a ajouté la copie de la capture d'écran dans le
presse-papiers (§ « Affichage » ci-dessus), en repartant à nouveau d'un
besoin utilisateur réel plutôt que d'un nouveau point de l'audit — même
démarche que la session 16 (capture PNG), pas de phase de confirmation
préalable façon session 17 puisque la demande de continuation n'était pas
une longue liste non bornée à trier. Idées écartées à cette occasion, pour
rester une feature unique : raccourci clavier dédié pour la copie
presse-papiers (la capture PNG en a un depuis la session 17,
`<Shift>F12`, mais rien n'indique un besoin équivalent ici sans
confirmation), retour visuel sur l'affichage lui-même (déjà écarté pour la
capture PNG en session 16, cf. ci-dessus) — cf.
`docs/sessions/session-18.md`.

Session 19 (2026-09-11) a repris la première de ces deux idées
différées — raccourci clavier dédié pour la copie presse-papiers,
`<Control>F12` (§ « Entrées » ci-dessus) — sur une simple continuation
générale de l'utilisateur, sans repasser par une confirmation explicite
point par point (jugée suffisamment bornée : une entrée de table, pure
délégation, cf. `docs/sessions/session-19.md`). Idée encore écartée à ce
stade : retour visuel temporaire sur l'affichage lui-même (toujours pas de
besoin exprimé, cf. sessions 16 et 18 ci-dessus).

Session 20 (2026-09-12) a ajouté l'envoi de texte au serveur distant comme
suite de frappes clavier (§ « Entrées » ci-dessus), en repartant à nouveau
d'un besoin utilisateur réel plutôt que d'un nouveau point de l'audit —
même démarche que les sessions 16 et 18, pas de phase de confirmation
préalable façon session 17 puisque la demande de continuation n'était pas
une longue liste non bornée à trier. API GTK4 vérifiée contre
`docs.gtk.org` avant écriture (`Gdk.unicode_to_keyval()`), MCP Context7
non connecté cette fois — même situation que la session 18. Idées écartées
à cette occasion, pour rester une feature unique : raccourci clavier dédié
pour ouvrir le dialogue de saisie (aucun besoin exprimé, contrairement au
raccourci de la session 19 qui reprenait une piste déjà différée en
session 18) ; garder le dialogue ouvert après l'envoi pour permettre un
envoi répété (fermeture systématique retenue à la place, plus simple,
cohérente avec le dialogue de confirmation d'oubli de clé ; repris depuis
par #93, case à cocher) — cf. `docs/sessions/session-20.md`.

Session 21 (2026-09-13) a ajouté le bouton « Déconnecter » (§ « Connexion »
ci-dessus), encore à partir d'un besoin utilisateur réel (fermer la
connexion sans fermer l'onglet) plutôt que d'une des deux pistes différées
en session 20 — aucune des deux n'a de besoin exprimé qui la rendrait
prioritaire par défaut, cf. « Prochaine étape suggérée » ci-dessous à
l'époque. Contrairement aux sessions 16-20, aucune nouvelle logique de bas
niveau à écrire : le bouton appelle `VncDisplay.stop()`, déjà existante et
déjà partiellement testée (annulation de la reconnexion automatique en
attente, `tests/test_auto_reconnect.py`) — seule la décision de
sensibilité du bouton (`VncTab._resolve_disconnect_button_sensitivity()`,
sensible sauf DISCONNECTED) est nouvelle et testée. Icône
`process-stop-symbolic` vérifiée par recherche web (remplaçante
documentée de l'ancien `GTK_STOCK_STOP` depuis GTK 3.10, cf.
`docs.gtk.org`) — Context7 non connecté. Aucune idée écartée à noter cette
fois (pas d'alternative de conception sérieuse envisagée au-delà de la
sensibilité du bouton elle-même) — cf. `docs/sessions/session-21.md`.

Session 22 (2026-09-14) a ajouté l'icône « Coller » sur le champ du
dialogue « Envoyer du texte » (§ « Entrées » ci-dessus) — complément
naturel de la session 20, plutôt qu'une des deux pistes différées à
l'époque (toujours aucun besoin exprimé pour l'une ou l'autre). Comme en
session 21, essentiellement de la coordination GTK réutilisant un
mécanisme déjà en place (`read_text_async`/`read_text_finish`, déjà
utilisés pour la surveillance du presse-papiers local) ; seule la
décision « que faire du texte lu » (`VncTab._resolve_pasted_clipboard_text()`)
est nouvelle et testée, à l'identique du choix fait en session 21 de
n'extraire et tester QUE la petite décision pure, pas la construction du
dialogue elle-même. API `Gtk.Entry.set_icon_from_icon_name()`/signal
`icon-press`/`set_icon_tooltip_text()` vérifiées contre `docs.gtk.org` et
`api.pygobject.gnome.org` — Context7 non connecté. Choix délibéré de
préremplir le champ plutôt que d'envoyer directement le contenu du
presse-papiers : l'utilisateur voit et peut corriger avant confirmation
(cf. `docs/sessions/session-22.md` pour l'alternative envisagée et
écartée : un bouton de barre d'outils séparé envoyant le presse-papiers
sans passer par le dialogue, jugé moins sûr).

Session 23 (2026-09-14) a complété le message affiché pendant l'attente
d'une reconnexion automatique (§ « Connexion » ci-dessus) — un vrai manque
d'UX identifié en relisant le mécanisme de la session 17 : le délai avant
la prochaine tentative n'existait que dans les logs
(`logger.info` dans `_schedule_auto_reconnect`), l'utilisateur ne voyait
qu'un message d'erreur brut sans savoir qu'une reconnexion était prévue.
Câblage dans `VncDisplay._set_status()`, en amont de l'émission du signal
`vnc-status-changed` — sans changer la signature de
`_schedule_auto_reconnect()` (le délai est recalculé une deuxième fois,
délibérément, plutôt que de le lui faire remonter en paramètre : ça
aurait cassé les tests existants qui la monkeypatchent en 0 argument,
cf. `tests/test_auto_reconnect.py`). Pas de minuteur d'affichage en
temps réel (décompte seconde par seconde) : message statique fixé au
moment de la planification, pour rester une feature bornée sans ajouter
un deuxième système de minuteur à câbler et tester en plus de celui déjà
en place.

Prochaine étape suggérée pour une future session : toujours pas de
nouveau point de la famille « accès à `self.client` » à chercher (audit
exhaustif, cf. ci-dessus). Trois pistes différées à ce jour (raccourci
clavier pour ouvrir le dialogue d'envoi de texte, depuis la session 20 ;
dialogue restant ouvert pour un envoi répété, depuis la session 20 et
livré par #93 ;
décompte en temps réel pour la reconnexion automatique plutôt qu'un
message statique, depuis la session 23), à reprendre seulement si un
besoin réel se confirme — pas par défaut. Sinon, continuer à repartir
exhaustif, cf. ci-dessus). Le raccourci clavier du dialogue d'envoi de
texte, différé depuis la session 20, est fait (`<Alt>F12`, Gcm4 #92).
Deux pistes différées restent, à réaliser (issues rouvertes par l'autrice
le 12/10/2026) : dialogue restant ouvert pour un envoi répété (session 20,
Gcm4 #93) ; décompte en temps réel pour la reconnexion automatique plutôt
qu'un message statique (session 23, Gcm4 #94). Sinon, continuer à repartir
d'un besoin utilisateur réel ou d'une extension protocolaire côté
`asyncvnc2_patched` (cf. § « Dépendance » ci-dessus) plutôt que d'inventer
une fonctionnalité hors périmètre.

Session 24 (2026-09-15) a commencé par une demande explicite de
correction des erreurs ruff. Investigation détaillée (cf. nouvelle
entrée `docs/pieges.md`) : le ruleset configuré dans `pyproject.toml`
(`select = ["E", "F", "W", "C90"]` + `ignore` long) ne remonte AUCUNE
erreur (`ruff check`/`ruff format --check` déjà propres, comme à chaque
session précédente) ; ce n'est qu'avec `ruff check --isolated` (jeu de
règles par défaut, hors config du projet) que 57 signalements
apparaissent. Analysés un par un : l'écrasante majorité correspond à des
catégories délibérément écartées par la config du projet (le patron
`except Exception` du fichier, la préférence de style `Optional[X]`, les
`datetime` naïfs intentionnels pour un horodatage local de nom de
fichier, `__gsignals__` qui est un patron PyGObject requis) — corriger
ces catégories aurait dégradé le projet, pas amélioré sa qualité. Seuls 5
signalements réellement sans rapport avec ces choix ont été corrigés :
deux imports de test morts (`os`, `types`), une variable de tuple
réellement inutilisée dans un test (renommée `_public_key`, convention
déjà en place dans le fichier), un `dict()` réécrit en littéral, un
shebang de `vnc_tab.py` rendu exécutable (`chmod +x`). Le tri d'imports
(`I001`) a été explicitement écarté : `ruff --fix` proposait de
réordonner le bloc d'imports de `vnc_tab.py` d'une façon qui aurait
déplacé `from gi.repository import Gtk, ...` par rapport à
`gi.require_version("Gtk", "4.0")`, cassant potentiellement un ordre
obligatoire pour PyGObject.

Suite à ça, feature ajoutée à partir d'un besoin utilisateur réel :
inversion du sens de la molette (« défilement naturel » façon trackpad
macOS, § « Entrées » ci-dessus), en réglage de connexion
(`VncConnectionInfo.invert_scroll`, défaut `False`) plutôt qu'en bouton
de barre d'outils, cohérent avec `sync_clipboard`/`shared`/etc. déjà
traités ainsi. Pas de nouvelle vérification d'API GTK4 nécessaire cette
fois (aucune nouvelle fonction GTK/GDK utilisée) — cf.
`docs/sessions/session-24.md`.

Documenter ici, une fois câblée, toute extension protocolaire qui
dépend d'`asyncvnc2` (QEMU Extended Key Event notamment, cf.
ci-dessus) — pour l'instant rien de tout ça n'est utilisé dans ce
plugin.
