// Bus Bench：ESP32 匯流排訊號產生器 + REST API
//   GET  /api/info    韌體版本、IP、腳位
//   GET  /api/status  目前執行狀態與計數
//   POST /api/run     開始送訊號（需 Basic Auth + X-Requested-With: busbench）
//   POST /api/stop    停止
#include <Arduino.h>
#include <ArduinoJson.h>
#include <DNSServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>

#include "esp_log.h"
#include "bus_gen.h"
#include "secrets.h"

#ifndef FW_VERSION
#define FW_VERSION "dev"
#endif

WebServer web(80);
Preferences prefs;
DNSServer dnsPortal;

#include "wifi_setup.h"

static const char* HOSTNAME = "busbench";
static const int WIFI_RESET_PIN = 0;  // BOOT 鍵：開機時按住可清除已存的 Wi-Fi

// 會改變狀態的 API 需要 Basic Auth，且必須帶自訂標頭（瀏覽器的 <img>/<form> 無法附加，可擋 CSRF）。
// 做法沿用 esp32-c3-adblock 的 requireAuth()。
static const char* CSRF_HEADER = "X-Requested-With";
static const char* CSRF_VALUE = "busbench";

static bool requireAuth() {
  if (web.header(CSRF_HEADER) != CSRF_VALUE) {
    web.send(403, "application/json", "{\"ok\":false,\"error\":\"missing X-Requested-With header\"}");
    return false;
  }
  if (web.authenticate(WEB_USER, WEB_PASS)) return true;
  web.requestAuthentication();
  return false;
}

static void sendJson(int code, const JsonDocument& doc) {
  String out;
  serializeJson(doc, out);
  web.send(code, "application/json", out);
}

static void sendError(int code, const String& msg) {
  JsonDocument doc;
  doc["ok"] = false;
  doc["error"] = msg;
  sendJson(code, doc);
}

static void fillStatus(JsonObject o) {
  const BusStatus s = bus::status();
  o["running"] = s.running;
  o["run_count"] = s.runCount;
  o["protocol"] = bus::protoName(s.cfg.proto);
  o["speed_hz"] = s.cfg.speedHz;
  o["actual_hz"] = s.actualHz;
  o["pattern"] = bus::patternName(s.cfg.pattern);
  o["spi_mode"] = s.cfg.spiMode;
  o["duration_ms"] = s.cfg.durationMs;
  o["burst"] = s.cfg.burst;
  o["gap_us"] = s.cfg.gapUs;
  o["i2c_addr"] = s.cfg.i2cAddr;
  o["loopback"] = s.cfg.loopback;
  o["frames"] = s.frames;
  o["bytes"] = s.bytes;
  o["nacks"] = s.nacks;
  o["loop_checked"] = s.loopChecked;
  o["loop_errors"] = s.loopErrors;
  o["elapsed_ms"] = s.elapsedMs;
}

static void handleInfo() {
  JsonDocument doc;
  doc["fw"] = FW_VERSION;
  doc["chip"] = ESP.getChipModel();
  doc["chip_rev"] = ESP.getChipRevision();
  doc["mac"] = WiFi.macAddress();
  doc["ip"] = WiFi.localIP().toString();
  doc["rssi"] = WiFi.RSSI();
  doc["uptime_s"] = millis() / 1000;
  doc["free_heap"] = ESP.getFreeHeap();
  JsonObject pins = doc["pins"].to<JsonObject>();
  JsonObject u = pins["uart"].to<JsonObject>();
  u["tx"] = bus::UART_TX; u["rx"] = bus::UART_RX;
  JsonObject s = pins["spi"].to<JsonObject>();
  s["sclk"] = bus::SPI_SCLK; s["mosi"] = bus::SPI_MOSI; s["miso"] = bus::SPI_MISO; s["cs"] = bus::SPI_CS;
  JsonObject i = pins["i2c"].to<JsonObject>();
  i["sda"] = bus::I2C_SDA; i["scl"] = bus::I2C_SCL;
  JsonObject is = pins["i2c_slave"].to<JsonObject>();
  is["sda"] = bus::I2C_SLAVE_SDA; is["scl"] = bus::I2C_SLAVE_SCL;
  JsonObject r = doc["ranges"].to<JsonObject>();
  JsonArray ru = r["uart"].to<JsonArray>(); ru.add(bus::UART_MIN); ru.add(bus::UART_MAX);
  JsonArray rs = r["spi"].to<JsonArray>();  rs.add(bus::SPI_MIN);  rs.add(bus::SPI_MAX);
  JsonArray ri = r["i2c"].to<JsonArray>();  ri.add(bus::I2C_MIN);  ri.add(bus::I2C_MAX);
  sendJson(200, doc);
}

