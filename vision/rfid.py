"""Private RFID identities, reader credentials and atomic tap accounting.

Stored in the assistance journal so an acknowledged tap, its passenger count
and its onboard card identity are committed together. No card data is public.
"""
import copy
import hashlib
import math
import re
import secrets


CARD_UIDS = ('04:A0:00:01', '04:A0:00:02', '04:A0:00:03', '04:A0:00:04')
PROFILE_TYPES = {'normal', 'senior', 'pregnant', 'custom'}
PRIORITY_MESSAGE = ('A passenger who needs priority seating has boarded. If you do not need a priority seat, '
                    'please offer it to them and move to an available standard seat. Thank you for your kindness.')


def canonical_uid(value):
    if not isinstance(value, str):
        raise ValueError('Provide the card UID in hexadecimal format.')
    value = value.strip().upper()
    if not re.fullmatch(r'(?:[0-9A-F]{8}|[0-9A-F]{14}|[0-9A-F]{20}|[0-9A-F]{2}(?:[:-][0-9A-F]{2}){3}|[0-9A-F]{2}(?:[:-][0-9A-F]{2}){6}|[0-9A-F]{2}(?:[:-][0-9A-F]{2}){9})', value):
        raise ValueError('Use a valid 4-, 7- or 10-byte hexadecimal card UID.')
    compact = value.replace(':', '').replace('-', '')
    return ':'.join(compact[i:i + 2] for i in range(0, len(compact), 2))


