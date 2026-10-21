// The savor seal: Gloock's S (outlined, so it renders without the web font) inside a double ring.
// Gloock ships in a single weight, so the S and the wordmark are thickened with a same-colour stroke.
const S_PATH = 'M235 -14Q209 -14 192.5 -12.0Q176 -10 160.0 -7.0Q144 -4 121.0 -2.0Q98 0 58 0L23 260H35Q50 137 103.5 70.5Q157 4 241 4Q303 4 342.0 39.0Q381 74 381 130Q381 154 372.5 176.0Q364 198 344 223Q324 248 291.0 277.5Q258 307 209 344L168 375Q93 432 64.0 477.0Q35 522 35 575Q35 663 99.5 713.5Q164 764 270 764Q296 764 312.5 762.0Q329 760 344.5 757.0Q360 754 381.0 752.0Q402 750 435 750L445 548H435Q421 641 375.0 695.0Q329 749 264 749Q218 749 190.5 722.0Q163 695 163 650Q163 611 188.5 575.5Q214 540 277 493L320 461Q386 413 424.5 372.5Q463 332 480.0 294.0Q497 256 497 213Q497 147 464.0 96.0Q431 45 372.5 16.5Q314 -12 235 -14Z'

export function Seal({ className = 'size-8' }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={className} aria-hidden>
      <circle cx="24" cy="24" r="21" fill="none" stroke="currentColor" strokeWidth="2.8" />
      <circle cx="24" cy="24" r="16.8" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path fill="currentColor" stroke="currentColor" strokeWidth="26" strokeLinejoin="round"
        transform="translate(16.6478 34.6041) scale(0.02828 -0.02828)" d={S_PATH} />
    </svg>
  )
}

export default function Logo() {
  return (
    <span className="inline-flex items-center gap-2.5 text-teal">
      <Seal className="size-9" />
      <span className="font-display text-[25px] leading-none tracking-[0.2em] [-webkit-text-stroke:0.9px_currentColor] [paint-order:stroke_fill]">SAVOR</span>
    </span>
  )
}
