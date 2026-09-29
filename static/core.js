export class Generation {
  constructor() { this.value = 0; this.abort = new AbortController(); }
  next() { this.abort.abort(); this.abort = new AbortController(); return ++this.value; }
  current(value) { return value === this.value; }
}

export function promptCategories(prompt, mode = 'objects') {
  const seen = new Set();
  return (mode === 'phrase' ? prompt.split(/[\r\n;]+/) : prompt.split(',')).map(label => label.trim()).filter(label => {
    const key = label.toLowerCase();
    if (!label || seen.has(key)) return false;
    seen.add(key); return true;
  });
}

export function evidenceLabels(result, appliedOptions, draftOptions) {
  const options = result ? appliedOptions : draftOptions;
  return options ? promptCategories(options.prompt,options.mode) : [];
}

export function inferencePresentation(result) {
  if(result?.cached) return {text:'Cached',title:'Unchanged image and settings; reusing the previous detection result.'};
  return {text:Number.isFinite(result?.inference_ms) ? `${Math.round(result.inference_ms)} ms` : '—',
    title:result?.detection_engine ? `Detection engine: ${result.detection_engine}` : ''};
}

export function validatePrompt(prompt, mode = 'objects') {
  const categories = promptCategories(prompt, mode);
  if (!categories.length) throw new Error('Enter at least one detection target.');
  if (prompt.length > 2048) throw new Error('Keep the complete prompt within 2,048 characters.');
  if (/[<>\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(prompt)) throw new Error('Use plain text without model control tokens.');
  if (categories.length > (mode === 'phrase' ? 8 : 32)) throw new Error(mode === 'phrase' ? 'Use at most 8 detailed phrases, separated by new lines or semicolons.' : 'Enter at most 32 object categories.');
  if (mode === 'phrase' && categories.some(label => label.length > 160)) throw new Error('Keep each detailed phrase within 160 characters.');
  return categories;
}

export class PromptDrafts {
  constructor(objects, phrase = 'a person with a red shirt\na person carrying a backpack\na person using a wheelchair') {
    this.mode = 'objects'; this.values = {objects, phrase};
  }
  switch(mode, current) {
    this.values[this.mode] = current; this.mode = mode;
    return this.values[mode];
  }
  restore(mode, prompt) { this.mode = mode; this.values[mode] = prompt; }
}

export class ResolutionProfiles {
  constructor() { this.mode = 'objects'; this.values = {objects:640, phrase:512}; }
  switch(mode, current) { this.values[this.mode] = Number(current); this.mode = mode; return this.values[mode]; }
  restore(mode, size) { this.mode = mode; this.values[mode] = Number(size); }
}

// A single completed frame, never a queue. Its boxes describe its captured
// pixels, so display lifetime is independent of the live-video overlay TTL.
export class AnalyzedFrame {
  constructor(maxAgeMs = 1500) { this.maxAgeMs = maxAgeMs; this.reset(0); }
  reset(generation) { this.generation = generation; this.frame = null; this.image = null; this.latestCapture = -Infinity; }
  accept(frame, generation, now) {
    const age = now - frame.capturedAt;
    if (generation !== this.generation || !Number.isFinite(age) || age < 0 || age >= this.maxAgeMs || frame.capturedAt <= this.latestCapture) return false;
    this.latestCapture = frame.capturedAt;
    this.frame = {...frame, detections:frame.detections.filter(det => !det.predicted)};
    // Keep pixels available independently of evidence. This record deliberately
    // contains no detections or posture: expired results cannot be drawn again.
    this.image = {capturedAt:frame.capturedAt, frameId:frame.frameId, width:frame.width, height:frame.height};
    return true;
  }
  current(now) {
    if (!this.frame) return null;
    const age = now - this.frame.capturedAt;
    if (!Number.isFinite(age) || age < 0 || age >= this.maxAgeMs) { this.frame = null; return null; }
    return this.frame;
  }
  preview(now) {
    const frame = this.current(now);
    return this.image ? {frame:frame || this.image, fresh:Boolean(frame)} : null;
  }
}

export function captureDimensions(width, height, size, mode = 'objects') {
  if (![width,height,size].every(value => Number.isFinite(value) && value > 0)) throw new Error('Capture dimensions must be positive numbers.');
  const scale = mode === 'phrase'
    ? Math.min(1,size/Math.min(width,height),2*size/Math.max(width,height))
    : Math.min(1,size/Math.max(width,height));
  return {width:Math.max(1,Math.round(width*scale)),height:Math.max(1,Math.round(height*scale))};
}

export function detectionScoreLabel(score, mode = 'objects') {
  if (!Number.isFinite(score)) return '';
  return mode === 'phrase' ? ` ${score.toFixed(2)}` : ` ${Math.round(score*100)}%`;
}

export function abortable(promise, signal) {
  return new Promise((resolve,reject) => {
    const abort = () => reject(signal.reason ?? new DOMException('Operation cancelled.','AbortError'));
    if (signal.aborted) abort(); else signal.addEventListener('abort',abort,{once:true});
    Promise.resolve(promise).then(resolve,reject).finally(() => signal.removeEventListener('abort',abort));
  });
}

export function loadVideo(video, signal) {
  return new Promise((resolve,reject) => {
    const cleanup = () => {video.removeEventListener('loadeddata',loaded);video.removeEventListener('error',failed);signal.removeEventListener('abort',aborted);};
    const loaded = () => {cleanup();resolve();};
    const failed = () => {cleanup();reject(new Error('This browser cannot decode this video. Try an MP4 with H.264 video.'));};
    const aborted = () => {cleanup();reject(signal.reason ?? new DOMException('Operation cancelled.','AbortError'));};
    if(signal.aborted) {aborted();return;}
    video.addEventListener('loadeddata',loaded,{once:true});video.addEventListener('error',failed,{once:true});signal.addEventListener('abort',aborted,{once:true});
    try {video.load();} catch(error) {cleanup();reject(error);}
  });
}

export class ConfidenceChoices {
  constructor() { this.values = new Map(); this.fallback = .35; this.labels = []; }
  sync(prompt, mode = 'objects') {
    this.labels = promptCategories(prompt, mode);
    const active = new Set(this.labels.map(label => label.toLowerCase()));
    for (const key of active) if (!this.values.has(key)) this.values.set(key, this.fallback);
    // Retain choices through temporary edits/cut-and-paste, with a bounded history.
    for (const key of this.values.keys()) {
      if (this.values.size <= 256) break;
      if (!active.has(key)) this.values.delete(key);
    }
    return this.labels.map(label => ({label, value:this.values.get(label.toLowerCase())}));
  }
  set(label, value) {
    if (!Number.isFinite(value) || value < .05 || value > .95) throw new Error('Confidence must be between 5% and 95%.');
    this.values.set(label.toLowerCase(), value);
  }
  restore(prompt, mode, overrides = {}, fallback = .35) {
    this.fallback = fallback;
    this.values = new Map(Object.entries(overrides).map(([label, value]) => [label.trim().toLowerCase(), value]));
    return this.sync(prompt, mode);
  }
  toJSON() { return Object.fromEntries(this.labels.map(label => [label, this.values.get(label.toLowerCase())])); }
}

export class ConfidenceProfiles {
  constructor(objectDefault = .4, phraseDefault = .3) {
    this.objects = new ConfidenceChoices(); this.objects.fallback = objectDefault;
    this.phrase = new ConfidenceChoices(); this.phrase.fallback = phraseDefault;
  }
  forMode(mode) { return mode === 'phrase' ? this.phrase : this.objects; }
}

export function boxesAtTime(rows, time, maxGap = 0.25) {
  if (!rows.length || time < rows[0].time) return [];
  let lo = 0, hi = rows.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (rows[mid].time <= time) lo = mid; else hi = mid - 1;
  }
  const row = rows[lo], next = rows[lo + 1];
  if (time - row.time > maxGap) return [];
  const visible = row.detections.filter(d => !d.predicted || d.observed_at_ms == null || time * 1000 <= (d.prediction_expires_at_ms ?? d.observed_at_ms + 150));
  if (!next || next.time - row.time > maxGap || next.time <= row.time) return visible;
  const future = new Map(next.detections.filter(d => d.track_id != null).map(d => [d.track_id, d]));
  const t = (time - row.time) / (next.time - row.time);
  return visible.map(det => {
    const target = future.get(det.track_id);
    if (!target || target.label !== det.label) return det;
    return { ...det, bbox: det.bbox.map((n, i) => n + (target.bbox[i] - n) * t), predicted: !!det.predicted || !!target.predicted };
  });
}

