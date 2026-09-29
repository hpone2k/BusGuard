const norm = value => String(value).trim().toLowerCase().replace(/[\s_-]+/g, ' ');
const PEOPLE = new Set(['person', 'people', 'persons', 'human']);
const TYPE_LABELS = [
  {name: 'Wheelchair', aliases: ['wheelchair', 'wheelchairs', 'wheelchair user', 'person in a wheelchair', 'person using a wheelchair']},
  {name: 'Pram / stroller', aliases: ['pram', 'prams', 'stroller', 'strollers', 'baby stroller', 'baby pram', 'pushchair']},
  {name: 'Walking aid', aliases: ['walker', 'walking aid', 'walking frame', 'walking stick', 'cane', 'crutch', 'crutches', 'rollator']}
];
const number = value => Number.isInteger(value) && value >= 0;

// An interior camera estimates visible people, not occupancy of unseen areas.
// Never add overlapping attribute/equipment classes to the person estimate.
export function cabinPresentation(snapshot, elapsedMs = 0, online = true) {
  const empty = {people: null, types: TYPE_LABELS.map(type => ({name: type.name, count: null})), typeCount: null,
    state: 'waiting', status: 'Awaiting cabin', detail: 'Waiting for cabin data', note: 'Connect an inside source in the detection console.'};
  const source = snapshot?.sources?.inside;
  if (!online) return {...empty, state: 'stale', status: 'Offline', detail: 'Connection interrupted', note: 'Reconnect to the detection console to receive current observations.'};
  if (!source?.session_id) return empty;
  if (source.is_demo) return {...empty, status: 'Demo source', note: 'Demo detections do not establish real passengers aboard.'};
  const age = (source.age_ms ?? Infinity) + Math.max(0, elapsedMs);
  const maxAge = source.kind === 'image' ? 10000 : 1500;
  if (!source.connected || age >= maxAge) return {...empty, state: 'stale', status: 'Signal paused', detail: 'No current cabin estimate', note: 'Cabin data is out of date. Waiting for a fresh observation.'};
  const classes = Object.entries(source.count_summary?.classes || {}).map(([label, estimate]) => [norm(label), estimate]);
  const read = estimate => estimate && ['stable', 'instant'].includes(estimate.status) && number(estimate.stable) ? estimate.stable : null;
  const personEstimates = classes.filter(([label]) => PEOPLE.has(label));
  const people = personEstimates.length === 1 ? read(personEstimates[0][1]) : null;
  const types = TYPE_LABELS.flatMap(type => {
    const matches = classes.filter(([label]) => type.aliases.includes(label));
    if (matches.length <= 1) return [{name: type.name, count: matches.length ? read(matches[0][1]) : null}];
    // Individually requested aids are shown separately; they may overlap.
    return matches.map(([label, estimate]) => ({name: label.charAt(0).toUpperCase() + label.slice(1), count: read(estimate)}));
  });
  const knownTypes = types.filter(type => type.count !== null);
  const typeCount = knownTypes.length ? knownTypes.filter(type => type.count > 0).length : null;
  const image = source.kind === 'image', video = source.kind === 'video';
  const stable = people !== null;
  const support = personEstimates.length === 1 ? personEstimates[0][1].support : null;
  return {people, types, typeCount, state: image || video ? 'recorded' : stable ? 'live' : 'warming',
    status: image ? 'Image snapshot' : video ? 'Video playback' : stable ? 'Live cabin' : 'Confirming',
    detail: image ? 'Single image observation' : stable && Number.isFinite(support) ? `${Math.round(support * 100)}% temporal support` : 'Gathering a stable count',
    note: image || video ? 'Recorded source; this does not confirm current occupancy.' : 'Visible cabin estimate. Passenger and aid types can overlap.'};
}
