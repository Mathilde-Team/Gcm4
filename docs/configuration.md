# Fichier de configuration

Inventaire du format lu par `Wmain.loadConfig` et écrit par
`Wmain.writeConfig` (application GTK3, `gnome_connection_manager.py`), et
de sa reprise sans GTK dans `src/gcm4/core/config.py` (#137). Le format est
le même pour les deux applications ; seuls les emplacements diffèrent.

## Emplacements (#99)

| | GTK3 | gcm4 |
|---|---|---|
| Dossier | `~/.gcm/` | `$XDG_CONFIG_HOME/gcm4/` (`~/.config/gcm4/` si la variable est absente ou relative) |
| Configuration | `gcm.conf` | `gcm4.conf` |
| Clé de chiffrement | `.gcm.key` | `.gcm4.key` |
| Sauvegarde | `gcm.conf.bak` | `gcm4.conf.bak` |
| Journal | `log/gcm-app.log` | `log/gcm-app.log` |

`-c/--config DOSSIER` remplace le dossier dans les deux applications.

Migration (`gcm4.core.chemins.preparer`, au démarrage de gcm4, avant toute
écriture) :

1. sans `-c`, si le dossier de gcm4 est absent et que `~/.gcm/` existe,
   celui-ci est **copié** en entier, droits compris, puis les fichiers y
   sont renommés (`gcm.conf` → `gcm4.conf`, etc.) ;
2. dans le dossier retenu, un `gcm.conf` / `.gcm.key` / `gcm.conf.bak` sans
   équivalent `gcm4` est copié sous le nouveau nom en `0600`, ce qui couvre
   `-c ~/.gcm`. Les anciens fichiers y restent, pour GTK3.

Rien n'est jamais supprimé ni écrasé dans `~/.gcm/` : l'application GTK3
continue de l'utiliser jusqu'à son retrait (#177), et les deux
configurations évoluent ensuite **séparément**. En cas d'échec (droits,
disque plein), le dossier partiel est supprimé et gcm4 s'arrête avec le
code 4 et un message.

## Règles de `gcm4.core.config`

- Aucune perte : `save_config(path, load_config(path))` conserve chaque
  section, chaque clé et chaque valeur. Ce qui n'est pas typé reste brut
  dans `Config.brut` et est réécrit à l'identique.
- Une option typée inchangée garde son texte d'origine (`1` reste `1`,
  une valeur invalide n'est pas remplacée par le défaut).
- Écriture atomique : temporaire dans le même dossier, `fsync`,
  `os.replace`, permissions `0600`. Copie `gcm.conf.bak` (`0600`) avant la
  première écriture de la session. En cas d'erreur (disque plein, droits),
  `ConfigError` est levée et l'ancien fichier reste intact.
- Fichier illisible (droits, encodage, dossier) ou INI corrompu :
  `ConfigError` traduite. Jamais de configuration vide enregistrée
  par-dessus. Fichier absent : valeurs par défaut.
- Mots de passe : le champ `pass` passe par un objet `Chiffrement`
  (`dechiffrer` / `chiffrer`). Par défaut, `SansChiffrement` conserve la
  valeur stockée ; le chiffrement de `.gcm.key` est le lot 2c (#138).

## `[options]`

Typées dans `AppConfig` (options génériques, défauts de `utils.conf`) :

| Clé | Type | Défaut | Attribut |
|---|---|---|---|
| `confirm-exit` | bool | `True` | `confirm_exit` |
| `confirm-close-tab` | bool | `True` | `confirm_close_tab` |
| `confirm-close-tab-middle` | bool | `True` | `confirm_close_tab_middle` |
| `auto-close-tab` | int | `0` | `auto_close_tab` |
| `cycle-tabs` | bool | `True` | `cycle_tabs` |
| `disable-shortcuts` | bool | `False` | `disable_shortcuts` |
| `disable-notifications` | bool | `False` | `disable_notifications` |
| `restore-open-tabs` | bool | `False` | `restore_open_tabs` |
| `check-updates` | bool | `True` | `check_updates` |
| `disable-hosts-stripes` | bool | `False` | `disable_hosts_stripes` |
| `update-title` | bool | `False` | `update_title` |
| `app-title` | str | `""` | `app_title` |
| `font` | str | `""` | `font` |
| `font-color` | str | `""` | `font_color` |
| `back-color` | str | `""` | `back_color` |
| `transparency` | int | `0` | `transparency` |

Conservées brutes (terminal, journaux, protocoles, plugins ; elles seront
typées par le lot qui les utilise) : `word-separators`, `buffer-lines`,
`startup-local`, `log-local`, `term`, `paste-right-click`,
`auto-copy-selection`, `log-path`, `continuous-tab-log`,
`continuous-log-path`, `version` (format des mots de passe, lot 2c),
`libvirt-default-user`, `proxmox-default-user`, `virtualbox-default-user`,
et toute clé inconnue.

## `[window]`

| Clé | Lecture GTK4 |
|---|---|
| `window-width`, `window-height`, `left-panel-width`, `show-panel` | `gcm4.core.window_state` (#134) |
| `group-colors` | `Group.color` ; format `chemin=#rrggbb,…` (`gcm4_core.parse_group_colors`) |
| `collapsed-folders` | `Group.collapsed` ; chemins séparés par des virgules |
| `open-tabs`, `dark-mode`, `theme-mode` | brutes (lots 3 et 1c) |

`group-colors` et `collapsed-folders` sont réécrites depuis le modèle. Un
groupe replié qui n'existe plus n'est pas conservé.

## `[empty_groups]`

`names` : groupes sans hôte, séparés par des virgules. Relus comme groupes
du `HostStore`, réécrits depuis le modèle (groupes sans hôte, même dans un
sous-groupe).

## `[host N]`

Une section par hôte, renumérotée `host 1`…`host N` à l'écriture (ordre :
groupe puis nom, comme `writeConfig`). Une section en double (même groupe
et même nom) est gardée à la lecture : rien n'est écarté.

Champs communs (`Host`, `CHAMPS_HOTE`) :

| Clé | Attribut | Remarque |
|---|---|---|
| `group`, `name`, `description`, `host`, `user` | idem | |
| `pass` | `password` | via `Chiffrement` |
| `port` | `port` (int) | vide ou invalide : texte conservé dans `protocol_options` |
| `protocol` | `protocol` | pas de défaut dans le cœur (neutralité) |
| `font-color`, `back-color` | `font_color`, `back_color` | |
| `extra_params`, `auto-close-tab` | `extra_params`, `auto_close_tab` | |
| `workspace` | `workspace` (bool) | invalide : texte conservé |
| `pre-connect-command`, `post-connect-command` | idem | |

Toutes les autres clés (`private_key`, `tunnel`, `type`, `commands`,
`keepalive`, `x11`, `agent`, `compression`, `compression-level`, `log`,
`backspace-key`, `delete-key`, `term`, `encoding`, champs déclarés par les
plugins…) vont dans `host.protocol_options[host.protocol]`, texte inchangé,
et sont réécrites telles quelles même sans le plugin du protocole.

`encoding` (#85, gcm4 seulement) : encodage des caractères d'un onglet
terminal (telnet, local), choisi dans la liste « Encodage » du dialogue
d'hôte : `ISO-8859-1`, `ISO-8859-15`, `WINDOWS-1252`, `KOI8-R`, `CP437`,
`SHIFT_JIS`, `GB18030`… Absent ou vide : UTF-8. Une valeur hors de la liste
est ignorée (UTF-8, avertissement au journal) ; un encodage que VTE refuse
(VTE compilé sans ICU) est signalé dans l'onglet, qui reste en UTF-8.
L'application GTK3 ignore cette clé.

## `[shortcuts]` et sections inconnues

Conservées brutes (raccourcis : lot 4).
