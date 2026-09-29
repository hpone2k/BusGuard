// Shared with the operator and 3D viewer; served within the passenger site's asset mount.
// Presentation only: the server owns detection activity and departure deadlines.
export function departureConfirmationMs(controller) {
  const values = [controller?.readiness?.posture?.confirmation_ms, controller?.scenario?.posture_wait?.confirmation_ms];
  return values.find(value => Number.isFinite(value) && value > 0) ?? 5000;
}

export function departureNotice(scenario, elapsedMs = 0) {
  const empty = {visible: false, waiting: false, announced: false, timer: '—', detail: ''};
  if (!scenario?.enabled || !['boarding', 'stowing', 'closing', 'departure_wait'].includes(scenario.phase)
      || !Number.isFinite(elapsedMs) || elapsedMs < 0) return empty;
  const notice = scenario.departure_notice;
  if (!notice) return empty;
  const announced = notice.announced === true;
  const waiting = notice.waiting_for_detection === true && !announced;
  if (waiting) return {visible: true, waiting: true, announced: false, timer: 'Waiting',
    detail: 'The outside camera is still detecting activity. Boarding can stay open for up to 50 seconds; a departure warning follows before moving.'};
  if (!Number.isFinite(notice.remaining_ms) || notice.remaining_ms < 0) return empty;
  const ms = Math.max(0, notice.remaining_ms - elapsedMs);
  const timer = `${Math.ceil(ms / 1000)}s`;
  return {visible: true, waiting: false, announced, timer,
    detail: ms > 0
      ? `${announced ? 'Departure warning' : 'Planned departure'} · ${timer}. Please keep the doorway clear. Departure follows the closed-door standing check.`
      : 'Departure checks are in progress. Please remain seated until the bus confirms it is moving.'};
}
