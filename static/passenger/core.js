import {departureNotice, departureConfirmationMs} from './departure-notice.js?v=standing-five-1';

export const NEEDS = [
  {id: 'ramp', label: 'Ramp access', description: 'Step-free access · 20 seconds', icon: 'ramp'},
  {id: 'extra_time', label: 'A little more time', description: 'Board or leave at your pace', icon: 'clock'},
  {id: 'audio', label: 'Audio guidance', description: 'Spoken journey instructions', icon: 'sound'},
  {id: 'visual', label: 'Visual guidance', description: 'Clear messages on screen', icon: 'eye'},
  {id: 'priority_seat', label: 'Priority seating', description: 'Request a suitable seat', icon: 'seat'},
];
export const TERMINAL = new Set(['completed', 'cancelled', 'error']);
export const ACTIVE_STATUSES = new Set(['accepted', 'queued', 'assisting', 'awaiting_completion', 'fault']);
export const STORAGE_KEY = 'bustech.passenger.request.v1';
export const SCENARIO_TTL_MS = 2000;

export function postureTimerApplies(controller) {
  const vehicle = controller?.vehicle, reasons = controller?.readiness?.reasons;
  return vehicle?.doors === 'closed' && vehicle?.ramp === 'stowed'
    && !vehicle.emergency && !vehicle.revalidation_required && !vehicle.obstruction
    && !(vehicle.wheelchair_aboard && !vehicle.wheelchair_secured)
    && !controller.mobility_area?.review_required && !controller.active_count
    && (!Array.isArray(reasons) || !reasons.some(reason => reason !== controller.readiness?.posture?.message
      && !['Wait for the boarding window, door closure and three-second departure interval.',
        'Wait for the boarding window, door closure and final departure checks.',
        'Wait for the ten-second departure announcement to finish.'].includes(reason)));
}

