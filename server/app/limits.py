"""規格上下限與 PASS/FAIL 判定。

預設值寫在這裡；要調整時在 server/limits_override.json 覆寫，例如：
{"uart": {"TX": {"baud_error_pct": [-3, 3]}}}
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .config import SERVER_DIR

OVERRIDE_FILE = SERVER_DIR / "limits_override.json"


@dataclass(frozen=True)
class Limit:
    min: float | None
    max: float | None
    source: str = ""


# I2C 規格（NXP UM10204）：上升 / 下降時間以 VDD 的 30% 與 70% 量測
I2C_MODES = [
    # (最高 SCL, 模式名稱, tr 上限, tf 上限)
    (100_000, "Standard-mode", 1000e-9, 300e-9),
    (400_000, "Fast-mode", 300e-9, 300e-9),
    (1_000_000, "Fast-mode Plus", 120e-9, 120e-9),
]

UM10204 = "I2C 規格 UM10204"
LVTTL = "3.3 V LVTTL 準位"
GUIDE = "一般建議值"


def i2c_mode(speed_hz: int) -> tuple[int, str, float, float]:
    for mode in I2C_MODES:
        if speed_hz <= mode[0]:
            return mode
    return I2C_MODES[-1]


def default_limits(protocol: str, signal: str, speed_hz: int) -> dict[str, Limit]:
    if protocol == "uart":
        return {
            "baud_error_pct": Limit(-2.0, 2.0, GUIDE),
            "vhigh": Limit(2.4, None, LVTTL),
            "vlow": Limit(None, 0.4, LVTTL),
            "overshoot": Limit(None, 20.0, GUIDE),
        }
    if protocol == "spi":
        lim = {
            "vhigh": Limit(2.4, None, LVTTL),
            "vlow": Limit(None, 0.4, LVTTL),
            "overshoot": Limit(None, 20.0, GUIDE),
        }
        if signal == "SCLK":
            lim["freq_error_pct"] = Limit(-2.0, 2.0, GUIDE)
            lim["duty"] = Limit(45.0, 55.0, GUIDE)
        return lim
    if protocol == "i2c":
        max_hz, name, tr, tf = i2c_mode(speed_hz)
        src = f"{UM10204} {name}"
        lim = {
            "rise_30_70": Limit(None, tr, src),
            "fall_70_30": Limit(None, tf, src),
            "vlow": Limit(None, 0.4, f"{UM10204} VOL"),
            "vhigh": Limit(0.7 * 3.3, None, f"{UM10204} VIH"),
        }
        if signal == "SCL":
            lim["frequency"] = Limit(None, max_hz * 1.0, src)
        return lim
    return {}


def _load_override() -> dict:
    try:
        return json.loads(OVERRIDE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def limits_for(protocol: str, signal: str, speed_hz: int) -> dict[str, Limit]:
    lim = default_limits(protocol, signal, speed_hz)
    for name, pair in _load_override().get(protocol, {}).get(signal, {}).items():
        lim[name] = Limit(pair[0], pair[1], "自訂（limits_override.json）")
    return lim


def judge(value: float | None, limit: Limit | None) -> bool | None:
    if limit is None:
        return None
    if value is None:
        return False   # 有規格但量不到，視為不合格
    if limit.min is not None and value < limit.min:
        return False
    if limit.max is not None and value > limit.max:
        return False
    return True
