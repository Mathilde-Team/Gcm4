# Comparaison PATTERNS.md (project-skeleton) ↔ ce dépôt (GCM/gcm4)

> Analyse du 2026-09-06. Réponse à une demande explicite : comparer le
> catalogue `PATTERNS.md` d'un projet tiers (« project-skeleton », lui-même
> dérivé du `CLAUDE.md` de « switch-capture ») contre le code et la
> documentation réels de ce dépôt, section par section, en vérifiant
> chaque règle dans le code source (grep/lecture) plutôt que par
> supposition.
>
> **Précision de contexte** : GCM (GNOME Connection Manager) n'a **aucun
> lien de filiation** avec project-skeleton ni switch-capture — c'est une
> application open-source préexistante et indépendante (gestionnaire de
> connexions SSH/RDP/VNC/etc.), pas un projet dérivé du squelette. Toute
> ressemblance de pratique relevée ci-dessous est une convergence
> indépendante, pas un héritage commun.
>
> GCM est actuellement **GTK3** (migration GTK4 planifiée, non commencée —
> voir `claude.md` § Chantier prioritaire). Les règles de PATTERNS.md
> rédigées en termes GTK4 sont donc évaluées ici pour leur **équivalent
> GTK3** quand un existe, et marquées non applicables sinon.

---

## 1. Architecture générale

