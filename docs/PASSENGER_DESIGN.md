# Passenger interface

A compact charcoal route strip sits above the assistance commands. The command
panel includes a static bus image, assistance choices and the request controls.
Live seat counts provide supporting information below. This order is preserved
on phones.

## Shared visual language

- Forest ink `#173f32`, primary action `#245b43`, muted text `#4d6753`.
- Light green canvas `#dcebd0`, panel `#edf5e6`, border `#b8ceb0`.
- Rounded panels, restrained shadows, native system typography and consistent
  focus outlines. Primary actions and secondary buttons have 44px or larger
  targets. Detailed explanations use native disclosure controls.

## Components and behavior

- Assistance choices are native checkboxes; boarding/alighting is a radio group.
  Selected, disabled, focused and hovered choices have distinct styling.
- The top-right Settings button opens voice, arrival alerts, demo location and
  accessibility controls. Voice selection, preview and privacy information are
  expandable. When voice is connecting or listening, a header Stop button stays
  available even after Settings closes. Closing Settings does not end a session.
- A compact voice status strip exposes microphone permission, reconnection and
  HTTPS problems on the main screen. Start listening is available there; voice
  resumes automatically only with the saved listening preference and permission.
  Stop remains an explicit opt-out. Public announcements and assistant replies
  share one audio queue and finish before the next clip plays.
- A one-time arrival reminder watches a selected stop independently of demo
  location. It is visible on the route strip and can be cancelled there. Setting
  a reminder does not send an assistance request, reserve a seat or extend a stop.
- The A–E line represents stop order, not GPS or geographic distance. Its marker
  uses fresh server progress. E-to-A travel has explicit return text and hides
  the marker instead of moving it through stops D, C and B. A text list provides
  equivalent route information for screen readers.
- The bus illustration is a static SVG image. The route marker updates to the
  current server position without interpolated movement, wheel rotation or
  decorative animation. Status remains available as text.
- Countdown, seat availability, request eligibility, location and arrival alerts
  continue to use the shared controller. The page does not create its own trip
  timers or passenger counts.

## Verification

Checked at desktop and phone viewport widths. Verified that feature controls are
inside Settings, the alert preview closes Settings so it remains accessible,
the bus image loads and no SVG animation runs. Passenger, route, voice activity
and passenger asset-serving tests pass. Live microphone and cloud audio are
separate from these presentation checks.
