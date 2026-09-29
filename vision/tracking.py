"""BoT-SORT association with stream-local IDs and bounded visual stabilization."""
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import count
from types import SimpleNamespace

import cv2
import numpy as np
from .schema import Detection, Options


def xywh(box):
    box = np.asarray(box, dtype=float)
    return np.r_[(box[:2] + box[2:]) / 2, box[2:] - box[:2]]


def xyxy(box):
    return np.clip(np.r_[box[:2] - box[2:] / 2, box[:2] + box[2:] / 2], 0, 1)


class OneEuro:
    """Adaptive low-pass filter using real timestamps in seconds."""
    def __init__(self, min_cutoff=2.5, beta=8.0):
        self.min_cutoff = np.array([min_cutoff, min_cutoff, min_cutoff * .6, min_cutoff * .6])
        self.beta = np.array([beta, beta, beta * .4, beta * .4])
        self.value = self.raw = self.timestamp = None
        self.derivative = np.zeros(4)

    @staticmethod
    def alpha(cutoff, dt):
        return 1 / (1 + 1 / (2 * np.pi * cutoff * dt))

    def update(self, value, timestamp):
        value = np.asarray(value, dtype=float)
        if self.timestamp is None or timestamp - self.timestamp > .3:
            self.value, self.raw, self.timestamp = value.copy(), value.copy(), timestamp
            self.derivative = np.zeros(4)
            return self.value.copy()
        dt = max(timestamp - self.timestamp, .001)
        derivative = (value - self.raw) / dt
        self.derivative += self.alpha(1.0, dt) * (derivative - self.derivative)
        a = self.alpha(self.min_cutoff + self.beta * np.abs(self.derivative), dt)
        self.value += a * (value - self.value)
        self.raw, self.timestamp = value.copy(), timestamp
        return self.value.copy()


class BoxResults:
    """Minimal NumPy results contract consumed by Ultralytics trackers."""
    def __init__(self, rows):
        self.rows = np.asarray(rows, dtype=np.float32).reshape(-1, 6)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        return BoxResults(self.rows[index])

    @property
    def xyxy(self):
        return self.rows[:, :4]

    @property
    def xywh(self):
        boxes = self.xyxy.copy()
        boxes[:, 2:] -= boxes[:, :2]
        boxes[:, :2] += boxes[:, 2:] / 2
        return boxes

    @property
    def conf(self):
        return self.rows[:, 4]

    @property
    def cls(self):
        return self.rows[:, 5]


class CachedMotion:
    method = 'sharedSparseOptFlow'

    def __init__(self, warp):
        self.warp = warp

    def apply(self, *args):
        return self.warp


@lru_cache(maxsize=1)
def tracker_types():
    # Pinned Ultralytics supplies Kalman, high/low-score association and GMC.
    from ultralytics import settings
    settings.update({'sync': False})
    from ultralytics.trackers.bot_sort import BOTSORT, BOTrack
    from ultralytics.trackers.utils.gmc import GMC
    from ultralytics.trackers.utils.stracks import parse_bboxes

    class LocalTrack(BOTrack):
        def __init__(self, box, score, category, allocate):
            super().__init__(box, score, category)
            self.next_id = allocate

        def activate(self, *args):
            super().activate(*args)
            # Display confirmation is 2-of-3. Keep candidates eligible for
            # association across an intervening missed observation.
            self.is_activated = True

    class ScopedBoTSORT(BOTSORT):
        def __init__(self, args, allocate):
            self.allocate = allocate
            super().__init__(args)

        @staticmethod
        def reset_id():
            pass  # Never reset another camera/job's global Ultralytics IDs.

        def init_track(self, results, img=None):
            return [LocalTrack(box, score, category, self.allocate)
                    for box, score, category in zip(parse_bboxes(results), results.conf, results.cls)] if len(results) else []

    return ScopedBoTSORT, GMC


@dataclass
class VisualTrack:
    label: str
    seen: float
    raw: np.ndarray
    box: np.ndarray
    filter: OneEuro
    score: float | None
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))
    hits: deque = field(default_factory=lambda: deque(maxlen=3))
    confirmed: bool = False


