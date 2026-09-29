import hashlib
import copy
import threading
import time
import uuid
from collections import OrderedDict, Counter
from dataclasses import dataclass, field

from .counting import RollingCountVoter, age_summary, snapshot_counts
from .scheduler import BusyError, SupersededError
from .seating import age_seating_summary, asynchronous_posture, posture_ttl_ms, unique_matches
from .departure import track_key, finite
from .schema import iou
from .semantic_seating import estimate as semantic_seating
from .standing import estimate as standing_evidence
from .tracking import StableTracker
from .detection_gate import DetectionGate


POSTURE_COUNT_LABELS = ('standing person', 'sitting person', 'uncertain posture')
POSTURE_COUNT_MAP = dict(standing='standing person', seated='sitting person', unknown='uncertain posture')


@dataclass
class Session:
    options: object
    kind: str
    source_role: str = 'unassigned'
    source_name: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    touched: float = field(default_factory=time.monotonic)
    latest: int = -1
    closed: threading.Event = field(default_factory=threading.Event)
    tracker: object = None
    count_voter: object = None
    posture_voter: object = None
    posture_generation: object = None
    posture_backend: object = field(default=None, repr=False)
    lock: object = field(default_factory=threading.RLock)
    detection_generation: object = None

    def accept(self, frame_id):
        with self.lock:
            if self.closed.is_set() or frame_id <= self.latest:
                raise SupersededError("Old frame or closed session.")
            self.latest = frame_id
            self.touched = time.monotonic()

    def check(self, frame_id):
        if self.closed.is_set() or frame_id != self.latest:
            raise SupersededError("A newer frame or source replaced this request.")


