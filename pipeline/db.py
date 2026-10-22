"""SQLite store: one file (savor.db) that lives on the `data` branch next to the snapshots."""

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    business_id       INTEGER PRIMARY KEY,
    place_id          TEXT,
    name              TEXT NOT NULL,
    address           TEXT,
    neighborhood      TEXT,
    city              TEXT,
    country           TEXT,
    lat               REAL,
    lng               REAL,
    price             INTEGER,          -- 1..4
    cuisines          TEXT,             -- JSON array
    beli_score        REAL,             -- 0..10, Beli-wide average when available
    review_count      INTEGER,
    website           TEXT,
    status            TEXT,
    in_sg             INTEGER,          -- 1 inside Singapore, 0 outside, NULL unknown
    geo_source        TEXT,             -- 'beli' | 'onemap'
    first_seen        TEXT NOT NULL,    -- YYYY-MM-DD (SGT)
    last_seen         TEXT NOT NULL,
    detail_fetched_on TEXT,
    count_fetched_on  TEXT,
    geo_fetched_on    TEXT
);

-- What each list contains right now: replaced on every successful fetch, so it stays one row per
-- place per list however many days pass. A failed fetch leaves the last good copy in place.
CREATE TABLE IF NOT EXISTS list_members (
    list_type   TEXT NOT NULL,          -- ranked | bookmarks | popular | catalogue
    business_id INTEGER NOT NULL,
    position    INTEGER,                -- 0-based order in the list
    user_score  REAL,                   -- your score, ranked list only
    since       TEXT NOT NULL,          -- YYYY-MM-DD it has been on the list since
    captured_on TEXT NOT NULL,          -- YYYY-MM-DD of the fetch this row came from
    PRIMARY KEY (list_type, business_id)
);

-- Friend activity, aggregated per business so no friend identities are stored.
CREATE TABLE IF NOT EXISTS social (
    business_id  INTEGER PRIMARY KEY,
    friend_count INTEGER NOT NULL,
    friend_avg   REAL,
    updated_on   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_on      TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT,                   -- ok | partial | failed
    api_calls   INTEGER,
    summary     TEXT                    -- JSON: per-source counts and failures
);

CREATE TABLE IF NOT EXISTS picks (
    pick_date   TEXT NOT NULL,          -- Monday of the week
    context     TEXT NOT NULL DEFAULT 'default',
    rank        INTEGER NOT NULL,
    business_id INTEGER NOT NULL,
    score       REAL NOT NULL,
    reasons     TEXT NOT NULL,          -- JSON array of strings
    PRIMARY KEY (pick_date, context, rank)
);

CREATE INDEX IF NOT EXISTS picks_by_business ON picks (business_id, pick_date);
"""

JSON_COLS = ("cuisines",)
UPSERT_COLS = (
    "place_id", "name", "address", "neighborhood", "city", "country", "lat", "lng", "price",
    "cuisines", "beli_score", "review_count", "website", "status",
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    if "tags" in {r[1] for r in con.execute("PRAGMA table_info(businesses)")}:
        con.execute("ALTER TABLE businesses DROP COLUMN tags")  # vibes were removed
    if "context" not in {r[1] for r in con.execute("PRAGMA table_info(picks)")}:
        # Older DBs only kept the default context's picks; rebuild with a context column.
        con.executescript("""
            ALTER TABLE picks RENAME TO picks_old;
            CREATE TABLE picks (pick_date TEXT NOT NULL, context TEXT NOT NULL DEFAULT 'default',
                rank INTEGER NOT NULL, business_id INTEGER NOT NULL, score REAL NOT NULL,
                reasons TEXT NOT NULL, PRIMARY KEY (pick_date, context, rank));
            INSERT INTO picks (pick_date, rank, business_id, score, reasons)
                SELECT pick_date, rank, business_id, score, reasons FROM picks_old;
            DROP TABLE picks_old;
            CREATE INDEX IF NOT EXISTS picks_by_business ON picks (business_id, pick_date);
        """)
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'sightings'").fetchone():
        migrate_sightings(con)
    con.commit()
    return con


def migrate_sightings(con: sqlite3.Connection) -> None:
    """Older DBs kept every list's contents for every day (`sightings`, ~18k rows a day). Keep each
    list's latest capture as `list_members`, drop the history, and shrink the file."""
    # Even older runs called the catalogue `filter_trending` and kept Beli's worldwide trending list.
    con.execute("UPDATE OR REPLACE sightings SET list_type = 'catalogue' WHERE list_type = 'filter_trending'")
    con.execute("DELETE FROM sightings WHERE list_type = 'trending'")
    con.execute("""DELETE FROM businesses WHERE in_sg IS NOT 1
                   AND business_id NOT IN (SELECT business_id FROM sightings)""")
    con.execute(
        """INSERT OR REPLACE INTO list_members
           SELECT s.list_type, s.business_id, s.position, s.user_score,
                  (SELECT MIN(f.captured_on) FROM sightings f
                   WHERE f.list_type = s.list_type AND f.business_id = s.business_id),
                  s.captured_on
           FROM sightings s
           WHERE s.captured_on = (SELECT MAX(captured_on) FROM sightings l WHERE l.list_type = s.list_type)""")
    con.execute("DROP TABLE sightings")
    con.commit()
    con.execute("VACUUM")


def upsert_business(con: sqlite3.Connection, row: dict, today: str) -> None:
    """Insert or merge. A NULL in `row` never overwrites a known value: list endpoints return
    thinner objects than the detail endpoint, and a sparse response must not erase good data."""
    vals = {c: row.get(c) for c in UPSERT_COLS}
    for c in JSON_COLS:
        if vals[c] is not None:
            vals[c] = json.dumps(vals[c])
    cols = ", ".join(UPSERT_COLS)
    params = ", ".join(f":{c}" for c in UPSERT_COLS)
    merge = ", ".join(f"{c} = COALESCE(excluded.{c}, businesses.{c})" for c in UPSERT_COLS)
    con.execute(
        f"""INSERT INTO businesses (business_id, {cols}, first_seen, last_seen)
            VALUES (:business_id, {params}, :today, :today)
            ON CONFLICT (business_id) DO UPDATE SET {merge}, last_seen = excluded.last_seen""",
        {**vals, "business_id": row["business_id"], "today": today},
    )


def set_fields(con: sqlite3.Connection, business_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    con.execute(f"UPDATE businesses SET {sets} WHERE business_id = :id", {**fields, "id": business_id})


def replace_list(con: sqlite3.Connection, list_type: str, today: str,
                 members: list[tuple[int, int, float | None]]) -> tuple[int, int]:
    """Make `members` [(business_id, position, user_score)] the list's contents, keeping each
    returning place's `since`. Returns (added, removed) versus the previous contents."""
    before = dict(con.execute("SELECT business_id, since FROM list_members WHERE list_type = ?", (list_type,)).fetchall())
    con.execute("DELETE FROM list_members WHERE list_type = ?", (list_type,))
    con.executemany(
        "INSERT OR IGNORE INTO list_members VALUES (?, ?, ?, ?, ?, ?)",
        [(list_type, bid, pos, score, before.get(bid, today), today) for bid, pos, score in members])
    now = {bid for bid, _, _ in members}
    return len(now - before.keys()), len(before.keys() - now)


def business_rows(con: sqlite3.Connection, where: str = "1", params: tuple = ()) -> list[dict]:
    out = []
    for r in con.execute(f"SELECT * FROM businesses WHERE {where}", params):
        d = dict(r)
        for c in JSON_COLS:
            d[c] = json.loads(d[c]) if d[c] else []
        out.append(d)
    return out
