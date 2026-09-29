// Virtual departure interlock. This never commands a vehicle or proves that
// passengers outside the camera's coverage are seated.
export const DEPARTURE_CONFIRM_MS = 3000;
export const DEPARTURE_MAX_GAP_MS = 1000;
export const DEPARTURE_FRESH_MS = 1000;
const CLOSED_EPSILON = .001;
const MAX_OCCUPANTS = 300;
const MAX_EXPECTED = 600;
const finite = value => typeof value === 'number' && Number.isFinite(value);
const count = value => Number.isInteger(value) && value >= 0 && value <= MAX_OCCUPANTS ? value : null;
const frameId = value => Number.isSafeInteger(value) && value >= 0;
const trackKey = value => Number.isSafeInteger(value) && value >= 0 ? String(value)
  : typeof value === 'string' && value.trim() && value.length <= 128 ? value.trim() : null;
const emptyCounts = () => ({people: 0, seated: 0, standing: 0, unknown: 0});

function occupants(summary) {
  const rows = Array.isArray(summary?.occupants) ? summary.occupants : [];
  const byId = new Map(); const totals = emptyCounts(); let valid = Array.isArray(summary?.occupants) && rows.length <= MAX_OCCUPANTS;
  for (const row of rows.slice(0, MAX_OCCUPANTS)) {
    const id = trackKey(row?.track_id), posture = row?.posture;
    if (id == null || byId.has(id) || !['seated', 'standing', 'unknown'].includes(posture)) valid = false;
    if (id != null && !byId.has(id)) byId.set(id, posture);
    if (['seated', 'standing', 'unknown'].includes(posture)) totals[posture]++;
    else totals.unknown++;
  }
  totals.people = rows.length;
  const declared = Object.fromEntries(Object.keys(totals).map(key => [key, count(summary?.[key])]));
  valid &&= Object.keys(totals).every(key => declared[key] !== null && declared[key] === totals[key]);
  return {byId, totals, declared, valid};
}

export class DepartureState {
  constructor({allowDemo = false} = {}) { this.allowDemo = allowDemo === true; this.reset(); }

  reset() {
    this.closed = false; this.closedAt = null; this.session = null;
    this.lastFrame = null; this.lastCapture = null; this.packet = null;
    this.expected = new Set(); this.rosterOverflow = false;
    this.clearConfirmation();
  }

  clearConfirmation() {
    this.progressMs = 0; this.confirmedFrames = 0;
    this.confirmedFrame = null; this.confirmedCapture = null; this.confirmedRoster = null;
  }

  remember(byId) {
    for (const id of byId.keys()) {
      if (this.expected.has(id)) continue;
      if (this.expected.size >= MAX_EXPECTED) { this.rosterOverflow = true; break; }
      this.expected.add(id);
    }
  }

  result(status, message, counts = emptyCounts()) {
    return {status, canDepart: status === 'ready', alert: status === 'standing' || status === 'unknown',
      message, ...counts, progressMs: Math.min(DEPARTURE_CONFIRM_MS, this.progressMs)};
  }

  hold(status, message, counts) {
    this.clearConfirmation();
    return this.result(status, message, counts);
  }

