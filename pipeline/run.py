"""Daily job: ingest -> audit -> recommend -> write the static API.

    python -m pipeline.run --out data-store                 # full run (needs Beli credentials)
    python -m pipeline.run --out data-store --skip-ingest   # re-rank from the stored DB only
    python -m pipeline.run --out data-store --fixtures tests/fixtures   # offline, canned responses

Credentials come from BELI_EMAIL + BELI_PASSWORD (or BELI_REFRESH_TOKEN), read from the
environment or, locally, from .env (see .env.example). Output layout:

    savor.db                         current state (businesses, list_members, social) + a year of picks
    snapshots/<date>/<source>.json.gz  raw API responses, last SNAPSHOT_RETENTION_DAYS days
    reports/<date>.json              schema audit + missing-field report
    api/index.json                   latest week, contexts, run status
    api/picks/<week>.json            this week's picks per context (week = its Monday, SGT)
    api/places.json                  every open Singapore place (~18k), as compact columns + rows
"""

import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import audit, db, ingest, recommend
from .config import CLOSED_STATUSES, PICKS_RETENTION_DAYS, SNAPSHOT_RETENTION_DAYS

SGT = timezone(timedelta(hours=8))


def _write(path: Path, obj) -> None:
    """Atomic write: a crash mid-run never leaves a truncated JSON file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False, default=str))
    os.replace(tmp, path)


def _read(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def load_env(path: Path) -> None:
    """Fill os.environ from a KEY=value file for local runs. Variables already set (CI secrets) win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value:
            os.environ.setdefault(key.strip(), value)


def connect_beli():
    from .beli import Beli, BeliSession

    email, password = os.environ.get("BELI_EMAIL"), os.environ.get("BELI_PASSWORD")
    refresh = os.environ.get("BELI_REFRESH_TOKEN", "")
    if not (email and password) and not refresh:
        sys.exit("Set BELI_EMAIL and BELI_PASSWORD (or BELI_REFRESH_TOKEN).")
    return Beli(BeliSession(email=email, password=password, refresh_token=refresh))


def fingerprints(snap: Path) -> dict:
    import gzip

    out = {}
    for f in sorted(snap.glob("*.json.gz")):
        with gzip.open(f, "rt") as fh:
            out[f.name.removesuffix(".json.gz")] = audit.fingerprint(json.load(fh))
    return out


def prune(con, out: Path, today: str) -> None:
    """Keep the data branch a bounded size: raw snapshots and daily reports for a week, picks
    (the no-repeat cooldown reads them) for a year."""
    cutoff = (date.fromisoformat(today) - timedelta(days=SNAPSHOT_RETENTION_DAYS)).isoformat()
    for d in (out / "snapshots").glob("*"):
        if d.is_dir() and d.name < cutoff:
            shutil.rmtree(d)
    for f in (out / "reports").glob("????-??-??.json"):
        if f.stem < cutoff:
            f.unlink()
    year_ago = (date.fromisoformat(today) - timedelta(days=PICKS_RETENTION_DAYS)).isoformat()
    con.execute("DELETE FROM picks WHERE pick_date < ?", (year_ago,))
    for f in (out / "api" / "picks").glob("????-??-??.json"):
        if f.stem < year_ago:
            f.unlink()


# places.json is ~18k rows, so it ships as {cols, rows} (about 1.5 MB, 0.5 MB gzipped)
# instead of one object per place.
CATALOGUE_COLS = ("business_id", "name", "neighborhood", "lat", "lng", "price", "cuisines",
                  "beli_score", "review_count", "website", "popular_rank", "michelin")


def catalogue_json(con) -> dict:
    closed = ", ".join(f"'{s}'" for s in CLOSED_STATUSES)
    rows = db.business_rows(con, f"in_sg = 1 AND lat IS NOT NULL AND COALESCE(status, '') NOT IN ({closed})")
    # Beli's popular-in-Singapore order from the latest capture, 1-based (the SG Top 200 view).
    ranks = dict(con.execute(
        "SELECT business_id, position + 1 FROM list_members WHERE list_type = 'popular'").fetchall())
    guide = recommend.michelin()
    for b in rows:
        b["popular_rank"] = ranks.get(b["business_id"])
        b["michelin"] = guide.get(b["business_id"])
    rows.sort(key=lambda b: (b["beli_score"] is None, -(b["beli_score"] or 0), b["name"]))
    return {"cols": list(CATALOGUE_COLS), "rows": [
        [round(b[c], 5) if c in ("lat", "lng") else b[c] for c in CATALOGUE_COLS] for b in rows]}


def place_json(b: dict) -> dict:
    keys = ("business_id", "name", "address", "neighborhood", "lat", "lng", "price", "cuisines", "beli_score", "review_count", "website")
    return {k: b.get(k) for k in keys}


