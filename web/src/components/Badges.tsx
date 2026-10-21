import { Flame } from 'lucide-react'
import type { Place } from '../lib/api'

const MICHELIN_RED = '#c8102e'

// The guide's star: five rounded petals around a hole.
export function MichelinStar({ className = 'size-3' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden fill="currentColor">
      <path
        fillRule="evenodd"
        d="M12 1.6c1.5 0 2.6 1.3 2.4 2.8l-.3 2.2 1.8-1.3c1.2-.9 2.9-.6 3.7.7.8 1.3.3 2.9-1 3.6l-2 1 2 1c1.3.7 1.8 2.3 1 3.6-.8 1.3-2.5 1.6-3.7.7l-1.8-1.3.3 2.2c.2 1.5-.9 2.8-2.4 2.8s-2.6-1.3-2.4-2.8l.3-2.2-1.8 1.3c-1.2.9-2.9.6-3.7-.7-.8-1.3-.3-2.9 1-3.6l2-1-2-1c-1.3-.7-1.8-2.3-1-3.6.8-1.3 2.5-1.6 3.7-.7l1.8 1.3-.3-2.2C9.4 2.9 10.5 1.6 12 1.6zm0 7.6a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z"
      />
    </svg>
  )
}

// Bib Gourmand: the Michelin Man's face, licking his lips.
export function BibFace({ className = 'size-3.5' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M5 9.5c0-3.6 3.1-6.5 7-6.5s7 2.9 7 6.5c0 1.2-.4 2.3-1 3.3.6.8 1 1.8 1 2.9 0 3.1-3.1 5.3-7 5.3s-7-2.2-7-5.3c0-1.1.4-2.1 1-2.9-.6-1-1-2.1-1-3.3z" />
      <path d="M9 9.2v.6M15 9.2v.6" strokeWidth="2.4" />
      <path d="M8.5 14c1 1.6 2.2 2.4 3.5 2.4s2.5-.8 3.5-2.4" />
      <path d="M13.6 15.6c.4.8 1.2 1.1 1.8.8" strokeWidth="1.6" />
    </svg>
  )
}

const chip = 'inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold'

export function MichelinBadge({ award, compact = false }: { award: Place['michelin']; compact?: boolean }) {
  if (award == null) return null
  if (award === 'bib') {
    return (
      <span className={`${chip} bg-[#fde8eb]`} style={{ color: MICHELIN_RED }} title="MICHELIN Guide Singapore 2026: Bib Gourmand">
        <BibFace /> {compact ? 'Bib' : 'Bib Gourmand'}
      </span>
    )
  }
  return (
    <span className={`${chip} bg-[#fde8eb]`} style={{ color: MICHELIN_RED }} title={`MICHELIN Guide Singapore 2026: ${award} star${award > 1 ? 's' : ''}`}>
      <span className="inline-flex">
        {Array.from({ length: award }, (_, i) => <MichelinStar key={i} />)}
      </span>
      {!compact && 'MICHELIN'}
    </span>
  )
}

export function PopularBadge({ rank }: { rank: number | null | undefined }) {
  if (rank == null) return null
  return (
    <span className={`${chip} bg-[#fdf1dc] text-[#8a5a00]`} title={`#${rank} on Beli's popular-in-Singapore list`}>
      <Flame className="size-3" strokeWidth={2.5} /> Popular in SG #{rank}
    </span>
  )
}

// The place-level distinctions, in the order they matter: Michelin, then Beli's popular rank.
export default function Badges({ place, hidePopular = false, compact = false }: { place: Place; hidePopular?: boolean; compact?: boolean }) {
  return (
    <>
      <MichelinBadge award={place.michelin} compact={compact} />
      {!hidePopular && <PopularBadge rank={place.popular_rank} />}
    </>
  )
}
