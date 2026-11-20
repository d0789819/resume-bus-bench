"""示波器驅動。"""
from __future__ import annotations

from ..config import Settings
from .base import CaptureSetup, Channel, ScopeDriver, ScopeError, Waveform

__all__ = ["CaptureSetup", "Channel", "ScopeDriver", "ScopeError", "Waveform", "make_scope"]


def make_scope(settings: Settings) -> ScopeDriver:
    driver = settings.scope_driver.lower()
    if driver == "sim":
        from .sim import SimScope
        return SimScope(bandwidth_hz=settings.sim_bandwidth_hz)
    if driver == "tek":
        from .tek import TekScope
        if not settings.scope_resource:
            raise ScopeError("SCOPE_DRIVER=tek 時必須在 .env 設定 SCOPE_RESOURCE")
        return TekScope(settings.scope_resource, settings.visa_backend)
    raise ScopeError(f"未知的 SCOPE_DRIVER：{settings.scope_driver}（可用 sim / tek）")
