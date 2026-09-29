import {audioTurns} from './audio-coordination.js?v=voice-flow-1';

// All public speech on this page shares one FIFO. Clips can prepare ahead, but
// neither a new announcement nor a voice preview cuts off current playback.
const pending = [], instances = new Set(), clips = new Map();
const MAX_QUEUED = 16, MAX_PREPARING = 2, MAX_CACHED = 32;
let current = null, running = false, preparing = 0;

const cancelled = reason => ({cancelled: true, reason});
const aborted = () => new DOMException('Speech cancelled.', 'AbortError');
const isHidden = () => typeof document !== 'undefined' && document.hidden;
const expired = job => Date.now() >= job.expiresAt;

function finish(job, result) {
  if (job.settled) return;
  job.settled = true;
  job.owner.jobs.delete(job);
  job.resolve(result);
  if (result?.unavailable && !job.options.preview && typeof window !== 'undefined'
      && typeof CustomEvent !== 'undefined') {
    window.dispatchEvent(new CustomEvent('busguard:guidance-unavailable', {
      detail: {reason: result.reason, group: job.options.group || null},
    }));
  }
}
function cancelJob(job, reason = 'cancelled') {
  if (job.settled) return;
  job.cancelReason = reason;
  job.controller.abort();
  const result = cancelled(reason);
  job.readyResolve(result);
  finish(job, result);
}

// A timeout remains active until the body has been consumed, not just headers.
async function boundedFetch(path, options, signal, timeoutMs, read) {
  const controller = new AbortController();
  let rejectAbort;
  const abortPromise = new Promise((_, reject) => { rejectAbort = reject; });
  const abort = () => { controller.abort(); rejectAbort(aborted()); };
  const timer = setTimeout(abort, Math.max(1, timeoutMs));
  signal?.addEventListener('abort', abort, {once: true});
  if (signal?.aborted) abort();
  try {
    return await Promise.race([
      fetch(path, {...options, signal: controller.signal}).then(read),
      abortPromise,
    ]);
  } finally {
    clearTimeout(timer); signal?.removeEventListener('abort', abort);
  }
}
function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(aborted()); return; }
    const abort = () => { clearTimeout(timer); reject(aborted()); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    signal.addEventListener('abort', abort, {once: true});
  });
}
function cached(key) {
  const blob = clips.get(key);
  if (blob) { clips.delete(key); clips.set(key, blob); }
  return blob;
}
function saveClip(key, blob) {
  clips.set(key, blob);
  while (clips.size > MAX_CACHED) clips.delete(clips.keys().next().value);
}

async function prepare(job) {
  const signal = job.controller.signal;
  const config = await job.owner.configuration();
  if (signal.aborted) return cancelled(job.cancelReason);
  if (expired(job)) return cancelled('expired');
  if (!config.enabled) return {unavailable: true, reason: 'configuration'};
  const requested = job.options.voice || config.default_voice;
  const voice = ['marin', 'cedar'].includes(requested) ? requested : 'marin';
  const key = `${voice}:${job.message}`;
  const existing = cached(key);
  if (existing) return {blob: existing};
  const until = Math.min(job.expiresAt, Date.now() + job.owner.preparationTimeoutMs);
  let attempt = 0;
  while (!signal.aborted && Date.now() < until) {
    let result;
    try {
      result = await boundedFetch('/api/voice/speech', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: job.message, voice}),
      }, signal, Math.min(4000, until - Date.now()), async response => {
        if (response.status === 202) return {preparing: true};
        if (!response.ok) return {unavailable: true,
          reason: response.status === 429 ? 'rate_limit' : 'service', status: response.status};
        if (!response.headers.get('content-type')?.startsWith('audio/')) return {unavailable: true, reason: 'format'};
        const blob = await response.blob();
        return blob.size > 0 && blob.size <= 8 * 1024 * 1024
          ? {blob} : {unavailable: true, reason: 'format'};
      });
    } catch {
      if (signal.aborted) return cancelled(job.cancelReason);
      return expired(job) ? cancelled('expired') : {unavailable: true, reason: 'connection'};
    }
    if (signal.aborted) return cancelled(job.cancelReason);
    if (result.blob) { saveClip(key, result.blob); return result; }
    if (!result.preparing) return result; // A definitive failure is never retried.
    const delays = job.owner.pollDelaysMs;
    const wait = Math.min(delays[Math.min(attempt++, delays.length - 1)], until - Date.now());
    if (wait <= 0) break;
    try { await delay(wait, signal); } catch { return cancelled(job.cancelReason); }
  }
  if (signal.aborted) return cancelled(job.cancelReason);
  if (expired(job)) return cancelled('expired');
  return job.options.preview ? {preparing: true} : {unavailable: true, reason: 'preparing'};
}

function prepareAhead() {
  for (const job of [current, ...pending]) {
    if (preparing >= MAX_PREPARING) break;
    if (!job || job.preparationStarted || job.settled) continue;
    preparing++; job.preparationStarted = true; job.state = 'preparing';
    void prepare(job).catch(() => ({unavailable: true, reason: 'service'})).then(job.readyResolve).finally(() => {
      preparing--; prepareAhead();
    });
  }
}

