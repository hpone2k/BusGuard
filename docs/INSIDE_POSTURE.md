# Inside-camera standing detection

With the default hybrid backend or YOLOE backend, the inside camera uses **YOLOE** for two separate passes: the ordinary `person` pass counts passengers, and the optional `standing person` pass checks for standing. Both reuse the same local model on the existing GPU worker. Sitting is not classified. These backends do not start MediaPipe or LocateAnything for standing detection.

## Use it

1. Connect the **Inside** CCTV or camera. Select **Object categories** and include `person`.
2. Enable **Standing detection · YOLOE**, adjust the separate **Standing threshold** if needed, then **Apply setting**.
3. Ordinary counting continues throughout boarding and travel. Standing inference starts only when the controller confirms fully closed doors. Opening, open, closing or unknown doors remain count-only.
4. The inside table has a separate **standing person** row. Detected standing people have labelled boxes; no sitting labels or joints are drawn. Standing detections never add again to the passenger count.
5. Enable **Read bus announcements aloud** on the speaking device.

Standing holds simulated departure. After doors are fully closed, departure requires **five continuous seconds of fresh, successful standing checks with no standing detected**. The standing-only check does not require persistent person track IDs, a matching historical occupancy total or a seated classification. Camera errors, stale input and standing detections reset confirmation. **No standing detected is not proof that everyone is seated.** A standing observation during travel gives a reminder without abruptly stopping the simulated bus. The departure announcement and other controller holds must also clear; see [the current workflow](SYSTEM_WORKFLOW.md).

Images and recordings can inspect detections but cannot authorize live departure. Existing doorway, emergency, capacity and mobility-securement holds still apply. After a server restart, **Reset held simulation** is required before movement.

## Implementation and limits

The standing pass prompts exactly `standing person`, at 640-pixel inference resolution. Its separate score threshold defaults to **0.10** and is adjustable from 0.05 to 0.95. The bundled standing sample produced scores around 0.125–0.172; the previous 0.35 threshold hid those detections. This is not a calibrated probability or a threshold validated on bus CCTV. Lower scores can increase false positives; choose the threshold using positive and negative camera examples. Text embeddings are cached. Person counts keep their existing thresholds, tracking and 30-second rounded-mean reconciliation. The standing display has its own 1-second temporal vote; immediate standing observations still block departure. Live observations expire after one second.

The detector is the pretrained YOLOE checkpoint already installed for objects. It has not been fine-tuned on bus CCTV. Text-prompt support does not guarantee accurate posture distinction: seated, leaning, partially hidden or unusual poses can be confused. Test representative positive and negative clips from your camera. Fine-tuning on labelled cabin examples is the next step if the prompt is unreliable. Temporal voting reduces flicker but cannot correct a consistent model error.

See the official [YOLOE documentation](https://docs.ultralytics.com/models/yoloe/) for text prompting and custom training. All inference is local. The older LocateAnything and joint adapters remain historical code and are not loaded by the active hybrid backend.

## Historical verification on the development machine

The measurement below predates the September 28 model/predictor reuse optimization. It is retained as a historical sample result, not a current latency guarantee or a cabin accuracy evaluation. New performance measurements must include the actual camera/network path and both active inputs.

The 90-frame bundled street-sample test kept the ordinary count at four people and detected three standing people. All 30 open-door frames skipped standing inference; the 60 closed-door frames ran it. Combined processing measured 406 ms median and 687 ms maximum; the standing pass alone was 200 ms median. Sitting, MediaPipe and LocateAnything were not invoked. The development run recorded `validation/yolo-standing-integration.json`; generated validation artifacts may be absent from a source-only checkout. The sample does not test seated negatives or cabin accuracy. Run the current diagnostic scripts on the intended hardware rather than treating these historical numbers as a fresh measurement.
