import {postureTtlMs} from './posture.js?v=standing-only-1';
import {standingConfidence} from './standing-settings.js?v=standing-threshold-1';
import {departureConfirmationMs} from './passenger/departure-notice.js?v=standing-five-1';

const counter = value => Number.isInteger(value) && value >= 0 && value <= 300;
const text = (value, fallback) => typeof value === 'string' && value.trim() ? value.trim() : fallback;

// Explain missing evidence rather than displaying disabled detection as zero.
export function posturePresentation({controller, online = true, elapsedMs = 0, source,
  summary = source?.seating_summary, model, enabled = false, requested = enabled,
  confidence = .10, requestedConfidence = confidence,
  eligible = true, hasInput = false, kind = 'rtsp', pending = false} = {}) {
  const engine=text(model?.engine,text(summary?.engine,'YOLOE')).slice(0,60);
  const result = (status, message, detail = '') => ({status, message, detail,
    standing: null, clear: false, engine, observed: false});
  if (!online || !Number.isFinite(elapsedMs) || elapsedMs < 0 || elapsedMs >= 1500)
    return result('offline', 'Controller offline · posture state is unavailable.');
  const vehicle = controller?.vehicle;
  if (vehicle?.revalidation_required)
    return result('held', 'Restart hold · reset and revalidate the simulation.',
      `Door state is unconfirmed.${!source?.connected ? ' The inside camera also needs to be connected.' : ''} Use Reset held simulation in Manual & Safety Controls.`);
  if (!vehicle || !['open', 'opening', 'closing', 'closed'].includes(vehicle.doors))
    return result('held', 'Door state unconfirmed · posture checks are waiting.',
      'The controller must confirm closed doors before it can check for standing passengers.');
  if (!eligible) return result('disabled', 'Posture needs Object categories with person.',
    'Detailed phrases select only some passengers. Add person to the inside camera’s object categories.');
  if (hasInput && requested !== enabled)
    return result('draft', `Posture ${requested ? 'enabled' : 'disabled'} in settings · apply to activate.`,
      `The running source still has posture ${enabled ? 'on' : 'off'}. Apply posture to update it.`);
  if (hasInput && requested && standingConfidence(requestedConfidence) !== standingConfidence(confidence))
    return result('draft', 'Standing threshold changed · apply to activate.',
      `The running source still uses ${standingConfidence(confidence).toFixed(2)}. Apply setting to use ${standingConfidence(requestedConfidence).toFixed(2)}. Scores are not accuracy probabilities.`);
  if (!enabled && hasInput) return result('disabled', `${engine} standing detection is off for this source.`,
    'Select Standing detection, then apply the setting.');
  if (!hasInput) return result('disconnected', 'Inside camera not connected · no posture observations.',
    'Connect the inside CCTV or open a camera. The checkbox alone does not start a source.');
  if (pending) return result('waiting', 'Applying source settings · waiting for fresh frames.');
  if (model?.available === false)
    return result('unavailable', `${engine} standing detector unavailable.`,
      text(model.error, 'The server could not initialize the standing detector. No substitute detector is used.'));
  if (vehicle.doors !== 'closed')
    return result('paused', `Doors ${vehicle.doors} · standing checks are paused.`,
      'Person counting continues. Standing checks and reminders start only after the doors are fully closed.');
  const stillImage = kind === 'image';
  if (!stillImage && (!source?.connected || !Number.isFinite(source.age_ms) || source.age_ms >= 1000))
    return result(source?.session_id ? 'stale' : 'disconnected', source?.session_id
      ? 'Inside camera observations are stale · waiting for a fresh frame.'
      : 'Inside camera not connected · no posture observations.',
    'Departure needs fresh live cabin evidence. Old posture boxes are not displayed.');
  if (!summary || summary.status !== 'observed') {
    const status = ['disabled', 'unavailable', 'stale'].includes(summary?.status) ? summary.status : 'waiting';
    return result(status, text(summary?.reason, `Waiting for the next analysed frame with ${engine} standing results.`));
  }
  const ageMs = stillImage ? 0 : Number.isFinite(summary.age_ms) && summary.age_ms >= 0 ? summary.age_ms + elapsedMs : Infinity;
  if (!stillImage && ageMs >= postureTtlMs(summary))
    return result('stale', 'Posture observations are stale · waiting for a fresh analysed frame.');
  const occupants = Array.isArray(summary.occupants) ? summary.occupants : [];
  const details = [...new Set(occupants.filter(item => item?.posture === 'unknown').map(item => text(item.reason, 'Posture unclear.')))];
  const unknown = counter(summary.unknown) ? summary.unknown : null;
  const test = ['image', 'video'].includes(kind);
  const standing = counter(summary.standing) ? summary.standing : null;
  const standingOnly=summary.mode==='standing_only';
  const clear=standingOnly&&summary.standing_check_valid===true&&summary.clear===true&&standing===0;
  const message = `${test ? 'Test standing detection' : 'Live standing detection'} · ${standing ?? '—'} standing`;
  const accounting=standingOnly
    ? summary.standing_check_valid===true?'':'Standing detection is incomplete; departure is not cleared. '
    : unknown>0?'Some passengers are unresolved; the check is incomplete. '
      :!summary.complete?'The check is incomplete; departure is not cleared. ':'';
  const fallback = clear?`No standing detected. The controller requires ${departureConfirmationMs(controller) / 1000} continuous seconds of fresh observations after the doors close.`
    : 'Only standing detections are labelled. Other passengers are not classified as sitting.';
  return {status: 'observed', message,
    detail: `${accounting}${(!standingOnly && details.slice(0, 2).join(' ')) || fallback} Standing detections are not added to the person total.${test ? ' Test media cannot clear live departure.' : ''}`,
    standing, clear, engine, ageMs, observed: true};
}
