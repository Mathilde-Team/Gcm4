# Roadmap — Gcm4

> Dernière mise à jour : 2026-09-21
> Source : GitHub issues, `docs/features-backlog.md`, `docs/gtk4-migration.md`

## Vue d'ensemble

| Métrique | Valeur |
|----------|--------|
| Issues ouvertes | 31 |
| Issues closes | 70 (sessions + fonctionnalités) |
| Migration GTK4 | 4/4 menus contextuels portés, 3/3 GestureClick, 2/4 signaux éditeur inline |
| Chantiers GTK4 restants | 3 (audités, #96, #97, #71) |

## Phases d'intégration

### Phase 0 — Décisions produit 🔄 (bloque Phase 1+)

| Issue | Décision | Impact |
|-------|----------|--------|
| #99 | Renommage GCM → gcm4 (6 noms à confirmer) | Débloque versioning + packaging |
| #98 | Master password (comportement oubli + protection) | Débloque câblage GTK |
| #74 | Sort de snmp_push_core.py : transports portés dans snmp_bulk_core.py, module supprimé (PR en relecture) | Nettoyage architecture |
| #98 | Master password (comportement oubli + protection) — tranché le 12/10/2026 (facultatif, code de recouvrement), câblé dans gcm4 (PR en relecture) | Débloque câblage GTK |
| #74 | Sort de snmp_push_core.py (supprimer ou fusionner) | Nettoyage architecture |

### Phase 1 — Migration GTK4 + Qualité 🔄

**Chantiers GTK4 (3 indépendants, audités) :**

| Issue | Chantier | Lignes | Difficulté | Bloque |
|-------|----------|--------|------------|--------|
| #96 | focus-out / populate-popup (éditeur inline) | — | Haute | — |
| #97 | plugins/ssh/gtk4.py | 4435 | Haute | #73 |
| #71 | Chantier RDP | — | Haute | AsyncRdp4 #36 |
| #72 | Vérification GestureClick (manuel) | — | Moyenne | — |
| #73 | Généraliser core.py/gtk4.py | — | Moyenne | (après #97) |

**Qualité / dette technique :**

| Issue | Titre | Difficulté | Parallélisable |
|-------|-------|------------|----------------|
| #88 | flake8 — 182 erreurs | Faible | Avec #89, #90, #91 |
| #89 | ruff format — 26 fichiers | Faible | Avec #88, #90, #91 |
| #90 | Pre-commit hook | Faible | Avec #88, #89, #91 |
| #91 | Relecture docstrings tests | Faible | Avec #88, #89, #90 |

**Avancement GTK4** :
- ✅ Verrou GTK3 levé (plugin_base.py)
- ✅ Plugin pilote SSH réorganisé (core.py)
- ✅ 4/4 menus contextuels portés (Gio.Menu/Popover)
- ✅ 3/3 sites GestureClick portés
- ✅ 2/4 signaux éditeur inline portés (key-press, button-press)
- 🔄 3 chantiers restants (focus-out/populate-popup, plugins/ssh/gtk4.py, RDP)

### Phase 2 — Fonctionnalités post-GTK4 + PluginVNC2 🔜

| Issue | Titre | Dépend de | Difficulté |
|-------|-------|-----------|------------|
| #75 | SFTP — navigateur de fichiers (plugin gcm4 `plugins/sftp/`, PR en relecture) | — | Haute |
| #76 | Terminal web xterm.js | #71 | Moyenne |
| #77 | Wayland natif | #71 | Moyenne |
| #79 | Refactor MVC | Migration GTK4 | Faible |
| #92 | PluginVNC2 — Raccourci clavier | — | Faible |
| #93 | PluginVNC2 — Dialogue texte ouvert : case « Garder la fenêtre ouverte après l'envoi » (PR en relecture) | — | Faible |
| #92 | PluginVNC2 — Raccourci clavier : `Alt+F12` ouvre l'envoi de texte (PR en relecture) | — | Faible |
| #93 | PluginVNC2 — Dialogue texte ouvert | — | Faible |
| #94 | PluginVNC2 — Décompte reconnexion | — | Faible |
| #95 | PluginVNC2 — Presse-papiers image | Bloqué (fork) | Faible |
| #101 | PluginVNC2 — QEMU Extended Key | Bloqué (env) | Faible |

### Phase 3 — Backlog faible 🔜

| Issue | Titre | Difficulté |
|-------|-------|------------|
| #100 | Multi-sélection hôtes : port et groupe en lot (PR en relecture) | Faible |
| #83 | Icône system tray | Faible |
| #84 | Cluster liste déroulante : commandes favorites éditables (PR en relecture) | Faible |
| #85 | Charset dynamique | Faible |
| #84 | Cluster liste déroulante | Faible |
| #85 | Charset dynamique : encodage par hôte des onglets terminal gcm4 (PR en relecture) | Faible |
| #86 | Aide contextuelle | Faible |
| #87 | Tests orphelins | Faible |
| #80 | Notifications desktop | Faible |
| #81 | Application mobile | Faible |
| #82 | Partage multi-utilisateurs | Faible |

## Dépendance cross-repo

- **AsyncRdp4 #36** (assembler RDP dans GCM) dépend de Gcm4 #71, #96, #97
- Si gtk-frdp n'a pas de chemin GTK4, l'alternative asyncrdp (dépôt AsyncRdp4) remplace le plugin RDP

## Voir aussi

- [Backlog complet](features-backlog.md)
- [Migration GTK4](gtk4-migration.md)
- [Renommage GCM → gcm4](gcm4-rename.md)
