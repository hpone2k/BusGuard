# Project file map

BusGuard runs one shared controller with three web interfaces. The detection console owns camera connections and operator controls; the bus viewer and passenger app display the same controller state through their own server routes.

## Start and configure the project

| File | Purpose |
| --- | --- |
| [`run.py`](../run.py) | Starts the detection/controller server on port 4479, the bus viewer on 4480, and the passenger app on 4481. Optional certificate arguments add the HTTPS passenger site on 4482. |
| [`start.ps1`](../start.ps1) | Windows launcher. Selects the Python environment and supports LAN and passenger HTTPS switches. |
| [`start-lan.cmd`](../start-lan.cmd) / [`start-lan-voice.cmd`](../start-lan-voice.cmd) | Shortcuts for sharing the HTTP sites or the sites plus passenger HTTPS. |
| [`vision/config.py`](../vision/config.py) | Model selection, resource limits and local data/model paths. |
| [`requirements.txt`](../requirements.txt) | Active Python dependency groups. Install a suitable PyTorch build separately as described in the main README. |
| [`package.json`](../package.json) / [`package-lock.json`](../package-lock.json) | JavaScript tests and reproducible dependencies for rebuilding the 3D viewer. |
| [`scripts/setup_grounding.py`](../scripts/setup_grounding.py) | Downloads the optional detailed-phrase model separately from the source. |

## Detection and camera pipeline

| File | Responsibility |
| --- | --- |
| [`vision/api.py`](../vision/api.py) | Main FastAPI app, detection endpoints, camera sessions and integration with the shared controller. |
| [`vision/schema.py`](../vision/schema.py) | Validated detection request and option schemas. |
| [`vision/backends.py`](../vision/backends.py) | Detector adapters, including YOLOE and the hybrid object/phrase backend. |
| [`vision/grounding.py`](../vision/grounding.py) / [`vision/attributes.py`](../vision/attributes.py) | Detailed-phrase inference and attribute handling. |
| [`vision/rtsp.py`](../vision/rtsp.py) | RTSP camera capture, current frames and connection lifecycle. |
| [`vision/scheduler.py`](../vision/scheduler.py) / [`vision/jobs.py`](../vision/jobs.py) | Inference scheduling and uploaded-video jobs. |
| [`vision/sessions.py`](../vision/sessions.py) | Per-source tracking, result freshness, count summaries and standing-check integration. |
| [`vision/tracking.py`](../vision/tracking.py) / [`vision/counting.py`](../vision/counting.py) | Stable tracks and time-window count voting. |
| [`vision/detection_gate.py`](../vision/detection_gate.py) | Applies controller policy to outside detection and door-dependent standing checks. |
| [`vision/standing.py`](../vision/standing.py) | YOLOE standing-only inference and its valid/no-standing summary. |
| [`vision/bus_bridge.py`](../vision/bus_bridge.py) | Converts current source results into observations for the bus controller. |

## Shared controller, seats and RFID

| File | Responsibility |
| --- | --- |
| [`vision/assistance.py`](../vision/assistance.py) / [`vision/assistance_api.py`](../vision/assistance_api.py) | Shared assistance state, passenger requests, operator actions, persistence and API routes. |
| [`vision/scenario.py`](../vision/scenario.py) | Stop/boarding/departure sequence, activity allowances, admission deadlines, announcements and available seats. |
| [`vision/departure.py`](../vision/departure.py) | Departure readiness from fresh observations. The active standing-only path requires five continuous seconds without detected standing after doors close. |
| [`vision/cabin_audit.py`](../vision/cabin_audit.py) | Thirty-second cabin-count audit and reconciliation data. |
| [`vision/cabin_count.py`](../vision/cabin_count.py) | Supporting cabin count state and validation. |
| [`vision/route.py`](../vision/route.py) | Shared A–E demonstration route and stop names. |
| [`vision/rfid.py`](../vision/rfid.py) | Four-card registry, passenger profiles, reader credentials, duplicate-tap handling and occupancy updates. |
| [`vision/rfid_api.py`](../vision/rfid_api.py) | Operator card/reader endpoints, authenticated ESP32 tap ingress and firmware downloads. |
| [`firmware/BusGuardRFID/BusGuardRFID.ino`](../firmware/BusGuardRFID/BusGuardRFID.ino) | ESP32 + RC522 sketch. Sends UIDs to the server; it does not write cards or drive a ramp, doors or an LED. |
| [`firmware/BusGuardRFID/config.example.h`](../firmware/BusGuardRFID/config.example.h) | Template for a private `config.h` containing Wi-Fi and reader connection settings. |

