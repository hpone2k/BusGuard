"""Conservative, visible-person posture evidence for the virtual bus.

The pose network estimates joints; these geometry rules are not a trained
seated/standing classifier or evidence about people outside the camera view.
"""

import copy
import math
from collections import Counter
from pathlib import Path

import numpy as np

from .schema import iou
from .counting import age_summary


METHOD = 'pose geometry'
PERSON_LABELS = {'person', 'persons'}
MIN_KEYPOINT_CONFIDENCE = .55
MIN_LIMB_PIXELS = 10
SEATING_TTL_MS = 1000
SEMANTIC_SEATING_TTL_MS = 4000
POSTURE_LANDMARK_INDICES = (11, 12, 23, 24, 25, 26, 27, 28)


def asynchronous_posture(summary):
    """Only the server's independently identified semantic observations get 4s."""
    return (isinstance(summary, dict) and summary.get('engine') == 'LocateAnything'
            and isinstance(summary.get('posture_frame_id'), int)
            and not isinstance(summary.get('posture_frame_id'), bool)
            and summary['posture_frame_id'] >= 0
            and isinstance(summary.get('source_session_id'), str)
            and bool(summary['source_session_id']))


def posture_ttl_ms(summary):
    return SEMANTIC_SEATING_TTL_MS if asynchronous_posture(summary) else SEATING_TTL_MS


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _people(detections):
    return [{'bbox': _get(item, 'bbox', []), 'track_id': _get(item, 'track_id'),
             'predicted': bool(_get(item, 'predicted', False))}
            for item in detections[:300]
            if str(_get(item, 'label', '')).strip().casefold() in PERSON_LABELS]


def _valid_box(box):
    return (len(box) == 4 and all(isinstance(x, (float, int)) and math.isfinite(x) for x in box)
            and box[2] > box[0] and box[3] > box[1])


def unique_matches(left, right, threshold=.5):
    """Accept only unambiguous one-to-one box overlap, not a greedy assignment."""
    edges = {}
    reverse = {}
    for a, first in enumerate(left):
        if not _valid_box(first.get('bbox', [])):
            continue
        for b, second in enumerate(right):
            if _valid_box(second.get('bbox', [])) and iou(first['bbox'], second['bbox']) >= threshold:
                edges.setdefault(a, []).append(b)
                reverse.setdefault(b, []).append(a)
    return {a: candidates[0] for a, candidates in edges.items()
            if len(candidates) == 1 and len(reverse[candidates[0]]) == 1}


def _angle(first, pivot, last):
    a, b = first - pivot, last - pivot
    denominator = np.linalg.norm(a) * np.linalg.norm(b)
    if denominator <= 0:
        return None
    return math.degrees(math.acos(float(np.clip(np.dot(a, b) / denominator, -1, 1))))


def classify_posture(keypoints):
    """Classify only clearly visible, consistent bilateral limb geometry.

    Input is COCO's 17 keypoints in PIXELS with per-keypoint confidence. Using
    normalized coordinates would distort angles on a widescreen CCTV frame.
    """
    try:
        points = np.asarray(keypoints, dtype=float)
    except (TypeError, ValueError):
        return 'unknown', 'Pose keypoints are unavailable.'
    if points.shape != (17, 3):
        return 'unknown', 'Pose keypoints or confidence are unavailable.'
    sides = []
    for indices in ((5, 11, 13, 15), (6, 12, 14, 16)):
        limb = points[list(indices)]
        if (not np.isfinite(limb).all() or np.any(limb[:, 2] < MIN_KEYPOINT_CONFIDENCE)
                or np.any(limb[:, :2] < 0)):
            return 'unknown', 'Shoulders or legs are occluded or uncertain.'
        shoulder, hip, knee, ankle = limb[:, :2]
        torso, thigh, shin = hip - shoulder, knee - hip, ankle - knee
        lengths = [float(np.linalg.norm(vector)) for vector in (torso, thigh, shin)]
        if min(lengths) < MIN_LIMB_PIXELS:
            return 'unknown', 'Person is too small or foreshortened to verify posture.'
        torso_down, thigh_down, shin_down = [vector[1] / length
                                            for vector, length in zip((torso, thigh, shin), lengths)]
        knee_angle, hip_angle = _angle(hip, knee, ankle), _angle(shoulder, hip, knee)
        if torso_down < math.cos(math.radians(35)):
            sides.append('unknown')
        elif (thigh_down >= math.cos(math.radians(25)) and shin_down >= math.cos(math.radians(25))
              and knee_angle >= 160):
            sides.append('standing')
        elif (abs(thigh_down) <= math.sin(math.radians(25))
              and shin_down >= math.cos(math.radians(35))
              and 70 <= knee_angle <= 115 and 65 <= hip_angle <= 115):
            sides.append('seated')
        else:
            sides.append('unknown')
    if sides[0] == sides[1] and sides[0] != 'unknown':
        return sides[0], 'Both visible leg poses agree.'
    return 'unknown', 'Posture is ambiguous or the two leg poses disagree.'


