// A closed loop of [lng, lat] points, as drawn on the map.
export type Ring = [number, number][]

export function bbox(ring: Ring) {
  let [minX, minY, maxX, maxY] = [Infinity, Infinity, -Infinity, -Infinity]
  for (const [x, y] of ring) {
    if (x < minX) minX = x
    if (x > maxX) maxX = x
    if (y < minY) minY = y
    if (y > maxY) maxY = y
  }
  return { minX, minY, maxX, maxY }
}

// Ray casting. At Singapore's scale lng/lat are flat enough to treat as plane coordinates.
export function contains(ring: Ring, [x, y]: [number, number]) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

// Every place whose pin falls inside the ring; the bounding box check skips almost all of them cheaply.
export function within<T extends { lat: number | null; lng: number | null }>(places: T[], ring: Ring) {
  const b = bbox(ring)
  return places.filter((p) =>
    p.lat != null && p.lng != null &&
    p.lng >= b.minX && p.lng <= b.maxX && p.lat >= b.minY && p.lat <= b.maxY &&
    contains(ring, [p.lng, p.lat]))
}

const KEY = 'savor.area'
export function loadArea(): Ring | null {
  try {
    const r = JSON.parse(localStorage.getItem(KEY) ?? 'null')
    return Array.isArray(r) && r.length >= 3 ? r : null
  } catch {
    return null
  }
}
export function saveArea(r: Ring | null) {
  try {
    if (r) localStorage.setItem(KEY, JSON.stringify(r))
    else localStorage.removeItem(KEY)
  } catch { /* private mode */ }
}
