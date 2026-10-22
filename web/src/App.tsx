import { BarChart3, CalendarDays, Flame, Layers, List, MapPin, SlidersHorizontal } from 'lucide-react'
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BibFace, MichelinStar } from './components/Badges'
import Catalogue, { type CatalogueMode, type Filters, NO_FILTERS, VIEWS, applyFilters } from './components/Catalogue'
import Logo from './components/Logo'
import PanelHeader from './components/PanelHeader'
import PickCard from './components/PickCard'
import PlaceMap, { type Origin } from './components/PlaceMap'
import Stats from './components/Stats'
import { type Index, type Pick, type Picks, type Place, api, price } from './lib/api'

type View = 'picks' | 'stats' | CatalogueMode

const NONE: Place[] = []
const NO_PICKS: Pick[] = []

// Names (contexts, views, section headings) are Title Case; descriptions and controls stay sentence case.
const titleCase = (s: string) => s.replace(/\b\w/g, (m) => m.toUpperCase())
const label = (c: string) => (c === 'default' ? 'Any Day' : titleCase(c.replace(/-/g, ' ')))
// Picks are weekly, keyed by the Monday of the week.
const weekLabel = (monday: string) => {
  const a = new Date(`${monday}T00:00:00`)
  const b = new Date(a)
  b.setDate(a.getDate() + 6)
  const day = (d: Date, month: boolean) => d.toLocaleDateString('en-SG', month ? { day: 'numeric', month: 'short' } : { day: 'numeric' })
  return `${day(a, a.getMonth() !== b.getMonth())} – ${day(b, true)}`
}

// One-line summary of a context's rules, e.g. "$$$–$$$$ · Beli 8+ · within 8 km".
function contextHint(prefs: Record<string, unknown> | undefined) {
  if (!prefs) return ''
  const num = (k: string) => (typeof prefs[k] === 'number' ? (prefs[k] as number) : null)
  const [lo, hi] = [num('price_min') ?? 1, num('price_max') ?? 4]
  const bits: string[] = []
  if (lo > 1 || hi < 4) bits.push(lo === hi ? price(lo) : lo > 1 && hi === 4 ? `${price(lo)}+` : `${price(lo)}–${price(hi)}`)
  if (num('min_beli_score') != null) bits.push(`Beli ${num('min_beli_score')}+`)
  if (num('max_ratings') != null) bits.push(`under ${num('max_ratings')! + 1} ratings`)
  if (num('min_km') != null) bits.push(`${num('min_km')} km+ away`)
  if (num('max_km') != null) bits.push(`within ${num('max_km')} km`)
  return bits.join(' · ')
}

