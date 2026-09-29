import test from 'node:test';
import assert from 'node:assert/strict';
import {setImmediate as settle} from 'node:timers/promises';
import {mountVoiceAssistant} from '../static/passenger/voice.js';
import {audioTurns} from '../static/passenger/audio-coordination.js?v=voice-flow-1';

class Element {
  constructor(value = '') { this.value = value; this.listeners = new Map(); this.children = []; this.dataset = {}; this.classList = {add() {}}; }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  removeEventListener(name) { this.listeners.delete(name); }
  append(...items) { this.children.push(...items); }
  async click() { return this.listeners.get('click')?.(); }
}

async function harness({pendingSession = false, onRequest = async () => ({status: 'accepted'}), onActivityChange,
  onReminder, onPreference, onPanel, onStatusChange, permissionState, secure = true, storedPreference, sessionStatus = 200, maxSessionSeconds = 3300,
  acknowledgeClears = true, audioPlay = () => Promise.resolve()} = {}) {
  const keys = ['window', 'document', 'navigator', 'RTCPeerConnection', 'Audio', 'fetch', 'setTimeout', 'setInterval', 'localStorage'];
  const descriptors = Object.fromEntries(keys.map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const nativeTimeout = globalThis.setTimeout;
  const nativeInterval = globalThis.setInterval;
  const elements = new Map();
  const host = new Element(); host.querySelector = selector => {
    const key = selector.match(/data-voice="([^"]+)"/)[1];
    if (!elements.has(key)) elements.set(key, new Element(key === 'choice' ? 'marin' : ''));
    return elements.get(key);
  };
  const sent = [], fetches = [], timeouts = [], intervals = [], mediaConstraints = [], tracks = [{stopped: false, enabled: true, stop() { this.stopped = true; }}];
  const storage = new Map(storedPreference ? [['busguard.voice.listening', storedPreference]] : []);
  let mediaCalls = 0;
  const channel = {readyState: 'open', send: json => {
    const event = JSON.parse(json); sent.push(event);
    if (acknowledgeClears && event.type === 'input_audio_buffer.clear') queueMicrotask(() => {
      if (channel.readyState === 'open') channel.onmessage({data: JSON.stringify({type: 'input_audio_buffer.cleared'})});
    });
  }, close() { this.readyState = 'closed'; }};
  let peer, resolveSession;
  const sessionReply = new Promise(resolve => { resolveSession = resolve; });
  class Peer {
    constructor() { peer = this; this.iceGatheringState = 'complete'; }
    addTrack() {} createDataChannel() { channel.readyState = 'open'; return channel; }
    async createOffer() { return {type: 'offer', sdp: 'v=offer'}; }
    async setLocalDescription(value) { this.localDescription = value; }
    async setRemoteDescription() { channel.onopen(); }
    close() { this.closed = true; }
  }
  const window = new EventTarget(); window.isSecureContext = secure; window.RTCPeerConnection = Peer;
  window.speechSynthesis = {cancel() {}};
  const document = new EventTarget(); document.hidden = false;
  document.createElement = () => new Element(); document.createTextNode = text => text;
  const globals = {window, document, navigator: {mediaDevices: {getUserMedia: async constraints => { mediaCalls++; mediaConstraints.push(constraints); return {getTracks: () => tracks}; }},
      ...(permissionState ? {permissions: {query: async () => ({state: permissionState})}} : {})},
    RTCPeerConnection: Peer, Audio: class { play() { return audioPlay(); } pause() {} },
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
    setTimeout: (fn, ms, ...args) => { const handle = nativeTimeout(fn, ms, ...args); timeouts.push({fn, ms, handle}); if (ms >= 10000) handle.unref(); return handle; },
    setInterval: (fn, ms, ...args) => { const handle = nativeInterval(fn, ms, ...args); intervals.push({fn, ms, handle}); handle.unref(); return handle; },
    fetch: async (url, options) => {
      fetches.push({url, options});
      if (url === '/api/voice/config') return {ok: true, json: async () => ({enabled: true, default_voice: 'marin', max_session_seconds: maxSessionSeconds})};
      if (url === '/api/voice/session') {
        if (pendingSession) await sessionReply;
        return {ok: sessionStatus === 200, status: sessionStatus, json: async () => sessionStatus === 200
          ? {sdp: 'v=answer', session_id: 'private-session'} : {detail: 'Voice access needs attention.'}};
      }
      if (url === '/api/assistance/state') return {ok: true, json: async () => ({simulation: true, vehicle: {stop_id: 'campus'}})};
      return {ok: true, json: async () => ({})};
    }};
  for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, {value, configurable: true, writable: true});
  const app = mountVoiceAssistant({host, getContext: () => ({location: {mode: 'at_stop', stop_id: 'campus'}, isBusy: false}),
    getFreshRequest: async () => null, onRequest, onComplete: async () => ({status: 'completed'}), onCancel: async () => ({status: 'cancelled'}), onActivityChange, onReminder, onPreference, onPanel, onStatusChange});
  await settle();
  return {app, elements, sent, fetches, timeouts, intervals, tracks, mediaConstraints, storage, get mediaCalls() { return mediaCalls; }, get peer() { return peer; }, resolveSession,
    start: () => elements.get('start').click(),
    async event(event) { channel.onmessage({data: JSON.stringify(event)}); await settle(); },
    latestTurn: () => sent.filter(event => event.type === 'response.create').at(-1)?.response.metadata.busguard_turn,
    async dispose() {
      app.dispose(); await settle();
      timeouts.forEach(({handle}) => clearTimeout(handle));
      intervals.forEach(({handle}) => clearInterval(handle));
      for (const key of keys) {
        if (descriptors[key]) Object.defineProperty(globalThis, key, descriptors[key]); else delete globalThis[key];
      }
    }};
}

