import {test} from 'node:test';
import assert from 'node:assert/strict';
import {AssistanceState, observedSummary} from '../static/bus/src/live-state.js';

const source = changes => ({role: 'outside', kind: 'live', session_id: 'outside-a',
  source_name: 'Kerb camera', connected: true, status: 'connected', is_demo: false,
  age_ms: 0, received_at_ms: 100000, frame_id: 40, detections: [], ...changes});
const event = (id, changes = {}) => ({id, type: 'wheelchair', origin: 'vision',
  source_kind: 'live', source_role: 'outside', session_id: 'outside-a',
  source_name: 'Kerb camera', confidence: .88, timestamp_ms: 100000,
  is_demo: false, ...changes});
const snapshot = (changes = {}) => ({instance_id: 'boot-a', revision: 1, server_time_ms: 100000,
  events: [], sources: {outside: source(), inside: source({role: 'inside', session_id: 'inside-a'})}, ...changes});
function armed(now = 0) { const state = new AssistanceState(); state.ingest(snapshot(), now); return state; }

test('opening the twin suppresses historical requests and duplicate polls never replay assistance', () => {
  const state = new AssistanceState();
  assert.deepEqual(state.ingest(snapshot({events: [event(8)]}), 0), []);
  assert.equal(state.response().active, false);
  const next = snapshot({revision: 2, events: [event(8), event(9)]});
  assert.deepEqual(state.ingest(next, 100).map(e => e.id), [9]);
  const deadline = state.holdUntil;
  assert.deepEqual(state.ingest(next, 500), []);
  assert.equal(state.holdUntil, deadline);
  assert.equal(state.pending.length, 1);
  assert.equal(state.acknowledge(20100), true);
  state.ingest(next, 20200);
  assert.equal(state.response().active, false);
});

test('only fresh confirmed exterior live events from the currently assigned camera trigger a response', () => {
  const rejected = [
    ['image event', {source_kind: 'image'}],
    ['interior observation', {source_role: 'inside'}],
    ['unassigned observation', {source_role: 'unassigned'}],
    ['previous camera session', {session_id: 'outside-old'}],
    ['demo event', {is_demo: true}],
    ['stale event', {timestamp_ms: 97499}],
    ['future event', {timestamp_ms: 100001}],
    ['unsupported type', {type: 'pregnant'}],
    ['visual age inference', {type: 'senior'}],
    ['visual concession inference', {type: 'assistance'}],
    ['unrecognized origin', {origin: 'manual'}],
    ['disconnected source', {}, {connected: false}],
    ['stale source', {}, {age_ms: 2501}],
    ['demo source', {}, {is_demo: true}],
    ['image source', {}, {kind: 'image'}],
  ];
  for (const [name, eventChanges, sourceChanges = {}] of rejected) {
    const state = armed();
    const data = snapshot({revision: 2, events: [event(1, eventChanges)], sources: {outside: source(sourceChanges)}});
    assert.deepEqual(state.ingest(data, 100), [], name);
    assert.equal(state.response().active, false, name);
  }
  const state = armed();
  const observationsOnly = snapshot({revision: 2, sources: {outside: source({detections: [
    {label: 'wheelchair', score: .99, track_id: 4, predicted: false},
  ]})}});
  assert.deepEqual(state.ingest(observationsOnly, 100), []);
  assert.equal(state.response().active, false, 'Raw frame boxes do not replace backend event confirmation');
  const confirmed = event(1);
  assert.deepEqual(state.ingest(snapshot({revision: 3, events: [confirmed]}), 200), [confirmed]);
  assert.deepEqual(state.response(), {active: true, ramp: true, priority: false, types: ['wheelchair']});
});

test('explicit RFID requests work without a camera and visual detections never substitute for them', () => {
  const state = armed();
  const rfid = {origin: 'rfid', source_kind: 'rfid', source_role: 'inside', session_id: null, confidence: null};
  assert.deepEqual(state.ingest(snapshot({revision: 2, sources: {}, events: [
    event(1, {...rfid, type: 'senior'}), event(2, {...rfid, type: 'assistance'}),
    event(3, {...rfid, type: 'wheelchair'}), event(4, {...rfid, type: 'senior', source_kind: 'live'}),
    event(5, {...rfid, type: 'senior', is_demo: true}),
  ]}), 100).map(e => e.id), [1, 2]);
  assert.deepEqual(state.response(), {active: true, ramp: false, priority: true, types: ['senior', 'assistance']});
});

