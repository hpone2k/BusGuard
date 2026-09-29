# BusTech connected assistance demonstration

> **Historical demonstration guide.** This describes an earlier manual/MediaPipe workflow. For the current YOLOE standing-only, RFID and timed-stop demonstration, use [Demonstration guide](DEMONSTRATION_GUIDE.md) and [System workflow](SYSTEM_WORKFLOW.md).

This prototype uses real CCTV and RFID inputs. Bus travel, doors, ramp and mobility-area confirmation are **simulated**. There is no actuator or vehicle-control driver. Describe it that way when demonstrating it.

## Open the three websites

Start `start-lan.cmd` on the host computer. All three websites use one controller and one detector process:

| Website | On the host computer | Purpose |
| --- | --- | --- |
| Detection and operations | <http://localhost:4479/> | CCTV, recognition, optional posture, RFID and demonstration controls |
| Bus viewer | <http://localhost:4480/> | 3D doors/ramp and the same shared passenger/assistance state |
| Passenger | <http://localhost:4481/> | Boarding/alighting requests and private progress |

On another device, use the passenger link shown under **Connected assistance** in the detection console. It contains the computer's LAN address; `localhost` on a phone means the phone itself. Devices must be on a network that permits connections to the host. The host must remain running, and Windows must allow the three ports on the intended private network. No cloud account is required.

Passenger requests need no operator code. A second device using the detection console must pair with the six-digit code displayed **on the host computer**. Camera previews, camera configuration, RFID injection and demonstration controls are unavailable to an unpaired remote device. Pairing codes change when the server restarts.

If the server has restarted after a previous run, the controller deliberately shows a held, unverified state. Review the demonstration and select **Reset held simulation**. Pending requests survive restart, but their old completion confirmations do not.

## Before showing the judges

1. Open the three sites, preferably passenger on a phone and viewer on a large screen.
2. Check the displayed stop. A new demonstration starts at **Campus**. Changing stops requires **Depart simulation**, then **Arrive at stop**. Arriving repeatedly at the same stop does not reset assistance.
3. Configure the two source cards as **Outside** and **Inside**. Use live CCTV for automatic assistance. Images and videos remain useful tests, but cannot trigger live assistance or clear the live departure check.
4. Use simple, relevant outside categories such as `person, wheelchair, stroller, walking aid`. A live, non-demo assistance observation must pass the existing bridge's confirmation and deduplication before creating a request. Recognition remains fallible; the passenger page and RFID provide alternative request paths.
5. For optional seating checks, use **Object categories** with `person` in the **Inside** source. Select **MediaPipe body joints · seated / standing**, then apply settings. It is off by default. See [MEDIAPIPE.md](MEDIAPIPE.md) for setup, camera placement and limitations.

The four flows below can be repeated without physical ramp or door hardware.

## Flow 1 — ramp-assisted boarding

1. In the passenger website select **Boarding the bus**, the current stop, **Ramp access**, and any other useful preferences such as **A little more time** or **Visual guidance**.
2. Select **Request assistance**. The passenger page should move from received/preparing to ready. The operations list and 3D viewer should show the same request and simulated door/ramp sequence.
3. Wait until the passenger page offers **I have boarded the bus**. Do not press it while access is preparing. It means the passenger has completed their action; it is not a door sensor.
4. Confirm boarding. The controller waits for the minimum dwell interval and every other active passenger at this stop before stowing the ramp and closing the doors. A timer never completes an unconfirmed request.
5. Review the simulated mobility area and select **Confirm mobility position in simulation** after the ramp sequence completes. This is an explicit demonstration confirmation, not camera proof of physical wheelchair securement.
6. When enabled, the posture gate separately waits for complete, stable, live seated evidence. Once all holds clear, **Depart simulation** becomes available.

For a camera-triggered version, present a visible wheelchair to the outside live source. A confirmed observation creates a sensor-origin request. After showing the simulated boarding, the operator selects **Confirm sensor-requested assistance complete**, because a camera observation has no private passenger browser to acknowledge it. The UI must not claim that detection alone proves boarding has finished.

Every ramp boarding **or alighting** invalidates the mobility-area confirmation. The internal request balance is a bounded count of completed requests, not a passenger count or a matched identity. One person requesting alighting can never silently clear another person's mobility-area hold.

## Flow 2 — extra time through RFID

For a USB reader that types a card code followed by Enter:

