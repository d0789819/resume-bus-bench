"""測試流程：ESP32 送訊號 → 示波器擷取 → 計算量測 → 判定 → 存資料庫。"""
from __future__ import annotations

import logging
import threading
import time
import uuid

from . import analysis, limits
from .config import Settings
from .models import NATIVE_KINDS, PRESETS, RANGES, SIGNAL_METRICS, SIGNALS, RunRequest, SweepRequest
from .scope import CaptureSetup, Channel, ScopeDriver, make_scope

log = logging.getLogger(__name__)


class Busy(RuntimeError):
    pass


class ScopeProvider:
    """維持一個示波器連線；出錯時關閉，下次再重新連線。"""

    def __init__(self, settings: Settings, factory=make_scope):
        self.settings = settings
        self._factory = factory
        self._scope: ScopeDriver | None = None

    def get(self) -> ScopeDriver:
        if self._scope is None:
            self._scope = self._factory(self.settings)
        return self._scope

    def reset(self) -> None:
        if self._scope is not None:
            try:
                self._scope.close()
            finally:
                self._scope = None

    close = reset


def plan_capture(req: RunRequest, actual_hz: int, primary: str, secondary: str) -> CaptureSetup:
    """依協定與速度決定通道、時基與觸發。"""
    sigs = SIGNALS[req.protocol]
    channels = [Channel(primary, sigs[0])]
    if len(sigs) > 1 and secondary:
        channels.append(Channel(secondary, sigs[1]))
    f = float(actual_hz)
    if req.protocol == "uart":
        h_scale, trig, slope = 2.0 / f, primary, "FALL"            # 畫面約 20 個位元，觸發在起始位元
    elif req.protocol == "spi":
        h_scale, trig = 1.2 / f, primary                             # 畫面約 12 個 SCLK 週期
        slope = "RISE" if req.spi_mode in (0, 1) else "FALL"         # 觸發在 SCLK 前緣
    else:
        h_scale = 2.4 / f                                            # 觸發點之後放得下整個位址框
        trig, slope = (secondary or primary), "FALL"                 # SDA 下降 = 起始條件
    params = req.model_dump(exclude={"protocol", "speed_hz", "note"})
    return CaptureSetup(protocol=req.protocol, speed_hz=req.speed_hz, actual_hz=actual_hz, channels=channels,
                        h_scale=h_scale, trigger_source=trig, trigger_slope=slope, params=params)


def evaluate(protocol: str, signal: str, speed_hz: int, actual_hz: int, res: dict) -> list[dict]:
    """把一個通道的分析結果轉成要存的量測列（含上下限與判定）。"""
    derived = analysis.derive(protocol, signal, speed_hz, actual_hz, res)
    lims = limits.limits_for(protocol, signal, speed_hz)
    rows = []
    for name in SIGNAL_METRICS[(protocol, signal)]:
        value = derived.get(name)
        lim = lims.get(name)
        rows.append({
            "channel": signal,
            "name": name,
            "value": None if value is None else float(value),
            "unit": analysis.METRICS[name][0],
            "source": "calc",
            "limit_min": lim.min if lim else None,
            "limit_max": lim.max if lim else None,
            "pass": limits.judge(value, lim),
        })
    return rows


