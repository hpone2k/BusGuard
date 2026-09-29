import test from 'node:test';
import assert from 'node:assert/strict';
import {countPresentation, countsAtTime, observedCounts} from '../static/counts.js';

const estimate = (stable, overrides = {}) => ({raw: stable, stable, candidate: stable,
  support: .8, status: 'stable', held_age_ms: 0, held_remaining_ms: 1000, ...overrides});
const summary = (total = 5, overrides = {}) => ({window_ms: 1000, coverage_ms: 1000,
  coverage: 1, status: 'stable', last_observed_ms: 1000, age_ms: 0, stale_ms: 1500,
  uncertain_hold_ms: 1000, total: estimate(total), classes: {person: estimate(total)}, ...overrides});
const detected = (label = 'person', track_id = null, predicted = false) => ({label, track_id, predicted});

test('observed counts exclude predicted boxes and deduplicate identities within a class', () => {
  const counts = observedCounts([detected('Person', 3), detected('person', 3), detected('person', 4, true),
    detected('laptop', 3), detected('person'), detected('person')], ['person', 'laptop', 'bottle']);
  assert.deepEqual(counts, {total: 4, classes: {person: 3, laptop: 1, bottle: 0}});
});

test('warming counts retain observed comparison but do not manufacture a vote as time passes', () => {
  const input = summary(5, {coverage_ms: 400, coverage: .4});
  const first = countPresentation(input, {ageMs: 0});
  const later = countPresentation(input, {ageMs: 900});
  assert.equal(first.total.stable, null);
  assert.equal(first.total.raw, 5);
  assert.equal(first.total.display, '—');
  assert.equal(later.status, 'warming');
  assert.equal(later.coverageMs, 400);
  assert.match(later.message, /400 \/ 1000 ms/);
  assert.equal(countPresentation(input, {ageMs: 1500}).status, 'stale');
});

test('total and each object type keep their independent authoritative votes', () => {
  const data = summary(5, {classes: {Person: estimate(4, {raw: 6}), laptop: estimate(2, {support: .67})}});
  const view = countPresentation(data, {detections: Array.from({length: 9}, () => detected()), labels: ['PERSON', 'laptop']});
  assert.equal(view.total.stable, 5);
  assert.equal(view.classes.person.stable, 4);
  assert.equal(view.classes.person.raw, 6);
  assert.equal(view.classes.person.label, 'PERSON');
  assert.equal(view.classes.laptop.supportText, '67% vote support');
  assert.notEqual(view.total.stable, view.classes.person.stable + view.classes.laptop.stable);
  assert.match(view.message, /not detection accuracy/);
});

test('uncertain holds expire across delayed snapshots without double counting source age', () => {
  const uncertain = estimate(5, {raw: 3, candidate: 3, support: .6, status: 'uncertain', held_age_ms: 700});
  const input = summary(5, {status: 'uncertain', age_ms: 100, total: uncertain, classes: {person: {...uncertain}}});
  const original = JSON.stringify(input);
  const held = countPresentation(input, {ageMs: 400});
  assert.equal(held.total.stable, 5); // 700 + (400 - 100) = 1000 ms, last permitted instant.
  assert.equal(held.total.held, true);
  assert.equal(held.total.status, 'uncertain');
  assert.match(held.total.supportText, /leading vote · uncertain/);
  const expired = countPresentation(input, {ageMs: 401});
  assert.equal(expired.total.stable, null);
  assert.equal(expired.total.raw, 3);
  assert.equal(expired.classes.person.stable, null);
  assert.equal(countPresentation(input, {ageMs: 1499}).total.raw, 3);
  const stale = countPresentation(input, {ageMs: 1500});
  assert.equal(stale.status, 'stale');
  assert.equal(stale.total.raw, null);
  assert.equal(stale.total.support, null);
  assert.equal(stale.classes.person.display, '—');
  assert.equal(JSON.stringify(input), original);
  assert.equal(countPresentation(summary(5, {total: {...uncertain, held_age_ms: undefined}})).total.stable, null);
});

test('single images remain instantaneous and static while legacy video results remain explicitly raw', () => {
  const detections = [detected('person', 1), detected('person', 1), detected('person', 2, true), detected('laptop')];
  const image = countPresentation(null, {mode: 'image', detections, ageMs: 999999});
  assert.equal(image.mode, 'instant');
  assert.equal(image.total.display, '2');
  assert.equal(image.total.support, null);
  assert.equal(image.classes.person.stable, 1);
  assert.match(image.message, /Temporal voting does not apply/);
  const supplied = countPresentation(summary(2, {total: estimate(2, {status: 'instant'})}), {mode: 'image', ageMs: Infinity});
  assert.equal(supplied.total.stable, 2);
  const legacy = countPresentation(null, {mode: 'video', detections});
  assert.equal(legacy.mode, 'legacy');
  assert.equal(legacy.total.raw, 2);
  assert.equal(legacy.total.stable, null);
  assert.equal(legacy.total.display, '2');
  assert.equal(legacy.total.supportText, 'No vote data');
  assert.equal(countPresentation(null, {mode: 'video', detections, ageMs: 1500}).total.raw, null);
});

test('recorded counts use only the current timeline observation and age on the media clock', () => {
  const rows = [{time: 0, detections: [], count_summary: summary(2, {coverage_ms: 0})},
    {time: .7, detections: [], count_summary: summary(2, {coverage_ms: 700})},
    {time: 1, detections: [], count_summary: summary(2)},
    {time: 1.2, detections: [], count_summary: summary(0)}];
  assert.equal(countsAtTime(rows, -.1).mode, 'empty');
  assert.equal(countsAtTime(rows, .99).status, 'warming');
  assert.equal(countsAtTime(rows, 1.1).total.stable, 2);
  assert.equal(countsAtTime(rows, 1.2).total.stable, 0);
  assert.equal(countsAtTime(rows, 2.71).status, 'stale');
  assert.equal(countsAtTime(rows, 1.1).total.stable, 2); // Rewinding restores the recorded vote.
  assert.deepEqual(countsAtTime(rows, 1.1), countsAtTime(rows, 1.1)); // Paused playback never ages on wall time.
  const legacy = [{time: 0, detections: [detected(), detected()]}, {time: 1, detections: []}];
  assert.equal(countsAtTime(legacy, .5).total.display, '2');
  assert.equal(countsAtTime(legacy, 1).total.display, '0');
});

test('missing and malformed counts stay unavailable without prototype key collisions', () => {
  const view = countPresentation(null, {labels: ['person', '__proto__']});
  assert.equal(view.mode, 'empty');
  assert.equal(view.total.display, '—');
  assert.equal(view.classes.__proto__.stable, null);
  const unsafe = countPresentation(summary(4, {total: estimate(NaN, {raw: -1, candidate: 2.5})}));
  assert.equal(unsafe.total.stable, null);
  assert.equal(unsafe.total.raw, null);
  assert.equal(unsafe.total.candidate, null);
  assert.equal(countPresentation(summary(), {ageMs: -1}).status, 'stale');
  assert.equal(countPresentation(summary(), {ageMs: NaN}).status, 'stale');
});
