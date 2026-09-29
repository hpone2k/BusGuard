// Visual travel follows server state. This never opens doors, completes a stop,
// or sends a vehicle command; distances are presentation metres, not telemetry.
export const TRAVEL_TTL_MS = 2000;
export const CRUISE_SPEED = 5.2;
export const ACCELERATION_MS = 2600;
const clamp = value => Math.max(0, Math.min(1, value));
const ease = value => { const t = clamp(value); return t * t * (3 - 2 * t); };
const finite = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;

// A requestAnimationFrame timestamp describes the frame's start; a network
// response can arrive later in that same frame. Such a receipt is fresh, not an
// invalid negative-aged observation. Invalid clocks still fail closed.
export function observationAge(now, receivedAt) {
  return Number.isFinite(now) && Number.isFinite(receivedAt) ? Math.max(0, now - receivedAt) : Infinity;
}

export function shouldWakeTravel(policy, previousKey, idle, reducedMotion = false) {
  return policy.key !== previousKey || (!reducedMotion && idle
    && (policy.phase === 'moving' || (policy.phase === 'braking' && policy.remainingMs > 0)));
}

export function travelPresentation(snapshot, elapsedMs = 0, online = true) {
  const controller = snapshot?.assistance, scenario = controller?.scenario, vehicle = controller?.vehicle;
  const idle = {phase: 'stationary', key: 'stationary', elapsedMs: 0, remainingMs: 0};
  if (!online || !finite(elapsedMs) || elapsedMs >= TRAVEL_TTL_MS || controller?.simulation !== true
      || !scenario?.enabled || !vehicle || vehicle.emergency || vehicle.obstruction
      || vehicle.revalidation_required || vehicle.doors !== 'closed' || vehicle.ramp !== 'stowed') return idle;
  const motion = scenario.motion;
  if (scenario.phase === 'travelling' && vehicle.motion === 'moving') {
    return {phase: 'moving', key: `moving:${scenario.cycle_id}:${motion?.started_at_ms ?? ''}`,
      elapsedMs: finite(motion?.elapsed_ms) ? motion.elapsed_ms + elapsedMs : ACCELERATION_MS,
      remainingMs: 0};
  }
  if (scenario.phase === 'braking' && vehicle.motion === 'braking') {
    const duration = scenario.braking_duration_ms ?? motion?.duration_ms;
    const remaining = scenario.braking_remaining_ms ?? motion?.remaining_ms;
    if (!finite(duration) || duration <= 0 || !finite(remaining) || remaining > duration) return idle;
    return {phase: 'braking', key: `braking:${scenario.cycle_id}:${motion?.started_at_ms ?? ''}`,
      elapsedMs: 0, remainingMs: Math.max(0, remaining - elapsedMs)};
  }
  return idle;
}

export function createTravelMotion() {
  let phaseKey = '', speed = 0, distance = 0, brakeSpeed = 0, brakeRemaining = 0, lastBrakeRemaining = 0;
  return {
    step(policy, deltaSeconds, reducedMotion = false) {
      const previousSpeed = speed;
      if (policy.key !== phaseKey) {
        if (policy.phase === 'braking') {
          // A stop during acceleration decelerates from the speed already shown.
          // A viewer joining during braking starts from the remaining curve.
          brakeSpeed = phaseKey.startsWith('moving:') ? speed : CRUISE_SPEED * ease(policy.remainingMs / 2000);
          brakeRemaining = lastBrakeRemaining = policy.remainingMs;
        }
        phaseKey = policy.key;
      }
      if (reducedMotion || policy.phase === 'stationary') speed = 0;
      else if (policy.phase === 'moving') speed = CRUISE_SPEED * ease(policy.elapsedMs / ACCELERATION_MS);
      else {
        // Network jitter must not briefly accelerate the bus during a stop.
        lastBrakeRemaining = Math.min(lastBrakeRemaining, policy.remainingMs);
        speed = brakeRemaining > 0 ? brakeSpeed * ease(lastBrakeRemaining / brakeRemaining) : 0;
      }
      const seconds = Number.isFinite(deltaSeconds) ? Math.max(0, Math.min(.05, deltaSeconds)) : 0;
      // A lost connection freezes immediately, without one last coasting step.
      if (!reducedMotion && policy.phase !== 'stationary') distance += (previousSpeed + speed) * .5 * seconds;
      return {speed, distance, active: !reducedMotion && policy.phase !== 'stationary'
        && (policy.phase === 'moving' || policy.remainingMs > 0)};
    },
  };
}

export function roadOffset(distance, repeat = 6) {
  if (!Number.isFinite(distance) || !Number.isFinite(repeat) || repeat <= 0) return 0;
  const offset = (distance % repeat + repeat) % repeat;
  return offset === 0 ? 0 : -offset;
}
