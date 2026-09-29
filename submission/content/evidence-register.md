# BusGuard submission evidence and verification register

Prepared 29 September 2026. This register supports the report writer and final staff review. It is not a claim that every requirement or physical test is complete.

## Source hierarchy

| ID | Source | Use and limitation |
| --- | --- | --- |
| O1 | `C:/Users/hpone/Downloads/BusTech/SGBTGC 2026 - IHL Updates 18 Aug 2026.pdf`; extracted copy `tmp/submission/SGBTGC 2026 - IHL Updates 18 Aug 2026.txt` | Official supplied briefing. Read pages 8–18 for task, scoring, dates and report requirements. Later organiser amendments have not been checked. |
| P1 | `publication/BusGuard-2026-09-29/` | Source export of the prototype; not the private running installation. Contains synthetic card UIDs. |
| P2 | `publication/BusGuard-2026-09-29-manifest.json` | 245 listed files; all checked against SHA-256 and matched during this task. Manifest hash `e35f65ac1913a7b67c3d81cf4bdcbdc648f67c73b8375183bfb108d1f3f391f0`. |
| V1 | `publication/BusGuard-2026-09-29/docs/VALIDATION_CURRENT.md` | Recorded 936 Python / 307 JavaScript passes and successful bus build. Existing Python environment; tests not rerun by this content-writing subtask. |
| C1 | Current `vision4479/docs/SYSTEM_WORKFLOW.md`, `ARCHITECTURE.md` and corresponding code | Actual current workflow cross-check. Legacy documents can contain superseded timers and posture methods. |
| R1 | `tmp/bustech-review/PROJECT_HANDOFF.md` and friend's `https://github.com/zerefdragneel4356-afk/BusTech` | Design influence: passenger requests, assistance/progress concepts. Do not transfer its MQTT, GPS/DataMall or physical integration claims to this implementation. |
| U1 | User's explicit hardware statement in the conversation | Current physical demo scope is CCTV and RFID, not a physical ramp or door. Team must verify actual hardware readiness before asserting completed rehearsals. |

## Official submission checklist

| Requirement | Brief evidence | Present status / final action |
| --- | --- | --- |
| English report; title page, project/institution/team/member/staff names | Page 18 | Content drafted; institution, team, members and staff must be confirmed. Do not infer names from logos. |
| Abstract; background/purpose; methods/results; proposed budget; discussion/conclusion; references | Page 18 | All sections provided. SGD 144 proposed cash allocation is provisional; equipment reuse, actual costs and funding remain unconfirmed. |
| 5–15 pages excluding title and reference pages | Page 18 | Final draft has 12 body pages, one title page and one reference page; counted from the exported 14-page PDF. |
| Arial 10; 1.5 spacing; 1-inch left/right margins | Page 18 | Applied in the DOCX and checked in the Word-exported PDF. All pages visually reviewed. |
| Entire report and supplementary figures/tables in one PDF | Page 18 | Do not submit a report plus separate technical appendix instead of the required single report file. |
| Team name in filename, following IHL name–team name | Page 18 | Blocked until identity metadata confirmed. |
| Staff-in-charge emails the report; one e-submission per team | Page 18 | User/staff final approval required before actual sending; no email has been sent by this task. |
| Report deadline 30 September 2026 | Pages 17–18 | Treat as supplied deadline; check for any subsequent organiser amendment with staff. |
| Poster artwork deadline 30 September 2026 | Page 17 | Editable official-template poster and reviewed PDF proofs prepared. Actual canvas preserved; A0 proof provisional pending organiser confirmation. |
| Pre-event 25-minute prototype showcase on 7 October 2026 | Pages 15,17 | Prepare timed rehearsal; schedule/venue marked tentative. |
| Slideshow/video deadline 15 October; main event 24 October 2026 | Pages 16–17 | Five-minute presentation + up-to-five-minute video + five-minute Q&A. Advancement/main-event participation subject to format; confirm schedule. |

The supplied brief lists report recipients as Colin_YEO@lta.gov.sg, Peter_LIM@lta.gov.sg and GOH_Siok_Luan@lta.gov.sg. This is extracted administrative information, not authorisation to send or proof of a later updated address list.

## Claims supported for the report

