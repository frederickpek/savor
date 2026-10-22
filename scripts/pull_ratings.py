"""Pull Beli ratings (score + rating count) for every Singapore place, until stopped.

    .venv/bin/python scripts/pull_ratings.py [--out data] [--interval 1.25] [--batch 10] [--quiet]

Same order as the daily job: Beli's top 200, this week's picks, your lists, catalogue places with
a price and cuisine, then the rest. Each place is two requests (rating count, then the Beli
average when anyone has rated it), at least `--interval` seconds apart. A 429 backs off up to 3
times (1, 3, 10 minutes); if Beli is still refusing, it waits 30 minutes and starts the next round,
so it never gives up on its own. Every `--batch` places are committed to savor.db, so Ctrl-C or
kill loses at most one batch, and the published JSON (places.json and this week's picks) is
rewritten every 250 places so the site shows ratings as they land. Never-fetched places come
first and then the stalest, so rerunning picks up where it stopped. Every request is printed unless
--quiet.
"""

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import beli as beli_mod  # noqa: E402
from pipeline import db, ingest  # noqa: E402
from pipeline.beli import RateLimited  # noqa: E402
from pipeline.run import SGT, _write, catalogue_json, connect_beli, load_env, week_of  # noqa: E402

BACKOFF = (60, 180, 600)  # seconds; three tries after a 429
LONG_PAUSE = 1800  # after all three, wait this long and start over
PUBLISH_EVERY = 250

stop = False


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def say(msg: str) -> None:
    print(f"{now()}  {msg}", flush=True)


def on_signal(*_):
    global stop
    if stop:  # second Ctrl-C: leave right away (the last batch may be lost)
        raise SystemExit(130)
    stop = True
    say("stop requested: finishing this place, then saving. Ctrl-C again to quit immediately.")


def log_request(method: str, path: str, params, status, ms: float) -> None:
    qs = "" if not params else "?" + "&".join(f"{k}={v}" for k, v in params.items())
    say(f"      {method} {path}{qs} → {status} ({ms:.0f} ms)")


def sleep_interruptibly(seconds: int, label: str) -> None:
    end = time.monotonic() + seconds
    next_note = 0.0
    while not stop and (left := end - time.monotonic()) > 0:
        if time.monotonic() >= next_note:
            say(f"  {label}: {left / 60:.1f} min left")
            next_note = time.monotonic() + 60
        time.sleep(min(1, left))


def publish(con, out: Path, today: str) -> None:
    """Refresh ratings in the site's JSON without reshuffling this week's picks."""
    api = out / "api"
    _write(api / "places.json", catalogue_json(con))
    path = api / "picks" / f"{week_of(today)}.json"
    if path.exists():
        picks = json.loads(path.read_text())
        fresh = {b["business_id"]: b for b in db.business_rows(con, "in_sg = 1")}
        for c in picks["contexts"].values():
            for p in c["picks"]:
                if (b := fresh.get(p["business_id"])):
                    p.update({k: b[k] for k in ("beli_score", "review_count")})
        _write(path, picks)
    say(f"  published {api / 'places.json'} and this week's picks")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--interval", type=float, default=1.25, help="seconds between requests (min 1.0)")
    ap.add_argument("--batch", type=int, default=10, help="places per save to savor.db")
    ap.add_argument("--quiet", action="store_true", help="don't print every request")
    ap.add_argument("--limit", type=int, help="stop after this many places (default: run until stopped)")
    ap.add_argument("--michelin", action="store_true", help="only MICHELIN-listed places")
    args = ap.parse_args()
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    say(f"loading credentials from .env and opening {args.out / 'savor.db'}")
    load_env(Path(".env"))
    today = datetime.now(timezone.utc).astimezone(SGT).date().isoformat()
    con = db.connect(args.out / "savor.db")

    say("logging in to Beli")
    beli = connect_beli()
    beli_mod.MIN_INTERVAL = max(1.0, args.interval)
    if not args.quiet:
        beli.log = log_request

    say("building the queue (no data yet first: top 200 → MICHELIN → this week's picks → your lists → priced → rest; then stalest)")
    todo = ingest.score_queue(con, today)
    if args.michelin:
        from pipeline.recommend import michelin
        guide = michelin()
        todo = [r for r in todo if r["business_id"] in guide]
    if args.limit:
        todo = todo[: args.limit]
    names = dict(con.execute("SELECT business_id, name FROM businesses").fetchall())
    ranks = dict(con.execute(
        "SELECT business_id, MIN(position) + 1 FROM sightings WHERE list_type = 'popular' GROUP BY business_id").fetchall())
    total = len(todo)
    fresh = sum(r["count_fetched_on"] is None for r in todo)
    say(f"{total:,} places queued: {fresh:,} with no data yet, then {total - fresh:,} already fetched (stalest first)")
    say(f"{args.interval:g}s between requests · saving every {args.batch} places · Ctrl-C to stop")

    done = scored = batch_scored = 0
    started = time.monotonic()
    i = 0
    while i < total and not stop:
        bid = todo[i]["business_id"]
        tag = f" (Top 200 #{ranks[bid]})" if bid in ranks else ""
        refresh = "" if todo[i]["count_fetched_on"] is None else f"  (refresh, last {todo[i]['count_fetched_on']})"
        say(f"[{i + 1:,}/{total:,}] {names.get(bid, bid)}{tag}  id={bid}{refresh}")
        fields = None
        for attempt in range(len(BACKOFF) + 1):
            try:
                fields = ingest.fetch_score(beli, bid, today)
                break
            except RateLimited:
                con.commit()
                if attempt < len(BACKOFF):
                    say(f"  429 rate limited; backoff {attempt + 1}/{len(BACKOFF)}, then retrying this place")
                    sleep_interruptibly(BACKOFF[attempt], f"backoff {attempt + 1}/{len(BACKOFF)}")
                else:
                    say(f"  still rate limited after {len(BACKOFF)} backoffs; long pause, then a fresh round")
                    sleep_interruptibly(LONG_PAUSE, "long pause")
                if stop:
                    break
            except Exception as e:  # network blip or token trouble: note it, move on
                say(f"  error: {e!r}; skipping this place for now")
                break
        else:
            continue  # exhausted backoffs: retry the same place in the next round
        if stop and fields is None:
            break

        if fields:
            db.set_fields(con, bid, **fields)
            if "beli_score" in fields:
                scored += 1
                batch_scored += 1
                say(f"  ✓ Beli {fields['beli_score']:.2f} from {fields.get('review_count', 0):,} ratings")
            elif fields.get("review_count") == 0:
                say("  ✓ nobody has rated it yet (no score to fetch)")
            else:
                say("  ✓ no score available")
        done += 1
        i += 1

        if done % args.batch == 0:
            con.commit()
            elapsed = max(1.0, time.monotonic() - started)
            rate = done / elapsed * 3600
            say(f"  saved batch: {args.batch} places, {batch_scored} with a score · total {done:,}/{total:,} "
                f"({scored:,} scored) · {rate:,.0f}/h · ~{(total - done) / max(rate, 1):.1f} h left")
            batch_scored = 0
        if done % PUBLISH_EVERY == 0:
            publish(con, args.out, today)

    say("saving and publishing before exit")
    con.commit()
    publish(con, args.out, today)
    say(f"done for now: {done:,} checked, {scored:,} with a Beli score; {total - done:,} still queued.")


if __name__ == "__main__":
    main()
