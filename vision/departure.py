"""Conservative posture gate for simulated departure, never a vehicle controller.

Standing-only mode confirms five continuous seconds of valid negative inference.
The legacy seated mode retains track identities for one closed-door cycle.
"""
import math


def _semantic_summary(summary):
    # Kept local to avoid importing the joint geometry module into this gate.
    return (summary.get('engine') == 'LocateAnything'
            and isinstance(summary.get('posture_frame_id'), int)
            and not isinstance(summary.get('posture_frame_id'), bool)
            and summary['posture_frame_id'] >= 0
            and isinstance(summary.get('source_session_id'), str)
            and bool(summary['source_session_id']))


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def counter(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 300


def track_key(value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.strip() and len(value) <= 128:
        return value.strip()
    return None


class DepartureInterlock:
    FRESH_SECONDS = 1.
    CONFIRM_SECONDS = 3.
    STANDING_CONFIRM_SECONDS = 5.
    MAX_EXPECTED = 600

    def __init__(self):
        self.reset()

    def reset(self):
        self.closed = False
        self.closed_at = None
        self.session = None
        self.expected = set()
        self.high_water = 0
        self.overflow = False
        self.last_frame = None
        self.last_capture = None
        self.packet_at = None
        self.packet_age = 0.
        self.confirmed_capture = None
        self.confirmed_at = None
        self.confirmed_roster = None
        self.confirmed_samples = 0
        self.fresh_seconds = self.FRESH_SECONDS
        self.source_packet_at = self.source_packet_age = None
        self.source_frame = None
        self.progress = 0.
        self.mode = None
        self.message = 'Fresh live cabin posture is required after the doors close.'
        self.status = 'unknown'

    def hold(self, message, status='unknown'):
        self.progress = 0.
        self.confirmed_capture = self.confirmed_at = self.confirmed_roster = None
        self.confirmed_samples = 0
        self.message, self.status = message, status

    @property
    def confirmation_seconds(self):
        return self.CONFIRM_SECONDS if self.mode == 'seated' else self.STANDING_CONFIRM_SECONDS

    def update(self, source, *, closed, enabled, now, expected_people=0, allow_empty=False):
        if not enabled or not closed:
            self.reset()
            self.status = 'disabled' if not enabled else 'boarding'
            return
        source = source if isinstance(source, dict) else {}
        valid_identity = source.get('kind') == 'live' and not source.get('is_demo')
        session = source.get('session_id') if valid_identity else None
        session = session if isinstance(session, str) and session else None
        new_cycle = not self.closed
        if new_cycle:
            self.reset()
            self.closed, self.closed_at = True, now
        if counter(expected_people):
            self.high_water = max(self.high_water, expected_people)
        if session is not None and session != self.session:
            # Track IDs are session-local. Keep the known minimum number across
            # a replacement camera, but start a fresh roster and confirmation.
            self.high_water = max(self.high_water, len(self.expected))
            self.expected.clear()
            self.session = session
            self.last_frame = self.last_capture = self.packet_at = None
            self.source_frame = self.source_packet_at = self.source_packet_age = None
            self.closed_at = now
            self.hold('A new cabin source needs fresh observations and a new departure confirmation.')
            new_cycle = True
        if not source.get('connected') or session is None:
            self.hold('Fresh live observations from the indoor camera are required.')
            return
        summary = source.get('seating_summary')
        summary = summary if isinstance(summary, dict) else {}
        standing_only = summary.get('mode') == 'standing_only'
        semantic = not standing_only and _semantic_summary(summary)
        mode = ('standing_only' if standing_only else 'seated') if summary else self.mode
        if self.mode is not None and mode != self.mode:
            self.hold('The cabin detection mode changed. A fresh departure confirmation is required.')
        self.mode = mode
        frame = summary['posture_frame_id'] if semantic else source.get('frame_id')
        capture = summary.get('captured_at_ms')
        object_frame = source.get('frame_id')
        source_age, posture_age = source.get('age_ms'), summary.get('age_ms')
        if (not isinstance(frame, int) or isinstance(frame, bool) or frame < 0
                or not isinstance(object_frame, int) or isinstance(object_frame, bool) or object_frame < 0
                or not finite(capture) or capture < 0
                or not finite(source_age) or source_age < 0
                or not finite(posture_age) or posture_age < 0):
            self.hold('Cabin frame identity, timestamp or posture evidence is unavailable.')
            return
        if semantic and summary.get('source_session_id') != session:
            self.hold('Waiting for semantic posture that matches the current camera.')
            return
        self.fresh_seconds = 4. if semantic else self.FRESH_SECONDS
        if self.source_frame is None or object_frame > self.source_frame:
            self.source_frame = object_frame
            self.source_packet_age = source_age / 1000
        elif object_frame < self.source_frame:
            self.hold('Waiting for indoor person frames in increasing order.')
            return
        else:
            self.source_packet_age = max(source_age / 1000,
                                         self.source_packet_age + max(0, now - self.source_packet_at))
        self.source_packet_at = now
        if self.source_packet_age >= 1.:
            self.hold('Fresh person observations from the indoor camera are required.')
            return
        new_frame = self.last_frame is None or frame > self.last_frame
        if self.last_frame is not None and (frame < self.last_frame
                or frame == self.last_frame and capture != self.last_capture
                or new_frame and capture <= self.last_capture):
            self.hold('Waiting for cabin observations in increasing frame and timestamp order.')
            return
        if new_frame:
            self.last_frame, self.last_capture = frame, capture
            self.packet_at, self.packet_age = now, max(source_age, posture_age) / 1000
        age = max(self.packet_age + max(0, now - self.packet_at), source_age / 1000, posture_age / 1000)
        self.packet_at, self.packet_age = now, age

        if standing_only:
            self._update_standing(source, summary, now, capture, age, new_frame, new_cycle)
            return

        rows = summary.get('occupants')
        valid = isinstance(rows, list) and len(rows) <= 300
        rows = rows[:300] if isinstance(rows, list) else []
        by_id, totals = {}, dict(seated=0, standing=0, unknown=0)
        for row in rows:
            if not isinstance(row, dict):
                valid = False
                continue
            key, posture = track_key(row.get('track_id')), row.get('posture')
            if key is None or key in by_id or posture not in totals:
                valid = False
            if key is not None and key not in by_id:
                by_id[key] = posture
            totals[posture if posture in totals else 'unknown'] += 1
        totals['people'] = len(rows)
        valid = valid and all(counter(summary.get(key)) and summary[key] == totals[key] for key in totals)
        if counter(summary.get('people')):
            self.high_water = max(self.high_water, summary['people'])
        self.high_water = max(self.high_water, len(rows))
        for key in by_id:
            if key not in self.expected and len(self.expected) >= self.MAX_EXPECTED:
                self.overflow = True
                break
            self.expected.add(key)
        if age >= self.fresh_seconds or summary.get('status') != 'observed':
            self.hold('Cabin posture is stale or unavailable. Departure remains held.')
            return
        if totals['standing'] or summary.get('standing', 0):
            self.hold('Please take a seat. Standing posture was observed.', 'standing')
            return
        if semantic and summary.get('roster_verified') is not True:
            self.hold('Waiting for semantic posture that matches the complete current passenger roster.')
            return
        if self.overflow or self.expected - by_id.keys() or totals['people'] < self.high_water:
            self.hold('A previously observed passenger is no longer accounted for. Departure remains held.')
            return
        # The pose estimator deliberately marks an empty frame incomplete:
        # there is nobody whose seated posture it can establish. An explicitly
        # whole-cabin view may instead confirm emptiness, but only after the
        # roster/floor checks above and the stable literal-person zero below.
        empty_cabin = allow_empty and valid and totals['people'] == 0
        if (not valid or (summary.get('complete') is not True and not empty_cabin)
                or summary.get('capacity_reached') is True or totals['unknown']
                or (totals['people'] == 0 and not allow_empty)
                or totals['seated'] != totals['people']):
            self.hold('Every observed passenger needs a unique, fresh track and a clear seated posture.')
            return
        # Object totals can include walking aids and bags. Only the unambiguous
        # literal person category may validate the posture population.
        classes = (source.get('count_summary') or {}).get('classes') or {}
        person_counts = [value for key, value in classes.items() if str(key).strip().casefold() in {'person', 'persons'}]
        count = person_counts[0] if len(person_counts) == 1 and isinstance(person_counts[0], dict) else None
        if (not count or count.get('status') != 'stable' or not counter(count.get('stable'))
                or count['stable'] != totals['people']):
            self.hold('Waiting for a stable person count that matches every visible posture track.')
            return
        received = source.get('received_at_ms')
        observed_at = now - age
        if finite(received):
            observed_at = min(observed_at, received / 1000)
        if new_cycle or observed_at <= self.closed_at:
            self.hold('Waiting for new cabin observations captured after the doors closed.')
            return
        if not new_frame:
            return  # Repeated polls never accumulate confirmation time.
        roster = tuple(sorted(by_id))
        capture_gap = None if self.confirmed_capture is None else (capture - self.confirmed_capture) / 1000
        receipt_gap = None if self.confirmed_at is None else now - self.confirmed_at
        if (capture_gap is not None and 0 < capture_gap < self.fresh_seconds
                and receipt_gap is not None and 0 < receipt_gap < self.fresh_seconds
                and roster == self.confirmed_roster):
            # A manipulated/fast-forwarded capture clock cannot manufacture
            # three seconds of actual fresh observations on the server.
            self.progress += min(capture_gap, receipt_gap)
            self.confirmed_samples += 1
        else:
            self.progress = 0.
            self.confirmed_samples = 1
        self.confirmed_capture, self.confirmed_at, self.confirmed_roster = capture, now, roster
        ready = self.progress + 1e-8 >= self.confirmation_seconds and (not semantic or self.confirmed_samples >= 3)
        self.status = 'ready' if ready else 'confirming'
        self.message = (('The whole-cabin view is confirmed empty. Simulated departure may proceed.' if ready else
                         'Confirming an empty whole-cabin view for three seconds of fresh observations.') if empty_cabin else
                        ('Observed passengers are seated. Simulated departure may proceed.' if ready else
                         'Confirming everyone is seated for three seconds of fresh cabin observations.'))

    def _update_standing(self, source, summary, now, capture, age, new_frame, new_cycle):
        # Generic-person tracking and historical/RFID occupancy are independent
        # of a successful standing-only pass. Missing or changing IDs must not
        # turn a valid negative into a seated-roster requirement.
        self.expected.clear()
        self.high_water = summary['people'] if counter(summary.get('people')) else 0
        self.overflow = False
        if age >= self.fresh_seconds or summary.get('status') != 'observed':
            self.hold('Standing detection is stale or unavailable. Departure remains held.')
            return
        rows = summary.get('occupants')
        valid = isinstance(rows, list) and len(rows) <= 300
        rows = rows if valid else []
        totals = dict(standing=0, not_detected=0, unknown=0)
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get('posture'), str)
                    or row['posture'] not in totals):
                valid = False
                continue
            totals[row['posture']] += 1
        totals['people'] = len(rows)
        valid = valid and all(counter(summary.get(key)) and summary[key] == value
                              for key, value in totals.items())
        if totals['standing'] or (counter(summary.get('standing')) and summary['standing'] > 0):
            self.hold('Please take a seat. Standing posture was observed.', 'standing')
            return
        if (not valid or summary.get('standing_check_valid') is not True
                or summary.get('clear') is not True or summary.get('seated') is not None
                or summary.get('capacity_reached') is True
                or not counter(summary.get('standing_detection_count'))
                or summary['standing_detection_count'] != 0
                or (summary.get('source_session_id') is not None
                    and summary['source_session_id'] != self.session)):
            self.hold('A valid fresh standing detection result is required. Departure remains held.')
            return
        observed_at = now - age
        received = source.get('received_at_ms')
        if finite(received):
            observed_at = min(observed_at, received / 1000)
        if new_cycle or observed_at <= self.closed_at:
            self.hold('Waiting for new cabin observations captured after the doors closed.')
            return
        if not new_frame:
            return
        capture_gap = None if self.confirmed_capture is None else (capture - self.confirmed_capture) / 1000
        receipt_gap = None if self.confirmed_at is None else now - self.confirmed_at
        if (capture_gap is not None and 0 < capture_gap < self.fresh_seconds
                and receipt_gap is not None and 0 < receipt_gap < self.fresh_seconds):
            self.progress += min(capture_gap, receipt_gap)
            self.confirmed_samples += 1
        else:
            self.progress = 0.
            self.confirmed_samples = 1
        self.confirmed_capture, self.confirmed_at = capture, now
        ready = self.progress + 1e-8 >= self.STANDING_CONFIRM_SECONDS
        self.status = 'ready' if ready else 'confirming'
        self.message = ('No standing detected. Simulated departure may proceed.' if ready else
                        'No standing detected. Confirming five continuous seconds of fresh cabin observations.')

    def result(self, now):
        source_stale = (self.source_packet_at is not None and
                        self.source_packet_age + max(0, now - self.source_packet_at) >= 1.)
        if (self.packet_at is None or self.packet_age + max(0, now - self.packet_at) >= self.fresh_seconds
                or source_stale):
            self.hold('Fresh live cabin posture is required. Departure remains held.')
        return {'can_depart': self.status == 'ready', 'status': self.status, 'message': self.message,
                'mode': self.mode,
                'progress_ms': min(round(self.confirmation_seconds * 1000), round(self.progress * 1000)),
                'confirmation_ms': round(self.confirmation_seconds * 1000),
                'observations': self.confirmed_samples, 'max_age_ms': round(self.fresh_seconds * 1000),
                'expected_people': max(self.high_water, len(self.expected))}
