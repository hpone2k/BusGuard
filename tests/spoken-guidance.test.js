import test from 'node:test';
import assert from 'node:assert/strict';
import {SpokenGuidance} from '../static/passenger/spoken-guidance.js';
import {audioTurns, AudioTurnCoordinator} from '../static/passenger/audio-coordination.js?v=voice-flow-1';

const clip = () => new Response(new Uint8Array([1, 2, 3]), {headers: {'Content-Type': 'audio/mpeg'}});
const tick = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate) {
  for (let i = 0; i < 100; i++) { if (predicate()) return; await tick(); }
  assert.ok(predicate(), 'Expected asynchronous condition');
}

function harness(t, fetcher, {autoEnd = true, ...options} = {}) {
  const original = {}, spoken = [], audios = [], events = [], guides = [];
  for (const name of ['window', 'document', 'SpeechSynthesisUtterance', 'Audio']) original[name] = globalThis[name];
  globalThis.document = {hidden: false};
  globalThis.window = {dispatchEvent: event => events.push(event),
    speechSynthesis: {cancel() {}, getVoices: () => [], speak: value => spoken.push(value.text)}};
  globalThis.SpeechSynthesisUtterance = class {constructor(text) {this.text = text;}};
  globalThis.window.SpeechSynthesisUtterance = globalThis.SpeechSynthesisUtterance;
  globalThis.Audio = class {
    constructor(url) {this.src = url; this.paused = false;}
    async play() {audios.push(this); if (autoEnd) setTimeout(() => this.onended?.(), 1);}
    pause() {this.paused = true;}
  };
  t.mock.method(globalThis, 'fetch', fetcher);
  const create = () => {
    const guide = new SpokenGuidance({preparationTimeoutMs: 100, pollDelaysMs: [1, 2, 3], audioTimeoutMs: 500, ...options});
    guides.push(guide); return guide;
  };
  t.after(async () => {
    guides.forEach(guide => guide.dispose());
    await tick(); await tick();
    for (const [key, value] of Object.entries(original)) {
      if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
    }
  });
  return {guide: create(), create, spoken, audios, events};
}
const configured = async path => path.endsWith('/config') ? Response.json({enabled: true, default_voice: 'marin'}) : clip();

test('missing private voice configuration is explicit and never substitutes device speech', async t => {
  const paths = [];
  const {guide, spoken, audios} = harness(t, async path => {paths.push(path); return Response.json({enabled: false});});
  assert.deepEqual(await guide.speak('Please take a seat.'), {unavailable: true, reason: 'configuration'});
  assert.deepEqual(paths, ['/api/voice/config']);
  assert.deepEqual(spoken, []); assert.deepEqual(audios, []);
});

test('cold generated speech waits for its audio and only sends the configured new voice', async t => {
  let calls = 0; const voices = [];
  const {guide, spoken, audios} = harness(t, async (path, options) => {
    if (path.endsWith('/config')) return Response.json({enabled: true, default_voice: 'cedar'});
    voices.push(JSON.parse(options.body).voice);
    return ++calls < 3 ? Response.json({preparing: true}, {status: 202}) : clip();
  });
  assert.deepEqual(await guide.speak('Departure is planned in 10 seconds.'), {played: true});
  assert.deepEqual(voices, ['cedar', 'cedar', 'cedar']);
  assert.equal(audios.length, 1); assert.deepEqual(spoken, []);
});

test('an uncached preview has a bounded preparation wait and never impersonates its voice', async t => {
  const {guide, spoken, audios} = harness(t, async path => path.endsWith('/config')
    ? Response.json({enabled: true}) : Response.json({preparing: true}, {status: 202}),
  {preparationTimeoutMs: 10});
  assert.deepEqual(await guide.preview('marin'), {preparing: true});
  assert.deepEqual(spoken, []); assert.deepEqual(audios, []);
});

test('definitive speech failures are reported once without retry or device fallback', async t => {
  let calls = 0;
  const {guide, spoken} = harness(t, async path => {
    if (path.endsWith('/config')) return Response.json({enabled: true});
    calls++; return Response.json({detail: 'Unavailable'}, {status: 429});
  });
  assert.deepEqual(await guide.speak('Please wait.'), {unavailable: true, reason: 'rate_limit', status: 429});
  assert.equal(calls, 1); assert.deepEqual(spoken, []);
});

test('cancelling during configuration prevents later audio', async t => {
  let resolve;
  const {guide, spoken, audios} = harness(t, () => new Promise(done => {resolve = done;}));
  const pending = guide.speak('Obsolete announcement.');
  guide.cancel(); resolve(Response.json({enabled: true}));
  assert.deepEqual(await pending, {cancelled: true, reason: 'cancelled'});
  await tick();
  assert.deepEqual(spoken, []); assert.deepEqual(audios, []);
});

test('cached audio is shared across instances without generating a second clip', async t => {
  let speechCalls = 0;
  const {guide, create, spoken, audios} = harness(t, async path => {
    if (path.endsWith('/config')) return Response.json({enabled: true, default_voice: 'marin'});
    speechCalls++; return clip();
  });
  await guide.speak('Welcome aboard.'); await create().speak('Welcome aboard.');
  assert.equal(speechCalls, 1); assert.equal(audios.length, 2); assert.deepEqual(spoken, []);
});

