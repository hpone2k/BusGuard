import {test} from 'node:test';
import assert from 'node:assert/strict';
import {SEATS, SCENARIOS, boardingState} from '../static/bus/src/simulation.js';

test('bus has exactly 6 priority and 20 standard seats with distinct positions', () => {
  assert.equal(SEATS.length, 26);
  assert.equal(SEATS.filter(s => s.type === 'priority').length, 6);
  assert.equal(SEATS.filter(s => s.type === 'standard').length, 20);
  assert.equal(new Set(SEATS.map(s => s.id)).size, 26);
  assert.equal(new Set(SEATS.map(s => `${s.x}:${s.z}`)).size, 26);
  assert(SEATS.every(s => Math.abs(s.z) > .35)); // Continuous centre aisle.
  assert(SEATS.filter(s => s.type === 'priority').every(s => Math.abs(s.x - 3.23) > .68 && s.y === .58));
});

test('ramp deploys only after the doors open and retracts before they close', () => {
  for (const scenario of Object.keys(SCENARIOS)) {
    for (const extra of [8, 12, 24]) {
      const end = boardingState(scenario, Infinity, extra); // Invalid clocks reset.
      assert.equal(end.t, 0);
      for (let t = 0; t < 40; t += .05) {
        const s = boardingState(scenario, t, extra);
        if (s.ramp > .001) assert(s.doors > .999, `${scenario} t=${t}`);
        if (s.boarding > 0 && s.boarding < 1) {
          assert.equal(s.doors, 1);
          if (SCENARIOS[scenario].ramp) assert.equal(s.ramp, 1);
        }
        assert(s.remaining >= 0);
      }
    }
  }
});

test('RFID senior assistance is explicit and resets deterministically', () => {
  assert.equal(SCENARIOS.senior.source, 'Concession RFID');
  assert.equal(boardingState('senior', 6).ramp, 0);
  assert.equal(boardingState('senior', 100).seat, 'P2');
  assert.equal(SEATS.find(s => s.id === SCENARIOS.senior.seat).z, -.47);
  assert.equal(SEATS.find(s => s.id === SCENARIOS.walking.seat).z, -.47);
  assert.equal(boardingState('wheelchair', 100).seat, null);
  assert.equal(boardingState('senior', 0).seat, null);
  assert.equal(boardingState('senior', 100).doors, 0);
  assert.equal(boardingState('wheelchair', 100).ramp, 0);
  assert.equal(boardingState('senior', 100).complete, true);
  assert.throws(() => boardingState('age-estimation'));
});
