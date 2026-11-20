import math

import numpy as np
import pytest

from app import analysis
from app.scope import CaptureSetup, Channel
from app.scope.sim import SimScope

VDD = 3.3


def rc_square(freq, tau, dt, n, noise=0.0, lsb=0.0, seed=1):
    """理想 RC 邊緣的方波（每個半週期都足夠穩定）。回傳 (volts, x_zero, x_incr)。"""
    t = np.arange(n) * dt
    half = 0.5 / freq
    k = np.floor(t / half).astype(int)
    since = t - k * half
    high = (k % 2) == 0
    v = np.where(high, VDD * (1 - np.exp(-since / tau)), VDD * np.exp(-since / tau))
    rng = np.random.default_rng(seed)
    if noise:
        v = v + rng.normal(0, noise, n)
    if lsb:
        v = np.round(v / lsb) * lsb
    return v, 0.0, dt


@pytest.mark.parametrize("noise,lsb,tol", [(0.0, 0.0, 0.02), (0.008, 0.04, 0.06)])
def test_rc_edges(noise, lsb, tol):
    tau = 20e-9
    v, x0, dt = rc_square(1e6, tau, 0.5e-9, 20000, noise, lsb)
    r = analysis.analyze(v, x0, dt)
    assert r.get("error") is None
    assert r["rise_10_90"] == pytest.approx(math.log(9) * tau, rel=tol)
    assert r["fall_90_10"] == pytest.approx(math.log(9) * tau, rel=tol)
    assert r["rise_30_70"] == pytest.approx(math.log(7 / 3) * tau, rel=tol + 0.02)
    assert r["fall_70_30"] == pytest.approx(math.log(7 / 3) * tau, rel=tol + 0.02)
    assert r["frequency"] == pytest.approx(1e6, rel=0.002)
    assert r["duty"] == pytest.approx(50.0, abs=1.0)
    assert r["vhigh"] == pytest.approx(VDD, abs=0.05)
    assert r["vlow"] == pytest.approx(0.0, abs=0.05)


def test_no_signal():
    r = analysis.analyze(np.full(1000, 0.01), 0.0, 1e-9)
    assert "error" in r
    assert r["frequency"] is None


def _sim_capture(protocol, speed, actual=None, signals=("TX",), **params):
    actual = actual or speed
    chans = [Channel(f"CH{i + 1}", s) for i, s in enumerate(signals)]
    h = {"uart": 2.0, "spi": 1.2, "i2c": 2.4}[protocol] / actual
    cap = CaptureSetup(protocol=protocol, speed_hz=speed, actual_hz=actual, channels=chans, h_scale=h,
                       trigger_source="CH1", params=params)
    sc = SimScope(seed=3)
    sc.setup(cap)
    sc.acquire()
    return {c.signal: sc.fetch(c.source) for c in chans}


@pytest.mark.parametrize("pattern", ["0x55", "counter", "prbs7"])
@pytest.mark.parametrize("baud", [9600, 115200, 2_000_000])
def test_uart_baud_from_sim(pattern, baud):
    wf = _sim_capture("uart", baud, pattern=pattern)["TX"]
    r = analysis.derive("uart", "TX", baud, baud, analysis.analyze(wf.volts, wf.x_zero, wf.x_incr))
    assert abs(r["baud_error_pct"]) < 0.5


def test_decimate_keeps_extremes():
    v = np.zeros(100_000)
    v[12345] = 5.0
    v[67890] = -5.0
    d = analysis.decimate(0.0, 1e-9, v, 2000)
    assert len(d["v"]) <= 2000
    assert max(d["v"]) == 5.0 and min(d["v"]) == -5.0
    assert d["t"] == sorted(d["t"])
