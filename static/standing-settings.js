import {postureAllowed} from './posture.js?v=standing-only-1';

export function standingConfidence(value = .10) {
  const score = typeof value === 'string' && value.trim() ? Number(value) : value;
  if (!Number.isFinite(score) || score < .05 || score > .95)
    throw new Error('Standing threshold must be between 0.05 and 0.95.');
  return Math.round(score * 100) / 100;
}

// This category has its own score; never reuse or change ordinary person confidence.
export function standingSettings({role, mode, prompt, checked, confidence} = {}) {
  return {
    posture_enabled: postureAllowed(role, mode, prompt) && Boolean(checked),
    standing_confidence: standingConfidence(confidence)
  };
}
