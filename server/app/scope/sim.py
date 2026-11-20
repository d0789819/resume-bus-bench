"""模擬示波器：依測試參數合成接近真實的波形，讓整套流程不接硬體也能跑。

模型（刻意簡化，只為了讓量測與判定有合理的輸入）：
- 推挽輸出（UART TX、SPI）：一階 RC 邊緣約 4 ns，加上少量振鈴。
- 開汲極（I2C）：上升由上拉電阻 × 匯流排電容決定；ESP32 內部上拉約 45 kΩ。
  ESP32 的 SCL 高電位計時從 SCL 升到 VIH 才開始，所以上升越慢，實際 SCL 頻率越低。
- 示波器頻寬：以 tr ≈ 0.35 / BW 與訊號邊緣合成。
- 8 位元垂直解析度（每格 25 階）＋ 8 mV 雜訊。
"""
from __future__ import annotations

import math

import numpy as np

from .base import CaptureSetup, ScopeError, Waveform

VDD = 3.3
INTERNAL_PULLUP_OHM = 45_000
PUSH_PULL_EDGE_S = 4e-9            # 10–90% 邊緣時間
OPEN_DRAIN_FALL_S = 6e-9
RC_10_90 = math.log(9.0)           # 一階系統 10–90% = 2.197 τ
VIH_DELAY = math.log(1 / 0.3)      # 由 0 升到 0.7 VDD 需要 1.204 τ
RING_FREQ_HZ = 80e6
RING_DECAY_S = 6e-9
RING_AMP = 0.08                    # 振鈴幅度（相對 VDD）


class _Line:
    """一條訊號線：起始電位 + 依時間排序的轉換（時間, 目標電位 0/1）。"""

    def __init__(self, initial: int, tau_rise: float, tau_fall: float, push_pull: bool):
        self.initial = initial
        self.tau_rise = tau_rise
        self.tau_fall = tau_fall
        self.push_pull = push_pull
        self.times: list[float] = []
        self.levels: list[int] = []
        self._last = initial

    def set(self, t: float, level: int) -> None:
        if level != self._last:
            self.times.append(t)
            self.levels.append(level)
            self._last = level


def pattern_bytes(pattern: str, n: int) -> list[int]:
    if pattern == "counter":
        return [i & 0xFF for i in range(n)]
    if pattern == "prbs7":   # 與韌體相同：x^7 + x^6 + 1，初值 0x7F
        state, out = 0x7F, []
        for _ in range(n):
            v = 0
            for _ in range(8):
                nb = ((state >> 6) ^ (state >> 5)) & 1
                state = ((state << 1) | nb) & 0x7F
                v = (v << 1) | nb
            out.append(v)
        return out
    return [0x55] * n


