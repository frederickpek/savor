import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import type { FeatureCollection } from 'geojson'
import { useEffect, useRef, useState } from 'react'
import { type Pick, type Place, mapsUrl, price, ratingsLabel } from '../lib/api'
import { googleStyle } from '../lib/mapStyle'
import { scoreTone } from './ScoreRing'

// v6 resolves its worker next to the main bundle; Vite inlines that away, so point at the emitted asset.
maplibregl.setWorkerUrl(workerUrl)

const SG: [number, number] = [103.8198, 1.3521]
const SG_BOUNDS: [[number, number], [number, number]] = [[103.5, 1.13], [104.15, 1.5]]
const TEAL = '#134f5c'
const TEAL_BRIGHT = '#1d7383'

export type Origin = { label: string; lat: number; lng: number }

type Props = {
  picks: Pick[]
  // Every other place to show, drawn as one WebGL circle layer so the whole catalogue (~18k) stays fast.
  dots: Place[]
  selected: number | null
  onSelect: (id: number) => void
  origin: Origin | null
  // What the camera frames when the inputs change.
  fitTo: 'picks' | 'dots'
}

// lucide ArrowUpRight, inlined because popups are HTML strings.
const EXTERNAL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 7h10v10"/><path d="M7 17 17 7"/></svg>'

// Same petal star as Badges.tsx, inlined because popups are HTML strings.
const STAR = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M12 1.6c1.5 0 2.6 1.3 2.4 2.8l-.3 2.2 1.8-1.3c1.2-.9 2.9-.6 3.7.7.8 1.3.3 2.9-1 3.6l-2 1 2 1c1.3.7 1.8 2.3 1 3.6-.8 1.3-2.5 1.6-3.7.7l-1.8-1.3.3 2.2c.2 1.5-.9 2.8-2.4 2.8s-2.6-1.3-2.4-2.8l.3-2.2-1.8 1.3c-1.2.9-2.9.6-3.7-.7-.8-1.3-.3-2.9 1-3.6l2-1-2-1c-1.3-.7-1.8-2.3-1-3.6.8-1.3 2.5-1.6 3.7-.7l1.8 1.3-.3-2.2C9.4 2.9 10.5 1.6 12 1.6zm0 7.6a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z"/></svg>'

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)

