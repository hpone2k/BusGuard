# Local API reference

This documents the routes implemented by the current BusGuard application. The authoritative API is the detection service, normally `http://<HOST_IP>:4479`. The other websites proxy specific allowlisted routes to that service; they do not create independent controllers.

The generated FastAPI schema is available at `/openapi.json` on the detection service. Interactive `/docs` and `/redoc` pages are disabled to avoid CDN dependencies. Some bodies, especially voice and camera requests, are validated manually and are described below. See [current workflow](SYSTEM_WORKFLOW.md) for controller semantics and [setup](SETUP_AND_OPERATIONS.md) for starting the listeners.

## Conventions and access

Requests with JSON bodies use `Content-Type: application/json`. Image/video uploads use raw binary bodies, not multipart forms. State is returned as JSON unless a route explicitly returns an image, audio, source video or export. JSON examples contain placeholders; generate new private tokens locally rather than reusing example text.

| Access level | Mechanism |
| --- | --- |
| Public local-network state | Read-only redacted bus/assistance state, configuration and permitted passenger operations. No operator pairing required. |
| Operator | Loopback clients are local operators. A remote browser pairs once and sends the `bustech_operator` cookie. |
| Passenger request owner | A random `request_token` in the creation body, then `X-Request-Token` on private request reads/completion/cancellation. |
| ESP32 reader | `Authorization: Bearer <READER_TOKEN>` plus its assigned `reader_id`. Grants only reader state and tap submission. |
| Voice session owner | Opaque local `session_id`, client identity and client address; the standard OpenAI API key remains on the server. |

Non-GET writes reject an `Origin` whose host/port differs from the request's `Host`. Use each website's same-origin proxy rather than enabling broad cross-origin access. Trusted hosts are established at server startup; restart after a LAN address change. The HTTP listeners are for a trusted LAN, not public internet hosting.

Public bus state excludes card UIDs, reader tokens, passenger ownership tokens and private request handles. It includes aggregate observations and anonymized request summaries. Operator-only camera metadata never returns the credential-bearing RTSP URL. The public state is still visible to other devices on the demo network.

### Endpoint availability by website

| Port | API exposure |
| --- | --- |
| 4479 | All routes below, subject to their access checks. |
| 4480 | `GET /api/site`, `GET /api/bus/state`, voice configuration and approved announcement audio. No operator controls or camera frames. |
| 4481 / optional 4482 | Assistance config/state, passenger request lifecycle, voice config/session/close/speech, phone setup information and public CA download. No operator controls or RFID management. |

All four share the detection service. If the proxy cannot reach it, it returns `503`; the viewer does not substitute an old snapshot as fresh state.

## Discovery, pairing and model status

| Method | Path | Access | Result |
| --- | --- | --- | --- |
| GET | `/api/site` | Public | Configured controller `api_port`. |
| GET | `/api/access` | Public | `operator`, `local`, `lan_enabled`, available LAN links and the pairing code only for a local loopback client in LAN mode. |
| POST | `/api/access/pair` | Public, rate-limited | Validates the six-digit code and sets an HttpOnly, SameSite=Strict operator cookie. |
| GET | `/api/status` | Public | Model load state/error, backend capabilities, configured backend, version and media limits. |
| GET | `/api/bus/state` | Public | Vision bridge snapshot plus the authoritative `assistance` snapshot. |
| GET | `/api/bus/sources` | Operator | Registered source list. |

Pairing body:

```json
{"code": "123456"}
```

Use the actual code shown at `http://localhost:4479/` on the host. Successful pairing returns `{"operator": true}`. Sessions expire after 12 hours or a server restart. Pairing is limited to five attempts per client address per minute. Do not log pairing cookies or copy the operator cookie into the ESP32.

## Detection sessions and options

### Shared options object

```json
{
  "prompt": "person, wheelchair, stroller",
  "mode": "objects",
  "size": 640,
  "confidence": 0.35,
  "class_confidences": {"person": 0.4, "wheelchair": 0.3},
  "stabilization": "balanced",
  "camera_motion": false,
  "posture_enabled": true,
  "standing_confidence": 0.1
}
```

