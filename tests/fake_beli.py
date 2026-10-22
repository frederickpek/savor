"""Offline stand-in for `pipeline.beli.Beli`, serving canned responses from a fixtures dir.

Fixture files are named after the endpoint: `/api/get-ranking/` -> `get-ranking.json`,
`/api/business-count-rated/7/` -> `business-count-rated/7.json`,
`/api/business-histogram-data/7/` -> `business-histogram-data/7.json`.
A missing fixture behaves like a 404.
"""

import json
from pathlib import Path

from pipeline.beli import PermanentError

USER = "00000000-0000-4000-8000-000000000001"


class FakeBeli:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.user_id = USER
        self.calls = 0

    def _load(self, name: str):
        self.calls += 1
        path = self.root / f"{name}.json"
        if not path.exists():
            raise PermanentError(f"no fixture {path}")
        return json.loads(path.read_text())

    def get(self, path: str, params: dict | None = None, patient: bool = True):
        parts = [p for p in path.split("/") if p and p != "api" and p != USER]
        if parts[0] == "databusinessfloat-sparse" and params:
            return self._load(f"databusinessfloat-sparse/{params['business']}")
        if parts[0] == "business-histogram-data":
            return self._load(f"business-histogram-data/{parts[1]}")
        if parts[0] == "business-count-rated":
            return self._load(f"business-count-rated/{parts[1]}")
        if parts[0] == "popular-in-city":
            return self._load(f"popular-in-city/{parts[1]}")
        return self._load(parts[0])

    def post(self, path: str, body: dict):
        return self._load(path.strip("/").split("/")[-1])

    def geocode(self, query: str):
        """Stand-in for `pipeline.geocode.lookup` (OneMap)."""
        return json.loads((self.root / "onemap.json").read_text()).get(query)