**Un seul exécutable, un seul fichier source, arbitrant CLI/GTK4/auto** —
**non applicable.** GCM n'a pas de mode CLI complet à arbitrer : l'option
`--config PATH` (backlog #80, `claude.md` § « il y a dix sessions ») ne
fait que choisir un dossier de configuration au démarrage, elle ne bascule
jamais vers un mode sans fenêtre. Il n'existe qu'un seul point d'entrée
GUI (`gnome_connection_manager.py::main()`), pas de dichotomie CLI/GUI à
arbitrer comme dans le squelette.

**Séparation core / cli / gtk** — **implémenté ET documenté.**
`gcm4_core.py` (zéro import `gi`/`Gtk`/`Vte`, vérifié dans cette session en
l'important seul : `python3 -c "import gcm4_core"` réussit) contient le
métier (chiffrement, résolution `--config`, ports par défaut, zoom,
couleurs, masquage cluster, `auto_close_tab`, `workspace_hosts`...) ;
`gnome_connection_manager.py` porte tout le GTK. Documenté en détail dans
`claude.md` § Architecture réelle (lignes 72-98, description fichier par
fichier) et dans `features.md` (entrée « Séparation cœur métier / cœur
GTK4 », § déjà fait). Pas d'équivalent `app_cli.py` séparé puisqu'il n'y a
pas de mode CLI (voir point précédent) — seul le clivage core/gtk
s'applique, pas le troisième pan « cli ».

**GUI : plusieurs pages plutôt qu'un formulaire à rallonge** —
**implémenté**, via l'équivalent GTK3 de `Gtk.Stack`/`Gtk.StackSwitcher` :
`Gtk.Notebook`. Vérifié : `Whost` utilise `self.nbHost` (`Gtk.Notebook`,
`gnome_connection_manager.py:5553` `nbHost.insert_page(page, ...)`) pour
loger la page générique à côté de la page spécifique à chaque protocole
(`plugin.build_edit_page()`, `plugins/plugin_base.py:209`). Documenté dans
`claude.md` (description de `Whost`, § Architecture réelle) mais sans
jamais nommer explicitement ce principe d'organisation en pages — le
*fait* est documenté, la *règle générale* qu'il illustre ne l'est pas.

---

## 2. Concurrence

**`prepare()` / étape bloquante séparés (deux `Thread` distincts pour deux
phases déclenchées à des moments différents)** — **non applicable.** Tous
les usages de `threading.Thread` trouvés dans le dépôt
(`plugins/plugin_import_libvirt.py:930`, `plugin_import_ovirt.py:819`,
`plugin_import_proxmox.py:851`, `plugin_import_virtualbox.py:816`,
`plugin_netmiko_push.py:699`, `plugin_snmp_push.py:889`,
`plugin_spice.py:639/1054`, `plugin_vnc.py:462`, et `CheckUpdates(Thread)`
à `gnome_connection_manager.py:7529`) sont des workers à **une seule
phase** (`daemon=True`, démarré une fois, tourne jusqu'à la fin). Aucun
workflow du type « installer puis démarrer plus tard » n'existe dans le
domaine de GCM — pas de tâche équivalente à trouver.

**Ctrl+C (SIGINT) propre — CLI** — **non applicable** (pas de mode CLI,
voir § 1).

**Ctrl+C (SIGINT) propre — GTK** — **implémenté de façon insuffisante,
non documenté.** `utils.py::GCMBase.run()` (ligne 240) :
```python
try:
    Gtk.main()
except KeyboardInterrupt:
    pass
```
C'est précisément l'approche que le principe même de PATTERNS.md qualifie
d'insuffisante : aucun `GLib.unix_signal_add()`, donc le `KeyboardInterrupt`
ne remonte que ponctuellement (quand la boucle GLib rend la main à
l'interpréteur), à un point indéterminé de l'exécution, et n'est
rattaché à **aucun** chemin de fermeture propre — le `except: pass` avale
l'exception sans jamais appeler `writeConfig()` ni le nettoyage que fait
`on_wMain_destroy()`. Recherche de `SIGINT`/`KeyboardInterrupt` dans
`claude.md`/`features.md`/`CONSIGNES-AGENTS-IA.md` : **aucun résultat** —
ni implémenté correctement, ni documenté comme limite connue.

**Point de fermeture unique** — **violation concrète trouvée, non
documentée.** Trois chemins de fermeture coexistent et **divergent** :
- `on_wMain_delete_event` (`gnome_connection_manager.py:4243`, signal
  `delete-event`, clic sur le bouton X) : capture `conf.WINDOW_WIDTH/HEIGHT`
  **et** demande confirmation si `conf.CONFIRM_ON_EXIT` et des consoles
  sont ouvertes (`msgconfirm(...)`, bloque la fermeture si annulé).
- `on_wMain_destroy` (`:4230`, signal `destroy`) : `self.writeConfig()` +
  `Gtk.main_quit()`.
- `on_salir1_activate` (`:4374`, menu « Quit », `win.quit`) : **duplique**
  la capture de taille de fenêtre + `writeConfig()` + `Gtk.main_quit()`,
  mais **sans jamais appeler la confirmation** de `on_wMain_delete_event`.
  Choisir « Quitter » dans le menu ferme donc l'application avec des
  consoles ouvertes sans poser la question, contrairement au bouton X —
  divergence de comportement directe, du type que ce principe est censé
  prévenir. Recherche dans `claude.md`/`features.md` : **aucune mention**
  de `on_salir1_activate`, `CONFIRM_ON_EXIT` ou de ce point précis.

---

## 3. Motifs d'interface (GTK3, équivalents des motifs GTK4 du squelette)

**Champs conditionnels** — **implémenté, mais pas selon la structure
décrite.** 8 occurrences de `set_visible(is_vte)` dispersées dans
`gnome_connection_manager.py` (ex. lignes autour de 6090-6140, section
« Pages spécifiques à chaque protocole » de `Whost`) gèrent la visibilité
conditionnelle des champs `Logging`/`TERM`/`Close console` selon le
protocole sélectionné — mais via des appels directs répétés, pas via une
classe générique à deux dictionnaires (widget de saisie / ligne complète)
comme `ConditionalRow` du squelette. Aucune trace d'une telle abstraction
dans GCM (`grep -rn "ConditionalRow\|ConditionalField"` : 0 résultat).

**Scrollbars classiques, jamais de défilement horizontal en overlay** —
**non implémenté.** `grep -rn "overlay_scrolling"` sur tout le dépôt :
**0 résultat**. `Gtk.PolicyType` est utilisé de façon incohérente selon les
zones scrollables (mélange `AUTOMATIC`/`NEVER`/`ALWAYS` selon l'endroit),
sans convention unifiée. Non documenté nulle part comme choix (ni comme
lacune).

**Menu hamburger + page Préférences (actions sur la fenêtre, singleton)**
— **partiellement implémenté, partiellement documenté.**
- Les actions sont bien posées sur la fenêtre (`win.*`), jamais
  l'application (`app.*`) : confirmé par grep — `win.cluster`,
  `win.open-workspace`, `win.quit` (`gnome_connection_manager.py`,
  section `add_action(...)` autour de la ligne 2100-2120, et
  `tools_section.append(...)`/`file_section3.append(...)` pour le
  `Gio.Menu` correspondant). **Documenté** — mais seulement en passant,
  dans `claude.md`, journal de session du 2026-09-05 (entrée « espace de
  travail »), pas dans une section dédiée aux conventions d'architecture
  GTK.
- Le singleton existe réellement : `open_management_tab(key, title,
  factory)` (`gnome_connection_manager.py:3007`, méthode documentée
  « Si un onglet portant déjà cette clé est ouvert, lui redonne
  simplement le focus plutôt que d'en recréer un second ») est utilisé
  pour Préférences (`"settings"`) et Cluster (`"cluster"`). Implémenté
  comme **onglet épinglé dans le notebook principal**, pas comme fenêtre
  séparée (différence d'implémentation par rapport au squelette, même
  principe de singleton). **Non documenté** dans `claude.md`/`features.md`
  — ni le mécanisme, ni le principe qu'il incarne.

**Préférences persistantes : fusion à l'écriture, jamais un écrasement**
— **non implémenté, prémisse elle-même différente.** `writeConfig()`
(`gnome_connection_manager.py:3826`) fait `cp = configparser.RawConfigParser()`
(vide) puis lit **`CONFIG_FILE + ".tmp"`** à la ligne 3834 — pas le
fichier réel (`CONFIG_FILE`, lu ailleurs par `loadConfig()` à la ligne
3399). Le `.tmp` n'existe normalement pas (il est renommé vers le fichier
réel juste après une écriture réussie, ligne 3914 — mécanisme d'écriture
atomique correct en soi) : `writeConfig()` reconstruit donc la totalité du
fichier depuis l'état mémoire courant à chaque sauvegarde, sans jamais
fusionner avec un contenu existant. Toute clé qu'un outil externe (ou une
version future de GCM) aurait ajoutée sans que la version courante la
connaisse serait silencieusement perdue au prochain `writeConfig()`. Cela
dit, la prémisse même du principe du squelette (un même fichier partagé
entre un usage GUI et un usage CLI/scripté qui doivent coexister sans
s'écraser) ne correspond à rien dans GCM : il n'y a qu'un seul point
d'entrée qui écrit `gcm.ini`. Non documenté comme lacune.

**Icône de l'application** — **non applicable**, mécanisme différent.
GCM a une icône réelle et déjà intégrée (`icon.png` à la racine, pas un
squelette qui en manque). Chargement via `set_icon_from_file(ICON_PATH)`
(confirmé à 7 endroits : `gnome_connection_manager.py:319/391/977/6402/7559`,
`utils.py:144`, `widgets.py:704`), **pas** via `Gtk.IconTheme`/recherche
dans une racine `hicolor/` — le piège précis documenté par le squelette
(racine vs `hicolor/` elle-même) ne peut donc pas se produire ici, aucun
chemin de recherche de thème d'icônes n'étant utilisé. Le fichier
`.desktop` (`gnome-connection-manager.desktop`) référence l'icône par
**chemin absolu** (`Icon=/usr/share/gnome-connection-manager/icon.png`),
pas par nom symbolique de thème — cohérent avec cette approche.

---

## 4. Sudo, affichage graphique, session distante

**Diagnostic actionnable sudo + GUI + RDP** — **non implémenté, non
documenté, pertinence réduite pour GCM.** `grep -n "cannot open
display\|RuntimeError.*display\|Gtk.init_check"` : aucun résultat — GCM
ne diagnostique jamais spécifiquement un échec de connexion à l'affichage.
Un usage `sudo` existe bien dans le dépôt (`plugin_spice.py`, écriture
privilégiée d'une entrée dans `/etc/hosts` pour un tunnel SPICE via
`_write_via_sudo`) mais c'est un scénario différent (élever une opération
ponctuelle précise, pas lancer toute l'application via `sudo`). GCM
n'ayant normalement pas besoin de `sudo` pour se lancer (contrairement à
switch-capture et son besoin de `CAP_NET_ADMIN`), ce diagnostic est moins
central pour ce projet — mais son absence reste un vrai manque si
quelqu'un lance malgré tout GCM via `sudo` sur une session RDP.

**Contournement GVFS/GOA (`GIO_USE_VFS`/`GIO_USE_VOLUME_MONITOR`)** —
**non implémenté, non documenté.** `grep -rn "GIO_USE_VFS\|GIO_USE_VOLUME_MONITOR\|gvfs"`
sur tout le dépôt (code et docs) : **0 résultat**. Pourtant GCM utilise
des sélecteurs de fichiers dans plusieurs endroits (`gnome_connection_manager.py`,
`ssh_key_manager_dialog.py`, `plugins/plugin_export_csv.py`,
`plugin_export_json.py`, `plugin_import_csv.py`, `plugin_import_json.py`,
`plugin_import_putty.py`, `plugin_netmiko_push.py`, `plugin_snmp_push.py`,
`utils.py`) — un usage en session RDP (cas d'usage réaliste pour un
gestionnaire de connexions à distance) pourrait rencontrer exactement le
type de plantage/avertissement GVFS que ce contournement prévient.

---

## 5. Secrets

**Chaîne de résolution explicite > env > trousseau > repli KeePass** —
**non applicable, modèle de sécurité différent par construction.**
`grep -rn "keyring\|secretstorage\|libsecret\|pykeepass"` sur tout le
dépôt : **0 résultat**. GCM n'intègre ni trousseau système ni KeePass —
il est **lui-même** le gestionnaire de secrets (mots de passe chiffrés
directement dans `gcm.ini` via `pyAES.py`, AES-256/OFB, et
`gcm4_core.py::encrypt/decrypt`). C'est l'inverse du modèle du squelette,
pas une variante dégradée : le squelette délègue toujours à un stockage
externe, GCM stocke lui-même par conception (c'est sa fonction même).

**Aucun secret n'est jamais écrit sur disque par l'outil lui-même** —
**non applicable, contredit délibérément par le modèle de GCM.** GCM
écrit bel et bien des mots de passe chiffrés dans son propre fichier de
configuration — c'est le cœur de sa proposition de valeur (mémoriser les
identifiants de connexion). Ce choix est implicitement documenté
(`claude.md:139` `pyAES.py — AES-256/OFB pur Python (mots de passe)`,
`features.md:140` `✅ Chiffrement AES des mots de passe`) et une évolution
du modèle de menace est déjà identifiée : ajouter un mot de passe maître
(`claude.md`, § Prochaines étapes, « ajouter le master password ») — mais
la migration vers « ne jamais stocker soi-même » n'est ni prévue ni
souhaitée, le produit perdrait sa fonction.

**Confirmation avant action destructrice : retaper une valeur
identifiante** — **non implémenté, non documenté.** La suppression d'un
hôte **ou d'un groupe entier** (`on_btnDel_clicked`,
`gnome_connection_manager.py:4561`) ne demande qu'une confirmation
Oui/Non (`msgconfirm("%s [%s]?" % (_("Do you really want to remove..."),
name))`) — jamais de resaisie du nom/de l'IP. Recherche de
`retaper`/`confirm_match`/`retype` dans toute la documentation : **0
résultat**.

**Journalisation d'audit systématique (commande loguée AVANT envoi)** —
**non implémenté, non documenté.** Ni `vte_feed()`
(`gnome_connection_manager.py:609`, écriture directe sur le fd PTY) ni
`send_cluster_commands()` (`Wcluster`, câblée avec le masquage `#P=` lors
de la session du 2026-09-01) ne contiennent le moindre appel
`app_logger.*` journalisant la commande envoyée — vérifié par grep dans
le corps des deux fonctions. C'est une vraie lacune de sécurité/traçabilité
compte tenu de la fonction même de ces deux chemins (envoyer des
commandes, potentiellement sensibles, à des systèmes distants).

**Principe du moindre privilège (capacités Linux, binaire helper séparé)**
— **non applicable.** Domaine entièrement différent : GCM ne fait aucune
capture réseau bas niveau, aucun besoin de `CAP_NET_ADMIN` ni de binaire
privilégié séparé. L'usage `sudo` trouvé (`plugin_spice.py`, écriture dans
`/etc/hosts`) est ponctuel et n'a pas d'équivalent architectural au
mécanisme `setcap`/binaire C minimal du squelette.

---

## 6. Internationalisation (gettext)

**Domaine gettext partagé, résolution dev/installé, `fallback=True`** —
**implémenté et documenté.** `gcm4_core.py:260` : `fallback=True` dans
l'appel à `gettext.translation(...)` (fonction `bindtextdomain()`,
docstring : « Injecte `_()` dans les builtins... ainsi la fonction de
traduction devient disponible sans import dans tout le reste du code »).
Domaine et catalogues documentés dans `claude.md` (§ Architecture réelle)
et dans le `Makefile` (cibles `msgfmt lang/<locale>.po -o
lang/<locale>/LC_MESSAGES/gcm-lang.mo` pour de/en/fr/it/ko/pl/pt_BR, et
bn_BD ajouté lors d'une session antérieure, `claude.md` § « il y a neuf
sessions »).

**Seuls les libellés d'interface dans `_()`, jamais les messages de
logger** — **implémenté (par la pratique), non formalisé comme règle.**
`grep -n 'app_logger\.\(debug\|info\|warning\|error\)(_('` sur les
fichiers cœur : **0 résultat** — la convention est bien respectée dans les
faits, mais elle n'est écrite nulle part (ni dans `CONSIGNES-AGENTS-IA.md`,
ni dans `claude.md`) contrairement au squelette qui l'énonce explicitement.

