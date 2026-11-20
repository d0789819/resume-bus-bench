"""完整流程：模擬 ESP32 + 模擬示波器 + 記憶體資料庫。"""
import threading

import pytest

from app.config import Settings
from app.esp32_client import Esp32Error, MockEsp32
from app.models import RunRequest
from app.runner import Busy, Runner, ScopeProvider
from app.scope.sim import SimScope


class FakeRepo:
    def __init__(self):
        self.runs, self.meas, self.waves = {}, {}, {}

    def create_run(self, protocol, speed_hz, params, sweep_id=None, note=None):
        rid = len(self.runs) + 1
        self.runs[rid] = {"protocol": protocol, "speed_hz": speed_hz, "params": params, "status": "running"}
        return rid

    def finish_run(self, run_id, **kw):
        self.runs[run_id].update(kw)

    def add_measurements(self, run_id, rows):
        self.meas.setdefault(run_id, []).extend(rows)

    def add_waveform(self, run_id, channel, source, x_zero, x_incr, points):
        self.waves.setdefault(run_id, {})[channel] = (x_zero, x_incr, len(points))

    def value(self, run_id, channel, name):
        return next(m for m in self.meas[run_id] if m["channel"] == channel and m["name"] == name)


def make_runner(esp32=None):
    s = Settings(scope_driver="sim", esp32_mock=True)
    repo = FakeRepo()
    scopes = ScopeProvider(s, factory=lambda _s: SimScope(seed=7))
    return Runner(repo, esp32 or MockEsp32(), scopes, s, settle_s=0), repo


def test_uart_pass():
    runner, repo = make_runner()
    rid = runner.run(RunRequest(protocol="uart", speed_hz=115200))
    run = repo.runs[rid]
    assert run["status"] == "done" and run["verdict"] == "PASS"
    assert abs(repo.value(rid, "TX", "baud_error_pct")["value"]) < 0.5
    assert repo.waves[rid]["TX"][2] == 10_000


def test_spi_divider_quantization():
    runner, repo = make_runner()
    rid = runner.run(RunRequest(protocol="spi", speed_hz=30_000_000))
    assert repo.runs[rid]["actual_hz"] == 26_666_666          # 80 MHz / 3
    assert abs(repo.value(rid, "SCLK", "freq_error_pct")["value"]) < 1.0
    assert repo.value(rid, "SCLK", "freq_error_req_pct")["value"] == pytest.approx(-11.1, abs=0.5)
    assert set(repo.waves[rid]) == {"SCLK", "MOSI"}


def test_i2c_internal_pullup_fails_fast_mode():
    runner, repo = make_runner()
    ok = runner.run(RunRequest(protocol="i2c", speed_hz=100_000))
    bad = runner.run(RunRequest(protocol="i2c", speed_hz=400_000))
    assert repo.runs[ok]["verdict"] == "PASS"
    assert repo.runs[bad]["verdict"] == "FAIL"
    rise = repo.value(bad, "SCL", "rise_30_70")
    assert rise["pass"] is False and rise["value"] > 300e-9


def test_i2c_external_pullup_passes_fast_mode():
    runner, repo = make_runner()
    rid = runner.run(RunRequest(protocol="i2c", speed_hz=400_000, pullup="external", pullup_ohm=2200))
    assert repo.runs[rid]["verdict"] == "PASS"
    assert repo.value(rid, "SCL", "rise_30_70")["value"] < 300e-9


def test_esp32_error_recorded():
    class Broken(MockEsp32):
        def run(self, cfg):
            raise Esp32Error("連不到 ESP32")

    runner, repo = make_runner(Broken())
    rid = runner.run(RunRequest(protocol="uart", speed_hz=9600))
    assert repo.runs[rid]["status"] == "error"
    assert "連不到" in repo.runs[rid]["note"]


def test_out_of_range_rejected():
    runner, _ = make_runner()
    with pytest.raises(ValueError):
        runner.run(RunRequest(protocol="i2c", speed_hz=5_000_000))


def test_busy():
    runner, _ = make_runner()
    runner._lock.acquire()
    try:
        with pytest.raises(Busy):
            runner.run(RunRequest(protocol="uart", speed_hz=9600))
    finally:
        runner._lock.release()