test('all instances are FIFO and wait for ended rather than the play promise', async t => {
  const {guide, create, audios} = harness(t, configured, {autoEnd: false});
  const other = create();
  let firstDone = false;
  const first = guide.speak('First announcement.').then(result => {firstDone = true; return result;});
  const second = other.speak('Second announcement.');
  await until(() => audios.length === 1);
  await tick();
  assert.equal(firstDone, false); assert.equal(audios.length, 1);
  assert.equal(audios[0].paused, false);
  audios[0].onended();
  await until(() => audios.length === 2);
  assert.deepEqual(await first, {played: true});
  audios[1].onended(); assert.deepEqual(await second, {played: true});
});

test('duplicate pending messages share a job without cutting off or repeating speech', async t => {
  const {guide, audios} = harness(t, configured, {autoEnd: false});
  const one = guide.speak('Same message.');
  assert.equal(guide.speak('Same message.'), one);
  await until(() => audios.length === 1);
  assert.equal(guide.speak('Same message.'), one);
  audios[0].onended(); await one;
  assert.equal(audios.length, 1);
});

test('superseded grouped updates skip old queued speech but finish the playing sentence', async t => {
  const {guide, audios} = harness(t, configured, {autoEnd: false});
  const current = guide.speak('First status.', {group: 'request'});
  await until(() => audios.length === 1);
  const old = guide.speak('Old next status.', {group: 'request'});
  const latest = guide.speak('Latest status.', {group: 'request'});
  assert.deepEqual(await old, {cancelled: true, reason: 'superseded'});
  assert.equal(audios[0].paused, false);
  audios[0].onended(); await current;
  await until(() => audios.length === 2);
  audios[1].onended(); assert.deepEqual(await latest, {played: true});
});

test('cancelling one instance does not stop another instance’s current sentence', async t => {
  const {guide, create, audios} = harness(t, configured, {autoEnd: false});
  const other = create(), current = guide.speak('Keep speaking.');
  await until(() => audios.length === 1);
  const next = other.speak('Cancelled future message.');
  other.cancel();
  assert.deepEqual(await next, {cancelled: true, reason: 'cancelled'});
  assert.equal(audios[0].paused, false);
  audios[0].onended(); assert.deepEqual(await current, {played: true});
  await tick(); assert.equal(audios.length, 1);
});

test('announcements and Realtime own mutually exclusive playback turns', async t => {
  const {guide, audios} = harness(t, configured, {autoEnd: false});
  const reply = await audioTurns.acquire({kind: 'realtime', owner: 'assistant'});
  const speech = guide.speak('Announcement after the reply.');
  await until(() => audioTurns.state.announcementPending);
  assert.equal(audios.length, 0);
  reply.release();
  await until(() => audios.length === 1);
  let granted = false;
  const nextReply = audioTurns.acquire({kind: 'realtime', owner: 'assistant'}).then(turn => {granted = true; return turn;});
  await tick(); assert.equal(granted, false);
  audios[0].onended(); await speech;
  const nextTurn = await nextReply; assert.equal(granted, true);
  nextTurn.release(); assert.equal(audioTurns.state.kind, null);
});

test('time-sensitive speech expires while waiting for Realtime and never plays late', async t => {
  const {guide, audios} = harness(t, configured);
  const reply = await audioTurns.acquire({kind: 'realtime', owner: 'assistant'});
  try {
    assert.deepEqual(await guide.speak('Leaving very soon.', {expiresMs: 8}), {cancelled: true, reason: 'expired'});
    assert.equal(audios.length, 0);
  } finally {reply.release();}
});

test('audio errors release ownership so the following announcement can finish', async t => {
  const {guide, audios} = harness(t, configured, {autoEnd: false});
  const failed = guide.speak('A broken audio file.'), next = guide.speak('The next announcement.');
  await until(() => audios.length === 1);
  audios[0].onerror();
  assert.deepEqual(await failed, {unavailable: true, reason: 'playback'});
  await until(() => audios.length === 2);
  audios[1].onended(); assert.deepEqual(await next, {played: true});
});

test('a missing media ended event cannot block the entire queue indefinitely', async t => {
  const {guide, audios} = harness(t, configured, {autoEnd: false, audioTimeoutMs: 8});
  assert.deepEqual(await guide.speak('Broken player.'), {unavailable: true, reason: 'playback_timeout'});
  assert.equal(audios[0].paused, true); assert.equal(audioTurns.state.kind, null);
});

test('queue capacity is bounded even when audio output remains occupied', async t => {
  const {guide} = harness(t, configured);
  const reply = await audioTurns.acquire({kind: 'realtime', owner: 'assistant'});
  const jobs = Array.from({length: 16}, (_, i) => guide.speak(`Queued sentence ${i}.`));
  assert.deepEqual(await guide.speak('Over capacity.'), {unavailable: true, reason: 'queue_full'});
  guide.cancel(); await Promise.all(jobs); reply.release(); await tick();
});

test('audio coordinator cancels queued turns and release is idempotent', async () => {
  const coordinator = new AudioTurnCoordinator();
  const first = await coordinator.acquire({kind: 'realtime', owner: 'one'});
  const controller = new AbortController();
  const pending = coordinator.acquire({kind: 'announcement', owner: 'two', signal: controller.signal});
  assert.equal(coordinator.state.announcementPending, true);
  controller.abort(); await assert.rejects(pending, {name: 'AbortError'});
  first.release(); first.release();
  assert.deepEqual(coordinator.state, {kind: null, waiting: 0, announcementPending: false});
});
