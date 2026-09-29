# Demonstrating BusGuard

This guide presents the current CCTV/RFID prototype. Bus motion, doors, ramp and mobility-position checks are simulated. Use [System workflow](SYSTEM_WORKFLOW.md) as the timing specification and [Setup and operations](SETUP_AND_OPERATIONS.md) for installation.

## A clear explanation for the audience

“Some passengers need more time, a ramp or clearer instructions. BusGuard combines two camera views, a passenger request page and RFID boarding records. One local controller coordinates the assistance and shows the same state on a passenger phone, an operator console and a 3D bus. We demonstrate the physical camera and card-reader inputs; the bus movement, ramp and doors are simulated.”

The contribution is the connected assistance workflow and its handling of missing information. Do not describe a successful text-prompt detection as a medically informed assessment, a certified safety system or proof that every passenger is seated.

## Prepare the demonstration

1. Start the server and keep the computer awake. Open the detection console on the host, the passenger page on a phone, and the bus viewer on a second device if available. Use current LAN addresses.
2. Pair the operator browser if required. Following a restart, review the simulation and select **Reset held simulation**. Clear only the demonstration conditions that are actually resolved; mobility confirmation is an explicit simulated check.
3. Connect the outside and inside sources. For live departure checks, use live inputs, not an image or recorded clip. Verify the inside view covers the whole cabin before enabling full-cabin count reconciliation.
4. Configure `person` in the inside object categories. Enable **Standing detection · YOLOE**. Compare seated and standing examples in the actual view, with the separate standing threshold. Do not rely on one successful positive example.
5. Choose one device to play bus announcements. Multiple audible pages on different devices can overlap even though each page queues its own audio correctly. The bus viewer has a mute control.
6. Verify that the four cards match the preconfigured UIDs, then assign their passenger profiles in the console. The console cannot register a new UID or change a UID mapping; replacing the demo cards currently requires a source/configuration change and a compatible saved registry. Power the RC522 from 3.3 V and use the documented ESP32 wiring. Remove a card between presentations.
7. For voice, open the trusted HTTPS passenger page, grant microphone permission and verify the selected voice. The text controls remain the primary fallback if internet access or voice service is unavailable.
8. Set a deliberate demo passenger location in the passenger app's Settings. The illustrated route is A–E, not a GPS map. Keep the selected assistance stop consistent with the scenario.

Record the input mode, camera placement, thresholds and network conditions before comparing results.

## Demonstration 1 — a quiet stop

**Purpose:** prove the bus does not wait for the full admission limit when nothing happens.

1. Start the stop/go scenario or command the next stop while travelling.
2. Observe the approach announcement, gradual braking and door animation.
3. Once doors are fully open, show the initial **15-second** quiet window. Keep the outside view free of fresh qualifying activity and do not tap/request assistance.
4. The door-closing sequence begins when the quiet window ends. The inside camera continues counting throughout; it does not extend boarding.
5. After doors are fully closed, show **five continuous seconds of fresh no-standing evidence**. The prior departure announcement and other controller checks must also be complete.
6. The bus starts travelling, with road/wheel motion. It remains in travel until the next Stop command.

If the bus remains held, show the exact reason. A disconnected camera, unconfirmed mobility condition or restart hold is different from a standing detection. Do not silently disable the check to make the demonstration pass.

## Demonstration 2 — priority passenger using RFID

**Purpose:** show that a declared card profile requests appropriate consideration without guessing age or pregnancy from appearance.

1. Assign a registered card the **Senior resident** profile in the console.
2. Open a stop. Present and remove the card once while admission is open.
3. Show its accepted boarding result, onboard status and immediate occupancy increase of one.
4. With bus audio enabled, listen to the priority-seat offer. Explain that the seat-group display is estimated; the system does not identify which rider should stand up or which exact seat they occupy.
5. Show that the tap protects ten seconds from its acceptance time, without shortening a later existing deadline.
6. At a later open stop, present the same card again. Show alighting and the count decrease of one.
7. Hold the card at the reader and demonstrate that it does not repeatedly toggle the count. Remove it before another valid presentation.

Also show that taps while travelling, during door closure, or after the admission cutoff are rejected. Use an unknown test UID to demonstrate rejection only when appropriate; never imply the UID is a secure personal identity.

## Demonstration 3 — ramp request on a passenger phone

**Purpose:** show an explicit request, shared countdown and coordinated simulated access.

1. In Settings, select the current stop as the passenger's demo location. Wait for the bus to arrive there and admission to open.
2. Select **Board the bus**, that stop and **Ramp access**. Select exactly one assistance option.
3. Submit and wait for the server's accepted status. Show the matching request in the console and the door/ramp animation in the 3D bus.
4. Explain that ramp access protects **20 seconds from acceptance**. The countdown is an allowance/door-closing indication, not a guaranteed departure time.
5. Use the offered completion button only when the demonstrated boarding action has finished. Completion changes the assistance request; it does not add a second passenger to the RFID/camera count.
6. Review and explicitly confirm the simulated mobility position when requested. The camera does not verify wheelchair securement.
7. Allow the normal stow/close, standing and departure checks to complete.

For assisted alighting, set the passenger location to **On the bus**, wait until the selected stop is reached, then request the appropriate single option. New future-stop assistance requests are not accepted; an arrival reminder can be set separately.

