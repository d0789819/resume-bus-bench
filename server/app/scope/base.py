"""示波器驅動的共同介面。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


class ScopeError(RuntimeError):
    pass


@dataclass
class Channel:
    source: str   # 示波器通道，例如 CH1
    signal: str   # 量測的訊號，例如 SCL


@dataclass
class CaptureSetup:
    protocol: str
    speed_hz: int
    actual_hz: int
    channels: list[Channel]
    h_scale: float                  # 秒 / 格（畫面 10 格）
    trigger_source: str
    trigger_slope: str = "FALL"     # RISE / FALL
    trigger_level: float = 1.65     # V
    v_scale: float = 1.0            # V / 格
    v_position: float = -3.0        # 格：0 V 放在中心線下方 3 格，0–3.3 V 都在畫面內
    record_length: int = 10_000
    params: dict = field(default_factory=dict)


@dataclass
class Waveform:
    source: str
    x_zero: float        # 第一點的時間（秒，相對於觸發點）
    x_incr: float        # 取樣間隔（秒）
    volts: np.ndarray

    def times(self) -> np.ndarray:
        return self.x_zero + np.arange(self.volts.size) * self.x_incr


class ScopeDriver(Protocol):
    name: str

    def idn(self) -> str: ...

    def setup(self, cap: CaptureSetup) -> None: ...

    def acquire(self, timeout_s: float = 10.0) -> None:
        """單次擷取（single sequence），擷取完成才返回。"""

    def fetch(self, source: str) -> Waveform: ...

    def native_measure(self, source: str, kind: str) -> float | None:
        """示波器內建量測（交叉比對用）；不支援時回傳 None。kind：frequency / rise_10_90 / fall_90_10 / duty / vpp"""

    def close(self) -> None: ...
