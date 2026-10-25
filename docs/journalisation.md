# Journalisation

Gcm4 journalise avec [loguru](https://loguru.readthedocs.io/). La
configuration est centralisée dans `logging_config.py` (issue #169) : c'est
le seul endroit qui pose les sorties (`logger.add`), en dehors du `main()`
des outils autonomes (issue #170).

## Niveaux

| Niveau | Quand |
|---|---|
| `TRACE` | détail très fin, sur demande uniquement |
| `DEBUG` | entrée et sortie des fonctions (consigne « 2 `debug()` par fonction »), valeurs résumées |
| `INFO` | événements normaux : démarrage, connexion ouverte, import terminé (**niveau par défaut**) |
| `WARNING` | situation anormale dont l'application se remet : journal inaccessible, niveau inconnu, plugin ignoré |
| `ERROR` | échec d'une opération demandée par l'utilisateur ; `logger.exception` ajoute la pile |

## Activer le mode debug

Par ordre de priorité :

1. `GCM_LOG_LEVEL=TRACE|DEBUG|INFO|WARNING|ERROR` fixe le niveau. Un niveau
   inconnu retombe sur `INFO` avec un avertissement.
2. `--debug` sur la ligne de commande, ou `GCM_DEBUG=1` (`true`, `yes`,
   `oui` et `on` sont aussi acceptés) : niveau `DEBUG`.
3. Sans rien : `INFO`.

```sh
./gnome_connection_manager.py --debug
GCM_LOG_LEVEL=TRACE ./gnome_connection_manager.py --config ~/test-gcm
GCM_DEBUG=1 GCM_LOG_FILE=/tmp/gcm-debug.log ./gnome_connection_manager.py
```

`--debug` est retiré des arguments avant `--config` et avant la lecture des
hôtes passés en argument : il n'est jamais pris pour un nom d'hôte.

En mode debug, chaque ligne indique aussi le processus et le thread
émetteurs (les connexions et la vérification des mises à jour tournent dans
des threads), et `logger.exception` affiche la pile **avec la valeur des
variables** (`backtrace` et `diagnose` de loguru).

## Où vont les journaux

| Sortie | Contenu | Rotation |
|---|---|---|
| stderr | tout, au niveau demandé, en couleur dans un terminal | — |
| `<dossier de configuration>/log/gcm-app.log` | journal permanent de l'application (dossier par défaut `~/.gcm` pour GTK3, `~/.config/gcm4` pour gcm4, ou celui de `--config`) | 10 Mo, conservé 14 jours |
| `GCM_LOG_FILE=chemin` | copie supplémentaire, par exemple pour joindre à un ticket | 10 Mo, 5 fichiers |

Si un fichier ne peut pas être ouvert (droits, disque plein), un
avertissement est émis et l'application démarre quand même : stderr reste
disponible.

À ne pas confondre avec l'**enregistrement des sessions de terminal**
(option « Journaliser » d'un hôte, case de journalisation continue) : ces
fichiers contiennent la sortie des terminaux, pas les traces de
l'application, et sont rangés dans `<dossier de configuration>/logs/` et
`<dossier de configuration>/log/` sous le nom de l'onglet.

## Données sensibles

- Les traces ne doivent contenir **aucun secret** : mots de passe d'hôte,
  mots de passe VNC, URI SPICE, fichiers `.vv` Proxmox, clés privées,
  jetons d'API. Pour tracer une valeur, passer par
  `logging_config.summarize(valeur, nom)` : un nom qui évoque un secret
  (`password`, `pwd`, `token`, `key`, `ticket`...) est rendu `***`, les
  chaînes longues et les octets par leur taille, les identifiants d'URL
  masqués.
- **En mode debug, les piles d'exception affichent la valeur des variables
  locales**, qui peuvent contenir un mot de passe ou le contenu d'un
  fichier. Relire un journal debug (et le fichier `GCM_LOG_FILE`) avant de
  le joindre à un rapport de bug, et masquer ce qui doit l'être.
- Les journaux des sessions de terminal enregistrent tout ce qui s'affiche,
  y compris un mot de passe saisi en clair dans une commande.

## Règles pour les contributeurs

Détail dans `CONSIGNES-AGENTS-IA.md` §3.

- `from loguru import logger` ; jamais `import logging`, ni repli « si
  loguru est absent » (c'est une dépendance obligatoire). Vérifié par
  `tests/test_loguru_only.py`.
- Ne pas appeler `logger.add` ni `logger.remove` dans un module : seuls
  `logging_config.py` et le `main()` d'un outil autonome (`tools/*.py`,
  `plugins/ssh/core.py`, `scripts/pr_coverage_comment.py`, `scripts/i18n_report.py`,
  `scripts/audit_debug_sorties.py`, `scripts/generate_class_diagram.py`,
  `scripts/check_branch_protection.py`) configurent les
  sorties. Vérifié par le même test.
- Au moins deux `debug()` par fonction : à l'entrée et à chaque sortie.
  `scripts/audit_debug_sorties.py` liste les fonctions qui ne le font pas
  encore (`--details`) ; leur total ne doit pas dépasser
  `scripts/audit_debug_sorties.baseline` (le baisser après avoir
  instrumenté du code).
- Placeholders `{}` de loguru (`logger.info("hôte {} ouvert", nom)`) ou
  f-string ; autant de `{}` que d'arguments, jamais `%s`, que loguru
  n'interprète pas.
- Chaque `except` journalise (`logger.exception` ou `logger.warning` avec la
  cause) ; un `except` muet cache une panne. Les manques hérités sont
  comptés fichier par fichier et ce compte ne peut que diminuer.
- Tout module qui définit des fonctions a un logger de module
  (`from loguru import logger`).
- Aucune variable de secret (`pwd`, `password`, `passphrase`, `key`,
  `token`...) interpolée telle quelle : journaliser `bool(password)`,
  `len(pwd)` ou `logging_config.summarize(valeur, nom)`.

Ces règles sont vérifiées par `tests/test_loguru_regles.py` (analyse AST de
tout le dépôt, issue #171) et `tests/test_secrets_journal.py`, dans le job
Qualité.
- Les messages de journal ne sont pas traduits : ils s'adressent aux
  développeurs et doivent rester identiques d'une langue à l'autre. Seuls
  les textes affichés à l'utilisateur passent par `_()`.
