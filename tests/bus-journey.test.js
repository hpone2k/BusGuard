import {test} from 'node:test';
import assert from 'node:assert/strict';
import {VirtualJourney} from '../static/bus/src/journey.js';

test('virtual motion starts only with departure permission and stops without coasting when permission is lost', () => {
  const journey = new VirtualJourney();
  for (const permission of [false, null, undefined, 'true']) assert.equal(journey.step(permission, .1).distance, 0);
  for (let i = 0; i < 60; i++) journey.step(true, .1);
  assert.equal(journey.speed * 3.6, 8);
  const before = journey.distance;
  const held = journey.step(false, .1);
  assert.equal(held.speedKph, 0);
  assert.equal(held.distance, before);
  assert.equal(journey.step(false, 100).distance, before);
  journey.reset();
  assert.equal(journey.distance, 0);
  assert.equal(journey.speed, 0);
});

test('invalid and suspended render clocks do not jump the virtual journey', () => {
  const journey = new VirtualJourney();
  for (const dt of [-1, NaN, Infinity]) assert.equal(journey.step(true, dt).distance, 0);
  assert(journey.step(true, 60).distance < .01);
});
