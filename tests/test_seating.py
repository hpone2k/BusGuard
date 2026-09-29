import threading
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from vision.bus_bridge import VisionBusBridge
from vision.config import Settings
from vision.schema import Detection, Options
from vision.scheduler import SupersededError
from vision.seating import (PoseSeating, age_seating_summary, classify_posture,
                            seating_evidence, unique_matches, unique_pose_matches)
from vision.mediapipe_pose import unpack_pose
from vision.sessions import Sessions
from test_bus_bridge import Clock


BOX = [.1, .1, .4, .9]


def points(posture='seated'):
    result = np.tile([120., 80., .95], (17, 1))
    for shoulder, hip, knee, ankle, x in ((5, 11, 13, 15, 100), (6, 12, 14, 16, 130)):
        result[shoulder, :2] = [x, 100]
        result[hip, :2] = [x, 220]
        result[knee, :2] = [x + 100, 220] if posture == 'seated' else [x, 350]
        result[ankle, :2] = [x + 100, 350] if posture == 'seated' else [x, 480]
    return result.tolist()


def person(track_id=1, predicted=False, bbox=None):
    return Detection('person', bbox or BOX, .95, track_id=track_id, predicted=predicted)


def pose(posture='seated', bbox=None):
    return {'bbox': bbox or BOX, 'keypoints': points(posture)}


def evidence(tracked=None, raw=None, poses=None):
    return seating_evidence([person()] if tracked is None else tracked,
                            [person(None)] if raw is None else raw,
                            [pose()] if poses is None else poses, 1234)


def test_clear_bilateral_pose_uses_pixel_geometry_for_seated_and_standing():
    assert classify_posture(points('seated'))[0] == 'seated'
    assert classify_posture(points('standing'))[0] == 'standing'
    seated = evidence()
    assert seated['people'] == seated['seated'] == 1
    assert seated['standing'] == seated['unknown'] == 0
    assert seated['complete'] and seated['status'] == 'observed'
    standing = evidence(poses=[pose('standing')])
    assert standing['standing'] == 1 and standing['seated'] == 0
    assert standing['complete']  # Complete accounting does not mean everyone seated.


@pytest.mark.parametrize('mutation', ['occluded', 'conflicting', 'short', 'nonfinite', 'normalized', 'missing'])
def test_uncertain_or_unusable_pose_stays_unknown(mutation):
    sample = np.asarray(points())
    if mutation == 'occluded':
        sample[15, 2] = .2
    elif mutation == 'conflicting':
        standing = np.asarray(points('standing'))
        sample[[6, 12, 14, 16]] = standing[[6, 12, 14, 16]]
    elif mutation == 'short':
        sample[13, :2] = sample[11, :2]
    elif mutation == 'nonfinite':
        sample[5, 0] = float('nan')
    elif mutation == 'normalized':
        sample[:, :2] /= [1920, 1080]
    else:
        sample = sample[:, :2]
    assert classify_posture(sample)[0] == 'unknown'


def test_unknown_posture_does_not_become_seated_from_no_standing_detection():
    unclear = pose()
    unclear['keypoints'][13][2] = .1
    summary = evidence(poses=[unclear])
    assert summary['seated'] == summary['standing'] == 0
    assert summary['unknown'] == 1
    assert summary['occupants'][0]['posture'] == 'unknown'
    assert summary['complete']  # Identity covered, posture still unknown.


def test_new_raw_person_and_pose_only_person_cannot_hide_during_tracker_warmup():
    incoming_box = [.6, .1, .9, .9]
    newcomer = person(None, bbox=incoming_box)
    newcomer_pose = pose(bbox=incoming_box)
    both = evidence(raw=[person(None), newcomer], poses=[pose(), newcomer_pose])
    assert both['people'] == 2 and both['unknown'] == 1 and both['seated'] == 1
    assert not both['complete']
    assert both['occupants'][1]['track_id'] is None
    for raw, poses in (([person(None), newcomer], [pose()]),
                       ([person(None)], [pose(), newcomer_pose])):
        summary = evidence(raw=raw, poses=poses)
        assert summary['people'] == 2 and summary['unknown'] == 1
        assert not summary['complete']


