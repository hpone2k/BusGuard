"""Elapsed-time voting for visible object counts, independent of frame rate.

Only a newly observed frame closes the preceding observation's interval. Reading
a summary never credits more time to a frozen camera frame. Support is the share
of observed time, not a calibrated probability that the count is correct.
"""

import copy
import math
import threading
from collections import Counter, deque
from collections.abc import Mapping


MAX_LABELS = 64
MAX_DETECTIONS = 300
MAX_INTERVALS = 2048
EPSILON_MS = 1e-6


def _label(value):
    return str(value or '').strip().casefold()[:160]


def _labels(values):
    result = []
    for value in values:
        label = _label(value)
        if label and label not in result:
            result.append(label)
        if len(result) >= MAX_LABELS:
            break
    return result


def _observed_counts(detections, labels):
    """Count observed tracks once within each class; preserve requested zeros."""
    counts = dict.fromkeys(labels, 0)
    seen = set()
    total = 0
    for index, detection in enumerate(detections):
        if index >= MAX_DETECTIONS:
            break
        get = detection.get if isinstance(detection, Mapping) else lambda key, default=None: getattr(detection, key, default)
        if get('predicted', False):
            continue
        label = _label(get('label'))
        if not label:
            continue
        track_id = get('track_id')
        if track_id is not None:
            key = (label, str(track_id))
            if key in seen:
                continue
            seen.add(key)
        total += 1
        if label not in counts and len(counts) < MAX_LABELS:
            counts[label] = 0
        if label in counts:
            counts[label] += 1
    return counts, total


