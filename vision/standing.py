"""Standing-only evidence, separate from the generic passenger census.

No sitting class is inferred. A healthy negative result means only that this
model did not detect standing; it never labels passengers as seated.
"""
from collections import Counter
import math

from .departure import track_key
from .schema import iou, Options
from .seating import unique_matches

LABELS = {'standing person', 'person standing', 'standing'}
LIMIT = 300


def _get(row, name, default=None):
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def _valid_box(box):
    return (isinstance(box, (tuple, list)) and len(box) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) and 0 <= v <= 1 for v in box)
            and box[2] > box[0] and box[3] > box[1])


def estimate(tracked, raw, standing_detections, captured_at_ms, available=True, unavailable_reason=None):
    valid = all(isinstance(rows, (list, tuple)) for rows in (tracked, raw, standing_detections))
    tracked, raw, classified = [rows if isinstance(rows, (list, tuple)) else []
                               for rows in (tracked, raw, standing_detections)]
    overflow = any(len(rows) > LIMIT for rows in (tracked, raw, classified))
    invalid = not valid

    def people(rows):
        nonlocal invalid
        found = []
        for row in rows[:LIMIT]:
            if str(_get(row, 'label', '')).strip().casefold() not in {'person', 'persons'}:
                continue
            box = _get(row, 'bbox')
            if not _valid_box(box):
                invalid = True
            found.append(dict(bbox=list(box) if _valid_box(box) else [],
                              track_id=_get(row, 'track_id'), predicted=bool(_get(row, 'predicted', False))))
        return found

    people_tracked, people_raw = people(tracked), people(raw)
    positives = []
    for row in classified[:LIMIT]:
        if str(_get(row, 'label', '')).strip().casefold() not in LABELS:
            invalid = True
            continue
        box = _get(row, 'bbox')
        score = _get(row, 'score')
        if (not _valid_box(box) or _get(row, 'predicted', False)
                or not isinstance(score, (int, float)) or isinstance(score, bool)
                or not math.isfinite(score) or not 0 <= score <= 1):
            invalid = True
            continue
        if any(iou(other['bbox'], box) >= .95 for other in positives):
            continue
        positives.append(dict(bbox=list(box), score=_get(row, 'score')))
    if not available:
        positives = []
    raw_matches = unique_matches(people_tracked, people_raw, .5)
    positive_matches = unique_matches(people_tracked, positives, .5)
    identities = Counter(track_key(row['track_id']) for row in people_tracked)
    complete = available and valid and not invalid and not overflow
    occupants = []
    for index, person in enumerate(people_tracked):
        identity = track_key(person['track_id'])
        accounted = (identity is not None and identities[identity] == 1
                     and not person['predicted'] and index in raw_matches
                     and not people_raw[raw_matches[index]]['predicted'])
        overlap = any(person['bbox'] and iou(person['bbox'], row['bbox']) >= .5 for row in positives)
        positive = index in positive_matches
        resolved = accounted and available and not invalid and (positive or not overlap)
        state = 'standing' if positive and available else 'not_detected' if resolved else 'unknown'
        complete = complete and accounted and resolved
        reason = ('YOLOE detected a standing person.' if state == 'standing' else
                  'No standing detected for this tracked person; sitting was not classified.' if state == 'not_detected' else
                  unavailable_reason or 'Waiting for fresh, uniquely matched person observations.')
        occupants.append(dict(track_id=person['track_id'], bbox=person['bbox'], posture=state, reason=reason))
    for index, person in enumerate(people_raw):
        if index not in set(raw_matches.values()):
            complete = False
            occupants.append(dict(track_id=None, bbox=person['bbox'], posture='unknown',
                                  reason='A person has not yet been uniquely tracked.'))
    # Even an unmatched positive holds departure and appears in the overlay.
    # This evidence never changes the independent ordinary-person count.
    for index, positive in enumerate(positives):
        if index not in set(positive_matches.values()):
            complete = False
            overlaps = [row for row in occupants if row['bbox'] and iou(row['bbox'], positive['bbox']) >= .5]
            if overlaps:
                # An untracked newcomer is still one person. For ambiguous
                # overlap, keep the existing people as potential positives;
                # do not manufacture an extra passenger for the same box.
                for row in overlaps:
                    row.update(posture='standing', reason='Standing evidence overlaps this person; unique association is unresolved.')
            else:
                occupants.append(dict(track_id=None, bbox=positive['bbox'], posture='standing',
                                      reason='Standing detected without a unique generic person track.'))
    if len(occupants) > LIMIT:
        overflow, complete = True, False
        occupants = occupants[:LIMIT]
    counts = Counter(row['posture'] for row in occupants)
    # A completed standing pass does not depend on persistent person IDs or
    # proving a seated roster. Keep those diagnostics separate from whether
    # this frame contains valid, negative standing evidence.
    standing_check_valid = bool(available and valid and not invalid and not overflow)
    clear = standing_check_valid and not positives
    reason = (unavailable_reason or 'Standing detection is unavailable.' if not available or not valid else
              'Standing person detected.' if counts['standing'] else
              'No standing detected. Sitting is not classified.' if clear else
              'A valid fresh standing detection result is required.')
    return dict(mode='standing_only', engine='YOLOE', method='YOLOE standing-person text prompt',
                status='observed' if available and valid else 'unavailable',
                people=len(occupants), standing=counts['standing'], seated=None,
                standing_check_valid=standing_check_valid,
                not_detected=counts['not_detected'], unknown=counts['unknown'], complete=bool(complete), clear=clear,
                occupants=occupants, captured_at_ms=captured_at_ms, age_ms=0, max_age_ms=1000,
                raw_person_count=len(people_raw), standing_detection_count=len(positives),
                capacity_reached=overflow, scores_available=True, reason=reason)


class YoloStanding:
    """Reuse the object model, serialized on its existing GPU owner thread."""
    def __init__(self, detector):
        self.detector = detector
        self.options = Options(prompt='standing person', mode='objects', size=640, confidence=.10)
        self.info = dict(available=True, engine='YOLOE', model=detector.info_model,
                         mode='standing_only', method='YOLOE standing-person text prompt',
                         asynchronous=False, scores=True, freshness_ms=1000, threshold=.10,
                         threshold_configurable=True,
                         error=None, limitations='Pretrained text prompting; not a bus-specific action classifier.')

    def warmup(self, sample):
        try:
            self.detector.detect(sample, self.options)
        except Exception:
            self.info.update(available=False, error='YOLOE standing detection could not initialize.')

    def estimate(self, frame, tracked, raw, captured_at_ms, **kwargs):
        if not self.info['available']:
            return estimate(tracked, raw, [], captured_at_ms, False, self.info['error'])
        try:
            options = Options(prompt='standing person', mode='objects', size=640,
                              confidence=kwargs.get('confidence', self.options.confidence))
            standing = self.detector.detect(frame, options)
            summary = estimate(tracked, raw, standing, captured_at_ms)
            summary['threshold'] = options.confidence
            return summary
        except Exception:
            return estimate(tracked, raw, [], captured_at_ms, False,
                            'YOLOE standing inference failed for this frame; waiting for a fresh result.')

    def invalidate(self, session_id):
        pass  # Same-frame synchronous results have no queued or cached posture.

    def close(self):
        pass  # The detector owns the shared model; no subprocess is started.