export default function App() {
  const [index, setIndex] = useState<Index | null>(null)
  const [date, setDate] = useState<string | null>(null)
  const [picks, setPicks] = useState<Picks | null>(null)
  const [places, setPlaces] = useState<Place[]>([])
  const [context, setContext] = useState('default')
  const [selected, setSelected] = useState<number | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [view, setView] = useState<View>('picks')
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)
  const [error, setError] = useState<string | null>(null)
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.index().then((i) => { setIndex(i); setDate(i.latest) }).catch((e) => setError(String(e)))
    api.places().then(setPlaces).catch(() => {})
  }, [])

  useEffect(() => {
    if (date) api.picks(date).then(setPicks).catch((e) => setError(String(e)))
  }, [date])

  const list: Pick[] = useMemo(() => picks?.contexts[context]?.picks ?? [], [picks, context])
  const prefs = picks?.contexts[context]?.prefs
  const pool = picks?.contexts[context]?.pool
  const origin = (prefs?.origin as Origin | undefined) ?? null
  const select = useCallback((id: number) => setSelected((cur) => (cur === id ? null : id)), [])

  // In the catalogue the map shows whatever the filters match; with picks, everything only on request.
  const browsing = view !== 'picks' && view !== 'stats'
  const inView = useMemo(() => (browsing ? places.filter(VIEWS[view as CatalogueMode].keep) : places), [browsing, view, places])
  const filtered = useMemo(() => applyFilters(inView, filters), [inView, filters])
  const count = (mode: CatalogueMode) => places.filter(VIEWS[mode].keep).length
  // Memoised: a fresh [] each render would make the map rebuild its layers and re-pan on every click.
  const dots = useMemo(() => (browsing ? filtered : showAll && view === 'picks' ? places : NONE), [browsing, filtered, showAll, places, view])
  const mapPicks = view === 'picks' ? list : NO_PICKS

  const chooseContext = (c: string) => {
    setContext(c)
    setSelected(null)
    setView('picks')
    scroller.current?.scrollTo({ top: 0, behavior: 'smooth' })
  }
  // Anything that changes what's on the map drops the selection, so the camera stops snapping back
  // to the last place clicked. Panning or zooming the map keeps it.
  const changeFilters = useCallback((f: Filters) => { setSelected(null); setFilters(f) }, [])
  const openView = (v: View) => {
    setSelected(null)
    setView(v)
  }
  const openCatalogue = (mode: CatalogueMode) => {
    setSelected(null)
    setFilters(NO_FILTERS)
    setView(mode)
  }

  if (error) {
    return (
      <main className="grid h-full place-items-center p-8">
        <div className="max-w-md text-center">
          <Logo />
          <p className="mt-4 text-muted">No data yet: {error}. Run the pipeline first; the README has the steps.</p>
        </div>
      </main>
    )
  }

  const contexts = index?.contexts ?? ['default']

  return (
    <div className="flex h-full flex-col lg:flex-row">
      {/* Nav rail */}
      <nav className="scroll-thin flex shrink-0 gap-1 overflow-x-auto border-b border-line bg-white px-3 py-2 lg:w-56 lg:flex-col lg:overflow-y-auto lg:border-r lg:border-b-0 lg:px-3 lg:py-5">
        <div className="hidden px-3 pb-6 lg:block"><Logo /></div>

        <RailHeading>This Week</RailHeading>
        {contexts.map((c) => (
          <RailItem key={c} active={view === 'picks' && c === context} onClick={() => chooseContext(c)} hint={contextHint(picks?.contexts[c]?.prefs)}>
            {label(c)}
          </RailItem>
        ))}

        <RailHeading className="lg:mt-6">Explore</RailHeading>
        <RailItem icon={<Flame className="size-4" />} active={view === 'top200'} onClick={() => openCatalogue('top200')}
          hint="Beli's most popular">
          SG Top 200
        </RailItem>
        <RailItem icon={<MichelinStar className="size-4" />} active={view === 'michelin'} onClick={() => openCatalogue('michelin')}
          hint={places.length ? `${count('michelin')} starred · Guide 2026` : undefined}>
          MICHELIN Stars
        </RailItem>
        <RailItem icon={<BibFace className="size-4" />} active={view === 'bib'} onClick={() => openCatalogue('bib')}
          hint={places.length ? `${count('bib')} good-value picks` : undefined}>
          Bib Gourmand
        </RailItem>
        <RailItem icon={<List className="size-4" />} active={view === 'all'} onClick={() => openCatalogue('all')}
          hint={places.length ? `${places.length.toLocaleString()} in Singapore` : undefined}>
          All Places
        </RailItem>
        <RailItem icon={<BarChart3 className="size-4" />} active={view === 'stats'} onClick={() => openView('stats')}
          hint="Score distribution and more">
          Statistics
        </RailItem>

        {index && (
          <div className="mt-auto hidden space-y-2 px-3 pt-6 text-xs text-muted lg:block">
            {index.dates.length > 1 ? (
              <label className="block">
                <span className="mb-1 block font-semibold text-ink">Earlier Weeks</span>
                <select
                  value={date ?? ''}
                  onChange={(e) => setDate(e.target.value)}
                  className="w-full rounded-lg border border-line bg-white px-2 py-1.5 text-sm text-ink"
                >
                  {[...index.dates].reverse().map((d) => <option key={d} value={d}>Week of {weekLabel(d)}</option>)}
                </select>
              </label>
            ) : null}
            <p>
              Updated {new Date(index.generatedAt).toLocaleString('en-SG', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}
            </p>
            {index.status !== 'ok' && index.status !== 'skipped' && (
              <p className="font-semibold text-poor">Last ingest {index.status}; picks re-ranked from stored data.</p>
            )}
          </div>
        )}
      </nav>

      {/* Content panel: picks, or the catalogue browser, which widens to make room for filters. */}
      <div
        ref={scroller}
        className={`scroll-thin order-last min-h-0 flex-1 bg-white transition-[width] duration-300 ease-out lg:order-none lg:flex-none lg:shadow-[4px_0_16px_-8px_rgb(0_0_0/0.12)] ${view !== 'picks' ? 'overflow-hidden lg:w-[720px] xl:w-[920px]' : 'overflow-y-auto lg:w-[540px] xl:w-[600px]'}`}
      >
        {view === 'stats' ? (
          <Stats places={places} onClose={() => openView('picks')} />
        ) : browsing ? (
          <Catalogue mode={view as CatalogueMode} places={places} filters={filters} onFilters={changeFilters} selected={selected} onSelect={select} onClose={() => { setSelected(null); setView('picks') }} />
        ) : (<>
        <PanelHeader
          title={label(context)}
          subtitle={<>
            {list.length ? `${list.length} places worth trying this week` : picks ? 'Nothing this week' : 'Loading picks…'}
            {pool != null && `, drawn at random from ${pool.toLocaleString()} that fit`}
          </>}
        >
            <div className="mt-5 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm font-medium text-ink/80">
              {date && (
                <span className="inline-flex items-center gap-1.5"><CalendarDays className="size-4 text-teal" />{weekLabel(date)}</span>
              )}
              {origin && (
                <span className="inline-flex items-center gap-1.5"><MapPin className="size-4 text-teal" />From {origin.label}</span>
              )}
              {contextHint(prefs) && (
                <span className="inline-flex items-center gap-1.5"><SlidersHorizontal className="size-4 text-teal" />{contextHint(prefs)}</span>
              )}
            </div>
        </PanelHeader>

        <div className="px-5 pt-4 pb-16">
          <Section id="picks" title="This Week's Picks">
            {!picks ? (
              <Skeleton />
            ) : list.length ? (
              <ol className="-mx-3 space-y-0.5">
                {list.map((p, i) => (
                  <PickCard key={p.business_id} pick={p} rank={i + 1} selected={p.business_id === selected} onSelect={() => select(p.business_id)} />
                ))}
              </ol>
            ) : (
              <p className="text-sm text-muted">Nothing fits this context this week. Loosen its rules in prefs.json.</p>
            )}
          </Section>
        </div>
        </>)}
      </div>

      {/* Map */}
      <div className="relative h-[45vh] shrink-0 lg:h-auto lg:flex-1">
        <PlaceMap
          picks={mapPicks}
          dots={dots}
          selected={selected}
          onSelect={select}
          origin={view === 'picks' ? origin : null}
          fitTo={browsing ? 'dots' : 'picks'}
        />
        {view === 'picks' && <button
          onClick={() => { setSelected(null); setShowAll((s) => !s) }}
          aria-pressed={showAll}
          className={`absolute top-4 right-4 inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold shadow-[0_1px_4px_rgb(0_0_0/0.18),0_2px_8px_rgb(0_0_0/0.08)] transition-colors ${showAll ? 'bg-teal text-white hover:bg-teal-hover' : 'bg-white text-ink hover:bg-panel'}`}
        >
          <Layers className="size-4" />
          {showAll ? 'Showing every place' : 'Show every place'}
        </button>}
      </div>
    </div>
  )
}