**Test de complétude i18n (extraction `ast` des `_()`, comparaison au
`.mo` compilé, sans dépendance GTK réelle)** — **non implémenté.**
`find tests -iname "*i18n*"` : aucun fichier. Le `Makefile` compile les
catalogues (`msgfmt`) mais n'extrait ni ne vérifie la complétude des
chaînes (`xgettext` absent des cibles du `Makefile` inspectées). Aucun
test ne verrouille contre une régression de traduction oubliée.

---

## 7. Tests

**`gi.require_version()` sans filet bloque toute la collecte pytest** —
**le même symptôme existe, et n'est pas mitigé.** Vérifié en conditions
réelles dans cette session :
```
$ python3 -m pytest tests/ -q
ERROR collecting tests/test_gcm.py
  gnome_connection_manager.py:67: ...
  key_picker_dialog.py:53: in KeyPickerDialog
E   AttributeError: module 'GObject' has no attribute 'SignalFlags'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.18s
```
Zéro test exécuté, y compris les 65 tests de `test_gcm4_core.py`,
`test_ssh_migrate_gcm.py`, `test_snmp_bulk_core.py`,
`test_putty_import_core.py` — sans aucun rapport avec GTK. Le mécanisme
déclencheur diffère de celui décrit par le squelette (ici, une
`AttributeError` dans le stub GTK maison de `test_gcm.py` plutôt qu'un
`ValueError` de `gi.require_version()` brut) mais l'**effet est
identique**. `features.md` (lignes ~180-211) documente déjà que le stub
GTK de `test_gcm.py` est cassé (`GObject.SignalFlags` manquant) — mais ne
documente **pas** la conséquence plus large que cela bloque toute
collecte pytest si on ne pense pas à exclure ce fichier. Aucun
équivalent `tests/conftest.py::require_gtk4()` (ou `require_gtk3()`)
n'existe pour isoler ce risque au niveau collecte. Conséquence concrète :
`make test` (`Makefile:163`, `python3 -m pytest tests/ -v`, sans
exclusion) **échoue entièrement** dans un environnement sans GTK3/VTE
réels — non documenté comme tel.