export function liveBoxes(detections, capturedAt, now, { compensate = true, bridgeMs = 150 } = {}) {
  const age = now - capturedAt;
  if (!Number.isFinite(age) || age < 0 || age > 250) return [];
  return detections.filter(det => !det.predicted || now <= Math.min(det.prediction_expires_at_ms ?? Infinity, (det.observed_at_ms ?? capturedAt) + bridgeMs)).map(det => {
    if (!compensate || det.predicted || !det.velocity?.every(Number.isFinite)) return det;
    const dt = Math.min(age, 100) / 1000;
    const [x1, y1, x2, y2] = det.bbox;
    const boundX = (x2 - x1) * .25, boundY = (y2 - y1) * .25;
    const dx = Math.max(-boundX, Math.min(boundX, det.velocity[0] * dt));
    const dy = Math.max(-boundY, Math.min(boundY, det.velocity[1] * dt));
    return { ...det, bbox: [x1 + dx, y1 + dy, x2 + dx, y2 + dy].map(v => Math.max(0, Math.min(1, v))) };
  });
}

export function contentRect(width, height, sourceWidth, sourceHeight) {
  const scale = Math.min(width / sourceWidth, height / sourceHeight);
  const w = sourceWidth * scale, h = sourceHeight * scale;
  return { x: (width - w) / 2, y: (height - h) / 2, w, h };
}

export function colorFor(label) {
  const palette = ['#b8f27b', '#80cff2', '#ffa18a', '#d6afff', '#f0db84'];
  const sum = new TextEncoder().encode(label).reduce((a, b) => a + b, 0);
  return palette[sum % palette.length];
}

export async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail); } catch {}
    const error = new Error(detail); error.status = response.status; throw error;
  }
  return response.json();
}
