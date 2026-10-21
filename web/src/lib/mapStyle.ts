import type { LayerSpecification, StyleSpecification } from 'maplibre-gl'

// OpenFreeMap's Liberty style (free, keyless vector tiles), repainted in Google Maps' palette.
// We rewrite paint by layer id rather than ship a fork, so upstream fixes keep flowing in.
const STYLE_URL = 'https://tiles.openfreemap.org/styles/liberty'

const G = {
  land: '#f6f6f4',
  urban: '#eff0f2',
  park: '#c0ead0',
  wood: '#afe0bf',
  sand: '#f4ecd6',
  water: '#90daee',
  hospital: '#fbe5e4',
  school: '#eceff7',
  airport: '#e6e8ef',
  runway: '#d3d6de',
  building: '#e9eaee',
  buildingLine: '#dcdee3',
  road: '#ffffff',
  tunnel: '#f3f4f6',
  path: '#e2e4e8',
  motorway: '#a9b8d0',
  motorwayTunnel: '#c9d2e0',
  casing: { motorway: '#8d9db7', major: '#c3c9d1', mid: '#d4d7dc', minor: '#e0e2e6' },
  rail: '#c4c8ce',
  boundary: '#9aa0a6',
  text: '#5f6368',
  textStrong: '#3c4043',
  waterText: '#3f86ad',
}

const HIDE = /^(natural_earth|building-3d|park_outline|road_one_way_arrow.*|highway-shield-us-interstate|road_shield_us|road_area_pattern|landcover_wetland|landcover_ice|boundary_3)$/

const FILLS: Record<string, string> = {
  park: G.park,
  landuse_residential: G.urban,
  landcover_wood: G.wood,
  landcover_grass: G.park,
  landuse_pitch: G.park,
  landuse_track: G.park,
  landuse_cemetery: '#d3ecd9',
  landuse_hospital: G.hospital,
  landuse_school: G.school,
  water: G.water,
  landcover_sand: G.sand,
  aeroway_fill: G.airport,
}

type Paint = Record<string, unknown>
const paint = (l: LayerSpecification, p: Paint) => {
  ;(l as { paint?: Paint }).paint = { ...((l as { paint?: Paint }).paint ?? {}), ...p }
}

function casingColor(id: string) {
  if (id.includes('motorway')) return G.casing.motorway
  if (id.includes('trunk_primary')) return G.casing.major
  if (id.includes('secondary_tertiary')) return G.casing.mid
  return G.casing.minor
}

function repaint(l: LayerSpecification) {
  const id = l.id
  if (HIDE.test(id)) {
    l.layout = { ...l.layout, visibility: 'none' }
    return
  }
  // Google shows MRT stations but not every bus stop.
  if (id === 'poi_transit') (l as { filter?: unknown }).filter = ['==', ['get', 'class'], 'rail']
  // At street zoom Google drops the country and capital labels that would sit under our pins.
  if (/^label_(country|city_capital)/.test(id)) l.maxzoom = 11
  if (id === 'background') return paint(l, { 'background-color': G.land })
  if (FILLS[id]) return paint(l, { 'fill-color': FILLS[id], 'fill-opacity': id === 'landuse_residential' ? 0.7 : 1, ...(id === 'park' && { 'fill-outline-color': G.park }) })
  if (id.startsWith('waterway') && l.type === 'line') return paint(l, { 'line-color': G.water })
  if (id.startsWith('aeroway_')) return paint(l, { 'line-color': G.runway })
  if (id === 'building') return paint(l, { 'fill-color': G.building, 'fill-outline-color': G.buildingLine })
  if (id.startsWith('boundary')) return paint(l, { 'line-color': G.boundary })

  if (l.type === 'line' && /^(road|bridge|tunnel)_/.test(id)) {
    if (/rail/.test(id)) return paint(l, { 'line-color': G.rail })
    if (/path_pedestrian/.test(id)) return paint(l, { 'line-color': G.path })
    if (id.includes('casing')) return paint(l, { 'line-color': casingColor(id) })
    const tunnel = id.startsWith('tunnel')
    if (id.includes('motorway')) return paint(l, { 'line-color': tunnel ? G.motorwayTunnel : G.motorway })
    return paint(l, { 'line-color': tunnel ? G.tunnel : G.road })
  }

  if (l.type === 'symbol') {
    const halo = { 'text-halo-color': '#ffffff', 'text-halo-width': 1.4, 'text-halo-blur': 0 }
    if (id.startsWith('water')) return paint(l, { 'text-color': G.waterText, ...halo })
    if (id.startsWith('poi')) return paint(l, { 'text-color': '#70757a', 'icon-opacity': 0.65, ...halo })
    if (id.startsWith('highway-name')) return paint(l, { 'text-color': G.text, ...halo })
    if (/^label_(city|town|country|state)/.test(id)) return paint(l, { 'text-color': G.textStrong, ...halo })
    if (id.startsWith('label') || id === 'airport') return paint(l, { 'text-color': G.text, ...halo })
  }
}

export async function googleStyle(): Promise<StyleSpecification> {
  const r = await fetch(STYLE_URL)
  if (!r.ok) throw new Error(`map style: HTTP ${r.status}`)
  const style: StyleSpecification = await r.json()
  style.layers.forEach(repaint)
  return style
}
