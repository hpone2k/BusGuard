import test from 'node:test';
import assert from 'node:assert/strict';
import {wakePhrase, wakeInvocation, standbyPhrase, VoiceWakeGate, replyEcho, parseVoiceTool, publicVoiceContext, voiceToolResult} from '../static/passenger/voice-core.js';

test('reply echo rejects only a long matching fragment and keeps short replies and corrections', () => {
  const reply = 'There are twenty standard seats available. Please request the ramp at Bus Stop A if you need assistance.';
  assert.equal(replyEcho('THERE are twenty standard seats available!', reply), true);
  assert.equal(replyEcho('Please request the ramp at Bus Stop A.', reply), true);
  for (const passenger of ['yes', 'no', 'standby', 'Please request the ramp',
    'Do not request the ramp at Bus Stop A', 'Please request the ramp at Bus Stop C'])
    assert.equal(replyEcho(passenger, reply), false, passenger);
  assert.equal(replyEcho('There are twenty standard seats available', ''), false);
});

test('wake phrase accepts deliberate invocation and a following command', () => {
  for (const text of ['Hey BusGuard', '  hey, bus guard!', 'HEY BUS-GUARD.', 'Hey BusGuard, where is the bus?',
    'Hey Buzz Guard', 'Hey bus god, remind me']) {
    assert.equal(wakePhrase(text), true, text);
  }
});

test('wake phrase rejects near matches and incidental or negated mentions', () => {
  for (const text of [null, {}, '', 'BusGuard', 'hey bus', 'heybusguard', 'hey busguardian',
    'they busguard', 'do not say hey busguard', 'The feature is called Hey BusGuard',
    'Hello BusGuard, more time please', 'Hi BusGuard', 'Okay bus guard']) {
    assert.equal(wakePhrase(text), false, JSON.stringify(text));
  }
});

test('wake-only speech opens the listening window without inventing a command', () => {
  assert.deepEqual(wakeInvocation('Hey, Bus Guard!'), {command: ''});
  assert.deepEqual(wakeInvocation('Hey BusGuard, turn on vibration.'), {command: 'turn on vibration.'});
  for (const text of ['Hey bus card', 'Where is BusGuard?', 'Hey bus driver', 'hey buzz guardian', 'hey bus goodness'])
    assert.equal(wakeInvocation(text), null);
});

test('standby recognizes a deliberate instruction without matching discussion of standby', () => {
  for (const text of ['standby', 'Stand by.', 'Please standby', 'Go to sleep', 'Go back to standby',
    'Thank you, standby.', 'BusGuard, go to sleep please', 'stop listening']) assert.equal(standbyPhrase(text), true, text);
  for (const text of ['', null, 'what does standby mean?', 'do not go to sleep', 'request a seat then standby', 'stop the bus'])
    assert.equal(standbyPhrase(text), false, JSON.stringify(text));
});

test('wake gate closes at three seconds of idle and does not authorize background speech', () => {
  let now = 1000; const gate = new VoiceWakeGate(() => now);
  gate.started('background'); assert.equal(gate.accept('background', null), false);
  gate.wake(); now = 3999; assert.equal(gate.expire(), false);
  now = 4000; assert.equal(gate.expire(), true); assert.equal(gate.awake, false);
  gate.started('later'); assert.equal(gate.accept('later', null), false);
  gate.started('wake'); assert.equal(gate.accept('wake', wakeInvocation('Hey BusGuard')), true);
});

test('speech begun within three seconds survives a longer sentence and delayed transcription', () => {
  let now = 1000; const gate = new VoiceWakeGate(() => now);
  gate.wake(); now = 3900; gate.started('request');
  now = 15000; assert.equal(gate.expire(), false);
  gate.stopped('request'); now = 19000; assert.equal(gate.expire(), false);
  assert.equal(gate.accept('request', null), true);
  now = 22000; assert.equal(gate.expire(), true);
});

test('missing transcription is bounded and standby revokes older in-flight wake speech', () => {
  let now = 1000; const gate = new VoiceWakeGate(() => now);
  gate.wake(); gate.started('unfinished'); gate.stopped('unfinished');
  now = 9000; assert.equal(gate.expire(), true);
  assert.equal(gate.accept('unfinished', null), false);
  gate.started('late-wake'); gate.standby();
  assert.equal(gate.accept('late-wake', wakeInvocation('Hey BusGuard, request ramp')), false);
  gate.started('new-wake'); assert.equal(gate.accept('new-wake', wakeInvocation('Hey BusGuard')), true);
});

