"""Weekly picks: a seeded random draw from the places that pass each context's rules.

There is no ranking. A context is a set of hard rules (price range, distance from the origin,
Beli score and rating-count bounds, cuisines to avoid, whether Beli's top 200 counts); every
place that passes is equally likely. The draw is seeded by the week and context, so a re-run in
the same week gives the same list and next week reshuffles.

Rotation is the point, across weeks more than within one: a place picked in any context sits out
every context for `cooldown_weeks`, and one picked `max_picks_per_26_weeks` times in the last 26
weeks sits out until the oldest of those picks falls outside the window. Only when a context's
pool runs dry does it reuse places on cooldown, longest-rested first. Contexts draw smallest
pool first so the broad ones (Any Day) don't use up what the narrow ones need.

Each list takes `popular_picks` places from Beli's popular-in-Singapore list and the rest from
everywhere else, and keeps any cuisine or neighbourhood to `max_same_cuisine` /
`max_same_neighbourhood` places. Your own lists never filter or boost; a pick you've ranked or
bookmarked is only tagged. Chains (more than `max_outlets` branches) are left out.
"""

import hashlib
import math
import random
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from .config import CLOSED_STATUSES

PRICE_SIGNS = {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}
WINDOW_WEEKS = 26  # for max_picks_per_26_weeks


def haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def shuffled(items: list[dict], seed: str) -> list[dict]:
    """A stable random order: the same seed always gives the same order."""
    rng = random.Random(hashlib.sha256(seed.encode()).digest())
    out = sorted(items, key=lambda b: b["business_id"])  # independent of input order
    rng.shuffle(out)
    return out


def michelin() -> dict[int, int | str]:
    """{business_id: 1-3 stars or "bib"} from the hand-maintained guide snapshot."""
    import json

    from .config import MICHELIN_FILE

    if not MICHELIN_FILE.exists():
        return {}
    doc = json.loads(MICHELIN_FILE.read_text())
    return {r["business_id"]: r["award"] for r in doc["restaurants"] if r.get("business_id")}


def michelin_label(award: int | str | None) -> str | None:
    if award == "bib":
        return "MICHELIN Bib Gourmand"
    return f"{award} MICHELIN Star{'s' if award != 1 else ''}" if award else None


def brand(name: str) -> str:
    """Chain key from a branch name: 'Ya Kun Kaya Toast @ Bugis (Level 1)' -> 'ya kun kaya toast'."""
    head = re.split(r"\s*(?:@|\(|\||,|[-–]\s|\sat\s)", name.lower(), maxsplit=1)[0]
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9& ]", "", head)).strip()


def outlet_counts(names: list[str]) -> dict[str, int]:
    """{name: branches of its brand}. Branches named without a separator ("McDonald's Ang Mo Kio")
    join the longest brand seen elsewhere in the catalogue that prefixes them."""
    counts = Counter(brand(n) for n in names)  # per row: many branches share one exact name
    known = sorted((k for k, c in counts.items() if c >= 2 and len(k) >= 3), key=len, reverse=True)
    keys = {}
    for n in set(names):
        k = brand(n)
        keys[n] = k if counts[k] >= 2 else next((c for c in known if k.startswith(c + " ")), k)
    merged = Counter(keys[n] for n in names)
    return {n: merged[k] if k else 1 for n, k in keys.items()}


def resolve_prefs(prefs: dict, context: str | None) -> dict:
    out = {k: v for k, v in prefs.items() if k != "contexts"}
    if context:
        out.update(prefs.get("contexts", {}).get(context, {}))
        out["context"] = context
    return out


def candidates(con, week: str | None = None) -> tuple[list[dict], list[dict]]:
    """(candidate places, your ranked places). Every open Singapore place is a candidate,
    including ones you've ranked; your lists only tag a pick."""
    from . import db

    closed = ", ".join(f"'{s}'" for s in CLOSED_STATUSES)
    rows = db.business_rows(con, f"in_sg = 1 AND COALESCE(status, '') NOT IN ({closed})")
    guide = michelin()
    outlets = outlet_counts([r[0] for r in con.execute("SELECT name FROM businesses WHERE in_sg = 1")])
    lists = defaultdict(dict)
    for r in con.execute("SELECT * FROM list_members WHERE list_type != 'catalogue'"):
        lists[r["business_id"]][r["list_type"]] = dict(r)
    # Earlier weeks only, so regenerating this week (after a prefs edit) doesn't count its own picks.
    history = defaultdict(list)
    for bid, day in con.execute("SELECT DISTINCT business_id, pick_date FROM picks WHERE pick_date < ? ORDER BY pick_date",
                                (week or "9999",)):
        history[bid].append(day)

    cands, ranked = [], []
    for b in rows:
        seen = lists.get(b["business_id"], {})
        b["ranked_score"] = seen.get("ranked", {}).get("user_score")
        if b["ranked_score"] is not None:
            ranked.append({**b, "user_score": b["ranked_score"]})
        b["bookmarked_on"] = seen.get("bookmarks", {}).get("since")
        b["popular"] = "popular" in seen
        # Beli's own order on the popular list, 1-based; None when it isn't on the current list.
        b["popular_rank"] = seen["popular"]["position"] + 1 if b["popular"] and seen["popular"]["position"] is not None else None
        b["outlets"] = outlets.get(b["name"], 1)
        b["michelin"] = guide.get(b["business_id"])
        b["picked_on"] = history.get(b["business_id"], [])  # earlier weeks it was picked, oldest first
        cands.append(b)
    return cands, ranked


