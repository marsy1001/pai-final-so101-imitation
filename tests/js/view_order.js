// Drives the real app.js approvalScreen() with a minimal DOM and controllable fetch and
// WebSocket, and prints what the page shows after each step as JSON lines.
// Usage: node view_order.js <app.js> <scenario.json>
"use strict";
const fs = require("fs");
const vm = require("vm");

const [appPath, scenarioPath] = process.argv.slice(2);
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));

function el(id) {
  return {
    id, textContent: "", hidden: false, disabled: false, dataset: {}, style: {},
    children: [], onclick: null, attrs: {},
    replaceChildren() { this.children = []; },
    append(...c) { this.children.push(...c); },
    setAttribute(k, v) { this.attrs[k] = v; },
  };
}
const els = {};
const document = {
  getElementById: (id) => (els[id] ||= el(id)),
  createElement: () => el(""),
  createTextNode: (t) => ({ text: t }),
};
const pending = [];
function fetch(url, opts) {
  return new Promise((resolve) => pending.push({ url, opts, resolve }));
}
let socket = null;
class WebSocket { constructor(url) { this.url = url; socket = this; } }
const ctx = { document, fetch, WebSocket, setTimeout: () => 0, JSON, Math, console, location: { host: "h" } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(appPath, "utf8") + "\napprovalScreen();", ctx);

const respond = (i, view) => {
  const p = pending.splice(i, 1)[0];
  p.resolve({ ok: true, json: async () => view });
};
const shown = () => ({
  phase: els.phase.textContent,
  read_enabled: !els.read.disabled,
  approve_enabled: !els.approve.disabled,
});

(async () => {
  for (const step of scenario) {
    if (step.do === "get") respond(0, step.view);             // the initial GET
    if (step.do === "click") els[step.id].onclick();          // a POST is now pending
    if (step.do === "push") socket.onmessage({ data: JSON.stringify(step.view) });
    if (step.do === "answer") respond(0, step.view);          // the oldest pending POST
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
    console.log(JSON.stringify({ step: step.do, ...shown() }));
  }
})();