**Suite pytest hermétique, validation réelle documentée à part** —
**implémenté en pratique et documenté, par convention plutôt que par
mécanisme structurel.** `CONSIGNES-AGENTS-IA.md` § 5-6 et de nombreuses
entrées de `claude.md`/`features.md` (« câblage GTK non exécuté en
conditions réelles, pas de GTK/VTE dans cet environnement ») répètent
cette limite explicitement à chaque session touchant du code GTK. Pas de
mécanisme de saut automatique comme `require_gtk4()` (voir point
précédent) — c'est une discipline documentée et suivie par convention,
pas appliquée par un garde-fou dans le code des tests eux-mêmes.

**Fakes conçus pour échouer bruyamment sur un appel illégal** —
**implémenté, non documenté comme principe délibéré.** `FakePlugin`/
`FakePluginRegistry` (`tests/test_gcm4_core.py`, depuis l'extraction du
2026-08-30) et `FakeHost` (ajouté lors de la session « Espace de
travail ») n'implémentent que les méthodes réellement utilisées par le
code testé — aucune méthode générique attrape-tout. Le résultat (un appel
à une méthode non prévue lève `AttributeError`) correspond exactement au
principe du squelette, mais rien dans le code ou la documentation
n'explique que c'est un choix délibéré plutôt qu'une simple économie
d'écriture.

