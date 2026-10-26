# CLAUDE.md — notes pour une future session sur pluginvnc2

Contexte : onglet VNC GTK4 pour gnome-connection-manager, construit par-dessus
un fork maison d'`asyncvnc2` (Tight/Hextile/ZlibHex, curseur, resize, Fence/
ContinuousUpdates — voir le `CLAUDE.md` du fork `asyncvnc2` lui-même pour ce
qui concerne le protocole ; celui-ci ne couvre que la couche GTK4).

## Contexte détaillé (imports)

Ce fichier reste volontairement court — c'est ce qu'il faut lire à
chaque démarrage de session. Le reste est recomposé via import :

@docs/pieges.md — pièges déjà rencontrés, à ne pas réintroduire (par
thème, pas par date). À lire avant de toucher au multi-écran, aux
holds souris/clavier, ou à `HostKeyStore`.

@docs/features-backlog.md — fonctionnalités actuelles, ce qui est hors
périmètre, état des dépendances, et le backlog encore ouvert (y compris,
dans sa section « Backlog ouvert », le détail et le raisonnement complet
de chaque session récente — pas dupliqué ici pour que ce fichier reste
court).

L'historique détaillé session par session (raisonnement, décisions de
conception, contre-épreuves) vit dans `docs/sessions/session-NN.md` —
un fichier par session, **non importé ici**. À ouvrir seulement si tu
as besoin du contexte précis d'une session passée ; sinon les deux
imports ci-dessus suffisent.

## Pourquoi un fichier unique

`vnc_tab.py` regroupe volontairement tout — y compris `HostKeyStore`
(pinning de clé hôte), qui vivait avant dans un module séparé — pour
simplifier l'installation dans le dépôt principal (un seul fichier à
copier). Ne PAS re-scinder en plusieurs modules sans en discuter d'abord :
c'est un choix demandé explicitement, pas un oubli de refactoring.

Conséquence directe pour les tests : comme `Gtk`/`Gdk` sont importés au
niveau module, **tout le fichier échoue à l'import sans GTK4** — y compris
pour tester de la logique pure (`HostKeyStore`, `VncConnectionInfo`). Voir
`docs/pieges.md` § « État des tests ».

## État courant

- 158 tests, tous verts via le stub `tests/gtk_stub/` (voir Commandes de
- 172 tests, tous verts via le stub `tests/gtk_stub/` (voir Commandes de
- 166 tests, tous verts via le stub `tests/gtk_stub/` (voir Commandes de
  qualité). Sans GTK4 installé, `pytest tests/` sans le `PYTHONPATH`
  du stub les collecte mais les SKIP tous — pas un échec caché.
- 24 sessions de développement documentées dans `docs/sessions/`
  (2026-09-01 à 2026-09-15), résumées par thème dans `docs/pieges.md`.
- Aucune hypothèse d'intégration ouverte (conteneur d'onglets et action
  plein écran tranchés contre le vrai dépôt `gcm4`, cf.
  `docs/features-backlog.md`).
- Backlog ouvert : aucun. L'audit « tous les accès à `self.client` qui
  écrivent réellement sur le socket » (sessions 09 à 15) est clos — les
  huit points trouvés sont tous protégés, ne pas en chercher un neuvième
  par principe. Depuis, chaque session (16 à 22 à ce jour) est repartie
  d'un besoin utilisateur réel plutôt que d'un nouveau point de cet
  audit, avec API vérifiée avant écriture (MCP Context7 si connecté,
  sinon recherche web contre `docs.gtk.org`/PyGObject) : capture d'écran ;
  raccourcis clavier globaux + reconnexion automatique ; copie d'écran
  dans le presse-papiers ; raccourci dédié à cette copie ; envoi de texte
  comme frappes clavier ; bouton « Déconnecter » ; icône « Coller » sur le
  champ d'envoi de texte ; délai affiché pendant l'attente d'une
  reconnexion automatique ; inversion du sens de la molette. Détail,
  raisonnement et idées écartées de chaque session :
  `docs/features-backlog.md` § « Backlog ouvert ».
- **Le ruleset ruff (`pyproject.toml`) est volontairement étroit — ne
  PAS l'élargir ni lancer `ruff check --isolated`/`--fix` sans lire
  `docs/pieges.md` d'abord.** Vérifié en détail en session 24 : la
  quasi-totalité de ce qu'un ruleset par défaut signalerait en plus
  correspond à des patrons délibérés de ce fichier (garde `except
  Exception`, style `Optional[X]`, `datetime` naïfs intentionnels,
  `__gsignals__` requis par PyGObject) — et le tri d'imports (`I001`)
  est activement dangereux ici (casserait l'ordre obligatoire
  `gi.require_version` avant `from gi.repository import ...`).

## Commandes de qualité

```bash
ruff check vnc_tab.py
ruff format --check vnc_tab.py

# Tests sans GTK4 installé (stub CI) :
PYTHONPATH=tests/gtk_stub pytest tests/ --ignore=tests/gtk_stub

# Tests sur un poste avec GTK4/PyGObject réel :
pytest tests/
```

`./install.sh` fait les trois dans l'ordre (plus la mise en place du
venv et des dépendances).

## Prochaine feature

Plus de point ouvert dans l'audit « accès à `self.client` » (clos en
session 15, cf. `docs/features-backlog.md` § « Backlog ouvert »). Ne pas
chercher un neuvième point de cette famille par principe — l'audit était
exhaustif. Repartir d'un besoin utilisateur réel ou d'une extension
protocolaire côté `asyncvnc2_patched` (pas d'hypothèse d'intégration
ouverte, pas d'extension câblée à ce jour — voir § « Dépendance »
ci-dessous) plutôt que d'inventer une fonctionnalité hors périmètre.

Si le connecteur MCP Context7 est disponible, s'en servir pour vérifier
les API réellement disponibles AVANT de proposer un candidat (sinon,
recherche web contre `docs.gtk.org`/PyGObject). Ne jamais implémenter
unilatéralement une longue liste de fonctionnalités sans confirmation,
même sur une demande formulée largement (« ajoute tout ce qui manque ») ;
borner, proposer, laisser choisir. Une demande de continuation générale
(« continue les features à faire », « continuer »), en revanche, n'appelle
pas cette phase de confirmation tant que la feature choisie reste unique
et bornée — précédent établi en sessions 16, 18, 19, 20, 21, 22, 23 et 24.

Idées différées à ce jour, à reprendre seulement si un besoin réel se
confirme (pas par défaut) : presse-papiers image en plus du texte
(dépend du fork `asyncvnc2_patched`, pas livré ici) ; retour visuel sur
l'affichage pour la capture/copie d'écran ; raccourci clavier dédié pour
ouvrir le dialogue d'envoi de texte ; dialogue restant ouvert pour un
envoi répété ; bouton de barre d'outils séparé envoyant directement le
presse-papiers sans passer par le dialogue (jugé moins sûr qu'un
préremplissage relisible) ; décompte en temps réel pour la reconnexion
automatique plutôt qu'un message statique. Détail et raisonnement complet
de chaque piste : `docs/features-backlog.md` § « Backlog ouvert ».

## Dépendances

`PyGObject>=3.50` (intégration asyncio native via
`gi.events.GLibEventLoopPolicy`, plus besoin de `gbulb`), `numpy`,
`cryptography` (pinning de clé hôte), `loguru` (logging — jamais de
`print()`, cf. le style déjà en place dans le fichier).