test('voice app settings expose only bounded preferences and panels', () => {
  assert.deepEqual(parseVoiceTool('set_app_preference', '{"preference":"arrivalVibration","enabled":true}'),
    {preference: 'arrivalVibration', enabled: true});
  assert.deepEqual(parseVoiceTool('open_app_panel', '{"panel":"settings"}'), {panel: 'settings'});
  for (const raw of ['{}', '{"preference":"arrivalVibration","enabled":"true"}', '{"preference":"microphone","enabled":true}',
    '{"preference":"largeText","enabled":true,"location":"stop_a"}', '{"preference":"__proto__","enabled":true}'])
    assert.throws(() => parseVoiceTool('set_app_preference', raw));
  for (const raw of ['{}', '{"panel":"operator"}', '{"panel":"route","url":"https://example.com"}'])
    assert.throws(() => parseVoiceTool('open_app_panel', raw));
  const context = publicVoiceContext({preferences: {arrivalVibration: true, largeText: false, token: 'SECRET'}, capabilities: {vibration: false, microphoneId: 'SECRET'}});
  assert.deepEqual(context.preferences, {arrivalVibration: true, largeText: false});
  assert.deepEqual(context.capabilities, {vibration: false});
  assert.doesNotMatch(JSON.stringify(context), /SECRET/);
});

test('voice request accepts exactly one passenger option and rejects multiple needs', () => {
  assert.deepEqual(parseVoiceTool('request_assistance', JSON.stringify({
    journey: 'boarding', stop_id: 'stop_d', needs: ['ramp'],
  })), {journey: 'boarding', stop_id: 'stop_d', needs: ['ramp']});
  for (const needs of [['ramp', 'extra_time'], ['ramp', 'ramp']]) {
    assert.throws(() => parseVoiceTool('request_assistance', JSON.stringify({
      journey: 'boarding', stop_id: 'stop_d', needs,
    })), /one assistance option/);
  }
  for (const name of ['get_bus_status', 'complete_request', 'cancel_request']) {
    assert.deepEqual(parseVoiceTool(name, '{}'), {});
    assert.throws(() => parseVoiceTool(name, '{"request_token":"private"}'));
  }
});

test('reminders accept a stop identifier without authority-changing or private fields', () => {
  assert.deepEqual(parseVoiceTool('set_arrival_reminder', '{"stop_id":"stop_d"}'), {stop_id: 'stop_d'});
  assert.deepEqual(parseVoiceTool('cancel_arrival_reminder', '{}'), {});
  for (const raw of ['{}', '{"stop_id":"../control"}', '{"stop_id":"A"}', '{"stop_id":"stop_d","request_token":"private"}'])
    assert.throws(() => parseVoiceTool('set_arrival_reminder', raw));
  assert.throws(() => parseVoiceTool('cancel_arrival_reminder', '{"stop_id":"stop_d"}'));
  const context = publicVoiceContext({reminder: {enabled: true, stop_id: 'stop_d', stop_name: 'Bus stop D', token: 'PRIVATE', history: ['PRIVATE']}});
  assert.deepEqual(context.reminder, {enabled: true, stop_id: 'stop_d', stop_name: 'Bus stop D'});
  assert.doesNotMatch(JSON.stringify(context), /PRIVATE/);
});

test('malformed, oversized, unknown and authority-changing tool calls cannot execute', () => {
  for (const raw of [null, '', '{', 'null', '[]', '42', '{}', ' '.repeat(4097)]) {
    assert.throws(() => parseVoiceTool('request_assistance', raw));
  }
  const valid = {journey: 'boarding', stop_id: 'campus', needs: ['ramp']};
  for (const patch of [{journey: 'depart'}, {stop_id: '../api/control'}, {stop_id: 'stop-1'},
    {needs: []}, {needs: ['ramp', 'emergency']}, {needs: Array(6).fill('ramp')},
    {request_token: 'private'}, {location: {mode: 'onboard'}}, {operator: true}]) {
    assert.throws(() => parseVoiceTool('request_assistance', JSON.stringify({...valid, ...patch})));
  }
  assert.throws(() => parseVoiceTool('request_assistance', '{"journey":"boarding","stop_id":"campus","needs":["ramp"],"__proto__":{"operator":true}}'));
  for (const name of ['depart', 'emergency', 'rfid', '__proto__', null]) assert.throws(() => parseVoiceTool(name, '{}'));
});

