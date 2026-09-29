import test from 'node:test';
import assert from 'node:assert/strict';
import {scenarioPresentation, requestAvailability, requestCountdown} from '../static/passenger/core.js';
import {scenarioPresentation as twinPresentation} from '../static/bus/src/controller-state.js';

function controller(patch = {}) {
  return {simulation: true, vehicle: {motion: 'stationary', doors: 'open', stop_id: 'campus'},
    announcements: ['Please remain seated.'],
    scenario: {enabled: true, phase: 'boarding', admission_open: true, detection_enabled: true,
      boarding_remaining_ms: 8000, hard_remaining_ms: 43000, departure_remaining_ms: 0,
      seats: {priority: {capacity: 6, occupied: 2, reserved: 1, available: 3},
        standard: {capacity: 20, occupied: 13, reserved: 2, available: 5}}, ...patch}};
}

test('passenger and twin use the same confirmed seat ledger, excluding reservations and camera counts from occupants', () => {
  const input = controller();
  input.seating = {people: 99, seated: 99};
  const view = scenarioPresentation(input);
  assert.equal(twinPresentation, scenarioPresentation);
  assert.deepEqual([view.priority, view.standard, view.occupied, view.reserved, view.available], [3, 5, 15, 3, 8]);
  assert.equal(view.boardingOpen, true);
});

test('unknown, inconsistent and stale seat data never presents zero available seats as a fact', () => {
  for (const data of [undefined, null, {priority: {capacity: 6, occupied: 2, reserved: 1, available: 9}},
    {priority: {capacity: 6, occupied: -1, reserved: 0, available: 7}, standard: {capacity: 20, occupied: 0, reserved: 0, available: 20}}]) {
    const view = scenarioPresentation(controller({seats: data}));
    assert.equal(view.available, null); assert.equal(view.occupied, null);
    assert.equal(requestAvailability(view, 'boarding', 'campus', false, {mode: 'at_stop', stop_id: 'campus'}).allowed, false);
  }
  for (const view of [scenarioPresentation(controller(), 2000), scenarioPresentation(controller(), 0, false),
    scenarioPresentation(controller(), NaN), scenarioPresentation(controller(), -1), scenarioPresentation(null)]) {
    assert.equal(view.connected, false); assert.equal(view.available, null);
    assert.equal(view.boardingOpen, false); assert.equal(view.timer, '—');
  }
  assert.equal(scenarioPresentation(controller(), 1999).connected, true);
});

test('countdown ages the received deadline but never creates a local departure or extends the window', () => {
  const input = controller({boarding_remaining_ms: 900});
  assert.equal(scenarioPresentation(input, 0).timer, '1s');
  assert.equal(scenarioPresentation(input, 899).boardingOpen, true);
  const expired = scenarioPresentation(input, 900);
  assert.equal(expired.timer, '0s'); assert.equal(expired.boardingOpen, false);
  assert.equal(expired.phase, 'boarding'); assert.equal(expired.moving, false);
  assert.equal(scenarioPresentation(controller({hard_remaining_ms: 500}), 500).boardingOpen, false);
  const conflicting = controller(); conflicting.vehicle.motion = 'moving';
  assert.equal(scenarioPresentation(conflicting).boardingOpen, false);
});

test('full capacity blocks boarding while alighting remains available only at the current stop', () => {
  const full = controller({seats: {priority: {capacity: 6, occupied: 6, reserved: 0, available: 0},
    standard: {capacity: 20, occupied: 20, reserved: 0, available: 0}}});
  let view = scenarioPresentation(full);
  assert.equal(requestAvailability(view, 'boarding', 'campus', false, {mode: 'at_stop', stop_id: 'campus'}).allowed, false);
  assert.match(requestAvailability(view, 'boarding', 'campus', false, {mode: 'at_stop', stop_id: 'campus'}).reason, /occupied or reserved/);
  assert.equal(requestAvailability(view, 'alighting', 'campus', false, {mode: 'onboard'}).allowed, true);
  assert.equal(requestAvailability(view, 'boarding', 'interchange', false, {mode: 'at_stop', stop_id: 'interchange'}).allowed, false);
  full.scenario.phase = 'travelling'; full.vehicle.motion = 'moving';
  view = scenarioPresentation(full);
  assert.equal(view.moving, true);
  assert.equal(requestAvailability(view, 'boarding', 'campus', false, {mode: 'at_stop', stop_id: 'campus'}).allowed, false);
  assert.match(requestAvailability(view, 'boarding', 'campus', false, {mode: 'at_stop', stop_id: 'campus'}).reason, /Wait for the bus/);
});

