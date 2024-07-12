"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const PIE_R = 24;
const PIE_C = 2 * Math.PI * PIE_R;

let latestData = null;
let openModalState = null; // { machineKey: "main"|"helper", machineTitle, component: "cpu"|"ram"|"gpu_vram"|"gpu_load", kindLabel }

function h(tag, attrs, ...kids) {
  const el = tag.startsWith("svg:")
    ? document.createElementNS(SVG_NS, tag.slice(4))
    : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v !== undefined && v !== null) el.setAttribute(k, String(v));
  }
  for (const kid of kids.flat()) {
    if (kid !== null && kid !== undefined && kid !== false) {
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
  }
  return el;
}

function fmtMem(mib) {
  if (mib === null || mib === undefined || Number.isNaN(Number(mib))) return "–";
  const v = Number(mib);
  if (v < 1024) return `${Math.round(v).toLocaleString()} MB`;
  const gb = v / 1024;
  return (gb >= 10 ? gb.toFixed(1) : gb.toFixed(2)) + " GB";
}

function fmtCapacityGB(mib) {
  if (mib === null || mib === undefined || Number.isNaN(Number(mib))) return "";
  const gb = Number(mib) / 1024;
  const rounded = Math.round(gb);
  if (rounded > 0 && Math.abs(gb - rounded) / rounded < 0.03) {
    return `${rounded} GB`;
  }
  return `${gb >= 10 ? gb.toFixed(1) : gb.toFixed(2)} GB`;
}

function fmtPct(pct) {
  if (pct === null || pct === undefined || Number.isNaN(Number(pct))) return "–";
  return Number(pct).toFixed(1) + "%";
}

function fmtTime(ts) {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleTimeString();
}

function fnv1a(str) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash;
}

// Brand color palette indexed by FNV-1a hash of the normalized primary vendor word
// so brand styling works dynamically without hardcoding vendor names in the repo.
const BRAND_STYLES = {
  0x5f3e70e5: { bg: "#00539b", fg: "#ffffff" }, // primary blue
  0x0b3c2220: { bg: "#00539b", fg: "#ffffff" },
  0x9a3a5e78: { bg: "#e2231a", fg: "#ffffff" }, // signature red
  0x55d0e1a8: { bg: "#007db8", fg: "#ffffff" },
  0x0a660417: { bg: "#0096d6", fg: "#ffffff" },
  0x30c028a0: { bg: "#0096d6", fg: "#ffffff" },
  0xd50f50a5: { bg: "#d91a21", fg: "#ffffff" },
  0x2c0fd8e1: { bg: "#83b81a", fg: "#ffffff" },
  0x67b604e0: { bg: "#ea580c", fg: "#ffffff" },
  0x3f24d47d: { bg: "#16a34a", fg: "#ffffff" },
  0xa4f4c8ab: { bg: "#1428a0", fg: "#ffffff" },
  0x4f03923e: { bg: "#1f2937", fg: "#ffffff" },
};
const FALLBACK_BRAND_COLORS = ["#1d4ed8", "#0f766e", "#7c3aed", "#b45309", "#be123c", "#334155"];

