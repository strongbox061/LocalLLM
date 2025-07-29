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
  if (!state || !state.types.length) list.append(el("div", { class: "sub" }, "No chat types yet."));
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
    const row = el("div", { class: "row" }, inputs, el("button", { class: "small", type: "button", title: "Remove", onclick: () => row.remove() }, "×"));
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
  isNew = fromTemplate;
  current = id;
  const t = fromTemplate ? { id: "", data: structuredClone(state.template), problems: [], preset: {}, knowledge: null }
                         : state.types.find((x) => x.id === id);
  const d = t.data;
  drawList();
  const f = {};
  const field = (key, label, hint, input) => { f[key] = input; return [el("label", { for: `f-${key}` }, label), hint ? el("div", { class: "hint" }, hint) : null, input]; };
  const text = (key, value, attrs = {}) => { const i = el("input", { type: "text", id: `f-${key}`, ...attrs }); i.value = value || ""; return i; };
  const area = (key, value, rows_, attrs = {}) => { const i = el("textarea", { id: `f-${key}`, rows: rows_, ...attrs }); i.value = value || ""; return i; };

  const status = [];
  if (!fromTemplate) {
    status.push(t.preset.exists ? `In Open WebUI as "chat-${t.id}", updated ${when(t.preset.updated)}` : "Not in Open WebUI yet: save to build it");
    if (t.knowledge) status.push(`Knowledge base: ${t.knowledge.files} file(s)`);
  }
  const problems = t.problems.length ? el("div", { class: "msg bad" }, "This file has problems:", el("ul", {}, t.problems.map((p) => el("li", {}, p)))) : null;

  // Accounts: every Open WebUI account, plus emails in the file with no account yet.
  const chosen = new Set((d.users || []).map((u) => u.toLowerCase()));
  const accounts = state.users.map((u) => ({ ...u, key: (u.email || "").toLowerCase() }));
  for (const e of chosen) if (!accounts.some((a) => a.key === e)) accounts.push({ email: e, name: "", role: "no account yet", key: e });
  const userBox = el("div", { class: "users" }, accounts.map((a) => el("label", {},
    el("input", { type: "checkbox", value: a.email, checked: chosen.has(a.key) || false, disabled: a.role === "admin" || false }), " ",
    `${a.name ? a.name + " · " : ""}${a.email}`, el("span", { class: "sub" }, ` (${a.role === "admin" ? "admin: always sees it" : a.role})`))));

  const sourcesBox = el("div");
  const getSources = rows(sourcesBox, d.sources, [
    { key: "name", placeholder: "Name, e.g. Civil code" },
    { key: "urls", placeholder: "Official page(s), one per line. {n:03} = numbered pages 001, 002...", multiline: true, mono: true, list: true },
  ], "+ Add a source");
  const patternsBox = el("div");
  const getPatterns = rows(patternsBox, d.patterns, [
    { key: "name", placeholder: "What it is, e.g. ID number" },
    { key: "regex", placeholder: "Regular expression (Python)", mono: true },
  ], "+ Add a format");
  const allowedBox = el("div");
  const getAllowed = rows(allowedBox, d.allowed_patterns, [
    { key: "name", placeholder: "What it is, e.g. ruling code" },
    { key: "regex", placeholder: "Regular expression (Python)", mono: true },
  ], "+ Add a reference");

  const msg = el("div");
  const save = el("button", { class: "primary", type: "button" }, fromTemplate ? "Create" : "Save and update Open WebUI");
  const refresh = el("button", { type: "button", disabled: fromTemplate || !(d.sources || []).length }, "Download sources");
  const del = el("button", { class: "danger", type: "button", hidden: fromTemplate }, "Delete");

  $("editor").replaceChildren(...[
    el("h2", {}, fromTemplate ? "New chat type" : d.name || t.id),
    el("div", { class: "status" }, status.map((s) => el("span", {}, s))),
    problems,
    field("id", "Id", fromTemplate ? "Lower case letters, digits and dashes, e.g. research-notes. It can't be changed later." : "Can't be changed.",
      text("id", t.id, { class: "mono", disabled: !fromTemplate })),
    field("name", "Name", "What people see in Open WebUI's model list.", text("name", d.name)),
    field("description", "Description", "One line under the name.", text("description", d.description)),
    field("system_prompt", "Instructions for the model", "How it should work in these chats. A note about the protection is added automatically.", area("system_prompt", d.system_prompt, 8)),
    field("allowed_sites", "Allowed websites", "One domain per line, e.g. example.org (its subdomains are included). Lookups can only search and read these.", area("allowed_sites", (d.allowed_sites || []).join("\n"), 3, { class: "mono" })),
    el("label", {}, "Who sees it"), el("div", { class: "hint" }, "Open WebUI accounts. Make an account in Open WebUI first (Admin Panel → Users) to see it here."), userBox,
    field("starter_prompts", "Starter prompts", "One per line: suggestions shown in a new chat.", area("starter_prompts", (d.starter_prompts || []).join("\n"), 3)),
    el("label", {}, "Sources for its Knowledge base"), el("div", { class: "hint" }, "Official pages downloaded as text with \"Download sources\". Never put case documents here: everyone using this chat type would search them."), sourcesBox,
    el("details", {},
      el("summary", {}, "Personal data rules"),
      el("div", { class: "hint" }, "Emails, phone numbers, addresses, long numbers, and names and numbers from the chat's own documents are always removed from lookups. Add formats specific to this kind of work here."),
      el("h3", {}, "Formats never sent"), patternsBox,
      el("h3", {}, "Public references that may be sent"),
      field("reference_words", "Words before a public number", "One per line: a number right after one of these words is a public reference and is kept, e.g. rule, section.", area("reference_words", (d.reference_words || []).join("\n"), 3)),
      el("div", { class: "hint" }, "Reference codes whose numbers would otherwise be removed, as regular expressions:"), allowedBox,
      field("allowed_terms", "Words that are never names", "One per line: capitalised words from the documents that are not personal (institutions, places).", area("allowed_terms", (d.allowed_terms || []).join("\n"), 3))),
    el("div", { class: "actions" }, save, refresh, del),
    msg,
  ].flat(3).filter((x) => x != null));

  const collect = () => ({
    name: f.name.value, description: f.description.value, system_prompt: f.system_prompt.value,
    allowed_sites: lines(f.allowed_sites.value), starter_prompts: lines(f.starter_prompts.value),
    allowed_terms: lines(f.allowed_terms.value), reference_words: lines(f.reference_words.value),
    users: [...userBox.querySelectorAll("input:checked:not(:disabled)")].map((i) => i.value),
    sources: getSources(), patterns: getPatterns(), allowed_patterns: getAllowed(),
  });
  const say = (ok, text, list = []) => msg.replaceChildren(el("div", { class: `msg ${ok ? "ok" : "bad"}` }, text, list.length ? el("ul", {}, list.map((p) => el("li", {}, p))) : null));

  save.onclick = async () => {
    const id = f.id.value.trim();
    save.disabled = true;
    const r = await api("/chat-types/save", { id, new: fromTemplate, data: collect() });
    save.disabled = false;
    if (!r.ok) return say(false, "Not saved:", r.data.problems || [r.data.error || `HTTP ${r.status}`]);
    say(true, "Saved. Updating Open WebUI…");
    isNew = false; current = id;
    await load(id);
    poll();
  };
  refresh.onclick = async () => {
    if (!confirm("Download this chat type's sources now? It can take several minutes; unchanged texts are left as they are.")) return;
    const r = await api("/chat-types/refresh", { id: t.id });
    if (!r.ok) return say(false, r.data.message || "Couldn't start.", r.data.problems || []);
    say(true, "Downloading the sources…");
    poll();
  };
  del.onclick = async () => {
    if (!confirm(`Delete "${d.name || t.id}"? Its model and Knowledge base are removed from Open WebUI; chats already made with it stay. The file is kept in chat-types/.deleted/.`)) return;
    const r = await api("/chat-types/delete", { id: t.id });
    if (!r.ok) return say(false, r.data.message || "Couldn't start.", r.data.problems || []);
    current = null;
    $("editor").replaceChildren(el("div", { class: "empty" }, "Deleting…"));
    poll();
  };
}

function showJob(j) {
  $("job").hidden = false;
  const verdict = j.running ? "running…" : j.exit === 0 ? "done" : `failed (exit ${j.exit})`;
  $("job-title").textContent = `Last job: ${j.kind} ${j.id}: ${verdict}${j.ended ? " · " + when(j.ended) : ""}`;
  $("job-out").textContent = j.output || "(no output yet)";
  $("job-out").scrollTop = $("job-out").scrollHeight;
}

function poll() {
  if (polling) return;
  polling = setInterval(async () => {
    const r = await api("/chat-types/job");
    if (!r.ok) return;
    showJob(r.data);
    if (!r.data.running) { clearInterval(polling); polling = null; await load(); }
  }, 1500);
}

$("new").onclick = () => open(null, true);
load();