class Sessions:
    def __init__(self, settings, dispatcher, bus_bridge=None, detection_gate=None):
        self.settings = settings
        self.dispatcher = dispatcher
        self.bus_bridge = bus_bridge
        self.detection_gate = detection_gate or DetectionGate()
        self.items = {}
        self.lock = threading.RLock()
        # Accessed only by the single GPU thread. Store normalized detections, not images.
        self.image_cache = OrderedDict()

    def create(self, options, kind, source_role='unassigned', source_name=None):
        options.categories()
        with self.lock:
            self.cleanup()
            if len(self.items) >= self.settings.max_sessions:
                raise BusyError("Too many active sources. Stop an existing source first.")
            session = Session(options, kind, source_role=source_role, source_name=source_name)
            if kind in {'live', 'video'}:
                session.tracker = StableTracker(options)
                session.count_voter = RollingCountVoter(options.categories())
                if source_role == 'inside':
                    session.posture_voter = RollingCountVoter(POSTURE_COUNT_LABELS, stale_ms=1000)
            self.items[session.id] = session
            if self.bus_bridge:
                self.bus_bridge.register(session)
            return session

    def get(self, id):
        with self.lock:
            session = self.items.get(id)
            if session is None or session.closed.is_set():
                raise KeyError("Session expired. Start detection again.")
            session.touched = time.monotonic()
            return session

    def delete(self, id):
        with self.lock:
            session = self.items.pop(id, None)
            if session:
                with session.lock:
                    session.closed.set()
                    self._invalidate_posture(session)
                if self.bus_bridge:
                    self.bus_bridge.disconnect(id)
            self.dispatcher.cancel("session:" + id)

    def cleanup(self):
        with self.lock:
            for id, session in list(self.items.items()):
                if time.monotonic() - session.touched > self.settings.session_ttl:
                    self.delete(id)

    def infer(self, session, frame, frame_id, timestamp, digest, backend, canceled, queue_ms,
              generation=None, posture_token=None):
        session.check(frame_id)
        generation = self.detection_gate.check(generation, session.source_role)
        if posture_token is None:
            posture_token = self.detection_gate.posture_token()
        if session.detection_generation != generation:
            # A fresh stop must build new tracks/count votes, never reuse an old
            # stopped-bus observation as evidence for a new boarding cycle.
            if session.kind in {'live', 'video'}:
                session.tracker = StableTracker(session.options)
                session.count_voter = RollingCountVoter(session.options.categories())
                session.posture_voter = None
                session.posture_generation = None
            session.detection_generation = generation
        if canceled.is_set():
            raise SupersededError("Canceled.")
        key = (digest, session.options.model_dump_json())
        start = time.perf_counter()
        cached = session.kind == "image" and key in self.image_cache
        if cached:
            detections = self.image_cache[key]
            self.image_cache.move_to_end(key)
        else:
            options = session.tracker.detection_options if session.tracker else session.options
            detections = backend.detect(frame, options)
            if session.kind == "image":
                self.image_cache[key] = detections
                if len(self.image_cache) > 24:
                    self.image_cache.popitem(last=False)
        infer_ms = (time.perf_counter() - start) * 1000
        self.detection_gate.check(generation, session.source_role)
        session.check(frame_id)
        if canceled.is_set():
            raise SupersededError("Canceled.")
        track_start = time.perf_counter()
        raw_detections = detections
        if session.kind in {'live', 'video'}:
            detections = session.tracker.update(detections, timestamp / 1000, frame)
        tracking_ms = (time.perf_counter() - track_start) * 1000
        seating_summary, seating_ms = None, None
        if session.source_role == 'inside':
            session.check(frame_id)
            if canceled.is_set():
                raise SupersededError('Canceled before posture analysis.')
            seating_start = time.perf_counter()
            self.detection_gate.check(generation, session.source_role)
            estimate = getattr(backend, 'estimate_seating', None)
            session.posture_backend = backend
            if not session.options.posture_enabled:
                self._invalidate_posture(session)
                seating_summary = standing_evidence(
                    detections, raw_detections, [], timestamp, False,
                    'Posture detection is switched off for this source.')
                seating_summary['status'] = 'disabled'
            elif not self.detection_gate.posture_allowed(posture_token):
                self._invalidate_posture(session)
                seating_summary = self._paused_posture(detections, timestamp)
            elif session.options.mode == 'phrase':
                self._invalidate_posture(session)
                # Referring expressions observe subsets, never a cabin census.
                # Avoid an unnecessary pose pass and keep departure held.
                seating_summary = standing_evidence(
                    detections, raw_detections, [], timestamp, False,
                    'Detailed phrases select passenger subsets. Use Object categories with person for cabin seating evidence.')
            elif not any(label.casefold() in {'person', 'persons'} for label in session.options.categories()):
                self._invalidate_posture(session)
                seating_summary = standing_evidence(
                    detections, raw_detections, [], timestamp, False,
                    'Add person to Object categories to enable posture detection.')
            else:
                if estimate and getattr(backend, 'posture_async', False):
                    image_estimate = getattr(backend, 'estimate_seating_image', None) if session.kind == 'image' else None
                    call = image_estimate or estimate
                    seating_summary = call(frame, detections, raw_detections, timestamp,
                                           context=(session.id, posture_token[1]),
                                           input_age_ms=max(0, queue_ms) + infer_ms + tracking_ms)
                    seating_summary = self._current_semantic_posture(
                        seating_summary, session, detections, raw_detections, timestamp, posture_token)
                elif estimate and getattr(backend, 'posture_options', False):
                    seating_summary = estimate(frame, detections, raw_detections, timestamp,
                                               confidence=session.options.standing_confidence)
                else:
                    seating_summary = (estimate(frame, detections, raw_detections, timestamp) if estimate else
                                       standing_evidence(detections, raw_detections, [], timestamp, False,
                                                        'This detector does not provide posture evidence.'))
            seating_ms = round((time.perf_counter() - seating_start) * 1000, 1)
            session.check(frame_id)
            if canceled.is_set():
                raise SupersededError('Canceled during posture analysis.')
            if session.options.posture_enabled and not self.detection_gate.posture_allowed(posture_token):
                self._invalidate_posture(session)
                seating_summary = self._paused_posture(detections, timestamp)
            elif seating_summary is not None:
                seating_summary['door_generation'] = posture_token[1]
        self.detection_gate.check(generation, session.source_role)
        return {"session_id": session.id, "frame_id": frame_id, "captured_at": timestamp,
                '_detection_generation': generation,
                '_posture_token': posture_token,
                'detection_engine': (backend.engine_for(session.options) if hasattr(backend, 'engine_for')
                                     else getattr(backend, 'info', {}).get('name')),
                'prompt_mode': session.options.mode,
                'posture_enabled': session.options.posture_enabled,
                "detections": [d.json() for d in detections],
                'seating_summary': seating_summary, 'seating_ms': seating_ms,
                '_posture_age_checked_at': time.monotonic(),
                "width": frame.shape[1], "height": frame.shape[0], "cached": cached,
                "inference_ms": round(infer_ms, 1), "tracking_ms": round(tracking_ms, 1), "queue_ms": round(queue_ms, 1)}

    def finalize(self, session, result, canceled, received=None, backend=None):
        """Count accepted observations once, immediately before publication.

        Canceled and superseded work must not become temporal evidence. The same
        commit path is used by browser frames and the RTSP capture workers.
        """
        with session.lock:
            session.check(result['frame_id'])
            self.detection_gate.check(result.get('_detection_generation'), session.source_role)
            if canceled.is_set():
                raise SupersededError('Canceled before publication.')
            if (session.source_role == 'inside' and session.options.posture_enabled
                    and not self.detection_gate.posture_allowed(result.get('_posture_token'))):
                self._invalidate_posture(session)
                # Object/count observations remain useful across door transitions;
                # posture from an earlier closed/open epoch never grants clearance.
                result['seating_summary'] = self._paused_posture(result['detections'], result['captured_at'])
            if session.kind in {'live', 'video'}:
                previous_capture = session.count_voter.last_observed_ms
                if previous_capture is not None and result['captured_at'] <= previous_capture:
                    raise SupersededError('Frame capture timestamps must increase. Restart this detection source.')
                result['count_summary'] = session.count_voter.observe(
                    result['detections'], result['captured_at'])
                if received is not None:
                    now = self.bus_bridge.monotonic() if self.bus_bridge else time.monotonic()
                    result['count_summary'] = age_summary(result['count_summary'],
                                                          max(0, now - received[0]) * 1000)
            else:
                result['count_summary'] = snapshot_counts(result['detections'], session.options.categories())
            # A still image keeps its matching boxes for inspection even
            # after a slow cold inference. It is never live posture evidence;
            # the shared bridge separately ages its public image observation.
            if session.kind != 'image' and result.get('seating_summary') is not None and received is not None:
                now = self.bus_bridge.monotonic() if self.bus_bridge else time.monotonic()
                result['seating_summary'] = age_seating_summary(result['seating_summary'],
                                                                max(0, now - received[0]) * 1000)
            summary = result.get('seating_summary')
            checked_at = result.pop('_posture_age_checked_at', None)
            if session.kind != 'image' and asynchronous_posture(summary) and checked_at is not None:
                elapsed = max(0, time.monotonic() - checked_at) * 1000
                result['seating_summary'] = age_seating_summary(summary, (summary.get('age_ms') or 0) + elapsed)
            self._posture_counts(session, result)
            if self.bus_bridge:
                result.pop('_detection_generation', None)
                result.pop('_posture_token', None)
                return self.bus_bridge.publish(session, result, received, backend)
            result.pop('_detection_generation', None)
            result.pop('_posture_token', None)
            return True

    @staticmethod
    def _invalidate_posture(session):
        invalidate = getattr(session.posture_backend, 'invalidate_seating', None)
        if invalidate is not None:
            invalidate(session.id)

    def _current_semantic_posture(self, summary, session, tracked, raw, timestamp, token):
        """Never attach an older semantic result to changed live passengers."""
        summary = copy.deepcopy(summary) if isinstance(summary, dict) else {}
        if summary.get('status') != 'observed':
            return summary or semantic_seating(tracked, raw, [], timestamp, False,
                                               'Waiting for LocateAnything posture inference.')
        valid_context = (asynchronous_posture(summary) and summary['source_session_id'] == session.id
                         and summary.get('door_generation') == token[1]
                         and finite(summary.get('captured_at_ms')) and summary['captured_at_ms'] >= 0
                         and finite(summary.get('age_ms')) and summary['age_ms'] >= 0)
        if not valid_context:
            result = self._paused_posture(tracked, timestamp)
            result.update(status='awaiting_fresh_frame',
                          reason='Waiting for posture captured in this camera session and closed-door cycle.')
            return result
        if session.kind == 'image':
            return summary  # Test images never authorize live departure.
        summary = age_seating_summary(summary, summary.get('age_ms') or 0)
        if summary['status'] != 'observed':
            return summary
        people = []
        for row in tracked:
            get = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
            if str(get('label', '')).strip().casefold() in {'person', 'persons'}:
                people.append(dict(track_id=get('track_id'), bbox=get('bbox', []), predicted=get('predicted', False)))
        raw_people = []
        for row in raw:
            get = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
            if str(get('label', '')).strip().casefold() in {'person', 'persons'}:
                raw_people.append(dict(bbox=get('bbox', []), predicted=get('predicted', False)))
        rows = summary.get('occupants') or []
        roster = {track_key(row.get('track_id')): row for row in rows if isinstance(row, dict)}
        current = {track_key(row['track_id']): row for row in people}
        matches = unique_matches(people, raw_people, .5)
        consistent = (None not in roster and None not in current and len(roster) == len(rows)
                      and len(current) == len(people) and roster.keys() == current.keys()
                      and len(matches) == len(people) == len(raw_people)
                      and all(not row['predicted'] for row in people + raw_people))
        if consistent:
            for identity, person in current.items():
                box = roster[identity].get('bbox')
                if not isinstance(box, (list, tuple)) or len(box) != 4 or iou(person['bbox'], box) < .75:
                    consistent = False
                    break
        if consistent:
            summary['roster_verified'] = True
            return summary
        # Preserve individually verified labels even when another passenger is
        # unresolved. A partial observation can explain a standing reminder,
        # but never grants the whole-roster verification needed for departure.
        old_ids = Counter(track_key(row.get('track_id')) for row in rows if isinstance(row, dict))
        current_ids = Counter(track_key(row['track_id']) for row in people)
        occupants = []
        for index, person in enumerate(people):
            identity = track_key(person['track_id'])
            previous = roster.get(identity) or {}
            box = previous.get('bbox')
            verified = (identity is not None and current_ids[identity] == old_ids[identity] == 1
                        and not person['predicted'] and index in matches
                        and not raw_people[matches[index]]['predicted']
                        and isinstance(box, (list, tuple)) and len(box) == 4
                        and iou(person['bbox'], box) >= .75)
            if verified:
                occupant = copy.deepcopy(previous)
                occupant['bbox'] = list(person['bbox'])
                occupant['posture'] = occupant.get('posture') if occupant.get('posture') in POSTURE_COUNT_MAP else 'unknown'
            else:
                occupant = dict(track_id=person['track_id'], bbox=person['bbox'], posture='unknown',
                                reason='This passenger identity or position differs from the analysed frame.')
            occupants.append(occupant)
        def overlaps(box, other, threshold=.5):
            return (isinstance(box, (list, tuple)) and len(box) == 4
                    and isinstance(other, (list, tuple)) and len(other) == 4
                    and iou(box, other) >= threshold)
        for person in raw_people:
            if not any(overlaps(person['bbox'], row['bbox']) for row in occupants):
                occupants.append(dict(track_id=None, bbox=person['bbox'], posture='unknown',
                                      reason='A currently detected person is not yet uniquely tracked.'))
        for previous in rows:
            if not isinstance(previous, dict):
                continue
            identity = track_key(previous.get('track_id'))
            if identity is not None and identity in current:
                continue
            box = previous.get('bbox', [])
            if not any(overlaps(box, row['bbox']) for row in occupants):
                occupants.append(dict(track_id=None, bbox=box, posture='unknown',
                                      reason='A person in the analysed frame remains unresolved in the current view.'))
        overflow = len(occupants) > 300
        occupants = occupants[:300]
        totals = Counter(row['posture'] for row in occupants)
        summary.update(status='observed', complete=False, roster_verified=False,
                       people=len(occupants), seated=totals['seated'], standing=totals['standing'],
                       unknown=totals['unknown'], occupants=occupants,
                       capacity_reached=bool(summary.get('capacity_reached')) or overflow,
                       reason='Some current passengers are unresolved; individually matched posture labels are shown.')
        return summary

    def _posture_counts(self, session, result):
        """Vote accepted posture rows separately from ordinary object/person counts.

        Unresolved people contribute to the uncertain class. These display votes
        never overwrite immediate posture evidence used by the departure gate.
        Opening doors, losing the model or stale inference discards the previous
        window, even when the ordinary object stream continues uninterrupted.
        """
        if session.source_role != 'inside':
            return
        summary = result.get('seating_summary')
        generation = None if not isinstance(summary, dict) else summary.get('door_generation')
        if (not session.options.posture_enabled or not isinstance(summary, dict)
                or summary.get('status') != 'observed'):
            session.posture_voter = None
            session.posture_generation = generation
            if isinstance(summary, dict):
                summary['count_summary'] = None
            return
        rows = summary.get('occupants') or []
        # Each row already represents an accounted or unresolved individual.
        # Do not deduplicate uncertain rows by a duplicated/missing track ID.
        standing_only = summary.get('mode') == 'standing_only'
        labels = ('standing person',) if standing_only else POSTURE_COUNT_LABELS
        observations = [dict(label=POSTURE_COUNT_MAP.get(row.get('posture'), 'uncertain posture'))
                        for row in rows[:300] if isinstance(row, dict)
                        and (not standing_only or row.get('posture') == 'standing')]
        if session.kind == 'image':
            summary['count_summary'] = snapshot_counts(observations, labels)
            return
        ttl = posture_ttl_ms(summary)
        if (session.posture_voter is None or session.posture_generation != generation
                or session.posture_voter.stale_ms != ttl):
            session.posture_voter = RollingCountVoter(labels, stale_ms=ttl, max_gap_ms=ttl)
        session.posture_generation = generation
        capture = summary.get('captured_at_ms') if asynchronous_posture(summary) else result['captured_at']
        if asynchronous_posture(summary) and capture == session.posture_voter.last_observed_ms:
            current_counts = Counter(row['label'] for row in observations)
            if any(current_counts[label] != session.posture_voter.raw_counts.get(label, 0) for label in POSTURE_COUNT_LABELS):
                # The same semantic frame can lose a person's verified match
                # while object frames advance. Discard the old display vote;
                # do not manufacture a new timed sample for this changed roster.
                session.posture_voter = RollingCountVoter(POSTURE_COUNT_LABELS, stale_ms=ttl, max_gap_ms=ttl)
        counts = session.posture_voter.observe(observations, capture)
        summary['count_summary'] = age_summary(counts, summary.get('age_ms') or 0, ttl)

    def _paused_posture(self, detections, timestamp):
        token = self.detection_gate.posture_token()
        reason = ('Posture checks begin after the doors are fully closed.' if not token[0]
                  else 'Waiting for a fresh frame captured after the doors closed.')
        summary = standing_evidence(detections, detections, [], timestamp, False, reason)
        summary.update(status='paused' if not token[0] else 'awaiting_fresh_frame',
                       door_generation=token[1])
        return summary


def image_digest(data):
    return hashlib.sha256(data).hexdigest()
