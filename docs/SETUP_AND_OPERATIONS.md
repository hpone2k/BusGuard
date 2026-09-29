# Setup and operations

This guide installs the current BusGuard / BusTech prototype on a new computer and explains day-to-day operation. Run commands from the application directory containing `run.py`, `requirements.txt` and `static/`. In a checkout that preserves the original workspace layout, this is `vision4479/`.

The three websites share one Python process and one authoritative controller. The optional HTTPS passenger website is a fourth listener on that same server. Opening the 3D site on another device moves its rendering workload to that device; detection still runs on the server computer. See [system workflow](SYSTEM_WORKFLOW.md) for the current timing and departure rules and [architecture](ARCHITECTURE.md) for the implementation.

## 1. Requirements

| Component | Purpose |
| --- | --- |
| Python 3.12 | The project's recorded Windows runtime; use a project virtual environment. |
| Git | Source checkout and the pinned CLIP dependency installed from Git. |
| NVIDIA GPU and compatible driver, optional | Practical acceleration for two live inputs. CPU mode exists, but its speed depends on the workload. |
| Node.js and npm | Run browser unit tests and rebuild the Three.js bus bundle. The committed static website can be served without npm running. |
| PowerShell 7, optional | Generate the local phone HTTPS certificate with the supplied Windows helper. |
| Same reachable LAN | Computer, phone, second display, IP cameras and ESP32 communicate locally. Guest/client-isolated Wi-Fi may prevent this. |
| Internet, during setup | Package/model downloads. OpenAI voice also needs internet while in use. Local camera detection, RFID and text controls do not inherently need cloud inference. |

Model weights, virtual environments, private settings, recordings and generated runtime data are intentionally not part of a source checkout. Download models and configure credentials on each host.

## 2. Create a Python environment

From the application directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

