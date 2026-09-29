export const REMINDER_KEY = 'busguard.passenger.arrival-reminder.v1';

export function vibrateArrival({enabled, active, navigator: device = globalThis.navigator} = {}) {
  if (!enabled || !active) return {attempted: false, supported: typeof device?.vibrate === 'function'};
  if (typeof device?.vibrate !== 'function') return {attempted: false, supported: false};
  try { return {attempted: true, supported: true, accepted: device.vibrate([220, 100, 220, 100, 400]) === true}; }
  catch { return {attempted: true, supported: true, accepted: false}; }
}

export function parseReminder(raw, stops = []) {
  try { if (typeof raw === 'string') raw = JSON.parse(raw); } catch { return null; }
  if (raw?.enabled !== true || !stops.some(stop => stop.id === raw.stop_id)) return null;
  return {enabled: true, stop_id: raw.stop_id,
    after_id: typeof raw.after_id === 'string' ? raw.after_id.slice(0, 160) : ''};
}

export function createReminder(stopId, stops, route) {
  if (!stops?.some(stop => stop.id === stopId)) throw new Error('Choose one of the five configured bus stops.');
  const id = route?.arrival_event?.id;
  return {enabled: true, stop_id: stopId, after_id: id == null ? '' : String(id).slice(0, 160)};
}

// A reminder watches its chosen stop independently of the passenger's demo
// location. It never submits an assistance request or extends a boarding window.
export function reminderArrival(reminder, route, fresh = true) {
  if (!fresh || !reminder?.enabled || route?.simulated !== true || route.phase !== 'at_stop') return null;
  const event = route.arrival_event;
  if (!event || !['string', 'number'].includes(typeof event.id) || !String(event.id)
      || String(event.id) === reminder.after_id || event.stop_id !== reminder.stop_id
      || route.current_stop_id !== reminder.stop_id) return null;
  return event;
}