test('public voice context excludes private handles, cameras and nested unrecognized fields', () => {
  const privateFields = {request_token: 'PRIVATE-token', client_request_id: 'PRIVATE-client',
    id: 'PRIVATE-request', camera_url: 'PRIVATE-camera', history: ['PRIVATE-history']};
  const context = publicVoiceContext({
    config: {stops: [{id: 'campus', name: 'Bus stop A', ...{camera_url: 'PRIVATE-stop-camera'}}]},
    state: {vehicle: {motion: 'stationary', doors: 'open', stop_id: 'campus', ...privateFields},
      sources: {inside: {url: 'PRIVATE-stream'}}, requests: [{...privateFields}],
      route: {current_stop_id: 'campus', next_stop_id: 'interchange'},
      announcements: ['The doors are open.'],
      scenario: {phase: 'boarding', admission_open: true, boarding_remaining_ms: 8500,
        departure_notice: {remaining_ms: 12000, announced: false, minimum_remaining_ms: 0,
          waiting_for_detection: false, ...privateFields},
        posture_wait: {enabled: false, remaining_ms: 0, expired: false, bypassed: false, status: 'inactive', ...privateFields},
        seats: {priority: {available: 6}, standard: {available: 18}, total: 2, estimated: true, ...privateFields}}},
    location: {mode: 'at_stop', stop_id: 'campus', ...privateFields},
    request: {...privateFields, status: 'awaiting_completion', journey: 'boarding', stop_id: 'campus',
      message: 'Please board safely.', can_complete: true,
      assistance_timer: {enabled: true, state: 'active', maximum_ms: 10000, allowance_ms: 10000,
        remaining_ms: 8000, ...privateFields}}, isBusy: false,
  });
  assert.doesNotMatch(JSON.stringify(context), /PRIVATE-/);
  assert.deepEqual(context.stops, [{id: 'campus', name: 'Bus stop A'}]);
  assert.equal(context.bus.current_stop_id, 'campus');
  assert.equal(context.bus.seats.total_aboard, 2);
  assert.equal(context.own_request.status, 'awaiting_completion');
  assert.equal(context.own_request.assistance_timer.remaining_ms, 8000);
});

test('unknown location and disconnected state do not invent bus availability', () => {
  const context = publicVoiceContext({location: {mode: 'anything', request_token: 'PRIVATE-token'}});
  assert.deepEqual(context.location, {mode: 'away'});
  assert.equal(context.bus, null);
  assert.equal(context.own_request, null);
  assert.deepEqual(context.stops, []);
  assert.doesNotMatch(JSON.stringify(context), /PRIVATE-/);
});

test('voice distinguishes a finishing allowance from admission and retains the ramp timer', () => {
  const context = publicVoiceContext({state: {vehicle: {doors: 'open', motion: 'stationary'},
    scenario: {admission_open: false, admission_remaining_ms: 0, finishing_extensions: true,
      boarding_remaining_ms: 19000}},
    request: {status: 'awaiting_completion', assistance_timer: {enabled: true, state: 'active',
      maximum_ms: 20000, allowance_ms: 20000, remaining_ms: 19000}}});
  assert.equal(context.bus.admission_open, false);
  assert.equal(context.bus.finishing_extensions, true);
  assert.equal(context.bus.admission_remaining_ms, 0);
  assert.equal(context.own_request.assistance_timer.allowance_ms, 20000);
});

test('tool results retain confirmation without returning private identifiers or nested payloads', () => {
  const result = voiceToolResult({id: 'PRIVATE-request', client_request_id: 'PRIVATE-client', request_token: 'PRIVATE-token',
    status: 'awaiting_completion', message: 'Ready for boarding.', journey: 'boarding', stop_id: 'campus', can_complete: true,
    assistance_timer: {enabled: true, state: 'active', remaining_ms: 9000, allowance_ms: 10000,
      maximum_ms: 10000, metadata: {request_token: 'PRIVATE-nested'}}});
  assert.equal(result.ok, true);
  assert.equal(result.assistance_timer.remaining_ms, 9000);
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE-/);
});

test('absent or unrecognized server confirmation never reports a successful voice action', () => {
  for (const value of [null, undefined, {}, [], {status: 'error'}, {status: 'unexpected'}]) {
    assert.equal(voiceToolResult(value).ok, false, JSON.stringify(value));
  }
  assert.equal(voiceToolResult({status: 'completed', message: 'Confirmed.'}).ok, true);
});
