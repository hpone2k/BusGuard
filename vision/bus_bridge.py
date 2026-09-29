"""Small, frame-free bridge from current inference sessions to Bus Studio.

This emits assistance requests, never commands to a physical ramp or vehicle.
Freshness uses server receipt time and a monotonic clock, not a camera's clock.
"""

import copy
import re
import threading
import time
import uuid
from collections import OrderedDict, deque

from .counting import age_summary
from .seating import age_seating_summary, asynchronous_posture


ALIASES = {
    'wheelchair': {'wheelchair', 'wheelchairs', 'wheelchair user', 'person in a wheelchair',
                   'person using a wheelchair', 'person with wheelchair'},
    'pram': {'pram', 'prams', 'stroller', 'strollers', 'baby stroller', 'baby pram', 'pushchair'},
    'walking': {'walker', 'walking frame', 'walking aid', 'walking stick', 'cane', 'crutch',
                'crutches', 'rollator', 'person with a walking stick', 'person using a walker'},
}
TYPE_FOR_LABEL = {label: kind for kind, labels in ALIASES.items() for label in labels}


def assistance_type(label):
    normalized = re.sub(r'[\s_-]+', ' ', str(label).strip().casefold())
    return TYPE_FOR_LABEL.get(normalized)


class VisionBusBridge:
    LIVE_TTL = 2.5
    COUNT_TTL = 1.5
    IMAGE_TTL = 10.0
    RAW_COOLDOWN = 15.0
    RAW_ABSENCE = 3.0

    def __init__(self, monotonic=time.monotonic, wall_time=time.time):
        self.monotonic, self.wall_time = monotonic, wall_time
        self.instance_id = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.revision = 0
        self.event_id = 0
        self.records = {}
        self.roles = {'outside': None, 'inside': None}
        self.events = deque(maxlen=30)
        # Bounded deduplication survives source setting changes and client retries.
        self.rfid_seen = OrderedDict()
        self.raw_last_event = {}

    def receipt(self):
        return self.monotonic(), self.wall_time() * 1000

    def register(self, session):
        with self.lock:
            record = {
                'role': session.source_role, 'session_id': session.id,
                'source_name': session.source_name or {
                    'outside': 'Outside camera', 'inside': 'Inside camera',
                    'unassigned': 'Unassigned source'}[session.source_role],
                'kind': session.kind, 'frame_id': None, 'received_at_ms': None,
                'detections': [], 'count_summary': None, 'inference_ms': None, 'tracking_ms': None,
                'seating_summary': None, 'seating_ms': None,
                'posture_enabled': bool(getattr(getattr(session, 'options', None), 'posture_enabled', False)),
                'queue_ms': None, 'server_ms': None, 'is_demo': False,
                'detection_engine': None, 'prompt_mode': getattr(getattr(session, 'options', None), 'mode', None),
                '_received': None, '_closed': False, '_fresh': False,
                '_candidates': {}, '_emitted': OrderedDict(), '_sequence': 0,
            }
            self.records[session.id] = record
            if session.source_role in self.roles:
                previous = self.roles[session.source_role]
                if previous:
                    previous['_fresh'] = False
                    previous['_candidates'].clear()
                    previous['detections'] = []
                self.roles[session.source_role] = record
            self.revision += 1

    def disconnect(self, session_id):
        with self.lock:
            record = self.records.pop(session_id, None)
            if record:
                record['_closed'] = True
                record['_fresh'] = False
                record['detections'] = []
                record['_candidates'].clear()
                self.revision += 1

    def publish(self, session, result, received=None, backend=None):
        # Deletion cannot pass this check and then allow a late result to publish.
        with session.lock:
            session.check(result['frame_id'])
            with self.lock:
                record = self.records.get(session.id)
                if (not record or record['role'] not in self.roles
                        or self.roles[record['role']] is not record):
                    return False
                if record['frame_id'] is not None and result['frame_id'] <= record['frame_id']:
                    return False
                receipt_mono, receipt_wall = received or self.receipt()
                record.update({key: result.get(key) for key in (
                    'frame_id', 'inference_ms', 'tracking_ms', 'seating_ms', 'queue_ms', 'server_ms',
                    'detection_engine', 'prompt_mode')})
                record['received_at_ms'] = round(receipt_wall, 1)
                record['_received'] = receipt_mono
                record['is_demo'] = bool((backend or {}).get('demo', False))
                record['_sequence'] += 1
                record['detections'] = copy.deepcopy(result.get('detections', [])[:300])
                record['count_summary'] = copy.deepcopy(result.get('count_summary'))
                record['seating_summary'] = copy.deepcopy(result.get('seating_summary'))
                posture = record['seating_summary']
                if asynchronous_posture(posture):
                    published_at = self.monotonic()
                    posture_key = (posture.get('source_session_id'), posture.get('door_generation'),
                                   posture['posture_frame_id'], posture.get('captured_at_ms'))
                    age = max(0, posture.get('age_ms') or 0)
                    if posture_key == record.get('_posture_key'):
                        age = max(age, record['_posture_age'] +
                                  max(0, published_at - record['_posture_published']) * 1000)
                    record['_posture_key'] = posture_key
                    record['_posture_age'], record['_posture_published'] = age, published_at
                    record['seating_summary'] = age_seating_summary(posture, age)
                else:
                    record['_posture_key'] = None
                self._expire(record, self.monotonic())
                if record['_fresh'] and record['role'] == 'outside' and not record['is_demo']:
                    self._observe(record)
                self.revision += 1
                return True

    def _expire(self, record, now):
        ttl = self.IMAGE_TTL if record['kind'] == 'image' else self.LIVE_TTL
        assigned = self.roles.get(record['role']) is record
        fresh = (assigned and not record['_closed'] and record['_received'] is not None
                 and 0 <= now - record['_received'] <= ttl)
        if record['_fresh'] and not fresh:
            self.revision += 1
        record['_fresh'] = fresh
        if not fresh:
            record['detections'] = []
            record['_candidates'].clear()

    def _observe(self, record):
        now, sequence = record['_received'], record['_sequence']
        observed = {}
        for detection in record['detections']:
            kind = assistance_type(detection.get('label'))
            if not kind or detection.get('predicted', False):
                continue
            track_id = detection.get('track_id')
            # Untracked boxes use one presence episode per type, not unstable geometry.
            key = (kind, track_id)
            previous = observed.get(key)
            if previous is None or (detection.get('score') or 0) > (previous.get('score') or 0):
                observed[key] = detection
        candidates = record['_candidates']
        for key, candidate in list(candidates.items()):
            if key not in observed:
                candidate['hits'] = 0
                if now - candidate['last_seen'] >= self.RAW_ABSENCE:
                    del candidates[key]
        for key, detection in observed.items():
            kind, track_id = key
            previous = candidates.get(key)
            if previous and now - previous['last_seen'] >= self.RAW_ABSENCE:
                previous = None
            consecutive = (previous and previous['sequence'] == sequence - 1
                           and now - previous['last_seen'] <= self.LIVE_TTL)
            hits = previous['hits'] + 1 if consecutive else 1
            eligible = not previous or not previous.get('emitted')
            candidates[key] = {'hits': hits, 'sequence': sequence, 'last_seen': now,
                               'emitted': not eligible}
            image = record['kind'] == 'image'
            if not image and hits < 2:
                continue
            if key in record['_emitted'] and (image or track_id is not None):
                continue
            if not image and track_id is None:
                if not eligible or now - self.raw_last_event.get(kind, -float('inf')) < self.RAW_COOLDOWN:
                    continue
                self.raw_last_event[kind] = now
            self._event(type=kind, origin='vision', source_role=record['role'],
                        source_name=record['source_name'], source_kind=record['kind'],
                        session_id=record['session_id'], frame_id=record['frame_id'],
                        timestamp_ms=record['received_at_ms'], confidence=detection.get('score'),
                        track_id=track_id, is_demo=record['is_demo'])
            candidates[key]['emitted'] = True
            record['_emitted'][key] = True
            if len(record['_emitted']) > 1024:
                record['_emitted'].popitem(last=False)
        # At most the current detector output and short-lived candidates are retained.
        if len(candidates) > 600:
            for key in sorted(candidates, key=lambda k: candidates[k]['last_seen'])[:len(candidates)-600]:
                del candidates[key]

    def _event(self, **values):
        self.event_id += 1
        event = {'id': self.event_id, **values}
        self.events.append(event)
        self.revision += 1
        return event

    def rfid(self, request):
        with self.lock:
            existing = self.rfid_seen.get(request.event_id)
            if existing:
                if existing['type'] != request.passenger_type:
                    raise ValueError('This RFID event ID was already used for a different passenger type.')
                return {'event': copy.deepcopy(existing), 'duplicate': True}
            event = self._event(type=request.passenger_type, origin='rfid', source_role='inside',
                                source_name=request.source_name or 'RFID reader', source_kind='rfid',
                                session_id=None, frame_id=None, timestamp_ms=round(self.wall_time() * 1000, 1),
                                confidence=None, track_id=None, is_demo=False, event_id=request.event_id)
            self.rfid_seen[request.event_id] = event
            if len(self.rfid_seen) > 1024:
                self.rfid_seen.popitem(last=False)
            return {'event': copy.deepcopy(event), 'duplicate': False}

    def _public_source(self, role, record, now):
        if record is None:
            return {'role': role, 'session_id': None, 'source_name': None, 'kind': None,
                    'connected': False, 'status': 'disconnected', 'age_ms': None,
                    'received_at_ms': None, 'frame_id': None, 'detections': [], 'count_summary': None,
                    'seating_summary': None, 'seating_ms': None,
                    'inference_ms': None, 'tracking_ms': None, 'queue_ms': None,
                    'server_ms': None, 'is_demo': False}
        self._expire(record, now)
        source = {key: value for key, value in record.items() if not key.startswith('_')}
        source['connected'] = record['_fresh']
        source['age_ms'] = (round(max(0, now - record['_received']) * 1000, 1)
                            if record['_received'] is not None else None)
        assigned = self.roles.get(role) is record
        source['status'] = ('disconnected' if record['_closed'] or not assigned else
                            'connected' if record['_fresh'] else
                            'waiting' if record['_received'] is None else 'stale')
        source = copy.deepcopy(source)
        summary = source['count_summary']
        if summary is not None:
            # Only completed frame intervals constitute votes. Polling merely
            # ages a frozen summary using server receipt time, never camera time.
            age = source['age_ms'] or 0
            summary = age_summary(summary, age, self.COUNT_TTL * 1000)
            summary['age_ms'] = max(summary.get('age_ms') or 0, age)
            if not record['_fresh']:
                for estimate in [summary['total'], *summary['classes'].values()]:
                    estimate.update(raw=None, stable=None, candidate=None, support=None,
                                    status='stale', distribution=[], held_age_ms=None, held_remaining_ms=0)
                summary['status'] = 'stale'
            source['count_summary'] = summary
        posture_age = source['age_ms'] or 0
        if asynchronous_posture(source['seating_summary']) and record.get('_posture_key') is not None:
            posture_age = max(posture_age, record['_posture_age'] +
                              max(0, now - record['_posture_published']) * 1000)
        source['seating_summary'] = age_seating_summary(source['seating_summary'], posture_age,
                                                       force_stale=not record['_fresh'])
        return source

    def snapshot(self):
        with self.lock:
            now = self.monotonic()
            sources = {role: self._public_source(role, record, now) for role, record in self.roles.items()}
            return {'instance_id': self.instance_id, 'revision': self.revision,
                    'server_time_ms': round(self.wall_time() * 1000, 1),
                    'sources': sources, 'events': copy.deepcopy(list(self.events))}

    def source_list(self):
        with self.lock:
            now = self.monotonic()
            return {'sources': [{**self._public_source(record['role'], record, now),
                                 'source_role': record['role']} for record in self.records.values()]}