async function utterance(h, item = 'user-1', text = 'Hey BusGuard, request more time.') {
  await h.event({type: 'input_audio_buffer.speech_started', item_id: item});
  await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: item, transcript: text});
  return h.latestTurn();
}
const call = id => ({type: 'function_call', call_id: id, name: 'request_assistance', arguments: JSON.stringify({journey: 'boarding', stop_id: 'campus', needs: ['extra_time']})});
const response = (type, turn, id, status = 'completed', calls = []) => ({type, response: {id, metadata: {busguard_turn: turn}, status, output: calls}});
const toolOutputs = h => [...new Map(h.sent.filter(event => event.type === 'response.create')
  .flatMap(event => event.response.input || []).filter(item => item.type === 'function_call_output')
  .map(item => [item.call_id, item])).values()];

test('configured voice default is honored until the passenger explicitly chooses another voice', async () => {
  const h = await harness();
  try {
    assert.equal(h.elements.get('choice').value, 'marin');
    h.elements.get('choice').value = 'cedar'; h.elements.get('choice').listeners.get('change')();
    await h.app.refresh(); assert.equal(h.elements.get('choice').value, 'cedar');
  } finally { await h.dispose(); }
});

test('external voice controls receive public activity state during connection, listening and remote disconnection', async () => {
  const activity = [];
  const h = await harness({onActivityChange: state => activity.push(state)});
  try {
    assert.deepEqual(activity.at(-1), {listening: false, connecting: false});
    const starting = h.start();
    assert.deepEqual(activity.at(-1), {listening: false, connecting: true});
    await starting;
    assert.deepEqual(activity.at(-1), {listening: true, connecting: false});
    h.peer.connectionState = 'disconnected'; h.peer.onconnectionstatechange();
    assert.deepEqual(activity.at(-1), {listening: false, connecting: false});
    assert.equal(h.tracks[0].stopped, true);
    assert.ok(activity.every(state => Object.keys(state).sort().join(',') === 'connecting,listening'));
  } finally { await h.dispose(); }
});

test('a broken activity display cannot prevent voice connection or microphone cleanup', async () => {
  const h = await harness({onActivityChange: () => { throw new Error('Display unavailable'); }});
  try {
    await h.start();
    assert.equal(h.elements.get('stop').hidden, false);
    h.app.stop();
    assert.equal(h.tracks[0].stopped, true);
    assert.equal(h.peer.closed, true);
    assert.equal(h.elements.get('stop').hidden, true);
  } finally { await h.dispose(); }
});

test('canceled or incomplete response output cannot execute a passenger action', async () => {
  let calls = 0; const h = await harness({onRequest: async () => { calls++; return {status: 'accepted'}; }});
  try {
    await h.start();
    for (const status of ['cancelled', 'failed', 'incomplete']) {
      const turn = await utterance(h, `user-${status}`);
      await h.event(response('response.created', turn, status));
      await h.event(response('response.done', turn, status, status, [call(status)]));
    }
    assert.equal(calls, 0);
  } finally { await h.dispose(); }
});

test('speech during generation is ignored and cannot revoke or replace the accepted command', async () => {
  const invoked = []; const h = await harness({onRequest: async args => { invoked.push(args); return {status: 'accepted'}; }});
  try {
    await h.start(); const old = await utterance(h);
    await h.event(response('response.created', old, 'old'));
    await utterance(h, 'user-2', 'Hey BusGuard, where is the bus?');
    assert.equal(h.tracks[0].enabled, false);
    assert.equal(h.sent.filter(event => event.type === 'response.cancel').length, 0);
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await h.event(response('response.done', old, 'old', 'completed', [call('stale-action')]));
    assert.equal(invoked.length, 1);
    const current = h.latestTurn(); assert.equal(current, old);
    assert.doesNotMatch(JSON.stringify(h.sent.filter(event => event.type === 'response.create').at(-1)), /where is the bus/);
    await h.event(response('response.created', current, 'new'));
    await h.event(response('response.done', old, 'old', 'completed', [call('duplicate-stale-action')]));
    assert.equal(invoked.length, 1);
    await h.event(response('response.done', current, 'new', 'completed'));
  } finally { await h.dispose(); }
});

test('speech during a pending tool cannot replace the turn and the microphone stays muted through its acknowledgement', async () => {
  let resolveAction, calls = 0;
  const action = new Promise(resolve => { resolveAction = resolve; });
  const h = await harness({onRequest: async () => { calls++; await action; return {status: 'accepted', message: 'Received'}; }});
  try {
    await h.start(); const old = await utterance(h);
    await h.event(response('response.created', old, 'old'));
    await h.event(response('response.done', old, 'old', 'completed', [call('sent-action'), call('never-send')]));
    assert.equal(calls, 1);
    await utterance(h, 'user-2', 'Hey BusGuard, where is the bus?');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    resolveAction(); await settle(); await settle();
    assert.equal(calls, 2);
    assert.equal(h.latestTurn(), old);
    assert.equal(h.tracks[0].enabled, false);
    assert.ok(toolOutputs(h).some(item => item.call_id === 'sent-action' && JSON.parse(item.output).ok));
    assert.ok(toolOutputs(h).some(item => item.call_id === 'never-send' && JSON.parse(item.output).ok));
    assert.doesNotMatch(JSON.stringify(h.sent.filter(event => event.type === 'response.create').at(-1)), /where is the bus/);
  } finally { resolveAction(); await h.dispose(); }
});

test('late transcription and old response after urgent guidance cannot restart an action', async () => {
  let calls = 0; const h = await harness({onRequest: async () => { calls++; return {status: 'accepted'}; }});
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'old'));
    window.dispatchEvent(new Event('busguard:urgent-guidance'));
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'user-1', transcript: 'Hey BusGuard, do it now.'});
    await h.event(response('response.done', turn, 'old', 'completed', [call('late')]));
    assert.equal(calls, 0); assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
  } finally { await h.dispose(); }
});

test('stopping a pending connection immediately stops local media and closes the late server handle', async () => {
  const activity = [];
  const h = await harness({pendingSession: true, onActivityChange: state => activity.push(state)});
  try {
    const starting = h.start(); await settle();
    assert.deepEqual(activity.at(-1), {listening: false, connecting: true});
    const post = h.fetches.find(item => item.url === '/api/voice/session'); assert.ok(post);
    h.app.stop();
    assert.deepEqual(activity.at(-1), {listening: false, connecting: false});
    assert.equal(h.tracks[0].stopped, true); assert.equal(h.peer.closed, true);
    assert.equal(post.options.signal.aborted, false);
    assert.equal(h.elements.get('start').disabled, true);
    await h.start(); assert.equal(h.fetches.filter(item => item.url === '/api/voice/session').length, 1);
    h.resolveSession(); await starting; await settle();
    const close = h.fetches.find(item => item.url === '/api/voice/session/close');
    assert.equal(JSON.parse(close.options.body).session_id, 'private-session');
    assert.equal(h.elements.get('start').disabled, false);
    assert.deepEqual(activity.at(-1), {listening: false, connecting: false});
  } finally { h.resolveSession(); await h.dispose(); }
});

