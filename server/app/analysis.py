"""波形量測。

所有主要量測都在這裡用 numpy 計算，與示波器型號無關：
只要拿得到原始波形（時間軸 + 電壓），不同廠牌、型號的結果都一致。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 量測項目：名稱 -> (單位, 中文名稱)
METRICS: dict[str, tuple[str, str]] = {
    "frequency": ("Hz", "頻率"),
    "period": ("s", "週期"),
    "duty": ("%", "工作週期"),
    "rise_10_90": ("s", "上升時間 10–90%"),
    "fall_90_10": ("s", "下降時間 90–10%"),
    "rise_30_70": ("s", "上升時間 30–70%"),
    "fall_70_30": ("s", "下降時間 70–30%"),
    "overshoot": ("%", "過衝"),
    "undershoot": ("%", "下衝"),
    "vhigh": ("V", "高準位"),
    "vlow": ("V", "低準位"),
    "vpp": ("V", "峰對峰值"),
    "vmax": ("V", "最大值"),
    "vmin": ("V", "最小值"),
    "amplitude": ("V", "振幅"),
    "bit_time": ("s", "位元時間"),
    "edges": ("", "邊緣數"),
    # 依協定推導的項目
    "baud_measured": ("Bd", "實測鮑率"),
    "baud_error_pct": ("%", "鮑率誤差"),
    "freq_error_pct": ("%", "頻率誤差（對實際設定）"),
    "freq_error_req_pct": ("%", "頻率誤差（對要求值）"),
}

BASE_METRICS = [
    "frequency", "period", "duty", "rise_10_90", "fall_90_10", "rise_30_70", "fall_70_30",
    "overshoot", "undershoot", "vhigh", "vlow", "vpp", "vmax", "vmin", "amplitude", "bit_time", "edges",
]


@dataclass
class Levels:
    low: float
    high: float

    @property
    def amp(self) -> float:
        return self.high - self.low

    def at(self, pct: float) -> float:
        return self.low + self.amp * pct / 100.0


def _mode(x: np.ndarray, fallback: float) -> float:
    """直方圖眾數（示波器常用的「高/低準位」演算法）。"""
    if x.size == 0:
        return fallback
    if x.size < 16:
        return float(np.median(x))
    hist, edges = np.histogram(x, bins=64)
    i = int(np.argmax(hist))
    sel = x[(x >= edges[i]) & (x <= edges[i + 1])]
    return float(sel.mean()) if sel.size else float(np.median(x))


def find_levels(v: np.ndarray) -> Levels:
    vmin, vmax = float(v.min()), float(v.max())
    mid = (vmin + vmax) / 2.0
    return Levels(low=_mode(v[v < mid], vmin), high=_mode(v[v >= mid], vmax))


def edge_times(v: list[float], x_zero: float, x_incr: float, level: float, hyst: float):
    """回傳 (上升交越時間, 下降交越時間)。

    使用遲滯（Schmitt trigger）避免雜訊在門檻附近造成重複交越；
    交越時間以門檻兩側的取樣點線性內插。
    """
    rising: list[float] = []
    falling: list[float] = []
    hi_th, lo_th = level + hyst, level - hyst
    state = None
    last_below = last_above = -1
    n = len(v)

    def interp(j: int) -> float:
        x0, x1 = v[j], v[j + 1]
        frac = (level - x0) / (x1 - x0) if x1 != x0 else 0.0
        return x_zero + (j + frac) * x_incr

    for i, x in enumerate(v):
        if x < level:
            last_below = i
        else:
            last_above = i
        if state is None:
            if x >= hi_th:
                state = 1
            elif x <= lo_th:
                state = 0
        elif state == 0:
            if x >= hi_th:
                if 0 <= last_below < n - 1:
                    rising.append(interp(last_below))
                state = 1
        elif x <= lo_th:
            if 0 <= last_above < n - 1:
                falling.append(interp(last_above))
            state = 0
    return np.asarray(rising), np.asarray(falling)


def pair_durations(start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """把每個 end 配對到它之前最近、且在上一個 end 之後的 start，回傳時間差。"""
    out = []
    prev_end = -np.inf
    for e in end:
        k = int(np.searchsorted(start, e)) - 1
        if k >= 0 and start[k] > prev_end:
            out.append(e - start[k])
        prev_end = e
    return np.asarray(out)


def estimate_bit_time(rise: np.ndarray, fall: np.ndarray) -> float | None:
    """UART 位元時間：所有 50% 交越之間的間隔都是位元時間的整數倍。"""
    e = np.sort(np.concatenate([rise, fall]))
    w = np.diff(e)
    if w.size < 3:
        return None
    base = float(np.percentile(w, 10))
    near = w[(w > 0.7 * base) & (w < 1.3 * base)]
    if near.size == 0:
        return None
    tb = float(np.median(near))
    n = np.round(w / tb)
    ok = (n >= 1) & (n <= 9) & (np.abs(w / tb - n) < 0.25)
    if ok.sum() < 2:
        return tb
    return float(w[ok].sum() / n[ok].sum())


def analyze(volts, x_zero: float, x_incr: float, min_amplitude: float = 0.3) -> dict:
    """計算單一通道的量測值。無法計算的項目為 None；沒有訊號時帶 'error'。"""
    v = np.asarray(volts, dtype=float)
    res: dict = {k: None for k in BASE_METRICS}
    if v.size < 8:
        res["error"] = "波形點數不足"
        return res

    vmax, vmin = float(v.max()), float(v.min())
    lv = find_levels(v)
    res.update(vmax=vmax, vmin=vmin, vpp=vmax - vmin, vhigh=lv.high, vlow=lv.low, amplitude=lv.amp)
    if lv.amp < min_amplitude:
        res["error"] = f"沒有訊號（振幅 {lv.amp:.2f} V）"
        return res

    vl = v.tolist()
    hyst = max(0.03 * lv.amp, 0.02)
    cross = {p: edge_times(vl, x_zero, x_incr, lv.at(p), hyst) for p in (10, 30, 50, 70, 90)}
    r50, f50 = cross[50]
    res["edges"] = int(r50.size + f50.size)

    rise_10_90 = pair_durations(cross[10][0], cross[90][0])
    fall_90_10 = pair_durations(cross[90][1], cross[10][1])
    rise_30_70 = pair_durations(cross[30][0], cross[70][0])
    fall_70_30 = pair_durations(cross[70][1], cross[30][1])
    for key, arr in (("rise_10_90", rise_10_90), ("fall_90_10", fall_90_10),
                     ("rise_30_70", rise_30_70), ("fall_70_30", fall_70_30)):
        res[key] = float(np.median(arr)) if arr.size else None

    # 週期與工作週期：只取接近中位數的週期，排除突發之間的空檔
    if r50.size >= 2:
        periods = np.diff(r50)
        med = float(np.median(periods))
        good_idx = np.nonzero((periods > 0.8 * med) & (periods < 1.2 * med))[0]
        if good_idx.size:
            period = float(periods[good_idx].mean())
            res["period"] = period
            res["frequency"] = 1.0 / period
            duties = []
            for i in good_idx:
                k = int(np.searchsorted(f50, r50[i]))
                if k < f50.size and f50[k] < r50[i + 1]:
                    duties.append((f50[k] - r50[i]) / (r50[i + 1] - r50[i]))
            if duties:
                res["duty"] = float(np.mean(duties)) * 100.0

    res["bit_time"] = estimate_bit_time(r50, f50)
    res["overshoot"] = max(0.0, (vmax - lv.high) / lv.amp * 100.0)
    res["undershoot"] = max(0.0, (lv.low - vmin) / lv.amp * 100.0)
    return res


def derive(protocol: str, signal: str, speed_hz: int, actual_hz: int | None, res: dict) -> dict:
    """依協定加上推導項目（鮑率誤差、頻率誤差等）。"""
    d = dict(res)
    if protocol == "uart" and res.get("bit_time"):
        baud = 1.0 / res["bit_time"]
        d["baud_measured"] = baud
        d["baud_error_pct"] = (baud / speed_hz - 1.0) * 100.0
    freq = res.get("frequency")
    if freq and ((protocol == "spi" and signal == "SCLK") or (protocol == "i2c" and signal == "SCL")):
        d["freq_error_req_pct"] = (freq / speed_hz - 1.0) * 100.0
        if actual_hz:
            d["freq_error_pct"] = (freq / actual_hz - 1.0) * 100.0
    return d


def decimate(x_zero: float, x_incr: float, points, max_points: int = 2000) -> dict:
    """給儀表板畫圖用：每個區段保留最小值與最大值，降點數但保留邊緣形狀。"""
    v = np.asarray(points, dtype=float)
    n = v.size
    if n <= max_points:
        t = x_zero + np.arange(n) * x_incr
        return {"t": t.tolist(), "v": v.tolist()}
    buckets = max_points // 2
    size = n // buckets
    m = v[: buckets * size].reshape(buckets, size)
    imin, imax = m.argmin(axis=1), m.argmax(axis=1)
    base = np.arange(buckets) * size
    first = np.minimum(imin, imax) + base
    second = np.maximum(imin, imax) + base
    idx = np.empty(buckets * 2, dtype=int)
    idx[0::2], idx[1::2] = first, second
    return {"t": (x_zero + idx * x_incr).tolist(), "v": v[idx].tolist()}
