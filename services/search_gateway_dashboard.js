"use strict";
const COLORS = ["#2f6fde", "#e8833a", "#2a9d8f", "#c94f7c", "#8a63d2", "#d4a72c", "#4f9a3a", "#d35454",
                "#3aa3c9", "#a0703c", "#6d7fcc", "#b85fb5", "#5c8c7a", "#c47a2c", "#7a8a99"];
const SPECIAL = { cache: "#9aa5b1", searxng: "#56606b", none: "#c0392b", "Pi": "#2f6fde", "Open WebUI": "#e8833a" };
const colorOf = (() => { const seen = {}; let i = 0;
  return k => SPECIAL[k] || (seen[k] = seen[k] || COLORS[i++ % COLORS.length]); })();
const STATUS = { "available": ["available", "available"], "near reserve": ["near", "near the reserve"],
                 "used up": ["usedup", "used up"], "paused": ["paused", "paused (rate limit)"], "no key": ["nokey", "no key"] };
const SVG = "http://www.w3.org/2000/svg";

function h(tag, attrs, ...kids) {
  const el = tag.startsWith("svg:") ? document.createElementNS(SVG, tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== undefined && v !== null) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false)
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}

function amount(x, unit) {
  if (x === null || x === undefined) return "–";
  if (unit === "$") return "$" + Number(x).toFixed(x < 1 ? 3 : 2);
  return Number(x).toLocaleString(undefined, { maximumFractionDigits: 1 }) + " " + unit;
}