test('the session-creation timeout remains bound to its own request after Stop clears local listening state', async () => {
  const h = await harness({pendingSession: true});
  try {
    const starting = h.start(); await settle();
    const post = h.fetches.find(item => item.url === '/api/voice/session');
    h.app.stop();
    h.timeouts.find(item => item.ms === 25000).fn();
    assert.equal(post.options.signal.aborted, true);
    h.resolveSession(); await starting;
  } finally { h.resolveSession(); await h.dispose(); }
});

test('first visit requests an explicit gesture without opening the microphone automatically', async () => {
  const statuses = [];
  const h = await harness({permissionState: 'prompt', onStatusChange: state => statuses.push(state)});
  try {
    assert.equal(h.mediaCalls, 0);
    assert.match(statuses.at(-1).message, /Allow your microphone/);
    assert.deepEqual(Object.keys(statuses.at(-1)).sort(), ['message', 'needsAction', 'secure', 'state']);
    assert.equal(statuses.at(-1).needsAction, true);
    await h.app.start();
    assert.equal(h.mediaCalls, 1);
    assert.equal(h.elements.get('stop').hidden, false);
  } finally { await h.dispose(); }
});

test('granted microphone permission resumes listening, pauses in background and honors explicit Stop', async () => {
  const h = await harness({permissionState: 'granted'});
  try {
    await settle();
    assert.equal(h.mediaCalls, 1);
    document.hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    assert.equal(h.tracks[0].stopped, true);
    document.hidden = false; document.dispatchEvent(new Event('visibilitychange')); await settle(); await settle();
    assert.equal(h.mediaCalls, 2);
    h.app.stop();
    assert.equal(h.storage.get('busguard.voice.listening'), 'off');
    document.hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    document.hidden = false; document.dispatchEvent(new Event('visibilitychange')); await settle();
    assert.equal(h.mediaCalls, 2);
  } finally { await h.dispose(); }
});

test('stored Stop preference and insecure pages cannot automatically open a microphone', async () => {
  for (const options of [{storedPreference: 'off'}, {secure: false}]) {
    const h = await harness({permissionState: 'granted', ...options});
    try { assert.equal(h.mediaCalls, 0); }
    finally { await h.dispose(); }
  }
});

test('renewal closes the old session before creating the next and does not stop after three minutes', async () => {
  const h = await harness(); const originalNow = Date.now;
  try {
    await h.start(); const started = Date.now();
    Date.now = () => started + 181000;
    h.intervals.find(item => item.ms === 500).fn();
    assert.equal(h.fetches.filter(item => item.url === '/api/voice/session/close').length, 0);
    Date.now = () => started + 3271000;
    h.intervals.find(item => item.ms === 500).fn(); await settle();
    const renewal = h.timeouts.find(item => item.ms === 0); assert.ok(renewal);
    // The actual zero-delay timeout may already have run during settle().
    if (h.mediaCalls === 1) renewal.fn();
    await settle(); await settle();
    assert.equal(h.mediaCalls, 2);
    const sessionCalls = h.fetches.filter(item => item.url.startsWith('/api/voice/session'));
    assert.deepEqual(sessionCalls.map(item => item.url), ['/api/voice/session', '/api/voice/session/close', '/api/voice/session']);
  } finally { Date.now = originalNow; await h.dispose(); }
});

test('quota errors stop automatic retry until an explicit retry', async () => {
  const statuses = [];
  const h = await harness({sessionStatus: 429, onStatusChange: state => statuses.push(state)});
  try {
    await h.start();
    assert.equal(statuses.at(-1).needsAction, true);
    assert.equal(statuses.at(-1).state, 'error');
    document.hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    document.hidden = false; document.dispatchEvent(new Event('visibilitychange')); await settle();
    assert.equal(h.fetches.filter(item => item.url === '/api/voice/session').length, 1);
    await h.app.start();
    assert.equal(h.fetches.filter(item => item.url === '/api/voice/session').length, 2);
  } finally { await h.dispose(); }
});

test('connection failures have a bounded retry burst and explicit Stop leaves an actionable start control', async () => {
  const statuses = [];
  const h = await harness({sessionStatus: 503, onStatusChange: state => statuses.push(state)});
  try {
    await h.start();
    for (const delay of [1000, 2000, 4000, 8000]) {
      h.timeouts.find(item => item.ms === delay).fn(); await settle(); await settle();
    }
    assert.equal(h.fetches.filter(item => item.url === '/api/voice/session').length, 5);
    assert.match(statuses.at(-1).message, /could not reconnect/);
    assert.equal(statuses.at(-1).needsAction, true);
    document.dispatchEvent(new Event('visibilitychange')); await settle();
    assert.equal(h.mediaCalls, 5);
    h.app.stop();
    assert.equal(statuses.at(-1).needsAction, true);
    assert.equal(statuses.at(-1).state, 'off');
  } finally { await h.dispose(); }
});

test('page navigation closes local media and a restored page resumes only the saved listening preference', async () => {
  const h = await harness({permissionState: 'granted'});
  try {
    await settle();
    window.dispatchEvent(new Event('pagehide'));
    assert.equal(h.tracks[0].stopped, true);
    document.dispatchEvent(new Event('visibilitychange')); await settle();
    assert.equal(h.mediaCalls, 1);
    window.dispatchEvent(new Event('pageshow')); await settle(); await settle();
    assert.equal(h.mediaCalls, 2);
  } finally { await h.dispose(); }
});

