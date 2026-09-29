export const LOCATION_KEY = 'bustech.passenger.demo-location.v1';
export const ARRIVAL_KEY = 'bustech.passenger.last-arrival.v1';

export function parseLocation(raw, stops = []) {
  let value = raw;
  try { if (typeof raw === 'string') value = JSON.parse(raw); } catch { return {mode: 'away'}; }
  if (value?.mode === 'onboard') return {mode: 'onboard'};
  if (value?.mode === 'at_stop' && typeof value.stop_id === 'string'
      && stops.some(stop => stop.id === value.stop_id)) return {mode: 'at_stop', stop_id: value.stop_id};
  return {mode: 'away'};
}

export function routePresentation(config, state, location, fresh = true) {
  const configured = config?.route?.stops || config?.stops || [];
  // A route diagram shows stop order, not geographic distance or GPS position.
  const stops = configured.map((stop, index) => ({id: stop.id, name: stop.name,
    x: configured.length === 1 ? 260 : 44 + index * 432 / (configured.length - 1),
    y: 64, letter: String.fromCharCode(65 + index)}));
  const route = state?.route;
  const current = stops.find(stop => stop.id === route?.current_stop_id);
  const next = stops.find(stop => stop.id === route?.next_stop_id);
  const known = fresh && route?.simulated === true && current && next
    && ['at_stop', 'travelling', 'approaching'].includes(route.phase)
    && Number.isFinite(route.progress) && route.progress >= 0 && route.progress <= 1;
  const progress = known && route.phase !== 'at_stop' ? route.progress : 0;
  // The return leg is off this straight diagram. Sliding E → A would falsely
  // suggest that the bus visits D, C and B on the way back.
  const wrapping = Boolean(known && route.phase !== 'at_stop' && stops.length > 1
    && current === stops.at(-1) && next === stops[0]);
  const marker = known && !wrapping ? {x: current.x + (next.x - current.x) * progress, y: current.y} : null;
  const selected = stops.find(stop => stop.id === location?.stop_id);
  return {stops, marker, known: Boolean(known), current, next, wrapping,
    motion: known && route.phase !== 'at_stop' ? route.phase : 'still',
    phase: known ? route.phase : 'unavailable',
    title: !known ? 'Checking the route' : route.phase === 'at_stop' ? `At ${current.name}`
      : route.phase === 'approaching' ? `Arriving at ${next.name}`
      : wrapping ? `Returning to ${next.name}` : `On the way to ${next.name}`,
    locationLabel: location?.mode === 'onboard' ? 'You are on the bus' : selected && location?.mode === 'at_stop'
      ? `You are at ${selected.name}` : 'You are away from the stops',
    detail: !known ? 'Your bus location will appear when reconnected.' : route.phase === 'at_stop'
      ? 'Here at your stop? Request help while the doors are open.' : wrapping
        ? `The next loop begins at ${next.name}.`
        : route.phase === 'approaching' ? 'Almost there. Please wait for the doors to open.'
          : 'Follow your bus here. We’ll let you know when it arrives.'};
}

// Seed the first observation silently: opening or refreshing an app must not
// replay an old arrival. Seen IDs are also retained across page reloads.
export class ArrivalTracker {
  constructor(lastId = '') { this.lastId = lastId; this.initialized = false; }
  observe(route, {enabled = false, location, selectedStop, fresh = true} = {}) {
    if (!fresh || !route || route.simulated !== true) return null;
    const event = route.arrival_event;
    const id = typeof event?.id === 'string' || Number.isFinite(event?.id) ? String(event.id) : '';
    if (!this.initialized) { this.initialized = true; if (id) this.lastId = id; return null; }
    if (!id || id === this.lastId) return null;
    this.lastId = id;
    if (!enabled || route.phase !== 'at_stop' || event.stop_id !== route.current_stop_id) return null;
    const watchedStop = location?.mode === 'at_stop' ? location.stop_id : location?.mode === 'onboard' ? selectedStop : null;
    return watchedStop === event.stop_id ? event : null;
  }
}
