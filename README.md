# Bus Bench：ESP32 匯流排訊號品質測試台

<img width="2500" height="1318" alt="bandicam 2026-11-20 16-09-07-869" src="https://github.com/user-attachments/assets/b39259d5-6c7d-4f3b-af95-5dfd83170c4c" />

<img width="2291" height="1423" alt="bandicam 2026-11-20 16-13-29-966" src="https://github.com/user-attachments/assets/28d93d91-e79f-4cce-adaa-6a39252dbe87" />

ESP32 依指令送出 UART / SPI / I2C 訊號 → 後端用 VISA 控制 Tektronix 示波器擷取波形 →
計算頻率、上升 / 下降時間、過衝、工作週期等 → 依規格判定 PASS/FAIL → 存進 PostgreSQL →
網頁儀表板顯示波形、量測值、趨勢與歷史紀錄。

```
          Wi-Fi (REST)                        USB / LAN (VISA, SCPI)
 ESP32 ◄────────────────── 後端 FastAPI ──────────────────► Tektronix 示波器
   │  GPIO 17/18/21/22/23      │                                 ▲
   └───────────── 探棒 ────────┼─────────────────────────────────┘
                               ├──► PostgreSQL（Docker）
                               └──► 儀表板 http://127.0.0.1:8000
```

沒有示波器時可用**模擬示波器**（`SCOPE_DRIVER=sim`），沒有 ESP32 時可用**模擬 ESP32**（`ESP32_MOCK=1`），
整套流程照樣能跑。

## 目錄

| 路徑 | 內容 |
|---|---|
| `firmware/` | ESP32 韌體（PlatformIO / Arduino）：訊號產生器 + REST API |
| `server/app/analysis.py` | 波形量測演算法（numpy，與示波器型號無關） |
| `server/app/limits.py` | 規格上下限與 PASS/FAIL 判定 |
| `server/app/scope/` | 示波器驅動：`sim.py` 模擬、`tek.py` Tektronix |
| `server/app/runner.py` | 測試流程與速度掃描 |
| `server/app/static/` | 儀表板（HTML / JS / Chart.js） |
| `server/sql/` | 資料表定義 |
| `docs/PLAN.md` | 實作計畫 |

## 快速開始（Windows / PowerShell）

需要：Python 3.11、Docker Desktop、ESP32 DevKit（傳統 ESP32，4 MB Flash）。

```powershell
# 1. Python 環境
python -m venv .venv
.venv\Scripts\python -m pip install -r server\requirements.txt platformio

# 2. 設定檔（改掉裡面的密碼）
copy .env.example .env
copy firmware\src\secrets.example.h firmware\src\secrets.h
#    secrets.h 的 WEB_PASS 必須等於 .env 的 ESP32_PASS

# 3. 資料庫
docker compose up -d

# 4. 燒錄韌體（COM 埠依實際情況）
cd firmware
..\.venv\Scripts\pio run -e esp32dev -t upload --upload-port COM4
cd ..

# 5. 啟動後端與儀表板 → 瀏覽器開 http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --app-dir server --port 8000

# 6. 單元測試
cd server
..\.venv\Scripts\python -m pytest
```

ESP32 開機會先用 NVS 裡已存的 Wi-Fi 帳密；沒有或連不上時開設定熱點 `BusBench-XXXX`，
用手機連上後選擇 Wi-Fi、輸入密碼即可。開機時按住 BOOT 鍵可清除已存的 Wi-Fi。

## 探棒接線

只需要 ESP32 開發板與示波器探棒，**不必額外零件**。探棒設 10X，探針勾在排針上，接地夾接開發板 GND。

| 協定 | CH1（主通道） | CH2（副通道） | 觸發 |
|---|---|---|---|
| UART | TX = GPIO 17 | — | CH1 下降緣（起始位元） |
| SPI | SCLK = GPIO 18 | MOSI = GPIO 23 | CH1 前緣 |
| I2C | SCL = GPIO 22 | SDA = GPIO 21 | CH2 下降緣（起始條件） |

選用的迴路驗證（勾選儀表板「已接迴路線」）：UART 17→16、SPI 23→19（MISO）、I2C 21/22 ↔ 25/26。

只有一支探棒時，把 `.env` 的 `SCOPE_CH_SECONDARY` 留空，只量主通道。

## 連接 Tektronix 示波器