def test_missing_ambiguous_duplicate_or_predicted_tracks_never_complete():
    for tracked, raw, poses in (
        ([person()], [], [pose()]),
        ([person()], [person(None)], []),
        ([person()], [person(None)], [pose(), pose()]),
        ([person(1), person(1)], [person(None)], [pose()]),
        ([person(predicted=True)], [person(None)], [pose()]),
        ([person(None)], [person(None)], [pose()]),
    ):
        summary = evidence(tracked, raw, poses)
        assert not summary['complete']
        assert summary['unknown'] > 0 and summary['seated'] == 0
    assert unique_matches([{'bbox': BOX}], [{'bbox': BOX}, {'bbox': BOX}]) == {}


def test_empty_scene_and_nonperson_classes_do_not_authorize_seating():
    summary = evidence([], [Detection('wheelchair', BOX, .9)], [])
    assert summary['people'] == 0 and summary['complete'] is False
    assert summary['status'] == 'observed'
    assert 'cannot be verified' in summary['reason']


def test_local_missing_model_is_explicit_and_does_not_require_download(tmp_path):
    model = PoseSeating(tmp_path / 'absent-pose.pt', 'cpu')
    assert not model.info['available'] and model.model is None
    result = model.estimate(np.zeros((20, 20, 3), np.uint8), [person()], [person(None)], 0)
    assert result['status'] == 'unavailable'
    assert result['unknown'] == 1 and result['seated'] == 0 and not result['complete']


def test_pose_failure_is_unavailable_for_frame_without_breaking_object_results(tmp_path):
    model = PoseSeating(tmp_path / 'absent-pose.pt', 'cpu')
    model.info['available'] = True
    model._predict = lambda _: (_ for _ in ()).throw(RuntimeError('test failure'))
    result = model.estimate(np.zeros((20, 20, 3), np.uint8), [person()], [person(None)], 0)
    assert result['status'] == 'unavailable' and result['unknown'] == 1
    assert not result['complete']
    model.warmup(np.zeros((20, 20, 3), np.uint8))
    assert not model.info['available'] and 'warm-up failed' in model.info['error']


def test_seating_stale_boundary_is_unknown_not_zero_and_aging_is_not_added_twice():
    original = evidence()
    first = age_seating_summary(original, 400)
    assert age_seating_summary(first, 600)['age_ms'] == 600
    assert age_seating_summary(original, 999)['complete']
    expired = age_seating_summary(original, 1000)
    assert expired['status'] == 'stale' and not expired['complete']
    assert expired['people'] is expired['seated'] is expired['standing'] is expired['unknown'] is None
    assert expired['occupants'] == []
    assert original['complete']


class PostureBackend:
    def __init__(self):
        self.calls = []

    def detect(self, frame, options):
        return [person(None)]

    def estimate_seating(self, frame, tracked, raw, captured_at_ms):
        self.calls.append((tracked, raw, captured_at_ms))
        return seating_evidence(tracked, raw, [pose()], captured_at_ms)


def run_frame(sessions, session, backend, frame_id, captured_at, canceled=None):
    session.accept(frame_id)
    return sessions.infer(session, np.zeros((640, 640, 3), np.uint8), frame_id, captured_at,
                          None, backend, canceled or threading.Event(), 0)


def test_inside_pose_hook_receives_raw_people_and_tracks_but_other_sources_skip(tmp_path):
    sessions = Sessions(Settings(data_dir=tmp_path), None)
    backend = PostureBackend()
    for role in ('outside', 'unassigned'):
        session = sessions.create(Options(prompt='person', posture_enabled=True, camera_motion=False), 'live', role)
        result = run_frame(sessions, session, backend, 0, 0)
        assert result['seating_summary'] is None and result['seating_ms'] is None
    assert not backend.calls
    inside = sessions.create(Options(prompt='person', posture_enabled=True, camera_motion=False), 'live', 'inside')
    first = run_frame(sessions, inside, backend, 0, 0)
    assert first['detections'] == []  # Track confirmation is still pending.
    assert first['seating_summary']['people'] == first['seating_summary']['unknown'] == 1
    assert not first['seating_summary']['complete']
    second = run_frame(sessions, inside, backend, 1, 100)
    assert second['seating_summary']['seated'] == 1 and second['seating_summary']['complete']
    assert second['seating_ms'] >= 0
    assert backend.calls[-1][2] == 100


def test_image_posture_is_available_for_inspection_but_untracked_image_is_not_complete(tmp_path):
    sessions = Sessions(Settings(data_dir=tmp_path), None)
    session = sessions.create(Options(prompt='person', posture_enabled=True), 'image', 'inside')
    result = run_frame(sessions, session, PostureBackend(), 0, 0)
    assert result['seating_summary']['status'] == 'observed'
    assert not result['seating_summary']['complete']
    assert result['seating_summary']['unknown'] == 1


