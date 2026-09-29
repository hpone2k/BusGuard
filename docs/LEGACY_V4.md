# BusTech — Detection Console & Bus Viewer

This is an archived version 4.1 reference. Its two-site interface, seating interlock, RFID integration and validation describe that earlier delivery, not the current system. For current behavior and installation, read the [main README](../README.md), [System workflow](SYSTEM_WORKFLOW.md) and [Setup and operations](SETUP_AND_OPERATIONS.md). Runnable examples below use a project-local `.venv`; historical results have not been re-measured here.

Version 4.1 has two separate local websites: the **[Detection Console](http://localhost:4479)** and the **[3D Bus Viewer](http://localhost:4480)**. Both start with `start.cmd`. One model service supplies both websites; the viewer does not load a second detector. The earlier `../vision4477` project remains separate.

## Detailed phrases in version 4.1

In either source card, open **Detection settings → Detailed phrases**. Enter up to eight descriptions, one per line or separated by semicolons. Each gets its own match threshold. Prompts, thresholds and resolution choices are retained separately when switching between object and phrase modes. Start with 512 px for phrases; use 640 px when smaller details need more resolution. Matching scores are not calibrated probabilities.

The default **hybrid** backend uses YOLOE for object categories and a local Grounding DINO model for detailed phrases. Requested phrases are evaluated together in one image pass, with bounded text caching, exact output labels and separate tracking per phrase. Description matching remains imperfect: visibility, wording, occlusion and similar nearby people can cause misses or incorrect matches. See [PHRASE_DETECTION.md](../PHRASE_DETECTION.md) for setup, implementation, measurements and limitations.

For browser cameras and recorded video, phrase previews show the analysed frame with its matching boxes and observation age. This avoids placing delayed boxes on a different video frame. The original capture continues in the background and only one request per source is in flight. RTSP previews already pair each processed frame with its result.

Keep the indoor source in **Object categories** with `person` when evaluating cabin occupancy and seating. Descriptions such as `a person with a red shirt` select subsets and cannot establish that every passenger is seated. One person may match several phrases, so phrase counts must not be added into a passenger total.

## Version 4 workflow

- The Detection Console has two independently configurable inputs, **Outside** and **Inside**. Each can use an RTSP/RTSPS CCTV, a browser webcam, an image, or a recorded video. Choose the source type on the card, provide its input and start detection. Stop or change source to release its session and device. Browser video formats depend on installed browser codec support.
- Each card has its own object prompt and confidence per category. Image counts are a single snapshot. Camera and recorded-video counts use a one-second time-weighted vote, with 65% support required for a stable estimate. Vote support is agreement over time, not the probability that the model is correct.
- The Bus Viewer is a full-screen Singapore-inspired autonomous bus with a small glass passenger panel and compact inspection controls. Its onboard estimate comes only from the indoor **person** category; outside passengers are waiting passengers. Wheelchairs, strollers and walking aids are separate observations that can overlap with people, so their counts are not added to the passenger total. Images and recorded video keep their provenance; missing/stale input is unavailable, not zero.
- The default hybrid backend supports detailed phrases as described above. The previous restricted clothing-colour heuristic remains available only when launching explicitly with `--backend yoloe`.
- CCTV preview requests are bounded and independent of the 3D rendering. FFmpeg retains normal probing/buffering for reliable startup, with a 20-second first-frame allowance and an 8-second established-stream stall timeout. Only the latest decoded frame waits for inference; skipped frames prevent a processing backlog. Pipeline timings do not measure latency inside the physical camera.
- Detection and decision information lives in the console. Recorded inputs cannot satisfy the live seating requirement. The existing conservative interlock requires fresh, complete indoor posture evidence and keeps departure held when a known passenger is missing or standing. All bus responses are virtual prototype behavior.

Camera credentials remain in memory and must be entered again after a server restart. The viewer on port 4480 only proxies `/api/bus/state`; it cannot configure cameras. Use `--viewer-port PORT` to change its port or `--viewer-port 0` to run only the detector. Navigation links use the default ports.

The sections below retain details of the previous inference implementation and historical validation. Version 4's interface and source workflow above supersede the old Studio controls and single-input workspace instructions.

## Historical host setup

The original development launchers used this folder's `.venv` when present and could otherwise reuse an installed Python/CUDA environment in a sibling checkout. That sibling is not included in the public source. For a fresh clone, create a project-local `.venv` using [Setup and operations](SETUP_AND_OPERATIONS.md), then double-click **start.cmd** or run `./start.ps1`.

The original host had downloaded the YOLOE-26m model, text encoder and Grounding DINO Tiny checkpoint; a source checkout must obtain them through setup. Open [4479 Vision](http://localhost:4479) if the server is already running; avoid starting a second instance on the same port. Stop a foreground server with Ctrl+C. Stop other GPU benchmarks when comparing detector performance.

```powershell
# From the project root, after creating its local environment:
.\.venv\Scripts\python.exe run.py
```

## What changed

**Seating before departure:** Bus Studio now alerts standing passengers after door closure and holds virtual motion until all accounted passengers have two seconds of fresh seated evidence. Inside CCTV sessions use an additional local pose model; missing or uncertain posture keeps the bus held. Try **Scenario lab → Test seating interlock** for the animated three-passenger demonstration. See [SEATING_INTERLOCK.md](../SEATING_INTERLOCK.md) for the data contract, model setup and limits.

**BusTech 3D studio:** Open **[the autonomous bus concept](http://localhost:4479/bus)** for the Singapore-inspired model, 6 priority seats, 20 regular seats, an exterior camera between the two doors, an interior aisle camera, cabin inspection and animated boarding assistance. Bus Studio receives actual detection results from two assigned sources and can ingest two RTSP/RTSPS IP cameras through the existing model. Live source state and recent annotated camera previews are separate from the virtual scene and manual demonstration controls. See [BUS_STUDIO.md](../BUS_STUDIO.md) for connection steps, API contracts, rebuilding and validation.

To connect the CCTVs, enter each camera's RTSP address in Bus Studio and assign it **Outside** or **Inside**. The computer must be on a network that can reach the cameras. URLs and credentials remain in memory; camera configuration must be entered again after a server restart. For browser-camera testing, select the **Accessibility** prompt preset in the detection workspace, choose a **Bus Studio source** role and start detection. Images are explicitly tagged as tests; offline video jobs do not trigger live bus actions. Senior assistance accepts an RFID event category through the local API, with no visual age or pregnancy inference. The doors and ramp remain virtual, and no physical actuator or RFID device driver is bundled.

- **Confidence per object:** each comma-separated object name gets its own 5–95% slider, initially 35%. Choices follow their object names through additions, reordering and temporary text edits, and are saved with video jobs. Images, live sessions and video analysis apply the separate thresholds.
- **Time-weighted count voting:** camera and newly analyzed video counts use a rolling 1,000 ms window, with 65% vote support required for a stable estimate. Counts are calculated separately for every object type and for all objects, using observed tracks only. The workspace and bus dashboard show stable estimates, observed counts and vote support, with explicit warming, uncertain and stale states. See [COUNT_VOTING.md](../COUNT_VOTING.md).
- **YOLOE-26m** replaces the small model as the default, using CUDA FP16. Default input resolution is 640 and new-object confidence is 35% per type.
- **BoT-SORT** associates detections over time, using Kalman motion prediction and high/low-confidence matching. A separate tracker for each object label prevents cross-label matches. IDs are local to a source; starting another session cannot reset them.
- **Two-of-three confirmation** hides one-frame candidates. A newly seen object must match in two of three processed observations before it becomes visible. This intentionally adds a short confirmation delay and may omit very brief appearances.
- **Confidence hysteresis:** each object's slider controls new tracks of that type; candidates down to `min(0.15, that type's confidence / 2)` can maintain its existing tracks. Lowering the detector threshold does not allow weak unmatched candidates to create boxes.
- **One Euro filtering** smooths each track's box center and size using source timestamps. It responds more quickly when motion increases. This reduces visual jitter; it does not make a wrong detection correct.
- **Brief-miss recovery:** confirmed tracks can be predicted through up to 150 ms in Balanced mode or 100 ms in Responsive mode. Predicted boxes are dashed in preview and MP4 export. Prediction deadlines remain attached to results, so network delay or video interpolation cannot extend them.
- **Camera-motion compensation:** sparse optical flow estimates camera movement once per frame and shares the transform across all label trackers. Implausible transforms are rejected. This assists association; it cannot guarantee compensation for scene cuts or heavy motion blur.
- **Live overlay compensation:** observed boxes can shift by estimated velocity for up to 100 ms of capture-to-display delay, bounded to a quarter of the box width/height. Size is not extrapolated. Already-predicted boxes are not extrapolated again. Live results older than 250 ms are hidden.

BoT-SORT's upstream association machinery is supplied by pinned Ultralytics 8.4.143. This app customizes ID allocation and candidate activation to support source isolation and two-of-three display confirmation. Appearance/ReID encoding is **not enabled**; this version does not promise re-identification through long occlusions or crowded look-alike objects.

## Controls

1. Choose Camera, Image, Video, or the bundled sample. Enter object names and press Start detection.
2. Begin with **640 · Balanced**, **35% confidence per object**, and **Balanced · Steadier boxes**. Entering `person, laptop, bottle` creates three sliders. Increase a type's threshold to reject more weak detections of that type, or lower it to accept more. Changing one slider leaves the other choices intact.
3. Use **Responsive · Faster motion** if smoothing trails quick movement. Use **Off · Raw detections** to compare with unfiltered predictions; this disables tracking, confirmation, smoothing and brief-miss prediction.
4. Choose **Fixed camera** to disable optical-flow camera compensation when the camera does not move.
5. For video, analyze every frame for the most complete tracking. Skipping frames adds confirmation time and can miss short appearances. Changing settings requires starting a new video job.
6. Play or seek the original video, save the detection JSONL, or export an annotated H.264 MP4. The first source audio track is retained and transcoded to AAC when present.

Stabilization applies to cameras and recorded video. Still images use the detector directly, with no multi-frame confirmation or smoothing. The default hybrid backend routes descriptions to Grounding DINO and object names to YOLOE.

## Installation elsewhere

Tested here on Windows, Python 3.12, RTX 4060 Laptop GPU (8 GB), CUDA PyTorch 2.11.0+cu128. Git is needed for the pinned CLIP tokenizer dependency.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\setup_grounding.py
.\.venv\Scripts\python.exe run.py
```

First startup downloads official YOLOE-26m weights (about 70 MB) and the text encoder (about 254 MB) if absent. A model warm-up runs before detection becomes available. `requirements-lock-windows.txt` records the installed environment; restoring CUDA wheels from it needs the PyTorch CUDA index above. On Linux use an appropriate CUDA build and `.venv/bin/python`; Linux has not been tested in this delivery.

`--device cpu`, `--model CHECKPOINT`, `--phrase-model DIRECTORY`, `--port PORT` and `--ffmpeg PATH` are supported. To test the original small YOLOE checkpoint, supply the path to your own downloaded file, for example `--model path/to/yoloe-26s-seg.pt`. `--backend demo` detects colored regions for offline UI checks and is explicitly labeled as nonsemantic detection. The default hybrid path requires only `requirements.txt` and the local model download above; it does not require quantization packages or executable remote model code. Legacy standalone `grounding-dino` and `locateanything` adapters remain separate experiments; LocateAnything requires its own compatible environment and is not validated in this delivery.

## Reliability retained from version 2

The single inference worker uses a bounded fair queue. Only one live frame per browser session is in flight; obsolete requests/results are invalidated. Stop cancels pending work, discards a running canceled GPU result, and terminates an active FFmpeg export. A GPU call already executing must finish before its resources can be reused.

Uploads are streamed and validated. Limits remain 12 MB/24 megapixels per image, 512 MB/15 minutes/4K/120 FPS per video, one upload, three admitted video jobs, 24-hour retention and a 4 GB application data budget. Video jobs have durable status and restart recovery. Export tries NVENC then libx264, checks both failures, verifies the encoded output and atomically publishes it. Decoder handles close before a job becomes deletable on Windows.

Original-video playback requires browser codec support. Export uses source metadata for constant frame rate; variable-frame-rate sources may need additional timing checks. Prediction is short and bounded; extended occlusion, fast motion, camera cuts and false detections can still cause track loss. This app is localhost-only and has no multi-user authentication.

## Validation and API

See [VALIDATION.md](../VALIDATION.md) for historical measurements, limitations and completed checks. The original `benchmark.json` and `benchmark-stability.json` were generated outputs and are not required or included in a source-only checkout. The jitter measurement uses a controlled synthetic input, not a labeled camera dataset.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm test
.\.venv\Scripts\python.exe -m pip check
# Standalone model timing: stop other GPU inference before running.
.\.venv\Scripts\python.exe scripts\benchmark.py --repeats 30
# Against a running 4479 server: generates a fixture from the bundled sample.
.\.venv\Scripts\python.exe scripts\validate_live.py
```

The [local OpenAPI schema](http://localhost:4479/openapi.json) lists endpoints. Create a live session with `POST /api/sessions`, specifying `kind: live` and options `stabilization: balanced|responsive|off`, `camera_motion: true|false`, `confidence`, `class_confidences`, `size` and `prompt`. Send binary JPEG frames to `/api/sessions/{id}/frames?frame_id=N&captured_at=MS`. Timestamps must increase and use the browser/source clock. Responses include separate model/tracking/queue timings, capture time, track ID, normalized coordinates, observed time and prediction expiry. Still-image confidence remains the actual detector score; optional backends without scores return null and their confidence controls are disabled.

Session creation also accepts `source_role: outside|inside|unassigned` and an optional `source_name` of at most 80 characters. Assigned sources feed `GET /api/bus/state`, which includes a per-process `instance_id`, a revision, current observations and recent assistance events. The instance ID lets consumers discard old event cursors after a server restart. RTSP camera configuration and RFID input contracts are documented in [BUS_STUDIO.md](../BUS_STUDIO.md).

Frame responses, Bus Studio sources and newly generated video timeline rows include `count_summary`. For a passenger estimate, read `count_summary.classes.person`, check its `status`, and use `stable` when available. `support` is a time-vote fraction, separate from the detector's per-object confidence. Full schema and freshness behavior are documented in [COUNT_VOTING.md](../COUNT_VOTING.md).

`class_confidences` is an optional map of prompt names to thresholds, for example `{"person": 0.70, "laptop": 0.30, "bottle": 0.50}` with `prompt: "person, laptop, bottle"`. Names are matched case-insensitively; values must be between 0.05 and 0.95. The legacy `confidence` option remains the fallback for omitted types, so old clients and saved jobs still work. With `mode: "phrase"`, separate descriptions using newlines or semicolons and use each complete phrase as its confidence-map key. Commas inside a description are retained. Tracking has a separate recovery floor for each label.

The source archive excludes environments, downloaded weights and user uploads. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and `LICENSE.ultralytics.txt` for model/dependency attribution and license information.
