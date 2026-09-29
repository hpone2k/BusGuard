# BusGuard AI voice setup

The passenger assistant uses OpenAI Realtime over WebRTC. The server creates the call using its private API key; the phone receives only the WebRTC answer and an opaque local session handle. Eight browser tools read current bus status, request assistance, confirm completion, cancel a request, set/cancel an arrival reminder, change supported app preferences and show a passenger panel. Assistance actions use the same request/location/capacity checks as the normal buttons. A reminder can target a future stop independently of assistance eligibility; it does not reserve a seat or extend the stop. No bus actuator tools are provided, and voice cannot change the passenger's demo location to bypass request eligibility.

Marin is the default voice, with Cedar available for comparison. These are AI-generated voices; the app discloses that rather than presenting the assistant as a human announcer.

## Configure the key privately

For a fresh clone, first create the local Python environment using
[Setup and operations](SETUP_AND_OPERATIONS.md), then run the private setup helper below.
If `data/voice.env` already exists on your configured host, open it locally, paste the key immediately after
`OPENAI_API_KEY=`, and save with Ctrl+S. Keep `BUSGUARD_VOICE=marin` for the default
voice. Never publish this populated file or add the key to HTML or JavaScript. The passenger page checks
for newly saved configuration every five seconds while it is visible. A first
visit requires Enable voice and browser microphone permission. Once the passenger
has granted permission and left listening enabled, a visible page can resume that
preference automatically. The standard key never reaches the browser.

For a fresh copy of the project, use the private setup helper below to create a
protected configuration file.

On the host computer, from the project root, run in an interactive terminal:

You can also double-click `setup-private-voice.cmd` to open the same hidden-key prompt.

```powershell
.\.venv\Scripts\python.exe scripts\setup_voice.py
```

Enter the OpenAI API key at the hidden prompt and choose `marin` or `cedar`. Do not paste the key into chat, HTML, source code, a URL, or command arguments. The helper restricts file permissions before writing `data/voice.env`; the file and generated speech cache are excluded from the source archive. The running server re-reads this file, so reload the page after setup; no restart is needed for the key. `OPENAI_API_KEY` and `BUSGUARD_VOICE` environment variables take precedence when set on the server.

OpenAI API billing and access are separate from a ChatGPT subscription. No paid request is made by reading the configuration. The first explicit voice connection or announcement request uses the configured API account.

Start `start-lan-voice.cmd` from your normal Windows desktop account. A server
started under `CodexSandboxOffline` cannot reach OpenAI even with a valid key.
The setup helpers resolve the desktop profile when running under a Codex sandbox
account, so Notepad retains access to `voice.env`; they never grant access to
Everyone or the Users group.

## Phone microphone access needs trusted HTTPS

On the phone, open `http://<SERVER_IP>:4481/setup` while connected to the same
Wi-Fi, replacing `<SERVER_IP>` with the host computer's current LAN address.
The setup page provides the fixed public certificate download, its SHA-256
fingerprint, Android/iPhone instructions, and the secure passenger link. Compare
the fingerprint against `/setup` opened on the host computer before choosing to
trust the certificate. The page never installs trust automatically or serves the
private key. No API key is entered on the phone.

Localhost can use the microphone on the host computer, but `http://192.168.x.x` is not a secure context on an Android phone or iPhone. Browsing through a certificate warning is not a substitute for a trusted certificate. The normal text interface continues to work over HTTP.

Use a certificate for the exact LAN address or hostname that the phone opens, signed by a CA that the phone trusts. To create an optional temporary demo certificate with PowerShell 7:

```powershell
pwsh -File scripts\setup_passenger_https.ps1 -IPAddress <SERVER_IP>
```

This creates `data/tls/passenger-cert.pem`, `data/tls/passenger-key.pem`, and the public `data/tls/BusGuard-demo-root.cer`. It does not install a trusted root, change firewall rules, or start any service. The private CA key exists only while generating the certificate and is then discarded. Existing files are never overwritten. If the host IP changes or the 30-day server certificate expires, issue a replacement deliberately and update the phone's trust.

