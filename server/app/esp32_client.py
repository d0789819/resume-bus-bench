"""ESP32 Bus Bench 韌體的 REST 用戶端（以及不接硬體時用的模擬版）。"""
from __future__ import annotations

import math
import time

import httpx


class Esp32Error(RuntimeError):
    pass


class Esp32Client:
    mode = "real"

    def __init__(self, base_url: str, user: str, password: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self._c = httpx.Client(
            base_url=self.base_url,
            auth=(user, password),
            headers={"X-Requested-With": "busbench"},   # 韌體用來擋 CSRF 的自訂標頭
            timeout=timeout,
        )

    def _req(self, method: str, path: str, json: dict | None = None) -> dict:
        try:
            r = self._c.request(method, path, json=json)
        except httpx.HTTPError as e:
            raise Esp32Error(f"連不到 ESP32（{self.base_url}）：{e}") from e
        try:
            data = r.json()
        except ValueError:
            raise Esp32Error(f"ESP32 回應 HTTP {r.status_code}") from None
        if r.status_code >= 400 or data.get("ok") is False:
            if r.status_code == 401:
                raise Esp32Error("ESP32 帳密錯誤：請確認 .env 的 ESP32_PASS 與韌體 secrets.h 的 WEB_PASS 一致")
            raise Esp32Error(data.get("error") or f"ESP32 回應 HTTP {r.status_code}")
        return data

    def info(self) -> dict:
        return self._req("GET", "/api/info")

    def status(self) -> dict:
        return self._req("GET", "/api/status")

    def run(self, cfg: dict) -> dict:
        return self._req("POST", "/api/run", json=cfg)

    def stop(self) -> dict:
        return self._req("POST", "/api/stop")

    def close(self) -> None:
        self._c.close()


class MockEsp32:
    """不接 ESP32 時使用：行為與韌體一致（含 SPI 除頻造成的實際頻率）。"""

    mode = "mock"
    base_url = "mock://esp32"

    def __init__(self):
        self._status = {"running": False, "run_count": 0, "protocol": "none", "actual_hz": 0,
                        "frames": 0, "bytes": 0, "nacks": 0, "loop_checked": 0, "loop_errors": 0}
        self._t0 = 0.0

    @staticmethod
    def actual_hz(protocol: str, speed_hz: int) -> int:
        if protocol == "spi":   # ESP32 SPI 時脈 = 80 MHz / 整數除頻
            return int(80_000_000 / math.ceil(80_000_000 / speed_hz))
        return int(speed_hz)

    def info(self) -> dict:
        return {"fw": "mock", "chip": "MOCK", "ip": "-", "mac": "-"}

    def status(self) -> dict:
        return dict(self._status)

    def run(self, cfg: dict) -> dict:
        self._t0 = time.time()
        self._status.update(running=True, run_count=self._status["run_count"] + 1, protocol=cfg["protocol"],
                            speed_hz=cfg["speed_hz"], actual_hz=self.actual_hz(cfg["protocol"], cfg["speed_hz"]))
        return {"ok": True, "status": self.status()}

    def stop(self) -> dict:
        self._status["running"] = False
        return {"ok": True, "status": self.status()}

    def close(self) -> None:
        pass