test('closed boarding, safety holds and connection loss do not create new current-stop boarding requests', () => {
  for (const phase of ['stowing', 'closing', 'departure_wait', 'held', 'unknown']) {
    const view = scenarioPresentation(controller({phase}));
    assert.equal(requestAvailability(view, 'boarding', 'campus', false, {mode: 'at_stop', stop_id: 'campus'}).allowed, false);
    assert.equal(view.boardingOpen, false);
  }
  const offline = scenarioPresentation(controller(), 0, false);
  assert.equal(requestAvailability(offline, 'alighting', 'campus').allowed, false);
  // Retrying the original idempotency identity must still recover its outcome.
  assert.equal(requestAvailability(offline, 'boarding', 'campus', true).allowed, true);
  const held = scenarioPresentation(controller({phase: 'held'}));
  assert.equal(held.timer, 'Held'); assert.match(held.detail, /remain seated/);
});

test('server departure interval is a display only and legacy manual mode remains usable', () => {
  const view = scenarioPresentation(controller({phase: 'departure_wait', departure_remaining_ms: 2300}), 800);
  assert.equal(view.timer, '2s'); assert.equal(view.moving, false);
  const manual = controller(); delete manual.scenario;
  const legacy = scenarioPresentation(manual);
  assert.equal(legacy.connected, true); assert.equal(legacy.enabled, false);
  assert.equal(requestAvailability(legacy, 'boarding', 'campus', false, {mode: 'at_stop', stop_id: 'campus'}).allowed, true);
});

test('braking and opening show distinct phases without accepting new requests', () => {
  for (const phase of ['braking', 'opening']) {
    const view = scenarioPresentation(controller({phase, boarding_remaining_ms: 0, hard_remaining_ms: 0}));
    assert.equal(view.boardingOpen, false);
    assert.equal(view.moving, false);
    assert.equal(requestAvailability(view, 'boarding', 'campus', false, {mode: 'at_stop', stop_id: 'campus'}).allowed, false);
    assert.notEqual(view.timer, '0s');
  }
});

test('request countdown ages only its own granted allowance without advancing the authoritative status', () => {
  const input = {status: 'awaiting_completion', assistance_timer: {enabled: true, state: 'active', remaining_ms: 1900, allowance_ms: 10000}};
  assert.equal(requestCountdown(input).value, '2s');
  assert.equal(requestCountdown(input, 1100).value, '1s');
  assert.equal(requestCountdown(input, 1100).canComplete, true);
  assert.equal(requestCountdown(input, 1900).value, '0s');
  assert.equal(requestCountdown(input, 1900).canComplete, false);
  assert.equal(requestCountdown(input, 1900).state, 'confirming');
  assert.equal(input.status, 'awaiting_completion');
  assert.equal(requestCountdown(input, 2000).state, 'offline');
  assert.equal(requestCountdown(input, 0, false).value, '—');
});

test('queued request time waits for the stop; expired and held states are not counted as completion', () => {
  const value = state => requestCountdown({assistance_timer: {enabled: true, state, remaining_ms: null, allowance_ms: 0}});
  assert.equal(value('queued').value, 'Queued');
  assert.match(value('queued').detail, /when your assistance is accepted/);
  assert.equal(value('queued').canComplete, false);
  assert.equal(value('expired').value, 'Ended');
  assert.equal(value('held').value, 'Held');
  assert.equal(value('completed').visible, false);
  assert.equal(requestCountdown({}).visible, false);
});

test('ramp assistance renders its full 20-second grant and queued explanation', () => {
  const input = {needs: ['ramp'], assistance_timer: {enabled: true, state: 'active', remaining_ms: 20000, allowance_ms: 20000}};
  assert.equal(requestCountdown(input).value, '20s');
  assert.equal(requestCountdown(input).ratio, 1);
  assert.equal(requestCountdown(input, 1500).value, '19s');
  assert.equal(requestCountdown(input, 1500).canComplete, true);
  assert.match(requestCountdown(input).detail, /after new requests close/);
  input.assistance_timer.state = 'queued';
  assert.match(requestCountdown(input).detail, /^20 seconds/);
});