def week_of(day: str) -> str:
    """Monday of the SGT week containing `day`: picks are generated once per week, keyed by it."""
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def build_picks(con, prefs: dict, today: str) -> tuple[dict, dict[str, dict[int, float]]]:
    """(public picks JSON, {context: {business_id: internal score}}) for the week containing today."""
    week = week_of(today)
    cands, ranked = recommend.candidates(con, week)
    recent = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    # Everything is "new" on the first day of history, so only count places first seen after it.
    first_day = con.execute("SELECT MIN(first_seen) FROM businesses").fetchone()[0] or today
    recent = max(recent, (date.fromisoformat(first_day) + timedelta(days=1)).isoformat())
    contexts = {"default": None, **{c: c for c in prefs.get("contexts", {})}}
    out, scores = {}, {}
    for name, ctx in contexts.items():
        p = recommend.resolve_prefs(prefs, ctx)
        # Seeded by the week, so re-running mid-week gives the same list unless data or prefs changed.
        scored = recommend.rank(cands, ranked, p, week, recent)
        picks = recommend.mix(scored, p)
        scores[name] = {b["business_id"]: b["score"] for b in picks}
        out[name] = {
            "prefs": {k: v for k, v in p.items() if k != "contexts"},
            "picks": [{
                # Internal score breakdown stays in savor.db; the public JSON carries reasons only.
                **place_json(b), "km": b.get("km"), "reasons": b["reasons"],
                "bookmarked": bool(b["bookmarked_on"]), "ranked_score": b["ranked_score"], "pool": b["pool"],
                "popular_rank": b["popular_rank"], "michelin": b["michelin"],
            } for b in picks],
        }
    return {"date": week, "generated_on": today, "candidates": len(cands), "contexts": out}, scores


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--prefs", type=Path, default=Path("prefs.json"))
    ap.add_argument("--today", help="YYYY-MM-DD (SGT); defaults to today")
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument("--keep-picks", action="store_true",
                    help="republish without rebuilding this week's picks (code/UI pushes)")
    ap.add_argument("--fixtures", type=Path, help="serve Beli calls from canned JSON (offline)")
    ap.add_argument("--status-file", type=Path)
    ap.add_argument("--env-file", type=Path, default=Path(".env"), help="local credentials (default: .env)")
    args = ap.parse_args()
    load_env(args.env_file)

    now = datetime.now(timezone.utc)
    today = args.today or now.astimezone(SGT).date().isoformat()
    out: Path = args.out
    con = db.connect(out / "savor.db")
    prefs = json.loads(args.prefs.read_text())

    summary = {"sources": {}, "failures": {}, "backlog": {}, "api_calls": 0}
    if not args.skip_ingest:
        geocoder = None
        if args.fixtures:
            from tests.fake_beli import FakeBeli
            beli = FakeBeli(args.fixtures)
            geocoder = beli.geocode
        else:
            beli = connect_beli()
        con.execute("INSERT OR REPLACE INTO runs (run_on, started_at) VALUES (?, ?)", (today, now.isoformat()))
        snap = out / "snapshots" / today
        summary = ingest.run(beli, con, snap, today, now.isoformat(), *([geocoder] if geocoder else []))

        previous = _read(out / "reports" / "fingerprints.json", {})
        schema = audit.schema_report(fingerprints(snap), previous)
        missing = audit.missing_fields(con)
        _write(out / "reports" / f"{today}.json", {"date": today, "summary": summary, "schema": schema, "missing": missing})
        # Only advance the baseline from sources that answered, so one bad day isn't the new normal.
        _write(out / "reports" / "fingerprints.json",
               {**previous, **{s: e["fingerprint"] for s, e in schema.items() if "fingerprint" in e}})
        if args.status_file:
            args.status_file.with_suffix(".md").write_text(audit.markdown(schema, missing, summary))
        prune(con, out, today)

    core_ok = all(s not in summary["failures"] for s in ingest.CORE_SOURCES)
    status = "skipped" if args.skip_ingest else ("ok" if not summary["failures"] else "partial" if core_ok else "failed")

    # Weekly: a new list on the first run of each week, or when prefs.json changed (a
    # --skip-ingest push). Daily ingests in between only fill in scores for next week.
    api = out / "api"
    week = week_of(today)
    existing = _read(api / "picks" / f"{week}.json", None)
    regenerate = existing is None or ((args.skip_ingest or args.fixtures is not None) and not args.keep_picks)
    picks, scores = build_picks(con, prefs, today)
    if regenerate:
        # Every context's picks, so the cooldown keeps a place out of all of them next weeks.
        con.execute("DELETE FROM picks WHERE pick_date = ?", (week,))
        for ctx, c in picks["contexts"].items():
            for i, b in enumerate(c["picks"]):
                con.execute("INSERT INTO picks (pick_date, context, rank, business_id, score, reasons) VALUES (?, ?, ?, ?, ?, ?)",
                            (week, ctx, i + 1, b["business_id"], scores[ctx][b["business_id"]], json.dumps(b["reasons"])))
    else:
        picks = existing
        # Ratings fill in daily; refresh them on this week's picks without reshuffling.
        fresh = {b["business_id"]: b for b in db.business_rows(con, "in_sg = 1")}
        for c in picks["contexts"].values():
            for p in c["picks"]:
                if (b := fresh.get(p["business_id"])):
                    p.update({k: b[k] for k in ("beli_score", "review_count", "price", "cuisines")})
    if not args.skip_ingest:
        con.execute("UPDATE runs SET finished_at = ?, status = ?, api_calls = ?, summary = ? WHERE run_on = ?",
                    (datetime.now(timezone.utc).isoformat(), status, summary["api_calls"], json.dumps(summary), today))
    con.commit()

    _write(api / "picks" / f"{week}.json", picks)
    _write(api / "places.json", catalogue_json(con))
    (api / "grid.json").unlink(missing_ok=True)
    dates = sorted(p.stem for p in (api / "picks").glob("*.json"))
    _write(api / "index.json", {
        "latest": week, "dates": dates[-26:], "contexts": list(picks["contexts"]),
        "generatedAt": now.isoformat(), "status": status, "candidates": picks["candidates"],
    })

    print(f"\n{today}: {status}; {picks['candidates']} candidates; week of {week} "
          f"({'new picks' if regenerate else 'kept this week' + chr(39) + 's picks'})")
    for i, b in enumerate(picks["contexts"]["default"]["picks"], 1):
        tag = {"popular": "[popular] ", "discover": "[discover]"}.get(b.get("pool"), "")
        print(f"  {i:>2}. {tag} {b['name']} — {'; '.join(b['reasons'])}")
    if args.status_file:
        _write(args.status_file, {"date": today, "status": status, **summary})
    if status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
