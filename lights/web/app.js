"use strict";
// Shared by the four pages; each page calls one of the init functions at the bottom.
// Charts are drawn here as SVG: phones on the hotspot have no internet for a library.
// Every visible string comes from i18n.js through t().

const $ = (id) => document.getElementById(id);
const pad = (n) => String(n).padStart(2, "0");
const fmtDateTime = (d) => `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
const fmtTime = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const fmtFull = (d) => `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
const hours = (hms) => { const [h, m, s] = hms.split(":").map(Number); return h + m / 60 + s / 3600; };
const SVGNS = "http://www.w3.org/2000/svg";
const sensorName = (key) => t(`sensor.${key}`);

let status = null, offset = 0;
const serverNow = () => new Date(Date.now() + offset);
const EXPERIMENT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
// Set on the Archivo page: the archived experiment's name and the time of its last reading.
let archived = null;

// Photoperiod notation, e.g. 20L:4D; durations that aren't quarter hours show as minutes or seconds.
function notation(tr) {
  const f = (hms) => {
    const sec = Math.round(hours(hms) * 3600);
    if (sec % 900 === 0) return String(sec / 3600);
    return sec % 60 === 0 ? `${sec / 60}min` : `${sec}s`;
  };
  return `${f(tr.light)}L:${f(tr.dark)}D`;
}
// "20 h" or "20 h 30 min", from HH:MM:SS.
function hoursText(hms) {
  const min = Math.round(hours(hms) * 60), h = Math.floor(min / 60), m = min % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}