| Field | Values / meaning |
| --- | --- |
| `prompt` | Object mode: comma-separated categories, maximum 32. Phrase mode: phrases separated by newline or semicolon, maximum 8, each at most 160 characters. Total text limit is 2,048 characters. |
| `mode` | `objects` or `phrase`; the selected backend must support the requested mode. |
| `size` | `384`, `512`, `640` or `960`. |
| `confidence` | Default confidence threshold, 0.05–0.95. |
| `class_confidences` | Optional per-category/phrase overrides, 0.05–0.95. Keys are normalized case-insensitively and must match configured targets. |
| `stabilization` | `balanced`, `responsive` or `off`. |
| `camera_motion` | Enables camera-motion-aware tracking behavior. |
| `posture_enabled` | Legacy field name retained for the active standing check. On an inside live person source, current standing inference uses YOLOE; it does not mean MediaPipe is active. |
| `standing_confidence` | Separate standing-prompt threshold, 0.05–0.95; default 0.10. |

Unknown option fields are rejected. Detailed phrase categories can overlap and must not be summed as a unique passenger census. For cabin counting use the literal object category `person`.

### Browser camera / image / streamed video session

| Method | Path | Access | Result |
| --- | --- | --- | --- |
| POST | `/api/sessions` | Operator | `201`: creates a session with fixed options, kind and role. |
| POST | `/api/sessions/{id}/frames?frame_id=0&captured_at=0` | Operator | Runs the newest frame and returns detections/timing/count and standing information. |
| DELETE | `/api/sessions/{id}` | Operator | Cancels/removes the session. |

Create example:

```json
{
  "kind": "live",
  "source_role": "inside",
  "source_name": "Cabin camera",
  "options": {
    "prompt": "person",
    "mode": "objects",
    "size": 640,
    "confidence": 0.35,
    "posture_enabled": true,
    "standing_confidence": 0.1
  }
}
```

`kind` is `image`, `live` or `video`; `source_role` is `outside`, `inside` or `unassigned`. Names are at most 80 plain-text characters. Success returns `id`, role, name and kind. Submit a JPEG/other supported image directly as the frame body. `frame_id` must increase within the session; `captured_at` is a nonnegative finite capture timestamp in **milliseconds**, increasing with actual captures.

Send one frame at a time per source, then take the newest available frame. Do not replay old frames to advance a standing check. A superseded result returns `409`; disabled inference in the current journey/door phase returns `423` with code `journey_detection_paused`. Client code should respect that phase instead of repeatedly flooding the queue.

Detection rows contain `label`, normalized `bbox: [x1,y1,x2,y2]`, `score`, optional `track_id`, and tracking/prediction metadata. Response fields include dimensions, capture/frame identity, `detection_engine`, `detections`, `count_summary`, `seating_summary`, `inference_ms`, `tracking_ms`, `queue_ms` and `server_ms`. `seating_summary` retains its historical field name even for standing-only inference. Treat validity, freshness and engine fields as part of the result; an absent box is not sufficient evidence of a completed standing pass. The controller's readiness state is the authoritative departure decision.

### Server-managed RTSP cameras

`{role}` must be `inside` or `outside`.

| Method | Path | Access | Result |
| --- | --- | --- | --- |
| GET | `/api/bus/cameras` | Operator | Both camera statuses, source IDs, options, freshness and timing metrics. |
| GET | `/api/bus/cameras/{role}` | Operator | One role's status. |
| PUT | `/api/bus/cameras/{role}` | Operator | `202`: starts/replaces the role's server-managed stream. |
| DELETE | `/api/bus/cameras/{role}` | Operator | Disconnects the role. |
| GET | `/api/bus/cameras/{role}/preview.jpg` | Operator | Fresh JPEG preview; `404` if unavailable. |

Configure body:

```json
{
  "url": "rtsp://CAMERA_USER:URL_ENCODED_PASSWORD@CAMERA_IP:554/STREAM_PATH",
  "name": "Inside CCTV",
  "options": {"prompt": "person", "camera_motion": false, "posture_enabled": true}
}
```

