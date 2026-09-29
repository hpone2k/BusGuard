import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {entranceLedPresentation} from '../static/bus/src/entrance-led.js';
import {buildBus, ENTRANCE_LED_LAYOUT} from '../static/bus/src/model.js';

function snapshot(phase = 'boarding', patch = {}) {
  return {assistance: {simulation: true, vehicle: {motion: 'stationary', doors: 'open', ramp: 'stowed'},
    scenario: {enabled: true, phase, admission_open: true, boarding_remaining_ms: 10_000, hard_remaining_ms: 40_000,
      seats: {priority: {capacity: 6, occupied: 2, reserved: 1, available: 3},
        standard: {capacity: 20, occupied: 10, reserved: 2, available: 8}}, ...patch}}};
}

test('entrance LED uses available seats after occupancy and reservations', () => {
  assert.equal(entranceLedPresentation(snapshot()).seatsLine, 'PRIORITY 03   STANDARD 08');
  const full = snapshot('boarding', {seats: {
    priority: {capacity: 6, occupied: 6, reserved: 0, available: 0},
    standard: {capacity: 20, occupied: 18, reserved: 2, available: 0}}});
  assert.equal(entranceLedPresentation(full).statusLine, 'BUS FULL / ALIGHT ONLY');
  const invalid = snapshot(); invalid.assistance.scenario.seats.standard.available = 20;
  assert.equal(entranceLedPresentation(invalid).seatsLine, 'PRIORITY --   STANDARD --');
});

test('departure LED counts down the authoritative planned movement, not boarding closure', () => {
  const state = snapshot('boarding', {boarding_remaining_ms: 6_000,
    departure_notice: {remaining_ms: 10_000, announced: true, waiting_for_detection: false}});
  assert.equal(entranceLedPresentation(state, 501).statusLine, 'DEPART IN 10s');
  assert.equal(entranceLedPresentation(state, 1000).statusLine, 'DEPART IN 9s');
  state.assistance.scenario.departure_notice.announced = false;
  assert.equal(entranceLedPresentation(state).statusLine, 'PLANNED IN 10s');
  delete state.assistance.scenario.departure_notice;
  assert.equal(entranceLedPresentation(state).statusLine, 'WELCOME / BOARDING');
});

test('late accepted allowance shows door closure and no new taps, not a departure promise', () => {
  const state = snapshot('boarding', {admission_open: false, finishing_extensions: true,
    hard_remaining_ms: 0, boarding_remaining_ms: 19_000,
    departure_notice: {remaining_ms: 24_000, announced: false}});
  assert.equal(entranceLedPresentation(state, 500).statusLine, 'CLOSE IN 19s / NO NEW TAPS');
  state.assistance.vehicle.obstruction = true;
  assert.equal(entranceLedPresentation(state).statusLine, 'DEPARTURE HELD');
});

test('held, opening and detection states never display a misleading movement countdown', () => {
  const notice = {remaining_ms: 10_000, announced: true};
  for (const [phase, expected] of [['held', 'DEPARTURE HELD'], ['opening', 'DOORS OPENING'],
    ['braking', 'ARRIVING / PLEASE WAIT']]) {
    assert.equal(entranceLedPresentation(snapshot(phase, {departure_notice: notice})).statusLine, expected);
  }
  const activity = snapshot('boarding', {departure_notice: {remaining_ms: 12_000,
    announced: false, waiting_for_detection: true}});
  assert.equal(entranceLedPresentation(activity).statusLine, 'WAIT / BOARDING ACTIVITY');
  const emergency = snapshot('departure_wait', {departure_notice: notice});
  emergency.assistance.vehicle.emergency = true;
  assert.equal(entranceLedPresentation(emergency).statusLine, 'DEPARTURE HELD');
  emergency.assistance.vehicle.emergency = false;
  emergency.assistance.vehicle.revalidation_required = true;
  assert.equal(entranceLedPresentation(emergency).statusLine, 'DEPARTURE HELD');
});

test('zero countdown means checks, and movement requires authoritative moving state', () => {
  const state = snapshot('departure_wait', {departure_notice: {remaining_ms: 900, announced: true}});
  assert.equal(entranceLedPresentation(state, 901).statusLine, 'DEPARTURE CHECK');
  state.assistance.scenario.phase = 'travelling';
  assert.equal(entranceLedPresentation(state).statusLine, 'PLEASE WAIT');
  state.assistance.vehicle.motion = 'moving';
  assert.equal(entranceLedPresentation(state).statusLine, 'BUS IN SERVICE');
});

