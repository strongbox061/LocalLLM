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
  form.replaceChildren();
  fields = data.fields || [];
  const fileEl = document.getElementById("file");
  if (data.file) fileEl.textContent = data.file;

  for (const f of fields) {
    const div = el("div", { class: "field changed", "data-name": (f.label || f.key).toLowerCase() + " " + (f.placeholder || "").toLowerCase() });
    const input = el("input", { type: f.type === "number" ? "number" : "text", name: f.key, value: f.value ?? "", placeholder: f.placeholder || "" });
    input.addEventListener("input", () => {
      div.classList.toggle("changed", input.value !== "");
      saveBtn.disabled = form.querySelectorAll(".field.changed").length === 0;
    });
    div.append(el("label", null, f.label || f.key), input);
    form.append(div);
  }

  saveBtn.disabled = true;
}

function applyFilter() {
  const q = document.getElementById("filter").value.trim().toLowerCase();
  for (const box of form.children) box.style.display = !q || box.dataset.name.includes(q) ? "" : "none";
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
  for (const box of form.children) {
    const input = box.querySelector("input");
    if (input && input.value !== "") changed = true;
  }
  saveBtn.disabled = !changed;
});

document.getElementById("filter").addEventListener("input", applyFilter);
saveBtn.addEventListener("click", save);

load();
