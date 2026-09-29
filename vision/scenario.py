"""Timed, explicitly simulated stop/board/depart demonstration.

Confirmed seat assignments and sustained cabin counts constrain admission.
Camera-only seat distribution is explicitly estimated. Fresh standing checks
must report no standing for five continuous seconds before departure.
"""
import copy
import math
import re
from . import route


class StopScenario:
    CAPACITY = {'priority': 6, 'standard': 20}
    BASE_SECONDS = 15.
    REQUEST_SECONDS = 10.
    RAMP_REQUEST_SECONDS = 20.
    ACTIVITY_SECONDS = 10.
    MAX_SECONDS = 50.
    DEPART_SECONDS = 3.
    BRAKE_SECONDS = 2.
    NOTICE_SECONDS = 10.
    POSTURE_REMINDER_SECONDS = 20.
    DETECTION_MAX_AGE_MS = 1000
    WARNING = ('Departure is planned in 10 seconds. Please finish boarding or alighting safely. Thank you.')
    STANDING_REMINDER = ('For your comfort and safety, please take a seat while the bus is moving. Thank you.')
    FULL = ('We are sorry, all seats are currently occupied or allocated. Please wait for the next bus. '
            'If you have just boarded, please step off safely. Thank you for your understanding.')
    YIELD = ('Please offer the priority seat to the passenger who needs it and move to an available '
             'standard seat. Thank you.')
    RFID_CLOSED = ('We are sorry, the boarding period has ended and no more card scans can be accepted. '
                   'Please wait for the next bus. Thank you for your understanding.')

    def __init__(self, owner, error):
        self.owner, self.error = owner, error
        self.enabled = False
        self.phase = 'disabled'
        self.cycle_id = 0
        self.started_at = self.boarding_until = self.closed_at = None
        self.motion_started_at = None
        self.occupied = {'priority': 0, 'standard': 0}
        self.priority_regular = 0
        self.movement_balance = 0
        self.camera_adjustment = 0
        self.camera_reconciliation = None
        self.events = {}
        self.last_admission = None
        self.announcement = None
        self.announcement_events = []
        self.announcement_sequence = 0
        self.warned = False
        self.notice_at = None
        self.notice_held = False
        self.detection_sample = None
        self.last_detection_at = None
        self.detection_positive = False
        self.activity_seen = False
        self.passenger_evidence = False
        self.route_from_stop = None
        self.route_approach_progress = 0.
        self.arrival_event = None
        self.departure_posture = None
        self.last_posture_reminder_at = None
        self.posture_reminder_sample = None

    def dump(self):
        return dict(enabled=self.enabled, cycle_id=self.cycle_id, occupied=self.occupied,
                    priority_regular=self.priority_regular, movement_balance=self.movement_balance, events=self.events,
                    passenger_evidence=self.passenger_evidence,
                    camera_adjustment=self.camera_adjustment, camera_reconciliation=self.camera_reconciliation,
                    announcement_sequence=self.announcement_sequence)

    def restore(self, value):
        if value is None:
            return
        if not isinstance(value, dict) or not isinstance(value.get('enabled'), bool):
            raise ValueError('Invalid scenario journal.')
        counts = value.get('occupied')
        recorded_limit = 1_000_000 if isinstance(value.get('camera_reconciliation'), dict) else None
        if not isinstance(counts, dict) or any(not self._count(counts.get(key), recorded_limit or limit)
                                              for key, limit in self.CAPACITY.items()):
            raise ValueError('Invalid seat ledger.')
        ordinary = value.get('priority_regular', 0)
        if not self._count(ordinary, counts['priority']):
            raise ValueError('Invalid priority seat ledger.')
        events = value.get('events', {})
        if not isinstance(events, dict) or len(events) > 1000:
            raise ValueError('Invalid scenario event ledger.')
        cycle = value.get('cycle_id', 0)
        sequence = value.get('announcement_sequence', 0)
        movement = value.get('movement_balance', 0)
        if not isinstance(movement, int) or isinstance(movement, bool):
            raise ValueError('Invalid passenger movement balance.')
        if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in (cycle, sequence)):
            raise ValueError('Invalid scenario cycle.')
        self.enabled, self.occupied = value['enabled'], dict(counts)
        self.priority_regular, self.events = ordinary, dict(events)
        self.cycle_id, self.announcement_sequence = cycle, sequence
        self.movement_balance = movement
        correction = value.get('camera_reconciliation')
        adjustment = value.get('camera_adjustment', 0)
        if (not isinstance(adjustment, int) or isinstance(adjustment, bool) or abs(adjustment) > 2_000_000):
            raise ValueError('Invalid camera reconciliation adjustment.')
        if correction is not None:
            if (not isinstance(correction, dict) or correction.get('version') != 1
                    or not self._count(correction.get('rounded'), 300)
                    or not self._finite(correction.get('mean')) or not 0 <= correction['mean'] <= 300
                    or not self._finite(correction.get('completed_at')) or correction['completed_at'] < 0
                    or not self._count(correction.get('recorded_at_check', sum(counts.values())), 2_000_000)
                    or math.floor(correction['mean'] + .5) != correction['rounded']):
                raise ValueError('Invalid camera reconciliation record.')
            self.camera_adjustment, self.camera_reconciliation = adjustment, copy.deepcopy(correction)
        elif adjustment != 0:
            raise ValueError('Camera adjustment requires a completed reconciliation record.')
        # Older journals contain only an instantaneous retained camera count.
        # That value is not a completed 30-second reconciliation and is ignored
        # for seat availability; the original boarding ledger stays intact.
        evidence = value.get('passenger_evidence', False)
        if not isinstance(evidence, bool):
            raise ValueError('Invalid retained passenger evidence.')
        self.passenger_evidence = evidence
        self.phase = 'held' if self.enabled else 'disabled'
        # Deadlines are deliberately not restored. Revalidation must precede
        # a new operator-commanded cycle, even after a clean shutdown.
        for item in self.owner.requests.values():
            item.pop('_scenario_reserved', None)
            item.pop('_scenario_cycle', None)
            item.pop('_scenario_deadline', None)
            item.pop('_scenario_allowance', None)
            item.pop('_scenario_count_at_accept', None)
            item.pop('_scenario_movement_at_accept', None)

    @staticmethod
    def _count(value, maximum):
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum

    def announce(self, message):
        self.announcement_sequence += 1
        self.announcement = dict(id=f'scenario:{self.cycle_id}:{self.announcement_sequence}', message=message)
        self.announcement_events.append(dict(self.announcement, at_ms=round(self.owner.clock() * 1000)))
        self.announcement_events = self.announcement_events[-16:]
        self.owner._changed()

    def observe_moving_posture(self, inside, now):
        """Request an ordinary queued announcement; posture never brakes the bus."""
        owner, v = self.owner, self.owner.vehicle
        result = owner.departure.result(now)
        if (v['motion'] != 'moving' or v['doors'] != 'closed' or not owner.posture_enabled
                or v['emergency'] or v['revalidation_required'] or not owner.seating
                or result['status'] != 'standing'):
            return
        received = inside.get('received_at_ms')
        if not self._finite(received) or received / 1000 <= owner._doors_changed_at:
            return
        sample = (inside.get('session_id'), inside.get('frame_id'), received)
        if sample == self.posture_reminder_sample:
            return
        self.posture_reminder_sample = sample
        if self.last_posture_reminder_at is not None and now - self.last_posture_reminder_at < self.POSTURE_REMINDER_SECONDS:
            return
        self.last_posture_reminder_at = now
        self.announce(self.STANDING_REMINDER)

    def occupancy_total(self):
        return max(0, sum(self.occupied.values()) + self.camera_adjustment)

    def reconcile_camera(self, result):
        """A completed camera cycle corrects the baseline, never adds a second population."""
        rounded = result.rounded
        # The last frame can arrive just after the window boundary. Preserve
        # accepted scans that happened after that boundary while it was finishing.
        later_delta = later_recorded_delta = 0
        for event in self.events.values():
            admission = event.get('admission') or {}
            at = event.get('occurred_at')
            if not self._finite(at) and self._finite(admission.get('at_ms')):
                at = admission['at_ms'] / 1000  # Compatibility with older journals.
            if admission.get('allowed') is True and self._finite(at) and at >= result.completed_at:
                operation = str(event.get('operation', '')).split(':', 1)[0]
                delta = 1 if operation in {'board', 'rfid'} else -1 if operation == 'alight' else 0
                later_delta += delta
                # Camera-only alighting does not change the raw RFID ledger.
                later_recorded_delta += event.get('recorded_delta', delta)
        self.camera_adjustment = rounded + later_delta - sum(self.occupied.values())
        self.camera_reconciliation = dict(version=1, mean=result.mean, rounded=rounded,
                                          completed_at=result.completed_at,
                                          recorded_at_check=max(0, sum(self.occupied.values()) - later_recorded_delta))
        self.owner._changed()

    def camera_check(self):
        check = self.owner.cabin_audit.snapshot(self.owner.clock())
        if check.get('last_mean') is None and self.camera_reconciliation:
            check.update(last_mean=self.camera_reconciliation['mean'],
                         last_rounded=self.camera_reconciliation['rounded'],
                         cycle_completed_at=self.camera_reconciliation['completed_at'])
        check['mismatch'] = (None if check.get('last_rounded') is None else
                             check['last_rounded'] - (self.camera_reconciliation or {}).get('recorded_at_check', sum(self.occupied.values())))
        return check

    def seats(self):
        reserved = {key: 0 for key in self.CAPACITY}
        for item in self.owner._active():
            seat = item.get('_scenario_reserved')
            if seat in reserved:
                reserved[seat] += 1
        occupied = {key: min(self.occupied[key], capacity) for key, capacity in self.CAPACITY.items()}
        total = self.occupancy_total()
        difference = min(26, total) - sum(occupied.values())
        for key in ('standard', 'priority'):
            amount = min(difference, self.CAPACITY[key] - occupied[key]) if difference >= 0 else -min(-difference, occupied[key])
            occupied[key] += amount
            difference -= amount
        rows = {key: dict(capacity=capacity, occupied=occupied[key], confirmed=self.occupied[key], reserved=reserved[key],
                          available=max(0, capacity - occupied[key] - reserved[key]))
                for key, capacity in self.CAPACITY.items()}
        rows['priority']['non_priority_occupied'] = min(self.priority_regular, occupied['priority'])
        check = self.camera_check()
        estimated = self.camera_reconciliation is not None
        basis = ('Boarding/RFID record; no complete 30-second camera check has been applied.' if not estimated else
                 'Count corrected by the last complete 30-second camera average; subsequent boarding/alighting scans update it. '
                 'Priority and standard seat allocation is estimated.')
        source = dict(people=check.get('last_rounded'), status=check['status'], whole_cabin=self.owner.cabin.whole_cabin,
                      source='inside_person_30_second_mean', seat_locations_verified=False)
        return rows | dict(total=min(26, total), actual_total=total, confirmed_total=sum(self.occupied.values()),
                           capacity=26, reserved=sum(reserved.values()), estimated=estimated,
                           available=sum(row['available'] for row in rows.values()),
                           count_source=source, camera_check=check, basis=basis)

    def admission_open(self, now=None):
        now = self.owner.clock() if now is None else now
        return (self.boarding_open(now) and self.started_at is not None
                and now < self.started_at + self.MAX_SECONDS)

    def boarding_open(self, now=None):
        """An accepted allowance may finish after admission closes at 50 seconds."""
        now = self.owner.clock() if now is None else now
        v = self.owner.vehicle
        return (self.enabled and self.phase == 'boarding' and self.boarding_until is not None
                and now < self.boarding_until and v['motion'] == 'stationary'
                and not v['emergency'] and not v['revalidation_required'])

    def _admission(self, allowed, message, event_id=None):
        self.last_admission = dict(allowed=allowed, message=message, event_id=event_id,
                                   at_ms=round(self.owner.clock() * 1000))
        if not allowed:
            self.announce(message)
        self.owner._changed()
        return allowed

    def _event(self, event_id, operation):
        if not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', event_id):
            raise self.error('Provide a unique event ID for this simulated passenger action.')
        previous = self.events.get(event_id)
        if previous is not None:
            if previous['operation'] != operation:
                raise self.error('A retried event must describe the same passenger action.', 409)
            self.last_admission = copy.deepcopy(previous['admission'])
            return False
        return True

    def _remember(self, event_id, operation, occurred_at, recorded_delta):
        self.events[event_id] = dict(operation=operation, admission=copy.deepcopy(self.last_admission),
                                    occurred_at=occurred_at, recorded_delta=recorded_delta)
        while len(self.events) > 1000:
            del self.events[next(iter(self.events))]

    def _seat(self, requested):
        """Allocate a simulated seat, allowing ordinary use of unused priority seats."""
        seats = self.seats()
        if seats['available'] <= 0:
            return None
        if requested == 'standard':
            return 'standard' if seats['standard']['available'] else 'priority'
        if seats['priority']['available']:
            return 'priority'
        if self.priority_regular and seats['standard']['available']:
            # This changes the demo assignment only; the announcement asks the
            # actual passenger to move and posture still gates departure.
            self.occupied['priority'] -= 1
            self.occupied['standard'] += 1
            self.priority_regular -= 1
            self.announce(self.YIELD)
            return 'priority'
        return 'standard' if seats['standard']['available'] else None

    def _extend(self, seconds, now):
        if self.admission_open(now):
            self.activity_seen = True
            # Only admission is capped. A scan/request accepted before the
            # cutoff receives its entire allowance, without stacking time
            # onto an already longer ramp or initial quiet-window deadline.
            self.boarding_until = max(self.boarding_until, now + seconds)
            self.owner._changed()

    @staticmethod
    def _finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def observe_boarding(self, outside, now):
        """Only new, fresh outside-camera frames can renew the quiet window.

        Polling, tracking predictions and uploaded test media are not continued
        boarding activity. An outside object also is not evidence of a rider aboard.
        """
        age, received, frame = (outside.get(key) for key in ('age_ms', 'received_at_ms', 'frame_id'))
        v = self.owner.vehicle
        if (not self.enabled or self.phase != 'boarding' or self.started_at is None
                or self.boarding_until is None or now >= self.started_at + self.MAX_SECONDS
                or v['motion'] != 'stationary' or v['doors'] != 'open'
                or v['emergency'] or v['revalidation_required']
                or outside.get('kind') != 'live' or not outside.get('connected') or outside.get('is_demo')
                or not self._finite(age) or not 0 <= age < self.DETECTION_MAX_AGE_MS
                or not self._finite(received) or not -50 <= now * 1000 - received < self.DETECTION_MAX_AGE_MS
                or not self.started_at * 1000 <= received < self.boarding_until * 1000
                or not outside.get('session_id') or not isinstance(frame, int) or isinstance(frame, bool) or frame < 0):
            self.detection_positive = False
            return
        sample = (outside['session_id'], frame, received)
        if self.detection_sample is not None:
            session, previous_frame, previous_received = self.detection_sample
            if received <= previous_received or (sample[0] == session and frame <= previous_frame):
                return
        self.detection_sample = sample
        detections = outside.get('detections')
        positive = isinstance(detections, list) and any(
            isinstance(row, dict) and isinstance(row.get('label'), str) and row['label'].strip()
            and not row.get('predicted', False) and self._finite(row.get('score')) and 0 < row['score'] <= 1
            for row in detections)
        self.detection_positive = bool(positive)
        if positive:
            # Capture time, not the next dashboard poll, defines the extension.
            # A fresh frame received just before expiry can finish inference
            # just after it, provided closure has not begun and the cap remains.
            self.last_detection_at = min(now, received / 1000)
            self.boarding_until = max(self.boarding_until,
                                      min(self.started_at + self.MAX_SECONDS,
                                          self.last_detection_at + self.BASE_SECONDS))
        self.owner._changed()

    def detection_wait(self, now):
        age = None if self.last_detection_at is None else max(0, round((now - self.last_detection_at) * 1000))
        active = (self.admission_open(now) and self.detection_positive
                  and age is not None and age < self.DETECTION_MAX_AGE_MS)
        return dict(active=active, roles=['outside'] if active else [], last_detection_age_ms=age)

    def _planned_departure(self):
        owner = self.owner
        check_seconds = self.DEPART_SECONDS
        if owner.posture_enabled:
            check_seconds = max(check_seconds, owner.departure.result(owner.clock())['confirmation_ms'] / 1000)
        if self.phase == 'boarding' and self.boarding_until is not None:
            return (self.boarding_until + owner.MOVE_SECONDS + check_seconds
                    + (owner.MOVE_SECONDS if owner.vehicle['ramp'] != 'stowed' else 0))
        if self.phase in {'stowing', 'closing'} and owner.deadline is not None:
            return owner.deadline + check_seconds + (owner.MOVE_SECONDS if self.phase == 'stowing' else 0)
        if self.phase == 'departure_wait' and self.closed_at is not None:
            planned = self.closed_at + self.DEPART_SECONDS
            if owner.posture_enabled:
                posture = owner.departure.result(owner.clock())
                if not posture['can_depart']:
                    if posture['status'] != 'confirming':
                        return None  # Unknown/standing evidence has no departure deadline.
                    planned = max(planned, owner.clock() +
                                  (posture['confirmation_ms'] - posture['progress_ms']) / 1000)
            return planned
        return None

    def notice_remaining(self, now):
        return 0 if self.notice_at is None else max(0, round((self.notice_at + self.NOTICE_SECONDS - now) * 1000))

    def _update_notice(self, now):
        owner, v = self.owner, self.owner.vehicle
        planned = self._planned_departure()
        held = (v['emergency'] or v['revalidation_required'] or v['obstruction']
                or self.phase == 'departure_wait' and self.closed_at is not None
                and now >= self.closed_at + self.DEPART_SECONDS
                and not owner._readiness(include_notice=False, include_posture=False)['can_depart'])
        self.notice_held = bool(held)
        if held:
            self.notice_at, self.warned = None, False
            return
        if planned is None:
            return
        if self.notice_at is not None and planned - now > self.NOTICE_SECONDS + .001:
            self.notice_at, self.warned = None, False
            self.announce('Boarding activity continues. Departure has been delayed. Please board or exit safely.')
        if self.notice_at is None and planned - now <= self.NOTICE_SECONDS + .001:
            # Never backdate an announcement after a late controller tick.
            self.notice_at, self.warned = now, True
            self.announce(self.WARNING)

    def departure_notice(self, now):
        v = self.owner.vehicle
        planned = self._planned_departure()
        held = self.notice_held or v['emergency'] or v['revalidation_required'] or v['obstruction']
        remaining = None
        if self.enabled and v['motion'] == 'stationary' and planned is not None and not held:
            earliest = max(planned, self.notice_at + self.NOTICE_SECONDS if self.notice_at is not None else planned)
            remaining = max(0, round((earliest - now) * 1000))
        return dict(remaining_ms=remaining, announced=self.notice_at is not None and not held,
                    minimum_remaining_ms=self.notice_remaining(now) if not held else 0,
                    waiting_for_detection=self.detection_wait(now)['active'])

    def note_passengers(self):
        # Independent of the posture roster, which resets while doors are open.
        # Losing a camera or opening the doors cannot turn a known rider into
        # the empty-demonstration exception.
        if not self.passenger_evidence:
            self.passenger_evidence = True
            self.owner._changed()

    def posture_wait(self, now):
        v = self.owner.vehicle
        active = self.owner.posture_enabled and v['doors'] == 'closed'
        result = self.owner.departure.result(now) if active else None
        status = ('inactive' if not active else 'monitoring' if v['motion'] == 'moving' else
                  'ready' if result['can_depart'] else 'confirming' if result['status'] == 'confirming' else 'checking')
        remaining = (max(0, result['confirmation_ms'] - result['progress_ms']) if status == 'confirming'
                     else None if status == 'checking' else 0)
        return dict(enabled=bool(active), remaining_ms=remaining,
                    confirmation_ms=result['confirmation_ms'] if result else round(self.owner.departure.STANDING_CONFIRM_SECONDS * 1000),
                    expired=False, bypassed=False, status=status)

    def _arrived(self):
        self.arrival_event = dict(id=f'route:{self.cycle_id}:{self.owner.vehicle["stop_id"]}',
                                  stop_id=self.owner.vehicle['stop_id'])

    @staticmethod
    def request_seat(item):
        return 'priority' if 'priority_seat' in item['needs'] or 'ramp' in item['needs'] else 'standard'

    def _accept(self, item, now):
        if item.get('_scenario_cycle') == self.cycle_id:
            return
        if not self.admission_open(now):
            return
        if item['journey'] == 'boarding':
            requested = self.request_seat(item)
            seat = self._seat(requested)
            if seat is None:
                item['scenario_message'] = self.FULL
                self.owner._status(item, 'error')
                self._admission(False, self.FULL)
                return
            item['_scenario_reserved'] = seat
            item['_scenario_regular'] = requested == 'standard'
            item['seat_type'] = seat
            item['_scenario_count_at_accept'] = self.seats()['total']
            item['_scenario_movement_at_accept'] = self.movement_balance
        item['_scenario_cycle'] = self.cycle_id
        item['_scenario_completed'] = False
        allowance = (self.RAMP_REQUEST_SECONDS if 'ramp' in item['needs'] else
                     self.REQUEST_SECONDS if item['origin'] == 'passenger' else self.ACTIVITY_SECONDS)
        item['_scenario_deadline'] = now + allowance
        item['_scenario_allowance'] = item['_scenario_deadline'] - now
        item['scenario_message'] = (f'Your request has a {allowance:g}-second allowance at this stop. '
                                    'Please confirm only after you have safely boarded or exited.')
        self.owner._status(item, 'assisting')
        self._extend(allowance, now)
        if 'ramp' in item['needs']:
            self.owner.vehicle.update(wheelchair_aboard=True, wheelchair_secured=False)

    def request_clock(self, item):
        """Only the owner of a request receives its server-authoritative allowance."""
        enabled = self.enabled and item['origin'] == 'passenger'
        deadline = item.get('_scenario_deadline')
        terminal = item['status'] in {'completed', 'cancelled', 'error'}
        held = self.owner.vehicle['emergency'] or self.owner.vehicle['revalidation_required']
        state = ('inactive' if not enabled else
                 'expired' if item['status'] == 'error' and deadline is not None else
                 item['status'] if terminal else
                 'held' if held else 'active' if deadline is not None else 'queued')
        maximum = self.RAMP_REQUEST_SECONDS if 'ramp' in item['needs'] else self.REQUEST_SECONDS
        return dict(enabled=enabled, state=state, maximum_ms=round(maximum * 1000),
                    allowance_ms=round(item.get('_scenario_allowance', 0) * 1000),
                    remaining_ms=None if deadline is None else
                    0 if terminal else max(0, round((deadline - self.owner.clock()) * 1000)))

    def _expire(self, now=None):
        for item in list(self.owner._active(True)):
            if now is not None and (item.get('_scenario_deadline') is None or now < item['_scenario_deadline']):
                continue
            item.pop('_scenario_reserved', None)
            item['scenario_message'] = ('Your assistance allowance ended before completion was confirmed. '
                                        'You have not been counted as boarded. Please request assistance again at a later stop.'
                                        if item['journey'] == 'boarding' else
                                        'Your assistance allowance ended before your exit was confirmed. '
                                        'You remain recorded aboard. Please ask the operator for assistance.')
            self.owner._status(item, 'error')

    def begin(self, stop_id, braking=False):
        owner, now = self.owner, self.owner.clock()
        prior_route = route.snapshot(owner)
        self.route_from_stop = owner.vehicle['stop_id'] if braking else None
        self.route_approach_progress = prior_route['progress'] if braking else 0.
        if owner.departure.result(now)['expected_people'] > 0 or any(
                (owner.seating or {}).get(key, 0) for key in ('people', 'seated', 'standing', 'unknown')):
            self.note_passengers()
        self.enabled = True
        self.phase = 'braking' if braking else 'opening'
        self.cycle_id += 1
        self.started_at = self.boarding_until = None
        self.motion_started_at = now
        self.closed_at = None
        self.departure_posture = None
        self.warned = False
        self.notice_at = None
        self.notice_held = False
        self.detection_sample = None
        self.last_detection_at = None
        self.detection_positive = False
        self.activity_seen = False
        self.last_admission = None
        owner.vehicle.update(stop_id=stop_id, motion='braking' if braking else 'stationary',
                             phase=self.phase, doors='closed' if braking else 'opening')
        owner.deadline = now + (self.BRAKE_SECONDS if braking else owner.MOVE_SECONDS)
        owner.dwell_until = now
        owner.departure.reset()
        if not braking:
            self._arrived()
        self.announce(f'Approaching {route.stop_name(stop_id)}. The bus is slowing down. Please remain seated until the doors open.' if braking else
                      f'The bus has arrived at {route.stop_name(stop_id)}. Please wait for the doors to open.')
        owner._changed()

    def reset_after_hold(self):
        if self.enabled:
            for item in self.owner._active():
                item.pop('_scenario_reserved', None)
                item.pop('_scenario_cycle', None)
                item.pop('_scenario_deadline', None)
                item.pop('_scenario_allowance', None)
                item.pop('_scenario_count_at_accept', None)
                item.pop('_scenario_movement_at_accept', None)
            self.begin(self.owner.vehicle['stop_id'])

    def control(self, action, stop_id=None, seat_type=None, event_id=None,
                priority_occupied=None, standard_occupied=None, priority_regular=None):
        owner, v, now = self.owner, self.owner.vehicle, self.owner.clock()
        valid_stops = {stop['id'] for stop in owner.config()['stops']}
        if action in {'start', 'stop'}:
            if stop_id is not None and stop_id not in valid_stops:
                raise self.error('Choose a valid demo stop.')
            if v['emergency'] or v['revalidation_required']:
                raise self.error('Reset and revalidate the held demonstration before starting a stop.', 409)
            if self.enabled and v['motion'] != 'moving':
                return  # A repeated click cannot replenish either deadline.
            if action == 'stop' and not self.enabled:
                raise self.error('Start the scenario before commanding a stop.', 409)
            if not self.enabled and (v['doors'] != 'closed' or v['ramp'] != 'stowed' or owner._active(True)):
                raise self.error('Finish current manual assistance before starting the timed scenario.', 409)
            chosen = route.next_stop(v['stop_id']) if v['motion'] == 'moving' else v['stop_id']
            self.begin(chosen, braking=v['motion'] == 'moving')
        elif action == 'disable':
            if v['motion'] != 'stationary' or owner._active(True) or v['doors'] != 'closed' or v['ramp'] != 'stowed':
                raise self.error('Stop and finish assistance before leaving scenario mode.', 409)
            self.enabled, self.phase = False, 'disabled'
            self.started_at = self.boarding_until = self.closed_at = None
            self.motion_started_at = None
            owner.deadline = None
            v['phase'] = 'idle'
            owner._changed()
        elif action == 'load':
            preparation = (not self.enabled and v['motion'] == 'stationary' and v['doors'] == 'closed'
                           and v['ramp'] == 'stowed' and not v['emergency'] and not v['revalidation_required'])
            if not self.admission_open(now) and not preparation:
                raise self.error('Load simulated passengers before starting, or during an open boarding window.', 409)
            if owner._active(True):
                raise self.error('Finish or cancel current requests before changing the demo seat load.', 409)
            if (not self._count(priority_occupied, 6) or not self._count(standard_occupied, 20)
                    or priority_regular is not None and not self._count(priority_regular, priority_occupied)):
                raise self.error('Use 0–6 priority seats, 0–20 standard seats, and a valid ordinary-priority count.')
            self.occupied = dict(priority=priority_occupied, standard=standard_occupied)
            self.priority_regular = priority_regular or 0
            self.camera_adjustment = 0
            self.camera_reconciliation = None
            self.owner.cabin_audit.reset()
            if (priority_occupied + standard_occupied == 0 and not owner.cabin.count
                    and not any((owner.seating or {}).get(key, 0)
                                for key in ('people', 'seated', 'standing', 'unknown'))):
                # Explicit operator initialization of the simulated population.
                # Missing camera observations alone never clear this latch.
                self.passenger_evidence = False
            self._admission(True, 'Confirmed demo seat load updated. A fresh 30-second camera check will cross-check this record.', event_id)
            owner._changed()
        elif action in {'board', 'alight', 'rfid'}:
            if seat_type not in self.CAPACITY:
                raise self.error('Choose priority or standard seating for the simulated passenger.')
            operation = f'{action}:{seat_type}'
            if not self._event(event_id, operation):
                return
            if not self.admission_open(now) or v['doors'] != 'open':
                raise self.error('Wait for an open boarding window before simulating passenger activity.', 409)
            recorded_before = sum(self.occupied.values())
            if action in {'board', 'rfid'}:
                seat = self._seat(seat_type)
                if seat is None:
                    self._admission(False, self.FULL, event_id)
                else:
                    before = self.seats()['total']
                    self.occupied[seat] += 1
                    self.movement_balance += 1
                    if seat == 'priority' and seat_type == 'standard':
                        self.priority_regular += 1
                    self._admission(True, f'Boarding accepted. A {seat} seat is assigned in the simulation.', event_id)
                    self._extend(self.ACTIVITY_SECONDS, now)
            else:
                before = self.seats()
                if before[seat_type]['occupied'] <= 0:
                    self._admission(False, 'No passenger is recorded in that seat group. The seat count was not changed.', event_id)
                else:
                    if self.occupied[seat_type] > 0:
                        self.occupied[seat_type] -= 1
                    else:
                        self.camera_adjustment -= 1
                    self.movement_balance -= 1
                    if seat_type == 'priority':
                        self.priority_regular = min(self.priority_regular, self.occupied['priority'])
                    self._admission(True, 'Alighting confirmed in the simulation. A seat is now available.', event_id)
                    self._extend(self.ACTIVITY_SECONDS, now)
            self._remember(event_id, operation, now, sum(self.occupied.values()) - recorded_before)
        else:
            raise self.error('Unknown stop scenario control.')

    def rfid(self, event_id, passenger_type, simulated=False):
        priority = {'senior', 'walking', 'wheelchair', 'priority', 'assistance'}
        allowed = priority | {'person', 'standard', 'pram'}
        if passenger_type not in allowed:
            raise self.error('Choose a valid RFID passenger category.')
        self.control('rfid', seat_type='priority' if passenger_type in priority else 'standard', event_id=event_id)

    def complete(self, item, now):
        if item.get('_scenario_completed'):
            return
        if (not self.boarding_open(now) or self.owner.vehicle['doors'] != 'open'
                or item.get('_scenario_cycle') != self.cycle_id
                or now >= item.get('_scenario_deadline', now)):
            raise self.error('This boarding window has closed. Please request assistance for a later stop.', 409)
        item.pop('_scenario_reserved', None)
        item.update(passenger_confirmed=True, _scenario_completed=True,
                    scenario_message='Your assistance is complete. Thank you for travelling with us.')
        self.owner._status(item, 'completed')
        if item['origin'] != 'passenger':
            self._extend(self.ACTIVITY_SECONDS, now)
        if 'ramp' in item['needs']:
            v = self.owner.vehicle
            delta = 1 if item['journey'] == 'boarding' else -1
            v.update(mobility_request_balance=max(0, min(self.owner.MAX_ACTIVE, v.get('mobility_request_balance', 0) + delta)),
                     wheelchair_aboard=True, wheelchair_secured=False)
        self.owner._changed()

    def advance(self):
        owner, v, now = self.owner, self.owner.vehicle, self.owner.clock()
        if v['emergency'] or v['revalidation_required'] or v['motion'] == 'moving':
            if v['motion'] != 'moving':
                self._update_notice(now)
            return
        if self.phase == 'braking':
            if owner.deadline is None or now < owner.deadline:
                return
            self.phase = 'opening'
            v.update(motion='stationary', phase='opening', doors='opening')
            self._arrived()
            owner.deadline = now + owner.MOVE_SECONDS
            self.announce(f'The bus has arrived at {route.stop_name(v["stop_id"])}. Please wait for the doors to open.')
        if self.phase == 'opening':
            if owner.deadline is None or now < owner.deadline:
                return
            self.phase = 'boarding'
            self.started_at, self.boarding_until = now, now + self.BASE_SECONDS
            v.update(doors='open', phase='awaiting_completion')
            owner.deadline = None
            self.announce('The doors are open for boarding and alighting. Please board or exit safely.')
        if self.started_at is None:
            return
        if self.phase == 'boarding':
            self._expire(now)
            if now >= self.boarding_until:
                self._expire()
                if v['obstruction']:
                    v.update(doors='open', phase='awaiting_completion')
                    self._update_notice(now)
                    return
                if v['ramp'] != 'stowed':
                    self.phase = 'stowing'
                    v.update(phase='stowing', ramp='stowing')
                else:
                    self.phase = 'closing'
                    v.update(phase='closing', doors='closing')
                owner.deadline = now + owner.MOVE_SECONDS
                if self.notice_at is None or self.notice_remaining(now) == 0:
                    self.announce('Boarding has ended. The doors are closing. Please keep the doorway clear.')
                owner._changed()
            else:
                for item in list(owner._active(True)):
                    if item['status'] in {'queued', 'accepted'}:
                        self._accept(item, now)
                pending = owner._active(True)
                needs_ramp = any('ramp' in item['needs'] for item in pending)
                if v['doors'] == 'open' and needs_ramp and v['ramp'] == 'stowed':
                    v.update(ramp='deploying', phase='deploying')
                    owner.deadline = now + owner.MOVE_SECONDS
                    owner._changed()
                if v['ramp'] == 'deploying' and owner.deadline is not None and now >= owner.deadline:
                    v.update(ramp='deployed', phase='awaiting_completion')
                    owner.deadline = None
                    owner._changed()
                if v['doors'] == 'open':
                    for item in pending:
                        if 'ramp' not in item['needs'] or v['ramp'] == 'deployed':
                            owner._status(item, 'awaiting_completion')
        if self.phase in {'stowing', 'closing'}:
            if v['obstruction']:
                # Reported obstruction reopens access without re-opening
                # admission or resetting the 50-second limit.
                v.update(doors='open', phase='awaiting_completion')
                owner.deadline = None
                self.phase = 'boarding'
                self._update_notice(now)
                return
            if owner.deadline is not None and now >= owner.deadline:
                if self.phase == 'stowing':
                    v.update(ramp='stowed', phase='closing', doors='closing')
                    self.phase = 'closing'
                    owner.deadline = now + owner.MOVE_SECONDS
                else:
                    v.update(doors='closed', phase='departure_wait')
                    self.phase, self.closed_at = 'departure_wait', now
                    owner.deadline = None
                    owner.departure.reset()
                    # Routine phase speech must not cancel a departure warning
                    # that is still playing on the three connected websites.
                    if self.notice_at is None or self.notice_remaining(now) == 0:
                        self.announce('The doors are closed. Please remain seated while the final departure checks are completed.')
                owner._changed()
        self._update_notice(now)
        if self.phase == 'departure_wait' and now >= self.closed_at + self.DEPART_SECONDS:
            readiness = owner._readiness(include_notice=False)
            if readiness['can_depart'] and self.notice_at is not None and now + 1e-9 >= self.notice_at + self.NOTICE_SECONDS:
                self.departure_posture = self.posture_wait(now)
                self.departure_posture['remaining_ms'] = 0
                v.update(motion='moving', phase='travelling')
                self.phase = 'travelling'
                self.motion_started_at = now
                self.announce('The bus is departing. Please remain seated. Thank you.')
                owner._changed()
            elif not readiness['can_depart'] and (not self.announcement or self.announcement['message'] != readiness['message']):
                self.announce(readiness['message'])
        owner.dwell_until = self.boarding_until

    def snapshot(self):
        now, v = self.owner.clock(), self.owner.vehicle
        seats = self.seats()
        held = self.enabled and (v['emergency'] or v['revalidation_required'] or v['obstruction']
                                or self.phase == 'departure_wait' and self.closed_at is not None
                                and now >= self.closed_at + self.DEPART_SECONDS
                                and not self.owner._readiness(include_notice=False)['can_depart']) and v['motion'] != 'moving'
        return dict(enabled=self.enabled, phase='held' if held else self.phase, cycle_id=self.cycle_id,
                    simulation=True, detection_enabled=v['motion'] == 'stationary' and v['doors'] == 'open',
                    inside_detection_enabled=True, posture_detection_enabled=v['doors'] == 'closed',
                    braking_duration_ms=round(self.BRAKE_SECONDS * 1000),
                    braking_remaining_ms=max(0, round((self.owner.deadline - now) * 1000))
                    if self.phase == 'braking' and self.owner.deadline is not None else 0,
                    motion=dict(phase=v['motion'],
                                started_at_ms=None if self.motion_started_at is None else round(self.motion_started_at * 1000),
                                duration_ms=round(self.BRAKE_SECONDS * 1000) if self.phase == 'braking' else 0,
                                elapsed_ms=0 if self.motion_started_at is None else max(0, round((now - self.motion_started_at) * 1000)),
                                remaining_ms=max(0, round((self.owner.deadline - now) * 1000))
                                if self.phase == 'braking' and self.owner.deadline is not None else 0),
                    admission_open=self.admission_open(now),
                    admission_remaining_ms=0 if self.started_at is None or not self.admission_open(now) else
                    max(0, round((min(self.boarding_until, self.started_at + self.MAX_SECONDS) - now) * 1000)),
                    finishing_extensions=self.boarding_open(now) and self.started_at is not None
                    and now >= self.started_at + self.MAX_SECONDS,
                    stop_elapsed_ms=0 if self.started_at is None else max(0, round((now - self.started_at) * 1000)),
                    boarding_remaining_ms=0 if self.boarding_until is None or self.phase != 'boarding' else
                    max(0, round((self.boarding_until - now) * 1000)),
                    hard_remaining_ms=0 if self.started_at is None else
                    max(0, round((self.started_at + self.MAX_SECONDS - now) * 1000)),
                    departure_remaining_ms=0 if self.closed_at is None or v['motion'] == 'moving' else
                    max(0, round((self.closed_at + self.DEPART_SECONDS - now) * 1000)),
                    warning=self.enabled and v['motion'] == 'stationary' and self.notice_at is not None
                    and not self.notice_held and self.notice_remaining(now) > 0,
                    detection_wait=self.detection_wait(now), departure_notice=self.departure_notice(now),
                    posture_wait=self.posture_wait(now),
                    seats=seats, last_admission=copy.deepcopy(self.last_admission),
                    announcement=copy.deepcopy(self.announcement),
                    announcement_events=copy.deepcopy(self.announcement_events))
