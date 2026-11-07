# Renommage GCM → gcm4 — audit exhaustif et plan (session-30, 2026-09-10)

Décision de renommer le projet actée le 2026-08-29 (voir
`docs/gtk4-migration.md` §3.0). Fait à cette date dans les livrables (nom du
zip) et la documentation ; **pas fait dans le code** — chantier à part
entière, distinct de la migration GTK4 elle-même (voir `CLAUDE.md`,
« Prochaine étape » point 2). Cette session : audit exhaustif de tout ce qui
doit changer, et un premier mécanisme générique testé (la migration de
config utilisateur), **sans exécuter le renommage réel** — voir
« Pourquoi pas d'exécution cette session » en fin de document.

## 1. Inventaire — module principal `gnome_connection_manager.py`

Recherche précise des imports réels (`import gnome_connection_manager` /
`from gnome_connection_manager import ...`), hors dossiers vendorisés
`SSH-Studio/`, `gtk-frdp/` :

**12 fichiers** importent réellement le module (tous en import différé à
l'intérieur d'une fonction/méthode, `# noqa: PLC0415`, conformément au
mécanisme documenté dans `CONSIGNES-AGENTS-IA.md` §7 pour éviter tout import
circulaire avec le cœur) :

`widgets.py`, `models.py`, `ssh_config_editor.py`,
`ssh_key_manager_dialog.py`, `key_picker_dialog.py`, `utils.py`,
`plugins/plugin_import_proxmox.py`, `plugins/plugin_telnet.py`,
`plugins/plugin_local.py`, `plugins/plugin_ssh.py`,
`plugins/plugin_import_libvirt.py`, `tests/test_gcm.py`.

