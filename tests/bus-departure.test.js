import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DepartureState} from '../static/bus/src/departure-state.js';

function source(frame = 0, captured = 0, occupants = [{track_id: 1, posture: 'seated'}], changes = {}) {
  const seating_summary = {status: 'observed', people: occupants.length,
    seated: occupants.filter(p => p.posture === 'seated').length,
    standing: occupants.filter(p => p.posture === 'standing').length,
    unknown: occupants.filter(p => p.posture === 'unknown').length,
    complete: occupants.every(p => p.posture !== 'unknown'), occupants, age_ms: 0, captured_at_ms: captured,
    ...changes.seating_summary};
  return {role: 'inside', kind: 'live', is_demo: false, connected: true, session_id: 'inside-a',
    frame_id: frame, age_ms: 0, ...changes, seating_summary};
}
const update = (state, input, now) => state.update({doors: 0, ramp: 0, online: true, source: input}, now);
function closed(occupants) { const state = new DepartureState(); update(state, source(0, 0, occupants), 0); return state; }
function ready(state = closed(), occupants) {
  update(state, source(1, 100, occupants), 100);
  update(state, source(2, 1100, occupants), 1100);
  update(state, source(3, 2100, occupants), 2100);
  return {state, result: update(state, source(4, 3100, occupants), 3100)};
}

test('departure requires new full seated evidence after closure, not the baseline frame', () => {
  const state = closed();
  assert.equal(update(state, source(0, 0), 500).canDepart, false);
  assert.equal(update(state, source(1, 1000), 1000).progressMs, 0);
  assert.equal(update(state, source(2, 1999), 1999).progressMs, 999);
  assert.equal(update(state, source(3, 2999), 2999).canDepart, false);
  assert.equal(update(state, source(4, 3000), 3000).canDepart, false);
  assert.equal(update(state, source(5, 3999), 3999).canDepart, false);
  const result = update(state, source(6, 4000), 4000);
  assert.equal(result.status, 'ready'); assert.equal(result.canDepart, true);
  assert.equal(result.progressMs, 3000); assert.equal(result.alert, false);
});

test('repeated identical polls never add confirmation and eventually become stale', () => {
  const state = closed(), packet = source(1, 100);
  assert.equal(update(state, packet, 100).progressMs, 0);
  assert.equal(update(state, packet, 600).progressMs, 0);
  assert.equal(update(state, packet, 1099).status, 'confirming');
  const stale = update(state, packet, 1100);
  assert.equal(stale.status, 'unknown'); assert.equal(stale.canDepart, false);
  assert.match(stale.message, /stale/);
  assert.equal(update(state, packet, 1200).progressMs, 0);
});

test('ready state loses permission when its repeated frame exceeds the freshness limit', () => {
  const {state, result} = ready(); assert.equal(result.canDepart, true);
  assert.equal(update(state, source(4, 3100), 4099).canDepart, true);
  const stale = update(state, source(4, 3100), 4100);
  assert.equal(stale.canDepart, false); assert.equal(stale.status, 'unknown');
});

test('standing observation blocks immediately, including during an otherwise ready cycle', () => {
  const {state} = ready();
  const result = update(state, source(5, 3200, [{track_id: 1, posture: 'standing'}]), 3200);
  assert.equal(result.status, 'standing'); assert.equal(result.standing, 1);
  assert.equal(result.canDepart, false); assert.equal(result.alert, true); assert.equal(result.progressMs, 0);
  assert.equal(update(state, source(6, 3300), 3300).progressMs, 0);
});

test('missing standing passenger remains unknown instead of silently leaving the roster', () => {
  const both = [{track_id: 1, posture: 'seated'}, {track_id: 2, posture: 'standing'}];
  const state = closed(both);
  for (const [frame, time] of [[1, 100], [2, 1100], [3, 2100], [4, 3100]]) {
    const result = update(state, source(frame, time), time);
    assert.equal(result.canDepart, false); assert.equal(result.status, 'unknown');
    assert.equal(result.people, 2); assert.equal(result.unknown, 1);
  }
  const seated = both.map(person => ({...person, posture: 'seated'}));
  update(state, source(5, 3200, seated), 3200);
  update(state, source(6, 4200, seated), 4200);
  assert.equal(update(state, source(7, 5200, seated), 5200).canDepart, false);
  assert.equal(update(state, source(8, 6200, seated), 6200).canDepart, true);
});

