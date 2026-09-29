"""Real card identity accounting, replay protection and reader access scope."""
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from vision.api import create_app
from vision.assistance import AssistanceController, AssistanceError
from vision.backends import Demo
from vision.config import Settings
from vision.rfid import CARD_UIDS, PRIORITY_MESSAGE, canonical_uid
from timing_helpers import assume_clear_standing_check


class Clock:
    def __init__(self):
        self.now = 1000.

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def system(tmp_path):
    clock = Clock()
    controller = AssistanceController(tmp_path / 'assistance.json', clock=clock)
    assume_clear_standing_check(controller)
    return controller, clock


def open_doors(controller, clock):
    controller.scenario_control('start')
    clock.advance(.8)
    controller.tick()
    assert controller.vehicle['doors'] == 'open'


def tap(controller, uid=CARD_UIDS[0], event='tap-1', source='simulation'):
    return controller.rfid_call('test_tap', uid=uid, event_id=event, source=source)


def reader(controller, name='busguard-esp32'):
    token = controller.rfid_call('provision', reader_id=name, label='Door reader')['token']
    return name, 'Bearer ' + token


def hardware_body(controller, name, uid=CARD_UIDS[0], event='boot:1'):
    return dict(reader_id=name, uid=uid, event_id=event, window_id=controller.rfid.window_id(),
                observed_at_ms=round(controller.clock() * 1000), source='hardware')


def passenger_state(controller):
    scenario = controller.scenario
    return copy.deepcopy(dict(cards=controller.rfid.cards, occupied=scenario.occupied,
                              count=scenario.occupancy_total(), movement=scenario.movement_balance,
                              camera_adjustment=scenario.camera_adjustment, events=scenario.events,
                              started_at=scenario.started_at, boarding_until=scenario.boarding_until,
                              closed_at=scenario.closed_at, deadline=controller.deadline,
                              dwell_until=controller.dwell_until))


def assert_hardware_directions_closed(controller, name, authorization, stage):
    state = controller.rfid_call('reader_state', reader_id=name, authorization=authorization)
    assert not state['accepting']
    before = passenger_state(controller)
    for uid, action in ((CARD_UIDS[0], 'alighting'), (CARD_UIDS[1], 'boarding')):
        result = controller.rfid_call(
            'tap', authorization=authorization,
            **hardware_body(controller, name, uid=uid, event=f'{stage}:{action}'))
        assert result['code'] == 'closed' and not result['accepted'] and not result['allowed']
        assert result['action'] == action and not result['duplicate']
        assert result['count'] == before['count']
        assert passenger_state(controller) == before


@pytest.mark.parametrize('value', ['04a00001', '04-A0-00-01', '04:A0:00:01'])
def test_uid_normalizes_without_losing_leading_zero(value):
    assert canonical_uid(value) == CARD_UIDS[0]
    assert canonical_uid('00010203') == '00:01:02:03'


@pytest.mark.parametrize('value', ['', '04:A0:00:1', '4-160-0-1', 'G4:A0:00:01', None, '010203'])
def test_bad_uid_is_not_guessed(value):
    with pytest.raises(ValueError):
        canonical_uid(value)


def test_known_cards_seed_only_normal_profiles(system):
    c, _ = system
    cards = c.rfid_call('snapshot')['cards']
    assert [row['uid'] for row in cards] == list(CARD_UIDS)
    assert [row['name'] for row in cards] == ['Card 1', 'Card 2', 'Card 3', 'Card 4']
    assert all(row['passenger_type'] == 'normal' and not row['onboard'] and not row['priority'] for row in cards)


def test_profile_edit_survives_restart_and_keeps_presence(system):
    c, clock = system
    c.rfid_call('update_card', card_id='1', passenger_type='senior', priority=False, extra_time=False)
    open_doors(c, clock)
    assert tap(c)['accepted']
    c.rfid_call('update_card', card_id='1', passenger_type='custom', custom_label='Needs more time', priority=False, extra_time=True)
    restored = AssistanceController(c.path, clock)
    card = restored.rfid.snapshot()['cards'][0]
    assert card['onboard'] and card['seat_type'] == 'priority'
    assert card['passenger_type'] == 'custom' and card['custom_label'] == 'Needs more time'
    assert card['extra_time'] and not card['priority']
    assert restored.vehicle['revalidation_required']
    assert restored.scenario.occupancy_total() == 1


