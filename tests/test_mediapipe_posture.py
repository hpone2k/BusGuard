from types import SimpleNamespace
import copy

import numpy as np
import pytest

from vision.config import Settings
from vision.media import draw_posture
from vision.mediapipe_pose import CONNECTIONS, MediaPipeSeating, pose_quality, unpack_pose
from vision.schema import Detection, Options
from vision.seating import classify_posture
from vision.sessions import Sessions
from test_seating import PostureBackend, run_frame


def landmarks(posture='standing'):
    result = [SimpleNamespace(x=.5, y=.18, z=0., visibility=.95, presence=.99) for _ in range(33)]
    for shoulder, hip, knee, ankle, x in ((11, 23, 25, 27, .35), (12, 24, 26, 28, .45)):
        result[shoulder].x, result[shoulder].y = x, .25
        result[hip].x, result[hip].y = x, .45
        result[knee].x, result[knee].y = (x + .23, .45) if posture == 'seated' else (x, .65)
        result[ankle].x, result[ankle].y = (x + .23, .7) if posture == 'seated' else (x, .85)
    return result


@pytest.mark.parametrize('posture', ['standing', 'seated'])
def test_mediapipe_33_points_preserve_pixel_angles_and_visible_geometry(posture):
    pose = unpack_pose(landmarks(posture), 1280, 720)
    assert len(pose['landmarks']) == 33
    assert classify_posture(pose['keypoints'])[0] == posture
    assert len(pose['keypoints']) == 17


@pytest.mark.parametrize('invalid', ['offscreen', 'invisible', 'absent', 'nonfinite'])
def test_occluded_or_extrapolated_media_pipe_joints_cannot_become_seated(invalid):
    points = landmarks('seated')
    if invalid == 'offscreen':
        points[27].x = -0.05
    elif invalid == 'invisible':
        points[27].visibility = .2
    elif invalid == 'absent':
        points[27].presence = .2
    else:
        points[27].x = float('nan')
    pose = unpack_pose(points, 1280, 720)
    assert pose is None or classify_posture(pose['keypoints'])[0] == 'unknown'


def test_disabled_default_skips_posture_work_and_returns_explicit_status(tmp_path):
    sessions = Sessions(Settings(data_dir=tmp_path), None)
    backend = PostureBackend()
    session = sessions.create(Options(prompt='person'), 'live', 'inside')
    result = run_frame(sessions, session, backend, 0, 0)
    assert not backend.calls
    assert result['seating_summary']['status'] == 'disabled'
    assert not result['seating_summary']['complete']
    assert result['posture_enabled'] is False


def test_enabled_but_no_literal_person_category_skips_pose(tmp_path):
    sessions = Sessions(Settings(data_dir=tmp_path), None)
    backend = PostureBackend()
    session = sessions.create(Options(prompt='wheelchair, laptop', posture_enabled=True), 'live', 'inside')
    result = run_frame(sessions, session, backend, 0, 0)
    assert not backend.calls
    assert result['seating_summary']['status'] == 'unavailable'
    assert 'Add person' in result['seating_summary']['reason']


def test_missing_local_model_keeps_objects_and_unknown_posture(tmp_path):
    model = MediaPipeSeating(tmp_path / 'absent.task')
    assert not model.info['available']
    raw = [Detection('person', [.2, .1, .7, .9], .9, track_id=2)]
    result = model.estimate(np.zeros((720, 1280, 3), np.uint8), raw, raw, 123)
    assert result['unknown'] == 1 and not result['complete']
    assert result['status'] == 'unavailable'
    assert result['engine'] == 'MediaPipe Pose Landmarker'