def _landmark_diagnostics(pose):
    """Describe fresh MediaPipe joint visibility without authorizing posture.

    Association and bilateral geometry still determine the occupant's posture.
    These diagnostics also let the interface show an unassociated skeleton as
    unknown, instead of implying that the model found no joints at all.
    """
    landmarks = pose.get('landmarks')
    if not isinstance(landmarks, list) or len(landmarks) != 33:
        return None
    confidence = []
    for point in landmarks:
        if not isinstance(point, dict):
            return None
        values = [point.get(key) for key in ('x', 'y', 'visibility', 'presence')]
        if any(not isinstance(value, (int, float)) or isinstance(value, bool)
               or not math.isfinite(value) for value in values):
            return None
        x, y, visibility, presence = values
        if not 0 <= visibility <= 1 or not 0 <= presence <= 1:
            return None
        confidence.append(min(visibility, presence) if 0 <= x <= 1 and 0 <= y <= 1 else 0.)
    visible = sum(value >= MIN_KEYPOINT_CONFIDENCE for value in confidence)
    if visible < 4:
        return None
    posture_confidence = [confidence[index] for index in POSTURE_LANDMARK_INDICES]
    return dict(visible_joints=visible, total_joints=33,
                posture_joints_visible=sum(value >= MIN_KEYPOINT_CONFIDENCE for value in posture_confidence),
                posture_joints_total=len(POSTURE_LANDMARK_INDICES),
                minimum_posture_confidence=round(min(posture_confidence), 5))


def _pose_inside_person(person_box, pose):
    """A narrow skeleton can belong to a broader object-detection rectangle.

    Require a substantial, visible torso and near-complete skeleton containment.
    This only proposes an association; crowded/nested alternatives are rejected
    by the bidirectional uniqueness check, and posture thresholds stay separate.
    """
    if not _valid_box(pose.get('bbox', [])):
        return False
    x0, y0, x1, y1 = person_box
    if not all(0 <= value <= 1 for value in person_box):
        return False
    width, height = x1 - x0, y1 - y0
    px0, py0, px1, py1 = pose['bbox']
    pose_width, pose_height = px1 - px0, py1 - py0
    intersection = max(0., min(x1, px1) - max(x0, px0)) * max(0., min(y1, py1) - max(y0, py0))
    # Almost all pairs in a full cabin are disjoint. Reject their inexpensive
    # rectangle geometry before inspecting thirty-three individual landmarks.
    if (pose_width < .1 * width or pose_height < .45 * height
            or intersection / (pose_width * pose_height) < .85):
        return False
    if _landmark_diagnostics(pose) is None:
        return False
    landmarks = pose['landmarks']
    visible = lambda point: (0 <= point['x'] <= 1 and 0 <= point['y'] <= 1
                             and min(point['visibility'], point['presence']) >= MIN_KEYPOINT_CONFIDENCE)
    inside = lambda point: x0 <= point['x'] <= x1 and y0 <= point['y'] <= y1
    anchors = [landmarks[index] for index in (11, 12, 23, 24)]
    if not all(visible(point) and inside(point) for point in anchors):
        return False
    torso_width = max(point['x'] for point in anchors) - min(point['x'] for point in anchors)
    torso_height = max(point['y'] for point in anchors) - min(point['y'] for point in anchors)
    if torso_width < .05 * width or torso_height < .12 * height:
        return False
    visible_points = [point for point in landmarks if visible(point)]
    if sum(inside(point) for point in visible_points) / len(visible_points) < .9:
        return False
    return True


