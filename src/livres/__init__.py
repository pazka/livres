"""livres: legal French ebook catalogue for "Ma liseuse" (https://livres.hosh.it).

Layers (see docs/ARCHITECTURE.md):
    domain      pure data types, no I/O
    sources/    one adapter per source kind, talk to the outside through HttpClient only
    services    search, health monitor, book requests, mail outbox (no HTTP framework)
    web/        HTTP layer: routing, auth, OPDS 2.0 / JSON / HTML rendering
"""

import os

__version__ = os.environ.get("LIVRES_VERSION", "0.1.0-dev")
