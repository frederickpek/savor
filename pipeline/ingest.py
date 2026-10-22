"""Pull every source from Beli, keep the raw responses, and upsert normalized rows.

The core is `catalogue`: one filter-list call that returns every Singapore place Beli knows
(~18k, with coords, neighbourhood, status, and price/cuisine for about half). Your ranked and
bookmark lists are collected for tagging and taste. Each source is isolated: a failure is
recorded in the run summary and the rest carry on.
"""

import gzip
import json
import os
import time
import traceback
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from . import db, geocode, normalize
from .beli import Beli, PermanentError, RateLimited
from .config import CATEGORY, CITY, EXCLUDED_BUSINESS_IDS, MAX_SCORE_PLACES, RATE_LIMIT_COOLDOWN, SG_CENTER

CORE_SOURCES = ("catalogue",)
# Beli's city path segment format is unconfirmed; the first variant that returns rows wins.
CITY_VARIANTS = (CITY, f"{CITY}, {CITY}", "Singapore, SG")


def write_raw(dir_: Path, name: str, payload: Any) -> None:
    """Gzipped raw response, written atomically so a crash never leaves a truncated file."""
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / f"{name}.json.gz"
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, path)


def list_sources(beli: Beli) -> dict[str, Callable[[], Any]]:
    uid = beli.user_id
    coords = f"{SG_CENTER[0]},{SG_CENTER[1]}"

    def popular():
        for city in CITY_VARIANTS:
            try:
                payload = beli.get(f"/api/popular-in-city/{uid}/{city}/")
            except PermanentError:
                continue
            if normalize.records(payload):
                return payload
        return []

    def catalogue():
        # Despite list_field/sort_method, this returns the whole city, not a trending shortlist.
        return beli.post("/api/filter-list/", {
            "filters": [{"key": "CITY", "value": [CITY]}],
            "list_field": "TRENDING", "user": uid, "user2": uid, "category": CATEGORY,
            "bounds": None, "sort_method": "Most Trending", "coords": coords, "ids": [],
            "load_businesses": True,
        })

    return {
        "ranked": lambda: beli.get("/api/get-ranking/", {"user": uid, "category": CATEGORY}),
        "bookmarks": lambda: beli.get("/api/get-bookmark/", {"user": uid, "category": CATEGORY}),
        "popular": popular,
        "catalogue": catalogue,
    }


def list_rows(source: str, payload: Any) -> list[tuple[dict, float | None]]:
    """(business object, your score) pairs from a list response, in list order."""
    out = []
    for item in normalize.records(payload):
        b = normalize.business_of(item)
        if b is not None:
            score = normalize.to_float(item.get("score")) if source == "ranked" else None
            out.append((b, score))
    if not out and isinstance(payload, dict) and isinstance(payload.get("results"), list):
        # filter-list without load_businesses: bare ids, kept as placeholders.
        out = [({"id": i, "name": f"#{i}"}, None) for i in payload["results"] if isinstance(i, int)]
    return out


def ingest_lists(beli: Beli, con, snap: Path, today: str, now: str, summary: dict) -> None:
    for source, fetch in list_sources(beli).items():
        try:
            payload = fetch()
        except Exception as e:
            summary["failures"][source] = repr(e)[:300]
            print(f"  {source}: FAILED {e!r}")
            continue
        write_raw(snap, source, payload)
        n = 0
        for pos, (b, user_score) in enumerate(list_rows(source, payload)):
            row = normalize.business(b)
            if row is None:
                continue
            if b.get("name") == f"#{row['business_id']}":
                # Placeholder from a bare id; never let it overwrite a real name.
                con.execute(
                    "INSERT OR IGNORE INTO businesses (business_id, name, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?)", (row["business_id"], f"#{row['business_id']}", today, today))
            else:
                db.upsert_business(con, row, today)
            db.add_sighting(con, today, now, source, row["business_id"], pos, user_score)
            n += 1
        summary["sources"][source] = n
        print(f"  {source}: {n} businesses{churn(con, source, today)}")
    con.commit()