function RailHeading({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <p className={`hidden px-3 pb-1.5 text-[15px] font-bold lg:block ${className}`}>{children}</p>
}

function RailItem({ children, active, hint, icon, onClick }: { children: ReactNode; active?: boolean; hint?: string; icon?: ReactNode; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-current={active || undefined}
      className={`flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-left whitespace-nowrap transition-colors ${active ? 'bg-teal text-white' : 'text-ink hover:bg-panel'}`}
    >
      {icon && <span className={active ? 'text-white' : 'text-muted'}>{icon}</span>}
      <span className="min-w-0">
        <span className="block text-sm font-semibold">{children}</span>
        {hint && <span className={`hidden text-xs lg:block ${active ? 'text-white/75' : 'text-muted'}`}>{hint}</span>}
      </span>
    </button>
  )
}

function Section({ id, title, subtitle, children }: { id: string; title: string; subtitle?: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-4 py-5">
      <h2 className="text-[22px] font-extrabold tracking-tight">{title}</h2>
      <div className="mt-3">
        {subtitle && <p className="mb-3 text-sm text-muted">{subtitle}</p>}
        {children}
      </div>
    </section>
  )
}

function Skeleton() {
  return (
    <div className="space-y-3">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex animate-pulse gap-3.5 py-2">
          <div className="size-7 rounded-full bg-panel" />
          <div className="flex-1 space-y-2">
            <div className="h-4 w-1/2 rounded bg-panel" />
            <div className="h-3 w-3/4 rounded bg-panel" />
          </div>
        </div>
      ))}
    </div>
  )
}