@pytest.mark.parametrize('profile', ['senior', 'pregnant'])
def test_priority_profile_announces_only_on_accepted_boarding(system, profile):
    c, clock = system
    c.rfid_call('update_card', card_id='1', passenger_type=profile)
    open_doors(c, clock)
    first = tap(c)
    assert first['accepted'] and first['action'] == 'boarding' and first['count'] == 1
    assert c.scenario.announcement['message'] == PRIORITY_MESSAGE
    sequence = c.scenario.announcement_sequence
    assert tap(c)['duplicate']
    assert c.scenario.announcement_sequence == sequence
    clock.advance(2.1)
    second = tap(c, event='tap-2')
    assert second['accepted'] and second['action'] == 'alighting' and second['count'] == 0
    assert not second['onboard']
    assert c.scenario.announcement_sequence == sequence


def test_custom_priority_announces_but_normal_does_not(system):
    c, clock = system
    open_doors(c, clock)
    before = c.scenario.announcement_sequence
    tap(c)
    assert c.scenario.announcement_sequence == before
    c.rfid_call('update_card', card_id='2', passenger_type='custom', custom_label='Mobility assistance', priority=True)
    tap(c, uid=CARD_UIDS[1], event='tap-2')
    assert c.scenario.announcement['message'] == PRIORITY_MESSAGE


def test_repeat_read_is_debounced_and_duplicate_does_not_extend_deadline(system):
    c, clock = system
    open_doors(c, clock)
    first = tap(c)
    deadline = c.scenario.boarding_until
    clock.advance(.5)
    assert tap(c)['duplicate']
    assert c.scenario.boarding_until == deadline
    repeated = tap(c, event='tap-2')
    assert repeated['code'] == 'debounced' and not repeated['accepted']
    assert repeated['count'] == first['count'] == 1
    clock.advance(2)
    assert tap(c, event='tap-3')['count'] == 0


def test_conflicting_event_does_not_change_count(system):
    c, clock = system
    open_doors(c, clock)
    tap(c)
    with pytest.raises(AssistanceError, match='same card'):
        tap(c, uid=CARD_UIDS[1])
    assert c.scenario.occupancy_total() == 1


def test_full_bus_rejects_without_card_toggle_or_timer_extension(system):
    c, clock = system
    c.scenario_control('load', priority_occupied=6, standard_occupied=20)
    open_doors(c, clock)
    clock.advance(3)
    before = c.scenario.boarding_until
    result = tap(c)
    assert result['code'] == 'full' and not result['accepted'] and not result['onboard']
    assert result['count'] == 26 and c.scenario.boarding_until == before
    assert c.scenario.announcement['message'] == c.scenario.FULL


def test_closed_unknown_and_emergency_taps_never_change_occupancy(system):
    c, clock = system
    assert tap(c)['code'] == 'closed'
    open_doors(c, clock)
    assert tap(c, uid='00:01:02:03', event='unknown')['code'] == 'unknown_card'
    c.control('emergency', value=True)
    assert tap(c, event='emergency')['code'] == 'closed'
    assert c.scenario.occupancy_total() == 0
    assert not c.rfid.cards['1']['onboard']


def test_card_uses_its_boarded_seat_after_profile_changes(system):
    c, clock = system
    open_doors(c, clock)
    tap(c)
    c.rfid_call('update_card', card_id='1', passenger_type='pregnant')
    clock.advance(2.1)
    assert tap(c, event='out')['count'] == 0
    assert c.scenario.occupied == dict(priority=0, standard=0)


def test_reassignment_preserves_registered_card_seat_identity(system):
    c, clock = system
    c.scenario_control('load', priority_occupied=5, standard_occupied=20)
    open_doors(c, clock)
    tap(c)
    assert c.rfid.cards['1']['seat_type'] == 'priority'
    c.scenario_control('alight', seat_type='standard', event_id='someone-alights')
    c.rfid_call('update_card', card_id='2', passenger_type='senior')
    result = tap(c, uid=CARD_UIDS[1], event='senior')
    assert result['accepted'] and result['count'] == 26
    assert c.rfid.cards['1']['seat_type'] == 'standard'
    assert c.rfid.cards['2']['seat_type'] == 'priority'


