# Typage statique (mypy)

Issue #173. `uv run mypy` vérifie les modules listés dans `[tool.mypy] files`
(`pyproject.toml`). Le job CI « Types (mypy) » est bloquant.

## Périmètre

- Modules sans GTK d'abord : `src/gcm4/core/metier.py`, `src/gcm4/core/journal.py`,
  `models.py`, `src/gcm4/core/mot_de_passe.py`, les `*_core.py`,
  `plugins/plugin_base.py` et `plugins/ssh/core.py`. Ils sont tous à
  0 erreur.
- La liste ne peut que s'allonger : `tests/test_mypy_config.py` échoue si
  un module de référence en sort.
- Options : `python_version`, `ignore_missing_imports`,
  `warn_unused_ignores`, `warn_redundant_casts`, `follow_imports = "silent"`
  (les modules non listés ne produisent pas d'erreur).

## Ajouter un module

1. L'ajouter à `files` dans `pyproject.toml`.
2. `uv run mypy` doit afficher `Success`. Préférer une annotation ou un
   `Protocol` (voir `netmiko_bulk_core.PushResultLike`) à un
   `# type: ignore[code]`, qui doit toujours nommer son code d'erreur.
3. L'ajouter à `MODULES_TYPES_MINIMUM` dans `tests/test_mypy_config.py`.

## Suite

- `src/gcm4/core/` (épopée GTK4) : override `strict = true` à ajouter dans
  `[[tool.mypy.overrides]]` dès que le paquet existe. Le faire avant, c'est
  une section inutilisée (`warn_unused_configs`) qui rend le strict actif
  ailleurs avec mypy 2.x.
- `src/gcm4/ui/` : PyGObject n'a pas de types. `pygobject-stubs` couvre
  GTK 3 et 4 mais doit être installé avec la bonne version
  (`--config-settings=config=Gtk4`) ; à évaluer quand l'interface GTK4
  arrivera, en attendant les modules GTK restent hors de la liste.