def unique_pose_matches(people, poses, threshold=.5):
    """Match fresh poses conservatively, including supported narrow skeletons.

    Existing box-IoU matching remains available to legacy COCO poses. MediaPipe
    adds a containment candidate only with visible, sufficiently sized anchors.
    All candidates participate in uniqueness: a stronger overlap never silently
    assigns a skeleton to one of two plausible neighbouring or nested people.
    """
    edges, reverse = {}, {}
    for index, person in enumerate(people):
        box = person.get('bbox', [])
        if not _valid_box(box):
            continue
        for pose_index, pose in enumerate(poses):
            if not _valid_box(pose.get('bbox', [])):
                continue
            if iou(box, pose['bbox']) >= threshold or _pose_inside_person(box, pose):
                edges.setdefault(index, []).append(pose_index)
                reverse.setdefault(pose_index, []).append(index)
    return {index: candidates[0] for index, candidates in edges.items()
            if len(candidates) == 1 and len(reverse[candidates[0]]) == 1}


def seating_evidence(tracked_detections, raw_detections, poses, captured_at_ms,
                     available=True, unavailable_reason=None):
    """Account for tracked, newly detected, and pose-only visible people."""
    tracked = _people(tracked_detections)
    raw = _people(raw_detections)
    poses = poses[:300]
    raw_matches = unique_matches(tracked, raw)
    pose_matches = unique_pose_matches(tracked, poses)
    ids = Counter(item['track_id'] for item in tracked if item['track_id'] is not None)
    occupants = []
    attached_poses = set()
    complete = bool(tracked) and available
    for index, person in enumerate(tracked):
        track_id = person['track_id']
        accounted = (track_id is not None and ids[track_id] == 1 and not person['predicted']
                     and index in raw_matches and index in pose_matches and available)
        complete &= accounted
        posture, reason = 'unknown', 'Waiting for a unique, fresh person track and pose.'
        if accounted:
            posture, reason = classify_posture(poses[pose_matches[index]].get('keypoints'))
        elif person['predicted']:
            reason = 'Track is predicted; fresh posture is required.'
        if not available:
            reason = unavailable_reason or 'Pose analysis is unavailable.'
        occupant = {'track_id': track_id, 'posture': posture, 'reason': reason}
        if available and not person['predicted'] and index in pose_matches:
            matched_pose = poses[pose_matches[index]]
            landmarks = matched_pose.get('landmarks')
            if landmarks:
                occupant['landmarks'] = landmarks
                attached_poses.add(pose_matches[index])
                quality = _landmark_diagnostics(matched_pose)
                if quality is not None:
                    occupant['pose_quality'] = quality
        occupants.append(occupant)

    # An incoming person must remain unknown during the tracker's confirmation
    # window. Pose-only people also cannot disappear from the accounting.
    unused_raw = [item for index, item in enumerate(raw) if index not in set(raw_matches.values())]
    unused_poses = [item for index, item in enumerate(poses) if index not in set(pose_matches.values())]
    extra_matches = unique_pose_matches(unused_raw, unused_poses)
    extras = len(unused_raw) + len(unused_poses) - len(extra_matches)
    if extras:
        complete = False
        occupants.extend({'track_id': None, 'posture': 'unknown',
                          'reason': 'A visible person is not yet uniquely tracked.'} for _ in range(extras))
    counts = Counter(item['posture'] for item in occupants)
    if not available:
        reason = unavailable_reason or 'Pose analysis is unavailable.'
    elif not occupants:
        reason = 'No passengers are visible; seating cannot be verified.'
    elif not complete:
        reason = 'Some visible people lack a unique, fresh track and matching pose.'
    elif counts['unknown']:
        reason = 'Some passengers have unclear or occluded posture.'
    elif counts['standing']:
        reason = 'Standing posture observed.'
    else:
        reason = 'Seated posture observed for every currently accounted visible person.'
    # Overlay-only information. Never add these a second time to occupants or
    # use them as seated/standing evidence: association is still unresolved.
    unassigned_poses = []
    if available:
        for index, pose in enumerate(poses):
            if index in attached_poses:
                continue
            quality = _landmark_diagnostics(pose)
            if quality is not None:
                unassigned_poses.append(dict(
                    landmarks=pose['landmarks'], posture='unknown', pose_quality=quality,
                    reason='Visible joints are not uniquely associated with a fresh passenger track.'))
    return {'status': 'observed' if available else 'unavailable', 'people': len(occupants),
            'seated': counts['seated'], 'standing': counts['standing'], 'unknown': counts['unknown'],
            'complete': bool(complete), 'occupants': occupants, 'age_ms': 0,
            'unassigned_poses': unassigned_poses,
            'captured_at_ms': captured_at_ms, 'method': METHOD, 'reason': reason}


