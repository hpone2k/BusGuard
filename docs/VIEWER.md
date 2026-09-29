# Run the bus display on another device

Keep the server and detection console on the computer connected to the CCTVs and
RFID. Start with `start-lan.cmd`. On a second laptop, tablet or phone connected to
the same Wi-Fi/LAN, open `http://<server-LAN-IP>:4480/` in a WebGL-capable browser.
No installation, separate model weights or operator pairing is needed on that
display device. Keep the server awake. `localhost` on a second device refers to
that device, so use the server's LAN address instead.

The viewer downloads its local JavaScript/model geometry and receives current
bus state from the shared server. Three.js renders on the **display device's
GPU**. Detection inference continues on the **server's GPU**. This is a browser
viewer, not a remote video/screen-sharing session.

Close any 3D bus tabs on the detection computer to free their graphics resources.
Alternatively, use the pause icon beside Rotate to stop local 3D work while
keeping the passenger message panel updated; use Resume to inspect the model
again. Pausing freezes only this display, never the shared controller, passenger
requests or the other device's viewer. A paused canvas is not a live door/ramp
position display.

The host's LAN IP can change when Wi-Fi changes. Use the address on the detection
console's **Connected assistance** section, replacing the passenger port with
`4480`. If a client cannot connect, follow the firewall setup in the main README;
guest Wi-Fi client isolation can also prevent devices from reaching one another.

## Rendering changes

- Stationary scenes stop scheduling frames. View changes, orbit/zoom, selection
  and changed controller actuator targets wake rendering again.
- Shadows are cached during camera movement and rebuilt when the vehicle's
  cutaway, doors or ramp change. The detailed bus geometry remains intact.
- A maximum 1.5 device pixel ratio and two-million-pixel drawing budget keep
  high-resolution displays from multiplying rendering cost without a limit.
- Flat, double-sided signs render in one pass. Glass retains its original
  front/back rendering for correct transparency.
- Hidden tabs suspend rendering and state polling. Returning to the page resumes
  data retrieval and freshness checks. Pausing only 3D keeps polling active.
- The message panel has an independent freshness clock. Offline/expired
  controller state freezes actuator targets as before.

The canvas host exposes `data-rendered-frames`, `data-shadow-updates` and
`data-render-state` for development checks. In a settled, unchanged view the
frame/shadow counts remain fixed. During orbit, frames rise while shadow count
stays fixed. These counters do not measure GPU time or promise a frame rate on
untested devices.

## Travel presentation

The bus remains framed while its wheels roll over textured charcoal asphalt.
Contrasting lane dashes, directional arrows, road studs and pavement joints move
past, with avenue trees, lights and delineators beyond the carriageway providing
a second motion cue. Repeated objects use instanced geometry and a small generated
texture; their movement does not require fresh shadow maps or geometry uploads.
Departure accelerates smoothly; a normal commanded
stop follows the server's two-second braking phase before opening doors. No
client animation decides when boarding or departure is allowed. Emergency,
obstruction, stale controller state and open doors stop the road immediately.
Reduced-motion preferences disable travel animation but retain live status.

Use the speaker button to enable announcements on the viewing device. Browser
audio needs this first interaction. Travel renders continuously while visible;
stationary scenes return to idle, with cached shadows during wheel/road motion.
The additional `data-travel-speed` and `data-travel-distance` counters expose
visual movement for development checks, not physical speed or distance.

## Customize the On board panel

Drag the panel header to move it, or use the corner handle to resize it. The
header's minus and expand buttons minimize or maximize the panel; select them
again to restore it. Live journey information, seats and messages remain available
when minimized. Reset returns the panel to its default position and size.

The panel stays within the viewport and above the bottom view controls. Position,
size and display mode are remembered on this browser. With the header or resize
handle focused, arrow keys move or resize in ten-pixel steps; hold Shift for
one-pixel adjustments. Home resets the layout and Escape cancels an active drag.