def test_hardware_tokens_persist_rotate_and_are_never_listed(system):
    c, clock = system
    name, authorization = reader(c)
    c.rfid_call('reader_state', reader_id=name, authorization=authorization)
    restored = AssistanceController(c.path, clock)
    assert restored.rfid_call('reader_state', reader_id=name, authorization=authorization)
    stored = json.loads(c.path.read_text())
    assert authorization[7:] not in json.dumps(stored)
    assert 'token_hash' not in json.dumps(c.rfid.snapshot())
    new_name, new_auth = reader(c)
    with pytest.raises(AssistanceError, match='credentials'):
        c.rfid_call('reader_state', reader_id=name, authorization=authorization)
    c.rfid_call('reader_state', reader_id=new_name, authorization=new_auth)
    c.rfid_call('revoke', reader_id=name)
    with pytest.raises(AssistanceError):
        c.rfid_call('reader_state', reader_id=name, authorization=new_auth)


def test_hardware_retry_once_stale_and_wrong_window_rejected(system):
    c, clock = system
    name, auth = reader(c)
    open_doors(c, clock)
    body = hardware_body(c, name)
    first = c.rfid_call('tap', authorization=auth, **body)
    clock.advance(2.1)
    assert c.rfid_call('tap', authorization=auth, **body)['duplicate']
    assert first['count'] == c.scenario.occupancy_total() == 1
    stale = hardware_body(c, name, event='stale')
    stale['observed_at_ms'] -= 6000
    assert c.rfid_call('tap', authorization=auth, **stale)['code'] == 'stale'
    changed = hardware_body(c, name, event='old-window')
    changed['window_id'] = 'old:window'
    assert c.rfid_call('tap', authorization=auth, **changed)['code'] == 'stale'
    assert c.scenario.occupancy_total() == 1


@pytest.mark.parametrize('elapsed', [49, 49.999])
@pytest.mark.parametrize('direction', ['boarding', 'alighting'])
def test_last_second_hardware_scan_gets_full_ten_seconds_but_no_new_scan_after_fifty(system, elapsed, direction):
    c, clock = system
    name, auth = reader(c)
    open_doors(c, clock)
    opened = c.scenario.started_at
    # A real registered passenger boards first; another card renews this stop
    # through successive, distinct boarding/alighting scans.
    for index, at in enumerate((9, 18, 27, 36, 45)):
        clock.now = opened + at
        uid = CARD_UIDS[0] if index == 0 else CARD_UIDS[1]
        assert c.rfid_call('tap', authorization=auth,
                          **hardware_body(c, name, uid=uid, event=f'keep:{index}'))['accepted']
    clock.now = opened + elapsed
    uid = CARD_UIDS[0] if direction == 'alighting' else CARD_UIDS[2]
    last = hardware_body(c, name, uid=uid, event='last-accepted')
    receipt = c.rfid_call('tap', authorization=auth, **last)
    assert receipt['accepted'] and receipt['action'] == direction
    assert c.scenario.boarding_until == pytest.approx(opened + elapsed + 10)
    assert c.rfid_call('reader_state', reader_id=name, authorization=auth)['accepting']

    clock.now = opened + 50
    state = c.tick()
    assert state['vehicle']['doors'] == 'open'
    assert not state['scenario']['admission_open']
    assert state['scenario']['admission_remaining_ms'] == 0
    assert state['scenario']['finishing_extensions']
    assert state['scenario']['boarding_remaining_ms'] == round((elapsed - 40) * 1000)
    reader_status = c.rfid_call('reader_state', reader_id=name, authorization=auth)
    assert not reader_status['accepting'] and 'next bus' in reader_status['message']
    before = passenger_state(c)
    rejected = c.rfid_call('tap', authorization=auth,
                           **hardware_body(c, name, uid=CARD_UIDS[3], event='too-late'))
    assert not rejected['accepted'] and rejected['code'] == 'closed'
    assert 'next bus' in rejected['message']
    assert c.scenario.announcement['message'] == c.scenario.RFID_CLOSED
    assert passenger_state(c) == before
    sequence = c.scenario.announcement_sequence
    assert c.rfid_call('tap', authorization=auth, **last)['duplicate']
    assert passenger_state(c) == before
    c.rfid_call('tap', authorization=auth,
                **hardware_body(c, name, uid=CARD_UIDS[3], event='another-rejected'))
    assert c.scenario.announcement_sequence == sequence

    clock.now = c.scenario.boarding_until - .001
    assert c.tick()['vehicle']['doors'] == 'open'
    clock.now = c.scenario.boarding_until
    assert c.tick()['vehicle']['doors'] == 'closing'


