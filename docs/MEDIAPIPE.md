# Optional interior posture detection

In the detector website's **Inside** source, choose **Object categories**, include
`person`, and select **MediaPipe posture**. Deselect it to stop posture inference.
New Inside object-source controls select this option by default; saved source preferences are respected. It is saved in that source's detection options as
`posture_enabled`. Outside sources and detailed-phrase queries do not run it.

Inside object counting runs throughout the journey. Pose inference and joint overlays pause while doors are opening, open or closing, then resume on fresh closed-door frames. Stationary departure requires three continuous seconds of confirmed seating with no timeout. Standing during travel produces a reminder without stopping the bus. Unknown or missing posture cannot clear a stationary departure hold.

MediaPipe Pose Landmarker Full produces 33 body landmarks. The detector preview
draws the visible joints and their connecting segments on the analysed frame.
The shared state reports `seated`, `standing`, and `unknown` observations; absence
of a standing detection is never treated as proof that everyone is seated.

## Install and start

Use the same Python environment as the detector:

```powershell
python -m pip install -r requirements-posture.txt
python scripts/setup_posture.py
python run.py
```

`requirements.txt` also includes the posture requirements. Stop the running
detector before installing dependencies on Windows because an active process can
lock OpenCV's native library. No PyTorch or CUDA change is needed. The pinned
MediaPipe wheel uses the CPU; the object detector continues to use its configured
device. The setup script downloads Google's version 1 Full `.task` file and checks
the project's pinned SHA-256 before installing it. Missing dependencies or weights disable posture
analysis with an explicit message while object detection remains available.

Full is the default balance. To compare variants on the same camera, install one
and restart the server with its name:

```powershell
python scripts/setup_posture.py --variant lite
python run.py --lan --pose-model lite
# Or install/select heavy for an offline comparison; it takes more CPU time.
```

The choices are `lite`, `full`, and `heavy`. Selection applies to the local
posture model only; it does not change the object detector or per-class confidence
settings. No model is silently substituted when the selected file is absent.
Google's published BlazePose comparison reports better joint metrics for Full
than Lite on its Yoga, Dance and HIIT datasets, which use single subjects 2–4 m
from the camera. Those scores do not establish performance on crowded buses.

## How evidence is produced

- Each frame is processed in MediaPipe **IMAGE** mode. MediaPipe does not carry
  tracking state from another camera or session into the current frame.
- The existing object tracker supplies identities. A pose needs an unambiguous
  match to both a fresh person track and a raw person detection before it can
  contribute seated or standing evidence. Predicted tracks remain unknown.
- Geometry uses pixel coordinates so widescreen frames do not distort limb
  angles. Both visible shoulder/hip/knee/ankle chains must agree. Low visibility,
  low presence, extrapolated joints, very short limbs and ambiguous angles return
  unknown. The exported overlay retains all 33 normalized joints with visibility
  and presence values; only sufficiently visible joints are drawn.
- One full-frame pass can return up to 27 poses: 26 passenger places plus one
  overflow sentinel. Exactly 26 uniquely tracked, visible poses can therefore be
  complete; reaching 27 still marks the accounting incomplete. Up to four additional crops use
  the original-resolution image to revisit missing poses, weak joints and poor
  box matches. A crop must belong unambiguously to one detected person, and may
  replace an existing pose only when visibility or localization improves. It is
  never preferred merely because its posture label is seated.
- Refinement stops starting new crops once 120 ms has elapsed since the frame's
  posture pass began. This is a soft budget: an already running native inference
  can finish after it, and the initial pass costs more for a crowded cabin. Work
  skipped for the budget remains unknown. Increasing the pose limit does not
  establish that a single camera covers 26 passengers.
- A reached pose capacity marks accounting incomplete. Missing poses and people
  outside the detector's confirmed tracks cannot silently become seated.
