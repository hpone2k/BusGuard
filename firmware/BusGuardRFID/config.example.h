#pragma once

// Copy this file to config.h, or edit the supplied config.h placeholders.
// Keep credentials private. config.h is ignored by Git.
constexpr char WIFI_SSID[] = "YOUR_2_4_GHZ_WIFI_NAME";
constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";

// Your COMPUTER's LAN address, not localhost and not the ESP32's address.
// No trailing slash. Update this if your computer changes Wi-Fi/IP address.
constexpr char BUSGUARD_SERVER[] = "http://192.168.1.100:4479";

// Create a reader in the detection console and copy its dedicated token here.
// This is NOT your OpenAI API key or the six-digit operator pairing code.
constexpr char RFID_READER_ID[] = "busguard-esp32";
constexpr char RFID_READER_TOKEN[] = "PASTE_READER_TOKEN_HERE";