Replace every placeholder. `url` accepts RTSP/RTSPS only, with a valid host/port and no fragment, whitespace or control characters. Rejected camera settings deliberately do not echo the submitted URL. Credential-bearing URLs stay in memory; a restart needs reconfiguration.

Metrics distinguish captured/processed/skipped frames and queue/inference/server timings. Camera age begins at decoded-frame receipt on this computer; it **does not measure latency already introduced inside the camera or network**.

## Recorded video and jobs

| Method | Path | Access | Result |
| --- | --- | --- | --- |
| POST | `/api/videos` | Operator | Raw video upload; use `X-Filename: example.mp4`. Returns `201` and a video record. |
| GET | `/api/videos/{id}/source` | Operator | Original uploaded file. |
| DELETE | `/api/videos/{id}` | Operator | Deletes the uploaded video when permitted by job state. |
| POST | `/api/jobs` | Operator | Starts processing; returns `202`. |
| GET | `/api/jobs/{id}` | Operator | Job status/progress/metadata. |
| POST | `/api/jobs/{id}/cancel` | Operator | Requests cancellation. |
| GET | `/api/jobs/{id}/timeline` | Operator | Detection timeline as newline-delimited JSON (`application/x-ndjson`). |
| POST | `/api/jobs/{id}/export?height=720` | Operator | Starts annotated MP4 export; accepted height 480–1080. |
| GET | `/api/jobs/{id}/export` | Operator | Exported MP4, or `404` until ready. |
| DELETE | `/api/jobs/{id}` | Operator | Deletes a job when permitted. |
| GET | `/api/library` | Operator | Saved videos and jobs, newest first. |

Job body:

```json
{"video_id": "<32_HEX_CHARACTER_VIDEO_ID>", "options": {"prompt": "person"}, "stride": 1}
```

`stride` accepts 1–30. Videos must have valid metadata and decodable frames. Default limits are 512 MiB upload, 15 minutes, up to 4K dimensions and 1–120 FPS. Extensions accepted by the upload handler are `.mp4`, `.mov`, `.mkv`, `.avi`, `.webm` and `.m4v`; codec support still depends on the decoder. Images are limited to 12 MiB / 24 million pixels. Stored media uses a 4 GiB configured budget and 24-hour retention; do not use the server as permanent video storage. Uploaded material is a test source, not live evidence for automatic departure.

## Passenger assistance

### Read configuration and state

| Method | Path | Access | Result |
| --- | --- | --- | --- |
| GET | `/api/assistance/config` | Public | Stops, assistance choices, capacities and prototype configuration. |
| GET | `/api/assistance/state` | Public | Redacted authoritative controller snapshot. |
| POST | `/api/assistance/requests` | Public, capability created by client | Creates or idempotently retries one passenger request. |
| GET | `/api/assistance/requests/{id}` | Owner token | Private request status/progress/countdown. |
| POST | `/api/assistance/requests/{id}/complete` | Owner token | Confirms boarding/alighting only when access is ready. |
| POST | `/api/assistance/requests/{id}/cancel` | Owner token | Cancels an active request. |

Stop identifiers are stable API values, not the display letters:

| Display | `stop_id` |
| --- | --- |
| Bus stop A | `campus` |
| Bus stop B | `interchange` |
| Bus stop C | `community` |
| Bus stop D | `stop_d` |
| Bus stop E | `stop_e` |

New boarding request:

```json
{
  "client_request_id": "<NEW_UNIQUE_CLIENT_ID>",
  "request_token": "<RANDOM_URL_SAFE_SECRET_24_TO_128_CHARACTERS>",
  "journey": "boarding",
  "stop_id": "campus",
  "location": {"mode": "at_stop", "stop_id": "campus"},
  "needs": ["ramp"]
}
```

