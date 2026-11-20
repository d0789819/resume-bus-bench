"""Bus Bench step 2：找 Tektronix 示波器，並用 server/app/scope/tek.py 實機跑一次 UART 擷取。

用法：python scope_probe.py [示波器IP] [通道，預設 CH1]
- 記錄每一道 SCPI 指令與回應（含 *ESR? / ALLEv? 錯誤訊息），用來判斷哪些指令不相容。
- ESP32 帳密從 .env 讀取，不會印出。
- 波形存成 logs/uart_115200.csv。
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGDIR = Path(__file__).resolve().parent / "logs"
sys.path.insert(0, str(ROOT / "server"))

T0 = time.perf_counter()


def p(*a) -> None:
    print(f"[{time.perf_counter() - T0:7.2f}s]", *a, flush=True)


def read_env() -> dict[str, str]:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def find_scope(ip_hint: str):
    import pyvisa
    rm = pyvisa.ResourceManager("@py")
    cands: list[str] = []
    if ip_hint:
        cands += [f"TCPIP0::{ip_hint}::inst0::INSTR", f"TCPIP0::{ip_hint}::4000::SOCKET"]
    arp = subprocess.run(["arp", "-a"], capture_output=True).stdout.decode("cp950", "replace")
    for ip, mac in re.findall(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f]{2}(?:-[0-9a-f]{2}){5})", arp, re.I):
        if mac.lower().startswith("08-00-11"):      # Tektronix OUI
            p(f"ARP: Tektronix MAC {mac} at {ip}")
            cands.append(f"TCPIP0::{ip}::inst0::INSTR")
    try:
        found = rm.list_resources("TCPIP?*::INSTR")
        p("VXI-11 discovery:", found)
        cands += list(found)
    except Exception as e:  # noqa: BLE001
        p("VXI-11 discovery failed:", e)
    tried = set()
    for res in cands:
        if res in tried:
            continue
        tried.add(res)
        try:
            inst = rm.open_resource(res, open_timeout=3000)
            inst.timeout = 3000
            inst.read_termination = "\n"
            inst.write_termination = "\n"
            idn = inst.query("*IDN?").strip()
            inst.close()
            p(f"{res} -> {idn}")
            if "TEKTRONIX" in idn.upper():
                ip = res.split("::")[1]
                return (res if "SOCKET" in res else f"TCPIP0::{ip}::inst0::INSTR"), idn
        except Exception as e:  # noqa: BLE001
            p(f"{res} -> {type(e).__name__}: {e}")
    return None, None


class Trace:
    """包住 pyvisa 資源，記錄每一道指令。"""

    def __init__(self, inst):
        object.__setattr__(self, "_i", inst)

    def write(self, cmd):
        t = time.perf_counter()
        r = self._i.write(cmd)
        p(f"  W {cmd}  ({(time.perf_counter() - t) * 1000:.0f} ms)")
        return r

    def query(self, cmd):
        t = time.perf_counter()
        try:
            r = self._i.query(cmd)
        except Exception as e:
            p(f"  Q {cmd} -> !! {type(e).__name__}: {e}  ({(time.perf_counter() - t) * 1000:.0f} ms)")
            raise
        p(f"  Q {cmd} -> {r.strip()[:160]!r}  ({(time.perf_counter() - t) * 1000:.0f} ms)")
        return r

    def query_binary_values(self, cmd, **kw):
        t = time.perf_counter()
        r = self._i.query_binary_values(cmd, **kw)
        p(f"  B {cmd} -> {len(r)} points  ({(time.perf_counter() - t) * 1000:.0f} ms)")
        return r

    def __getattr__(self, n):
        return getattr(self._i, n)

    def __setattr__(self, n, v):
        setattr(self._i, n, v)


def main() -> int:
    ip_hint = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    ch = (sys.argv[2].strip().upper() if len(sys.argv) > 2 and sys.argv[2].strip() else "CH1")
    env = read_env()
    if not ip_hint:
        m = re.match(r"TCPIP\d*::([^:]+)::", env.get("SCOPE_RESOURCE", ""))
        ip_hint = m.group(1) if m else ""
        p(f"scope IP from .env SCOPE_RESOURCE: {ip_hint or '(none)'}")

    p("== 0. network preflight ==")
    p(subprocess.run(["powershell", "-NoProfile", "-Command",
                      "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | "
                      "ForEach-Object { $a = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue; "
                      "'{0,-34} {1,-16}/{2}  {3,-8} {4}' -f $_.InterfaceAlias, $_.IPAddress, $_.PrefixLength, $_.PrefixOrigin, $a.Status }"],
                     capture_output=True).stdout.decode("cp950", "replace"))
    esp_ip = re.sub(r"^https?://", "", env.get("ESP32_URL", "")).split("/")[0].split(":")[0]
    for label, host in (("scope", ip_hint), ("esp32", esp_ip)):
        if not host:
            continue
        route = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Find-NetRoute -RemoteIPAddress {host} | Select-Object -First 1).InterfaceAlias"],
            capture_output=True).stdout.decode("cp950", "replace").strip()
        ping = subprocess.run(["ping", "-n", "2", "-w", "1000", host], capture_output=True).stdout.decode("cp950", "replace")
        lost = re.search(r"(\d+)% ", ping)
        p(f"{label} {host}: route via [{route}], ping loss {lost.group(1) + '%' if lost else '?'}")
        if label == "scope":
            import socket
            for port in (111, 4000, 80):
                try:
                    with socket.create_connection((host, port), timeout=1.0):
                        p(f"  tcp {port}: open")
                except OSError as e:
                    p(f"  tcp {port}: {type(e).__name__}")
    p(subprocess.run(["powershell", "-NoProfile", "-Command",
                      "Get-NetIPInterface -AddressFamily IPv4 -ConnectionState Connected | "
                      "Sort-Object InterfaceMetric | Format-Table InterfaceAlias,InterfaceMetric,Dhcp -AutoSize | Out-String"],
                     capture_output=True).stdout.decode("cp950", "replace"))

    p("== 1. find scope ==")
    res, idn = find_scope(ip_hint)
    if not res:
        p("沒有找到 Tektronix 示波器。請確認示波器 IP，重新執行並輸入 IP。")
        return 2
    p("SCOPE_RESOURCE =", res)

    import logging
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    for noisy in ("pyvisa", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from app import analysis
    from app.esp32_client import Esp32Client
    from app.models import NATIVE_KINDS, RunRequest
    from app.runner import evaluate, plan_capture
    from app.scope.tek import TekScope

    p("== 2. open with tek.py ==")
    scope = TekScope(res, "@py")
    scope._inst = Trace(scope._inst)
    p("IDN:", scope.idn(), "| new_style:", scope.new_style)

    esp = Esp32Client(env["ESP32_URL"], env.get("ESP32_USER", "admin"), env.get("ESP32_PASS", ""))
    try:
        p("== 3. ESP32 start UART 115200 0x55 ==")
        r = esp.run({"protocol": "uart", "speed_hz": 115200, "pattern": "0x55", "spi_mode": 0,
                     "loopback": False, "duration_ms": 30000})
        actual = int(r["status"].get("actual_hz") or 115200)
        p("ESP32 actual_hz:", actual)
        time.sleep(0.2)

        cap = plan_capture(RunRequest(protocol="uart", speed_hz=115200), actual, ch, "")
        p("capture plan:", cap)

        p("== 4. setup ==")
        scope.setup(cap)
        p("== 4b. read back settings ==")
        scope._inst.timeout = 2000      # 不支援的查詢只等 2 秒
        for q in ("SELect?", f"{ch}:SCAle?", f"{ch}:POSition?", f"{ch}:OFFSet?", f"{ch}:COUPling?",
                  f"{ch}:PRObe:GAIN?", f"{ch}:PRObe:ID:TYPe?", f"{ch}:BANdwidth?", f"{ch}:TERmination?",
                  "HORizontal:SCAle?", "HORizontal:RECOrdlength?", "HORizontal:POSition?",
                  "HORizontal:DELay:MODe?", "TRIGger:A:TYPe?", "TRIGger:A:EDGE:SOUrce?", "TRIGger:A:EDGE:SLOpe?",
                  f"TRIGger:A:LEVel:{ch}?", "TRIGger:A:MODe?", "ACQuire:MODe?", "ACQuire:STOPAfter?",
                  "DATa:RESOlution?"):
            try:
                scope._inst.query(q)
            except Exception:  # noqa: BLE001
                try:
                    scope._inst.clear()
                except Exception:  # noqa: BLE001
                    pass
        scope._inst.timeout = 15000
        scope._clear_errors()

        p("== 5. acquire ==")
        scope.acquire()
        p("== 6. fetch ==")
        wf = scope.fetch(ch)
        v = wf.volts
        p(f"points={v.size} x_zero={wf.x_zero:.4e} x_incr={wf.x_incr:.4e} "
          f"vmin={float(v.min()):.3f} vmax={float(v.max()):.3f}")
        LOGDIR.mkdir(exist_ok=True)
        with open(LOGDIR / "uart_115200.csv", "w", encoding="utf-8") as f:
            f.write("t_s,v\n")
            for i, val in enumerate(v):
                f.write(f"{wf.x_zero + i * wf.x_incr:.6e},{float(val):.4f}\n")

        p("== 7. analysis (Python) ==")
        res_a = analysis.analyze(v, wf.x_zero, wf.x_incr)
        for k, val in res_a.items():
            p(f"  {k} = {val}")
        for row in evaluate("uart", "TX", 115200, actual, res_a):
            p(f"  {row['name']:16s} {row['value']!s:24s} limit=[{row['limit_min']}, {row['limit_max']}] pass={row['pass']}")

        p("== 8. native measurements ==")
        for kind in NATIVE_KINDS["uart"] + ["frequency", "vpp"]:
            t = time.perf_counter()
            val = scope.native_measure(ch, kind)
            p(f"  native {kind} = {val}  ({(time.perf_counter() - t):.2f} s)")
    except Exception:  # noqa: BLE001
        p("!! exception:\n" + traceback.format_exc())
        try:
            scope._inst.query("ALLEv?")
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        try:
            esp.stop()
            p("ESP32 stopped")
        except Exception as e:  # noqa: BLE001
            p("ESP32 stop failed:", e)
        scope.close()
    p("== done ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
