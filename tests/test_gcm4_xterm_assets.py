# SPDX-License-Identifier: GPL-3.0-or-later
"""Intégrité hors ligne et limites du frontend terminal embarqué (#76)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/gcm4/ui/xterm_assets"


def test_distribution_figee_et_licences():
    """Chaque bundle tiers et sa licence MIT gardent leur empreinte d'origine."""
    manifeste = json.loads((ASSETS / "versions.json").read_text())
    assert {n: p["version"] for n, p in manifeste["packages"].items()} == {
        "@xterm/xterm": "6.0.0",
        "@xterm/addon-fit": "0.11.0",
        "@xterm/addon-search": "0.16.0",
    }
    for paquet in manifeste["packages"].values():
        assert paquet["integrity"].startswith("sha512-")
        assert paquet["url"].startswith("https://registry.npmjs.org/@xterm/")
    for nom, empreinte in manifeste["sha256"].items():
        assert hashlib.sha256((ASSETS / nom).read_bytes()).hexdigest() == empreinte
    for nom in ("LICENSE-xterm", "LICENSE-addon-fit", "LICENSE-addon-search"):
        assert (
            "MIT" in (ASSETS / nom).read_text()
            or "Permission is hereby granted" in (ASSETS / nom).read_text()
        )


def test_page_sans_acces_reseau_ni_html_dynamique():
    """CSP restrictive et aucun code distant, WebSocket ou interprétation du flux PTY."""
    page = (ASSETS / "index.html").read_text()
    script = (ASSETS / "terminal.js").read_text()
    assert "connect-src 'none'" in page and "default-src 'none'" in page
    assert 'src="https:' not in page and 'src="http:' not in page
    assert "innerHTML" not in script.replace(
        "// Ressources figées locales. Le contenu du PTY ne passe jamais par innerHTML.", ""
    )
    assert "WebSocket" not in script and "fetch(" not in script
