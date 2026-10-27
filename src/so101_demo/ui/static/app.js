// Both screens follow the view pushed on /ws. Only labels and colours are shown.
"use strict";

function chip(el, item) {
  el.replaceChildren();
  if (!item) return;
  const sw = document.createElement("span");
  sw.className = "swatch";
  sw.style.background = item.css;
  el.append(sw, document.createTextNode(item.text)); // colour + registry label
}

// GET, POST answers and the socket race: a view older than the one on screen is never
// drawn (the version only grows). The same version is drawn again: the waiting seconds
// change without a new version.
// A restarted server counts from 0 again: its `boot` differs, so the count starts over.
let applied = -1;
let boot = null;
function follow(show) {
  const render = (v) => {
    if (v.boot !== boot) {
      boot = v.boot;
      applied = -1;
    }
    if (v.version < applied) return;
    applied = v.version;
    session = v.session;
    show(v);
  };
  draw = render;
  let socket;
  const connect = () => {
    socket = new WebSocket(`ws://${location.host}/ws`);
    socket.onmessage = (ev) => render(JSON.parse(ev.data));
    socket.onclose = () => setTimeout(connect, 1000);
  };
  fetch("/api/view").then((r) => r.json()).then(render);
  connect();
}

function common(v) {
  const phase = document.getElementById("phase");
  phase.textContent = v.phase;
  phase.dataset.key = v.phase_key;
  const shown = v.target && v.destination && (v.phase_key === "result" || v.phase_key === "running");
  document.getElementById("proposal").hidden = !shown;
  chip(document.getElementById("target"), v.target);
  chip(document.getElementById("destination"), v.destination);
  document.getElementById("message").textContent = v.message || "";
}

// Every operation names the session on screen, so a stale page cannot act on a new run.
// The answer is the new view; it is drawn at once, the socket then keeps it current.
let draw = () => {};
let session = null;
async function post(op, body) {
  const res = await fetch(`/api/${op}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session, ...(body || {}) }),
  });
  document.getElementById("error").textContent = res.ok ? "" : "今は操作できません";
  if (res.ok) draw(await res.json());
}

function approvalScreen() {
  const ops = ["read", "approve", "correct", "reject", "manual", "prerecorded", "idle"];
  let view = null;
  let choice = { target: null, destination: null };
  let mode = null; // "correct" or "choose" while the picker is open
  let pickerSession = null; // the session the picker was opened for

  const picker = document.getElementById("picker");
  const confirm = document.getElementById("confirm");

  function fillPicker(v) {
    const make = (box, items, key) => {
      box.replaceChildren();
      for (const item of items) {
        const b = document.createElement("button");
        chip(b, item);
        b.setAttribute("aria-pressed", String(choice[key] === item.id));
        b.onclick = () => { choice[key] = item.id; fillPicker(view); };
        box.append(b);
      }
    };
    make(document.getElementById("pick-target"), v.objects, "target");
    make(document.getElementById("pick-destination"), v.destinations, "destination");
    confirm.disabled = !(choice.target && choice.destination);
  }

  function openPicker(m) {
    mode = m;
    pickerSession = session;
    choice = { target: null, destination: null };
    picker.hidden = false;
    fillPicker(view);
  }

  for (const op of ops) {
    document.getElementById(op).onclick = () => (op === "correct" ? openPicker("correct") : post(op));
  }
  confirm.onclick = async () => {
    await post(mode, { ...choice, session: pickerSession });
    picker.hidden = true;
    mode = null;
  };

  follow((v) => {
    view = v;
    common(v);
    const r = document.getElementById("reading");
    r.textContent = v.reading ? `読み取った内容: ${v.reading}` : "";
    const w = document.getElementById("waiting");
    w.textContent = v.waiting_s == null ? "" : `${Math.floor(v.waiting_s)} 秒 / 上限 ${Math.round(v.wait_limit_s)} 秒`;
    document.getElementById("last").textContent = v.last_result ? `前回: ${v.last_result}` : "";
    for (const op of ops) document.getElementById(op).disabled = !v.actions.includes(op);
    if (v.phase_key === "manual" && mode !== "choose") openPicker("choose");
    if (v.phase_key !== "manual" && mode === "choose") { picker.hidden = true; mode = null; }
    if (mode === "correct" && !v.actions.includes("correct")) { picker.hidden = true; mode = null; }
  });
}

function bigScreen() {
  const img = document.getElementById("frame");
  const refresh = () => {
    const next = new Image();
    next.onload = () => { img.src = next.src; setTimeout(refresh, 40); };
    next.onerror = () => setTimeout(refresh, 1000);
    next.src = `/frame.jpg?t=${Date.now()}`;
  };
  refresh();
  follow(common);
}
