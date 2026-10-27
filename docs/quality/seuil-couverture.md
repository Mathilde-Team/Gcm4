# Couverture de tests et seuil bloquant

Issue #128, reprise des mécanismes de Netcross (#224, #246).

## Ce qui est mesuré

`[tool.coverage.run]` dans `pyproject.toml` :

- **périmètre** : le code de l'application (`source = [".", "src/gcm4"]`), sans `tests/`, les sous-projets vendorisés (`SSH-Studio/`, `gtk-frdp/`), `tools/`, `scripts/`, `rdp/` et `vnc/`. Avec `source`, un module qu'aucun test n'importe compte pour 0 % au lieu d'être ignoré : la mesure ne flatte pas la couverture réelle. `src/gcm4` est nommé à part (#163) : `src/` n'est pas un paquet Python, coverage.py n'y cherchait donc pas les modules jamais importés, et `src/gcm4/ui/` (que seul `tests/gtk4` importe) manquait du total du job Qualité au lieu d'y compter à 0 % ;
- **chemins relatifs** (`relative_files = true`, #163) : les données du runner « maison » et du conteneur GTK4, aux chemins de checkout différents, se fusionnent ;
- **branches** (`branch = true`) en plus des lignes : la couverture d'un `if` dont un seul côté est exécuté est comptée à moitié.

## Référence et seuil

| Date | Commit `dev` | Tests | Couverture (lignes + branches) |
|---|---|---|---|
| 01/10/2026 | b5fa496 | 739 | 19,7 % (2818/13135 lignes, 421/3304 branches) |

Le seuil initial `fail_under = 18` (relevé depuis, voir « Palier 1 » ci-dessous) laissait moins de 2 points de marge. Il absorbe une PR qui ajoute du code GTK non testable sans Xvfb, et rien de plus.

État à la mesure de référence : le cœur GTK3 `gnome_connection_manager.py` est à 8 %. `ssh_key_manager_dialog.py`, `ssh_config_editor.py`, `snmp_push_core.py` et `netmiko_bulk_core.py` ne sont importés par aucun test (0 %). Les modules sans GTK (`gcm4_core.py` 85 %, `models.py` 86 %, `master_password_core.py` et `*_menu_core.py` 100 %) tirent la moyenne.

**Règle** : relever le seuil quand la base monte, ne jamais le baisser pour faire passer une PR. Le seuil ne s'applique qu'aux exécutions avec `--cov` (la CI, ou en local `uv run pytest --cov`).

## Seuil séparé du cœur (issue #164)

La base GTK4 doit naître très couverte : un second contrôle bloquant s'applique
à `src/gcm4/core/` seul, à **95 %** (lignes + branches, comme le total).

- **job Qualité** (`ci.yml`) : `uv run coverage report --include='src/gcm4/core/*' --fail-under=95`, juste après `pytest --cov`. Une PR qui fait passer le cœur sous 95 % échoue ;
- **commentaire de PR** (`scripts/pr_coverage_comment.py`) : ligne dédiée « Cœur `src/gcm4/core/` » (base, PR, delta) et rappel du seuil avec la marge.

Mesure de référence (18/10/2026, branche `dev`) : **99,3 %** (1476/1485 instructions,
290/294 branches) — 4,3 points de marge.

À la mesure de référence : `chiffrement.py`, `cli.py`, `deplacements.py`, `host_tree.py`,
`i18n.py`, `menu.py`, `model.py`, `registre.py`, `replies.py`, `theme.py`,
`validation.py` et `window_state.py` sont à 100 % ; `chemins.py` à 98,9 %,
`menu_arbre.py` à 99,0 % et `config.py` à 98,5 % ; `plugins.py` (93,8 %) est le
seul module du cœur sous le seuil, tiré vers le haut par l'ensemble.

Vérification du critère d'acceptation (faite le 18/10/2026) : la commande échoue
bien sur un périmètre sous le seuil — `coverage report --include='src/gcm4/core/plugins.py'
--fail-under=95` sort en échec (« Coverage failure: total of 93.8 is less than
fail-under=95.0 »).

## Couverture combinée Qualité + GTK4 (issue #163)

Le code d'interface `src/gcm4/ui/` n'est exécuté que par `tests/gtk4`, sous Xvfb, dans le job « Tests GTK4 (Xvfb) ». Les deux jobs mesurent la couverture et publient leurs données (artefacts `coverage-data-qualite`, `coverage-data-gtk4`) ; le job « Couverture combinée » les fusionne (`coverage combine`) :

- le **seuil `fail_under`** s'applique au total combiné **seulement** (`coverage report`, l'étape échoue dessous). Le job « Couverture combinée » est requis par la protection de `dev` (`.github/branch-protection/dev.json`, #278) ; le job Qualité passe `--cov-fail-under=0` (même commande dans `make ci`), car son total n'exécute pas `src/gcm4/ui/`. Le seuil du cœur (95 %) reste appliqué dans le job Qualité ;
- le **résumé du job** détaille, par périmètre (`src/gcm4/ui/`, `src/gcm4/core/`, `src/gcm4/`), la couverture du job Qualité seul, du job GTK4 seul et combinée (`scripts/couverture_combinee.py`) ;
- `coverage-combinee.xml` est publié en artefact `coverage-report-combinee`.

Les données fusionnées sont téléchargées dans un sous-dossier : coverage.py 7.16 fusionne puis supprime d'office les fichiers `.coverage.*` voisins du fichier de données à chaque rapport.

Mesure (21/10/2026, `dev` + #163, 1178 tests hors GTK4, 88 tests GTK4) :

| Périmètre | Qualité seul | GTK4 seul | Combiné |
|---|---|---|---|
| total | 26,6 % | — | 36,6 % |
| `src/gcm4/ui/` | 0,0 % | 92,2 % | 92,2 % |
| `src/gcm4/core/` | 99,5 % | 82,8 % | 99,5 % |
| `src/gcm4/` | 47,0 % | 87,0 % | 95,4 % |

Le total du job Qualité seul baisse (29,9 % → 26,6 %) parce que `src/gcm4/ui/` y compte désormais à 0 % au lieu d'être absent.

### Palier 1 vers 95 % (issue #293, 22/10/2026)

Mesure de `dev` (a592a8f, run CI 39840291314) : total combiné **41,7 %**, job Qualité seul **28,2 %**. `fail_under` passe de 18 à **41** : la mesure combinée arrondie à l'entier inférieur (0,7 pt de marge). Il ne pouvait pas monter tant qu'il s'appliquait aussi au job Qualité seul (28,2 %) : d'où #278. Cible de l'équipe : 95 % (Mathilde-Team/.github#9). Le seuil est relevé à chaque PR de tests, jamais baissé.

### Palier 2 (issue #293, 24/10/2026)

Mesure de `dev` (438340f, run CI 40101560678) : total combiné **43,9 %**. Tests ajoutés sans GTK ni réseau : `tests/test_plugin_base_registres.py` (contrats et `autoload()` des deux registres de `plugins/plugin_base.py`, 24,9 % → 100 %) et `tests/test_hypervisor_import_common.py` (clés SSH, `paramiko_connect` avec une doublure de paramiko, scan nmap, dconf ; 45,6 % → 100 %). Gain : 348 lignes ou branches sur 25 011, soit environ +1,4 pt (≈ 45,3 %). `fail_under` passe de 41 à **45** (entier inférieur de la nouvelle mesure combinée).

### Palier 3 (issue #293, 26/10/2026)

Mesure de `dev` + PR de tests (093ea00, run CI 40293284698) : total combiné **50,9 %**. Tests ajoutés sans GTK ni réseau : `tests/test_netmiko_bulk_core.py` (67 tests) couvre `src/gcm4/netmiko_bulk_core.py` — 324 instructions jusqu'alors importées par aucun test (0 %) — à **99,5 %** : profils (`DeviceProfile`, `ProfileStore`), inventaire CSV (`load_inventory` : délimiteurs, colonnes manquantes), gabarits (`render_template`), `scan_output_for_errors`, `push_to_device` (netmiko remplacé par une doublure), `run_bulk_push` (concurrence, callbacks, répertoire de logs) et `summarize_results`. Seule la ligne 492 (`ValueError` défensive inatteignable après les validations amont) reste non couverte. Suite complète : 1706 tests. `fail_under` passe de 45 à **50** (entier inférieur de la nouvelle mesure combinée, 0,9 pt de marge).

## Où le voir

- **job Couverture combinée** (`ci.yml`) : total fusionné Qualité + GTK4 et tableau par périmètre dans le résumé du job ;
- **job Qualité** (`ci.yml`) : `pytest --cov`, rapport `term-missing` dans les logs, `coverage.xml` publié en artefact `coverage-report`. L'étape échoue sous le seuil ;
- **commentaire de PR** (`pr-coverage.yml`, `scripts/pr_coverage_comment.py`) : chaque PR vers `dev` reçoit un commentaire unique, mis à jour à chaque push. Il donne la couverture de la base et de la PR, le delta, la ligne du cœur (`src/gcm4/core/`) et les fichiers dont la couverture baisse. La base est mesurée avec la configuration de la PR, pour comparer le même périmètre. Il ne mesure que la suite hors GTK4 et le dit (« Périmètre de ce commentaire… ») : `src/gcm4/ui/` y figure à 0 %. Il n'en compare plus le total au seuil, qui s'applique au total combiné, visible dans le résumé du job « Couverture combinée » (#278).

## Suite prévue

- relever `fail_under` palier par palier jusqu'à 95 % (#293) : en tête des modules les moins couverts, le cœur GTK3 historique (`gnome_connection_manager.py`) et les plugins ;