The angle-bracket placeholders are explanatory and do not satisfy schema validation. Generate a plain client ID and a random URL-safe token; the supplied app uses cryptographic randomness. `client_request_id` allows letters, digits, `_`, `.`, `:`, `-` and is at most 128 characters. `request_token` allows letters, digits, `_`, `-`, with length 24–128.

Each **new** passenger request must contain exactly one choice: `ramp`, `extra_time`, `audio`, `visual` or `priority_seat`. The schema retains a list and allows older exact retries to remain compatible; sending several choices for a new request is rejected by the controller. `journey` is `boarding` or `alighting`. Boarding location must be `at_stop` at the bus's current stop; alighting uses `{"mode":"onboard"}` and still targets the current stop. `{"mode":"away"}` cannot request boarding. Demo location is self-selected, not GPS verification.

The bus must be stationary at the selected stop and, in the timed scenario, have doors/admission open. A ramp request accepted before cutoff gets its 20-second allowance; other options get 10 seconds. A last accepted allowance can finish beyond the 50-second admission cutoff. The controller rejects new requests afterward. See the current workflow for all extensions and holds.

Creation returns the private request including `id`, `status`, `message`, progress, `can_complete`, `can_cancel` and `assistance_timer`. Preserve the client ID, exact request choices and private token when retrying an uncertain creation. A retry does not reserve another seat or extend time again. Never put the private token in a URL.

For later operations send:

```text
X-Request-Token: <SAME_PRIVATE_REQUEST_TOKEN>
```

Unknown IDs and incorrect/missing ownership tokens intentionally return the same `404` unavailable message. Calling complete too early returns `409`; client confirmation is not a physical doorway-clearance or securement sensor. Request states include `queued`, `accepted`, `assisting`, `awaiting_completion`, `completed`, `cancelled` and `error`. The controller allows at most 100 active requests and retains up to 500 request records.

### Authoritative snapshot fields

| Field | Meaning |
| --- | --- |
| `simulation` | Explicitly true for this prototype. |
| `revision`, `server_time_ms` | Controller revision and server clock for reconciling client displays. |
| `vehicle` | Current simulated motion, stop, phase, doors, ramp, obstruction, emergency and revalidation state. |
| `route` | Current/next stop, schematic segment progress and arrival event identity; not GPS. |
| `scenario` | Timed stop phase, admission, seats, camera reconciliation and server-owned countdown data. |
| `readiness` | Departure permission, reasons and standing confirmation progress. Use this instead of inferring readiness from boxes. |
| `seating` | Latest accepted posture/standing summary, retaining the legacy property name. |
| `requests`, `active_count` | Redacted active summaries/count; ownership secrets and private IDs are absent. |
| `mobility_area` | Simulated review/confirmation state, explicitly not camera proof of physical securement. |
| `announcements` | Current/recent controller messages available for display and approved TTS. |

Public `/api/bus/state` wraps this under `assistance` alongside the vision sources. Passenger clients use `/api/assistance/state` directly. Polling/interpolating a countdown does not grant an extension or change controller authority.

## Operator controls

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| POST | `/api/assistance/scenario` | Operator | Timed scenario actions. |
| POST | `/api/assistance/control` | Operator | Manual/safety/revalidation controls. |
| POST | `/api/bus/rfid` | Operator | Legacy anonymous preference-event simulation; not the physical UID reader endpoint. |

Scenario actions: `start`, `stop`, `board`, `alight`, `rfid`, `load`, `disable`, `reset`. Optional fields depend on the action: `stop_id`, `seat_type` (`priority` or `standard`), `event_id`, `priority_occupied` (0–6), `standard_occupied` (0–20), `priority_regular` (0–6). Integer occupancy fields are strict integers. Examples are mutations to the simulation:

```json
{"action": "start"}
```

```json
{"action": "stop"}
```

```json
{"action": "load", "priority_occupied": 0, "standard_occupied": 3, "priority_regular": 0}
```

`stop` invokes the next route arrival; it is not an emergency brake on a real vehicle. Timed-scenario departure is automatic; manual departure cannot bypass the scenario gate.

