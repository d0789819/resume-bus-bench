"""Bus Bench 診斷：ESP32 網路連線、帳密一致性（只印是否一致）、VISA 資源。

用法：python diag.py <serial.log>
不會修改任何檔案；不會印出 .env 或 secrets.h 的內容。
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sh(cmd: list[str], timeout: float = 30) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = r.stdout + r.stderr
        for enc in ("utf-8", "cp950", "cp437"):
            try:
                return out.decode(enc)
            except UnicodeDecodeError:
                continue
        return out.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return f"<{cmd[0]} failed: {e}>"


def section(t: str) -> None:
    print(f"\n===== {t} =====", flush=True)


def read_env() -> dict[str, str]:
    env = {}
    p = ROOT / ".env"
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> None:
    serial_log = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    ip = None
    if serial_log and serial_log.exists():
        txt = serial_log.read_text(encoding="utf-8", errors="replace")
        m = re.findall(r"WiFi up: (\d+\.\d+\.\d+\.\d+)", txt)
        ip = m[-1] if m else None
    section("ESP32 IP from serial")
    print(ip or "NOT FOUND")

    env = read_env()
    section(".env keys (values hidden)")
    for k in sorted(env):
        shown = env[k] if k in ("ESP32_MOCK", "ESP32_URL", "ESP32_USER", "SCOPE_DRIVER", "SCOPE_RESOURCE",
                                "VISA_BACKEND", "SCOPE_CH_PRIMARY", "SCOPE_CH_SECONDARY") else "<hidden>"
        print(f"{k}={shown}")

    section("password consistency (.env ESP32_PASS vs firmware/src/secrets.h WEB_PASS)")
    sec = ROOT / "firmware" / "src" / "secrets.h"
    web_pass = None
    web_user = None
    if sec.exists():
        s = sec.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'WEB_PASS\b[^"\n]*"([^"]*)"', s)
        web_pass = m.group(1) if m else None
        m = re.search(r'WEB_USER\b[^"\n]*"([^"]*)"', s)
        web_user = m.group(1) if m else None
    print("secrets.h found:", sec.exists())
    print("ESP32_PASS == WEB_PASS:", (web_pass is not None and env.get("ESP32_PASS") == web_pass))
    print("ESP32_USER == WEB_USER:", (web_user is not None and env.get("ESP32_USER", "admin") == web_user))
    print("WEB_PASS is placeholder:", web_pass == "CHANGE_ME_WEB_PASSWORD")

    section("this PC: IPv4 config")
    cfg = sh(["ipconfig"])
    keep = [l for l in cfg.splitlines()
            if l.strip() and (not l.startswith(" ") or re.search(r"IPv4|Subnet|Gateway|子網路|閘道|Media State|媒體狀態", l))]
    print("\n".join(keep))

    if ip:
        section(f"ARP / ping {ip}")
        print(sh(["ping", "-n", "4", "-w", "1500", ip]))
        print(sh(["arp", "-a", ip]))
        print("expected ESP32 MAC: e8-9f-6d-f5-3e-e4")
        section(f"route to {ip}")
        print(sh(["powershell", "-NoProfile", "-Command",
                  f"Find-NetRoute -RemoteIPAddress {ip} | Select-Object -First 2 | Format-List InterfaceAlias,IPAddress,NextHop,DestinationPrefix"]))

        section(f"HTTP GET http://{ip}/api/info x10")
        import httpx
        ok, lat, first = 0, [], None
        for i in range(10):
            t = time.perf_counter()
            try:
                r = httpx.get(f"http://{ip}/api/info", timeout=3.0)
                dt = (time.perf_counter() - t) * 1000
                if r.status_code == 200:
                    ok += 1
                    lat.append(dt)
                    first = first or r.text
                print(f"  #{i+1}: HTTP {r.status_code} {dt:.0f} ms")
            except Exception as e:  # noqa: BLE001
                print(f"  #{i+1}: {type(e).__name__}: {e}")
            time.sleep(0.3)
        print(f"success {ok}/10" + (f", latency min/avg/max {min(lat):.0f}/{sum(lat)/len(lat):.0f}/{max(lat):.0f} ms" if lat else ""))
        print("info:", first)
        if first:
            section("auth checks")
            h = {"X-Requested-With": "busbench"}
            try:
                r = httpx.post(f"http://{ip}/api/run", headers=h, json={}, timeout=5)
                print("POST /api/run without credentials ->", r.status_code, "(expect 401)")
            except Exception as e:  # noqa: BLE001
                print("POST /api/run without credentials ->", e)
            try:
                r = httpx.post(f"http://{ip}/api/stop", headers=h,
                               auth=(env.get("ESP32_USER", "admin"), env.get("ESP32_PASS", "")), timeout=5)
                print("POST /api/stop with .env credentials ->", r.status_code, "(200 = ESP32_PASS matches flashed WEB_PASS)")
            except Exception as e:  # noqa: BLE001
                print("POST /api/stop with .env credentials ->", e)

    section("VISA")
    for dll in (r"C:\Windows\System32\visa64.dll", r"C:\Windows\System32\visa32.dll"):
        print(dll, "exists:", Path(dll).exists())
    try:
        import pyvisa
        print("pyvisa", pyvisa.__version__)
        for backend in ("", "@py"):
            label = backend or "default (system VISA)"
            try:
                rm = pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()
                print(f"[{label}] library: {rm.visalib}")
                res = rm.list_resources()
                print(f"[{label}] resources: {res}")
                for r in res:
                    if r.upper().startswith("ASRL"):
                        continue   # 不碰序列埠（ESP32 也在序列埠上）
                    try:
                        inst = rm.open_resource(r)
                        inst.timeout = 3000
                        print(f"[{label}] {r} *IDN? -> {inst.query('*IDN?').strip()}")
                        inst.close()
                    except Exception as e:  # noqa: BLE001
                        print(f"[{label}] {r} *IDN? failed: {e}")
                rm.close()
            except Exception as e:  # noqa: BLE001
                print(f"[{label}] unavailable: {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        print("pyvisa import failed:", e)

    section("network adapters (IPv4)")
    print(sh(["powershell", "-NoProfile", "-Command",
              "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' } | "
              "ForEach-Object { $a = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue; "
              "'{0} | {1} | {2}/{3} | {4} | {5} | {6}' -f $_.InterfaceAlias, $a.InterfaceDescription, $_.IPAddress, "
              "$_.PrefixLength, $_.PrefixOrigin, $a.Status, $a.MediaType }"]))

    section("scope search on wired (802.3) adapters only")
    neigh = sh(["powershell", "-NoProfile", "-Command",
                "Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' -and $_.MediaType -eq '802.3' } | "
                "ForEach-Object { Get-NetNeighbor -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
                "Where-Object { $_.State -notin @('Unreachable','Permanent','Incomplete') } | "
                "ForEach-Object { $_.IPAddress + ' ' + $_.LinkLayerAddress + ' ' + $_.State } }"])
    print(neigh.strip() or "(no neighbours on wired adapters)")
    cands = []
    for line in neigh.splitlines():
        m = re.match(r"(\d+\.\d+\.\d+\.\d+)\s", line.strip())
        if m and not m.group(1).startswith(("224.", "239.", "255.")) and not m.group(1).endswith(".255"):
            cands.append(m.group(1))
    import socket
    for host in cands[:16]:
        open_ports = []
        for port in (111, 4880, 4000, 5025, 80):
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    open_ports.append(port)
            except OSError:
                pass
        print(f"{host}: open ports {open_ports}")
        if not open_ports:
            continue
        try:
            import pyvisa
            rm = pyvisa.ResourceManager("@py")
            tries = []
            if 111 in open_ports:
                tries.append(f"TCPIP0::{host}::inst0::INSTR")
            if 4000 in open_ports:
                tries.append(f"TCPIP0::{host}::4000::SOCKET")
            if 5025 in open_ports:
                tries.append(f"TCPIP0::{host}::5025::SOCKET")
            for res in tries:
                try:
                    inst = rm.open_resource(res)
                    inst.timeout = 3000
                    inst.read_termination = "\n"
                    inst.write_termination = "\n"
                    print(f"  {res} *IDN? -> {inst.query('*IDN?').strip()}")
                    inst.close()
                except Exception as e:  # noqa: BLE001
                    print(f"  {res} failed: {type(e).__name__}: {e}")
            rm.close()
        except Exception as e:  # noqa: BLE001
            print("  pyvisa @py failed:", e)

    section("USB devices from Tektronix (VID 0699)")
    print(sh(["powershell", "-NoProfile", "-Command",
              "Get-CimInstance Win32_PnPEntity | Where-Object { $_.DeviceID -match 'VID_0699' } | "
              "Format-List Name,DeviceID,Status,ConfigManagerErrorCode"]))


if __name__ == "__main__":
    main()
