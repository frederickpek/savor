import type { ReactNode } from 'react'

// The teal cover band with a white card overlapping it, shared by every panel view so switching
// between picks and the catalogue keeps the same top.
export default function PanelHeader({ title, subtitle, leading, children }: {
  title: string
  subtitle?: ReactNode
  leading?: ReactNode
  children?: ReactNode
}) {
  return (
    <header className="relative">
      <div className="h-3 bg-teal bg-[radial-gradient(circle_at_85%_-20%,var(--teal-bright),transparent_60%)] lg:h-28" />
      <div className="relative mx-5 mt-4 rounded-2xl bg-white p-6 shadow-[0_4px_24px_-6px_rgb(0_0_0/0.18)] lg:-mt-16">
        <div className="flex items-start gap-3">
          {leading}
          <div className="min-w-0 flex-1">
            <h1 className="text-[32px] font-extrabold leading-tight tracking-tight">{title}</h1>
            {subtitle && <p className="mt-1 text-[15px] text-muted">{subtitle}</p>}
          </div>
        </div>
        {children}
      </div>
    </header>
  )
}
