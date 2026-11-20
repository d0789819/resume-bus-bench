"""Tektronix 示波器驅動（PyVISA + SCPI）。

以 Tek 各系列共通的程式介面為主：
- 波形傳輸 DATa / WFMOutpre / CURVe? 在 TBS、DPO/MSO2000–4000、MDO3、MSO 4/5/6 系列都相同。
- 部分設定指令新舊系列寫法不同（例如 HORizontal:SCAle 與 HORizontal:MAIn:SCAle、
  觸發準位 TRIGger:A:LEVel 與 TRIGger:A:LEVel:CHx），這裡依序嘗試，失敗就換另一種。
- 示波器內建量測只用來交叉比對：舊系列用 MEASUrement:IMMed，新系列（MSO 2/4/5/6）用 MEASUrement:ADDMEAS；
  依 *IDN? 的型號只用其中一種（用錯系列的查詢不會回應，只會等到逾時）。

實機驗證：MDO3014（FV:v1.10，LAN / VXI-11 + pyvisa-py）。setup / acquire / fetch / IMMed 指令全部相容；
DISplay:GLObal:CHx:STATE 不支援，會自動改用 SELect:CHx。

實機型號不同時，指令可能需要依該型號的程式設計手冊微調。
"""
from __future__ import annotations

import logging
import re
import time

import numpy as np

from .base import CaptureSetup, ScopeError, Waveform

log = logging.getLogger(__name__)

INVALID = 9.0e37   # Tek 量測無效時回傳 9.91E37

# 量測種類 -> (舊系列 IMMed 類型, 新系列 ADDMEAS 類型)
NATIVE_TYPES = {
    "frequency": ("FREQuency", "FREQUENCY"),
    "rise_10_90": ("RISe", "RISETIME"),
    "fall_90_10": ("FALL", "FALLTIME"),
    "duty": ("PDUty", "PDUTY"),
    "vpp": ("PK2pk", "PK2PK"),
}


