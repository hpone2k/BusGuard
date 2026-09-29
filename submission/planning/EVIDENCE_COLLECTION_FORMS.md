# BusGuard demonstration evidence forms

**Blank recording sheets. No measurement results are supplied.** Copy the relevant table for each run. Use a short clip/screenshot filename as evidence, and keep unsuccessful runs. Report the actual number of trials; do not turn software test counts into camera accuracy or passenger-study results.

For a short rehearsal, prioritise: standing versus seated, camera disconnect, one complete 30-second count cycle, RFID board/alight, one late RFID tap, one ramp request, and one phone voice/arrival check. Repeat the standing and timing cases if time permits. A small demonstration sample cannot establish real-bus safety.

## 1. Run identification

| Field | Record |
| --- | --- |
| Date, time and tester | |
| Project version / commit | |
| Camera input, position, resolution and frame rate | |
| Model/checkpoint, standing prompt and confidence threshold | |
| Laptop / GPU, phone model / browser, Wi-Fi arrangement | |
| Lighting, distance, occlusion and number of participants | |
| Standing check enabled; whole-cabin coverage enabled | |
| Evidence folder / file prefix | |

Use participant codes, such as P01. Do not put API keys, Wi-Fi passwords, raw card UIDs or identifiable passenger details in the submission logs.

## 2. Standing detection accuracy and error log

**Question being measured:** does the inside camera detect that at least one person is standing after the doors are fully closed? The current system does not classify sitting.

Fix the camera position and threshold for a batch. At each planned sample, compare a human observer's ground truth with a **fresh** standing result from the same moment. Use one sample per second, or a fixed set of labelled frames; record which method you used. Do not count the same frozen result repeatedly as new evidence. Include all-seated, standing, partially obscured and mixed groups.

| Outcome | Ground truth | Fresh detector result |
| --- | --- | --- |
| TP: true positive | At least one standing person | Standing detected |
| FP: false positive | No standing person | Standing detected |
| FN: false negative | At least one standing person | No standing detected |
| TN: true negative | No standing person | No standing detected |
| Unscorable | Ground truth unclear, feed stale/disconnected, or result not time-aligned | Record separately; exclude from the four accuracy counts |

| Sample ID / clip time | Doors fully closed? | Ground-truth standing count | Fresh? / result age | Detected standing count | TP / FP / FN / TN / unscorable | Occlusion / failure reason | Evidence |
| --- | --- | ---: | --- | ---: | --- | --- | --- |
| | | | | | | | |
| | | | | | | | |
| | | | | | | | |
| | | | | | | | |

| Summary | Measured value |
| --- | --- |
| Planned samples / scored samples / unscorable samples | |
| TP / FP / FN / TN | |
| Precision = TP / (TP + FP) | |
| Recall = TP / (TP + FN) | |
| False-positive rate = FP / (FP + TN) | |
| Miss rate = FN / (TP + FN) | |
| Conditions not yet tested | |

Report a zero denominator as **N/A**, not 0% or 100%. Preserve the raw counts beside percentages. Consecutive video samples are correlated; describe these as results from this camera setup, not independent people or general model performance. No standing detected is not proof that everyone is seated.

## 3. Departure and simultaneous-camera checks

Use the simulated bus. Standing confirmation begins only when doors are fully closed. With the feature enabled, departure requires five continuous seconds of fresh no-standing results plus the other controller conditions. The ten-second announcement lead and other holds can make total departure time longer than five seconds.

| Case | Expected behavior to check | Actual behavior / timing | Pass / fail / blocked | Evidence |
| --- | --- | --- | --- | --- |
| Doors open; inside person moves | Inside counts continue; standing check and seated reminder do not run | | | |
| Doors fully close; nobody standing | Fresh no-standing confirmation lasts 5 s; departure proceeds when other conditions clear | | | |
| Standing before departure | Departure stays held; confirm the standing result is fresh | | | |
| Person sits during the hold | New continuous 5 s confirmation begins after the last standing result | | | |
| Inside feed disconnects or source changes | Confirmation resets; missing evidence does not clear departure | | | |
| Standing while bus moves | Reminder is issued; no abrupt simulated stop | | | |
| Both cameras active with doors open | Both previews update; inside counts and outside activity remain responsive | | | |
| Doors close while both sources connected | Outside inference pauses; previews do not flash; inside counting/checking continues | | | |
| Inside activity alone after doors open | Does not renew the outside/RFID quiet boarding window | | | |

## 4. Latency measurements

Use one recording clock for both endpoints: record a physical change and its visible result in the same phone video, or use timestamps already aligned to one server clock. Record the method. Do not subtract unsynchronised phone and laptop clock times. Separate camera display delay, detection delay, controller delay and voice delay.

| Trial | What is timed | Start event and timestamp | End event and timestamp | Difference (ms) | Clock / recording method | Evidence |
| --- | --- | --- | --- | ---: | --- | --- |
| | Physical movement to camera preview | | | | | |
| | Physical standing to standing label | | | | | |
| | Accepted RFID tap to updated occupancy | | | | | |
| | Accepted app request to acknowledgement/countdown | | | | | |
| | End of spoken request to first audible reply | | | | | |