// Shared public display data. Camera counts never become seat assignments here.
export function scenarioPresentation(controller, elapsedMs = 0, online = true) {
  const fresh = online && Number.isFinite(elapsedMs) && elapsedMs >= 0 && elapsedMs < SCENARIO_TTL_MS
    && controller?.simulation === true && !!controller.vehicle;
  const empty = {connected: false, enabled: false, phase: 'unavailable', title: 'Checking the bus',
    detail: 'Waiting for current journey information.', timer: '—', timerLabel: 'Journey status',
    priority: null, standard: null, occupied: null, reserved: null, available: null,
    boardingOpen: false, moving: false, detectionEnabled: false, warning: false, stopId: null, stationary: false};
  if (!fresh) return {...empty, title: 'Journey information unavailable', detail: 'Reconnect to see current seats and boarding times.'};
  const scenario = controller.scenario;
  if (!scenario?.enabled) return {...empty, connected: true, phase: 'disabled', title: 'Ready for the next journey', detail: 'The operator will start the boarding scenario.', stopId: controller.vehicle.stop_id, stationary: controller.vehicle.motion === 'stationary'};
  const seats = scenario.seats;
  const group = (value, capacity) => value?.capacity === capacity
    && ['occupied', 'reserved', 'available'].every(key => Number.isInteger(value[key]) && value[key] >= 0)
    && value.occupied <= capacity && value.reserved <= capacity
    && value.available === Math.max(0, capacity - value.occupied - value.reserved) ? value : null;
  const priority = group(seats?.priority, 6), standard = group(seats?.standard, 20);
  const known = priority !== null && standard !== null;
  const remaining = value => Number.isFinite(value) && value >= 0 ? Math.max(0, value - elapsedMs) : null;
  const boardingMs = remaining(scenario.boarding_remaining_ms);
  const admissionMs = remaining(scenario.admission_remaining_ms ?? scenario.hard_remaining_ms);
  const departureMs = remaining(scenario.departure_remaining_ms);
  const seconds = value => value === null ? '—' : `${Math.ceil(value / 1000)}s`;
  const phase = scenario.phase;
  const notice = departureNotice(scenario, elapsedMs);
  const moving = phase === 'travelling' && controller.vehicle.motion === 'moving';
  const announcements = Array.isArray(controller.announcements) ? controller.announcements : [];
  const hold = announcements.find(message => typeof message === 'string' && message.trim())
    || controller.readiness?.message || 'Please stay seated while the bus checks that it is safe to depart.';
  const labels = {
    braking: ['Arriving at the stop', 'Please remain seated while the bus slows down. Your request time starts when the doors open.', 'Slowing', 'Journey status'],
    opening: ['The bus has stopped', 'Please wait for the doors to open. Boarding time has not started yet.', 'Opening', 'Doors'],
    boarding: [scenario.finishing_extensions ? 'Finishing accepted assistance' : notice.waiting ? 'Detection continuing' : 'Boarding at this stop',
      scenario.finishing_extensions ? 'New requests and RFID taps are closed. Already accepted assistance keeps its remaining time.'
        : notice.visible ? notice.detail : 'Please board or alight while the doors are open.',
      seconds(boardingMs), scenario.finishing_extensions ? 'Doors close in' : 'Boarding time left'],
    stowing: ['Preparing to depart', notice.visible ? notice.detail : 'Boarding has ended. Please keep the doorway clear while access is secured.', 'Closed', 'Boarding window'],
    closing: ['Doors are closing', notice.visible ? notice.detail : 'Please keep the doorway clear while the doors close.', 'Closed', 'Boarding window'],
    departure_wait: ['Ready to leave', notice.visible ? notice.detail : 'Doors are closed. Please remain seated for departure.', seconds(departureMs), 'Departure check'],
    travelling: ['On the way', 'Requests open when the bus arrives at your stop. Please remain seated.', 'Moving', 'Journey status'],
    held: ['Departure is on hold', hold, 'Held', 'Safety check'],
  };
  const posture = scenario.posture_wait;
  if (['held', 'departure_wait'].includes(phase) && postureTimerApplies(controller) && posture?.enabled) {
    if (posture.status === 'checking') {
      labels[phase] = ['Checking for standing passengers', 'Please take a seat. Departure needs fresh camera observations with no standing detected; there is no automatic timeout.', 'Waiting', 'Standing check'];
    } else if (posture.status === 'confirming') {
      // Remaining confirmation is based on new camera frames, not elapsed UI time.
      labels[phase] = ['No standing detected', `Please remain seated while the camera completes ${departureConfirmationMs(controller) / 1000} continuous seconds with no standing detected.`, seconds(posture.remaining_ms), 'Departure check'];
    }
  }
  if (moving && controller.seating?.standing > 0 && controller.vehicle.doors === 'closed') {
    labels.travelling[1] = 'Please take a seat and remain seated while the bus is moving. Thank you.';
  }
  const [title, detail, timer, timerLabel] = labels[phase] || ['Checking the bus', 'Waiting for a confirmed journey state.', '—', 'Journey status'];
  return {connected: true, enabled: true, phase, title, detail, timer, timerLabel,
    priority: known ? priority.available : null, standard: known ? standard.available : null,
    occupied: known ? priority.occupied + standard.occupied : null,
    reserved: known ? priority.reserved + standard.reserved : null,
    available: known ? priority.available + standard.available : null,
    boardingOpen: phase === 'boarding' && controller.vehicle.motion === 'stationary'
      && controller.vehicle.doors === 'open' && scenario.admission_open === true && boardingMs > 0 && admissionMs > 0,
    moving, detectionEnabled: scenario.inside_detection_enabled !== false, warning: Boolean(scenario.warning),
    estimated: seats?.estimated === true, countSource: seats?.count_source || null,
    seatBasis: typeof seats?.basis === 'string' ? seats.basis : 'Simulated seat assignments.',
    stopId: controller.vehicle.stop_id, stationary: controller.vehicle.motion === 'stationary'};
}

