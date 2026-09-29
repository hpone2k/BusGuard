import {test} from 'node:test';
import assert from 'node:assert/strict';
import {controllerPresentation, serviceLink} from '../static/bus/src/controller-state.js';

function snapshot(doors = 'closed', ramp = 'stowed', patch = {}) {
  return {assistance: {simulation: true, active_count: 2,
    vehicle: {doors, ramp, ...patch}, announcements: ['Take your time. Access is ready.'],
    readiness: {status: 'held', message: 'Wait for assistance to finish.'}}};
}

test('twin animates only the authoritative controller phase targets', () => {
  for (const [doors, ramp, expectedDoors, expectedRamp] of [
    ['closed', 'stowed', 0, 0], ['opening', 'stowed', 1, 0],
    ['open', 'deploying', 1, 1], ['open', 'deployed', 1, 1],
    ['open', 'stowing', 1, 0], ['closing', 'stowed', 0, 0],
  ]) {
    const result = controllerPresentation(snapshot(doors, ramp));
    assert.equal(result.doors, expectedDoors); assert.equal(result.ramp, expectedRamp);
    assert.equal(result.connected, true); assert.equal(result.activeCount, 2);
    assert.match(result.detail, /Simulated access/); assert.match(result.detail, /2 active requests/);
  }
});

test('vision events and local dwell times cannot independently open the bus', () => {
  const input = snapshot();
  input.events = [{type: 'wheelchair', origin: 'vision'}, {type: 'senior', origin: 'rfid'}];
  input.assistance.readiness.minimum_dwell_remaining_ms = 0;
  const result = controllerPresentation(input);
  assert.equal(result.doors, 0); assert.equal(result.ramp, 0);
  assert.equal(result.message, input.assistance.announcements[0]);
});

test('offline, expired, unknown, restart and emergency state freeze existing geometry', () => {
  for (const result of [controllerPresentation(snapshot('open', 'deployed'), 0, false),
    controllerPresentation(snapshot('open', 'deployed'), 2000),
    controllerPresentation(snapshot('unknown', 'deployed')),
    controllerPresentation(snapshot('open', 'unknown')),
    controllerPresentation(snapshot('opening', 'deploying', {emergency: true})),
    controllerPresentation(snapshot('closed', 'stowed', {revalidation_required: true})),
    controllerPresentation(null), controllerPresentation({assistance: {simulation: false}}),
  ]) {
    assert.equal(result.doors, null); assert.equal(result.ramp, null);
  }
  assert.equal(controllerPresentation(snapshot('open', 'deployed'), 1999).doors, 1);
  assert.equal(controllerPresentation(snapshot(), 2000).activeCount, null);
});

test('central announcement and readiness messaging replace independent display decisions', () => {
  const input = snapshot();
  input.sources = {inside: {seating_summary: {standing: 1}}};
  assert.equal(controllerPresentation(input).message, 'Take your time. Access is ready.');
  input.assistance.announcements = [];
  assert.equal(controllerPresentation(input).message, 'Wait for assistance to finish.');
  input.assistance.active_count = 1;
  assert.match(controllerPresentation(input).detail, /1 active request$/);
  assert.equal(controllerPresentation(input).status, 'Simulation held');
});

test('LAN and custom ports retain the viewer hostname without pointing to phone localhost', () => {
  assert.equal(serviceLink('http://192.168.20.9:4480/bus?old=1#top', 4479), 'http://192.168.20.9:4479/');
  assert.equal(serviceLink('http://host-pc:8500/', 8501), 'http://host-pc:8501/');
  assert.equal(serviceLink('http://192.168.20.9:4479/bus'), 'http://192.168.20.9:4479/');
  assert.equal(serviceLink('http://[::1]:4480/', 4479), 'http://[::1]:4479/');
  for (const invalid of [0, -1, 65536, '4479', 3.5]) assert.throws(() => serviceLink('http://localhost:4480/', invalid));
});
