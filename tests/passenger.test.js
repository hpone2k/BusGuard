import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {makeIdentity, parseSaved, requestBody, requestPresentation, connectionPresentation} from '../static/passenger/core.js';

test('passenger identities are secure and work without the HTTPS-only randomUUID API', () => {
  const crypto = {getRandomValues: bytes => webcrypto.getRandomValues(bytes)};
  const first = makeIdentity(crypto), second = makeIdentity(crypto);
  assert.match(first.request_token, /^[a-f0-9]{64}$/);
  assert.match(first.client_request_id, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  assert.notEqual(first.request_token, second.request_token);
  assert.notEqual(first.client_request_id, second.client_request_id);
  assert.throws(() => makeIdentity({}), /secure request/);
});
test('restoring an uncertain submission keeps the exact retry identity and single choice', () => {
  const body = requestBody(makeIdentity(webcrypto), 'alighting', 'campus', ['ramp'], {mode: 'onboard'});
  const saved = parseSaved(JSON.stringify({body, id: null}));
  assert.deepEqual(saved.body, body);
  assert.equal(saved.id, null);
  assert.deepEqual(saved.body.needs, ['ramp']);
  assert.equal(parseSaved('{corrupt'), null);
  assert.equal(parseSaved(JSON.stringify({body: {...body, request_token: 'guessable'}})), null);
  assert.equal(parseSaved(JSON.stringify({body: {...body, needs: ['unknown']}})), null);
});
test('empty and invalid requests do not become pending requests', () => {
  assert.throws(() => requestBody(makeIdentity(webcrypto), 'boarding', 'campus', []), /one assistance option/);
  assert.throws(() => requestBody(makeIdentity(webcrypto), 'boarding', '', ['ramp']), /where/);
});

test('new requests reject multiple choices for both form and voice callers', () => {
  for (const needs of [['ramp', 'extra_time'], ['ramp', 'ramp'], ['ramp', 'unknown'], null, 'ramp']) {
    assert.throws(() => requestBody(makeIdentity(webcrypto), 'boarding', 'campus', needs,
      {mode: 'at_stop', stop_id: 'campus'}), /one assistance option/);
  }
});

test('old accepted multi-option requests remain readable with their original retry identity', () => {
  const body = {...makeIdentity(webcrypto), journey: 'boarding', stop_id: 'campus', needs: ['ramp', 'visual']};
  const saved = parseSaved(JSON.stringify({body, id: 'accepted-before-update'}));
  assert.deepEqual(saved.body, body);
  assert.equal(saved.id, 'accepted-before-update');
});
test('passenger completion is available only with server permission at the correct stage', () => {
  for (const status of ['accepted', 'queued', 'assisting', 'completed', 'cancelled', 'error', 'unknown']) {
    assert.equal(requestPresentation({status, can_complete: true}).canComplete, false);
  }
  assert.equal(requestPresentation({status: 'awaiting_completion', can_complete: false}).canComplete, false);
  assert.equal(requestPresentation({status: 'awaiting_completion', can_complete: true}).canComplete, true);
  assert.equal(requestPresentation({status: 'awaiting_completion', can_complete: true, passenger_confirmed: true}).canComplete, false);
});
test('alighting instructions differ from boarding and do not announce completion before the server', () => {
  const waiting = requestPresentation({status: 'awaiting_completion', journey: 'alighting', passenger_confirmed: true, message: 'Waiting for the simulated ramp to stow.'});
  assert.equal(waiting.title, 'Thank you for confirming');
  assert.equal(waiting.terminal, false);
  assert.match(waiting.completeLabel, /exited/);
  assert.match(waiting.message, /ramp/);
  assert.equal(requestPresentation({status: 'completed', can_cancel: true}).canCancel, false);
});
test('connection freshness never silently treats old data as live', () => {
  assert.equal(connectionPresentation(0, 1000, false).state, 'connecting');
  assert.equal(connectionPresentation(0, 1000, true).state, 'offline');
  assert.equal(connectionPresentation(1000, 5000).state, 'online');
  assert.equal(connectionPresentation(1000, 7000).state, 'offline');
  assert.equal(connectionPresentation(1000, 1500, true).state, 'offline');
});
