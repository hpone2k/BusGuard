import copy
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from vision.bus_bridge import VisionBusBridge
from vision.config import Settings
from vision.departure import DepartureInterlock
from vision.detection_gate import DetectionGate
from vision.schema import Detection, Options
from vision.seating import age_seating_summary
from vision.semantic_seating import estimate
from vision.sessions import Sessions


BOX = [.1, .1, .4, .9]


def person(identifier=1, box=BOX, predicted=False):
    return Detection('person', list(box), .9, track_id=identifier, predicted=predicted)


def summary(posture_id=1, capture=98700, age=1300, posture='seated', session='inside', epoch=1, empty=False):
    people = [] if empty else [person()]
    classified = [] if empty else [Detection('sitting person' if posture == 'seated' else 'standing person', BOX)]
    result = estimate(people, people, classified, capture)
    result.update(posture_frame_id=posture_id, source_session_id=session, door_generation=epoch,
                  age_ms=age, roster_verified=True)
    return result


def source(posture, generic_frame=1, now=100, people=1, **changes):
    value = dict(kind='live', connected=True, is_demo=False, session_id='inside', frame_id=generic_frame,
                 age_ms=0, received_at_ms=now * 1000, seating_summary=posture,
                 count_summary=dict(classes={'person': dict(stable=people, status='stable')}, age_ms=0))
    value.update(changes)
    return value


def gate_ready_for_evidence():
    gate = DepartureInterlock()
    gate.update(source({}, now=90, generic_frame=0), closed=True, enabled=True, now=90)
    return gate


def test_async_posture_has_four_second_deadline_without_loosening_legacy_pose():
    result = summary()
    assert age_seating_summary(result, 3999)['status'] == 'observed'
    assert age_seating_summary(result, 4000)['status'] == 'stale'
    legacy = {key: value for key, value in result.items() if key != 'posture_frame_id'}
    assert age_seating_summary(legacy, 1000)['status'] == 'stale'


def test_three_second_confirmation_uses_distinct_semantic_results_not_fast_object_frames():
    gate = gate_ready_for_evidence()
    for index, now in enumerate((100, 101.3, 102.6)):
        evidence = summary(index, (now - 1.3) * 1000)
        gate.update(source(evidence, index + 1, now), closed=True, enabled=True, now=now)
    assert gate.result(102.6)['progress_ms'] == 2600
    assert gate.result(102.6)['observations'] == 3
    assert not gate.result(102.6)['can_depart']
    duplicate = summary(2, 101300, age=1700)
    gate.update(source(duplicate, 20, 103), closed=True, enabled=True, now=103)
    assert gate.result(103)['progress_ms'] == 2600
    assert gate.result(103)['observations'] == 3
    next_result = summary(3, 102600)
    gate.update(source(next_result, 21, 103.9), closed=True, enabled=True, now=103.9)
    assert gate.result(103.9)['can_depart']


def test_two_samples_spanning_three_seconds_cannot_confirm_everyone_seated():
    gate = gate_ready_for_evidence()
    for index, now in enumerate((100, 103.2)):
        gate.update(source(summary(index, (now - 1.3) * 1000), index + 1, now),
                    closed=True, enabled=True, now=now)
    assert gate.result(103.2)['observations'] == 2
    assert not gate.result(103.2)['can_depart']


@pytest.mark.parametrize('invalid', ['standing', 'expired', 'roster', 'source_stale', 'source_changed', 'capture_old'])
def test_invalid_async_evidence_holds_and_restarts_seated_confirmation(invalid):
    gate = gate_ready_for_evidence()
    for index, now in enumerate((100, 101.3, 102.6)):
        gate.update(source(summary(index, (now - 1.3) * 1000), index + 1, now),
                    closed=True, enabled=True, now=now)
    evidence = summary(3, 102600)
    packet = source(evidence, 4, 103.9)
    if invalid == 'standing':
        evidence.update(seated=0, standing=1)
        evidence['occupants'][0]['posture'] = 'standing'
    elif invalid == 'expired':
        evidence['age_ms'] = 4000
    elif invalid == 'roster':
        evidence['roster_verified'] = False
    elif invalid == 'source_stale':
        packet['age_ms'] = 1000
    elif invalid == 'source_changed':
        evidence['source_session_id'] = 'another-camera'
    else:
        evidence['captured_at_ms'] = 101300
    gate.update(packet, closed=True, enabled=True, now=103.9)
    assert not gate.result(103.9)['can_depart']
    assert gate.result(103.9)['progress_ms'] == 0


