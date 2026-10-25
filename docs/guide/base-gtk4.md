# gcm4, base GTK4 : installation et premiers pas

`gcm4` est la nouvelle génération de Gnome Connection Manager, réécrite en
GTK 4 et libadwaita (épopée #118). Cette page décrit sa **base** (#126) :
l'application sans aucun plugin de connexion. L'application GTK3
(`gnome-connection-manager`) reste disponible à côté jusqu'à son retrait
(#177).

## Limites de la base

**Aucune connexion ne peut être ouverte sans plugin.** La base n'en contient
aucun, et les plugins GTK4 (SSH, RDP, VNC, série...) ne sont pas encore
publiés. Avec la base seule :

- l'arbre des groupes et des hôtes, la recherche, le glisser-déposer, les
  couleurs de groupe et le dialogue d'hôte (création, modification,
  duplication) fonctionnent ;
- le choix du protocole dans le dialogue d'hôte indique « Aucune extension
  n'est installée. » ; les options d'un protocole sans plugin sont
  conservées telles quelles ;
- **Connecter** (menu de l'arbre, double-clic ou Entrée) affiche « Aucune extension
  ne gère le protocole « ssh ». Installez-la pour ouvrir cette
  connexion. » : aucun onglet ne s'ouvre, l'hôte n'est pas modifié ;
- les onglets de la session précédente ne sont pas restaurés : un message
  liste les hôtes sans extension ; les hôtes passés sur la ligne de
  commande (`gcm4 groupe/nom`) sont acceptés mais ne s'ouvrent pas ;
- l'import et l'export de configuration ne sont pas encore dans la base
  (#152, #153).

**Paramètres > Extensions** liste les plugins trouvés, chargés ou en erreur
(avec la raison) ; sans plugin, elle indique « Aucune extension trouvée. ».

## Installation

Distributions testées par la CI (job « Paquets ») : **Debian 13**,
**Ubuntu 24.04**, **Ubuntu 26.04** et **Fedora 44**. Il faut Python 3.12 ou
plus récent : Ubuntu 22.04, Debian 12 et EL 9 ne conviennent pas.

Le paquet `.deb` est joint aux releases à partir de la prochaine (#156) ; le
`.rpm` pas encore. Les deux se construisent aussi depuis le dépôt (voir
[Publication](../release.md)) ou se téléchargent
comme artefacts `paquet-deb-gcm4` / `paquet-rpm-gcm4` d'une exécution de la
CI.

=== "Debian, Ubuntu"

    ```sh
    make deb-gcm4
    sudo apt install ./build-deb/dist/gcm4_4.0.0_all.deb
    ```

    apt installe les dépendances : `python3-gi`, `gir1.2-gtk-4.0`,
    `gir1.2-adw-1`, `python3-loguru`.

=== "Fedora"

    ```sh
    make rpm-gcm4
    sudo dnf install ./build-rpm/dist/gcm4-4.0.0-1.noarch.rpm
    ```

    dnf installe les dépendances : `python3-gobject`,
    `gobject-introspection`, `gtk4`, `libadwaita`, `python3-loguru`.

=== "Depuis les sources"

    ```sh
    make run-gtk4
    ```

    Voir le README : PyGObject, GTK 4 et libadwaita viennent des paquets de
    la distribution (`uv` avec `--system-site-packages`).

Le paquet installe la commande `gcm4`, l'entrée « Gnome Connection
Manager » du menu des applications, son icône, et les traductions des
29 langues. Désinstallation : `sudo apt remove gcm4` ou `sudo dnf remove
gcm4` ; la configuration (`~/.config/gcm4/`) est conservée.

Vérifier la version installée :

```sh
gcm4 --version
```

Si PyGObject, GTK 4 ou libadwaita manque (installation depuis les sources),
gcm4 s'arrête avec le code 3 et affiche la commande d'installation adaptée
à la distribution.

## Premier lancement

1. **Configuration.** gcm4 utilise `~/.config/gcm4/` (ou
   `$XDG_CONFIG_HOME/gcm4/`). Au premier lancement, si ce dossier n'existe
   pas et que celui de l'application GTK3, `~/.gcm/`, existe, il est
   **copié** : hôtes, groupes, mots de passe chiffrés et préférences sont
   repris. `~/.gcm/` n'est jamais modifié ; ensuite, les deux
   configurations évoluent séparément. Détails :
   [Fichier de configuration](../configuration.md#emplacements-99).
   Si la copie échoue (droits, disque plein), gcm4 s'arrête avec le code 4
   et un message, sans rien laisser de partiel.
2. **Fenêtre principale.** L'arbre des hôtes à gauche, l'onglet
   « Accueil » à droite.
3. **Mises à jour.** gcm4 demande une fois « Vérifier les mises à jour ? » :
   s'il est autorisé, il interroge GitHub (api.github.com) à chaque
   démarrage et signale une version plus récente. Le choix se change dans
   **Paramètres > Général** (« Vérifier les mises à jour ») ;
   `GCM_NO_UPDATE_CHECK=1` désactive la vérification sans rien demander.

Un second `gcm4` lancé pendant que le premier tourne ne crée pas de
seconde fenêtre : la fenêtre existante est présentée, et les options du
second lancement (`--debug`, hôtes) sont ignorées.

Ligne de commande :

```text
gcm4 [--version] [--debug] [-c DOSSIER] [GROUPE/NOM ...]
```

`-c DOSSIER` utilise un autre dossier de configuration (sans migration
depuis `~/.gcm/`).

## Configuration et journaux

| Quoi | Où |
|---|---|
| Configuration | `~/.config/gcm4/gcm4.conf` |
| Clé de chiffrement des mots de passe | `~/.config/gcm4/.gcm4.key` |
| Sauvegarde de la configuration | `~/.config/gcm4/gcm4.conf.bak` |
| Journal de l'application | `~/.config/gcm4/log/gcm-app.log` (10 Mo, conservé 14 jours) |

Avec `-c DOSSIER`, tout est dans `DOSSIER`. Le journal est toujours écrit,
au niveau `INFO` par défaut ; **Paramètres > Journalisation** règle ce
niveau et ouvre le dossier des journaux.

## Mode debug

Le mode debug écrit chaque étape de l'application dans le journal et sur la
sortie d'erreur, avec le processus et le thread émetteurs. Au choix :

- l'action **gcm4 (debug mode)** du lanceur (clic droit sur l'icône dans le
  menu des applications ou le dock) ;
- `gcm4 --debug` dans un terminal ;
- `GCM_DEBUG=1 gcm4`, ou `GCM_LOG_LEVEL=TRACE gcm4` pour encore plus de
  détail ;
- `GCM_LOG_FILE=/tmp/gcm4-debug.log gcm4 --debug` en écrit en plus une copie
  à part, pratique à joindre à un rapport.

Le mode debug se choisit au lancement : si gcm4 tourne déjà, quittez-le
d'abord.

Référence complète : [Journaux et mode debug](../journalisation.md).

## Signaler un bug

1. Notez la version (`gcm4 --version`) et la distribution
   (`cat /etc/os-release`).
2. Quittez gcm4, puis relancez-le en mode debug avec une copie à part du
   journal :

    ```sh
    GCM_LOG_FILE=/tmp/gcm4-debug.log gcm4 --debug
    ```

3. Reproduisez le problème, puis quittez gcm4.
4. **Relisez `/tmp/gcm4-debug.log` avant de le joindre.** Les mots de passe
   ne sont pas écrits dans le journal, mais en mode debug une erreur
   inattendue affiche la valeur des variables du programme, qui peut
   contenir une adresse, un nom d'utilisateur, un mot de passe ou le
   contenu d'un fichier. Remplacez ce qui doit rester privé (par exemple
   par `***`).
5. Ouvrez un ticket sur
   [github.com/Mathilde-Team/Gcm4/issues](https://github.com/Mathilde-Team/Gcm4/issues)
   avec : ce que vous avez fait, ce que vous attendiez, ce qui s'est passé,
   la version, la distribution et le journal relu.
