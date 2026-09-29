import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .backends import create_backend
from .attributes import validate_yoloe_options
from .bus_bridge import VisionBusBridge
from .config import ROOT, Settings
from .jobs import VideoJobs
from .media import decode_image, probe_video
from .rtsp import CameraRequest, RTSPManager
from .scheduler import BusyError, Dispatcher, SupersededError
from .schema import JobRequest, RFIDRequest, SessionRequest
from .sessions import Sessions, image_digest
from .assistance import AssistanceController, AssistanceError
from .detection_gate import DetectionGate, DetectionPausedError
from .assistance_api import assistance_router
from .rfid_api import rfid_router
from .network import OperatorAccess, public_api, trusted_hosts
from .voice import VoiceService, voice_router

log = logging.getLogger(__name__)


def create_app(settings=None, backend_factory=None, *, lan=False, api_port=4479, viewer_port=4480, passenger_port=4481):
    settings = settings or Settings()
    dispatcher = Dispatcher(backend_factory or (lambda: create_backend(settings)), settings.queue_size)
    bus_bridge = VisionBusBridge()
    assistance = AssistanceController(path=settings.data_dir / 'assistance.json')
    voice = VoiceService(settings.data_dir, lambda: assistance.snapshot().get('announcements', []))
    detection_gate = DetectionGate(assistance.detection_policy)
    sessions = Sessions(settings, dispatcher, bus_bridge, detection_gate)
    cameras = RTSPManager(settings, sessions, dispatcher, bus_bridge)
    jobs = VideoJobs(settings, dispatcher, detection_gate)
    frames = asyncio.Semaphore(4)
    access = OperatorAccess(lan, api_port, viewer_port, passenger_port)

    async def controller_loop():
        while True:
            try:
                state = await asyncio.to_thread(assistance.tick, bus_bridge.snapshot())
                voice.observe(state.get('announcements', []))
            except Exception:
                log.exception('Assistance controller tick failed; motion held')
                assistance.fail_hold()
            await asyncio.sleep(.2)

    async def maintenance():
        while True:
            await asyncio.sleep(60)
            try:
                sessions.cleanup()
                await asyncio.to_thread(jobs.cleanup)
            except Exception:
                log.exception("Retention cleanup failed")

    @asynccontextmanager
    async def lifespan(app):
        assistance.tick(bus_bridge.snapshot())
        dispatcher.start()
        await asyncio.to_thread(jobs.cleanup)
        task = asyncio.create_task(maintenance())
        controller_task = asyncio.create_task(controller_loop())
        yield
        task.cancel()
        controller_task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await controller_task
        except asyncio.CancelledError:
            pass
        await asyncio.to_thread(cameras.close)
        await asyncio.to_thread(jobs.close)
        await asyncio.to_thread(dispatcher.close)
        await asyncio.to_thread(voice.close)

    # The schema stays available locally; CDN-based documentation is incompatible
    # with this application's offline content security policy.
    app = FastAPI(title="BusTech Detection Console", version="5.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.dispatcher, app.state.sessions, app.state.jobs = dispatcher, sessions, jobs
    app.state.bus_bridge = bus_bridge
    app.state.cameras = cameras
    app.state.assistance, app.state.access = assistance, access
    app.state.voice = voice
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts(lan))
    app.include_router(assistance_router(assistance, bus_bridge, access.require))
    app.include_router(rfid_router(assistance, bus_bridge, access.require))
    app.include_router(voice_router(voice))

    @app.middleware("http")
    async def local_only(request, call_next):
        reader_route = ((request.method == 'GET' and request.url.path == '/api/rfid/reader-state')
                        or (request.method == 'POST' and request.url.path == '/api/rfid/tap'))
        if request.url.path.startswith('/api/') and not public_api(request.url.path) and not reader_route and not access.authorized(request):
            return JSONResponse({'detail': 'Pair this operator device using the code on the host computer.'}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
            limit = settings.upload_bytes if request.url.path == "/api/videos" else settings.image_bytes
            try:
                if int(request.headers.get("content-length", "0")) > limit:
                    return JSONResponse({"detail": "Request exceeds the upload limit."}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid content length."}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/") and not request.url.path.endswith(("/source", "/export", "/timeline")):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.exception_handler(BusyError)
    async def busy(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=429, headers={"Retry-After": "1"})

    @app.exception_handler(SupersededError)
    async def superseded(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(DetectionPausedError)
    async def paused_detection(_, exc):
        return JSONResponse({'detail': str(exc), 'code': 'journey_detection_paused'}, status_code=423,
                            headers={'Retry-After': '1'})

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse({"detail": str(exc.args[0])}, status_code=404)

    def ready(options=None):
        status = dispatcher.status()
        if status["state"] != "ready":
            raise HTTPException(503, status["error"] or "Model is still loading.")
        if options:
            options.categories()
            if options.mode == "phrase" and not status["backend"]["phrases"]:
                raise ValueError("The active detector accepts object names. Select object-list mode.")
            if status['backend'].get('phrase_scope') == 'person-clothing-color':
                validate_yoloe_options(options)
            validator = getattr(dispatcher.backend, 'validate_options', None)
            if validator:
                # Text-only input checks happen before replacing any live input.
                # GPU inference remains confined to the dispatcher worker.
                validator(options)

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get('/passenger')
    @app.get('/passenger/')
    async def passenger():
        return FileResponse(ROOT / 'static' / 'passenger' / 'index.html')

    @app.get('/api/access')
    async def access_status(request: Request):
        return access.status(request)

    @app.get('/api/site')
    async def site():
        return {'api_port': api_port}

    @app.post('/api/access/pair')
    async def pair(request: Request):
        try:
            payload = await request.json()
            code = payload.get('code', '') if isinstance(payload, dict) else ''
        except ValueError:
            raise HTTPException(400, 'Enter the six-digit pairing code.') from None
        token = access.pair(request, code)
        response = JSONResponse({'operator': True})
        response.set_cookie('bustech_operator', token, max_age=43200, httponly=True, samesite='strict')
        return response

    @app.get("/bus")
    @app.get("/bus/")
    async def bus_studio():
        return FileResponse(ROOT / "static" / "bus" / "index.html")

    @app.get("/api/status")
    async def status():
        return {**dispatcher.status(), "version": "5.0.0", "configured_backend": settings.backend,
                "limits": {"image_bytes": settings.image_bytes, "video_bytes": settings.upload_bytes,
                           "video_minutes": settings.max_duration / 60, "retention_hours": settings.retention_hours}}

    @app.post("/api/sessions", status_code=201)
    async def create_session(body: SessionRequest):
        ready(body.options)
        session = sessions.create(body.options, body.kind, body.source_role, body.source_name)
        return {"id": session.id, "source_role": session.source_role,
                "source_name": session.source_name, "kind": session.kind}

    @app.get('/api/bus/state')
    async def bus_state():
        return {**bus_bridge.snapshot(), 'assistance': assistance.snapshot()}

    @app.get('/api/bus/sources')
    async def bus_sources():
        return bus_bridge.source_list()

    @app.post('/api/bus/rfid')
    async def bus_rfid(body: RFIDRequest):
        if assistance.snapshot().get('scenario', {}).get('enabled'):
            try:
                return assistance.scenario_rfid(body.event_id, body.passenger_type)
            except AssistanceError as exc:
                raise HTTPException(exc.status, str(exc)) from None
            except OSError:
                assistance.fail_hold()
                raise HTTPException(503, 'The request journal is unavailable. The bus remains held.') from None
        return bus_bridge.rfid(body)

    def camera_role(role):
        if role not in {'outside', 'inside'}:
            raise HTTPException(404, 'Camera role must be outside or inside.')
        return role

    @app.get('/api/bus/cameras')
    async def bus_cameras():
        return cameras.snapshot()

    @app.get('/api/bus/cameras/{role}')
    async def bus_camera(role: str):
        camera_role(role)
        return next(camera for camera in cameras.snapshot()['cameras'] if camera['role'] == role)

    @app.put('/api/bus/cameras/{role}', status_code=202)
    async def configure_bus_camera(role: str, request: Request):
        camera_role(role)
        # FastAPI's normal request-validation response includes the rejected
        # input. Camera URLs can contain passwords, so never return that input.
        try:
            body = CameraRequest.model_validate(await request.json())
        except (ValueError, UnicodeError):
            raise HTTPException(422, 'Invalid camera settings. Use an RTSP or RTSPS address, a short name and valid detection options.') from None
        ready(body.options)
        return await asyncio.to_thread(cameras.configure, role, body)

    @app.delete('/api/bus/cameras/{role}')
    async def disconnect_bus_camera(role: str):
        return await asyncio.to_thread(cameras.disconnect, camera_role(role))

    @app.get('/api/bus/cameras/{role}/preview.jpg')
    async def bus_camera_preview(role: str):
        image = cameras.preview(camera_role(role))
        if not image:
            raise HTTPException(404, 'No fresh camera preview is available.')
        return Response(image, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    @app.delete("/api/sessions/{id}")
    async def delete_session(id: str):
        sessions.delete(id)
        return {"canceled": True}

    @app.post("/api/sessions/{id}/frames")
    async def detect_frame(id: str, request: Request, frame_id: int = Query(ge=0),
                           captured_at: float = Query(ge=0, allow_inf_nan=False)):
        session = sessions.get(id)
        admission = detection_gate.capture(session.source_role)
        generation = detection_gate.check(admission['generation'], session.source_role)
        ready()
        if frames.locked():
            raise BusyError("Too many frames in flight. Send the newest frame after this request finishes.")
        async with frames:
            session.accept(frame_id)
            received = bus_bridge.receipt()
            start = time.perf_counter()
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > settings.image_bytes:
                    raise HTTPException(413, "Image exceeds the upload limit.")
                body.extend(chunk)
            session.check(frame_id)
            frame = await asyncio.to_thread(decode_image, body, settings.image_pixels)
            digest = image_digest(body) if session.kind == "image" else None
            work = dispatcher.submit("session:" + id,
                                     lambda backend, canceled, queued: sessions.infer(
                                         session, frame, frame_id, captured_at, digest, backend, canceled, queued,
                                         generation, admission['posture_token']))
            try:
                result = await asyncio.wait_for(asyncio.wrap_future(work.future), settings.inference_timeout)
            except asyncio.TimeoutError:
                work.canceled.set()
                raise HTTPException(504, "Detection timed out. Try a smaller image or a faster backend.")
            except asyncio.CancelledError:
                work.canceled.set()
                raise
            except (SupersededError, ValueError):
                raise
            except Exception as exc:
                log.exception("Detection failed")
                raise HTTPException(500, f"Detection failed: {exc}") from exc
            session.check(frame_id)
            result["server_ms"] = round((time.perf_counter() - start) * 1000, 1)
            sessions.finalize(session, result, work.canceled, received, dispatcher.status().get('backend'))
            return result

    @app.post("/api/videos", status_code=201)
    async def upload_video(request: Request):
        # Binary streaming avoids buffering an entire multipart upload before validation.
        if not jobs.upload_lock.acquire(blocking=False):
            raise BusyError("Another upload is in progress.")
        id = uuid.uuid4().hex
        partial = jobs.media_dir / f"{id}.partial"
        final = None
        try:
            name = Path(unquote(request.headers.get("x-filename", "video.mp4"))).name
            suffix = Path(name).suffix.lower()
            if suffix not in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}:
                raise ValueError("Upload MP4, MOV, MKV, AVI or WebM video.")
            await asyncio.to_thread(jobs.require_space)
            size, checkpoint = 0, 0
            with partial.open("wb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.upload_bytes:
                        raise HTTPException(413, "Video exceeds the upload limit.")
                    await asyncio.to_thread(stream.write, chunk)
                    if size - checkpoint > 4 * 1024**2:
                        await asyncio.to_thread(jobs.require_space)
                        checkpoint = size
            if not size:
                raise ValueError("The uploaded video is empty.")
            final = jobs.media_dir / f"{id}{suffix}"
            os.replace(partial, final)
            info = await asyncio.to_thread(probe_video, final, settings.max_duration)
            record = jobs.register_upload(id, suffix, name, info)
            final = None
            return record
        finally:
            partial.unlink(missing_ok=True)
            if final is not None:
                final.unlink(missing_ok=True)
            jobs.upload_lock.release()

    @app.get("/api/videos/{id}/source")
    async def video_source(id: str):
        record = jobs.video(id)
        return FileResponse(jobs.media_path(record))

    @app.delete("/api/videos/{id}")
    async def delete_video(id: str):
        await asyncio.to_thread(jobs.delete_video, id)
        return {"deleted": True}

    @app.post("/api/jobs", status_code=202)
    async def start_job(body: JobRequest):
        ready(body.options)
        return await asyncio.to_thread(jobs.start, body.video_id, body.options, body.stride)

    @app.get("/api/jobs/{id}")
    async def job_status(id: str):
        return jobs.snapshot(id)

    @app.post("/api/jobs/{id}/cancel")
    async def cancel_job(id: str):
        return await asyncio.to_thread(jobs.cancel, id)

    @app.get("/api/jobs/{id}/timeline")
    async def timeline(id: str):
        return FileResponse(jobs.timeline(id), media_type="application/x-ndjson", filename="detections.jsonl")

    @app.post("/api/jobs/{id}/export", status_code=202)
    async def start_export(id: str, height: int = Query(default=720, ge=480, le=1080)):
        return await asyncio.to_thread(jobs.export, id, height)

    @app.get("/api/jobs/{id}/export")
    async def download_export(id: str):
        record = jobs.snapshot(id)
        path = jobs.directory(id) / "export.mp4"
        if not record["export_ready"] or not path.is_file():
            raise HTTPException(404, "Export is not ready.")
        return FileResponse(path, media_type="video/mp4", filename="4477-annotated.mp4")

    @app.delete("/api/jobs/{id}")
    async def delete_job(id: str):
        await asyncio.to_thread(jobs.delete_job, id)
        return {"deleted": True}

    @app.get("/api/library")
    async def library():
        with jobs.lock:
            return {"jobs": sorted([dict(j.data) for j in jobs.jobs.values()], key=lambda x: x["created"], reverse=True),
                    "videos": sorted([dict(v) for v in jobs.videos.values()], key=lambda x: x["created"], reverse=True)}

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app