**Isolation `sys.modules` lors du mock de `gi`** — **non implémenté,
plus fragile que le squelette.** `tests/test_gcm.py:493` fait
`sys.modules["gi"] = _gi` **une seule fois**, au niveau module, sans
jamais sauvegarder l'état précédent ni le restaurer. Aucune fonction
`_snapshot_gi_modules`/`_restore_gi_modules` n'existe dans ce dépôt. Le
risque que décrit le squelette (contaminer un autre fichier de test qui
aurait besoin d'un `gi` différent dans la même session pytest) est donc
structurellement possible ici — non observé actuellement seulement parce
qu'aucun autre fichier de test de ce dépôt ne touche à `gi` autrement (et
que, de toute façon, `test_gcm.py` échoue déjà à la collecte pour une
autre raison, voir plus haut).

**Piège de capture d'écran sous Xvfb sans gestionnaire de fenêtres** —
**non applicable.** Aucun test de capture d'écran dans ce dépôt.

---

## 8. Packaging

**Source unique, N méthodes de build sans dupliquer le code** —
**implémenté, avec une structure différente.** GCM n'a pas de séparation
`src/` vs scripts de packaging à la racine (pas de layout `src/` du tout
— les modules Python sont directement à la racine du dépôt) mais le
principe « ne pas dupliquer le code trois fois » est respecté : le
`Makefile` a des cibles `deb`/`rpm`/`opensuse` qui appellent toutes
`$(MAKE) install DESTDIR=...` puis empaquettent le résultat via `fpm` —
une seule copie source, trois emballages.

