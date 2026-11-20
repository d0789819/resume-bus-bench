"""設定：從專案根目錄的 .env 讀取。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SERVER_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql://bench:bench@127.0.0.1:5432/busbench"

    # ESP32
    esp32_mock: bool = True
    esp32_url: str = "http://busbench.local"
    esp32_user: str = "admin"
    esp32_pass: str = ""

    # 示波器
    scope_driver: str = "sim"          # sim | tek
    scope_resource: str = ""           # VISA 資源字串
    visa_backend: str = ""             # 空白 = 系統 VISA；@py = pyvisa-py
    scope_ch_primary: str = "CH1"
    scope_ch_secondary: str = "CH2"    # 只有一支探棒時留空
    sim_bandwidth_hz: float = 100e6    # 模擬示波器的頻寬


@lru_cache
def get_settings() -> Settings:
    return Settings()
