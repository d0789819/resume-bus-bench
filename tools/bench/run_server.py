"""啟動 Bus Bench 後端；日誌同時輸出到主控台與 tools/bench/logs/server.log。

雙擊 start_server.cmd 執行；關閉視窗即停止。
"""
import logging
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
LOG = Path(__file__).resolve().parent / "logs" / "server.log"
LOG.parent.mkdir(exist_ok=True)

fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
root = logging.getLogger()
root.setLevel(logging.INFO)
for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG, mode="w", encoding="utf-8")):
    h.setFormatter(fmt)
    root.addHandler(h)

if __name__ == "__main__":
    # log_config=None：uvicorn 的日誌也走 root logger，一起寫進 server.log
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, app_dir=str(ROOT / "server"), log_config=None)
