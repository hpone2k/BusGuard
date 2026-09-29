export const SEATS = Object.freeze([
  ...[4.6, 2.16, 1.23].flatMap((x, row) => [-.96, -.47].map((z, col) =>
    Object.freeze({id: `P${row * 2 + col + 1}`, type: 'priority', x, z, y: .58}))),
  ...[-1.13, -2.04, -2.95, -3.86, -4.77].flatMap((x, row) => [-.96, -.47, .47, .96].map((z, col) =>
    Object.freeze({id: `S${String(row * 4 + col + 1).padStart(2, '0')}`, type: 'standard', x, z, y: x < -2.4 ? .88 : .58})))
]);

export const SCENARIOS = Object.freeze({
  wheelchair: {label: 'Wheelchair user', source: 'Exterior CCTV', ramp: true, door: 'middle', seat: null, description: 'A clear path from the kerb to the wheelchair bay.'},
  pram: {label: 'Parent with pram', source: 'Exterior CCTV', ramp: true, door: 'middle', seat: null, description: 'Step-free boarding, with extra time to settle in.'},
  senior: {label: 'Senior · RFID', source: 'Concession RFID', ramp: false, door: 'front', seat: 'P2', description: 'A concession-card tap requests more boarding time.'},
  walking: {label: 'Walking aid', source: 'Exterior CCTV', ramp: true, door: 'middle', seat: 'P4', description: 'A gentler entry and a nearby priority seat.'}
});

export const clamp = (value, min = 0, max = 1) => Math.max(min, Math.min(max, value));
export const smooth = value => { const t = clamp(value); return t * t * (3 - 2 * t); };

// Deterministic source-time state: pause, seek and reset never leave delayed actions running.
// These are presentation states only; no hardware commands or visual age classification.
export function boardingState(scenario = 'wheelchair', elapsed = 0, extraTime = 12) {
  if (!(scenario in SCENARIOS)) throw new Error('Unknown boarding scenario');
  const spec = SCENARIOS[scenario];
  const extra = clamp(Number.isFinite(extraTime) ? extraTime : 12, 8, 24);
  const boardingEnd = 8 + extra;
  const total = boardingEnd + 5;
  const t = clamp(Number.isFinite(elapsed) ? elapsed : 0, 0, total);
  const phase = t < 2 ? 'approach' : t < 4 ? 'recognized' : t < 8 ? 'preparing' : t < boardingEnd ? 'boarding' : t < boardingEnd + 3 ? 'securing' : t < total ? 'closing' : 'complete';
  const doors = smooth((t - 3.5) / 1.8) * (1 - smooth((t - boardingEnd - 3) / 2));
  const ramp = spec.ramp ? smooth((t - 5.5) / 2.5) * (1 - smooth((t - boardingEnd) / 2.5)) : 0;
  return {t, total, phase, doors, ramp, approach: smooth(t / 2), boarding: smooth((t - 8) / extra),
    detected: t >= 2, settled: t >= boardingEnd, extra, remaining: Math.max(0, boardingEnd - t),
    seat: t >= boardingEnd ? spec.seat : null, complete: t >= total};
}

export const PHASE_TEXT = {
  approach: ['Passenger approaching', 'Watching the boarding area'],
  recognized: ['Assistance requested', 'Allowing extra time at the stop'],
  preparing: ['Preparing a clear entry', 'Doors opening · bus remains stationary'],
  boarding: ['Boarding in progress', 'Keeping the doors open while the passenger boards'],
  securing: ['Passenger safely aboard', 'Clearing the doorway and stowing the ramp'],
  closing: ['Closing the doors', 'Completing the boarding sequence'],
  complete: ['Boarding sequence complete', 'Doors closed · checking seated passengers before departure']
};
