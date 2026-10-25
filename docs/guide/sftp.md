# Navigateur de fichiers SFTP (gcm4)

Le plugin `sftp` (`plugins/sftp/`, issue #75) ouvre, dans un onglet, la liste
des fichiers d'un serveur SSH. Il utilise la bibliothèque **paramiko**
(`pip install "paramiko>=3.4.0"`, ou le paquet `python3-paramiko` de la
distribution) ; sans elle, le plugin est signalé en erreur dans
**Paramètres > Extensions** et le reste de gcm4 fonctionne.

## Créer un hôte

Dans le dialogue d'hôte, choisir le protocole **sftp**. Le port proposé est
22. Utilisateur et mot de passe sont les champs communs de l'hôte. Le
protocole a deux options :

| Option (`gcm4.conf`) | Champ | Effet |
|---|---|---|
| `remote-dir` | Dossier distant | dossier ouvert à la connexion, absolu ou relatif au dossier d'accueil ; vide : dossier d'accueil |
| `identity-file` | Fichier de clé privée | clé utilisée à la place de l'agent SSH (`~` accepté) ; le fichier doit exister |

Sans mot de passe enregistré, gcm4 essaie l'agent SSH et les clés par défaut
de `~/.ssh`. Si le serveur les refuse, un mot de passe est demandé ; il n'est
pas enregistré.

## Clé d'hôte

La clé du serveur est vérifiée dans `~/.ssh/known_hosts`, le fichier
d'OpenSSH (les clés déjà acceptées par `ssh` sont donc reconnues).

- **Clé inconnue** : une barre en haut de l'onglet affiche son empreinte
  (`SHA256:...`, la même que `ssh-keygen -lf`). **Faire confiance et se
  connecter** ajoute la ligne à la fin de `~/.ssh/known_hosts`, sans
  réécrire le reste du fichier ; **Annuler** ne fait rien.
- **Clé différente de celle connue** : la connexion est refusée, sans
  proposition d'accepter, comme avec OpenSSH. Vérifier le serveur, puis
  retirer l'ancienne ligne (`ssh-keygen -R "[hôte]:port"`).

## Dans l'onglet

| Action | Comment |
|---|---|
| Ouvrir un dossier | double-clic ou Entrée sur la ligne, ou chemin saisi dans la barre puis Entrée |
| Dossier parent, actualiser | boutons de la barre d'outils |
| Télécharger un fichier | double-clic sur le fichier, ou bouton **Télécharger** |
| Envoyer des fichiers | bouton **Envoyer des fichiers**, ou glisser-déposer depuis le gestionnaire de fichiers |
| Nouveau dossier, renommer | boutons de la barre d'outils, nom saisi dans la barre en haut de l'onglet |
| Supprimer | bouton **Supprimer**, puis confirmation |

Les téléchargements vont dans le dossier des téléchargements de
l'utilisateur (XDG), ou dans celui de la clé `download-dir` de la section
`[plugin sftp]` de `gcm4.conf`. Un fichier déjà présent n'est jamais
écrasé : `rapport (1).pdf`, `rapport (2).pdf`...

Les opérations s'exécutent l'une après l'autre en arrière-plan ; la ligne
d'état et la barre de progression en suivent l'avancement. Les erreurs du
serveur (droits, dossier absent...) s'affichent dans la ligne d'état.

## Limites

- Pas de transfert de dossier : un dossier déposé est ignoré et signalé.
- Suppression d'un dossier **vide** seulement (pas de suppression
  récursive).
- Les commandes avant et après la connexion de l'hôte ne sont pas
  exécutées.
- Pas de tunnel ni de serveur de rebond (`ProxyJump`) ; `~/.ssh/config`
  n'est pas lu.
