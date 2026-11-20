#pragma once
// Wi-Fi 連線與設定熱點（captive portal）。
// 改寫自 esp32-c3-adblock（MIT License, M-Abozaid）src/main.cpp 的
// hasCreds() / connectWiFi() / startConfigPortal() / handleWifiSave()。
// 沿用相同的 NVS 命名空間 "wifi"（鍵 ssid / pass），所以先前用廣告攔截韌體
// 存過的 Wi-Fi，燒錄本韌體後會直接沿用，不必重新設定。
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>

extern WebServer web;
extern Preferences prefs;
extern DNSServer dnsPortal;

static String portalOpts;   // 設定頁的 <option> 清單（啟動熱點時掃描一次）

// 設定頁的 SSID 可能是別人廣播的惡意名稱，輸出到 HTML 前一律跳脫
static String htmlEscape(const String& s) {
  String o; o.reserve(s.length());
  for (char ch : s) {
    switch (ch) {
      case '&':  o += "&amp;";  break;
      case '<':  o += "&lt;";   break;
      case '>':  o += "&gt;";   break;
      case '"':  o += "&quot;"; break;
      case '\'': o += "&#39;";  break;
      default:   o += ch;
    }
  }
  return o;
}

static bool secretsHaveWifi() {
  return WIFI_SSID && *WIFI_SSID && strcmp(WIFI_SSID, "YOUR_WIFI_SSID") != 0;
}

static bool hasCreds() {
  prefs.begin("wifi", true);
  bool nvs = prefs.getString("ssid", "").length() > 0;
  prefs.end();
  return nvs || secretsHaveWifi();
}

// 先用 NVS 已存的帳密，沒有才用 secrets.h；連上回傳 true
static bool connectWiFi() {
  prefs.begin("wifi", true);
  String ss = prefs.getString("ssid", "");
  String pw = prefs.getString("pass", "");
  prefs.end();
  const char* ssid = ss.length() ? ss.c_str() : WIFI_SSID;
  const char* pass = ss.length() ? pw.c_str() : WIFI_PASS;
  if (!ss.length() && !secretsHaveWifi()) return false;   // 完全沒有設定
  Serial.printf("WiFi: connecting to \"%s\"%s\n", ssid, ss.length() ? " (saved)" : " (secrets.h)");
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setHostname("busbench");
  WiFi.begin(ssid, pass);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 30000) { delay(250); Serial.print("."); }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

static void handlePortalRoot() {
  const char* field = "width:100%;box-sizing:border-box;padding:11px;margin:6px 0;border-radius:6px;"
                      "border:1px solid #30363d;background:#161b22;color:#c9d1d9";
  String html =
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>Bus Bench Wi-Fi 設定</title>"
    "<body style='font:16px system-ui,sans-serif;max-width:420px;margin:36px auto;padding:0 16px;background:#0d1117;color:#c9d1d9'>"
    "<h2>Bus Bench &mdash; Wi-Fi 設定</h2>"
    "<p style='color:#8b949e'>選擇要連線的 Wi-Fi 並輸入密碼，儲存後裝置會重新開機並連線。</p>"
    "<form method=POST action=/wifisave>"
    "<select name=s style='" + String(field) + "'>" + portalOpts + "</select>"
    "<input name=s2 placeholder='或手動輸入 Wi-Fi 名稱' style='" + String(field) + "'>"
    "<input name=p type=password placeholder='密碼' style='" + String(field) + "'>"
    "<button style='width:100%;padding:12px;margin-top:8px;border-radius:6px;border:0;background:#3fb950;color:#000;font-weight:600'>連線</button>"
    "</form></body>";
  web.send(200, "text/html; charset=utf-8", html);
}

static void handleWifiSave() {
  String ss = web.arg("s2"); ss.trim();          // 手動輸入優先
  if (!ss.length()) ss = web.arg("s");
  String pw = web.arg("p");
  if (!ss.length()) { web.send(400, "text/plain; charset=utf-8", "缺少 Wi-Fi 名稱"); return; }
  prefs.begin("wifi", false); prefs.putString("ssid", ss); prefs.putString("pass", pw); prefs.end();
  web.send(200, "text/html; charset=utf-8",
           "<!doctype html><meta charset=utf-8><body style='font:16px system-ui;text-align:center;margin-top:60px'>"
           "已儲存，正在重新開機並連線到 <b>" + htmlEscape(ss) + "</b>&hellip;</body>");
  delay(900);
  ESP.restart();
}

// 不會返回：停在設定熱點，直到使用者儲存帳密（之後重新開機）
static void startConfigPortal() {
  WiFi.mode(WIFI_STA);
  delay(200);
  int n = 0;
  for (int attempt = 1; attempt <= 3 && n <= 0; attempt++) {
    if (attempt > 1) delay(700);
    n = WiFi.scanNetworks();
    Serial.printf("[setup] scan %d/3: %d networks\n", attempt, n);
  }
  portalOpts = "<option value=''>-- 選擇 Wi-Fi --</option>";
  for (int i = 0; i < n && i < 15; i++) {
    const String s = WiFi.SSID(i);
    portalOpts += "<option value='" + htmlEscape(s) + "'>" + htmlEscape(s) + "</option>";
  }
  uint8_t mac[6]; WiFi.macAddress(mac);
  char ap[24]; snprintf(ap, sizeof(ap), "BusBench-%02X%02X", mac[4], mac[5]);
  WiFi.mode(WIFI_AP);
  WiFi.softAP(ap);
  WiFi.setTxPower(WIFI_POWER_8_5dBm);           // 手機就在旁邊，低功率即可
  IPAddress apIP = WiFi.softAPIP();
  dnsPortal.start(53, "*", apIP);                // 讓手機自動跳出設定頁
  web.on("/", handlePortalRoot);
  web.on("/wifisave", HTTP_POST, handleWifiSave);
  web.onNotFound(handlePortalRoot);
  web.begin();
  Serial.printf("\n[setup] No WiFi. Join open network \"%s\" (or http://%s)\n", ap, apIP.toString().c_str());
  // 已有帳密只是暫時連不上（路由器重開等）：沒人使用熱點時每 3 分鐘重開機重試
  const bool configured = hasCreds();
  uint32_t t0 = millis();
  while (true) {
    dnsPortal.processNextRequest();
    web.handleClient();
    delay(2);
    if (WiFi.softAPgetStationNum() > 0) t0 = millis();
    if (configured && millis() - t0 > 180000UL) { Serial.println("[setup] retrying WiFi"); ESP.restart(); }
  }
}