def churn(con, source: str, today: str) -> str:
    """How much a list changed since its previous capture, e.g. " (+3 / -3 since 2026-10-20)",
    so the run log shows how often Beli actually refreshes it."""
    prev = con.execute("SELECT MAX(captured_on) FROM sightings WHERE list_type = ? AND captured_on < ?",
                       (source, today)).fetchone()[0]
    if prev is None:
        return ""
    ids = lambda day: {r[0] for r in con.execute(
        "SELECT business_id FROM sightings WHERE list_type = ? AND captured_on = ?", (source, day))}
    now, before = ids(today), ids(prev)
    return f" (+{len(now - before)} / -{len(before - now)} since {prev})"


def score_queue(con, today: str) -> list[dict]:
    """Singapore places to fetch ratings for, in order. Never-fetched places always come first, by
    usefulness: Beli's top 200 (in Beli's order), MICHELIN-listed places, this week's picks, your
    lists, catalogue places with a price and cuisine, then the rest. Once every place has been
    fetched, the queue is everything ordered stalest first, so each run refreshes the oldest data."""
    from .recommend import michelin

    con.execute("CREATE TEMP TABLE IF NOT EXISTS guide (business_id INTEGER PRIMARY KEY)")
    con.execute("DELETE FROM guide")
    con.executemany("INSERT OR IGNORE INTO guide VALUES (?)", [(b,) for b in michelin()])
    return [dict(r) for r in con.execute(
        """SELECT b.business_id, b.beli_score, b.count_fetched_on FROM businesses b
           WHERE b.in_sg = 1 AND COALESCE(b.status, '') NOT IN ('CLOSED', 'PERMANENTLY_CLOSED', 'CLOSED_PERMANENTLY')
           ORDER BY
             b.count_fetched_on IS NOT NULL,
             b.count_fetched_on,
             COALESCE((SELECT MIN(s.position) FROM sightings s WHERE s.business_id = b.business_id
                       AND s.list_type = 'popular'), 9999),
             EXISTS (SELECT 1 FROM guide g WHERE g.business_id = b.business_id) DESC,
             EXISTS (SELECT 1 FROM picks p WHERE p.business_id = b.business_id
                     AND p.pick_date = (SELECT MAX(pick_date) FROM picks)) DESC,
             EXISTS (SELECT 1 FROM sightings s WHERE s.business_id = b.business_id
                     AND s.list_type IN ('bookmarks', 'ranked')) DESC,
             (b.price IS NOT NULL AND b.cuisines IS NOT NULL) DESC,
             b.business_id""")]


def fetch_score(beli: Beli, bid: int, today: str) -> dict:
    """Fields for one place: its rating count, and its Beli average when anyone has rated it.
    Raises RateLimited on a 429 so the caller decides how to back off."""
    try:
        n = normalize.to_int(beli.get(f"/api/business-count-rated/{bid}/", patient=False))
        fields = {"count_fetched_on": today}
        if n is not None:
            fields["review_count"] = n
        if n:  # nobody has rated it: there is no average to ask for
            payload = beli.get("/api/databusinessfloat-sparse/",
                               {"business": bid, "field__name": "AVGBUSINESSSCORE"}, patient=False)
            if (s := normalize.score_from_sparse(payload, bid)) is not None:
                fields["beli_score"] = round(s, 2)
        return fields
    except PermanentError:
        return {"count_fetched_on": today}  # gone or malformed: don't retry it every day


