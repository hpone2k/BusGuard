# BusGuard ESP32 + RC522 reader

The ESP32 reads a card UID and sends the tap over Wi-Fi to the shared BusGuard server. The server decides whether that tap boards or alights a passenger, updates the available seats, and publishes the result to the detection console, bus display and passenger app. Card types and announcements are configured on the server, so changing a passenger type does not require reflashing the ESP32.

## Wiring

These are the pins used by the supplied sketch. Disconnect power while changing wires.

| RC522 pin | ESP32 pin |
| --- | --- |
| SCK | GPIO 18 / D18 |
| MISO | GPIO 19 / D19 |
| SDA / SS | GPIO 21 / D21 |
| RST | GPIO 22 / D22 |
| MOSI | GPIO 23 / D23 |
| GND | GND |
| 3.3V | 3V3 |
| IRQ | Leave unconnected |

The RC522's `SDA` label is the SPI chip-select connection here, not I²C. Power it from **3.3V**, not 5V. There is no external LED, buzzer, speaker or GPIO 12 control in this sketch. Announcements play through the existing bus website's audio output.

## Install and configure

1. In Arduino IDE's Boards Manager, install **esp32 by Espressif Systems**. If it is missing, add `https://espressif.github.io/arduino-esp32/package_esp32_index.json` under **File → Preferences → Additional Boards Manager URLs**, following [Espressif's installation guide](https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html). Select **ESP32 Dev Module** for the board shown in your photograph.
2. In Library Manager, install **MFRC522** by GithubCommunity and **ArduinoJson 7** by Benoit Blanchon. The project was compiled with ESP32 core **3.3.5**, MFRC522 **1.4.12** and ArduinoJson **7.4.3**.
3. Open `BusGuardRFID.ino` inside the `BusGuardRFID` folder. Keep the sketch filename and folder name the same. Keep `config.h` in that same folder. If you downloaded only `config.example.h`, copy it to `config.h` first.
4. Open the detection console on your computer: **http://192.168.1.100:4479/** (example; replace it with your computer's current LAN IP). Pair the operator browser if requested. In the RFID section, create an ESP32 reader and copy the generated reader ID and reader token. The token is displayed when it is created.
5. Edit `config.h`:

   ```cpp
   constexpr char WIFI_SSID[] = "Your Wi-Fi name";
   constexpr char WIFI_PASSWORD[] = "Your Wi-Fi password";
   constexpr char BUSGUARD_SERVER[] = "http://192.168.1.100:4479";
   constexpr char RFID_READER_ID[] = "busguard-esp32";
   constexpr char RFID_READER_TOKEN[] = "Paste the dedicated reader token";
   ```

   Use the reader ID returned by your console if it differs. This token is **not** an OpenAI API key and is **not** the six-digit operator pairing code. `config.h` is excluded from Git; do not share the populated file. The server stores a hash of the reader token, not the original token.

6. Connect the ESP32 to the same local network as the computer. The pictured ESP32 uses **2.4 GHz Wi-Fi**. The computer and phone may use 5 GHz if both bands belong to the same reachable LAN. Guest/client-isolated Wi-Fi can prevent access.
7. In Arduino IDE, choose the ESP32's current USB port. The photograph showed COM4, but check the current port rather than assuming it stayed the same. Click **Verify**, then **Upload** when ready. Open Serial Monitor at **115200 baud**.
8. Check for `RC522 ready` and `Wi-Fi connected`, then start the stop/go scenario in the console and open a stop for boarding. Present one card, remove it completely, and wait for `Reader ready for the next tap` before presenting another card.

The server address must be the computer's current LAN IP. Do not use `localhost`, `127.0.0.1`, the ESP32's IP, or the passenger app's port. The firmware uses the local HTTP API on port **4479**. If the computer's address changes, update `BUSGUARD_SERVER` and upload again; a router DHCP reservation can keep the computer's address stable.

## Configure four demo cards

The repository contains four synthetic example UIDs, not the author's physical card addresses. Before the first server start, replace `CARD_UIDS` in `vision/rfid.py` with your four card UIDs. The console changes passenger profiles, not UID enrollment. Do not change these identities beneath an existing controller journal. The firmware reads UIDs and does not hardcode the list.

| Card | UID |
| --- | --- |
| Card 1 | `04:A0:00:01` |
| Card 2 | `04:a0:00:02` |
| Card 3 | `04:A0:00:03` |
| Card 4 | `04:A0:00:04` |

