# Stop scenario validation — 23 September 2026

Historical results below describe earlier timing rules. The 24 September route,
ten-second activity/posture rules and voice update are recorded in
[current validation](VALIDATION_ROUTE_VOICE.md) and [current behavior](STOP_SCENARIO.md).

## Outside-detection waiting and departure notice, 23 September

The controller now renews a ten-second quiet window from each fresh positive
outside-camera observation, with a fifty-second open-door cap. Inside-camera
counts and posture checks do not extend boarding. Twenty-nine new regressions
cover freshness, replayed frames, late inference, the cap, late controller ticks,
notice rescheduling, safety holds and uninterrupted departure-warning messages.
The full backend suite passed **401 tests**; interface tests passed **132 tests**.
This includes loading the passenger entry module and all its JavaScript imports
through the separate passenger server, without exposing operator assets.

Two live cycles exercised the restarted hybrid backend and shared LAN service:

| Case | Doors open to closure starting | Closed to moving | Departure notice to moving* |
| --- | ---: | ---: | ---: |
| No requests or detections | 10.078 s | 3.000 s | 10.144 s |
| Continued outside detections | 50.062 s | 3.109 s | 10.072 s |

\* Estimated from the first observed warning and its server-supplied remaining
duration; state polling can observe transitions slightly after they occur.
Clock-controlled tests independently enforce the full ten-second minimum.

The second cycle sent a staged sample image repeatedly through the real
live-session inference endpoint, producing 117 positive frames. It tests
controller integration with model output, not physical CCTV capture or model
accuracy. Both cycles ended travelling with inference paused, zero passengers
and no active requests. The staged session was removed. Results are saved in
`validation/detection-wait-live.json`.

The operator Stop button was also exercised in the browser. The passenger page
displayed its boarding clock alongside the separate departure-warning countdown;
the 3D page followed the same state. The final display was travelling at its
configured visual speed, with the operator Stop button enabled.

## Quiet-stop correction, 23 September

The preceding Python suite passed **371 tests**, including 14 regressions for the
empty demonstration exception. The updated console script passes its syntax
check. Passenger evidence is retained independently of the posture roster and
survives door transitions, source loss, reset and restart. Known occupants,
accepted activity, emergency, obstruction and mobility holds remain protected.

Two live cycles ran with both CCTVs disconnected and posture checking still
enabled. Open-door intervals were **10.11 s** and **10.10 s**; the post-close waits
were **3.06 s** and **3.00 s**. Both departed automatically and stayed moving until
another Stop command. The final server state is travelling with no active
requests. The LAN operator session was restored and its Stop button verified
enabled. Raw results: `validation/quiet-empty-stop-live.json`.

The following checks describe the preceding 22 September implementation; its
unconditional disconnected-camera hold for an otherwise empty demonstration
has now been replaced by the narrowly scoped exception above.

Final automated suite: **357 Python tests** and **129 JavaScript tests** passed.
The Three.js viewer bundle was rebuilt, and the shared LAN service restarted
with the hybrid detector and MediaPipe Full (27-pose overflow limit).

## Timing and requests

Clock-controlled tests cover 2-second braking with detection paused and doors
closed, the initial 10-second interval starting after door opening, 5-second
activity grace, individual request expiry capped at 10 seconds, the 50-second
boarding cap, exact duplicate retries, and the 3-second wait after door closure.
Emergency, obstruction, missing posture and mobility-area review still hold
departure. Tests cover source changes and in-flight results crossing a motion
transition; old frames cannot publish into a new stop.

Live LAN validation with disconnected cameras recorded:

| Transition | Seconds after stop command |
| --- | ---: |
| Braking, doors closed, detection paused | 0.00 |
| Stationary, doors opening, detection enabled | 2.08 |
| Doors open, boarding starts | 2.92 |
| Doors closing | 12.97 |
| Doors closed, departure check | 13.78 |
| Moving automatically, detection paused | 16.81 |

This is 10.05 seconds from open to closing and 3.03 seconds from closed to moving.
The earlier run with posture enabled correctly remained held because the inside
camera was disconnected. The optional posture setting was temporarily disabled
for the travel-only check, then restored. No camera observations were fabricated.
Raw timing: `validation/quiet-stop-live.json`.

The passenger website was tested through the LAN address: a travelling request
displayed **Queued**, became **7s** during its own allowance at the selected stop,
then **Ended** with an honest unconfirmed-boarding message. The reservation was
released and no passenger was added. No passenger-page browser errors or warnings
were recorded.

## Capacity and pose

Tests cover voted literal-person counts, partial-view retention, 2.5-second
whole-cabin decrease confirmation, frozen frames, source gaps, failed cameras,
restart retention, explicit boarding records as an occupancy floor, full-bus
RFID refusal, and reservations consumed by new camera occupancy. App completion
does not double count a passenger already observed after acceptance; concurrent
requests and intervening RFID/alighting events are included. A count above 26
cannot be cleared by completing an earlier request.

Pose regressions cover weak-joint refinement, ambiguous crop association,
missing joints, the refinement budget, and complete evidence for exactly 26
people versus incomplete evidence at the 27-pose overflow limit. The independent
model comparison and its accuracy limitations are documented in `MEDIAPIPE.md`.
These are functional checks, not a measured bus-camera accuracy percentage.

## Viewer

The live bus showed moving road and wheel distance, then stopped during the
boarding phase. Travel speed reached the configured visual speed of 5.2;
stationary speed returned to zero. Shadow updates remain cached during travel.
A browser-discovered frame-timestamp stall was fixed and regression-tested:
fresh controller state no longer appears invalid when received after the current
animation frame timestamp, and unchanged moving state can wake an idle renderer.

Pure motion tests also cover late braking snapshots, monotonic deceleration,
skipped stop updates, offline/hazard freezing, pause/reduced motion and bounded
frame deltas. Motion is presentation only and does not control the server.

All three LAN roots returned HTTP 200 on ports 4479, 4480 and 4481. Test sessions
were disconnected, posture checking restored, and there were zero active test
requests and zero seeded passengers after cleanup.
