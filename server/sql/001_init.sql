-- Bus Bench 資料表（冪等：可重複執行）

-- 一次測試 = 一筆 test_run
CREATE TABLE IF NOT EXISTS test_run (
    id          bigserial PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    protocol    text        NOT NULL CHECK (protocol IN ('uart', 'spi', 'i2c')),
    speed_hz    integer     NOT NULL,          -- 要求的速度（鮑率 / SCLK / SCL）
    actual_hz   integer,                       -- ESP32 實際設定的速度（除頻後）
    params      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    sweep_id    uuid,                          -- 屬於哪一次掃描（單次測試為 NULL）
    scope_idn   text,
    esp32_fw    text,
    status      text        NOT NULL DEFAULT 'running',   -- running / done / error
    verdict     text,                                     -- PASS / FAIL
    note        text
);
CREATE INDEX IF NOT EXISTS test_run_proto_speed_idx ON test_run (protocol, speed_hz, started_at DESC);
CREATE INDEX IF NOT EXISTS test_run_sweep_idx       ON test_run (sweep_id);

-- 每個量測項目一筆
CREATE TABLE IF NOT EXISTS measurement (
    id        bigserial PRIMARY KEY,
    run_id    bigint NOT NULL REFERENCES test_run (id) ON DELETE CASCADE,
    channel   text   NOT NULL,                 -- 訊號名稱，例如 SCL、TX
    name      text   NOT NULL,                 -- 例如 rise_time_30_70
    value     double precision,
    unit      text,
    source    text   NOT NULL DEFAULT 'calc',  -- calc = Python 計算；scope = 示波器內建量測
    limit_min double precision,
    limit_max double precision,
    pass      boolean
);
CREATE INDEX IF NOT EXISTS measurement_run_idx  ON measurement (run_id);
CREATE INDEX IF NOT EXISTS measurement_name_idx ON measurement (name);

-- 原始波形（每個通道一筆）
CREATE TABLE IF NOT EXISTS waveform (
    run_id  bigint NOT NULL REFERENCES test_run (id) ON DELETE CASCADE,
    channel text   NOT NULL,                   -- 訊號名稱
    source  text,                              -- 示波器通道，例如 CH1
    x_zero  double precision NOT NULL,         -- 第一點的時間（秒）
    x_incr  double precision NOT NULL,         -- 取樣間隔（秒）
    y_unit  text   NOT NULL DEFAULT 'V',
    points  real[] NOT NULL,
    PRIMARY KEY (run_id, channel)
);
