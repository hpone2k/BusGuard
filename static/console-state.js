// Ownership of an input changes before asynchronous cleanup begins. A late
// camera permission or detection result can never reclaim a replaced source.
export class SourceLease {
  constructor() { this.generation = 0; this.abort = new AbortController(); this.stream = null; }
  renew() { this.abort.abort(); this.abort = new AbortController(); return ++this.generation; }
  current(token) { return token === this.generation; }
  acceptStream(token, stream) {
    if (!this.current(token)) { stream.getTracks().forEach(track => track.stop()); return false; }
    this.releaseStream(); this.stream = stream; return true;
  }
  releaseStream() { this.stream?.getTracks().forEach(track => track.stop()); this.stream = null; }
}

// A preview fetch may span door closing and reopening without replacing the
// source. Give that interval its own ownership; a late image cannot unpause it.
// Posture freshness is intentionally separate: refreshing an overlay must not
// blank the cabin video or discard its otherwise valid person-count frame.
export class PreviewLease {
  constructor() { this.generation = 0; this.context = null; this.paused = true; }
  sync({kind, sessionId, sourceGeneration, paused}) {
    const context = JSON.stringify([kind, sessionId, sourceGeneration, Boolean(paused)]);
    if (context !== this.context) { this.context = context; this.generation++; }
    this.paused = Boolean(paused);
    return this.generation;
  }
  current(token) { return !this.paused && token === this.generation; }
}

export function agedSource(source, elapsedMs = 0, online = true) {
  if (!source) return null;
  const elapsed = Number.isFinite(elapsedMs) && elapsedMs >= 0 ? elapsedMs : Infinity;
  const age = typeof source.age_ms === 'number' && Number.isFinite(source.age_ms) ? source.age_ms + elapsed : Infinity;
  const ttl = source.kind === 'image' ? 10000 : 1500;
  return {...source, age_ms: age, connected: Boolean(online && source.connected && age < ttl),
    status: !online || age >= ttl ? 'stale' : source.status};
}

// The bridge retains role history across page reloads. A card must not present
// that history as evidence from its newly selected input type.
export function matchingSource(source, {role, kind, session = null, camera = null, pending = false} = {}) {
  if(pending || !source?.session_id || source.role !== role) return null;
  if(kind === 'rtsp') return camera?.enabled && camera.session_id === source.session_id && source.kind === 'live' ? source : null;
  const expectedKind = {webcam:'live',image:'image',video:'video'}[kind];
  return session && source.session_id === session && source.kind === expectedKind ? source : null;
}

export function sourceEvidence(source) {
  if (!source?.connected) return source?.session_id ? 'Waiting for fresh input' : 'Disconnected';
  const kind = source.kind === 'image' ? 'Image test' : source.kind === 'video' ? 'Recorded video' : 'Live';
  return `${kind} · ${Math.round(source.age_ms || 0)} ms old`;
}

export function shouldPreview(camera, lastId, visible = true) {
  return Boolean(visible && camera?.enabled && camera.preview_available
    && camera.preview_frame_id != null && camera.preview_frame_id !== lastId);
}

// Live camera pixels do not need to wait for inference. Keep the matching
// analysed image available separately when phrases or standing need it.
export function browserPreviewMode(kind, options, selected = 'live') {
  const needsFrame = Boolean(options?.mode === 'phrase' || options?.posture_enabled);
  const selectable = kind === 'webcam' && needsFrame;
  return {selectable, retainFrame:needsFrame && ['webcam','video'].includes(kind),
    aligned:needsFrame && (kind === 'video' || selectable && selected === 'detection')};
}

export function backgroundCameraActive(card) {
  return Boolean(card?.kind === 'webcam' && card.active && card.hasMedia);
}