class Runner:
    def __init__(self, repo, esp32, scopes: ScopeProvider, settings: Settings, settle_s: float = 0.15):
        self.repo = repo
        self.esp32 = esp32
        self.scopes = scopes
        self.settings = settings
        self.settle_s = settle_s
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def run(self, req: RunRequest, sweep_id: str | None = None, wait: bool = False) -> int:
        if not self._lock.acquire(blocking=wait):
            raise Busy("目前有測試正在執行")
        try:
            return self._run(req, sweep_id)
        finally:
            self._lock.release()

    def _run(self, req: RunRequest, sweep_id: str | None) -> int:
        lo, hi = RANGES[req.protocol]
        if not lo <= req.speed_hz <= hi:
            raise ValueError(f"{req.protocol.upper()} 速度需在 {lo}–{hi} Hz")
        params = req.model_dump(exclude={"protocol", "speed_hz", "note"})
        run_id = self.repo.create_run(req.protocol, req.speed_hz, params, sweep_id, req.note)
        esp_running = False
        actual = None
        fw = None
        idn = None
        try:
            fw = self.esp32.info().get("fw")
            resp = self.esp32.run({
                "protocol": req.protocol, "speed_hz": req.speed_hz, "pattern": req.pattern,
                "spi_mode": req.spi_mode, "loopback": req.loopback, "duration_ms": 15000,
            })
            esp_running = True
            actual = int(resp["status"].get("actual_hz") or req.speed_hz)
            time.sleep(self.settle_s)

            scope = self.scopes.get()
            idn = scope.idn()
            cap = plan_capture(req, actual, self.settings.scope_ch_primary, self.settings.scope_ch_secondary)
            try:
                scope.setup(cap)
                scope.acquire()
                rows, notes = [], []
                for i, ch in enumerate(cap.channels):
                    wf = scope.fetch(ch.source)
                    self.repo.add_waveform(run_id, ch.signal, ch.source, wf.x_zero, wf.x_incr, wf.volts)
                    res = analysis.analyze(wf.volts, wf.x_zero, wf.x_incr)
                    if res.get("error"):
                        notes.append(f"{ch.signal}：{res['error']}")
                    rows += evaluate(req.protocol, ch.signal, req.speed_hz, actual, res)
                    if i == 0:   # 主通道再用示波器內建量測交叉比對
                        for kind in NATIVE_KINDS[req.protocol]:
                            val = scope.native_measure(ch.source, kind)
                            if val is not None:
                                rows.append({"channel": ch.signal, "name": kind, "value": float(val),
                                             "unit": analysis.METRICS[kind][0], "source": "scope",
                                             "limit_min": None, "limit_max": None, "pass": None})
            except Exception:
                self.scopes.reset()
                raise

            self.esp32.stop()
            esp_running = False
            if req.loopback:
                st = self.esp32.status()
                rows.append({"channel": "loopback", "name": "loop_errors", "value": float(st.get("loop_errors", 0)),
                             "unit": "", "source": "esp32", "limit_min": None, "limit_max": 0.0,
                             "pass": st.get("loop_checked", 0) > 0 and st.get("loop_errors", 0) == 0})

            judged = [r["pass"] for r in rows if r["pass"] is not None]
            verdict = "FAIL" if (notes or any(p is False for p in judged)) else ("PASS" if judged else None)
            self.repo.add_measurements(run_id, rows)
            self.repo.finish_run(run_id, status="done", verdict=verdict, actual_hz=actual, scope_idn=idn,
                                 esp32_fw=fw, note="；".join(notes) or None)
        except Exception as e:  # noqa: BLE001 — 任何錯誤都記在這筆測試上，不讓 API 500
            log.exception("run %s failed", run_id)
            self.repo.finish_run(run_id, status="error", actual_hz=actual, scope_idn=idn, esp32_fw=fw,
                                 note=str(e)[:500])
        finally:
            if esp_running:
                try:
                    self.esp32.stop()
                except Exception:  # noqa: BLE001
                    pass
        return run_id


class SweepManager:
    """在背景依序跑一組速度。"""

    def __init__(self, runner: Runner):
        self.runner = runner
        self._sweeps: dict[str, dict] = {}

    def start(self, req: SweepRequest) -> dict:
        speeds = req.speeds or PRESETS[req.protocol]
        lo, hi = RANGES[req.protocol]
        bad = [s for s in speeds if not lo <= s <= hi]
        if bad:
            raise ValueError(f"超出範圍的速度：{bad}（{req.protocol.upper()} 需在 {lo}–{hi} Hz）")
        sid = str(uuid.uuid4())
        state = {"id": sid, "protocol": req.protocol, "speeds": speeds, "total": len(speeds), "done": 0,
                 "run_ids": [], "status": "running", "error": None}
        self._sweeps[sid] = state
        base = req.model_dump(exclude={"speeds"})
        threading.Thread(target=self._work, args=(state, base), daemon=True).start()
        return state

    def _work(self, state: dict, base: dict) -> None:
        try:
            for s in state["speeds"]:
                rid = self.runner.run(RunRequest(**base, speed_hz=s), sweep_id=state["id"], wait=True)
                state["run_ids"].append(rid)
                state["done"] += 1
            state["status"] = "done"
        except Exception as e:  # noqa: BLE001
            log.exception("sweep %s failed", state["id"])
            state["status"] = "error"
            state["error"] = str(e)

    def get(self, sid: str) -> dict | None:
        return self._sweeps.get(sid)
