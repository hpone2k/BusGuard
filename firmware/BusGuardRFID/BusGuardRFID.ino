/*
 * BusGuard / ESP32 + MFRC522 reader
 * The server owns card profiles, occupancy, admission and announcements.
 * This reader sends a UID only; it never writes to a card.
 * See README.md and fill in config.h before uploading.
 */
#include <Arduino.h>
#include <SPI.h>
#include <MFRC522.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <esp_system.h>
#include "config.h"

constexpr uint8_t RFID_SCK = 18;
constexpr uint8_t RFID_MISO = 19;
constexpr uint8_t RFID_MOSI = 23;
constexpr uint8_t RFID_SS = 21;
constexpr uint8_t RFID_RST = 22;
constexpr uint32_t POLL_MS = 80;
constexpr uint32_t REMOVE_MS = 700;
constexpr uint32_t WIFI_RETRY_MS = 10000;
constexpr uint32_t DELIVERY_MS = 3000;
constexpr uint8_t MAX_POST_ATTEMPTS = 2;

MFRC522 reader(RFID_SS, RFID_RST);
bool configured = false;
bool readerReady = false;
bool cardLatched = false;
bool absenceStarted = false;
bool wasConnected = false;
uint32_t absenceSince = 0;
uint32_t lastPoll = 0;
uint32_t lastWifiAttempt = 0;
uint32_t lastReaderAttempt = 0;
uint32_t tapCounter = 0;
char bootId[17];

// Unsigned subtraction keeps short intervals correct across millis() rollover.
uint32_t age(uint32_t started) {
  return static_cast<uint32_t>(millis() - started);
}

bool validConfiguration() {
  if (!WIFI_SSID[0] || String(WIFI_SSID).startsWith("YOUR_") ||
      !RFID_READER_TOKEN[0] || String(RFID_READER_TOKEN).startsWith("PASTE_")) {
    Serial.println("SETUP: Fill in Wi-Fi and the dedicated reader token in config.h.");
    return false;
  }
  const String base(BUSGUARD_SERVER);
  if (!base.startsWith("http://") || base.endsWith("/")) {
    Serial.println("SETUP: Use the local http://COMPUTER-IP:4479 URL, without a trailing slash.");
    return false;
  }
  if (!RFID_READER_ID[0] || strlen(RFID_READER_ID) > 64) return false;
  for (const char *p = RFID_READER_ID; *p; ++p) {
    if (!isalnum(static_cast<unsigned char>(*p)) && *p != '-' && *p != '_') {
      Serial.println("SETUP: Reader ID must contain letters, numbers, hyphens or underscores.");
      return false;
    }
  }
  return true;
}

void initializeReader() {
  lastReaderAttempt = millis();
  reader.PCD_Init();
  const byte version = reader.PCD_ReadRegister(MFRC522::VersionReg);
  readerReady = version != 0x00 && version != 0xFF;
  if (readerReady) {
    Serial.print("RC522 ready. Version 0x");
    Serial.println(version, HEX);
    Serial.println("Present one card, then remove it before the next tap.");
  } else {
    Serial.println("RC522 not responding. Check 3.3V power, GND and the SPI wiring in README.md.");
  }
}

void maintainWifi() {
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected != wasConnected) {
    wasConnected = connected;
    if (connected) {
      Serial.print("Wi-Fi connected. Reader IP: ");
      Serial.println(WiFi.localIP());
    } else {
      Serial.println("Wi-Fi disconnected. Offline taps will not be queued or replayed.");
    }
  }
  if (!connected && age(lastWifiAttempt) >= WIFI_RETRY_MS) {
    lastWifiAttempt = millis();
    WiFi.reconnect();
    Serial.println("Reconnecting Wi-Fi...");
  }
}

// Every request has a short timeout. The reader never follows redirects with
// its token and never prints the token, Wi-Fi password or raw response body.
int requestJson(const char *method, const String &path, const String &body,
                JsonDocument &result, uint32_t remainingMs) {
  result.clear();
  if (WiFi.status() != WL_CONNECTED || remainingMs < 200) return -1;
  WiFiClient client;
  HTTPClient http;
  const uint32_t connectMs = remainingMs / 3 < 350 ? remainingMs / 3 : 350;
  const uint32_t readMs = remainingMs / 3 < 650 ? remainingMs / 3 : 650;
  http.setConnectTimeout(static_cast<int32_t>(connectMs));
  http.setTimeout(static_cast<uint16_t>(readMs));
  http.setReuse(false);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  if (!http.begin(client, String(BUSGUARD_SERVER) + path)) return -2;
  http.addHeader("Authorization", String("Bearer ") + RFID_READER_TOKEN);
  http.addHeader("Accept", "application/json");
  int status;
  if (strcmp(method, "POST") == 0) {
    http.addHeader("Content-Type", "application/json");
    status = http.POST(body);
  } else {
    status = http.GET();
  }
  if (status > 0) {
    // The API uses ordinary Content-Length JSON responses. Reject unexpectedly
    // large responses instead of reading arbitrary content into device memory.
    const int length = http.getSize();
    if (length < 0 || length > 8192) {
      http.end();
      return -3;
    }
    const String reply = http.getString();
    if (reply.length() != static_cast<size_t>(length) ||
        deserializeJson(result, reply) || !result.is<JsonObject>()) {
      http.end();
      return -4;
    }
  }
  http.end();
  return status;
}