Control actions: `arrive`, `depart`, `obstruction`, `emergency`, `reset`, `secure_wheelchair`, `confirm_sensor_requests`, `cabin_coverage`. Body fields are `action`, optional `stop_id` and optional boolean `value`. For example `{"action":"cabin_coverage","value":true}` is the operator's confirmation that the inside camera covers the whole cabin; it is not an automatic camera calibration result.

The legacy `/api/bus/rfid` body accepts `passenger_type: "senior" | "assistance"`, a unique `event_id` and optional `source_name`. Prefer the registered-card routes below for the implemented four-card / ESP32 demonstration.

## RFID administration and ESP32 ingress

### Operator management

| Method | Path | Access | Result |
| --- | --- | --- | --- |
| GET | `/api/rfid/cards` | Operator | Four registered card profiles, onboard states, readers and last scan. |
| PUT | `/api/rfid/cards/{card_id}` | Operator | Updates card `1`, `2`, `3` or `4`. |
| POST | `/api/rfid/readers` | Operator | Creates/reprovisions a reader; returns its token once. |
| DELETE | `/api/rfid/readers/{reader_id}` | Operator | Revokes reader access. |
| POST | `/api/rfid/test-tap` | Operator | Plays a simulated/keyboard UID tap through the same registry. |
| GET | `/api/rfid/firmware/{filename}` | Operator | Downloads only `BusGuardRFID.ino`, `config.example.h` or `README.md`. Never serves populated `config.h`. |

Card profile body:

```json
{"passenger_type": "senior", "custom_label": "", "priority": true, "extra_time": true}
```

`passenger_type` is `normal`, `senior`, `pregnant` or `custom`; the custom label is limited to 60 characters. Profiles are assigned deliberately, not inferred from camera appearance.

Provisioning body:

```json
{"reader_id": "busguard-esp32", "label": "Front entrance RC522"}
```

Success returns `reader_id` and `token`. Reprovisioning the same ID rotates the token; update/reflash the reader. Up to eight readers can be configured. Only a token hash is saved in the journal.

An operator test tap uses `{"uid":"<REGISTERED_UID>","event_id":"<NEW_EVENT_ID>","source":"simulation"}`; `source` can also be `keyboard`. It changes the actual demonstration ledger if accepted.

### Hardware requests

First fetch a fresh admission window:

```text
GET /api/rfid/reader-state?reader_id=busguard-esp32
Authorization: Bearer <READER_TOKEN>
```

The response includes `reader_id`, `server_time_ms`, `window_id`, `accepting` and `message`. Submit the observed UID using that freshly fetched window and time:

```text
POST /api/rfid/tap
Authorization: Bearer <READER_TOKEN>
Content-Type: application/json
```

```json
{
  "reader_id": "busguard-esp32",
  "uid": "<REGISTERED_HEX_UID>",
  "event_id": "<BOOT_ID_AND_SEQUENCE>",
  "window_id": "<FRESH_WINDOW_ID>",
  "observed_at_ms": 1790000000000
}
```

The example timestamp is illustrative; use the current server time from reader-state. `window_id` may be null if no window exists. UIDs are 4-, 7- or 10-byte hexadecimal strings, optionally colon/hyphen separated. `event_id` must be unique for each physical presentation; retries use the same ID and exact payload.

The server rejects stale events older than 5 seconds, materially future-dated events, a different admission window, unknown cards, debounced repeats, full-bus boarding and closed admission. A held card must be removed before another presentation; firmware also enforces its own re-arm interval. First accepted tap boards the card's passenger; the next accepted tap alights them. Request retries never toggle twice.

**HTTP 200 does not necessarily mean admission succeeded.** Inspect `accepted` (also exposed as `allowed`), `code`, `message`, `direction`, `card_name`, `passenger_type`, `onboard`, `count`, `seats` and `duplicate`. Rejected taps do not change occupancy. Bad reader credentials return `401`. See [firmware details](../firmware/BusGuardRFID/README.md) for wiring, delivery budgets and retry behavior.

## Voice and phone setup

