import threading

import pytest

from vision.config import Settings
from vision.detection_gate import DetectionGate
from vision.scheduler import SupersededError
from vision.schema import Options
from vision.seating import age_seating_summary
from vision.sessions import Sessions


def system(tmp_path, kind='live'):
    policy = dict(enabled=True, generation='inside', posture_enabled=True, posture_generation=1)
    sessions = Sessions(Settings(data_dir=tmp_path), None, detection_gate=DetectionGate(lambda: dict(policy)))
    session = sessions.create(Options(prompt='person', posture_enabled=True), kind, 'inside')
    return policy, sessions, session


def packet(session, index, timestamp, postures=('standing', 'seated', 'unknown'), status='observed', epoch=1):
    session.accept(index)
    return dict(frame_id=index, captured_at=timestamp, _detection_generation='inside',
                _posture_token=(True, epoch), detections=[dict(label='person', track_id=i) for i in range(len(postures))],
                seating_summary=dict(status=status, age_ms=0, door_generation=epoch,
                                     captured_at_ms=timestamp, people=len(postures),
                                     standing=postures.count('standing'), seated=postures.count('seated'),
                                     unknown=postures.count('unknown'), complete='unknown' not in postures,
                                     occupants=[dict(track_id=i, posture=posture) for i, posture in enumerate(postures)]))


def accepted(sessions, session, index, timestamp, **kwargs):
    result = packet(session, index, timestamp, **kwargs)
    assert sessions.finalize(session, result, threading.Event())
    return result


def test_posture_classes_are_voted_separately_without_inflating_person_total(tmp_path):
    _, sessions, session = system(tmp_path)
    for index in range(5):
        result = accepted(sessions, session, index, index * 250)
    counts = result['seating_summary']['count_summary']
    assert counts['status'] == 'stable' and counts['window_ms'] == 1000
    assert set(counts['classes']) == {'standing person', 'sitting person', 'uncertain posture'}
    assert all(row['stable'] == 1 for row in counts['classes'].values())
    assert result['count_summary']['classes']['person']['stable'] == 3
    assert set(result['count_summary']['classes']) == {'person'}
    assert result['seating_summary']['standing'] == 1


def test_posture_mode_is_time_weighted_and_does_not_replace_immediate_safety_evidence(tmp_path):
    _, sessions, session = system(tmp_path)
    times = (0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000)
    for index, stamp in enumerate(times):
        current = ('standing',) if stamp < 700 or stamp == 1000 else ('seated',)
        result = accepted(sessions, session, index, stamp, postures=current)
    counts = result['seating_summary']['count_summary']
    assert counts['classes']['standing person']['stable'] == 1
    assert counts['classes']['standing person']['support'] == pytest.approx(.7)
    assert counts['classes']['sitting person']['stable'] == 0
    assert result['seating_summary']['standing'] == 1


@pytest.mark.parametrize('status', ['disabled', 'paused', 'unavailable', 'stale', 'awaiting_fresh_frame'])
def test_nonobserved_posture_discards_previous_vote_window(tmp_path, status):
    _, sessions, session = system(tmp_path)
    for index in range(5):
        accepted(sessions, session, index, index * 250)
    generic_voter = session.count_voter
    result = accepted(sessions, session, 5, 1250, status=status)
    assert result['seating_summary']['count_summary'] is None
    assert session.posture_voter is None and session.count_voter is generic_voter
    resumed = accepted(sessions, session, 6, 1500)
    assert resumed['seating_summary']['count_summary']['status'] == 'warming'
    assert resumed['seating_summary']['count_summary']['coverage_ms'] == 0


def test_new_closed_door_epoch_resets_posture_votes_only(tmp_path):
    policy, sessions, session = system(tmp_path)
    for index in range(5):
        accepted(sessions, session, index, index * 250)
    generic_voter = session.count_voter
    policy['posture_generation'] = 3
    result = accepted(sessions, session, 5, 1250, epoch=3)
    assert result['seating_summary']['count_summary']['status'] == 'warming'
    assert result['seating_summary']['count_summary']['coverage_ms'] == 0
    assert session.count_voter is generic_voter
    assert result['count_summary']['classes']['person']['stable'] == 3


def test_canceled_and_superseded_frames_never_accumulate_posture_votes(tmp_path):
    _, sessions, session = system(tmp_path)
    accepted(sessions, session, 0, 0)
    canceled = threading.Event()
    canceled.set()
    result = packet(session, 1, 500)
    with pytest.raises(SupersededError):
        sessions.finalize(session, result, canceled)
    assert session.posture_voter.last_observed_ms == 0
    result = packet(session, 2, 1000)
    session.accept(3)
    with pytest.raises(SupersededError):
        sessions.finalize(session, result, threading.Event())
    assert session.posture_voter.last_observed_ms == 0


def test_opened_door_during_inference_invalidates_posture_vote_before_publication(tmp_path):
    policy, sessions, session = system(tmp_path)
    accepted(sessions, session, 0, 0)
    result = packet(session, 1, 500)
    policy.update(posture_enabled=False, posture_generation=2)
    assert sessions.finalize(session, result, threading.Event())
    assert result['seating_summary']['status'] == 'paused'
    assert result['seating_summary']['count_summary'] is None
    assert session.posture_voter is None
    assert result['count_summary']['last_observed_ms'] == 500


def test_still_image_posture_counts_are_explicitly_instantaneous(tmp_path):
    _, sessions, session = system(tmp_path, 'image')
    result = accepted(sessions, session, 0, 0)
    counts = result['seating_summary']['count_summary']
    assert counts['status'] == 'instant'
    assert counts['classes']['standing person']['stable'] == 1
    assert counts['classes']['standing person']['support'] is None


@pytest.mark.parametrize('kind', ['image', 'live'])
def test_stale_containing_posture_expires_nested_vote_values_and_keeps_original(tmp_path, kind):
    _, sessions, session = system(tmp_path, kind)
    for index in range(5 if kind == 'live' else 1):
        result = accepted(sessions, session, index, index * 250)
    original = result['seating_summary']
    expired = age_seating_summary(original, 1000)
    counts = expired['count_summary']
    assert expired['status'] == counts['status'] == 'stale'
    assert all(row['raw'] is None and row['stable'] is None for row in counts['classes'].values())
    assert original['count_summary']['classes']['sitting person']['stable'] == 1


def test_duplicate_unknown_id_does_not_hide_unresolved_occupants(tmp_path):
    _, sessions, session = system(tmp_path, 'image')
    result = packet(session, 0, 0, postures=('unknown', 'unknown'))
    for row in result['seating_summary']['occupants']:
        row['track_id'] = 7
    sessions.finalize(session, result, threading.Event())
    assert result['seating_summary']['count_summary']['classes']['uncertain posture']['stable'] == 2
