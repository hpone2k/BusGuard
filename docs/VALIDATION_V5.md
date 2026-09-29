# Version 5 validation — 22 September 2026

## Automated checks

- Python: **278 passed**, with two upstream test-client deprecation warnings.
- JavaScript: **99 passed**.
- `pip check`: no broken requirements.
- 3D bundle rebuilt with esbuild.

Coverage includes ownership and idempotent passenger retries, bounded requests,
same-origin checks, LAN operator pairing/expiry/rate limiting, proxy allowlists,
unwritable/corrupt journals, restart holds, RFID deduplication, multi-request
completion, mobility-area review, and departure permissions.

The new backend posture interlock retains expected occupants through missing or
stale frames, rejects reordered frames and changing capture clocks, requires a
matching stable person count, and accepts only fresh post-closure observations.
Still-image joints remain visible after slow inference; test inputs never clear
the live gate. Camera source switches invalidate previous evidence.

## Browser and running-server checks

- Passenger boarding with ramp, extra time and visual guidance; reload restored
  the same private request; completion appeared after shared closing/stowing.
- Passenger alighting, queued future-stop cancellation, and empty-choice errors.
- Mobile layout at 390 px, larger text and high contrast; no horizontal overflow.
- Inside source checkbox and real MediaPipe skeleton overlay on the bundled image.
- 3D viewer renders the bus and shows the shared simulated state and announcements.
- All three services returned HTTP 200 through the Wi-Fi LAN address.
- Windows Firewall rule verified: only the server executable, ports 4479–4481,
  physical-interface addresses and LocalSubnet peers.

The host is running at ports 4479 (operator), 4480 (bus) and 4481 (passenger).
LAN addresses can change with the network; the console lists current addresses
at launch. The firewall helper should be rerun after changing networks.

## Limits of these checks

No physical camera feed or RFID scan was available for this final validation.
Live bus camera angles, occlusion, lighting and RFID adapter settings still need
hardware acceptance testing. No physical doors, ramp or vehicle were connected.
Audio controls were checked; sound output was not measured.

The MediaPipe sample benchmark measured about 72 ms warm added pose processing,
with two standing and two unknown people. This is neither an accuracy score nor
a guarantee for a crowded cabin. Posture is optional and bounded to six poses;
uncertain or capacity-limited observations remain held when enabled.

An independent phone connection was not available to test. LAN binding, firewall
configuration and requests addressed to the host's LAN IP were verified locally;
guest Wi-Fi isolation and other network equipment remain outside these checks.
