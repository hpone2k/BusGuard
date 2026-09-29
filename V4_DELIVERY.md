# BusTech v4 — September 22, 2026

## Two websites, one inference service

- `http://localhost:4479`: two-input detection console and decision diagnostics.
- `http://localhost:4480`: 3D bus viewer with a compact glass passenger panel.
- `start.cmd` or `python run.py` starts both. The viewer has a separate HTTP listener, reads the detector's current JSON state over loopback, and never instantiates the GPU model. A failed upstream request produces an unavailable state, never a cached count.
- The legacy `http://localhost:4479/bus` route also serves the new viewer.

The Outside and Inside inputs can independently use CCTV, webcam, image or video. Recorded-video sessions now receive tracking and time-weighted count voting, while preserving `kind: video`. Images remain `kind: image`. Neither type can satisfy the live departure interlock. Stop/source replacement cancels pending detection and discards late results.

The indoor `person` estimate supplies the bus's visible passenger count. Equipment categories are not added to it: a wheelchair and its user may both be detected. Counts describe the camera view; occluded or unseen passengers are not accounted for by object counts alone.

## Phrase handling

YOLOE object detection remains the primary model. Clothing phrase mode first detects and segments people, then checks dominant color within the central torso, rejecting ambiguous, small, cropped and overlapping regions. Examples: `a person with a red shirt`, `person wearing a blue top`. Eleven basic colors are supported, with `grey` accepted as an alias for `gray`.

This is a bounded, uncalibrated clothing-color filter. It is not a general language grounding model, nor a separate garment classifier. The returned score remains the person detector's confidence. Unsupported descriptions fail before creating a source. Solid visible tops are the intended use; covered shirts, patterns, illumination and segmentation errors can cause misses.

The adapter uses the official [YOLOE interface](https://docs.ultralytics.com/models/yoloe) and original-image normalized [segmentation results](https://docs.ultralytics.com/modes/predict). Optional grounding adapters remain available through the existing CLI but were not validated as part of this release.

## Validation

- 137 Python tests passed, including source isolation, cancellation, recorded-video provenance/voting, separate viewer failure behavior, credential redaction, clothing-color masks/distractors, RTSP backpressure and the existing seating logic. Two upstream test-library deprecation warnings remain.
- 71 JavaScript tests passed, including camera geometry, seat inventory, cutaway visibility, count provenance, source-switch cancellation, late webcam permission handling, reload ownership and the departure gate.
- Browser checks verified desktop and 390-pixel mobile layouts, exterior/cabin view transitions, all four input selectors, image upload/detection, H.264 recorded-video playback/detection, source switching and per-source phrase/confidence controls. The fixture produced four stable people with approximately 26 ms model inference in the observed video frame. This timing excludes camera capture; it is not a general benchmark. No new browser console errors were observed in the final console check.
- A real image inference request on port 4479 was independently read through port 4480: the session identity, image provenance and four-person estimate matched. Test sources were released afterward. The viewer correctly showed unavailable data once the snapshot expired.
- Real YOLOE/CUDA smoke testing on the bundled sample detected four people. A black-top query selected the central dark-clothed person at about 24 ms warm inference. Red, blue and white queries returned no match. The person's partly covered red shirt was missed, which demonstrates the covered-clothing limitation; this is not an accuracy benchmark.
- The previously configured camera's RTSP port was unreachable from this computer during this release's check. The September 14 successful physical-camera test remains historical evidence only. Current camera-to-screen latency and current physical-camera detection accuracy have not been verified.
- The previous preview cadence was approximately one second. The console now requests processed camera frames independently of the model scene, without overlapping preview requests. Decoder probing/buffering remains at the previously working settings; startup allowance remains 20 seconds, followed by an 8-second stall timeout.

No user camera images or camera credentials are saved in this validation record or the source archive.
