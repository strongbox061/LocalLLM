"use strict";
const form = document.getElementById("form"), msg = document.getElementById("msg"), saveBtn = document.getElementById("save");
let fields = [];

function el(tag, attrs = {}, text = "") {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  return e;
}

function render(data) {
  document.getElementById("file").textContent = data.file;
  fields = data.fields;
  form.replaceChildren();
  for (const f of fields) {
    const box = el("div", { class: "field" + (f.changed ? " changed" : ""), "data-name": f.name.toLowerCase() + " " + f.description.toLowerCase() });
    box.append(el("label", { for: "f-" + f.name }, f.name), el("p", {}, f.description));
    let input;
    if (f.type === "bool") {
      input = el("input", { type: "checkbox", id: "f-" + f.name });
      input.checked = !!f.value;
    } else if (f.secret) {
      input = el("input", { type: "password", id: "f-" + f.name, autocomplete: "off",
                            placeholder: f.set ? "set: leave empty to keep it, or type a new one" : "not set" });
    } else {
      input = el("input", { type: f.type === "str" ? "text" : "number", id: "f-" + f.name });
      if (f.type === "float") input.step = "any";
      input.value = f.value ?? "";
    }
    input.addEventListener("input", () => { saveBtn.disabled = false; msg.textContent = ""; });
    box.append(input);
    const meta = el("div", { class: "meta" });
    if (f.secret) {
      meta.append(el("span", { class: f.set ? "set" : "unset" }, f.set ? "● key set" : "○ no key"));
      if (f.set) {
        const clear = el("label");
        const cb = el("input", { type: "checkbox", "data-clear": f.name });
        cb.addEventListener("change", () => { saveBtn.disabled = false; });
        clear.append(cb, document.createTextNode(" remove this key"));
        meta.append(clear);
      }
    } else if (f.type !== "bool") {
      meta.append(el("span", {}, "default: " + (f.default === "" ? "(empty)" : String(f.default))));
    }
    box.append(meta);
    form.append(box);
  }
  applyFilter();
}

function applyFilter() {
  const q = document.getElementById("filter").value.trim().toLowerCase();
  for (const box of form.children) box.style.display = !q || box.dataset.name.includes(q) ? "" : "none";
}

async function load() {
  const r = await fetch("/settings/data", { cache: "no-store" });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.status);
  render(data);
}

async function save() {
  const values = {}, clear = [];
  for (const f of fields) {
    const input = document.getElementById("f-" + f.name);
    if (f.type === "bool") values[f.name] = input.checked;
    else if (f.secret) { if (input.value !== "") values[f.name] = input.value; }
    else if (f.type === "int" || f.type === "float") values[f.name] = input.value === "" ? f.default : Number(input.value);
    else values[f.name] = input.value;
  }
  for (const cb of form.querySelectorAll("input[data-clear]")) if (cb.checked) clear.push(cb.dataset.clear);
  saveBtn.disabled = true;
  const r = await fetch("/settings/save", { method: "POST", headers: { "Content-Type": "application/json" },
                                             body: JSON.stringify({ values, clear }) });
  const data = await r.json().catch(() => ({}));
  if (r.ok) {
    msg.className = "ok";
    msg.textContent = data.changed && data.changed.length ? "Saved: " + data.changed.join(", ") : "Saved (nothing changed)";
    await load();
  } else {
    msg.className = "bad";
    msg.textContent = (data.problems || [data.error || ("HTTP " + r.status)]).join("\n");
    saveBtn.disabled = false;
  }
}

document.getElementById("filter").addEventListener("input", applyFilter);
saveBtn.addEventListener("click", () => save().catch(e => { msg.className = "bad"; msg.textContent = String(e); saveBtn.disabled = false; }));
load().catch(e => { msg.className = "bad"; msg.textContent = "Couldn't load the settings: " + e; });
