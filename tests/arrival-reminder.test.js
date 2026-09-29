import test from 'node:test';
import assert from 'node:assert/strict';
import {parseReminder, createReminder, reminderArrival, vibrateArrival} from '../static/passenger/arrival-reminder.js';

const stops = [{id: 'a'}, {id: 'b'}];
const route = (id, stop = 'a', phase = 'at_stop') => ({simulated: true, phase,
  current_stop_id: stop, arrival_event: {id, stop_id: stop}});

test('a reminder only fires at the chosen stop on a new, fresh arrival', () => {
  const reminder = createReminder('b', stops, route('previous', 'b'));
  assert.equal(reminderArrival(reminder, route('previous', 'b')), null);
  assert.equal(reminderArrival(reminder, route('next', 'a')), null);
  assert.equal(reminderArrival(reminder, route('next', 'b', 'approaching')), null);
  assert.equal(reminderArrival(reminder, route('next', 'b'), false), null);
  assert.equal(reminderArrival(reminder, {...route('next', 'b'), simulated: false}), null);
  assert.equal(reminderArrival(reminder, {...route('next', 'b'), current_stop_id: 'a'}), null);
  assert.deepEqual(reminderArrival(reminder, route('next', 'b')), {id: 'next', stop_id: 'b'});
  assert.equal(reminderArrival(null, route('next', 'b')), null);
});

test('arrival vibration respects the independent preference for regular and requested alerts', () => {
  const calls = [], device = {vibrate: pattern => { calls.push(pattern); return true; }};
  assert.equal(vibrateArrival({enabled: false, active: true, navigator: device}).attempted, false);
  assert.equal(vibrateArrival({enabled: true, active: false, navigator: device}).attempted, false);
  assert.equal(calls.length, 0);
  assert.deepEqual(vibrateArrival({enabled: true, active: true, navigator: device}), {attempted: true, supported: true, accepted: true});
  assert.deepEqual(calls, [[220, 100, 220, 100, 400]]);
});

test('unavailable or rejected vibration is reported without breaking the arrival alert', () => {
  assert.deepEqual(vibrateArrival({enabled: true, active: true, navigator: {}}), {attempted: false, supported: false});
  for (const vibrate of [() => false, () => { throw new Error('Permission denied'); }]) {
    assert.deepEqual(vibrateArrival({enabled: true, active: true, navigator: {vibrate}}), {attempted: true, supported: true, accepted: false});
  }
});

test('saved reminders retain the baseline without accepting unknown stops or extra data', () => {
  const reminder = createReminder('b', stops, route(7));
  assert.deepEqual(parseReminder(JSON.stringify({...reminder, request_token: 'private'}), stops), reminder);
  assert.equal(parseReminder('{broken', stops), null);
  assert.equal(parseReminder({...reminder, stop_id: 'unknown'}, stops), null);
  assert.equal(parseReminder({...reminder, enabled: false}, stops), null);
  assert.throws(() => createReminder('unknown', stops), /configured/);
});