def test_late_outside_detection_cannot_shorten_accepted_rfid_extension(system):
    c, clock = system
    open_doors(c, clock)
    for at in (9, 18, 27, 36, 45, 49):
        clock.now = c.scenario.started_at + at
        assert tap(c, event=f'at:{at}')['accepted']
    deadline = c.scenario.boarding_until
    clock.advance(.5)
    c.tick(dict(instance_id='outside', events=[], sources={'outside': dict(
        kind='live', connected=True, is_demo=False, session_id='outside', frame_id=1,
        received_at_ms=clock() * 1000, age_ms=0,
        detections=[dict(label='person', score=.9, predicted=False)])}))
    assert c.scenario.boarding_until == deadline
    assert c.snapshot()['scenario']['boarding_remaining_ms'] == 9500


@pytest.mark.parametrize('ramp_deployed', [False, True], ids=['doors-only', 'ramp-stows-first'])
def test_hardware_waits_through_departure_until_next_stop_fully_opens(system, ramp_deployed):
    c, clock = system
    name, auth = reader(c)
    open_doors(c, clock)
    assert c.rfid_call('reader_state', reader_id=name, authorization=auth)['accepting']
    original_body = hardware_body(c, name)
    receipt = c.rfid_call('tap', authorization=auth, **original_body)
    assert receipt['accepted'] and receipt['count'] == 1
    if ramp_deployed:
        c.vehicle['ramp'] = 'deployed'

    clock.now = c.scenario.boarding_until
    c.tick()
    if ramp_deployed:
        assert c.scenario.phase == 'stowing' and c.vehicle['doors'] == 'open'
        assert_hardware_directions_closed(c, name, auth, 'stowing')
        clock.advance(c.MOVE_SECONDS)
        c.tick()
    assert c.scenario.phase == 'closing'
    assert_hardware_directions_closed(c, name, auth, 'closing')

    clock.advance(c.MOVE_SECONDS)
    c.tick()
    assert c.scenario.phase == 'departure_wait' and c.vehicle['doors'] == 'closed'
    assert_hardware_directions_closed(c, name, auth, 'departure-wait')

    clock.now = max(c.scenario.closed_at + c.scenario.DEPART_SECONDS,
                    c.scenario.notice_at + c.scenario.NOTICE_SECONDS)
    c.tick()
    assert c.scenario.phase == 'travelling' and c.vehicle['motion'] == 'moving'
    assert_hardware_directions_closed(c, name, auth, 'travelling')
    before = passenger_state(c)
    assert c.rfid_call('tap', authorization=auth, **original_body) == receipt | {'duplicate': True}
    assert passenger_state(c) == before
    assert not c.rfid_call('reader_state', reader_id=name, authorization=auth)['accepting']

    c.scenario_control('stop')
    assert c.scenario.phase == 'braking'
    assert_hardware_directions_closed(c, name, auth, 'braking')
    clock.advance(c.scenario.BRAKE_SECONDS)
    c.tick()
    assert c.scenario.phase == 'opening' and c.vehicle['doors'] == 'opening'
    assert_hardware_directions_closed(c, name, auth, 'opening')
    clock.now = c.deadline - .01
    assert_hardware_directions_closed(c, name, auth, 'still-opening')
    clock.now = c.deadline
    c.tick()
    assert c.vehicle['doors'] == 'open' and c.scenario.phase == 'boarding'
    state = c.rfid_call('reader_state', reader_id=name, authorization=auth)
    assert state['accepting'] and state['window_id'] != original_body['window_id']

    alighted = c.rfid_call('tap', authorization=auth, **hardware_body(c, name, event='next-stop:out'))
    assert alighted['accepted'] and alighted['action'] == 'alighting' and alighted['count'] == 0
    # A retry returns its historical receipt even after the passenger has left.
    before = passenger_state(c)
    assert c.rfid_call('tap', authorization=auth, **original_body) == receipt | {'duplicate': True}
    assert passenger_state(c) == before
    boarded = c.rfid_call('tap', authorization=auth,
                          **hardware_body(c, name, uid=CARD_UIDS[1], event='next-stop:in'))
    assert boarded['accepted'] and boarded['action'] == 'boarding' and boarded['count'] == 1
    assert not c.rfid.cards['1']['onboard'] and c.rfid.cards['2']['onboard']


