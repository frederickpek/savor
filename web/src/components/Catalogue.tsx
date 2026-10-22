import { ArrowLeft, Check, ChevronDown, Search, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { type Place, price, ratingsLabel } from '../lib/api'
import Badges from './Badges'
import PanelHeader from './PanelHeader'
import PlaceDetails from './PlaceDetails'
import ScoreRing from './ScoreRing'

export type Filters = { prices: number[]; cuisines: string[]; q: string; scoredOnly: boolean }
export const NO_FILTERS: Filters = { prices: [], cuisines: [], q: '', scoredOnly: false }

type Sort = 'rank' | 'stars' | 'score' | 'ratings' | 'name'
// 'all' is the whole catalogue; the rest are curated subsets of it.
export type CatalogueMode = 'all' | 'top200' | 'michelin' | 'bib'

const starsOf = (p: Place) => (typeof p.michelin === 'number' ? p.michelin : 0)

// Each view: which places it holds, its default sort, and its header copy.
export const VIEWS: Record<CatalogueMode, { title: string; keep: (p: Place) => boolean; sort: Sort; sorts: Sort[] }> = {
  all: { title: 'All Places', keep: () => true, sort: 'score', sorts: ['score', 'ratings', 'name'] },
  top200: { title: 'SG Top 200', keep: (p) => p.popular_rank != null, sort: 'rank', sorts: ['rank', 'score', 'ratings', 'name'] },
  michelin: { title: 'MICHELIN Stars', keep: (p) => starsOf(p) > 0, sort: 'stars', sorts: ['stars', 'score', 'ratings', 'name'] },
  bib: { title: 'Bib Gourmand', keep: (p) => p.michelin === 'bib', sort: 'score', sorts: ['score', 'ratings', 'name'] },
}
const SORT_LABELS: Record<Sort, string> = { rank: 'Beli rank', stars: 'MICHELIN stars', score: 'Beli score', ratings: 'Most rated', name: 'Name' }

// Filters stack: prices OR'd together, cuisines OR'd together, and the groups AND'd.
export function applyFilters(places: Place[], f: Filters) {
  const q = f.q.trim().toLowerCase()
  const cs = new Set(f.cuisines)
  return places.filter((p) =>
    (!f.prices.length || (p.price != null && f.prices.includes(p.price))) &&
    (!cs.size || p.cuisines.some((c) => cs.has(c))) &&
    (!f.scoredOnly || p.beli_score != null) &&
    (!q || p.name.toLowerCase().includes(q) || (p.neighborhood ?? '').toLowerCase().includes(q)))
}

const completeness = (p: Place) => Number(p.price != null) + Number(p.cuisines.length > 0)

const SORTS: Record<Sort, (a: Place, b: Place) => number> = {
  rank: (a, b) => (a.popular_rank ?? 999) - (b.popular_rank ?? 999),
  // Most stars first; within a tier, best Beli score.
  stars: (a, b) => starsOf(b) - starsOf(a) || (b.beli_score ?? -1) - (a.beli_score ?? -1) || a.name.localeCompare(b.name),
  // Unscored places tie on score; fuller listings (price and cuisine known) beat bare stall entries.
  score: (a, b) => (b.beli_score ?? -1) - (a.beli_score ?? -1) || (b.review_count ?? 0) - (a.review_count ?? 0) || completeness(b) - completeness(a),
  ratings: (a, b) => (b.review_count ?? -1) - (a.review_count ?? -1),
  name: (a, b) => a.name.localeCompare(b.name),
}

const PAGE = 60
const TOP_CUISINES = 15
// Too broad to narrow anything down: about one in ten places is tagged with it.
const HIDDEN_CUISINES = new Set(['Singaporean'])

type Props = {
  mode: CatalogueMode
  places: Place[]
  filters: Filters
  onFilters: (f: Filters) => void
  selected: number | null
  onSelect: (id: number) => void
  onClose: () => void
}

export default function Catalogue({ mode, places: all, filters: f, onFilters, selected, onSelect, onClose }: Props) {
  const view = VIEWS[mode]
  const top = mode === 'top200'
  const places = useMemo(() => all.filter(view.keep), [all, view])
  const [sort, setSort] = useState<Sort>(view.sort)
  useEffect(() => setSort(view.sort), [view])
  const stars = useMemo(() => [3, 2, 1].map((n) => [n, places.filter((p) => starsOf(p) === n).length] as const), [places])
  const [shown, setShown] = useState(PAGE)
  const list = useRef<HTMLDivElement>(null)
  // The open row, so a place picked on the map scrolls into view here.
  const openRow = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (selected != null) openRow.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selected])

  const cuisines = useMemo(() => {
    const n = new Map<string, number>()
    for (const p of places) for (const c of p.cuisines) if (!HIDDEN_CUISINES.has(c)) n.set(c, (n.get(c) ?? 0) + 1)
    return [...n].sort((a, b) => b[1] - a[1])
  }, [places])
  const results = useMemo(() => applyFilters(places, f).sort(SORTS[sort]), [places, f, sort])
  const scored = useMemo(() => places.filter((p) => p.beli_score != null).length, [places])

  useEffect(() => {
    setShown(PAGE)
    list.current?.scrollTo({ top: 0 })
  }, [f, sort, mode])

  const toggle = <T,>(xs: T[], x: T) => (xs.includes(x) ? xs.filter((y) => y !== x) : [...xs, x])
  // Quick chips: the most common cuisines, plus any picked from the full list so they stay visible.
  const visible = [
    ...cuisines.slice(0, TOP_CUISINES),
    ...cuisines.slice(TOP_CUISINES).filter(([c]) => f.cuisines.includes(c)),
  ]
  const toggleCuisine = (c: string) => onFilters({ ...f, cuisines: toggle(f.cuisines, c) })
  const active = f.prices.length + f.cuisines.length + (f.q ? 1 : 0) + (f.scoredOnly ? 1 : 0)

  return (
    <div ref={list} className="scroll-thin h-full overflow-y-auto"
      onScroll={(e) => {
        const t = e.currentTarget
        if (t.scrollTop + t.clientHeight > t.scrollHeight - 400 && shown < results.length) setShown((s) => s + PAGE)
      }}
    >
      <PanelHeader
        title={view.title}
        leading={
          <button onClick={onClose} aria-label="Back to this week's picks" className="-ml-2 mt-1 grid size-9 shrink-0 place-items-center rounded-full hover:bg-panel">
            <ArrowLeft className="size-5" strokeWidth={2.5} />
          </button>
        }
        subtitle={<>{{
          all: <>{results.length.toLocaleString()} of {places.length.toLocaleString()} open places in Singapore · {scored.toLocaleString()} rated so far</>,
          top200: <>Beli's most popular places in Singapore, in Beli's order
            {places.length < 200 && ` · ${200 - places.length} closed ones left out`}</>,
          michelin: <>MICHELIN Guide Singapore 2026 · {stars.map(([n, c]) => `${c} with ${n} star${n > 1 ? 's' : ''}`).join(', ')}</>,
          bib: <>MICHELIN Guide Singapore 2026: good food at moderate prices · {places.length} places</>,
        }[mode]}
        {mode !== 'all' && results.length !== places.length && <> · {results.length} of {places.length} match</>}</>}
      >
        <label className="relative mt-5 block">
          <Search className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted" strokeWidth={2.5} />
          <input
            value={f.q}
            onChange={(e) => onFilters({ ...f, q: e.target.value })}
            placeholder="Search by name or neighbourhood"
            className="w-full rounded-full border border-line bg-panel py-2.5 pr-4 pl-10 text-sm outline-none placeholder:text-faint focus:border-teal-bright focus:bg-white"
          />
        </label>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="w-16 text-xs font-semibold text-muted">Price</span>
          {[1, 2, 3, 4].map((p) => (
            <Chip key={p} on={f.prices.includes(p)} onClick={() => onFilters({ ...f, prices: toggle(f.prices, p) })}>{price(p)}</Chip>
          ))}
          <span className="mx-1 h-5 w-px bg-line" />
          <Chip on={f.scoredOnly} onClick={() => onFilters({ ...f, scoredOnly: !f.scoredOnly })}>Has Beli score</Chip>
        </div>
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <span className="w-16 text-xs font-semibold text-muted">Cuisine</span>
          {/* First in the row so it never moves, however many chips wrap after it. */}
          <CuisinePicker cuisines={cuisines} picked={f.cuisines} onToggle={toggleCuisine} onClear={() => onFilters({ ...f, cuisines: [] })} />
          {visible.map(([c, n]) => (
            <Chip key={c} on={f.cuisines.includes(c)} onClick={() => toggleCuisine(c)}>
              {c} <span className={f.cuisines.includes(c) ? 'text-white/70' : 'text-faint'}>{n.toLocaleString()}</span>
            </Chip>
          ))}
        </div>

        <div className="mt-4 flex items-center gap-3 text-sm">
          {active > 0 && (
            <button onClick={() => onFilters(NO_FILTERS)} className="inline-flex items-center gap-1 font-semibold text-teal hover:underline">
              <X className="size-3.5" strokeWidth={3} /> Clear {active} filter{active > 1 ? 's' : ''}
            </button>
          )}
          <label className="ml-auto inline-flex items-center gap-2 text-muted">
            Sort
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} className="rounded-lg border border-line bg-white px-2 py-1 text-ink">
              {view.sorts.map((s) => <option key={s} value={s}>{SORT_LABELS[s]}</option>)}
            </select>
          </label>
        </div>
      </PanelHeader>

      <div className="px-2 pt-4 pb-10">
        {results.length ? (
          <ul className="grid gap-x-2 xl:grid-cols-2">
            {results.slice(0, shown).map((p) => {
              const open = p.business_id === selected
              return (
              // Selected rows expand downward in their own column (directions, website, ratings breakdown).
              <li key={p.business_id} ref={open ? openRow : undefined}
                className={`self-start rounded-xl transition-colors ${open ? 'bg-teal-softer ring-1 ring-teal-soft' : 'hover:bg-panel'}`}>
                <button
                  onClick={() => onSelect(p.business_id)}
                  aria-expanded={open}
                  className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left"
                >
                  {top && (
                    <span className="w-9 shrink-0 text-right text-[15px] font-extrabold tabular-nums text-teal">#{p.popular_rank}</span>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate font-semibold">{p.name}</span>
                      <Badges place={p} hidePopular={top} compact />
                    </div>
                    <div className="truncate text-[13px] text-muted">
                      {[price(p.price), p.cuisines.slice(0, 2).join(', '), p.neighborhood,
                        ratingsLabel(p)].filter(Boolean).join(' · ')}
                    </div>
                  </div>
                  <ScoreRing score={p.beli_score} count={p.review_count} size={34} />
                </button>
                {open && <div className={`pb-4 pr-3 ${top ? 'pl-[60px]' : 'pl-3'}`}><PlaceDetails place={p} /></div>}
              </li>
              )
            })}
          </ul>
        ) : (
          <p className="px-3 py-8 text-center text-sm text-muted">No places match these filters. Try removing one.</p>
        )}
      </div>
    </div>
  )
}

