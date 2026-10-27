// Drives the real big.js bigScreen() (with app.js) through a minimal DOM and a fake
// WebSocket, and prints what the big screen shows after each pushed view as JSON lines.
// Usage: node big_screen.js <app.js> <big.js> <scenario.json>
"use strict";
const fs = require("fs");
const vm = require("vm");
const { makeDom } = require("./fake_dom");

const [appPath, bigPath, scenarioPath] = process.argv.slice(2);
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));

const { els, ctx, state } = makeDom();
ctx.document.getElementById("clip").hidden = true; // as in big.html
ctx.fetch = () => new Promise(() => {}); // the initial GET never answers here
vm.createContext(ctx);
const code = fs.readFileSync(appPath, "utf8") + "\n" + fs.readFileSync(bigPath, "utf8");
vm.runInContext(code + "\nbigScreen();", ctx);

const shown = () => ({
  phase: els.phase.textContent,
  video_shown: !els.clip.hidden,
  sim_shown: !els.frame.hidden,
  src: els.clip.src,
  played: els.clip.played,
  paused: els.clip.paused,
  time: els.clip.currentTime,
});

(async () => {
  for (const view of scenario) {
    els.clip.currentTime = 3; // playback advanced since the last view
    state.socket.onmessage({ data: JSON.stringify(view) });
    await new Promise((r) => setImmediate(r));
    console.log(JSON.stringify(shown()));
  }
})();
