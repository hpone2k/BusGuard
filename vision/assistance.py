"""Authoritative assistance workflow for the local BusTech demonstration.

Door, ramp, travel and securement state are explicitly simulated. Passenger
confirmation is evidence of intent, never proof of physical clearance. This
module deliberately has no actuator driver or vehicle-control interface.
"""

import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from .departure import DepartureInterlock, counter
from .cabin_count import CabinCount
from .cabin_audit import CabinAudit
from .seating import posture_ttl_ms
from .scenario import StopScenario
from .rfid import RFIDRegistry
from . import route


STOPS = route.STOPS
NEEDS = [
    dict(id='ramp', label='Step-free access', description='Prepare the simulated ramp.'),
    dict(id='extra_time', label='More time', description='Allow a longer boarding or alighting interval.'),
    dict(id='audio', label='Audio guidance', description='Read progress messages aloud on your device.'),
    dict(id='visual', label='Visual guidance', description='Show clear, large progress messages.'),
    dict(id='priority_seat', label='Priority seating', description='Request access to a priority seat.'),
]
ACTIVE = {'accepted', 'queued', 'assisting', 'awaiting_completion'}
TERMINAL = {'completed', 'cancelled', 'error'}
LIVE_MAX_AGE_MS = 1000


class AssistanceError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class AssistanceController:
    MAX_ACTIVE = 100
    MAX_RECORDS = 500
    MOVE_SECONDS = .8
    BASE_DWELL = 3.
    EXTRA_DWELL = 8.

    def __init__(self, path=None, clock=time.time):
        self.path = Path(path) if path else None
        self.clock = clock
        self.lock = threading.RLock()
        self.requests = {}
        self.revision = 0
        self.vehicle = dict(stop_id='campus', motion='stationary', phase='idle',
                            doors='closed', ramp='stowed', obstruction=False,
                            emergency=False, revalidation_required=False,
                            wheelchair_aboard=False, wheelchair_secured=False,
                            mobility_request_balance=0,
                            simulation=True)
        self.deadline = None
        self.dwell_until = 0.
        self.posture_enabled = False
        self.seating = None
        self.departure = DepartureInterlock()
        self.cabin = CabinCount()
        self.cabin_audit = CabinAudit()
        self._door_state = self.vehicle['doors']
        self._door_epoch = 0
        self._doors_changed_at = self.clock()
        self._bridge_instance = None
        self._event_cursor = 0
        self._sensor_dedup = {}
        self._saved = None
        self.scenario = StopScenario(self, AssistanceError)
        self.rfid_error = AssistanceError
        self.rfid = RFIDRegistry(self)
        self._load()

    def config(self):
        return {'service': 'BusTech Demo', 'stops': copy.deepcopy(STOPS),
                'route': route.config(),
                'needs': copy.deepcopy(NEEDS), 'simulation': True,
                'notice': 'CCTV and RFID are real inputs. Vehicle movement, doors and ramp are simulated.',
                'max_active_requests': self.MAX_ACTIVE}

    def fail_hold(self):
        """Latch an in-memory hold even when the durable journal is unavailable."""
        with self.lock:
            self.vehicle.update(emergency=True, revalidation_required=True, motion='stationary')
            self.departure.reset()
            self._changed()

    @staticmethod
    def _hash(token):
        return hashlib.sha256(str(token).encode()).hexdigest()

    def _load(self):
        if not self.path or not self.path.exists():
            return
        try:
            stored = json.loads(self.path.read_text(encoding='utf-8'))
            if stored.get('version') != 1 or not isinstance(stored.get('requests'), dict):
                raise ValueError('Unknown assistance journal format.')
            records = dict(list(stored['requests'].items())[-self.MAX_RECORDS:])
            required = {'id', 'client_request_id', '_token_hash', 'journey', 'stop_id', 'needs',
                        'status', 'origin', 'created_at_ms', 'updated_at_ms', 'passenger_confirmed'}
            if any(not isinstance(item, dict) or not required.issubset(item)
                   or item['id'] != key or item['status'] not in ACTIVE | TERMINAL
                   or not isinstance(item['_token_hash'], str)
                   for key, item in records.items()):
                raise ValueError('Invalid assistance request journal.')
            self.requests = records
            if not isinstance(stored.get('vehicle', {}), dict):
                raise ValueError('Invalid vehicle journal.')
            self.vehicle.update(stored.get('vehicle', {}))
            if self.vehicle['stop_id'] not in route.STOP_IDS:
                raise ValueError('Invalid vehicle stop in journal.')
            self.revision = int(stored.get('revision', 0)) + 1
            self.posture_enabled = bool(stored.get('posture_enabled', False))
            self.cabin.restore(stored.get('cabin_count'))
            self.scenario.restore(stored.get('scenario'))
            self.rfid.restore(stored.get('rfid'))
        except (OSError, ValueError, TypeError):
            # Do not overwrite a damaged journal with an apparently clean bus.
            self.vehicle.update(emergency=True, revalidation_required=True,
                                phase='held', motion='stationary', doors='unknown', ramp='unknown')
            self._journal_error = True
            return
        self.vehicle.update(revalidation_required=True, phase='held', motion='stationary',
                            doors='unknown', ramp='unknown', wheelchair_secured=False)
        for item in self.requests.values():
            if item['status'] in ACTIVE:
                item.update(status='queued', passenger_confirmed=False)
        self._save()

    def _save(self):
        if not self.path or getattr(self, '_journal_error', False):
            return
        content = json.dumps({'version': 1, 'revision': self.revision,
                              'requests': self.requests, 'vehicle': self.vehicle,
                              'posture_enabled': self.posture_enabled,
                              'cabin_count': self.cabin.dump(),
                              'rfid': self.rfid.dump(),
                              'scenario': self.scenario.dump()}, sort_keys=True)
        if content == self._saved:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + '.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        self._saved = content

    def _changed(self):
        self._sync_door_transition()
        self.revision += 1

    def _sync_door_transition(self):
        if self.vehicle['doors'] != self._door_state:
            self._door_state = self.vehicle['doors']
            self._door_epoch += 1
            self._doors_changed_at = self.clock()
            self.seating = None
            self.departure.reset()

    def _status(self, item, status):
        if item['status'] != status:
            item.update(status=status, updated_at_ms=round(self.clock() * 1000))
            self._changed()

    def _active(self, current=False):
        return [item for item in self.requests.values() if item['status'] in ACTIVE
                and (not current or item['stop_id'] == self.vehicle['stop_id'])]

    def _validate_request(self, payload):
        expected = {'client_request_id', 'request_token', 'journey', 'stop_id', 'needs'}
        if not expected.issubset(payload) or set(payload) - expected - {'location'}:
            raise AssistanceError('Provide a request ID, private token, journey, stop and assistance needs.')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', str(payload['client_request_id'])):
            raise AssistanceError('Use a unique, plain-text client request ID.')
        if not re.fullmatch(r'[A-Za-z0-9_-]{24,128}', str(payload['request_token'])):
            raise AssistanceError('The private request token must contain 24 to 128 random characters.')
        if payload['journey'] not in {'boarding', 'alighting'}:
            raise AssistanceError('Choose boarding or alighting assistance.')
        if payload['stop_id'] not in {stop['id'] for stop in STOPS}:
            raise AssistanceError('Choose a stop from the demo route.')
        needs = payload['needs']
        if (not isinstance(needs, list) or not 1 <= len(needs) <= len(NEEDS)
                or any(not isinstance(need, str) or need not in {n['id'] for n in NEEDS} for need in needs)
                or len(set(needs)) != len(needs)):
            raise AssistanceError('Choose one assistance option for this request.')

    def create_request(self, payload):
        with self.lock:
            if getattr(self, '_journal_error', False):
                raise AssistanceError('The request journal needs operator repair before new requests can be saved.', 503)
            self._validate_request(payload)
            owner_hash = self._hash(payload['request_token'])
            previous = next((item for item in self.requests.values()
                             if item['client_request_id'] == payload['client_request_id']), None)
            if previous:
                if not hmac.compare_digest(previous['_token_hash'], owner_hash):
                    raise AssistanceError('This request ID is unavailable. Generate a new request ID.', 409)
                if (previous['journey'] != payload['journey'] or previous['stop_id'] != payload['stop_id']
                        or sorted(previous['needs']) != sorted(payload['needs'])):
                    raise AssistanceError('A retried request must contain the same assistance preferences.', 409)
                self._save()
                return self._private(previous)
            # Keep exact retries of requests accepted by older versions valid,
            # while every new passenger request has one clear assistance choice.
            # Camera-generated assistance is created separately and may combine needs.
            if len(payload['needs']) != 1:
                raise AssistanceError('Choose one assistance option for this request.')
            self._advance()
            location = payload.get('location')
            valid_location = isinstance(location, dict) and location.get('mode') in {'at_stop', 'onboard', 'away'}
            if not valid_location or (location['mode'] == 'at_stop'
                                      and (set(location) != {'mode', 'stop_id'} or location['stop_id'] not in route.STOP_IDS)) \
                    or (location['mode'] != 'at_stop' and set(location) != {'mode'}):
                raise AssistanceError('Choose your demonstration location before requesting assistance.')
            current = self.vehicle['stop_id']
            if self.vehicle['motion'] != 'stationary' or payload['stop_id'] != current:
                raise AssistanceError('Please wait until the bus is at your selected stop before requesting assistance.', 409)
            if payload['journey'] == 'boarding' and (location['mode'] != 'at_stop' or location.get('stop_id') != current):
                raise AssistanceError('Boarding assistance is available only at the stop where you and the bus are located.', 409)
            if payload['journey'] == 'alighting' and location['mode'] != 'onboard':
                raise AssistanceError('Choose On board in your demonstration location to request alighting assistance.', 409)
            if (self.vehicle['emergency'] or self.vehicle['revalidation_required']
                    or self.scenario.enabled and (self.vehicle['doors'] != 'open' or not self.scenario.admission_open())):
                raise AssistanceError('Requests are closed. Please wait for an open boarding window at this stop.', 409)
            if len(self._active()) >= self.MAX_ACTIVE:
                raise AssistanceError('The assistance queue is full. Please try again shortly.', 429)
            self._prune()
            self._advance()
            now = round(self.clock() * 1000)
            item = dict(id=secrets.token_hex(16), client_request_id=payload['client_request_id'],
                        _token_hash=owner_hash, journey=payload['journey'], stop_id=payload['stop_id'],
                        needs=sorted(payload['needs']), status='queued', origin='passenger',
                        created_at_ms=now, updated_at_ms=now, passenger_confirmed=False)
            item['_demo_location'] = copy.deepcopy(location)
            self.requests[item['id']] = item
            if (self.scenario.enabled and self.vehicle['motion'] == 'stationary'
                    and self.scenario.phase not in {'opening', 'braking'}
                    and item['stop_id'] == self.vehicle['stop_id'] and not self.scenario.admission_open()):
                item['scenario_message'] = ('Boarding and alighting requests for this stop have closed. '
                                            'Please select a later stop or ask the operator for assistance.')
                self._status(item, 'error')
            self._changed()
            self._advance()
            self._save()
            return self._private(item)

    def _prune(self):
        terminal = sorted((item for item in self.requests.values() if item['status'] in TERMINAL),
                          key=lambda item: item['updated_at_ms'])
        while len(self.requests) >= self.MAX_RECORDS and terminal:
            del self.requests[terminal.pop(0)['id']]

    def _owned(self, request_id, token):
        item = self.requests.get(request_id)
        if not item or not token or not hmac.compare_digest(item['_token_hash'], self._hash(token)):
            raise AssistanceError('This request is unavailable or its private token is incorrect.', 404)
        return item

    def get_request(self, request_id, token):
        with self.lock:
            item = self._owned(request_id, token)
            self._advance()
            self._save()
            return self._private(item)

    def complete_request(self, request_id, token):
        with self.lock:
            item = self._owned(request_id, token)
            self._advance()
            if item['status'] == 'completed' or item['passenger_confirmed']:
                return self._private(item)
            if (item['status'] != 'awaiting_completion' or self.vehicle['emergency']
                    or self.vehicle['revalidation_required']):
                raise AssistanceError('Please wait until assistance is ready before confirming completion.', 409)
            if self.scenario.enabled:
                self.scenario.complete(item, self.clock())
                self._advance()
                self._save()
                return self._private(item)
            item.update(passenger_confirmed=True, updated_at_ms=round(self.clock() * 1000))
            self._changed()
            self._advance()
            self._save()
            return self._private(item)

    def cancel_request(self, request_id, token):
        with self.lock:
            item = self._owned(request_id, token)
            if item['status'] == 'cancelled':
                return self._private(item)
            if item['status'] in TERMINAL:
                raise AssistanceError('This request has already finished.', 409)
            self._status(item, 'cancelled')
            item.pop('_scenario_reserved', None)
            if 'scenario_message' in item:
                item['scenario_message'] = 'Your assistance request has been cancelled. Any allocated seat is released.'
            self._advance()
            self._save()
            return self._private(item)

    def _private(self, item):
        status = item['status']
        messages = {'queued': 'Your request is saved. Waiting for the bus at your selected stop.',
                    'accepted': 'Your request has been accepted.',
                    'assisting': 'Preparing the simulated doors and step-free access. Please wait.',
                    'awaiting_completion': ('Thank you. Waiting for other passengers or the minimum assistance interval.'
                                            if item['passenger_confirmed'] else
                                            'Access is ready. Confirm only after you have safely ' +
                                            ('boarded.' if item['journey'] == 'boarding' else 'exited.')),
                    'completed': 'Your assistance request is complete. Thank you for travelling with us.',
                    'cancelled': 'Your assistance request has been cancelled.',
                    'error': 'Assistance needs operator attention.'}
        if status in ACTIVE and self.vehicle['emergency']:
            message = 'Emergency hold. Your request is saved; wait for the operator to reset the demonstration.'
        elif status in ACTIVE and self.vehicle['revalidation_required']:
            message = 'The server restarted. Your request is saved and is waiting for operator revalidation.'
        elif status in ACTIVE and self.vehicle['obstruction']:
            message = 'Doorway obstruction reported. Assistance remains held open.'
        else:
            message = item.get('scenario_message', messages[status])
        result = {key: copy.deepcopy(value) for key, value in item.items() if not key.startswith('_')}
        result.update(message=message, progress={'step': {'accepted': 0, 'queued': 1, 'assisting': 2,
                                                         'awaiting_completion': 3, 'completed': 4,
                                                         'cancelled': 0, 'error': 0}[status],
                                                  'total': 4, 'label': status.replace('_', ' ')},
                      can_complete=status == 'awaiting_completion' and not item['passenger_confirmed']
                                   and not self.vehicle['emergency'] and not self.vehicle['revalidation_required'],
                      can_cancel=status in ACTIVE)
        result['assistance_timer'] = self.scenario.request_clock(item)
        return result

    def control(self, action, stop_id=None, value=None):
        with self.lock:
            self._advance()
            v = self.vehicle
            if self.scenario.enabled and action in {'arrive', 'depart'}:
                if action == 'depart':
                    raise AssistanceError('Scenario departure is automatic after the boarding window, door closure and safety checks.', 409)
                self.scenario.control('stop', stop_id=stop_id)
                self._advance()
                self._save()
                return self._snapshot()
            if action == 'arrive':
                if stop_id not in {stop['id'] for stop in STOPS}:
                    raise AssistanceError('Choose a valid demo stop.')
                if stop_id == v['stop_id'] and v['motion'] == 'stationary':
                    # A repeated button press cannot reset assistance, posture
                    # confirmation or a held state at the current stop.
                    self._save()
                    return self._snapshot()
                if v['emergency'] or v['revalidation_required']:
                    raise AssistanceError('Reset the held demonstration before arriving.', 409)
                if v['motion'] != 'moving':
                    raise AssistanceError('Depart the simulation before arriving at a different stop.', 409)
                if any(item['status'] in {'assisting', 'awaiting_completion'} for item in self._active(True)):
                    raise AssistanceError('Complete current assistance before changing stops.', 409)
                if v['doors'] != 'closed' or v['ramp'] != 'stowed':
                    raise AssistanceError('Complete current assistance before changing stops.', 409)
                v.update(stop_id=stop_id, motion='stationary', phase='idle')
                self.departure.reset()
            elif action == 'depart':
                readiness = self._readiness()
                if not readiness['can_depart']:
                    raise AssistanceError(readiness['message'], 409)
                v.update(motion='moving', phase='travelling')
            elif action == 'obstruction':
                if not isinstance(value, bool):
                    raise AssistanceError('Provide a true or false obstruction value.')
                v['obstruction'] = value
                if value:
                    v['motion'] = 'stationary'
                    if v['phase'] in {'closing', 'stowing'}:
                        v.update(phase='awaiting_completion', doors='open',
                                 ramp='deployed' if any('ramp' in r['needs'] for r in self._active(True)) else 'stowed')
                        self.deadline = None
            elif action == 'emergency':
                v.update(emergency=True, motion='stationary')
                self.departure.hold('Emergency hold is active. Fresh cabin confirmation is required after reset.')
            elif action == 'reset':
                if v['obstruction']:
                    raise AssistanceError('Clear the obstruction before resetting the demonstration.', 409)
                if getattr(self, '_journal_error', False):
                    raise AssistanceError('The request journal is damaged. Preserve it and repair it before resetting.', 409)
                v.update(emergency=False, revalidation_required=False, motion='stationary',
                         phase='idle', doors='closed', ramp='stowed', wheelchair_secured=False)
                self.deadline, self.dwell_until = None, 0.
                self.departure.reset()
                for item in self._active():
                    item['passenger_confirmed'] = False
                    self._status(item, 'queued')
                self.scenario.reset_after_hold()
            elif action == 'cabin_coverage':
                if not isinstance(value, bool):
                    raise AssistanceError('Confirm whether the inside camera covers the whole cabin.')
                self.cabin.configure(value)
                self.cabin_audit.reset()
            elif action == 'secure_wheelchair':
                if not isinstance(value, bool):
                    raise AssistanceError('Provide an explicit simulated securement confirmation.')
                if v['motion'] != 'stationary':
                    raise AssistanceError('Stop the demonstration before confirming the wheelchair position.', 409)
                v['wheelchair_secured'] = value
            elif action == 'confirm_sensor_requests':
                if v['emergency'] or v['revalidation_required']:
                    raise AssistanceError('Reset the demonstration before confirming assistance.', 409)
                for item in self._active(True):
                    if item['origin'] != 'passenger' and item['status'] == 'awaiting_completion':
                        if self.scenario.enabled:
                            self.scenario.complete(item, self.clock())
                        else:
                            item.update(passenger_confirmed=True, updated_at_ms=round(self.clock() * 1000))
            else:
                raise AssistanceError('Unknown demonstration control.')
            self._changed()
            self._advance()
            self._save()
            return self._snapshot()

    def scenario_control(self, action, stop_id=None, seat_type=None, event_id=None,
                         priority_occupied=None, standard_occupied=None, priority_regular=None):
        with self.lock:
            if action == 'reset':
                return self.control('reset')
            self._advance()
            self.scenario.control(action, stop_id=stop_id, seat_type=seat_type, event_id=event_id,
                                  priority_occupied=priority_occupied, standard_occupied=standard_occupied,
                                  priority_regular=priority_regular)
            self._advance()
            self._save()
            return self._snapshot()

    def scenario_rfid(self, event_id, passenger_type, simulated=False):
        with self.lock:
            self._advance()
            if not self.scenario.enabled:
                raise AssistanceError('Start the timed scenario before playing an RFID tap.', 409)
            self.scenario.rfid(event_id, passenger_type, simulated=simulated)
            self._advance()
            self._save()
            return self._snapshot()

    def rfid_call(self, action, *, authorization=None, **kwargs):
        """Keep card presence, deduplication and occupancy in one durable commit."""
        with self.lock:
            if authorization is not None or action in {'tap', 'reader_state'}:
                self.rfid.authenticate(kwargs.get('reader_id'), authorization)
            if getattr(self, '_journal_error', False):
                raise AssistanceError('The RFID journal needs operator repair before scans can be accepted.', 503)
            self._advance()
            if action == 'snapshot':
                return self.rfid.snapshot()
            if action == 'reader_state':
                return self.rfid.reader_state(kwargs['reader_id'])
            backup = (self.rfid.dump(), copy.deepcopy({key: value for key, value in vars(self.scenario).items()
                                                     if key not in {'owner', 'error'}}),
                      self.revision, self._saved)
            try:
                if action == 'test_tap':
                    result = self.rfid.tap(reader_id='operator', **kwargs)
                else:
                    result = getattr(self.rfid, action)(**kwargs)
                self._save()
                return result
            except OSError:
                self.rfid.restore(backup[0])
                vars(self.scenario).update(backup[1])
                self.revision, self._saved = backup[2:]
                self.fail_hold()
                raise

    def detection_policy(self, role=None):
        """Outside detection follows open doors; inside counts never pause."""
        with self.lock:
            self._sync_door_transition()
            moving = self.vehicle['motion'] != 'stationary'
            outside_enabled = not moving and self.vehicle['doors'] == 'open'
            return dict(enabled=True if role == 'inside' else outside_enabled,
                        generation='inside-live' if role == 'inside' else
                        f'{int(self.scenario.enabled)}:{self.scenario.cycle_id}:{int(moving)}:{self._door_epoch}',
                        inside_enabled=True, inside_generation='inside-live',
                        posture_enabled=self.vehicle['doors'] == 'closed',
                        posture_generation=str(self._door_epoch))

    def tick(self, bridge_snapshot=None):
        with self.lock:
            if bridge_snapshot is not None:
                self._ingest(bridge_snapshot)
            self._advance()
            self._save()
            return self._snapshot()

    def _ingest(self, snapshot):
        now = self.clock()
        self._sync_door_transition()
        inside = snapshot.get('sources', {}).get('inside') or {}
        self.scenario.observe_boarding(snapshot.get('sources', {}).get('outside') or {}, now)
        audit = self.cabin_audit.observe(inside, now, whole_cabin=self.cabin.whole_cabin)
        if audit is not None:
            self.scenario.reconcile_camera(audit)
        if self.cabin.observe(inside, now, stationary=self.vehicle['motion'] == 'stationary',
                              doors_open=self.vehicle['doors'] == 'open'):
            if self.cabin.count:
                self.scenario.note_passengers()
            self._changed()
        preference = inside.get('posture_enabled')
        if isinstance(preference, bool) and preference != self.posture_enabled:
            self.posture_enabled = preference
            self.departure.reset()
            self._changed()
        summary = inside.get('seating_summary')
        closed = self.vehicle['doors'] == 'closed'
        generation_matches = (not isinstance(summary, dict) or 'door_generation' not in summary
                              or summary['door_generation'] == str(self._door_epoch))
        fresh = (closed and generation_matches and inside.get('kind') == 'live' and inside.get('connected') and not inside.get('is_demo')
                 and isinstance(inside.get('age_ms'), (int, float)) and 0 <= inside['age_ms'] < LIVE_MAX_AGE_MS
                 and isinstance(summary, dict) and summary.get('status') == 'observed'
                 and isinstance(summary.get('age_ms'), (int, float))
                 and 0 <= summary['age_ms'] < posture_ttl_ms(summary))
        self.seating = copy.deepcopy(summary) if fresh else None
        live_inside = (inside.get('kind') == 'live' and inside.get('connected') and not inside.get('is_demo')
                       and isinstance(inside.get('age_ms'), (int, float))
                       and 0 <= inside['age_ms'] < LIVE_MAX_AGE_MS)
        if live_inside and (any(isinstance(row, dict) and str(row.get('label', '')).strip().casefold() in {'person', 'persons'}
                                and not row.get('predicted', False) for row in (inside.get('detections') or []))
                            or fresh and any(counter(summary.get(key)) and summary[key] > 0
                                             for key in ('people', 'seated', 'standing', 'unknown'))):
            self.scenario.note_passengers()
        if self.departure.result(now)['expected_people'] > 0:
            self.scenario.note_passengers()
        posture_source = inside if generation_matches else dict(inside, seating_summary=None)
        self.departure.update(posture_source, closed=closed, enabled=self.posture_enabled, now=now,
                              expected_people=max(self.scenario.occupancy_total(), self.cabin.count or 0),
                              allow_empty=self.cabin.whole_cabin)
        self.scenario.observe_moving_posture(inside, now)
        events = snapshot.get('events', [])
        instance = snapshot.get('instance_id')
        if instance != self._bridge_instance:
            self._bridge_instance = instance
            self._event_cursor = max((int(event.get('id', 0)) for event in events), default=0)
            return  # Replayed events from before this controller joined are not new requests.
        for event in events:
            identifier = int(event.get('id', 0))
            if identifier <= self._event_cursor:
                continue
            self._event_cursor = identifier
            if getattr(self, '_journal_error', False):
                continue
            origin, kind = event.get('origin'), event.get('source_kind')
            if event.get('is_demo') or not ((origin == 'vision' and kind == 'live')
                                           or (origin == 'rfid' and kind == 'rfid')):
                continue
            if origin == 'vision' and event.get('source_role') != 'outside':
                continue
            age_ms = now * 1000 - event.get('timestamp_ms', 0)
            # Bridge timestamps are rounded to 0.1 ms; tolerate their tiny
            # rounding lead and ordinary wall-clock jitter on this same host.
            if not -50 <= age_ms <= 5000:
                continue
            if (self.scenario.enabled and self.scenario.started_at is not None
                    and event.get('timestamp_ms', 0) < self.scenario.started_at * 1000):
                continue  # A pre-stop detection cannot open a new stop's request.
            if self.scenario.enabled and not self.scenario.admission_open():
                continue  # Closing/stale sensor events cannot create a departure-blocking request.
            category = event.get('type')
            if self.scenario.enabled and origin == 'rfid':
                if self.scenario.admission_open() and self.vehicle['doors'] == 'open':
                    self.scenario.rfid(f'bridge:{instance}:{identifier}', category, simulated=False)
                continue
            needs = {'wheelchair': ['ramp', 'extra_time'], 'pram': ['ramp', 'extra_time'],
                     'walking': ['extra_time', 'priority_seat'],
                     'senior': ['extra_time', 'priority_seat'], 'assistance': ['extra_time']}.get(category)
            if needs is None or self.vehicle['motion'] != 'stationary':
                continue
            dedup = (origin, category, self.vehicle['stop_id'])
            if now - self._sensor_dedup.get(dedup, -float('inf')) < 30:
                continue
            if any(item['origin'] == origin and item.get('category') == category
                   and item['stop_id'] == self.vehicle['stop_id'] for item in self._active()):
                continue
            if len(self._active()) >= self.MAX_ACTIVE:
                continue
            self._prune()
            item = dict(id=secrets.token_hex(16), client_request_id='sensor:' + secrets.token_hex(12),
                        _token_hash=self._hash(secrets.token_hex(32)), journey='boarding',
                        stop_id=self.vehicle['stop_id'], needs=needs, status='queued', origin=origin,
                        category=category, created_at_ms=round(now * 1000), updated_at_ms=round(now * 1000),
                        passenger_confirmed=False)
            self.requests[item['id']] = item
            self._sensor_dedup[dedup] = now
            self._changed()

    def _start(self, pending, now):
        for item in pending:
            self._status(item, 'assisting')
        self.vehicle.update(phase='opening', doors='opening')
        self.departure.reset()
        if any('ramp' in item['needs'] for item in pending):
            # Legacy UI names are retained, but wheelchair_aboard is a review
            # latch, NOT proof of a wheelchair aboard. Any ramp journey changes
            # the mobility area and invalidates the previous demo confirmation.
            self.vehicle.update(wheelchair_aboard=True, wheelchair_secured=False)
        self.deadline = now + self.MOVE_SECONDS
        self.dwell_until = max(self.dwell_until, now + (self.EXTRA_DWELL if any(
            'extra_time' in item['needs'] for item in pending) else self.BASE_DWELL))
        self._changed()

    def _advance(self):
        if self.scenario.enabled:
            self.scenario.advance()
            return
        now, v = self.clock(), self.vehicle
        if (v['emergency'] or v['revalidation_required'] or v['motion'] != 'stationary'):
            return
        pending = self._active(True)
        newcomers = [item for item in pending if item['status'] in {'queued', 'accepted'}]
        if newcomers:
            self._start(pending, now)
        # Durations apply to successive simulated movement phases, not request completion.
        for _ in range(4):
            phase = v['phase']
            if self.deadline is None or now < self.deadline:
                break
            completed_at = self.deadline
            if phase == 'opening':
                v['doors'] = 'open'
                if any('ramp' in item['needs'] for item in pending):
                    v.update(phase='deploying', ramp='deploying')
                    self.deadline = completed_at + self.MOVE_SECONDS
                else:
                    self._await(pending)
            elif phase == 'deploying':
                v['ramp'] = 'deployed'
                self._await(pending)
            elif phase == 'stowing':
                if v['obstruction']:
                    break
                v.update(ramp='stowed', phase='closing', doors='closing')
                self.deadline = completed_at + self.MOVE_SECONDS
            elif phase == 'closing':
                if v['obstruction']:
                    break
                v.update(doors='closed', phase='idle')
                self.deadline = None
                for item in pending:
                    if item['passenger_confirmed']:
                        self._status(item, 'completed')
                        if 'ramp' in item['needs']:
                            # This bounded ledger counts completed REQUESTS,
                            # not people, aids or matched passenger identities.
                            # An exit request cannot clear another rider's hold.
                            delta = 1 if item['journey'] == 'boarding' else -1
                            balance = max(0, min(self.MAX_ACTIVE, v.get('mobility_request_balance', 0) + delta))
                            v.update(mobility_request_balance=balance, wheelchair_aboard=True,
                                     wheelchair_secured=False)
            else:
                break
            self._changed()
        if (v['phase'] == 'awaiting_completion' and now >= self.dwell_until
                and all(item['passenger_confirmed'] for item in pending) and not v['obstruction']):
            if v['ramp'] == 'deployed':
                v.update(phase='stowing', ramp='stowing')
            else:
                v.update(phase='closing', doors='closing')
            self.deadline = now + self.MOVE_SECONDS
            self._changed()

    def _await(self, pending):
        self.vehicle['phase'] = 'awaiting_completion'
        self.deadline = None
        for item in pending:
            self._status(item, 'awaiting_completion')

    def _readiness(self, *, include_notice=True, include_posture=True):
        now, v = self.clock(), self.vehicle
        reasons = []
        if v['emergency']:
            reasons.append('Emergency hold is active. An explicit reset is required.')
        if v['revalidation_required']:
            reasons.append('The server restarted. Revalidate and reset the demonstration.')
        if v['obstruction']:
            reasons.append('Clear the reported doorway obstruction.')
        if self.scenario.occupancy_total() > 26 or (self.cabin.snapshot(now)['status'] == 'fresh' and (self.cabin.count or 0) > 26):
            reasons.append('The inside camera indicates more than 26 passengers. Check capacity before departure.')
        if self._active(True):
            reasons.append('Wait for every assistance request at this stop to finish.')
        if v['doors'] != 'closed' or v['ramp'] != 'stowed':
            reasons.append('Wait for the simulated doors to close and ramp to stow.')
        if v['wheelchair_aboard'] and not v['wheelchair_secured']:
            reasons.append('Review the mobility area after ramp assistance and confirm it in the simulation. Cameras do not verify securement.')
        if (self.scenario.enabled and v['motion'] == 'stationary'
                and (self.scenario.phase != 'departure_wait' or self.scenario.closed_at is None
                     or now < self.scenario.closed_at + self.scenario.DEPART_SECONDS)):
            reasons.append('Wait for the boarding window, door closure and final departure checks.')
        posture = self.departure.result(now) if self.posture_enabled else None
        if (include_posture and self.scenario.enabled and v['motion'] == 'stationary'
                and v['doors'] == 'closed' and not self.posture_enabled):
            reasons.append('Enable the inside camera standing check before departure.')
        if posture is not None:
            if include_posture and v['doors'] == 'closed' and not posture['can_depart']:
                reasons.append(posture['message'])
        if (include_notice and self.scenario.enabled and v['motion'] == 'stationary'
                and self.scenario.phase == 'departure_wait'
                and (self.scenario.notice_at is None or self.scenario.notice_remaining(now) > 0)):
            reasons.append('Wait for the ten-second departure announcement to finish.')
        can_depart = not reasons and v['motion'] == 'stationary'
        return {'can_depart': can_depart, 'posture_enabled': self.posture_enabled,
                'posture': posture,
                'reasons': reasons, 'status': 'travelling' if v['motion'] == 'moving' else 'ready' if can_depart else 'held',
                'message': reasons[0] if reasons else ('Travelling between demo stops.' if v['motion'] == 'moving' else
                                                     'Simulation ready. The operator may depart.'),
                'minimum_dwell_remaining_ms': max(0, round((self.dwell_until - now) * 1000))}

    def _snapshot(self):
        readiness = self._readiness()
        active = self._active()
        # Public summaries cannot identify a passenger or reveal their private request handle.
        summaries = [{key: copy.deepcopy(item[key]) for key in ('journey', 'stop_id', 'needs', 'status', 'origin')}
                     | {'passenger_confirmed': item['passenger_confirmed']} for item in active]
        if self.vehicle['emergency']:
            announcement = 'Emergency hold. Remain in place and wait for guidance.'
        elif self.vehicle['obstruction']:
            announcement = 'Please keep the doorway clear. Assistance remains on hold.'
        elif self.vehicle['phase'] == 'awaiting_completion':
            announcement = 'Access is ready. Take your time. Confirm on your device after boarding or exiting.'
        elif self.vehicle['phase'] in {'opening', 'deploying'}:
            announcement = 'Preparing accessible boarding. Please wait while access is arranged.'
        else:
            announcement = readiness['message']
        if ((self.scenario.enabled or self.vehicle['motion'] == 'moving') and self.scenario.announcement and not self.vehicle['emergency']
                and not self.vehicle['obstruction'] and not self.vehicle['revalidation_required']):
            announcement = self.scenario.announcement['message']
        announcements = [announcement]
        if not (self.vehicle['emergency'] or self.vehicle['obstruction'] or self.vehicle['revalidation_required']):
            for event in self.scenario.announcement_events:
                if 0 <= self.clock() * 1000 - event['at_ms'] <= 30_000 and event['message'] not in announcements:
                    announcements.append(event['message'])
        return {'service': 'BusTech Demo', 'simulation': True, 'revision': self.revision,
                'server_time_ms': round(self.clock() * 1000), 'vehicle': copy.deepcopy(self.vehicle),
                'route': route.snapshot(self),
                'mobility_area': {
                    'review_required': self.vehicle['wheelchair_aboard'] and not self.vehicle['wheelchair_secured'],
                    'request_balance': self.vehicle.get('mobility_request_balance', 0),
                    'basis': 'Completed ramp-assistance requests; not a passenger count or identity match.',
                    'confirmation': 'Explicit operator confirmation in the simulation; not physical securement evidence.',
                },
                'requests': summaries, 'active_count': len(active),
                'sensor_confirmation_count': sum(item['origin'] != 'passenger' and
                                                 item['status'] == 'awaiting_completion' and
                                                 not item['passenger_confirmed'] for item in active),
                'announcements': announcements, 'readiness': readiness, 'seating': copy.deepcopy(self.seating),
                'scenario': self.scenario.snapshot()}

    def snapshot(self):
        with self.lock:
            self._advance()
            self._save()
            return self._snapshot()