def test_frozen_generic_frame_cannot_remain_fresh_while_posture_budget_is_four_seconds():
    gate = gate_ready_for_evidence()
    packet = source(summary(), generic_frame=1)
    gate.update(packet, closed=True, enabled=True, now=100)
    gate.update(packet, closed=True, enabled=True, now=101)
    assert not gate.result(101)['can_depart']
    assert gate.result(101)['progress_ms'] == 0


@pytest.mark.parametrize('allow_empty,known,ready', [(True, 0, True), (False, 0, False), (True, 1, False)])
def test_actual_empty_semantic_summary_requires_fresh_zero_whole_cabin_confirmation(allow_empty, known, ready):
    gate = gate_ready_for_evidence()
    for index, now in enumerate((100, 101.3, 102.6, 103.9)):
        evidence = summary(index, (now - 1.3) * 1000, empty=True)
        assert evidence['complete'] is False and evidence['raw_person_count'] == 0
        gate.update(source(evidence, index + 1, now, people=0), closed=True, enabled=True,
                    now=now, allow_empty=allow_empty, expected_people=known)
    assert gate.result(103.9)['can_depart'] is ready


def session_system(tmp_path):
    policy = dict(enabled=True, generation=0, posture_enabled=True, posture_generation=1)
    sessions = Sessions(Settings(data_dir=tmp_path), None, detection_gate=DetectionGate(lambda: dict(policy)))
    session = sessions.create(Options(prompt='person', posture_enabled=True), 'live', 'inside')
    return policy, sessions, session


@pytest.mark.parametrize('change', ['identity', 'new_person', 'missing_person', 'predicted', 'moved', 'ambiguous_raw', 'epoch'])
def test_older_seated_result_never_transfers_to_a_changed_person_or_closed_door_context(tmp_path, change):
    _, sessions, session = session_system(tmp_path)
    people, raw = [person()], [person(None)]
    result = summary(session=session.id)
    if change == 'identity':
        people = [person(2)]
    elif change == 'new_person':
        people.append(person(2, [.6, .1, .9, .9]))
        raw.append(person(None, [.6, .1, .9, .9]))
    elif change == 'missing_person':
        people = raw = []
    elif change == 'predicted':
        people[0].predicted = True
    elif change == 'moved':
        people[0].bbox = [.25, .1, .55, .9]
        raw[0].bbox = people[0].bbox
    elif change == 'ambiguous_raw':
        raw.append(person(None))
    else:
        result['door_generation'] = 0
    validated = sessions._current_semantic_posture(result, session, people, raw, 100000, (True, 1))
    assert validated['status'] == ('awaiting_fresh_frame' if change == 'epoch' else 'observed')
    assert validated['complete'] is False
    assert validated['seated'] == (None if change == 'epoch' else 1 if change == 'new_person' else 0)
    if change != 'epoch':
        assert validated['roster_verified'] is False and validated['unknown'] > 0


def test_matching_current_roster_keeps_semantic_age_capture_and_id_unchanged(tmp_path):
    _, sessions, session = session_system(tmp_path)
    result = summary(session=session.id)
    validated = sessions._current_semantic_posture(result, session, [person()], [person(None)], 100000, (True, 1))
    assert validated['status'] == 'observed' and validated['roster_verified']
    assert validated['captured_at_ms'] == 98700 and validated['posture_frame_id'] == 1
    assert validated['age_ms'] == 1300


def test_semantic_vote_uses_slow_capture_identity_while_generic_person_vote_stays_fast(tmp_path):
    _, sessions, session = session_system(tmp_path)
    for index in range(6):
        session.accept(index)
        result = dict(frame_id=index, captured_at=100000 + index * 250,
                      _detection_generation=0, _posture_token=(True, 1),
                      detections=[person().json()], seating_summary=summary(session=session.id, age=1300 + index * 250))
        sessions.finalize(session, result, threading.Event())
    assert session.posture_voter.last_observed_ms == 98700
    assert result['seating_summary']['count_summary']['coverage_ms'] == 0
    assert result['count_summary']['classes']['person']['stable'] == 1
    session.accept(6)
    result = dict(frame_id=6, captured_at=101500, _detection_generation=0, _posture_token=(True, 1),
                  detections=[person().json()], seating_summary=summary(2, 100200, 1300, session=session.id))
    sessions.finalize(session, result, threading.Event())
    assert result['seating_summary']['count_summary']['classes']['sitting person']['stable'] == 1
    assert result['seating_summary']['count_summary']['last_observed_ms'] == 100200