test('dwell uses a local monotonic clock, extends on new requests, and needs explicit acknowledgement', () => {
  const state = armed();
  state.ingest(snapshot({revision: 2, events: [event(1)]}), 1000);
  assert.equal(state.remaining(1000), 20);
  assert.equal(state.acknowledge(20999), false);
  // A server wall-clock change cannot expire the local minimum dwell early.
  state.ingest(snapshot({revision: 3, server_time_ms: 4000000,
    events: [event(1), event(2, {type: 'walking', timestamp_ms: 4000000})]}), 5000);
  assert.equal(state.holdUntil, 25000);
  assert.equal(state.remaining(24501), 1);
  assert.equal(state.acknowledge(24999), false);
  assert.deepEqual(state.response(), {active: true, ramp: true, priority: true, types: ['wheelchair', 'walking']});
  assert.equal(state.remaining(30000), 0);
  assert.equal(state.response().active, true, 'Elapsed dwell alone does not claim the passenger has boarded');
  assert.equal(state.acknowledge(30000), true);
  assert.deepEqual(state.response(), {active: false, ramp: false, priority: false, types: []});
});

test('connection loss and empty detections hold existing assistance until acknowledgement', () => {
  const state = armed();
  state.ingest(snapshot({revision: 2, events: [event(1)]}), 100);
  state.disconnect();
  assert.equal(state.isOnline(200), false);
  assert.equal(state.response().active, true);
  state.ingest(snapshot({revision: 3, sources: {outside: source({connected: false, detections: []})}}), 300);
  assert.equal(state.response().active, true);
  assert.equal(state.acknowledge(5000), false);
  assert.equal(state.acknowledge(20100), true);
});

test('server instance changes reset the event cursor even if the new revision is higher', () => {
  const state = armed();
  state.ingest(snapshot({revision: 30, events: [event(10)]}), 100);
  const restart = snapshot({instance_id: 'boot-b', revision: 500, events: [event(1)]});
  assert.deepEqual(state.ingest(restart, 200), [], 'Restart backlog is not a new boarding request');
  assert.deepEqual(state.pending.map(e => e.id), [10], 'Restart cannot silently clear a pending request');
  assert.deepEqual(state.ingest({...restart, revision: 501, events: [event(1), event(2, {type: 'pram'})]}, 300)
    .map(e => e.id), [2], 'Fresh events after the new baseline must be accepted');
  assert.deepEqual(state.response().types, ['wheelchair', 'pram']);
});

test('source freshness advances between polls and hides stale observations without changing the server snapshot', () => {
  const state = new AssistanceState();
  const camera = source({age_ms: 500, detections: [{label: 'wheelchair', score: .8}]});
  state.ingest(snapshot({sources: {outside: camera}}), 1000);
  assert.equal(state.source('outside', 2000).age_ms, 1500);
  assert.equal(state.source('outside', 2000).connected, true);
  const expired = state.source('outside', 3101);
  assert.equal(expired.connected, false);
  assert.deepEqual(expired.detections, []);
  assert.equal(camera.age_ms, 500);
  assert.equal(camera.connected, true, 'The server snapshot remains immutable when client time advances');
  assert.equal(camera.detections.length, 1);
  assert.equal(state.isOnline(4000), true);
  assert.equal(state.isOnline(4001), false);
  state.disconnect();
  assert.equal(state.source('outside', 2000).connected, false);
});

test('observation summaries exclude predicted and disconnected boxes', () => {
  const detections = [{label: 'person'}, {label: 'person'}, {label: 'wheelchair'},
    {label: 'walker', predicted: true}];
  assert.deepEqual(observedSummary(source({detections})), ['2 person', '1 wheelchair']);
  assert.deepEqual(observedSummary(source({connected: false, detections})), []);
  assert.deepEqual(observedSummary(undefined), []);
});

