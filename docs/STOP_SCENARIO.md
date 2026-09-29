# Stop / go and RFID demonstration

> **Historical timing notes.** Earlier timers, clipped extensions and posture descriptions below are superseded by [System workflow](SYSTEM_WORKFLOW.md). Current rules use a 15-second initial window, a 50-second admission cutoff with accepted allowances finishing in full, and five seconds of fresh standing-only clearance.

For the current five-second, standing-only departure rule, see [Standing detection and departure](STANDING_DEPARTURE.md). It supersedes the seated-posture and person-roster requirements described below.

Open the detection console on port **4479**, then **Stop / go scenario** near the
top. The passenger website (4481) and 3D bus (4480) follow this single controller.
Only a local or paired operator can start, stop, seed or play RFID events.

## Timing

1. **Start stop/go scenario** opens the simulated doors at the current stop.
   **Stop at next stop** visits A → B → C → D → E → A, in that order; only the
   operator's Stop button starts a new arrival. The route never stops itself.
   The bus announces the approaching stop by name before it finishes braking.
   It then announces arrival and opens the simulated
   doors. The initial boarding window lasts **10 seconds from the moment the
   doors finish opening**. With no activity, closure starts then; the bus does
   not wait for the 50-second maximum. A moving bus first brakes for 2 seconds,
   keeping its doors closed, then opens them over 0.8 seconds.
2. While the live **outside boarding CCTV** continues detecting an object or passenger, each fresh
   positive observation renews the 10-second quiet window, capped at 50 seconds
   from the doors opening. Repeated observations without a new capture timestamp,
   old observations, predicted-only boxes, uploaded images/videos and demonstration
   inputs do not renew this window.
   Detection does not imply that someone boarded or reserve a seat. Inside CCTV
   observations do not extend boarding; they still inform occupancy and posture
   checks.
   A confirmed boarding/alighting event or accepted RFID tap guarantees up to
   10 seconds from that event. It does not stack another 10 seconds onto every tap
   at the same instant. An app request grants up to 10 seconds from acceptance,
   once; retries and confirmation do not add another allowance. The passenger
   app shows that request's remaining time. New app requests are accepted only
   at the current stop while the doors and admission window are open. Boarding
   requires the passenger's demo location to match that stop; alighting requires
   the demo location to be **On board**. Requests cannot queue for a future stop.
3. No event can extend admission past **50 seconds from doors fully open**.
   A spoken and visual warning is issued **10 seconds before planned movement**,
   including time for ramp stowing, door closure and the three-second closed-door
   check. While detections renew the window, the displays say **Detection
   continuing**. Once the final departure warning starts, its countdown is shown.
   Fresh activity can postpone departure; it never makes the bus leave earlier
   than the announced warning permits. Safety holds can delay movement further.
4. Each unconfirmed request expires at its own allowance deadline, even if a
   different passenger keeps the stop open. Late requests receive only the time
   left before the 50-second cap. At the stop deadline, new boarding is refused,
   all remaining current-stop requests expire, and reserved seats are released. Ramp stowing and door
   closure follow. An expired request never counts as a completed journey.
5. Inside person counting stays active throughout boarding, braking and travel.
   MediaPipe seated/standing inference begins only when the doors are fully
   closed. When enabled, standing, uncertain, missing or stale posture holds
   departure without a timeout. Every accounted-for passenger must be clearly
   seated for **three continuous seconds of fresh camera observations**. Any
   interruption resets confirmation; repeated polls cannot advance it.
   The remaining departure warning and other checks must also complete.
   While moving, fresh standing posture produces a polite seated reminder; it
   never commands an abrupt stop. Boarding announcements do not ask people to
   sit while the doors are opening, open or closing.

The 50-second limit applies to boarding extensions. Door/ramp animation,
seated confirmation and any remaining departure-notice time follow it.
Emergency, doorway obstruction, mobility-area review and over-capacity checks
can hold the bus longer. No posture timeout bypass exists.

### Quiet demonstration

With the optional posture check off, a quiet stop still closes after the
10-second open-door window, completes the three-second post-close interval and
remaining warning, then travels until the next Stop click. It does not wait for
50 seconds. This mode does not claim camera verification of seated passengers.
With posture checking enabled, missing camera data holds departure even in a
nominally empty demo. Fresh zero-person evidence must satisfy the explicit empty
cabin checks; known occupants or an incomplete view cannot be silently ignored.

The server owns every transition. Passenger countdowns interpolate a fresh
received remaining duration for smooth display; they never extend an allowance
or declare a request complete. After two seconds without fresh request data the
countdown becomes unavailable, and completion waits for reconnection. Only outside detection pauses during braking and travel. The inside camera remains active; doors and boarding remain disabled while braking.

## Seats and RFID

There are **6 priority and 20 standard seats**. General passengers use a standard
seat first, then an unused priority seat. A passenger requesting priority uses a
priority seat where available. If a general passenger occupies one and a standard
seat is free, the simulation reassigns that passenger and announces a polite
request to offer the priority seat and move to standard seating.

