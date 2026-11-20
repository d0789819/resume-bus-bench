# ESP32 匯流排訊號品質測試台（Bus Bench）實作計畫

## Context（背景）
- 目前這片 ESP32（ESP32-D0WD，4MB，CH340 / COM4）燒的是 DNS 廣告攔截韌體，已連上實驗室 Wi-Fi（192.168.0.83），Wi-Fi 帳密存在 NVS。
- 使用者決定把它改做「#2 匯流排訊號品質測試台」：ESP32 依網頁指令送出 UART / SPI / I2C 訊號 → Python 後端用 VISA 控制 Tektronix 示波器擷取波形 → 計算頻率、上升時間、過衝、工作週期等 → 依規格判定 PASS/FAIL → 存進 PostgreSQL → Web 儀表板顯示結果、波形與趨勢。
- 已確認：示波器為 **Tektronix**（型號、連線方式未定 → 先用「模擬示波器」開發，USB / LAN 兩種都支援）；後端用 **Python FastAPI**；零件**只有 ESP32 開發板**，以最少元件為原則。
- 本機環境（唯讀檢查結果）：Python 3.11、Docker 29.8（引擎運作中）、Node 24、.NET 8/10、PlatformIO 6.2 已有（舊 venv）；**沒有** PostgreSQL、**沒有** VISA、**沒有**偵測到示波器。
- 預期成果：不接任何額外零件，只用 ESP32 + 示波器探棒，就能自動量測三種匯流排並留下可追溯的測試紀錄。

---

## 零件清單（使用者要求列出缺少的元件）

| 類別 | 項目 | 用途 | 狀態 |
|---|---|---|---|
| 必要 | ESP32 開發板 + Micro-USB 線 | 訊號產生器、供電 | ✅ 已有 |
| 必要 | Tektronix 示波器 + 探棒 **至少 1 支，建議 2 支** | 同時量 SCL+SDA 或 SCLK+MOSI | 需確認探棒數 |
| 必要 | 示波器連電腦的線：**USB-B 線**（印表機線）或**網路線** | VISA 控制 | 需確認 |
| 建議 | 母對母杜邦線 ×10 | 迴路驗證：UART TX→RX、SPI MOSI→MISO、I2C 主↔從 | ❌ 缺少（約數十元） |
| 建議 | 電阻 1k / 2.2k / 4.7k / 10k 各數顆 | I2C 外部上拉比較「上拉電阻 vs 上升時間」，最有說服力的圖表 | ❌ 缺少（約數十元） |
| 建議 | 迷你麵包板 | 插電阻、方便換值 | ❌ 缺少 |
| 選購 | AT24C256（I2C）或 W25Q64（SPI）模組 | 當真實從端裝置 | 可不買 |

**零額外元件即可做的量測**（探棒直接勾在開發板排針上，接地夾接 GND）：
- UART：TX 連續送 `0x55` → 方波頻率 = 鮑率 / 2 → 算鮑率誤差。
- SPI：只當主端送時脈與資料（不需從端）→ SCLK 頻率準確度、工作週期、過衝。
- I2C：用 ESP32 內部上拉（約 45 kΩ）送位址框（無從端時會 NACK，不影響波形量測）→ 上升時間。預期內部上拉在 400 kHz 會超出 I2C 規格，正好做成 FAIL 案例（實際以量測為準）。

---

## 腳位配置（ESP32 DevKit，避開 strapping 腳 0/2/12/15 與 Flash 腳 6–11）

| 匯流排 | 訊號 | GPIO | 示波器 |
|---|---|---|---|
| UART2 | TX / RX | 17 / 16 | CH1 = TX |
| SPI (VSPI, IOMUX) | SCLK / MOSI / MISO / CS | 18 / 23 / 19 / 5 | CH1 = SCLK、CH2 = MOSI |
| I2C0（主端） | SDA / SCL | 21 / 22 | CH1 = SCL、CH2 = SDA |
| I2C1（從端，選用迴路） | SDA / SCL | 25 / 26（以杜邦線接到 21 / 22） | — |

---

## 專案結構（新建 `bus-bench/`）

