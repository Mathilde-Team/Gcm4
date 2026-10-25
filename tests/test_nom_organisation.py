"""Le nom d'organisation doit être « Mathilde-Team », jamais sa faute de frappe (issue #310).

Le nom erroné (sans le « l » de « Mathilde ») ne désigne aucun compte
existant (404 via l'API GitHub) : GitHub redirige aujourd'hui l'ancien nom
vers « Mathilde-Team », mais cette redirection disparaît dès que quelqu'un
crée un compte de ce nom. Une CI qui installe ou compare depuis le nom
erroné installerait alors du code d'un tiers — le test interdit la faute
dans tous les fichiers suivis, catalogues compilés compris. Le nom est
reconstruit par morceaux pour que ce fichier ne se signale pas lui-même.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# « Mathide-Team », écrit en deux morceaux pour échapper à la recherche.
ANCIEN_NOM = "Mathi" + "de-Team"


def fichiers_suivis() -> list[str]:
    """Chemins suivis par git à la racine du dépôt."""
    resultat = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return [ligne for ligne in resultat.stdout.splitlines() if ligne]


def test_aucun_ancien_nom_dans_les_fichiers_suivis() -> None:
    """Aucun fichier suivi ne contient le nom d'organisation erroné."""
    coupables: list[str] = []
    for chemin in fichiers_suivis():
        cible = ROOT / chemin
        if not cible.is_file():
            continue  # sous-module ou lien supprimé en attente de commit
        contenu = cible.read_text(encoding="utf-8", errors="replace")
        if ANCIEN_NOM in contenu:
            coupables.append(chemin)
    assert coupables == [], f"ancien nom d'organisation présent dans : {coupables}"