class RFIDRegistry:
    DEBOUNCE_SECONDS = 2.
    MAX_EVENT_AGE_MS = 5000
    MAX_EVENTS = 1000
    MAX_READERS = 8

    def __init__(self, owner):
        self.owner = owner
        self.cards = {str(i): dict(id=str(i), name=f'Card {i}', uid=uid,
                                  passenger_type='normal', custom_label='', priority=False, extra_time=False,
                                  onboard=False, seat_type=None, boarding_priority=False, last_tap_at_ms=None)
                      for i, uid in enumerate(CARD_UIDS, 1)}
        self.readers = {}
        self.events = {}
        self.last_scan = None
        self.last_seen = {}

    @staticmethod
    def _hash(value):
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    def dump(self):
        return copy.deepcopy(dict(version=1, cards=self.cards, readers=self.readers,
                                  events=self.events, last_scan=self.last_scan))

    def restore(self, value):
        if value is None:
            return  # Upgrade old journals with the four explicitly supplied UIDs.
        if not isinstance(value, dict) or value.get('version') != 1:
            raise ValueError('Invalid RFID journal.')
        cards, readers, events = (value.get(key) for key in ('cards', 'readers', 'events'))
        if not isinstance(cards, dict) or set(cards) != set(self.cards):
            raise ValueError('Invalid RFID card registry.')
        for key, card in cards.items():
            if (not isinstance(card, dict) or card.get('id') != key or card.get('uid') != self.cards[key]['uid']
                    or card.get('passenger_type') not in PROFILE_TYPES
                    or not isinstance(card.get('custom_label'), str) or len(card['custom_label']) > 60
                    or any(not isinstance(card.get(flag), bool) for flag in ('priority', 'extra_time', 'onboard', 'boarding_priority'))
                    or card.get('seat_type') not in {None, 'priority', 'standard'}
                    or card['onboard'] != (card['seat_type'] is not None)
                    or card.get('last_tap_at_ms') is not None and not self._finite(card['last_tap_at_ms'])):
                raise ValueError('Invalid RFID card record.')
        if not isinstance(readers, dict) or len(readers) > self.MAX_READERS:
            raise ValueError('Invalid RFID reader registry.')
        for key, reader in readers.items():
            if (not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key) or not isinstance(reader, dict)
                    or not isinstance(reader.get('label'), str) or len(reader['label']) > 60
                    or not re.fullmatch(r'[a-f0-9]{64}', str(reader.get('token_hash', '')))):
                raise ValueError('Invalid RFID reader credentials.')
        if (not isinstance(events, dict) or len(events) > self.MAX_EVENTS
                or any(not isinstance(item, dict) or not isinstance(item.get('result'), dict)
                       or not re.fullmatch(r'[a-f0-9]{64}', str(item.get('fingerprint', '')))
                       for item in events.values())):
            raise ValueError('Invalid RFID event journal.')
        if value.get('last_scan') is not None and not isinstance(value['last_scan'], dict):
            raise ValueError('Invalid RFID scan record.')
        self.cards, self.readers, self.events = copy.deepcopy((cards, readers, events))
        self.last_scan = copy.deepcopy(value.get('last_scan'))

    @staticmethod
    def _finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def snapshot(self):
        return dict(cards=copy.deepcopy(list(self.cards.values())),
                    readers=[dict(reader_id=key, label=value['label'], configured=True,
                                  last_seen_at_ms=self.last_seen.get(key, value.get('last_seen_at_ms')))
                             for key, value in self.readers.items()],
                    last_scan=copy.deepcopy(self.last_scan), debounce_ms=round(self.DEBOUNCE_SECONDS * 1000))

    def update_card(self, card_id, passenger_type, custom_label='', priority=False, extra_time=False):
        if card_id not in self.cards:
            raise self.owner.rfid_error('Unknown card.', 404)
        if passenger_type not in PROFILE_TYPES:
            raise self.owner.rfid_error('Choose a supported passenger type.')
        label = custom_label.strip()
        if (len(label) > 60 or any(ord(char) < 32 for char in label)
                or passenger_type == 'custom' and not label):
            raise self.owner.rfid_error('Provide a custom passenger label of 1–60 characters.')
        if passenger_type != 'custom':
            label = ''
            priority = extra_time = passenger_type in {'senior', 'pregnant'}
        self.cards[card_id].update(passenger_type=passenger_type, custom_label=label,
                                   priority=priority, extra_time=extra_time)
        self.owner._changed()
        return self.snapshot()

    def provision(self, reader_id, label):
        if reader_id not in self.readers and len(self.readers) >= self.MAX_READERS:
            raise self.owner.rfid_error('The maximum number of readers is already configured.', 409)
        token = secrets.token_urlsafe(32)
        self.readers[reader_id] = dict(label=label.strip(), token_hash=self._hash(token),
                                      created_at_ms=round(self.owner.clock() * 1000))
        self.owner._changed()
        return dict(reader_id=reader_id, token=token)

    def revoke(self, reader_id):
        if reader_id not in self.readers:
            raise self.owner.rfid_error('Unknown RFID reader.', 404)
        del self.readers[reader_id]
        self.last_seen.pop(reader_id, None)
        self.owner._changed()
        return self.snapshot()

    def authenticate(self, reader_id, authorization):
        reader = self.readers.get(reader_id)
        prefix = 'Bearer '
        token = authorization[len(prefix):] if isinstance(authorization, str) and authorization.startswith(prefix) else ''
        if (not reader or not 20 <= len(token) <= 128
                or not secrets.compare_digest(reader['token_hash'], self._hash(token))):
            raise self.owner.rfid_error('RFID reader credentials are missing or invalid.', 401)
        self.last_seen[reader_id] = round(self.owner.clock() * 1000)

    def window_id(self):
        scenario = self.owner.scenario
        return (f'{scenario.cycle_id}:{round(scenario.started_at * 1000)}'
                if scenario.enabled and scenario.started_at is not None else None)

    def reader_state(self, reader_id):
        accepting = self.owner.scenario.admission_open() and self.owner.vehicle['doors'] == 'open'
        return dict(reader_id=reader_id, server_time_ms=round(self.owner.clock() * 1000),
                    window_id=self.window_id(), accepting=accepting,
                    message='Ready to scan for boarding or alighting.' if accepting else
                    self._closed_message(self.owner.clock()))

    def _closed_message(self, now):
        scenario = self.owner.scenario
        if (scenario.enabled and scenario.started_at is not None
                and now >= scenario.started_at + scenario.MAX_SECONDS):
            return scenario.RFID_CLOSED
        return 'RFID access is closed. Wait until the bus stops and the doors are open.'

    def tap(self, *, uid, event_id, reader_id, source, window_id=None, observed_at_ms=None):
        uid = canonical_uid(uid)
        now = self.owner.clock()
        card = next((card for card in self.cards.values() if card['uid'] == uid), None)
        fingerprint = self._hash(repr((uid, source, window_id, observed_at_ms)))
        key = self._hash(reader_id + ':' + event_id)
        previous = self.events.get(key)
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise self.owner.rfid_error('A retried event must contain the same card and scan data.', 409)
            return dict(copy.deepcopy(previous['result']), duplicate=True)
        action = 'alighting' if card and card['onboard'] else 'boarding' if card else None
        accepted = False
        scenario = self.owner.scenario
        if source == 'hardware' and (not self._finite(observed_at_ms)
                                    or not -250 <= now * 1000 - observed_at_ms <= self.MAX_EVENT_AGE_MS):
            code, message = 'stale', 'This scan expired. Please remove the card and tap again.'
        elif source == 'hardware' and window_id != self.window_id():
            code, message = 'stale', 'The bus boarding window changed. Please remove the card and tap again.'
        elif not scenario.admission_open(now) or self.owner.vehicle['doors'] != 'open':
            code, message = 'closed', self._closed_message(now)
            if message == scenario.RFID_CLOSED and not any(
                    item['message'] == message and 0 <= now * 1000 - item['at_ms'] < 5000
                    for item in scenario.announcement_events):
                # A retry reuses its receipt above; rapid new taps also must not
                # fill the announcement queue with the same refusal.
                scenario.announce(message)
        elif card is None:
            code, message = 'unknown_card', 'This card is not registered. Please ask the operator for assistance.'
        elif card['last_tap_at_ms'] is not None and now * 1000 - card['last_tap_at_ms'] < self.DEBOUNCE_SECONDS * 1000:
            code, message = 'debounced', 'Repeated scan ignored. Remove the card before tapping again.'
        else:
            card['last_tap_at_ms'] = round(now * 1000)
            opaque_event = 'card:' + key[:48]
            if action == 'boarding':
                seats = scenario.seats()
                requested = 'priority' if card['priority'] else 'standard'
                reassigned = (requested == 'priority' and not seats['priority']['available']
                              and seats['standard']['available'] and scenario.priority_regular)
                assigned = ('priority' if requested == 'priority' and (seats['priority']['available'] or reassigned)
                            or requested == 'standard' and not seats['standard']['available'] else 'standard')
                scenario.control('rfid', seat_type=requested, event_id=opaque_event)
                accepted = scenario.last_admission['allowed']
                if accepted:
                    if reassigned:
                        other = next((item for item in self.cards.values() if item['onboard']
                                      and item['seat_type'] == 'priority' and not item['boarding_priority']), None)
                        if other:
                            other['seat_type'] = 'standard'
                    card.update(onboard=True, seat_type=assigned, boarding_priority=card['priority'])
                    scenario.note_passengers()
                    code, message = 'boarded', 'Boarding recorded. Welcome aboard.'
                    if card['priority']:
                        scenario.announce(PRIORITY_MESSAGE)
                else:
                    code, message = 'full', scenario.last_admission['message']
            else:
                # Remove this UID's simulated allocation, not whichever profile
                # happens to be selected after it boarded. The camera correction
                # remains a separate baseline; a scan adjusts it exactly once.
                seat = card['seat_type']
                recorded_before = sum(scenario.occupied.values())
                if scenario.occupied[seat] <= 0:
                    seat = next((key for key in ('standard', 'priority') if scenario.occupied[key] > 0), seat)
                if scenario.occupied[seat] > 0:
                    scenario.occupied[seat] -= 1
                elif scenario.occupancy_total() > 0:
                    scenario.camera_adjustment -= 1
                scenario.movement_balance -= 1
                if seat == 'priority' and not card['boarding_priority']:
                    scenario.priority_regular = max(0, scenario.priority_regular - 1)
                scenario.priority_regular = min(scenario.priority_regular, scenario.occupied['priority'])
                scenario._admission(True, 'Alighting recorded. Thank you for travelling with us.', opaque_event)
                scenario._extend(scenario.ACTIVITY_SECONDS, now)
                scenario._remember(opaque_event, 'alight:' + seat, now,
                                   sum(scenario.occupied.values()) - recorded_before)
                card.update(onboard=False, seat_type=None, boarding_priority=False)
                accepted, code, message = True, 'alighted', 'Alighting recorded. Thank you for travelling with us.'
        result = dict(accepted=accepted, allowed=accepted, code=code, action=action, direction=action,
                      message=message, card_id=card['id'] if card else None, card_name=card['name'] if card else None,
                      passenger_type=card['passenger_type'] if card else None,
                      onboard=card['onboard'] if card else False, count=scenario.occupancy_total(),
                      seats=scenario.seats(), duplicate=False, event_id=event_id,
                      at_ms=round(now * 1000), source=source)
        self.last_scan = dict(result, uid=uid, reader_id=reader_id)
        self.events[key] = dict(fingerprint=fingerprint, result=copy.deepcopy(result))
        while len(self.events) > self.MAX_EVENTS:
            del self.events[next(iter(self.events))]
        if reader_id in self.readers:
            self.readers[reader_id]['last_seen_at_ms'] = round(now * 1000)
        self.owner._changed()
        return result
