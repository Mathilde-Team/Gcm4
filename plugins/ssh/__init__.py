# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin SSH — dossier pilote de la migration GTK4 (découpage par plugin).

Contient aujourd'hui uniquement :

- ``core.py`` — logique métier (parsing ``~/.ssh/config``, migration/import
  gcm.conf, zéro GTK), fusion de l'ancien ``ssh_config_parser.py`` +
  ``ssh_migrate_gcm.py`` (session-26).

Ne contient PAS encore de ``gtk4.py`` : les widgets réels (éditeur visuel,
gestion des clés) restent pour l'instant dans les fichiers historiques à
la racine du dépôt (``ssh_config_editor.py``, ``ssh_key_manager_dialog.py``,
``key_picker_dialog.py``) et dans ``plugins/plugin_ssh.py`` (toujours GTK3),
qui importent ``plugins.ssh.core`` à la place des anciens modules —
extraction de ``gtk4.py`` réservée à une session séparée, tributaire du
portage du cœur GTK4 (voir ``docs/gtk4-migration.md`` §3.6, point 3).

Ce fichier n'expose donc volontairement aucun ``get_plugin()``/
``get_batch_plugin()`` pour l'instant : le mécanisme de découverte du
cœur reste le glob plat existant sur ``plugins/plugin_*.py``
(``plugin_base._discover_plugin_modules()``), inchangé par cette session
— ce sous-dossier n'est pas (encore) un point d'entrée de plugin, juste
l'emplacement du nouveau ``core.py``.
"""

from __future__ import annotations