| Claim | Evidence | Appropriate wording |
| --- | --- | --- |
| One controller shared by three websites | P1 `run.py`, `vision/api.py`, `viewer.py`, `passenger_site.py` | Implemented local architecture; no independent viewer decision engine. |
| Outside only when stationary/full-open; inside counting continues | P1 `detection_gate.py`, `sessions.py`, `assistance.py` | Configured live inputs follow this policy; connection/model failure can interrupt observations. |
| 15-second quiet initial window | P1 `scenario.py`, `BASE_SECONDS = 15` | Timer starts at fully open doors. |
| 50-second new-admission cutoff, accepted RFID10/ramp20 finish beyond | P1 `scenario.py`, admission/extension logic and tests | Example tap49→59, ramp49→69; further new admission rejected. |
| Five continuous fresh negative standing seconds after closure | P1 `departure.py`, `standing.py`, `tests/test_five_second_departure_flow.py` | No standing detected, not verified sitting or safety. Other holds still apply. |
| Standing while moving warns rather than brakes | P1 `scenario.py`, moving-posture observer | Rate-limited reminder; not a vehicle emergency algorithm. |
| Thirty-second rounded mean corrects occupancy | P1 `cabin_audit.py`, `scenario.py` | Thirty fresh stable one-second buckets; failed cycles not zero. |
| RFID direction, retry protection and profiles | P1 `rfid.py`, `rfid_api.py`, firmware | Implemented for four configured UIDs; console edits profiles, not UID enrollment. |
| 26 seats,6priority20standard | P1 `scenario.py` capacity and model source | Simulation capacity; seat locations not recognised by cameras. |
| Current voice turn-taking | P1 `voice.py`, `static/passenger/voice.js` | Activated conversation, noninterruptible-by-speech reply, three-second follow-up; browser/service prerequisites apply. |
| Automated check counts | V1 | Recorded 936 Python/307 JS passes and build; not accuracy or physical hardware statistics. |
| Traceable source export | P2 hash validation | 245 manifest entries matched. Does not prove GitHub publication or successful fresh install. |

## Claims that must not be made without new evidence

- “X% accurate,” “no false detections,” or a measured standing/assistance-recognition rate: no labelled cabin evaluation supplied.
- “Improved passenger satisfaction by X%” or accessibility/time savings: no controlled user-study dataset supplied.
- “Physical ramp deployed automatically,” “door obstruction sensor works,” or “wheelchair restraint verified”: outputs/checks are simulated/operator-confirmed.
- “Fully autonomous Level 4 bus,” “certified safe,” or production-readiness: outside the prototype's scope.
- “Detects pregnancy, age or sensory impairment from appearance”: contrary to the intended explicit request/profile method.
- “Runs offline including voice”: voice uses OpenAI and Internet; standby recognition is not an offline wake-word model.
- “Runs at a guaranteed FPS/latency” based on unit tests or old isolated benchmarks: requires current camera-to-display measurement under intended load.
- “All four physical demonstrations passed” or “all phones vibrate”: rehearsals/device capabilities need observation and records.
- “Entirely original models/code”: upstream components, friend influence and AI-assisted work require attribution.
- “Already published to GitHub” based only on a source ZIP: verify repository URL, branch and commit independently.

## Evidence gaps and required inputs

| Priority | Missing item | Required owner evidence / action |
| --- | --- | --- |
| Before submission | Institution/team/student/staff metadata | Confirm exact official spelling, names and team roster. |
| Before submission | Budget/seed amount | Receipts/quotes, existing vs loan asset status, SGD totals, taxes, approved contingency and seed-money limit. All report costs currently TBC. |
| Before submission | Original contribution credits | Student-by-student contribution list; friend's author/revision/license and any copied/adapted files; AI-assistance disclosure consistent with institution/organiser rules. |
| Before submission | Mechanical scope acceptance | Discuss with staff whether simulated ramp/doors satisfy the required viable prototype/model; report current scope honestly either way. |
| Before submission | Final report compliance | Rendered PDF body-page count, font/spacing/margins, no missing table rows, title metadata and filenames. |
| Before submission | Organiser amendments | Staff verifies any later dates, template, recipient or submission instruction changes. |
| Before demonstration | Physical RFID end-to-end run | Firmware version/build, reader receipt, card directions/count changes, debounce/retry and disconnection records. |
| Before demonstration | Both cameras simultaneously | Source type, camera placement/resolution, measured fresh result ages, controlled standing positives/negatives. |
| Before demonstration | Phone/voice/alerts | Device/browser, certificate trust, permission, wake success, completed reply, follow-up and supported vibration. |
| For quantified results | Labelled evaluation | Ground-truth clips/consent, split, thresholds, confusion counts, count errors, latency distribution and exclusions. |
| For satisfaction claims | User evaluation | Protocol, recruitment/consent, completion/errors/time and actual responses to a defined instrument. |

## Rehearsal record template

Complete one row per attempted case, including failures. Preserve redacted logs/recordings locally and publish only with participant consent.

| Run/date | Case | Software snapshot | Devices/settings | Expected | Observed | Pass/fail | Evidence filename | Issue/follow-up |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TBC | Ramp boarding | TBC | TBC | Accepted 20 s; simulated ramp; explicit completion/review | Not yet recorded | Not assessed | TBC | TBC |
| TBC | Priority/late RFID | TBC | TBC | +1 once; priority speech; late 10 s; rejection after cutoff | Not yet recorded | Not assessed | TBC | TBC |
| TBC | Assisted alighting | TBC | TBC | Eligible request; same onboard card −1; progress | Not yet recorded | Not assessed | TBC | TBC |
| TBC | Standing/failure | TBC | TBC | Standing holds; fresh clear 5 s; stale/obstruction hold | Not yet recorded | Not assessed | TBC | TBC |

## Content-pack handling

`technical-report-content.md` contains substantive draft report text and bibliography. Title metadata, budget values and verified repository/attribution details intentionally remain visible placeholders. Do not label the result submission-ready while those remain. The main report must contain every table/image intended for judging within the single PDF; the evidence register is an internal checklist unless the team deliberately includes relevant portions within the page limit.
