# BusGuard presentation and demonstration support

**Draft for team review.** Team/institution identity, presenter allocation and physical-trial evidence still need confirmation. Do not submit with unresolved TBCs.

Prepared for the Singapore BusTech Grand Challenge 2026 IHL Student Category. The August 18 briefing specifies a five-minute project presentation, an up-to-five-minute video and five minutes of judges' questions at the main event. It allocates a 25-minute prototype demonstration at the pre-event. Dates and arrangements remain subject to organiser updates.

## Five-minute speaker script

The eight-slide script contains about 725 words. Its timing targets total **5:00**, averaging 145 words per minute. Rehearse with the actual presenter and shorten spoken detail if needed. All quoted implementation behavior must remain true of the version demonstrated. Slides retain sources and the same script in editable speaker notes.

### Slide 1: BusGuard (0:00–0:25)

Good morning. Our project is BusGuard, an autonomous passenger assistance prototype for the Singapore BusTech Grand Challenge. It connects camera observations, RFID taps and a passenger website to one local controller. We will show physical camera and card-reader inputs alongside a software simulation of the bus, doors and ramp. Our focus is how these parts respond together when a passenger needs help.

### Slide 2: Assistance at the bus stop (0:25–1:00)

A passenger may need extra boarding time, step-free access, or information in a form they can use. In an autonomous bus, that need still requires a clear response. Our design gives passengers an explicit way to ask and see what happens next. We use camera observations for visible objects and standing checks. We use a request or configured card profile for needs such as priority seating. We do not guess pregnancy or age from appearance. The aim is a more understandable assistance process, whose benefit still needs evaluation with users.

### Slide 3: One shared assistance controller (1:00–1:40)

The outside camera observes boarding activity while the doors are open. The inside camera continues counting throughout the journey and checks standing after door closure. The ESP32 sends RFID card events over the local network. Passengers can also send a request from their phone. One server combines these inputs and owns the timing and assistance state. The detection console, passenger app and 3D bus all show that same state. The viewer can run on another device, so it does not need to share the detection computer's graphics workload. The vehicle outputs here are simulated.

### Slide 4: Boarding time follows activity (1:40–2:25)

The operator's Stop button brings the simulated bus to the next station. Once the doors are fully open, a quiet stop has a fifteen-second window. Fresh outside activity can keep it open. An accepted RFID tap protects ten seconds from the tap, and a ramp request protects twenty seconds from acceptance. At fifty seconds, the controller closes new admissions. However, it honours an allowance accepted before that cutoff. A tap at forty-nine seconds can therefore finish at fifty-nine. These are deadlines from accepted events, not ten seconds stacked onto every retry. The system gives a departure warning and then closes access when the conditions allow.

### Slide 5: Standing checks and passenger counts (2:25–3:10)

Standing confirmation and occupancy correction serve different purposes. After the doors close, the standing gate requires five continuous seconds of fresh successful checks with no standing person detected. A standing result or missing evidence resets that confirmation. Other holds still apply. During travel, standing produces a reminder rather than an abrupt stop. Occupancy changes immediately when an accepted card tap boards or alights someone. The inside camera then cross-checks that baseline using the rounded mean of thirty one-second samples. The camera does not add a second count on top of RFID. Neither zero standing detections nor a stable mean proves perfect perception.

### Slide 6: A passenger chooses one request (3:10–3:50)

The passenger app puts assistance commands first. A passenger chooses one option and receives a shared status and countdown. New assistance requests require an eligible stop and location in the demonstration. Voice offers another route to those same controls: Hey BusGuard activates it, and the assistant checks current state before answering. Replies are brief and finish before listening resumes. Arrival alerts can show a message, speak it, and vibrate when the device supports that feature. We retain text controls because internet access, microphone permissions and browser support can differ between passengers' devices.

### Slide 7: Engineering contribution (3:50–4:25)

Our contribution is the connected workflow around the models. One state keeps the three websites consistent. Event handling separates fresh evidence from old frames and repeat card deliveries. The controller keeps accepted allowances and admission cutoffs explicit. It also displays why departure is held, so a missing camera is not confused with a standing passenger. YOLOE provides detection and OpenAI provides optional voice capabilities. We credit those components and identify our work as the integration, controller logic and passenger experience, rather than claiming a new foundation model.