Card profile editing changes the passenger type and assistance preferences, not the UID. On a fresh clone, configure the four `CARD_UIDS` in `vision/rfid.py` before the first controller run when using physical cards. The controller journal validates those identities on later starts; do not replace them underneath an existing journal. Use the firmware README for wiring and reader setup. Keep your real card identifiers and populated configuration out of public commits.

## Web interfaces

| Area | Main files |
| --- | --- |
| Detection console | [`static/index.html`](../static/index.html), [`static/app.js`](../static/app.js), [`static/style.css`](../static/style.css). Camera input panels, detection options and operator controls. |
| Console state and labels | [`static/console-state.js`](../static/console-state.js), [`static/scenario-state.js`](../static/scenario-state.js), [`static/posture-status.js`](../static/posture-status.js), [`static/standing-settings.js`](../static/standing-settings.js). |
| RFID console | [`static/rfid-console.js`](../static/rfid-console.js), [`static/rfid-core.js`](../static/rfid-core.js), [`static/rfid.css`](../static/rfid.css). |
| 3D viewer server | [`vision/viewer.py`](../vision/viewer.py). Serves the bus site and forwards its permitted requests to the controller. |
| 3D viewer source | [`static/bus/src/app.js`](../static/bus/src/app.js), [`model.js`](../static/bus/src/model.js), [`road.js`](../static/bus/src/road.js), [`travel-motion.js`](../static/bus/src/travel-motion.js), [`entrance-led.js`](../static/bus/src/entrance-led.js), [`message-panel.js`](../static/bus/src/message-panel.js). |
| 3D viewer build | [`static/bus/bus.bundle.js`](../static/bus/bus.bundle.js). Generated from source with `npm run build:bus`; ship its accompanying `.LEGAL.txt` and Three.js license. |
| Passenger server | [`vision/passenger_site.py`](../vision/passenger_site.py). Passenger pages, phone setup and permitted controller/voice proxy routes. |
| Passenger interface | [`static/passenger/index.html`](../static/passenger/index.html), [`app.js`](../static/passenger/app.js), [`core.js`](../static/passenger/core.js), [`route.js`](../static/passenger/route.js), [`arrival-reminder.js`](../static/passenger/arrival-reminder.js). Requests, route display, settings and arrival alerts. |

## Voice and phone setup

| File | Responsibility |
| --- | --- |
| [`vision/voice.py`](../vision/voice.py) | Server-side OpenAI session configuration, short-response instructions, tool definitions and announcement preparation. |
| [`static/passenger/voice.js`](../static/passenger/voice.js) | Browser microphone/WebRTC lifecycle and turn handling. Capture pauses while a reply is prepared and played, then resumes for the follow-up window. |
| [`static/passenger/voice-core.js`](../static/passenger/voice-core.js) | Wake phrase, standby gate, validated app commands and public model context. |
| [`static/passenger/audio-coordination.js`](../static/passenger/audio-coordination.js) / [`spoken-guidance.js`](../static/passenger/spoken-guidance.js) | Audio ownership and queued announcements. |
| [`scripts/setup_voice.py`](../scripts/setup_voice.py) | Private local API-key setup. The key is not a browser setting. |
| [`scripts/setup_passenger_https.ps1`](../scripts/setup_passenger_https.ps1) | Local passenger HTTPS certificate setup. |
| [`docs/VOICE_SETUP.md`](VOICE_SETUP.md) | Microphone, certificate and phone setup instructions. |

## Tests, assets and historical code

Python tests under [`tests/`](../tests/) exercise controller behavior, detection integration, RFID, camera freshness, standing checks and server APIs. JavaScript tests in the same folder exercise browser state, viewer behavior, passenger requests and voice turn handling. Use the README's test commands; the `scripts/check_*` and `scripts/validate_*` utilities may additionally require downloaded models or camera/video inputs.

MediaPipe, semantic seating and LocateAnything adapters remain as historical or optional paths. Their presence does not mean the active standing-only workflow loads all of them. See [`docs/INSIDE_POSTURE.md`](INSIDE_POSTURE.md), [`docs/MEDIAPIPE.md`](MEDIAPIPE.md) and [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) before enabling or distributing optional components.

Publish first-party source, tests, setup templates, the generated viewer bundle and its notices. Exclude `data/`, `models/`, environments, dependency caches, validation recordings/results, private `config.h`, certificates and keys. Model weights are downloaded separately. The project includes third-party assets and dependencies with their own licenses; retain [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md), [`LICENSE.ultralytics.txt`](../LICENSE.ultralytics.txt), [`static/bus/LICENSE.three.txt`](../static/bus/LICENSE.three.txt) and [`static/bus/bus.bundle.js.LEGAL.txt`](../static/bus/bus.bundle.js.LEGAL.txt).