def test_matched_pose_emits_skeleton_and_predicted_identity_does_not(tmp_path):
    model = MediaPipeSeating(tmp_path / 'absent.task')
    model.info.update(available=True, error=None)
    points = landmarks()
    pose = unpack_pose(points, 1280, 720)
    model._predict = lambda frame: SimpleNamespace(pose_landmarks=[points])
    raw = [Detection('person', pose['bbox'], .9, track_id=1)]
    frame = np.zeros((720, 1280, 3), np.uint8)
    result = model.estimate(frame, raw, raw, 123)
    assert result['standing'] == 1 and result['complete']
    assert len(result['occupants'][0]['landmarks']) == 33
    assert result['connections'] == CONNECTIONS
    raw[0].predicted = True
    result = model.estimate(frame, raw, raw, 124)
    assert result['unknown'] == 1 and not result['complete']
    assert 'landmarks' not in result['occupants'][0]


def test_capacity_cannot_assert_all_people_accounted_for(tmp_path):
    model = MediaPipeSeating(tmp_path / 'absent.task', max_people=1)
    model.info.update(available=True, error=None)
    points = landmarks('seated')
    pose = unpack_pose(points, 1280, 720)
    model._predict = lambda frame: SimpleNamespace(pose_landmarks=[points])
    raw = [Detection('person', pose['bbox'], .9, track_id=1)]
    result = model.estimate(np.zeros((720, 1280, 3), np.uint8), raw, raw, 123)
    assert result['capacity_reached'] and not result['complete']
    assert result['seated'] == 1


def test_crop_refinement_is_bounded_for_crowded_scenes(tmp_path):
    model = MediaPipeSeating(tmp_path / 'absent.task', max_people=3)
    model.info.update(available=True, error=None)
    calls = []
    def predict(frame):
        calls.append(frame.shape)
        return SimpleNamespace(pose_landmarks=[])
    model._predict = predict
    raw = [Detection('person', [.1, .1, .4, .9], .9, track_id=index) for index in range(20)]
    result = model.estimate(np.zeros((720, 1280, 3), np.uint8), raw, raw, 123)
    assert len(calls) == 3  # One full frame, at most max_people - 1 refinements.
    assert result['crop_refinements'] == 2
    assert result['capacity_reached'] and not result['complete']
    assert result['seated'] == result['standing'] == 0


def test_failure_does_not_reuse_previous_pose(tmp_path):
    model = MediaPipeSeating(tmp_path / 'absent.task')
    model.info.update(available=True, error=None)
    model._predict = lambda frame: (_ for _ in ()).throw(RuntimeError('bad frame'))
    raw = [Detection('person', [.2, .1, .7, .9], .9, track_id=2)]
    result = model.estimate(np.zeros((720, 1280, 3), np.uint8), raw, raw, 123)
    assert result['status'] == 'unavailable' and not result['complete']
    assert all('landmarks' not in item for item in result['occupants'])


def test_crop_extrapolated_joints_remain_invisible_in_full_frame(tmp_path):
    model = MediaPipeSeating(tmp_path / 'absent.task')
    points = landmarks()
    points[27].y = 1.08  # Inside the whole frame after projection, outside the actual crop.
    # Build a broad, visible skeleton envelope so this crop is accepted for the
    # target, then verify that the extrapolated ankle still loses confidence.
    points[15].x, points[15].y = .04, .45
    points[16].x, points[16].y = .96, .45
    points[0].y = .02
    points[28].y = .98
    model._predict = lambda frame: SimpleNamespace(pose_landmarks=[points])
    raw = [Detection('person', [.2, .2, .7, .7], .9)]
    poses = []
    model._refine(np.zeros((720, 1280, 3), np.uint8), raw, poses)
    assert len(poses) == 1
    assert 0 < poses[0]['landmarks'][27]['y'] < 1
    assert poses[0]['landmarks'][27]['visibility'] == 0
    assert classify_posture(poses[0]['keypoints'])[0] == 'unknown'


def test_legacy_skeleton_is_not_rendered_by_semantic_posture_overlay():
    frame = np.zeros((720, 1280, 3), np.uint8)
    pose = unpack_pose(landmarks(), 1280, 720)
    summary = {'status': 'observed', 'occupants': [{'posture': 'standing', 'landmarks': pose['landmarks']}],
               'connections': CONNECTIONS}
    assert not draw_posture(frame.copy(), summary).any()
    summary['status'] = 'stale'
    assert not draw_posture(frame.copy(), summary).any()
    summary['status'] = 'observed'
    for point in summary['occupants'][0]['landmarks']:
        point['visibility'] = .2
    assert not draw_posture(frame.copy(), summary).any()