const when = t => t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }) : "";
const short = d => d ? new Date(d + "T00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";

const fact = (label, value) => h("div", null, h("span", null, label + " "), h("b", null, value));

const R = 46, C = 2 * Math.PI * R;
function donut(parts, label) {  // parts: [[fraction, stroke color, extra attributes]], drawn clockwise from the top
  const svg = h("svg:svg", { viewBox: "0 0 112 112", role: "img", "aria-label": label },
    h("svg:title", null, label),
    h("svg:circle", { cx: 56, cy: 56, r: R, fill: "none", stroke: "var(--track)", "stroke-width": 12 }));
  let at = 0;
  for (const [f, color, extra] of parts) {
    if (!(f > 0)) continue;
    svg.append(h("svg:circle", { cx: 56, cy: 56, r: R, fill: "none", stroke: color, "stroke-width": 12,
      "stroke-dasharray": `${f * C} ${C}`, "stroke-dashoffset": -at * C, ...(extra || {}) }));
    at += f;
  }
  return svg;
}

function compact(x, unit) {
  if (x === null || x === undefined) return "–";
  if (unit === "$") return "$" + Number(x).toFixed(x < 10 ? 2 : x < 1000 ? 1 : 0);
  return Number(x).toLocaleString(undefined, { notation: x >= 100000 ? "compact" : "standard", maximumFractionDigits: x >= 100 ? 0 : 1 });
}

const CHEVRON = () => h("svg:svg", { viewBox: "0 0 16 16", "aria-hidden": "true" },
  h("svg:path", { d: "M4 6l4 4 4-4", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round" }));

const info = (name, ...kids) => h("div", { class: "info" },
  h("button", { type: "button", "aria-label": `Details for ${name}` }, CHEVRON()), h("div", { class: "pop", role: "tooltip" }, kids));

function serviceCard(s, reserve) {
  const [cls, label] = STATUS[s.status] || ["nokey", s.status];
  const color = colorOf(s.id);
  const leftF = s.allowance > 0 ? Math.min(1, s.left / s.allowance) : 0;
  const resF = s.allowance > 0 ? Math.min(1 - leftF, s.reserve / s.allowance) : 0;
  const facts = [
    fact("Used", `${amount(s.used, s.unit)} of ${amount(s.allowance, s.unit)}`),
    fact("Left", `${amount(s.left, s.unit)}` + (s.unit !== "searches" && s.searches_left !== null ? ` (≈ ${s.searches_left.toLocaleString()} searches)` : "")),
    fact("Searches", s.searches),
    s.resets ? fact(s.id === "you" ? "Resets" : "Cycle resets", `${short(s.resets)} (since ${short(s.cycle_start)})`) : fact("Cycle", "one-time credit"),
    fact("Numbers", s.source === "service" ? `confirmed by the service, ${when(s.checked)}` : "counted by the tool"),
    fact("Retry group", s.retry_group.length > 1 ? s.retry_group.join(" / ") : "own"),
    s.group && s.group.includes("/") ? fact("Ranked with", s.group) : null,
    s.rank ? null : fact("Order", "not listed: tried after the listed ones"),
  ];
  const unitWord = s.unit === "$" ? "left" : `${s.unit} left`;
  return h("div", { class: "card svc" + (s.key ? "" : " off"), style: `--svc:${color}` },
    s.key ? info(s.name, h("div", { class: "facts" }, facts),
      s.extras.length ? h("ul", { class: "extras" }, s.extras.map(e => h("li", null, e))) : null) : null,
    h("span", { class: "rank" }, s.rank ? "#" + s.rank : "–"),
    h("span", { class: "name" }, s.name),
    h("span", { class: "pill " + cls }, label),
    h("div", { class: "donut" },
      donut(s.key ? [[leftF, color], [resF, "var(--muted)", { "stroke-opacity": 0.35 }]] : [],
        s.key ? `${s.name}: ${amount(s.left, s.unit)} left of ${amount(s.allowance, s.unit)}; grey: the reserve the tool keeps (${reserve} searches)`
              : `${s.name}: no key`),
      h("div", { class: "hole" }, s.key ? [h("b", null, compact(s.left, s.unit)), h("span", null, unitWord)] : h("span", null, "no key"))),
    h("div", { class: "limit" }, s.key ? `of ${amount(s.allowance, s.unit)}` : "\u00a0"));
}

function stackedBars(days, keyOf, title) {
  if (!days || !days.length) return h("div", { class: "card chart empty" }, "No data");
  const keys = [...new Set(days.flatMap(d => Object.keys(keyOf(d))))]
    .sort((a, b) => days.reduce((n, d) => n + (keyOf(d)[b] || 0), 0) - days.reduce((n, d) => n + (keyOf(d)[a] || 0), 0));
  const W = 900, H = 170, L = 30, B = 22, max = Math.max(1, ...days.map(d => d.total));
  const step = (W - L) / days.length, bw = Math.max(2, step - 4);
  const svg = h("svg:svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title });
  const y = v => (H - B) - (H - B - 8) * v / max;
  for (const v of [0, Math.ceil(max / 2), max]) {
    svg.append(h("svg:line", { x1: L, x2: W, y1: y(v), y2: y(v), stroke: "currentColor", "stroke-opacity": 0.12 }));
    svg.append(h("svg:text", { x: L - 6, y: y(v) + 3, "text-anchor": "end" }, v));
  }
  days.forEach((d, i) => {
    const x = L + i * step + 2; let base = 0;
    for (const k of keys) {
      const n = keyOf(d)[k] || 0; if (!n) continue;
      svg.append(h("svg:rect", { x, width: bw, y: y(base + n), height: y(base) - y(base + n), fill: colorOf(k), rx: 1 },
        h("svg:title", null, `${d.day}: ${k} ${n}`)));
      base += n;
    }
    if (i % 5 === 0 || i === days.length - 1)
      svg.append(h("svg:text", { x: x + bw / 2, y: H - 6, "text-anchor": "middle" }, short(d.day)));
  });
  return [h("div", { class: "sub", style: "font-size:12px;margin-bottom:4px" }, title), svg,
          h("div", { class: "legend" }, keys.map(k => h("span", null, h("i", { style: `background:${colorOf(k)}` }), k)))];
}

function render(r) {
  const subEl = document.getElementById("sub");
  if (r.date) subEl.textContent = "Updated " + when(r.date);
  else subEl.textContent = "";

  const tilesEl = document.getElementById("tiles");
  tilesEl.innerHTML = "";
  tilesEl.append(
    h("div", { class: "tile" }, h("b", null, r.total || 0), h("span", null, "Total")),
    h("div", { class: "tile" }, h("b", null, r.today || 0), h("span", null, "Today"))
  );

  const svcEl = document.getElementById("services");
  svcEl.innerHTML = "";
  (r.services || []).forEach(s => svcEl.append(serviceCard(s, r.reserve)));

  document.getElementById("perService").innerHTML = "";
  document.getElementById("perService").append(stackedBars(r.days || [], null, "Requests by service"));
}

async function load() {
  const err = document.getElementById("error");
  try {
    const res = await fetch("/usage.json", { cache: "no-store" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.status);
    render(data);
    err.replaceChildren();
  } catch (e) {
    err.replaceChildren(h("div", { class: "error" }, `Could not load the usage data: ${e.message}. Retrying in 30 s.`));
  }
}

// keep a details popup inside the window: shift it left when it would run past the right edge
const fitPop = e => { const info = e.target.closest && e.target.closest(".info"); if (!info) return;
  const pop = info.querySelector(".pop"); pop.style.left = "0px";
  requestAnimationFrame(() => { const over = pop.getBoundingClientRect().right - (innerWidth - 16);
    if (over > 0) pop.style.left = -over + "px"; }); };
document.addEventListener("pointerover", fitPop);
document.addEventListener("focusin", fitPop);
load();
setInterval(load, 30000);
