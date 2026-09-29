import {NEEDS, STORAGE_KEY, makeIdentity, parseSaved, requestBody, requestPresentation, requestCountdown, connectionPresentation, scenarioPresentation, requestAvailability} from './core.js?v=standing-five-1';
import {LOCATION_KEY, ARRIVAL_KEY, parseLocation, routePresentation, ArrivalTracker} from './route.js?v=passenger-commands-4';
import {SpokenGuidance} from './spoken-guidance.js?v=voice-flow-1';
import {mountVoiceAssistant} from './voice.js?v=voice-finish-first-1';
import {REMINDER_KEY, parseReminder, createReminder, reminderArrival, vibrateArrival} from './arrival-reminder.js?v=voice-reliable-2';

const $ = id => document.getElementById(id);
let config = null;
let saved = null;
let request = null;
let busy = false;
let polling = false;
let pollTimer = null;
let lastSuccess = 0;
let failed = false;
let lastAnnouncement = '';
let audioUnlocked = false;
let lostRequest = false;
let mutationVersion = 0;
let journeyState = null, stateReceivedAt = 0, stateFailed = false;
let requestReceivedAt = 0;
let journeyAnnouncementKey = '';
let preferences = {largeText: false, highContrast: false, reduceMotion: false, audio: false, arrivalAlerts: false, arrivalVibration: false};
const speaker = new SpokenGuidance();
let demoLocation = {mode: 'away'}, locationRaw = null, alertsEnabled = false;
let routeSignature = '', notificationAllowed = false;
let lastArrival = '';
let reminder = null, reminderRaw = null;
try { reminderRaw = localStorage.getItem(REMINDER_KEY); } catch { /* Optional storage. */ }
try { locationRaw = localStorage.getItem(LOCATION_KEY); lastArrival = localStorage.getItem(ARRIVAL_KEY) || ''; } catch { /* Optional storage. */ }
const arrivals = new ArrivalTracker(lastArrival);
try { saved = parseSaved(localStorage.getItem(STORAGE_KEY)); } catch { /* Storage is optional; the current tab still works. */ }
try {
  const stored = JSON.parse(localStorage.getItem('bustech.passenger.preferences.v1'));
  for (const key of Object.keys(preferences)) preferences[key] = stored?.[key] === true;
} catch { /* Use accessible defaults if a preference is unavailable. */ }
alertsEnabled = preferences.arrivalAlerts;

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`); svg.setAttribute('aria-hidden', 'true'); svg.append(use); return svg;
}
for (const need of NEEDS) {
  const label = document.createElement('label'); label.className = 'need-choice';
  const input = document.createElement('input'); input.type = 'radio'; input.name = 'needs'; input.value = need.id;
  const copy = document.createElement('span'); copy.className = 'need-copy';
  const title = document.createElement('strong'); title.textContent = need.label;
  const description = document.createElement('small'); description.textContent = need.description;
  copy.append(title, description);
  const check = document.createElement('i'); check.className = 'need-check'; check.setAttribute('aria-hidden', 'true');
  label.append(input, icon(need.icon), copy, check); $('needChoices').append(label);
}

function notice(id, message = '') { $(id).textContent = message; $(id).hidden = !message; }
function persist() {
  try {
    if (saved) localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    notice(saved?.id ? 'statusNotice' : 'formNotice', 'This browser cannot save your request. Keep this page open until your assistance is complete.');
  }
}
function speak(message, userInitiated = false, options = {}) {
  if (!preferences.audio || !audioUnlocked || document.hidden || !message) return;
  speaker.speak(message, {expiresMs: 30000, ...options}).then(result => {
    if (result?.unavailable) $('speechStatus').textContent = 'Spoken guidance is unavailable. Your updates are still shown on screen.';
    else if (result?.played) $('speechStatus').textContent = '';
  }).catch(() => { $('speechStatus').textContent = 'Spoken guidance is unavailable. Your updates are still shown on screen.'; });
}
function announce(message) {
  if (!message || message === lastAnnouncement) return;
  lastAnnouncement = message; $('announcement').textContent = message; speak(message, false, {group: 'request-status'});
}
function applyPreferences() {
  for (const [key, className] of [['largeText', 'large-text'], ['highContrast', 'high-contrast'], ['reduceMotion', 'reduce-motion']]) {
    document.documentElement.classList.toggle(className, preferences[key]); $(key).checked = preferences[key];
  }
  $('audioSetting').checked = preferences.audio;
  $('arrivalVibration').checked = preferences.arrivalVibration;
  $('replayAudio').hidden = !preferences.audio || !request;
  try { localStorage.setItem('bustech.passenger.preferences.v1', JSON.stringify(preferences)); } catch { /* Optional preferences. */ }
}
function enableAudio(enabled) {
  preferences.audio = enabled; audioUnlocked = enabled;
  if (!enabled) speaker.cancel();
  applyPreferences();
  if (enabled) speak(request ? requestPresentation(request).message : 'Spoken updates are on. Choose the assistance you need, then select Request assistance.', true);
}
applyPreferences();
for (const key of ['largeText', 'highContrast', 'reduceMotion']) $(key).addEventListener('change', event => { preferences[key] = event.target.checked; applyPreferences(); });
$('audioSetting').addEventListener('change', event => enableAudio(event.target.checked));
$('arrivalVibration').addEventListener('change', event => { preferences.arrivalVibration = event.target.checked; applyPreferences(); alertCapability(); });
$('openSettings').addEventListener('click', () => $('settingsDialog').showModal());
$('closeSettings').addEventListener('click', () => $('settingsDialog').close());
$('doneSettings').addEventListener('click', () => $('settingsDialog').close());
$('replayAudio').addEventListener('click', () => { audioUnlocked = true; speak(requestPresentation(request).message, true); });

async function api(path, {method = 'GET', body, token} = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const headers = {'Accept': 'application/json'};
    if (body) headers['Content-Type'] = 'application/json';
    if (token) headers['X-Request-Token'] = token;
    const response = await fetch(`/api/assistance${path}`, {method, headers, body: body ? JSON.stringify(body) : undefined, signal: controller.signal, cache: 'no-store'});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : `The server could not process this request (${response.status}).`);
      error.status = response.status; throw error;
    }
    lastSuccess = Date.now(); failed = false; updateConnection();
    return data;
  } catch (error) {
    if (!error.status || error.status >= 500) { failed = true; updateConnection(); }
    throw error;
  } finally { clearTimeout(timeout); }
}
function updateConnection() {
  const presentation = connectionPresentation(lastSuccess, Date.now(), failed || stateFailed);
  $('connection').dataset.state = presentation.state;
  $('connection').querySelector('span').textContent = presentation.label;
  if (request) {
    const fresh = presentation.state === 'online' && currentJourney().connected;
    const timer = requestCountdown(request, Date.now() - requestReceivedAt, fresh);
    $('completeRequest').disabled = busy || !fresh || timer.visible && !timer.canComplete;
    $('cancelRequest').disabled = busy || !fresh;
    $('statusNote').querySelector('span:last-child').textContent = presentation.state === 'offline' ? 'Connection interrupted. Your last confirmed status is shown.' : 'Updates arrive automatically';
  }
  renderJourney();
  renderRequestCountdown();
}
function renderRequestCountdown() {
  const view = requestCountdown(request, Date.now() - requestReceivedAt, !failed && !stateFailed && currentJourney().connected);
  $('requestCountdown').hidden = !view.visible;
  $('requestCountdown').dataset.state = view.state;
  $('requestTimerLabel').textContent = view.label;
  $('requestTimerValue').textContent = view.value;
  $('requestTimerDetail').textContent = view.detail;
  $('requestTimerBar').style.transform = `scaleX(${view.ratio})`;
}
function currentJourney() {
  return scenarioPresentation(journeyState, Date.now() - stateReceivedAt, !failed && !stateFailed);
}
function currentAvailability() {
  return requestAvailability(currentJourney(), $('requestForm').querySelector('input[name="journey"]:checked')?.value,
    $('stop').value, Boolean(saved && !saved.id), demoLocation);
}
function renderJourney() {
  const view = currentJourney(), value = number => number === null ? '—' : String(number);
  $('liveJourney').dataset.phase = view.phase;
  $('liveJourney').dataset.warning = String(view.warning);
  $('liveJourneyTitle').textContent = view.title;
  $('liveJourneyDetail').textContent = view.detail;
  $('journeyTimer').textContent = view.timer;
  $('journeyTimerLabel').textContent = view.timerLabel;
  $('priorityAvailable').textContent = value(view.priority);
  $('standardAvailable').textContent = value(view.standard);
  $('routePriorityAvailable').textContent = value(view.priority);
  $('routeStandardAvailable').textContent = value(view.standard);
  $('seatsRemaining').textContent = view.available === null ? 'Seat availability unavailable' : `${view.available} of 26 seats available`;
  $('seatLedgerNote').textContent = view.connected && view.enabled
    ? `${view.seatBasis}${view.reserved ? ` · ${view.reserved} reserved` : ''} Priority seats may be used by anyone; please give way when needed.`
    : 'Seat availability appears when the scenario starts and the shared controller receives passenger evidence.';
  // Only authoritative announcements are spoken. Countdown ticks stay visual.
  const message = view.connected && view.enabled && Array.isArray(journeyState?.announcements)
    ? journeyState.announcements.find(item => typeof item === 'string' && item.trim()) : '';
  const announcementKey = journeyState?.scenario?.announcement?.id || message;
  if (message && announcementKey !== journeyAnnouncementKey) {
    journeyAnnouncementKey = announcementKey; $('journeyAnnouncement').textContent = message;
    speak(message, true, {group: 'bus-status', expiresMs: 10000});
  }
  if (!view.connected) $('journeyAnnouncement').textContent = '';
  renderRoute(view.connected);
  formAvailability();
}
function formAvailability() {
  const locked = busy || Boolean(saved);
  for (const control of $('requestForm').querySelectorAll('input[name="journey"],input[name="needs"],select')) control.disabled = locked || !config;
  const availability = currentAvailability();
  $('sendRequest').disabled = busy || !config || !availability.allowed;
  $('sendRequest').querySelector('span').textContent = busy ? 'Sending your request…' : saved && !saved.id ? 'Retry the same request' : 'Request assistance';
  $('boardingNotice').textContent = availability.reason;
  $('boardingNotice').hidden = !availability.reason;
}
function svgElement(name, attributes = {}, content = '') {
  const element = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (content) element.textContent = content;
  return element;
}
function renderRoute(fresh) {
  const view = routePresentation(config, journeyState, demoLocation, fresh);
  $('routeTitle').textContent = view.title; $('routeDetail').textContent = view.detail;
  $('routeMap').dataset.motion = view.motion;
  $('routeMap').dataset.wrapping = String(view.wrapping);
  $('locationSummary').textContent = view.locationLabel;
  const signature = JSON.stringify(view.stops);
  if (signature !== routeSignature) {
    routeSignature = signature;
    const points = view.stops.map(stop => `${stop.x},${stop.y}`).join(' ');
    $('routeLine').setAttribute('points', points);
    $('routeStations').replaceChildren(...view.stops.map(stop => {
      const group = svgElement('g', {'data-stop': stop.id, transform: `translate(${stop.x} ${stop.y})`});
      group.append(svgElement('circle', {r: 11}), svgElement('text', {'text-anchor': 'middle', dy: '.35em'}, stop.letter),
        svgElement('text', {class: 'station-name', y: 32, 'text-anchor': 'middle'}, `Stop ${stop.letter}`));
      return group;
    }));
    $('routeStopList').replaceChildren(...view.stops.map(stop => {
      const item = document.createElement('li'); item.dataset.stop = stop.id;
      const badge = document.createElement('b'); badge.textContent = stop.letter;
      const label = document.createElement('span'); label.textContent = stop.name;
      const status = document.createElement('small'); item.append(badge, label, status); return item;
    }));
  }
  for (const group of $('routeStations').children) {
    group.classList.toggle('is-current', view.known && group.dataset.stop === view.current?.id && view.phase === 'at_stop');
    group.classList.toggle('is-yours', demoLocation.mode === 'at_stop' && group.dataset.stop === demoLocation.stop_id);
  }
  for (const item of $('routeStopList').children) {
    const current = view.known && view.phase === 'at_stop' && item.dataset.stop === view.current?.id;
    const next = view.known && view.phase !== 'at_stop' && item.dataset.stop === view.next?.id;
    const yours = demoLocation.mode === 'at_stop' && item.dataset.stop === demoLocation.stop_id;
    item.dataset.current = String(current); item.querySelector('small').textContent = [current ? 'Bus here' : next ? 'Next' : '', yours ? 'You' : ''].filter(Boolean).join(' · ');
  }
  $('routeBus').setAttribute('visibility', view.marker ? 'visible' : 'hidden');
  if (view.marker) $('routeBus').setAttribute('transform', `translate(${view.marker.x} ${view.marker.y - 28})`);
  $('routeMap').setAttribute('aria-label', `Simulated five-stop bus route. ${view.title}. ${view.locationLabel}.`);
  const arrival = arrivals.observe(journeyState?.route, {enabled: true, location: demoLocation, selectedStop: $('stop').value, fresh});
  const reminded = reminderArrival(reminder, journeyState?.route, fresh);
  try { if (arrivals.lastId) localStorage.setItem(ARRIVAL_KEY, arrivals.lastId); } catch { /* In-memory dedup still works. */ }
  if (reminded) { reminder = null; persistReminder(); }
  const event = reminded || arrival;
  if (event) showArrival(view.stops.find(stop => stop.id === event.stop_id)?.name || 'your stop', false, Boolean(reminded));
}
function persistReminder() {
  reminderRaw = reminder ? JSON.stringify(reminder) : null;
  try {
    if (reminderRaw) localStorage.setItem(REMINDER_KEY, reminderRaw);
    else localStorage.removeItem(REMINDER_KEY);
  } catch { /* The reminder remains available in this tab. */ }
  renderReminder();
}
function reminderContext() {
  return reminder ? {enabled: true, stop_id: reminder.stop_id,
    stop_name: config?.stops?.find(stop => stop.id === reminder.stop_id)?.name || reminder.stop_id} : {enabled: false};
}
function renderReminder() {
  const view = reminderContext();
  $('reminderStatus').hidden = !view.enabled;
  $('reminderLabel').textContent = view.enabled ? `Arrival reminder · ${view.stop_name}` : '';
  $('reminderNote').textContent = view.enabled ? `We’ll alert you at ${view.stop_name}. Keep this page open and visible.` : 'Ask “Hey BusGuard, remind me when the bus reaches Stop A”, or choose a stop below.';
}
function setReminder({action, stop_id} = {}) {
  if (action === 'cancel') {
    reminder = null; persistReminder();
    return {status: 'cancelled', message: 'Your arrival reminder is cancelled.', reminder: {enabled: false}};
  }
  if (action !== 'set' || !config) throw new Error('Wait for the route to connect before setting a reminder.');
  const route = currentJourney().connected ? journeyState?.route : null;
  reminder = createReminder(stop_id, config.stops, route);
  const stopName = config.stops.find(stop => stop.id === stop_id).name;
  alertsEnabled = true; preferences.audio = true; audioUnlocked = true;
  applyPreferences(); updateAlertsButton(); persistReminder();
  if (route?.simulated === true && route.phase === 'at_stop' && route.current_stop_id === stop_id) {
    reminder = null; persistReminder(); showArrival(stopName, false, true);
    return {status: 'accepted', message: `The bus is already at ${stopName}. Check whether boarding is open before requesting assistance.`, reminder: {enabled: false}};
  }
  return {status: 'accepted', message: `I’ll remind you when the bus reaches ${stopName}. Keep this page open and visible. This reminder does not reserve a seat or request assistance.`, reminder: reminderContext()};
}
function updateAlertsButton() {
  preferences.arrivalAlerts = alertsEnabled;
  applyPreferences();
  $('enableArrivalAlerts').textContent = alertsEnabled ? 'Arrival alerts on' : 'Enable arrival alerts';
  $('enableArrivalAlerts').setAttribute('aria-pressed', String(alertsEnabled));
}
function saveLocation() {
  const value = $('demoLocation').value;
  demoLocation = parseLocation(value.startsWith('stop:') ? {mode: 'at_stop', stop_id: value.slice(5)} : {mode: value}, config?.stops);
  locationRaw = JSON.stringify(demoLocation);
  try { localStorage.setItem(LOCATION_KEY, locationRaw); } catch { /* This tab retains the selection. */ }
  if (!saved && demoLocation.mode === 'at_stop') $('stop').value = demoLocation.stop_id;
  renderRoute(currentJourney().connected); formAvailability();
}
function showArrival(stopName, test = false, requested = false) {
  const message = test ? 'This is a test arrival alert. Your bus location has not changed.'
    : `Your bus has arrived at ${stopName}. Please wait for the doors to open, then request the assistance you need.`;
  if ($('settingsDialog').open) $('settingsDialog').close();
  $('arrivalTitle').textContent = test ? 'Arrival alert preview' : `Your bus is here`;
  $('arrivalMessage').textContent = message; $('arrivalPopup').hidden = false;
  $('arrivalAnnouncement').textContent = message;
  const vibration = vibrateArrival({enabled: preferences.arrivalVibration, active: alertsEnabled || test || requested});
  if (vibration.attempted && !vibration.accepted) $('arrivalCapability').textContent = 'Your browser did not accept vibration. Text and spoken arrival alerts remain available.';
  if (alertsEnabled || test || requested) speak(message, true, {group: 'arrival', expiresMs: 10000});
  if (!test && notificationAllowed && 'Notification' in window && Notification.permission === 'granted') {
    try { new Notification('BusGuard · Your bus has arrived', {body: message, tag: 'busguard-arrival'}); }
    catch { $('arrivalCapability').textContent = 'This browser uses the on-page arrival alert. Keep this page open; system notifications are unavailable.'; }
  }
  // Keep arrival guidance visible until dismissed; there is no short timeout
  // that could hide it from someone who needs more time to read.
}
function alertCapability() {
  const vibration = typeof navigator.vibrate === 'function'
    ? 'Vibration is available when supported by this device’s settings.' : 'Vibration is unavailable on this browser, including iPhone Safari.';
  $('arrivalCapability').textContent = `${vibration} Keep this page open and the screen awake for live arrival alerts. Text and optional spoken guidance remain available.`;
  $('arrivalVibration').disabled = typeof navigator.vibrate !== 'function';
  $('enableNotifications').hidden = !window.isSecureContext || !('Notification' in window);
}
$('demoLocation').addEventListener('change', saveLocation);
$('cancelReminder').addEventListener('click', () => setReminder({action: 'cancel'}));
$('setReminder').addEventListener('click', () => {
  try { setReminder({action: 'set', stop_id: $('reminderStop').value}); }
  catch (error) { $('reminderNote').textContent = error.message; }
});
$('enableArrivalAlerts').addEventListener('click', () => {
  alertsEnabled = !alertsEnabled;
  updateAlertsButton();
  if (alertsEnabled) { audioUnlocked = true; preferences.audio = true; applyPreferences(); showArrival('', true); }
  alertCapability();
});
$('testArrivalAlerts').addEventListener('click', () => { audioUnlocked = true; showArrival('', true); });
$('dismissArrival').addEventListener('click', () => { $('arrivalPopup').hidden = true; });
$('enableNotifications').addEventListener('click', async () => {
  try {
    const permission = await Notification.requestPermission(); notificationAllowed = permission === 'granted';
    $('notificationStatus').textContent = notificationAllowed
      ? 'System notifications enabled where supported. Keep the app open for live updates.' : 'System notifications were not enabled. On-page alerts still work.';
  } catch { $('notificationStatus').textContent = 'This device uses on-page notifications. Keep the app open for alerts.'; }
});
alertCapability();
updateAlertsButton();

function setVoicePreference({preference, enabled}) {
  if (!Object.hasOwn(preferences, preference) || typeof enabled !== 'boolean') throw new Error('That setting is unavailable.');
  if (preference === 'arrivalVibration' && enabled && typeof navigator.vibrate !== 'function')
    throw new Error('Vibration is unavailable in this browser. Visual and spoken arrival alerts are available.');
  if (preference === 'audio') enableAudio(enabled);
  else {
    preferences[preference] = enabled;
    if (preference === 'arrivalAlerts') { alertsEnabled = enabled; updateAlertsButton(); }
    applyPreferences();
  }
  const names = {audio: 'Spoken updates', arrivalAlerts: 'Arrival alerts', arrivalVibration: 'Arrival vibration',
    largeText: 'Larger text', highContrast: 'High contrast', reduceMotion: 'Reduced motion'};
  return {status: 'accepted', message: `${names[preference]} ${enabled ? 'enabled' : 'disabled'} on this device.${preference === 'arrivalVibration' && enabled ? ' Your browser and device settings must also allow vibration.' : ''}`};
}
function openVoicePanel({panel}) {
  if (panel === 'settings') { if (!$('settingsDialog').open) $('settingsDialog').showModal(); }
  else {
    if ($('settingsDialog').open) $('settingsDialog').close();
    const target = panel === 'route' ? $('routeMap') : $('requestHeading');
    target?.scrollIntoView({behavior: preferences.reduceMotion ? 'instant' : 'smooth', block: 'center'});
    target?.setAttribute('tabindex', '-1'); target?.focus({preventScroll: true});
  }
  return {status: 'accepted', message: `${panel === 'commands' ? 'Assistance commands' : panel === 'route' ? 'Bus route' : 'Settings'} shown.`};
}
function restoreForm() {
  if (!saved) return;
  for (const input of $('requestForm').querySelectorAll('input[name="journey"]')) input.checked = input.value === saved.body.journey;
  for (const input of $('requestForm').querySelectorAll('input[name="needs"]')) input.checked = saved.body.needs.includes(input.value);
  if (config) $('stop').value = saved.body.stop_id;
  if (!saved.id) notice('formNotice', 'The previous request has not been confirmed. Retry to check it without creating a duplicate.');
  formAvailability();
}
function renderSummary(data) {
  const stopName = config?.stops?.find(stop => stop.id === data.stop_id)?.name || data.stop_id;
  $('journeySummary').textContent = `${data.journey === 'alighting' ? 'Leaving' : 'Boarding'} at ${stopName}`;
  $('requestNeeds').replaceChildren();
  for (const id of data.needs || []) {
    const label = document.createElement('span'); label.textContent = NEEDS.find(need => need.id === id)?.label || id; $('requestNeeds').append(label);
  }
}
function renderRequest(data, focus = false) {
  request = data; requestReceivedAt = Date.now(); const view = requestPresentation(data);
  $('requestForm').hidden = true; $('requestStatus').hidden = false; $('journey').dataset.status = data.status;
  $('requestReference').textContent = data.id ? `REF ${data.id.slice(-6).toUpperCase()}` : '';
  $('statusHeading').textContent = view.title; $('statusMessage').textContent = view.message;
  $('statusSymbol').querySelector('use').setAttribute('href', `#i-${data.status === 'completed' ? 'check' : 'bus'}`);
  renderSummary(data);
  for (const [index, item] of [...$('progress').children].entries()) {
    const done = data.status === 'completed' || index < view.step - 1;
    const current = !view.terminal && index === Math.max(0, view.step - 1);
    item.classList.toggle('done', done); item.classList.toggle('current', current);
    if (current) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
    item.querySelector('i').textContent = done ? '✓' : String(index + 1);
    if (index === 2) item.querySelector('span').textContent = data.journey === 'alighting' ? 'Leave the bus at your pace' : 'Board the bus at your pace';
  }
  $('completeRequest').hidden = !view.canComplete;
  $('completeRequest').querySelector('span').textContent = view.completeLabel;
  $('cancelRequest').hidden = !view.canCancel;
  $('newRequest').hidden = !view.terminal;
  $('newRequest').querySelector('span').textContent = data.status === 'completed' ? 'Request more assistance' : 'Make a new request';
  $('replayAudio').hidden = !preferences.audio;
  updateConnection(); announce(`${view.title}. ${view.message}`);
  if (focus) {
    if ($('settingsDialog').open) $('settingsDialog').close();
    $('statusHeading').focus();
  }
}
function showRestoring() {
  $('requestForm').hidden = true; $('requestStatus').hidden = false;
  renderSummary(saved.body);
  $('statusMessage').textContent = 'Reconnecting to your saved request. We’ll show its confirmed status shortly.';
  $('progress').hidden = true;
}
async function loadConfig() {
  const data = await api('/config');
  if (!Array.isArray(data.stops) || !data.stops.length) throw new Error('The bus has no stops configured. Please ask the operator to check the server.');
  config = data; $('serviceName').textContent = data.service || 'BusTech Demo';
  reminder = parseReminder(reminderRaw, data.stops);
  if (reminder) alertsEnabled = true;
  $('reminderStop').replaceChildren();
  for (const stop of data.stops) {
    const option = document.createElement('option'); option.value = stop.id; option.textContent = stop.name; $('reminderStop').append(option);
  }
  if (reminder) $('reminderStop').value = reminder.stop_id;
  $('setReminder').disabled = false;
  updateAlertsButton(); renderReminder();
  const selected = $('stop').value || saved?.body.stop_id;
  $('stop').replaceChildren();
  for (const stop of data.stops) {
    const option = document.createElement('option'); option.value = stop.id; option.textContent = stop.name; $('stop').append(option);
  }
  if (selected && data.stops.some(stop => stop.id === selected)) $('stop').value = selected;
  demoLocation = parseLocation(locationRaw, data.stops);
  $('demoLocation').replaceChildren();
  for (const [value, name] of [['away', 'Away from all stops'], ...data.stops.map(stop => [`stop:${stop.id}`, `At ${stop.name}`]), ['onboard', 'On the bus']]) {
    const option = document.createElement('option'); option.value = value; option.textContent = name; $('demoLocation').append(option);
  }
  $('demoLocation').value = demoLocation.mode === 'at_stop' ? `stop:${demoLocation.stop_id}` : demoLocation.mode;
  if (!selected && demoLocation.mode === 'at_stop') $('stop').value = demoLocation.stop_id;
  formAvailability();
  if (saved) restoreForm(); else notice('formNotice');
  if (request) renderSummary(request);
}
async function poll() {
  clearTimeout(pollTimer);
  if (document.hidden || polling) return;
  polling = true;
  try {
    if (!config) await loadConfig();
    try {
      journeyState = await api('/state'); stateReceivedAt = Date.now(); stateFailed = false; updateConnection();
    } catch (error) { stateFailed = true; updateConnection(); error.stateRead = true; throw error; }
    if (saved?.id && !busy && !lostRequest) {
      const id = saved.id, version = mutationVersion;
      const data = await api(`/requests/${encodeURIComponent(id)}`, {token: saved.body.request_token});
      // A response that began before a completion/cancellation cannot replace that action's result.
      if (saved?.id === id && mutationVersion === version && !busy) {
        $('progress').hidden = false; notice('statusNotice'); renderRequest(data);
      }
    }
  } catch (error) {
    if (!config) { failed = true; updateConnection(); }
    if (saved?.id && !error.stateRead && [403, 404].includes(error.status)) {
      lostRequest = true; request = null;
      notice('statusNotice', 'This saved request is no longer available on the bus server. It may have restarted. Please make a new request.');
      $('statusHeading').textContent = 'Request unavailable'; $('statusMessage').textContent = 'We cannot confirm the previous request’s status.';
      $('completeRequest').hidden = true; $('cancelRequest').hidden = true; $('newRequest').hidden = false; $('newRequest').querySelector('span').textContent = 'Make a new request';
    } else {
      notice(saved?.id ? 'statusNotice' : 'formNotice', error.status ? error.message : 'Cannot reach the bus server. Check that this device is on the same Wi-Fi. We’ll reconnect automatically.');
    }
  } finally {
    polling = false;
    if (!document.hidden) pollTimer = setTimeout(poll, failed ? 2500 : 1000);
  }
}
async function submitRequest(selection = null) {
  if (busy || !config) throw new Error('Please wait for the current action to finish and the bus connection to be ready.');
  if (saved?.id) throw new Error('You already have a request. Complete or cancel it before starting another.');
  const form = new FormData($('requestForm'));
  const choice = selection || {journey: form.get('journey'), stop_id: form.get('stop'), needs: form.getAll('needs')};
  if (selection && saved && (choice.journey !== saved.body.journey || choice.stop_id !== saved.body.stop_id
      || !Array.isArray(choice.needs) || [...new Set(choice.needs)].sort().join(',') !== [...saved.body.needs].sort().join(',')))
    throw new Error('A previous request is still unconfirmed. Retry that same request before changing the assistance preferences.');
  if (!saved && (!Array.isArray(choice.needs) || !config.stops.some(stop => stop.id === choice.stop_id))) throw new Error('Choose a configured bus stop and the assistance you need.');
  const available = requestAvailability(currentJourney(), choice.journey, choice.stop_id, Boolean(saved && !saved.id), demoLocation);
  if (!available.allowed) { notice('formNotice', available.reason); formAvailability(); throw new Error(available.reason); }
  notice('formNotice');
  try {
    if (!saved) {
      const body = requestBody(makeIdentity(), choice.journey, choice.stop_id, choice.needs, demoLocation);
      // Save identity before sending: retrying an uncertain network outcome must not create a second request.
      saved = {body, id: null}; persist();
    }
    busy = true; mutationVersion++; formAvailability();
    const data = await api('/requests', {method: 'POST', body: saved.body});
    if (typeof data.id !== 'string' || typeof data.status !== 'string') throw new Error('The server returned an incomplete confirmation. Retry the same request.');
    saved.id = data.id; persist(); lostRequest = false; $('progress').hidden = false;
    renderRequest(data, true);
    return data;
  } catch (error) {
    if (!saved) notice('formNotice', error.message);
    else if (error.status && error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status)) {
      saved = null; persist();
      notice('formNotice', error.message);
    } else notice('formNotice', error.status ? error.message : 'The server has not confirmed your request. Check your connection, then retry the same request.');
    throw error;
  } finally { busy = false; formAvailability(); updateConnection(); }
}
$('requestForm').addEventListener('submit', event => {
  event.preventDefault(); submitRequest().catch(() => { /* The form shows the actual validation or server error. */ });
});
$('requestForm').addEventListener('change', () => { formAvailability(); renderRoute(currentJourney().connected); });
async function action(name) {
  if (!saved?.id || busy) throw new Error('No request is ready for this action, or another action is still in progress.');
  const view = requestPresentation(request || {});
  if (name === 'complete' && (!view.canComplete || $('completeRequest').disabled)) throw new Error('Completion is not available yet. Wait for the bus to confirm your assistance is ready.');
  if (name === 'cancel' && (!view.canCancel || $('cancelRequest').disabled)) throw new Error('Cancellation is not available for this request.');
  busy = true; mutationVersion++; updateConnection(); notice('statusNotice');
  try {
    const data = await api(`/requests/${encodeURIComponent(saved.id)}/${name}`, {method: 'POST', token: saved.body.request_token});
    renderRequest(data);
    if (name === 'complete' && data.passenger_confirmed) {
      $('demoLocation').value = data.journey === 'boarding' ? 'onboard' : `stop:${data.stop_id}`;
      saveLocation();
    }
    return data;
  } catch (error) {
    notice('statusNotice', error.status ? error.message : 'The bus has not confirmed this action. Your last confirmed status is shown; reconnect and try again.');
    throw error;
  } finally { busy = false; updateConnection(); }
}
async function getFreshRequest() {
  if (!saved?.id) return null;
  const id = saved.id, version = mutationVersion;
  const data = await api(`/requests/${encodeURIComponent(id)}`, {token: saved.body.request_token});
  if (saved?.id !== id || mutationVersion !== version) throw new Error('Your request changed while checking its status. Please ask again.');
  return data;
}
$('completeRequest').addEventListener('click', () => action('complete').catch(() => {}));
$('cancelRequest').addEventListener('click', () => action('cancel').catch(() => {}));
$('newRequest').addEventListener('click', () => {
  if (busy) return;
  mutationVersion++; saved = null; request = null; lostRequest = false; lastAnnouncement = ''; persist();
  $('announcement').textContent = '';
  $('requestStatus').hidden = true; $('requestForm').hidden = false; delete $('journey').dataset.status;
  $('requestForm').reset(); applyPreferences(); notice('formNotice'); notice('statusNotice'); formAvailability();
  $('requestHeading').setAttribute('tabindex', '-1'); $('requestHeading').focus();
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { clearTimeout(pollTimer); speaker.cancel(); }
  else { updateConnection(); poll(); }
});
window.addEventListener('online', () => poll());
window.addEventListener('offline', () => { failed = true; updateConnection(); });
setInterval(() => { if (!document.hidden) updateConnection(); }, 250);
if (saved?.id) showRestoring();
const voiceAssistant = mountVoiceAssistant({host: $('voiceAssistant'), onStatusChange: ({message, state, needsAction, secure}) => {
  $('voiceStatusText').textContent = message;
  $('voiceStatusBar').dataset.state = state;
  $('startVoice').hidden = !needsAction || !secure;
  $('voiceSecureLink').hidden = secure;
  $('voiceSetupLink').hidden = secure;
}, onActivityChange: ({listening, connecting}) => {
  $('voiceIndicator').hidden = !listening && !connecting;
  $('voiceIndicatorLabel').textContent = connecting ? 'Connecting · Stop' : 'Voice on · Stop';
  $('voiceIndicator').setAttribute('aria-label', connecting ? 'Cancel voice connection' : 'Voice is enabled. Stop listening');
  if (listening) audioUnlocked = true;
}, getContext: () => ({config, state: currentJourney().connected ? journeyState : null, location: demoLocation, request, reminder: reminderContext(), preferences, capabilities: {vibration: typeof navigator.vibrate === 'function'}, isBusy: busy}),
  getFreshRequest, onRequest: submitRequest, onComplete: () => action('complete'), onCancel: () => action('cancel'), onReminder: setReminder,
  onPreference: setVoicePreference, onPanel: openVoicePanel});
$('voiceIndicator').addEventListener('click', () => voiceAssistant?.stop());
$('startVoice').addEventListener('click', () => { audioUnlocked = true; voiceAssistant?.start(); });
const secureVoiceUrl = new URL(window.location.href);
secureVoiceUrl.protocol = 'https:'; secureVoiceUrl.port = '4482'; secureVoiceUrl.pathname = '/'; secureVoiceUrl.search = ''; secureVoiceUrl.hash = '';
$('voiceSecureLink').href = secureVoiceUrl.href;
restoreForm(); poll();