function popupHtml(p: Place) {
  const count = ratingsLabel(p)
  const stars = typeof p.michelin === 'number' ? STAR.repeat(p.michelin) : ''
  const award = p.michelin === 'bib' ? 'MICHELIN Bib Gourmand' : p.michelin ? `MICHELIN ${p.michelin} star${p.michelin > 1 ? 's' : ''}` : null
  const meta = [price(p.price), p.cuisines.slice(0, 2).join(', '), p.neighborhood, count].filter(Boolean).map((s) => esc(String(s))).join(' · ')
  const score = p.beli_score != null
    ? `<span style="color:${scoreTone(p.beli_score)};border:2px solid currentColor" class="grid size-8 shrink-0 place-items-center rounded-full text-[11px] font-bold">${p.beli_score.toFixed(1)}</span>`
    : ''
  return `<div class="flex items-start gap-3 pr-5">
    <div class="min-w-0 flex-1">
      <div class="text-[15px] font-bold leading-tight text-ink">${esc(p.name)}</div>
      <div class="mt-0.5 text-[13px] text-muted">${meta}</div>
      ${award ? `<div class="mt-1 inline-flex items-center gap-1 text-[12px] font-semibold" style="color:#c8102e">${stars}${esc(award)}</div>` : ''}
      ${p.popular_rank != null ? `<div class="mt-0.5 text-[12px] font-semibold text-[#8a5a00]">Popular in SG #${p.popular_rank}</div>` : ''}
      <a class="mt-2 inline-flex items-center gap-1 text-[13px] font-semibold text-teal hover:underline" href="${esc(mapsUrl(p))}" target="_blank" rel="noreferrer">Open in Google Maps${EXTERNAL}</a>
    </div>${score}</div>`
}

function dotsGeoJSON(dots: Place[], skip: Set<number>): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: dots
      .filter((p) => p.lat != null && p.lng != null && !skip.has(p.business_id))
      .map((p) => ({ type: 'Feature', id: p.business_id, properties: { id: p.business_id }, geometry: { type: 'Point', coordinates: [p.lng!, p.lat!] } })),
  }
}

// MapLibre with keyless vector tiles, styled to read like Google Maps. Picks are DOM pins so they
// share the app's CSS; everything else is a circle layer.
export default function PlaceMap({ picks, dots, selected, onSelect, origin, fitTo }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const pins = useRef(new Map<number, { marker: maplibregl.Marker; node: HTMLElement }>())
  const extras = useRef<maplibregl.Marker[]>([])
  const popup = useRef<maplibregl.Popup | null>(null)
  const byId = useRef(new Map<number, Place>())
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    googleStyle()
      .then((style) => {
        if (cancelled || !el.current) return
        const m = new maplibregl.Map({
          container: el.current,
          style,
          center: SG,
          zoom: 11.5,
          maxBounds: SG_BOUNDS,
          attributionControl: false,
          dragRotate: false,
          pitchWithRotate: false,
        })
        m.touchZoomRotate.disableRotation()
        m.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
        m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
        // Google-style distance gauge under the zoom buttons.
        m.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }), 'bottom-right')
        m.on('load', () => {
          // Compact attribution opens itself on first load; Google keeps it as a small chip.
          el.current?.querySelector('.maplibregl-ctrl-attrib')?.classList.remove('maplibregl-compact-show')
          m.addSource('dots', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
          m.addLayer({
            id: 'dots',
            type: 'circle',
            source: 'dots',
            paint: {
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 2.5, 13, 4.5, 16, 7],
              'circle-color': ['case', ['boolean', ['feature-state', 'selected'], false], TEAL, '#ffffff'],
              'circle-stroke-color': ['case', ['boolean', ['feature-state', 'selected'], false], '#ffffff', TEAL_BRIGHT],
              'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 10, 1.2, 14, 2.5],
            },
          })
          m.on('click', 'dots', (e) => {
            const id = e.features?.[0]?.id
            if (typeof id === 'number') onSelectRef.current(id)
          })
          m.on('mouseenter', 'dots', () => { m.getCanvas().style.cursor = 'pointer' })
          m.on('mouseleave', 'dots', () => { m.getCanvas().style.cursor = '' })
          setReady(true)
        })
        map.current = m
      })
      .catch(() => setFailed(true))
    return () => {
      cancelled = true
      map.current?.remove()
      map.current = null
    }
  }, [])

  // Rebuild pins and the dot layer when the inputs change.
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return
    for (const { marker } of pins.current.values()) marker.remove()
    for (const x of extras.current) x.remove()
    pins.current.clear()
    extras.current = []
    popup.current?.remove()

    byId.current = new Map([...dots, ...picks].map((p) => [p.business_id, p]))
    const pickIds = new Set(picks.map((p) => p.business_id))
    ;(m.getSource('dots') as maplibregl.GeoJSONSource).setData(dotsGeoJSON(dots, pickIds))

    const bounds = new maplibregl.LngLatBounds()
    for (const p of fitTo === 'picks' ? picks : dots) if (p.lat != null && p.lng != null) bounds.extend([p.lng, p.lat])
    const padding = { top: 80, bottom: 80, left: 60, right: 80 }
    // Pixel distances scale with 2^zoom, so measure overlap at the zoom fitBounds will land on.
    const target = bounds.isEmpty() ? undefined : m.cameraForBounds(bounds, { padding, maxZoom: 14.5 })
    const scale = 2 ** ((target?.zoom ?? m.getZoom()) - m.getZoom())
    const placed: maplibregl.Point[] = []
    picks.forEach((p, i) => {
      if (p.lat == null || p.lng == null) return
      const node = document.createElement('button')
      node.className = 'pin-wrap'
      node.setAttribute('aria-label', `${i + 1}. ${p.name}`)
      node.innerHTML = `<div class="pin"><span>${i + 1}</span></div>`
      node.addEventListener('click', (e) => { e.stopPropagation(); onSelectRef.current(p.business_id) })
      // Higher-ranked pins draw on top; pins that would land on an earlier one fan out sideways.
      node.style.zIndex = String(picks.length - i)
      const at = m.project([p.lng, p.lat]).mult(scale)
      const clashes = placed.filter((q) => q.dist(at) < 24).length
      placed.push(at)
      const marker = new maplibregl.Marker({ element: node, anchor: 'bottom', offset: [clashes * 16, -4] }).setLngLat([p.lng, p.lat]).addTo(m)
      pins.current.set(p.business_id, { marker, node: node.firstElementChild as HTMLElement })
    })
    if (origin) {
      const node = document.createElement('div')
      node.className = 'origin'
      node.title = `Distances from ${origin.label}`
      extras.current.push(new maplibregl.Marker({ element: node }).setLngLat([origin.lng, origin.lat]).addTo(m))
    }
    if (!bounds.isEmpty()) m.fitBounds(bounds, { padding, maxZoom: 14.5, duration: 600 })
  }, [picks, dots, origin, ready, fitTo])

  // Selection: raise the pin or fill the dot, open a Google-style info card, and pan it into view.
  useEffect(() => {
    const m = map.current
    if (!m || !ready) return
    popup.current?.remove()
    m.removeFeatureState({ source: 'dots' })
    for (const [id, { node, marker }] of pins.current) {
      node.dataset.selected = String(id === selected)
      const pinEl = marker.getElement()
      pinEl.dataset.z ??= pinEl.style.zIndex
      pinEl.style.zIndex = id === selected ? '100' : pinEl.dataset.z
    }
    const place = selected != null ? byId.current.get(selected) : undefined
    if (!place || place.lat == null || place.lng == null) return
    const isPin = pins.current.has(place.business_id)
    if (!isPin) m.setFeatureState({ source: 'dots', id: place.business_id }, { selected: true })
    const at: [number, number] = [place.lng, place.lat]
    popup.current = new maplibregl.Popup({ offset: isPin ? 40 : 12, maxWidth: '300px', focusAfterOpen: false })
      .setLngLat(at)
      .setHTML(popupHtml(place))
      .addTo(m)
    m.easeTo({ center: at, zoom: Math.max(m.getZoom(), 14), duration: 500 })
  }, [selected, ready, picks, dots])

  return (
    <div className="relative h-full w-full bg-[#f6f6f4]">
      <div ref={el} className="h-full w-full" />
      {failed && (
        <p className="absolute inset-0 grid place-items-center text-sm text-muted">Couldn't load map tiles. Check your connection.</p>
      )}
    </div>
  )
}