function CuisinePicker({ cuisines, picked, onToggle, onClear }: {
  cuisines: [string, number][]
  picked: string[]
  onToggle: (c: string) => void
  onClear: () => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    search.current?.focus()
    const away = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  const term = q.trim().toLowerCase()
  const shown = term ? cuisines.filter(([c]) => c.toLowerCase().includes(term)) : cuisines

  return (
    <div ref={box} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={`inline-flex h-[30px] min-w-36 items-center justify-between gap-2 rounded-lg border bg-white pr-2 pl-3 text-[13px] font-semibold transition-colors ${open ? 'border-teal-bright ring-2 ring-teal-soft' : picked.length ? 'border-teal-bright text-teal' : 'border-line text-ink hover:border-teal-bright'}`}
      >
        <span>{picked.length ? `${picked.length} selected` : `All cuisines`}</span>
        <ChevronDown className={`size-4 text-muted transition-transform duration-200 ${open ? 'rotate-180' : ''}`} strokeWidth={2.5} />
      </button>
      {open && (
        <div className="absolute top-full left-0 z-20 mt-2 w-72 overflow-hidden rounded-xl border border-line bg-white shadow-[0_8px_28px_-6px_rgb(0_0_0/0.22)]">
          <div className="border-b border-line p-2">
            <input
              ref={search}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Find a cuisine"
              className="w-full rounded-lg bg-panel px-3 py-2 text-sm outline-none placeholder:text-faint focus:bg-white focus:ring-1 focus:ring-teal-bright"
            />
          </div>
          <ul className="scroll-thin max-h-72 overflow-y-auto py-1">
            {shown.map(([c, n]) => {
              const on = picked.includes(c)
              return (
                <li key={c}>
                  <button onClick={() => onToggle(c)} className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-sm hover:bg-panel">
                    <span className={`grid size-4 shrink-0 place-items-center rounded border ${on ? 'border-teal bg-teal text-white' : 'border-line'}`}>
                      {on && <Check className="size-3" strokeWidth={3.5} />}
                    </span>
                    <span className={`flex-1 truncate ${on ? 'font-semibold' : ''}`}>{c}</span>
                    <span className="text-xs text-faint">{n.toLocaleString()}</span>
                  </button>
                </li>
              )
            })}
            {!shown.length && <li className="px-3 py-3 text-sm text-muted">No cuisine matches "{q}".</li>}
          </ul>
          <div className="flex items-center justify-between border-t border-line px-3 py-2 text-[13px]">
            <button onClick={onClear} disabled={!picked.length} className="font-semibold text-teal hover:underline disabled:text-faint disabled:no-underline">
              Clear
            </button>
            <button onClick={() => setOpen(false)} className="rounded-full bg-teal px-3 py-1 font-semibold text-white hover:bg-teal-hover">Done</button>
          </div>
        </div>
      )}
    </div>
  )
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={on}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[13px] font-semibold transition-colors ${on ? 'border-teal bg-teal text-white' : 'border-line bg-white text-ink hover:border-teal-bright'}`}
    >
      {children}
    </button>
  )
}