**Architecture `all`/`noarch` → réelle dès qu'un binaire compilé existe**
— **non applicable.** GCM n'a aucun binaire compilé/helper privilégié
(voir § 5, moindre privilège) — la question ne se pose pas.

**GUI toujours en dépendance recommandée, jamais obligatoire** —
**non suivi, divergence concrète vérifiée.** La cible `deb` du `Makefile`
(lignes 97-115) déclare **tout** en dépendance dure (`fpm -d ...`), y
compris `python3-gi`/`gir1.2-gtk-3.0` (le GTK lui-même — défendable, GCM
ne peut fonctionner sans) **mais aussi** des bibliothèques spécifiques à
un usage protocolaire précis : `gir1.2-gtk-vnc-2.0`, `gir1.2-spiceclientgtk-3.0`,
`freerdp2-x11 | freerdp3-x11`, `python3-paramiko`. Aucun usage de
dépendance « recommandée » (`fpm` supporte `--deb-recommends`, jamais
utilisé ici) pour ces bibliothèques pourtant liées à un seul protocole
parmi d'autres (quelqu'un qui n'utilise GCM que pour SSH doit quand même
installer les bindings VNC/SPICE/RDP). Au niveau Python/Poetry en
revanche, le principe **est** suivi : `pyproject.toml`
`[project.optional-dependencies]` (netmiko, paramiko, libvirt,
pycryptodomex pour VNC) sont bien des extras, pas des dépendances dures —
mais cette distinction ne survit pas jusqu'au paquet `.deb`/`.rpm` final.
Non documenté comme divergence.

---

## 9. Pratique de journalisation de session (méta)

