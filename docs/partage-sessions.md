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

## VNC : volet non encore livré au formulaire d'hôte

`plugins/pluginvnc2/vnc_tab.py` possède déjà le champ de profil booléen
`VncConnectionInfo.shared`, vrai par défaut, transmis au connecteur
asyncvnc2. Le composant n'est pas encore enregistré comme plugin de
connexion du contrat v1 : aucun formulaire d'hôte GTK4 VNC actif ne peut
donc recevoir ce réglage dans ce lot.

Le portage/intégration est suivi par
[#179](https://github.com/Mathilde-Team/Gcm4/issues/179) et
[#107](https://github.com/Mathilde-Team/Gcm4/issues/107).
Ce lot ne modifie pas ce chantier réservé à un autre intervenant.
Après intégration, le formulaire devra exposer `shared`, conserver vrai
comme valeur par défaut et vérifier sa transmission au connecteur.
Le serveur VNC reste décisionnaire : `shared=True` demande le maintien
des autres clients, sans créer de droits d'accès ; `shared=False` peut
déconnecter les autres clients selon la politique du serveur.

L'issue #82 reste ouverte : la livraison SSH seule ne constitue pas
une réalisation complète des options 1+2.
