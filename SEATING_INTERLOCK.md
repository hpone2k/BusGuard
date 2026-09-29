# Seating and virtual departure

> **Historical seated-posture design.** The active application uses YOLOE standing-only checks, not this earlier seated-roster rule. See [Standing departure](docs/STANDING_DEPARTURE.md) and [System workflow](docs/SYSTEM_WORKFLOW.md).

This document records an earlier browser-only prototype. For current standing-only departure behavior, see [Standing detection and departure](docs/STANDING_DEPARTURE.md).

Bus Studio holds its virtual journey until the doors have fully closed, the ramp is stowed, assistance is acknowledged, and every accounted passenger has remained visibly seated in fresh indoor observations for two seconds. A standing passenger triggers the announcement: **Please take a seat. The bus will move when everyone is seated.** Optional spoken announcements are enabled with the panel's audio switch.

## Try the complete interaction

1. Open `/bus`, select **Scenario lab**, then **Test seating interlock** under Vehicle inspection.
2. Three passengers appear: two seated and one standing. The doors close, the seating panel shows HOLD, and virtual speed stays at zero.
3. Select **Seat passenger**. The passenger sits, followed by a two-second confirmation. The virtual journey starts: wheels turn, the stop moves past the following camera, and speed rises to 8 km/h.
4. Select **Stand passenger**. Permission is withdrawn and virtual motion stops. Re-seating requires another complete confirmation period.
5. **Reset test** returns the scene to the stop. Live detection and scripted evidence remain separate.

The existing boarding scenarios also enter the seating check after their doors close. A scenario with a standing pram user remains held. The dedicated seating test demonstrates the full standing-to-seated transition.

## Indoor CCTV evidence

Inside-assigned live sessions run a small, separate YOLO26n-pose pass on the same GPU worker as object detection. Outside and unassigned sessions retain their current inference path. The pose model produces 17 body keypoints; local geometry rules classify clear bilateral leg configurations as `seated` or `standing`. They require visible shoulders, hips, knees and ankles, confident joints, sufficiently large limbs, and consistent geometry in **pixel coordinates**. All other configurations are `unknown`.

This is experimental apparent-posture estimation, not a trained bus-seat occupancy classifier. A crouching person can resemble a seated person in 2D. Foreshortening, seat backs, a wheelchair or a standing passenger blocking another passenger can obscure the necessary joints. These conditions require camera-specific validation, better coverage and, for an actual vehicle, independent occupancy and vehicle-control inputs. Nobody is required to leave a wheelchair to satisfy a visual seat-class label. This prototype cannot verify restraints or wheelchair securement.

The model is installed at `models/yolo26n-pose.pt`. On another computer run `python scripts/download_pose.py` from its environment, then restart the server. The downloader uses the official Ultralytics release and checks the asset size and published SHA-256 digest when supplied. Missing, failed or unavailable pose weights leave normal object detection available while departure remains held. `/api/status` exposes `backend.seating` availability.

The pose model and keypoint mapping are documented by [Ultralytics](https://docs.ultralytics.com/tasks/pose). The installed checkpoint is from [official assets release v8.4.0](https://github.com/ultralytics/assets/releases/tag/v8.4.0), SHA-256 `eb3bb8268828aeaf515cec23a4bfafd793944a86fe9af94ba7823609c14522a9`.

## Evidence and confirmation rules

- Raw people, confirmed person tracks and pose detections must match unambiguously. New, unmatched and predicted people remain unknown; tracker warm-up cannot hide an arriving person from the check.
- After closure, the interlock remembers each observed person ID. A disappeared person or changed ID stays unaccounted for through that closed-door cycle. Reopening the doors or replacing the source starts a new cycle.
- This virtual interlock runs in the browser. Reloading the page or restarting the server resets its history and requires new confirmation; it is not a persistent vehicle controller.
- Zero standing detections alone never release departure. Zero visible people also cannot establish that the cabin is empty.
- Freshness expires at **1,000 ms**. Disconnection, unknown posture, incomplete accounting, lost identity or reordered timestamps revoke permission. The live UI checks the interlock every 100 ms.
- Confirmation requires **2,000 ms of advancing capture timestamps** after closure, with gaps no longer than 1,000 ms. Repeated polls do not add evidence. A new passenger or an interruption restarts confirmation.
- Images, demo detectors and outside cameras cannot authorize live departure. The Scenario lab uses a separate interlock instance with explicitly allowed synthetic evidence.
- The animation also checks actual door/ramp positions, opening requests, assistance and the permission callback's freshness before moving. Permission loss stops the virtual scene immediately. This is not a physical braking algorithm.

Frame results and bus-source snapshots expose `seating_summary` and `seating_ms`. The summary contains `status`, `people`, `seated`, `standing`, `unknown`, `complete`, `occupants`, `captured_at_ms`, `age_ms`, `method` and `reason`. Each occupant includes `track_id` and `posture`. `complete` refers to matching/accounting coverage; it does **not** mean everyone is seated. An expired summary has null counts and `complete: false`.

The one-second count vote remains a separate display estimate. It does not delay a standing alert or replace person-by-person seating checks. All permissions and movement in this implementation apply only to the digital twin; no hardware controller is connected.

## Validation

The integrated Python suite passes 97 tests; the frontend suite passes 56 tests, including 18 departure-state tests and two motion tests. Cases cover exact freshness boundaries, door/ramp interlocks, lost passengers, source changes, stale/replayed frames, unknown posture, invalid accounting, and permission loss during movement.

On this computer, the pose-only GPU sample check measured 12.5–15.8 ms after warm-up. The bundled photograph was conservatively classified as one standing person and three people with unknown posture. This is a performance and integration check, not a CCTV accuracy benchmark.

The actual running API and Bus Studio were checked together with repeated sample frames: the live panel showed one standing and three unconfirmed passengers at zero speed; stopping the source changed the posture data to stale and kept departure held. The scripted three-passenger path was verified in the browser through standing, seated confirmation, moving at 8 km/h, standing again, and reset. Desktop and narrow-screen layouts were inspected without browser errors. Local runtime results are in `validation/seating-live-smoke.json` (excluded from the source archive).
