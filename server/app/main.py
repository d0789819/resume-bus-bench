"""Bus Bench 後端：REST API + 靜態儀表板。

啟動：  .venv\\Scripts\\python.exe -m uvicorn app.main:app --app-dir server --port 8000
"""
from __future__ import annotations

import csv
import io
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import analysis, limits
from .config import SERVER_DIR, get_settings
from .db import Repository
from .esp32_client import Esp32Client, MockEsp32
from .models import GPIO, PRESETS, RANGES, SIGNAL_METRICS, SIGNALS, RunRequest, SweepRequest
from .runner import Busy, Runner, ScopeProvider, SweepManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC_DIR = SERVER_DIR / "app" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    repo = Repository(s.database_url)
    repo.apply_schema()
    esp32 = MockEsp32() if s.esp32_mock else Esp32Client(s.esp32_url, s.esp32_user, s.esp32_pass)
    scopes = ScopeProvider(s)
    runner = Runner(repo, esp32, scopes, s)
    app.state.settings = s
    app.state.repo = repo
    app.state.esp32 = esp32
    app.state.scopes = scopes
    app.state.runner = runner
    app.state.sweeps = SweepManager(runner)
    yield
    scopes.close()
    esp32.close()
    repo.close()


app = FastAPI(title="Bus Bench", lifespan=lifespan)


def _run_detail(repo: Repository, run_id: int, max_points: int) -> dict:
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, "找不到這筆測試")
    run["waveforms"] = [
        {"channel": w["channel"], "source": w["source"], "y_unit": w["y_unit"],
         **analysis.decimate(w["x_zero"], w["x_incr"], w["points"], max_points)}
        for w in repo.get_waveforms(run_id)
    ]
    return run


@app.get("/api/health")
def health(request: Request):
    st = request.app.state
    out: dict = {"busy": st.runner.busy}
    try:
        out["db"] = {"ok": st.repo.ping()}
    except Exception as e:  # noqa: BLE001
        out["db"] = {"ok": False, "error": str(e)}
    esp = {"mode": st.esp32.mode, "url": st.esp32.base_url}
    try:
        info = st.esp32.info()
        esp.update(ok=True, fw=info.get("fw"), ip=info.get("ip"), chip=info.get("chip"))
    except Exception as e:  # noqa: BLE001
        esp.update(ok=False, error=str(e))
    out["esp32"] = esp
    sc = {"driver": st.settings.scope_driver, "resource": st.settings.scope_resource or None}
    if st.runner.busy:
        sc["ok"] = None   # 測試中不打擾示波器
    else:
        try:
            sc.update(ok=True, idn=st.scopes.get().idn())
        except Exception as e:  # noqa: BLE001
            st.scopes.reset()
            sc.update(ok=False, error=str(e))
    out["scope"] = sc
    return out


@app.get("/api/config")
def config(request: Request):
    s = request.app.state.settings
    chans = [s.scope_ch_primary, s.scope_ch_secondary]
    wiring = {
        p: [{"signal": sig, "gpio": GPIO[sig], "scope": chans[i] or None} for i, sig in enumerate(sigs)]
        for p, sigs in SIGNALS.items()
    }
    lim_table = {}
    for p, speeds in PRESETS.items():
        for sig in SIGNALS[p]:
            for spd in sorted({speeds[0], speeds[len(speeds) // 2], speeds[-1]}):
                lim_table.setdefault(p, {}).setdefault(sig, {})[spd] = {
                    k: asdict(v) for k, v in limits.limits_for(p, sig, spd).items()}
    return {
        "presets": PRESETS,
        "ranges": RANGES,
        "signals": SIGNALS,
        "signal_metrics": {f"{p}:{sig}": m for (p, sig), m in SIGNAL_METRICS.items()},
        "metrics": {k: {"unit": u, "label": lbl} for k, (u, lbl) in analysis.METRICS.items()},
        "wiring": wiring,
        "limits": lim_table,
        "simulated_scope": s.scope_driver == "sim",
        "esp32_mock": s.esp32_mock,
    }


@app.post("/api/runs")
def create_run(req: RunRequest, request: Request, points: int = Query(2000, ge=100, le=20000)):
    st = request.app.state
    try:
        run_id = st.runner.run(req)
    except Busy as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return _run_detail(st.repo, run_id, points)


@app.get("/api/runs")
def list_runs(request: Request, protocol: str | None = None, sweep_id: str | None = None,
              limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    return request.app.state.repo.list_runs(protocol, sweep_id, limit, offset)


@app.get("/api/runs/{run_id}")
def get_run(run_id: int, request: Request, points: int = Query(2000, ge=100, le=20000)):
    return _run_detail(request.app.state.repo, run_id, points)


@app.post("/api/sweeps")
def create_sweep(req: SweepRequest, request: Request):
    try:
        return request.app.state.sweeps.start(req)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/sweeps/{sweep_id}")
def get_sweep(sweep_id: str, request: Request):
    sw = request.app.state.sweeps.get(sweep_id)
    if sw is None:
        raise HTTPException(404, "找不到這次掃描")
    return sw


@app.get("/api/trend")
def trend(request: Request, protocol: str, metric: str, channel: str):
    return request.app.state.repo.trend(protocol, metric, channel)


@app.get("/api/export.csv")
def export_csv(request: Request, protocol: str | None = None):
    rows = request.app.state.repo.export_rows(protocol)
    buf = io.StringIO()
    buf.write("﻿")   # BOM：讓 Excel 正確顯示中文
    cols = ["run_id", "started_at", "protocol", "speed_hz", "actual_hz", "verdict", "pattern", "pullup",
            "pullup_ohm", "scope_idn", "esp32_fw", "channel", "name", "value", "unit", "source",
            "limit_min", "limit_max", "pass"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)
    name = f"busbench_{protocol or 'all'}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