Install a matched `torch` / `torchvision` build **before** the application dependencies. Choose Windows, Pip, Python and the compute platform appropriate to the computer in the [official PyTorch installer](https://pytorch.org/get-started/locally/). Use `.\.venv\Scripts\python.exe -m pip` in place of `pip` in the selected command so it installs into this environment.

The project's recorded Windows environment uses `torch 2.11.0+cu128`, `torchvision 0.26.0+cu128` and Ultralytics `8.4.143`. These are the recorded project versions, not a promise that every machine supports that CUDA build. `requirements-lock-windows.txt` is a snapshot of the original host, including optional and historical packages; it is not the recommended first installation command for another platform.

Then install the application:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import torch; print('PyTorch:', torch.__version__); print('CUDA available:', torch.cuda.is_available())"
```

The normal requirements include FastAPI, Uvicorn, OpenCV, NumPy, SciPy, Pillow, imageio-ffmpeg, Ultralytics, tracking support and the phrase runtime. The CLIP dependency is pinned to a Git commit. Missing dependencies are installed deliberately through these steps; the launcher disables Ultralytics' automatic package installation.

For a lightweight interface smoke check without PyTorch/model downloads, a separate environment can install only `requirements-base.txt` and start `run.py --backend demo`. This backend detects **colored regions**, not passengers. Use image input or disable stabilization for that limited check; tracked live detection needs the normal runtime. Demo observations cannot establish live passenger safety or verify the standing check.

Linux can use `python3.12 -m venv .venv` and `.venv/bin/python` in the same Python commands. The `.cmd` launchers, firewall helper and demo-certificate helper are Windows-specific. Linux deployment and package combinations have not been validated to the same extent as the recorded Windows host.

## 3. Prepare the detector models

### YOLOE: objects and standing

```powershell
.\.venv\Scripts\python.exe run.py --backend yoloe --device auto
```

The default checkpoint name is `yoloe-26m-seg.pt`. The Ultralytics loader handles a recognized checkpoint name and may download it on first use; a network connection is needed if it is not already present. You can instead provide a local trusted checkpoint:

```powershell
.\.venv\Scripts\python.exe run.py --backend yoloe --model C:\Models\yoloe-26m-seg.pt --device cuda:0
```

Use `--device cpu` if CUDA is unavailable. Initial startup warms multiple inference sizes and the standing prompt; wait for the console's model status to become ready. A reachable homepage alone does not mean the model is ready. Model capabilities come from `GET /api/status`.

The active standing check reuses YOLOE with a separate `standing person` prompt. It does not require MediaPipe, joints, WSL or a LocateAnything worker. See [the standing departure specification](STANDING_DEPARTURE.md). The prompt is not a bus-specific trained classifier; evaluate it using the actual cabin camera and lighting before demonstrating its results.

### Hybrid: add detailed phrases

The default `hybrid` backend adds Grounding DINO Tiny for detailed phrase mode. Prepare its checkpoint before starting that backend:

```powershell
.\.venv\Scripts\python.exe scripts\setup_grounding.py
.\.venv\Scripts\python.exe run.py --backend hybrid --device auto
```

The download helper retrieves the pinned official `IDEA-Research/grounding-dino-tiny` revision into `models/grounding-dino-tiny/`. Its roughly 689 MB `model.safetensors` file is checked against the expected size and SHA-256 before installation. The helper downloads data/configuration, not repository Python code. Once prepared, phrase inference is local. `--phrase-model` selects another explicitly prepared model location.

Hybrid startup fails with a clear instruction when its default phrase checkpoint is missing. Use `--backend yoloe` for object and standing demonstrations that do not need detailed phrases. Historical `grounding-dino` and `locateanything` backends remain in the CLI; they are optional research paths, not prerequisites for the active bus workflow. `requirements-grounding.txt` and `requirements-posture.txt` are not needed for ordinary YOLOE standing detection.

### FFmpeg

RTSP capture and video export use FFmpeg. Resolution order is the explicit `--ffmpeg` path, an executable on `PATH`, then the executable provided by `imageio-ffmpeg`. To override it:

```powershell
.\.venv\Scripts\python.exe run.py --backend yoloe --ffmpeg C:\Tools\ffmpeg\bin\ffmpeg.exe
```

## 4. Serve the websites on the local network

```powershell
.\.venv\Scripts\python.exe run.py --lan
```

Or use `start-lan.cmd` after the Python environment and default hybrid models are prepared. For a YOLOE-only LAN launch, use the explicit command with `--backend yoloe`, or `start.ps1 -Lan -Backend yoloe`.

| Website | Address pattern | Responsibility |
| --- | --- | --- |
| Detection/operator console | `http://<HOST_IP>:4479/` | Cameras, settings, RFID profiles and simulated bus controls. |
| 3D bus | `http://<HOST_IP>:4480/` | Shared bus state, movement, doors, ramp, seats and announcements. |
| Passenger app | `http://<HOST_IP>:4481/` | Assistance requests, route, countdowns and accessibility settings. |
| Secure passenger voice | `https://<HOST_IP>:4482/` | Same passenger app with a trusted HTTPS microphone context; requires the TLS setup below. |

Replace `<HOST_IP>` with the server computer's current physical Wi-Fi/Ethernet IPv4 address, as shown by the LAN console or launch output. Do not use a stale address from a screenshot, a virtual-adapter address, the camera's address or the phone's own address. `localhost` on a phone means the phone itself.

The `--lan` flag binds the websites to `0.0.0.0`; without it, the default is loopback only. Ports are configurable with `--port`, `--viewer-port`, `--passenger-port` and `--passenger-https-port`. Setting either secondary HTTP port to `0` disables that standalone website. The main console also serves `/bus` and `/passenger/`.

Only one server should own these ports. Press Ctrl+C in its launch terminal to stop it cleanly before restarting. The launcher probes for occupied ports and does not silently replace another process.

### Pair an operator device

On the host computer, open `http://localhost:4479/`. The six-digit pairing code is shown only to a loopback client. Enter it in the detection console opened on the LAN device. It grants an operator cookie for up to 12 hours; restarting the server invalidates it and creates a new code. The passenger app and read-only 3D viewer do not need this code. An ESP32 uses its own dedicated reader token instead.

### Windows Firewall

If the host can load the website but the phone cannot, confirm the IP address, common LAN and lack of Wi-Fi client isolation first. `enable-lan-firewall.cmd` asks for administrator approval and runs the supplied helper. Its rule is restricted to:

- TCP ports **4479–4482**;
- the resolved Python server executable;
- currently active physical-interface IPv4 addresses;
- peers in `LocalSubnet`.

The rule applies across Windows network profiles; it does not disable the firewall or change the network profile. It must be refreshed after the computer's IP changes. An operator can later remove the demo rule in administrator PowerShell with `Remove-NetFirewallRule -Name BusTech-LAN-Demo`.

This is a trusted-LAN prototype. Do not port-forward these listeners or treat the local pairing mechanism as an internet deployment design.

## 5. Connect and configure the two cameras

Each Outside/Inside input card can select RTSP CCTV, browser camera, image or recorded video. Assign the role deliberately; an inside source and an outside source have different controller permissions.

For RTSP, supply the exact camera URL verified with the camera vendor's configuration or VLC, for example:

```text
rtsp://<CAMERA_USER>:<URL_ENCODED_PASSWORD>@<CAMERA_IP>:554/<CAMERA_STREAM_PATH>
```

The stream path is vendor-specific, so the example is not a universal address. Percent-encode credentials when they contain URL delimiters. Use `rtsp://`, without a backslash before the colon. Camera credentials stay in server memory; reconnect streams after a restart. Do not put real camera URLs in documentation, Git commits or shared screenshots.

For the inside input, include the literal `person` category and use a live source for cabin counting. Enable **Standing detection · YOLOE**. The timed departure scenario requires the standing pass after closure; a disconnected camera, unsupported test source or stale result cannot be treated as zero standing. Confirm full-cabin coverage only if the camera actually sees the whole passenger area; this enables the 30-second cabin-count reconciliation.

Outside inference runs only while stationary with doors fully open. Inside person counting continues while boarding, braking and travelling. Standing inference is paused during opening/open/closing doors and runs with doors fully closed. Five continuous seconds of fresh successful standing checks with no standing detections can clear the standing departure gate; other holds still apply.

Confidence thresholds can be configured per object category, with a separate standing threshold. Start with the established project settings and check both positive and negative examples; lowering a threshold indiscriminately can increase false holds. Browser camera access normally requires localhost or trusted HTTPS. A phone can use the passenger app while the server reads the two RTSP streams.

For a smoother live view, use the console's live-video display; detection-frame display shows the exact frame associated with the result. The server replaces old pending frames instead of accumulating a backlog. If latency rises, inspect queue/inference timings, verify CUDA use, and test a smaller supported input size. A separate device for the 3D viewer frees the detection computer from rendering that scene.

## 6. Optional OpenAI voice

Run the existing hidden-input helper in a local interactive terminal:

```powershell
.\.venv\Scripts\python.exe scripts\setup_voice.py
```

Enter a normal OpenAI API key privately and choose `marin` or `cedar`. The helper writes `data/voice.env` with restricted local permissions. Do not commit that file or paste the key into frontend code, command arguments or a URL. Server environment variables `OPENAI_API_KEY` and `BUSGUARD_VOICE` override the file. The running service re-reads the settings; changing the key does not require restarting all cameras.

The configured integration currently requests `gpt-realtime-2.1` for the assistant, `gpt-4o-mini-transcribe` for transcription and `gpt-4o-mini-tts` for announcements. Those are source configuration values, not an account-access guarantee. An API account with billing/access and outbound internet is required. Reading configuration does not incur a paid voice request; connecting voice or generating an uncached announcement can.

After enabling voice and granting microphone permission, say **Hey BusGuard**. The assistant waits in standby between conversations, returns to standby after the follow-up inactivity window, and summarizes replies. During reply preparation/playback the microphone is paused; it resumes after playback finishes, so speech cannot interrupt the answer. Wake detection uses cloud transcription, not a local offline wake-word model: while listening in standby, audio still goes to the transcription service. **Stop listening** disables it. See [voice behavior and setup](VOICE_SETUP.md) for supported commands, limits and privacy details.

To inspect the common-announcement cache without making paid requests:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_voice_announcements.py
```

Adding `--generate` explicitly generates missing clips using the configured account. Bus announcements and assistant replies are serialized within each page. Keep only one audible announcement page per device to avoid separate browser tabs speaking simultaneously.

## 7. Trusted HTTPS for phone voice

The HTTP passenger site works for text controls. Microphone access on a phone needs a valid, trusted HTTPS origin; clicking past a certificate warning is not equivalent to trusting the certificate.

From PowerShell 7, generate a temporary certificate for the computer's **current** LAN IP:

```powershell
pwsh -File scripts\setup_passenger_https.ps1 -IPAddress <HOST_IP>
```

Replace `<HOST_IP>` before running. The helper creates a 30-day server certificate and a temporary demo CA, and refuses to overwrite existing TLS files. It does not change firewall settings, start a server or install certificate trust. Its outputs are:

| File | Handling |
| --- | --- |
| `data/tls/passenger-cert.pem` | Server certificate/chain. |
| `data/tls/passenger-key.pem` | Private server key; keep on the host, excluded from source control. |
| `data/tls/BusGuard-demo-root.cer` | Public CA certificate used for deliberate trust on your demo devices. |

Start with HTTPS enabled:

```powershell
.\.venv\Scripts\python.exe run.py --lan --passenger-tls-cert data\tls\passenger-cert.pem --passenger-tls-key data\tls\passenger-key.pem
```

Or use `start-lan-voice.cmd`. On the phone, open `http://<HOST_IP>:4481/setup`, compare the public certificate fingerprint with the host's setup page/helper output, and follow the provided Android or iPhone trust instructions. Install **only** the public `.cer` file. iPhone requires explicitly enabling full trust after installing the profile. Never transfer the `.pem` private key. Then open `https://<HOST_IP>:4482/` and enable voice.

When changing Wi-Fi/IP or replacing an expired certificate, stop the server, keep old TLS files in a private backup location, deliberately generate matching replacements, and update trust on the demo devices. This helper makes a new CA for each issuance; the old trusted CA does not validate the replacement. Remove temporary CA trust after the demonstration. An already trusted certificate and matching unencrypted PEM private key can be supplied instead.

Keep the phone page visible and unlocked for reliable foreground voice/arrival alerts. Browser/device support determines whether vibration works; the app reports unsupported vibration and retains visual/optional spoken alerts. It does not provide a native background notification service.

## 8. ESP32 and RC522

Use [the firmware guide](../firmware/BusGuardRFID/README.md), `BusGuardRFID.ino` and `config.example.h` in `firmware/BusGuardRFID/`.

| RC522 | ESP32 |
| --- | --- |
| SCK | GPIO 18 |
| MISO | GPIO 19 |
| MOSI | GPIO 23 |
| SDA / SS | GPIO 21 |
| RST | GPIO 22 |
| GND | GND |
| 3.3V | 3V3 |
| IRQ | Unconnected |

`SDA` is SPI chip select here. No LED/buzzer is connected or needed. Create a reader in the operator console and copy its generated ID/token into a private `config.h` copied from `config.example.h`, beside the sketch. Also enter the 2.4 GHz Wi-Fi credentials and the computer's current `http://<HOST_IP>:4479` address. This dedicated reader token is neither the operator pairing code nor an OpenAI API key.

Arduino libraries and the recorded build versions are in the firmware guide. Flashing embeds the configuration in the ESP32, so it can run independently of USB afterward. A Wi-Fi/server-address change requires updating the firmware configuration and uploading again. Card type/profile changes are server settings and do not require reflashing.

Only accepted taps during the open admission window toggle a card's onboard state. Retries reuse the same event ID to avoid adding and immediately removing a passenger. Present one card at a time and remove it before the next tap. Announcements play on the bus website's device, not the ESP32.

## 9. Routine demonstration and recovery

1. Start one server, wait for the model to be ready, and open the console on the host.
2. Reconnect camera sources, enable inside standing detection and confirm genuine full-cabin coverage.
3. Review any saved passenger count, card profiles and active requests. A restart restores the journal but holds motion for explicit operator revalidation; use **Reset held simulation** after checking the setup.
4. Open the 3D viewer on the display device and the passenger app on the phone. Enable desired audio/alerts with a deliberate user gesture.
5. Start the timed scenario. Subsequent Stop commands trigger arrivals at the next A–E station. Follow the [current workflow](SYSTEM_WORKFLOW.md) and [demonstration guide](DEMONSTRATION_GUIDE.md).
6. After the demonstration, stop voice, disconnect inputs and stop the server with Ctrl+C.

If Wi-Fi changes, restart the server so its trusted-host list reflects the current network; update links, narrow firewall addresses, certificate/IP trust and the ESP32 server address as appropriate. A DHCP reservation for the host can reduce repeated IP changes.

`data/assistance.json` is the controller journal: requests, RFID profiles/readers, card presence and count state are saved there with capability/reader token hashes. Do not delete it as an ordinary restart procedure. Preserve it privately if the state matters; a corrupt/unwritable journal intentionally holds operation rather than silently resetting passengers. RTSP credentials are not restored from this journal.

## 10. Checks and troubleshooting

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
npm ci
npm test
npm run build:bus
.\.venv\Scripts\python.exe -m pip check
```

Python tests exercise controller rules, inputs, request ownership, RFID, timing, counting and voice behavior with deterministic/mocked cases. Browser tests exercise UI logic. They do not by themselves establish real-camera standing accuracy, physical RFID connectivity or live OpenAI audio quality. Performance/model validation scripts are in `scripts/`; run them deliberately with the appropriate installed models and input assets.

| Symptom | First checks |
| --- | --- |
| Homepage loads, detection says loading/unavailable | Read model status; allow warmup; check missing weights, dependency errors and CUDA availability. |
| Hybrid fails at startup | Prepare Grounding DINO with `setup_grounding.py`, or select YOLOE-only. |
| Phone cannot connect | Current host IP, `--lan`, Wi-Fi isolation, sleeping host and firewall address/port scope. |
| HTTP works; HTTPS fails | Listener 4482 enabled, current-IP certificate, matching trusted CA and firewall. |
| Pairing code is missing | Open the detection console on host `localhost`; LAN clients do not receive the code. |
| Camera connected in VLC but unavailable here | Exact RTSP path, URL encoding, reachable camera, FFmpeg; keep credentials out of shared logs. |
| Outside camera has no detection while travelling | Expected role gate; inference resumes only while stationary with fully open doors. |
| Departure held with no standing box | Check freshness/validity of the standing pass and five-second progress, then the explicit emergency/doorway/mobility/capacity/restart hold reason. No box alone is not proof of a valid negative inference. |
| Passenger count differs from instantaneous boxes | RFID changes are immediate; the camera correction requires a complete 30-second cycle. See current workflow. |
| Voice cannot activate | Trusted HTTPS, microphone permission, visible page, enabled voice, network/API configuration; use Talk now to distinguish wake recognition from connection failure. |
| Voice output is silent | User gesture/audio permission, selected page mute and output device; visual controls remain usable. |
| `config.h: No such file or directory` | Copy `config.example.h` to `config.h` in the same folder as `BusGuardRFID.ino`, fill it privately, and open that saved sketch. |

Source-level endpoint details and request examples are in the [API reference](API_REFERENCE.md).
