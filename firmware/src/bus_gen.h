#pragma once
// 匯流排訊號產生器：依設定持續送出 UART / SPI / I2C 訊號，讓示波器擷取量測。
#include <Arduino.h>

enum class Proto : uint8_t { None = 0, Uart, Spi, I2c };
enum class Pattern : uint8_t { P55 = 0, Counter, Prbs7 };

struct BusConfig {
  Proto    proto      = Proto::None;
  uint32_t speedHz    = 0;            // UART 鮑率 / SPI SCLK / I2C SCL
  Pattern  pattern    = Pattern::P55; // 0x55 = UART 會變成 鮑率/2 的方波
  uint8_t  spiMode    = 0;            // 0..3
  uint32_t durationMs = 5000;         // 0 = 一直送到 stop
  uint16_t burst      = 64;           // 每次突發的位元組數
  uint16_t gapUs      = 200;          // 突發之間的閒置時間（方便示波器觸發）
  uint8_t  i2cAddr    = 0x28;
  bool     loopback   = false;        // 有接迴路線時才比對回讀資料
};

struct BusStatus {
  bool      running;
  uint32_t  runCount;     // 第幾次啟動（開機後累計）
  uint32_t  actualHz;     // 周邊實際設定的速度（除頻後）
  uint32_t  frames;
  uint32_t  bytes;
  uint32_t  nacks;        // I2C 沒有從端回應的次數
  uint32_t  loopChecked;  // 迴路比對的位元組數
  uint32_t  loopErrors;
  uint32_t  elapsedMs;
  BusConfig cfg;
};

namespace bus {
// 腳位（ESP32 DevKit，避開 strapping 腳 0/2/12/15 與 Flash 腳 6-11）
constexpr int UART_TX = 17, UART_RX = 16;
constexpr int SPI_SCLK = 18, SPI_MISO = 19, SPI_MOSI = 23, SPI_CS = 5;
constexpr int I2C_SDA = 21, I2C_SCL = 22;
constexpr int I2C_SLAVE_SDA = 25, I2C_SLAVE_SCL = 26;

// 速度範圍
constexpr uint32_t UART_MIN = 300,    UART_MAX = 5000000;
constexpr uint32_t SPI_MIN  = 100000, SPI_MAX  = 40000000;
constexpr uint32_t I2C_MIN  = 10000,  I2C_MAX  = 1000000;

bool start(const BusConfig& cfg, String& err);  // 若正在執行，會先停止
void stop();                                    // 等待產生器結束（最多約 1 秒）
BusStatus status();

const char* protoName(Proto p);
bool parseProto(const char* s, Proto& out);
const char* patternName(Pattern p);
bool parsePattern(const char* s, Pattern& out);
}  // namespace bus
