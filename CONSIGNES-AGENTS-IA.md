# CONSIGNES-AGENTS-IA.md — Règles strictes pour le développement des plugins GCM

> Destiné à tout agent IA (Claude ou autre) travaillant sur les plugins de
> GNOME Connection Manager, en particulier lors du découpage en dossiers
> `plugins/<nom>/` (voir `docs/proposition-architecture-plugins-gtk4.md`) où
> plusieurs agents peuvent travailler en parallèle sur des plugins
> différents. Ces règles sont **strictes** : un agent qui livre du code ne
> les respectant pas doit le corriger avant de le considérer terminé, pas
> après coup sur demande.
>
> Contexte : voir `claude.md` et `features.md` pour l'état du projet.
> `src/gcm4/core/metier.py` (extrait le 2026-08-30) est la référence vivante de ces
> conventions — en cas de doute sur la forme attendue, s'y référer.

## 1. Neutralité de protocole (règle d'architecture, pas de style)

Le cœur de l'application (`gnome_connection_manager.py`, et sa partie
métier extraite dans `src/gcm4/core/metier.py`) **ne doit contenir aucune donnée ni
logique spécifique à un protocole** (SSH, RDP, VNC, série, telnet,
SNMP...). Tout ce qui nomme un protocole, un constructeur, ou un format de
configuration propre à un plugin appartient au plugin, jamais au cœur.

- **Contre-exemple corrigé le 2026-08-30** : les templates série (presets
  Cisco/Huawei/Arduino...) avaient été déplacés par erreur dans
  `src/gcm4/core/metier.py` lors de sa création, puis remis dans
  `gnome_connection_manager.py` après relecture — le protocole série étant
  déjà un plugin (`plugins/plugin_serial.py`), ces données n'ont leur place
  dans aucun des deux fichiers du cœur à terme, seulement dans le plugin.
- **Bon exemple** : `proto_default_port()`/`all_default_ports()` dans
  `src/gcm4/core/metier.py` sont un *mécanisme* générique paramétré par
  `plugin_registry` — aucun nom de protocole n'y est écrit en dur.
- Un agent qui découvre un vestige de protocole dans le cœur en travaillant
  sur autre chose **le signale dans `features.md`/`claude.md`, mais ne le
  déplace pas sans confirmation** — sauf si la tâche en cours porte
  justement sur ce protocole.
