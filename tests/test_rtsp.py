import json
import subprocess
import threading
import time
from collections import deque

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from vision.bus_bridge import VisionBusBridge
from vision.config import Settings
from vision.detection_gate import DetectionGate
from vision.rtsp import CameraRequest, RTSPManager
from vision.scheduler import BusyError, Dispatcher
from vision.schema import Detection
from vision.sessions import Sessions
from vision.seating import seating_evidence


def wait_for(call, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = call()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError("Camera condition did not become true before timeout")


class Pipe:
    """A controllable byte pipe, including short reads and blocked reads."""
    def __init__(self):
        self.condition = threading.Condition()
        self.chunks = deque()
        self.closed = False

    def push(self, data):
        with self.condition:
            self.chunks.append(data)
            self.condition.notify_all()

    def read(self, size):
        with self.condition:
            self.condition.wait_for(lambda: self.chunks or self.closed)
            if self.closed:
                return b""
            data = self.chunks.popleft()
            # Deliberately return less than a full frame.
            chunk = data[:min(size, 65536)]
            if len(chunk) < len(data):
                self.chunks.appendleft(data[len(chunk):])
            return chunk

    def close(self):
        with self.condition:
            self.closed = True
            self.chunks.clear()
            self.condition.notify_all()


class Process:
    def __init__(self):
        self.stdout = Pipe()
        self.done = threading.Event()

    def poll(self):
        return 0 if self.done.is_set() else None

    def terminate(self):
        self.done.set()
        self.stdout.close()

    kill = terminate

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired("decoder", timeout)
        return 0

    def frame(self, value, width=384, height=216):
        self.stdout.push(np.full((height, width, 3), value, np.uint8).tobytes())


class Backend:
    info = {"name": "test", "demo": False, "phrases": False}

    def __init__(self):
        self.seen = []
        self.release = threading.Event()
        self.release.set()

    def warmup(self):
        pass

    def detect(self, frame, options):
        self.seen.append(int(frame[0, 0, 0]))
        self.release.wait(4)
        return [Detection("wheelchair", [.1, .1, .35, .8], .9)]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    backend = Backend()
    dispatcher = Dispatcher(lambda: backend)
    bridge = VisionBusBridge()
    settings = Settings(data_dir=tmp_path, ffmpeg="ffmpeg")
    sessions = Sessions(settings, dispatcher, bridge)
    manager = RTSPManager(settings, sessions, dispatcher, bridge)
    processes = []

    def start(command):
        process = Process()
        processes.append(process)
        return process

    monkeypatch.setattr(manager, "_start_process", start)
    dispatcher.start()
    wait_for(lambda: dispatcher.state == "ready")
    try:
        yield manager, backend, processes, bridge, sessions
    finally:
        backend.release.set()
        manager.close()
        dispatcher.close()


def request(**values):
    return CameraRequest(url="rtsp://camera-user:private-password@192.168.1.20:554/live?token=private-token", name="Outdoor CCTV",
                         options={"prompt": "person, wheelchair", "size": 384, "stabilization": "off", "camera_motion": False}, **values)


def test_camera_request_validates_protocol_hosts_and_secrets():
    for url in ("rtsp://192.168.1.10/live", "rtsps://user:password@camera.local:322/live", "rtsp://[::1]/stream"):
        assert CameraRequest(url=url).url.get_secret_value() == url
    for url in ("file:///video.mp4", "http://camera/live", "rtsp:///live", "rtsp://camera:99999/live", "rtsp://camera:0/live",
                "rtsp://camera\n/live", "rtsp://camera/live%0a", "rtsp://bad host/live", "rtsp://bad..host/live", "rtsp://camera/live#fragment"):
        with pytest.raises(ValidationError):
            CameraRequest(url=url)
    config = request()
    assert "private-password" not in repr(config)
    assert "private-token" not in config.model_dump_json()
    assert CameraRequest(url="rtsp://camera/live").options.prompt == "person, wheelchair, stroller, walker, crutch, cane"
    with pytest.raises(ValidationError):
        CameraRequest(url="rtsp://camera/live", name="\n")


def test_latest_frame_only_and_preview_matches_published_result(rig):
    manager, backend, processes, bridge, _ = rig
    backend.release.clear()
    status = manager.configure("outside", request())
    assert status["enabled"] and status["session_id"]
    process = wait_for(lambda: processes and processes[0])
    process.frame(1)
    wait_for(lambda: backend.seen == [1])
    for value in range(2, 41):
        process.frame(value)
    camera = manager.cameras["outside"]
    wait_for(lambda: camera.sequence == 40)
    assert camera.latest[0] == 40
    assert camera.latest[2].shape == (216, 384, 3)
    assert manager.preview("outside") is None  # No unmatched capture preview.
    assert backend.seen == [1]  # One in-flight inference per camera.
    backend.release.set()
    wait_for(lambda: camera.preview_frame_id == 40)
    assert backend.seen == [1, 40]
    preview = cv2.imdecode(np.frombuffer(manager.preview("outside"), np.uint8), cv2.IMREAD_COLOR)
    assert preview.shape == (216, 384, 3)
    assert abs(int(preview[-1, -1, 0]) - 40) <= 2
    assert np.any(preview != 40)  # Real returned boxes were annotated.
    public = manager.snapshot()
    assert public["cameras"][0]["state"] == "detecting"
    assert public["cameras"][0]["preview_frame_id"] == 40
    assert public["cameras"][0]["preview_available"]
    assert public['cameras'][0]['metrics']['captured_frames'] == 40
    assert public['cameras'][0]['metrics']['processed_frames'] == 2
    assert public['cameras'][0]['metrics']['skipped_frames'] == 38
    assert public['cameras'][0]['metrics']['server_ms'] >= 0
    assert public['cameras'][0]['metrics']['inference_ms'] >= 0
    assert all(secret not in json.dumps(public) for secret in ("192.168.1.20", "private-password", "private-token", "rtsp://"))
    record = bridge.roles["outside"]
    assert record["frame_id"] == 40 and record["detections"][0]["label"] == "wheelchair"
    with camera.lock:
        camera.preview_at = time.monotonic() - 4
    assert manager.preview("outside") is None
    assert not manager.snapshot()["cameras"][0]["preview_available"]


def test_inside_rtsp_runs_while_moving_and_outside_waits_for_stop(rig):
    manager, backend, processes, bridge, sessions = rig
    policy = dict(enabled=False, generation='moving', inside_enabled=True,
                  inside_generation='inside-live', posture_enabled=True, posture_generation=1)
    sessions.detection_gate = DetectionGate(lambda: dict(policy))
    manager.configure('outside', request())
    outside_process = wait_for(lambda: processes and processes[0])
    manager.configure('inside', request())
    inside_process = wait_for(lambda: len(processes) == 2 and processes[1])
    outside_process.frame(10)
    inside_process.frame(20)
    wait_for(lambda: bridge.roles['inside']['frame_id'] == 1)
    assert backend.seen == [20]
    assert bridge.roles['outside']['frame_id'] is None
    assert manager.snapshot()['cameras'][0]['state'] == 'paused_journey'
    assert manager.preview('inside') is not None


def test_two_camera_bursts_skip_backlogs_without_starving_either_role(rig):
    """Controlled decoder/model latency checks scheduling, not visual accuracy."""
    manager, backend, processes, bridge, _ = rig
    backend.release.clear()
    manager.configure('outside', request())
    outside = wait_for(lambda: processes and processes[0])
    manager.configure('inside', request())
    inside = wait_for(lambda: len(processes) == 2 and processes[1])
    outside.frame(1)
    wait_for(lambda: backend.seen == [1])
    inside.frame(101)
    wait_for(lambda: manager.dispatcher.status()['pending'] == 1)
    for sequence in range(2, 41):
        outside.frame(sequence)
        inside.frame(100 + sequence)
    wait_for(lambda: all(camera.sequence == 40 for camera in manager.cameras.values()))
    assert manager.dispatcher.status()['pending'] == 1
    assert backend.seen == [1]  # One shared model owner, one pending frame per role.
    backend.release.set()
    wait_for(lambda: all(camera.preview_frame_id == 40 for camera in manager.cameras.values()))
    assert sorted(backend.seen) == [1, 40, 101, 140]
    assert backend.seen[1] == 101  # Already queued inside work runs before the next outside frame.
    for role, marker in (('outside', 40), ('inside', 140)):
        camera = manager.cameras[role]
        assert camera.processed_frames == 2 and camera.skipped_frames == 38
        assert bridge.roles[role]['frame_id'] == 40
        preview = cv2.imdecode(np.frombuffer(manager.preview(role), np.uint8), cv2.IMREAD_COLOR)
        assert abs(int(preview[-1, -1, 0]) - marker) <= 2


def test_rtsp_posture_completed_after_doors_open_does_not_publish_or_draw(rig):
    manager, backend, processes, bridge, sessions = rig
    policy = dict(enabled=False, generation='moving', inside_enabled=True,
                  inside_generation='inside-live', posture_enabled=True, posture_generation=1)
    sessions.detection_gate = DetectionGate(lambda: dict(policy))
    entered, release = threading.Event(), threading.Event()

    def estimate(frame, tracked, raw, captured_at_ms):
        entered.set()
        release.wait(3)
        return seating_evidence(tracked, raw, [], captured_at_ms)

    backend.estimate_seating = estimate
    config = request()
    config.options = config.options.model_copy(update={'posture_enabled': True})
    manager.configure('inside', config)
    process = wait_for(lambda: processes and processes[0])
    process.frame(15)
    assert entered.wait(2)
    policy.update(posture_enabled=False, posture_generation=2)
    release.set()
    wait_for(lambda: bridge.roles['inside']['frame_id'] == 1)
    public = bridge.snapshot()['sources']['inside']
    assert public['seating_summary']['status'] == 'paused'
    assert public['count_summary'] is not None
    assert manager.cameras['inside'].preview_posture_token is None
    assert manager.preview('inside') is not None


def test_previously_drawn_posture_boxes_are_hidden_immediately_when_door_epoch_changes(rig):
    manager, _, _, _, sessions = rig
    policy = dict(enabled=True, generation=0, inside_enabled=True,
                  inside_generation='inside-live', posture_enabled=True, posture_generation=1)
    sessions.detection_gate = DetectionGate(lambda: dict(policy))
    manager.configure('inside', request())
    camera = manager.cameras['inside']
    with camera.lock:
        camera.preview_at = time.monotonic()
        camera.jpeg = b'preview-with-posture-boxes'
        camera.jpeg_without_posture = b'object-only-preview'
        camera.preview_posture_token = (True, 1)
    assert manager.preview('inside') == b'preview-with-posture-boxes'
    policy.update(posture_enabled=False, posture_generation=2)
    assert manager.preview('inside') == b'object-only-preview'
    policy.update(posture_enabled=True, posture_generation=3)
    assert manager.preview('inside') == b'object-only-preview'


def test_cached_posture_box_expires_without_extending_generic_preview_lifetime(rig):
    manager, _, _, _, sessions = rig
    sessions.detection_gate = DetectionGate(lambda: dict(enabled=True, generation=0, inside_enabled=True,
        inside_generation='inside-live', posture_enabled=True, posture_generation=1))
    manager.configure('inside', request())
    camera = manager.cameras['inside']
    with camera.lock:
        camera.preview_at = time.monotonic()
        camera.jpeg = b'semantic-posture'
        camera.jpeg_without_posture = b'generic-objects'
        camera.preview_posture_token = (True, 1)
        camera.preview_posture_expires_at = time.monotonic() - .01
    assert manager.preview('inside') == b'generic-objects'
    with camera.lock:
        camera.preview_at = time.monotonic() - 4
    assert manager.preview('inside') is None


def test_semantic_posture_boxes_are_visible_only_while_doors_are_closed(rig):
    manager, backend, processes, bridge, sessions = rig
    policy = dict(enabled=True, generation=0, inside_enabled=True,
                  inside_generation='inside-live', posture_enabled=True, posture_generation=1)
    sessions.detection_gate = DetectionGate(lambda: dict(policy))

    def estimate(frame, tracked, raw, captured_at_ms):
        summary = seating_evidence(tracked, raw, [], captured_at_ms)
        summary.update(people=1, standing=1, seated=0, unknown=0,
                       occupants=[dict(track_id=1, posture='standing', bbox=[.4, .15, .65, .85],
                                       reason='LocateAnything matched the person.')])
        return summary

    backend.estimate_seating = estimate
    config = request()
    config.options = config.options.model_copy(update={'posture_enabled': True})
    manager.configure('inside', config)
    process = wait_for(lambda: processes and processes[0])
    process.frame(15)
    camera = manager.cameras['inside']
    wait_for(lambda: camera.preview_frame_id == 1)
    assert camera.preview_posture_token == (True, 1)
    assert camera.jpeg != camera.jpeg_without_posture
    assert bridge.snapshot()['sources']['inside']['seating_summary']['seated'] == 0
    policy.update(posture_enabled=False, posture_generation=2)
    assert manager.preview('inside') == camera.jpeg_without_posture


def test_legacy_joints_do_not_enable_rtsp_semantic_overlay(rig):
    manager, backend, processes, bridge, sessions = rig
    sessions.detection_gate = DetectionGate(lambda: dict(enabled=True, generation=0, inside_enabled=True,
        inside_generation='inside-live', posture_enabled=True, posture_generation=1))

    def estimate(frame, tracked, raw, captured_at_ms):
        summary = seating_evidence(tracked, raw, [], captured_at_ms)
        summary['unassigned_poses'] = [dict(landmarks=[dict(x=.4, y=.4, visibility=.9, presence=.9)] * 33)]
        return summary

    backend.estimate_seating = estimate
    config = request()
    config.options = config.options.model_copy(update={'posture_enabled': True})
    manager.configure('inside', config)
    process = wait_for(lambda: processes and processes[0])
    process.frame(15)
    camera = manager.cameras['inside']
    wait_for(lambda: camera.preview_frame_id == 1)
    assert camera.preview_posture_token is None
    assert camera.jpeg == camera.jpeg_without_posture


def test_replacement_cancels_old_inference_and_disconnect_unblocks_pipe(rig):
    manager, backend, processes, bridge, sessions = rig
    backend.release.clear()
    old_id = manager.configure("outside", request())["session_id"]
    old_camera = manager.cameras["outside"]
    old_process = wait_for(lambda: processes and processes[0])
    old_process.frame(10)
    wait_for(lambda: backend.seen)
    new_id = manager.configure("outside", request())["session_id"]
    assert new_id != old_id and old_id not in sessions.items
    assert old_process.done.is_set()
    assert not old_camera.capture_thread.is_alive() and not old_camera.inference_thread.is_alive()
    backend.release.set()
    new_process = wait_for(lambda: len(processes) == 2 and processes[1])
    new_process.frame(20)
    wait_for(lambda: manager.cameras["outside"].preview_frame_id == 1)
    assert bridge.roles["outside"]["session_id"] == new_id
    assert bridge.roles["outside"]["detections"]
    started = time.monotonic()
    result = manager.disconnect("outside")
    assert time.monotonic() - started < 2
    assert result["state"] == "disconnected" and not result["enabled"]
    assert new_process.done.is_set() and manager.preview("outside") is None
    assert new_id not in sessions.items
    assert bridge.roles["outside"]["_closed"]


def test_stalled_camera_is_terminated_and_retries_are_bounded(rig):
    manager, _, processes, _, _ = rig
    manager.STARTUP_TIMEOUT = .08
    manager.READ_TIMEOUT = .08
    manager.MAX_RETRIES = 1
    manager.configure("inside", request())
    wait_for(lambda: manager.snapshot()["cameras"][1]["state"] == "error")
    status = manager.snapshot()["cameras"][1]
    assert len(processes) == 1 and processes[0].done.is_set()
    assert status["retries"] == 1 and status["age_ms"] is None
    assert "private-password" not in json.dumps(status)


def test_browser_role_takeover_stops_old_camera_without_disconnect_of_replacement(rig):
    manager, _, processes, bridge, sessions = rig
    old_id = manager.configure("outside", request())["session_id"]
    camera = manager.cameras["outside"]
    process = wait_for(lambda: processes and processes[0])
    process.frame(10)
    wait_for(lambda: camera.preview_frame_id == 1)
    replacement = sessions.create(request().options, "live", source_role="outside", source_name="Browser camera")
    process.frame(20)
    wait_for(lambda: manager.snapshot()["cameras"][0]["state"] == "superseded")
    wait_for(lambda: not camera.capture_thread.is_alive() and not camera.inference_thread.is_alive())
    status = manager.snapshot()["cameras"][0]
    assert not status["enabled"] and status["session_id"] is None
    assert not status["preview_available"] and manager.preview("outside") is None
    assert process.done.is_set() and old_id not in sessions.items
    assert replacement.id in sessions.items and not replacement.closed.is_set()
    assert bridge.roles["outside"]["session_id"] == replacement.id
    assert not bridge.roles["outside"]["_closed"]
    # Explicit reconnect takes ownership with a new session and decoder.
    new_id = manager.configure("outside", request())["session_id"]
    assert new_id not in {old_id, replacement.id}
    assert manager.snapshot()["cameras"][0]["enabled"]
    assert bridge.roles["outside"]["session_id"] == new_id
    sessions.delete(replacement.id)


def test_decoder_command_and_two_role_close(rig):
    manager, _, processes, _, sessions = rig
    manager.configure("outside", request())
    manager.configure("inside", request())
    assert len(manager.cameras) == 2
    command, width, height = manager._command(manager.cameras["outside"])
    assert (width, height) == (384, 216)
    assert command[command.index("-rtsp_transport") + 1] == "tcp"
    assert command[command.index("-timeout") + 1] == "5000000"
    assert all(flag not in command for flag in ("-fflags", "-flags", "-analyzeduration", "-probesize"))
    assert command[command.index("-i") + 1] == manager.cameras["outside"].request.url.get_secret_value()
    assert "pad=384:216" in command[command.index("-vf") + 1]
    assert command[-1] == "pipe:1"
    with pytest.raises(ValueError):
        manager.configure("third", request())
    with pytest.raises(ValueError):
        manager.preview("third")
    manager.close()
    assert not sessions.items and not manager.cameras
    assert all(process.done.is_set() for process in processes)
    assert all(not item["enabled"] for item in manager.snapshot()["cameras"])
    with pytest.raises(BusyError):
        manager.configure("outside", request())


def test_camera_query_url_is_preserved_as_one_argument_and_public_status_stays_private(rig):
    manager, _, _, _, _ = rig
    url = "rtsp://camera-user:private-password@192.168.1.20:554/?inst=1&token=private-token"
    manager.configure("outside", CameraRequest(url=url, name="Outdoor CCTV"))
    command, _, _ = manager._command(manager.cameras["outside"])
    assert command[command.index("-i") + 1] == url
    assert command.count(url) == 1
    assert command[-1] == "pipe:1"
    public = json.dumps(manager.snapshot())
    assert all(secret not in public for secret in ("private-password", "private-token", "192.168.1.20", "rtsp://"))


def test_initial_decoder_gets_longer_startup_allowance_but_still_times_out(rig):
    manager, _, processes, _, _ = rig
    manager.MAX_RETRIES = 1
    manager.configure("outside", request())
    process = wait_for(lambda: processes and processes[0])
    camera = manager.cameras["outside"]
    wait_for(lambda: camera.process is process)
    assert not camera.process_has_frame
    with camera.lock:
        camera.process_last_frame = time.monotonic() - 9
    manager._watchdog(camera)
    assert not process.done.is_set()  # Beyond the 8-second established-stream limit.
    with camera.lock:
        camera.process_last_frame = time.monotonic() - 20.1
    manager._watchdog(camera)
    assert process.done.is_set()


def test_first_decoded_frame_switches_to_established_stream_stall_timeout(rig):
    manager, _, processes, _, _ = rig
    manager.MAX_RETRIES = 1
    manager.configure("inside", request())
    process = wait_for(lambda: processes and processes[0])
    camera = manager.cameras["inside"]
    process.frame(10)
    wait_for(lambda: camera.preview_frame_id == 1)
    assert camera.process_has_frame
    with camera.lock:
        camera.process_last_frame = time.monotonic() - 8.1
    manager._watchdog(camera)
    assert process.done.is_set()


def test_reconnect_resets_startup_allowance_after_an_old_process_had_frames(rig):
    manager, _, processes, _, _ = rig
    manager.configure("outside", request())
    first = wait_for(lambda: processes and processes[0])
    camera = manager.cameras["outside"]
    first.frame(10)
    wait_for(lambda: camera.preview_frame_id == 1)
    assert camera.process_has_frame and camera.last_frame_at is not None
    first.terminate()
    second = wait_for(lambda: len(processes) > 1 and processes[1])
    wait_for(lambda: camera.process is second)
    assert not camera.process_has_frame
    assert camera.last_frame_at is not None  # Previous connection history still exists.
    with camera.lock:
        camera.process_last_frame = time.monotonic() - 9
    manager._watchdog(camera)
    assert not second.done.is_set()
    second.frame(20)
    wait_for(lambda: camera.preview_frame_id == 2)
    assert camera.process_has_frame
