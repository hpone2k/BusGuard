"""Two local RTSP camera inputs with bounded capture and current-result delivery.

Credentials remain in memory. FFmpeg stderr is deliberately discarded: many
camera errors include their complete URL. Public errors never expose exceptions.
"""

import ipaddress
import re
import subprocess
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .media import draw_detections, draw_posture, posture_boxes, ffmpeg_path, subprocess_flags
from .scheduler import BusyError, SupersededError
from .schema import Options


ROLES = ("outside", "inside")
DEFAULT_PROMPT = "person, wheelchair, stroller, walker, crutch, cane"


class CameraRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: SecretStr
    name: str = Field(default="IP camera", min_length=1, max_length=80)
    options: Options = Field(default_factory=lambda: Options(prompt=DEFAULT_PROMPT, camera_motion=False))

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value):
        value = value.get_secret_value() if isinstance(value, SecretStr) else value
        error = "Enter an RTSP or RTSPS camera address with a valid host."
        if not isinstance(value, str) or not 1 <= len(value) <= 2048:
            raise ValueError(error)
        if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(error)
        if any(ord(c) < 32 or ord(c) == 127 for c in unquote(value)):
            raise ValueError(error)
        try:
            parts = urlsplit(value)
            host = parts.hostname
            port = parts.port
            if parts.scheme not in {"rtsp", "rtsps"} or not host or parts.fragment:
                raise ValueError(error)
            if port is not None and not 1 <= port <= 65535:
                raise ValueError(error)
            try:
                ipaddress.ip_address(host)
            except ValueError:
                labels = host.rstrip(".").encode("idna").decode("ascii").split(".")
                if len(host) > 253 or any(not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label) for label in labels):
                    raise ValueError(error)
        except (ValueError, UnicodeError):
            raise ValueError(error) from None
        return value

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Use a short camera name without control characters.")
        return value


@dataclass
class _Camera:
    role: str
    request: CameraRequest = field(repr=False)
    session: object = field(repr=False)
    stop: threading.Event = field(default_factory=threading.Event)
    wake: threading.Event = field(default_factory=threading.Event)
    lock: object = field(default_factory=threading.RLock)
    state: str = "connecting"
    error: str | None = None
    process: object = field(default=None, repr=False)
    process_started: float = 0
    process_last_frame: float = 0
    process_has_frame: bool = False
    last_frame_at: float | None = None
    last_result_at: float | None = None
    latest: object = field(default=None, repr=False)
    jpeg: bytes | None = field(default=None, repr=False)
    jpeg_without_posture: bytes | None = field(default=None, repr=False)
    preview_posture_token: object = field(default=None, repr=False)
    preview_posture_expires_at: float | None = field(default=None, repr=False)
    preview_at: float | None = None
    preview_frame_id: int | None = None
    sequence: int = 0
    processed_frames: int = 0
    skipped_frames: int = 0
    last_inference_ms: float | None = None
    last_queue_ms: float | None = None
    last_server_ms: float | None = None
    retries: int = 0
    capture_thread: object = None
    inference_thread: object = None