| Method | Path | Access / effect |
| --- | --- | --- |
| GET | `/api/voice/config` | Public configuration; `enabled`, available/default voices, model, session-duration limit and setup message. Does not return the API key or make a paid call. |
| POST | `/api/voice/session` | Creates a WebRTC OpenAI call with the server key. Available on detection and passenger sites, not the 3D viewer proxy. Can incur API charges. |
| POST | `/api/voice/session/close` | Closes the calling client's opaque local session handle. |
| POST | `/api/voice/speech` | Cached/generating audio for approved text only. Can incur charges for an uncached clip. |
| GET | `/api/phone-setup` | Passenger-site-only public CA availability and SHA-256 fingerprint. |
| GET | `/setup/certificate.cer` | Passenger-site-only fixed public CA download. No private key is served. |

Session creation body:

```json
{"sdp": "<VALID_WEBRTC_AUDIO_OFFER>", "voice": "marin", "client_id": "<RANDOM_CLIENT_ID>"}
```

The app obtains microphone consent, creates a real WebRTC audio offer and submits it. The response is `sdp` (answer), opaque `session_id` and `max_session_seconds`. `client_id` is 8–96 URL-safe characters; SDP is validated as an audio offer and bounded to 32,000 characters. Supported voices are `marin` and `cedar`. Do not replace the offer with the illustrative placeholder.

Close body:

```json
{"session_id": "<SESSION_HANDLE>", "client_id": "<SAME_CLIENT_ID>"}
```

Speech body:

```json
{"text": "<CURRENT_APPROVED_ANNOUNCEMENT>", "voice": "marin"}
```

Only the approved catalog or current/recent controller messages can generate speech. Arbitrary text is rejected. A cached/ready response is `audio/mpeg`; a cache miss returns `202` with `preparing: true` and `Retry-After: 2` while preparing in the background. Respect retries; failed generation has a cooldown. Audio responses are marked `X-AI-Generated: true`.

Current limits include one active voice session per client address, three total active sessions, three creations per client/minute and 30 creations across the demo/hour. The maximum session timer is 3,300 seconds (55 minutes); the enabled foreground app may renew a session. These are local usage bounds, not an account billing cap. Announcement requests are limited separately: 80 per client/minute and 30 cloud-generation starts across the demo/minute, with bounded pending work. See [voice setup](VOICE_SETUP.md) for the wake phrase, turn-taking, tool permissions and device limits.

## Error handling and limits

Typical errors return `{"detail":"..."}`. Schema validation can return a structured `detail` list. Treat text as user-facing explanation, not a stable machine enum; use explicit result codes where supplied.

| Status | Typical meaning |
| --- | --- |
| 400 | Invalid payload/options/URL format or unsupported action values. |
| 401 | Missing/invalid dedicated RFID reader token. |
| 403 | Operator pairing required, mismatched code, cross-origin write or wrong voice-session owner. |
| 404 | Missing resource, unavailable preview/export, or unknown request/incorrect ownership capability. |
| 409 | Superseded frame, conflicting idempotent retry, closed request window or invalid current-state action. |
| 413 | Body exceeds media/route-specific limits. |
| 422 | Schema validation or sanitized invalid camera settings. |
| 423 | Detection paused by the current role/journey/door gate. |
| 429 | Queue, active-request, pairing or voice limits; observe `Retry-After` when present. |
| 500 | Unexpected inference failure. |
| 502 / 503 | Upstream voice problem, model loading, unavailable local service or journal failure. |
| 504 | Inference timeout. |

Default detection configuration allows 24 sessions with a five-minute idle lifetime, a dispatcher queue of 12, four simultaneous frame HTTP handlers and three video jobs. Inference timeout is 120 seconds. Clients should reduce outstanding work on `429` and send the latest frame only. Missing, expired or superseded observations must never be counted as successful zero-person/zero-standing inference.

The controller writes important state durably. A journal failure returns an error and holds simulated movement; a caller must not show an action as completed before the server acknowledges it. Current APIs are tailored to this prototype and are not a versioned public integration contract; update clients and server together.
