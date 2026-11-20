"""API 的請求格式與各協定的預設值。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Protocol = Literal["uart", "spi", "i2c"]

# 每個協定量測哪些訊號（第一個接主通道、第二個接副通道）
SIGNALS: dict[str, list[str]] = {
    "uart": ["TX"],
    "spi": ["SCLK", "MOSI"],
    "i2c": ["SCL", "SDA"],
}

# ESP32 腳位（與 firmware/src/bus_gen.h 一致）
GPIO: dict[str, int] = {"TX": 17, "SCLK": 18, "MOSI": 23, "SCL": 22, "SDA": 21}

# 每個訊號在結果表中顯示、存入資料庫的量測項目
SIGNAL_METRICS: dict[tuple[str, str], list[str]] = {
    ("uart", "TX"): ["baud_measured", "baud_error_pct", "bit_time", "rise_10_90", "fall_90_10",
                     "overshoot", "undershoot", "vhigh", "vlow", "vpp"],
    ("spi", "SCLK"): ["frequency", "freq_error_pct", "freq_error_req_pct", "duty", "rise_10_90",
                      "fall_90_10", "overshoot", "undershoot", "vhigh", "vlow"],
    ("spi", "MOSI"): ["rise_10_90", "fall_90_10", "overshoot", "vhigh", "vlow"],
    ("i2c", "SCL"): ["frequency", "freq_error_req_pct", "duty", "rise_30_70", "fall_70_30",
                     "rise_10_90", "vhigh", "vlow"],
    ("i2c", "SDA"): ["rise_30_70", "fall_70_30", "vhigh", "vlow"],
}

# 示波器內建量測（交叉比對用，只量主通道）
NATIVE_KINDS: dict[str, list[str]] = {
    "uart": ["rise_10_90", "fall_90_10"],
    "spi": ["frequency", "rise_10_90", "duty"],
    "i2c": ["frequency"],
}

# 速度掃描的預設清單
PRESETS: dict[str, list[int]] = {
    "uart": [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1_000_000, 2_000_000],
    "spi": [1_000_000, 2_000_000, 5_000_000, 10_000_000, 16_000_000, 20_000_000, 30_000_000, 40_000_000],
    "i2c": [100_000, 200_000, 400_000, 600_000, 800_000, 1_000_000],
}

RANGES: dict[str, tuple[int, int]] = {
    "uart": (300, 5_000_000),
    "spi": (100_000, 40_000_000),
    "i2c": (10_000, 1_000_000),
}


class RunOptions(BaseModel):
    pattern: Literal["0x55", "counter", "prbs7"] = "0x55"
    spi_mode: int = Field(0, ge=0, le=3)
    pullup: Literal["internal", "external"] = "internal"
    pullup_ohm: int | None = Field(None, gt=0, description="外接上拉電阻（Ω）")
    bus_cap_pf: float = Field(20.0, gt=0, le=1000, description="匯流排電容（pF），只影響模擬示波器")
    loopback: bool = False
    note: str | None = Field(None, max_length=500)


class RunRequest(RunOptions):
    protocol: Protocol
    speed_hz: int = Field(gt=0)


class SweepRequest(RunOptions):
    protocol: Protocol
    speeds: list[int] | None = Field(None, description="不填則使用預設清單")