@pytest.mark.parametrize('interrupted_phase', ['boarding', 'closing', 'stowing'])
def test_obstruction_cannot_reopen_expired_hardware_admission(system, interrupted_phase):
    c, clock = system
    name, auth = reader(c)
    open_doors(c, clock)
    assert c.rfid_call('tap', authorization=auth, **hardware_body(c, name))['accepted']
    if interrupted_phase == 'stowing':
        c.vehicle['ramp'] = 'deployed'
    if interrupted_phase == 'boarding':
        c.control('obstruction', value=True)
    clock.now = c.scenario.boarding_until
    c.tick()
    assert c.scenario.phase == interrupted_phase
    c.control('obstruction', value=True)
    assert c.vehicle['doors'] == 'open' and c.scenario.phase == 'boarding'
    assert c.snapshot()['scenario']['phase'] == 'held'
    assert_hardware_directions_closed(c, name, auth, 'expired-obstruction')

    deadline = c.scenario.boarding_until
    c.scenario_control('stop')
    clock.advance(60)
    assert_hardware_directions_closed(c, name, auth, 'held-obstruction')
    assert c.scenario.boarding_until == deadline
    c.control('obstruction', value=False)
    assert c.scenario.phase == 'closing'
    assert_hardware_directions_closed(c, name, auth, 'obstruction-cleared')


@pytest.mark.parametrize('doors,motion', [('closed', 'stationary'), ('opening', 'stationary'),
                                        ('closing', 'stationary'), ('open', 'moving'), ('open', 'braking')])
def test_hardware_requires_fully_open_stationary_vehicle_even_before_timer_expires(system, doors, motion):
    c, clock = system
    name, auth = reader(c)
    open_doors(c, clock)
    assert c.rfid_call('tap', authorization=auth, **hardware_body(c, name))['accepted']
    clock.advance(2.1)
    # Isolate the vehicle guard from the independent scenario-phase guard.
    c.vehicle.update(doors=doors, motion=motion)
    assert c.scenario.phase == 'boarding' and clock() < c.scenario.boarding_until
    assert_hardware_directions_closed(c, name, auth, 'vehicle-guard')


def test_camera_mean_corrects_baseline_then_each_card_adjusts_once(system):
    c, clock = system
    open_doors(c, clock)
    tap(c)
    c.scenario.reconcile_camera(SimpleNamespace(mean=3.4, rounded=3, completed_at=clock() + .01))
    assert c.scenario.occupancy_total() == 3
    clock.advance(2.1)
    assert tap(c, event='out')['count'] == 2
    assert tap(c, event='out')['duplicate']
    assert c.scenario.occupancy_total() == 2
    assert tap(c, uid=CARD_UIDS[1], event='next-person')['count'] == 3


def test_reconciliation_keeps_scans_after_its_sample_window(system):
    c, clock = system
    open_doors(c, clock)
    completed_at = clock()
    clock.advance(.1)
    tap(c)
    c.scenario.reconcile_camera(SimpleNamespace(mean=5., rounded=5, completed_at=completed_at))
    assert c.scenario.occupancy_total() == 6
    clock.advance(2.1)
    completed_at = clock()
    clock.advance(.1)
    tap(c, event='out')
    c.scenario.reconcile_camera(SimpleNamespace(mean=6., rounded=6, completed_at=completed_at))
    assert c.scenario.occupancy_total() == 5


def test_hardware_retries_after_server_restart_keep_original_outcome(system):
    c, clock = system
    name, auth = reader(c)
    open_doors(c, clock)
    body = hardware_body(c, name)
    c.rfid_call('tap', authorization=auth, **body)
    restored = AssistanceController(c.path, clock)
    duplicate = restored.rfid_call('tap', authorization=auth, **body)
    assert duplicate['duplicate'] and duplicate['accepted'] and duplicate['onboard']
    assert restored.scenario.occupancy_total() == 1
    assert restored.vehicle['revalidation_required']


def test_atomic_journal_failure_rolls_back_presence_and_count(system, monkeypatch):
    c, clock = system
    open_doors(c, clock)
    def fail():
        raise OSError('disk unavailable')
    monkeypatch.setattr(c, '_save', fail)
    with pytest.raises(OSError):
        tap(c)
    assert c.scenario.occupancy_total() == 0
    assert not c.rfid.cards['1']['onboard'] and not c.rfid.events
    assert c.vehicle['emergency'] and c.vehicle['revalidation_required']


def test_corrupt_card_registry_holds_and_preserves_file(system):
    c, clock = system
    c.tick()
    stored = json.loads(c.path.read_text())
    stored['rfid']['cards']['1']['uid'] = CARD_UIDS[1]
    text = json.dumps(stored)
    c.path.write_text(text)
    restored = AssistanceController(c.path, clock)
    assert restored.vehicle['revalidation_required'] and restored.vehicle['emergency']
    with pytest.raises(AssistanceError, match='repair'):
        restored.rfid_call('snapshot')
    assert c.path.read_text() == text


