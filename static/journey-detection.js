// Source scheduling follows the shared journey. The cabin stays observed even
// while the outside boarding detector waits for the next stop.
export function detectionPausedFor(role, controller, online = true, elapsedMs = 0) {
  if (role === 'inside') return false;
  return !online || !Number.isFinite(elapsedMs) || elapsedMs < 0 || elapsedMs >= 1500
    || controller?.vehicle?.motion !== 'stationary' || controller?.vehicle?.doors !== 'open';
}

export function postureVisible(controller, online = true, elapsedMs = 0) {
  return Boolean(online && Number.isFinite(elapsedMs) && elapsedMs >= 0 && elapsedMs < 1500
    && controller?.vehicle?.doors === 'closed');
}
