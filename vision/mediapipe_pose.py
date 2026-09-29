"""Optional, bounded MediaPipe posture observations for the interior camera.

IMAGE mode deliberately keeps no tracking state between camera sessions. The
existing detector tracker supplies identities; uncertain poses stay unknown.
This is visible-body geometry, not proof of seat occupancy or securement.
"""
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np

from .schema import iou
from .seating import (MIN_KEYPOINT_CONFIDENCE, _people, _valid_box, classify_posture,
                      seating_evidence, unique_pose_matches)


ENGINE = 'MediaPipe Pose Landmarker'
METHOD = 'MediaPipe 33 joints · visible-body geometry'
CONNECTIONS = [
    [0, 1], [1, 2], [2, 3], [3, 7], [0, 4], [4, 5], [5, 6], [6, 8], [9, 10],
    [11, 12], [11, 13], [13, 15], [15, 17], [15, 19], [15, 21], [17, 19],
    [12, 14], [14, 16], [16, 18], [16, 20], [16, 22], [18, 20],
    [11, 23], [12, 24], [23, 24], [23, 25], [24, 26], [25, 27], [26, 28],
    [27, 29], [28, 30], [29, 31], [30, 32], [27, 31], [28, 32],
]
# MediaPipe landmark indices for COCO nose, eyes, ears and paired limb joints.
COCO_INDICES = [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]
POSTURE_INDICES = [5, 6, 11, 12, 13, 14, 15, 16]


def pose_quality(pose):
    """Score observable posture joints, never reward a desired posture label."""
    points = np.asarray(pose.get('keypoints'), dtype=float)
    if points.shape != (17, 3) or not np.isfinite(points).all():
        return 0.
    confidence = np.clip(points[POSTURE_INDICES, 2], 0, 1)
    return float(.65 * np.min(confidence) + .35 * np.mean(confidence))


def needs_refinement(person, pose):
    return (pose is None or pose_quality(pose) < .8
            or iou(person['bbox'], pose['bbox']) < .7
            or classify_posture(pose['keypoints'])[0] == 'unknown')


def unpack_pose(landmarks, width, height):
    """Keep all 33 normalized joints but use undistorted pixel limb geometry."""
    if len(landmarks) != 33:
        return None
    joints = []
    for point in landmarks:
        values = [getattr(point, name, None) for name in ('x', 'y', 'z', 'visibility', 'presence')]
        if any(value is None or not np.isfinite(value) for value in values):
            return None
        x, y, z, visibility, presence = map(float, values)
        # A model can extrapolate joints outside the image. Preserve that fact
        # as zero confidence rather than clamping a hallucinated foot on screen.
        on_screen = 0 <= x <= 1 and 0 <= y <= 1
        joints.append({'x': round(x, 6), 'y': round(y, 6), 'z': round(z, 6),
                       'visibility': round(max(0., min(visibility, 1.)), 5) if on_screen else 0.,
                       'presence': round(max(0., min(presence, 1.)), 5) if on_screen else 0.})
    visible = [point for point in joints
               if min(point['visibility'], point['presence']) >= MIN_KEYPOINT_CONFIDENCE]
    if len(visible) < 4:
        return None
    xs, ys = [point['x'] for point in visible], [point['y'] for point in visible]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    if x1 - x0 <= .005 or y1 - y0 <= .005:
        return None
    # Joints lie inside a detector's person rectangle. A small symmetric pad
    # includes head/limb thickness without inferring invisible full body extent.
    pad_x, pad_y = max(.008, (x1 - x0) * .12), max(.008, (y1 - y0) * .06)
    box = [max(0., x0 - pad_x), max(0., y0 - pad_y), min(1., x1 + pad_x), min(1., y1 + pad_y)]
    keypoints = [[joints[index]['x'] * width, joints[index]['y'] * height,
                  min(joints[index]['visibility'], joints[index]['presence'])]
                 for index in COCO_INDICES]
    return {'bbox': box, 'keypoints': keypoints, 'landmarks': joints}


