"""Conservative capacity evidence from fresh, voted inside-person observations.

This is a visible-person estimate, not a seat detector or an entry/exit counter.
Confirmed boarding records remain a lower bound. Decreases from vision require
an operator-confirmed whole-cabin view and sustained new frames with doors open
while stationary. Passenger counting continues throughout the trip, independently
of the post-closure seating check.
"""
import math


class CabinCount:
    RISE_SECONDS = .6
    FALL_SECONDS = 2.5
    EVENT_SETTLE_SECONDS = 5.
    MAX_AGE_MS = 1000

    def __init__(self):
        self.count = None
        self.whole_cabin = False
        self.received_at = None
        self.source = None
        self.frame = None
        self.candidate = None
        self.since = None
        self.last_sample = None
        self.protected_until = 0.

    @staticmethod
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def _clear_candidate(self):
        self.candidate = self.since = self.last_sample = None

    def configure(self, whole_cabin):
        self.whole_cabin = whole_cabin
        self._clear_candidate()

    def restore(self, value):
        if not isinstance(value, dict):
            return
        count = value.get('count')
        if count is not None and (not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 300):
            raise ValueError('Invalid retained cabin count.')
        coverage = value.get('whole_cabin', False)
        if not isinstance(coverage, bool):
            raise ValueError('Invalid cabin coverage setting.')
        self.count, self.whole_cabin = count, coverage

    def dump(self):
        return dict(count=self.count, whole_cabin=self.whole_cabin)

    def event(self, before, delta, now):
        # A just-confirmed board/alight must not be undone by a delayed frame.
        if self.count is not None:
            self.count = max(0, min(300, before + delta))
            self.protected_until = now + self.EVENT_SETTLE_SECONDS
            self._clear_candidate()

    def observe(self, inside, now, *, stationary, doors_open):
        summary = inside.get('count_summary') or {}
        # Match the literal person labels used by posture detection. Multiple
        # aliases may overlap, so never add them or select an arbitrary subset.
        classes = summary.get('classes') or {}
        people = [value for label, value in classes.items()
                  if str(label).strip().casefold() in {'person', 'persons'}]
        person = people[0] if len(people) == 1 and isinstance(people[0], dict) else {}
        count, age = person.get('stable'), inside.get('age_ms')
        valid = (inside.get('kind') == 'live' and inside.get('connected')
                 and not inside.get('is_demo') and inside.get('prompt_mode') == 'objects'
                 and self.finite(age) and 0 <= age < self.MAX_AGE_MS
                 and self.finite(summary.get('age_ms')) and 0 <= summary['age_ms'] < self.MAX_AGE_MS
                 and self.finite(summary.get('coverage')) and summary['coverage'] >= .65
                 and person.get('status') == 'stable' and self.finite(person.get('support'))
                 and person['support'] >= .65 and isinstance(count, int) and not isinstance(count, bool)
                 and 0 <= count <= 300 and inside.get('session_id')
                 and isinstance(inside.get('frame_id'), int) and not isinstance(inside.get('frame_id'), bool)
                 and self.finite(inside.get('received_at_ms'))
                 and -50 <= now * 1000 - inside['received_at_ms'] < self.MAX_AGE_MS)
        if not valid:
            self._clear_candidate()
            return False
        source, frame = inside['session_id'], inside['frame_id']
        if source != self.source:
            self.source, self.frame = source, None
            self._clear_candidate()
        if self.frame is not None and frame <= self.frame:
            return False  # Repeated polling never creates temporal evidence.
        self.frame = frame
        self.received_at = inside['received_at_ms'] / 1000
        if now < self.protected_until:
            self._clear_candidate()
            return False
        if count == self.count:
            self._clear_candidate()
            return False
        falling = self.count is not None and count < self.count
        if falling and (not self.whole_cabin or not stationary or not doors_open):
            self._clear_candidate()
            return False
        if self.candidate != count or self.last_sample is None or now - self.last_sample >= 1:
            self.candidate, self.since = count, now
        self.last_sample = now
        delay = self.FALL_SECONDS if falling else self.RISE_SECONDS
        if now - self.since + 1e-8 < delay:
            return False
        self.count = count
        self._clear_candidate()
        return True

    def snapshot(self, now):
        age = None if self.received_at is None else max(0, round((now - self.received_at) * 1000))
        status = 'unavailable' if self.count is None else 'fresh' if age is not None and age < self.MAX_AGE_MS else 'retained'
        return dict(people=self.count, status=status, age_ms=age, whole_cabin=self.whole_cabin,
                    decrease_confirmation_ms=round(self.FALL_SECONDS * 1000),
                    source='inside_person_time_vote', seat_locations_verified=False)