Le module lui-même contient une astuce d'auto-alias à préserver/adapter :
quand il est lancé directement (`__name__ == "__main__"`), il fait
`sys.modules["gnome_connection_manager"] = sys.modules[__name__]` pour que
ces imports différés fonctionnent qu'il soit lancé en script ou importé
normalement — un renommage du fichier doit impérativement adapter cette
ligne au nouveau nom, sous peine de casser silencieusement les 12 imports
différés au lancement normal (le mode `__main__` est le mode de lancement
réel de l'application).

Environ **28 autres fichiers** mentionnent la chaîne `gnome_connection_manager`
sans l'importer réellement (commentaires, docstrings, chaînes de log) —
priorité basse, cosmétique, à traiter en passant lors du renommage réel mais
sans risque fonctionnel si oublié dans un premier temps.

## 2. Inventaire — domaine i18n `gcm-lang`

**2 occurrences** depuis l'issue #165 : 1 dans `gnome_connection_manager.py`
(`domain_name = "gcm-lang"`, ligne unique, transmise à
`gcm4_core.bindtextdomain()`) + 1 dans `scripts/i18n-update.sh`
(`DOMAIN="${GCM_I18N_DOMAIN:-gcm-lang}"`, utilisé par `--compile` pour
écrire `lang/<code>/LC_MESSAGES/gcm-lang.mo`). Les 28 lignes `msgfmt` du
`Makefile` (qui omettaient d'ailleurs une langue sur les 29) ont été
remplacées par `scripts/i18n-update.sh --compile lang`.

`generate_pot.sh` (qui visait un dossier `po/` inexistant) déléguait
à `scripts/i18n-update.sh` ; il a été supprimé (#427), appeler
directement `scripts/i18n-update.sh` ou `make i18n-update`.

## 3. Inventaire — fichier `.desktop`

`gnome-connection-manager.desktop` — 5 lignes à changer : `Name=`,
`Exec=/usr/share/gnome-connection-manager/gnome_connection_manager.py`,
`Icon=/usr/share/gnome-connection-manager/icon.png`,
`StartupWMClass=gnome_connection_manager.py`, `Name[en]=`. Le chemin
`/usr/share/gnome-connection-manager/` dépend aussi de `PKG_NAME` (§4).

## 4. Inventaire — packaging (`Makefile`, `postinst`)

`PKG_NAME = gnome-connection-manager` (`Makefile`, ligne 1) pilote à lui
seul les noms de paquets générés (`$(PKG_DEB)`, `$(PKG_RPM)`,
`$(PKG_OPENSUSE)`), le chemin d'installation
`/usr/share/$(PKG_NAME)/`, et le dossier de doc
`/usr/share/doc/$(PKG_NAME)/` — changer cette seule variable propage
correctement à l'essentiel du packaging. `postinst` référence séparément
`gnome-connection-manager`/`gnome_connection_manager.py` à quelques endroits
(dépendances post-installation), à vérifier ligne à ligne au moment du
renommage réel.

**Bug trouvé et corrigé cette session (sans lien avec une décision de nom)**
: la cible `install` du `Makefile` copiait
`gnome-connection-manager.glade`, et la cible `validate` appelait
`tools/validate_xml_json.py` dessus — fichier absent du dépôt depuis la
suppression de tout `.glade`/`.ui` (session-08). `make validate` (et donc
`make check`) échouait donc silencieusement à chaque exécution
(`FileNotFoundError`), sans rapport avec les 423 erreurs `ruff` déjà
documentées pour `make lint`. Corrigé : référence retirée des deux cibles ;
vérifié en conditions réelles (`gettext` installé dans le bac à sable) —
`make validate` passe désormais proprement. `tools/validate_xml_json.py`
reste disponible tel quel, générique, pour un futur fichier `.xml`/`.glade`/
`.json`.

## 5. Inventaire — configuration utilisateur `~/.gcm/`

Le point le plus sensible : `gcm4_core._resolve_config_dir()` résout par
défaut `~/.gcm` (`os.path.join(default_home, ".gcm")`, non paramétrable
autrement que par l'option `--config`) ; `gnome_connection_manager.py`
dérive `CONFIG_FILE` (`gcm.conf`) et `KEY_FILE` de ce dossier. Un renommage
du dossier par défaut sans mécanisme de migration ferait perdre à chaque
utilisateur existant ses connexions et son mot de passe maître au premier
lancement de la version renommée — c'est le risque explicitement identifié
dans `CLAUDE.md`.

`widgets.py` duplique indépendamment `CONFIG_DIR`/`CONFIG_FILE`/`KEY_FILE`
(constat déjà consigné dans `docs/architecture.md` §2.8, non corrigé) : le
jour du renommage réel, cette duplication devra être traitée en même temps
que le changement de nom pour ne pas laisser les deux définitions diverger
silencieusement sur le nouveau nom.

### Mécanisme livré cette session : `gcm4_core.migrate_legacy_config_dir()`

Fonction générique, testée en isolation (8 tests, `tests/test_gcm4_core.py`
classe `TestMigrateLegacyConfigDir`), **pas encore câblée** dans
`gnome_connection_manager.py` ni `widgets.py` :

- reçoit `old_dir`/`new_dir` en paramètres explicites — ne décide et ne
  connaît aucun nom de dossier (`CONSIGNES-AGENTS-IA.md` §2) ;
- copie (`shutil.copytree`, permissions préservées — important pour le
  fichier de clé `0600`) plutôt que déplace : `old_dir` n'est jamais
  supprimé, pour qu'un retour à une version antérieure retrouve la config
  intacte ;
- idempotente : ne fait rien si `new_dir` existe déjà (jamais d'écrasement,
  y compris d'une édition faite par l'utilisateur après une première
  migration) ni si `old_dir` est absent (premier lancement) ;
- nettoie `new_dir` en cas d'échec en cours de copie, pour ne pas laisser un
  dossier partiel pris pour une migration terminée au prochain démarrage.

Le câblage réel (appel au démarrage de `gnome_connection_manager.py`, avec
les noms définitifs une fois choisis) reste à faire dans une session
ultérieure.

## 6. Choix de noms encore ouverts — à confirmer avec l'auteure

Aucun nom définitif n'a été acté au-delà de la direction générale
« GCM → gcm4 » (`docs/gtk4-migration.md` §3.0). Cette session n'en tranche
aucun (`CONSIGNES-AGENTS-IA.md` §8) :

| Élément | Aujourd'hui | Pistes (non tranchées) |
|---|---|---|
| Module principal | `gnome_connection_manager.py` | `gcm4.py` / `gcm4_app.py` / `gcm4_gtk.py` |
| Dossier de config | `~/.gcm/` | **Tranché (#99)** : `$XDG_CONFIG_HOME/gcm4/` (`~/.config/gcm4/`) pour gcm4 |
| Fichier de conf / clé | `gcm.conf` / `.gcm.key` | **Tranché (#99)** : `gcm4.conf` / `.gcm4.key`, migration par `gcm4.core.chemins` (voir `docs/configuration.md`) |
| Domaine i18n | `gcm-lang` | `gcm4-lang` |
| Identifiant `.desktop` | `gnome-connection-manager.desktop` | `gcm4.desktop` / id inversé façon `io.github.*` |
| `PKG_NAME` (Makefile) | `gnome-connection-manager` | `gcm4` |

Question déjà connue et toujours ouverte, indépendante de ce tableau :
numérotation de version après renommage (reprise en 1.x ou redémarrage en
4.0.0) — voir `CLAUDE.md`.

Point notable : le passage à `~/.config/gcm4/` (XDG Base Directory) plutôt
qu'un nouveau dossier cousin de l'ancien (`~/.gcm4/`) serait cohérent avec
les conventions actuelles, mais élargirait le renommage à une vraie
migration de layout (XDG sépare aussi cache/data/state) — à trancher
explicitly avec l'auteure, `migrate_legacy_config_dir()` fonctionne dans les
deux cas (elle ne connaît aucun chemin).

## 7. Pourquoi pas d'exécution cette session

Le renommage mécanique touche ~12 sites d'import réel, 29 occurrences i18n,
le `.desktop`, le packaging, et surtout un chemin de configuration utilisateur
réel — un rayon d'action bien plus large que les fixes ponctuels de cette
session, et six choix de noms non tranchés (tableau §6) que je ne peux pas
inventer seul sans risquer de devoir tout refaire si l'auteure préfère
d'autres noms. Conformément à `CONSIGNES-AGENTS-IA.md` §8 (« proposer un plan
avant de coder un gros morceau », « ne pas trancher seul une ambiguïté ») :
livré cette session l'inventaire complet et le mécanisme de migration
générique et testé, prêt à être câblé dès que les noms du tableau ci-dessus
seront confirmés — l'exécution réelle (renommer les fichiers, mettre à jour
les ~12+28 sites, câbler la migration) tiendra dans une session dédiée une
fois ces noms actés.