@pytest.mark.parametrize('kind', ['image', 'live'])
def test_slow_still_image_preserves_matching_joints_but_live_evidence_expires(tmp_path, kind):
    class JointBackend(PostureBackend):
        def estimate_seating(self, frame, tracked, raw, captured_at_ms):
            visible_pose = pose()
            visible_pose['landmarks'] = [dict(x=.3, y=.4, z=0, visibility=.95, presence=.95) for _ in range(33)]
            return seating_evidence(tracked, raw, [visible_pose], captured_at_ms)

    clock = Clock()
    bridge = VisionBusBridge(clock.monotonic, clock.wall)
    sessions = Sessions(Settings(data_dir=tmp_path), None, bridge)
    session = sessions.create(Options(prompt='person', posture_enabled=True, stabilization='off'), kind, 'inside')
    received = bridge.receipt()
    result = run_frame(sessions, session, JointBackend(), 0, 0)
    assert len(result['seating_summary']['occupants'][0]['landmarks']) == 33
    clock.advance(1.3)  # Simulate the first model/decoder pass taking over a second.
    sessions.finalize(session, result, threading.Event(), received)
    if kind == 'image':
        assert result['seating_summary']['status'] == 'observed'
        assert len(result['seating_summary']['occupants'][0]['landmarks']) == 33
        assert not result['seating_summary']['complete']
    else:
        assert result['seating_summary']['status'] == 'stale'
        assert result['seating_summary']['occupants'] == []
    # Public source data still expires independently. Keeping the direct image
    # response does not turn recorded evidence into live cabin clearance.
    public = bridge.snapshot()['sources']['inside']
    assert public['kind'] == kind
    assert public['seating_summary']['status'] == 'stale'
    assert not public['seating_summary']['complete']
    assert not result['seating_summary']['complete']


def test_phrase_subsets_skip_pose_and_cannot_supply_cabin_clearance(tmp_path):
    sessions = Sessions(Settings(data_dir=tmp_path), None)
    backend = PostureBackend()
    session = sessions.create(Options(prompt='person', posture_enabled=True, mode='phrase', stabilization='off'), 'live', 'inside')
    result = run_frame(sessions, session, backend, 0, 0)
    assert not backend.calls
    assert result['seating_summary']['status'] == 'unavailable'
    assert not result['seating_summary']['complete']
    assert 'subsets' in result['seating_summary']['reason']


def test_backend_without_pose_keeps_object_detections_with_explicit_unknown(tmp_path):
    class ObjectsOnly:
        def detect(self, frame, options):
            return [person(None)]
    sessions = Sessions(Settings(data_dir=tmp_path), None)
    session = sessions.create(Options(prompt='person', posture_enabled=True, stabilization='off'), 'live', 'inside')
    result = run_frame(sessions, session, ObjectsOnly(), 0, 0)
    assert len(result['detections']) == 1
    assert result['seating_summary']['status'] == 'unavailable'
    assert result['seating_summary']['unknown'] == 1


def test_cancellation_during_pose_does_not_publish_posture(tmp_path):
    canceled = threading.Event()
    backend = PostureBackend()
    original = backend.estimate_seating
    def cancel_during_pose(*args):
        canceled.set()
        return original(*args)
    backend.estimate_seating = cancel_during_pose
    bridge = VisionBusBridge()
    sessions = Sessions(Settings(data_dir=tmp_path), None, bridge)
    session = sessions.create(Options(prompt='person', posture_enabled=True), 'live', 'inside')
    with pytest.raises(SupersededError):
        run_frame(sessions, session, backend, 0, 0, canceled)
    assert bridge.snapshot()['sources']['inside']['seating_summary'] is None


