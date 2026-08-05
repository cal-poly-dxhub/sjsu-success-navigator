"""Destination: camp backend/services/cards.py - chunk-to-card shaping (dedupe,
section deprioritization, 220-char trim, 4-card cap, follow-up mapping). Moves in
unchanged; depends on `section` arriving in the retrieval metadata (the scraper's
sidecars must carry it)."""
