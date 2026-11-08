# Environnements de test RDP et VNC

Serveurs jetables pour essayer à la main les plugins `rdp` et `vnc` de gcm4
contre un vrai serveur (#427, lot 5). Ils remplacent les anciens dossiers
`rdp/` et `vnc/` de la racine.

| Service | Image | Port | Identifiants de test |
|---|---|---|---|
| `rdp` | `lscr.io/linuxserver/rdesktop:ubuntu-xfce` (bureau XFCE) | 3389 | `abc` / `abc` (défaut de l'image) |
| `vnc` | `gcm4-test-vnc`, construite depuis `vnc/` (TigerVNC + XFCE) | 5901 | mot de passe VNC `testpass`, ou `VNC_PASSWORD` |

Ces mots de passe sont publics : ne pas exposer ces serveurs hors du poste
ou du réseau de test.

## Version standard (docker compose)

```sh
docker compose -f environnements-test/docker-compose.yml up -d        # les deux
docker compose -f environnements-test/docker-compose.yml up -d vnc    # un seul
VNC_PASSWORD=autre docker compose -f environnements-test/docker-compose.yml up -d vnc
docker compose -f environnements-test/docker-compose.yml down         # arrêt
docker compose -f environnements-test/docker-compose.yml down -v      # arrêt + état RDP effacé
```

## Version swarm (docker stack)

`docker stack deploy` ne construit pas d'image : construire d'abord l'image
VNC, puis déployer la pile.

```sh
docker compose -f environnements-test/docker-compose.yml build vnc
docker stack deploy -c environnements-test/docker-stack.yml gcm4-tests
docker stack services gcm4-tests
docker stack rm gcm4-tests
```

Sur un swarm de plusieurs nœuds, pousser `gcm4-test-vnc:latest` dans un
registre accessible à tous les nœuds, ou ajouter une contrainte de placement
vers le nœud qui l'a construite.

## Différences avec les anciens fichiers

- Un seul projet `gcm4-tests` au lieu de deux fichiers séparés ; conteneurs
  nommés `gcm4-test-rdp` et `gcm4-test-vnc`.
- L'état du bureau RDP va dans le volume nommé `rdp-config` au lieu de
  `rdp/data/` : il ne s'accumule plus dans l'arbre du dépôt (84 Mo et des
  fichiers sensibles constatés en session 01).
- Les montages optionnels `/var/run/docker.sock` (accès root au démon de
  l'hôte) et `/dev/dri` (absent sur beaucoup de postes, fait échouer le
  démarrage) sont retirés. Les rajouter localement au besoin.
- En swarm, `security_opt`, `shm_size`, `container_name` et `restart` ne sont
  pas pris en charge : `/dev/shm` est monté en tmpfs de même taille et
  `deploy.restart_policy` remplace `restart`.