class SimScope:
    name = "sim"

    def __init__(self, bandwidth_hz: float = 100e6, noise_v: float = 0.008, seed: int | None = None):
        self.bandwidth_hz = bandwidth_hz
        self.noise_v = noise_v
        self._rng = np.random.default_rng(seed)
        self._cap: CaptureSetup | None = None
        self._data: dict[str, Waveform] = {}

    def idn(self) -> str:
        return f"SIMULATED,BusBench-Sim,0,BW {self.bandwidth_hz / 1e6:.0f} MHz"

    def setup(self, cap: CaptureSetup) -> None:
        self._cap = cap
        self._data = {}

    def acquire(self, timeout_s: float = 10.0) -> None:
        cap = self._cap
        if cap is None:
            raise ScopeError("請先呼叫 setup()")
        n = cap.record_length
        window = cap.h_scale * 10
        dt = window / n
        t = -window / 2 + np.arange(n) * dt          # 觸發點在畫面中央（t = 0）
        lines = self._build_lines(cap, window)
        bw_tau = 0.35 / self.bandwidth_hz / RC_10_90
        lsb = cap.v_scale / 25.0
        for ch in cap.channels:
            line = lines.get(ch.signal)
            v = np.zeros(n) if line is None else self._render(t, line, bw_tau)
            v = v + self._rng.normal(0.0, self.noise_v, n)
            v = np.round(v / lsb) * lsb
            self._data[ch.source] = Waveform(ch.source, float(t[0]), dt, v.astype(np.float32))

    def fetch(self, source: str) -> Waveform:
        if source not in self._data:
            raise ScopeError(f"{source} 沒有資料（請先 acquire()）")
        return self._data[source]

    def native_measure(self, source: str, kind: str) -> float | None:
        return None   # 模擬示波器不提供內建量測

    def close(self) -> None:
        pass

    # ---- 波形合成 ----
    def _build_lines(self, cap: CaptureSetup, window: float) -> dict[str, _Line]:
        bw_tau = 0.35 / self.bandwidth_hz / RC_10_90
        if cap.protocol == "uart":
            return {"TX": self._uart(cap, window)}
        if cap.protocol == "spi":
            return self._spi(cap, window)
        if cap.protocol == "i2c":
            return self._i2c(cap, window, bw_tau)
        raise ScopeError(f"未知協定：{cap.protocol}")

    @staticmethod
    def _pp_tau() -> float:
        return PUSH_PULL_EDGE_S / RC_10_90

    def _uart(self, cap: CaptureSetup, window: float) -> _Line:
        tb = 1.0 / cap.actual_hz
        frames_half = math.ceil((window / 2) / (10 * tb)) + 1
        data = pattern_bytes(cap.params.get("pattern", "0x55"), 2 * frames_half + 1)
        line = _Line(1, self._pp_tau(), self._pp_tau(), push_pull=True)
        t0 = -frames_half * 10 * tb                     # 讓某個起始位元剛好落在 t = 0
        k = 0
        for byte in data:
            bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]   # 8N1，LSB 先送
            for b in bits:
                line.set(t0 + k * tb, b)
                k += 1
        return line

    def _spi(self, cap: CaptureSetup, window: float) -> dict[str, _Line]:
        T = 1.0 / cap.actual_hz
        mode = int(cap.params.get("spi_mode", 0))
        cpol, cpha = mode >> 1, mode & 1
        half = math.ceil((window / 2) / T) + 1
        nbits = 2 * half + 1
        data = pattern_bytes(cap.params.get("pattern", "0x55"), nbits // 8 + 2)
        bits = [(data[i // 8] >> (7 - i % 8)) & 1 for i in range(nbits)]   # MSB 先送
        tau = self._pp_tau()
        sclk = _Line(cpol, tau, tau, push_pull=True)
        mosi = _Line(bits[0], tau, tau, push_pull=True)
        for j, k in enumerate(range(-half, half + 1)):
            lead = k * T                                  # 前緣在 t = k·T（t = 0 為觸發點）
            # CPHA=0：資料在前緣取樣，於前一個後緣（前緣 − T/2）改變；CPHA=1：資料在前緣改變
            mosi.set(lead - T / 2 if cpha == 0 else lead, bits[j])
            sclk.set(lead, 1 - cpol)
            sclk.set(lead + T / 2, cpol)
        return {"SCLK": sclk, "MOSI": mosi}

    def _i2c(self, cap: CaptureSetup, window: float, bw_tau: float) -> dict[str, _Line]:
        p = cap.params
        r = INTERNAL_PULLUP_OHM
        if p.get("pullup") == "external" and p.get("pullup_ohm"):
            r = 1.0 / (1.0 / INTERNAL_PULLUP_OHM + 1.0 / float(p["pullup_ohm"]))
        c = float(p.get("bus_cap_pf") or 20.0) * 1e-12
        tau_rise = r * c
        tau_fall = OPEN_DRAIN_FALL_S / RC_10_90
        rise_to_vih = math.hypot(tau_rise, bw_tau) * VIH_DELAY
        T = 1.0 / cap.actual_hz
        lo, hi = T / 2, T / 2

        scl = _Line(1, tau_rise, tau_fall, push_pull=False)
        sda = _Line(1, tau_rise, tau_fall, push_pull=False)
        addr = int(p.get("i2c_addr", 0x28))
        frame = [(addr << 1) | 0]                         # 位址 + 寫入
        ack = 0 if p.get("loopback") else 1               # 沒有從端時 NACK
        if not ack:
            frame += pattern_bytes(p.get("pattern", "0x55"), 4)

        t = 0.0
        sda.set(t, 0)                                     # 起始條件：SCL 高時 SDA 下降
        t += hi
        scl.set(t, 0)
        for byte in frame:
            for bit in [(byte >> (7 - i)) & 1 for i in range(8)] + [ack]:
                sda.set(t + lo / 2, bit)                  # SCL 低電位中段改變 SDA
                t += lo
                scl.set(t, 1)                             # 釋放 SCL，由上拉慢慢拉高
                t += rise_to_vih + hi                     # 升到 VIH 後才開始計高電位時間
                scl.set(t, 0)
            if ack:
                break
        sda.set(t + lo / 2, 0)                            # 停止條件
        t += lo
        scl.set(t, 1)
        t += rise_to_vih + hi
        sda.set(t, 1)
        return {"SCL": scl, "SDA": sda}

    def _render(self, t: np.ndarray, line: _Line, bw_tau: float) -> np.ndarray:
        tr = math.hypot(line.tau_rise, bw_tau)
        tf = math.hypot(line.tau_fall, bw_tau)
        times, levels = line.times, line.levels
        out = np.empty(t.size)
        x = line.initial * VDD
        target = x
        prev = min(t[0], times[0]) if times else t[0]
        ti = 0

        def step(x: float, tg: float, d: float) -> float:
            if d <= 0:
                return x
            tau = tr if tg > x else tf
            return tg + (x - tg) * math.exp(-d / tau)

        tl = t.tolist()
        for i, tt in enumerate(tl):
            while ti < len(times) and times[ti] <= tt:
                te = max(times[ti], prev)
                x = step(x, target, te - prev)
                prev = te
                target = levels[ti] * VDD
                ti += 1
            x = step(x, target, tt - prev)
            prev = tt
            out[i] = x

        # 推挽輸出邊緣後的少量振鈴（取樣太粗時看不到，就不加）
        dt = tl[1] - tl[0] if len(tl) > 1 else 1.0
        if line.push_pull and dt < 1 / (8 * RING_FREQ_HZ):
            span = int(5 * RING_DECAY_S / dt) + 1
            prev_level = line.initial
            for te, lvl in zip(times, levels):
                direction = 1 if lvl > prev_level else -1
                prev_level = lvl
                i0 = int(np.searchsorted(t, te))
                if i0 >= t.size:
                    continue
                seg = slice(i0, min(t.size, i0 + span))
                u = t[seg] - te
                out[seg] += direction * RING_AMP * VDD * np.exp(-u / RING_DECAY_S) * np.sin(2 * np.pi * RING_FREQ_HZ * u)
        return out