- Live and shared-state evidence expires after one second. A direct still-image
  result keeps its matching joints for static inspection, even after a slow first
  inference, but is never live cabin evidence. A source disconnect, cancelled
  request or changed source cannot reuse a previous posture result. RTSP overlays
  use the exact processed frame, rather than drawing old joints over a newer one.

The API's `seating_summary` retains the existing counts and adds `engine`,
`connections`, `model`, `max_people`, `capacity_reached`, `crop_refinements`,
`refined_poses`, `refinement_budget_ms`, `refinement_budget_exhausted`, and
`analysis_ms`. Each uniquely matched visible occupant may contain `landmarks`:
33 objects with `x`, `y`, `z`, `visibility`, and `presence`.

## Measured performance and limits

Eight local runs of the updated pipeline on the included four-person street
sample gave these results; the first two runs of each variant were excluded from
timing statistics. This comparison used a configured maximum of 26 poses before
the overflow sentinel was added; the JSON retains that original configuration:

| Model | Median posture time | Warm range | Final visible posture counts |
| --- | --- | --- | --- |
| Lite | 62.95 ms | 61.9–65.8 ms | 2 standing, 2 unknown |
| Full (default) | 68.9 ms | 67.4–71.1 ms | 1 standing, 3 unknown |
| Heavy | 126.8 ms | 121.2–129.3 ms | 1 standing, 3 unknown |

All results remain incomplete, with zero seated people. Full and Lite used three
crop attempts; Heavy reached the soft budget after two. These results support
using Full at a modest additional CPU cost on this laptop. They do **not** measure
joint accuracy: there are no labelled bus-camera joints in this sample. A stronger
landmarker can still produce ambiguous projected posture rather than a definite
seated or standing label. The classification rules were not weakened to make the
counts look more certain.

Raw results: `validation/mediapipe-precision.json`. Reproduce the comparison using
`python scripts/benchmark_posture.py`; pass `--image path/to/local-image.jpg` for
a local test image. Images remain local. The previous Lite benchmark is retained
in `validation/mediapipe-smoke.json` for history.

Posture is an experimental geometric estimate, not a trained bus-seat occupancy
classifier. Seats, bags, other passengers and camera angle can hide the legs;
frontal sitting may foreshorten thighs. Expect unknown results in those cases.
Visible joints do not establish that a wheelchair is secured, a passenger is
safely seated, the cabin is empty, or every passenger is in view. The simulated
bus controller must retain those distinctions. Before a demonstration, test the
actual interior camera angle with seated, standing, occluded and wheelchair
passengers; show unknown explicitly rather than weakening the checks to force a
clear result.

For camera setup, keep both shoulders, hips, knees and ankles visible; reduce
motion blur and avoid looking straight down into the aisle. A side-oblique view
separates a seated thigh from a standing leg more clearly than a frontal view.
Use the displayed joints to check placement before adjusting confidence. Hidden
legs require another view or explicit confirmation, not fabricated joints or
longer averaging. The all-seated departure check still requires fresh evidence
to remain clear for its two-second verification interval.

## Local processing and the MediaPipe SDK

Frames and pose inference stay on the server in this implementation, and it
works without an internet connection once installed. However, the official
MediaPipe 0.10.35 binary attempts to send SDK usage telemetry. Google states that
this telemetry does not send input data and has no official runtime opt-out API;
building the SDK from source or blocking its telemetry host are the documented
alternatives. This project does not silently change the computer's firewall or
claim that the binary produces zero outbound traffic.

References:

- [Official Python Pose Landmarker guide](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/python)
- [Official models and landmark definitions](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker)
- [Google's model card, evaluation scope and limitations](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20BlazePose%20GHUM%203D.pdf)
- [Published BlazePose model comparison](https://chuoling.github.io/mediapipe/solutions/pose.html#pose-estimation-quality)
- [Google's response about SDK telemetry](https://github.com/google-ai-edge/mediapipe/issues/6291)
