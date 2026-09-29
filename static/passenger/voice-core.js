// The requested wake phrase and complete name are required. These bounded ASR
// variants accommodate accents without matching incidental mentions of a bus.
export function wakeInvocation(text) {
  if (typeof text !== 'string') return null;
  const match = text.normalize('NFKC').match(/^\s*["“']?hey[\s,!.:\-]+(?:bus[\s\-]*guard|buzz[\s\-]*guard|bus[\s\-]+god)\b[\s,!.:;?"”'\-]*(.*)$/iu);
  return match ? {command: match[1].trim()} : null;
}
export function wakePhrase(text) { return wakeInvocation(text) !== null; }

export function standbyPhrase(text) {
  if (typeof text !== 'string') return false;
  return /^(?:(?:thanks|thank you)[,!.\s]+)?(?:bus[\s-]*guard[,!.\s]+)?(?:please\s+)?(?:stand\s*by|go (?:back )?to (?:sleep|stand\s*by)|stop (?:listening|talking))(?:[,!.\s]+(?:please|thank you|thanks))?[.!?\s]*$/iu.test(text.normalize('NFKC').trim());
}

export const VOICE_IDLE_MS = 3000;

// Browser acoustic echo cancellation does the main work. Only suppress long,
// exact fragments of the overlapping reply; short answers and different wording
// must remain usable for passenger interruptions.
export function replyEcho(text, reply) {
  const normalize = value => String(value || '').normalize('NFKC').toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const heard = normalize(text), spoken = normalize(reply);
  return heard.length >= 32 && heard.split(' ').length >= 6
    && (` ${spoken} `).includes(` ${heard} `);
}

// User silence and a tool's authorization lifetime are separate clocks. A VAD
// turn admitted before the idle deadline keeps that authorization while speech
// and its bounded transcription finish; background turns never acquire it.
export class VoiceWakeGate {
  constructor(now = () => Date.now()) {
    this.now = now; this.awake = false; this.until = 0; this.epoch = 0; this.sequence = 0; this.speech = new Map();
  }
  pendingSpeech() {
    const now = this.now();
    return [...this.speech.values()].some(turn => turn.epoch === this.epoch && turn.eligible
      && (turn.ended === null ? now - turn.started < 60000 : now - turn.ended < 8000));
  }
  expire(busy = false) {
    if (!this.awake || this.now() < this.until || busy || this.pendingSpeech()) return false;
    this.standby(); return true;
  }
  standby() { this.awake = false; this.until = 0; this.epoch++; }
  reset() { this.standby(); this.speech.clear(); }
  touch() { if (this.awake) this.until = this.now() + VOICE_IDLE_MS; }
  wake(itemId) {
    const invoking = this.speech.get(itemId);
    this.awake = true; this.touch();
    // A wake-only transcription can arrive after the following request's VAD
    // start. Admit that request only if it followed this wake in the same epoch.
    if (invoking) for (const turn of this.speech.values()) {
      if (turn.epoch === this.epoch && turn.sequence >= invoking.sequence && turn.started >= invoking.started
          && turn.started <= (invoking.ended ?? invoking.started) + VOICE_IDLE_MS) turn.eligible = true;
    }
  }
  started(itemId, busy = false) {
    const expired = this.expire(busy);
    if (itemId) {
      this.speech.set(itemId, {epoch: this.epoch, sequence: ++this.sequence, eligible: this.awake, started: this.now(), ended: null});
      if (this.speech.size > 100) this.speech.delete(this.speech.keys().next().value);
    }
    return expired;
  }
  stopped(itemId) {
    const turn = this.speech.get(itemId);
    if (!turn || turn.epoch !== this.epoch) return;
    turn.ended = this.now();
    if (turn.eligible) this.touch();
  }
  discard(itemId) { this.speech.delete(itemId); }
  accept(itemId, invocation, busy = false) {
    this.expire(busy);
    const turn = this.speech.get(itemId);
    // An explicit standby invalidates even a delayed wake transcript from a
    // previously started utterance. A new wake requires newly spoken audio.
    const current = !turn || turn.epoch === this.epoch;
    const timely = !turn || (turn.ended === null ? this.now() - turn.started < 60000 : this.now() - turn.ended < 8000);
    const accepted = current && timely && (Boolean(invocation) || this.awake && (!turn || turn.eligible));
    if (accepted) {
      if (invocation) this.wake(itemId);
      this.touch();
    }
    this.discard(itemId);
    return accepted;
  }
}

const NEEDS = new Set(['ramp', 'extra_time', 'audio', 'visual', 'priority_seat']);
const TOOLS = new Set(['get_bus_status', 'request_assistance', 'complete_request', 'cancel_request', 'set_arrival_reminder', 'cancel_arrival_reminder', 'set_app_preference', 'open_app_panel']);
export const VOICE_PREFERENCES = ['audio', 'arrivalAlerts', 'arrivalVibration', 'largeText', 'highContrast', 'reduceMotion'];
const scalars = (value, keys) => value && typeof value === 'object' ? Object.fromEntries(
  keys.filter(key => value[key] === null || ['string', 'number', 'boolean'].includes(typeof value[key]))
    .map(key => [key, value[key]])) : null;
const requestTimer = value => scalars(value, ['enabled', 'state', 'maximum_ms', 'allowance_ms', 'remaining_ms']);

export function parseVoiceTool(name, raw) {
  if (!TOOLS.has(name)) throw new Error('That command is not available to the passenger assistant.');
  if (typeof raw !== 'string' || raw.length > 4096) throw new Error('The voice command was incomplete. Please try again.');
  const args = JSON.parse(raw);
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Invalid voice command.');
  if (name === 'set_app_preference') {
    if (Object.keys(args).length !== 2 || !VOICE_PREFERENCES.includes(args.preference) || typeof args.enabled !== 'boolean')
      throw new Error('Choose a supported app setting and say on or off.');
    return {preference: args.preference, enabled: args.enabled};
  }
  if (name === 'open_app_panel') {
    if (Object.keys(args).length !== 1 || !['settings', 'commands', 'route'].includes(args.panel))
      throw new Error('Choose settings, commands or route.');
    return {panel: args.panel};
  }
  if (name === 'set_arrival_reminder') {
    if (Object.keys(args).length !== 1 || typeof args.stop_id !== 'string' || !/^[a-z_]{1,32}$/.test(args.stop_id))
      throw new Error('Please choose a valid stop for your arrival reminder.');
    return {stop_id: args.stop_id};
  }
  if (name !== 'request_assistance') {
    if (Object.keys(args).length) throw new Error('Unexpected voice command fields.');
    return {};
  }
  if (Object.keys(args).some(key => !['journey', 'stop_id', 'needs'].includes(key))
      || !['boarding', 'alighting'].includes(args.journey)
      || typeof args.stop_id !== 'string' || !/^[a-z_]{1,32}$/.test(args.stop_id)
      || !Array.isArray(args.needs) || args.needs.length !== 1
      || args.needs.some(need => !NEEDS.has(need))) throw new Error('Please choose one assistance option and a valid bus stop. Ramp access already includes extra time.');
  return {...args, needs: [...new Set(args.needs)]};
}

// Private tokens/handles, camera observations, and personal request histories are never model context.
export function publicVoiceContext({config, state, location, request, isBusy, reminder, preferences, capabilities} = {}) {
  const stops = Array.isArray(config?.stops) ? config.stops.map(({id, name}) => ({id, name})) : [];
  const seats = state?.scenario?.seats;
  return {
    simulated: true, stops,
    location: location?.mode === 'at_stop' ? {mode: 'at_stop', stop_id: location.stop_id}
      : {mode: ['onboard', 'away'].includes(location?.mode) ? location.mode : 'away'},
    bus: state ? {motion: state.vehicle?.motion, doors: state.vehicle?.doors,
      current_stop_id: state.route?.current_stop_id || state.vehicle?.stop_id,
      next_stop_id: state.route?.next_stop_id, phase: state.scenario?.phase,
      admission_open: state.scenario?.admission_open === true,
      boarding_remaining_ms: state.scenario?.boarding_remaining_ms,
      admission_remaining_ms: state.scenario?.admission_remaining_ms,
      finishing_extensions: state.scenario?.finishing_extensions === true,
      departure_notice: scalars(state.scenario?.departure_notice, ['remaining_ms', 'announced', 'minimum_remaining_ms', 'waiting_for_detection']),
      posture_wait: scalars(state.scenario?.posture_wait, ['enabled', 'remaining_ms', 'expired', 'bypassed', 'status']),
      seats: seats ? {priority_available: seats.priority?.available, standard_available: seats.standard?.available,
        total_aboard: seats.total, estimated: seats.estimated} : null,
      announcement: state.announcements?.[0] || ''} : null,
    own_request: request ? {status: request.status, journey: request.journey,
      stop_id: request.stop_id, message: request.message, can_complete: request.can_complete,
      assistance_timer: requestTimer(request.assistance_timer)} : null,
    reminder: reminder ? scalars(reminder, ['enabled', 'stop_id', 'stop_name']) : null,
    preferences: scalars(preferences, VOICE_PREFERENCES),
    capabilities: scalars(capabilities, ['vibration']),
    busy: Boolean(isBusy),
  };
}

export function voiceToolResult(result) {
  if (!result || typeof result !== 'object' || !['accepted', 'queued', 'assisting', 'awaiting_completion', 'completed', 'cancelled', 'error'].includes(result.status)) {
    return {ok: false, message: 'No server confirmation was received.'};
  }
  return {ok: result.status !== 'error', status: result.status, message: result.message,
    journey: result.journey, stop_id: result.stop_id, can_complete: result.can_complete,
    assistance_timer: requestTimer(result.assistance_timer)};
}