def test_bridge_never_refreshes_a_republished_semantic_result_age(tmp_path):
    monotonic = [10.]
    bridge = VisionBusBridge(lambda: monotonic[0], lambda: 100 + monotonic[0])
    sessions = Sessions(Settings(data_dir=tmp_path), None, bridge)
    session = sessions.create(Options(prompt='person', posture_enabled=True), 'live', 'inside')
    def publish(frame):
        session.accept(frame)
        result = dict(frame_id=frame, detections=[person().json()], seating_summary=summary(session=session.id))
        bridge.publish(session, result, bridge.receipt())
    publish(1)
    monotonic[0] += .5
    assert bridge.snapshot()['sources']['inside']['seating_summary']['age_ms'] == 1800
    publish(2)  # A buggy caller returns the old age=1300 again.
    assert bridge.snapshot()['sources']['inside']['age_ms'] == 0
    assert bridge.snapshot()['sources']['inside']['seating_summary']['age_ms'] == 1800
    monotonic[0] += 1.
    publish(3)
    assert bridge.snapshot()['sources']['inside']['seating_summary']['age_ms'] == 2800
    monotonic[0] += 1.2
    publish(4)
    assert bridge.snapshot()['sources']['inside']['connected']
    assert bridge.snapshot()['sources']['inside']['seating_summary']['status'] == 'stale'


def test_async_backend_receives_context_and_capture_age_without_blocking_generic_pipeline(tmp_path):
    _, sessions, session = session_system(tmp_path)
    calls = []
    class Backend:
        posture_async = True
        def detect(self, frame, options):
            return [person(None)]
        def estimate_seating(self, frame, tracked, raw, captured, **kwargs):
            calls.append(kwargs)
            return dict(status='warming', engine='LocateAnything', age_ms=0, occupants=[],
                        people=0, seated=0, standing=0, unknown=0, complete=False, captured_at_ms=captured)
    session.accept(1)
    result = sessions.infer(session, np.zeros((32, 32, 3), np.uint8), 1, 100000,
                            None, Backend(), threading.Event(), 25)
    assert calls[0]['context'] == (session.id, 1)
    assert calls[0]['input_age_ms'] >= 25
    sessions.finalize(session, result, threading.Event())
    assert result['count_summary'] is not None
    assert result['seating_summary']['count_summary'] is None


def test_still_image_uses_explicit_waiting_backend_and_never_enters_live_departure(tmp_path):
    sessions = Sessions(Settings(data_dir=tmp_path), None)
    session = sessions.create(Options(prompt='person', posture_enabled=True), 'image', 'inside')
    class Backend:
        posture_async = True
        def detect(self, frame, options):
            return [person(None)]
        def estimate_seating(self, *args, **kwargs):
            raise AssertionError('Still images need the waiting result path.')
        def estimate_seating_image(self, frame, tracked, raw, captured, **kwargs):
            return summary(capture=captured, age=6500, session=kwargs['context'][0], epoch=kwargs['context'][1])
    session.accept(1)
    result = sessions.infer(session, np.zeros((32, 32, 3), np.uint8), 1, 100000,
                            None, Backend(), threading.Event(), 0)
    sessions.finalize(session, result, threading.Event())
    assert result['seating_summary']['count_summary']['status'] == 'instant'
    gate = gate_ready_for_evidence()
    packet = source(result['seating_summary'], now=101.3, kind='image')
    gate.update(packet, closed=True, enabled=True, now=101.3)
    assert not gate.result(101.3)['can_depart']


@pytest.mark.parametrize('blocked', ['open', 'disabled', 'phrase', 'no_person'])
def test_posture_gate_invalidates_pending_background_work_without_stopping_person_counting(tmp_path, blocked):
    policy, sessions, session = session_system(tmp_path)
    calls = []
    class Backend:
        posture_async = True
        def detect(self, frame, options):
            return [person(None)]
        def estimate_seating(self, *args, **kwargs):
            raise AssertionError('The closed-door posture gate should skip inference.')
        def invalidate_seating(self, identity):
            calls.append(identity)
    if blocked == 'open':
        policy.update(posture_enabled=False, posture_generation=2)
    elif blocked == 'disabled':
        session.options = Options(prompt='person', posture_enabled=False)
    elif blocked == 'phrase':
        session.options = Options(prompt='a sitting person', mode='phrase', posture_enabled=True)
    else:
        session.options = Options(prompt='laptop', posture_enabled=True)
    session.accept(1)
    result = sessions.infer(session, np.zeros((32, 32, 3), np.uint8), 1, 100000,
                            None, Backend(), threading.Event(), 0)
    assert calls and all(identity == session.id for identity in calls)
    assert sessions.finalize(session, result, threading.Event())
    assert result['count_summary'] is not None
    assert result['seating_summary']['status'] != 'observed'


