"""Turn raw Beli payloads into flat business rows.

Beli returns at least five envelope shapes (DRF pages, bare lists, category-keyed dicts,
id lists with a side `business_hash`, ...) and fields come and go with feature flags. Every
helper here is defensive: unknown shapes yield nothing rather than raising, and the schema
audit is where drift gets noticed.
"""

import re
from typing import Any, Iterable

from .config import ADDRESS_KEYS, REVIEW_COUNT_KEYS, SCORE_KEYS, SG_BOUNDS


def records(payload: Any) -> list[dict]:
    """Flatten any list-ish envelope into a list of dicts."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("results", "businesses", "data", "items"):
        inner = payload.get(key)
        if isinstance(inner, list) and any(isinstance(r, dict) for r in inner):
            return [r for r in inner if isinstance(r, dict)]
    if isinstance(payload.get("business_hash"), dict):
        return [v for v in payload["business_hash"].values() if isinstance(v, dict)]
    # Category-keyed, e.g. get-bookmark -> {"Restaurants": [...], "Bars": [...]}.
    lists = [v for v in payload.values() if isinstance(v, list)]
    if lists and all(all(isinstance(r, dict) for r in v) for v in lists):
        return [r for v in lists for r in v]
    return []


def business_of(item: dict) -> dict | None:
    """The business object inside a list row, or the row itself if it is one."""
    b = item.get("business") or item.get("business_full")
    if isinstance(b, dict):
        return b
    if "name" in item and isinstance(item.get("id"), int):
        return item
    return None


def first(obj: dict, keys: Iterable[str]) -> Any:
    for k in keys:
        v = obj.get(k)
        if v not in (None, "", []):
            return v
    return None


def to_float(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def to_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, dict):
        v = first(v, ("count", "value", "result", "total"))
    f = to_float(v)
    return int(f) if f is not None else None


def price_tier(b: dict) -> int | None:
    """1-4 ($ to $$$$) from `price`, falling back to `price_key`."""
    for v in (b.get("price"), b.get("price_key")):
        if v is None:
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v) if 1 <= v <= 4 else None
        s = str(v)
        if s.count("$"):
            return min(s.count("$"), 4)
        if m := re.search(r"[1-4]", s):
            return int(m.group())
    return None


def names(v: Any) -> list[str]:
    """Cuisine/label lists arrive as strings or objects; keep the display names."""
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, dict):
            x = first(x, ("name", "display", "label", "cuisine", "distinction"))
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return sorted(set(out))


def in_singapore(lat: float | None, lng: float | None) -> bool:
    if lat is None or lng is None:
        return False
    lat_min, lat_max, lng_min, lng_max = SG_BOUNDS
    return lat_min <= lat <= lat_max and lng_min <= lng <= lng_max


def business(b: dict) -> dict | None:
    """Normalized business row. Missing values stay None so upserts never clobber data."""
    bid = to_int(b.get("id"))
    name = b.get("name")
    if bid is None or not isinstance(name, str):
        return None
    return {
        "business_id": bid,
        "place_id": b.get("place_id") or None,
        "name": name.strip(),
        "address": first(b, ADDRESS_KEYS),
        "neighborhood": first(b, ("neighborhood", "borough")),
        "city": b.get("city") or None,
        "country": b.get("country") or None,
        "lat": to_float(b.get("lat")),
        "lng": to_float(b.get("lng")),
        "price": price_tier(b),
        "cuisines": names(b.get("cuisines")) or None,
        "beli_score": to_float(first(b, SCORE_KEYS)),
        "review_count": to_int(first(b, REVIEW_COUNT_KEYS)),
        "website": b.get("website") or None,
        "status": b.get("status") or None,
    }


def from_search(pred: dict) -> dict | None:
    """A search-app prediction carries only the Beli id (if known), name and an address line."""
    bid = to_int(pred.get("business"))
    sf = pred.get("structured_formatting") or {}
    name = sf.get("main_text") or pred.get("description")
    if bid is None or not isinstance(name, str):
        return None
    return {
        "business_id": bid,
        "place_id": pred.get("place_id") or None,
        "name": name.strip(),
        "address": sf.get("secondary_text") or None,
    }


def score_from_sparse(payload: Any, business_id: int) -> float | None:
    """AVGBUSINESSSCORE (EAV field 206) out of a databusinessfloat-sparse response."""
    for r in records(payload):
        field = r.get("field")
        fname = field.get("name") if isinstance(field, dict) else field
        if fname not in ("AVGBUSINESSSCORE", 206, "206"):
            continue
        rb = r.get("business")
        rb = rb.get("id") if isinstance(rb, dict) else rb
        if rb is None or to_int(rb) == business_id:
            return to_float(r.get("value"))
    return None
