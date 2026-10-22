"""Sync local data/ with the `data` branch, without losing ratings on either side.

    .venv/bin/python scripts/data_branch.py pull   # data branch -> data/
    .venv/bin/python scripts/data_branch.py push   # data/ -> data branch (the site updates on the next scheduled run)
    .venv/bin/python scripts/data_branch.py push --dry-run

Both directions merge rather than overwrite: start from the data branch (it has the scheduled
job's latest catalogue, lists and picks), then copy in every rating your local savor.db fetched
more recently. So a scheduled run that lands while you are pulling ratings locally loses nothing,
and neither do you. The published JSON (places.json, this week's picks) is rebuilt from the merged
database. `push` writes the branch as a single commit like the workflow does, guarded with
--force-with-lease so it never clobbers a run that pushed in the meantime (it just retries).
Stop pull_ratings.py first; both commands refuse while savor.db is in use.
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pipeline import db  # noqa: E402
from pipeline.run import SGT  # noqa: E402
from pull_ratings import publish  # noqa: E402

BRANCH = "data"
RATING_FIELDS = ("review_count", "beli_score", "count_fetched_on")


def git(*args: str, **kw) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, text=True, capture_output=True, **kw).stdout.strip()


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def say(msg: str) -> None:
    print(msg, flush=True)


def ensure_idle(path: Path) -> None:
    """Refuse to touch savor.db while pull_ratings.py (or anything else) is writing to it."""
    if not path.exists():
        return
    con = sqlite3.connect(path, timeout=0)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.rollback()
    except sqlite3.OperationalError:
        sys.exit(f"{path} is in use. Stop pull_ratings.py (Ctrl-C) and run this again.")
    finally:
        con.close()


def fetch_remote(into: Path) -> str:
    """Download the data branch into `into`; returns its commit sha."""
    say(f"fetching origin/{BRANCH}")
    git("fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
    sha = git("rev-parse", f"origin/{BRANCH}")
    archive = into.parent / "data.tar"
    with open(archive, "wb") as f:
        subprocess.run(["git", "archive", "--format=tar", sha], cwd=ROOT, check=True, stdout=f)
    with tarfile.open(archive) as t:
        t.extractall(into, filter="data")
    archive.unlink()
    say(f"  {git('log', '-1', '--format=%h  %s  (%cr)', sha)}")
    return sha


def coverage(path: Path) -> tuple[int, int]:
    con = sqlite3.connect(path)
    try:
        return con.execute(
            "SELECT SUM(count_fetched_on IS NOT NULL), SUM(beli_score IS NOT NULL) FROM businesses WHERE in_sg = 1"
        ).fetchone()
    finally:
        con.close()


def merge(base: Path, local: Path) -> int:
    """Copy into `base` every rating that `local` fetched more recently. Returns how many places."""
    if not local.exists():
        return 0
    con = sqlite3.connect(base)
    try:
        con.execute("ATTACH ? AS l", (str(local),))
        sets = ", ".join(f"{f} = lb.{f}" for f in RATING_FIELDS)
        cur = con.execute(
            f"""UPDATE businesses SET {sets}
                FROM l.businesses lb
                WHERE lb.business_id = businesses.business_id
                  AND lb.count_fetched_on IS NOT NULL
                  AND (businesses.count_fetched_on IS NULL OR lb.count_fetched_on > businesses.count_fetched_on)""")
        n = cur.rowcount
        con.commit()
        return n
    finally:
        con.close()


def build(tmp: Path, local_db: Path) -> tuple[Path, str]:
    """Remote tree with local ratings merged in and the JSON rebuilt. Returns (tree, remote sha)."""
    tree = tmp / "tree"
    tree.mkdir()
    sha = fetch_remote(tree)
    remote_fetched, remote_scored = coverage(tree / "savor.db")
    n = merge(tree / "savor.db", local_db)
    fetched, scored = coverage(tree / "savor.db")
    say(f"  data branch: {remote_fetched:,} fetched, {remote_scored:,} scored")
    say(f"  merged {n:,} newer ratings from {rel(local_db)} → {fetched:,} fetched, {scored:,} scored")
    today = datetime.now(timezone.utc).astimezone(SGT).date().isoformat()
    con = db.connect(tree / "savor.db")
    try:
        publish(con, tree, today)
    finally:
        con.close()
    return tree, sha


def install(tree: Path, out: Path) -> None:
    """Replace `out` with `tree`, keeping a backup of the old savor.db outside the repo."""
    if (out / "savor.db").exists():
        backup = Path(tempfile.gettempdir()) / f"savor-{datetime.now():%Y%m%d-%H%M%S}.db"
        shutil.copy2(out / "savor.db", backup)
        say(f"  backed up old {rel(out)}/savor.db to {backup}")
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(tree, out)


def cmd_pull(out: Path) -> None:
    ensure_idle(out / "savor.db")
    with tempfile.TemporaryDirectory() as tmp:
        tree, _ = build(Path(tmp), out / "savor.db")
        install(tree, out)
    say(f"done: {rel(out)}/ is up to date with the data branch")


def commit_tree(tree: Path, message: str) -> str:
    """A parentless commit of `tree`, made with a throwaway index so the working tree is untouched."""
    with tempfile.TemporaryDirectory() as idx:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(idx) / "index")}
        run = lambda *a: subprocess.run(["git", f"--work-tree={tree}", *a], cwd=ROOT, check=True,
                                        text=True, capture_output=True, env=env).stdout.strip()
        run("add", "-A", "--force", ".")
        return run("commit-tree", run("write-tree"), "-m", message)


def cmd_push(out: Path, dry_run: bool = False) -> None:
    if not (out / "savor.db").exists():
        sys.exit(f"nothing to push: {rel(out)}/savor.db doesn't exist (run pull first)")
    ensure_idle(out / "savor.db")
    for attempt in range(3):
        with tempfile.TemporaryDirectory() as tmp:
            tree, remote_sha = build(Path(tmp), out / "savor.db")
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            commit = commit_tree(tree, f"snapshot {stamp} (local ratings sync)")
            if dry_run:
                say(f"dry run: would push {commit[:7]} to origin/{BRANCH}; nothing changed")
                return
            say(f"pushing {commit[:7]} to origin/{BRANCH}")
            r = subprocess.run(
                ["git", "push", "--quiet", f"--force-with-lease=refs/heads/{BRANCH}:{remote_sha}",
                 "origin", f"{commit}:refs/heads/{BRANCH}"],
                cwd=ROOT, text=True, capture_output=True)
            if r.returncode == 0:
                git("fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
                install(tree, out)
                say("done: data branch updated; the site picks it up on the next scheduled run")
                return
            if "stale info" not in r.stderr and "rejected" not in r.stderr:
                sys.exit(f"push failed:\n{r.stderr}")
            say(f"  the data branch moved while syncing (a scheduled run?); merging again ({attempt + 1}/3)")
    sys.exit("gave up: the data branch kept changing. Try again in a few minutes.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("pull", "push"))
    ap.add_argument("--out", type=Path, default=ROOT / "data", help="local data directory (default: data/)")
    ap.add_argument("--dry-run", action="store_true", help="push: merge and commit, but don't push")
    args = ap.parse_args()
    out = args.out.resolve()
    try:
        cmd_pull(out) if args.command == "pull" else cmd_push(out, args.dry_run)
    except subprocess.CalledProcessError as e:
        sys.exit(f"git {' '.join(e.cmd[1:])} failed:\n{e.stderr or e.stdout}")


if __name__ == "__main__":
    main()