void printOutcome(const JsonDocument &reply) {
  Serial.print(reply["accepted"].as<bool>() ? "ACCEPTED: " : "NOT ACCEPTED: ");
  Serial.println(reply["message"] | "The server returned no message.");
  if (reply["card_name"].is<const char *>()) {
    Serial.print("Card: ");
    Serial.print(reply["card_name"].as<const char *>());
    Serial.print(" | Type: ");
    Serial.println(reply["passenger_type"] | "Unassigned");
  }
  if (reply["count"].is<int>()) {
    Serial.print("Passengers aboard: ");
    Serial.println(reply["count"].as<int>());
  }
  if (reply["duplicate"] | false) {
    Serial.println("Receipt recovered. This tap was applied only once.");
  }
}

void sendTap(const String &uid) {
  const uint32_t tapAt = millis();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("NOT SENT: Wi-Fi offline. Reconnect, check the console, then remove and tap again.");
    return;
  }
  JsonDocument state;
  const int stateStatus = requestJson("GET", String("/api/rfid/reader-state?reader_id=") +
                                     RFID_READER_ID, "", state, DELIVERY_MS);
  if (stateStatus != 200 || !state["server_time_ms"].is<uint64_t>() ||
      !state["accepting"].is<bool>() || age(tapAt) >= DELIVERY_MS) {
    if (stateStatus == 401 || stateStatus == 403) {
      Serial.println("NOT SENT: Reader authorization failed. Check the reader ID and token in config.h.");
    } else {
      Serial.print("NOT SENT: Cannot get a fresh bus state. HTTP ");
      Serial.println(stateStatus);
    }
    return;
  }

  JsonDocument tap;
  tap["reader_id"] = RFID_READER_ID;
  tap["uid"] = uid;
  tap["event_id"] = String(bootId) + ":" + String(++tapCounter);
  tap["observed_at_ms"] = state["server_time_ms"].as<uint64_t>();
  if (state["window_id"].is<const char *>()) {
    tap["window_id"] = state["window_id"].as<const char *>();
  } else {
    tap["window_id"] = nullptr;
  }
  String payload;
  serializeJson(tap, payload);
  // Closed-state taps are still reported, so the operator sees the rejection.
  // Reusing this EXACT payload allows a lost receipt to be retried safely.
  for (uint8_t attempt = 0; attempt < MAX_POST_ATTEMPTS; ++attempt) {
    if (WiFi.status() != WL_CONNECTED || age(tapAt) >= DELIVERY_MS - 200) break;
    JsonDocument reply;
    const int status = requestJson("POST", "/api/rfid/tap", payload, reply,
                                   DELIVERY_MS - age(tapAt));
    if (status == 200 && reply["accepted"].is<bool>()) {
      printOutcome(reply);
      return;
    }
    if (status >= 400 && status < 500) {
      Serial.print("NOT ACCEPTED: Reader request rejected. HTTP ");
      Serial.println(status);
      if (status == 401 || status == 403) Serial.println("Check the reader ID/token in config.h.");
      return; // Auth, validation, conflict and policy failures are not retried.
    }
    // An unknown transport result may already have been accepted. Retry only
    // this event, briefly; never mint another event or save it for later.
    if (attempt + 1 < MAX_POST_ATTEMPTS && age(tapAt) < DELIVERY_MS - 400) delay(150);
  }
  Serial.println("RECEIPT UNKNOWN: Check the detection console before tapping again; the tap may have applied.");
  Serial.println("The event was discarded locally. It will not be replayed at another stop.");
}

String currentUid() {
  // Building by byte avoids leading-zero loss and keeps the server's format.
  String uid;
  uid.reserve(29);
  for (byte i = 0; i < reader.uid.size; ++i) {
    if (i) uid += ':';
    if (reader.uid.uidByte[i] < 16) uid += '0';
    uid += String(reader.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  return uid;
}

void pollReader() {
  if (age(lastPoll) < POLL_MS) return;
  lastPoll = millis();
  byte atqa[2];
  byte length = sizeof(atqa);
  // WUPA also sees HALTed cards. IsNewCardPresent alone would mistake a held,
  // halted card for removal and could alternate boarding/alighting repeatedly.
  const MFRC522::StatusCode status = reader.PICC_WakeupA(atqa, &length);
  if (status == MFRC522::STATUS_TIMEOUT) {
    if (!absenceStarted) {
      absenceStarted = true;
      absenceSince = millis();
    } else if (cardLatched && age(absenceSince) >= REMOVE_MS) {
      cardLatched = false;
      Serial.println("Reader ready for the next tap.");
    }
    return;
  }
  // Errors/collisions are not proof that the field is empty. Keep the latch.
  absenceStarted = false;
  if (status != MFRC522::STATUS_OK && status != MFRC522::STATUS_COLLISION) return;
  if (!reader.PICC_ReadCardSerial()) return;
  const String uid = currentUid();
  reader.PICC_HaltA();
  reader.PCD_StopCrypto1();
  if (cardLatched) return;
  cardLatched = true;
  Serial.print("Tap UID: ");
  Serial.println(uid);
  sendTap(uid);
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\nBusGuard RFID reader — Wi-Fi + RC522");
  snprintf(bootId, sizeof(bootId), "%08lx%08lx",
           static_cast<unsigned long>(esp_random()), static_cast<unsigned long>(esp_random()));
  SPI.begin(RFID_SCK, RFID_MISO, RFID_MOSI, RFID_SS);
  initializeReader();
  configured = validConfiguration();
  if (!configured) return;
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWifiAttempt = millis();
  Serial.println("Connecting to Wi-Fi. Serial monitor: 115200 baud.");
}

void loop() {
  if (!configured) {
    delay(100);
    return;
  }
  maintainWifi();
  if (!readerReady) {
    if (age(lastReaderAttempt) >= 5000) initializeReader();
  } else {
    pollReader();
  }
  delay(5); // Yield to the ESP32 networking/RTOS tasks.
}