def enrich_scores(beli: Beli, con, today: str, summary: dict, budget: int = MAX_SCORE_PLACES) -> None:
    """Beli-wide average score (EAV field AVGBUSINESSSCORE) and how many people rated it, one place
    at a time: neither has a bulk form. A 429 gets one cool-down, then the step ends for the day
    and the queue carries over; a slow drip beats a run that spends its time being refused."""
    todo = score_queue(con, today)
    ok = scored = 0
    cooled = False
    for r in todo[:budget]:
        bid = r["business_id"]
        try:
            fields = fetch_score(beli, bid, today)
            scored += "beli_score" in fields
        except RateLimited:
            if cooled:
                summary["failures"]["scores"] = f"rate limited after {ok} places; resumes next run"
                print(f"  rate limited again after {ok} places; stopping for today")
                break
            print(f"  rate limited after {ok} places; cooling down {RATE_LIMIT_COOLDOWN}s")
            cooled = True
            time.sleep(RATE_LIMIT_COOLDOWN)
            continue
        except Exception as e:
            summary["failures"]["scores"] = repr(e)[:300]
            break
        db.set_fields(con, bid, **fields)
        ok += 1
        if ok % 25 == 0:
            con.commit()
            print(f"  {ok}/{min(budget, len(todo))} places")
    summary["sources"]["review_count"] = ok
    summary["sources"]["beli_score"] = scored
    summary["backlog"]["scores"] = max(0, len(todo) - ok)
    print(f"  {ok} places checked, {scored} with a Beli score; {summary['backlog']['scores']} still queued")
    con.commit()


def ingest_social(beli: Beli, con, today: str, summary: dict) -> None:
    """Friends' scores. The endpoint ignores its business id and returns everything, so one call
    covers all places. Only per-business aggregates are stored, never who scored what, and the
    raw response is deliberately not snapshotted (it names friends)."""
    any_id = con.execute("SELECT business_id FROM businesses LIMIT 1").fetchone()
    if any_id is None:
        return
    try:
        payload = beli.get(f"/api/scores/{beli.user_id}/{any_id[0]}/")
    except Exception as e:
        summary["failures"]["social"] = repr(e)[:300]
        return
    agg: dict[int, list[float]] = {}
    for r in normalize.records(payload):
        uid = r.get("user_id") or r.get("user")
        uid = uid.get("id") if isinstance(uid, dict) else uid
        bid = normalize.to_int(r.get("business_id") or r.get("business"))
        if bid is None or uid == beli.user_id:
            continue
        v = normalize.to_float(r.get("value") if r.get("value") is not None else r.get("score"))
        agg.setdefault(bid, [])
        if v is not None:
            agg[bid].append(v)
    con.execute("DELETE FROM social")
    for bid, vals in agg.items():
        con.execute(
            "INSERT INTO social VALUES (?, ?, ?, ?)",
            (bid, max(len(vals), 1), sum(vals) / len(vals) if vals else None, today))
    summary["sources"]["social"] = len(agg)
    con.commit()


def classify_sg(con) -> None:
    """in_sg from coordinates when known, else from the city/country text."""
    for r in con.execute("SELECT business_id, lat, lng, city, country FROM businesses").fetchall():
        if r["lat"] is not None and r["lng"] is not None:
            flag = int(normalize.in_singapore(r["lat"], r["lng"]))
        elif r["city"] or r["country"]:
            flag = int("singapore" in f"{r['city']} {r['country']}".lower())
        else:
            flag = None
        if r["business_id"] in EXCLUDED_BUSINESS_IDS:
            flag = 0
        db.set_fields(con, r["business_id"], in_sg=flag)
    con.execute("UPDATE businesses SET geo_source = 'beli' WHERE lat IS NOT NULL AND geo_source IS NULL")
    con.commit()


def run(beli: Beli, con, snap: Path, today: str, now: str, geocoder=geocode.lookup) -> dict:
    summary: dict = {"sources": {}, "failures": {}, "backlog": {}}
    steps = [
        ("lists", lambda: ingest_lists(beli, con, snap, today, now, summary)),
        ("classify", lambda: classify_sg(con)),
        ("geocode", lambda: geocode.enrich(con, today, summary, geocoder)),
        ("classify", lambda: classify_sg(con)),
        ("scores", lambda: enrich_scores(beli, con, today, summary)),
        ("social", lambda: ingest_social(beli, con, today, summary)),
    ]
    for name, step in steps:
        print(f"[{name}]")
        try:
            step()
        except Exception as e:  # a bug in one step must not lose the others' data
            traceback.print_exc()
            summary["failures"].setdefault(name, repr(e)[:300])
            con.rollback()
    summary["api_calls"] = beli.calls
    return summary
