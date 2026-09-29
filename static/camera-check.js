const finiteCount = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 300;
const count = value => finiteCount(value) && Number.isInteger(value);

// This is presentation only. The controller owns sampling and seat availability.
export function cameraCheckPresentation(seats, online = true) {
  const empty = {recorded: 'Recorded passengers: —', progress: 'Camera check unavailable.',
    result: 'No completed camera check.', difference: '', status: 'unavailable'};
  if (!online) return {...empty, progress: 'Controller offline · camera check unavailable.'};
  const recorded = count(seats?.confirmed_total) ? `Recorded passengers: ${seats.confirmed_total}` : empty.recorded;
  const check = seats?.camera_check;
  if (!check || !['unavailable', 'collecting', 'incomplete', 'complete'].includes(check.status)) {
    return {...empty, recorded};
  }
  const status = check.status;
  const collected = count(check.collected) && check.collected <= 30 ? check.collected : null;
  const remaining = finiteCount(check.remaining_seconds) && check.remaining_seconds <= 30
    ? Math.ceil(check.remaining_seconds) : null;
  const samples = collected === null ? '— / 30 samples' : `${collected} / 30 samples`;
  const timing = remaining === null ? '' : ` · ${remaining}s remaining`;
  let progress;
  if (status === 'collecting') progress = `Camera check · ${samples}${timing} · one sample each second.`;
  else if (status === 'incomplete') progress = `Camera check incomplete · ${samples}. Missing observations are not counted as zero.`;
  else if (status === 'complete') progress = 'Camera check complete · 30 / 30 samples over 30 seconds.';
  else progress = 'Inside camera unavailable · no new check. Recorded passengers stay counted.';
  const hasResult = finiteCount(check.last_mean) && count(check.last_rounded);
  const result = hasResult
    ? `Last complete cycle: mean ${check.last_mean.toFixed(2)} → ${check.last_rounded} ${check.last_rounded === 1 ? 'person' : 'people'}.`
    : empty.result;
  const mismatch = check.mismatch;
  const difference = hasResult && Number.isInteger(mismatch) && Math.abs(mismatch) <= 300
    ? mismatch === 0 ? 'Last check matched the passenger records.'
      : `Last check: ${Math.abs(mismatch)} ${Math.abs(mismatch) === 1 ? 'person' : 'people'} ${mismatch > 0 ? 'more' : 'fewer'} than the passenger records.`
    : '';
  return {recorded, progress, result, difference, status};
}
