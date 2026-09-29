# Version 3.1 validation — 4479

Completed on September 9, 2026, on Windows / Python 3.12 / NVIDIA RTX 4060 Laptop GPU (8 GB). Detector: YOLOE-26m, Ultralytics 8.4.143, CUDA PyTorch 2.11.0+cu128. Tracker assignment dependency: LAP 0.5.12.

## Version 3.1 confidence controls

- **35 Python tests and 11 frontend tests passed.** Added checks cover per-class validation and fallback, independent YOLOE filtering, image-cache separation, different track-creation/recovery thresholds, durable video settings and the full video analysis pipeline. Frontend checks cover adding/reordering names, temporary empty edits, restoring new and legacy settings, and phrase mode. Python compilation and JavaScript syntax checks also passed.
- In the actual localhost interface, entering `person, laptop, bottle` produced three sliders with independent values. Reordering the names preserved person at 80%, laptop at 20% and bottle at 35%.
- The actual CUDA model detected five objects (people and a bus) in the bundled sample with person at 35%, bus at 35% and laptop at 20%. Raising only person to 95% left one bus detection, with the other two slider values unchanged.
- The performance measurements below are retained from version 3; a new full timing benchmark was not run for this settings update.

## Version 3 baseline automated checks

- **30 Python tests passed.** This includes actual BoT-SORT association, source-local IDs, cross-label isolation, two-of-three confirmation, rejection of one-frame false positives, low-confidence continuation, camera-shift compensation, adaptive filtering, long-gap resets, source-time prediction expiry and distinct Responsive-mode deadlines. Retained API, image validation, stale/canceled requests, queue fairness, retention, decoder release, restart recovery, actual MP4 encoding, audio retention and encoder-failure tests also pass.
- **8 frontend tests passed.** Source invalidation, letterboxing, stable-ID interpolation, bounded live compensation, no repeated extrapolation, total prediction age including transport, offline expiry and Responsive-mode expiry.
- `pip check`, Python compilation and JavaScript syntax checks passed. Test output contains two upstream Starlette/AnyIO deprecation warnings; neither fails the tests.

## Actual GPU / HTTP sequence

`scripts/validate_live.py` sent 80 JPEG frames through a live session using the actual medium model, Balanced stabilization, camera compensation, 640 detector input and confidence 0.35. Source frames are 480 × 640, generated from the bundled bus sample with horizontal camera translations, a two-frame blank interval, and a longer blank interval. Source timestamps simulate 30 FPS; this is not a physical webcam recording.

The first observation remained hidden, subsequent observations confirmed tracks, the short blank interval produced dashed predictions, and the longer blank interval cleared the results. Cancellation/cleanup of the test session succeeded.

After omitting five warm-up requests:

| Measurement | Result |
|---|---:|
| JPEG encode + local HTTP median | 29.46 ms |
| JPEG encode + local HTTP 95th percentile | 31.71 ms |
| Model inference median | 17.9 ms |
| Tracking median | 4.8 ms |
| First request, including prompt initialization | 1045.35 ms |

These timings exclude camera capture and browser/display latency. The sequence includes blank frames, so detector timing differs from the all-photo benchmark below. First use and changed prompts can remain much slower than warmed requests. Full summary: `benchmark-stability.json`; fixture and raw responses are under `validation/` in the installed project.

## Controlled jitter test

The test feeds a stationary known box 100 noisy horizontal measurements at 30 FPS, with a fixed random seed and normalized-coordinate noise standard deviation 0.004. After the initial 11 observations, the filtered box's horizontal-coordinate standard deviation is **55.1% lower** than the raw inputs.

This demonstrates filter behavior on synthetic jitter. It does not establish a 55% improvement on real footage, detection accuracy, identity switches in crowds, or performance through long occlusions. Adaptive filtering trades some motion response for steadier coordinates; Responsive mode reduces that filtering delay.

## Standalone detector benchmark

`scripts/benchmark.py --repeats 30` used the bundled sample, `person, bus`, confidence 0.35, five warm-up calls per resolution and 30 measurements. Cached-weight startup plus model/tracker warm-up took 9.0 seconds.

| Input | Median | 95th percentile |
|---|---:|---:|
| 384 | 19.46 ms | 32.00 ms |
| 512 | 19.01 ms | 28.50 ms |
| 640 | 21.46 ms | 29.38 ms |
| 960 | 26.14 ms | 32.69 ms |

These numbers cover detector preprocessing, inference and output extraction; they exclude per-frame tracking and HTTP/display. Memory values in `benchmark.json` are PyTorch allocated memory, not total process VRAM. No direct accuracy comparison with the earlier small model was performed.

## Browser workflow

The actual localhost 4479 interface loaded with YOLOE-26m/CUDA, 640 resolution, 35% confidence and Balanced mode. Browser checks covered fixture upload, 80-frame analysis, playback to the end without a media error, MP4 export with a download link, restoring a job after server restart, and changing stabilization/camera settings. The video decoded at readiness 4; the browser console reported no JavaScript errors or warnings during the completed analysis/export flow.

Physical webcam access, an annotated real-world tracking dataset, hours-long stress tests, arbitrary codecs and optional phrase-grounding backends have not been exercised. Appearance/ReID is disabled. Long occlusions, similar overlapping objects, scene cuts, lighting changes and very fast motion may still cause track loss or incorrect detections.

The source ZIP contains code, tests, launchers, pinned dependency files and these reports. It excludes environments, weights, uploads and generated test videos. The installed 4479 folder reuses the sibling 4477 virtual environment and has its own code, model files and data directory.
