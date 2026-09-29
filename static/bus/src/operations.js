import {AssistanceState, TYPE_NAMES} from './live-state.js';
import {DepartureState, DEPARTURE_CONFIRM_MS} from './departure-state.js';
import {countPresentation} from '../../counts.js';
const $ = id => document.getElementById(id);
const roles = ['outside', 'inside'];
const classes = ['person', 'wheelchair', 'stroller', 'walker', 'crutch', 'cane'];
const time = value => new Date(value).toLocaleTimeString('en-SG', {hour12: false});
const seconds = value => value == null ? '—' : `${(value / 1000).toFixed(1)}s`;
let departureRenderKey = '';

export function renderDeparture(view, mode = 'live') {
  if (!$('departurePanel')) return;
  const key = JSON.stringify([view, mode]);
  if (key === departureRenderKey) return;
  departureRenderKey = key;
  const status = view?.status || 'unknown';
  const labels = {boarding: 'BOARDING', standing: 'HOLD', unknown: 'HOLD', confirming: 'CHECKING', ready: 'READY'};
  const titles = {boarding: 'Boarding in progress', standing: 'Waiting for passengers to sit',
    unknown: 'Cabin confirmation needed', confirming: 'Confirming seated passengers', ready: 'Ready for virtual departure'};
  const count = value => Number.isInteger(value) && value >= 0 ? String(value) : '—';
  $('departurePanel').dataset.state = status;
  $('departureStatus').textContent = labels[status] || 'HOLD';
  $('departureTitle').textContent = titles[status] || titles.unknown;
  $('departureMessage').textContent = view?.message || 'Fresh interior observations are needed before departure can be confirmed.';
  $('seatedCount').textContent = count(view?.seated);
  $('standingCount').textContent = count(view?.standing);
  $('unknownPostureCount').textContent = count(view?.unknown);
  $('visiblePostureCount').textContent = `${count(view?.people)} observed passengers`;
  const progress = Math.max(0, Math.min(DEPARTURE_CONFIRM_MS, Number(view?.progressMs) || 0));
  const confirmationSeconds = DEPARTURE_CONFIRM_MS / 1000;
  $('departureProgress').max = DEPARTURE_CONFIRM_MS;
  $('departureProgress').value = progress;
  $('departureProgressText').textContent = status === 'ready' ? `${confirmationSeconds}-second seating check complete`
    : status === 'confirming' ? `Continuous seating check · ${(progress / 1000).toFixed(1)} / ${confirmationSeconds.toFixed(1)}s`
    : status === 'boarding' ? 'Seating check begins after the doors close'
    : `Fresh, complete observations required for ${confirmationSeconds} seconds`;
  $('seatingAnnouncement').hidden = status !== 'standing' || !view?.alert;
  $('departureSource').textContent = mode === 'lab'
    ? 'SCRIPTED SEATING TEST · NO CAMERA INPUT'
    : 'INTERIOR CCTV · EXPERIMENTAL POSTURE ESTIMATION';
  $('seatingCoverage').textContent = mode === 'lab'
    ? 'The test accounts for its scripted passengers. All movement is virtual.'
    : 'Every observed passenger must be accounted for. Missing or uncertain posture keeps departure on hold; unseen areas cannot be checked.';
}

async function request(path, options = {}, timeout = 5000) {
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {...options, signal: controller.signal, cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the camera settings and try again.');
    return data;
  } finally { clearTimeout(timer); }
}