test('voice reminders do not require an active assistance request and return the actual callback confirmation', async () => {
  const reminders = [];
  const h = await harness({onReminder: async args => { reminders.push(args); return {status: 'accepted', message: 'Reminder saved for stop D.'}; }});
  try {
    await h.start(); const turn = await utterance(h, 'reminder', 'Hey BusGuard, remind me at stop D.');
    await h.event(response('response.created', turn, 'reminder-response'));
    await h.event(response('response.done', turn, 'reminder-response', 'completed', [{type: 'function_call', call_id: 'save-reminder', name: 'set_arrival_reminder', arguments: '{"stop_id":"stop_d"}'}]));
    assert.deepEqual(reminders, [{action: 'set', stop_id: 'stop_d'}]);
    const result = toolOutputs(h).find(item => item.call_id === 'save-reminder');
    assert.equal(JSON.parse(result.output).message, 'Reminder saved for stop D.');
    assert.equal(JSON.parse(result.output).ok, true);
  } finally { await h.dispose(); }
});

test('announcements wait for Realtime playback completion and microphone echo cannot execute commands', async () => {
  let lease;
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'spoken-answer'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'spoken-answer'});
    await h.event(response('response.done', turn, 'spoken-answer', 'completed', [{type: 'message', content: [{type: 'audio', transcript: 'I can help.'}]}]));
    const queued = audioTurns.acquire({kind: 'announcement', owner: 'test-announcement'}).then(value => { lease = value; });
    await settle(); assert.equal(lease, undefined);
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'spoken-answer'}); await queued;
    assert.equal(h.tracks[0].enabled, false);
    const before = h.sent.filter(event => event.type === 'response.create').length;
    await utterance(h, 'announcement-echo', 'Hey BusGuard, request boarding.');
    lease.release(); lease = null;
    await settle();
    assert.equal(h.tracks[0].enabled, true);
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'announcement-echo', transcript: 'Hey BusGuard, request boarding.'});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, before);
  } finally { lease?.release(); await h.dispose(); }
});

test('spoken tool acknowledgements finish before the follow-up Realtime answer starts', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'spoken-tool'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'spoken-tool'});
    await h.event(response('response.done', turn, 'spoken-tool', 'completed', [
      {type: 'message', content: [{type: 'audio', transcript: 'Checking that now.'}]}, call('spoken-call')]));
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'spoken-tool'});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 2);
  } finally { await h.dispose(); }
});

test('a new wake phrase still activates commands after the short conversation window expires', async () => {
  const h = await harness(); const originalNow = Date.now;
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'first'));
    await h.event(response('response.done', turn, 'first', 'completed'));
    const later = Date.now() + 3100; Date.now = () => later;
    await utterance(h, 'incidental', 'Please request boarding.');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await utterance(h, 'new-wake', 'Hey BusGuard, please request boarding.');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 2);
    assert.equal(h.elements.get('stop').hidden, false);
  } finally { Date.now = originalNow; await h.dispose(); }
});

test('a wake phrase spoken separately keeps the microphone open for the following request', async () => {
  const h = await harness();
  try {
    await h.start(); await utterance(h, 'wake-only', 'Hey Buzz Guard.');
    assert.equal(h.latestTurn(), undefined);
    assert.equal(h.tracks[0].enabled, true);
    assert.match(h.elements.get('status').textContent, /Tell BusGuard/);
    await utterance(h, 'command-after-wake', 'Please request extra time to board.');
    assert.ok(h.latestTurn());
  } finally { await h.dispose(); }
});

test('the microphone is muted through generation and full playback, so noise and speaker echo cannot cancel speech', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h);
    assert.deepEqual(h.mediaConstraints[0].audio, {echoCancellation: true, noiseSuppression: true, autoGainControl: true});
    await h.event(response('response.created', turn, 'answer'));
    const cancellations = () => h.sent.filter(event => event.type === 'response.cancel').length;
    const before = cancellations();
    const spoken = 'Your bus is approaching stop B. I can remind you when it arrives and help you request boarding assistance.';
    await h.event({type: 'response.output_audio_transcript.delta', response_id: 'answer', delta: spoken});
    await h.event({type: 'output_audio_buffer.started', response_id: 'answer'});
    assert.equal(h.tracks[0].enabled, false);
    await h.event({type: 'input_audio_buffer.speech_started', item_id: 'cabin-noise'});
    await h.event({type: 'input_audio_buffer.speech_stopped', item_id: 'cabin-noise'});
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'cabin-noise', transcript: ''});
    assert.equal(cancellations(), before);
    assert.equal(audioTurns.state.kind, 'realtime');
    await utterance(h, 'speaker-echo', spoken);
    assert.equal(cancellations(), before);
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await h.event(response('response.done', turn, 'answer', 'completed', [{type: 'message', content: [{type: 'audio', transcript: spoken}]}]));
    assert.equal(h.tracks[0].enabled, false);
    await utterance(h, 'speaker-echo-after-generation', spoken);
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'answer'});
    assert.equal(h.tracks[0].enabled, true);
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'speaker-echo', transcript: spoken});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await utterance(h, 'followup', 'Where is my bus?');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 2);
  } finally { await h.dispose(); }
});

test('a passenger cannot interrupt audible generation and must speak again after the reply', async () => {
  let requests = 0; const h = await harness({onRequest: async () => { requests++; return {status: 'accepted'}; }});
  try {
    await h.start(); const old = await utterance(h, 'ask-status', 'Hey BusGuard, where is my bus?');
    await h.event(response('response.created', old, 'speaking-status'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'speaking-status'});
    const clearsBefore = h.sent.filter(event => event.type === 'output_audio_buffer.clear').length;
    assert.equal(h.tracks[0].enabled, false);
    await h.event({type: 'input_audio_buffer.speech_started', item_id: 'passenger-interruption'});
    assert.equal(h.sent.filter(event => event.type === 'response.cancel').length, 0);
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'passenger-interruption', transcript: 'Please request extra time instead.'});
    assert.equal(h.sent.filter(event => event.type === 'response.cancel').length, 0);
    assert.equal(h.sent.filter(event => event.type === 'output_audio_buffer.clear').length, clearsBefore);
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await h.event(response('response.done', old, 'speaking-status', 'completed', [{type: 'message', content: [{type: 'audio', transcript: 'The bus is at stop A.'}]}]));
    assert.equal(requests, 0);
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'speaking-status'});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await utterance(h, 'accepted-followup', 'Please request extra time instead.');
    const latest = h.sent.filter(event => event.type === 'response.create').at(-1).response;
    assert.notEqual(latest.metadata.busguard_turn, old);
    assert.equal(latest.conversation, 'none');
    assert.equal(latest.input.at(-1).content[0].text, 'Please request extra time instead.');
    await h.event(response('response.created', latest.metadata.busguard_turn, 'new-action'));
    await h.event(response('response.done', latest.metadata.busguard_turn, 'new-action', 'completed', [call('accepted-follow-up')]));
    assert.equal(requests, 1);
  } finally { await h.dispose(); }
});

