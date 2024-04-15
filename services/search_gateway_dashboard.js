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
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (k === "className") el.className = v;
    else if (k === "textContent") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) el.append(typeof k === "string" ? document.createTextNode(k) : k);
  return el;
}

function amount(x, unit) {
  if (x >= 1e6) return (x / 1e6).toFixed(1) + "M" + (unit || "");
  if (x >= 1e3) return (x / 1e3).toFixed(1) + "K" + (unit || "");
  return x + (unit || "");
}

function when(t) {
  return t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }) : "";
}

function short(d) {
  return d ? new Date(d + "T00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
}

function fact(label, value) {
  return h("div", null, h("span", null, label + " "), h("b", null, value));
}

const R = 46, C = 2 * Math.PI * R;
function donut(parts, label) {
  const circles = parts.map(([frac, color]) => {
    const dash = (Math.max(frac, 0.001) * C).toFixed(2);
    return h("svg:circle", { cx: 58, cy: 58, r: R, fill: "none", stroke: color,
      "stroke-width": 16, "stroke-dasharray": dash + " " + (C - dash),
      "stroke-dashoffset": -C / 4, transform: "rotate(-90 58 58)" });
  });
  const svg = h("svg:svg", { viewBox: "0 0 116 116", class: "donut" }, ...circles);
  const hole = h("div", { class: "hole" }, h("b", null, label));
  return h("div", { class: "donut-wrap" }, svg, hole);
}

function compact(x, unit) {
  return amount(x, unit);
}

const CHEVRON = () => h("svg:svg", { viewBox: "0 0 16 16", "aria-hidden": "true", width: 12, height: 12 },
  h("path", { d: "M4.5 3l5 5-5 5" }));

function info(name, ...kids) {
  return h("div", { class: "info" },
    h("span", null, name),
    ...kids,
    h("button", { "aria-label": "Details", title: "Details" }, CHEVRON()));
}

function serviceCard(s, reserve) {
  const [cls, statusText] = STATUS[s.status] || ["", s.status];
  const pct = reserve ? Math.round((s.used / reserve) * 100) : null;
  return h("div", { class: "svc " + cls },
    info(s.name),
    h("span", { class: "pill " + cls }, statusText),
    pct != null && h("span", { class: "limit" }, compact(pct, "%")),
    h("div", { class: "meta" },
      fact("Used", compact(s.used)),
      reserve && fact("Limit", compact(reserve)),
      fact("Today", s.today || 0),
      s.last && fact("Last", when(s.last))
    )
  );
}

function stackedBars(days, keyOf, title) {
  if (!days || !days.length) return h("div", { class: "card chart empty" }, "No data");
  const max = Math.max(...days.map(d => Object.values(d).reduce((a, v) => a + (v || 0), 0)));
  if (!max) return h("div", { class: "card chart empty" }, "All zeros");
  const labels = days.map((d, i) => short(d.date));
  const keys = [...new Set(days.flatMap(d => Object.keys(d).filter(k => k !== "date")))].slice(0, 12);
  const barW = Math.max(4, Math.min(20, 300 / days.length));
  const svgH = 180;
  const bars = days.map((d, i) => {
    let yOff = 0;
    const segs = keys.filter(k => d[k]).map(k => {
      const segH = (d[k] / max) * svgH;
      const row = h("svg:rect", { x: i * barW + 2, y: svgH - yOff - segH,
        width: barW - 3, height: segH, fill: colorOf(k) });
      yOff += segH;
      return row;
    });
    return h("g", null, ...segs);
  });
  const legend = keys.map(k => h("span", { style: "border-left:3px solid " + colorOf(k) },
    " ", k));
  return h("div", { class: "card chart" },
    h("h3", null, title),
    h("svg:svg", { viewBox: "0 0 " + (days.length * barW + 10) + " " + (svgH + 30), width: "100%" },
      ...bars,
      ...labels.filter((_, i) => i % Math.max(1, Math.floor(days.length / 8)) === 0).map((l, j, arr) =>
        h("svg:text", { x: arr.indexOf(l) * barW + 2 + barW / 2, y: svgH + 18, "text-anchor": "middle",
          "font-size": 9, fill: "#5f6b77" }, l)))
    ),
    h("div", { class: "legend" }, ...legend)
  );
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
  try {
    const res = await fetch("/usage.json");
    if (!res.ok) throw new Error("HTTP " + res.status);
    render(await res.json());
    document.getElementById("error").textContent = "";
  } catch (e) {
    document.getElementById("error").textContent = "Failed to load data: " + e.message;
  }
}

load();
setInterval(load, 30000);
