"""Associate LocateAnything posture boxes with independently detected people.

This module does no inference and contains no joint geometry. Its caller supplies
same-frame semantic boxes only after the door-state gate permits posture work.
Ambiguous, untracked or conflicting evidence remains unknown and cannot clear
departure. Generic person counts continue to come from the object detector.
"""
import math
from collections import Counter

from .departure import track_key
from .schema import iou
from .seating import unique_matches


ENGINE = 'LocateAnything'
METHOD = 'LocateAnything semantic posture'
MAX_PEOPLE = 300
MATCH_IOU = .5
DEDUP_IOU = .95
POSTURE_LABELS = {
    'standing person': 'standing', 'person standing': 'standing', 'standing': 'standing',
    'sitting person': 'seated', 'person sitting': 'seated', 'sitting': 'seated',
    'seated person': 'seated', 'person seated': 'seated', 'seated': 'seated',
}


def _get(row, key, default=None):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def _box(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                   or not math.isfinite(v) or not 0 <= v <= 1 for v in value)
            or value[2] <= value[0] or value[3] <= value[1]):
        return None
    return list(value)


def _people(rows):
    result, invalid = [], False
    for row in rows[:MAX_PEOPLE]:
        if str(_get(row, 'label', '')).strip().casefold() not in {'person', 'persons'}:
            continue
        bbox = _box(_get(row, 'bbox'))
        if bbox is None:
            invalid = True
        result.append(dict(bbox=bbox or [], track_id=_get(row, 'track_id'),
                           predicted=bool(_get(row, 'predicted', False))))
    return result, invalid


def _classes(rows):
    result, invalid = [], False
    for row in rows[:MAX_PEOPLE]:
        label = ' '.join(str(_get(row, 'label', '')).strip().casefold().split())
        posture = POSTURE_LABELS.get(label)
        if posture is None:
            continue
        bbox = _box(_get(row, 'bbox'))
        if bbox is None or _get(row, 'predicted', False):
            invalid = True
            continue
        if any(prior['posture'] == posture and iou(prior['bbox'], bbox) >= DEDUP_IOU for prior in result):
            continue
        result.append(dict(bbox=bbox, posture=posture))
    return result, invalid


def _overlaps(person, classified):
    return bool(person['bbox']) and iou(person['bbox'], classified['bbox']) >= MATCH_IOU


def estimate(tracked, raw, classified_detections, captured_at_ms,
             available=True, unavailable_reason=None):
    """Return a seating summary from three independent, same-frame inputs.

    Duplicate same-class model boxes are collapsed. Conflicting standing/seated
    boxes are deliberately kept during association, making the person unknown.
    A class box never supplies a track ID or changes the generic person count.
    """
    valid_inputs = all(isinstance(rows, (list, tuple)) for rows in (tracked, raw, classified_detections))
    tracked, raw, classified_detections = [rows if isinstance(rows, (list, tuple)) else []
                                          for rows in (tracked, raw, classified_detections)]
    capacity_reached = any(len(rows) > MAX_PEOPLE for rows in (tracked, raw, classified_detections))
    people, invalid_tracks = _people(tracked)
    raw_people, invalid_raw = _people(raw)
    classes, invalid_classes = _classes(classified_detections)
    raw_matches = unique_matches(people, raw_people, MATCH_IOU)
    class_matches = unique_matches(people, classes, MATCH_IOU)
    identities = Counter(track_key(person['track_id']) for person in people)
    complete = bool(people) and available and valid_inputs and not (
        capacity_reached or invalid_tracks or invalid_raw or invalid_classes)
    occupants = []
    for index, person in enumerate(people):
        identity = track_key(person['track_id'])
        accounted = (identity is not None and identities[identity] == 1 and not person['predicted']
                     and index in raw_matches and not raw_people[raw_matches[index]]['predicted']
                     and index in class_matches and available)
        complete &= accounted
        posture = classes[class_matches[index]]['posture'] if accounted else 'unknown'
        overlaps = [row for row in classes if _overlaps(person, row)]
        if not available:
            reason = unavailable_reason or 'LocateAnything posture inference is unavailable.'
        elif person['predicted']:
            reason = 'Track is predicted; a fresh person detection is required.'
        elif len({row['posture'] for row in overlaps}) > 1:
            reason = 'LocateAnything returned conflicting standing and sitting labels for this person.'
        elif accounted:
            reason = 'A unique fresh person track matches the LocateAnything posture label.'
        else:
            reason = 'Waiting for a unique fresh person track, detection and posture label.'
        occupants.append(dict(track_id=person['track_id'], posture=posture,
                              bbox=person['bbox'], reason=reason))

    # Unconfirmed object detections remain passengers with unknown posture.
    # The separate semantic pass must not make them disappear from the roster.
    for index, person in enumerate(raw_people):
        if index not in set(raw_matches.values()):
            occupants.append(dict(track_id=None, posture='unknown', bbox=person['bbox'],
                                  reason='A detected person is not yet uniquely tracked.'))
            complete = False

    # Semantic-only detections are additional unknown evidence, never permission
    # to depart. Do not double-count a conflicting label attached to an already
    # represented person, or near-identical standing/sitting boxes for one extra.
    semantic_extras = []
    if available:
        for classified in classes:
            if any(_overlaps(person, classified) for person in people + raw_people):
                continue
            if any(iou(extra['bbox'], classified['bbox']) >= DEDUP_IOU for extra in semantic_extras):
                continue
            semantic_extras.append(classified)
            occupants.append(dict(track_id=None, posture='unknown', bbox=classified['bbox'],
                                  reason='LocateAnything sees a possible person without a matching object track.'))
            complete = False
    if len(occupants) > MAX_PEOPLE:
        occupants = occupants[:MAX_PEOPLE]
        capacity_reached, complete = True, False
    counts = Counter(row['posture'] for row in occupants)
    if not available or not valid_inputs:
        reason = unavailable_reason or 'LocateAnything posture inference is unavailable.'
    elif capacity_reached:
        reason = 'Posture accounting capacity reached; some passengers may be unresolved.'
    elif not occupants:
        reason = 'No passengers are visible; seating cannot be verified.'
    elif counts['standing']:
        reason = 'Standing posture observed by LocateAnything.'
    elif not complete or counts['unknown']:
        reason = 'Some passengers lack a unique, fresh and unambiguous LocateAnything posture label.'
    else:
        reason = 'LocateAnything labels every currently accounted passenger as seated.'
    return dict(status='observed' if available and valid_inputs else 'unavailable',
                engine=ENGINE, method=METHOD, people=len(occupants), seated=counts['seated'],
                standing=counts['standing'], unknown=counts['unknown'], complete=bool(complete),
                occupants=occupants, captured_at_ms=captured_at_ms, age_ms=0, reason=reason,
                raw_person_count=len(raw_people), capacity_reached=capacity_reached,
                semantic_detection_count=len(classes), scores_available=False)
