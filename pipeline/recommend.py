"""Daily picks: score Singapore candidates and explain why each one made the cut.

    score = Σ weight · component, each component in [0, 1]

    fit       cuisine match (your explicit likes + what you rank highly), price vs budget
    quality   Beli score shrunk toward the city mean when few people have rated it
    novelty   newly surfaced, on Beli's popular-in-Singapore list, not picked recently
    distance  decays with km from the origin; neutral when coordinates are unknown
    social    how many friends rated it and how highly

Candidates are every open place in Beli's Singapore catalogue. Your own lists don't change the
score: a pick you've ranked or bookmarked is only tagged as such. Chains (more than
`max_outlets` branches under one name) and `cuisines_avoid` are filtered out.

Picks are weekly. Each list mixes `popular_picks` places from Beli's popular-in-Singapore list
with discoveries from the rest of the catalogue; discoveries must clear `min_beli_score` with at
least `min_ratings` ratings, falling back to the best of the rest while scores are still filling
in. So the list never goes stale, a place picked in any context sits out the next `COOLDOWN_WEEKS`
weeks entirely, then returns with reduced novelty; a week-seeded jitter reshuffles near-ties, and
selection is greedy with a penalty for repeating a cuisine or neighbourhood.
"""

import hashlib
import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from .config import CLOSED_STATUSES

WEIGHTS = {"fit": 0.30, "quality": 0.25, "novelty": 0.20, "distance": 0.15, "social": 0.10}
# Big enough to reshuffle near-ties every week (most candidates sit within ~0.05 of each other),
# small enough that a clearly better place still wins.
JITTER = 0.06
# A place picked in any context is kept out of every context for this many weeks after.
COOLDOWN_WEEKS = 6
PRIOR_STRENGTH = 30  # ratings needed before a place's own score outweighs the city mean
REPEAT_PENALTY = 0.08  # per earlier pick sharing a cuisine or neighbourhood
PRICE_SIGNS = {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}


def haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def jitter(day: str, business_id: int) -> float:
    h = hashlib.sha256(f"{day}:{business_id}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2**32


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


def taste_profile(ranked: list[dict]) -> dict[str, float]:
    """Cuisine affinity in [-1, 1] from your own ranked scores: cuisines you score above your
    personal average lean positive. Needs 2+ places per cuisine to count."""
    scored = [(r["cuisines"], r["user_score"]) for r in ranked if r.get("user_score") is not None]
    if len(scored) < 5:
        return {}
    mean = sum(s for _, s in scored) / len(scored)
    by_cuisine = defaultdict(list)
    for cuisines, s in scored:
        for c in cuisines:
            by_cuisine[c].append(s)
    return {c: max(-1.0, min(1.0, (sum(v) / len(v) - mean) / 2)) for c, v in by_cuisine.items() if len(v) >= 2}


def components(b: dict, prefs: dict, ctx: dict) -> tuple[dict[str, float], list[tuple[float, str]]]:
    """(component scores, reasons as (strength, text)) for one candidate."""
    reasons: list[tuple[float, str]] = []
    cuisines = b["cuisines"]
    likes = {c.lower() for c in prefs.get("cuisines_like", [])}
    taste = ctx["taste"]

    # fit
    liked = [c for c in cuisines if c.lower() in likes]
    learned = max((taste.get(c, 0.0) for c in cuisines), default=0.0)
    cuisine_fit = 1.0 if liked else 0.5 + 0.5 * learned if cuisines else 0.4
    if liked:
        reasons.append((0.8, f"{liked[0]}, one of your go-to cuisines"))
    elif learned > 0.25:
        best = max(cuisines, key=lambda c: taste.get(c, 0.0))
        reasons.append((0.6 + learned / 4, f"You rank {best} places above your average"))
    budget = prefs.get("budget")
    if b["price"] is None or budget is None:
        price_fit = 0.6
    elif b["price"] <= budget:
        price_fit = 1.0
        if b["price"] < budget:
            reasons.append((0.3, f"{PRICE_SIGNS[b['price']]}, under your {PRICE_SIGNS[budget]} budget"))
    else:
        price_fit = max(0.0, 1 - 0.6 * (b["price"] - budget))
    fit = 0.6 * cuisine_fit + 0.4 * price_fit

    # quality
    s, n = b["beli_score"], b["review_count"] or 0
    if s is None:
        quality = 0.45
    else:
        shrunk = (n * s + PRIOR_STRENGTH * ctx["city_mean"]) / (n + PRIOR_STRENGTH) if n else (s + ctx["city_mean"]) / 2
        quality = max(0.0, min(1.0, (shrunk - 5) / 5))
        if s >= 8:
            count = f" from {n:,} ratings" if n else ""
            reasons.append((0.4 + (s - 8) / 4, f"Beli {s:.1f}{count}"))

    # novelty
    novelty = 0.4
    if b["first_seen"] >= ctx["recent_cutoff"]:
        novelty = 0.8
        reasons.append((0.5, f"New on Beli ({_fmt(b['first_seen'])})"))
    if (m := michelin_label(b.get("michelin"))):
        reasons.append((0.85, m))
    if b["popular"]:
        novelty = min(1.0, novelty + 0.15)
        reasons.append((0.45, f"#{b['popular_rank']} on Beli's popular-in-Singapore list" if b["popular_rank"]
                              else "On Beli's popular-in-Singapore list"))
    if b["last_picked"]:
        # Back from cooldown: novelty recovers over another few weeks rather than all at once.
        days = (date.fromisoformat(ctx["today"]) - date.fromisoformat(b["last_picked"])).days
        novelty *= min(1.0, days / (COOLDOWN_WEEKS * 7 * 2))

    # distance
    origin = prefs.get("origin")
    if origin and b["lat"] is not None and b["lng"] is not None:
        km = haversine_km(origin["lat"], origin["lng"], b["lat"], b["lng"])
        distance = math.exp(-km / max(prefs.get("max_km") or 8, 1) * 1.5)
        b["km"] = round(km, 1)
        if km <= (prefs.get("max_km") or 8) / 2:
            reasons.append((0.35, f"{km:.1f} km from {origin.get('label', 'you')}"))
    else:
        distance = 0.5

    # social
    friends, favg = b["friend_count"], b["friend_avg"]
    social = 0.0
    if friends:
        social = min(1.0, math.log1p(friends) / math.log1p(10)) * (0.5 + 0.5 * ((favg or 7) - 5) / 5)
        avg = f", avg {favg:.1f}" if favg else ""
        reasons.append((0.4 + min(friends, 10) / 25, f"{friends} friend{'s' if friends > 1 else ''} rated it{avg}"))

    comps = {"fit": fit, "quality": quality, "novelty": novelty, "distance": distance, "social": max(0.0, social)}
    return comps, reasons


def _fmt(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.day} {d.strftime('%b')}"


def candidates(con, week: str | None = None) -> tuple[list[dict], list[dict]]:
    """(candidate places, your ranked places). Every open Singapore place is a candidate,
    including ones you've ranked; your lists only tag a pick and shape the taste profile."""
    from . import db

    closed = ", ".join(f"'{s}'" for s in CLOSED_STATUSES)
    rows = db.business_rows(con, f"in_sg = 1 AND COALESCE(status, '') NOT IN ({closed})")
    guide = michelin()
    outlets = outlet_counts([r[0] for r in con.execute("SELECT name FROM businesses WHERE in_sg = 1")])
    latest = con.execute("SELECT MAX(captured_on) FROM sightings").fetchone()[0]
    lists = defaultdict(dict)
    for r in con.execute(
        """SELECT business_id, list_type, MIN(captured_on) first_on, MAX(captured_on) last_on,
                  MAX(CASE WHEN captured_on = ? THEN user_score END) score,
                  MIN(CASE WHEN captured_on = ? THEN position END) position
           FROM sightings GROUP BY business_id, list_type""", (latest, latest)):
        lists[r["business_id"]][r["list_type"]] = dict(r)
    social = {r["business_id"]: dict(r) for r in con.execute("SELECT * FROM social")}
    # Earlier weeks only, so regenerating this week (after a prefs edit) doesn't penalise its own picks.
    last_pick = dict(con.execute("SELECT business_id, MAX(pick_date) FROM picks WHERE pick_date < ? GROUP BY business_id",
                                 (week or "9999",)).fetchall())

    cands, ranked = [], []
    for b in rows:
        seen = lists.get(b["business_id"], {})
        rk = seen.get("ranked")
        b["ranked_score"] = rk["score"] if rk and rk["last_on"] == latest else None
        if b["ranked_score"] is not None:
            ranked.append({**b, "user_score": b["ranked_score"]})
        bm = seen.get("bookmarks")
        b["bookmarked_on"] = bm["first_on"] if bm and bm["last_on"] == latest else None
        b["popular"] = "popular" in seen and seen["popular"]["last_on"] == latest
        # Beli's own order on the popular list, 1-based; None when it isn't on today's list.
        b["popular_rank"] = seen["popular"]["position"] + 1 if b["popular"] and seen["popular"]["position"] is not None else None
        b["outlets"] = outlets.get(b["name"], 1)
        b["michelin"] = guide.get(b["business_id"])
        b["friend_count"] = social.get(b["business_id"], {}).get("friend_count") or 0
        b["friend_avg"] = social.get(b["business_id"], {}).get("friend_avg")
        b["last_picked"] = last_pick.get(b["business_id"])
        cands.append(b)
    return cands, ranked


def rank(cands: list[dict], ranked: list[dict], prefs: dict, today: str, recent_cutoff: str) -> list[dict]:
    """Every candidate scored, best first (before diversity selection)."""
    scores = [b["beli_score"] for b in cands + ranked if b["beli_score"] is not None]
    ctx = {
        "today": today,
        "recent_cutoff": recent_cutoff,
        "city_mean": sum(scores) / len(scores) if scores else 7.0,
        "taste": taste_profile(ranked),
    }
    avoid = {c.lower() for c in prefs.get("cuisines_avoid", [])}
    max_outlets = prefs.get("max_outlets")
    out = []
    cooldown = (date.fromisoformat(today) - timedelta(weeks=COOLDOWN_WEEKS)).isoformat()
    for b in cands:
        if any(c.lower() in avoid for c in b["cuisines"]):
            continue
        if b["last_picked"] and b["last_picked"] > cooldown:
            continue
        if max_outlets and b["outlets"] > max_outlets:
            continue
        b = dict(b)
        comps, reasons = components(b, prefs, ctx)
        total = sum(WEIGHTS[k] * v for k, v in comps.items()) + JITTER * jitter(today, b["business_id"])
        reasons.sort(key=lambda r: -r[0])
        out.append({**b, "score": round(total, 4), "components": {k: round(v, 3) for k, v in comps.items()},
                    "reasons": [text for _, text in reasons[:3]] or ["A solid Singapore option you haven't tried"]})
    out.sort(key=lambda r: -r["score"])
    return out


def is_good(b: dict, prefs: dict) -> bool:
    """A discovery worth recommending on Beli's own evidence, not just on fit and distance."""
    return (b["beli_score"] is not None and b["beli_score"] >= prefs.get("min_beli_score", 0)
            and (b["review_count"] or 0) >= prefs.get("min_ratings", 0))


def mix(scored: list[dict], prefs: dict) -> list[dict]:
    """`popular_picks` from Beli's popular list, the rest discoveries from the wider catalogue.
    Discoveries that clear the quality bar go first; if there aren't enough yet, the best of the
    rest fill in, and so does the other pool when one runs short."""
    k = int(prefs.get("picks", 10))
    n_pop = min(int(prefs.get("popular_picks", 0)), k)
    popular = [b for b in scored if b["popular"]]
    others = [b for b in scored if not b["popular"]]
    picked = select(popular, n_pop)
    good = [b for b in others if is_good(b, prefs)]
    picked += select(good, k - len(picked), picked)
    # Short on proven discoveries (scores still filling in): keep the split, best of the rest first.
    for pool in ([b for b in others if not is_good(b, prefs)], scored):
        if len(picked) < k:
            ids = {p["business_id"] for p in picked}
            picked += select([b for b in pool if b["business_id"] not in ids], k - len(picked), picked)
    for p in picked:
        p["pool"] = "popular" if p["popular"] else "discover"
    return sorted(picked, key=lambda b: -b["score"])


def select(scored: list[dict], k: int, already: list[dict] | None = None) -> list[dict]:
    """Greedy pick with a penalty for repeating a cuisine or neighbourhood (including `already`)."""
    picked: list[dict] = []
    prior = already or []
    pool = scored[: max(k * 8, 50)]
    while pool and len(picked) < k:
        def adjusted(b):
            overlap = sum(
                bool(set(b["cuisines"]) & set(p["cuisines"])) + (b["neighborhood"] is not None and b["neighborhood"] == p["neighborhood"])
                for p in prior + picked)
            return b["score"] - REPEAT_PENALTY * overlap
        best = max(pool, key=adjusted)
        picked.append(best)
        pool.remove(best)
    return picked