test('reopening the twin recovers an ongoing exact confirmed track using its current observation', () => {
  const original = event(12, {track_id: 42, timestamp_ms: 1000, confidence: .72});
  const data = snapshot({revision: 90, events: [original], sources: {outside: source({
    detections: [{track_id: 42, label: 'Wheelchair user', score: .93, predicted: false}],
  })}});
  const state = new AssistanceState();
  const recovered = state.ingest(data, 100);
  assert.deepEqual(recovered, [{...original, recovered: true, observed_at_ms: 100000, confidence: .93}]);
  assert.equal(state.remaining(100), 20);
  assert.equal(state.response().ramp, true);
  assert.equal(original.timestamp_ms, 1000, 'The historical confirmation retains its original timestamp');
  assert.equal(original.confidence, .72, 'Recovery cannot rewrite the event log confidence');
  assert.equal(original.recovered, undefined);
  assert.equal(state.acknowledge(20100), true);
  assert.deepEqual(state.ingest(data, 20200), []);
  assert.equal(state.response().active, false, 'A still-visible acknowledged track is not replayed on every poll');
  assert.deepEqual(state.ingest({...data, revision: 89}, 20300), [],
    'A revision reset in the same instance cannot replay an acknowledged track');
});

test('recovery requires current same-session same-type observations, never historical or predicted presence', () => {
  const detection = {track_id: 42, label: 'wheelchair', score: .9, predicted: false};
  const rejected = [
    ['no current observation', {}, {}, []],
    ['only a prediction', {}, {}, [{...detection, predicted: true}]],
    ['generic person with the same ID', {}, {}, [{...detection, label: 'person'}]],
    ['different mobility type', {}, {}, [{...detection, label: 'walker'}]],
    ['unrecognized appearance inference', {}, {}, [{...detection, label: 'pregnant person'}]],
    ['different current track', {}, {}, [{...detection, track_id: 43}]],
    ['untracked observation', {}, {}, [{...detection, track_id: null}]],
    ['untracked old event', {track_id: null}],
    ['previous camera session', {session_id: 'outside-old'}],
    ['interior old event', {source_role: 'inside'}],
    ['old image result', {source_kind: 'image'}],
    ['old demo result', {is_demo: true}],
    ['old RFID tap', {type: 'senior', origin: 'rfid', source_kind: 'rfid'}],
    ['stale current source', {}, {age_ms: 2501}],
    ['disconnected current source', {}, {connected: false}],
    ['current image source', {}, {kind: 'image'}],
    ['current demo source', {}, {is_demo: true}],
  ];
  for (const [name, eventChanges = {}, sourceChanges = {}, detections = [detection]] of rejected) {
    const state = new AssistanceState();
    const oldEvent = event(12, {track_id: 42, timestamp_ms: 1000, ...eventChanges});
    const data = snapshot({events: [oldEvent], sources: {outside: source({detections, ...sourceChanges})}});
    assert.deepEqual(state.ingest(data, 100), [], name);
    assert.equal(state.response().active, false, name);
  }
});

test('new server instances can recover confirmed mobility aliases but normal event processing is unchanged', () => {
  for (const [type, label] of [['wheelchair', ' WHEELCHAIRS '], ['pram', 'baby-stroller'],
    ['walking', 'walking_aid'], ['walking', 'crutch'], ['walking', 'cane']]) {
    const state = armed();
    const retained = event(1, {type, track_id: 7, timestamp_ms: 1000});
    const data = snapshot({instance_id: 'boot-b', revision: 500, events: [retained], sources: {
      outside: source({detections: [{label, track_id: 7, score: .84}]}),
    }});
    assert.deepEqual(state.ingest(data, 100).map(e => [e.type, e.recovered, e.confidence]), [[type, true, .84]], label);
    const fresh = event(2, {type, track_id: null});
    assert.deepEqual(state.ingest({...data, revision: 501, events: [retained, fresh]}, 200), [fresh],
      'Fresh confirmed events remain eligible even without a tracked recovery match');
  }
});
