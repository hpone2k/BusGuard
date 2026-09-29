import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cabinPresentation} from '../static/bus/src/showcase-state.js';

const estimate = (stable, status = 'stable') => ({stable, status, raw: stable, support: .8});
function source(classes, options = {}) {
  return {role: 'inside', kind: 'live', session_id: 'interior', connected: true, age_ms: 25,
    count_summary: {classes, total: estimate(77)}, ...options};
}
const snapshot = inside => ({sources: {inside, outside: source({person: estimate(99)})}});

test('only an interior person class provides the people estimate', () => {
  const result = cabinPresentation(snapshot(source({person: estimate(7), wheelchair: estimate(1), stroller: estimate(2)})));
  assert.equal(result.people, 7);
  assert.equal(result.typeCount, 2);
  assert.equal(result.state, 'live');
  assert.equal(result.types.find(type => type.name === 'Wheelchair').count, 1);
});

test('outside counts, overlapping aliases and phrase subsets never become onboard totals', () => {
  assert.equal(cabinPresentation(snapshot(null)).people, null);
  assert.equal(cabinPresentation(snapshot(source({'person in a red shirt': estimate(2)}))).people, null);
  assert.equal(cabinPresentation(snapshot(source({person: estimate(7), people: estimate(7)}))).people, null);
});

test('missing, stale and uncertain evidence is unknown rather than zero or a held count', () => {
  for (const input of [source({person: estimate(7)}, {connected: false}),
    source({person: estimate(7)}, {age_ms: 1500}),
    source({person: estimate(7, 'uncertain')}), source({})]) {
    assert.equal(cabinPresentation(snapshot(input)).people, null);
  }
  assert.equal(cabinPresentation(snapshot(source({person: estimate(7)})), 1475).people, null);
  assert.equal(cabinPresentation(snapshot(source({person: estimate(7)})), 0, false).people, null);
  assert.equal(cabinPresentation(snapshot(source({person: estimate(0)}))).people, 0);
});

test('image and recorded video are clearly identified and not live occupancy', () => {
  const image = cabinPresentation(snapshot(source({person: estimate(3, 'instant')}, {kind: 'image'})));
  assert.equal(image.people, 3); assert.equal(image.state, 'recorded');
  assert.equal(image.status, 'Image snapshot'); assert.match(image.note, /does not confirm current occupancy/);
  const video = cabinPresentation(snapshot(source({person: estimate(4)}, {kind: 'video'})));
  assert.equal(video.people, 4); assert.equal(video.state, 'recorded'); assert.equal(video.status, 'Video playback');
  assert.equal(cabinPresentation(snapshot(source({person: estimate(4)}, {is_demo: true}))).people, null);
});

test('multiple aid labels stay separate rather than double-counting passenger types', () => {
  const result = cabinPresentation(snapshot(source({person: estimate(7), walker: estimate(1), cane: estimate(1)})));
  assert.equal(result.people, 7);
  assert(result.types.some(type => type.name === 'Walker' && type.count === 1));
  assert(result.types.some(type => type.name === 'Cane' && type.count === 1));
  assert(!result.types.some(type => type.name === 'Walking aid' && type.count === 2));
});
