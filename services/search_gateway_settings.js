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
  form.innerHTML = "";
  fields = data.fields || [];
  const fileEl = document.getElementById("file");
  if (data.file) fileEl.textContent = data.file;

  for (const f of fields) {
    const div = el("div", { class: "field" });
    const label = el("label", null, f.label || f.key);
    const p = el("p", { class: f.value !== undefined && f.value !== "" ? "set" : "unset" },
      f.value !== undefined && f.value !== "" ? "● set" : "○ unset");
    if (f.type === "number") {
      const input = el("input", { type: "number", name: f.key, value: f.value ?? "", placeholder: f.placeholder || "" });
      div.append(label, p, input);
    } else if (f.type === "search" || f.searchable) {
      const input = el("input", { type: "text", name: f.key, value: f.value ?? "", placeholder: f.placeholder || "" });
      div.append(label, p, input);
    } else {
      const input = el("input", { type: "text", name: f.key, value: f.value ?? "", placeholder: f.placeholder || "" });
      div.append(label, p, input);
    }
    div.dataset.key = f.key;
    form.append(div);
  }

  saveBtn.disabled = true;
}

function applyFilter() {
  const q = document.getElementById("filter").value.toLowerCase();
  for (const f of form.querySelectorAll(".field")) {
    const key = f.dataset.key.toLowerCase();
    f.style.display = key.includes(q) ? "" : "none";
  }
}

async function load() {
  try {
    const res = await fetch("/settings/data");
    if (!res.ok) throw new Error(res.status);
    render(await res.json());
  } catch (e) {
    msg.textContent = "Failed to load settings: " + e.message;
    msg.className = "bad";
  }
}

async function save() {
  const data = {};
  for (const f of fields) {
    const input = form.querySelector(`input[name="${f.key}"]`);
    if (input) data[f.key] = input.value;
  }
  try {
    const res = await fetch("/settings/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    });
    if (!res.ok) throw new Error(res.status);
    msg.textContent = "Saved";
    msg.className = "ok";
  } catch (e) {
    msg.textContent = "Save failed: " + e.message;
    msg.className = "bad";
  }
}

form.addEventListener("input", () => {
  let changed = false;
  for (const f of form.querySelectorAll(".field")) {
    const input = f.querySelector("input");
    if (input && input.value !== "") {
      f.classList.add("changed");
      changed = true;
    } else {
      f.classList.remove("changed");
    }
  }
  saveBtn.disabled = !changed;
});

document.getElementById("filter").addEventListener("input", applyFilter);
saveBtn.addEventListener("click", save);

load();
