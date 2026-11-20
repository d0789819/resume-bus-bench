#include "bus_gen.h"

#include <HardwareSerial.h>
#include <SPI.h>
#include <Wire.h>

#include <atomic>

namespace {

BusConfig cfg;
TaskHandle_t genTaskHandle = nullptr;
std::atomic<bool> stopReq{false};
std::atomic<bool> running{false};

uint32_t runCount = 0;
uint32_t actualHz = 0;
uint32_t tStart = 0, tEnd = 0;
// 產生器任務寫入、網頁伺服器讀取
std::atomic<uint32_t> frames{0}, bytes{0}, nacks{0}, loopChecked{0}, loopErrors{0};
std::atomic<uint32_t> slaveRx{0};

SPIClass spi(VSPI);
uint8_t txBuf[256], rxBuf[256];
uint8_t prbsState = 0x7F;
uint8_t counterVal = 0;

// 依樣式填入資料
void fillPattern(uint8_t* b, size_t n) {
  for (size_t i = 0; i < n; i++) {
    switch (cfg.pattern) {
      case Pattern::P55:
        b[i] = 0x55;
        break;
      case Pattern::Counter:
        b[i] = counterVal++;
        break;
      case Pattern::Prbs7: {  // x^7 + x^6 + 1
        uint8_t v = 0;
        for (int k = 0; k < 8; k++) {
          uint8_t nb = ((prbsState >> 6) ^ (prbsState >> 5)) & 1;
          prbsState = ((prbsState << 1) | nb) & 0x7F;
          v = (v << 1) | nb;
        }
        b[i] = v;
        break;
      }
    }
  }
}

void onSlaveReceive(int) {
  while (Wire1.available()) { Wire1.read(); slaveRx++; }
}

bool setupBus(String& err) {
  switch (cfg.proto) {
    case Proto::Uart:
      Serial2.setRxBufferSize(1024);
      // 沒接迴路線時不開 RX，避免浮接腳收到雜訊
      Serial2.begin(cfg.speedHz, SERIAL_8N1, cfg.loopback ? bus::UART_RX : -1, bus::UART_TX);
      actualHz = Serial2.baudRate();
      while (Serial2.available()) Serial2.read();
      return true;

    case Proto::Spi:
      if (!spi.begin(bus::SPI_SCLK, cfg.loopback ? bus::SPI_MISO : -1, bus::SPI_MOSI, -1)) {
        err = "SPI begin failed";
        return false;
      }
      pinMode(bus::SPI_CS, OUTPUT);
      digitalWrite(bus::SPI_CS, HIGH);
      spi.setFrequency(cfg.speedHz);
      actualHz = spiClockDivToFrequency(spi.bus(), spi.getClockDivider());
      return true;

    case Proto::I2c:
      if (cfg.loopback) {
        slaveRx = 0;
        Wire1.onReceive(onSlaveReceive);
        if (!Wire1.begin(cfg.i2cAddr, bus::I2C_SLAVE_SDA, bus::I2C_SLAVE_SCL, cfg.speedHz)) {
          err = "I2C slave (Wire1) begin failed";
          return false;
        }
      }
      // 使用 ESP32 內部上拉（約 45 kΩ）；外接上拉電阻時兩者並聯
      if (!Wire.begin(bus::I2C_SDA, bus::I2C_SCL, cfg.speedHz)) {
        err = "I2C master (Wire) begin failed";
        if (cfg.loopback) Wire1.end();
        return false;
      }
      Wire.setTimeOut(20);
      actualHz = Wire.getClock();
      return true;

    default:
      err = "unknown protocol";
      return false;
  }
}

void teardownBus() {
  switch (cfg.proto) {
    case Proto::Uart:
      Serial2.end();
      break;
    case Proto::Spi:
      spi.end();
      pinMode(bus::SPI_CS, INPUT);
      break;
    case Proto::I2c:
      Wire.end();
      if (cfg.loopback) Wire1.end();
      break;
    default:
      break;
  }
}

void sendBurstUart(size_t n) {
  Serial2.write(txBuf, n);
  Serial2.flush();  // 等到全部送出
  if (!cfg.loopback) return;
  // 讀回並比對（GPIO17 TX 以杜邦線接到 GPIO16 RX）
  const uint32_t waitUs = 2000 + (uint32_t)((uint64_t)n * 10ULL * 1000000ULL / cfg.speedHz);
  const uint32_t t0 = micros();
  size_t got = 0;
  while (got < n && micros() - t0 < waitUs) {
    if (Serial2.available()) rxBuf[got++] = (uint8_t)Serial2.read();
    else delayMicroseconds(20);
  }
  loopChecked += n;
  for (size_t i = 0; i < n; i++) {
    if (i >= got || rxBuf[i] != txBuf[i]) loopErrors++;
  }
}

void sendBurstSpi(size_t n) {
  spi.beginTransaction(SPISettings(cfg.speedHz, MSBFIRST, cfg.spiMode));
  digitalWrite(bus::SPI_CS, LOW);
  spi.transferBytes(txBuf, rxBuf, n);
  digitalWrite(bus::SPI_CS, HIGH);
  spi.endTransaction();
  if (!cfg.loopback) return;
  // GPIO23 MOSI 以杜邦線接到 GPIO19 MISO
  loopChecked += n;
  for (size_t i = 0; i < n; i++) {
    if (rxBuf[i] != txBuf[i]) loopErrors++;
  }
}

void sendBurstI2c(size_t n) {
  Wire.beginTransmission(cfg.i2cAddr);
  Wire.write(txBuf, n);
  // 沒有從端時位址會被 NACK，只送出位址框，但波形仍可量測
  if (Wire.endTransmission() != 0) nacks++;
}

void genTask(void*) {
  const size_t n = cfg.burst;
  uint32_t lastYield = millis();
  while (!stopReq && (cfg.durationMs == 0 || millis() - tStart < cfg.durationMs)) {
    fillPattern(txBuf, n);
    switch (cfg.proto) {
      case Proto::Uart: sendBurstUart(n); break;
      case Proto::Spi:  sendBurstSpi(n);  break;
      case Proto::I2c:  sendBurstI2c(n);  break;
      default: break;
    }
    frames++;
    bytes += n;
    if (cfg.gapUs >= 1000) vTaskDelay(pdMS_TO_TICKS(cfg.gapUs / 1000));
    else if (cfg.gapUs) delayMicroseconds(cfg.gapUs);
    // 定期讓出 CPU，避免網頁伺服器（loop）沒有機會執行
    if (millis() - lastYield >= 10) { vTaskDelay(1); lastYield = millis(); }
  }
  teardownBus();
  tEnd = millis();
  running = false;
  genTaskHandle = nullptr;
  vTaskDelete(nullptr);
}

bool inRange(uint32_t v, uint32_t lo, uint32_t hi) { return v >= lo && v <= hi; }

}  // namespace