class StableTracker:
    """One instance per live session or video; source-time expiry, isolated IDs."""
    def __init__(self, options=None, max_age=.7):
        self.options = options or Options()
        self.max_age = max_age
        self.bridge = .15 if self.options.stabilization == 'balanced' else .1
        self.next_id = count(1).__next__
        self.trackers, self.visual = {}, {}
        self.motion = None
        self.last_time = self.shape = None
        self.index = 0

    @property
    def detection_options(self):
        if self.options.stabilization == 'off':
            return self.options
        # Low-score candidates may maintain an existing track, never create one.
        return self.options.model_copy(update={
            'confidence': min(.15, self.options.confidence * .5),
            'class_confidences': {label: min(.15, value * .5) for label, value in self.options.class_confidences.items()}})

    def _tracker(self, label):
        if label not in self.trackers:
            bot, _ = tracker_types()
            confidence = self.options.confidence_for(label)
            args = SimpleNamespace(track_high_thresh=confidence,
                track_low_thresh=min(.15, confidence * .5),
                new_track_thresh=confidence, track_buffer=120,
                match_thresh=.8, fuse_score=True, gmc_method='none',
                proximity_thresh=.5, appearance_thresh=.8, with_reid=False, model='auto')
            self.trackers[label] = bot(args, self.next_id)
        return self.trackers[label]

    def _warp(self, frame, boxes):
        identity = np.eye(2, 3, dtype=np.float64)
        if frame is None or not self.options.camera_motion:
            return identity
        if self.motion is None:
            _, gmc = tracker_types()
            self.motion = gmc(method='sparseOptFlow', downscale=2)
        try:
            warp = self.motion.apply(frame, boxes)
            h, w = frame.shape[:2]
            if (np.isfinite(warp).all() and .6 < np.linalg.det(warp[:, :2]) < 1.6
                    and abs(warp[0, 2]) < w * .3 and abs(warp[1, 2]) < h * .3):
                return warp
        except (cv2.error, ValueError):
            self.motion.reset_params()
        return identity

    def update(self, detections, timestamp, frame=None):
        if not np.isfinite(timestamp) or (self.last_time is not None and timestamp <= self.last_time):
            return []
        if self.options.stabilization == 'off':
            self.last_time = timestamp
            return [Detection(d.label, d.bbox, d.score, observed_at_ms=timestamp * 1000)
                    for d in detections if d.score is None or d.score >= self.options.confidence_for(d.label)]
        if frame is not None:
            scale = min(1, 640 / max(frame.shape[:2]))
            frame = cv2.resize(frame, (max(2, round(frame.shape[1] * scale)), max(2, round(frame.shape[0] * scale))))
            shape = frame.shape[:2]
        else:
            shape = (640, 640)
        if self.shape != shape or (self.last_time is not None and timestamp - self.last_time > self.max_age):
            self.trackers.clear(); self.visual.clear(); self.motion = None
        self.shape, self.last_time = shape, timestamp
        self.index += 1
        height, width = shape
        factors = np.array([width, height, width, height])
        grouped = {}
        for detection in detections:
            grouped.setdefault(detection.label, []).append(detection)
        pixels = np.array([np.array(d.bbox) * factors for d in detections]).reshape(-1, 4)
        # Camera motion is computed once for all prompted classes.
        warp = CachedMotion(self._warp(frame, pixels))
        current, output = set(), []
        for label in sorted(set(grouped) | set(self.trackers)):
            candidates = grouped.get(label, [])
            rows = [list(np.array(d.bbox) * factors) + [d.score if d.score is not None else 1.0, 0]
                    for d in candidates]
            tracker = self._tracker(label)
            for name in ('tracked_stracks', 'lost_stracks'):
                setattr(tracker, name, [track for track in getattr(tracker, name)
                    if track.track_id in self.visual and timestamp - self.visual[track.track_id].seen <= self.max_age])
            tracker.gmc = warp
            tracked = tracker.update(BoxResults(rows), img=frame)
            for row in tracked:
                track_id, source_index = int(row[4]), int(row[-1])
                if not 0 <= source_index < len(candidates):
                    continue
                detection = candidates[source_index]
                raw = xywh(detection.bbox)
                visual = self.visual.get(track_id)
                if visual is None:
                    fast = self.options.stabilization == 'responsive'
                    filt = OneEuro(min_cutoff=5 if fast else 2.5, beta=12 if fast else 8)
                    visual = VisualTrack(label, timestamp, raw, raw, filt, detection.score)
                    self.visual[track_id] = visual
                else:
                    dt = max(timestamp - visual.seen, .001)
                    if dt <= self.bridge:
                        measured = np.clip((raw[:2] - visual.raw[:2]) / dt, -2, 2)
                        visual.velocity += dt / (dt + .08) * (measured - visual.velocity)
                    else:
                        visual.velocity[:] = 0
                        visual.filter.timestamp = None
                visual.box = visual.filter.update(raw, timestamp)
                visual.raw, visual.seen, visual.score = raw, timestamp, detection.score
                visual.hits.append(self.index)
                visual.confirmed |= len(visual.hits) >= 2 and visual.hits[-1] - visual.hits[-2] <= 2
                current.add(track_id)
                if visual.confirmed:
                    output.append(self._detection(track_id, visual, timestamp))
        for track_id, visual in list(self.visual.items()):
            age = timestamp - visual.seen
            if age > self.max_age:
                del self.visual[track_id]
            elif track_id not in current and visual.confirmed and age <= self.bridge:
                output.append(self._detection(track_id, visual, timestamp, predicted=True))
        return sorted(output, key=lambda d: d.track_id)

    def _detection(self, track_id, visual, timestamp, predicted=False):
        box = visual.box.copy()
        if predicted:
            offset = visual.velocity * min(timestamp - visual.seen, self.bridge)
            box[:2] += np.clip(offset, -box[2:] * .25, box[2:] * .25)
        velocity = [*visual.velocity, *visual.velocity]
        return Detection(visual.label, xyxy(box).tolist(), visual.score, track_id,
                         velocity, predicted, visual.seen * 1000, (visual.seen + self.bridge) * 1000)
