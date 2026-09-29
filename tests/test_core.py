import threading
import time
from concurrent.futures import CancelledError

import pytest

from vision.backends import Demo, parse_locate
from vision.schema import Detection, Options, sanitize
from vision.scheduler import Dispatcher, BusyError, SupersededError
from vision.tracking import StableTracker


def wait_ready(dispatcher):
    deadline = time.monotonic() + 5
    while dispatcher.status()["state"] != "ready":
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_exact_category_attribution_and_normalization():
    text = '<ref>caterpillar</ref><box><100><100><200><200></box>'
    assert parse_locate(text, ['cat', 'caterpillar'])[0].label == 'caterpillar'
    assert parse_locate(text, ['CAT', 'CATERPILLAR'])[0].label == 'CATERPILLAR'
    assert parse_locate('<box><-20><10><2000><900></box>', ['person'])[0].bbox == [0, .01, 1, .9]


def test_bare_labels_and_duplicate_boxes():
    box = '<box><100><100><200><200></box>'
    dets = parse_locate('cat' + box + box + 'dog' + box, ['cat', 'dog'])
    assert [d.label for d in dets] == ['cat', 'dog']
    assert sanitize([Detection('bad', [0, 0, float('nan'), 1])]) == []
    assert sanitize([Detection('bad', [1, 1, 0, 0])]) == []


def test_options_validation():
    assert Options(prompt=' Cat, cat, dog ').categories() == ['Cat', 'dog']
    with pytest.raises(ValueError):
        Options(prompt='<ref>person</ref>')
    with pytest.raises(ValueError):
        Options(prompt=',,,').categories()


def test_tracks_expire_and_predictions_are_bounded():
    tracker = StableTracker(max_age=.5)
    detection = Detection('person', [.1, .1, .2, .2], .8)
    assert tracker.update([detection], 0) == []
    first = tracker.update([detection], .033)[0]
    assert tracker.update([], .1)[0].predicted
    assert tracker.update([], .3) == []
    assert tracker.update([detection], 8) == []
    last = tracker.update([detection], 8.033)[0]
    assert first.track_id != last.track_id


def test_tracking_uses_labels_and_motion():
    tracker = StableTracker()
    original = [Detection('cat', [.1,.1,.2,.2]), Detection('dog', [.5,.1,.6,.2])]
    assert tracker.update(original, 0) == []
    first = {d.label:d for d in tracker.update(original, .033)}
    second = tracker.update([Detection('dog', [.48,.1,.58,.2]), Detection('cat', [.12,.1,.22,.2])], .1)
    second = {d.label:d for d in second}
    assert second['dog'].track_id == first['dog'].track_id
    assert second['cat'].track_id == first['cat'].track_id
    assert .1 < second['cat'].bbox[0] < .12
    assert second['cat'].velocity[0] > 0


def test_dispatcher_replaces_pending_frames_and_serializes_calls():
    dispatcher = Dispatcher(Demo, capacity=2)
    dispatcher.start(); wait_ready(dispatcher)
    entered, release = threading.Event(), threading.Event()
    active = dispatcher.submit('first', lambda *_: (entered.set(), release.wait(3)))
    assert entered.wait(1)
    old = dispatcher.submit('camera', lambda *_: 'old')
    latest = dispatcher.submit('camera', lambda *_: 'latest')
    with pytest.raises(SupersededError): old.future.result(timeout=1)
    queued = dispatcher.submit('another', lambda *_: 'another')
    with pytest.raises(BusyError): dispatcher.submit('overflow', lambda *_: None)
    release.set()
    active.future.result(timeout=2)
    assert latest.future.result(timeout=2) == 'latest'
    assert queued.future.result(timeout=2) == 'another'
    dispatcher.close()


def test_dispatcher_cancellation_and_fairness():
    dispatcher = Dispatcher(Demo, capacity=12)
    dispatcher.start(); wait_ready(dispatcher)
    entered, release = threading.Event(), threading.Event()
    active = dispatcher.submit('first', lambda *_: (entered.set(), release.wait(3)))
    entered.wait(1)
    order = []
    background = dispatcher.submit('video', lambda *_: order.append('video'), interactive=False)
    interactive = [dispatcher.submit(str(i), lambda *_, i=i: order.append(i)) for i in range(6)]
    obsolete = dispatcher.submit('cancel-me', lambda *_: order.append('bad'))
    dispatcher.cancel('cancel-me')
    with pytest.raises(SupersededError): obsolete.future.result(timeout=1)
    release.set(); active.future.result(timeout=2)
    background.future.result(timeout=2)
    for work in interactive: work.future.result(timeout=2)
    assert order.index('video') <= 4
    assert 'bad' not in order
    dispatcher.close()


def test_failed_model_startup_is_visible():
    def failure(): raise RuntimeError('missing checkpoint')
    dispatcher = Dispatcher(failure); dispatcher.start(); dispatcher.thread.join(2)
    assert dispatcher.status()['state'] == 'error'
    assert dispatcher.status()['error'] == 'missing checkpoint'
    with pytest.raises(BusyError): dispatcher.submit('x', lambda *_: None)
    dispatcher.close()
