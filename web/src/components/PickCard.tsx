import { Bookmark, Check } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { type Pick, price, ratingsLabel } from '../lib/api'
import Badges from './Badges'
import PlaceDetails from './PlaceDetails'
import ScoreRing from './ScoreRing'

type Props = { pick: Pick; rank: number; selected: boolean; onSelect: () => void }

// Facts the card already shows elsewhere (score ring, badges, the km in the meta line).
const redundant = (r: string) => /^Beli \d/.test(r) || /^\d+(\.\d)? km from /.test(r) || /on Beli's popular-in-Singapore list$/i.test(r) || /^(\d MICHELIN Stars?|MICHELIN Bib Gourmand)$/.test(r)

export default function PickCard({ pick: p, rank, selected, onSelect }: Props) {
  const ref = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selected])

  return (
    <li ref={ref}>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={selected}
        onClick={onSelect}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect())}
        className={`group flex cursor-pointer gap-3.5 rounded-xl px-3 py-3.5 transition-colors ${selected ? 'bg-teal-softer ring-1 ring-teal-soft' : 'hover:bg-panel'}`}
      >
        <span
          className={`mt-0.5 grid size-7 shrink-0 place-items-center rounded-full text-[13px] font-bold text-white ${selected ? 'bg-teal-hover' : 'bg-teal'}`}
        >
          {rank}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-[17px] font-bold leading-snug">{p.name}</h3>
            <Badges place={p} />
            {p.ranked_score != null && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-panel px-2 py-0.5 text-[11px] font-semibold text-ink/75">
                <Check className="size-3" strokeWidth={2.5} /> Been · {p.ranked_score.toFixed(1)}
              </span>
            )}
            {p.bookmarked && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-teal-soft px-2 py-0.5 text-[11px] font-semibold text-teal">
                <Bookmark className="size-3" strokeWidth={2.5} /> Want to Try
              </span>
            )}
          </div>
          <p className="mt-0.5 truncate text-sm text-muted">
            {[price(p.price), p.cuisines.slice(0, 2).join(', '), p.neighborhood, p.km != null && `${p.km} km`, ratingsLabel(p)]
              .filter(Boolean)
              .join(' · ')}
          </p>
          <ul className="mt-2 space-y-1 text-sm leading-snug text-ink/85">
            {p.reasons.filter((r) => !redundant(r)).map((r) => (
              <li key={r} className="flex gap-2">
                <span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-teal-bright/60" />
                {r}
              </li>
            ))}
          </ul>

          {selected && <PlaceDetails place={p} />}
        </div>
        <ScoreRing score={p.beli_score} count={p.review_count} />
      </div>
    </li>
  )
}