async function play(job, blob) {
  if (expired(job) || isHidden()) return cancelled(expired(job) ? 'expired' : 'hidden');
  job.state = 'waiting';
  const expiryTimer = setTimeout(() => {
    if (job.state !== 'playing') cancelJob(job, 'expired');
  }, Math.max(1, job.expiresAt - Date.now()));
  let turn;
  try {
    turn = await audioTurns.acquire({kind: 'announcement', owner: job.owner, signal: job.controller.signal});
  } catch { return cancelled(job.cancelReason || 'cancelled'); }
  finally { clearTimeout(expiryTimer); }
  try {
    if (job.settled || job.controller.signal.aborted) return cancelled(job.cancelReason || 'cancelled');
    if (expired(job) || isHidden()) return cancelled(expired(job) ? 'expired' : 'hidden');
    job.state = 'playing';
    return await new Promise(resolve => {
      let url, audio, timer, done = false;
      const end = result => {
        if (done) return;
        done = true; clearTimeout(timer);
        job.controller.signal.removeEventListener('abort', abort);
        if (audio) {
          audio.onended = audio.onerror = null;
          try { audio.pause(); audio.src = ''; } catch { /* Already disposed by the browser. */ }
          if (job.owner.audio === audio) job.owner.audio = null;
        }
        if (url) URL.revokeObjectURL(url);
        resolve(result);
      };
      const abort = () => end(cancelled(job.cancelReason || 'cancelled'));
      job.controller.signal.addEventListener('abort', abort, {once: true});
      try {
        url = URL.createObjectURL(blob); audio = new Audio(url); job.owner.audio = audio;
        audio.onended = () => end({played: true});
        audio.onerror = () => end({unavailable: true, reason: 'playback'});
        // A broken media element must not own the output forever.
        timer = setTimeout(() => end({unavailable: true, reason: 'playback_timeout'}), job.owner.audioTimeoutMs);
        Promise.resolve(audio.play()).catch(() => end({unavailable: true, reason: 'playback'}));
      } catch { end({unavailable: true, reason: 'playback'}); }
    });
  } finally { turn.release(); }
}

async function drain() {
  if (running) return;
  running = true;
  try {
    while (pending.length) {
      current = pending.shift();
      if (current.settled) continue;
      prepareAhead();
      const job = current, prepared = await job.ready;
      if (job.settled) continue;
      const result = prepared.blob ? await play(job, prepared.blob) : prepared;
      finish(job, result);
    }
  } finally { current = null; running = false; }
}

export class SpokenGuidance {
  constructor({preparationTimeoutMs = 15000, pollDelaysMs = [250, 500, 1000], audioTimeoutMs = 60000} = {}) {
    this.audio = null; this.jobs = new Set(); this.disposed = false;
    this.config = null; this.configAt = 0; this.configPromise = null;
    this.preparationTimeoutMs = Math.max(1, preparationTimeoutMs);
    this.pollDelaysMs = pollDelaysMs.length ? pollDelaysMs.map(value => Math.max(1, value)) : [1000];
    this.audioTimeoutMs = Math.max(1, audioTimeoutMs);
    instances.add(this);
  }

  cancel() {
    for (const job of [...this.jobs]) cancelJob(job);
  }

  async configuration() {
    if (this.config && Date.now() - this.configAt < 30000) return this.config;
    if (this.configPromise) return this.configPromise;
    this.configPromise = boundedFetch('/api/voice/config', {cache: 'no-store'}, null, 1200,
      async response => response.ok ? response.json() : {enabled: false})
      .then(config => { this.config = config; this.configAt = Date.now(); return config; })
      .catch(() => ({enabled: false})).finally(() => { this.configPromise = null; });
    return this.configPromise;
  }

  speak(message, options = {}) {
    if (typeof message !== 'string' || !message.trim()) return Promise.resolve(cancelled('empty'));
    if (this.disposed || isHidden()) return Promise.resolve(cancelled(this.disposed ? 'disposed' : 'hidden'));
    message = message.trim();
    const duplicate = [...this.jobs].find(job => job.message === message
      && job.options.voice === options.voice && Boolean(job.options.preview) === Boolean(options.preview));
    if (duplicate) return duplicate.promise;
    // Superseding a countdown/request update may remove stale queued speech,
    // but the currently spoken sentence always finishes.
    if (options.group) {
      for (const job of [...this.jobs]) {
        if (job.options.group === options.group && job.state !== 'playing') cancelJob(job, 'superseded');
      }
    }
    if (pending.filter(job => !job.settled).length + (current && !current.settled ? 1 : 0) >= MAX_QUEUED)
      return Promise.resolve({unavailable: true, reason: 'queue_full'});
    const lifetime = Number.isFinite(options.expiresMs) ? Math.max(1, options.expiresMs) : 60000;
    const job = {owner: this, message, options: {...options}, expiresAt: Date.now() + lifetime,
      controller: new AbortController(), state: 'queued', settled: false, preparationStarted: false};
    job.promise = new Promise(resolve => { job.resolve = resolve; });
    job.ready = new Promise(resolve => { job.readyResolve = resolve; });
    this.jobs.add(job); pending.push(job); prepareAhead(); void drain();
    return job.promise;
  }

  preview(voice) {
    return this.speak('Welcome aboard BusGuard. Your journey, your pace.', {voice, preview: true, group: 'voice-preview'});
  }

  dispose() {
    this.disposed = true; this.cancel(); instances.delete(this);
    if (!instances.size) clips.clear();
  }
}