**Implémenté et documenté de façon quasi identique.** `claude.md` de ce
dépôt EST ce journal : chaque session y est datée, documente ce qui a été
fait, ce qui a été vérifié réellement (`python3 -m py_compile`, `pytest`,
`ruff check`/`format` par comparaison miroir — présent dans la quasi
totalité des entrées du journal), et ce qui reste ouvert (§ « En tête de
liste »). La convention de compression progressive des sessions les plus
anciennes en une phrase (`claude.md`, queue du journal — actuellement
compressée à partir de la 8ᵉ session en arrière) répond au même besoin que
la section « pratique de journalisation » du squelette : garder le
détail récent complet sans laisser le fichier grossir indéfiniment. Note
de filiation : GCM n'a pas hérité cette pratique de switch-capture ou du
squelette (aucun lien entre les projets) — convergence indépendante vers
un besoin similaire (garder trace de ce qu'un agent IA a réellement
vérifié d'une session à l'autre).

---

## 10. Traçabilité systématique des fonctions (entrée/sortie)

**Implémenté selon un mécanisme différent (manuel, pas un décorateur),
documenté explicitement, couverture partielle et non rétroactive.**
`CONSIGNES-AGENTS-IA.md` § 3 impose « au moins 2 `debug()` par
fonction/méthode non triviale » avec le même objectif que
`tracing.py::traced` du squelette (couvrir l'entrée et *chaque* sortie,
y compris les retours anticipés et les exceptions) — **mais** :
- **Mécanisme** : appels `app_logger.debug()` écrits à la main à chaque
  site d'appel (l'exemple canonique de la règle en donne le modèle), pas
  un décorateur unique appliqué systématiquement. Pas de fichier
  équivalent à `tracing.py` dans ce dépôt.