### Slide 8: Evidence and next validation (4:25–5:00)

The implemented software provides a connected controller and repeatable cases for timing, ownership, retries and recovery. The next evidence we need is a labelled cabin evaluation of standing errors, a physical reader and Wi-Fi recovery trial, and usability testing with people who use these accessibility features. We have not established real-bus accuracy or measured passenger satisfaction. Doors, ramp and motion remain simulated. The video and demonstration will show the current prototype, including a hold and recovery case, so you can judge both the intended response and the limits of the evidence. Thank you.

## Video storyboard and shot list

Target length: **4 minutes 45 seconds**. The official limit is **up to five minutes**. This is a filming plan, not a completed video or a record of completed physical trials. Record the real prototype only after consent and hardware checks. Use captions throughout. Label the 3D vehicle view **Software simulation: bus, doors and ramp** whenever it appears.

| Time | Shot | Action and evidence | Suggested spoken line / caption |
| --- | --- | --- | --- |
| 0:00–0:20 | Wide view of the demonstration setup, then close-ups of both CCTVs and ESP32/RC522 | Identify the actual input hardware. Do not show Wi-Fi credentials, reader tokens or private camera URLs. | “BusGuard connects two cameras, an RFID reader and a passenger website. Vehicle movement and access hardware are simulated.” |
| 0:20–0:45 | Screen capture of console, passenger phone and 3D bus | Show that the websites follow one shared state. Label the schematic route as a demonstration route. | “One local controller keeps requests, counts and the bus display consistent.” |
| 0:45–1:20 | Continuous screen recording of a quiet stop | Press Stop, show approach/opening, the 15-second quiet interval, closing and five fresh clear seconds. Use the server countdown. Keep the outside view free of qualifying activity. | “With no new activity, the doors start closing after fifteen seconds. The standing check starts after closure.” |
| 1:20–1:55 | Reader close-up paired with accepted event and seat ledger | Present and remove one configured priority card. Show one accepted boarding event and polite seat announcement. Show the same card alighting at an eligible open stop. | “A configured card profile requests priority consideration. An accepted tap changes occupancy once.” |
| 1:55–2:40 | Passenger app request, then bus display | At the current stop, request only Ramp access. Show acceptance, the 20-second allowance and simulated ramp response. Complete boarding and explicitly perform the simulated mobility review. | “The passenger receives one clear request status and countdown. The ramp response here is a software simulation.” |
| 2:40–3:25 | Consented participant and console result, then bus display | With doors closed, show a real standing detection holding departure. Show sitting followed by five fresh negative standing checks. If the model misses or misclassifies, report that and re-stage honestly. | “Standing or missing evidence resets confirmation. Five clear seconds can release this gate, subject to the other holds.” |
| 3:25–3:55 | Console close-up during source disconnection and recovery | Stop/disconnect the inside source during confirmation. Show a missing-evidence hold, reconnect and rebuild confirmation. | “A missing camera does not count as zero standing.” |
| 3:55–4:20 | Passenger phone with captions | Wake voice with Hey BusGuard, ask for bus location or one eligible request, and demonstrate an arrival popup. Show vibration only on a device that actually supports it. | “Text and voice share the same app actions. Visual arrival information remains available.” |
| 4:20–4:45 | Clean 3D preview, then concise evidence/next-work text | State current scope, unmeasured accuracy and planned user evaluation. End with project/team identity after confirmation. | “Our next steps are labelled cabin evaluation, physical recovery trials and accessibility-user testing.” |

Filming rules:

- Capture the physical reader and actual controller acknowledgement together when claiming an RFID demonstration. A button named Play tap is a simulation and must be labelled as such.
- Keep a visible elapsed-time reference for timing claims. If cutting out waiting time, use an explicit “time omitted” caption. Do not imply an edited sequence proves an exact 49-second boundary.
- Show at least one failure/hold and recovery, with the reason readable. Do not disable the standing gate between cuts.
- Use a quiet location and one audible announcement page. Record narration separately if necessary, but do not substitute narration for missing functional audio evidence.
- Obtain participants' consent. Use staged project scenes rather than unrelated passengers or private CCTV recordings. Avoid claiming a participant represents an impairment they do not have.
- Keep original clips and a short test log. Export a broadly playable MP4 and verify the actual organiser's required video format before submission. Do not assume a codec, resolution or file-size requirement absent from the briefing.
- Keep the exported video under 5:00, including opening and closing cards. The 4:45 target leaves a 15-second margin.

