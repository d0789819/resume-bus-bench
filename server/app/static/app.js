"use strict";

// ---------- 小工具 ----------
const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
  });
  let data = null;
  try { data = await res.json(); } catch { /* 非 JSON */ }
  if (!res.ok) {
    const d = data && data.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("；") : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

const PREFIX = [[1e9, "G"], [1e6, "M"], [1e3, "k"], [1, ""], [1e-3, "m"], [1e-6, "µ"], [1e-9, "n"], [1e-12, "p"]];
function si(v, unit) {
  if (v === null || v === undefined || !isFinite(v)) return "—";
  if (unit === "%") return `${v.toFixed(2)} %`;
  if (!unit) return String(Math.round(v));
  const a = Math.abs(v);
  if (a < 1e-15) return `0 ${unit}`;   // 浮點誤差造成的極小值（例如軸上的 0）
  for (const [f, p] of PREFIX) {
    if (a >= f * 0.9995) return `${Number((v / f).toPrecision(4))} ${p}${unit}`;
  }
  return `${Number((v / 1e-12).toPrecision(4))} p${unit}`;
}
function limitText(m) {
  const lo = m.limit_min, hi = m.limit_max;
  if (lo == null && hi == null) return "";
  if (lo != null && hi != null) return `${si(lo, m.unit)} … ${si(hi, m.unit)}`;
  return lo != null ? `≥ ${si(lo, m.unit)}` : `≤ ${si(hi, m.unit)}`;
}
const fmtTime = (s) => new Date(s).toLocaleString("zh-TW", { hour12: false });

// ---------- 狀態 ----------
let CFG = null;
let waveChart = null;
let trendChart = null;
let currentRunId = null;
let busy = false;

function setMsg(text, isErr = false) {
  const el = $("msg");
  el.textContent = text || "";
  el.className = "msg" + (isErr ? " err" : "");
}
function setBusy(b) {
  busy = b;
  $("btnRun").disabled = b;
  $("btnSweep").disabled = b;
}

// ---------- 連線狀態 ----------
async function loadHealth() {
  const box = $("chips");
  box.innerHTML = '<span class="chip">檢查連線中…</span>';
  try {
    const h = await api("/api/health");
    const chips = [];
    chips.push([h.db.ok, h.db.ok ? "資料庫" : `資料庫：${h.db.error}`]);
    const e = h.esp32;
    const espText = e.mode === "mock" ? "ESP32：模擬" : e.ok ? `ESP32 ${e.fw} @ ${e.ip}` : `ESP32：${e.error}`;
    chips.push([e.ok, espText]);
    const s = h.scope;
    const scopeText = s.ok === false ? `示波器：${s.error}` : s.driver === "sim" ? "示波器：模擬" : `示波器：${s.idn || s.resource}`;
    chips.push([s.ok !== false, scopeText]);
    box.innerHTML = chips.map(([ok, t]) => `<span class="chip ${ok ? "ok" : "bad"}" title="${esc(t)}">${esc(t)}</span>`).join("");
  } catch (err) {
    box.innerHTML = `<span class="chip bad">後端無回應：${esc(err.message)}</span>`;
  }
}

// ---------- 設定表單 ----------
function protocol() { return $("protocol").value; }

function onProtocolChange() {
  const p = protocol();
  document.querySelectorAll(".only-spi").forEach((el) => (el.hidden = p !== "spi"));
  document.querySelectorAll(".only-i2c").forEach((el) => (el.hidden = p !== "i2c"));
  document.querySelectorAll(".only-sim").forEach((el) => (el.hidden = !(CFG.simulated_scope && p === "i2c")));
  const sel = $("speedPreset");
  const unit = p === "uart" ? "Bd" : "Hz";
  sel.innerHTML = CFG.presets[p].map((v) => `<option value="${v}">${si(v, unit)}</option>`).join("") +
    '<option value="custom">自訂…</option>';
  const def = { uart: 115200, spi: 1000000, i2c: 100000 }[p];
  sel.value = String(def);
  $("speed").value = def;
  const [lo, hi] = CFG.ranges[p];
  $("speedHint").textContent = `範圍 ${si(lo, unit)} – ${si(hi, unit)}`;
  renderWiring();
  fillTrendMetrics();
  loadTrend();
}

function renderWiring() {
  const rows = CFG.wiring[protocol()];
  const colors = { CH1: css("--ch1"), CH2: css("--ch2") };
  $("wiring").innerHTML = rows.map((r) =>
    `<tr><td><span class="swatch" style="background:${colors[r.scope] || css("--muted")}"></span>${esc(r.scope || "（未接）")}</td>` +
    `<td>${esc(r.signal)}</td><td>GPIO ${r.gpio}</td></tr>`).join("");
}

function formRequest() {
  const p = protocol();
  const req = {
    protocol: p,
    speed_hz: Number($("speed").value),
    pattern: $("pattern").value,
    loopback: $("loopback").checked,
    note: $("note").value.trim() || null,
  };
  if (p === "spi") req.spi_mode = Number($("spiMode").value);
  if (p === "i2c") {
    req.pullup = document.querySelector("input[name=pullup]:checked").value;
    if (req.pullup === "external") req.pullup_ohm = Number($("pullupOhm").value);
    req.bus_cap_pf = Number($("busCap").value) || 20;
  }
  return req;
}

// ---------- 執行 ----------
async function runOnce() {
  if (busy) return;
  const req = formRequest();
  setBusy(true);
  setMsg("測試中…");
  try {
    const run = await api("/api/runs", { method: "POST", body: JSON.stringify(req) });
    renderRun(run);
    setMsg(run.status === "error" ? "測試失敗，原因見結果區。" : `完成：#${run.id}`, run.status === "error");
    await Promise.all([loadHistory(), loadTrend()]);
  } catch (err) {
    setMsg(err.message, true);
  } finally {
    setBusy(false);
  }
}

async function runSweep() {
  if (busy) return;
  const req = formRequest();
  delete req.speed_hz;
  setBusy(true);
  setMsg("");
  $("sweepBox").hidden = false;
  try {
    let sw = await api("/api/sweeps", { method: "POST", body: JSON.stringify(req) });
    let shown = 0;
    while (true) {
      $("sweepBar").style.width = `${(sw.done / sw.total) * 100}%`;
      $("sweepText").textContent = `速度掃描 ${sw.done} / ${sw.total}`;
      if (sw.run_ids.length > shown) {
        shown = sw.run_ids.length;
        const last = sw.run_ids[shown - 1];
        await Promise.all([loadRun(last), loadTrend(), loadHistory()]);
      }
      if (sw.status !== "running") break;
      await new Promise((r) => setTimeout(r, 700));
      sw = await api(`/api/sweeps/${sw.id}`);
    }
    setMsg(sw.status === "done" ? `掃描完成，共 ${sw.total} 筆。` : `掃描中止：${sw.error}`, sw.status !== "done");
  } catch (err) {
    setMsg(err.message, true);
  } finally {
    setBusy(false);
    setTimeout(() => ($("sweepBox").hidden = true), 2500);
  }
}

// ---------- 結果 ----------
async function loadRun(id) {
  const run = await api(`/api/runs/${id}`);
  renderRun(run);
}

function renderRun(run) {
  currentRunId = run.id;
  const badge = $("verdict");
  const v = run.status === "error" ? "ERROR" : run.verdict || "";
  badge.textContent = { PASS: "PASS", FAIL: "FAIL", ERROR: "錯誤" }[v] || "";
  badge.className = "badge " + v;

  const unit = run.protocol === "uart" ? "Bd" : "Hz";
  const p = run.params || {};
  const extras = [];
  if (run.protocol === "spi") extras.push(`Mode ${p.spi_mode ?? 0}`);
  if (run.protocol === "i2c") extras.push(p.pullup === "external" ? `外接上拉 ${si(p.pullup_ohm, "Ω")}` : "內部上拉");
  if (p.pattern) extras.push(`樣式 ${p.pattern}`);
  $("runMeta").innerHTML =
    `<b>#${run.id}</b> · ${fmtTime(run.started_at)} · <b>${run.protocol.toUpperCase()}</b> ` +
    `要求 ${si(run.speed_hz, unit)}` + (run.actual_hz ? `（ESP32 實際設定 ${si(run.actual_hz, unit)}）` : "") +
    (extras.length ? ` · ${esc(extras.join(" · "))}` : "") +
    (run.scope_idn ? `<br>示波器：${esc(run.scope_idn)} · 韌體：${esc(run.esp32_fw || "—")}` : "") +
    (run.note ? `<br><span class="${run.status === "error" ? "err" : ""}">${esc(run.note)}</span>` : "");

  renderMeasurements(run);
  renderWave(run);
  document.querySelectorAll("#histTable tbody tr").forEach((tr) => tr.classList.toggle("active", Number(tr.dataset.id) === run.id));
}

function renderMeasurements(run) {
  const ms = run.measurements || [];
  if (!ms.length) { $("measTable").innerHTML = ""; return; }
  let html = "<thead><tr><th>項目</th><th class=num>量測值</th><th>規格</th><th>判定</th></tr></thead><tbody>";
  let group = null;
  for (const m of ms) {
    if (m.channel !== group) {
      group = m.channel;
      html += `<tr class="group"><td colspan="4">${esc(group)}</td></tr>`;
    }
    const meta = CFG.metrics[m.name] || { label: m.name };
    const label = esc(meta.label) + (m.source === "scope" ? ' <span class="src-scope">（示波器內建）</span>' : "");
    const verdict = m.pass === true ? '<span class="pass">PASS</span>' : m.pass === false ? '<span class="fail">FAIL</span>' : "";
    html += `<tr><td>${label}</td><td class="num">${si(m.value, m.unit)}</td><td>${esc(limitText(m))}</td><td>${verdict}</td></tr>`;
  }
  $("measTable").innerHTML = html + "</tbody>";
}

function thresholdLines(run, wf, xmin, xmax) {
  const get = (name) => (run.measurements || []).find((m) => m.channel === wf.channel && m.name === name && m.source === "calc");
  const lo = get("vlow"), hi = get("vhigh");
  if (!lo || !hi || lo.value == null || hi.value == null) return [];
  const pcts = run.protocol === "i2c" ? [30, 70] : [10, 50, 90];
  return pcts.map((pc) => {
    const y = lo.value + (hi.value - lo.value) * pc / 100;
    return {
      label: `${wf.channel} ${pc}%`, data: [{ x: xmin, y }, { x: xmax, y }],
      borderColor: css("--muted"), borderDash: [4, 4], borderWidth: 1, pointRadius: 0, isThreshold: true,
    };
  });
}

function renderWave(run) {
  const colors = { CH1: css("--ch1"), CH2: css("--ch2") };
  const wfs = run.waveforms || [];
  const datasets = [];
  let xmin = Infinity, xmax = -Infinity;
  for (const wf of wfs) {
    xmin = Math.min(xmin, wf.t[0]);
    xmax = Math.max(xmax, wf.t[wf.t.length - 1]);
    datasets.push({
      label: `${wf.source} ${wf.channel}`,
      data: wf.t.map((t, i) => ({ x: t, y: wf.v[i] })),
      borderColor: colors[wf.source] || css("--accent"),
      borderWidth: 1.4, pointRadius: 0,
    });
  }
  if (wfs.length) datasets.push(...thresholdLines(run, wfs[0], xmin, xmax));
  const grid = css("--grid"), text = css("--muted");
  if (waveChart) waveChart.destroy();
  waveChart = new Chart($("wave"), {
    type: "line",
    data: { datasets },
    options: {
      animation: false, parsing: false, normalized: true, maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false, axis: "x" },
      plugins: {
        legend: { labels: { color: text, boxWidth: 12, filter: (it, d) => !d.datasets[it.datasetIndex].isThreshold } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${si(c.parsed.y, "V")} @ ${si(c.parsed.x, "s")}` } },
      },
      scales: {
        x: { type: "linear", min: isFinite(xmin) ? xmin : undefined, max: isFinite(xmax) ? xmax : undefined,
             ticks: { color: text, callback: (v) => si(v, "s"), maxTicksLimit: 11 }, grid: { color: grid } },
        y: { suggestedMin: -0.3, suggestedMax: 3.8, ticks: { color: text, callback: (v) => `${v} V` }, grid: { color: grid } },
      },
    },
  });
}

// ---------- 趨勢 ----------
const TREND_DEFAULT = { uart: "TX|baud_error_pct", spi: "SCLK|freq_error_pct", i2c: "SCL|rise_30_70" };

function fillTrendMetrics() {
  const p = protocol();
  const opts = [];
  for (const sig of CFG.signals[p]) {
    for (const m of CFG.signal_metrics[`${p}:${sig}`] || []) {
      opts.push(`<option value="${sig}|${m}">${esc(sig)} · ${esc(CFG.metrics[m].label)}</option>`);
    }
  }
  $("trendMetric").innerHTML = opts.join("");
  $("trendMetric").value = TREND_DEFAULT[p];
}

async function loadTrend() {
  const p = protocol();
  const [channel, metric] = $("trendMetric").value.split("|");
  if (!metric) return;
  const unit = CFG.metrics[metric].unit;
  const rows = await api(`/api/trend?protocol=${p}&metric=${metric}&channel=${channel}`);
  const passC = css("--pass"), failC = css("--fail"), muted = css("--muted"), grid = css("--grid");
  const points = rows.map((r) => ({ x: r.speed_hz, y: r.value, run: r.run_id, pass: r.pass }));
  const byX = (key) => {
    const m = new Map();
    for (const r of rows) if (r[key] != null) m.set(r.speed_hz, r[key]);
    return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([x, y]) => ({ x, y }));
  };
  const datasets = [{
    label: CFG.metrics[metric].label, data: points, showLine: false, pointRadius: 4,
    pointBackgroundColor: points.map((pt) => (pt.pass === true ? passC : pt.pass === false ? failC : muted)),
    pointBorderColor: "transparent",
  }];
  const maxLine = byX("limit_max"), minLine = byX("limit_min");
  if (maxLine.length) datasets.push({ label: "上限", data: maxLine, showLine: true, stepped: "middle", borderColor: failC, borderDash: [5, 4], borderWidth: 1.2, pointRadius: 0 });
  if (minLine.length) datasets.push({ label: "下限", data: minLine, showLine: true, stepped: "middle", borderColor: failC, borderDash: [5, 4], borderWidth: 1.2, pointRadius: 0 });

  const xUnit = p === "uart" ? "Bd" : "Hz";
  if (trendChart) trendChart.destroy();
  trendChart = new Chart($("trend"), {
    type: "scatter",
    data: { datasets },
    options: {
      animation: false, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: muted, boxWidth: 12 } },
        tooltip: { callbacks: { label: (c) => c.raw.run ? `#${c.raw.run}  ${si(c.raw.x, xUnit)} → ${si(c.raw.y, unit)}` : `${c.dataset.label} ${si(c.raw.y, unit)}` } },
      },
      onClick: (_e, els) => { const el = els.find((x) => x.datasetIndex === 0); if (el) loadRun(points[el.index].run); },
      scales: {
        x: { type: "logarithmic", title: { display: true, text: p === "uart" ? "鮑率" : "要求速度", color: muted },
             ticks: { color: muted, autoSkip: false, maxRotation: 0,
                      // 對數軸只標 1、2、5 開頭的刻度，避免擠在一起
                      callback: (v) => [1, 2, 5].includes(Number((v / 10 ** Math.floor(Math.log10(v))).toFixed(3))) ? si(v, xUnit) : "" },
             grid: { color: grid } },
        y: { ticks: { color: muted, callback: (v) => si(v, unit) }, grid: { color: grid } },
      },
    },
  });
  $("trendHint").textContent = rows.length
    ? `${rows.length} 筆資料。綠 = PASS、紅 = FAIL；點資料點可開啟該次測試。`
    : "還沒有資料：執行測試或速度掃描後會出現在這裡。";
}

// ---------- 歷史 ----------
async function loadHistory() {
  const p = $("histFilter").value;
  $("csvLink").href = "/api/export.csv" + (p ? `?protocol=${p}` : "");
  const rows = await api("/api/runs?limit=100" + (p ? `&protocol=${p}` : ""));
  const head = "<thead><tr><th>#</th><th>時間</th><th>協定</th><th class=num>要求速度</th><th class=num>實際設定</th><th>判定</th><th>備註</th></tr></thead>";
  const body = rows.map((r) => {
    const unit = r.protocol === "uart" ? "Bd" : "Hz";
    const v = r.status === "error" ? '<span class="fail">錯誤</span>' : r.verdict === "PASS" ? '<span class="pass">PASS</span>' : r.verdict === "FAIL" ? '<span class="fail">FAIL</span>' : esc(r.status);
    return `<tr data-id="${r.id}" class="${r.id === currentRunId ? "active" : ""}"><td>${r.id}</td><td>${fmtTime(r.started_at)}</td>` +
      `<td>${r.protocol.toUpperCase()}</td><td class="num">${si(r.speed_hz, unit)}</td><td class="num">${si(r.actual_hz, unit)}</td>` +
      `<td>${v}</td><td>${esc((r.note || "").slice(0, 40))}</td></tr>`;
  }).join("");
  $("histTable").innerHTML = head + `<tbody>${body || '<tr><td colspan="7" class="hint">尚無紀錄</td></tr>'}</tbody>`;
}

// ---------- 初始化 ----------
async function init() {
  try {
    CFG = await api("/api/config");
  } catch (err) {
    setMsg(`無法讀取設定：${err.message}`, true);
    return;
  }
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  $("protocol").addEventListener("change", onProtocolChange);
  $("speedPreset").addEventListener("change", () => {
    const v = $("speedPreset").value;
    if (v !== "custom") $("speed").value = v; else $("speed").focus();
  });
  $("speed").addEventListener("input", () => {
    const v = $("speed").value;
    $("speedPreset").value = CFG.presets[protocol()].includes(Number(v)) ? v : "custom";
  });
  $("btnRun").addEventListener("click", runOnce);
  $("btnSweep").addEventListener("click", runSweep);
  $("trendMetric").addEventListener("change", loadTrend);
  $("histFilter").addEventListener("change", loadHistory);
  $("chips").addEventListener("click", loadHealth);
  $("histTable").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (tr) loadRun(Number(tr.dataset.id)).catch((err) => setMsg(err.message, true));
  });
  onProtocolChange();
  loadHealth();
  await loadHistory();
  const first = document.querySelector("#histTable tbody tr[data-id]");
  if (first) loadRun(Number(first.dataset.id));
}

init();