def test_bridge_posture_expires_independently_and_disconnect_does_not_retain_clearance(tmp_path):
    clock = Clock()
    bridge = VisionBusBridge(clock.monotonic, clock.wall)
    sessions = Sessions(Settings(data_dir=tmp_path), None, bridge)
    backend = PostureBackend()
    inside = sessions.create(Options(prompt='person', posture_enabled=True, camera_motion=False), 'live', 'inside')
    for frame_id, captured_at in enumerate((0, 100)):
        result = run_frame(sessions, inside, backend, frame_id, captured_at)
        sessions.finalize(inside, result, threading.Event(), bridge.receipt())
    current = bridge.snapshot()['sources']['inside']
    assert current['seating_summary']['complete']
    assert current['seating_ms'] == result['seating_ms']
    clock.advance(1)
    expired = bridge.snapshot()['sources']['inside']
    assert expired['connected']
    assert expired['seating_summary']['status'] == 'stale'
    assert expired['seating_summary']['standing'] is None
    clock.advance(.1)
    result = run_frame(sessions, inside, backend, 2, 200)
    sessions.finalize(inside, result, threading.Event(), bridge.receipt())
    assert bridge.snapshot()['sources']['inside']['seating_summary']['complete']
    bridge.disconnect(inside.id)
    assert not bridge.snapshot()['sources']['inside']['seating_summary']['complete']


def visible_mediapipe_pose():
    joints = [SimpleNamespace(x=.5, y=.18, z=0., visibility=.95, presence=.99) for _ in range(33)]
    for shoulder, hip, knee, ankle, x in ((11, 23, 25, 27, .35), (12, 24, 26, 28, .45)):
        for index, y in ((shoulder, .25), (hip, .45), (knee, .65), (ankle, .85)):
            joints[index].x, joints[index].y = x, y
    return unpack_pose(joints, 1280, 720)


def test_loose_person_rectangle_matches_clear_standing_skeleton_once():
    skeleton = visible_mediapipe_pose()
    wide_person = person(bbox=[.2, .1, .8, .95])
    assert unique_matches([{'bbox': wide_person.bbox}], [skeleton]) == {}
    assert unique_pose_matches([{'bbox': wide_person.bbox}], [skeleton]) == {0: 0}
    summary = seating_evidence([wide_person], [wide_person], [skeleton], 123)
    assert summary['people'] == summary['standing'] == 1
    assert summary['unknown'] == 0 and summary['complete']
    assert summary['unassigned_poses'] == []
    quality = summary['occupants'][0]['pose_quality']
    assert quality['visible_joints'] == quality['total_joints'] == 33
    assert quality['posture_joints_visible'] == quality['posture_joints_total'] == 8
    assert quality['minimum_posture_confidence'] == .95


@pytest.mark.parametrize('second_box', [[.3, .15, .55, .92], [.3, .1, .9, .95]])
def test_nested_or_neighbouring_people_never_share_a_contained_pose(second_box):
    skeleton = visible_mediapipe_pose()
    crowd = [person(1, bbox=[.2, .1, .8, .95]), person(2, bbox=second_box)]
    assert unique_pose_matches([{'bbox': item.bbox} for item in crowd], [skeleton]) == {}
    summary = seating_evidence(crowd, crowd, [skeleton], 123)
    assert summary['standing'] == summary['seated'] == 0
    assert summary['unknown'] == summary['people'] and not summary['complete']
    assert len(summary['unassigned_poses']) == 1
    assert summary['unassigned_poses'][0]['posture'] == 'unknown'


@pytest.mark.parametrize('mutation', ['occluded_anchor', 'offscreen_anchor', 'outside_anchor',
                                       'tiny_torso', 'short_pose', 'mostly_outside'])
def test_weak_or_extrapolated_body_anchors_never_enable_containment_fallback(mutation):
    skeleton = visible_mediapipe_pose()
    if mutation == 'occluded_anchor':
        skeleton['landmarks'][11]['visibility'] = .1
    elif mutation == 'offscreen_anchor':
        skeleton['landmarks'][11]['x'] = -0.01
    elif mutation == 'outside_anchor':
        skeleton['landmarks'][11]['x'] = .19
    elif mutation == 'tiny_torso':
        for index in (11, 12, 23, 24):
            skeleton['landmarks'][index]['y'] = .45
    elif mutation == 'short_pose':
        skeleton['bbox'] = [.3, .2, .55, .4]
    else:
        for point in skeleton['landmarks'][:10]:
            point['x'] = .9
    assert unique_pose_matches([{'bbox': [.2, .1, .8, .95]}], [skeleton]) == {}


def test_legacy_pose_box_matching_keeps_existing_uniqueness_rules():
    assert unique_pose_matches([{'bbox': BOX}], [pose()]) == {0: 0}
    assert unique_pose_matches([{'bbox': BOX}], [pose(), pose()]) == {}
    assert unique_pose_matches([{'bbox': [.05, .05, .95, .95]}], [pose()]) == {}