  update({doors, ramp, assistance = false, source = null, online = true} = {}, now = performance.now()) {
    const assisted = assistance === true || assistance?.active === true;
    const shut = finite(doors) && doors >= 0 && doors <= CLOSED_EPSILON
      && finite(ramp) && ramp >= 0 && ramp <= CLOSED_EPSILON && !assisted;
    if (!shut) {
      this.reset();
      return this.result('boarding', assisted ? 'Assistance is active. Departure is held.'
        : 'Waiting for both doors to close and the ramp to stow.');
    }
    if (!finite(now)) return this.hold('unknown', 'The observation clock is unavailable. Departure is held.');

    const identityValid = source?.kind === 'live' && source.role === 'inside' && (!source.is_demo || this.allowDemo);
    const session = identityValid && typeof source?.session_id === 'string' && source.session_id ? source.session_id : null;
    // Missing data is a hold, not a new camera. Preserve the known passenger
    // roster across network loss and unavailable snapshots.
    const newCycle = !this.closed || session !== null && session !== this.session;
    if (newCycle) {
      this.reset(); this.closed = true; this.closedAt = now; this.session = session;
    }
    const fallback = {people: this.expected.size, seated: 0, standing: 0, unknown: this.expected.size};
    if (!online || !source?.connected || source.kind !== 'live' || source.is_demo && !this.allowDemo
        || source.role !== 'inside' || session === null) {
      return this.hold('unknown', 'Fresh live observations from the indoor camera are required.', fallback);
    }

    const summary = source.seating_summary;
    const capture = summary?.captured_at_ms;
    if (!frameId(source.frame_id) || !finite(capture) || capture < 0
        || !finite(source.age_ms) || source.age_ms < 0
        || !finite(summary?.age_ms) || summary.age_ms < 0) {
      return this.hold('unknown', 'Indoor seating evidence is unavailable or incomplete.', fallback);
    }
    const id = source.frame_id;
    const isNew = this.lastFrame === null || id > this.lastFrame;
    if (this.lastFrame !== null && (id < this.lastFrame || id === this.lastFrame && capture !== this.lastCapture
        || isNew && capture <= this.lastCapture)) {
      return this.hold('unknown', 'Waiting for a new indoor observation in timestamp order.', fallback);
    }
    if (isNew) {
      this.lastFrame = id; this.lastCapture = capture;
      this.packet = {age: Math.max(source.age_ms, summary.age_ms), checkedAt: now};
    }
    // Repeated polls cannot make an old frame younger, including callers that
    // repeatedly pass the same unchanged source with age_ms = 0.
    const elapsed = Math.max(0, now - this.packet.checkedAt);
    const age = Math.max(this.packet.age + elapsed, source.age_ms, summary.age_ms);
    this.packet = {age, checkedAt: Math.max(now, this.packet.checkedAt)};

    const observed = occupants(summary);
    this.remember(observed.byId);
    const missing = [...this.expected].filter(key => !observed.byId.has(key)).length;
    const counts = {
      people: Math.max(observed.declared.people ?? observed.totals.people, this.expected.size),
      seated: observed.declared.seated ?? observed.totals.seated,
      standing: Math.max(observed.declared.standing ?? 0, observed.totals.standing),
      unknown: Math.max(observed.declared.unknown ?? 0, observed.totals.unknown) + missing,
    };
    if (age >= DEPARTURE_FRESH_MS || summary.status === 'stale') {
      return this.hold('unknown', 'Indoor seating evidence is stale. Departure is held.',
        {people: this.expected.size, seated: 0, standing: 0, unknown: this.expected.size});
    }
    if (summary.status !== 'observed') {
      return this.hold('unknown', 'The indoor camera has not provided usable seating evidence.',
        {people: this.expected.size, seated: 0, standing: 0, unknown: this.expected.size});
    }
    if (counts.standing > 0) {
      return this.hold('standing', `${counts.standing} ${counts.standing === 1 ? 'passenger is' : 'passengers are'} standing. Departure is held.`, counts);
    }
    if (missing || this.rosterOverflow) {
      return this.hold('unknown', 'A previously observed passenger is no longer accounted for. Departure is held.', counts);
    }
    if (!observed.valid || summary.complete !== true || counts.unknown > 0
        || counts.people <= 0 || counts.seated !== counts.people) {
      return this.hold('unknown', counts.people <= 0
        ? 'An empty camera view does not confirm an empty bus. Departure is held.'
        : 'Every observed passenger must have a confirmed seated posture.', counts);
    }
    if (newCycle || now - age <= this.closedAt) {
      return this.hold('unknown', 'Waiting for new indoor observations after the doors have closed.', counts);
    }
    if (!isNew) {
      if (this.confirmedFrame !== id) return this.hold('unknown', 'Waiting for a new complete seating observation.', counts);
      return this.result(this.progressMs >= DEPARTURE_CONFIRM_MS ? 'ready' : 'confirming',
        this.progressMs >= DEPARTURE_CONFIRM_MS ? 'Observed passengers are seated. Virtual departure is permitted.'
          : 'Confirming that all observed passengers remain seated.', counts);
    }

    const roster = [...observed.byId.keys()].sort().join('\u0000');
    const gap = this.confirmedCapture === null ? null : capture - this.confirmedCapture;
    if (gap !== null && gap > 0 && gap <= DEPARTURE_MAX_GAP_MS && this.confirmedRoster === roster) {
      this.progressMs += gap; this.confirmedFrames++;
    } else {
      this.progressMs = 0; this.confirmedFrames = 1;
    }
    this.confirmedFrame = id; this.confirmedCapture = capture; this.confirmedRoster = roster;
    const ready = this.progressMs >= DEPARTURE_CONFIRM_MS && this.confirmedFrames >= 2;
    return this.result(ready ? 'ready' : 'confirming', ready
      ? 'Observed passengers are seated. Virtual departure is permitted.'
      : 'Confirming that all observed passengers remain seated.', counts);
  }
}