test('closed-door hold has no expiry; standing confirmation displays the server five-second check', () => {
  const state = snapshot('held', {posture_wait: {enabled: true, status: 'checking', remaining_ms: null}});
  state.assistance.vehicle.doors = 'closed';
  state.assistance.readiness = {reasons: ['One passenger is standing.'], posture: {message: 'One passenger is standing.'}};
  assert.equal(entranceLedPresentation(state, 501).statusLine, 'PLEASE SIT / WAITING');
  state.assistance.scenario.posture_wait = {enabled: true, status: 'confirming', remaining_ms: 4700};
  state.assistance.readiness.reasons=['Wait for the boarding window, door closure and final departure checks.'];
  assert.equal(entranceLedPresentation(state, 1000).statusLine, 'STANDING CHECK 5s');
  state.assistance.scenario.phase='boarding';
  assert.equal(entranceLedPresentation(state).statusLine,'WELCOME / BOARDING');
  state.assistance.scenario.phase = 'travelling'; state.assistance.vehicle.motion = 'moving';
  assert.equal(entranceLedPresentation(state).statusLine, 'BUS IN SERVICE');
  state.assistance.seating = {standing: 1};
  assert.equal(entranceLedPresentation(state).statusLine, 'PLEASE REMAIN SEATED');
});

test('posture checking never conceals another departure hold or begins with open doors', () => {
  const state = snapshot('held', {posture_wait: {enabled: true, status: 'checking', remaining_ms: 9000}});
  assert.equal(entranceLedPresentation(state).statusLine, 'DEPARTURE HELD');
  state.assistance.vehicle.doors = 'closed';
  for (const field of ['emergency', 'revalidation_required', 'obstruction', 'wheelchair_aboard']) {
    state.assistance.vehicle[field] = true;
    assert.equal(entranceLedPresentation(state).statusLine, 'DEPARTURE HELD', field);
    state.assistance.vehicle[field] = false;
  }
  state.assistance.active_count = 1;
  assert.equal(entranceLedPresentation(state).statusLine, 'DEPARTURE HELD');
  state.assistance.active_count = 0;
  state.assistance.readiness = {reasons: ['The inside camera indicates more than 26 passengers. Check capacity before departure.']};
  assert.equal(entranceLedPresentation(state).statusLine, 'DEPARTURE HELD');
});

test('offline, old, missing and invalid observations erase seats and times', () => {
  for (const view of [entranceLedPresentation(snapshot(), 0, false),
    entranceLedPresentation(snapshot(), 2000), entranceLedPresentation(snapshot(), -1),
    entranceLedPresentation(snapshot(), NaN), entranceLedPresentation(null)]) {
    assert.equal(view.seatsLine, 'PRIORITY --   STANDARD --');
    assert.equal(view.statusLine, 'CONNECTION LOST');
  }
  assert.equal(entranceLedPresentation(snapshot(), 1999).seatsLine, 'PRIORITY 03   STANDARD 08');
});

test('physical sign clears the entrance and only uploads its texture when visible text changes', () => {
  const savedDocument = globalThis.document;
  const drawLog = [];
  globalThis.document = {createElement(name) {
    assert.equal(name, 'canvas');
    const context = Object.fromEntries(['fillRect', 'clearRect', 'beginPath', 'roundRect', 'fill',
      'stroke', 'arc', 'moveTo', 'lineTo'].map(method => [method, () => {}]));
    context.fillText = text => drawLog.push(text);
    return {width: 1, height: 1, getContext: () => context};
  }};
  let bus;
  try { bus = buildBus(new THREE.Scene()); }
  finally {
    if (savedDocument === undefined) delete globalThis.document;
    else globalThis.document = savedDocument;
  }
  const {group, face} = bus.entranceLed;
  assert.deepEqual(group.position.toArray(), ENTRANCE_LED_LAYOUT.position);
  assert.equal(group.position.x, 4.75, 'The sign is centred over the front entrance');
  assert(group.position.y - ENTRANCE_LED_LAYOUT.size[1] / 2 > 2.73, 'Door leaves must clear the sign');
  assert(group.position.z > 1.35, 'The sign faces the outside kerb');
  assert.equal(face.material.side, THREE.FrontSide, 'The text faces approaching passengers, not the cabin');
  const view = entranceLedPresentation(snapshot());
  const before = face.material.map.version;
  assert.equal(bus.setEntranceDisplay(view), true);
  assert.equal(face.material.map.version, before + 1);
  assert.deepEqual(drawLog.slice(-2), [view.seatsLine, view.statusLine]);
  assert.equal(bus.setEntranceDisplay({...view}), false);
  assert.equal(face.material.map.version, before + 1, 'Unchanged polls must not re-upload textures');
  bus.setCutaway(1);
  assert.equal(group.parent.visible, false, 'The sign disappears with the shell for cabin inspection');
  bus.setCutaway(0);
  assert.equal(group.parent.visible, true);
});
