# Standing detection and departure

Current standing-only behavior, 28 September 2026:

- After the doors are fully closed, departure requires **five continuous seconds of fresh live inside-camera observations with no standing detected**.
- The standing detector must successfully complete each observation. A failed, unavailable, stale or repeated result cannot advance confirmation. Standing detections reset confirmation.
- Person track IDs, missing past tracks and changes to the person roster do not determine whether a valid standing-detector result is clear. Person counting remains separate.
- `seating_summary.standing_check_valid` identifies a successful standing check; `clear` means that check detected no standing passengers. `complete` and `unknown` retain person-accounting information, rather than defining this standing-only gate.
- Camera changes, door reopening and interruptions require fresh confirmation. The check has no automatic timeout bypass. Existing emergency, doorway, capacity, assistance and mobility holds still apply.
- The server owns departure. The operator console, passenger page and 3D bus display its confirmation duration and remaining evidence time. The confirmation countdown never decreases merely because time passes in the browser.

Public `readiness.posture.confirmation_ms` (or `scenario.posture_wait.confirmation_ms`) supplies the displayed duration. The standing-only display defaults to 5,000 ms when an older snapshot lacks that field. Legacy seated-posture validation is a separate mode and retains its own duration.

The current 3D bus uses the shared server controller through `controller-state.js`; the older browser-only interlock and Scenario lab documentation describe a previous implementation.