| Timing category | Trials | Median (ms) | Minimum / maximum (ms) | Conditions / excluded trials and reason |
| --- | ---: | ---: | --- | --- |
| | | | | |

## 5. Thirty-second occupancy check

Use one full cycle of **30 fresh one-second person samples**. For each sample, record the inside-camera count used by the controller and a manual reference count. A missing sample stays blank; it is not zero. Note boarding/alighting during the cycle, because the result represents a time average rather than the final instant's occupancy.

| Second | Fresh camera count | Manual count | Freshness / change note | Second | Fresh camera count | Manual count | Freshness / change note |
| ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |
| 1 | | | | 16 | | | |
| 2 | | | | 17 | | | |
| 3 | | | | 18 | | | |
| 4 | | | | 19 | | | |
| 5 | | | | 20 | | | |
| 6 | | | | 21 | | | |
| 7 | | | | 22 | | | |
| 8 | | | | 23 | | | |
| 9 | | | | 24 | | | |
| 10 | | | | 25 | | | |
| 11 | | | | 26 | | | |
| 12 | | | | 27 | | | |
| 13 | | | | 28 | | | |
| 14 | | | | 29 | | | |
| 15 | | | | 30 | | | |

| Cycle result | Record |
| --- | --- |
| Valid samples out of 30 | |
| Sum of 30 camera counts / 30 = camera mean | |
| Rounded camera mean / controller's corrected count | |
| Mean manual count / manual count at cycle end | |
| Absolute difference: camera mean versus manual mean | |
| Occupancy before / after correction | |
| Priority / standard availability shown after correction | |
| Incomplete-cycle behavior / evidence | |

Capacity is 26: six priority and 20 standard seats. Availability is estimated from occupancy and profiles; this is not proof of which physical seats are occupied.

## 6. RFID and allowance log

Use the demo card labels, not raw UIDs. First accepted tap boards; the next accepted tap for that card alights. Rejected taps should not change occupancy. Keep camera corrections separate from RFID changes.

| Event time from doors fully open | Card / profile or app option | Expected accept / reject | Before count | Actual accept / reject and reason | After count | Displayed closing deadline / allowance | Announcement / evidence |
| --- | --- | --- | ---: | --- | ---: | --- | --- |
| | Card boarding | | | | | | |
| | Same card alighting | | | | | | |
| | Duplicate / held card | | | | | | |
| | Senior or pregnant profile | | | | | | |
| | Tap before 50 s cutoff | | | | | | |
| | Tap at / after 50 s cutoff | | | | | | |
| | Ramp request before cutoff | | | | | | |
| | Tap with doors closed | | | | | | |

Check: an accepted tap grants 10 seconds from that tap; a ramp request grants 20 seconds from the request. Existing later allowances are preserved. Admission closes at 50 seconds, but an allowance accepted before the cutoff finishes in full. A 49-second tap can therefore run to 59 seconds; a 49-second ramp request can run to 69 seconds. Record actual timing rather than assuming the example passed.

## 7. Phone voice and arrival accessibility checklist

Phone / operating system: ______  Browser/version: ______  Date: ______  Tester: ______

Mark each item **pass, fail, unsupported, or not tested**, and record evidence. Test the actual demo device; Android and iPhone web capabilities differ.

| Check | Result | Observation / evidence |
| --- | --- | --- |
| Trusted HTTPS page loads; microphone permission can be granted | | |
| Enable listening, then ordinary conversation causes no assistant reply in standby | | |
| “Hey BusGuard” activates reliably in the planned demo conditions | | |
| One supported assistance option can be requested by voice | | |
| Voice action obeys selected-stop, capacity and admission checks | | |
| Assistant gives a brief, understandable reply | | |
| Speech during a reply does not interrupt it or queue an unintended command | | |
| Listening resumes after complete playback; a follow-up can be spoken | | |
| About 3 s of follow-up silence returns to standby; standby command works | | |
| Stop listening control immediately ends the session | | |
| Voice can set an arrival reminder / supported app preference | | |
| Requested-stop arrival produces the correct visible notification while page stays open | | |
| Spoken arrival update is clear; announcement audio does not overlap another announcement | | |
| Vibration setting on produces a vibration on a supported device | | |
| Vibration setting off suppresses vibration; unsupported devices remain usable visually | | |
| Large text / high contrast / reduced motion controls work | | |
| App can be used by touch without voice; requests/countdowns remain readable | | |
| Screen-reader labels and focus order are usable with the selected phone's screen reader | | |
| Behaviour after switching apps / locking phone is recorded, not assumed reliable | | |

Do not promise iPhone web vibration or background reminders unless demonstrated on the actual device. A teammate's usability rehearsal is not a study with disabled passengers; identify who participated accurately.

## 8. Fixes and report-ready evidence

| Finding ID | Observed problem | Impact | Fix / workaround | Retest result and evidence | Report wording supported by this evidence |
| --- | --- | --- | --- | --- | --- |
| | | | | | |
| | | | | | |

Before copying a result into the report, include the sample size, conditions, failures and limitations. Leave unmeasured results marked **not measured**.
