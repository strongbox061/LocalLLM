"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const PIE_R = 24;
const PIE_C = 2 * Math.PI * PIE_R;

let latestData = null;

/* ---- DOM helpers ---- */

function h(tag, attrs, ...kids) {
  const el = tag.startsWith("svg:")
    ? document.createElementNS(SVG_NS, tag.slice(4))
    : document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "children") {
        for (const c of v) el.appendChild(c);
      } else if (k.startsWith("on")) {
        el.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (k === "textContent") {
        el.textContent = v;
      } else {
        el.setAttribute(k, v);
      }
    }
  }
  for (const k of kids) {
    if (k != null) el.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
  }
  return el;
}

/* ---- Formatters ---- */

function fmtMem(mib) {
  if (mib >= 1024) return (mib / 1024).toFixed(1) + " GB";
  return Math.round(mib) + " MB";
}

function fmtCapacityGB(mib) {
  return mib.toFixed(1) + " GB";
}

function fmtPct(pct) {
  return pct.toFixed(1) + "%";
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

/* ---- Brand / icon helpers (simple SVG icons) ---- */

const LIST_ICON = () => h("svg:svg", { viewBox: "0 0 16 16", "aria-hidden": "true" },
  h("svg:line", { x1: "2", y1: "4", x2: "14", y2: "4" }),
  h("svg:line", { x1: "2", y1: "8", x2: "14", y2: "8" }),
  h("svg:line", { x1: "2", y1: "12", x2: "14", y2: "12" }),
);

function buildPieSvg(percent, ariaLabel) {
  const clamped = Math.max(0, Math.min(100, percent));
  const offset = PIE_C * (1 - clamped / 100);
  return h("svg:svg", { viewBox: "0 0 56 56", width: "56", height: "56", "aria-label": ariaLabel },
    h("svg:circle", { cx: "28", cy: "28", r: String(PIE_R), fill: "none",
      stroke: "var(--pie-free)", "stroke-width": "6" }),
    h("svg:circle", { cx: "28", cy: "28", r: String(PIE_R), fill: "none",
      stroke: "var(--pie-used)", "stroke-width": "6",
      "stroke-dasharray": String(PIE_C), "stroke-dashoffset": String(offset),
      "stroke-linecap": "round", transform: "rotate(-90 28 28)" }),
  );
}

/* ---- Pie card (gauge + label) ---- */

function pieCard(opts) {
  const { percent, label, sublabel } = opts;
  return h("div", { class: "pie-card" },
    buildPieSvg(percent, label),
    h("div", { class: "pie-info" },
      h("div", { class: "pie-label" }, label),
      h("div", { class: "pie-sub" }, sublabel || fmtPct(percent)),
    ),
  );
}

/* ---- Machine card ---- */

function renderMachineCard(machineKey, title, machine, isHelper) {
  const cards = [];

  // CPU gauge
  if (machine.cpu_percent != null) {
    cards.push(h("div", { class: "machine-section" },
      h("h4", null, "CPU"),
      pieCard({ percent: machine.cpu_percent, label: "CPU Usage", sublabel: fmtPct(machine.cpu_percent) }),
    ));
  }

  // RAM gauge
  if (machine.ram) {
    const pct = machine.ram.percent;
    cards.push(h("div", { class: "machine-section" },
      h("h4", null, "RAM"),
      pieCard({ percent: pct, label: "RAM Usage", sublabel: fmtMem(machine.ram.used_gb * 1024) + " / " + fmtMem(machine.ram.total_gb * 1024) }),
    ));
  }

  // GPU info
  if (machine.gpu && machine.gpu.length > 0) {
    for (const gpu of machine.gpu) {
      cards.push(h("div", { class: "machine-section" },
        h("h4", null, "GPU: " + gpu.name),
        h("div", { class: "gpu-info" },
          h("span", null, "Temp: " + gpu.temp + "°C"),
          h("span", null, "Util: " + fmtPct(gpu.util)),
          h("span", null, "VRAM: " + gpu.mem_used.toFixed(0) + " / " + gpu.mem_total.toFixed(0) + " MB"),
        ),
      ));
    }
  }

  // Process list button
  cards.push(h("div", { class: "machine-section" },
    h("h4", null, "Top Processes"),
    h("button", { type: "button", class: "proc-btn", "data-machine": machineKey }, "View processes"),
  ));

  return h("div", { class: "machine", "data-machine": machineKey },
    h("div", { class: "machine-head" },
      h("h2", { class: "machine-title" }, title),
      isHelper ? h("span", { class: "badge single" }, "Helper") : h("span", { class: "badge single" }, "Main"),
    ),
    ...cards,
  );
}

/* ---- Modal ---- */

let openModalState = null;

function openProcessModal(machineKey, machineTitle, component) {
  const data = latestData;
  if (!data) return;

  let source = null;
  if (machineKey === "main") {
    source = data.processes || [];
  } else if (data.helper) {
    source = data.helper.processes || [];
  }

  openModalState = { machineKey, machineTitle, component };

  document.getElementById("modalTitle").textContent = machineTitle + " — Processes";
  document.getElementById("modalSub").textContent = component.toUpperCase() + " process list";

  const tbody = h("table", { class: "proc-table" },
    h("thead", null,
      h("tr", null,
        h("th", { class: "col-pid" }, "PID"),
        h("th", { class: "col-name" }, "Name"),
        h("th", { class: "col-cpu" }, "CPU Time"),
      ),
    ),
    h("tbody", null,
      ...source.slice(0, 20).map(p =>
        h("tr", null,
          h("td", { class: "col-pid" }, String(p.pid)),
          h("td", { class: "col-name" }, p.name),
          h("td", { class: "col-cpu" }, String(p.cpu_time)),
        ),
      ),
    ),
  );

  document.getElementById("modalBody").replaceChildren(tbody);
  document.getElementById("procBackdrop").classList.add("open");
}

function closeProcessModal() {
  document.getElementById("procBackdrop").classList.remove("open");
  openModalState = null;
}

/* ---- Render ---- */

function renderSetupBadges(setup) {
  const modeBadge = document.getElementById("modeBadge");
  const appsBadge = document.getElementById("appsBadge");
  if (setup) {
    modeBadge.textContent = "Mode: " + setup.mode;
    modeBadge.className = "badge ok";
    appsBadge.textContent = "Apps: " + setup.apps;
    appsBadge.className = "badge ok";
  }
}

function render(data) {
  latestData = data;
  const errorBox = document.getElementById("errorBox");
  const machines = document.getElementById("machines");
  errorBox.replaceChildren();
  machines.replaceChildren();

  document.getElementById("updated").textContent = "Updated: " + fmtTime(data.ts);

  // Render main PC
  const mainCard = renderMachineCard("main", "Main PC", data, false);
  machines.appendChild(mainCard);

  // Render helper PC if available
  if (data.helper) {
    const helperCard = renderMachineCard("helper", "Helper PC", data.helper, true);
    machines.appendChild(helperCard);
  }

  // Attach process button listeners
  machines.querySelectorAll(".proc-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      openProcessModal(btn.dataset.machine, btn.closest(".machine").querySelector(".machine-title").textContent, "CPU");
    });
  });

  renderSetupBadges(data.setup);
}

/* ---- Data loading ---- */

let timerId = null;

async function load(forceRefresh = false) {
  const errorBox = document.getElementById("errorBox");
  try {
    const url = forceRefresh ? "/api/snapshot?t=" + Date.now() : "/api/snapshot";
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    render(data);
  } catch (err) {
    errorBox.innerHTML = '<div class="error-banner">Error: ' + err.message + '</div>';
  }
}

function schedule() {
  if (timerId) clearInterval(timerId);
  const sel = document.getElementById("intervalSel");
  timerId = setInterval(() => load(), parseInt(sel.value, 10) * 1000);
}

/* ---- Event wiring ---- */

document.getElementById("procBackdrop").addEventListener("click", e => {
  if (e.target === document.getElementById("procBackdrop")) closeProcessModal();
});

document.getElementById("modalCloseBtn").addEventListener("click", closeProcessModal);

document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeProcessModal();
});

document.getElementById("refreshBtn").addEventListener("click", () => load(true));

document.getElementById("intervalSel").addEventListener("change", schedule);

/* ---- Boot ---- */

load().then(schedule);