function cleanBrandLabel(raw) {
  const s = (raw || "").trim();
  if (!s) return "";
  const stripped = s
    .replace(/\b(computer|computers|technology|technologies|international|corporation|corp|inc|ltd|co|llc|gmbh)\b\.?/gi, "")
    .replace(/[.,]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const token = (stripped || s).split(" ")[0];
  return token.length > 12 ? token.slice(0, 12) : token;
}

function buildBrandIcon(brandRaw) {
  const label = cleanBrandLabel(brandRaw);
  if (!label) {
    return h("span", { class: "icon-badge", title: "PC Hardware" },
      h("svg:svg", { viewBox: "0 0 24 24", "aria-label": "PC Hardware" },
        h("svg:rect", { x: 3, y: 4, width: 18, height: 12, rx: 2, fill: "none", stroke: "currentColor", "stroke-width": 2 }),
        h("svg:path", { d: "M8 20h8M12 16v4", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round" })
      )
    );
  }
  const key = fnv1a(label.toLowerCase());
  const style = BRAND_STYLES[key] || {
    bg: FALLBACK_BRAND_COLORS[key % FALLBACK_BRAND_COLORS.length],
    fg: "#ffffff",
  };
  return h("span", {
    class: "brand-pill",
    style: `background:${style.bg};color:${style.fg}`,
    title: brandRaw,
  }, label.toUpperCase());
}

function buildOsIcon(osId, osName) {
  const id = (osId || "").toLowerCase();
  const title = osName || "OS";
  let svg;

  if (id === "windows") {
    svg = h("svg:svg", { viewBox: "0 0 24 24", role: "img", "aria-label": title },
      h("svg:rect", { x: 2, y: 2, width: 9, height: 9, rx: 1, fill: "#0078d4" }),
      h("svg:rect", { x: 13, y: 2, width: 9, height: 9, rx: 1, fill: "#0078d4" }),
      h("svg:rect", { x: 2, y: 13, width: 9, height: 9, rx: 1, fill: "#0078d4" }),
      h("svg:rect", { x: 13, y: 13, width: 9, height: 9, rx: 1, fill: "#0078d4" })
    );
  } else if (id === "arch") {
    svg = h("svg:svg", { viewBox: "0 0 24 24", role: "img", "aria-label": title },
      h("svg:path", {
        d: "M12 2C10.8 5 9.8 6.9 8.5 9.5c.8.8 1.9 1.7 3.5 2.6-1.8-.7-3-.9-4.1-1.3C6.1 14.4 4.5 18 2 22c3.5-2 6.2-3.2 8.7-3.6-.1-.5-.2-1-.2-1.6 0-1.9 1-3.3 2.2-3.2 1.2.1 2 1.5 1.9 3.4 0 .5-.1 1-.2 1.4 2.5.5 5.2 1.6 7.6 3.6-1.5-2.8-2.6-5.2-3.8-7.5-1.4-.9-2.8-1.9-4.6-2.8 1.6.4 2.8.9 3.7 1.4C14.8 7.5 13.7 5.2 12 2z",
        fill: "#1793d1",
      })
    );
  } else if (id === "ubuntu") {
    svg = h("svg:svg", { viewBox: "0 0 24 24", role: "img", "aria-label": title },
      h("svg:circle", { cx: 12, cy: 12, r: 9.5, fill: "#e95420" }),
      h("svg:circle", { cx: 12, cy: 12, r: 4.5, fill: "none", stroke: "#ffffff", "stroke-width": 1.8 }),
      h("svg:circle", { cx: 12, cy: 5.5, r: 1.5, fill: "#ffffff" }),
      h("svg:circle", { cx: 6.4, cy: 15.2, r: 1.5, fill: "#ffffff" }),
      h("svg:circle", { cx: 17.6, cy: 15.2, r: 1.5, fill: "#ffffff" })
    );
  } else if (id === "fedora") {
    svg = h("svg:svg", { viewBox: "0 0 24 24", role: "img", "aria-label": title },
      h("svg:circle", { cx: 12, cy: 12, r: 10, fill: "#294172" }),
      h("svg:path", { d: "M13.5 6.5c-1.7 0-3 1.3-3 3v5c0 1.4-1.1 2.5-2.5 2.5S5.5 15.9 5.5 14.5 6.6 12 8 12h6.5", fill: "none", stroke: "#ffffff", "stroke-width": 2.2, "stroke-linecap": "round" })
    );
  } else if (id === "debian") {
    svg = h("svg:svg", { viewBox: "0 0 24 24", role: "img", "aria-label": title },
      h("svg:path", { d: "M12 3a9 9 0 1 0 6.5 15.2M12 7a5 5 0 1 0 3.5 8.5", fill: "none", stroke: "#d70a53", "stroke-width": 2.2, "stroke-linecap": "round" })
    );
  } else {
    svg = h("svg:svg", { viewBox: "0 0 24 24", role: "img", "aria-label": title },
      h("svg:circle", { cx: 12, cy: 12, r: 9.5, fill: "none", stroke: "currentColor", "stroke-width": 2 }),
      h("svg:path", { d: "M8 15c1.2 1.5 6.8 1.5 8 0M9.5 9.5h.01M14.5 9.5h.01", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round" })
    );
  }

  return h("span", { class: "icon-badge", title }, svg);
}

/**
 * Filled SVG pie chart:
 * - Full circle in blue (--pie-free): max capacity / memory (100%)
 * - Red slice (--pie-used): occupied capacity / memory (0..100%)
 */
function buildPieSvg(percent, ariaLabel) {
  const valid = percent !== null && percent !== undefined && !Number.isNaN(Number(percent));
  const clamped = valid ? Math.max(0, Math.min(100, Number(percent))) : 0;
  const usedLen = (clamped / 100) * PIE_C;

  const svg = h("svg:svg", { viewBox: "0 0 100 100", role: "img", "aria-label": ariaLabel },
    h("svg:title", null, ariaLabel),
    h("svg:circle", {
      cx: 50, cy: 50, r: 48,
      fill: valid ? "var(--pie-free)" : "var(--pie-empty)",
    })
  );

  if (valid && clamped > 0) {
    svg.append(h("svg:circle", {
      class: "pie-slice",
      cx: 50, cy: 50, r: PIE_R,
      fill: "none",
      stroke: "var(--pie-used)",
      "stroke-width": 48,
      "stroke-dasharray": `${usedLen} ${PIE_C}`,
      transform: "rotate(-90 50 50)",
    }));
  }

  svg.append(h("svg:circle", {
    cx: 50, cy: 50, r: 48,
    fill: "none",
    stroke: "var(--line)",
    "stroke-width": 1.2,
  }));
  return svg;
}

const LIST_ICON = () => h("svg:svg", { viewBox: "0 0 16 16", "aria-hidden": "true" },
  h("svg:path", { d: "M3 4h10M3 8h10M3 12h7", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round" })
);

function pieCard(opts) {
  const { machineKey, machineTitle, component, kind, deviceName, specLine, percent, occupiedText, detailText } = opts;
  const label = `${kind} (${deviceName}${specLine ? " · " + specLine : ""}): ${occupiedText} occupied`;
  const open = () => openProcessModal(machineKey, machineTitle, component, kind);
  const btn = h("button", {
    type: "button",
    class: "pie-kind-btn",
    title: `Click to view ${kind} processes on ${machineTitle}`,
  }, kind, LIST_ICON());
  btn.addEventListener("click", open);

  const deviceEl = h("div", {
    class: "pie-device",
    role: "button",
    tabindex: "0",
    title: `Click to view ${kind} processes on ${machineTitle}`,
  },
    h("span", null, deviceName),
    specLine ? h("span", { class: "pie-spec" }, specLine) : null
  );
  deviceEl.addEventListener("click", open);
  deviceEl.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
  });

  return h("div", { class: "pie-card" },
    btn,
    deviceEl,
    h("div", { class: "pie-wrap" }, buildPieSvg(percent, label)),
    h("div", { class: "pie-occupied" }, occupiedText),
    detailText ? h("div", { class: "pie-detail" }, detailText) : null
  );
}

function statusBadge(machine, isHelper, mode) {
  const st = machine.status;
  if (st === "online") {
    if (isHelper && mode === "single") {
      return h("span", { class: "badge warn" }, "Online (standby · single mode)");
    }
    return h("span", { class: "badge ok" }, "Online");
  }
  if (st === "loading") return h("span", { class: "badge muted" }, "Connecting…");
  if (st === "not_configured") return h("span", { class: "badge muted" }, "Not configured");
  return h("span", { class: "badge bad" }, "Offline / Unreachable");
}

function formatCpuSpec(cpu) {
  const parts = [];
  if (cpu.normal_ghz && cpu.performant_ghz && cpu.performant_ghz !== cpu.normal_ghz) {
    parts.push(`Normal ${cpu.normal_ghz.toFixed(2)} GHz · Performant ${cpu.performant_ghz.toFixed(2)} GHz`);
  } else if (cpu.performant_ghz) {
    parts.push(`Max ${cpu.performant_ghz.toFixed(2)} GHz`);
  } else if (cpu.normal_ghz) {
    parts.push(`${cpu.normal_ghz.toFixed(2)} GHz`);
  }
  return parts.join(" · ");
}

function renderMachineCard(machineKey, title, machine, isHelper, setup) {
  const badges = [statusBadge(machine, isHelper, setup.mode)];
  if (isHelper && machine.gaming) {
    badges.push(h("span", { class: "badge warn" }, "Gaming mode ON"));
  }

  const metaParts = [machine.os || (isHelper ? "Windows" : "Linux")];
  if (machine.hostname) metaParts.push(machine.hostname);
  if (isHelper && machine.host && machine.host !== machine.hostname) metaParts.push(machine.host);
  if (machine.collected_at) metaParts.push("updated " + fmtTime(machine.collected_at));

  const icons = h("span", { class: "icon-pair" },
    buildBrandIcon(machine.brand || ""),
    buildOsIcon(machine.os_id || (isHelper ? "windows" : "linux"), machine.os || (isHelper ? "Windows" : "Linux"))
  );

  const head = h("div", { class: "machine-head" },
    h("div", { class: "machine-title" },
      h("div", { class: "machine-title-row" },
        h("h2", null, title),
        icons
      ),
      h("div", { class: "machine-meta" }, metaParts.join(" · "))
    ),
    h("div", { class: "machine-badges" }, badges)
  );

  if (machine.status !== "online") {
    const msg = machine.error || (machine.status === "loading"
      ? "Querying helper PC over SSH…"
      : "Helper PC is not reachable.");
    return h("article", { class: "machine" },
      head,
      h("div", { class: "notice" }, msg)
    );
  }

  const cards = [];

  // CPU
  const cpu = machine.cpu || {};
  const cpuName = cpu.name || "CPU";
  const cpuSpec = formatCpuSpec(cpu);
  const cpuDetail = cpu.threads ? `${cpu.threads} threads · occupied` : "occupied";
  cards.push(pieCard({
    machineKey, machineTitle: title, component: "cpu", kind: "CPU",
    deviceName: cpuName, specLine: cpuSpec, percent: cpu.percent,
    occupiedText: fmtPct(cpu.percent), detailText: cpuDetail,
  }));

  // RAM
  const ram = machine.ram || {};
  const ramCapMib = ram.installed_mib || ram.total_mib;
  const ramCapLabel = fmtCapacityGB(ramCapMib);
  const ramNameBase = ram.name || "System RAM";
  const ramHeader = ramCapLabel ? `${ramNameBase} · ${ramCapLabel}` : ramNameBase;
  const ramSpec = [
    ram.speed_mhz ? `${ram.speed_mhz} MT/s` : null,
    ram.total_mib ? `${fmtMem(ram.total_mib)} usable` : null,
  ].filter(Boolean).join(" · ");
  const ramOccupied = fmtMem(ram.used_mib);
  const ramDetail = ram.total_mib
    ? `${Math.round(ram.used_mib || 0).toLocaleString()} MB of ${Math.round(ram.total_mib).toLocaleString()} MB (${fmtPct(ram.percent)})`
    : "Unavailable";
  cards.push(pieCard({
    machineKey, machineTitle: title, component: "ram", kind: "RAM",
    deviceName: ramHeader, specLine: ramSpec, percent: ram.percent,
    occupiedText: ramOccupied, detailText: ramDetail,
  }));

  // GPUs
  const gpus = Array.isArray(machine.gpus) ? machine.gpus : [];
  if (gpus.length > 0) {
    gpus.forEach((gpu, idx) => {
      const prefix = gpus.length > 1 ? `GPU ${idx} ` : "GPU ";
      const vramCap = fmtCapacityGB(gpu.mem_total_mib);
      const gpuHeader = vramCap ? `${gpu.name || "GPU"} · ${vramCap}` : (gpu.name || "GPU");
      const tempSpec = gpu.temp_c !== null && gpu.temp_c !== undefined ? `${gpu.temp_c}°C` : "";

      const vramOccupied = fmtMem(gpu.mem_used_mib);
      const vramDetail = gpu.mem_total_mib
        ? `${Math.round(gpu.mem_used_mib || 0).toLocaleString()} MB of ${Math.round(gpu.mem_total_mib).toLocaleString()} MB (${fmtPct(gpu.mem_percent)})`
        : "Unavailable";
      cards.push(pieCard({
        machineKey, machineTitle: title, component: "gpu_vram", kind: prefix + "VRAM",
        deviceName: gpuHeader, specLine: tempSpec, percent: gpu.mem_percent,
        occupiedText: vramOccupied, detailText: vramDetail,
      }));

      const loadOccupied = fmtPct(gpu.util_percent);
      const loadDetail = tempSpec ? `Compute load · ${tempSpec}` : "Compute load";
      cards.push(pieCard({
        machineKey, machineTitle: title, component: "gpu_load", kind: prefix + "Load",
        deviceName: gpuHeader, specLine: "", percent: gpu.util_percent,
        occupiedText: loadOccupied, detailText: loadDetail,
      }));
    });
  } else {
    cards.push(pieCard({
      machineKey, machineTitle: title, component: "gpu_vram", kind: "GPU VRAM",
      deviceName: "No GPU detected", specLine: "", percent: null,
      occupiedText: "–", detailText: machine.gpu_error || "Unavailable",
    }));
    cards.push(pieCard({
      machineKey, machineTitle: title, component: "gpu_load", kind: "GPU Load",
      deviceName: "No GPU detected", specLine: "", percent: null,
      occupiedText: "–", detailText: machine.gpu_error || "Unavailable",
    }));
  }

  return h("article", { class: "machine" },
    head,
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