```
bus-bench/
├─ docs/PLAN.md                 本計畫副本（給使用者保存）
├─ docker-compose.yml           PostgreSQL 17（只綁 127.0.0.1:5432）
├─ .env.example / .env          DB 連線、ESP32 位址與帳密、示波器資源字串（.env 不進 git）
├─ firmware/                    PlatformIO 專案
│  ├─ platformio.ini
│  └─ src/ main.cpp、wifi_setup.h、bus_gen.h/.cpp、secrets.example.h
├─ server/
│  ├─ requirements.txt          fastapi uvicorn pyvisa pyvisa-py psycopg[binary,pool] httpx numpy pydantic-settings pytest
│  ├─ sql/001_init.sql
│  ├─ app/ main.py config.py db.py esp32_client.py runner.py analysis.py limits.py
│  ├─ app/scope/ base.py sim.py tek.py
│  ├─ app/static/ index.html app.js style.css
│  └─ tests/ test_analysis.py test_limits.py test_runner_sim.py
└─ README.md                    安裝、接線圖、操作步驟
```

---

## 實作步驟（每步：怎麼做 → 完成標準）

### 步驟 0｜專案資料夾與環境
- 用 `request_directory` 讓使用者選一個**持久、短路徑**的資料夾（建議 `C:\esp\`，避開這次遇到的長路徑問題），再 `change_directory` 過去。App 會把目前的 `esp32-c3-adblock/` 一起搬過去，**廣告攔截原始碼保留**，日後可隨時燒回。
- 舊 `.venv` 搬移後路徑失效 → 在新資料夾重建 `.venv`，安裝 platformio 與 `server/requirements.txt`。PlatformIO 平台快取在 `~/.platformio`，不會重新下載。
- `git init`，把本計畫存成 `docs/PLAN.md`，`.gitignore` 排除 `.env`、`secrets.h`、`.pio/`、`.venv/`。
- **完成標準**：`pio --version`、`python -c "import fastapi, pyvisa, numpy"` 都成功。

### 步驟 1｜PostgreSQL（Docker）
- `docker-compose.yml`：`postgres:17-alpine`、具名 volume、`127.0.0.1:5432`、帳密由 `.env` 提供（隨機產生）、掛載 `server/sql/` 到 `/docker-entrypoint-initdb.d` 自動建表。
- `001_init.sql` 資料表：
  - `test_run`（id, started_at, protocol, speed_hz, params jsonb, scope_idn, esp32_fw, verdict, note）
  - `measurement`（run_id FK, channel, name, value, unit, limit_min, limit_max, pass）
  - `waveform`（run_id FK, channel, x_zero, x_incr, y_unit, points real[]）
  - 索引：`test_run(protocol, speed_hz, started_at)`
- **完成標準**：`docker compose up -d` 後，`docker compose exec db psql -U bench -c '\dt'` 列出三張表。

### 步驟 2｜ESP32 韌體（取代廣告攔截韌體）
- `platformio.ini`：沿用廣告攔截專案已驗證可編譯的 pioarduino 平台版本（`esp32-c3-adblock/platformio.ini` 的 `platform = …55.03.37…`）與 `[env:esp32dev]`；`lib_deps = bblanchon/ArduinoJson@^7`；使用**預設分割表**（NVS 同樣在 0x9000，所以 Wi-Fi 帳密會保留）。
- **重用**廣告攔截專案的程式（複製到 `wifi_setup.h` 並改名）：
  - `hasCreds()` / `connectWiFi()`（`esp32-c3-adblock/src/main.cpp:658`、`:662`）：沿用相同 NVS 命名空間 `"wifi"` 與鍵 `ssid`/`pass` → **開機直接連上原本的 Wi-Fi，不必重設**。
  - `startConfigPortal()` / `handleWifiSave()`（`:722`、`:710`）：連不上時的設定熱點，改名 `BusBench-XXXX`。
  - `requireAuth()` + `CSRF_HEADER`（`:440`、`:438`）：保護會改變狀態的 API。
  - mDNS 寫法（`:800`）→ 主機名 `busbench`。
- `bus_gen.cpp`：FreeRTOS 任務以「突發 + 間隔」方式持續送訊號（間隔讓示波器穩定觸發）：
  - UART：`Serial2.begin(baud, SERIAL_8N1, 16, 17)`，樣式 `0x55` / 計數 / PRBS。
  - SPI：`SPIClass(VSPI)` + `SPISettings(hz, MSBFIRST, mode 0–3)`。
  - I2C：`Wire.begin(21, 22, hz)`（內部上拉），重複送位址框；接線後可用 `Wire1` 當從端做資料驗證。
  - 迴路驗證（有杜邦線時）：比對回讀資料、累計錯誤數。
- REST API：
  - `GET /api/info`：韌體版本、IP、腳位。
  - `GET /api/status`：是否執行中、已送框數、迴路錯誤數。
  - `POST /api/run`（需驗證）：`{protocol, speed_hz, pattern, spi_mode, duration_ms}`。
  - `POST /api/stop`（需驗證）。
- 燒錄：`pio run -e esp32dev -t upload --upload-port COM4`（**不要** `erase_flash`，否則 Wi-Fi 設定會消失）。
- **完成標準**：序列埠顯示 `WiFi up: 192.168.0.x`；`curl http://busbench.local/api/info` 有回應；未帶帳密 `POST /api/run` 回 401。