test('unknown posture, incomplete coverage and mismatched counts cannot release departure', () => {
  const cases = [
    source(1, 100, [{track_id: 1, posture: 'unknown'}]),
    source(1, 100, undefined, {seating_summary: {complete: false}}),
    source(1, 100, undefined, {seating_summary: {people: 2}}),
    source(1, 100, [{track_id: null, posture: 'seated'}]),
    source(1, 100, [{track_id: 1, posture: 'seated'}, {track_id: 1, posture: 'seated'}]),
    source(1, 100, undefined, {seating_summary: {status: 'unavailable'}}),
    source(1, 100, undefined, {seating_summary: {status: 'stale'}}),
  ];
  for (const packet of cases) {
    const result = update(closed(), packet, 100);
    assert.equal(result.status, 'unknown'); assert.equal(result.canDepart, false); assert.equal(result.progressMs, 0);
  }
});

test('zero observed people cannot establish that a cabin is empty', () => {
  const state = closed([]);
  for (const [frame, time] of [[1, 100], [2, 1100], [3, 2100]]) {
    const result = update(state, source(frame, time, []), time);
    assert.equal(result.status, 'unknown'); assert.equal(result.canDepart, false);
    assert.match(result.message, /empty/);
  }
});

test('reordered frames and repeated capture timestamps reset confirmation', () => {
  for (const packet of [source(1, 200), source(2, 100), source(3, 1100)]) {
    const state = closed();
    update(state, source(1, 100), 100); update(state, source(2, 1100), 1100);
    const result = update(state, packet, 1200);
    assert.equal(result.status, 'unknown'); assert.equal(result.progressMs, 0); assert.equal(result.canDepart, false);
    assert.equal(update(state, source(4, 1300), 1300).progressMs, 0);
  }
});

test('gaps beyond one second start a new confirmation period', () => {
  const state = closed();
  update(state, source(1, 100), 100);
  assert.equal(update(state, source(2, 1100), 1100).progressMs, 1000);
  assert.equal(update(state, source(3, 2101), 2101).progressMs, 0);
  update(state, source(4, 3101), 3101);
  assert.equal(update(state, source(5, 4101), 4101).canDepart, false);
  assert.equal(update(state, source(6, 5101), 5101).canDepart, true);
});

test('camera disconnection, unavailable data and stale pose age revoke permission', () => {
  for (const changes of [{connected: false}, {kind: 'image'}, {kind: 'video'}, {is_demo: true}, {role: 'outside'},
    {age_ms: 1000}, {seating_summary: {age_ms: 1000}}, {seating_summary: {captured_at_ms: NaN}}]) {
    const {state} = ready();
    const result = update(state, source(5, 3200, undefined, changes), 3200);
    assert.equal(result.canDepart, false); assert.equal(result.status, 'unknown');
  }
  const {state} = ready();
  assert.equal(state.update({doors: 0, ramp: 0, source: source(5, 3200), online: false}, 3200).canDepart, false);
  assert.equal(update(state, null, 3300).canDepart, false);
});

test('doors, ramp and assistance movement revoke permission and require a new closed cycle', () => {
  for (const change of [{doors: .01}, {ramp: .01}, {assistance: true}, {assistance: {active: true}}, {doors: NaN}]) {
    const {state} = ready();
    const result = state.update({doors: 0, ramp: 0, source: source(5, 3200), ...change}, 3200);
    assert.equal(result.status, 'boarding'); assert.equal(result.canDepart, false);
    assert.equal(update(state, source(6, 3300), 3300).progressMs, 0);
    update(state, source(7, 3400), 3400); update(state, source(8, 4400), 4400);
    assert.equal(update(state, source(9, 5400), 5400).canDepart, false);
    assert.equal(update(state, source(10, 6400), 6400).canDepart, true);
  }
});

