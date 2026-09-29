import {countPresentation} from './counts.js';
import {postureTtlMs} from './posture.js?v=standing-only-1';

const labels = ['standing person'];
const colour = {'standing person':'#d98c36'};

// A separate table section: these categories describe people already counted.
// Never merge its total or classes back into the object/person count summary.
export function postureCountRows(summary, {role = 'inside', status = 'waiting', mode = 'live', ageMs = 0} = {}) {
  if (role !== 'inside') return [];
  const ttlMs=postureTtlMs(summary);
  const effectiveAge=mode==='image'?0:Math.max(ageMs,summary?.age_ms ?? 0);
  const observed = status === 'observed' && summary?.status === 'observed'
    && Number.isFinite(effectiveAge) && effectiveAge>=0 && effectiveAge<ttlMs;
  const countSummary=summary?.count_summary ? {...summary.count_summary,stale_ms:ttlMs} : null;
  const view = observed ? countPresentation(countSummary, {mode, ageMs:effectiveAge, labels}) : null;
  const reasons = {paused:'Paused · doors open',disabled:'Disabled',unavailable:'Model unavailable',
    offline:'Controller offline',held:'Door state unconfirmed',disconnected:'No inside camera',stale:'Stale',draft:'Apply setting',waiting:'Waiting'};
  const currentStatus=status==='observed'&&!observed?'stale':status;
  return labels.map(label => {
    const entry = view?.classes?.[label];
    return {label,display:entry?.display ?? '—',raw:entry?.raw ?? null,
      supportText:entry?.supportText ?? reasons[currentStatus] ?? 'Waiting',colour:colour[label],posture:true};
  });
}