def eligible(b: dict, prefs: dict) -> bool:
    """The context's hard rules. Everything that passes is an equally good candidate."""
    avoid = {c.lower() for c in prefs.get("cuisines_avoid", [])}
    if any(c.lower() in avoid for c in b["cuisines"]):
        return False
    if prefs.get("max_outlets") and b["outlets"] > prefs["max_outlets"]:
        return False
    if b["price"] is None:
        if not prefs.get("include_unpriced", True):
            return False
    elif not prefs.get("price_min", 1) <= b["price"] <= prefs.get("price_max", 4):
        return False
    s, n = b["beli_score"], b["review_count"] or 0
    if s is None or s < prefs.get("min_beli_score", 0):
        return False
    if not prefs.get("min_ratings", 0) <= n <= prefs.get("max_ratings", math.inf):
        return False
    if prefs.get("exclude_popular") and b["popular"]:
        return False
    if prefs.get("min_km") or prefs.get("max_km"):
        if b.get("km") is None:
            return False
        if not prefs.get("min_km", 0) <= b["km"] <= prefs.get("max_km", math.inf):
            return False
    return True


def with_distance(cands: list[dict], prefs: dict) -> list[dict]:
    origin = prefs.get("origin")
    for b in cands:
        b["km"] = (round(haversine_km(origin["lat"], origin["lng"], b["lat"], b["lng"]), 1)
                   if origin and b["lat"] is not None and b["lng"] is not None else None)
    return cands


def rested(b: dict, week: str, prefs: dict) -> bool:
    """Out of cooldown: not picked in the last `cooldown_weeks`, and not picked
    `max_picks_per_26_weeks` times in the last 26 weeks."""
    if not b["picked_on"]:
        return True
    w = date.fromisoformat(week)
    since = lambda weeks: (w - timedelta(weeks=weeks)).isoformat()
    if b["picked_on"][-1] > since(prefs.get("cooldown_weeks", 10)):
        return False
    cap = prefs.get("max_picks_per_26_weeks")
    return not cap or sum(d > since(WINDOW_WEEKS) for d in b["picked_on"]) < cap


def reasons(b: dict, prefs: dict) -> list[str]:
    """Plain facts about why it fits, for the card; never used to choose."""
    out = []
    if (m := michelin_label(b.get("michelin"))):
        out.append(m)
    if b["popular"]:
        out.append(f"#{b['popular_rank']} on Beli's popular-in-Singapore list" if b["popular_rank"]
                   else "On Beli's popular-in-Singapore list")
    if b["beli_score"] is not None:
        out.append(f"Beli {b['beli_score']:.1f} from {b['review_count'] or 0:,} ratings")
    if prefs.get("max_ratings") and not b["popular"]:
        out.append("Well rated, still under the radar")
    if b.get("km") is not None and (prefs.get("min_km") or prefs.get("max_km")):
        out.append(f"{b['km']:.1f} km from {prefs['origin'].get('label', 'the origin')}")
    return out


def draw(cands: list[dict], prefs: dict, week: str, taken: set[int]) -> list[dict]:
    """This week's list for one context. `taken` holds places already drawn this week by other
    contexts; they count as picked (the cooldown is shared)."""
    k = int(prefs.get("picks", 10))
    pool = [b for b in cands if eligible(b, prefs) and b["business_id"] not in taken]
    order = shuffled(pool, f"{week}:{prefs.get('context') or 'default'}")
    fresh = [b for b in order if rested(b, week, prefs)]
    # Pool ran dry: reuse places on cooldown, longest-rested first.
    reuse = sorted((b for b in order if not rested(b, week, prefs)), key=lambda b: b["picked_on"][-1])

    max_cuisine = prefs.get("max_same_cuisine", 2)
    max_hood = prefs.get("max_same_neighbourhood", 2)
    picked: list[dict] = []
    ids: set[int] = set()
    cuisines, hoods = Counter(), Counter()

    def take(source: list[dict], want: int, strict: bool) -> None:
        for b in source:
            if want <= 0 or len(picked) >= k:
                return
            if b["business_id"] in ids:
                continue
            if strict and (any(cuisines[c] >= max_cuisine for c in b["cuisines"])
                           or (b["neighborhood"] and hoods[b["neighborhood"]] >= max_hood)):
                continue
            picked.append(b)
            ids.add(b["business_id"])
            cuisines.update(b["cuisines"])
            if b["neighborhood"]:
                hoods[b["neighborhood"]] += 1
            want -= 1

    n_pop = min(int(prefs.get("popular_picks", 0)), k)
    for source in (fresh, reuse):
        for strict in (True, False):  # variety rules give way before the list comes up short
            take([b for b in source if b["popular"]], n_pop - sum(b["popular"] for b in picked), strict)
            take([b for b in source if not b["popular"]], k - len(picked), strict)
            take(source, k - len(picked), strict)  # one half ran short: the other fills in

    out = []
    for b in picked:
        b = dict(b)
        b["pool"] = "popular" if b["popular"] else "discover"
        b["reasons"] = reasons(b, prefs)
        out.append(b)
    return sorted(out, key=lambda b: (-(b["beli_score"] or 0), b["name"]))


def pool_size(cands: list[dict], prefs: dict) -> int:
    return sum(eligible(b, prefs) for b in cands)
