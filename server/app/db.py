"""PostgreSQL 存取。"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import SERVER_DIR

SQL_DIR = SERVER_DIR / "sql"

RUN_COLUMNS = """id, started_at, finished_at, protocol, speed_hz, actual_hz, params, sweep_id,
                 scope_idn, esp32_fw, status, verdict, note"""


class Repository:
    def __init__(self, dsn: str):
        self.pool = ConnectionPool(dsn, min_size=1, max_size=4, kwargs={"row_factory": dict_row}, open=True)

    def close(self) -> None:
        self.pool.close()

    def ping(self) -> bool:
        with self.pool.connection() as conn:
            return conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1

    def apply_schema(self) -> None:
        with self.pool.connection() as conn:
            for f in sorted(SQL_DIR.glob("*.sql")):
                conn.execute(f.read_text(encoding="utf-8"))

    # ---- 寫入 ----
    def create_run(self, protocol: str, speed_hz: int, params: dict, sweep_id: str | None = None,
                   note: str | None = None) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO test_run (protocol, speed_hz, params, sweep_id, note) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (protocol, speed_hz, Jsonb(params), sweep_id, note),
            ).fetchone()
            return row["id"]

    def finish_run(self, run_id: int, *, status: str, verdict: str | None = None, actual_hz: int | None = None,
                   scope_idn: str | None = None, esp32_fw: str | None = None, note: str | None = None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE test_run SET finished_at = now(), status = %s, verdict = %s, actual_hz = %s, "
                "scope_idn = %s, esp32_fw = %s, note = COALESCE(%s, note) WHERE id = %s",
                (status, verdict, actual_hz, scope_idn, esp32_fw, note, run_id),
            )

    def add_measurements(self, run_id: int, rows: list[dict]) -> None:
        if not rows:
            return
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO measurement (run_id, channel, name, value, unit, source, limit_min, limit_max, pass) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(run_id, r["channel"], r["name"], r["value"], r["unit"], r.get("source", "calc"),
                  r.get("limit_min"), r.get("limit_max"), r.get("pass")) for r in rows],
            )

    def add_waveform(self, run_id: int, channel: str, source: str, x_zero: float, x_incr: float, points) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO waveform (run_id, channel, source, x_zero, x_incr, points) "
                "VALUES (%s, %s, %s, %s, %s, %s::real[])",
                (run_id, channel, source, float(x_zero), float(x_incr), [float(p) for p in points]),
            )

    # ---- 讀取 ----
    def list_runs(self, protocol: str | None = None, sweep_id: str | None = None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
        where, args = [], []
        if protocol:
            where.append("protocol = %s")
            args.append(protocol)
        if sweep_id:
            where.append("sweep_id = %s")
            args.append(sweep_id)
        sql = f"SELECT {RUN_COLUMNS} FROM test_run"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
        with self.pool.connection() as conn:
            return conn.execute(sql, (*args, limit, offset)).fetchall()

    def get_run(self, run_id: int) -> dict | None:
        with self.pool.connection() as conn:
            run = conn.execute(f"SELECT {RUN_COLUMNS} FROM test_run WHERE id = %s", (run_id,)).fetchone()
            if run is None:
                return None
            run["measurements"] = conn.execute(
                "SELECT channel, name, value, unit, source, limit_min, limit_max, pass "
                "FROM measurement WHERE run_id = %s ORDER BY id", (run_id,)).fetchall()
            return run

    def get_waveforms(self, run_id: int) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT channel, source, x_zero, x_incr, y_unit, points FROM waveform "
                "WHERE run_id = %s ORDER BY source", (run_id,)).fetchall()

    def trend(self, protocol: str, metric: str, channel: str) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT r.id AS run_id, r.started_at, r.speed_hz, r.actual_hz, r.params, r.verdict, "
                "       m.value, m.limit_min, m.limit_max, m.pass "
                "FROM measurement m JOIN test_run r ON r.id = m.run_id "
                "WHERE r.protocol = %s AND m.name = %s AND m.channel = %s AND m.source = 'calc' "
                "  AND r.status = 'done' AND m.value IS NOT NULL "
                "ORDER BY r.speed_hz, r.id", (protocol, metric, channel)).fetchall()

    def export_rows(self, protocol: str | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT r.id AS run_id, r.started_at, r.protocol, r.speed_hz, r.actual_hz, r.verdict, "
               "       r.params->>'pattern' AS pattern, r.params->>'pullup' AS pullup, "
               "       r.params->>'pullup_ohm' AS pullup_ohm, r.scope_idn, r.esp32_fw, "
               "       m.channel, m.name, m.value, m.unit, m.source, m.limit_min, m.limit_max, m.pass "
               "FROM test_run r JOIN measurement m ON m.run_id = r.id")
        args: tuple = ()
        if protocol:
            sql += " WHERE r.protocol = %s"
            args = (protocol,)
        sql += " ORDER BY r.id, m.id"
        with self.pool.connection() as conn:
            return conn.execute(sql, args).fetchall()
