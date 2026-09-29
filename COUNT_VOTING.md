# Rolling count estimates

The detection workspace and Bus Studio receive the same backend count estimates. The model and tracking continue to produce boxes; a separate voting layer stabilizes the reported numbers. This reduces count flicker and adds confirmation delay. It does not establish ground-truth passenger occupancy or correct consistent detector mistakes.

## Default behavior

- **Window:** the preceding 1,000 ms of observed source time.
- **Stable estimate:** a unique winning count with at least **65%** of observed time, after a full window has been collected.
- **Uncertainty:** keep the previously supported count for at most 1,000 ms after its last confirmation, explicitly marked uncertain. Then show no estimate.
- **Staleness:** clear counts at 1,500 ms without fresh observations. This is earlier than the bus bridge's existing 2,500 ms source expiry.
- **Long gap:** an observation gap over 1,000 ms resets the count history. A slow or interrupted feed must collect a new window.
- **Inputs:** observed detections only. Predicted boxes do not vote. The same track ID is counted once within its object class.

Example:

| Count | Observed duration | Vote support |
| --- | ---: | ---: |
| 7 | 700 ms | 70% |
| 6 | 200 ms | 20% |
| 5 | 100 ms | 10% |

The stable estimate is **7**, even when the newest frame currently contains 5 observed people. The 70% represents agreement over time, not a 70% probability of a correct count.

Each newly accepted frame closes the preceding observation's interval. Actual elapsed time supplies the vote weight, so processing more frames does not give that period extra votes. Polls and browser renders cannot extend the final frame or add evidence. They only age the previously computed result and clear expired values. Capture timestamps must advance within a session.

## Where it applies

- Browser cameras and RTSP sources each have an independent voter. Source replacement or settings changes start a new history.
- Newly analyzed videos use **media timestamps**, so slow processing, pausing and seeking do not change the vote. Playback selects the latest recorded result at or before the playhead.
- Still images show instantaneous counts with no temporal support percentage.
- Previously processed videos without count summaries display explicitly labelled observed counts. Reanalyze them to obtain voting data.
- Count voting remains active when box stabilization is set to Off.

Bus Studio shows **visible people per camera**. Inside and outside estimates are not added together: they can overlap, and a camera can miss occluded passengers. These counts do not command physical hardware or alter the existing assistance-event rules.

## Data contract

Every new inference result and video timeline row has `count_summary`; assigned bus sources expose the same field. Top-level fields include `window_ms`, `coverage_ms`, `coverage`, `support_threshold`, `stale_ms`, `uncertain_hold_ms`, `last_observed_ms`, `age_ms`, `status`, `total` and `classes`.

`total` and each normalized object name in `classes` contain:

| Field | Meaning |
| --- | --- |
| `raw` | Latest observed count, excluding predicted boxes; null when stale. |
| `stable` | Supported count, or a briefly held previous count when uncertain; null when unavailable. |
| `candidate` | Unique leading count in the voting window; null for a tie. |
| `support` | Leading count's share of observed time, from 0 to 1. It can refer to a different candidate than an uncertain held value. |
| `status` | `warming`, `stable`, `uncertain`, `stale` or `instant`. |
| `distribution` | Counts with their supporting `duration_ms` and `share`. |
| `held_age_ms` / `held_remaining_ms` | Bounds on retaining an uncertain previous estimate. |

Use `classes.person` for people. `total` includes every detected object type and is voted independently; independently voted class counts need not sum to the independently voted total. Consume status and freshness alongside any number. Treat uncertain held values as uncertain, and null as unavailable rather than zero.

For live sources, freshness includes server processing time since frame receipt; the workspace additionally accounts for capture-to-display age. Video freshness is relative to the playhead. These clocks cannot establish camera-side buffering that occurred before a frame reached the decoder.

Implementation: `vision/counting.py`, session finalization, RTSP publication and video timelines. Presentation: `static/counts.js`. Tests cover the exact example, unequal frame rates, zero counts, ties, gaps, stale polls, per-class independence, cancellation and playback seeking.

## Verification

The count unit tests (21), focused integration/RTSP/bridge tests (21), and frontend tests (36) pass. The bus bundle builds successfully.

A local check using YOLOE-26m on CUDA processed the bundled sample image repeatedly as 165 live observations. The final estimate was 4 visible people with 100% temporal support; stopping the observations cleared the count as stale. Bus Studio displayed both states correctly. This verifies the data path and freshness behavior, not real CCTV counting accuracy. The workspace also correctly displayed single-image counts of 4 people and 0 laptops. Desktop and narrow-screen camera cards were inspected without browser errors.

The local run's detailed results are saved in `validation/count-voting-smoke.json`; runtime validation data is excluded from the source archive.
