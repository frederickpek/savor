"""Static configuration for the daily Beli ingest."""

import os
from pathlib import Path

# Singapore only. Beli lists (ranked, bookmarks) span every city the user has eaten in, so
# anything outside this box is stored but never recommended.
CITY = "Singapore"
COUNTRY = "Singapore"
SG_BOUNDS = (1.15, 1.48, 103.59, 104.10)  # lat_min, lat_max, lng_min, lng_max
SG_CENTER = (1.2903, 103.8520)  # City Hall; used as `coords` for city-scoped discovery calls

CATEGORY = "RES"

# Raw gzipped API responses kept on the data branch; the DB keeps normalized history forever.
SNAPSHOT_RETENTION_DAYS = 30

# Beli score and rating count only come one place at a time (two calls each, no bulk form,
# login required), so each run (every 6 hours) fetches a small batch. At 1.25s per call,
# 150 places is ~7 minutes; a 429 ends the step for that run. Places with no data yet go first
# (top 200, MICHELIN, this week's picks, your lists, priced places, the rest); once all have data,
# the 150 stalest are refreshed. See ingest.score_queue.
MAX_SCORE_PLACES = int(os.environ.get("SAVOR_SCORE_BUDGET") or 150)
# One cool-down before giving up for the day when Beli starts answering 429.
RATE_LIMIT_COOLDOWN = 30

# Hand-maintained MICHELIN Guide Singapore snapshot (stars + Bib Gourmand), matched to Beli ids.
# The guide changes once a year; refresh this file when the next edition is out.
MICHELIN_FILE = Path(__file__).parent / "data" / "michelin.json"

# Places the Singapore bounding box lets through by mistake (the box clips southern Johor).
EXCLUDED_BUSINESS_IDS = {
    2960427,  # Crab Bro Noodle House 蟹哥哥 - SKS City Mall JBCC, Johor Bahru
}

# Beli statuses that mean "don't send anyone here".
CLOSED_STATUSES = ("CLOSED", "PERMANENTLY_CLOSED", "CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY")

# Fields every normalized business should have; drives the missing-field report.
REQUIRED_FIELDS = [
    "business_id", "name", "address", "lat", "lng", "price", "cuisines",
    "beli_score", "review_count",
]

# Keys tried, in order, when the API shape is unknown or drifts. The first non-null wins.
SCORE_KEYS = ("beli_score", "average_score", "avg_score", "avg_business_score", "rec_score")
REVIEW_COUNT_KEYS = ("review_count", "count_rated", "num_ratings", "rating_count", "num_rankings")
ADDRESS_KEYS = ("address", "formatted_address", "street_address", "vicinity")