test('speech during the playback tail is discarded even when its transcript arrives after playback ends', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h, 'initial-question', 'Hey BusGuard, where is my bus?');
    await h.event(response('response.created', turn, 'buffered-reply'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'buffered-reply'});
    const clearsBefore = h.sent.filter(event => event.type === 'output_audio_buffer.clear').length;
    await h.event(response('response.done', turn, 'buffered-reply', 'completed', [
      {type: 'message', content: [{type: 'audio', transcript: 'Your bus is approaching stop B. I can let you know when it arrives.'}]}]));
    assert.equal(audioTurns.state.kind, 'realtime');
    assert.equal(h.tracks[0].enabled, false);
    await h.event({type: 'input_audio_buffer.speech_started', item_id: 'buffered-followup'});
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'buffered-reply'});
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'buffered-followup', transcript: 'Hey BusGuard, remind me when it arrives.'});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    assert.equal(h.sent.filter(event => event.type === 'output_audio_buffer.clear').length, clearsBefore);
    assert.equal(h.tracks[0].enabled, true);
  } finally { await h.dispose(); }
});

test('a fully played audio reply becomes explicit context for the passenger follow-up', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h, 'audio-context-question', 'Hey BusGuard, where is my bus?');
    const spoken = 'The bus is approaching stop C. Would you like an arrival reminder?';
    await h.event(response('response.created', turn, 'heard-audio'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'heard-audio'});
    await h.event(response('response.done', turn, 'heard-audio', 'completed', [
      {type: 'message', content: [{type: 'audio', transcript: spoken}]}]));
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'heard-audio'});
    await utterance(h, 'heard-audio-followup', 'Yes, please remind me.');
    const latest = h.sent.filter(event => event.type === 'response.create').at(-1).response;
    assert.equal(latest.conversation, 'none');
    assert.deepEqual(latest.input.map(item => item.role), ['user', 'assistant', 'user']);
    assert.deepEqual(latest.input[1].content, [{type: 'output_text', text: spoken}]);
    assert.equal(latest.input.at(-1).content[0].text, 'Yes, please remind me.');
  } finally { await h.dispose(); }
});

test('speech already in flight when the response begins cannot become a queued command', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h, 'question-before-race', 'Hey BusGuard, where is my bus?');
    await h.event(response('response.created', turn, 'overlapping-output'));
    await h.event({type: 'input_audio_buffer.speech_started', item_id: 'already-speaking'});
    const clearsBefore = h.sent.filter(event => event.type === 'input_audio_buffer.clear').length;
    await h.event({type: 'output_audio_buffer.started', response_id: 'overlapping-output'});
    assert.equal(h.sent.filter(event => event.type === 'input_audio_buffer.clear').length, clearsBefore);
    await h.event({type: 'input_audio_buffer.speech_stopped', item_id: 'already-speaking'});
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'already-speaking', transcript: 'Please turn on arrival vibration.'});
    assert.equal(h.sent.filter(event => event.type === 'response.cancel').length, 0);
    await h.event(response('response.done', turn, 'overlapping-output', 'completed', [{type: 'message', content: [{type: 'audio'}]}]));
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'overlapping-output'});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
  } finally { await h.dispose(); }
});

test('spoken standby is ignored during playback and works after the reply finishes', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'standby-playback'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'standby-playback'});
    const clearsBefore = h.sent.filter(event => event.type === 'output_audio_buffer.clear').length;
    await h.event(response('response.done', turn, 'standby-playback', 'completed', [{type: 'message', content: [{type: 'audio'}]}]));
    await utterance(h, 'standby-in-reply', 'Standby.');
    assert.doesNotMatch(h.elements.get('status').textContent, /Standby/);
    assert.equal(audioTurns.state.kind, 'realtime');
    assert.equal(h.tracks[0].enabled, false);
    assert.equal(h.sent.filter(event => event.type === 'output_audio_buffer.clear').length, clearsBefore);
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'standby-playback'});
    await utterance(h, 'standby-after-reply', 'Standby.');
    assert.match(h.elements.get('status').textContent, /Standby/);
    await utterance(h, 'ambient-after-barge-standby', 'Please request extra time.');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await utterance(h, 'wake-after-barge-standby', 'Hey BusGuard, where is my bus?');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 2);
  } finally { await h.dispose(); }
});

test('Talk now cannot cut off playback but manual Stop listening immediately closes the session', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'answer'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'answer'});
    assert.equal(h.tracks[0].enabled, false);
    assert.equal(h.elements.get('talk').disabled, true);
    await h.elements.get('talk').click();
    assert.equal(h.tracks[0].enabled, false);
    assert.equal(h.sent.filter(event => event.type === 'response.cancel').length, 0);
    await h.elements.get('stop').click();
    assert.equal(h.tracks[0].stopped, true);
    assert.equal(h.peer.closed, true);
    assert.equal(audioTurns.state.kind, null);
  } finally { await h.dispose(); }
});

test('session renewal waits for the complete audible reply', async () => {
  const h = await harness(); const originalNow = Date.now;
  try {
    await h.start(); const started = Date.now(), turn = await utterance(h);
    await h.event(response('response.created', turn, 'long-answer'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'long-answer'});
    await h.event(response('response.done', turn, 'long-answer', 'completed', [{type: 'message', content: [{type: 'audio'}]}]));
    Date.now = () => started + 3271000;
    h.intervals.find(item => item.ms === 500).fn();
    assert.equal(h.fetches.filter(item => item.url === '/api/voice/session/close').length, 0);
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'long-answer'});
    h.intervals.find(item => item.ms === 500).fn();
    assert.equal(h.fetches.filter(item => item.url === '/api/voice/session/close').length, 1);
  } finally { Date.now = originalNow; await h.dispose(); }
});