// The light period from on to off, as HH:MM:SS; crossing midnight when off is earlier than on.
function lightBetween(on, off) {
  const mins = (s) => +s.slice(0, 2) * 60 + +s.slice(3, 5);
  const light = ((mins(off) - mins(on)) % 1440 + 1440) % 1440;
  return `${pad(Math.floor(light / 60))}:${pad(light % 60)}:00`;
}
const times = (tr) => tr.light_on ? `${tr.light_on}–${tr.light_off}` : "—";
function countdown(ms) {
  if (ms < 0) ms = 0;
  const s = Math.floor(ms / 1000), d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  if (d) return t("dur.d", { d, h });
  if (h) return t("dur.h", { h, m: pad(m) });
  return t("dur.m", { m });
}
function ago(ms) {
  const m = Math.round(ms / 60000);
  if (m < 1) return t("ago.now");
  if (m < 60) return t("ago.m", { n: m });
  const h = Math.floor(m / 60);
  if (h < 48) return t("ago.h", { n: h });
  return t("ago.d", { n: Math.floor(h / 24) });
}
function bytes(n) { return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`; }
const intervalSeconds = () => { const [h, m, s] = status.readings_interval.split(":").map(Number); return h * 3600 + m * 60 + s; };
const fixed = (v, d) => (v === null || v === undefined || v === "") ? "—" : Number(v).toFixed(d);

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return { ok: r.ok, code: r.status, body: await r.json() };
}

async function refreshStatus() {
  try {
    status = await getJSON("/api/status");
    offset = new Date(status.now) - Date.now();
    return true;
  } catch (err) {
    showAlerts([{ level: "bad", text: t("alert.offline") }]);
    return false;
  }
}

// ---------- alerts ----------

function computeAlerts() {
  const out = [], now = serverNow();
  if (!status.clock) out.push({ level: "bad", text: t("alert.clock") });
  const r = status.latest_reading;
  if (r && r.throttled) {
    const v = parseInt(r.throttled, 16);
    if (v & 0x1) out.push({ level: "bad", text: t("alert.undervoltage") });
    else if (v & 0x10000) out.push({ level: "warn", text: t("alert.undervoltage_past") });
  }
  if (r) {
    if (now - new Date(r.timestamp) > 3 * intervalSeconds() * 1000) out.push({ level: "warn", text: t("alert.stale", { when: fmtDateTime(new Date(r.timestamp)) }) });
    const silent = status.sensors.filter((s) => r[`temp_c_${s}`] === "");
    if (silent.length) out.push({ level: "warn", text: t("alert.silent", { names: silent.map(sensorName).join(", ") }) });
  }
  // Only the latest real power cut since the start, within three days; the rest are on Sistema.
  const since = Math.max(new Date(status.start), now - 3 * 86400000);
  const cut = status.outages.find((o) => o.message.startsWith("outage:") && new Date(o.time) >= since);
  if (status.started && cut) out.push({ level: "warn", text: translate(cut) });
  return out;
}

function showAlerts(list) {
  const box = $("alerts");
  if (!box) return;
  box.replaceChildren(...list.map((a) => Object.assign(document.createElement("div"), { className: `banner ${a.level}`, textContent: a.text })));
}

// ---------- events ----------

const EVENT_PREFIXES = [
  ["network: connected to home-wifi", "ev.net_home"], ["network: hotspot started", "ev.net_hotspot"],
  ["network: hotspot active", "ev.net_hotspot"], ["network: hotspot failed", "ev.net_hotspot_failed"],
  ["hotspot only switched on", "ev.hs_on"], ["hotspot only switched off", "ev.hs_off"],
  ["config changed from the monitoring page", "ev.config_page"], ["config replaced by deploy", "ev.config_deploy"],
  ["config reloaded", "ev.config_reloaded"], ["config NOT reloaded", "ev.config_rejected"],
  ["controller started", "ev.started"], ["controller stopped", "ev.stopped"],
  ["clock trusted (NTP)", "ev.clock_ntp"], ["clock synchronized", "ev.clock_ntp"], ["clock trusted (RTC)", "ev.clock_rtc"],
  ["clock NOT", "ev.clock_bad"], ["clock corrected", "ev.clock_corrected"],
  ["data copy to Google Drive failing", "ev.drive_bad"], ["data copy to Google Drive", "ev.drive_ok"],
  ["settings password changed", "ev.password"], ["shutdown requested", "ev.shutdown"],
];

function translate(e) {
  const m = e.message;
  if (e.kind === "switch") return t(e.state === "light" ? "ev.light_on" : "ev.light_off", { name: sensorName(e.treatment) });
  let x;
  if ((x = m.match(/^outage: last reading (\S+), system booted (\S+), readings resumed (\S+) \((\d+) min/)))
    return t("ev.outage", { last: fmtDateTime(new Date(x[1])), boot: fmtDateTime(new Date(x[2])), min: x[4] });
  if ((x = m.match(/^readings gap without reboot: last (\S+), resumed (\S+) \((\d+) min/)))
    return t("ev.gap", { min: x[3], last: fmtDateTime(new Date(x[1])) });
  if ((x = m.match(/^sensor (\S+) \(I2C bus \d+\) not reading/))) return t("ev.sensor_lost", { name: sensorName(x[1]) });
  if ((x = m.match(/^sensor (\S+) \(I2C bus \d+\) reading again/))) return t("ev.sensor_back", { name: sensorName(x[1]) });
  if ((x = m.match(/^readings columns changed: previous readings kept in (\S+)/))) return t("ev.columns", { file: x[1] });
  if ((x = m.match(/^experiment archived as archive\/(\S+);/))) return t("ev.archived", { name: x[1] });
  if ((x = m.match(/^experiment (\S+) started/))) return t("ev.exp_started", { name: x[1] });
  const known = EVENT_PREFIXES.find(([prefix]) => m.startsWith(prefix));
  return known ? t(known[1]) : m;
}

function eventItems(list) {
  return list.map((e) => {
    const li = document.createElement("li");
    li.className = e.kind;
    const time = document.createElement("time");
    time.dateTime = e.time; time.textContent = fmtDateTime(new Date(e.time));
    const msg = document.createElement("span");
    msg.className = "msg"; msg.textContent = translate(e);
    li.append(time, msg);
    return li;
  });
}

function renderEvents(list, el, empty, visible = Infinity) {
  if (!list.length) { el.replaceChildren(Object.assign(document.createElement("li"), { className: "empty", textContent: empty })); return; }
  el.replaceChildren(...eventItems(list.slice(0, visible)));
  const rest = list.slice(visible);
  if (!rest.length) return;
  const li = document.createElement("li"), more = document.createElement("details"), inner = document.createElement("ul");
  more.className = "more"; inner.className = "events nested";
  more.append(Object.assign(document.createElement("summary"), { textContent: t("ev.more", { n: rest.length }) }));
  inner.replaceChildren(...eventItems(rest));
  more.append(inner); li.style.display = "block"; li.append(more); el.append(li);
}

// ---------- charts ----------

function niceTicks(lo, hi, count) {
  const span = hi - lo || 1, raw = span / count, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw);
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(6));
  return ticks;
}

function extent(seriesList, i0, i1, pad, minSpan) {
  let lo = Infinity, hi = -Infinity;
  for (const s of seriesList) for (let i = i0; i < i1; i++) { const v = s[i]; if (v !== null) { if (v < lo) lo = v; if (v > hi) hi = v; } }
  if (lo === Infinity) return null;
  if (hi - lo < minSpan) { const mid = (hi + lo) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
  return [lo - pad, hi + pad];
}

function timeTicks(t0, t1, width) {
  const spanH = (t1 - t0) / 3600, maxTicks = Math.max(2, Math.floor(width / 70));
  const stepH = [1, 2, 3, 6, 12, 24, 48, 72, 168].find((h) => spanH / h <= maxTicks) || 336;
  const ticks = [], start = new Date(t0 * 1000);
  start.setMinutes(0, 0, 0);
  if (stepH >= 24) start.setHours(0);
  else start.setHours(Math.ceil(start.getHours() / stepH) * stepH);
  for (let d = start; d.getTime() / 1000 <= t1; d = new Date(d.getTime() + stepH * 3600000)) {
    const t = d.getTime() / 1000;
    if (t >= t0) ticks.push({ t, label: stepH >= 24 || d.getHours() === 0 ? `${pad(d.getDate())}/${pad(d.getMonth() + 1)}` : fmtTime(d) });
  }
  return ticks;
}

function svg(tag, attrs, parent) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.append(el);
  return el;
}

// Average the points that fall in each pixel column, so weeks of 5-minute rows stay light.
// The line breaks where a reading is missing or two readings are more than `gap` apart.
function pathFor(t, values, i0, i1, x, y, gap) {
  let d = "", bucket = null, sum = 0, n = 0, broken = true, prevT = -Infinity;
  const flush = () => {
    if (!n) return;
    d += `${broken ? "M" : "L"}${bucket.toFixed(1)},${y(sum / n).toFixed(1)}`;
    broken = false; n = 0; sum = 0;
  };
  for (let i = i0; i < i1; i++) {
    const v = values[i];
    if (v === null) { flush(); broken = true; bucket = null; continue; }
    if (t[i] - prevT > gap) { flush(); broken = true; bucket = null; }
    prevT = t[i];
    const px = Math.round(x(t[i]));
    if (px !== bucket) { flush(); bucket = px; }
    sum += v; n += 1;
  }
  flush();
  return d;
}

function drawChart(container, opts) {
  const { t, i0, i1, t0, t1, lines, domain, unit, height, nights, showTimeAxis, events = [], labelEvents = false } = opts;
  const width = Math.max(240, container.clientWidth);
  const m = { l: 30, r: 6, t: 6, b: showTimeAxis ? 18 : 4 };
  const x = (v) => m.l + (v - t0) / (t1 - t0) * (width - m.l - m.r);
  const y = (v) => m.t + (1 - (v - domain[0]) / (domain[1] - domain[0])) * (height - m.t - m.b);
  const root = svg("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": opts.label });
  for (const [a, b] of nights) {
    const xa = Math.max(m.l, x(a)), xb = Math.min(width - m.r, x(b));
    if (xb > xa) svg("rect", { class: "night", x: xa, y: m.t, width: xb - xa, height: height - m.t - m.b }, root);
  }
  for (const v of niceTicks(domain[0], domain[1], 3)) {
    svg("line", { class: "grid", x1: m.l, x2: width - m.r, y1: y(v), y2: y(v) }, root);
    svg("text", { x: m.l - 4, y: y(v) + 3, "text-anchor": "end" }, root).textContent = unit === "%" ? `${v}` : v.toFixed(Number.isInteger(v) ? 0 : 1);
  }
  for (const ev of events) {
    if (ev.t < t0 || ev.t > t1) continue;
    const xx = x(ev.t);
    svg("line", { class: `event ${eventClass(ev)}`, x1: xx, x2: xx, y1: m.t, y2: height - m.b }, root);
    if (labelEvents && ev.kind === "switch") svg("text", { class: `event-label ${eventClass(ev)}`, x: xx + 2, y: m.t + 9 }, root).textContent = ev.treatment;
  }
  if (showTimeAxis) for (const tk of timeTicks(t0, t1, width)) {
    svg("line", { class: "grid", x1: x(tk.t), x2: x(tk.t), y1: height - m.b, y2: height - m.b + 3 }, root);
    svg("text", { x: x(tk.t), y: height - 4, "text-anchor": "middle" }, root).textContent = tk.label;
  }
  const gap = intervalSeconds() * 2.5;
  for (const line of lines) svg("path", { class: `line ${line.cls}`, d: pathFor(t, line.values, i0, i1, x, y, gap) }, root);
  const cursor = svg("line", { class: "cursor", y1: m.t, y2: height - m.b, visibility: "hidden" }, root);
  container.replaceChildren(root);
  return { root, x, cursor, width, m };
}

// Periods with the light off, within [t0, t1], from the light-on periods.
function nightsFrom(lightPeriods, t0, t1, now) {
  const out = []; let cursor = t0;
  for (const [on, offRaw] of lightPeriods || []) {
    const off = offRaw === null ? now : offRaw;
    if (off < t0 || on > t1) continue;
    if (on > cursor) out.push([cursor, Math.min(on, t1)]);
    cursor = Math.max(cursor, off);
  }
  if (cursor < t1) out.push([cursor, t1]);
  return out;
}

// ---------- events over the charts ----------

const OVERLAY_KINDS = ["switch", "outage", "config", "clock", "controller", "sensor", "network", "experiment", "other"];
const eventClass = (ev) => ev.kind === "switch" ? `ev-${ev.state === "light" ? "on" : "off"}` : `ev-${ev.kind}`;
let overlay = new Set(["switch"]);
try { const saved = JSON.parse(localStorage.getItem("tenebrio-overlay")); if (Array.isArray(saved)) overlay = new Set(saved); } catch (err) { /* private mode */ }

function renderOverlayControls() {
  const box = $("overlay");
  if (!box || !series) return;
  const present = new Set(["switch", ...series.events.map((e) => e.kind)]);
  const kinds = OVERLAY_KINDS.filter((k) => present.has(k));
  const signature = kinds.join();
  if (box.dataset.kinds === signature) return;
  box.dataset.kinds = signature;
  $("overlay-chips").replaceChildren(...kinds.map((kind) => {
    const label = document.createElement("label");
    label.className = "chip";
    const swatch = kind === "switch" ? '<i class="ev-on"></i><i class="ev-off"></i>' : `<i class="ev-${kind}"></i>`;
    label.innerHTML = `<input type="checkbox" ${overlay.has(kind) ? "checked" : ""}><span class="swatch">${swatch}</span>${t(`ov.${kind}`)}`;
    label.querySelector("input").addEventListener("change", (ev) => {
      if (ev.target.checked) overlay.add(kind); else overlay.delete(kind);
      try { localStorage.setItem("tenebrio-overlay", JSON.stringify([...overlay])); } catch (err) { /* private mode */ }
      drawEnclosures();
    });
    return label;
  }));
  box.hidden = false;
}

// A treatment's card shows its own light switches; the control card shows every
// treatment's, since light and heat from the neighbours is what it controls for.
function cardEvents(e) {
  return series.events.filter((ev) => overlay.has(ev.kind) && (ev.kind !== "switch" || !e.treatment || ev.treatment === e.key));
}

function lowerBound(arr, v) { let lo = 0, hi = arr.length; while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid] < v) lo = mid + 1; else hi = mid; } return lo; }

// ---------- Inicio ----------

let series = null, windowHours = 48, userMovedSlider = false;

function experimentRange() {
  const now = archived ? archived.end : serverNow().getTime() / 1000, start = new Date(status.start).getTime() / 1000;
  const first = series && series.t.length ? series.t[0] : now - 3600;
  const from = archived ? Math.min(first, start) : status.started ? start : first;
  return { from: Math.min(from, now - 3600), now };
}

function setupSlider() {
  const input = $("window");
  const { from, now } = experimentRange();
  const totalH = (now - from) / 3600, minH = Math.min(6, totalH);
  const toHours = (v) => minH + (totalH - minH) * (v / 1000) ** 2;
  const toValue = (h) => totalH <= minH ? 1000 : Math.round(1000 * Math.sqrt(Math.max(0, (h - minH) / (totalH - minH))));
  if (!userMovedSlider) windowHours = archived ? totalH : Math.min(48, totalH);
  windowHours = Math.min(windowHours, totalH);
  input.value = toValue(windowHours);
  input.oninput = () => { userMovedSlider = true; windowHours = toHours(Number(input.value)); labelWindow(totalH); drawEnclosures(); };
  labelWindow(totalH);
}

function labelWindow(totalH) {
  const h = windowHours;
  $("window-label").textContent = h >= totalH - 0.01 ? t(archived ? "ar.window_all" : status.started ? "home.window_all" : "home.window_data")
    : h < 48 ? t("home.window_h", { n: Math.round(h) }) : t("home.window_d", { n: (h / 24).toFixed(h < 240 ? 1 : 0).replace(".0", "") });
  const { now } = experimentRange();
  $("window-range").textContent = `${fmtDateTime(new Date((now - h * 3600) * 1000))} – ${fmtTime(new Date(now * 1000))}`;
}

function enclosures() {
  const list = status.treatments.map((tr) => ({ key: String(tr.id), name: t("treatment", { id: tr.id }), treatment: tr }));
  if (status.sensors.includes("control")) list.push({ key: "control", name: sensorName("control"), treatment: null });
  return list;
}

function renderSummary() {
  const now = serverNow(), start = new Date(status.start);
  $("experiment-name").innerHTML = `${t("home.experiment")} · <b></b>`;
  $("experiment-name").querySelector("b").textContent = status.experiment || t("home.unnamed");
  $("day").innerHTML = status.started ? t("home.day", { n: Math.floor((now - start) / 86400000) + 1 }) : t("home.starts_in", { dur: countdown(start - now) });
  $("start-line").textContent = t(status.started ? "home.start" : "home.start_planned", { when: fmtFull(start).slice(0, 16) });
  const r = status.latest_reading || {};
  const rows = enclosures();
  if (status.sensors.includes("room")) rows.push({ key: "room", name: sensorName("room"), treatment: null });
  $("now-rows").replaceChildren(...rows.map((e) => {
    const row = document.createElement("tr"), tr = e.treatment, on = tr && tr.is_light && status.clock;
    const lamp = tr ? `<span class="lamp ${on ? "on" : "off"}" title="${t(on ? "card.on" : "card.off")}"></span>`
      : e.key === "control" ? `<span class="lamp off" title="${t("card.always_dark")}"></span>` : "";
    row.innerHTML = `<td>${e.name}${tr ? `<span class="sub">${notation(tr)}</span>` : ""}</td><td>${lamp}</td><td>${fixed(r[`temp_c_${e.key}`], 1)}</td><td>${fixed(r[`rh_${e.key}`], 0)}</td>`;
    return row;
  }));
  const outages = status.started ? status.outages.filter((o) => new Date(o.time) >= start).length : status.outages.length;
  $("facts").innerHTML = `<span>${t(status.started ? "home.outages_since" : "home.outages_all")} <b>${outages}</b></span>`
    + `<span>${t("home.last_reading")} <b>${r.timestamp ? ago(now - new Date(r.timestamp)) : t("home.none")}</b></span>`;
}

function enclosureCard(e) {
  const card = document.createElement("article");
  card.className = "card";
  card.dataset.key = e.key;
  card.innerHTML = `
    <div class="enc-head"><h3>${e.name}</h3><span class="period">${e.treatment ? notation(e.treatment) + (e.treatment.light_on ? ` · ${times(e.treatment)}` : "") : t("card.no_leds")}</span></div>
    ${archived ? "" : `<div class="state" data-state></div>
    <div class="values"><span class="v t" data-t>—</span><span class="v h" data-h>—</span></div>`}
    <div class="charts">
      <div class="chart-label"><span class="key" style="color:var(--temp)">${t("card.temp")}</span><span class="key room" style="color:var(--room)">${t("card.room")}</span>${e.treatment ? `<span class="key night">${t("card.dark")}</span>` : ""}</div>
      <div data-chart="temp"></div>
      <div class="chart-label"><span class="key" style="color:var(--humidity)">${t("card.hum")}</span></div>
      <div data-chart="hum"></div>
    </div>
    <div class="readout" data-readout></div>`;
  return card;
}

function renderEnclosureState(card, e) {
  const tr = e.treatment, el = card.querySelector("[data-state]");
  const r = status.latest_reading || {};
  card.querySelector("[data-t]").innerHTML = `${fixed(r[`temp_c_${e.key}`], 1)}<small>°C</small>`;
  card.querySelector("[data-h]").innerHTML = `${fixed(r[`rh_${e.key}`], 0)}<small>${t("unit.rh")}</small>`;
  if (!tr) { el.innerHTML = `<span class="lamp off"></span><strong>${t("card.always_dark")}</strong>`; return; }
  const on = tr.is_light && status.clock, next = tr.next_switch ? new Date(tr.next_switch) : null;
  let html = `<span class="lamp ${on ? "on" : "off"}"></span><strong>${t(on ? "card.on" : "card.off")}</strong>`;
  if (!status.started && next) html += `<span>${t("card.starts", { when: fmtDateTime(next) })}</span>`;
  else if (next) html += `<span>${t(tr.is_light ? "card.turns_off" : "card.turns_on", { dur: countdown(next - serverNow()), time: fmtTime(next) })}</span>`;
  if (!status.clock && tr.is_light) html += `<span class="caution">${t("card.held_off")}</span>`;
  el.innerHTML = html;
}

function drawEnclosures() {
  if (!series) return;
  const { now } = experimentRange();
  const t1 = now, t0 = now - windowHours * 3600;
  const times = series.t, i0 = lowerBound(times, t0), i1 = lowerBound(times, t1 + 1);
  const s = series.series, list = enclosures();
  const temps = list.map((e) => s[`temp_c_${e.key}`] || []).concat([s.temp_c_room || []]);
  const hums = list.map((e) => s[`rh_${e.key}`] || []).concat([s.rh_room || []]);
  const tDomain = extent(temps, i0, i1, 0.3, 2), hDomain = extent(hums, i0, i1, 2, 10);
  for (const e of list) {
    const card = document.querySelector(`[data-key="${e.key}"]`);
    if (!card) continue;
    const tempBox = card.querySelector('[data-chart="temp"]'), humBox = card.querySelector('[data-chart="hum"]');
    if (!tDomain || i1 <= i0) {
      tempBox.innerHTML = `<div class="nodata">${t("card.nodata")}</div>`; humBox.innerHTML = ""; continue;
    }
    const nights = e.treatment ? nightsFrom(series.light[e.key], t0, t1, series.now) : [];
    const common = { t: times, i0, i1, t0, t1, nights, events: cardEvents(e), labelEvents: !e.treatment };
    const tc = drawChart(tempBox, { ...common, domain: tDomain, unit: "°C", height: 130, showTimeAxis: false, label: t("chart.temp", { name: e.name }),
      lines: [{ cls: "room", values: s.temp_c_room || [] }, { cls: "temp", values: s[`temp_c_${e.key}`] || [] }] });
    const hc = drawChart(humBox, { ...common, domain: hDomain || [0, 100], unit: "%", height: 104, showTimeAxis: true, label: t("chart.hum", { name: e.name }),
      lines: [{ cls: "room", values: s.rh_room || [] }, { cls: "hum", values: s[`rh_${e.key}`] || [] }] });
    attachReadout(card, e, [tc, hc], t0, t1, i0, i1, common.events);
  }
}

function attachReadout(card, e, charts, t0, t1, i0, i1, events) {
  const readout = card.querySelector("[data-readout]"), times = series.t, s = series.series;
  const move = (ev) => {
    const c = charts[0], box = c.root.getBoundingClientRect();
    const px = (ev.clientX - box.left) * (c.width / box.width);
    const when = t0 + (px - c.m.l) / (c.width - c.m.l - c.m.r) * (t1 - t0);
    let i = Math.min(Math.max(lowerBound(times, when), i0), i1 - 1);
    if (i > i0 && Math.abs(times[i - 1] - when) < Math.abs(times[i] - when)) i -= 1;
    for (const ch of charts) { const xx = ch.x(times[i]); ch.cursor.setAttribute("x1", xx); ch.cursor.setAttribute("x2", xx); ch.cursor.setAttribute("visibility", "visible"); }
    const room = s.temp_c_room && s.temp_c_room[i] !== null ? t("card.readout_room", { t: fixed(s.temp_c_room[i], 1) }) : "";
    const near = (t1 - t0) / (c.width - c.m.l - c.m.r) * 6;  // events within 6 px of the finger
    const here = events.filter((ev) => Math.abs(ev.t - when) <= near).map((ev) => `${fmtTime(new Date(ev.t * 1000))} ${translate(ev)}`);
    readout.textContent = `${fmtDateTime(new Date(times[i] * 1000))} · ${fixed(s[`temp_c_${e.key}`][i], 1)} °C · ${fixed(s[`rh_${e.key}`][i], 0)} ${t("unit.rh")}${room}`
      + (here.length ? `\n${here.slice(0, 4).join(" · ")}${here.length > 4 ? ` · ${t("ov.more", { n: here.length - 4 })}` : ""}` : "");
  };
  const leave = () => { for (const ch of charts) ch.cursor.setAttribute("visibility", "hidden"); readout.textContent = ""; };
  for (const ch of charts) { ch.root.addEventListener("pointermove", move); ch.root.addEventListener("pointerdown", move); ch.root.addEventListener("pointerleave", leave); }
}

function renderDownloads() {
  const f = status.files;
  $("dl-readings-info").textContent = t("dl.rows", { n: f.readings.rows, size: bytes(f.readings.bytes) });
  $("dl-events-info").textContent = t("dl.rows", { n: f.events.rows, size: bytes(f.events.bytes) });
  const s = intervalSeconds();
  $("interval").textContent = s % 3600 === 0 ? t("dl.hours", { n: s / 3600 }) : t("dl.minutes", { n: Math.round(s / 60) });
}

async function refreshSeries() {
  try { series = await getJSON("/api/series"); } catch (err) { return; }
  setupSlider();
  renderOverlayControls();
  drawEnclosures();
}

async function initInicio() {
  applyLanguage();
  let built = "";
  const update = async () => {
    if (!(await refreshStatus())) return;
    showAlerts(computeAlerts());
    renderSummary();
    const list = enclosures(), signature = list.map((e) => e.key + (e.treatment ? notation(e.treatment) : "")).join();
    if (signature !== built) { $("enclosures").replaceChildren(...list.map(enclosureCard)); built = signature; drawEnclosures(); }
    for (const e of list) renderEnclosureState(document.querySelector(`[data-key="${e.key}"]`), e);
    renderDownloads();
  };
  await update();
  await refreshSeries();
  setInterval(update, 15000);
  setInterval(refreshSeries, 60000);
  let resize;
  window.addEventListener("resize", () => { clearTimeout(resize); resize = setTimeout(drawEnclosures, 150); });
}

// ---------- Archivo ----------

async function initArchivo() {
  applyLanguage();
  const name = new URLSearchParams(location.search).get("e") || "";
  const query = `?e=${encodeURIComponent(name)}`;
  let info;
  try {
    [info, series] = await Promise.all([getJSON(`/api/archive/experiment${query}`), getJSON(`/api/archive/series${query}`)]);
  } catch (err) {
    showAlerts([{ level: "bad", text: t("ar.missing") }]);
    $("summary").hidden = true; document.querySelector(".window").hidden = true; return;
  }
  const end = info.last_reading || info.archived || info.start;
  archived = { name: info.name, end: new Date(end).getTime() / 1000 };
  status = { ...info, experiment: info.name, started: true, latest_reading: null, clock: "archive",
             treatments: info.treatments.map((tr) => ({ ...tr, is_light: false, next_switch: null })) };
  document.title = `${t("ar.title")} · ${info.name}`;
  const start = new Date(info.start), days = Math.max(0, (new Date(end) - start) / 86400000);
  $("day").textContent = info.name;
  $("start-line").textContent = t("ar.range", { a: fmtFull(start).slice(0, 10), b: fmtFull(new Date(end)).slice(0, 10), n: days.toFixed(days < 10 ? 1 : 0) });
  const rows = info.treatments.map((tr) => `<tr><td>${t("treatment", { id: tr.id })}</td><td class="wide-only">${notation(tr)}</td><td>${times(tr)}</td><td>${tr.light}</td><td>${tr.dark}</td></tr>`);
  if (info.sensors.includes("control")) rows.push(`<tr><td>${sensorName("control")}</td><td class="wide-only">${t("card.no_leds")}</td><td>—</td><td>—</td><td>—</td></tr>`);
  $("config-rows").innerHTML = rows.join("");
  const outages = info.outages.filter((o) => o.message.startsWith("outage:") && new Date(o.time) >= start).length;
  $("facts").innerHTML = `<span>${t("ar.archived")} <b>${info.archived ? fmtFull(new Date(info.archived)).slice(0, 16) : "—"}</b></span>`
    + `<span>${t("ar.readings")} <b>${info.files.readings.rows}</b></span><span>${t("ar.outages")} <b>${outages}</b></span>`;
  $("enclosures").replaceChildren(...enclosures().map(enclosureCard));
  for (const a of document.querySelectorAll("a.download")) a.href = a.getAttribute("href").split("?")[0] + query;
  renderDownloads();
  setupSlider();
  renderOverlayControls();
  drawEnclosures();
  let resize;
  window.addEventListener("resize", () => { clearTimeout(resize); resize = setTimeout(drawEnclosures, 150); });
}

// ---------- Sistema ----------

async function renderArchiveList() {
  let list;
  try { list = await getJSON("/api/archive"); } catch (err) { return; }
  const el = $("archive-list");
  if (!list.length) { el.replaceChildren(Object.assign(document.createElement("li"), { className: "empty", textContent: t("sys.no_archive") })); return; }
  el.replaceChildren(...list.map((e) => {
    const li = document.createElement("li"), a = document.createElement("a");
    a.href = `/archivo?e=${encodeURIComponent(e.name)}`;
    const from = new Date(e.first_reading || e.start), to = new Date(e.last_reading || e.archived || e.start);
    const periods = e.treatments.map(notation).join(" · ");
    a.innerHTML = `<b></b><span class="num"></span><span class="muted small"></span>`;
    a.children[0].textContent = e.name;
    a.children[1].textContent = `${fmtDateTime(from)} – ${fmtDateTime(to)}`;
    a.children[2].textContent = [periods, t("sys.archive_rows", { n: e.files.readings.rows })].filter(Boolean).join(" · ");
    li.append(a);
    return li;
  }));
}

function wifiName(wifi) {
  return wifi === "home-wifi" ? t("sys.wifi_home") : wifi === "tenebrio-hotspot" ? t("sys.wifi_hotspot") : wifi || t("sys.wifi_none");
}

async function initSistema() {
  applyLanguage();
  const update = async () => {
    if (!(await refreshStatus())) return;
    showAlerts(computeAlerts());
    const clock = { NTP: ["sys.clock_ntp", "good"], RTC: ["sys.clock_rtc", "good"] }[status.clock] || ["sys.clock_bad", "alert"];
    $("clock").textContent = t(clock[0]); $("clock").className = clock[1];
    const r = status.latest_reading;
    $("cpu").textContent = r && r.cpu_temp_c ? `${Number(r.cpu_temp_c).toFixed(1)} °C` : "—";
    const power = $("power");
    if (!r || !r.throttled) { power.textContent = "—"; power.className = ""; }
    else {
      const v = parseInt(r.throttled, 16);
      const [key, cls] = v & 0x1 ? ["sys.power_low", "alert"] : v & 0x10000 ? ["sys.power_past", "caution"] : ["sys.power_ok", "good"];
      power.textContent = t(key); power.className = cls;
    }
    $("lastread").textContent = r ? fmtDateTime(new Date(r.timestamp)) : t("sys.no_readings");
    $("wifi").textContent = wifiName(status.network.wifi);
    $("hs-state").textContent = t(status.network.hotspot_only ? "sys.hs_on" : "sys.hs_off");
    renderEvents(status.outages, $("outages"), t("sys.no_outages"));
    renderEvents(status.recent_events, $("events"), t("sys.no_events"), 25);
  };
  setInterval(() => { if (status) $("now").textContent = fmtFull(serverNow()); }, 1000);
  renderArchiveList();
  await update();
  setInterval(update, 15000);
}

// ---------- Configuración ----------

const CLOCK_TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
let formDirty = false;

function toLocalInput(iso) { const d = new Date(iso); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; }

function renderForm() {
  if (formDirty) return;
  $("experiment").value = status.experiment;
  $("start").value = toLocalInput(status.start);
  const byId = Object.fromEntries(status.treatments.map((tr) => [tr.id, tr]));
  $("t-edit-list").replaceChildren(...[1, 2, 3, 4].map((id) => {
    const tr = byId[id], div = document.createElement("div");
    div.className = "t-edit"; div.dataset.off = String(!tr);
    div.innerHTML = `
      <label class="check"><input type="checkbox" id="on-${id}" ${tr ? "checked" : ""}> ${t("treatment", { id })}</label>
      <div class="pair">
        <div class="field"><label for="light-on-${id}">${t("set.light_on")}</label><input type="time" id="light-on-${id}" value="${tr ? tr.light_on : ""}"></div>
        <div class="field"><label for="light-off-${id}">${t("set.light_off")}</label><input type="time" id="light-off-${id}" value="${tr ? tr.light_off : ""}"></div>
      </div>
      <p class="hint photoperiod" id="photoperiod-${id}"></p>`;
    div.querySelector("input[type=checkbox]").addEventListener("change", (ev) => { div.dataset.off = String(!ev.target.checked); });
    for (const input of div.querySelectorAll("input[type=time]")) input.addEventListener("input", () => showPhotoperiod(id));
    return div;
  }));
  [1, 2, 3, 4].forEach(showPhotoperiod);
}

function showPhotoperiod(id) {
  const on = $(`light-on-${id}`).value.slice(0, 5), off = $(`light-off-${id}`).value.slice(0, 5);
  const ok = CLOCK_TIME.test(on) && CLOCK_TIME.test(off) && on !== off, light = ok ? lightBetween(on, off) : null;
  $(`photoperiod-${id}`).textContent = ok ? `${t("set.photoperiod", { l: hoursText(light), d: hoursText(lightBetween(off, on)) })} (${notation({ light, dark: lightBetween(off, on) })})` : "";
}

function readForm() {
  const errors = [], treatments = [];
  const start = $("start").value, experiment = $("experiment").value.trim();
  $("experiment").setCustomValidity(EXPERIMENT_NAME.test(experiment) ? "" : "name");
  if (!EXPERIMENT_NAME.test(experiment)) errors.push(t("set.err_name"));
  if (!start) errors.push(t("set.err_start"));
  for (const id of [1, 2, 3, 4]) {
    if (!$(`on-${id}`).checked) continue;
    const light_on = $(`light-on-${id}`).value.slice(0, 5), light_off = $(`light-off-${id}`).value.slice(0, 5);
    const valid = CLOCK_TIME.test(light_on) && CLOCK_TIME.test(light_off);
    for (const el of [$(`light-on-${id}`), $(`light-off-${id}`)]) el.setCustomValidity(valid && light_on !== light_off ? "" : "HH:MM");
    if (!valid) errors.push(t("set.err_time", { id }));
    else if (light_on === light_off) errors.push(t("set.err_same", { id }));
    treatments.push({ id, light_on, light_off });
  }
  return { form: { experiment, start: start.length === 16 ? start + ":00" : start, treatments }, errors };
}

function describeChanges(form) {
  const out = [], before = Object.fromEntries(status.treatments.map((tr) => [tr.id, tr]));
  if (form.experiment !== status.experiment) out.push(t("set.ch_name", { a: status.experiment || t("home.unnamed"), b: form.experiment }));
  if (toLocalInput(status.start) !== form.start) out.push(t("set.ch_start", { a: fmtFull(new Date(status.start)), b: fmtFull(new Date(form.start)) }));
  const after = Object.fromEntries(form.treatments.map((tr) => [tr.id, tr]));
  for (const id of [1, 2, 3, 4]) {
    const b = before[id], a = after[id];
    if (b && !a) out.push(t("set.ch_remove", { id }));
    else if (!b && a) out.push(t("set.ch_add", { id, on: a.light_on, off: a.light_off }));
    else if (a) {
      const parts = [];
      if (a.light_on !== b.light_on) parts.push(t("set.ch_on", { a: b.light_on, b: a.light_on }));
      if (a.light_off !== b.light_off) parts.push(t("set.ch_off", { a: b.light_off, b: a.light_off }));
      if (parts.length) out.push(`${t("treatment", { id })}: ${parts.join(", ")}`);
    }
  }
  return out;
}

function notify(id, text, ok) { const n = $(id); n.textContent = text; n.className = `notice ${ok ? "ok" : "bad"}`; n.hidden = false; }

// A protected request that finds the session gone sends the page back to the login form.
async function protectedPost(url, body, noticeId) {
  try {
    const res = await postJSON(url, body);
    if (res.code === 401) { status.authenticated = false; showLocked(); notify("login-notice", t("set.session_over"), false); return null; }
    return res;
  } catch (err) { notify(noticeId, t("set.unreachable"), false); return null; }
}

function showLocked() {
  $("locked").hidden = !!status.authenticated;
  $("unlocked").hidden = !status.authenticated;
}

// The next experiment's name: the running one's with its number raised (dry-run-1 -> dry-run-2).
function nextName(name) {
  const m = name.match(/^(.*?)(\d+)$/);
  return m ? m[1] + String(Number(m[2]) + 1).padStart(m[2].length, "0") : "";
}

function renderArchiveForm() {
  if ($("archive").dataset.dirty) return;
  $("ar-as").value = status.experiment;
  $("ar-new").value = nextName(status.experiment);
  const next = serverNow(); next.setHours(next.getHours() + 1, 0, 0, 0);
  $("ar-start").value = toLocalInput(next.toISOString());
}

function renderSettings() {
  showLocked();
  if (!status.authenticated) return;
  renderForm();
  renderArchiveForm();
  const on = status.network.hotspot_only;
  $("hs-state").textContent = t(on ? "sys.hs_on" : "sys.hs_off");
  $("hs-toggle").textContent = t(on ? "set.hs_turn_off" : "set.hs_turn_on");
  $("hs-question").textContent = t(on ? "set.hs_q_off" : "set.hs_q_on");
}

const EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.6 5.1A10.4 10.4 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-2.6 3.4M6.6 6.6C3.9 8.4 2 12 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="M2 2l20 20"/></svg>';

function addPasswordEyes() {
  for (const input of document.querySelectorAll('input[type="password"]')) {
    const wrap = document.createElement("div");
    wrap.className = "password";
    input.replaceWith(wrap);
    const eye = document.createElement("button");
    eye.type = "button";
    const show = (visible) => {
      input.type = visible ? "text" : "password";
      eye.innerHTML = visible ? EYE_OFF : EYE;
      eye.setAttribute("aria-label", t(visible ? "set.pw_hide" : "set.pw_show"));
      eye.setAttribute("aria-pressed", String(visible));
    };
    eye.addEventListener("click", () => { show(input.type === "password"); input.focus(); });
    input.form?.addEventListener("submit", () => show(false));
    wrap.append(input, eye);
    show(false);
  }
}

function initThemePicker() {
  const buttons = document.querySelectorAll("[data-theme-choice]");
  const mark = () => {
    const current = document.documentElement.dataset.theme || "";
    for (const b of buttons) b.setAttribute("aria-pressed", String(b.dataset.themeChoice === current));
  };
  for (const b of buttons) b.addEventListener("click", () => {
    const choice = b.dataset.themeChoice;
    if (choice) document.documentElement.dataset.theme = choice;
    else delete document.documentElement.dataset.theme;
    try { choice ? localStorage.setItem("tenebrio-theme", choice) : localStorage.removeItem("tenebrio-theme"); } catch (err) { /* private mode */ }
    mark();
  });
  mark();
}

async function initConfiguracion() {
  applyLanguage();
  addPasswordEyes();
  initThemePicker();
  const update = async () => { if (await refreshStatus()) { showAlerts(computeAlerts()); renderSettings(); } };
  await update();
  setInterval(update, 15000);

  $("login").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const res = await postJSON("/api/login", { password: $("password").value });
      if (!res.ok) return notify("login-notice", t("set.wrong"), false);
      status = res.body; $("password").value = ""; $("login-notice").hidden = true; renderSettings();
    } catch (err) { notify("login-notice", t("set.unreachable"), false); }
  });
  $("logout").addEventListener("click", async () => {
    try { status = (await postJSON("/api/logout", {})).body; } catch (err) { status.authenticated = false; }
    renderSettings();
  });

  $("config").addEventListener("input", () => { formDirty = true; $("notice").hidden = true; });
  $("config").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const { form, errors } = readForm();
    if (errors.length) { $("confirm").hidden = true; return notify("notice", errors.join(" "), false); }
    const changes = describeChanges(form);
    if (!changes.length) { $("confirm").hidden = true; return notify("notice", t("set.no_changes"), true); }
    $("changes").replaceChildren(...changes.map((c) => Object.assign(document.createElement("li"), { textContent: c })));
    $("confirm").hidden = false; $("notice").hidden = true;
  });
  $("cancel").addEventListener("click", () => { $("confirm").hidden = true; formDirty = false; renderForm(); });
  $("save").addEventListener("click", async () => {
    const res = await protectedPost("/api/config", readForm().form, "notice");
    if (!res) return;
    if (!res.ok) return notify("notice", t("set.not_saved", { err: res.body.error }), false);
    status = res.body; offset = new Date(status.now) - Date.now(); formDirty = false;
    $("confirm").hidden = true; renderSettings();
    notify("notice", t("set.saved"), true);
  });

  const archiveForm = () => {
    const start = $("ar-start").value;
    return { archive_as: $("ar-as").value.trim(), new_name: $("ar-new").value.trim(), start: start.length === 16 ? start + ":00" : start };
  };
  $("to-archive").addEventListener("click", () => { $("ar-new").focus(); $("archive").closest("section").scrollIntoView({ block: "start" }); });
  $("archive").addEventListener("input", () => { $("archive").dataset.dirty = "1"; $("ar-notice").hidden = true; $("ar-confirm").hidden = true; });
  $("archive").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const f = archiveForm(), errors = [];
    for (const id of ["ar-as", "ar-new"]) $(id).setCustomValidity(EXPERIMENT_NAME.test($(id).value.trim()) ? "" : "name");
    if (!EXPERIMENT_NAME.test(f.archive_as) || !EXPERIMENT_NAME.test(f.new_name)) errors.push(t("set.err_name"));
    else if (f.archive_as === f.new_name) errors.push(t("set.ar_err_same"));
    if (!f.start) errors.push(t("set.err_start"));
    if (errors.length) { $("ar-confirm").hidden = true; return notify("ar-notice", errors.join(" "), false); }
    const periods = status.treatments.map((tr) => `${t("treatment", { id: tr.id })} ${notation(tr)} (${times(tr)})`).join(", ") || "—";
    $("ar-changes").replaceChildren(...[
      t("set.ar_q_old", { name: f.archive_as, readings: status.files.readings.rows, events: status.files.events.rows }),
      t("set.ar_q_new", { name: f.new_name, when: fmtFull(new Date(f.start)).slice(0, 16), periods }),
    ].map((text) => Object.assign(document.createElement("li"), { textContent: text })));
    $("ar-confirm").hidden = false; $("ar-notice").hidden = true;
  });
  $("ar-cancel").addEventListener("click", () => { $("ar-confirm").hidden = true; delete $("archive").dataset.dirty; renderArchiveForm(); });
  $("ar-yes").addEventListener("click", async () => {
    const f = archiveForm();
    const res = await protectedPost("/api/archive", f, "ar-notice");
    if (!res) return;
    $("ar-confirm").hidden = true;
    if (!res.ok) {
      const key = { same_name: "set.ar_err_same", archive_exists: "set.ar_err_exists", experiment_name: "set.err_name", start: "set.err_start" }[res.body.error];
      return notify("ar-notice", key ? t(key) : t("set.not_saved", { err: res.body.error }), false);
    }
    status = res.body; offset = new Date(status.now) - Date.now();
    delete $("archive").dataset.dirty; formDirty = false; renderSettings();
    notify("ar-notice", t("set.ar_done", { name: f.new_name }), true);
  });

  $("hs-toggle").addEventListener("click", () => { $("hs-confirm").hidden = false; });
  $("hs-cancel").addEventListener("click", () => { $("hs-confirm").hidden = true; });
  $("hs-save").addEventListener("click", async () => {
    const on = !status.network.hotspot_only;
    const res = await protectedPost("/api/hotspot-only", { on }, "hs-notice");
    if (!res) return;
    $("hs-confirm").hidden = true;
    if (!res.ok) return notify("hs-notice", t("set.not_saved", { err: res.body.error }), false);
    status = res.body; renderSettings();
    notify("hs-notice", t(on ? "set.hs_done_on" : "set.hs_done_off"), true);
  });

  $("pw-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const current = $("pw-current").value, next = $("pw-new").value;
    if (next !== $("pw-repeat").value) return notify("pw-notice", t("set.pw_mismatch"), false);
    if (next.length < 4) return notify("pw-notice", t("set.pw_short"), false);
    const res = await protectedPost("/api/password", { current, new: next }, "pw-notice");
    if (!res) return;
    if (!res.ok) return notify("pw-notice", t(res.body.error === "wrong_password" ? "set.pw_wrong" : "set.not_saved", { err: res.body.error }), false);
    $("pw-form").reset();
    notify("pw-notice", t("set.pw_done"), true);
  });

  $("off-button").addEventListener("click", () => { $("off-confirm").hidden = false; });
  $("off-cancel").addEventListener("click", () => { $("off-confirm").hidden = true; });
  $("off-yes").addEventListener("click", async () => {
    const res = await protectedPost("/api/shutdown", {}, "off-notice");
    if (!res) return;
    $("off-confirm").hidden = true;
    if (!res.ok) return notify("off-notice", t("set.not_saved", { err: res.body.error }), false);
    notify("off-notice", t("set.off_done"), true);
    $("off-button").disabled = true;
  });
}
