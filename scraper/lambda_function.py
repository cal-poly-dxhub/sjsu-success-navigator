"""Destination: gav scraper/lambda_function.py - the Lambda wrapper: change-gated
S3 upload (content-sha256 fingerprints), stale-object prune keyed off the FULL
seed list, gated StartIngestionJob with overlap detection and LastModified
self-healing. Morphs in the pull: no tiers (single daily run is always the
complete sweep), no catalog regeneration, no enrichment model call."""