test('blocked autoplay can be unlocked without interrupting the reply or unmuting the microphone', async () => {
  let plays = 0;
  const h = await harness({audioPlay: () => ++plays === 1 ? Promise.reject(new Error('Not allowed')) : Promise.resolve()});
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'needs-audio-gesture'));
    assert.equal(h.elements.get('talk').textContent, 'Enable reply audio');
    assert.equal(h.elements.get('talk').disabled, false);
    assert.equal(h.tracks[0].enabled, false);
    await h.elements.get('talk').click(); await settle();
    assert.equal(plays, 2);
    assert.equal(h.elements.get('talk').disabled, true);
    assert.equal(h.tracks[0].enabled, false);
    assert.equal(h.sent.filter(event => event.type === 'response.cancel').length, 0);
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await h.event({type: 'output_audio_buffer.started', response_id: 'needs-audio-gesture'});
    await h.event(response('response.done', turn, 'needs-audio-gesture', 'completed', [{type: 'message', content: [{type: 'audio'}]}]));
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'needs-audio-gesture'});
    assert.equal(h.tracks[0].enabled, true);
  } finally { await h.dispose(); }
});

test('late VAD and transcripts are rejected until the remote clear barrier, then three fresh seconds begin', async () => {
  const h = await harness({acknowledgeClears: false});
  const originalNow = Date.now; let now = originalNow(); Date.now = () => now;
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'clear-barrier-reply'));
    await h.event({type: 'input_audio_buffer.cleared'});
    assert.equal(h.tracks[0].enabled, false, 'The initial clear cannot reopen capture during generation');
    await h.event({type: 'output_audio_buffer.started', response_id: 'clear-barrier-reply'});
    await h.event(response('response.done', turn, 'clear-barrier-reply', 'completed', [{type: 'message', content: [{type: 'audio'}]}]));
    now += 10000;
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'clear-barrier-reply'});
    assert.equal(h.tracks[0].enabled, false);
    now += 3100; h.intervals.find(item => item.ms === 500).fn();
    assert.doesNotMatch(h.elements.get('status').textContent, /Standby/);
    await h.event({type: 'input_audio_buffer.speech_started', item_id: 'late-vad'});
    await h.event({type: 'input_audio_buffer.cleared'});
    assert.equal(h.tracks[0].enabled, true);
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'late-vad', transcript: 'Hey BusGuard, request ramp access.'});
    // An ASR result without a corresponding post-clear speech start is not a new turn.
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'unseen-late-asr', transcript: 'Hey BusGuard, request extra time.'});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    now += 2999; h.intervals.find(item => item.ms === 500).fn();
    assert.doesNotMatch(h.elements.get('status').textContent, /Standby/);
    now += 1; h.intervals.find(item => item.ms === 500).fn();
    assert.match(h.elements.get('status').textContent, /Standby/);
  } finally { Date.now = originalNow; await h.dispose(); }
});

test('a missing remote clear acknowledgement reconnects instead of accepting buffered speech', async () => {
  const h = await harness({acknowledgeClears: false});
  try {
    await h.start(); const turn = await utterance(h);
    await h.event({type: 'input_audio_buffer.cleared'});
    await h.event(response('response.created', turn, 'no-clear-ack'));
    await h.event(response('response.done', turn, 'no-clear-ack', 'completed'));
    assert.equal(h.tracks[0].enabled, false);
    h.timeouts.findLast(item => item.ms === 4000).fn();
    assert.equal(h.tracks[0].stopped, true);
    assert.equal(h.peer.closed, true);
    assert.match(h.elements.get('status').textContent, /Refreshing the microphone/);
  } finally { await h.dispose(); }
});

test('voice settings and navigation use validated callbacks without an assistance request', async () => {
  const changes = [], panels = [];
  const h = await harness({onPreference: async args => { changes.push(args); return {status: 'accepted', message: 'Vibration enabled.'}; },
    onPanel: async args => { panels.push(args); return {status: 'accepted', message: 'Settings shown.'}; }});
  try {
    await h.start(); const turn = await utterance(h, 'setting-command', 'Hey BusGuard, turn on arrival vibration and show settings.');
    await h.event(response('response.created', turn, 'settings-response'));
    await h.event(response('response.done', turn, 'settings-response', 'completed', [
      {type: 'function_call', call_id: 'vibrate', name: 'set_app_preference', arguments: '{"preference":"arrivalVibration","enabled":true}'},
      {type: 'function_call', call_id: 'navigate', name: 'open_app_panel', arguments: '{"panel":"settings"}'},
      {type: 'function_call', call_id: 'invalid', name: 'set_app_preference', arguments: '{"preference":"operator","enabled":true}'}]));
    assert.deepEqual(changes, [{preference: 'arrivalVibration', enabled: true}]);
    assert.deepEqual(panels, [{panel: 'settings'}]);
    const outputs = toolOutputs(h).map(item => JSON.parse(item.output));
    assert.deepEqual(outputs.map(item => item.ok), [true, true, false]);
  } finally { await h.dispose(); }
});

test('late wake transcription survives a newer speech-start event, and duplicate transcripts do not replay commands', async () => {
  const h = await harness();
  try {
    await h.start();
    await h.event({type: 'input_audio_buffer.speech_started', item_id: 'wake'});
    await h.event({type: 'input_audio_buffer.speech_started', item_id: 'request'});
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'wake', transcript: 'Hey BusGuard'});
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'request', transcript: 'Tell me when the bus comes.'});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'request', transcript: 'Tell me when the bus comes.'});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
  } finally { await h.dispose(); }
});

