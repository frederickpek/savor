"""Fallback coordinates from OneMap (Singapore Land Authority) for places Beli gave no lat/lng.

The search endpoint answers without a token today (it adds an "authentication token missing"
note but still returns results); set ONEMAP_TOKEN if that ever stops. Only used for rows already
known to be in Singapore, so a same-named place abroad can't be pulled in by mistake.
"""

import json
import os
import time
import urllib.parse
import urllib.request

from . import db, normalize

URL = "https://www.onemap.gov.sg/api/common/elastic/search"
MAX_CALLS = 60


def lookup(query: str) -> dict | None:
    qs = urllib.parse.urlencode({"searchVal": query, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1})
    req = urllib.request.Request(f"{URL}?{qs}", headers={"User-Agent": "savor/0.1"})
    if token := os.environ.get("ONEMAP_TOKEN"):
        req.add_header("Authorization", token)
    with urllib.request.urlopen(req, timeout=20) as resp:
        results = json.load(resp).get("results") or []
    if not results:
        return None
    r = results[0]
    lat, lng = normalize.to_float(r.get("LATITUDE")), normalize.to_float(r.get("LONGITUDE"))
    if not normalize.in_singapore(lat, lng):
        return None
    address = r.get("ADDRESS")
    return {"lat": lat, "lng": lng, "address": address if address and address != "NIL" else None}


def enrich(con, today: str, summary: dict, lookup=lookup) -> None:
    rows = con.execute(
        """SELECT business_id, name, address FROM businesses
           WHERE in_sg = 1 AND (lat IS NULL OR lng IS NULL) AND geo_fetched_on IS NULL""").fetchall()
    found = 0
    for r in rows[:MAX_CALLS]:
        query = r["address"] or r["name"]
        try:
            hit = lookup(query)
        except Exception as e:
            summary["failures"]["geocode"] = repr(e)[:300]
            break
        fields = {"geo_fetched_on": today}
        if hit:
            fields.update(lat=hit["lat"], lng=hit["lng"], geo_source="onemap")
            if not r["address"] and hit["address"]:
                fields["address"] = hit["address"].title()
            found += 1
        db.set_fields(con, r["business_id"], **fields)
        time.sleep(0.25)
    summary["sources"]["geocode"] = found
    con.commit()