All four start as ordinary passenger cards. In the detection console, choose **normal**, **senior**, **pregnant** or **custom**, and save. Custom labels can have their own priority/extra-time preferences. Assign these deliberately for the demonstration; appearance is not used to infer pregnancy or age.

The first accepted tap boards that card's passenger (`+1`). Once it is aboard, the next accepted tap alights that passenger (`−1`). An unknown, full-capacity, duplicate, stale or closed-admission tap does not add a passenger. Alighting is still possible when the bus is full during an open boarding window. The existing 30-second inside-camera mean reconciliation remains on the server.

When an eligible priority passenger boards, the bus announcement politely asks passengers who do not need a priority seat to offer it. Enable bus audio in the bus website before the demonstration; the ESP32 itself has no speaker.

## Delivery and duplicate protection

- **One tap per presentation:** the sketch repeatedly wakes/selects/halts cards so a held card is still recognized as present. It re-arms only after the reader reports an empty field for at least 700 ms. Present one card at a time.
- **Same event on retry:** every tap gets a boot-specific event ID. A lost response is retried with the exact same payload, at most twice and within a three-second delivery budget. Server idempotency prevents the retry from toggling the passenger a second time.
- **No offline replay:** taps while disconnected are rejected locally. No tap queue is saved in flash or resent at a later stop.
- **Fresh bus window:** the reader gets the current admission window and server time before each tap. The server checks that window and freshness again when accepting the event. A delayed tap cannot be carried into a new stop.
- **Unknown receipt:** if both attempts fail, Serial Monitor says `RECEIPT UNKNOWN`. The first attempt might already have applied. Check the console's last scan/onboard status before tapping again.
- **Closed admission:** these taps are reported so the operator can see the rejection, but they do not change occupancy.

Wi-Fi reconnects in the background. An HTTP transaction can briefly pause card polling, with short network timeouts. Keep the card presented until the serial result, then remove it before the next tap. No internet connection is needed for RFID itself.

## API used by the sketch

Every request uses `Authorization: Bearer <dedicated reader token>`; that token is scoped to the named reader and does not provide operator access.

`GET /api/rfid/reader-state?reader_id=busguard-esp32` returns `server_time_ms`, `window_id`, `accepting` and `message`.

`POST /api/rfid/tap` submits:

```json
{
  "reader_id": "busguard-esp32",
  "uid": "04:A0:00:01",
  "event_id": "a1b2c3d4e5f60708:1",
  "window_id": "current-window-from-reader-state",
  "observed_at_ms": 1790000000000
}
```

The actual timestamp/window come from the immediately preceding state request. `window_id` is `null` if there is no active window. A HTTP 200 response can still have `accepted: false`; always inspect the response. Results include `code`, `message`, `direction`, `card_name`, `passenger_type`, `onboard`, `count` and `duplicate`. Responses use Content-Length JSON, as supplied by this project's API.

## Troubleshooting

| Serial message or symptom | Check |
| --- | --- |
| `SETUP` | Replace the placeholders in `config.h`. |
| `RC522 not responding` | 3.3V power, common GND, wire order, and GPIO 21 chip-select. |
| Wi-Fi never connects | Exact SSID/password and a reachable 2.4 GHz network. |
| `Reader authorization failed` | Reader ID and token must match; a rotated token needs a new upload. |
| `Cannot get a fresh bus state` | Server is running on port 4479, current LAN IP, firewall and Wi-Fi client isolation. |
| Tap rejected as closed | Bus must be stopped with boarding admission open, inside its time limit. |
| Unknown card | Compare the UID in Serial Monitor with the four registered card UIDs. |
| A held card never taps again | Expected. Remove it fully for at least 700 ms before presenting it again. |
| Priority announcement is silent | Check bus website audio permission, audio enabled, server voice setup, speaker volume and the selected card profile. |

This setup identifies demo cards by UID. UIDs are not proof of personal identity and are not a payment or secure fare credential.

## Validation and references

The final sketch compiled successfully for `esp32:esp32:esp32`: **962,611 bytes flash (73%)** and **46,744 bytes global RAM (14%)**. Compilation checks the sketch and library compatibility. Physical wiring, RF reads, your Wi-Fi credentials and actual board uploads still need to be tested on the ESP32; no hardware upload was performed.

- [MFRC522 library and wiring notes](https://github.com/miguelbalboa/rfid)
- [MFRC522 wake-up/select/halt implementation](https://github.com/miguelbalboa/rfid/blob/master/src/MFRC522.cpp)
- [Espressif Arduino Wi-Fi API](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/wifi.html)
- [ArduinoJson 7 deserialization](https://arduinojson.org/v7/tutorial/deserialization/)
