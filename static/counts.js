// Presentation only: elapsed-time voting is owned by the inference pipeline.
// ageMs is the complete age of the corresponding frame, not an extra poll age.
const finite = value => typeof value === 'number' && Number.isFinite(value);
const integer = value => finite(value) && Number.isInteger(value) && value >= 0 && value <= 10000 ? value : null;
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const statuses = new Set(['warming', 'stable', 'uncertain', 'stale', 'instant']);

function names(labels) {
  const seen = new Set();
  return (Array.isArray(labels) ? labels : []).filter(label => {
    if (typeof label !== 'string' || !label.trim()) return false;
    const key = label.trim().toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key); return true;
  }).map(label => label.trim().slice(0, 160)).slice(0, 64);
}

export function observedCounts(detections = [], labels = []) {
  const counts = new Map(names(labels).map(label => [label, 0]));
  const canonical = new Map([...counts.keys()].map(label => [label.toLowerCase(), label]));
  const tracked = new Set();
  for (const detection of (Array.isArray(detections) ? detections : []).slice(0, 300)) {
    if (!detection || detection.predicted || typeof detection.label !== 'string' || !detection.label.trim()) continue;
    const normalized = detection.label.trim().slice(0, 160);
    const key = normalized.toLowerCase();
    if (!canonical.has(key)) canonical.set(key, normalized);
    const label = canonical.get(key);
    if (detection.track_id != null) {
      const identity = JSON.stringify([key, String(detection.track_id)]);
      if (tracked.has(identity)) continue;
      tracked.add(identity);
    }
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  return {total: [...counts.values()].reduce((sum, count) => sum + count, 0), classes: Object.fromEntries(counts)};
}

function emptyEstimate(status = 'warming') {
  return {raw: null, stable: null, candidate: null, support: null, status,
    held: false, display: '—', supportText: status === 'stale' ? 'Stale' : 'Waiting'};
}

function presentEstimate(estimate, context) {
  if (!estimate || typeof estimate !== 'object') return emptyEstimate(context.stale ? 'stale' : 'warming');
  let status = statuses.has(estimate.status) ? estimate.status : 'warming';
  let raw = integer(estimate.raw), stable = integer(estimate.stable), candidate = integer(estimate.candidate);
  let support = finite(estimate.support) ? clamp(estimate.support, 0, 1) : null;
  if (context.stale || status === 'stale') return emptyEstimate('stale');
  if (context.instant) {
    const count = raw ?? stable;
    return {raw: count, stable: count, candidate: count, support: null, status: 'instant',
      held: false, display: count == null ? '—' : String(count), supportText: 'Single image'};
  }
  if (status === 'instant') status = 'warming'; // A live source never claims a temporal vote from one snapshot.
  if (context.coverageMs < context.windowMs) status = 'warming';
  if (status === 'warming') stable = null;
  if (status === 'uncertain' && stable != null) {
    // The server measures hold age; adding only time since its snapshot keeps
    // repeated uncertain responses from extending a held count indefinitely.
    const heldAge = finite(estimate.held_age_ms) ? estimate.held_age_ms : null;
    if (heldAge == null || heldAge + context.extraAgeMs > context.holdMs) stable = null;
  }
  const held = status === 'uncertain' && stable != null;
  const supportText = status === 'warming' ? 'Warming up'
    : support == null ? 'No vote support'
    : status === 'uncertain' ? `${Math.round(support * 100)}% leading vote · uncertain`
    : `${Math.round(support * 100)}% vote support`;
  return {raw, stable, candidate, support, status, held,
    display: stable == null ? '—' : String(stable), supportText};
}

export function countPresentation(summary, {ageMs = 0, mode = 'live', detections = null, labels = []} = {}) {
  const summaryValid = summary && typeof summary === 'object' && summary.total && typeof summary.total === 'object';
  const requested = names(labels);
  const age = finite(ageMs) && ageMs >= 0 ? ageMs : Infinity;
  if (mode === 'empty' || (!summaryValid && !Array.isArray(detections))) {
    return {mode: 'empty', status: 'warming', windowMs: 1000, coverageMs: 0, ageMs: age,
      total: emptyEstimate(), classes: Object.fromEntries(requested.map(label => [label.toLowerCase(), {...emptyEstimate(), label}])),
      message: 'Awaiting detection. Live counts use a full 1-second voting window.'};
  }
  const instant = mode === 'image';
  if (!summaryValid) {
    const observed = observedCounts(detections, requested);
    const stale = !instant && age >= 1500;
    const estimate = count => stale ? emptyEstimate('stale') : {
      raw: count, stable: instant ? count : null, candidate: instant ? count : null,
      support: null, status: instant ? 'instant' : 'legacy', held: false,
      display: String(count), supportText: instant ? 'Single image' : 'No vote data'};
    return {mode: instant ? 'instant' : 'legacy', status: stale ? 'stale' : instant ? 'instant' : 'legacy',
      windowMs: 1000, coverageMs: 0, ageMs: instant ? 0 : age,
      total: estimate(observed.total), classes: Object.fromEntries(Object.entries(observed.classes).map(([label, count]) => [label.toLowerCase(), {...estimate(count), label}])),
      message: instant ? 'Single-image counts. Temporal voting does not apply.'
        : stale ? 'Waiting for fresh count data.' : 'This result has no voting data. Showing observed counts only.'};
  }
  const baseAge = finite(summary.age_ms) && summary.age_ms >= 0 ? summary.age_ms : 0;
  const effectiveAge = instant ? 0 : Math.max(age, baseAge);
  const windowMs = finite(summary.window_ms) && summary.window_ms > 0 ? summary.window_ms : 1000;
  const coverageMs = finite(summary.coverage_ms) ? clamp(summary.coverage_ms, 0, windowMs) : 0;
  const staleMs = finite(summary.stale_ms) && summary.stale_ms > 0 ? summary.stale_ms : 1500;
  const holdMs = finite(summary.uncertain_hold_ms) && summary.uncertain_hold_ms >= 0 ? summary.uncertain_hold_ms : 1000;
  const context = {instant, stale: !instant && (effectiveAge >= staleMs || summary.status === 'stale'), windowMs, coverageMs,
    holdMs, extraAgeMs: Math.max(0, effectiveAge - baseAge)};
  const classes = summary.classes && typeof summary.classes === 'object' ? summary.classes : {};
  const allLabels = names([...requested, ...Object.keys(classes)]);
  const byName = new Map(Object.entries(classes).map(([label, entry]) => [label.trim().toLowerCase(), entry]));
  const total = presentEstimate(summary.total, context);
  const entries = Object.fromEntries(allLabels.map(label => [label.toLowerCase(), {...presentEstimate(byName.get(label.toLowerCase()), context), label}]));
  const requiredSupport = finite(summary.support_threshold) ? clamp(summary.support_threshold, .5, 1) : .65;
  const message = instant ? 'Single-image counts. Temporal voting does not apply.'
    : total.status === 'stale' ? 'Waiting for fresh count data.'
    : total.status === 'warming' ? `Building a full 1-second window · ${Math.round(coverageMs)} / ${Math.round(windowMs)} ms.`
    : `Elapsed-time vote over the last 1 second · at least ${Math.round(requiredSupport * 100)}% vote support. Support measures agreement, not detection accuracy.`;
  return {mode: instant ? 'instant' : 'voted', status: total.status, windowMs, coverageMs,
    ageMs: effectiveAge, total, classes: entries, message};
}

export function countsAtTime(rows, playheadSeconds, {labels = []} = {}) {
  if (!Array.isArray(rows) || !rows.length || !finite(playheadSeconds) || playheadSeconds < rows[0].time)
    return countPresentation(null, {mode: 'empty', labels});
  let lo = 0, hi = rows.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (rows[mid].time <= playheadSeconds) lo = mid; else hi = mid - 1;
  }
  const row = rows[lo];
  return countPresentation(row.count_summary, {mode: 'video', labels,
    detections: row.detections || [], ageMs: Math.max(0, (playheadSeconds - row.time) * 1000)});
}