## Twenty-five-minute pre-event demonstration

The official briefing allocates **25 minutes per team** on the pre-event day. This plan totals 25 minutes and includes a short discussion buffer. Confirm the reporting schedule and venue details with the staff-in-charge.

| Elapsed time | Segment | Operator / presenter actions | Evidence to point out |
| --- | --- | --- | --- |
| 0:00–2:00 | Scope and hardware | Identify the cameras, ESP32/RC522, local server, phone and separate display. State which outputs are simulated. | Physical inputs and software boundaries. |
| 2:00–5:00 | Shared workflow | Show both source roles, one authoritative state, card profiles and passenger location setting. | Outside inference only with fully open doors, inside counting active throughout. |
| 5:00–8:00 | Quiet stop and standing | Run a quiet stop. After closure, demonstrate standing hold, then five continuous clear seconds. | No automatic wait to the full 50-second cutoff, and no stale-frame release. |
| 8:00–12:00 | Physical RFID | Present/remove a card, show +1, priority guidance, rejected repeated presentation and later −1. | Physical-to-server acknowledgement and exactly one event effect. |
| 12:00–16:00 | Ramp request and alighting | Submit one ramp option from the phone at an eligible stop. Show 20-second allowance and shared response. Demonstrate completion/mobility review and explain the alighting choice. | Assistance request lifecycle, clear next step and simulated access. |
| 16:00–19:00 | Admission cutoff | Keep the stop active. Accept a late RFID tap before 50 seconds, then try a new tap after cutoff. | Earlier allowance finishes, new event rejected, count unchanged by rejection. Use the server time. |
| 19:00–21:00 | Missing evidence / recovery | Disconnect inside input or demonstrate a doorway hold. Show the reason, then restore it. | Failure does not silently authorize departure. |
| 21:00–23:00 | Voice and arrival feedback | Use trusted HTTPS, wake voice, ask a brief status question and show arrival feedback. Use text controls if cloud voice is unavailable. | App control follows the same eligibility checks; device support is visible. |
| 23:00–25:00 | Questions and limitations | Explain count averaging, show the test/evidence log if asked and identify remaining validation. | Honest separation of implemented logic, software tests, hardware trial evidence and future work. |

Prepare before the timed session:

1. Keep the host powered and awake. Start a single server and verify all needed local-network sites from the actual demonstration devices.
2. Reconnect cameras after a restart. Review saved card presence, occupancy and requests before resetting the held simulation.
3. Confirm the inside view genuinely covers the test passenger area. Set and record thresholds, then test visible standing and non-standing examples from that view.
4. Assign card profiles deliberately and test the physical reader once. Never display its token or private `config.h` on the projector.
5. Prepare a clean baseline for each case. A simulated load is acceptable for a labelled capacity case, but does not prove physical boarding or camera accuracy.
6. Select one audible output device. Verify microphone permission, trust and voice connection in advance if using cloud voice.
7. Keep a short local backup video and PDF for presentation continuity. If a live case fails, explain the failure and show the labelled recording as a recording.

Stop or switch demonstrations if a setup issue would consume the remaining time. Do not turn an unverified condition into a success claim to keep the schedule.

## Judges' questions and prepared answers

**1. What is original in your project?**

The contribution is the shared assistance workflow: role-specific sensing, fresh-evidence standing confirmation, accepted-allowance timing, per-card event handling and a passenger interface that shows the same state as the bus display. YOLOE, OpenAI, Three.js and other dependencies are third-party components, which we credit. We do not claim to have created those models.

**2. Which parts physically work?**

The intended physical inputs are the two CCTVs and ESP32/RC522 reader. The repository implements their integration paths. We will only claim successful physical operation for the trials we actually perform and log. The 3D bus, doors, ramp, route movement and seat assignments are simulated.