## Demonstration 4 — a late accepted allowance

**Purpose:** distinguish closing admission from finishing an already accepted request.

Use controlled outside activity to keep the stop open close to the cutoff. Watch the server's displayed admission time, not a separate phone stopwatch.

| Event | Expected result |
| --- | --- |
| RFID tap accepted at approximately 49 s | Admission closes at 50 s; the accepted ten-second allowance can finish at approximately 59 s |
| Ramp request accepted at approximately 49 s | Admission closes at 50 s; the accepted twenty-second allowance can finish at approximately 69 s |
| Another tap/request at or after the cutoff | Rejected; no new boarding record or extension |
| The last accepted allowance ends | Stow/close and departure checks proceed, subject to remaining holds |

Run the RFID and ramp examples as separate stop cycles. Network timing makes an exact last-millisecond boundary difficult to demonstrate manually; automated tests check those boundaries more precisely.

## Demonstration 5 — standing and missing evidence

**Purpose:** show the current standing-only gate and its limits.

1. During open doors, show ordinary person counts without standing classification or a seated-passenger departure reminder.
2. After doors close, have a visible participant stand. A fresh standing result must hold departure and reset the five-second confirmation.
3. Have the participant sit. Show the five-second confirmation rebuilding from fresh successful no-standing passes.
4. Disconnect or stop the inside source during confirmation. It must not turn into a zero-standing success.
5. Reconnect the source and demonstrate a new full confirmation period. Repeated old frames or the clock merely advancing cannot complete it.
6. During travel, show a standing observation triggering an announcement without abruptly stopping the simulated bus.

Explain the distinction: the detector checks for standing; it does not classify sitting, reconstruct MediaPipe joints or prove safety. A missed standing person remains a model error even if the five-second rule behaves correctly.

## Demonstration 6 — passenger voice and arrival alerts

1. Say **“Hey BusGuard, where is the bus?”** Show the brief spoken answer and caption based on current shared state.
2. Speak while the reply is playing. The reply should finish, and that overlapping speech should not become a later command.
3. Speak a follow-up after playback ends. After three seconds of silence, ordinary conversation should no longer trigger answers; use the wake phrase again.
4. Request a reminder for a named stop. It should not submit an assistance request or reserve a seat.
5. Enable arrival alerts and, on a supported device, arrival vibration in Settings. Command the relevant stop and show the popup and enabled spoken/haptic feedback.
6. Use voice to request one assistance option at an eligible stop or to change an accessibility preference. Show the actual server/app confirmation rather than treating the assistant's words as proof of success.
7. Use **Stop listening** to stop voice immediately. On a browser that blocks playback, **Enable reply audio** should unlock audio without opening the microphone mid-reply.

Vibration is not available in every browser, including common iPhone web setups. Do not promise background alerts while the page is suspended. The voice assistant needs internet access and configured OpenAI API access; local camera/RFID/text operation has different dependencies.

## Demonstration 7 — counting and capacity

Show both layers separately:

- A valid tap changes the onboard record immediately.
- The inside camera collects thirty fresh one-second samples; the displayed progress explains why correction is not immediate.
- After a complete cycle, the rounded mean corrects the baseline. A stopped camera keeps the last record rather than declaring the cabin empty.
- Full occupancy rejects new boarding; an onboard card can still alight during an eligible open window.
- Assistance requests may reserve availability while active, but do not substitute for confirmed passenger movement.

When using simulated-load controls, label that step clearly. Those controls are test setup, not evidence of camera accuracy, and they do not erase remembered per-card onboard status.

## What to measure

| Measure | What to record |
| --- | --- |
| Detection delay | Time from an action in the actual camera scene to a fresh displayed result; distinguish preview delay, inference time and controller response |
| Standing recognition | Labelled standing and non-standing examples, false positives, false negatives, thresholds, view/lighting and occlusion |
| Count stability | Raw vs voted count, 30-s correction mean, and disagreement with known staged occupancy |
| Event reliability | Accepted/rejected tap reasons, duplicate retries, door/window boundary behavior and restart recovery |
| Assistance usability | Request success, clear rejection reason, ability to understand the next step and finish with text-only controls |
| Voice behavior | Wake success, unintended activation, command success, reply length, uninterrupted playback and follow-up timing |
| Device behavior | Phone/browser, Wi-Fi conditions, certificate trust, microphone permission and supported arrival feedback |

Do not report only inference speed as end-to-end latency, a confidence score as accuracy, or a clean demo clip as general performance. Keep an honest list of what was exercised physically, what was automated and what remains simulated.

## Suggested next development work

1. Collect consented, representative cabin examples and evaluate standing detection on a held-out set before changing thresholds or fine-tuning.
2. Improve camera placement and calibration before increasing model size; full-cabin visibility matters for occupancy reconciliation.
3. Add measured end-to-end timing and traceable event logs using redacted identifiers, rather than relying on visual impressions.
4. Validate the complete ESP32-to-server path, reader re-arming and Wi-Fi recovery on the physical hardware.
5. Test the passenger experience with people who use the accessibility features; do not infer their needs from demographic labels alone.
6. Treat physical actuator integration as a separate engineering project with hardware feedback and appropriate independent safety controls. The current simulated checks are not that system.