test('late granted assistance counts down after admission closes without enabling new requests', () => {
  const input = controller({boarding_remaining_ms: 19000, hard_remaining_ms: 0,
    admission_remaining_ms: 0, admission_open: false, finishing_extensions: true});
  const view = scenarioPresentation(input, 500);
  assert.equal(view.timer, '19s');
  assert.equal(view.timerLabel, 'Doors close in');
  assert.equal(view.title, 'Finishing accepted assistance');
  assert.equal(view.boardingOpen, false);
  assert.match(view.detail, /New requests and RFID taps are closed/);
  assert.equal(requestAvailability(view, 'boarding', 'campus', false, {mode: 'at_stop', stop_id: 'campus'}).allowed, false);
  assert.equal(requestAvailability(view, 'alighting', 'campus', false, {mode: 'onboard'}).allowed, false);
  const privateRequest = {needs: ['ramp'], assistance_timer: {enabled: true, state: 'active', remaining_ms: 18000, allowance_ms: 20000}};
  assert.equal(requestCountdown(privateRequest, 500).canComplete, true);
});

test('estimated occupancy can consume a reserved space while every availability stays nonnegative', () => {
  const input = controller({seats: {estimated: true, basis: 'Estimated from cabin observations.',
    priority: {capacity: 6, occupied: 6, reserved: 1, available: 0},
    standard: {capacity: 20, occupied: 20, reserved: 1, available: 0}}});
  const view = scenarioPresentation(input);
  assert.equal(view.available, 0);
  assert.equal(view.occupied, 26);
  assert.equal(view.estimated, true);
  assert.match(view.seatBasis, /Estimated/);
});

test('passenger and twin show continued detection without replacing private or boarding allowances', () => {
  const input = controller({detection_wait: {active: true, roles: ['outside']},
    departure_notice: {remaining_ms: 14000, announced: false, waiting_for_detection: true}});
  const view = scenarioPresentation(input, 500);
  assert.equal(view.title, 'Detection continuing');
  assert.match(view.detail, /up to 50 seconds/);
  assert.equal(view.timer, '8s');
  assert.equal(view.timerLabel, 'Boarding time left');
  assert.equal(view.boardingOpen, true);
  assert.equal(view.moving, false);
});

test('announced movement countdown ages independently, while holds and stale state suppress it', () => {
  const input = controller({boarding_remaining_ms: 5000,
    departure_notice: {remaining_ms: 8800, announced: true, waiting_for_detection: true}});
  const view = scenarioPresentation(input, 1000);
  assert.match(view.detail, /Departure warning · 8s/);
  assert.equal(view.timer, '4s');
  assert.equal(view.title, 'Boarding at this stop');
  assert.equal(view.moving, false);
  assert.doesNotMatch(scenarioPresentation(input, 2000).detail, /Departure warning/);
  input.scenario.phase = 'held';
  assert.equal(scenarioPresentation(input).detail, 'Please remain seated.');
  input.scenario.phase = 'departure_wait';
  input.scenario.departure_notice.remaining_ms = 100;
  const expired = scenarioPresentation(input, 500);
  assert.match(expired.detail, /checks are in progress/);
  assert.equal(expired.moving, false);
  assert.equal(expired.boardingOpen, false);
});

test('closed-door standing hold has no timeout; confirmation never advances without fresh frames', () => {
  const input = controller({phase: 'held', posture_wait: {enabled: true, status: 'checking', remaining_ms: null}});
  input.vehicle = {...input.vehicle, doors: 'closed', ramp: 'stowed'};
  const checking = scenarioPresentation(input, 500);
  assert.equal(checking.timer, 'Waiting'); assert.equal(checking.timerLabel, 'Standing check');
  input.vehicle.obstruction = true;
  assert.equal(scenarioPresentation(input).timer, 'Held');
  input.vehicle.obstruction = false;
  input.scenario.posture_wait = {enabled: true, status: 'confirming', remaining_ms: 4700, confirmation_ms: 5000};
  assert.equal(scenarioPresentation(input, 1000).timer, '5s');
  assert.match(scenarioPresentation(input).detail, /5 continuous seconds with no standing detected/);
  input.readiness = {reasons:['Wait for the boarding window, door closure and final departure checks.'],
    posture:{confirmation_ms:6500}};
  assert.match(scenarioPresentation(input).detail, /6.5 continuous seconds/);
  input.vehicle.doors = 'open';
  assert.equal(scenarioPresentation(input).timer, 'Held');
});

test('moving standing passenger gets a reminder without presentation declaring a stop', () => {
  const input = controller({phase: 'travelling', inside_detection_enabled: true, detection_enabled: false});
  input.vehicle = {...input.vehicle, doors: 'closed', motion: 'moving'};
  input.seating = {standing: 1};
  const view = scenarioPresentation(input);
  assert.equal(view.moving, true); assert.equal(view.detectionEnabled, true);
  assert.match(view.detail, /take a seat/);
});
