# Import et export des hôtes (CSV, JSON)

Format des fichiers lus et écrits par `src/gcm4/core/exchange.py` (issue
#152). Le module est en code pur : l'interface GTK4 (menu Fichier, lot 7b)
ne fait que choisir le fichier, la politique de conflit et afficher le
rapport.

## Format JSON, version 1

```json
{
  "format": "gcm4-hosts",
  "version": 1,
  "hosts": [
    {
      "group": "PROD/eu",
      "name": "api",
      "description": "",
      "host": "api.example.org",
      "user": "admin",
      "port": "2222",
      "protocol": "ssh",
      "font_color": "",
      "back_color": "",
      "extra_params": "",
      "auto_close_tab": "",
      "workspace": "False",
      "pre_connect_command": "",
      "post_connect_command": "",
      "keep_alive": "30",
      "commands": "uptime\nwho",
      "other_protocol_options": {"rdp": {"rdp_cert_ignore": "True"}}
    }
  ]
}
```

- `format` et `version` sont obligatoires ; une autre valeur est refusée
  (« format ou version non pris en charge »). Toute évolution
  incompatible du schéma incrémente `version`.
- **Champs communs** (toujours présents, dans cet ordre) : `group`
  (chemin `a/b`, vide = racine), `name` (obligatoire), `description`,
  `host`, `user`, `port`, `protocol`, `font_color`, `back_color`,
  `extra_params`, `auto_close_tab`, `workspace`, `pre_connect_command`,
  `post_connect_command`.
- **Options du protocole de l'hôte** : à plat, sous le nom de colonne de
  l'export GTK3 (`keep_alive`, `compressionLevel`, `backspace_key`,
  `delete_key`) ; les autres gardent leur clé de `gcm4.conf`. `commands`
  contient de vrais retours à la ligne (écrits `\n` dans `gcm4.conf`).
  Aucune option n'est interprétée : c'est le plugin du protocole qui le
  fait.
- `other_protocol_options` (facultatif) : options déjà saisies pour
  d'autres protocoles (`{protocole: {clé: valeur}}`), pour un aller-retour
  sans perte.
- `password` : **absent par défaut**. Présent seulement si l'export est
  demandé avec « inclure les mots de passe (en clair) » ; il est alors
  repris à l'import.
- Valeurs : texte. À l'import, nombres, booléens et `null` sont acceptés
  et convertis en texte ; une liste ou un objet (hors
  `other_protocol_options`) est une erreur.

## Format CSV

Même contenu, une ligne par hôte, UTF-8 (BOM de tableur accepté). En-tête :
champs communs, `password` si demandé, toutes les options rencontrées, puis
`other_protocol_options` (texte JSON) si un hôte en a. Toutes les lignes
partageant les mêmes colonnes, **une cellule d'option vide vaut « option
absente »**. Lecture stricte : un guillemet non fermé ou une cellule en
trop est une erreur.

## Exports de l'application GTK3

Les deux formats GTK3 s'importent sans perte :

- CSV GTK3 : mêmes colonnes que ci-dessus (colonnes `type`, `x11`,
  `agent`… et champs des plugins rangés dans les options du protocole) ;
- JSON GTK3 : simple liste d'objets à plat, sans `format` ni `version`.

Différences voulues avec l'import GTK3 : un groupe vide reste à la racine
(GTK3 le plaçait dans `IMPORT`), un protocole absent reste vide (GTK3
supposait `ssh`), et un doublon peut être renommé ou remplacer l'existant
au lieu d'être toujours ignoré.

## Conflits et rapport

Un hôte dont le nom existe déjà dans son groupe (y compris un doublon à
l'intérieur du fichier) est, au choix :

| Politique | Effet |
|---|---|
| ignorer (défaut) | l'hôte existant est gardé |
| renommer | l'hôte importé devient `nom (2)`, `nom (3)`… |
| remplacer | l'hôte importé prend la place de l'existant (même rang) |

Le rapport liste les hôtes ajoutés, remplacés, renommés (ancien → nouveau
chemin), ignorés, et les groupes créés.

## Garanties

- Fichier illisible ou mal formé : **rien n'est importé**, la configuration
  n'est pas modifiée ; le message (traduit) indique la ligne (CSV) ou
  l'entrée (JSON) fautive.
- Fichier exporté : permissions `0600` (il peut contenir des mots de
  passe).

## Ligne de commande (sans fenêtre)

Scriptable sans display (cron, SSH, test de paquet), sans charger GTK
(issue #154) :

```bash
gcm4 --export-json hotes.json                     # tous les hôtes, sans mot de passe
gcm4 --export-csv hotes.csv --include-passwords   # mots de passe EN CLAIR
gcm4 --import-json hotes.json                     # conflits ignorés (défaut)
gcm4 --import-csv hotes.csv --on-conflict rename  # ou replace
gcm4 -c /autre/dossier --export-json sauvegarde   # → sauvegarde.json
```

- Une seule option d'import ou d'export à la fois ; gcm4 s'arrête ensuite
  sans ouvrir de fenêtre.
- L'extension du format est ajoutée si elle manque (`--export-csv x.json`
  écrit `x.json.csv`).
- Bilan sur la sortie standard (« Hôtes exportés : 3 (…) », « Import de … :
  ajoutés 2, remplacés 0, renommés 1, ignorés 0, nouveaux groupes 1 ») ;
  code de sortie `0`.
- Erreur (configuration illisible, fichier mal formé, clé de chiffrement) :
  message sur la sortie d'erreur, code de sortie `5`, `gcm4.conf` non
  modifié. Une option mal utilisée donne le code `2` (argparse).
- À l'import, `gcm4.conf` est réécrit (mots de passe chiffrés, copie
  `gcm4.conf.bak`). Ne pas importer pendant que l'interface tourne sur la
  même configuration : elle réécrirait le fichier avec son propre état.

## Menu Fichier (interface)

Issue #153 (`src/gcm4/ui/echange.py`, travail fait par
`src/gcm4/core/exchange.py` et `src/gcm4/core/sauvegarde.py`) :

- **Importer des serveurs** : choix d'un fichier `.json` ou `.csv`
  (`Gtk.FileDialog`, `Gtk.FileChooserNative` avant GTK 4.10), puis un
  aperçu : nombre d'hôtes du fichier et combien existent déjà, choix de la
  stratégie (garder l'hôte existant, renommer l'hôte importé en `nom (2)`,
  remplacer l'hôte existant) avec le bilan prévu mis à jour à chaque choix.
  Un fichier contenant des mots de passe (en clair) affiche un
  avertissement. « Importer » enregistre `gcm4.conf` (mots de passe
  chiffrés), recharge l'arbre et affiche le bilan final ; « Annuler » ne
  change rien. Fichier mal formé : message, aucun aperçu.
- **Exporter des serveurs** : case « Inclure les mots de passe (en clair) »,
  décochée par défaut ; cochée, un avertissement reste visible tant qu'elle
  l'est. Le format suit l'extension du fichier choisi (`.json` ajouté sinon).
- **Enregistrer la configuration sous…** : copie `gcm4.conf` et `.gcm4.key`
  (la clé est nécessaire pour relire les mots de passe) dans le dossier
  choisi, en `0600`. Rien n'est écrasé : un dossier qui contient déjà l'un
  des deux fichiers, ou le dossier de configuration lui-même, est refusé.

Importer et exporter sont désactivés quand `gcm4.conf` n'a pas pu être lu ;
la copie reste possible pour sauvegarder le fichier avant de le réparer.
