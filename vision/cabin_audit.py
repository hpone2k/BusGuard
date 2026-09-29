"""Thirty-second, equally weighted cabin counts from fresh live observations.

A cycle has thirty consecutive one-second buckets. Each bucket keeps its latest
accepted stable person count, so camera frame rate cannot weight the mean. Missing
or invalid evidence abandons the partial cycle; it never supplies a zero. Results
are immutable and revisioned, while partial observations are never persisted.
"""
from dataclasses import dataclass
import math


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class CabinAuditResult:
    revision: int
    mean: float
    rounded: int
    started_at: float
    completed_at: float
    source_id: str
    samples: int = 30


class CabinAudit:
    WINDOW_SECONDS = 30
    MAX_AGE_MS = 1000
    MAX_GAP_SECONDS = 2.0
    EPSILON = 1e-7

    def __init__(self):
        self.result = None
        self.revision = 0
        self._source = None
        self._frame = self._capture = self._last_packet_at = None
        self._packet_age = 0.
        self._started_at = None
        self._samples = []
        self._reason = 'Waiting for fresh indoor camera counts.'

    def _reset(self, reason):
        self._started_at = None
        self._samples.clear()
        self._reason = reason

    def reset(self):
        """Forget historical and partial counts for an explicit new demo load.

        Keep result revisions increasing for consumers that remember the last
        applied revision; an ordinary controller safety reset need not call this.
        """
        revision = self.revision
        self.__init__()
        self.revision = revision

    def _validate(self, inside, now, whole_cabin):
        if not whole_cabin:
            return None, 'Confirm that the indoor camera covers the whole cabin.'
        if not isinstance(inside, dict):
            return None, 'Waiting for fresh indoor camera counts.'
        if (inside.get('kind') != 'live' or inside.get('connected') is not True
                or inside.get('is_demo') or inside.get('prompt_mode') != 'objects'):
            return None, 'Waiting for a connected live indoor object detector.'
        summary = inside.get('count_summary')
        if not isinstance(summary, dict) or not isinstance(summary.get('classes'), dict):
            return None, 'Waiting for a stable person count.'
        people = [value for label, value in summary['classes'].items()
                  if str(label).strip().casefold() in {'person', 'persons'}]
        person = people[0] if len(people) == 1 and isinstance(people[0], dict) else {}
        count = person.get('stable')
        source, frame = inside.get('session_id'), inside.get('frame_id')
        capture, received = summary.get('last_observed_ms'), inside.get('received_at_ms')
        ages = inside.get('age_ms'), summary.get('age_ms')
        if (not isinstance(source, str) or not source or len(source) > 256
                or not isinstance(frame, int) or isinstance(frame, bool) or frame < 0
                or not _finite(capture) or capture < 0 or not _finite(received)
                or not all(_finite(age) and 0 <= age < self.MAX_AGE_MS for age in ages)
                or not -50 <= now * 1000 - received < self.MAX_AGE_MS):
            return None, 'Indoor camera evidence is missing or stale.'
        if (not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 300
                or person.get('status') != 'stable'
                or not _finite(person.get('support')) or not .65 <= person['support'] <= 1
                or not _finite(summary.get('coverage')) or not .65 <= summary['coverage'] <= 1):
            return None, 'Waiting for a stable literal person count.'
        return (source, frame, capture, count, max(*ages, max(0, now * 1000 - received))), None

    def observe(self, inside, now, *, whole_cabin=True):
        """Return a newly completed result once, or None; ``now`` is server seconds.

        Polling a repeated frame never adds evidence. A still-fresh repeated frame
        may close a fully populated window at its end, but cannot seed the next
        cycle. Capture timestamps are source-relative and must strictly increase;
        source and vote ages plus server receipt time establish their freshness.
        """
        if not _finite(now) or now < 0:
            self._reset('Waiting for a valid server timestamp.')
            return None
        packet, reason = self._validate(inside, now, whole_cabin)
        if packet is None:
            self._reset(reason)
            return None
        source, frame, capture, count, age = packet
        if source != self._source:
            self._reset('The indoor source changed; starting a new cycle.')
            self._source = source
            self._frame = self._capture = self._last_packet_at = None
        new_frame = self._frame is None or frame > self._frame
        if self._frame is not None and (frame < self._frame
                or frame == self._frame and capture != self._capture
                or new_frame and capture <= self._capture):
            self._reset('Waiting for indoor frames in increasing capture order.')
            return None
        if self._last_packet_at is not None:
            gap = now - self._last_packet_at
            if gap < -self.EPSILON or gap >= self.MAX_GAP_SECONDS - self.EPSILON:
                self._reset('A camera gap interrupted the cycle; starting again.')
        if not new_frame:
            # A caller cannot keep one frame alive by refreshing its age fields.
            if self._last_packet_at is None or self._packet_age + (now - self._last_packet_at) * 1000 >= self.MAX_AGE_MS:
                self._reset('Waiting for a new indoor frame.')
                return None

        completed = None
        if self._started_at is not None:
            elapsed = now - self._started_at
            bucket = math.floor(elapsed + self.EPSILON)
            if bucket >= self.WINDOW_SECONDS:
                boundary = self._started_at + self.WINDOW_SECONDS
                # An incoming replacement frame cannot validate a stale final
                # sample or retroactively complete multiple elapsed intervals.
                # Include exactly one second here so a true 1 Hz feed works.
                final_age = self._packet_age + (boundary - self._last_packet_at) * 1000
                if (len(self._samples) == self.WINDOW_SECONDS
                        and bucket == self.WINDOW_SECONDS
                        and final_age <= self.MAX_AGE_MS + self.EPSILON * 1000):
                    total = sum(self._samples)
                    self.revision += 1
                    self.result = CabinAuditResult(
                        revision=self.revision, mean=total / self.WINDOW_SECONDS,
                        rounded=(total * 2 + self.WINDOW_SECONDS) // (2 * self.WINDOW_SECONDS),
                        started_at=self._started_at,
                        completed_at=boundary,
                        source_id=self._source)
                    completed = self.result
                self._reset('Waiting for the next thirty-second cycle.')
            elif bucket > len(self._samples):
                self._reset('A one-second sample was missing; starting a new cycle.')

        if new_frame:
            self._frame, self._capture = frame, capture
            self._last_packet_at, self._packet_age = now, age
            if self._started_at is None:
                self._started_at = now
            bucket = math.floor(now - self._started_at + self.EPSILON)
            if bucket == len(self._samples):
                self._samples.append(count)
            elif 0 <= bucket < len(self._samples):
                self._samples[bucket] = count
            self._reason = 'Collecting one fresh stable count per second.'
        return completed

    def snapshot(self, now):
        fresh = (_finite(now) and self._last_packet_at is not None
                 and 0 <= now - self._last_packet_at <= self.MAX_GAP_SECONDS + self.EPSILON
                 and self._packet_age + (now - self._last_packet_at) * 1000 < self.MAX_AGE_MS)
        active = self._started_at is not None and fresh
        elapsed = min(self.WINDOW_SECONDS, max(0, now - self._started_at)) if active else 0.
        return dict(
            status='collecting' if active else 'unavailable',
            collected=len(self._samples) if active else 0,
            required=self.WINDOW_SECONDS,
            elapsed_seconds=round(elapsed, 3),
            remaining_seconds=round(self.WINDOW_SECONDS - elapsed, 3),
            last_mean=None if self.result is None else self.result.mean,
            last_rounded=None if self.result is None else self.result.rounded,
            revision=self.revision,
            cycle_completed_at=None if self.result is None else self.result.completed_at,
            source_id=self._source,
            reason=self._reason if active or self._started_at is None else 'Waiting for a new indoor frame.',
            method='thirty_second_mean',
        )
