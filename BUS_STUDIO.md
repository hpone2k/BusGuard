# Version 4 interface update

This is a historical record of the earlier Bus Studio interface and its September 2026 checks. Controls, occupancy rules and departure behavior described below may differ from the current application. For the current system, use the [main README](README.md), [workflow](docs/SYSTEM_WORKFLOW.md) and [viewer guide](docs/VIEWER.md). Development commands below use a project-local environment; no sibling checkout is required.

The current viewer is **http://localhost:4480** (also available at the legacy `/bus` route). It now contains the detailed 3D model and a compact glass passenger panel. Camera setup, input switching, thresholds, preview feeds and decision diagnostics have moved to **http://localhost:4479**. Each of the two console inputs accepts CCTV, webcam, image or recorded video. `start.cmd` starts both sites with one GPU inference service.

The model keeps 26 passenger seats (6 priority and 20 regular), no driver seat, and two CCTV models with the exterior unit between both doors. Current onboard counts use the indoor person category only; object/accessibility counts may overlap. Missing input is shown as unavailable, and recorded or image evidence is labelled. The historical Studio interface instructions below describe the earlier dashboard; API contracts and recorded validation remain useful.

---

# BusTech — Autonomous Bus Studio

**Seating before departure:** the indoor pose check holds virtual motion after door closure until all accounted passengers have two seconds of fresh seated evidence. Standing triggers an announcement; missing or unclear passengers keep the bus stationary. Select **Scenario lab → Test seating interlock** to try the full animation. See [SEATING_INTERLOCK.md](SEATING_INTERLOCK.md) for setup, behavior, data and validation limits.