class TekScope:
    name = "tek"

    def __init__(self, resource: str, backend: str = "", timeout_ms: int = 15000):
        import pyvisa

        try:
            self._rm = pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()
            self._inst = self._rm.open_resource(resource)
        except Exception as e:  # noqa: BLE001
            raise ScopeError(f"無法開啟示波器 {resource}：{e}") from e
        self._inst.timeout = timeout_ms
        self._inst.read_termination = "\n"
        self._inst.write_termination = "\n"
        self.resource = resource
        self._write("HEADer OFF")
        self._idn = self._query("*IDN?")
        model = self._idn.split(",")[1].strip() if "," in self._idn else ""
        # 新式指令集：MSO2/4/5/6 系列，型號是 MSO/LPD + 兩位數字（+ 字尾），例如 MSO24、MSO44B、MSO58LP、LPD64。
        # 舊款四位數字型號（MSO2024B、MSO4034、MSO5034B）與 MDO3/TBS/DPO 系列都是舊式指令集。
        self.new_style = bool(re.match(r"^(MSO|LPD)[2456]\d[A-Z]*$", model, re.I))
        self._clear_errors()

    # ---- 基本 I/O ----
    def _write(self, cmd: str) -> None:
        self._inst.write(cmd)

    def _query(self, cmd: str) -> str:
        return self._inst.query(cmd).strip()

    def _clear_errors(self) -> None:
        try:
            self._query("*ESR?")
            self._query("ALLEv?")
        except Exception:  # noqa: BLE001
            pass

    def _try(self, *cmds: str) -> bool:
        """依序嘗試幾種寫法，第一個沒有產生錯誤的就算成功。"""
        for cmd in cmds:
            self._clear_errors()
            try:
                self._write(cmd)
                if int(self._query("*ESR?")) & 0b0011_1100 == 0:   # 指令 / 執行 / 裝置 / 查詢錯誤位元
                    return True
            except Exception:  # noqa: BLE001
                continue
        log.warning("示波器不接受以下任一指令：%s", " | ".join(cmds))
        return False

    # ---- ScopeDriver 介面 ----
    def idn(self) -> str:
        return self._idn

    def setup(self, cap: CaptureSetup) -> None:
        self._write("ACQuire:STATE STOP")
        for ch in cap.channels:
            src = ch.source.upper()
            self._try(f"DISplay:GLObal:{src}:STATE ON", f"SELect:{src} ON")
            self._try(f"{src}:COUPling DC")
            self._try(f"{src}:SCAle {cap.v_scale:g}")
            self._try(f"{src}:POSition {cap.v_position:g}")
            self._try(f"{src}:OFFSet 0")
        self._try(f"HORizontal:SCAle {cap.h_scale:.6g}", f"HORizontal:MAIn:SCAle {cap.h_scale:.6g}")
        self._try(f"HORizontal:RECOrdlength {cap.record_length}")
        trig = cap.trigger_source.upper()
        self._try("TRIGger:A:TYPe EDGe")
        self._try(f"TRIGger:A:EDGE:SOUrce {trig}")
        self._try(f"TRIGger:A:EDGE:SLOpe {'RISe' if cap.trigger_slope.upper().startswith('R') else 'FALL'}")
        self._try("TRIGger:A:EDGE:COUPling DC")
        self._try(f"TRIGger:A:LEVel:{trig} {cap.trigger_level:g}", f"TRIGger:A:LEVel {cap.trigger_level:g}")
        self._try("TRIGger:A:MODe NORMal")
        self._try("ACQuire:MODe SAMple")
        self._write("ACQuire:STOPAfter SEQuence")

    def acquire(self, timeout_s: float = 10.0) -> None:
        self._write("ACQuire:STATE ON")
        t0 = time.time()
        time.sleep(0.05)
        while time.time() - t0 < timeout_s:
            state = self._query("ACQuire:STATE?")
            if state.strip() in ("0", "OFF", "STOP"):
                return
            time.sleep(0.05)
        self._write("ACQuire:STATE STOP")
        raise ScopeError(f"{timeout_s:.0f} 秒內沒有觸發：請確認探棒接線、觸發通道與 ESP32 是否正在送訊號")

    def fetch(self, source: str) -> Waveform:
        src = source.upper()
        self._write(f"DATa:SOUrce {src}")
        self._write("DATa:ENCdg RIBinary")
        self._try("WFMOutpre:BYT_Nr 1", "DATa:WIDth 1")
        self._write("DATa:STARt 1")
        try:
            n = int(float(self._query("HORizontal:RECOrdlength?")))
        except Exception:  # noqa: BLE001
            n = 1_000_000_000    # 大於紀錄長度時 Tek 會自動截到最後一點
        self._write(f"DATa:STOP {n}")
        x_incr = float(self._query("WFMOutpre:XINcr?"))
        x_zero = float(self._query("WFMOutpre:XZEro?"))
        y_mult = float(self._query("WFMOutpre:YMUlt?"))
        y_off = float(self._query("WFMOutpre:YOFf?"))
        y_zero = float(self._query("WFMOutpre:YZEro?"))
        raw = self._inst.query_binary_values("CURVe?", datatype="b", is_big_endian=True, container=np.array)
        volts = (raw.astype(np.float64) - y_off) * y_mult + y_zero
        if volts.size == 0:
            raise ScopeError(f"{src} 沒有波形資料")
        return Waveform(src, x_zero, x_incr, volts.astype(np.float32))

    def native_measure(self, source: str, kind: str) -> float | None:
        if kind not in NATIVE_TYPES:
            return None
        legacy, new = NATIVE_TYPES[kind]
        fn = self._measure_new if self.new_style else self._measure_legacy
        try:
            val = fn(source.upper(), legacy, new)
        except Exception as e:  # noqa: BLE001
            log.debug("內建量測失敗（%s %s）：%s", source, kind, e)
            self._recover()
            return None
        # 9.91E37 = 示波器無法量測（例如畫面中沒有完整週期）
        return val if val is not None and abs(val) < INVALID else None

    def _recover(self) -> None:
        """查詢逾時後清除裝置緩衝與錯誤佇列，避免下一道查詢讀到殘留回應。"""
        try:
            self._inst.clear()
        except Exception:  # noqa: BLE001
            pass
        self._clear_errors()

    def _measure_legacy(self, src: str, legacy: str, _new: str) -> float | None:
        self._clear_errors()
        self._write(f"MEASUrement:IMMed:SOUrce1 {src}")
        self._write(f"MEASUrement:IMMed:TYPe {legacy}")
        return float(self._query("MEASUrement:IMMed:VALue?"))

    def _measure_new(self, src: str, _legacy: str, new: str) -> float | None:
        self._clear_errors()
        self._write("MEASUrement:DELETEALL")
        self._write(f"MEASUrement:ADDMEAS {new}")
        self._write(f"MEASUrement:MEAS1:SOUrce1 {src}")
        time.sleep(0.2)
        return float(self._query("MEASUrement:MEAS1:RESUlts:CURRentacq:MEAN?"))

    def close(self) -> None:
        try:
            self._inst.close()
            self._rm.close()
        except Exception:  # noqa: BLE001
            pass
