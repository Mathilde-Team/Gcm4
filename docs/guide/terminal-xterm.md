# Terminal xterm.js

Les onglets SSH, telnet, local, série et IPMI SOL utilisent le même terminal
xterm.js dans WebKitGTK 6.0 (#76). Les commandes, options INI et constructeurs
de commandes propres aux plugins ne changent pas. Le programme reste un vrai
processus POSIX sous PTY, pas une commande exécutée dans JavaScript.

## Installation et compatibilité

Le backend demande le typelib WebKit 6.0 pour GTK4, par exemple
`gir1.2-webkit-6.0` sur Debian/Ubuntu. Les paquets de la base ne rendent pas
WebKit obligatoire : gcm4 doit toujours démarrer sans les dépendances des
plugins. Si WebKit manque, les plugins terminaux sont explicitement signalés
en erreur dans Préférences > Extensions.

Sur Fedora, le backend est fourni par
[`webkitgtk6.0`](https://packages.fedoraproject.org/pkgs/webkitgtk/webkitgtk6.0/index.html).
Ce catalogue présente aussi des paquets EPEL 10 ; la limite de Rocky 9
ci-dessous ne doit donc pas être généralisée à toute la famille EL.
La disponibilité de ce paquet n'est pas une validation complète de gcm4
sur chaque version d'EL.

VTE n'est plus importé par les terminaux. La CI GTK4 et Debian 12 n'installe
plus VTE et exécute les vrais tests de terminaux et d'authentification.
Rocky 9 conserve le démarrage de la base et le signalement du backend absent :
WebKitGTK 6.0 n'est pas fourni dans ses dépôts de base, comme précédemment
VTE GTK4. Ce changement ne prétend pas fournir un terminal fonctionnel sur
cette cible sans installer le backend ; aucun repli vers GTK3 ou un navigateur
externe n'ouvre une commande locale.

Node n'est nécessaire ni au lancement de gcm4 ni à la construction du rendu :
les ressources sont déjà incluses dans le paquet. Node 22.16.0 sert seulement
aux tests de développement du JavaScript propriétaire.

## Utilisation

- **Clavier et encodage** : xterm traite ANSI, Unicode, souris et les modes
  du terminal. La conversion de l'encodage configuré est incrémentale côté
  Python, y compris pour une séquence multioctet partagée entre deux lectures.
  Les commandes de l'application arrivent en UTF-8 et sont transcodées comme
  la saisie. Les cinq liaisons INI historiques sont conservées ; « TTY Erase »
  lit le VERASE effectif du PTY.
- **Presse-papiers** : Ctrl+Maj+C copie la sélection et Ctrl+Maj+V colle.
  Le collage passe par xterm pour conserver le mode bracketed paste.
  Les messages clavier longs sont segmentés sans couper les caractères Unicode.
  Une sortie du programme ne demande jamais elle-même l'accès au presse-papiers.
- **Recherche** : Ctrl+Maj+F ouvre la barre native. Entrée et les deux flèches
  parcourent les occurrences littérales dans l'historique local.
- **Dimensions et historique** : le terminal suit l'onglet et le PTY reçoit
  ses dimensions par TIOCSWINSZ/SIGWINCH. L'historique xterm est limité à
  10 000 lignes. Les couleurs d'hôte et la palette Tango restent appliquées.
- **Fin de session** : les commandes différées sont annulées à la fermeture.
  Le groupe du programme et le premier plan reçoivent SIGHUP, puis SIGTERM
  et SIGKILL si nécessaire, et le processus est récolté sans bloquer GTK.
  La sortie normale et les notifications d'échec suivent les règles de la
  session commune ; un problème de rendu est affiché dans l'onglet.

## Frontière locale

Le rendu charge exclusivement sept ressources autorisées sous
`gcm4-terminal://local/`. Il n'y a ni serveur HTTP, ni port d'écoute,
ni WebSocket, ni CDN, ni téléchargement à l'exécution. La session réseau
WebKit est éphémère ; les navigations et nouvelles fenêtres étrangères,
permissions web et accès universels aux fichiers sont refusés. La CSP
interdit les connexions réseau, formulaires et scripts externes.

Les octets du PTY sont décodés puis donnés à `term.write`, jamais interprétés
comme HTML ou code JavaScript. Les expressions d'évaluation sont internes ;
toute donnée y est sérialisée en JSON. Le pont valide type, taille et
dimensions. Une seule sortie de 64 Kio est en vol, acquittée après analyse
par xterm, avant de réarmer la lecture du PTY : le noyau régule ainsi un
programme bavard au lieu d'accumuler une file JavaScript non bornée.

Le mot de passe éventuel circule par un tube non bloquant vers FD3.
Il n'est jamais envoyé à la page, ajouté aux arguments de l'amorce, ni
journalisé. Les variables d'environnement propres aux plugins conservent
leur comportement, notamment celui déjà défini par IPMI SOL.

## Dépendances figées et licences

Les fichiers npm sont recopiés sans transformation et leurs licences MIT
sont conservées à côté des bundles. `versions.json` contient les intégrités
SHA-512 des archives et SHA-256 des fichiers ; les tests vérifient ces
dernières hors ligne. Les trois distributions sont
[@xterm/xterm 6.0.0](https://registry.npmjs.org/@xterm/xterm/-/xterm-6.0.0.tgz),
[@xterm/addon-fit 0.11.0](https://registry.npmjs.org/@xterm/addon-fit/-/addon-fit-0.11.0.tgz)
et [@xterm/addon-search 0.16.0](https://registry.npmjs.org/@xterm/addon-search/-/addon-search-0.16.0.tgz).

Le code de liaison du projet reste GPL-3.0-or-later. Les fichiers d'un paquet
installé doivent comprendre le HTML, les styles, le bootstrap JavaScript,
les bundles et les licences : le backend ne dépend pas de fichiers du dépôt
disponibles seulement en mode source.

## Validation

`make test-xterm` exécute les tests Node du bootstrap et applique séparément
aux lignes et aux branches le seuil unique lu dans `pyproject.toml`.
Les bundles tiers figés ne sont pas du code de liaison du projet à instrumenter.
La CI Qualité lance exactement le même script que `make ci`.

Les tests GTK4 utilisent le vrai WebKit et le vrai buffer xterm pour vérifier
ANSI, Unicode, entrée PTY, redimensionnement, recherche, presse-papiers,
navigation interdite et fin de processus. Les tests historiques de chacun
des cinq plugins restent exécutés, avec lecture asynchrone du buffer à la
place de l'ancienne API VTE. Le cœur PTY est aussi testé sans GTK.

Le sandbox du processus web reste actif en production. Les seuls tests
désactivent son sandbox dans les conteneurs sans user namespaces, comme la
suite existante du plugin web ; ce réglage ne figure pas dans l'application.
Ces tests ne constituent pas une certification de matériel série/IPMI,
de serveur distant réel ou de GPU physique.