class RTSPManager:
    FRAME_RATE = 15
    STARTUP_TIMEOUT = 20.0
    READ_TIMEOUT = 8.0
    MAX_RETRIES = 5
    PREVIEW_MAX_AGE = 3.0

    def __init__(self, settings, sessions, dispatcher, bridge):
        self.settings, self.sessions = settings, sessions
        self.dispatcher, self.bridge = dispatcher, bridge
        self.lock = threading.RLock()
        self.role_locks = {role: threading.Lock() for role in ROLES}
        self.cameras = {}
        self.stopping = False

    @staticmethod
    def _role(role):
        if role not in ROLES:
            raise ValueError("Camera role must be outside or inside.")
        return role

    def configure(self, role, request):
        self._role(role)
        if not isinstance(request, CameraRequest):
            request = CameraRequest.model_validate(request)
        with self.role_locks[role]:
            with self.lock:
                if self.stopping:
                    raise BusyError("Camera service is shutting down.")
                old = self.cameras.pop(role, None)
            if old:
                self._stop(old)
            session = self.sessions.create(request.options, "live", source_role=role, source_name=request.name)
            camera = _Camera(role, request, session)
            with self.lock:
                if self.stopping:
                    self.sessions.delete(session.id)
                    raise BusyError("Camera service is shutting down.")
                self.cameras[role] = camera
            camera.capture_thread = threading.Thread(target=self._capture, args=(camera,), name=f"rtsp-{role}-capture", daemon=True)
            camera.inference_thread = threading.Thread(target=self._infer, args=(camera,), name=f"rtsp-{role}-detect", daemon=True)
            camera.capture_thread.start()
            camera.inference_thread.start()
            return self._snapshot(role)

    def disconnect(self, role):
        self._role(role)
        with self.role_locks[role]:
            with self.lock:
                camera = self.cameras.pop(role, None)
            if camera:
                self._stop(camera)
        return self._snapshot(role)

    def _stop(self, camera):
        camera.stop.set()
        camera.wake.set()
        self.sessions.delete(camera.session.id)
        with camera.lock:
            process = camera.process
            camera.latest, camera.jpeg = None, None
            camera.jpeg_without_posture, camera.preview_posture_token = None, None
        self._terminate(process)
        for thread in (camera.capture_thread, camera.inference_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=1.5)

    @staticmethod
    def _terminate(process):
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=.6)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=.6)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass

    def close(self):
        with self.lock:
            self.stopping = True
        for role in ROLES:
            self.disconnect(role)

    def snapshot(self):
        return {"cameras": [self._snapshot(role) for role in ROLES]}

    def _snapshot(self, role):
        with self.lock:
            camera = self.cameras.get(role)
            if not camera:
                return {"role": role, "enabled": False, "state": "disconnected", "name": "Outdoor CCTV" if role == "outside" else "Indoor CCTV", "session_id": None,
                        "age_ms": None, "result_age_ms": None, "error": None, "retries": 0, "preview_available": False, "preview_age_ms": None, "preview_frame_id": None}
            with camera.lock:
                now = time.monotonic()
                age = None if camera.last_frame_at is None else max(0, round((now - camera.last_frame_at) * 1000))
                preview_age = None if camera.preview_at is None else max(0, round((now - camera.preview_at) * 1000))
                return {"role": role, "enabled": not camera.stop.is_set(), "state": camera.state, "name": camera.request.name, "session_id": None if camera.stop.is_set() else camera.session.id,
                        "age_ms": age, "result_age_ms": None if camera.last_result_at is None else max(0, round((now - camera.last_result_at) * 1000)),
                        "error": camera.error, "retries": camera.retries, "preview_available": camera.jpeg is not None and preview_age is not None and preview_age <= self.PREVIEW_MAX_AGE * 1000,
                        "preview_age_ms": preview_age, "preview_frame_id": camera.preview_frame_id,
                        "options": camera.request.options.model_dump(),
                        "metrics": {"captured_frames": camera.sequence, "processed_frames": camera.processed_frames,
                                    "skipped_frames": camera.skipped_frames, "inference_ms": camera.last_inference_ms,
                                    "queue_ms": camera.last_queue_ms, "server_ms": camera.last_server_ms,
                                    "age_origin": "decoded frame receipt; camera/network delay is not measured"}}

    def preview(self, role):
        self._role(role)
        with self.lock:
            camera = self.cameras.get(role)
            if camera:
                with camera.lock:
                    if camera.preview_at is not None and time.monotonic() - camera.preview_at <= self.PREVIEW_MAX_AGE:
                        if (camera.preview_posture_token is not None
                                and (not self.sessions.detection_gate.posture_allowed(camera.preview_posture_token)
                                     or camera.preview_posture_expires_at is not None
                                     and time.monotonic() >= camera.preview_posture_expires_at)):
                            return camera.jpeg_without_posture
                        return camera.jpeg
        return None

    def _command(self, camera):
        width = camera.request.options.size
        height = (round(width * 9 / 16) // 2) * 2
        filters = f"fps={self.FRAME_RATE},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        return [ffmpeg_path(self.settings), "-hide_banner", "-loglevel", "error", "-nostdin", "-rtsp_transport", "tcp", "-timeout", "5000000",
                "-i", camera.request.url.get_secret_value(),
                "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", filters, "-threads", "2", "-c:v", "rawvideo", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"], width, height

    def _start_process(self, command):
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, **subprocess_flags())

    @staticmethod
    def _read_frame(stream, size):
        # A partial pipe read is normal. The inference thread's watchdog kills
        # a silent decoder, so this read cannot keep a camera alive indefinitely.
        data = bytearray()
        while len(data) < size:
            chunk = stream.read(size - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return data

    def _capture(self, camera):
        failures = 0
        while not camera.stop.is_set():
            process = None
            frames = 0
            try:
                with camera.lock:
                    camera.state = "connecting" if failures == 0 else "reconnecting"
                command, width, height = self._command(camera)
                process = self._start_process(command)
                with camera.lock:
                    if not camera.stop.is_set():
                        camera.process = process
                        camera.process_started = camera.process_last_frame = time.monotonic()
                        camera.process_has_frame = False
                if camera.stop.is_set():
                    return
                while not camera.stop.is_set():
                    admission = self.sessions.detection_gate.capture(camera.role)
                    data = self._read_frame(process.stdout, width * height * 3)
                    if data is None:
                        break
                    received = self.bridge.receipt()
                    frame = np.frombuffer(data, np.uint8).reshape(height, width, 3)
                    with camera.lock:
                        if camera.stop.is_set():
                            break
                        camera.sequence += 1
                        # The only capture slot: never queue old frames behind inference.
                        # Windows' monotonic receipt clock can have coarser
                        # resolution than distinct decoded frames. Keep their
                        # observation times on the high-resolution counter.
                        camera.latest = (camera.sequence, received, frame, time.perf_counter() * 1000, admission)
                        camera.last_frame_at = camera.process_last_frame = received[0]
                        camera.process_has_frame = True
                        if camera.state in {"connecting", "reconnecting"}:
                            camera.state, camera.error = "streaming", None
                    frames += 1
                    camera.wake.set()
            except Exception:
                # Never forward FFmpeg, decoder, or URL exceptions to users/logs.
                pass
            finally:
                self._terminate(process)
                if process and process.stdout:
                    process.stdout.close()
                with camera.lock:
                    if camera.process is process:
                        camera.process = None
            if camera.stop.is_set():
                break
            failures = 1 if frames >= self.FRAME_RATE * 2 else failures + 1
            with camera.lock:
                camera.retries = failures
                camera.latest, camera.jpeg = None, None
                camera.jpeg_without_posture, camera.preview_posture_token = None, None
                camera.state = "error" if failures >= self.MAX_RETRIES else "reconnecting"
                camera.error = "Camera connection failed. Check the address, credentials and network."
            if failures >= self.MAX_RETRIES:
                break
            camera.stop.wait(min(8.0, .5 * 2 ** (failures - 1)))

    def _watchdog(self, camera):
        with camera.lock:
            process = camera.process
            timeout = self.READ_TIMEOUT if camera.process_has_frame else self.STARTUP_TIMEOUT
            stalled = process is not None and time.monotonic() - camera.process_last_frame > timeout
        if stalled:
            self._terminate(process)

    def _infer(self, camera):
        consumed = -1
        while not camera.stop.is_set():
            self._watchdog(camera)
            if camera.session.closed.is_set():
                with camera.lock:
                    camera.state, camera.error = "error", "Detection source was stopped. Reconnect the camera."
                camera.stop.set()
                self._terminate(camera.process)
                return
            # Keep a configured source alive during a temporary camera outage.
            with camera.session.lock:
                camera.session.touched = time.monotonic()
            if not self.sessions.detection_gate.enabled(camera.role):
                with camera.lock:
                    camera.state, camera.error = 'paused_journey', None
                    camera.jpeg, camera.preview_at, camera.preview_frame_id = None, None, None
                    camera.jpeg_without_posture, camera.preview_posture_token = None, None
                camera.stop.wait(.2)
                continue
            camera.wake.wait(.1)
            camera.wake.clear()
            with camera.lock:
                latest = camera.latest
            if latest is None or latest[0] == consumed or time.monotonic() - latest[1][0] > self.PREVIEW_MAX_AGE:
                continue
            frame_id, received, frame, captured_at, admission = latest
            with camera.lock:
                camera.skipped_frames += max(0, frame_id - max(consumed, 0) - 1)
            consumed = frame_id
            try:
                generation = self.sessions.detection_gate.check(admission['generation'], camera.role)
                if not admission['enabled']:
                    continue
                camera.session.accept(frame_id)
                work = self.dispatcher.submit("session:" + camera.session.id,
                    lambda backend, canceled, queued, frame=frame, frame_id=frame_id, captured_at=captured_at,
                           generation=generation, posture_token=admission['posture_token']: self.sessions.infer(
                        camera.session, frame, frame_id, captured_at, None, backend, canceled, queued,
                        generation, posture_token))
            except BusyError:
                with camera.lock:
                    if camera.process:
                        camera.state, camera.error = "waiting_model", "Waiting for detection capacity."
                camera.stop.wait(.15)
                continue
            except SupersededError:
                continue
            deadline = time.monotonic() + self.settings.inference_timeout
            timed_out = False
            while not camera.stop.is_set():
                try:
                    result = work.future.result(timeout=.1)
                    if timed_out or work.canceled.is_set():
                        break
                    camera.session.check(frame_id)
                    result["server_ms"] = round((time.monotonic() - received[0]) * 1000, 1)
                    superseded = False
                    with self.lock:
                        if self.cameras.get(camera.role) is camera and not camera.stop.is_set():
                            published = self.sessions.finalize(camera.session, result, work.canceled,
                                                              received, self.dispatcher.status().get("backend"))
                            # Finalize can discard posture across a door transition.
                            # Build the matching preview from that accepted result.
                            preview = frame.copy() if frame.shape[1] <= 640 else cv2.resize(frame, (640, round(frame.shape[0] * 640 / frame.shape[1])))
                            draw_detections(preview, result["detections"])
                            encoded, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
                            without_posture = jpeg.tobytes() if encoded else None
                            preview_token = None
                            posture_expires_at = None
                            summary = result.get('seating_summary') or {}
                            if posture_boxes(summary):
                                ttl = 4000 if summary.get('engine') == 'LocateAnything' else 1000
                                posture_expires_at = time.monotonic() + max(0, ttl - summary.get('age_ms', 0)) / 1000
                                draw_posture(preview, summary)
                                encoded, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
                                preview_token = admission['posture_token']
                            with camera.lock:
                                if published:
                                    camera.processed_frames += 1
                                    camera.last_inference_ms = result.get('inference_ms')
                                    camera.last_queue_ms = result.get('queue_ms')
                                    camera.last_server_ms = result.get('server_ms')
                                    camera.last_result_at = time.monotonic()
                                    camera.jpeg = jpeg.tobytes() if encoded else None
                                    camera.jpeg_without_posture = without_posture
                                    camera.preview_posture_token = preview_token
                                    camera.preview_posture_expires_at = posture_expires_at
                                    camera.preview_at = received[0]
                                    camera.preview_frame_id = frame_id
                                    if camera.process:
                                        camera.state, camera.error = "detecting", None
                                else:
                                    camera.state = "superseded"
                                    camera.error = "Another detection source uses this role. Reconnect this camera to use it again."
                                    camera.stop.set()
                                    camera.latest, camera.jpeg = None, None
                                    camera.jpeg_without_posture, camera.preview_posture_token = None, None
                                    camera.preview_at, camera.preview_frame_id = None, None
                                    superseded = True
                    if superseded:
                        # Do not take the role lock from this worker: configure
                        # may be waiting for it to exit while holding that lock.
                        # Deleting only this session preserves the replacing source.
                        self._stop(camera)
                        return
                    break
                except FutureTimeout:
                    self._watchdog(camera)
                    if not timed_out and time.monotonic() > deadline:
                        work.canceled.set()
                        self.dispatcher.cancel("session:" + camera.session.id)
                        timed_out = True
                        with camera.lock:
                            camera.state, camera.error = "waiting_model", "Detection timed out. Waiting for the model to recover."
                    # Do not submit another request until this one has finished.
                except SupersededError:
                    break
                except Exception:
                    with camera.lock:
                        if camera.process:
                            camera.state, camera.error = "waiting_model", "Detection unavailable. Check model status."
                    camera.stop.wait(.2)
                    break
            if camera.stop.is_set():
                work.canceled.set()
                self.dispatcher.cancel("session:" + camera.session.id)