def test_deleting_inside_session_invalidates_pending_semantic_context(tmp_path):
    _, sessions, session = session_system(tmp_path)
    canceled = []
    sessions.dispatcher = SimpleNamespace(cancel=lambda key: canceled.append(key))
    invalidated = []
    session.posture_backend = SimpleNamespace(invalidate_seating=lambda identity: invalidated.append(identity))
    sessions.delete(session.id)
    assert invalidated == [session.id]
    assert canceled == ['session:' + session.id]


def test_fresh_partial_semantic_observation_keeps_verified_standing_label_and_hold(tmp_path):
    _, sessions, session = session_system(tmp_path)
    result = summary(posture='standing', session=session.id)
    result['occupants'].append(dict(track_id=None, posture='unknown', bbox=[.6, .1, .9, .9],
                                    reason='An extra person is not uniquely tracked.'))
    result.update(people=2, unknown=1, complete=False)
    validated = sessions._current_semantic_posture(result, session, [person()], [person(None)], 100000, (True, 1))
    assert validated['status'] == 'observed'
    assert validated['people'] == 2 and validated['standing'] == validated['unknown'] == 1
    assert validated['seated'] == 0
    assert validated['roster_verified'] is False and validated['complete'] is False
    assert validated['occupants'][0]['posture'] == 'standing'
    gate = gate_ready_for_evidence()
    packet = source(validated, session_id=session.id)
    gate.update(packet, closed=True, enabled=True, now=100)
    assert gate.result(100)['status'] == 'standing'
    assert gate.result(100)['can_depart'] is False


def test_one_changed_person_does_not_erase_another_verified_standing_label(tmp_path):
    _, sessions, session = session_system(tmp_path)
    second = [.6, .1, .9, .9]
    result = estimate([person(), person(2, second)], [person(None), person(None, second)],
                      [Detection('standing person', BOX), Detection('sitting person', second)], 98700)
    result.update(posture_frame_id=1, source_session_id=session.id, door_generation=1, age_ms=1300)
    moved = [.68, .1, .98, .9]
    tracked = [person(), person(2, moved)]
    raw = [person(None), person(None, moved)]
    validated = sessions._current_semantic_posture(result, session, tracked, raw, 100000, (True, 1))
    assert validated['status'] == 'observed'
    assert validated['standing'] == validated['unknown'] == 1 and validated['seated'] == 0
    assert validated['people'] == 2
    assert validated['occupants'][1]['posture'] == 'unknown'
    assert validated['roster_verified'] is False and validated['complete'] is False


def test_partial_unknown_rows_overlapping_current_person_are_not_counted_twice(tmp_path):
    _, sessions, session = session_system(tmp_path)
    result = summary(posture='standing', session=session.id)
    result['occupants'].append(dict(track_id=None, posture='unknown', bbox=BOX))
    result.update(people=2, unknown=1, complete=False)
    validated = sessions._current_semantic_posture(result, session, [person()], [person(None)], 100000, (True, 1))
    assert validated['people'] == validated['standing'] == 1
    assert validated['unknown'] == 0 and validated['complete'] is False
    assert validated['roster_verified'] is False


def test_same_semantic_frame_losing_a_match_discards_seated_display_vote(tmp_path):
    _, sessions, session = session_system(tmp_path)
    for index, capture in enumerate((98700, 100200)):
        session.accept(index)
        result = dict(frame_id=index, captured_at=capture + 1300, _detection_generation=0,
                      _posture_token=(True, 1), detections=[person().json()],
                      seating_summary=summary(index, capture, session=session.id))
        sessions.finalize(session, result, threading.Event())
    assert result['seating_summary']['count_summary']['classes']['sitting person']['stable'] == 1
    changed = summary(1, 100200, session=session.id, age=1500)
    changed['occupants'][0]['posture'] = 'unknown'
    changed.update(seated=0, unknown=1, complete=False, roster_verified=False)
    session.accept(2)
    result = dict(frame_id=2, captured_at=101700, _detection_generation=0, _posture_token=(True, 1),
                  detections=[person().json()], seating_summary=changed)
    sessions.finalize(session, result, threading.Event())
    counts = result['seating_summary']['count_summary']
    assert counts['status'] == 'warming' and counts['coverage_ms'] == 0
    assert counts['classes']['sitting person']['raw'] == 0
    assert counts['classes']['uncertain posture']['raw'] == 1
    assert counts['classes']['sitting person']['stable'] is None