export function requestAvailability(view, journey, stopId, retry = false, location = {mode: 'away'}) {
  // A lost response may already have created the request. Always retain the
  // exact identity for a retry, even if the boarding window has since closed.
  if (retry) return {allowed: true, reason: ''};
  if (!view.connected) return {allowed: false, reason: 'Waiting for current bus information before sending a new request.'};
  if (!['boarding', 'alighting'].includes(journey) || !stopId) return {allowed: false, reason: 'Choose where you need assistance.'};
  if (journey === 'boarding' && (location.mode !== 'at_stop' || location.stop_id !== stopId))
    return {allowed: false, reason: 'For boarding, set your demo location to the same stop in Settings.'};
  if (journey === 'alighting' && location.mode !== 'onboard')
    return {allowed: false, reason: 'For alighting, set your demo location to “On the bus” in Settings.'};
  if (!view.stationary || stopId !== view.stopId) return {allowed: false, reason: 'Wait for the bus to arrive at your selected stop before requesting assistance.'};
  if (!view.enabled) return {allowed: true, reason: ''};
  if (!view.boardingOpen) return {allowed: false, reason: 'Requests are available only while the doors are open and the stop’s waiting window is active.'};
  if (journey === 'alighting') return {allowed: true, reason: ''};
  if (view.available === null) return {allowed: false, reason: 'Seat availability is being checked. Please wait for an update.'};
  if (view.available === 0) return {allowed: false, reason: 'All seats are occupied or reserved. Please choose a later stop for boarding assistance.'};
  return {allowed: true, reason: ''};
}

export function requestCountdown(request, elapsedMs = 0, online = true) {
  const clock = request?.assistance_timer;
  const hidden = {visible: false, state: 'inactive', label: '', value: '', detail: '', ratio: 0, canComplete: false};
  if (!clock?.enabled) return hidden;
  if (['completed', 'cancelled', 'error'].includes(clock.state)) return hidden;
  const fresh = online && Number.isFinite(elapsedMs) && elapsedMs >= 0 && elapsedMs < SCENARIO_TTL_MS;
  if (!fresh) return {...hidden, visible: true, state: 'offline', label: 'Your assistance time', value: '—',
    detail: 'Reconnecting to confirm your remaining time. Follow the bus announcements.'};
  if (clock.state === 'queued') return {...hidden, visible: true, state: 'queued', label: 'Your assistance time',
    value: 'Queued', detail: `${request?.needs?.includes('ramp') ? '20' : '10'} seconds when your assistance is accepted at your selected stop. Your confirmed countdown will appear here.`};
  if (clock.state === 'held') return {...hidden, visible: true, state: 'held', label: 'Assistance on hold', value: 'Held',
    detail: 'Please wait for the operator. Safety checks keep the bus stopped; they do not grant extra boarding time.'};
  if (clock.state === 'expired') return {...hidden, visible: true, state: 'expired', label: 'Your assistance time',
    value: 'Ended', detail: 'Your allowance has ended. Check the request message below for the next step.'};
  if (clock.state !== 'active' || !Number.isFinite(clock.remaining_ms) || clock.remaining_ms < 0
      || !Number.isFinite(clock.allowance_ms) || clock.allowance_ms <= 0 || clock.allowance_ms > 20000) return hidden;
  const remaining = Math.max(0, Math.min(clock.allowance_ms, clock.remaining_ms) - elapsedMs);
  return {visible: true, state: remaining > 0 ? 'active' : 'confirming', label: 'Your assistance time left',
    value: `${Math.ceil(remaining / 1000)}s`, ratio: remaining / clock.allowance_ms, canComplete: remaining > 0,
    detail: remaining > 0 ? 'Confirm once you have safely boarded or exited. This accepted time remains available even after new requests close.'
      : 'Your time has elapsed. Waiting for the bus to confirm the request status.'};
}

// getRandomValues also works on ordinary HTTP LAN pages, where randomUUID may not.
export function makeIdentity(cryptoObject = globalThis.crypto) {
  if (!cryptoObject?.getRandomValues) throw new Error('This browser cannot create a secure request. Please use an up-to-date browser.');
  const bytes = cryptoObject.getRandomValues(new Uint8Array(48));
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0'));
  bytes[38] = (bytes[38] & 0x0f) | 0x40;
  bytes[40] = (bytes[40] & 0x3f) | 0x80;
  const uuid = [...bytes.slice(32)].map(value => value.toString(16).padStart(2, '0')).join('');
  return {request_token: hex.slice(0, 32).join(''), client_request_id: `${uuid.slice(0, 8)}-${uuid.slice(8, 12)}-${uuid.slice(12, 16)}-${uuid.slice(16, 20)}-${uuid.slice(20)}`};
}