Admission uses the remaining capacity after the current passenger count and app
reservations. Boarding scans add one; alighting scans subtract one. The existing
scenario controls demonstrate these directions until the ESP32 card reader is
connected. Duplicate scan event IDs count once. Full-bus boarding is refused.
An app request reserves a seat; completion, cancellation or expiry releases that
reservation. Completing assistance is not another passenger scan and does not
add or subtract occupancy. The physical per-card toggle adapter is still deferred.

### Thirty-second camera cross-check

Inside detection continues throughout the journey. With full-cabin coverage
confirmed, the controller collects one fresh stable literal-person count per
second for thirty consecutive seconds. Each second contributes equally, using
its latest accepted observation; a faster frame rate has no extra weight.
After the full window closes:

`camera mean = (count_1 + count_2 + ... + count_30) / 30`

The mean is rounded to the nearest whole person (a half rounds up). That count
corrects the displayed passenger baseline, even if it is lower than the scan
record. Subsequent scans add/subtract immediately from the corrected baseline.
The camera estimate and RFID count are never added together. Scans accepted after
a window's end remain counted even if processing of that window finishes late.
The next thirty-second cycle begins with new evidence. The mean is a trailing
estimate, so quick changes during boarding can take a cycle to be reflected.

An incomplete window, disconnected camera, missing second, stale frame,
recording/image or phrase subset cannot make a correction. Missing evidence is
not zero. A camera replacement restarts collection. The previous completed
correction remains part of the count record during an outage, with the camera
check labelled unavailable. Old instantaneous camera counts from earlier software
are not valid thirty-second corrections and cannot create phantom passengers.
Explicitly loading a new simulated population clears the correction and starts
fresh sampling; a routine safety reset preserves the count record.

The operator sees recorded passengers, cycle progress, last mean, rounded result
and the discrepancy at the last check. Priority/standard distribution is estimated;
no camera-to-seat mapping or passenger identity matching is claimed. Corrected
counts above26 keep seats at zero and hold departure. Fresh live over-capacity
observations also retain their independent safety hold before an average finishes.

MediaPipe posture does not wait thirty seconds: its independent, closed-door
seating check still needs three continuous seconds of fresh evidence, and a
standing or missing person keeps a stationary bus held.

## Repeatable cases

- **Empty stop:** with optional posture off, load 0 priority / 0 standard, start, and do nothing. Show door
  closure after the 10-second window, then the 3-second wait and automatic travel. With posture enabled, connect a live full-cabin camera.
- **RFID boarding:** start, wait for the doors to open, choose a passenger
  preference and press **Play RFID tap**. The seat ledger updates and the activity
  grace is bounded. A connected keyboard RFID reader uses the same capacity gate.
  For a passenger with a reserved app request, complete their assistance to release
  the reservation before playing their tap. Linking a specific card to its own
  reservation belongs to the deferred ESP32 identity adapter; an unidentified tap
  cannot consume somebody else's reserved seat.
- **Priority offer:** before starting, open **Set up simulated passengers** and
  load 6 priority / 19 standard / 1 general passenger in priority. Start and play
  a priority/senior RFID tap. The announcement requests the seat change; simulated
  occupancy becomes 26, with no general passenger remaining in a priority seat.
- **Full bus:** load 6 priority / 20 standard. Start and play RFID. Admission is
  refused and the polite full-bus message is spoken/shown. Alight one passenger
  to make space before trying another unique tap during the open window.
- **Misuse and holds:** repeated app requests cannot push the boarding deadline
  beyond 50 seconds. A tap/request at 49 seconds receives only one second. At 50
  seconds new actions are refused and closure begins. Doorway/emergency/mobility
  holds remain; standing or uncertain posture holds departure without a timeout until fresh seating is confirmed for three seconds.

## Shared route and demo location

`/api/assistance/config` supplies all five stop names and normalized schematic
coordinates. `/api/assistance/state` supplies the authoritative current/next
stop, route phase, segment progress and one arrival event ID per visit. The map
is a **demonstration route**, not GPS. While moving, progress reaches 85% after
30 seconds and waits for the Stop command; braking completes the segment smoothly.
Repeated polls do not produce new arrival events. On restart, the journal retains
the current stop but requires operator revalidation before motion can resume.

The location supplied with a passenger request is an explicit demo setting:
`{"mode":"at_stop","stop_id":"campus"}`, `{"mode":"onboard"}`, or
`{"mode":"away"}`. This is not a verified geofence. The server enforces location
and stop eligibility, not just the form. Retrying an already-created request
returns the same result even after departure, including saved requests from the
older app that did not contain a location. Manual assistance mode remains available
for same-stop stationary requests with the correct location.

Scenario controls enable speech on that browser unless the operator explicitly
turns it off. Use **Read bus announcements aloud** to mute/unmute. Browser voice
availability varies; the same message always remains visible. A second display
device can keep the 3D site open while detection runs on the host.

For live seated/standing checks, enable **MediaPipe body joints · seated /
standing** on the inside `person` source. With that optional check off, the demo
does not claim camera confirmation that everyone is seated. All vehicle movement
and doors/ramp remain simulated; CCTV and RFID are the physical inputs.

The scenario, occupancy and event deduplication journal survives restart, but
automatic travel never resumes by itself. Use **Reset held simulation** after
revalidation. This is also an explicit way to restart a held scenario cycle.
