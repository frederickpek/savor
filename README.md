# savor

Restaurant discovery for **Singapore**, built on Beli. It keeps Beli's catalogue of every Singapore
place (~18k) in its own database and publishes, every Monday, 10 picks per context with a short
reason each, on a map and a list. The whole catalogue is browsable too: the SG Top 200, MICHELIN
stars, Bib Gourmand, every place with price and cuisine filters, and a statistics page.

## How it works

| | |
|---|---|
| **Sources** | Beli via [ProjectBarks/beli-api](https://github.com/ProjectBarks/beli-api) (pinned in `pipeline/requirements.txt`). One `filter-list` call returns the whole Singapore catalogue; Beli's popular-in-Singapore 200, your ranked list and your bookmarks are fetched alongside. |
| **Ratings** | Beli's score and rating count need one place at a time (two calls each), so each run fetches 150. Places with no data go first (Top 200, MICHELIN, this week's picks, your lists, priced places, the rest); once all have data, the 150 stalest are refreshed. |
| **MICHELIN** | `pipeline/data/michelin.json` is a hand-kept snapshot of the MICHELIN Guide Singapore 2026 (3 three-star, 9 two-star, 33 one-star, 97 Bib Gourmand), each linked to its Beli id. Refresh it when the next guide is out. |
| **Picks** | Every open Singapore place is scored on fit (cuisine, price), Beli quality, novelty, distance and friends. Each context's 10 = 4 from the Top 200 + 6 discoveries (Beli 7.5+ from 20+ ratings). A place picked anywhere sits out all contexts for 6 weeks. |
| **Filters** | `max_outlets` (5) drops chains, `cuisines_avoid` drops Fast Food and Food Court, and closed places and anything outside Singapore are skipped. |

## Preferences

`prefs.json` holds the defaults (budget, origin, distance, picks per list) and one entry per context
under `contexts` (Weeknight, Date Night, Weekend Explore) that overrides them. Push a change to
rebuild the current week's picks.

## Deploying

`.github/workflows/deploy.yml` builds and publishes the site to GitHub Pages:

| Trigger | What it does |
|---|---|
| Every 6 hours (00:17, 06:17, 12:17, 18:17 SGT) | Ingest from Beli, rate 150 places, deploy. The first run each Monday writes new picks. |
| Push to `master` touching `web/`, `pipeline/`, `prefs.json` or the workflow | Deploy without calling Beli. Only a `prefs.json` change rebuilds this week's picks. |
| **Actions → ingest + picks + deploy → Run workflow** | `ingest`, `rerank-only` or `deploy-only`, with an optional `score_budget`. |

The database (`savor.db`), raw responses and reports live on the `data` branch, which the workflow
force-pushes as a single commit each run. On a public repo that branch, and your lists in it, are public.

One-time setup:
- **Settings → Secrets and variables → Actions → Secrets:** `BELI_EMAIL`, `BELI_PASSWORD` (optional `ONEMAP_TOKEN`)
- **Settings → Pages → Source:** GitHub Actions
- **Settings → Actions → General → Workflow permissions:** Read and write

To pull ratings faster than the schedule, run `scripts/pull_ratings.py` locally (Ctrl-C to stop, it
resumes where it left off), then push `data/` to the `data` branch before the next scheduled run.

## Local development

```sh
uv venv .venv && uv pip install -p .venv -r pipeline/requirements.txt pytest
.venv/bin/python -m pytest -q tests

# offline, from canned responses
.venv/bin/python -m pipeline.run --out data --fixtures tests/fixtures
# live (your own account): credentials go in .env, which git ignores
cp .env.example .env    # then fill in BELI_EMAIL and BELI_PASSWORD
.venv/bin/python -m pipeline.run --out data

ln -sfn ../../data web/public/data && cd web && npm install && npm run dev
```

Real and fixture runs share `savor.db` if they write to the same `--out`, so move the fixture
output aside (`mv data data-fixtures`) before your first live run.

## Caveats

beli-api is unofficial and reverse-engineered. Several response shapes used here (popular-in-city,
count-rated, the score EAV field) are inferred from the spec rather than observed. Normalization
therefore tries several key names, and the schema audit reports each source's actual shape after the
first live run. Check `reports/<date>.json` then and adjust `pipeline/config.py` key lists if a
field comes back empty.