export function parseSaved(raw) {
  try {
    const saved = JSON.parse(raw);
    const body = saved?.body;
    if (!body || !/^[a-f0-9]{64}$/.test(body.request_token) || typeof body.client_request_id !== 'string' || body.client_request_id.length > 100) return null;
    if (!['boarding', 'alighting'].includes(body.journey) || typeof body.stop_id !== 'string' || !Array.isArray(body.needs) || !body.needs.length) return null;
    if (!body.needs.every(id => NEEDS.some(need => need.id === id))) return null;
    if (saved.id != null && (typeof saved.id !== 'string' || saved.id.length > 150)) return null;
    return {body, id: saved.id || null};
  } catch { return null; }
}

export function requestPresentation(request) {
  const exiting = request.journey === 'alighting';
  const labels = {
    accepted: ['Request received', 'Your assistance request has reached the bus.'],
    queued: ['You’re in the queue', 'Your request is saved. We’ll let you know when assistance is ready.'],
    assisting: ['Getting ready for you', 'The bus is preparing your requested assistance.'],
    awaiting_completion: ['Ready when you are', exiting ? 'Take your time leaving the bus. Let us know once you are off.' : 'Take your time boarding. Let us know once you are on board.'],
    completed: ['All taken care of', exiting ? 'Your alighting assistance is complete. Have a good day.' : 'Your boarding assistance is complete. Enjoy your journey.'],
    cancelled: ['Request cancelled', 'You can make another assistance request whenever you need.'],
    error: ['Assistance needs attention', 'This request could not be completed. Please contact the bus team or make a new request.'],
    fault: ['Assistance is on hold', 'The bus team needs to resolve an issue. Please wait for an update.'],
  };
  const [title, fallback] = labels[request.status] || ['Checking your request', 'Waiting for an update from the bus.'];
  const confirmed = Boolean(request.passenger_confirmed);
  return {
    title: confirmed && request.status === 'awaiting_completion' ? 'Thank you for confirming' : title,
    message: request.message || fallback,
    completeLabel: exiting ? 'I have exited the bus' : 'I have boarded the bus',
    canComplete: Boolean(request.can_complete) && !confirmed && request.status === 'awaiting_completion',
    canCancel: Boolean(request.can_cancel) && !TERMINAL.has(request.status),
    terminal: TERMINAL.has(request.status),
    step: Math.max(0, Math.min(4, Number(request.progress?.step) || 0)),
  };
}

export function connectionPresentation(lastSuccess, now, failed = false) {
  if (!lastSuccess) return {state: failed ? 'offline' : 'connecting', label: failed ? 'Bus server unavailable' : 'Connecting to the bus'};
  const seconds = Math.max(0, Math.floor((now - lastSuccess) / 1000));
  if (failed || seconds > 5) return {state: 'offline', label: `Connection interrupted · updated ${seconds}s ago`};
  return {state: 'online', label: 'Connected to the bus'};
}

export function requestBody(identity, journey, stopId, needs, location) {
  if (!Array.isArray(needs) || needs.length !== 1 || !NEEDS.some(need => need.id === needs[0]))
    throw new Error('Choose one assistance option for this request.');
  if (!['boarding', 'alighting'].includes(journey) || !stopId) throw new Error('Choose where you need assistance.');
  if (!location || !['at_stop', 'onboard', 'away'].includes(location.mode)) throw new Error('Choose your demo location in Settings.');
  const origin = location.mode === 'at_stop' ? {mode: 'at_stop', stop_id: location.stop_id} : {mode: location.mode};
  if (origin.mode === 'at_stop' && typeof origin.stop_id !== 'string') throw new Error('Choose your demo stop in Settings.');
  return {...identity, journey, stop_id: stopId, needs: [...needs], location: origin};
}