- **Redaction des secrets** : gérée par la discipline de l'auteur au cas
  par cas (« jamais un mot de passe... en clair — indiquer `(masqué)` »,
  voir `get_password()` dans `gcm4_core.py`), pas structurellement
  impossible à violer comme le décorateur du squelette (qui ne journalise
  *jamais* le moindre argument par construction, seul `__qualname__`
  l'est). Une fonction qui oublierait la redaction manuellement le
  ferait — le squelette ne peut pas.
- **Couverture** : la règle s'applique au code touché depuis le
  2026-08-30 (`gcm4_core.py` et le code neuf/retouché), **pas** de façon
  rétroactive à tout `gnome_connection_manager.py` préexistant — vérifié :
  `Wmain.__init__` (première centaine de lignes de la classe, ligne 938+)
  et `vte_feed()` (`:609`) ont **zéro** appel `app_logger.*`. Le
  décorateur du squelette, lui, couvre uniformément tout `src/` y compris
  `__init__`/gestionnaires de signaux GTK, par construction.

---

## 11. Absence de dépendances circulaires entre modules

**Implémenté, documenté, avec une différence structurelle sur
l'intégration aux tests.** `tools/check_circular_imports.py` existe,
analyse le graphe d'imports réel par `ast` (pas une relecture manuelle),
ignore délibérément les imports différés en corps de fonction et les
imports sous `TYPE_CHECKING` (même logique que le squelette). Documenté
dans sa propre docstring de module, dans `.pre-commit-config.yaml`
(commentaire expliquant le `pass_filenames: false`), et dans `claude.md`
§ « il y a huit sessions » (création lors du découpage des plugins,
2026-08-30). Différence : c'est un **script autonome lancé en hook
pre-commit**, pas un fichier `tests/test_no_cross_imports.py` exécuté par
pytest (`find tests -iname "*circular*"` : 0 résultat) — il ne tourne
donc **pas** avec `make test`/`pytest tests/`, seulement au commit (si
pre-commit est installé) ou en invocation manuelle. Autre différence
vérifiée : aucune auto-vérification du détecteur lui-même (le squelette
teste son détecteur contre un graphe à cycle volontaire avant de lui
faire confiance ; `grep -n "def test_\|assert.*cycle"
tools/check_circular_imports.py` : 0 résultat — rien de tel ici).

---

## 12. Tests automatisés sur chaque changement

**Non implémenté — divergence importante et vérifiée.**
`.pre-commit-config.yaml` (lu intégralement) ne contient **aucun hook
pytest** : seulement ruff (lint + format), une hygiène de base
(espaces/fin de fichier/conflits de merge/gros fichiers) et le détecteur
de cycles d'imports (§ 11). Contrairement au principe même du squelette
(« la suite complète tourne à CHAQUE commit »armé via pre-commit), GCM
**n'exécute jamais pytest automatiquement au commit** — l'exécution des
tests reste une discipline manuelle documentée dans
`CONSIGNES-AGENTS-IA.md` (et suivie systématiquement dans le journal de
`claude.md`, chaque session listant les commandes `pytest` lancées), pas
un mécanisme qui empêcherait mécaniquement un commit non testé de passer.
Non documenté comme limite : rien dans `claude.md`/`features.md` ne
signale que `.pre-commit-config.yaml` n'inclut pas les tests.

---

## Récapitulatif

**Implémenté et documenté**
- Séparation core (`gcm4_core.py`) / GTK (`gnome_connection_manager.py`) — §1
- Organisation en pages (`Gtk.Notebook`) plutôt qu'un formulaire géant — §1
- Domaine gettext, résolution dev/installé, `fallback=True` — §6
- Suite pytest hermétique / validation réelle documentée à part (par convention) — §7
- Détecteur d'imports circulaires (`tools/check_circular_imports.py`) — §11
- Journal de session chronologique (`claude.md` lui-même) — §9
- Traçabilité entrée/sortie des fonctions, mécanisme différent (manuel, `CONSIGNES-AGENTS-IA.md` §3) — §10

**Implémenté mais non documenté**
- Singleton pour les onglets « management » (`open_management_tab()`) — §3
- Actions `win.*` jamais `app.*` (documenté seulement en passant dans le journal, pas comme convention) — §3
- Seuls les libellés d'UI dans `_()`, jamais les messages de logger — §6
- Fakes de test qui échouent bruyamment sur un appel non prévu — §7

**Non implémenté (documenté comme choix délibéré / différence de modèle assumée)**
- Aucun secret jamais écrit sur disque par l'outil — GCM est lui-même un gestionnaire de secrets, par conception (mot de passe maître déjà identifié comme évolution future) — §5

**Non implémenté et non documenté**
- SIGINT propre rattaché à un chemin de fermeture unique (implémentation actuelle naïve, `try/except KeyboardInterrupt` nu) — §2
- Point de fermeture unique — divergence concrète entre le menu Quitter et le bouton X (confirmation manquante) — §2
- Scrollbars classiques (`set_overlay_scrolling`) — §3
- Fusion à l'écriture des préférences (réécriture complète depuis la mémoire, pas une fusion) — §3
- Diagnostic sudo + GUI + affichage distant — §4
- Contournement GVFS/GOA dans les sélecteurs de fichiers — §4
- Confirmation par resaisie avant suppression d'un hôte/groupe — §5
- Journalisation d'audit des commandes envoyées (`vte_feed`, `send_cluster_commands`) — §5
- Test de complétude i18n — §6
- Garde-fou anti-blocage de collecte pytest (équivalent `require_gtk4()`) — le symptôme existe et a été reproduit en conditions réelles cette session — §7
- Isolation `sys.modules` lors du mock de `gi` (stub non restauré) — §7
- GUI/bibliothèques protocolaires en dépendance recommandée plutôt qu'obligatoire dans le paquet `.deb` réel — §8
- Auto-vérification du détecteur de cycles d'imports contre un cycle volontaire — §11
- Exécution automatique de la suite de tests à chaque commit (absente de `.pre-commit-config.yaml`) — §12

**Non applicable**
- Exécutable unique arbitrant CLI/GTK — pas de mode CLI dans GCM — §1
- `prepare()`/étape bloquante séparés — aucun workflow multi-phases dans le domaine de GCM — §2
- SIGINT propre en mode CLI — pas de mode CLI — §2
- Icône de l'application (piège hicolor) — GCM charge son icône par chemin direct, aucune recherche de thème — §3
- Principe du moindre privilège / capacités Linux — aucun binaire privilégié dans GCM — §5
- Chaîne de résolution trousseau/KeePass — modèle de sécurité opposé (GCM stocke lui-même) — §5
- Piège de capture d'écran Xvfb — aucun test de capture d'écran dans ce dépôt — §7
- Architecture `all`/`noarch` → réelle — aucun binaire compilé dans GCM — §8
