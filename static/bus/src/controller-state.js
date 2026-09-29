// Presentation of the single server controller. This module never turns vision
// events into actuator commands or invents the next assistance phase.
export {scenarioPresentation} from '../../passenger/core.js';
export const CONTROLLER_TTL_MS = 2000;
const DOORS = {closed: 0, closing: 0, open: 1, opening: 1};
const RAMP = {stowed: 0, stowing: 0, deployed: 1, deploying: 1};
const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);

export function controllerPresentation(snapshot, elapsedMs = 0, online = true) {
  const controller = snapshot?.assistance;
  const vehicle = controller?.vehicle;
  const connected = online && Number.isFinite(elapsedMs) && elapsedMs >= 0
    && elapsedMs < CONTROLLER_TTL_MS && controller?.simulation === true && !!vehicle;
  const empty = {connected: false, doors: null, ramp: null, activeCount: null,
    status: 'Controller offline', detail: 'Simulated doors and ramp · state unavailable',
    message: 'Controller connection interrupted. The model is paused until current state returns.'};
  if (!connected) return empty;
  const known = own(DOORS, vehicle.doors) && own(RAMP, vehicle.ramp);
  const held = vehicle.emergency || vehicle.revalidation_required;
  const activeCount = Number.isInteger(controller.active_count) && controller.active_count >= 0
    ? controller.active_count : null;
  const requests = activeCount === null ? 'requests unknown'
    : `${activeCount} active ${activeCount === 1 ? 'request' : 'requests'}`;
  const announcements = Array.isArray(controller.announcements)
    ? controller.announcements.filter(message => typeof message === 'string' && message.trim()) : [];
  const fallback = typeof controller.readiness?.message === 'string'
    ? controller.readiness.message : 'Waiting for the next controller update.';
  const detail = known ? `Simulated access · Doors ${vehicle.doors} · Ramp ${vehicle.ramp} · ${requests}`
    : `Simulated access · Position unconfirmed · ${requests}`;
  return {connected: true, doors: known && !held ? DOORS[vehicle.doors] : null,
    ramp: known && !held ? RAMP[vehicle.ramp] : null, activeCount,
    status: held || !known || controller.readiness?.status === 'held' ? 'Simulation held' : controller.readiness?.status === 'ready'
      ? 'Simulation ready' : controller.readiness?.status === 'travelling' ? 'Simulated journey' : 'Assistance active',
    detail, message: announcements[0] || fallback};
}

// Use the host that served this viewer, not localhost on a passenger's phone.
export function serviceLink(pageUrl, apiPort, path = '/') {
  const url = new URL(pageUrl);
  if (apiPort !== undefined && apiPort !== null) {
    if (!Number.isInteger(apiPort) || apiPort < 1 || apiPort > 65535) throw new Error('Invalid service port.');
    url.port = String(apiPort);
  }
  url.pathname = path; url.search = ''; url.hash = '';
  return url.href;
}
