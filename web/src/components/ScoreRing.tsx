// Beli's score circle: the ring colour says good / fair / poor at a glance.
export const scoreTone = (s: number) => (s >= 6.7 ? 'var(--good)' : s >= 3.4 ? 'var(--fair)' : 'var(--poor)')

export default function ScoreRing({ score, count, size = 40 }: { score: number | null; count?: number | null; size?: number }) {
  if (score == null) {
    return (
      <span
        className="grid shrink-0 place-items-center rounded-full border-2 border-dashed border-line text-xs text-faint"
        style={{ width: size, height: size }}
        title="No Beli score yet"
      >
        –
      </span>
    )
  }
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full bg-white font-bold tracking-tight"
      style={{ width: size, height: size, border: `2.5px solid ${scoreTone(score)}`, color: scoreTone(score), fontSize: size * 0.36 }}
      title={`Beli ${score.toFixed(1)}${count ? ` from ${count.toLocaleString()} ratings` : ''}`}
    >
      {score.toFixed(1)}
    </span>
  )
}