### 步驟 3｜後端骨架 + 模擬示波器
- `config.py`：pydantic-settings 讀 `.env`（`DATABASE_URL`、`ESP32_URL`、`ESP32_USER/PASS`、`SCOPE_DRIVER=sim|tek`、`SCOPE_RESOURCE`）。
- `scope/base.py`：`ScopeDriver` 介面：`idn()`、`setup(channels, v_scale, h_scale, trigger)`、`single()`、`fetch_waveform(ch) -> (t0, dt, volts[])`、`native_measure(ch, kind)`（選用）。
- `scope/sim.py`：依測試參數合成波形（推挽輸出邊緣約數 ns；I2C 以 RC 指數邊緣模擬上拉電阻 × 匯流排電容，加雜訊），讓整個流程在沒有硬體時就能跑。
- `analysis.py`（numpy，**與示波器型號無關**，主要量測都在這裡算）：直方圖找高/低準位 → 10/50/90% 與 30/70% 門檻 → 內插求交越點 → 頻率、週期、工作週期、上升/下降時間、過衝、Vpp、VOL。
- `limits.py`：規格上下限：
  - I2C（UM10204，30–70%）：Standard 100k tr ≤ 1000 ns、Fast 400k tr ≤ 300 ns、Fast-mode Plus 1M tr ≤ 120 ns；tf 分別 ≤ 300 / 300 / 120 ns。
  - UART：鮑率誤差 ≤ ±2%。
  - SPI：頻率誤差、工作週期 45–55%。
  - 上下限皆可在設定中調整。
- `runner.py`：建立 `test_run` → 呼叫 ESP32 `/api/run` → 設定示波器（依速度算水平刻度，顯示約 10 個週期）→ `single()` → 抓波形 → `analysis` → 判定 → 寫入 `measurement` / `waveform` → `/api/stop`。
- `esp32_client.py`：httpx；另提供 `ESP32_MOCK=1`，完全不接硬體也能測。
- **完成標準**：`pytest` 全過（用已知 τ 合成的波形驗證 tr(10–90) ≈ 2.197τ、tr(30–70) ≈ 0.847τ、頻率誤差 < 1%）；`POST /api/runs` 後資料庫出現對應紀錄。

### 步驟 4｜Web 儀表板（FastAPI 提供靜態頁，`http://127.0.0.1:8000`）
- **測試控制**：選協定、速度、樣式、SPI 模式、上拉方式（內部 / 外部 + 阻值），按「執行」。
- **結果**：量測值表格（PASS 綠 / FAIL 紅、顯示上下限），以 Chart.js 畫波形（降採樣到約 2000 點），標出 10/50/90% 門檻線。
- **歷史與趨勢**：依協定篩選測試紀錄；畫「指標 vs 速度」，例如 I2C 上升時間 vs 速度、UART 鮑率誤差 vs 鮑率。
- **掃描**：一次跑一組速度（UART 9600–2M、I2C 100k/400k/1M、SPI 1–40 MHz），背景執行並顯示進度；可匯出 CSV。
- 後端 API：`POST /api/runs`、`POST /api/sweeps`、`GET /api/runs?protocol=`、`GET /api/runs/{id}`（含量測與波形）、`GET /api/trend?protocol=&metric=`、`GET /api/runs.csv`。
- **完成標準**：在內建瀏覽器開儀表板，用模擬示波器跑一次 → 看到波形與 PASS/FAIL；跑一次掃描 → 趨勢圖出現多個點。