**3. The brief includes automated mechanical systems. Does the simulation satisfy that requirement?**

The current scope demonstrates control logic and a visible software response, without a physical ramp or door actuator. That is a gap against a literal physical-mechanical interpretation. We will confirm acceptable demonstration scope with the organiser through our staff-in-charge. We will not describe simulated actuation as a completed mechanical prototype or imply the organiser has granted a waiver.

**4. How accurate is standing detection?**

We do not yet have a validated real-bus accuracy figure. The active detector uses a pretrained YOLOE standing prompt. We need labelled standing and non-standing cabin examples, including occlusion and lighting changes, to measure false positives, false negatives and response delay. Temporal confirmation reduces switching but cannot fix a consistently wrong classification.

**5. Does “no standing detected” prove that everyone is seated?**

No. It means the detector produced a valid negative standing result. The controller requires fresh results for five continuous seconds after closure and keeps other holds separate. A missed person remains a perception error. This prototype is not a certified departure safety system.

**6. Why use five seconds and thirty seconds?**

They serve different functions. Five seconds is the current continuous standing-clear confirmation interval. Thirty seconds forms an equally weighted cabin-count cross-check from one sample per second. These are project operating choices, not empirically proven universal optima. We should evaluate the delay/stability trade-off with representative scenes.

**7. Why combine RFID and the camera count?**

Accepted RFID taps update boarding/alighting immediately. A complete fresh camera cycle can correct drift using its rounded mean. We do not add both totals together. The mean is a trailing estimate and can lag a sudden change during boarding. Seat-group allocation remains estimated because we do not map every person to a physical seat.

**8. What happens if someone scans twice or Wi-Fi retries a tap?**

The firmware requires card removal before another presentation. Each event has an identity, and retries reuse that same event. The server validates freshness, the current admission window and duplicate identity before changing the ledger. A second distinct accepted presentation toggles that card's onboard state, so correct presentation behavior still matters.

**9. What happens at the fifty-second limit?**

New admission events close at fifty seconds from fully open doors. An allowance accepted before the cutoff can finish afterward. For example, an RFID tap at forty-nine seconds can finish its ten seconds at fifty-nine. It does not allow another new tap after cutoff. Door closure, standing evidence and other checks follow, so fifty seconds is not a forced-motion deadline.

**10. How do you detect seniors, pregnancy or hidden needs?**

We do not infer those from appearance. A passenger can request an option or use a deliberately configured card profile. The project uses visible-object detection for relevant observable inputs and explicit requests for assistance that cannot be reliably inferred from the scene.

**11. What happens if the camera or server fails?**

Missing/stale inside evidence cannot advance the standing confirmation. A restart restores the journal but requires operator revalidation. The UI exposes the hold or unavailable state. If the voice cloud is unavailable, text controls remain available while the local server runs. These behaviors are software fail-held rules, not a complete vehicle safety architecture.

**12. Is the system fully offline?**

The local camera, RFID, shared controller and text websites can operate locally after setup. The optional voice assistant and uncached AI announcements require internet access and OpenAI API access. That dependency is visible and does not replace the local text workflow.

**13. Is the wake word private or processed locally?**

The current wake activation uses cloud transcription while listening. Standby ignores ordinary conversation for assistant responses, but does not mean the microphone is off. Stop listening disables the microphone. We disclose that difference, keep the API key on the server and avoid putting keys into the phone app.

**14. Does voice directly move the bus?**

No. Voice can read status and use permitted passenger app actions, including one eligible assistance request. The controller still applies the same location, admission and capacity rules. It does not provide a voice tool for bypassing holds or driving actuators.

**15. How do you know this improves passenger satisfaction?**

That is a target, not an established result. We need structured tasks and feedback from people who use accessibility features, with consent and a clear baseline. Useful measures include task success, time to request help, understanding the next step and perceived confidence using the system. We will report the sample and limitations with any results.

**16. What would be required before use on a real bus?**

Bus-specific perception evaluation, accessible user testing, robust hardware feedback, independently engineered safety controls, vehicle/actuator interfaces, operational procedures and relevant regulatory/industry review. The current model/website prototype cannot replace those steps.

**17. How much does it cost?**

