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
  list.innerHTML = "";
  if (!state || !state.length) {
    list.appendChild(el("p", { class: "empty" }, "No chat types yet."));
    return;
  }
  for (const item of state) {
    const div = el("div", {
      class: `item${current && current.id === item.id ? " on" : ""}`,
      onclick: () => open(item.id),
    });
    div.appendChild(el("b", null, item.name));
    if (item.description) {
      div.appendChild(el("span", null, " — ", item.description));
    }
    list.appendChild(div);
  }
}

function rows(container, items, fields, addLabel) {
  for (const [label, key] of fields) {
    const row = el("div", { class: "row" });
    row.appendChild(el("label", null, label));
    const val = typeof key === "function" ? key(items) : items[key];
    if (typeof val === "string") {
      row.appendChild(el("textarea", { rows: 3 }, val));
    } else {
      row.appendChild(el("span", null, String(val ?? "")));
    }
    container.appendChild(row);
  }
  if (addLabel) {
    const extra = el("div", { class: "row" });
    extra.appendChild(el("label", null, addLabel));
    container.appendChild(extra);
  }
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