static void handleStatus() {
  JsonDocument doc;
  fillStatus(doc.to<JsonObject>());
  sendJson(200, doc);
}

static void handleRun() {
  if (!requireAuth()) return;
  JsonDocument req;
  if (deserializeJson(req, web.arg("plain"))) return sendError(400, "invalid JSON body");

  BusConfig c;
  if (!bus::parseProto(req["protocol"] | "", c.proto)) return sendError(400, "protocol must be uart / spi / i2c");
  if (!bus::parsePattern(req["pattern"] | "0x55", c.pattern)) return sendError(400, "pattern must be 0x55 / counter / prbs7");
  c.speedHz = req["speed_hz"] | 0;
  c.spiMode = req["spi_mode"] | 0;
  c.durationMs = std::min<uint32_t>(req["duration_ms"] | 5000, 600000);
  c.burst = std::min<uint32_t>(req["burst"] | 64, 65535);
  c.gapUs = std::min<uint32_t>(req["gap_us"] | 200, 60000);
  c.i2cAddr = req["i2c_addr"] | 0x28;
  c.loopback = req["loopback"] | false;

  String err;
  if (!bus::start(c, err)) return sendError(400, err);
  Serial.printf("[run] %s %lu Hz (actual %lu) pattern=%s %lu ms\n", bus::protoName(c.proto),
                (unsigned long)c.speedHz, (unsigned long)bus::status().actualHz,
                bus::patternName(c.pattern), (unsigned long)c.durationMs);
  JsonDocument doc;
  doc["ok"] = true;
  fillStatus(doc["status"].to<JsonObject>());
  sendJson(200, doc);
}

static void handleStop() {
  if (!requireAuth()) return;
  bus::stop();
  JsonDocument doc;
  doc["ok"] = true;
  fillStatus(doc["status"].to<JsonObject>());
  sendJson(200, doc);
}

static void handleRoot() {
  String html =
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>Bus Bench</title><body style='font:15px system-ui,sans-serif;max-width:560px;margin:32px auto;padding:0 16px'>"
    "<h2>Bus Bench 韌體 " FW_VERSION "</h2>"
    "<p>這是 ESP32 端的 API。測試請使用電腦上的 Bus Bench 儀表板。</p>"
    "<ul><li><a href=/api/info>/api/info</a></li><li><a href=/api/status>/api/status</a></li></ul></body>";
  web.send(200, "text/html; charset=utf-8", html);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.printf("\n[bus-bench] booting fw %s\n", FW_VERSION);
  // I2C 沒有從端時 IDF 會對每次 NACK 印錯誤，關掉
  esp_log_level_set("i2c.master", ESP_LOG_NONE);

  pinMode(WIFI_RESET_PIN, INPUT_PULLUP);
  if (digitalRead(WIFI_RESET_PIN) == LOW) {
    delay(60);
    if (digitalRead(WIFI_RESET_PIN) == LOW) {
      prefs.begin("wifi", false); prefs.clear(); prefs.end();
      Serial.println("[setup] BOOT held -> cleared saved WiFi");
    }
  }

  if (!connectWiFi()) startConfigPortal();  // 不會返回；儲存帳密後會重開機
  Serial.printf("WiFi up: %s\n", WiFi.localIP().toString().c_str());
  WiFi.setAutoReconnect(true);

  if (MDNS.begin(HOSTNAME)) {
    MDNS.addService("http", "tcp", 80);
    Serial.printf("api: http://%s.local/api/info\n", HOSTNAME);
  }
  if (strcmp(WEB_PASS, "CHANGE_ME_WEB_PASSWORD") == 0)
    Serial.println("[WARN] secrets.h WEB_PASS is still the placeholder");

  const char* headers[] = {CSRF_HEADER};
  web.collectHeaders(headers, 1);
  web.on("/", HTTP_GET, handleRoot);
  web.on("/api/info", HTTP_GET, handleInfo);
  web.on("/api/status", HTTP_GET, handleStatus);
  web.on("/api/run", HTTP_POST, handleRun);
  web.on("/api/stop", HTTP_POST, handleStop);
  web.onNotFound([]() { sendError(404, "not found"); });
  web.begin();
}

void loop() {
  web.handleClient();
  delay(2);
}