Transfer only the public `.cer` file to your own demo phone. Compare the SHA-256 fingerprint printed by the helper before trusting it. Never transfer `passenger-key.pem`.

- **iPhone:** install the public certificate profile in Settings, then explicitly enable its full trust under General → About → Certificate Trust Settings. Open the HTTPS passenger address in Safari and grant microphone access when you turn on voice. [Apple certificate trust instructions](https://support.apple.com/en-ie/102390).
- **Android:** install the public certificate as a CA certificate in the phone's credential/security settings, then open the HTTPS passenger address in Chrome and grant microphone access. Exact settings names vary by device and version; managed phones may restrict this. [Google credential settings reference](https://support.google.com/pixelphone/answer/2844832?hl=en).
- Remove the temporary demo CA from each phone after the demonstration. No code in this project bypasses certificate validation or installs trust automatically.

Then restart the server with the additional passenger HTTPS listener:

Once certificate files are present, `start-lan-voice.cmd` launches these same options.
Stop an existing server before running a launcher; a second copy cannot share its ports.

```powershell
.\.venv\Scripts\python.exe run.py --lan --passenger-tls-cert data\tls\passenger-cert.pem --passenger-tls-key data\tls\passenger-key.pem
```

The passenger voice page is `https://<SERVER_IP>:4482/`; the HTTP passenger page remains on 4481. The detector stays on 4479 and the 3D viewer on 4480. If Windows Firewall blocks a port, use the narrowly scoped setup instructions in [Setup and operations](SETUP_AND_OPERATIONS.md). Operator routes require authorization, but public passenger/viewer routes remain intended for a trusted local demonstration network, not public internet hosting.

If you already have a trusted certificate and matching unencrypted PEM key, use their paths instead. `--passenger-https-port` can select another unused port. Uvicorn verifies the supplied files before any listeners start; no HTTPS listener starts without both files.

## Voice behavior and limits

Tap Enable voice once to grant permission. During listening, microphone audio is sent to OpenAI for transcription, including speech before the wake phrase. The microphone pauses while the assistant prepares and speaks its reply. “Hey BusGuard” is a transcript-based activation, not an offline wake-word model. Use the on-screen talk control if the wake phrase is missed. With permission already granted, the listening preference resumes automatically while the app is visible. Stop listening turns that preference off. Switching apps or locking the phone pauses listening; keep the page visible for voice and arrival alerts. Browser permissions or interrupted connections may require another tap.

Say **“Hey BusGuard”** to start a conversation. Bounded spelling variants from
transcription are accepted, but “Hello BusGuard”, “Hi BusGuard” and a mention in
the middle of unrelated speech do not activate it. A wake-only utterance shows
“I'm listening” without an unnecessary spoken reply. After three seconds without
conversation, the assistant returns to standby and ignores ordinary conversation.
The timer waits while an accepted utterance is being transcribed, an action is
pending, or the assistant is finishing its reply. Say **“standby”** or **“go to
sleep”** while listening to end the active conversation immediately. **Talk now** remains a
deliberate manual alternative. Standby does not disable wake-phrase transcription
or the microphone; **Stop listening** does. English transcription hints include
the assistant name and the five stop names. Accuracy still depends on microphone
distance, noise, accent and the upstream transcription service.

The assistant finishes its reply before listening again. Microphone transmission
pauses while a response is being prepared, its actions are being confirmed, and
its audio is playing. Speech during that interval cannot interrupt the reply or
queue a new request, including “Hey BusGuard” and “standby”. Late transcripts from
the paused interval are discarded. Listening resumes after WebRTC reports that
playback has finished, not merely when text/audio generation is complete.
The three-second follow-up window starts after the complete audible reply.
**Talk now** waits until the current reply is finished. If the browser blocks audio,
**Enable reply audio** resumes playback without cancelling the reply or opening the
microphone. **Stop listening** can still stop voice immediately. Microphone transmission also
pauses for queued bus announcements; session renewal waits for active speech to finish.

Replies default to one short sentence of about 20 words, with a second short
sentence only for an essential next step (normally no more than 40 words).
Longer explanations are reserved for explicit requests for detail. Tool results
are summarized rather than read in full. The output-token budget retains enough
headroom to finish sentences and tool arguments instead of cutting speech short.
Announcements and assistant replies share one audio queue on each page; they do
not play over each other. Keep only one audible bus-announcement page per device
when demonstrating, because different browser pages have separate audio queues.

Supported examples:

- “Hey BusGuard, request extra time to board at Stop A.”
- “Hey BusGuard, remind me when the bus reaches Stop C.”
- “Hey BusGuard, turn on arrival vibration.”
- “Hey BusGuard, make the text larger.”
- “Hey BusGuard, show settings.”

App settings available by voice are spoken updates, arrival alerts, arrival
vibration, larger text, high contrast and reduced motion. The settings panel has a
separate **Vibrate on arrival** switch, saved on this browser, and **Test alert**.
Requested reminders respect that vibration preference. iPhone Safari does not
offer website vibration; use the visible popup and optional spoken alert instead.
Android vibration also depends on browser/device permissions and user interaction.
The interface reports unsupported or rejected vibration rather than claiming it
happened. No lock-screen or background delivery is promised.

The server allows one active session per client address, up to three across the demo, with a 55-minute timer that attempts to hang up each cloud call. The browser renews enabled listening with a new bounded session and closes its microphone and peer connection when listening stops or the page is hidden. Network failure can prevent the server hangup request from reaching OpenAI; these are demo limits, not an account-wide billing cap. Continued listening and automatic renewal can incur API charges. Configure API project limits separately if needed. Session creation and speech generation are rate-limited; standard keys are never returned to clients or echoed in errors.

Bus announcements use a bounded disk cache keyed by text, voice, model and delivery instructions. A cache miss returns HTTP 202 immediately and prepares the clip in the background. The browser waits up to 15 seconds for an uncached clip and coordinates announcement playback with the assistant. It does not fall back to the device's synthetic voice. Visual messages remain immediate; cloud preparation does not change the controller's departure timer. A failed generation returns a clear error and blocks repeat attempts for 30 seconds. Only current/recent controller announcements and a finite catalog of approved passenger guidance can generate audio, not arbitrary submitted text.

With no key, no internet, rejected API access or exhausted demo limits, normal visual controls remain available and the interface explains why AI audio is unavailable. The integration is covered by mocked API and access-control tests. Verify live voice quality and phone microphone behavior using the actual devices and trusted HTTPS connection.

## Prepare common announcements before the demonstration

Run this from the project root in your normal Windows desktop terminal, using
the project-local `.venv` created in [Setup and operations](SETUP_AND_OPERATIONS.md). By default it
only reports how many of the 26 common clips are cached or missing; it makes no
API requests:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_voice_announcements.py
```

Add `--generate` to explicitly make paid API requests for missing clips using the
private host configuration. Prepare the clips before opening the demonstration
pages or while announcement playback is idle:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_voice_announcements.py --generate
```

The catalog covers all five approach and arrival announcements, passenger arrival
alerts, boarding, the departure warning, door closure, departure, standing/full-bus
messages and voice preview. Generation is sequential with at least 2.1 seconds
between request starts and stops on the first failure. Existing clips are reused
with the same cache identity as the live server. Re-running never regenerates a
valid cached clip. Output shows counts only, and the key is never printed.

The host's selected voice is used by default. To prepare the other supported voice,
add `--voice cedar` or `--voice marin`; that separate voice needs its own cached
clips and can incur additional API charges. Generated files stay in
`data/voice-cache` and are excluded from source archives. No server restart is
needed after preparation.

## Official references

- [OpenAI Realtime WebRTC unified interface](https://developers.openai.com/api/docs/guides/voice-webrtc)
- [Realtime conversations, manual VAD responses and tools](https://developers.openai.com/api/docs/guides/realtime-conversations)
- [Text to speech, voice choice and AI disclosure](https://developers.openai.com/api/docs/guides/text-to-speech)
- [Ending a Realtime WebRTC call](https://developers.openai.com/api/reference/python/resources/realtime/subresources/calls/methods/hangup)
