# ESP32 RFID plan — recorded 25 September 2026

> **Historical implementation plan.** ESP32 + RC522 support is now implemented. Use the [firmware guide](../firmware/BusGuardRFID/README.md), [API reference](API_REFERENCE.md), and [current workflow](SYSTEM_WORKFLOW.md).

User-confirmed future hardware: an ESP32 with an RFID scanner. Implementation is
deferred until the user supplies the reader and connection details.

- On a card's first accepted boarding tap, register a stable passenger ID and
  add one onboard passenger.
- The next distinct accepted tap by that card marks that passenger as alighted
  and subtracts one. Subsequent taps alternate boarding and alighting.
- Duplicate radio reads, held cards and network retries must count once; use a
  debounced scan event ID distinct from the persistent card/passenger identity.
- Refused boarding (full bus, closed doors or admission closed) must not toggle
  the card's onboard state. Alighting must not produce a negative count.
- Every completed 30-second camera cycle computes the arithmetic mean of one count per second and rounds half up. This corrects the displayed count; later accepted scans change it immediately. Never add camera and card totals together. App assistance completion is not a count event.
- Keep card identifiers out of public passenger/3D endpoints. Select persistence,
  transport and pairing once the actual hardware interface is confirmed.

The existing RFID scenario buttons remain demo inputs. They do not yet implement
this per-card toggle behavior. The current inside CCTV has been confirmed by the
user to cover the whole cabin; camera counts are still detection estimates.

The future adapter must match a boarding card to its passenger request before
claiming that request's reserved seat. Until identity matching exists, the demo
operator completes the assistance request to release its reservation, then plays
the boarding RFID tap. An unidentified tap must not consume another passenger's
reservation, including when the last seat is reserved.