Open **[localhost:4479/bus](http://localhost:4479/bus)**. The existing object detection workspace remains at `/`; both pages link to each other. Use the existing `start.cmd` or `start.ps1` launcher. The 3D page works independently of the detection model's warm-up and needs a browser with WebGL 2 / hardware acceleration.

## The model

A custom, procedural 12 m single-deck autonomous bus, inspired by Singapore's green public-bus exterior. This is a hackathon concept, not a dimensional reproduction or an engineering-certified vehicle. [LTA's bus overview](https://www.lta.gov.sg/content/ltagov/en/who_we_are/our_work/public_transport_system/bus.html) is the visual/context reference.

- Exactly **6 priority seats** and **20 regular passenger seats**, with individual shells, upholstery, numbered backs, supports and grab handles. The priority seats are on the low floor, positioned clear of the front wheel arch. Rear seating is on a raised deck.
- No steering wheel, dashboard or driver's seat. The front passenger space includes an information display and RFID reader model.
- **One indoor CCTV camera and one outdoor CCTV camera.** The exterior camera sits midway between the front and centre doors and covers both boarding approaches. The interior camera is mounted on the forward ceiling, looking down the central aisle. A separate roof LiDAR pod represents autonomous sensing; it is not an additional CCTV camera. Virtual coverage is an illustration; verify lens choice, field of view and occlusion on the actual bus.
- Shaped green body panels, wheel-arch cutouts, window frames, tinted glazing, LED destination signs, headlamps, rear lights, detailed tyres/rims, handrails, hanging straps, roof HVAC and cabin lighting.
- Front and centre split sliding doors, an extending centre-door access ramp, and a marked wheelchair/pram area beside a kerb with tactile paving.

All vehicle/person/stop geometry and label textures are created by the source code. No external 3D model, remote font, CDN, paid asset or image-generation service is required at runtime.

## Interactions

- Drag to orbit; scroll/pinch to zoom. Select Exterior, Cabin, Seat plan or Coverage for eased camera transitions. Auto-rotate, reset, fullscreen and evening lighting are available.
- Cabin cutaway lifts and fades the outer shell to reveal the complete interior. Click a seat to inspect its ID/type; the seating summary buttons highlight each seating class.
- In **Live detection**, the CCTV panels show recent processed images from configured RTSP cameras. A missing or stale feed is shown as unavailable. The model's camera viewpoints and coverage can still be inspected in the 3D view.
- In **Scenario lab**, the two CCTV previews render the same 3D scene from its modelled camera positions. Preview renders keep the physical bus shell intact even when the main view is a cutaway; virtual feed rendering is disabled in live mode.
- Door/ramp switches are available only in **Scenario lab**. Opening the ramp first opens the doors; closing the doors first stows the ramp.
- In **Scenario lab**, run, pause, resume, reset or replay a boarding scenario: wheelchair user, parent with pram, walking aid, or senior assistance through RFID. Boarding time is adjustable from +8 to +24 seconds before starting. Scenario changes cancel the current sequence.
- In **Live detection**, assistance requests start a virtual response with a minimum 20-second hold and require manual acknowledgement. Elapsed time does not establish completed boarding or automatically clear the request. Bounding boxes do not establish a passenger's exact 3D position, so scripted passengers remain confined to Scenario lab.

## Live detection and RTSP cameras

Bus Studio now receives the detector's actual results through a local backend bridge. Each source is explicitly assigned **outside**, **inside**, or **unassigned**. Both CCTV sources use the existing detector scheduler and tracking configuration; they do not run a second copy of the model.

Each live camera also displays a **visible-people estimate** from a 1,000 ms time-weighted vote. A stable estimate requires 65% support; warming, mixed observations and stale data are shown explicitly. The current observed count remains available for comparison. Per-class and overall count summaries are exposed to consumers in `sources[role].count_summary`. Inside and outside counts remain separate. See [COUNT_VOTING.md](COUNT_VOTING.md) for the algorithm, API fields and timing limits.

For IP cameras, configure an RTSP or RTSPS address for each role in Bus Studio. The computer must be able to reach that camera's network. Configuration returns immediately while the decoder connects. Each camera retains only its latest frame, submits one inference request at a time, reconnects after temporary failure, and provides a recent annotated JPEG preview. Camera addresses and credentials stay in memory, are excluded from public status responses and are not saved to disk. Reconnect cameras after restarting the server.

RTSP decoding uses normal FFmpeg probing and buffering so startup can retain the first keyframe. A new decoder gets up to 20 seconds to produce its first frame; after that, an 8-second stall triggers reconnection. The latest-frame slot and one in-flight inference request still prevent an application frame backlog. Each reconnect receives its own startup allowance. Enter the URL with `rtsp://` or `rtsps://`, without a backslash; a query-only path such as `/?inst=1` is supported.

If VLC works but the project cannot connect, first verify that the camera's Ethernet link is up and its RTSP port is reachable from this computer. Launch the server with access to that network (for example, `start.cmd` from Windows); a process started in a network-restricted environment cannot reach the CCTV. The camera's credentials should be entered directly in the connection form.

The original detection workspace also has a **Bus Studio source** selector and an **Accessibility** prompt preset (`person, wheelchair, stroller, walker, crutch, cane`). Assign a browser camera to share live detections. Assigning a new source takes over that role; unassigned sources do not create bus assistance requests. Recorded-video jobs remain offline analysis. Individual images produce explicitly tagged image-test events, never live CCTV events.

Outside observations of a wheelchair, pram/stroller, or walking aid can produce an assistance request. The bridge requires two consecutive observed results for a live request, excludes predicted boxes, and suppresses repeat alerts from the same tracked object or continuous untracked presence. Inside detections supply cabin context. A generic `person` label does not determine assistance needs. The nonsemantic colored-region demo backend cannot produce vision assistance events.

Freshness is measured from **server receipt of the frame**, including inference delay. Live results expire after 2.5 seconds; image results expire after 10 seconds. Empty results clear current detections. Canceled frames, replaced sessions and disconnected sources cannot overwrite the current source. The event list is a bounded history of 30 requests, so an earlier entry is not evidence that a passenger is still present. No raw image data is stored in the bridge.

The page polls the bridge and also ages the last received state on its own clock. Stalled polling or a lost connection cannot leave old camera results displayed as fresh. Acknowledgement resolves the virtual assistance request; it is not an automated claim that the passenger has finished boarding.

When the page opens or the server restarts, it can recover a vision request only when a confirmed history entry matches a currently fresh, observed outside track and the same mobility-aid type. Recovered requests are labelled. Old RFID, image-test and demo events are never replayed as new live requests.

### Local API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/bus/state` | Current inside/outside sources, freshness, detections and the last 30 assistance events. |
| `GET /api/bus/sources` | Active session source names and assignments. |
| `GET /api/bus/cameras` | Both configured camera states and preview availability; no camera URLs. |
| `GET /api/bus/cameras/{role}` | One camera's connection state, where role is `outside` or `inside`. |
| `PUT /api/bus/cameras/{role}` | Configure `{url, name, options?}`; accepts RTSP/RTSPS and returns HTTP 202 while connecting. `options` uses the existing detection options, including `class_confidences`. |
| `DELETE /api/bus/cameras/{role}` | Stop decoding/inference and clear that camera source. |
| `GET /api/bus/cameras/{role}/preview.jpg` | Recent annotated JPEG, or HTTP 404 when unavailable/stale. Responses are not cached. |
| `POST /api/bus/rfid` | Submit `{passenger_type: "senior" \| "assistance", event_id, source_name?}` from an RFID integration. Repeated IDs return the original event rather than a duplicate. |

RFID event IDs must be 1–128 characters using letters, digits, `.`, `_`, `:`, or `-`. The in-memory duplicate window retains the most recent 1,024 unique RFID events and resets with the server. An RFID device adapter should generate a stable ID per tap and forward only the assistance category, not a raw card identifier. No RFID reader driver or card-account lookup is bundled.

Sources include `kind` (`live` or `image`), `session_id`, `source_name`, `frame_id`, `received_at_ms`, `age_ms`, `connected`, `status`, `is_demo`, inference timing, and normalized detections. Events have increasing numeric IDs, a timestamp, source/frame provenance, confidence where available, and `origin` (`vision` or `rfid`). The interface distinguishes live camera data, image tests, RFID input and manual demonstrations.

## Physical hardware boundary

The 3D bus, people, doors and ramp remain **virtual**. Live detection and RFID messages can drive its presentation; the manual demonstration controls provide a separate scripted walkthrough. The virtual CCTV views show the model from its camera positions, while configured RTSP previews show actual received images.

The system does not infer age or pregnancy from appearance. Senior assistance uses an explicit RFID category; other unobservable needs can be passenger-requested. The page sends no physical vehicle, door or ramp commands. Actual actuators require a separate controller with stop/clearance/door interlocks, operator or supervised authorization, emergency stop and fault handling. Physical cameras and RFID readers must be validated with the team's equipment before the competition demonstration.

## Development

```powershell
npm ci
npm run build:bus
npm test
.\.venv\Scripts\python.exe -m pytest -q
```

The prebuilt local `static/bus/bus.bundle.js` is included, so Node and npm are needed only when rebuilding the source. Three.js is pinned to 0.186.0 and esbuild to 0.28.2 in `package-lock.json`. The Three.js MIT license is included in `static/bus/LICENSE.three.txt`.

- `static/bus/src/model.js`: procedural geometry, material groups, seats, cameras, boarding assets.
- `static/bus/src/simulation.js`: seat inventory and deterministic boarding state. Ramp sequencing and boarding time use one elapsed-time source, with no independent action timers.
- `static/bus/src/app.js`: scene rendering, camera transitions, controls, previews, animation and UI state.
- `static/bus/src/operations.js` / `live-state.js`: live polling, RTSP configuration, per-type confidence, request provenance, ongoing-track recovery and acknowledgement.
- `static/bus/index.html` / `bus.css` / `operations.css`: responsive operations interface, accessible controls and interaction descriptions.
- `tests/bus.test.js`: inventory and aisle checks, low-floor priority placement, door/ramp timing across all four scenarios and boarding durations, and RFID/reset behavior.
- `vision/bus_bridge.py`: thread-safe, bounded source state, request confirmation, freshness and RFID deduplication.
- `vision/rtsp.py`: two camera capture/inference workers, latest-frame selection, reconnect handling and annotated previews.
- `tests/test_bus_bridge.py` / `tests/test_bus_camera_api.py`: source isolation, cancellation, provenance, freshness, RFID handling and camera API credential redaction.
- `tests/test_rtsp.py`: bounded capture, latest-frame inference, preview alignment, retries and source takeover cleanup.
- `tests/bus-model.test.js` / `tests/bus-live.test.js`: actual camera projection, glazing, live-state freshness, request recovery and acknowledgement.

Static parts are merged by material to reduce draw calls. Main rendering uses antialiasing, tone mapping, environment lighting and PCF shadows with a capped pixel ratio. In Scenario lab, the two lower-resolution virtual feeds refresh at 5 Hz only when near the viewport. Live mode skips those extra scene renders. Simulation time pauses in a hidden tab. Reduced-motion preference shortens camera/cutaway transitions; the demonstration remains user-initiated and can be paused. A graphics fallback explains how to recover if WebGL initialization or context availability fails.

## Initial studio validation — September 12, 2026

- The initial studio's inference and frontend suites passed, including its three inventory and scenario tests. Current integration totals are listed below.
- Production bundle builds successfully; the page and local assets load under the app's existing self-only Content Security Policy. The detector remains ready on CUDA.
- Browser checks verified the actual rendered inventory (26 / 6 priority / 20 standard / 2 cameras), cabin cutaway, camera navigation, manual ramp/door sequencing, and all four boarding demos through closed-door completion. Pause/resume and senior RFID assistance were exercised. Seated demos use aisle-side priority seats; wheelchairs and prams park within the front bay without a U-turn.
- The 390 px responsive layout has no horizontal overflow. Desktop/narrow desktop and mobile framing were inspected in the in-app browser. Rendering was observed around 60 FPS or higher on this computer during the checked views; this is an observation, not a cross-device benchmark.
- The graphics driver emitted a shader precision warning during environment setup. Rendering succeeded. No physical CCTV, RFID, pregnancy/age recognition or physical ramp validation is claimed.

## Detection integration validation — September 12, 2026

- **50 Python tests and 29 frontend tests passed**, covering the existing inference/video pipeline, actual session-result publication, freshness, cancellation, source takeover, assistance confirmation, credential redaction, camera teardown, model geometry, RFID deduplication and current-track request recovery. Two existing upstream Python deprecation warnings remain.
- The bundled FFmpeg 7.1 executable was checked for the RTSP TCP transport and microsecond socket-timeout options used by the camera decoder. Decoder tests use controlled frame pipes; no physical camera connection is claimed.
- A request through the running CUDA YOLOE-26m server detected four people and one bus in the bundled sample. The outside bridge contained exactly those returned detections with `kind: image` and `is_demo: false`. It generated no assistance event, and deleting the test session left no active source or camera connection. The verification record is `validation/bus-bridge-smoke.json`.
- The backend remains on the existing model and port 4479. The bridge publishes a stable per-process `instance_id` so clients can recognize restarts without comparing event IDs from separate processes.
- A browser integration check submitted a labelled RFID test event through the local endpoint. The live request showed the minimum 20-second hold, remained pending for acknowledgement, and reset after acknowledgement. Scenario lab remained a separate scripted mode. The server was restarted after verification to clear the test event.

## Physical CCTV validation — September 14, 2026

- A physical RTSP camera connected successfully as Outside CCTV. The dashboard displayed its processed preview and updating detections, with no camera error or reconnect attempts during the check. Inside CCTV remains unconfigured.
- A direct decoder comparison produced the first frame in approximately 8.8 seconds with the previous aggressive low-latency flags, exceeding the former 8-second startup watchdog. Normal FFmpeg buffering and probing produced the first frame in approximately 1.1 seconds on the same stream. These are observations from this camera, not general performance guarantees.
- Normal buffering and probing are now used, with a 20-second first-frame allowance on each connection and an 8-second stall timeout after streaming begins. The latest-frame inference queue remains bounded.
- All 37 focused RTSP, camera API, count integration and seating tests passed, including startup timing, reconnect reset and credential-redaction regression coverage. Camera credentials and captured media are excluded from this record.