- **Contrats vérifiés en CI (issue #174)** : `tests/test_architecture_imports.py`
  fait échouer toute PR qui fait importer GTK (`gi`) à un module cœur
  (`*_core.py`, `src/gcm4/core/metier.py`, `src/gcm4/core/journal.py`,
  `src/gcm4/core/mot_de_passe.py`, `plugins/*/core.py`), qui fait importer un
  plugin nommé au cœur, ou un plugin par un autre plugin.
  `tools/check_circular_imports.py` tourne aussi dans le job Qualité, et les
  contrats import-linter de `src/gcm4` (`pyproject.toml`) s'activeront
  avec ce paquet, avec l'interdiction des noms de protocole en littéral
  dans `src/gcm4/core/` et `src/gcm4/ui/`.

## 2. Aucune dépendance externe implicite

Une fonction ne va jamais chercher une donnée dont elle a besoin via un
singleton global (`wMain`, `utils.conf`, etc.) si cette donnée peut lui
être passée en paramètre. Voir `proto_default_port(proto, plugin_registry)`
et `decrypt(passw, string, version)` dans `src/gcm4/core/metier.py` : réécritures du
2026-08-30 qui reçoivent explicitement ce qu'elles utilisaient auparavant
en lisant un global. Ceci s'applique en particulier à tout code qui doit
rester importable sans GTK.

## 3. Logging — Loguru, au moins 2 `debug()` par fonction/méthode

Toute fonction ou méthode non triviale importe `app_logger` (`from loguru
import logger as app_logger`, ou l'instance déjà configurée du module) et
loggue :
- **un `debug()` en entrée**, avec les paramètres significatifs (jamais un
  mot de passe ou une clé en clair — indiquer `(masqué)` ou une longueur à
  la place, voir `get_password()` dans `src/gcm4/core/metier.py`) ;
- **un `debug()` à chaque sortie**, y compris chaque `return` anticipé et
  chaque levée d'exception volontaire (`raise`) — pas seulement un `debug()`
  générique en fin de fonction si plusieurs chemins de sortie existent.

```python
def exemple(proto: str, plugin_registry) -> str:
    """Résume ce que fait la fonction en une ligne.

    Args:
        proto (str): ...
        plugin_registry (PluginRegistry): ...

    Returns:
        str: ...
    """
    app_logger.debug(f"exemple() called | proto={proto!r}")
    plugin = plugin_registry.get(proto)
    if plugin is None:
        app_logger.debug("exemple() returning | plugin absent")
        return ""
    result = plugin.something()
    app_logger.debug(f"exemple() returning | result={result!r}")
    return result
```

Référence complète : n'importe quelle fonction de `src/gcm4/core/metier.py`.

Un seul système de journalisation (issue #170) : jamais `import logging`
(ni repli « si loguru est absent » : loguru est une dépendance
obligatoire), et les puits (`logger.add`) ne sont configurés que par
`src/gcm4/core/journal.py`, sauf dans la fonction `main()` d'un point d'entrée
autonome (`tools/*.py`, `plugins/ssh/core.py`, `scripts/pr_coverage_comment.py`, `scripts/i18n_report.py`, `scripts/audit_debug_sorties.py`, `scripts/generate_class_diagram.py`, `scripts/check_branch_protection.py`).
`tests/test_loguru_only.py` le vérifie par analyse `ast`.

`tests/test_loguru_regles.py` (issue #171) rend bloquantes les autres
règles : `except` journalisés (manques hérités comptés par fichier, compte
qui ne peut que diminuer), logger de module, placeholders `{}` cohérents,
aucun secret interpolé, et total de `scripts/audit_debug_sorties.py`
(fonctions sans `debug()` en entrée ou à chaque sortie) qui ne doit pas
augmenter.

## 4. Docstrings — style Google, avec Args/Returns/Raises

Déjà imposé par `pyproject.toml` (`[tool.ruff.lint.pydocstyle]` →
`convention = "google"`, règles `D` activées) — ceci formalise le standard
attendu, ruff ne fait que vérifier qu'une docstring existe et respecte la
structure Google, pas qu'elle documente correctement `Args`/`Returns`.

- Chaque fonction/méthode publique **et privée** (préfixée `_`) a une
  docstring.
- `Args:` liste chaque paramètre avec son type et une description courte.
- `Returns:` décrit la valeur de retour (type + sens). Omis seulement si la
  fonction ne retourne rien de significatif (`-> None`).
- `Raises:` si la fonction lève une exception volontairement (voir
  `load_encryption_key()` dans `src/gcm4/core/metier.py`).
- Toute divergence de comportement par rapport à une version antérieure
  (réécriture, correction de bug latent) est documentée **dans la
  docstring elle-même**, pas seulement dans le message de commit — voir
  `get_password()` dans `src/gcm4/core/metier.py` pour l'exemple.

## 5. Tests automatiques — obligatoires, exécutables sans stub GTK quand c'est possible

Tout code métier nouveau ou modifié (zéro import `gi`/`Gtk`/`Vte`) a des
tests dans un fichier dédié qui l'importe **directement**, sans passer par
`gnome_connection_manager.py` — voir `tests/test_gcm4_core.py` et
`tests/test_snmp_bulk_core.py` (stub minimal d'une seule dépendance C
manquante dans l'environnement de développement, pas de stub GTK).

Priorités de couverture, dans l'ordre :
1. Tout comportement **nouveau ou changé** par rapport à l'existant
   (signature modifiée, exception au lieu d'un ancien comportement...).
2. Toute régression trouvée en écrivant le code — un bug de transcription
   corrigé doit avoir un test qui aurait échoué avant la correction (voir
   `TestColorToHex.test_with_negative_diff` et `TestDepInstallHint` dans
   `tests/test_gcm4_core.py`, qui verrouillent deux erreurs de transcription
   trouvées le 2026-08-30).
3. Les cas limites déjà connus (fichier absent, dossier non accessible en
   écriture, registre de plugin vide...).

Code touchant GTK (widgets, dialogues) : tester la logique extractible
séparément plutôt que renoncer aux tests — voir la discipline déjà en place
dans `gnome_connection_manager.py` (`_compute_zoom_size()` extrait de
`Wmain.terminal_zoom()` justement pour rester testable).

## 6. Ruff — code propre, configuration déjà en place

`pyproject.toml` définit déjà la configuration (`select = ["E", "W", "F",
"I", "D", "UP", "B", "C4", "SIM"]`, ligne à 99 caractères, docstrings
Google). Avant de considérer un fichier terminé :

```bash
ruff check <fichier>
ruff format <fichier>
```

Les deux doivent passer sans erreur sur tout fichier **nouveau**. Sur un
fichier **existant** modifié, ne corriger que ce qui touche au code
effectivement changé — ne pas profiter d'une petite modification pour
reformater tout un fichier hérité (bruit inutile dans le diff, risque de
conflit avec un autre agent qui travaille sur le même fichier en parallèle).

⚠️ **Écart constaté (2026-08-30), non corrigé** : `tests/test_gcm.py`
compte à lui seul 423 erreurs ruff (essentiellement des docstrings
manquantes sur des méthodes de test), et `make lint`
(`ruff check gnome_connection_manager.py tests/`) échoue donc déjà
aujourd'hui. Un agent qui touche `tests/test_gcm.py` pour une raison
précise peut corriger les erreurs des lignes qu'il modifie, mais n'est pas
tenu de résorber tout l'historique en une fois.

## 7. Pre-commit — imports circulaires interdits

Depuis l'issue #90, la CI exécute aussi `pre-commit run --all-files` (job
« Qualité (lint, tests) ») : installer le hook en local évite seulement de
le découvrir en CI.

Voir `.pre-commit-config.yaml` (ruff, hygiène de base, et le hook local
`tools/check_circular_imports.py`). Installation (une fois) :

```bash
pip install pre-commit --break-system-packages
pre-commit install
```

Le détecteur d'imports circulaires (`tools/check_circular_imports.py`) ne
regarde que les imports **exécutés au chargement du module** (imports en
tête de fichier, dans un corps de classe, sous un `if`/`try` au niveau
module) — il ignore délibérément :
- les imports à l'intérieur d'un corps de fonction/méthode (pattern déjà
  utilisé intentionnellement dans ce dépôt pour casser un cycle logique,
  voir `models.py` qui importe `gcm4.core.metier` à l'intérieur de ses méthodes) ;
- les imports sous `if TYPE_CHECKING:` (jamais exécutés à l'exécution
  réelle).

**Si le détecteur bloque un commit**, la solution n'est presque jamais de
contourner le hook, mais de différer l'import côté appelant le moins
central (le passer à l'intérieur d'une fonction plutôt qu'en tête de
fichier), ou de le limiter à `if TYPE_CHECKING:` s'il ne sert qu'à
l'annotation de type.

⚠️ **Note d'adoption (vérifié le 2026-08-30)** : la toute première
exécution de `pre-commit run --all-files` sur ce dépôt reformate ~40
fichiers Python existants (jamais passés par `ruff format` jusqu'ici) et
corrige des espaces/fins de fichier sur une poignée de fichiers texte
(`README.md`, `claude.md`, `pyproject.toml`, `ssh.expect`, `postinst`,
`style.css`). C'est normal et sans risque (changements de forme
uniquement), mais **à faire comme un commit dédié et isolé** avant de
commencer le travail sur les plugins — pas mélangé à un premier commit de
fonctionnalité, pour ne pas noyer une vraie modification dans du bruit de
formatage. Les dossiers vendorisés (`SSH-Studio/`, `gtk-frdp/`) et les
environnements de test (`rdp/`, `vnc/`) sont exclus de tous les hooks
(`exclude:` en tête de `.pre-commit-config.yaml`) — ne pas les en retirer.

## 8. Ce que ces consignes ne couvrent pas (volontairement)

- Le style de code au sens large (nommage, longueur de fonction...) : géré
  par ruff, pas reformulé ici.
- Les décisions d'architecture au cas par cas (quel fichier pour quelle
  logique) : voir `docs/proposition-architecture-plugins-gtk4.md` et
  `features.md` §4 pour le contexte, ce fichier ne couvre que les règles
  transversales de qualité de code.
- Rien ici n'autorise à ignorer les autres consignes du projet
  (`claude.md` : ne pas trancher seul une ambiguïté, proposer un plan avant
  de coder un gros morceau, ne pas fixer aveuglément quelque chose signalé
  comme "à confirmer").