def test_full_model_and_whole_bus_capacity_are_the_defaults():
    settings = Settings()
    assert settings.pose_model.endswith('pose_landmarker_full.task')
    assert settings.pose_max_people == 27


@pytest.mark.parametrize('people, complete', [(26, True), (27, False)])
def test_full_cabin_can_be_complete_but_overflow_sentinel_cannot(tmp_path, people, complete):
    model = MediaPipeSeating(tmp_path / 'absent.task')
    model.info.update(available=True, error=None)
    frame = np.zeros((360, 8192, 3), np.uint8)
    skeletons, raw = [], []
    for index in range(people):
        points = landmarks('seated')
        for point in points:
            point.x = (index + .1 + .7 * point.x) / people
        pose = unpack_pose(points, 8192, 360)
        skeletons.append(points)
        raw.append(Detection('person', pose['bbox'], .9, track_id=index + 1))
    model._predict = lambda frame: SimpleNamespace(pose_landmarks=skeletons)
    result = model.estimate(frame, raw, raw, 123)
    assert result['people'] == result['seated'] == people
    assert result['unknown'] == result['standing'] == 0
    assert result['complete'] is complete
    assert result['capacity_reached'] is not complete
    assert result['max_people'] == 27


def test_weak_matched_joints_are_replaced_only_by_better_evidence(tmp_path, monkeypatch):
    model = MediaPipeSeating(tmp_path / 'absent.task')
    frame = np.zeros((720, 1280, 3), np.uint8)
    weak_points = landmarks()
    weak_points[27].visibility = .56
    weak = unpack_pose(weak_points, 1280, 720)
    strong = unpack_pose(landmarks(), 1280, 720)
    raw = [Detection('person', weak['bbox'], .9, track_id=1)]
    poses = [weak]
    model._predict = lambda frame: SimpleNamespace(pose_landmarks=[landmarks()])
    monkeypatch.setattr('vision.mediapipe_pose.unpack_pose', lambda *args: copy.deepcopy(strong))
    assert model._refine(frame, raw, poses) == 1
    assert poses[0]['refined']
    assert pose_quality(poses[0]) > pose_quality(weak)
    assert poses[0]['landmarks'][27]['visibility'] == .95


def test_refinement_never_prefers_seated_label_over_better_visible_joints(tmp_path, monkeypatch):
    model = MediaPipeSeating(tmp_path / 'absent.task')
    points = landmarks('standing')
    points[27].visibility = .65
    original = unpack_pose(points, 1280, 720)
    candidate = copy.deepcopy(original)
    candidate['keypoints'] = unpack_pose(landmarks('seated'), 1280, 720)['keypoints']
    candidate['keypoints'][15][2] = .56
    raw = [Detection('person', original['bbox'], .9, track_id=1)]
    model._predict = lambda frame: SimpleNamespace(pose_landmarks=[landmarks()])
    monkeypatch.setattr('vision.mediapipe_pose.unpack_pose', lambda *args: copy.deepcopy(candidate))
    poses = [original]
    model._refine(np.zeros((720, 1280, 3), np.uint8), raw, poses)
    assert poses[0] is original
    assert classify_posture(poses[0]['keypoints'])[0] == 'standing'


def test_ambiguous_neighbour_crop_cannot_supply_a_persons_pose(tmp_path, monkeypatch):
    model = MediaPipeSeating(tmp_path / 'absent.task')
    candidate = unpack_pose(landmarks(), 1280, 720)
    raw = [Detection('person', candidate['bbox'], .9, track_id=index) for index in (1, 2)]
    model._predict = lambda frame: SimpleNamespace(pose_landmarks=[landmarks()])
    monkeypatch.setattr('vision.mediapipe_pose.unpack_pose', lambda *args: copy.deepcopy(candidate))
    poses = []
    model._refine(np.zeros((720, 1280, 3), np.uint8), raw, poses)
    assert poses == []