export function startOperations({onMode, onResponse, getVehicleState = () => ({doors: 1, ramp: 0}), onDeparture = () => {}}) {
  const live = new AssistanceState(); let mode = 'live', cameras = [], stopped = false, timer, cycle = 0;
  let eventKey = '', lastResponse = '', backendReady = false;
  let departure = new DepartureState(), departureInstance = null;
  const previewFrames = {};
  const restoredCameras = new Set();
  function publishResponse() {
    const response = live.response(), key = JSON.stringify(response);
    if (key !== lastResponse) { lastResponse = key; onResponse(response); }
  }
  function setMode(next) {
    departure = new DepartureState();
    mode = next; document.body.dataset.mode = mode;
    document.querySelectorAll('[data-mode-choice]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.modeChoice === mode)));
    $('livePanel').hidden = mode !== 'live'; $('labPanel').hidden = mode !== 'lab';
    $('seatingDemoControls').hidden = mode !== 'lab';
    $('modeBadge').textContent = mode === 'live' ? 'DETECTION-LINKED TWIN' : 'SCRIPTED SCENARIO';
    $('feedMode').textContent = mode === 'live' ? 'PROCESSED CAMERA FRAMES' : 'VIRTUAL CAMERA VIEWS';
    roles.forEach(role => { $(role + 'Feed').hidden = mode !== 'lab'; $(role + 'Image').hidden = true; });
    onMode(mode); onResponse(live.response()); render();
  }
  document.querySelectorAll('[data-mode-choice]').forEach(b => b.onclick = () => setMode(b.dataset.modeChoice));
  $('acknowledge').onclick = () => { if (live.acknowledge()) { publishResponse(); render(); } };
  document.querySelectorAll('[data-connections]').forEach(button => button.onclick = () => $('connectionsDialog').showModal());
  $('closeConnections').onclick = () => $('connectionsDialog').close();
  $('connectionsDialog').addEventListener('close', () => roles.forEach(role => $(role + 'Url').value = ''));

  for (const role of roles) {
    $(role + 'Url').value = '';
    const parent = $(role + 'Thresholds');
    for (const name of classes) {
      const label = document.createElement('label'); const text = document.createElement('span'); text.textContent = name === 'stroller' ? 'Pram / stroller' : name[0].toUpperCase() + name.slice(1);
      const output = document.createElement('output'); output.textContent = '40%';
      const input = document.createElement('input'); input.type = 'range'; input.min = '5'; input.max = '95'; input.step = '5'; input.value = '40'; input.name = name;
      input.setAttribute('aria-label', `${role} ${name} minimum confidence`); input.oninput = () => output.textContent = input.value + '%';
      label.append(text, output, input); parent.append(label);
    }
    $(role + 'Form').onsubmit = async event => {
      event.preventDefault(); const button = $(role + 'Connect'); button.disabled = true;
      $(role + 'FormStatus').textContent = 'Connecting camera…';
      try {
        const class_confidences = Object.fromEntries([...parent.querySelectorAll('input')].map(input => [input.name, Number(input.value) / 100]));
        const configured = await request(`/api/bus/cameras/${role}`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
          url: $(role + 'Url').value.trim(), name: $(role + 'Name').value.trim(),
          options: {prompt: classes.join(', '), mode: 'objects', confidence: .4, class_confidences, size: Number($(role + 'Size').value), stabilization: 'balanced', camera_motion: false}
        })});
        cameras = [...cameras.filter(camera => camera.role !== role), configured]; restoredCameras.add(role);
        $(role + 'Url').value = ''; $(role + 'FormStatus').textContent = 'Camera configured. Waiting for the first processed frame.';
      } catch (error) { $(role + 'FormStatus').textContent = error.name === 'AbortError' ? 'Connection request timed out. Check camera status before retrying.' : error.message; }
      finally { button.disabled = false; }
    };
    $(role + 'Disconnect').onclick = async () => {
      const button = $(role + 'Disconnect'); button.disabled = true;
      try { await request(`/api/bus/cameras/${role}`, {method: 'DELETE'}); $(role + 'FormStatus').textContent = 'Camera disconnected.'; }
      catch { $(role + 'FormStatus').textContent = 'Could not disconnect. Check the local server.'; }
      finally { button.disabled = false; }
    };
    $(role + 'Image').onerror = () => { $(role + 'Image').hidden = true; $(role + 'Empty').hidden = mode !== 'live'; previewFrames[role] = null; };
    $(role + 'Image').onload = () => {
      const source = live.source(role), camera = cameras.find(c => c.role === role);
      $(role + 'Image').hidden = mode !== 'live' || !source?.connected || source.kind !== 'live' || source.is_demo || !camera?.preview_available || camera.session_id !== source.session_id;
      $(role + 'Empty').hidden = !$(role + 'Image').hidden || mode !== 'live';
    };
  }

  function updateDeparture() {
    if (stopped || document.hidden || mode !== 'live') return;
    const now = performance.now(), vehicle = getVehicleState() || {};
    const doorPosition = Number.isFinite(vehicle.doors) ? vehicle.doors : 1;
    const rampPosition = Number.isFinite(vehicle.ramp) ? vehicle.ramp : 1;
    const departureView = departure.update({
      doors: Math.max(doorPosition, vehicle.doorsRequested ? 1 : 0),
      ramp: Math.max(rampPosition, vehicle.rampRequested ? 1 : 0),
      assistance: live.response().active, source: live.source('inside', now), online: live.isOnline(now) && backendReady
    }, now);
    renderDeparture(departureView, mode);
    // Motion needs a current permission heartbeat even when the displayed view
    // is unchanged. DOM updates are independently deduplicated by the renderer.
    onDeparture(departureView);
  }

  function render() {
    const snapshot = live.snapshot, response = live.response(), remaining = live.remaining(), online = live.isOnline();
    updateDeparture();
    $('connectionStatus').textContent = online ? 'Data bridge connected' : 'Data bridge offline';
    $('connectionStatus').dataset.state = online ? 'ready' : 'offline';
    $('liveTitle').textContent = response.active ? response.types.map(type => TYPE_NAMES[type]).join(' + ') : online ? 'Watching for assistance' : 'Waiting for the data bridge';
    $('liveDetail').textContent = response.active ? (!online ? 'Connection lost. The virtual request is held for acknowledgement.' : remaining ? `Minimum dwell: ${remaining}s remaining. Virtual assistance is active.` : 'Minimum dwell elapsed. Acknowledge to reset the virtual response.') : 'Confirmed mobility-aid detections outside, or an RFID tap, will request assistance here.';
    $('requestIndicator').dataset.active = String(response.active);
    $('requestCount').textContent = String(live.pending.length).padStart(2, '0');
    $('rampState').textContent = response.active && response.ramp ? 'Requested' : 'Standby';
    $('doorState').textContent = response.active ? 'Hold requested' : 'Standby';
    $('holdState').textContent = response.active ? remaining ? `${remaining}s` : 'Awaiting reset' : '8s + 12s';
    $('acknowledge').disabled = !response.active || remaining > 0;
    $('acknowledge').textContent = remaining ? `Acknowledge in ${remaining}s` : 'Acknowledge & reset';
    $('livePendingBadge').textContent = response.active ? `${live.pending.length} pending` : 'No request';
    const latest = live.pending.at(-1);
    $('requestSource').textContent = latest ? `${latest.source_name} · ${latest.origin === 'rfid' ? 'RFID tap' : Math.round((latest.confidence || 0) * 100) + '% confidence'}${latest.recovered ? ' · ongoing observation' : ''} · ${time(latest.observed_at_ms ?? latest.timestamp_ms)}` : 'Live events only · images appear in the event log';
    let liveCount = 0;
    for (const role of roles) {
      const source = live.source(role), camera = cameras.find(c => c.role === role);
      if (camera?.enabled && camera.options && !restoredCameras.has(role) && !$('connectionsDialog').open) {
        restoredCameras.add(role); $(role + 'Name').value = camera.name; $(role + 'Size').value = String(camera.options.size);
        $(role + 'Thresholds').querySelectorAll('input').forEach(input => {
          input.value = String(Math.round((camera.options.class_confidences?.[input.name] ?? camera.options.confidence) * 100));
          input.previousElementSibling.textContent = input.value + '%';
        });
      }
      const connected = source?.connected;
      const isLive = connected && source.kind === 'live' && !source.is_demo;
      if (isLive) liveCount++;
      const status = !online ? 'Offline' : connected ? source.is_demo ? 'Demo detector' : source.kind === 'image' ? 'Image test' : 'Live' : source?.status === 'stale' ? 'Stale' : camera?.enabled ? ({connecting: 'Connecting', reconnecting: 'Reconnecting', error: 'Connection failed'}[camera.state] || camera.state) : 'Not connected';
      $(role + 'Status').textContent = status; $(role + 'Status').dataset.state = isLive ? 'ready' : 'offline';
      $(role + 'DialogStatus').textContent = camera?.error || (camera?.enabled ? camera.state : 'Not connected');
      $(role + 'Metrics').textContent = connected ? `${seconds(source.age_ms)} ago · ${Math.round(source.inference_ms || 0)}ms inference · frame ${source.frame_id}` : 'No current detection data';
      const counts = connected && !source.is_demo ? countPresentation(source.count_summary, {
        ageMs: source.age_ms || 0, mode: source.kind === 'image' ? 'image' : 'live', detections: source.detections
      }) : null;
      const people = counts?.mode === 'legacy' ? null : counts?.classes.person;
      const countStatus = people?.status || (source?.status === 'stale' ? 'stale' : 'waiting');
      $(role + 'CountPanel').hidden = mode === 'lab';
      $(role + 'CountPanel').dataset.state = countStatus;
      $(role + 'PeopleCount').textContent = people?.display ?? '—';
      $(role + 'CountState').textContent = ({stable: 'Stable estimate', instant: 'Image only', warming: 'Collecting', uncertain: 'Uncertain', stale: 'Stale'})[countStatus] || 'Awaiting data';
      $(role + 'CountRaw').textContent = people?.raw != null && countStatus !== 'stale' ? `Observed now: ${people.raw}` : 'No current observations';
      $(role + 'CountSupport').textContent = countStatus === 'stable' ? `${Math.round(people.support * 100)}% vote support · 1 second` : countStatus === 'instant' ? 'Single image · no time vote' : countStatus === 'uncertain' ? 'Mixed observations' : countStatus === 'warming' ? 'Collecting a full second' : '1,000 ms rolling vote';
      $(role + 'CountMeter').style.width = countStatus === 'stable' ? `${people.support * 100}%` : countStatus === 'warming' ? `${Math.min(1, (counts.coverageMs || 0) / 1000) * 100}%` : '0%';
      $(role + 'CountNote').textContent = counts?.mode === 'legacy' ? 'Waiting for a source with count voting enabled.' : countStatus === 'stale' ? 'No fresh count. Waiting for camera observations.' : countStatus === 'uncertain' ? people?.stable != null ? 'Previous estimate held briefly while the count is uncertain.' : 'No count has enough support yet.' : connected && !people && !source.is_demo ? 'Include “person” in the detection prompt to estimate visible passengers.' : 'Visible people only · camera counts are not added together.';
      const classCounts = Object.entries(counts?.classes || {}).filter(([, value]) => value.stable != null && value.stable > 0)
        .map(([label, value]) => `${value.stable} ${label}${value.status === 'uncertain' ? ' (uncertain)' : ''}`);
      $(role + 'Objects').textContent = mode === 'lab' ? 'Scripted scene · no live count estimate' : counts ? counts.status === 'stale' ? 'Count data is stale' : classCounts.length ? `${source.kind === 'image' ? 'Image counts' : 'Estimated counts'}: ${classCounts.join(' · ')}` : counts.total.status === 'stable' && counts.total.stable === 0 ? 'No objects supported by the current voting window' : 'Collecting reliable counts for each detection type' : 'Connect an RTSP camera to start detection';
      const preview = mode === 'live' && isLive && camera?.preview_available && camera.session_id === source.session_id;
      $(role + 'Empty').hidden = mode !== 'live' || (preview && !$(role + 'Image').hidden);
      $(role + 'EmptyText').textContent = connected && !preview ? 'Detection data received. Camera preview unavailable.' : camera?.enabled ? camera.error || 'Waiting for a processed frame…' : 'Camera not connected';
      $(role + 'FeedLabel').textContent = mode === 'lab' ? '3D PREVIEW' : preview ? 'PROCESSED FRAME' : 'AWAITING SIGNAL';
      if (preview) {
        const key = `${camera.session_id}:${camera.preview_frame_id}`;
        if (previewFrames[role] !== key) { previewFrames[role] = key; $(role + 'Image').src = `/api/bus/cameras/${role}/preview.jpg?frame=${camera.preview_frame_id}`; }
      } else { $(role + 'Image').hidden = true; previewFrames[role] = null; }
    }
    $('cameraCount').textContent = `${liveCount} / 2`;
    const lastRfid = snapshot?.events?.filter(e => e.origin === 'rfid').at(-1);
    $('rfidStatus').textContent = lastRfid ? `Last tap ${time(lastRfid.timestamp_ms)}` : 'Awaiting reader events';
    const events = [...(snapshot?.events || [])].reverse().slice(0, 8), key = events.map(e => `${e.id}:${e.timestamp_ms}`).join(',');
    if (key !== eventKey) {
      eventKey = key; $('eventLog').replaceChildren();
      for (const event of events) {
        const item = document.createElement('li'), dot = document.createElement('span'); dot.className = 'event-dot';
        const content = document.createElement('div'), title = document.createElement('strong'), meta = document.createElement('small');
        title.textContent = TYPE_NAMES[event.type] || event.type;
        const provenance = event.is_demo ? 'DEMO' : event.origin === 'rfid' ? 'RFID' : event.source_kind === 'image' ? 'IMAGE TEST' : 'LIVE VISION';
        meta.textContent = `${provenance} · ${event.source_name}${event.confidence == null ? '' : ' · ' + Math.round(event.confidence * 100) + '%'}`;
        const stamp = document.createElement('time'); stamp.textContent = time(event.timestamp_ms); content.append(title, meta); item.append(dot, content, stamp); $('eventLog').append(item);
      }
    }
    $('emptyEvents').hidden = events.length > 0;
  }
  async function poll() {
    if (stopped) return;
    if (document.hidden) { timer = setTimeout(poll, 1000); return; }
    try {
      const snapshot = await request('/api/bus/state', {}, 2500); if (stopped) return;
      if (snapshot.instance_id !== departureInstance) {
        departureInstance = snapshot.instance_id; departure = new DepartureState();
      }
      live.ingest(snapshot); publishResponse();
    } catch { live.disconnect(); }
    if (cycle++ % 2 === 0) {
      const results = await Promise.allSettled([request('/api/bus/cameras'), request('/api/status')]);
      if (results[0].status === 'fulfilled') cameras = results[0].value.cameras || [];
      if (results[1].status === 'fulfilled') {
        const status = results[1].value; backendReady = status.state === 'ready';
        $('engineStatus').textContent = backendReady ? (status.backend?.name || status.configured_backend || 'Detector ready') : status.state;
        $('engineStatus').dataset.state = backendReady ? 'ready' : 'offline';
      } else { backendReady = false; $('engineStatus').textContent = 'Offline'; $('engineStatus').dataset.state = 'offline'; }
    }
    if (stopped) return;
    render(); timer = setTimeout(poll, 500);
  }
  setMode('live'); poll();
  const tick = setInterval(() => { if (!document.hidden) render(); }, 500);
  const departureTick = setInterval(updateDeparture, 100);
  addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); clearInterval(tick); clearInterval(departureTick); roles.forEach(role => $(role + 'Url').value = ''); }, {once: true});
  return live;
}
