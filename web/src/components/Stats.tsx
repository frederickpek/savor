import { ArrowLeft } from 'lucide-react'
import { type ReactNode, useMemo, useState } from 'react'
import { type Place, price } from '../lib/api'
import PanelHeader from './PanelHeader'

// One hue for every mark: single-series charts, so the title names the series and no legend is needed.
const BAR = 'var(--teal-bright)'
const BAR_HOVER = 'var(--teal)'
const GRID = '#e8ecec'

const median = (xs: number[]) => {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  const m = s.length >> 1
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)
const fmt = (x: number | null, d = 2) => (x == null ? '–' : x.toFixed(d))

// Clean y ticks: the smallest round step (1, 2, 2.5 or 5 × 10^n) that covers the max in at most
// four steps, so the axis ends just above the tallest bar (max 340 → 0, 100, … 400, not 600).
function ticks(max: number) {
  const top = Math.max(max, 1)
  const mag = 10 ** Math.max(0, Math.floor(Math.log10(top / 4)))  // counts: whole-number steps
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s % 1 === 0 && Math.ceil(top / s) <= 4)!
  return Array.from({ length: Math.ceil(top / step) + 1 }, (_, i) => i * step)
}

export default function Stats({ places, onClose }: { places: Place[]; onClose: () => void }) {
  const rated = useMemo(() => places.filter((p) => p.beli_score != null), [places])
  const scores = useMemo(() => rated.map((p) => p.beli_score!), [rated])
  const ratings = useMemo(() => places.reduce((n, p) => n + (p.review_count ?? 0), 0), [places])

  return (
    <div className="scroll-thin h-full overflow-y-auto">
      <PanelHeader
        title="Statistics"
        leading={
          <button onClick={onClose} aria-label="Back to this week's picks" className="-ml-2 mt-1 grid size-9 shrink-0 place-items-center rounded-full hover:bg-panel">
            <ArrowLeft className="size-5" strokeWidth={2.5} />
          </button>
        }
        subtitle={<>Beli scores across Singapore · ratings are still filling in, so these shift as more places are scored</>}
      >
        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
          <Tile label="Places in the catalogue" value={places.length.toLocaleString()} />
          <Tile label="Rated so far" value={rated.length.toLocaleString()} note={`${((rated.length / Math.max(places.length, 1)) * 100).toFixed(1)}% of places`} />
          <Tile label="Median Beli score" value={fmt(median(scores))} note={`mean ${fmt(mean(scores))}`} />
          <Tile label="Ratings behind them" value={compact(ratings)} note="Beli users' ratings, summed" />
        </dl>
      </PanelHeader>

      <div className="space-y-8 px-5 pt-6 pb-16">
        {rated.length < 20 ? (
          <p className="text-sm text-muted">Too few places are rated yet to chart. Run the ratings pull, then come back.</p>
        ) : (
          <>
            <Card title="Score distribution" subtitle={`${rated.length.toLocaleString()} rated places, scores rounded to one decimal`}>
              <Histogram scores={scores} />
            </Card>

            <div className="grid gap-8 xl:grid-cols-2">
              <Card title="By price" subtitle="Median Beli score per price tier">
                <DotPlot rows={groupBy(rated, (p) => (p.price ? [price(p.price)] : []), 5, ['$', '$$', '$$$', '$$$$'])} />
              </Card>
              <Card title="MICHELIN vs the rest" subtitle="Median Beli score per MICHELIN Guide 2026 award">
                <DotPlot rows={groupBy(rated, (p) => [p.michelin === 'bib' ? 'Bib Gourmand' : p.michelin ? `${p.michelin} star${p.michelin > 1 ? 's' : ''}` : 'Not in the guide'], 1,
                  ['3 stars', '2 stars', '1 star', 'Bib Gourmand', 'Not in the guide'])} />
              </Card>
            </div>

            <div className="grid gap-8 xl:grid-cols-2">
              <Card title="Best-rated cuisines" subtitle="Median Beli score, cuisines with 15+ rated places">
                <DotPlot rows={groupBy(rated, (p) => p.cuisines.filter((c) => c !== 'Singaporean'), 15).slice(0, 12)} />
              </Card>
              <Card title="Best-rated neighbourhoods" subtitle="Median Beli score, neighbourhoods with 15+ rated places">
                <DotPlot rows={groupBy(rated, (p) => (p.neighborhood ? [p.neighborhood] : []), 15).slice(0, 12)} />
              </Card>
            </div>

            <Card title="Top rated" subtitle={`Highest Beli scores, places with ${TOP_MIN_RATINGS}+ ratings so a handful of fans can't top it`}>
              <TopList places={[...rated].filter((p) => (p.review_count ?? 0) >= TOP_MIN_RATINGS)
                .sort((a, b) => b.beli_score! - a.beli_score! || (b.review_count ?? 0) - (a.review_count ?? 0))} />
            </Card>

            <Card title="Most rated" subtitle="Places the most Beli users have rated">
              <TopList places={[...rated].sort((a, b) => (b.review_count ?? 0) - (a.review_count ?? 0))} />
            </Card>
          </>
        )}
      </div>
    </div>
  )
}