def _timestamp(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Count timestamps must be finite milliseconds.')
    return value


def snapshot_counts(detections, labels=()):
    """Return an explicitly instantaneous count for a still image.

    Temporal support is unknown for a single image, rather than falsely 100%.
    """
    counts, total = _observed_counts(detections, _labels(labels))

    def estimate(value):
        return {'raw': value, 'stable': value, 'candidate': value, 'support': None,
                'status': 'instant', 'distribution': [], 'held_age_ms': 0, 'held_remaining_ms': 0}

    return {'window_ms': 0, 'coverage_ms': 0, 'coverage': 0,
            'support_threshold': None, 'stale_ms': None, 'uncertain_hold_ms': 0, 'status': 'instant',
            'last_observed_ms': None, 'age_ms': 0,
            'total': estimate(total),
            'classes': {label: estimate(value) for label, value in counts.items()}}


def age_summary(summary, age_ms, stale_ms=None):
    """Age a frozen summary without moving its vote window or adding evidence.

    ``age_ms`` is the absolute age of its last observation, not an increment.
    Previously aged summaries are safe to age again without counting time twice.
    """
    result = copy.deepcopy(summary)
    if result.get('status') == 'instant' or result.get('last_observed_ms') is None:
        return result
    age = max(0.0, _timestamp(age_ms))
    previous_age = max(0.0, result.get('age_ms') or 0)
    elapsed = max(0.0, age - previous_age)
    result['age_ms'] = round(max(age, previous_age), 3)
    expiry = result.get('stale_ms', 1500) if stale_ms is None else stale_ms
    hold = result.get('uncertain_hold_ms', 1000)
    estimates = [result['total'], *result['classes'].values()]
    for estimate in estimates:
        if age >= expiry:
            estimate.update(raw=None, stable=None, candidate=None, support=None,
                            status='stale', distribution=[], held_age_ms=None, held_remaining_ms=0)
        elif estimate['status'] == 'uncertain' and estimate.get('held_age_ms') is not None:
            held_age = estimate['held_age_ms'] + elapsed
            estimate['held_age_ms'] = round(held_age, 3)
            estimate['held_remaining_ms'] = round(max(0, hold - held_age), 3)
            if held_age > hold:
                estimate['stable'] = None
    result['status'] = result['total']['status']
    return result


class RollingCountVoter:
    """A bounded per-source rolling mode with a minimum support requirement.

    ``observe`` takes monotonically increasing milliseconds from one source's
    clock. Repeated/out-of-order timestamps are ignored. A gap beyond
    ``max_gap_ms`` resets the evidence, so reconnecting cannot manufacture votes.
    The total is voted independently; class-wise modes need not sum to its mode.
    """

    def __init__(self, labels=(), window_ms=1000, support_threshold=.65,
                 max_gap_ms=1000, stale_ms=1500, uncertain_hold_ms=1000):
        self.window_ms = _timestamp(window_ms)
        self.support_threshold = float(support_threshold)
        self.max_gap_ms = _timestamp(max_gap_ms)
        self.stale_ms = _timestamp(stale_ms)
        self.uncertain_hold_ms = _timestamp(uncertain_hold_ms)
        if (self.window_ms <= 0 or self.max_gap_ms <= 0 or self.stale_ms <= 0
                or self.uncertain_hold_ms < 0
                or not math.isfinite(self.support_threshold)
                or not .5 < self.support_threshold <= 1):
            raise ValueError('Use positive count windows and a support threshold above 50%.')
        self.labels = _labels(labels)
        self.lock = threading.RLock()
        self.intervals = deque(maxlen=MAX_INTERVALS)
        self.last_observed_ms = None
        self.raw_counts = dict.fromkeys(self.labels, 0)
        self.raw_total = 0
        self.confirmed = {}
        self.summary = self._empty_summary()

    def _empty_summary(self):
        def estimate():
            return {'raw': None, 'stable': None, 'candidate': None, 'support': 0,
                    'status': 'warming', 'distribution': [],
                    'held_age_ms': None, 'held_remaining_ms': 0}
        return {'window_ms': self.window_ms, 'coverage_ms': 0, 'coverage': 0,
                'support_threshold': self.support_threshold, 'stale_ms': self.stale_ms,
                'uncertain_hold_ms': self.uncertain_hold_ms,
                'status': 'warming', 'last_observed_ms': None, 'age_ms': None,
                'total': estimate(), 'classes': {label: estimate() for label in self.labels}}

    def observe(self, detections, timestamp_ms):
        timestamp_ms = _timestamp(timestamp_ms)
        with self.lock:
            if self.last_observed_ms is not None and timestamp_ms <= self.last_observed_ms:
                return self.snapshot()
            counts, total = _observed_counts(detections, self.labels)
            self.labels = list(counts)
            if self.last_observed_ms is not None:
                gap = timestamp_ms - self.last_observed_ms
                if gap > self.max_gap_ms + EPSILON_MS:
                    self.intervals.clear()
                    self.confirmed.clear()
                else:
                    start = self.last_observed_ms
                    # Coalesce identical observations without changing weights.
                    if (self.intervals and self.intervals[-1][1] == start
                            and self.intervals[-1][2] == self.raw_counts
                            and self.intervals[-1][3] == self.raw_total):
                        prior = self.intervals.pop()
                        start = prior[0]
                    self.intervals.append((start, timestamp_ms, self.raw_counts, self.raw_total))
            self.last_observed_ms = timestamp_ms
            self.raw_counts, self.raw_total = counts, total
            cutoff = timestamp_ms - self.window_ms
            while self.intervals and self.intervals[0][1] <= cutoff:
                self.intervals.popleft()
            total_votes = Counter()
            class_votes = {label: Counter() for label in self.labels}
            coverage_ms = 0.0
            for start, end, interval_counts, interval_total in self.intervals:
                duration = max(0.0, end - max(start, cutoff))
                coverage_ms += duration
                total_votes[interval_total] += duration
                for label, votes in class_votes.items():
                    votes[interval_counts.get(label, 0)] += duration
            full_window = coverage_ms + EPSILON_MS >= self.window_ms
            total_estimate = self._estimate('total', total_votes, total, coverage_ms, full_window, timestamp_ms)
            self.summary = {
                'window_ms': self.window_ms, 'coverage_ms': round(coverage_ms, 3),
                'coverage': min(1.0, coverage_ms / self.window_ms),
                'support_threshold': self.support_threshold, 'stale_ms': self.stale_ms,
                'uncertain_hold_ms': self.uncertain_hold_ms,
                'status': total_estimate['status'], 'last_observed_ms': timestamp_ms, 'age_ms': 0,
                'total': total_estimate,
                'classes': {label: self._estimate(('class', label), votes, counts[label], coverage_ms,
                                                  full_window, timestamp_ms)
                            for label, votes in class_votes.items()},
            }
            return self.snapshot()

    def _estimate(self, key, votes, raw, coverage_ms, full_window, timestamp_ms):
        ranked = sorted(votes.items(), key=lambda item: (-item[1], item[0]))
        best_duration = ranked[0][1] if ranked else 0
        tied = len(ranked) > 1 and abs(best_duration - ranked[1][1]) <= EPSILON_MS
        candidate = ranked[0][0] if ranked and not tied else None
        support = best_duration / coverage_ms if coverage_ms else 0
        stable = None
        held_age = None
        if not full_window:
            status = 'warming'
        elif candidate is not None and support + 1e-9 >= self.support_threshold:
            status, stable = 'stable', candidate
            self.confirmed[key] = (candidate, timestamp_ms)
            held_age = 0
        else:
            status = 'uncertain'
            previous = self.confirmed.get(key)
            if previous:
                held_age = timestamp_ms - previous[1]
                if held_age <= self.uncertain_hold_ms:
                    stable = previous[0]
        return {'raw': raw, 'stable': stable, 'candidate': candidate, 'support': support,
                'status': status, 'held_age_ms': held_age,
                'held_remaining_ms': max(0, self.uncertain_hold_ms - held_age) if held_age is not None else 0,
                'distribution': [{'count': count, 'duration_ms': round(duration, 3),
                                  'share': duration / coverage_ms}
                                 for count, duration in ranked if duration > 0]}

    def snapshot(self, now_ms=None):
        """Read the last computed vote; only freshness/held-value expiry changes.

        ``now_ms`` must use the same clock as observation timestamps. No final
        frame interval is extended and no old votes are trimmed on a read.
        """
        with self.lock:
            if self.last_observed_ms is None:
                return copy.deepcopy(self.summary)
            now = self.last_observed_ms if now_ms is None else _timestamp(now_ms)
            age = max(0.0, now - self.last_observed_ms)
            return age_summary(self.summary, age)