def test_soft_budget_stops_starting_more_crops_and_leaves_unknown_people(tmp_path, monkeypatch):
    model = MediaPipeSeating(tmp_path / 'absent.task', max_people=26, budget_ms=0)
    model.info.update(available=True, error=None)
    calls = []
    model._predict = lambda frame: calls.append(frame.shape) or SimpleNamespace(pose_landmarks=[])
    raw = [Detection('person', [.1, .1, .4, .9], .9, track_id=index) for index in range(8)]
    result = model.estimate(np.zeros((720, 1280, 3), np.uint8), raw, raw, 123)
    assert len(calls) == 1 and result['crop_refinements'] == 0
    assert result['refinement_budget_exhausted']
    assert not result['complete'] and result['unknown'] >= 8


def test_larger_capacity_does_not_allow_unbounded_crops(tmp_path):
    model = MediaPipeSeating(tmp_path / 'absent.task', max_people=26)
    model.info.update(available=True, error=None)
    calls = []
    model._predict = lambda frame: calls.append(frame.shape) or SimpleNamespace(pose_landmarks=[])
    raw = [Detection('person', [.1, .1, .4, .9], .9, track_id=index) for index in range(26)]
    result = model.estimate(np.zeros((720, 1280, 3), np.uint8), raw, raw, 123)
    assert len(calls) == 5 and result['crop_refinements'] == 4
    assert not result['complete']


@pytest.mark.parametrize('crop_failure', ['inference', 'invalid_landmarks'])
def test_failed_optional_crop_keeps_fresh_full_frame_pose_and_other_person_unknown(tmp_path, crop_failure):
    model = MediaPipeSeating(tmp_path / 'absent.task')
    model.info.update(available=True, error=None)
    points = landmarks('standing')
    original = unpack_pose(points, 1280, 720)
    raw = [Detection('person', original['bbox'], .95, track_id=1),
           Detection('person', [.7, .1, .95, .9], .95, track_id=2)]
    calls = []
    def predict(frame):
        calls.append(frame.shape)
        if len(calls) == 1:
            return SimpleNamespace(pose_landmarks=[points])
        if crop_failure == 'inference':
            raise RuntimeError('private native crop error')
        return SimpleNamespace(pose_landmarks=[[object()]])
    model._predict = predict
    result = model.estimate(np.zeros((720, 1280, 3), np.uint8), raw, raw, 123)
    assert len(calls) == 2
    assert result['status'] == 'observed'
    assert result['people'] == 2 and result['standing'] == 1 and result['unknown'] == 1
    assert result['seated'] == 0 and not result['complete']
    assert len(result['occupants'][0]['landmarks']) == 33
    assert result['occupants'][1]['posture'] == 'unknown'
    assert 'landmarks' not in result['occupants'][1]
    assert result['crop_refinements'] == result['crop_errors'] == 1
    assert result['refined_poses'] == 0
    assert 'private native crop error' not in str(result)
    assert model.info['available']


def test_full_frame_failure_still_suppresses_all_posture_and_diagnostics(tmp_path):
    model = MediaPipeSeating(tmp_path / 'absent.task')
    model.info.update(available=True, error=None)
    model._predict = lambda _: (_ for _ in ()).throw(RuntimeError('private frame failure'))
    raw = [Detection('person', [.2, .1, .7, .9], .95, track_id=1)]
    result = model.estimate(np.zeros((720, 1280, 3), np.uint8), raw, raw, 123)
    assert result['status'] == 'unavailable'
    assert result['unknown'] == 1 and result['standing'] == result['seated'] == 0
    assert not result['complete'] and result['unassigned_poses'] == []
    assert all('landmarks' not in row for row in result['occupants'])
    assert result['crop_errors'] == result['crop_refinements'] == 0
    assert 'private frame failure' not in str(result)