The budget must use the team's actual purchase records and approved seed-funding rules. Camera, reader, compute/display and optional API costs should be itemized. No final budget or live API cost is asserted in this deck until the team confirms the amounts.

**18. Why a separate 3D display?**

It makes the shared bus response visible to judges and passengers. Running it on another device moves rendering work away from the inference computer. Its animation is a visualization of controller state, not physical actuation evidence.

## Last-mile submission checklist

### Identity and official requirements

- [ ] Confirm institution, team name, member names, staff-in-charge and presenter roles. Replace the visible TBC on the title slide. Verify the spelling against enrolment records.
- [ ] Obtain permission to use the supplied institution logo and select the correct original asset. Its presence in a folder alone does not establish team membership.
- [ ] Confirm the organiser's latest dates, venue/reporting schedule and any amendments. The August update marks event details tentative and subject to change.
- [ ] Confirm whether the current software simulation meets the required mechanical prototype scope. Do not invent acceptance or approval.
- [ ] Confirm report/poster submission on **30 September 2026**, the **7 October** pre-event, slideshow/video submission on **15 October**, and the **24 October** main event against the latest staff communication.

### Slide and script readiness

- [ ] Replace provisional identity information and remove every unintended TBC.
- [ ] Rehearse the 725-word script to five minutes with the actual presenter. Shorten spoken detail if transitions or pronunciation push it over time.
- [ ] Keep the physical-input/simulated-output statement on the slides and in the spoken introduction.
- [ ] Do not replace unmeasured accuracy, hardware success, user feedback or budget figures with invented numbers.
- [ ] Credit third-party models, libraries, existing designs and contributions. Keep source notes with the editable deck.
- [ ] Open the PPTX in the presentation software used on event day and check fonts, notes, aspect ratio and display scaling. The supplied PDF is a view copy, not an editable slide source.
- [ ] Confirm the projector can read the smallest text from the back of the room. Keep an offline PDF copy.

### Video and live demonstration

- [ ] Obtain consent for every identifiable person/voice recorded. Check camera content before screen recording.
- [ ] Remove credentials, private addresses where unnecessary, tokens, student IDs and unrelated browser content from recordings.
- [ ] Capture actual device-to-server acknowledgements for physical-integration claims. Label simulated button actions and software previews clearly.
- [ ] Keep the final video under five minutes with captions, intelligible audio and readable controller states.
- [ ] Verify the exported file on the event laptop without relying on a streaming link or internet connection.
- [ ] Run the 25-minute plan once end to end and record failures honestly. Keep a prepared fallback recording without representing it as a live result.
- [ ] Verify Wi-Fi/IP, firewall, phone HTTPS trust, saved RFID state, card removal/re-arm and one audible output device on the actual equipment.

### Evidence and final handoff

- [ ] Maintain a dated trial log with input conditions, expected result, actual result and failures. Save labelled standing examples separately from promotional footage.
- [ ] Separate automated software tests from model accuracy and physical hardware evidence in every submitted document.
- [ ] Ask the staff-in-charge to review technical claims and submission identity details.
- [ ] Keep a final versioned PPTX, PDF, video and source assets. Submit only the confirmed deliverables through the authorised channel.
- [ ] Treat organiser receipt/acknowledgement as a separate step. These prepared files have not been emailed or officially submitted by this workflow.

## Source and scope notes

- Official source: `SGBTGC 2026 - IHL Updates 18 Aug 2026.pdf`, especially pages 8–13 (challenge), 15–16 (session format and judging) and 17–18 (submission timeline/report requirements).
- Project sources: `vision4479/docs/SYSTEM_WORKFLOW.md`, `ARCHITECTURE.md`, `DEMONSTRATION_GUIDE.md`, `STANDING_DEPARTURE.md`, `VOICE_SETUP.md`, current controller/detector/API source and `firmware/BusGuardRFID/README.md`.
- Software-preview screenshots, where included, show an isolated fresh demo environment. They contain no CCTV recordings and do not establish sensor accuracy, physical RFID operation or passenger satisfaction.
- No real-bus accuracy percentage, user-study result, physical actuator test or organiser scope approval is asserted in these materials.
- The PPTX contains editable text and an editable architecture diagram. The PDF is a rendered view copy for reliable reading.
