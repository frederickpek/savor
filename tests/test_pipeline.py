import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import audit, db, normalize, recommend

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def run_pipeline(out: Path, today: str, *extra: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pipeline.run", "--out", str(out), "--fixtures", str(FIXTURES),
         "--today", today, *extra],
        cwd=ROOT, check=True, capture_output=True)


@pytest.fixture(scope="module")
def out(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("data")
    run_pipeline(out, "2026-10-14")
    run_pipeline(out, "2026-10-15")
    return out


def test_records_handles_every_envelope():
    b = {"id": 1, "name": "x"}
    assert normalize.records([b]) == [b]
    assert normalize.records({"results": [b], "count": 1}) == [b]
    assert normalize.records({"Restaurants": [b], "Bars": [b]}) == [b, b]
    assert normalize.records({"results": [1, 2], "business_hash": {"1": b}}) == [b]
    assert normalize.records(5990) == []


def test_business_normalizes_drifted_fields():
    row = normalize.business({"id": "7", "name": " Odette ", "price_key": "$$$$", "lat": "1.29",
                              "cuisines": [{"name": "French"}, "French"], "avg_score": 9.1})
    assert row["business_id"] == 7 and row["name"] == "Odette"
    assert row["price"] == 4 and row["lat"] == 1.29 and row["cuisines"] == ["French"]
    assert row["beli_score"] == 9.1 and row["review_count"] is None


def test_upsert_never_erases_known_values(tmp_path):
    con = db.connect(tmp_path / "t.db")
    db.upsert_business(con, {"business_id": 1, "name": "A", "lat": 1.3, "cuisines": ["Thai"]}, "2026-10-14")
    db.upsert_business(con, {"business_id": 1, "name": "A", "lat": None, "cuisines": None, "price": 2}, "2026-10-15")
    (r,) = db.business_rows(con)
    assert r["lat"] == 1.3 and r["cuisines"] == ["Thai"] and r["price"] == 2
    assert r["first_seen"] == "2026-10-14" and r["last_seen"] == "2026-10-15"


def test_singapore_only(out):
    con = db.connect(out / "savor.db")
    outside = con.execute("SELECT name FROM businesses WHERE in_sg = 0").fetchall()
    assert [r[0] for r in outside] == ["Joe's Pizza"]
    places = json.loads((out / "api" / "places.json").read_text())
    lat, lng = places["cols"].index("lat"), places["cols"].index("lng")
    assert places["rows"] and all(normalize.in_singapore(r[lat], r[lng]) for r in places["rows"])
    rank = places["cols"].index("popular_rank")
    assert sorted(r[rank] for r in places["rows"] if r[rank]) == [1, 2, 3]  # popular fixture: 3 places
    assert not (out / "api" / "grid.json").exists()


def test_geocode_fallback_fills_missing_coords(out):
    con = db.connect(out / "savor.db")
    r = con.execute("SELECT lat, geo_source FROM businesses WHERE name = 'Kok Sen'").fetchone()
    assert r["lat"] == pytest.approx(1.2808) and r["geo_source"] == "onemap"


def test_lists_keep_only_current_contents(out):
    con = db.connect(out / "savor.db")
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'sightings'").fetchone()
    # Two days of runs, still one row per place per list: membership is replaced, not appended.
    dupes = con.execute("SELECT COUNT(*) - COUNT(DISTINCT list_type || business_id) FROM list_members").fetchone()[0]
    assert dupes == 0
    r = con.execute("SELECT user_score, since, captured_on FROM list_members WHERE list_type='ranked' AND business_id=1").fetchone()
    assert tuple(r) == (9.6, "2026-10-14", "2026-10-15")


def test_replace_list_tracks_changes_and_since(tmp_path):
    con = db.connect(tmp_path / "t.db")
    assert db.replace_list(con, "popular", "2026-10-01", [(1, 0, None), (2, 1, None)]) == (2, 0)
    assert db.replace_list(con, "popular", "2026-10-02", [(2, 0, None), (3, 1, None)]) == (1, 1)
    rows = con.execute("SELECT business_id, position, since FROM list_members ORDER BY 1").fetchall()
    assert [tuple(r) for r in rows] == [(2, 0, "2026-10-01"), (3, 1, "2026-10-02")]


def test_old_sightings_migrate_to_latest_capture(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE businesses (business_id INTEGER PRIMARY KEY, name TEXT NOT NULL, in_sg INTEGER,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
        CREATE TABLE sightings (captured_on TEXT NOT NULL, list_type TEXT NOT NULL, business_id INTEGER NOT NULL,
            position INTEGER, user_score REAL, captured_at TEXT NOT NULL, PRIMARY KEY (captured_on, list_type, business_id));
        INSERT INTO businesses VALUES (1, 'A', 1, '2026-10-01', '2026-10-02'), (2, 'B', 1, '2026-10-01', '2026-10-02');
        INSERT INTO sightings VALUES
            ('2026-10-01', 'popular', 1, 4, NULL, 't'), ('2026-10-02', 'popular', 2, 0, NULL, 't'),
            ('2026-10-01', 'bookmarks', 2, 0, NULL, 't'), ('2026-10-02', 'bookmarks', 2, 0, NULL, 't'),
            ('2026-10-01', 'filter_trending', 1, 0, NULL, 't');
    """)
    old.commit()
    old.close()
    con = db.connect(path)
    rows = con.execute("SELECT list_type, business_id, position, since, captured_on FROM list_members ORDER BY 1, 2").fetchall()
    # Place 1 dropped off popular on the 2nd, so only its latest capture survives; bookmarks keep the first date.
    assert [tuple(r) for r in rows] == [("bookmarks", 2, 0, "2026-10-01", "2026-10-02"),
                                        ("catalogue", 1, 0, "2026-10-01", "2026-10-01"),
                                        ("popular", 2, 0, "2026-10-02", "2026-10-02")]
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'sightings'").fetchone()


def test_picks_come_from_catalogue_and_explain_themselves(out):
    # 2026-10-14/15 are a Wednesday and Thursday: both runs land in the week of Monday 12 Oct.
    picks = json.loads((out / "api" / "picks" / "2026-10-12.json").read_text())
    assert picks["date"] == "2026-10-12" and picks["generated_on"] == "2026-10-15"
    default = picks["contexts"]["default"]["picks"]
    assert 5 <= len(default) <= 10
    assert all(p["reasons"] for p in default)
    assert set(picks["contexts"]) == {"default", "weeknight", "date-night", "weekend-explore"}
    names = {p["name"] for c in picks["contexts"].values() for p in c["picks"]}
    # Chains over max_outlets, cuisines_avoid (Fast Food) and closed places never make it.
    assert not any(n.startswith(("Ya Kun", "McDonald's", "Gone Bistro")) for n in names)


def test_own_lists_tag_but_never_filter_or_boost(tmp_path):
    con = db.connect(tmp_path / "t.db")
    for bid, name in ((1, "Mine"), (2, "Theirs")):
        db.upsert_business(con, {"business_id": bid, "name": name, "lat": 1.29, "lng": 103.85, "price": 2,
                                 "cuisines": ["Thai"], "beli_score": 8.0, "review_count": 100}, "2026-10-14")
    con.execute("UPDATE businesses SET in_sg = 1")
    db.replace_list(con, "ranked", "2026-10-14", [(1, 0, 9.0)])
    db.replace_list(con, "bookmarks", "2026-10-14", [(1, 0, None)])
    db.replace_list(con, "catalogue", "2026-10-14", [(2, 0, None)])
    cands, ranked = recommend.candidates(con)
    by = {b["business_id"]: b for b in cands}
    assert set(by) == {1, 2} and [r["business_id"] for r in ranked] == [1]
    assert by[1]["ranked_score"] == 9.0 and by[1]["bookmarked_on"] == "2026-10-14"
    scored = {b["business_id"]: b for b in recommend.rank(cands, ranked, {"budget": 2}, "2026-10-14", "2026-10-15")}
    assert scored[1]["components"] == scored[2]["components"]


def test_chain_grouping():
    counts = recommend.outlet_counts(["Ya Kun Kaya Toast @ Bugis", "Ya Kun Kaya Toast (Orchard)",
                                      "McDonald's", "McDonald's - VivoCity", "McDonald's Ang Mo Kio", "Odette",
                                      "SUSHI TEI", "SUSHI TEI", "Sushi Tei (Tampines One)"])
    assert counts["Ya Kun Kaya Toast @ Bugis"] == 2
    assert counts["McDonald's Ang Mo Kio"] == 3
    assert counts["Odette"] == 1
    assert counts["SUSHI TEI"] == 3


def test_scores_stop_for_the_day_on_rate_limit(tmp_path, monkeypatch):
    from pipeline import ingest
    from pipeline.beli import RateLimited

    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    con = db.connect(tmp_path / "t.db")
    for bid in range(1, 6):
        db.upsert_business(con, {"business_id": bid, "name": f"P{bid}"}, "2026-10-14")
    con.execute("UPDATE businesses SET in_sg = 1")

    class Limited:
        calls = 0
        def get(self, path, params=None, patient=True):
            self.calls += 1
            if self.calls > 2:
                raise RateLimited("429")
            return 0
    summary = {"sources": {}, "failures": {}, "backlog": {}}
    ingest.enrich_scores(Limited(), con, "2026-10-14", summary)
    assert summary["sources"]["review_count"] == 2
    assert summary["backlog"]["scores"] == 3 and "scores" in summary["failures"]


def test_report_flags_missing_fields(out):
    report = json.loads((out / "reports" / "2026-10-15.json").read_text())
    fields = report["missing"]["all"]["fields"]
    assert fields["name"]["missing"] == 0
    assert fields["address"]["missing"] > 0
    assert report["schema"]["ranked"]["drifted"] is False


def test_schema_drift_detected():
    before = audit.fingerprint({"results": [{"id": 1, "lat": 1.3, "name": "a"}]})
    after = audit.fingerprint({"results": [{"id": "1", "name": "a"}]})
    d = audit.diff(before, after)
    assert d["removed"] == ["$.results[].lat"]
    assert "$.results[].id" in d["type_changed"]


def test_jitter_rotates_by_day_but_is_stable():
    assert recommend.jitter("2026-10-15", 1) == recommend.jitter("2026-10-15", 1)
    assert recommend.jitter("2026-10-15", 1) != recommend.jitter("2026-10-16", 1)


def test_select_spreads_cuisines():
    base = {"neighborhood": None, "reasons": ["x"]}
    scored = [{**base, "business_id": i, "cuisines": [c], "score": s}
              for i, (c, s) in enumerate([("Thai", 0.9), ("Thai", 0.89), ("French", 0.85)])]
    assert [p["business_id"] for p in recommend.select(scored, 2)] == [0, 2]


def test_env_file_fills_only_unset_vars(tmp_path, monkeypatch):
    from pipeline.run import load_env

    env = tmp_path / ".env"
    env.write_text('# local\nBELI_EMAIL="me@example.com"\nexport BELI_PASSWORD=s3cr=t\nONEMAP_TOKEN=\nBELI_REFRESH_TOKEN=from-file\n')
    for k in ("BELI_EMAIL", "BELI_PASSWORD", "ONEMAP_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BELI_REFRESH_TOKEN", "from-ci")
    load_env(env)
    import os
    assert os.environ["BELI_EMAIL"] == "me@example.com"
    assert os.environ["BELI_PASSWORD"] == "s3cr=t"
    assert "ONEMAP_TOKEN" not in os.environ
    assert os.environ["BELI_REFRESH_TOKEN"] == "from-ci"
    load_env(tmp_path / "missing")


def test_week_of_is_monday():
    from pipeline.run import week_of

    assert week_of("2026-10-12") == "2026-10-12"
    assert week_of("2026-10-18") == "2026-10-12"
    assert week_of("2026-10-19") == "2026-10-19"


def test_mix_takes_popular_then_good_discoveries():
    def place(i, popular, score, n=100):
        return {"business_id": i, "name": f"P{i}", "cuisines": [f"C{i}"], "neighborhood": None,
                "popular": popular, "beli_score": score, "review_count": n, "score": 1 - i / 100}
    scored = [place(1, True, 9), place(2, True, 8), place(3, True, 8),
              place(4, False, 6.0), place(5, False, 8.5), place(6, False, 8.2, n=3), place(7, False, 7.9)]
    prefs = {"picks": 4, "popular_picks": 2, "min_beli_score": 7.5, "min_ratings": 20}
    picked = recommend.mix(scored, prefs)
    assert [p["business_id"] for p in picked] == [1, 2, 5, 7]
    assert [p["pool"] for p in picked] == ["popular", "popular", "discover", "discover"]
    # Not enough good discoveries yet: other catalogue places fill in before more popular ones.
    six = recommend.mix(scored, {**prefs, "picks": 6})
    assert len(six) == 6 and sum(p["pool"] == "popular" for p in six) == 2


def test_last_weeks_picks_sit_out(tmp_path):
    con = db.connect(tmp_path / "t.db")
    for bid in range(1, 31):
        db.upsert_business(con, {"business_id": bid, "name": f"P{bid}", "lat": 1.29, "lng": 103.85,
                                 "price": 2, "cuisines": [f"C{bid}"]}, "2026-10-01")
    con.execute("UPDATE businesses SET in_sg = 1")
    prefs = {"picks": 10, "budget": 2}
    weeks = ["2026-10-05", "2026-10-12", "2026-10-19"]
    seen = []
    for week in weeks:
        cands, ranked = recommend.candidates(con, week)
        picked = recommend.mix(recommend.rank(cands, ranked, prefs, week, "9999"), prefs)
        for i, b in enumerate(picked):
            con.execute("INSERT INTO picks (pick_date, context, rank, business_id, score, reasons) VALUES (?, 'default', ?, ?, 0, '[]')",
                        (week, i + 1, b["business_id"]))
        seen.append({b["business_id"] for b in picked})
    # 30 places, 10 a week, 6-week cooldown: three weeks of entirely different lists.
    assert not (seen[0] & seen[1]) and not (seen[1] & seen[2]) and not (seen[0] & seen[2])


def test_michelin_snapshot_links_to_beli_ids():
    doc = json.loads((ROOT / "pipeline" / "data" / "michelin.json").read_text())
    awards = [r["award"] for r in doc["restaurants"]]
    assert {a for a in awards} <= {1, 2, 3, "bib"}
    assert awards.count(3) == 3 and "bib" in awards
    ids = [r["business_id"] for r in doc["restaurants"] if r["business_id"]]
    assert len(ids) == len(set(ids))  # one guide entry per Beli place
    assert recommend.michelin_label(2) == "2 MICHELIN Stars" and recommend.michelin_label("bib") == "MICHELIN Bib Gourmand"


def test_score_queue_does_unfetched_first_then_stalest(tmp_path):
    from pipeline import ingest

    con = db.connect(tmp_path / "t.db")
    for bid in (1, 2, 3, 4):
        db.upsert_business(con, {"business_id": bid, "name": f"P{bid}"}, "2026-10-01")
    con.execute("UPDATE businesses SET in_sg = 1")
    db.set_fields(con, 1, count_fetched_on="2026-10-20")
    db.set_fields(con, 2, count_fetched_on="2026-09-01")
    db.replace_list(con, "popular", "2026-10-01", [(4, 0, None)])
    order = [r["business_id"] for r in ingest.score_queue(con, "2026-10-22")]
    # Never fetched (popular #1 ahead of the rest), then oldest fetch first; no expiry window.
    assert order == [4, 3, 2, 1]