1. **USB**：安裝 VISA 驅動（NI-VISA 或 Tektronix 的 TekVISA），接上 USB-B 線。
   **LAN**：示波器設定同網段 IP，`.env` 設 `VISA_BACKEND=@py`（用 pyvisa-py，不必安裝 VISA）。
2. 找出資源字串：
   ```powershell
   .venv\Scripts\python -c "import pyvisa; print(pyvisa.ResourceManager().list_resources())"
   # LAN 用：pyvisa.ResourceManager('@py')
   ```
3. 修改 `.env`：
   ```ini
   SCOPE_DRIVER=tek
   SCOPE_RESOURCE=USB0::0x0699::0x....::INSTR      # 或 TCPIP0::192.168.0.x::inst0::INSTR
   ```
4. 重新啟動後端，儀表板右上角會顯示示波器的 `*IDN?`。

驅動以 Tek 共通的 `DATa` / `WFMOutpre` / `CURVe?` 傳輸波形；設定指令新舊系列寫法不同時會自動改用另一種。
示波器內建量測（`MEASUrement:IMMed` 或 `MEASUrement:ADDMEAS`）只用來和 Python 計算結果交叉比對，
在結果表中標示「示波器內建」。實機若有指令不相容，依該型號的程式設計手冊調整 `server/app/scope/tek.py`。

## 規格與判定

| 協定 | 項目 | 預設規格 | 來源 |
|---|---|---|---|
| I2C | 上升時間 30–70% | Standard ≤ 1000 ns、Fast ≤ 300 ns、Fast-mode Plus ≤ 120 ns | UM10204 |
| I2C | 下降時間 70–30% | ≤ 300 / 300 / 120 ns | UM10204 |
| I2C | SCL 頻率 | ≤ 該模式最高頻率 | UM10204 |
| I2C | 低 / 高準位 | VOL ≤ 0.4 V、VIH ≥ 0.7 VDD | UM10204 |
| UART | 鮑率誤差 | ±2 % | 一般建議值 |
| SPI | SCLK 頻率誤差（對 ESP32 實際設定）、工作週期 | ±2 %、45–55 % | 一般建議值 |
| UART / SPI | 高 / 低準位、過衝 | ≥ 2.4 V、≤ 0.4 V、≤ 20 % | LVTTL / 一般建議值 |

要調整時建立 `server/limits_override.json`：

```json
{ "uart": { "TX": { "baud_error_pct": [-3, 3] } } }
```

## ESP32 REST API

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/info` | 韌體版本、IP、腳位、速度範圍 |
| GET | `/api/status` | 執行狀態、已送框數、I2C NACK 數、迴路錯誤數 |
| POST | `/api/run` | `{protocol, speed_hz, pattern, spi_mode, duration_ms, burst, gap_us, i2c_addr, loopback}` |
| POST | `/api/stop` | 停止 |

POST 需要 HTTP Basic Auth（`secrets.h` 的 `WEB_USER` / `WEB_PASS`），以及標頭 `X-Requested-With: busbench`。

## 常見問題

- **mDNS 很慢**：Windows 解析 `busbench.local` 約需 3 秒，所以 `.env` 直接用 IP。建議在路由器替 ESP32 設定 DHCP 保留，避免 IP 變動。
- **不要執行 `erase_flash`**：會清掉 NVS 裡存的 Wi-Fi 帳密。一般 `upload` 不會清除。
- **UART 的「ESP32 實際設定」**：這是韌體 `Serial2.baudRate()` 回報的值，可能與實際輸出略有差異；真正的鮑率以示波器量測為準。
- **SPI 要求 30 MHz 卻是 26.67 MHz**：ESP32 的 SPI 時脈是 80 MHz 除以整數，這正是測試台要呈現的現象。
- **I2C 用內部上拉在 400 kHz 不合格**：內部上拉約 45 kΩ 太弱，上升時間過長；外接 2.2–4.7 kΩ 上拉電阻即可改善。
- **燒回廣告攔截韌體**：在 `..\esp32-c3-adblock` 執行
  `..\bus-bench\.venv\Scripts\pio run -e esp32dev -t upload --upload-port COM4`，再執行一次並把 `upload` 換成 `uploadfs`（黑名單）。

## 建議添購（選用）

| 零件 | 用途 |
|---|---|
| 母對母杜邦線 ×10 | 迴路驗證 |
| 電阻 1k / 2.2k / 4.7k / 10k | 比較 I2C 上拉電阻對上升時間的影響 |
| 迷你麵包板 | 插電阻、換值 |
| AT24C256 或 W25Q64 模組 | 當真實從端裝置 |
