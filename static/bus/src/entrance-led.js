import {scenarioPresentation} from './controller-state.js';
import {postureTimerApplies} from '../../passenger/core.js';

// The physical entrance display only presents the shared controller's state.
// Never turn a boarding-window timer into a promise that the bus will move.
export function entranceLedPresentation(snapshot, elapsedMs = 0, online = true) {
  const controller = snapshot?.assistance;
  const view = scenarioPresentation(controller, elapsedMs, online);
  const count = value => Number.isInteger(value) && value >= 0 ? String(value).padStart(2, '0') : '--';
  const seatsLine = `PRIORITY ${count(view.priority)}   STANDARD ${count(view.standard)}`;
  if (!view.connected) return {seatsLine, statusLine: 'CONNECTION LOST', tone: 'muted'};
  const vehicle = controller.vehicle;
  const scenario = controller.scenario;
  if (vehicle.emergency || vehicle.revalidation_required || vehicle.obstruction) {
    return {seatsLine, statusLine: 'DEPARTURE HELD', tone: 'warning'};
  }
  if (!view.enabled) return {seatsLine, statusLine: 'BUSGUARD / READY', tone: 'muted'};
  if (view.moving) return {seatsLine, statusLine: controller.seating?.standing > 0 && vehicle.doors === 'closed' ? 'PLEASE REMAIN SEATED' : 'BUS IN SERVICE', tone: controller.seating?.standing > 0 ? 'warning' : 'ready'};
  const posture = scenario.posture_wait;
  // Confirmation advances only on fresh evidence. An unresolved posture has
  // no deadline; other holds retain precedence over this display.
  if (['held', 'departure_wait'].includes(view.phase) && posture?.enabled === true && postureTimerApplies(controller)) {
    if (posture.status === 'confirming' && Number.isFinite(posture.remaining_ms)) {
      const seconds = Math.ceil(Math.max(0, posture.remaining_ms) / 1000);
      return {seatsLine, statusLine: `STANDING CHECK ${seconds}s`, tone: 'warning'};
    }
    if (posture.status === 'checking') return {seatsLine, statusLine: 'PLEASE SIT / WAITING', tone: 'warning'};
  }
  if (view.phase === 'held') return {seatsLine, statusLine: 'DEPARTURE HELD', tone: 'warning'};
  if (view.phase === 'braking') return {seatsLine, statusLine: 'ARRIVING / PLEASE WAIT', tone: 'warning'};
  if (view.phase === 'opening') return {seatsLine, statusLine: 'DOORS OPENING', tone: 'warning'};
  if (scenario.finishing_extensions === true && view.phase === 'boarding') {
    const seconds = Math.ceil(Math.max(0, scenario.boarding_remaining_ms - elapsedMs) / 1000);
    return {seatsLine, statusLine: `CLOSE IN ${seconds}s / NO NEW TAPS`, tone: 'warning'};
  }

  const notice = scenario.departure_notice;
  const timed = ['boarding', 'stowing', 'closing', 'departure_wait'].includes(view.phase);
  if (timed && notice?.waiting_for_detection === true && notice.announced !== true) {
    return {seatsLine, statusLine: 'WAIT / BOARDING ACTIVITY', tone: 'warning'};
  }
  if (timed && Number.isFinite(notice?.remaining_ms) && notice.remaining_ms >= 0) {
    const seconds = Math.ceil(Math.max(0, notice.remaining_ms - elapsedMs) / 1000);
    const statusLine = seconds > 0 ? `${notice.announced === true ? 'DEPART' : 'PLANNED'} IN ${seconds}s` : 'DEPARTURE CHECK';
    return {seatsLine, statusLine, tone: notice.announced === true || seconds === 0 ? 'warning' : 'ready'};
  }
  const labels = {
    boarding: view.available === 0 ? 'BUS FULL / ALIGHT ONLY' : 'WELCOME / BOARDING',
    stowing: 'RAMP STOWING', closing: 'DOORS CLOSING', departure_wait: 'PLEASE SIT / CHECKING',
  };
  return {seatsLine, statusLine: labels[view.phase] || 'PLEASE WAIT', tone: 'warning'};
}
