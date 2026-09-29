# Source publication checks — 29 September 2026

These checks were run against a separate source export of the current project, using synthetic example RFID card IDs. The running installation and its private configuration were not changed.

| Check | Result |
| --- | --- |
| Python test suite | 936 passed; two warnings. |
| JavaScript test suite | 307 passed. |
| Rebuild the Three.js bus viewer | Passed with the pinned npm dependencies. |
| Dependency installation for browser tests/build | `npm ci` completed from the existing package cache. |

The Python tests used the existing Windows Python environment. JavaScript dependencies were installed separately in the export. This is not a claim that a completely new computer installation, physical ESP32 upload, live OpenAI conversation, or real-camera accuracy benchmark was performed for this documentation update.

The first export run exposed a case mismatch in a synthetic RFID identifier; the exported constants were corrected and the complete suite passed. A video-job test timed out during the first cold run and passed on the complete rerun. The bus bundler required normal filesystem access outside the execution sandbox to resolve dependency paths; the build then passed.

Tests cover timing, request validation, observation freshness, standing confirmation, counting, RFID identity/idempotency, API authorization and browser/voice state handling. Model accuracy still needs labeled footage from the actual camera position. Hardware, microphone, mobile certificate trust and audio playback require a demonstration on the target devices.

Follow [setup and operations](SETUP_AND_OPERATIONS.md) to reproduce the commands and [the demonstration guide](DEMONSTRATION_GUIDE.md) for end-to-end scenarios.