test('camera session change resets the roster and cannot reuse old confirmation', () => {
  const {state} = ready();
  const occupant = [{track_id: 99, posture: 'seated'}];
  const next = (id, time) => source(id, time, occupant, {session_id: 'inside-b'});
  assert.equal(update(state, next(0, 0), 3200).canDepart, false);
  update(state, next(1, 100), 3300); update(state, next(2, 1100), 4300);
  assert.equal(update(state, next(3, 2100), 5300).canDepart, false);
  assert.equal(update(state, next(4, 3100), 6300).canDepart, true);
});

test('identity changes remain unaccounted for until the doors reopen', () => {
  const state = closed(), replacement = [{track_id: 99, posture: 'seated'}];
  for (const [frame, time] of [[1, 100], [2, 1100], [3, 2100]]) {
    const result = update(state, source(frame, time, replacement), time);
    assert.equal(result.canDepart, false); assert.equal(result.unknown, 1);
  }
  state.update({doors: 1, ramp: 0, source: source(4, 2200, replacement)}, 2200);
  update(state, source(5, 2300, replacement), 2300);
  update(state, source(6, 2400, replacement), 2400); update(state, source(7, 3400, replacement), 3400);
  assert.equal(update(state, source(8, 4400, replacement), 4400).canDepart, false);
  assert.equal(update(state, source(9, 5400, replacement), 5400).canDepart, true);
});

test('freshly delivered pre-closure captures do not begin the confirmation interval', () => {
  const state = closed();
  const old = source(1, 50, undefined, {age_ms: 150, seating_summary: {age_ms: 150}});
  const result = update(state, old, 100);
  assert.equal(result.status, 'unknown'); assert.equal(result.progressMs, 0);
  assert.equal(update(state, source(2, 200), 200).progressMs, 0);
});

test('a newly observed passenger restarts confirmation and joins the expected roster', () => {
  const state = closed();
  update(state, source(1, 100), 100); update(state, source(2, 1100), 1100);
  const both = [{track_id: 1, posture: 'seated'}, {track_id: 2, posture: 'seated'}];
  assert.equal(update(state, source(3, 1600, both), 1600).progressMs, 0);
  update(state, source(4, 2600, both), 2600);
  assert.equal(update(state, source(5, 3600, both), 3600).canDepart, false);
  assert.equal(update(state, source(6, 4600, both), 4600).canDepart, true);
});

test('stale and unknown frames do not erase the expected passenger roster', () => {
  const both = [{track_id: 1, posture: 'seated'}, {track_id: 2, posture: 'seated'}];
  const state = closed(both);
  update(state, source(1, 100, [], {seating_summary: {status: 'unavailable'}}), 100);
  const result = update(state, source(2, 200), 200);
  assert.equal(result.canDepart, false); assert.equal(result.unknown, 1); assert.equal(result.people, 2);
});

test('missing camera snapshots preserve the roster through reconnecting the same session', () => {
  const both = [{track_id: 1, posture: 'seated'}, {track_id: 2, posture: 'standing'}];
  const state = closed(both);
  assert.equal(update(state, null, 100).canDepart, false);
  assert.equal(update(state, source(1, 200, [], {connected: false}), 200).canDepart, false);
  const result = update(state, source(2, 300), 300);
  assert.equal(result.canDepart, false); assert.equal(result.people, 2); assert.equal(result.unknown, 1);
});

test('demo posture packets are permitted only by an explicitly configured lab instance', () => {
  const live = new DepartureState(), lab = new DepartureState({allowDemo: true});
  for (const [frame, time] of [[0, 0], [1, 100], [2, 1100], [3, 2100], [4, 3100]]) {
    const packet = source(frame, time, undefined, {is_demo: true});
    assert.equal(update(live, packet, time).canDepart, false);
    const view = update(lab, packet, time);
    assert.equal(view.canDepart, frame === 4);
  }
  lab.reset();
  assert.equal(update(lab, source(5, 3200, undefined, {is_demo: true}), 3200).canDepart, false);
});
