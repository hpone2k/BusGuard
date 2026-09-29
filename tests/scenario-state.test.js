import {test} from 'node:test';
import assert from 'node:assert/strict';
import {controllerAnnouncement, scenarioPresentation} from '../static/scenario-state.js';

function state(patch = {}) {
  return {readiness: {message: 'Please remain seated.'}, announcements: ['An earlier message'], scenario: {
    enabled: true, phase: 'boarding', detection_enabled: true, stop_elapsed_ms: 1900,
    boarding_remaining_ms: 8100, hard_remaining_ms: 48100,
    seats: {priority: {capacity: 6, occupied: 2, available: 4},
      standard: {capacity: 20, occupied: 19, available: 1}, total: 21, capacity: 26}, ...patch,
  }};
}

test('operator countdowns display server timing without deciding departure locally', () => {
  const result = scenarioPresentation(state());
  assert.equal(result.timer, '9s');
  assert.equal(result.hardRemaining, '49s');
  assert.equal(result.progress, 3.8);
  assert.equal(result.boarding, true);
  assert.equal(result.travelling, false);
  const expired = scenarioPresentation(state({boarding_remaining_ms: 0, hard_remaining_ms: 0}));
  assert.equal(expired.timer, '0s');
  assert.equal(expired.phase, 'boarding');
  assert.equal(expired.travelling, false);
});

test('offline state exposes neither live boarding controls nor a countdown', () => {
  const result = scenarioPresentation(state(), false);
  assert.equal(result.active, false);
  assert.equal(result.boarding, false);
  assert.equal(result.travelling, false);
  assert.equal(result.admission, null);
  assert.equal(result.timer, '—');
  assert.equal(result.hardRemaining, '—');
  assert.match(result.label, /offline/);
});

test('operator distinguishes the admission cutoff from the final accepted extension', () => {
  const result = scenarioPresentation(state({admission_open: false, finishing_extensions: true,
    boarding_remaining_ms: 18500, hard_remaining_ms: 0, stop_elapsed_ms: 50500}));
  assert.equal(result.timer, '19s');
  assert.equal(result.hardRemaining, '0s');
  assert.equal(result.label, 'Finishing accepted assistance');
  assert.match(result.detail, /New RFID taps and requests are closed/);
  assert.equal(result.travelling, false);
});

test('closed-door wait, travel and safety hold preserve distinct controller phases', () => {
  assert.equal(scenarioPresentation(state({phase: 'departure_wait', departure_remaining_ms: 2100})).timer, '3s');
  const travel = scenarioPresentation(state({phase: 'travelling', detection_enabled: false}));
  assert.equal(travel.travelling, true);
  assert.equal(travel.boarding, false);
  assert.equal(travel.detection, 'Inside active · outside paused');
  assert.equal(travel.hardRemaining, '—');
  const held = scenarioPresentation(state({phase: 'held', stop_elapsed_ms: 53000}));
  assert.equal(held.timer, 'Waiting');
  assert.equal(held.detail, 'Please remain seated.');
  assert.equal(held.progress, 100);
  assert.equal(held.travelling, false);
});

test('simulated capacity uses admitted seat counts rather than vision detections', () => {
  const snapshot = state();
  snapshot.sources = {inside: {detections: Array(30).fill({label: 'person'})}};
  assert.equal(scenarioPresentation(snapshot).total, 21);
  assert.equal(scenarioPresentation(snapshot).full, false);
  snapshot.scenario.seats.priority.available = 0;
  snapshot.scenario.seats.standard.available = 0;
  assert.equal(scenarioPresentation(snapshot).full, true);
  delete snapshot.scenario.seats;
  assert.equal(scenarioPresentation(snapshot).total, null);
  assert.equal(scenarioPresentation(snapshot).full, false);
});

test('new rejected RFID events can repeat the same spoken message without repeating on every poll', () => {
  const snapshot = state({announcement: {id: 'cycle-1-tap-1', message: 'Sorry, all seats are occupied. Please wait for the next bus.'}});
  snapshot.announcements = [snapshot.scenario.announcement.message];
  const first = controllerAnnouncement(snapshot);
  assert.deepEqual(controllerAnnouncement(snapshot), first);
  snapshot.scenario.announcement.id = 'cycle-1-tap-2';
  const second = controllerAnnouncement(snapshot);
  assert.notEqual(first.id, second.id);
  assert.equal(first.message, second.message);
  assert.equal(controllerAnnouncement(snapshot, false), null);
});

test('central safety announcements override a previous timed RFID announcement', () => {
  const snapshot = state({announcement: {id: 'cycle-1-tap-1', message: 'Boarding accepted.'}});
  snapshot.announcements = ['Please keep the doorway clear. Assistance remains on hold.'];
  assert.equal(controllerAnnouncement(snapshot).message, snapshot.announcements[0]);
  assert.notEqual(controllerAnnouncement(snapshot).id, 'scenario:cycle-1-tap-1');
});

test('manual guidance remains available outside the automatic scenario', () => {
  const snapshot = state({enabled: false, announcement: {id: 'old', message: 'Old scenario message'}});
  assert.equal(controllerAnnouncement(snapshot).message, 'An earlier message');
  assert.equal(scenarioPresentation(null).enabled, false);
  assert.equal(scenarioPresentation(null).total, null);
});

test('live detection wait and final movement notice are separate from the boarding timer', () => {
  const input = state({detection_wait: {active: true, roles: ['outside']},
    departure_notice: {remaining_ms: 13800, announced: false, waiting_for_detection: true}});
  let result = scenarioPresentation(input);
  assert.equal(result.timer, '9s');
  assert.equal(result.departureTimer, 'Waiting');
  assert.equal(result.departureLabel, 'DETECTION CONTINUING');
  assert.match(result.detail, /50 seconds/);
  input.scenario.departure_notice = {remaining_ms: 9500, announced: true, waiting_for_detection: false};
  result = scenarioPresentation(input);
  assert.equal(result.departureTimer, '10s');
  assert.equal(result.departureLabel, 'DEPARTURE WARNING');
  assert.equal(result.timer, '9s');
  assert.equal(scenarioPresentation(input, false).departureVisible, false);
  input.scenario.phase = 'held';
  assert.equal(scenarioPresentation(input).departureVisible, false);
});


test('standing hold has no deadline and confirmation follows fresh evidence and server duration', () => {
  const input = state({phase: 'held', posture_wait: {enabled: true, status: 'checking', remaining_ms: null}});
  input.vehicle = {doors: 'closed', ramp: 'stowed'};
  assert.equal(scenarioPresentation(input).timer, 'Waiting');
  assert.match(scenarioPresentation(input).detail, /no automatic timeout/);
  input.scenario.posture_wait = {enabled: true, status: 'confirming', remaining_ms: 4700, confirmation_ms:5000};
  assert.equal(scenarioPresentation(input).timer, '5s');
  assert.equal(scenarioPresentation(input).timerLabel, 'CHECKING FOR STANDING');
  assert.match(scenarioPresentation(input).detail,/5 continuous seconds/);
  input.readiness.posture={confirmation_ms:6500};
  assert.match(scenarioPresentation(input).detail,/6.5 continuous seconds/);
});