def age_seating_summary(summary, age_ms, force_stale=False):
    if summary is None:
        return None
    result = copy.deepcopy(summary)
    ttl = posture_ttl_ms(summary)
    result['max_age_ms'] = ttl
    result['age_ms'] = round(max(result.get('age_ms') or 0, age_ms, 0), 3)
    if force_stale or result['age_ms'] >= ttl:
        result.update(status='stale', people=None, seated=None, standing=None, unknown=None,
                      complete=False, occupants=[], unassigned_poses=[],
                      reason='Fresh interior posture evidence is required.')
        if result.get('mode') == 'standing_only':
            result['clear'] = False
            result['standing_check_valid'] = False
    elif result.get('status') != 'observed':
        result['unassigned_poses'] = []
        for occupant in result.get('occupants', []):
            occupant.pop('landmarks', None)
            occupant.pop('pose_quality', None)
    counts = result.get('count_summary')
    if isinstance(counts, dict):
        counts = age_summary(counts, result['age_ms'], ttl)
        if result.get('status') != 'observed':
            # Instant still-image summaries do not normally expire on their
            # own; a stale/disabled containing posture summary still invalidates
            # their display values so a previous seated count cannot linger.
            counts['status'] = 'stale'
            counts['age_ms'] = result['age_ms']
            for row in [counts['total'], *counts['classes'].values()]:
                row.update(raw=None, stable=None, candidate=None, support=None,
                           status='stale', distribution=[], held_age_ms=None, held_remaining_ms=0)
        result['count_summary'] = counts
    return result


class PoseSeating:
    """Optional local pose model, owned by the existing single GPU worker."""
    def __init__(self, path, device):
        self.path, self.device, self.model = Path(path), device, None
        self.info = {'available': False, 'model': self.path.name, 'method': METHOD,
                     'error': 'Local pose weights are not installed.'}
        if not self.path.is_file():
            return
        try:
            from ultralytics import YOLO
            self.model = YOLO(str(self.path), task='pose')
            self.info.update(available=True, error=None)
        except Exception:
            self.info['error'] = 'Local pose model could not be loaded.'

    def _predict(self, frame):
        return self.model.predict(source=frame, imgsz=640, conf=.35, max_det=200,
                                  device=self.device, quantize=16 if self.device.startswith('cuda') else 32,
                                  rect=False, verbose=False, save=False)[0]

    def warmup(self, frame):
        if self.info['available']:
            try:
                self._predict(frame)
            except Exception:
                self.info.update(available=False, error='Pose model warm-up failed; object detection remains available.')

    def estimate(self, frame, tracked, raw, captured_at_ms):
        if not self.info['available']:
            return seating_evidence(tracked, raw, [], captured_at_ms, False, self.info['error'])
        try:
            result = self._predict(frame)
            poses = []
            if result.boxes is not None:
                boxes = result.boxes.xyxyn.cpu().numpy().tolist()
                keypoints = (result.keypoints.data.cpu().numpy().tolist()
                             if result.keypoints is not None else [])
                poses = [{'bbox': box, 'keypoints': keypoints[index] if index < len(keypoints) else None}
                         for index, box in enumerate(boxes)]
            return seating_evidence(tracked, raw, poses, captured_at_ms)
        except Exception:
            return seating_evidence(tracked, raw, [], captured_at_ms, False,
                                    'Pose analysis failed for this frame; seating cannot be verified.')
