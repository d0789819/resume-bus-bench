#pragma once
// 複製成 secrets.h 後修改（secrets.h 不會進 git）。
//
// Wi-Fi：開機時先用 NVS 裡已儲存的帳密（例如先前用設定熱點存過的 Wi-Fi），
// 沒有才用這裡的值；兩者都連不上時，會開啟設定熱點 "BusBench-XXXX"。
static const char* WIFI_SSID = "YOUR_WIFI_SSID";
static const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

// 保護 POST /api/run、/api/stop 的 HTTP Basic Auth 帳密。
// 後端 .env 的 ESP32_USER / ESP32_PASS 必須一致。
static const char* WEB_USER = "admin";
static const char* WEB_PASS = "CHANGE_ME_WEB_PASSWORD";