def test_public_snapshot_does_not_identify_card_or_profile(system):
    c, clock = system
    c.rfid_call('update_card', card_id='1', passenger_type='custom', custom_label='Private category', priority=True)
    open_doors(c, clock)
    tap(c)
    public = json.dumps(c.snapshot())
    assert all(uid not in public for uid in CARD_UIDS)
    assert 'Private category' not in public and 'token_hash' not in public
    assert 'Card 1' not in public


def test_priority_announcement_history_survives_next_notice_and_expires(system):
    c, clock = system
    c.rfid_call('update_card', card_id='1', passenger_type='senior')
    open_doors(c, clock)
    tap(c)
    c.scenario.announce(c.scenario.WARNING)
    snapshot = c.snapshot()
    assert snapshot['announcements'][0] == c.scenario.WARNING
    assert PRIORITY_MESSAGE in snapshot['announcements']
    history = snapshot['scenario']['announcement_events']
    assert [row['message'] for row in history[-2:]] == [PRIORITY_MESSAGE, c.scenario.WARNING]
    assert all('at_ms' in row for row in history)
    c.control('emergency', value=True)
    assert PRIORITY_MESSAGE not in c.snapshot()['announcements']
    clock.advance(31)
    c.control('reset')
    assert PRIORITY_MESSAGE not in c.snapshot()['announcements']
    restored = AssistanceController(c.path, clock)
    assert restored.scenario.announcement_events == []


def test_hardware_api_scope_and_unknown_card_reporting(tmp_path):
    app = create_app(Settings(backend='demo', data_dir=tmp_path), backend_factory=Demo, lan=True)
    clock = Clock()
    c = app.state.assistance
    c.clock = clock
    local = TestClient(app)
    remote = TestClient(app, client=('192.168.1.88', 2345))
    assert remote.get('/api/rfid/cards').status_code == 403
    assert remote.get('/api/rfid/reader-state?reader_id=busguard-esp32').status_code == 401
    setup = local.post('/api/rfid/readers', json={'reader_id': 'busguard-esp32', 'label': 'Front door'}).json()
    auth = {'Authorization': 'Bearer ' + setup['token']}
    state = remote.get('/api/rfid/reader-state?reader_id=busguard-esp32', headers=auth)
    assert state.status_code == 200 and not state.json()['accepting']
    assert remote.get('/api/rfid/cards', headers=auth).status_code == 403
    assert remote.post('/api/assistance/control', json={'action': 'reset'}, headers=auth).status_code == 403
    open_doors(c, clock)
    state = remote.get('/api/rfid/reader-state?reader_id=busguard-esp32', headers=auth).json()
    body = dict(reader_id='busguard-esp32', uid='00:01:02:03', event_id='boot:1',
                window_id=state['window_id'], observed_at_ms=state['server_time_ms'])
    scan = remote.post('/api/rfid/tap', json=body, headers=auth)
    assert scan.status_code == 200 and scan.json()['code'] == 'unknown_card'
    assert local.get('/api/rfid/cards').json()['last_scan']['uid'] == '00:01:02:03'
    body.update(uid=CARD_UIDS[0], event_id='boot:2')
    assert remote.post('/api/rfid/tap', json=body, headers=auth).json()['count'] == 1
    assert remote.post('/api/rfid/tap', json=body, headers=auth).json()['duplicate']
    # An operator cookie is not a substitute for the scoped reader credential.
    assert local.post('/api/rfid/tap', json=body).status_code == 401
    assert remote.post('/api/rfid/tap', json=body, headers=auth | {'Origin': 'http://evil.example'}).status_code == 403


def test_operator_management_validation_and_download_allowlist(tmp_path):
    app = create_app(Settings(backend='demo', data_dir=tmp_path), backend_factory=Demo)
    client = TestClient(app)
    assert client.put('/api/rfid/cards/1', json={'passenger_type': 'senior'}).json()['cards'][0]['priority']
    assert client.put('/api/rfid/cards/1', json={'passenger_type': 'custom', 'custom_label': ''}).status_code == 400
    assert client.put('/api/rfid/cards/1', json={'passenger_type': 'normal', 'uid': '00:01:02:03'}).status_code == 422
    assert client.get('/api/rfid/firmware/config.h').status_code == 404
    assert client.get('/api/rfid/firmware/voice.env').status_code == 404
    result = client.post('/api/rfid/test-tap', json={'uid': CARD_UIDS[0], 'event_id': 'demo:1'}).json()
    assert result['code'] == 'closed' and result['count'] == 0
