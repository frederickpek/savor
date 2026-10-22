import { Globe, Navigation } from 'lucide-react'
import { type Place, mapsUrl, ratingsLabel } from '../lib/api'
import { scoreTone } from './ScoreRing'

// What a card shows once it's selected: directions, the website, and Beli's ratings breakdown.
// Clicks inside don't reach the card, so following a link never collapses it.
export default function PlaceDetails({ place: p }: { place: Place }) {
  return (
    <div className="mt-4 space-y-4" onClick={(e) => e.stopPropagation()}>
      <div className="flex flex-wrap gap-2">
        <a
          href={mapsUrl(p)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 rounded-full bg-teal px-3.5 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-teal-hover"
        >
          <Navigation className="size-3.5" strokeWidth={2.5} /> Directions
        </a>
        {p.website && (
          <a
            href={p.website}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full border border-line bg-white px-3.5 py-1.5 text-sm font-semibold text-ink transition-colors hover:border-teal-bright hover:text-teal"
          >
            <Globe className="size-3.5" strokeWidth={2.5} /> Website
          </a>
        )}
      </div>
      <Breakdown place={p} />
    </div>
  )
}

// Beli's "Ratings Breakdown": the score and count beside 20 half-point bars from 0 to 10. No y
// axis; the bars only show the shape. Without a histogram, says why in one line instead.
function Breakdown({ place: p }: { place: Place }) {
  const h = p.histogram
  if (!h?.length || p.beli_score == null) {
    return <p className="text-sm text-muted">{ratingsLabel(p) ?? 'No ratings breakdown yet'}</p>
  }
  const max = Math.max(...h, 1)
  const total = h.reduce((a, b) => a + b, 0)
  return (
    // Capped so a wide card keeps the chart's proportions.
    <section aria-label="Ratings breakdown" className="max-w-md">
      <h3 className="text-[13px] font-bold">Ratings Breakdown</h3>
      <div className="mt-2 flex items-center gap-4">
        <div className="w-16 shrink-0">
          <div className="text-[30px] font-extrabold leading-none tracking-tight" style={{ color: scoreTone(p.beli_score) }}>
            {p.beli_score.toFixed(1)}
          </div>
          <div className="mt-1 text-xs text-muted">{(p.review_count ?? total).toLocaleString()} ratings</div>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex h-16 items-end gap-[2px]" role="img" aria-label={`Ratings from 0 to 10, most common around ${(h.indexOf(max) * 0.5 + 0.25).toFixed(1)}`}>
            {h.map((n, i) => (
              <div
                key={i}
                // Bars sit at fractional widths, so a 1px gap can blur away; 2px always reads.
                className="flex-1 rounded-t-[1px] bg-teal"
                // Empty buckets keep a hairline so the 0-10 range stays readable.
                style={{ height: n ? `${Math.max(4, (n / max) * 100)}%` : '1px', opacity: n ? 1 : 0.35 }}
                title={`${(i * 0.5).toFixed(1)}–${(i * 0.5 + 0.5).toFixed(1)}: ${n.toLocaleString()} rating${n === 1 ? '' : 's'}`}
              />
            ))}
          </div>
          <div className="mt-1 flex justify-between text-[11px] text-muted tabular-nums">
            <span>0.0</span><span>10.0</span>
          </div>
        </div>
      </div>
    </section>
  )
}
