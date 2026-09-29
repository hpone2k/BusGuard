import test from 'node:test';
import assert from 'node:assert/strict';
import {cameraCheckPresentation} from '../static/camera-check.js';

const seats = (check = {}) => ({confirmed_total: 4, camera_check: {status: 'collecting',
  collected: 12, required: 30, remaining_seconds: 18, last_mean: null, last_rounded: null,
  cycle_completed_at: null, mismatch: null, ...check}});

test('camera-off state keeps recorded passengers separate from unavailable observations', () => {
  const view = cameraCheckPresentation(seats({status: 'unavailable', collected: 0}));
  assert.equal(view.recorded, 'Recorded passengers: 4');
  assert.match(view.progress, /unavailable/);
  assert.equal(view.result, 'No completed camera check.');
  assert.equal(view.difference, '');
  assert.doesNotMatch(view.progress, /0 people/);
});

test('sampling progress does not present a partial cycle as a completed mean', () => {
  const view = cameraCheckPresentation(seats());
  assert.match(view.progress, /12 \/ 30 samples · 18s remaining/);
  assert.match(view.progress, /one sample each second/);
  assert.equal(view.result, 'No completed camera check.');
  const interrupted = cameraCheckPresentation(seats({status: 'incomplete', collected: 23}));
  assert.match(interrupted.progress, /incomplete · 23 \/ 30/);
  assert.match(interrupted.progress, /not counted as zero/);
});

test('completed and retained means distinguish fractional average from the rounded estimate', () => {
  for (const status of ['complete', 'collecting', 'unavailable']) {
    const view = cameraCheckPresentation(seats({status, last_mean: 5.2, last_rounded: 5, mismatch: 1}));
    assert.equal(view.result, 'Last complete cycle: mean 5.20 → 5 people.');
    assert.equal(view.difference, 'Last check: 1 person more than the passenger records.');
  }
  const empty = cameraCheckPresentation(seats({status: 'complete', last_mean: 0, last_rounded: 0, mismatch: -4}));
  assert.equal(empty.result, 'Last complete cycle: mean 0.00 → 0 people.');
  assert.match(empty.difference, /4 people fewer/);
  const matched = cameraCheckPresentation(seats({last_mean: 4, last_rounded: 4, mismatch: 0}));
  assert.equal(matched.difference, 'Last check matched the passenger records.');
});

test('disconnected controller does not present a saved count as current', () => {
  const view = cameraCheckPresentation(seats({last_mean: 4, last_rounded: 4, mismatch: 0}), false);
  assert.equal(view.recorded, 'Recorded passengers: —');
  assert.match(view.progress, /Controller offline/);
  assert.equal(view.result, 'No completed camera check.');
  assert.equal(view.difference, '');
});

test('malformed values never become NaN, misleading zeros, or injected copy', () => {
  for (const value of [NaN, Infinity, -1, '4', true, null, undefined]) {
    const view = cameraCheckPresentation({confirmed_total: value, camera_check: {
      status: 'collecting', collected: value, remaining_seconds: value,
      last_mean: value, last_rounded: value, mismatch: value}});
    assert.equal(view.recorded, 'Recorded passengers: —');
    assert.equal(view.result, 'No completed camera check.');
    assert.equal(view.difference, '');
    assert.doesNotMatch(view.progress, /NaN|Infinity|null|undefined/);
  }
  assert.equal(cameraCheckPresentation({confirmed_total: 4}).status, 'unavailable');
  assert.equal(cameraCheckPresentation(seats({status: '<img src=x>'})).status, 'unavailable');
});