1. In the operations panel choose the configured **RFID assistance profile**.
2. Focus **RFID keyboard reader**, then tap a test card.
3. Confirm that an RFID-origin request appears. It requests more time, plus priority seating for the senior-concession profile.
4. Leave the request unconfirmed for longer than eight seconds. The doors should remain open. Eight seconds is the minimum extra-time interval, not a completion timer.
5. After narrating completion, select **Confirm sensor-requested assistance complete**. The doors may close once all requests at this stop are confirmed and there is no obstruction.

The selected reader profile supplies the preference. The prototype does not infer age from a card number, and the keyboard input discards the raw card code. Other reader types need an adapter that sends a unique event ID and a configured assistance profile to the existing endpoint:

```http
POST /api/bus/rfid
Content-Type: application/json

{"event_id":"reader-event-unique-001","passenger_type":"senior","source_name":"RFID reader"}
```

`passenger_type` is `senior` or `assistance`. A retry must reuse the same event ID, preventing duplicate requests. Run adapters on the host or provide operator pairing; the endpoint is not public to unpaired LAN clients. The software does not automatically speak serial, NFC, Wiegand or every reader vendor's protocol.

## Flow 3 — assisted alighting at another stop

1. On the passenger website, finish the previous request and select **Request more assistance**.
2. Select **Leaving the bus**, **Community hub**, and the desired assistance preferences.
3. The request remains queued while the simulated bus is elsewhere.
4. Once current-stop readiness is clear, select **Depart simulation**. Select **Community hub** in the operator stop list and then **Arrive at stop**.
5. Access opens at the selected stop. Confirm **I have left the bus** only when the simulated alighting demonstration is complete.
6. Show that the viewer follows the same stow/close sequence and that a new mobility-area review is required after ramp use.

A new request submitted to the previous stop while the bus is travelling waits for a later visit; it does not strand the bus between stops. Future-stop requests do not block leaving the current stop.

## Flow 4 — obstruction, emergency and uncertain posture

**Obstruction:** create a boarding request, then select **Simulate an obstructed doorway**. Confirm the passenger's action. Even after the minimum dwell interval, the controller keeps assistance held. Clear the obstruction and observe the coordinated closing sequence. The obstruction checkbox is simulated; it is not a calibrated obstruction detector.

**Emergency:** select **Emergency hold** during preparation or travel. Motion stops in the simulation and progress remains held. An explicit **Reset held simulation** is required. Reset invalidates old passenger confirmations and simulated mobility confirmation, so unfinished assistance must be confirmed again. Clear a reported obstruction before resetting.

**Posture uncertainty:** enable the inside MediaPipe checkbox with a live source. Show one seated and one standing person. Departure must stay held. If the standing person is obscured or disappears while the doors remain closed, departure still stays held: the controller remembers the known cabin roster. It does not accept the remaining seated subset as everyone.

To clear the optional posture gate, every accounted visible person needs a unique fresh track, matching pose and stable person count, followed by two seconds of successive seated observations **after** the simulated doors close. Stale frames, recorded inputs, repeated polls, ambiguous joints, missing people, mismatched counts and source changes cannot reuse an old confirmation. A source replacement starts fresh confirmation and preserves the minimum previously observed population. A proper reopened-door assistance cycle starts a new roster.

This is a conservative prototype. Occlusion, camera angle, lost tracking and the pose-capacity limit can hold the demonstration even when people are actually seated. An empty camera view does not prove an empty bus. The operator may explicitly deselect the optional posture checkbox and apply settings for a demonstration without that check; report that choice clearly rather than presenting disabled verification as a passed check.

## Audio, visual instructions and connectivity

Choosing **Audio guidance** records the requested preference. To speak on the passenger device, explicitly enable **Read updates aloud on this device**; the operator console has its own **Read bus announcements aloud** checkbox. Audio uses the browser's installed voices and remains accompanied by text. The passenger accessibility menu also supports larger text, high contrast and reduced motion.

The passenger browser privately retains the request ID and a random ownership token so a reload can reconnect to the same request. Tokens are sent in a request header, never a shared URL. If the network fails during submission, the UI shows that receipt is unconfirmed and retries the same request identity, instead of claiming success or silently making a duplicate. Public shared state omits the private request ID and token.

For repeatable evaluation, record the input method, requested assistance, whether the request was received, preparation time, completion time, false activations, missed requests and hold reason. Separate recorded-image tests from live-camera demonstrations and simulated door/ramp timing from any future physical-hardware timing.
