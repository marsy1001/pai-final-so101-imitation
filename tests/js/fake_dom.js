// The minimal DOM, fetch and WebSocket the page drivers run the real app.js / big.js
// against. Shared by view_order.js and big_screen.js so both fakes stay the same.
"use strict";

function el(id) {
  return {
    id, textContent: "", hidden: false, disabled: false, dataset: {}, style: {}, src: "",
    children: [], onclick: null, attrs: {}, played: 0, paused: 0, currentTime: 0,
    replaceChildren() { this.children = []; },
    append(...c) { this.children.push(...c); },
    setAttribute(k, v) { this.attrs[k] = v; },
    play() { this.played += 1; return Promise.resolve(); },
    pause() { this.paused += 1; },
  };
}

function makeDom() {
  const els = {};
  const document = {
    getElementById: (id) => (els[id] ||= el(id)),
    createElement: () => el(""),
    createTextNode: (t) => ({ text: t }),
  };
  const state = { socket: null };
  class WebSocket { constructor(url) { this.url = url; state.socket = this; } }
  class Image { set src(v) { this._src = v; } get src() { return this._src; } }
  const ctx = {
    document, WebSocket, Image, setTimeout: () => 0, Date, JSON, Math, console,
    encodeURIComponent, location: { host: "h" },
  };
  return { els, ctx, state };
}

module.exports = { el, makeDom };