def test_untracked_visible_skeleton_is_diagnostic_unknown_not_posture_clearance():
    skeleton = visible_mediapipe_pose()
    raw = [person(None, bbox=[.2, .1, .8, .95])]
    summary = seating_evidence([], raw, [skeleton], 123)
    assert summary['people'] == summary['unknown'] == 1
    assert summary['standing'] == summary['seated'] == 0 and not summary['complete']
    assert len(summary['unassigned_poses']) == 1
    diagnostic = summary['unassigned_poses'][0]
    assert diagnostic['posture'] == 'unknown'
    assert len(diagnostic['landmarks']) == 33
    assert 'not uniquely associated' in diagnostic['reason']
    assert diagnostic['pose_quality']['visible_joints'] == 33


def test_fresh_pose_on_predicted_track_is_drawable_only_as_unassigned_unknown():
    skeleton = visible_mediapipe_pose()
    tracked = [person(predicted=True, bbox=skeleton['bbox'])]
    raw = [person(None, bbox=skeleton['bbox'])]
    summary = seating_evidence(tracked, raw, [skeleton], 123)
    assert not summary['complete'] and summary['unknown'] == 1
    assert 'landmarks' not in summary['occupants'][0]
    assert len(summary['unassigned_poses']) == 1
    assert summary['unassigned_poses'][0]['posture'] == 'unknown'


def test_diagnostic_quality_exposes_missing_joint_without_changing_unknown_classification():
    skeleton = visible_mediapipe_pose()
    skeleton['landmarks'][27]['visibility'] = .2
    skeleton['keypoints'][15][2] = .2
    tracked = [person(bbox=skeleton['bbox'])]
    summary = seating_evidence(tracked, tracked, [skeleton], 123)
    occupant = summary['occupants'][0]
    assert occupant['posture'] == 'unknown'
    assert occupant['pose_quality']['visible_joints'] == 32
    assert occupant['pose_quality']['posture_joints_visible'] == 7
    assert occupant['pose_quality']['minimum_posture_confidence'] == .2


def test_disabled_unavailable_and_stale_summaries_never_retain_diagnostic_skeletons():
    skeleton = visible_mediapipe_pose()
    summary = seating_evidence([], [], [skeleton], 123)
    assert len(summary['unassigned_poses']) == 1
    expired = age_seating_summary(summary, 1000)
    assert expired['unassigned_poses'] == [] and expired['occupants'] == []
    assert len(summary['unassigned_poses']) == 1
    unavailable = seating_evidence([], [], [skeleton], 123, available=False)
    assert unavailable['unassigned_poses'] == []
    for status in ('disabled', 'paused', 'unavailable'):
        disabled = copy.deepcopy(summary)
        disabled['status'] = status
        disabled['occupants'] = [{'landmarks': skeleton['landmarks'], 'pose_quality': {'visible_joints': 33}}]
        result = age_seating_summary(disabled, 100)
        assert result['unassigned_poses'] == []
        assert 'landmarks' not in result['occupants'][0]
        assert 'pose_quality' not in result['occupants'][0]


@pytest.mark.parametrize('mutation', ['missing_joints', 'nan', 'invalid_confidence', 'invisible'])
def test_invalid_model_landmarks_do_not_produce_diagnostic_overlay(mutation):
    skeleton = visible_mediapipe_pose()
    if mutation == 'missing_joints':
        skeleton['landmarks'].pop()
    elif mutation == 'nan':
        skeleton['landmarks'][0]['x'] = float('nan')
    elif mutation == 'invalid_confidence':
        skeleton['landmarks'][0]['visibility'] = 3
    else:
        for point in skeleton['landmarks']:
            point['presence'] = .1
    summary = seating_evidence([], [], [skeleton], 123)
    assert summary['unassigned_poses'] == []
    assert not summary['complete']


def test_disjoint_pose_pairs_skip_per_joint_validation(monkeypatch):
    skeleton = visible_mediapipe_pose()
    def expensive_validation(_):
        raise AssertionError('Disjoint rectangles must be rejected before landmark validation.')
    monkeypatch.setattr('vision.seating._landmark_diagnostics', expensive_validation)
    people = [{'bbox': [.7, .1, .95, .9]} for _ in range(26)]
    assert unique_pose_matches(people, [skeleton] * 26) == {}
