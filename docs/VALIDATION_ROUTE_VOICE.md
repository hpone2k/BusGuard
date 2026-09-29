# Route, entrance LED and voice validation — 24 September 2026

The current behavior is documented in [STOP_SCENARIO.md](STOP_SCENARIO.md).
The recorded live trace is `validation/route-voice-live.json`.

## Live server and browser checks

- Restarted the real hybrid detector with its existing local models. YOLOE,
  Grounding DINO and MediaPipe Full report ready; CCTV inputs were disconnected.
- All three HTTP websites respond on ports 4479–4481. The additional HTTPS
  passenger site on 4482 validates against the generated local CA, including
  hostname/IP verification for 192.168.1.59. No trust store was modified.
- Stop advanced A → B → C → D → E. A named approach message appeared before
  braking finished. At E, the next stop correctly became A.
- Passenger location B received one visible arrival popup at B. Boarding there
  displayed a ten-second private allowance. Completion reduced standard seats
  from 20 to 19. Future-stop alighting was disabled; alighting at D succeeded and
  restored standard seats to 20. No active test request remains.
- The bus viewer displayed the physical entrance LED, shared seat availability,
  door animation and planned departure timer. The passenger route marker and
  status followed the same controller. Browser error logs were empty.
- The measured quiet stop at E began closing 10.094 seconds after doors were
  observed fully open. Movement followed the warning by 10.109 seconds, after
  a 3.078-second closed-door interval. No activity extended it to fifty seconds.

## Automated coverage

Final full suites: **446 Python tests and 167 JavaScript tests passed**. The two
Python warnings are existing test-client deprecations, not failed checks.

Controller tests cover ten-second renewals at seconds 9/18/27/36/45/49, the hard
fifty-second cap, last-second request expiry, idempotent retries, route wrapping,
server-side location checks and restart holds. Posture-policy tests hold standing
and unknown results until ten seconds after closure, prevent camera reconnect
from extending that deadline, and preserve emergency/obstruction/mobility/capacity
holds. The timeout remains explicitly unverified.

Frontend tests cover authoritative timer aging, stale-state suppression, matching
arrival deduplication, seat presentation and LED messages. Voice tests use mocked
cloud responses to cover private-key isolation, bounded session ownership/expiry,
restricted speech generation, approved arrival messages, 202 cache preparation,
immediate device fallback, wake activation, private-data redaction and truthful
tool results. The 3D production bundle builds successfully.

## Limits of this verification

No OpenAI key was configured and no paid cloud request was made. Realtime speech,
Marin/Cedar listening quality and microphone permissions require live testing
after private setup. TLS was verified using an explicit local CA for the test
connection; this does not mean a phone already trusts that certificate.

Phone vibration, mobile notifications and actual phone microphone behavior were
not tested on hardware. Desktop browser checks confirm the visual alert flow.
iPhone Safari needs visual/spoken fallback for vibration. Foreground web alerts
do not promise locked-screen delivery.

These changes do not constitute a new CCTV posture-accuracy evaluation. Actual
camera position, occlusion and visibility still determine joint quality. Vehicle
motion, doors, ramp, route position and the posture timeout are simulations.