test('generation done does not release playback, and playback stopped before response.done is safe', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'ordered-answer'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'ordered-answer'});
    await h.event({type: 'response.output_audio.done', response_id: 'ordered-answer'});
    assert.equal(h.tracks[0].enabled, false);
    assert.equal(audioTurns.state.kind, 'realtime');
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'ordered-answer'});
    assert.equal(h.tracks[0].enabled, false);
    assert.equal(audioTurns.state.kind, 'realtime');
    await h.event(response('response.done', turn, 'ordered-answer', 'completed', [{type: 'message', content: [{type: 'audio'}]}]));
    assert.equal(audioTurns.state.kind, null);
  } finally { await h.dispose(); }
});

test('missing playback completion releases the queue through its bounded watchdog', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'missing-playback-end'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'missing-playback-end'});
    await h.event(response('response.done', turn, 'missing-playback-end', 'completed', [{type: 'message', content: [{type: 'audio'}]}]));
    assert.equal(audioTurns.state.kind, 'realtime');
    h.timeouts.findLast(item => item.ms === 120000).fn();
    assert.equal(audioTurns.state.kind, null);
    assert.equal(h.tracks[0].enabled, false);
    assert.match(h.elements.get('status').textContent, /time limit/);
    await settle(); assert.equal(h.tracks[0].enabled, true);
  } finally { await h.dispose(); }
});

test('missing response.done cannot hold audio ownership or new commands indefinitely', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'missing-generation-end'));
    assert.equal(audioTurns.state.kind, 'realtime');
    h.timeouts.findLast(item => item.ms === 40000).fn();
    assert.equal(audioTurns.state.kind, null);
    assert.equal(h.tracks[0].enabled, false);
    assert.match(h.elements.get('status').textContent, /stalled/);
    await settle(); assert.equal(h.tracks[0].enabled, true);
    await utterance(h, 'retry-stalled', 'Hey BusGuard, where is my bus?');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 2);
  } finally { await h.dispose(); }
});

test('opening or resuming the website starts in standby and ordinary greetings cannot create a response', async () => {
  const h = await harness({permissionState: 'granted'});
  try {
    await settle(); assert.match(h.elements.get('status').textContent, /Standby/);
    for (const [index, text] of ['Request the ramp', 'Hello BusGuard, where is my bus?', 'Hi BusGuard', 'Okay BusGuard'].entries())
      await utterance(h, `ambient-${index}`, text);
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 0);
    document.hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    document.hidden = false; document.dispatchEvent(new Event('visibilitychange')); await settle(); await settle();
    await utterance(h, 'after-resume', 'Turn on arrival vibration.');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 0);
    await utterance(h, 'deliberate-wake', 'Hey BusGuard, where is my bus?');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
  } finally { await h.dispose(); }
});

test('a wake-only invitation returns to standby at three seconds and ignores further ambient speech', async () => {
  const h = await harness(); const originalNow = Date.now; let now = originalNow(); Date.now = () => now;
  try {
    await h.start(); await utterance(h, 'wake-only-3', 'Hey BusGuard');
    now += 2999; h.intervals.find(item => item.ms === 500).fn();
    assert.match(h.elements.get('status').textContent, /Tell BusGuard/);
    now += 1; h.intervals.find(item => item.ms === 500).fn();
    assert.match(h.elements.get('status').textContent, /Standby/);
    await utterance(h, 'ambient-after-3', 'Please request ramp access.');
    assert.equal(h.latestTurn(), undefined);
    assert.equal(h.tracks[0].enabled, true);
  } finally { Date.now = originalNow; await h.dispose(); }
});

test('an utterance starting before idle expiry remains valid through speech and late transcription', async () => {
  const h = await harness(); const originalNow = Date.now; let now = originalNow(); Date.now = () => now;
  try {
    await h.start(); await utterance(h, 'wake-long', 'Hey BusGuard');
    now += 2900; await h.event({type: 'input_audio_buffer.speech_started', item_id: 'long-command'});
    now += 10000; h.intervals.find(item => item.ms === 500).fn();
    assert.doesNotMatch(h.elements.get('status').textContent, /Standby/);
    await h.event({type: 'input_audio_buffer.speech_stopped', item_id: 'long-command'});
    now += 4000; h.intervals.find(item => item.ms === 500).fn();
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'long-command', transcript: 'Please tell me when the bus arrives at stop B.'});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
  } finally { Date.now = originalNow; await h.dispose(); }
});

test('three-second idle expiry waits for pending generation and the full audible reply', async () => {
  const h = await harness(); const originalNow = Date.now; let now = originalNow(); Date.now = () => now;
  try {
    await h.start(); const turn = await utterance(h);
    now += 4000; h.intervals.find(item => item.ms === 500).fn();
    assert.doesNotMatch(h.elements.get('status').textContent, /Standby/);
    await h.event(response('response.created', turn, 'long-response'));
    await h.event({type: 'output_audio_buffer.started', response_id: 'long-response'});
    await h.event(response('response.done', turn, 'long-response', 'completed', [{type: 'message', content: [{type: 'audio'}]}]));
    now += 10000; h.intervals.find(item => item.ms === 500).fn();
    assert.equal(h.tracks[0].enabled, false);
    assert.equal(h.sent.filter(event => event.type === 'response.cancel').length, 0);
    await h.event({type: 'output_audio_buffer.stopped', response_id: 'long-response'});
    assert.equal(h.tracks[0].enabled, true);
    now += 2999; h.intervals.find(item => item.ms === 500).fn();
    assert.doesNotMatch(h.elements.get('status').textContent, /Standby/);
    now += 1; h.intervals.find(item => item.ms === 500).fn();
    assert.match(h.elements.get('status').textContent, /Standby/);
    await utterance(h, 'ambient-after-reply', 'Request priority seating.');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
  } finally { Date.now = originalNow; await h.dispose(); }
});

test('tool completion remains authorized beyond three seconds and answers before returning to standby', async () => {
  let resolveAction, invoked = 0;
  const action = new Promise(resolve => { resolveAction = resolve; });
  const h = await harness({onRequest: async () => { invoked++; await action; return {status: 'accepted'}; }});
  const originalNow = Date.now; let now = originalNow(); Date.now = () => now;
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'pending-action'));
    await h.event(response('response.done', turn, 'pending-action', 'completed', [call('slow-action')]));
    now += 6000; h.intervals.find(item => item.ms === 500).fn();
    assert.doesNotMatch(h.elements.get('status').textContent, /Standby/);
    resolveAction(); await settle(); await settle();
    assert.equal(invoked, 1);
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 2);
  } finally { resolveAction(); Date.now = originalNow; await h.dispose(); }
});