// Below this many ratings a score is a few people's opinion; keep those out of "Top rated".
const TOP_MIN_RATINGS = 50

function TopList({ places }: { places: Place[] }) {
  return (
    <ol className="divide-y divide-line">
      {places.slice(0, 10).map((p, i) => (
        <li key={p.business_id} className="flex items-baseline gap-3 py-2 text-sm">
          <span className="w-5 text-right text-muted tabular-nums">{i + 1}</span>
          <span className="min-w-0 flex-1 truncate font-semibold">{p.name}</span>
          <span className="text-muted tabular-nums">{(p.review_count ?? 0).toLocaleString()} ratings</span>
          <span className="w-10 text-right font-semibold tabular-nums">{fmt(p.beli_score)}</span>
        </li>
      ))}
    </ol>
  )
}

const compact = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}K` : n.toLocaleString())

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div>
      <dt className="text-[13px] text-muted">{label}</dt>
      <dd className="mt-0.5 text-[26px] font-bold leading-tight tracking-tight">{value}</dd>
      {note && <dd className="text-xs text-muted">{note}</dd>}
    </div>
  )
}

function Card({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="text-[20px] font-extrabold tracking-tight">{title}</h2>
      <p className="mb-3 text-sm text-muted">{subtitle}</p>
      {children}
    </section>
  )
}

// ---------- Histogram: one column per 0.1, from the lowest bucket seen up to 10.0 ----------

function Histogram({ scores }: { scores: number[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const buckets = useMemo(() => {
    const lo = Math.round(Math.min(...scores) * 10)
    const n = new Map<number, number>()
    for (const s of scores) {
      const b = Math.min(100, Math.round(s * 10))  // scores carry 2 decimals; bucket by their 1-dp rounding
      n.set(b, (n.get(b) ?? 0) + 1)
    }
    return Array.from({ length: 100 - lo + 1 }, (_, i) => ({ b: lo + i, n: n.get(lo + i) ?? 0 }))
  }, [scores])
  const max = Math.max(...buckets.map((x) => x.n))
  const ys = ticks(max)
  const top = ys[ys.length - 1]
  const W = 860, H = 240, L = 40, R = 14, B = 26, T = 10
  const slot = (W - L - R) / buckets.length
  const bw = Math.min(24, slot - 2)
  const y = (v: number) => T + (H - T - B) * (1 - v / top)
  const hb = hover != null ? buckets[hover] : null

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Histogram of Beli scores">
        {ys.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke={GRID} />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" className="fill-muted text-[11px] tabular-nums">{t.toLocaleString()}</text>
          </g>
        ))}
        {buckets.map((x, i) => {
          const h = (H - T - B) * (x.n / top)
          const bx = L + i * slot + (slot - bw) / 2
          const r = Math.min(4, h / 2, bw / 2)
          return (
            <g key={x.b} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
              {/* Hit area is the whole slot, taller than the bar, so thin or empty buckets still respond. */}
              <rect x={L + i * slot} y={T} width={slot} height={H - T - B} fill="transparent" />
              {x.n > 0 && (
                <path
                  d={`M${bx},${H - B} V${H - B - h + r} q0,-${r} ${r},-${r} H${bx + bw - r} q${r},0 ${r},${r} V${H - B} Z`}
                  fill={hover === i ? BAR_HOVER : BAR}
                />
              )}
              {x.b % 5 === 0 && (
                <text x={L + i * slot + slot / 2} y={H - 8} textAnchor="middle" className="fill-muted text-[11px] tabular-nums">{(x.b / 10).toFixed(1)}</text>
              )}
            </g>
          )
        })}
        <line x1={L} x2={W - R} y1={H - B} y2={H - B} stroke="#cdd4d6" />
      </svg>
      {hb && (
        <div
          className="pointer-events-none absolute top-0 -translate-x-1/2 rounded-lg bg-white px-2.5 py-1.5 text-xs shadow-[0_2px_10px_rgb(0_0_0/0.15)]"
          style={{ left: `${((L + (hover! + 0.5) * slot) / W) * 100}%` }}
        >
          <div className="font-bold tabular-nums">{hb.n.toLocaleString()} places</div>
          <div className="text-muted tabular-nums">Beli {(hb.b / 10).toFixed(1)}</div>
        </div>
      )}
    </div>
  )
}

// ---------- Dot plot: median per group on a shared 5–10 scale (bars would have to start at 0) ----------

type Row = { label: string; median: number; n: number }

function groupBy(places: Place[], keys: (p: Place) => string[], minN: number, order?: string[]): Row[] {
  const g = new Map<string, number[]>()
  for (const p of places) for (const k of keys(p)) g.set(k, [...(g.get(k) ?? []), p.beli_score!])
  const rows = [...g].filter(([, v]) => v.length >= minN).map(([label, v]) => ({ label, median: median(v)!, n: v.length }))
  return order ? order.flatMap((o) => rows.filter((r) => r.label === o)) : rows.sort((a, b) => b.median - a.median)
}

function DotPlot({ rows }: { rows: Row[] }) {
  const [hover, setHover] = useState<string | null>(null)
  if (!rows.length) return <p className="text-sm text-muted">Not enough rated places yet.</p>
  const lo = Math.max(0, Math.floor(Math.min(...rows.map((r) => r.median)) - 0.5))
  const hi = 10
  const pct = (v: number) => ((v - lo) / (hi - lo)) * 100
  return (
    <div>
      <ul className="space-y-1">
        {rows.map((r) => (
          <li
            key={r.label}
            onPointerEnter={() => setHover(r.label)}
            onPointerLeave={() => setHover(null)}
            className="grid grid-cols-[8.5rem_1fr_3rem] items-center gap-3 rounded-md py-1 text-sm hover:bg-panel"
          >
            <span className="truncate pl-1">{r.label}</span>
            <span className="relative h-4">
              <span className="absolute inset-x-0 top-1/2 h-px" style={{ background: GRID }} />
              <span className="absolute top-1/2 h-px" style={{ left: 0, width: `${pct(r.median)}%`, background: '#cfe0e2' }} />
              <span
                className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-white"
                style={{ left: `${pct(r.median)}%`, background: hover === r.label ? BAR_HOVER : BAR }}
              />
              {/* Floats above the row so hovering never changes the layout. */}
              {hover === r.label && (
                <span
                  className="pointer-events-none absolute bottom-full z-10 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-lg bg-white px-2.5 py-1.5 text-xs shadow-[0_2px_10px_rgb(0_0_0/0.15)]"
                  style={{ left: `${pct(r.median)}%` }}
                >
                  <span className="block font-bold tabular-nums">{r.median.toFixed(2)} median</span>
                  <span className="block text-muted tabular-nums">{r.n.toLocaleString()} rated place{r.n === 1 ? '' : 's'}</span>
                </span>
              )}
            </span>
            <span className="text-right font-semibold tabular-nums">{r.median.toFixed(2)}</span>
          </li>
        ))}
      </ul>
      <div className="mt-1 grid grid-cols-[8.5rem_1fr_3rem] gap-3 text-[11px] text-muted tabular-nums">
        <span />
        <span className="flex justify-between"><span>{lo.toFixed(0)}</span><span>{hi.toFixed(0)}</span></span>
        <span />
      </div>
    </div>
  )
}
