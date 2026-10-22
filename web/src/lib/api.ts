// Static API written by the daily pipeline (see pipeline/run.py for the layout).

export type Place = {
  business_id: number
  name: string
  address: string | null
  neighborhood: string | null
  lat: number | null
  lng: number | null
  price: number | null
  cuisines: string[]
  beli_score: number | null
  review_count: number | null
  website: string | null
  // Position on Beli's popular-in-Singapore list (1 = most popular), when it's on it.
  popular_rank?: number | null
  // MICHELIN Guide Singapore (hand-maintained snapshot): 1-3 stars, or 'bib' for Bib Gourmand.
  michelin?: 1 | 2 | 3 | 'bib' | null
}

export type Pick = Place & {
  km: number | null
  reasons: string[]
  bookmarked: boolean
  // Your own score when you've ranked it on Beli; shown as a tag, never used to filter.
  ranked_score: number | null
  // Which half of the weekly mix it came from: Beli's popular list, or the wider catalogue.
  pool?: 'popular' | 'discover'
}

export type Picks = {
  date: string // Monday of the week
  generated_on?: string
  candidates: number
  contexts: Record<string, { prefs: Record<string, unknown>; picks: Pick[] }>
}

export type Index = {
  latest: string
  dates: string[]
  contexts: string[]
  generatedAt: string
  status: 'ok' | 'partial' | 'failed' | 'skipped'
  candidates: number
}


async function get<T>(path: string): Promise<T> {
  const r = await fetch(`./data/api/${path}`)
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`)
  return r.json()
}

export const api = {
  index: () => get<Index>('index.json'),
  picks: (date: string) => get<Picks>(`picks/${date}.json`),
  // The whole catalogue ships as {cols, rows} to keep ~18k places small; expand to objects here.
  places: async () => {
    const { cols, rows } = await get<{ cols: string[]; rows: unknown[][] }>('places.json')
    return rows.map((r) => {
      const o = Object.fromEntries(cols.map((c, i) => [c, r[i]])) as Place
      return { ...o, address: o.address ?? null, cuisines: o.cuisines ?? [] }
    })
  },
}

export const price = (p: number | null) => (p ? '$'.repeat(p) : '—')
// Where a place's Beli rating stands. review_count is null until the ratings pull reaches it, 0 when
// nobody has rated it, and Beli withholds the score until a place has about 5 ratings.
type Rated = { beli_score: number | null; review_count: number | null }
export type RatingState = 'scored' | 'pending' | 'unrated' | 'few'
export const ratingState = (p: Rated): RatingState =>
  p.beli_score != null ? 'scored' : p.review_count == null ? 'pending' : p.review_count === 0 ? 'unrated' : 'few'
export const ratingsLabel = (p: Rated) => {
  const n = p.review_count
  switch (ratingState(p)) {
    case 'pending': return 'Not checked yet'
    case 'unrated': return 'No ratings yet'
    case 'few': return `${n} rating${n === 1 ? '' : 's'}, too few for a score`
    default: return n ? `${n.toLocaleString()} ratings` : null
  }
}
export const RATING_TITLES: Record<Exclude<RatingState, 'scored'>, string> = {
  pending: "Not checked yet: the ratings pull hasn't reached this place",
  unrated: 'No one has rated this on Beli yet',
  few: 'Beli shows a score once about 5 people have rated it',
}
export const mapsUrl = (p: Place) =>
  `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${p.name} ${p.address ?? 'Singapore'}`)}`
