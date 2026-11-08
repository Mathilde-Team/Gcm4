# Partage de sessions SSH et VNC

La réponse « 1+2 » dans [l'issue #82](https://github.com/Mathilde-Team/Gcm4/issues/82)
valide tmux pour SSH et un réglage `shared` pour VNC. Elle ne demande pas
de relais entre instances gcm4.

## SSH : partage explicite via tmux

Le formulaire d'hôte SSH GTK4 propose « Session partagée (tmux) » et un
nom facultatif, enregistré dans les options du protocole :

```ini
shared_session = True
shared_session_name = equipe-42
```

Ces clés sont conservées telles quelles dans la section INI de l'hôte
et dans le dictionnaire `protocol_options["ssh"]`. Le nom vide sélectionne `gcm4` ;
seuls 1 à 64 caractères ASCII alphanumériques, `_` ou `-` sont acceptés.
Un nom invalide empêche l'enregistrement et le lancement lorsque le
partage est activé ; il reste modifiable sans bloquer un profil désactivé.

Le lancement demande un pseudo-terminal (`-tt`) et exécute sur le serveur
`exec tmux new-session -A -s <nom>`. La première option
`RemoteCommand=none` neutralise une commande distante configurée dans le
profil ou dans OpenSSH, afin d'éviter deux commandes distantes concurrentes.
L'authentification SSH habituelle, y compris le tube de mot de passe vers
expect, reste inchangée. Aucun mot de passe n'est ajouté à la commande tmux.

Le serveur doit disposer de tmux dans le PATH du compte distant.
Les participants doivent se connecter au même serveur, sous le même
compte Unix, avec le même nom de session. Cela ne fournit ni partage
inter-comptes, ni nouvelles permissions, ni accès sans authentification.
Les frappes et les secrets affichés sont visibles par les autres clients ;
les commandes automatiques après connexion s'appliquent également.
`Ctrl+B`, puis `D` détache le client ; fermer le shell partagé peut
terminer la session pour tous les participants.

Sans activation explicite, le lancement SSH est strictement inchangé.
L'absence de tmux s'affiche comme une erreur distante, sans repli qui
ferait croire à tort qu'une session est partagée.

## VNC : partage dans le formulaire d'hôte

L'adaptateur `plugins/pluginvnc2/core.py` + `gtk4.py` expose le composant
monofichier existant au contrat v1 (#179). La case « Session partagée
(VNC) » est active par défaut ; sa valeur est persistée dans la clé
`vnc_shared` et transmise à `VncConnectionInfo.shared`, puis au
`ClientInit` RFB d'AsyncVnc4. Un client ancien sans argument `shared=`
ne peut pas lancer une session demandée non partagée : le lancement
échoue plutôt que d'ignorer ce choix.

Le serveur VNC reste décisionnaire : `shared=True` demande le maintien
des autres clients, sans créer de droits d'accès ; `shared=False` peut
déconnecter les autres clients selon la politique du serveur.

Les options historiques `vnc_display`, `vnc_view_only`, `vnc_fullscreen`
et `vnc_share_clipboard` restent utilisables. Un affichage renseigné
remplace le port par `5900 + affichage` ; seules les valeurs ASCII de
0 à 59635 sont acceptées. La lecture seule supprime les contrôleurs
clavier/souris et interdit frappe, Ctrl+Alt+Suppr, commande d'alimentation
et presse-papier sortant. Fermer l'onglet arrête connexion et reconnexion.
La clé historique `vnc_viewer` est conservée, mais ne sélectionne plus
de programme externe : l'intégration retenue est le widget GTK4 embarqué.

AsyncVnc4 est importé à l'ouverture, pas à la découverte du plugin.
Sans sa dépendance ou sans asyncio natif dans la boucle GTK
(PyGObject ≥ 3.50), le démarrage de gcm4 et le formulaire restent
utilisables ; ouvrir VNC affiche une erreur explicite. Les anciennes
distributions ne sont pas retirées des cibles.

## Vérification et limites

Le job GTK4 requis lance les tests de contrat dans `tests/gtk4`,
la suite native `tests/vnc` avec `GCM_TESTS_VNC=1`, puis les tests
existants du composant. La mesure de ce dernier cible `vnc_tab`
(pas les tests du sous-projet) et s'ajoute à `.coverage.gtk4` avant
publication de l'artifact. Le total est soumis aux mêmes seuils ;
aucune exclusion de production n'est ajoutée.

Les tests natifs vérifient les deux valeurs du drapeau partagé sur une
connexion RFB 3.8 locale, une image brute, l'envoi de touche en contrôle,
la lecture seule, le cycle de fermeture et les interactions du widget.
Ils ne certifient pas les politiques d'exclusivité de tous les serveurs,
les équipements physiques, ni les variantes propriétaires d'authentification.