class MediaPipeSeating:
    """One CPU landmarker owned by the same serial inference worker as YOLOE."""
    def __init__(self, path, max_people=27, max_refinements=4, budget_ms=120):
        self.path, self.model, self.mp = Path(path), None, None
        # One overflow sentinel lets all 26 seats be accounted for without
        # mistaking a full bus for a truncated pose result.
        self.max_people = max(1, min(27, int(max_people)))
        self.max_refinements = max(0, min(4, self.max_people - 1, int(max_refinements)))
        self.budget_ms = max(0., min(500., float(budget_ms)))
        self.info = {'available': False, 'engine': ENGINE, 'model': self.path.name,
                     'method': METHOD, 'device': 'cpu', 'max_people': self.max_people,
                     'max_crop_refinements': self.max_refinements,
                     'refinement_budget_ms': self.budget_ms,
                     'landmarks_per_person': 33, 'optional': True, 'default_enabled': False,
                     'error': 'Local MediaPipe weights are missing. Run python scripts/setup_posture.py.'}
        if not self.path.is_file():
            return
        try:
            import mediapipe as mp
            options = mp.tasks.vision.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(self.path),
                                                  delegate=mp.tasks.BaseOptions.Delegate.CPU),
                running_mode=mp.tasks.vision.RunningMode.IMAGE,
                num_poses=self.max_people, min_pose_detection_confidence=.5,
                min_pose_presence_confidence=.6, min_tracking_confidence=.5,
                output_segmentation_masks=False)
            self.mp = mp
            self.model = mp.tasks.vision.PoseLandmarker.create_from_options(options)
            self.info.update(available=True, error=None)
        except ImportError:
            self.info['error'] = 'MediaPipe is not installed. Install requirements-posture.txt.'
        except Exception:
            self.info['error'] = 'Local MediaPipe pose model could not be loaded.'

    def _predict(self, frame):
        # Downscale only the input copy: detector input and output geometry stay
        # unchanged. MediaPipe's model operates internally at a fixed size.
        import cv2
        height, width = frame.shape[:2]
        if max(height, width) > 960:
            scale = 960 / max(height, width)
            frame = cv2.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
        rgb = np.ascontiguousarray(frame[:, :, ::-1])
        return self.model.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb))

    def warmup(self, frame):
        if self.info['available']:
            try:
                self._predict(frame)
            except Exception:
                self.info.update(available=False, error='MediaPipe warm-up failed; object detection remains available.')

    def _refine(self, frame, raw, poses, deadline=None, diagnostics=None):
        """Revisit missing or weak joints with original-resolution person crops.

        A soft deadline prevents starting more work after the budget, but cannot
        interrupt an in-progress native inference. Poses are never carried from
        another frame. Replacement is based on visibility and fit, not on whether
        the classifier would call someone seated.
        """
        people = [person for person in _people(raw) if _valid_box(person['bbox'])]
        matched = unique_pose_matches(people, poses)
        height, width = frame.shape[:2]
        count = 0
        # Missing evidence comes first, then the weakest already matched pose.
        order = sorted(range(len(people)), key=lambda index:
                       pose_quality(poses[matched[index]]) if index in matched else -1.)
        for index in order:
            person = people[index]
            previous_index = matched.get(index)
            previous = poses[previous_index] if previous_index is not None else None
            if not needs_refinement(person, previous):
                continue
            if count >= self.max_refinements or (deadline is not None and time.perf_counter() >= deadline):
                break
            if previous is None and len(poses) >= self.max_people:
                continue
            x0, y0, x1, y1 = person['bbox']
            pad_x, pad_y = (x1 - x0) * .1, (y1 - y0) * .08
            left, top = max(0, int((x0 - pad_x) * width)), max(0, int((y0 - pad_y) * height))
            right, bottom = min(width, int((x1 + pad_x) * width)), min(height, int((y1 + pad_y) * height))
            if right - left < 32 or bottom - top < 64:
                continue
            count += 1
            candidates = []
            try:
                result = self._predict(frame[top:bottom, left:right])
                for points in result.pose_landmarks:
                    projected = [SimpleNamespace(x=(point.x * (right - left) + left) / width,
                                                 y=(point.y * (bottom - top) + top) / height,
                                                 z=point.z * (right - left) / width,
                                                 visibility=point.visibility if 0 <= point.x <= 1 and 0 <= point.y <= 1 else 0.,
                                                 presence=point.presence if 0 <= point.x <= 1 and 0 <= point.y <= 1 else 0.)
                                 for point in points]
                    pose = unpack_pose(projected, width, height)
                    if pose is not None and unique_pose_matches(people, [pose]).get(index) == 0:
                        candidates.append(pose)
            except Exception:
                # An optional crop must not invalidate fresh full-frame poses.
                # This target keeps its original unknown/weak evidence; never
                # recover a pose from a previous frame or expose native errors.
                if diagnostics is not None:
                    diagnostics['crop_errors'] = diagnostics.get('crop_errors', 0) + 1
                continue
            # Crowded crops with more than one candidate are ambiguous. An
            # existing pose may already belong to this neighbour; do not copy it.
            if len(candidates) != 1:
                continue
            candidate = candidates[0]
            if any(iou(candidate['bbox'], pose['bbox']) >= .5
                   for other_index, pose in enumerate(poses) if other_index != previous_index):
                continue
            if previous is not None:
                old_quality, new_quality = pose_quality(previous), pose_quality(candidate)
                better_quality = new_quality >= old_quality + .05
                better_fit = (new_quality >= old_quality
                              and iou(person['bbox'], candidate['bbox']) >= iou(person['bbox'], previous['bbox']) + .15)
                if not (better_quality or better_fit):
                    continue
            candidate['refined'] = True
            if previous_index is None:
                poses.append(candidate)
            else:
                poses[previous_index] = candidate
            matched = unique_pose_matches(people, poses)
        return count

    def estimate(self, frame, tracked, raw, captured_at_ms):
        start = time.perf_counter()
        poses, capped, refinements = [], False, 0
        diagnostics = {'crop_errors': 0}
        available, error = self.info['available'], self.info['error']
        if available:
            try:
                result = self._predict(frame)
                landmarks = result.pose_landmarks
                capped = len(landmarks) >= self.max_people or len(_people(raw)) > self.max_people
                height, width = frame.shape[:2]
                poses = [pose for points in landmarks[:self.max_people]
                         if (pose := unpack_pose(points, width, height)) is not None]
                refinements = self._refine(frame, raw, poses, start + self.budget_ms / 1000, diagnostics)
                capped = capped or len(poses) >= self.max_people
            except Exception:
                available, error = False, 'MediaPipe failed for this frame; fresh posture is required.'
        summary = seating_evidence(tracked, raw, poses, captured_at_ms, available, error)
        summary.update(engine=ENGINE, method=METHOD, connections=CONNECTIONS,
                       max_people=self.max_people, capacity_reached=capped,
                       crop_refinements=refinements,
                       crop_errors=diagnostics['crop_errors'],
                       refined_poses=sum(bool(pose.get('refined')) for pose in poses),
                       model=self.path.name,
                       refinement_budget_ms=self.budget_ms,
                       refinement_budget_exhausted=(time.perf_counter() - start) * 1000 >= self.budget_ms,
                       analysis_ms=round((time.perf_counter() - start) * 1000, 1))
        if capped:
            summary.update(complete=False, reason='MediaPipe capacity reached; some passengers may lack posture evidence.')
        return summary

    def close(self):
        if self.model is not None:
            self.model.close()
            self.model = None