### 步驟 5｜接上真實 ESP32（示波器仍用模擬）
- `.env` 設 `ESP32_URL=http://busbench.local`（或 IP）、`ESP32_MOCK=0`。
- **完成標準**：儀表板按「執行」後，ESP32 `/api/status` 的框數增加；紀錄中的 `esp32_fw` 正確。

### 步驟 6｜接上 Tektronix 示波器
- **連線**（擇一）：
  - USB：需安裝 VISA 驅動（**NI-VISA** 或 Tek 的 **TekVISA**，下載與安裝前會先徵求你同意）；資源字串類似 `USB0::0x0699::0x….::INSTR`。
  - LAN：示波器設 IP（同 192.168.0.x 網段）；用 pyvisa-py，不需 NI-VISA；資源字串 `TCPIP0::<ip>::inst0::INSTR`。部分機型要在 Utility → I/O 開 Socket Server，或改用 `TCPIP0::<ip>::4000::SOCKET`。注意部分 TBS1000 系列只有 USB。
  - 用 `python -m pyvisa info`、`list_resources()`、`*IDN?` 確認連線，並記下型號。
- **`scope/tek.py`**（以 Tek 通用的程式介面為主）：
  - 擷取設定：`CH1:SCAle`、`HORizontal:SCAle`（舊機型為 `HORizontal:MAIn:SCAle`）、`TRIGger:A:EDGE:SOUrce CH1`、觸發準位 1.65 V、`ACQuire:STOPAfter SEQuence`、`ACQuire:STATE ON`、`*OPC?`。
  - 波形傳輸：`DATa:SOUrce CHx`、`DATa:ENCdg RIBinary`、`DATa:WIDth 1`、`WFMOutpre:XINcr? / XZEro? / YMUlt? / YOFf? / YZEro?`、`CURVe?`（`query_binary_values`）；電壓換算 = (raw − YOFF) × YMULT + YZERO。
  - 示波器內建量測（用來交叉比對，依 `*IDN?` 判斷機型系列）：舊系列（TBS / DPO / MDO3）用 `MEASUrement:IMMed:TYPe` / `VALue?`；新系列（MSO 4/5/6）用 `MEASUrement:ADDMEAS` / `MEASx:RESUlts:CURRentacq:MEAN?`。
  - 實機若有指令不相容，依型號手冊微調。
- 探棒：設 10X，探針勾在 GPIO 排針上，接地夾接開發板 GND；依上方腳位表接 CH1 / CH2。
- **完成標準**：UART 115200 量到約 57.6 kHz、鮑率誤差 < 2%；Python 計算的頻率與示波器內建量測相差 < 1%；I2C 100k / 400k 各跑一次，產生上升時間與判定結果。

### 步驟 7｜選購零件到貨後的延伸
- I2C 外部上拉比較：麵包板換 1k / 2.2k / 4.7k / 10k，儀表板記錄 `pullup_ohm` → 畫「上升時間 vs 上拉電阻」，並畫出規格線。
- 迴路驗證：用杜邦線接 UART 17→16、SPI 23→19、I2C 21/22 ↔ 25/26 → 紀錄加上資料錯誤數。
- **完成標準**：趨勢圖可看出阻值越小、上升時間越短，並標出哪些組合合乎規格。

### 步驟 8｜文件
- `README.md`：環境安裝、`docker compose up`、燒錄、啟動後端、ASCII 接線圖、常見問題（長路徑、VISA 驅動、NVS 不要清除）。
- 任何 git 提交都先經你同意。

---

## 驗證（端對端）
1. `pytest server/tests`：量測演算法與規格判定全過。
2. `docker compose up -d` → 三張表存在。
3. 燒錄後序列埠顯示 `WiFi up`；`curl /api/info` 成功；未驗證 `POST /api/run` 回 401。
4. 模擬示波器 + 模擬 ESP32：在內建瀏覽器操作儀表板 → 執行、掃描、趨勢、CSV 都正常，資料庫有紀錄。
5. 真實 ESP32 + 模擬示波器：ESP32 狀態隨測試變化。
6. 真實 Tektronix：`*IDN?` 成功；UART / SPI / I2C 各至少一筆實測紀錄，與示波器內建量測交叉比對。

## 需要你配合的事
- 步驟 0：選擇專案資料夾。
- 步驟 6：提供示波器型號與連線方式；USB 連線需安裝 NI-VISA 或 TekVISA（安裝前會先問你）。
- 若要做步驟 7，先購買杜邦線、電阻、麵包板。
