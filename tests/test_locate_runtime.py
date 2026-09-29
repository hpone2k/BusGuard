"""Exercise asynchronous semantic posture without loading models or using a GPU."""
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from vision.config import Settings
from vision.locate_runtime import LocatePosture
from vision.schema import Detection


BOX = [.1, .1, .4, .9]


def person(identity=7):
    return dict(label='person', bbox=list(BOX), score=.95, track_id=identity, predicted=False)


def frame(value=1):
    return np.full((12, 12, 3), value, np.uint8)


def wait_for(predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while True:
        value = predicate()
        if value:
            return value
        assert time.monotonic() < deadline, 'Worker condition did not become true.'
        time.sleep(.005)


@pytest.fixture
def runtime_factory(tmp_path, monkeypatch):
    runtimes, release_events = [], []

    def create(infer=None, clock=None, start=True):
        runtime = LocatePosture(Settings(data_dir=tmp_path), clock=clock or time.monotonic)
        runtime.info.update(available=True, error=None)
        runtime.stops = []
        monkeypatch.setattr(runtime, '_stop_process', lambda: runtime.stops.append(True))
        def classify(image):
            runtime.sequence += 1
            return [dict(label='sitting person', bbox=list(BOX))], 500
        monkeypatch.setattr(runtime, '_infer', infer or classify)
        if start:
            runtime.worker = threading.Thread(target=runtime._run, daemon=True)
            runtime.worker.start()
        runtimes.append(runtime)
        return runtime

    create.release_events = release_events
    yield create
    for event in release_events:
        event.set()
    for runtime in runtimes:
        runtime.close()


def test_estimate_returns_while_inference_is_blocked(runtime_factory):
    entered, release = threading.Event(), threading.Event()
    runtime_factory.release_events.append(release)
    def blocked(image):
        entered.set()
        assert release.wait(2)
        return [], 500
    runtime = runtime_factory(blocked)
    started = time.monotonic()
    result = runtime.estimate(frame(), [person()], [person(None)], 100, context=('inside', 'closed-1'))
    elapsed = time.monotonic() - started
    assert result['status'] == 'analyzing'
    assert elapsed < .3
    assert entered.wait(1) and not release.is_set()
    assert result['source_session_id'] == 'inside' and result['door_generation'] == 'closed-1'


def test_one_active_plus_latest_pending_frame_and_inputs_are_copied(runtime_factory):
    entered = queue.Queue()
    release_first, release_second = threading.Event(), threading.Event()
    runtime_factory.release_events.extend([release_first, release_second])
    calls = []
    def controlled(image):
        value = int(image[0, 0, 0])
        calls.append(value)
        entered.put(value)
        assert (release_first if len(calls) == 1 else release_second).wait(2)
        return [], 500
    runtime = runtime_factory(controlled)
    image, tracked, raw = frame(1), [person()], [person(None)]
    runtime.estimate(image, tracked, raw, 100, context=('inside', 'closed-1'))
    assert entered.get(timeout=1) == 1
    image[:] = 99
    tracked[0]['bbox'][0], raw[0]['label'] = .8, 'laptop'
    assert runtime.active['frame'][0, 0, 0] == 1
    assert runtime.active['tracked'][0]['bbox'] == BOX
    assert runtime.active['raw'][0]['label'] == 'person'
    for value in range(2, 31):
        runtime.estimate(frame(value), [person()], [person(None)], value * 100,
                         context=('inside', 'closed-1'))
    with runtime.condition:
        assert runtime.active['capture'] == 100
        assert runtime.pending['capture'] == 3000
    release_first.set()
    assert entered.get(timeout=1) == 30
    assert calls == [1, 30]
    with runtime.condition:
        assert runtime.pending is None and runtime.active['capture'] == 3000


def test_cached_evidence_retains_capture_identity_and_true_age(runtime_factory):
    clock = SimpleNamespace(now=10.)
    release = threading.Event()
    runtime_factory.release_events.append(release)
    calls = []
    def controlled(image):
        calls.append(True)
        assert release.wait(2)
        runtime.sequence += 1
        return [dict(label='sitting person', bbox=list(BOX))], 1500
    runtime = runtime_factory(controlled, clock=lambda: clock.now)
    context = ('inside-session', 'closed-4')
    runtime.estimate(frame(), [person()], [person(None)], 1000, context=context, input_age_ms=125)
    wait_for(lambda: runtime.active is not None)
    clock.now = 11.5
    release.set()
    wait_for(lambda: context in runtime.results)
    first = runtime.estimate(frame(), [person()], [person(None)], 1000, context=context)
    assert first['age_ms'] == 1625
    assert first['captured_at_ms'] == 1000 and first['posture_frame_id'] == 1
    assert first['seated'] == 1 and first['complete']
    first['occupants'][0]['posture'] = 'standing'
    clock.now = 12.
    repeated = runtime.estimate(frame(), [person()], [person(None)], 1000, context=context)
    assert repeated['age_ms'] == 2125
    assert repeated['captured_at_ms'] == 1000 and repeated['posture_frame_id'] == 1
    assert repeated['occupants'][0]['posture'] == 'seated'
    assert calls == [True]  # Re-reading the same capture never schedules another model pass.


def test_session_and_door_epoch_contexts_never_reuse_each_others_results(runtime_factory):
    runtime = runtime_factory()
    first_context = ('inside-1', 'closed-1')
    runtime.estimate(frame(), [person()], [person(None)], 100, context=first_context)
    wait_for(lambda: first_context in runtime.results)
    for context in [('inside-1', 'closed-2'), ('inside-2', 'closed-2')]:
        # Hold the worker outside its condition while examining immediate response.
        with runtime.condition:
            result = runtime.estimate(frame(), [person()], [person(None)], 100, context=context)
            assert result['status'] == 'analyzing'
            assert result['source_session_id'] == context[0] and result['door_generation'] == context[1]
            assert not result['complete'] and result['seated'] == 0
        wait_for(lambda: context in runtime.results)
        result = runtime.estimate(frame(), [person()], [person(None)], 100, context=context)
        assert result['status'] == 'observed'
        assert (result['source_session_id'], result['door_generation']) == context


def test_results_cache_is_bounded_across_old_camera_and_door_contexts(runtime_factory):
    runtime = runtime_factory()
    for epoch in range(7):
        context = ('inside', epoch)
        runtime.estimate(frame(), [person()], [person(None)], epoch + 1, context=context)
        wait_for(lambda: context in runtime.results)
    assert list(runtime.results) == [('inside', epoch) for epoch in range(3, 7)]


def test_invalidation_discards_active_pending_and_cached_old_door_work(runtime_factory):
    entered, release = threading.Event(), threading.Event()
    runtime_factory.release_events.append(release)
    calls = []
    def controlled(image):
        value = int(image[0, 0, 0])
        calls.append(value)
        if value == 20:
            entered.set()
            assert release.wait(2)
        runtime.sequence += 1
        return [dict(label='sitting person', bbox=list(BOX))], 500
    runtime = runtime_factory(controlled)
    other_context, old_context, new_context = ('other-session', 1), ('inside', 3), ('inside', 5)
    runtime.estimate(frame(1), [person()], [person(None)], 10, context=other_context, blocking=True)
    runtime.estimate(frame(10), [person()], [person(None)], 100, context=old_context, blocking=True)
    runtime.estimate(frame(20), [person()], [person(None)], 200, context=old_context)
    assert entered.wait(1)
    runtime.estimate(frame(30), [person()], [person(None)], 300, context=old_context)
    with runtime.condition:
        assert runtime.active['capture'] == 200 and runtime.pending['capture'] == 300
        runtime.invalidate('inside')  # Doors open: both running and queued old-epoch work is invalidated.
        assert runtime.active['canceled'] and runtime.pending is None
        assert old_context not in runtime.results and other_context in runtime.results
        reopened = runtime.estimate(frame(40), [person()], [person(None)], 400, context=new_context)
        assert reopened['status'] == 'analyzing' and reopened['door_generation'] == 5
    release.set()
    wait_for(lambda: new_context in runtime.results)
    assert calls == [1, 10, 20, 40]  # Capture 300 never reaches the worker.
    assert old_context not in runtime.results  # Late completion of capture 200 cannot repopulate it.
    result = runtime.estimate(frame(40), [person()], [person(None)], 400, context=new_context)
    assert result['status'] == 'observed' and result['captured_at_ms'] == 400
    assert result['door_generation'] == 5 and result['source_session_id'] == 'inside'
    assert other_context in runtime.results


def test_worker_error_invalidates_prior_evidence_and_leaves_unknown_without_fallback(runtime_factory):
    calls = []
    def classify_then_fail(image):
        calls.append(True)
        if len(calls) > 1:
            raise RuntimeError('synthetic failure')
        runtime.sequence += 1
        return [dict(label='sitting person', bbox=list(BOX))], 100
    runtime = runtime_factory(classify_then_fail)
    context = ('inside', 1)
    runtime.estimate(frame(), [person()], [person(None)], 100, context=context)
    wait_for(lambda: context in runtime.results)
    runtime.estimate(frame(), [person()], [person(None)], 200, context=context)
    wait_for(lambda: not runtime.info['available'])
    result = runtime.estimate(frame(), [person()], [person(None)], 300, context=context)
    assert result['status'] == 'unavailable'
    assert result['engine'] == 'LocateAnything'
    assert result['unknown'] == 1 and result['seated'] == result['standing'] == 0
    assert not result['complete']
    assert not runtime.results and runtime.pending is None
    wait_for(lambda: runtime.stops)
    assert len(calls) == 2  # No secondary posture model or automatic retry.


def test_blocking_image_uses_local_ids_for_raw_people_without_mutating_inputs(runtime_factory):
    second_box = [.6, .1, .9, .9]
    def classify(image):
        runtime.sequence += 1
        return [dict(label='sitting person', bbox=BOX), dict(label='standing person', bbox=second_box)], 200
    runtime = runtime_factory(classify)
    raw = [Detection('person', list(BOX), .95),
           dict(label='laptop', bbox=[.2, .3, .3, .4], track_id=None),
           dict(label='person', bbox=list(second_box), track_id=None)]
    result = runtime.estimate(frame(), [], raw, 500, context=('test-image', 1), blocking=True)
    assert result['status'] == 'observed' and result['complete']
    assert result['people'] == 2 and result['seated'] == result['standing'] == 1
    assert [row['track_id'] for row in result['occupants']] == [1, 3]
    assert raw[0].track_id is None and raw[2]['track_id'] is None
    assert result['source_session_id'] == 'test-image'


def test_blocking_waiter_reports_worker_failure_as_unavailable(runtime_factory):
    def fail(image):
        raise RuntimeError('synthetic failure')
    runtime = runtime_factory(fail)
    result = runtime.estimate(frame(), [person()], [person(None)], 500,
                              context=('image', 1), blocking=True)
    assert not runtime.info['available']
    assert result['status'] == 'unavailable'
    assert 'stopped' in result['reason']
    assert not result['complete'] and result['unknown'] == 1


def test_blocking_image_timeout_never_returns_another_images_cached_posture(runtime_factory, monkeypatch):
    runtime = runtime_factory()
    context = ('image-session', 1)
    first = runtime.estimate(frame(1), [person()], [person(None)], 100,
                             context=context, blocking=True)
    assert first['captured_at_ms'] == 100 and first['complete']
    original_wait = runtime.condition.wait_for
    # Simulate the bounded 22-second wait expiring without spending 22 seconds.
    # The surrounding condition lock prevents the worker finishing capture 200.
    monkeypatch.setattr(runtime.condition, 'wait_for',
                        lambda predicate, timeout=None: False if timeout == 22 else original_wait(predicate, timeout))
    with runtime.condition:
        result = runtime.estimate(frame(2), [person()], [person(None)], 200,
                                  context=context, blocking=True)
    assert result['captured_at_ms'] == 200
    assert result['status'] == 'unavailable' and not result['complete']


def test_closed_runtime_does_not_schedule_or_return_cached_observation(runtime_factory):
    runtime = runtime_factory()
    context = ('inside', 1)
    runtime.estimate(frame(), [person()], [person(None)], 100, context=context)
    wait_for(lambda: context in runtime.results)
    runtime.close()
    result = runtime.estimate(frame(), [person()], [person(None)], 200, context=context)
    assert result['status'] == 'unavailable' and not result['complete']
    assert runtime.pending is None and not runtime.results
