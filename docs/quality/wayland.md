# Validation du chemin Wayland natif

Le cœur GTK3 ayant été retiré dans
[#453](https://github.com/Mathilde-Team/Gcm4/pull/453),
le chemin GTK4 peut être vérifié sous un vrai compositeur Wayland.
Ce contrôle répond à [#77](https://github.com/Mathilde-Team/Gcm4/issues/77)
et ne remplace pas la suite graphique complète sous Xvfb.

## Contrôle reproductible

Installer Weston, GTK4, libadwaita, VTE GTK4 et les dépendances de test
habituelles. Avec le Python système dans un venv `--system-site-packages` :

```sh
GCM_PYTHON=/chemin/du/venv/bin/python bash scripts/test-wayland.sh
```

Le script démarre Weston headless avec rendu logiciel Pixman, un dossier
d'exécution privé et un socket propre au contrôle. Il supprime `DISPLAY`
et `WAYLAND_SOCKET`, impose `GDK_BACKEND=wayland` et vérifie le type réel
`GdkWayland.WaylandDisplay` ; l'absence du backend ou du compositeur est
un échec, pas un saut. Les tests de démarrage, terminal local/VTE et
raccourcis s'exécutent ensuite dans le même processus pytest.

Le contrôle figure dans le job GTK4 de CI, avant la suite Xvfb complète.
Il n'ajoute pas de serveur XWayland, ne retire aucun contrôle existant
et ne change ni les protections ni le seuil de couverture. La couverture
des modules d'interface reste mesurée par la suite GTK4 complète et
combinée à celle du job Qualité.

Le statut de pytest est propagé directement. C'est important : l'essai
local de `weston -- python -c 'raise SystemExit(7)'` avec Weston 14.0.2
retourne zéro côté Weston malgré l'échec du client. Deux tests portables
avec un compositeur simulé verrouillent les statuts 0 et 7, la suppression
des variables X11 et le nettoyage du dossier privé.

## Limites

Un compositeur headless avec rendu logiciel ne valide pas un GPU, un écran
physique, les gestes tactiles, le multi-écran réel ou une session GNOME
particulière. Les protocoles distants restent soumis à leurs intégrations :
le succès de ce contrôle n'est pas une validation de serveurs RDP/VNC,
de matériel distant ni des plugins encore en portage sous
[#179](https://github.com/Mathilde-Team/Gcm4/issues/179).

Le cœur et VTE sont testés ici sans X11 ; ne pas présenter ce lot comme
une certification Wayland exhaustive de tous les protocoles.