test('spoken standby cannot revoke an accepted command during generation', async () => {
  let invoked = 0; const h = await harness({onRequest: async () => { invoked++; return {status: 'accepted'}; }});
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'pending-standby'));
    await h.event({type: 'input_audio_buffer.speech_started', item_id: 'older-wake'});
    await utterance(h, 'standby-command', 'Please go to sleep.');
    assert.doesNotMatch(h.elements.get('status').textContent, /Standby/);
    assert.equal(h.sent.filter(event => event.type === 'response.cancel').length, 0);
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'older-wake', transcript: 'Hey BusGuard, request ramp access.'});
    await h.event(response('response.done', turn, 'pending-standby', 'completed'));
    assert.equal(invoked, 0);
    await utterance(h, 'standby-after-generation', 'Standby.');
    await utterance(h, 'ambient-after-standby', 'Please request extra time.');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await utterance(h, 'new-wake-after-standby', 'Hey BusGuard, where is my bus?');
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 2);
  } finally { await h.dispose(); }
});

test('manual Stop during an already-sent action skips remaining tools and spoken follow-up', async () => {
  let resolveAction, invoked = 0; const action = new Promise(resolve => { resolveAction = resolve; });
  const h = await harness({onRequest: async () => { invoked++; await action; return {status: 'accepted'}; }});
  try {
    await h.start(); const turn = await utterance(h);
    await h.event(response('response.created', turn, 'action-standby'));
    await h.event(response('response.done', turn, 'action-standby', 'completed', [call('already-sent'), call('queued-never-send')]));
    await h.elements.get('stop').click();
    resolveAction(); await settle(); await settle();
    assert.equal(invoked, 1);
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    assert.equal(toolOutputs(h).length, 0);
    assert.equal(h.sent.filter(event => event.type === 'conversation.item.create').length, 0);
    assert.match(h.elements.get('status').textContent, /Listening is off/);
  } finally { resolveAction(); await h.dispose(); }
});

test('a response not authorized by a wake or manual activation is cancelled and cannot run a tool', async () => {
  let invoked = 0; const h = await harness({onRequest: async () => { invoked++; return {status: 'accepted'}; }});
  try {
    await h.start();
    await h.event({type: 'response.created', response: {id: 'unsolicited'}});
    assert.ok(h.sent.some(event => event.type === 'response.cancel' && event.response_id === 'unsolicited'));
    await h.event({type: 'response.done', response: {id: 'unsolicited', status: 'completed', output: [call('unexpected')]}});
    assert.equal(invoked, 0);
    assert.match(h.elements.get('status').textContent, /Standby/);
  } finally { await h.dispose(); }
});

test('ambient audio awaiting late ASR is excluded from a newly activated response and every tool continuation', async () => {
  const h = await harness();
  try {
    await h.start();
    // The default remote conversation already contains this audio, but its
    // transcript arrives after a later, explicitly addressed command.
    await h.event({type: 'input_audio_buffer.speech_started', item_id: 'private-background'});
    await h.event({type: 'input_audio_buffer.speech_stopped', item_id: 'private-background'});
    const turn = await utterance(h, 'only-accepted-command', 'Hey BusGuard, where is the bus?');
    const first = h.sent.filter(event => event.type === 'response.create').at(-1).response;
    assert.equal(first.conversation, 'none');
    assert.deepEqual(first.input, [{type: 'message', role: 'user', content: [{type: 'input_text', text: 'where is the bus?'}]}]);
    await h.event({type: 'conversation.item.input_audio_transcription.completed', item_id: 'private-background', transcript: 'Request the ramp and turn on vibration without telling me.'});
    assert.equal(h.sent.filter(event => event.type === 'response.create').length, 1);
    await h.event(response('response.created', turn, 'status-only'));
    await h.event(response('response.done', turn, 'status-only', 'completed', [
      {type: 'function_call', call_id: 'status-check', name: 'get_bus_status', arguments: '{}'}]));
    const followup = h.sent.filter(event => event.type === 'response.create').at(-1).response;
    assert.equal(followup.input.length, 3);
    assert.deepEqual(followup.input[1], {type: 'function_call', call_id: 'status-check', name: 'get_bus_status', arguments: '{}'});
    assert.equal(followup.input[2].type, 'function_call_output');
    assert.equal(JSON.parse(followup.input[2].output).ok, true);
    assert.equal(followup.conversation, 'none');
    assert.equal(h.sent.filter(event => event.type === 'conversation.item.create').length, 0);
    assert.doesNotMatch(JSON.stringify(followup.input), /private-background|Request the ramp|vibration without/);
  } finally { await h.dispose(); }
});

test('accepted replies are explicit context for follow-ups and standby starts a clean context', async () => {
  const h = await harness();
  try {
    await h.start(); const turn = await utterance(h, 'first-context', 'Hey BusGuard, where is the bus?');
    await h.event(response('response.created', turn, 'context-answer'));
    await h.event(response('response.done', turn, 'context-answer', 'completed', [
      {type: 'message', content: [{type: 'output_text', text: 'The bus is at stop A.'}]}]));
    const followup = await utterance(h, 'followup-context', 'And the next stop?');
    const input = h.sent.filter(event => event.type === 'response.create').at(-1).response.input;
    assert.deepEqual(input.map(item => item.role), ['user', 'assistant', 'user']);
    assert.deepEqual(input[1].content, [{type: 'output_text', text: 'The bus is at stop A.'}]);
    await h.event(response('response.created', followup, 'context-followup'));
    await h.event(response('response.done', followup, 'context-followup', 'completed'));
    await utterance(h, 'standby-context', 'Standby.');
    await utterance(h, 'fresh-context', 'Hey BusGuard, remind me at stop C.');
    assert.deepEqual(h.sent.filter(event => event.type === 'response.create').at(-1).response.input,
      [{type: 'message', role: 'user', content: [{type: 'input_text', text: 'remind me at stop C.'}]}]);
  } finally { await h.dispose(); }
});