namespace bus {

bool start(const BusConfig& c, String& err) {
  switch (c.proto) {
    case Proto::Uart:
      if (!inRange(c.speedHz, UART_MIN, UART_MAX)) { err = "UART speed_hz must be 300..5000000"; return false; }
      break;
    case Proto::Spi:
      if (!inRange(c.speedHz, SPI_MIN, SPI_MAX)) { err = "SPI speed_hz must be 100000..40000000"; return false; }
      if (c.spiMode > 3) { err = "spi_mode must be 0..3"; return false; }
      break;
    case Proto::I2c:
      if (!inRange(c.speedHz, I2C_MIN, I2C_MAX)) { err = "I2C speed_hz must be 10000..1000000"; return false; }
      if (c.i2cAddr < 0x08 || c.i2cAddr > 0x77) { err = "i2c_addr must be 0x08..0x77"; return false; }
      break;
    default:
      err = "protocol must be uart / spi / i2c";
      return false;
  }
  if (c.burst < 1 || c.burst > sizeof(txBuf)) { err = "burst must be 1..256"; return false; }

  stop();
  if (running) { err = "previous run is still stopping, retry later"; return false; }
  cfg = c;
  frames = bytes = nacks = loopChecked = loopErrors = 0;
  slaveRx = 0;
  counterVal = 0;
  prbsState = 0x7F;
  actualHz = 0;
  if (!setupBus(err)) return false;

  stopReq = false;
  running = true;
  runCount++;
  tStart = millis();
  tEnd = 0;
  // 與 loop()（網頁伺服器）同在 core 1；Wi-Fi 在 core 0
  if (xTaskCreatePinnedToCore(genTask, "busgen", 4096, nullptr, 1, &genTaskHandle, 1) != pdPASS) {
    running = false;
    teardownBus();
    err = "failed to create generator task";
    return false;
  }
  return true;
}

void stop() {
  if (!running) return;
  stopReq = true;
  const uint32_t t0 = millis();
  while (running && millis() - t0 < 1500) delay(5);
}

BusStatus status() {
  BusStatus s{};
  s.running = running;
  s.runCount = runCount;
  s.actualHz = actualHz;
  s.frames = frames;
  s.bytes = bytes;
  s.nacks = nacks;
  s.loopChecked = loopChecked;
  s.loopErrors = loopErrors;
  if (cfg.proto == Proto::I2c && cfg.loopback) {
    // I2C 迴路：比較主端送出的資料量與從端收到的資料量
    s.loopChecked = bytes;
    s.loopErrors = bytes > slaveRx ? bytes - slaveRx : 0;
  }
  s.elapsedMs = runCount == 0 ? 0 : (running ? millis() - tStart : tEnd - tStart);
  s.cfg = cfg;
  return s;
}

const char* protoName(Proto p) {
  switch (p) {
    case Proto::Uart: return "uart";
    case Proto::Spi:  return "spi";
    case Proto::I2c:  return "i2c";
    default:          return "none";
  }
}

bool parseProto(const char* s, Proto& out) {
  if (!s) return false;
  if (!strcasecmp(s, "uart")) { out = Proto::Uart; return true; }
  if (!strcasecmp(s, "spi"))  { out = Proto::Spi;  return true; }
  if (!strcasecmp(s, "i2c"))  { out = Proto::I2c;  return true; }
  return false;
}

const char* patternName(Pattern p) {
  switch (p) {
    case Pattern::Counter: return "counter";
    case Pattern::Prbs7:   return "prbs7";
    default:               return "0x55";
  }
}

bool parsePattern(const char* s, Pattern& out) {
  if (!s || !*s || !strcasecmp(s, "0x55")) { out = Pattern::P55; return true; }
  if (!strcasecmp(s, "counter")) { out = Pattern::Counter; return true; }
  if (!strcasecmp(s, "prbs7"))   { out = Pattern::Prbs7;   return true; }
  return false;
}

}  // namespace bus
