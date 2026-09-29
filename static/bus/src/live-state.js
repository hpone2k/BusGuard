export const TYPE_NAMES = {wheelchair: 'Wheelchair', pram: 'Pram / stroller', walking: 'Walking aid', senior: 'Senior · RFID', assistance: 'Assistance · RFID'};
const MOBILITY_TYPES = ['wheelchair', 'pram', 'walking'];
const MOBILITY_LABELS = new Map(Object.entries({
  wheelchair: ['wheelchair', 'wheelchairs', 'wheelchair user', 'person in a wheelchair',
    'person using a wheelchair', 'person with wheelchair'],
  pram: ['pram', 'prams', 'stroller', 'strollers', 'baby stroller', 'baby pram', 'pushchair'],
  walking: ['walker', 'walking frame', 'walking aid', 'walking stick', 'cane', 'crutch',
    'crutches', 'rollator', 'person with a walking stick', 'person using a walker'],
}).flatMap(([type, labels]) => labels.map(label => [label, type])));
const mobilityType = label => MOBILITY_LABELS.get(String(label).trim().toLowerCase().replace(/[\s_-]+/g, ' '));
function currentVisionEvent(event, source) {
  return !event.is_demo && event.origin === 'vision' && MOBILITY_TYPES.includes(event.type)
    && event.source_kind === 'live' && event.source_role === 'outside' && source?.connected
    && !source.is_demo && source.kind === 'live' && source.session_id === event.session_id;
}

// The display requests virtual assistance; camera absence never proves boarding.
export class AssistanceState {
  constructor() { this.cursor = null; this.revision = null; this.instanceId = null; this.lastSnapshotAt = null; this.pending = []; this.holdUntil = 0; this.snapshot = null; this.online = false; this.handledTracks = new Set(); }
  ingest(snapshot, now = performance.now()) {
    const events = snapshot.events || [];
    const newest = Math.max(0, ...events.map(e => e.id));
    const reset = this.instanceId !== null && snapshot.instance_id !== this.instanceId || this.revision !== null && snapshot.revision < this.revision;
    const baseline = this.cursor === null || reset;
    if (baseline) this.cursor = newest;
    const incoming = events.filter(e => e.id > this.cursor);
    this.cursor = Math.max(this.cursor, newest); this.revision = snapshot.revision;
    this.snapshot = snapshot; this.online = true; this.lastSnapshotAt = now; this.instanceId = snapshot.instance_id ?? null;
    const source = this.source('outside', now);
    const trackKey = event => JSON.stringify([this.instanceId, event.session_id, event.type, event.track_id]);
    const accepted = incoming.filter(event => {
      const age = snapshot.server_time_ms - event.timestamp_ms;
      if (event.is_demo || !TYPE_NAMES[event.type] || age < 0 || age > 2500) return false;
      if (event.origin === 'rfid') return event.source_kind === 'rfid' && ['senior', 'assistance'].includes(event.type);
      return currentVisionEvent(event, source);
    });
    if (baseline) {
      // Reopening the twin can recover a passenger still observed in the current
      // frame. A retained confirmation alone is only history, never a new request.
      const recoveredTracks = new Set(this.handledTracks);
      for (const event of events) {
        if (!currentVisionEvent(event, source) || event.track_id == null || recoveredTracks.has(trackKey(event))) continue;
        const observation = source.detections?.find(detection => !detection.predicted
          && detection.track_id === event.track_id && mobilityType(detection.label) === event.type);
        if (!observation) continue;
        accepted.push({...event, recovered: true, observed_at_ms: source.received_at_ms,
          confidence: observation.score ?? null});
        recoveredTracks.add(trackKey(event));
      }
    }
    if (accepted.length) {
      this.pending = [...this.pending, ...accepted].slice(-30);
      this.holdUntil = now + 20000;
      for (const event of accepted) if (event.origin === 'vision' && event.track_id != null) this.handledTracks.add(trackKey(event));
      while (this.handledTracks.size > 1024) this.handledTracks.delete(this.handledTracks.values().next().value);
    }
    return accepted;
  }
  disconnect() { this.online = false; }
  isOnline(now = performance.now()) { return this.online && this.lastSnapshotAt !== null && now - this.lastSnapshotAt <= 3000; }
  source(role, now = performance.now()) {
    const source = this.snapshot?.sources?.[role]; if (!source) return null;
    const elapsed = Math.max(0, now - this.lastSnapshotAt), age = source.age_ms == null ? null : source.age_ms + elapsed;
    const connected = this.isOnline(now) && source.connected && age !== null && age <= (source.kind === 'image' ? 10000 : 2500);
    return {...source, age_ms: age, connected, status: source.connected && !connected ? 'stale' : source.status, detections: connected ? source.detections : []};
  }
  remaining(now = performance.now()) { return Math.max(0, Math.ceil((this.holdUntil - now) / 1000)); }
  acknowledge(now = performance.now()) {
    if (this.remaining(now)) return false;
    this.pending = []; this.holdUntil = 0; return true;
  }
  response() {
    return {active: !!this.pending.length, ramp: this.pending.some(e => ['wheelchair', 'pram', 'walking'].includes(e.type)),
      priority: this.pending.some(e => ['senior', 'walking', 'assistance'].includes(e.type)), types: [...new Set(this.pending.map(e => e.type))]};
  }
}

export function observedSummary(source) {
  if (!source?.connected) return [];
  const counts = new Map();
  for (const d of source.detections || []) if (!d.predicted) counts.set(d.label, (counts.get(d.label) || 0) + 1);
  return [...counts].map(([label, count]) => `${count} ${label}`);
}
