"use strict";
let state = null, current = null, isNew = false, polling = null;
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}

const lines = (text) => text.split("\n").map((s) => s.trim()).filter(Boolean);
const when = (sec) => sec ? new Date(sec * 1000).toLocaleString() : "";

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  let data = {};
  try { data = await r.json(); } catch { data = { error: `HTTP ${r.status}` }; }
  return { ok: r.ok, status: r.status, data };
}

async function load(selectId) {
  const r = await api("/chat-types/data");
  if (!r.ok) { $("error").replaceChildren(el("div", { class: "msg bad" }, r.data.error || "Can't load the chat types.")); return; }
  $("error").replaceChildren(r.data.status_error
    ? el("div", { class: "msg bad" }, `Open WebUI's side can't be read (${r.data.status_error}): the files show, without their presets' status. Saving still needs it.`)
    : "");
  state = r.data;
  drawList();
  const pick = selectId ?? (current && !isNew ? current : null);
  if (pick && state.types.some((t) => t.id === pick)) open(pick);
  if (state.job && (state.job.running || state.job.output)) showJob(state.job);
  if (state.job && state.job.running) poll();
}

function drawList() {
  const list = $("list");
  list.replaceChildren();
  if (!state.types.length) list.append(el("div", { class: "sub" }, "No chat types yet."));
  for (const t of state.types) {
    const pills = [];
    if (t.problems.length) pills.push(el("span", { class: "pill bad" }, "file has problems"));
    else if (t.preset.exists) pills.push(el("span", { class: "pill ok" }, "in Open WebUI"));
    else pills.push(el("span", { class: "pill warn" }, "not built yet"));
    if ((t.data.sources || []).length) {
      const n = t.knowledge ? t.knowledge.files : 0;
      pills.push(el("span", { class: `pill ${n >= t.data.sources.length ? "ok" : "warn"}` },
        `sources ${n}/${t.data.sources.length}`));
    }
    list.append(el("button", { class: `item${current === t.id && !isNew ? " on" : ""}`, onclick: () => open(t.id) },
      el("b", {}, t.data.name || t.id), el("span", {}, t.id), el("div", { class: "status" }, pills)));
  }
}

function rows(container, items, fields, addLabel) {
  // fields: [{key, placeholder, multiline, mono}]
  const box = el("div");
  const add = (item = {}) => {
    const inputs = fields.map((f) => {
      const value = Array.isArray(item[f.key]) ? item[f.key].join("\n") : (item[f.key] || "");
      const attrs = { placeholder: f.placeholder, class: f.mono ? "mono" : "", "data-key": f.key };
      const input = f.multiline ? el("textarea", { ...attrs, rows: Math.min(6, Math.max(2, value.split("\n").length + 1)) })
                                : el("input", { ...attrs, type: "text" });
      input.value = value;
      return input;
    });
    const row = el("div", { class: "row" }, inputs, el("button", { class: "small", type: "button", title: "Remove", onclick: () => row.remove() }, "\u00d7"));
    box.append(row);
  };
  (items || []).forEach(add);
  container.append(box, el("button", { class: "small", type: "button", onclick: () => add() }, addLabel));
  return () => [...box.children].map((row) => {
    const o = {};
    for (const input of row.querySelectorAll("[data-key]")) {
      const f = fields.find((x) => x.key === input.dataset.key);
      o[f.key] = f.list ? lines(input.value) : input.value.trim();
    }
    return o;
  }).filter((o) => Object.values(o).some((v) => (Array.isArray(v) ? v.length : v)));
}

function open(id, fromTemplate = false) {
  clearTimeout(polling);
  polling = null;
  const item = state.find((x) => x.id === id);
  if (!item) return;
  current = item;
  isNew = false;
  drawList();

  const editor = $("editor");
  editor.innerHTML = "";

  const title = el("h3", null, item.name);
  editor.appendChild(title);

  const info = el("div", { class: "actions" });
  info.appendChild(el("button", {
    class: "primary",
    onclick: () => save(item),
  }, "Save"));
  info.appendChild(el("button", {
    class: "danger",
    onclick: () => remove(item.id),
  }, "Delete"));
  editor.appendChild(info);

  rows(editor, item, [
    ["Name:", "name"],
    ["Description:", "description"],
    ["System prompt:", "system_prompt"],
  ]);

  if (item.users !== undefined) {
    const userRow = el("div", { class: "row" });
    userRow.appendChild(el("label", null, "Allowed users:"));
    const ul = el("ul", { class: "users" });
    for (const u of item.users) {
      ul.appendChild(el("li", null, u));
    }
    userRow.appendChild(ul);
    editor.appendChild(userRow);
  }

  if (item.created_at) {
    editor.appendChild(el("p", { class: "meta" }, `Created ${when(item.created_at)}`));
  }
}

function showJob(j) {
  const job = $("job");
  $("job-title").textContent = j.title || "Running…";
  $("job-out").textContent = j.output || "";
  job.hidden = false;
  if (j.done) {
    clearTimeout(polling);
    polling = null;
    load();
  }
}

async function save(item) {
  try {
    const result = await api("/chat-types/" + item.id, item);
    Object.assign(current, result);
    drawList();
    showJob({ title: "Saved", output: "Chat type updated.", done: true });
  } catch (e) {
    $("error").textContent = e.message;
  }
}

async function remove(id) {
  if (!confirm("Delete this chat type?")) return;
  try {
    await api("/chat-types/" + id, null);
    current = null;
    $("editor").innerHTML = '<div class="empty">Pick a chat type, or make a new one.</div>';
    load();
  } catch (e) {
    $("error").textContent = e.message;
  }
}

async function poll() {
  if (!current || !current._job_id) return;
  try {
    const j = await api("/chat-types/job/" + current._job_id);
    showJob(j);
    if (!j.done) polling = setTimeout(poll, 1000);
  } catch (_) {
    polling = setTimeout(poll, 2000);
  }
}

$("new").addEventListener("click", async () => {
  try {
    const item = await api("/chat-types/new");
    isNew = true;
    state.push(item);
    open(item.id);
  } catch (e) {
    $("error").textContent = e.message;
  }
});

document.addEventListener("DOMContentLoaded", () => {
  load().catch((e) => {
    $("error").textContent = "Failed to load: " + e.message;
  });
});
