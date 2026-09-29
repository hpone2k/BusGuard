import {departureNotice, departureConfirmationMs} from './passenger/departure-notice.js?v=standing-five-1';
import {postureTimerApplies} from './passenger/core.js?v=standing-five-1';

const PHASES = {
  disabled: 'Manual demonstration', braking: 'Slowing for the stop', opening: 'Doors opening', boarding: 'Boarding & alighting',
  stowing: 'Preparing to close', closing: 'Doors closing',
  departure_wait: 'Departure check', travelling: 'Bus travelling', held: 'Departure held',
};

function remaining(value) {
  return Number.isFinite(value) && value >= 0 ? `${Math.ceil(value / 1000)}s` : '—';
}

// This is presentation only. Timers, admission and departure stay on the server.
export function scenarioPresentation(state, online = true) {
  const scenario = state?.scenario;
  const enabled = Boolean(scenario?.enabled);
  const phase = scenario?.phase || 'disabled';
  const active = online && enabled;
  const confirmationSeconds = departureConfirmationMs(state) / 1000;
  const notice = online ? departureNotice(scenario) : departureNotice(null);
  const seats = scenario?.seats;
  const seat = type => {
    const value = seats?.[type];
    return value && [value.capacity, value.occupied, value.available].every(Number.isFinite)
      ? {capacity: value.capacity, occupied: value.occupied, available: value.available}
      : null;
  };
  const priority = seat('priority'), standard = seat('standard');
  let timer = '—', timerLabel = 'BOARDING WINDOW', detail = 'Start a repeatable stop. The shared controller handles departure automatically.';
  if (!online) detail = 'Controller offline. Timers and seat availability cannot be confirmed.';
  else if (enabled) {
    if (phase === 'braking') {
      timerLabel = 'ARRIVING'; timer = remaining(scenario.braking_remaining_ms);
      detail = 'The bus is slowing down. Doors stay closed. The inside camera keeps counting and monitoring posture while the bus slows down.';
    } else if (phase === 'opening') {
      timerLabel = 'DOORS OPENING'; timer = 'Please wait';
      detail = 'The quiet 15-second boarding window starts once the doors are fully open.';
    } else if (phase === 'boarding') {
      timer = remaining(scenario.boarding_remaining_ms);
      if (scenario.finishing_extensions === true) timerLabel = 'FINISHING ACCEPTED ASSISTANCE';
      detail = scenario.finishing_extensions === true
        ? 'New RFID taps and requests are closed. The last accepted allowance finishes before the doors close; standing checks follow.'
        : notice.waiting
        ? notice.detail
        : 'A quiet stop closes after 15 seconds. Only outside-camera activity and accepted passenger activity extend boarding. New taps and requests close at 50 seconds; accepted allowances can finish afterward.';
    } else if (phase === 'travelling') {
      timerLabel = 'NEXT STOP'; timer = 'On command';
      detail = 'Inside monitoring continues; outside boarding detection is paused. Press Stop to arrive at the next station on the A → B → C → D → E loop.';
    } else if (phase === 'departure_wait') {
      timerLabel = 'POST-CLOSE WAIT'; timer = remaining(scenario.departure_remaining_ms);
      detail = `Doors are closed. Departure requires ${confirmationSeconds} continuous seconds of fresh evidence with no standing detected. There is no automatic timeout.`;
    } else if (phase === 'held') {
      timerLabel = 'SAFETY HOLD'; timer = 'Waiting';
      detail = state?.readiness?.message || 'Waiting for the shared controller to confirm departure readiness.';
    } else {
      timerLabel = 'CLOSING SEQUENCE'; timer = 'Please wait';
      detail = `Access is closing. The ${confirmationSeconds}-second standing check begins after the doors have closed.`;
    }
  }
  const posture = scenario?.posture_wait;
  if (active && ['held', 'departure_wait'].includes(phase) && postureTimerApplies(state) && posture?.enabled) {
    if (posture.status === 'confirming') {
      timerLabel = 'CHECKING FOR STANDING'; timer = remaining(posture.remaining_ms);
      detail = `No standing is currently detected. Checking ${confirmationSeconds} continuous seconds of fresh observations before departure.`;
    } else if (posture.status === 'checking') {
      timerLabel = 'STANDING CHECK'; timer = 'Waiting';
      detail = state?.readiness?.posture?.message || `Departure waits for ${confirmationSeconds} continuous seconds of fresh observations with no standing detected. There is no automatic timeout.`;
    }
  }
  return {
    enabled, phase, active,
    label: online ? (scenario?.finishing_extensions === true && phase === 'boarding' ? 'Finishing accepted assistance' : PHASES[phase] || 'Controller updating') : 'Controller offline',
    tone: !online || phase === 'held' || scenario?.warning ? 'warning' : phase === 'travelling' ? 'moving' : 'ready',
    boarding: active && phase === 'boarding',
    travelling: active && phase === 'travelling',
    detection: !online ? 'Detection state unavailable' : scenario?.detection_enabled === false ? 'Inside active · outside paused' : 'Inside and outside active',
    timer, timerLabel, detail,
    departureTimer: notice.timer,
    departureLabel: notice.waiting ? 'DETECTION CONTINUING' : notice.announced ? 'DEPARTURE WARNING' : 'PLANNED DEPARTURE',
    departureVisible: notice.visible,
    hardRemaining: active && phase !== 'travelling' ? remaining(scenario.hard_remaining_ms) : '—',
    progress: active && phase !== 'travelling' && Number.isFinite(scenario.stop_elapsed_ms)
      ? Math.max(0, Math.min(100, scenario.stop_elapsed_ms / 500)) : 0,
    priority, standard,
    total: seats && Number.isFinite(seats.total) ? seats.total : null,
    capacity: seats && Number.isFinite(seats.capacity) ? seats.capacity : 26,
    full: Boolean(priority && standard && priority.available + standard.available <= 0),
    admission: online && scenario?.last_admission ? scenario.last_admission : null,
  };
}

export function controllerAnnouncement(state, online = true) {
  if (!online) return null;
  const event = state?.scenario?.enabled ? state.scenario.announcement : null;
  const message = state?.announcements?.[0] || event?.message;
  // Emergency and doorway guidance can override the most recent timed event.
  if (event?.id != null && event.message === message) return {id: `scenario:${event.id}`, message};
  return message ? {id: `manual:${message}`, message} : null;
}
