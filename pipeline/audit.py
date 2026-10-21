"""Schema audit and missing-field report.

The schema audit fingerprints every raw response (envelope shape, key paths and their types)
and diffs it against the previous run, so a Beli change shows up as "key X disappeared from
ranked" rather than as quietly empty columns weeks later.
"""

import sqlite3
from typing import Any

from .config import REQUIRED_FIELDS


def _type(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return {str: "string", list: "array", dict: "object"}.get(type(v), type(v).__name__)


def key_paths(v: Any, prefix: str = "$", out: dict | None = None, depth: int = 0) -> dict[str, set[str]]:
    """{path: {types}} with list items collapsed to `[]` and dynamic dict keys (ids,
    category names) collapsed to `{*}`, so the fingerprint is stable day to day."""
    out = {} if out is None else out
    out.setdefault(prefix, set()).add(_type(v))
    if depth > 6:
        return out
    if isinstance(v, list):
        for item in v[:200]:
            key_paths(item, f"{prefix}[]", out, depth + 1)
    elif isinstance(v, dict):
        dynamic = len(v) > 3 and all(k.isdigit() or " " in k or k[:1].isupper() for k in v)
        for k, item in list(v.items())[:500]:
            key_paths(item, f"{prefix}.{{*}}" if dynamic else f"{prefix}.{k}", out, depth + 1)
    return out


def fingerprint(payload: Any) -> dict[str, list[str]]:
    return {p: sorted(t) for p, t in sorted(key_paths(payload).items())}


def diff(prev: dict[str, list[str]], cur: dict[str, list[str]]) -> dict:
    """Paths added/removed, and type changes. `null` coming and going is ignored: optional
    fields flip between null and a value all the time and that isn't drift."""
    def core(ts):
        return sorted(set(ts) - {"null"})
    changed = {
        p: {"was": prev[p], "now": cur[p]}
        for p in prev.keys() & cur.keys()
        if core(prev[p]) and core(cur[p]) and core(prev[p]) != core(cur[p])
    }
    return {
        "added": sorted(cur.keys() - prev.keys()),
        "removed": sorted(prev.keys() - cur.keys()),
        "type_changed": changed,
    }


def schema_report(fingerprints: dict[str, dict], previous: dict[str, dict]) -> dict:
    report = {}
    for source, fp in fingerprints.items():
        entry = {"paths": len(fp), "fingerprint": fp}
        if source in previous:
            d = diff(previous[source], fp)
            entry["drift"] = d
            entry["drifted"] = bool(d["removed"] or d["type_changed"])
        report[source] = entry
    for source in previous.keys() - fingerprints.keys():
        report[source] = {"paths": 0, "missing_today": True, "drifted": True}
    return report


def missing_fields(con: sqlite3.Connection) -> dict:
    """Coverage of each required field, for Singapore places, overall and per list."""
    def coverage(where: str, params: tuple = ()) -> dict:
        rows = con.execute(f"SELECT * FROM businesses b WHERE in_sg = 1 AND {where}", params).fetchall()
        n = len(rows)
        fields = {}
        for f in REQUIRED_FIELDS:
            have = sum(1 for r in rows if r[f] not in (None, "", "[]"))
            fields[f] = {"present": have, "missing": n - have, "pct": round(100 * have / n, 1) if n else None}
        return {"businesses": n, "fields": fields}

    report = {"all": coverage("1")}
    for (lt,) in con.execute("SELECT DISTINCT list_type FROM sightings ORDER BY 1"):
        report[lt] = coverage(
            "business_id IN (SELECT business_id FROM sightings WHERE list_type = ?)", (lt,))

    # A short sample of the worst rows makes it easy to eyeball what's wrong.
    worst = con.execute(
        f"""SELECT business_id, name, {", ".join(REQUIRED_FIELDS[2:])} FROM businesses WHERE in_sg = 1""").fetchall()
    gaps = []
    for r in worst:
        missing = [f for f in REQUIRED_FIELDS[2:] if r[f] in (None, "", "[]")]
        if missing:
            gaps.append({"business_id": r["business_id"], "name": r["name"], "missing": missing})
    gaps.sort(key=lambda g: -len(g["missing"]))
    report["worst"] = gaps[:25]
    report["unclassified"] = con.execute("SELECT COUNT(*) FROM businesses WHERE in_sg IS NULL").fetchone()[0]
    report["outside_sg"] = con.execute("SELECT COUNT(*) FROM businesses WHERE in_sg = 0").fetchone()[0]
    return report


def markdown(schema: dict, missing: dict, summary: dict) -> str:
    """Compact text for the GitHub Actions step summary."""
    lines = ["### Sources", "| source | rows | note |", "|---|---|---|"]
    for s in sorted(set(summary["sources"]) | set(summary["failures"])):
        note = summary["failures"].get(s, "")
        if s in summary.get("backlog", {}) and summary["backlog"][s]:
            note = note or f"backlog {summary['backlog'][s]}"
        lines.append(f"| {s} | {summary['sources'].get(s, '—')} | {note} |")
    drifted = {s: e for s, e in schema.items() if e.get("drifted")}
    lines += ["", "### Schema drift", "none" if not drifted else ""]
    for s, e in drifted.items():
        if e.get("missing_today"):
            lines.append(f"- **{s}**: no response today")
            continue
        d = e["drift"]
        lines.append(f"- **{s}**: removed {d['removed'][:8]}, type changed {list(d['type_changed'])[:8]}")
    lines += ["", f"### Field coverage (Singapore, {missing['all']['businesses']} places)",
              "| field | present | missing |", "|---|---|---|"]
    for f, c in missing["all"]["fields"].items():
        lines.append(f"| {f} | {c['pct'] if c['pct'] is not None else '—'}% | {c['missing']} |")
    return "\n".join(lines) + "\n"
