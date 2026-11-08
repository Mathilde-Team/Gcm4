// SPDX-License-Identifier: GPL-3.0-or-later
"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("pont local, clavier, Unicode, options, recherche et acquittement", () => {
  const messages = [];
  let terminal;
  let observer;
  let fits = 0;
  const searches = [];
  const document = { body: { style: {} }, getElementById: () => ({}) };
  class Terminal {
    constructor(options) {
      terminal = this;
      this.options = options;
      this.cols = options.cols;
      this.rows = options.rows;
      this.buffer = { active: {
        length: 2,
        getLine: row => ({ translateToString: trim => {
          assert.equal(trim, true);
          return ["alpha", "beta"][row];
        } }),
      } };
    }
    loadAddon(addon) { assert.ok(addon); }
    open(element) { assert.ok(element); }
    attachCustomKeyEventHandler(callback) { this.key = callback; }
    onData(callback) { this.data = callback; }
    onBinary(callback) { this.binary = callback; }
    onResize(callback) { this.resize = callback; }
    write(text, callback) { this.written = text; callback(); }
    focus() { this.focused = true; }
    paste(text) { this.pasted = text; }
    getSelection() { return "sélection"; }
  }
  const window = { webkit: { messageHandlers: { terminal: {
    postMessage: value => messages.push(JSON.parse(value)),
  } } } };
  const context = {
    Terminal, document, window,
    FitAddon: { FitAddon: class { fit() { fits++; } } },
    SearchAddon: { SearchAddon: class {
      findNext(text) { searches.push(["next", text]); return true; }
      findPrevious(text) { searches.push(["previous", text]); return false; }
    } },
    ResizeObserver: class {
      constructor(callback) { observer = callback; }
      observe(element) { assert.ok(element); }
    },
  };
  const script = path.resolve(__dirname, "../../src/gcm4/ui/xterm_assets/terminal.js");
  vm.runInNewContext(fs.readFileSync(script, "utf8"), context, { filename: script });
  const api = window.gcmTerminal;
  assert.deepEqual(messages.pop(), { type: "ready" });
  assert.equal(terminal.options.allowProposedApi, false);
  assert.equal(terminal.options.screenReaderMode, true);
  assert.ok(Object.isFrozen(api));
  observer();
  assert.equal(fits, 1);
  api.write("<script>ni HTML ni code exécuté</script>");
  assert.equal(terminal.written, "<script>ni HTML ni code exécuté</script>");
  assert.deepEqual(messages.pop(), { type: "ack" });
  terminal.resize({ cols: 101, rows: 37 });
  assert.deepEqual(messages.pop(), { type: "resize", value: [101, 37] });
  terminal.binary("\x80\xff");
  assert.deepEqual(messages.pop(), { type: "binary", value: "\x80\xff" });
  terminal.data("");
  assert.equal(messages.length, 0);
  const long = "α😃\x1b".repeat(20000);
  terminal.data(long);
  assert.ok(messages.length > 1);
  assert.equal(messages.map(m => m.value).join(""), long);
  assert.ok(messages.every(m => m.type === "input" && JSON.stringify(m).length <= 131072));
  messages.length = 0;
  const event = key => ({
    key, type: "keydown", ctrlKey: false, shiftKey: false,
    altKey: false, metaKey: false, isTrusted: true,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
  });
  assert.equal(terminal.key({ ...event("x"), type: "keyup" }), true);
  assert.equal(terminal.key(event("x")), true);
  assert.equal(terminal.key({ ...event("Backspace"), altKey: true }), true);
  assert.equal(terminal.key({ ...event("Delete"), metaKey: true }), true);
  assert.equal(terminal.key({ ...event("C"), ctrlKey: true }), true);
  for (const [key, type] of [["c", "copy"], ["V", "paste"], ["f", "search"]]) {
    const trusted = { ...event(key), ctrlKey: true, shiftKey: true };
    assert.equal(terminal.key(trusted), false);
    assert.equal(trusted.defaultPrevented, true, "pas de seconde action native WebKit");
    assert.deepEqual(messages.pop(), { type });
    const synthetic = { ...event(key), ctrlKey: true, shiftKey: true, isTrusted: false };
    assert.equal(terminal.key(synthetic), false);
    assert.equal(synthetic.defaultPrevented, true);
    assert.equal(messages.length, 0);
  }
  for (let n = 0; n < 5; n++) {
    api.configure({ backspace: n, delete: n, theme: { background: "#123456" } });
    for (const key of ["Backspace", "Delete"]) {
      assert.equal(terminal.key(event(key)), false);
      const message = messages.pop();
      assert.equal(message.type, n === 4 ? "erase" : "input");
      assert.equal(message.value, n === 4 ? null :
        ["", "\x08", "\x7f", "\x1b[3~"][n] || (key === "Backspace" ? "\x7f" : "\x1b[3~"));
    }
  }
  assert.equal(document.body.style.background, "#123456");
  api.focus();
  assert.equal(terminal.focused, true);
  api.paste("texte\r\n");
  assert.equal(terminal.pasted, "texte\r\n");
  assert.equal(api.selection(), "sélection");
  assert.equal(api.search("alpha", false), true);
  assert.equal(api.search("alpha", true), false);
  assert.deepEqual(searches, [["next", "alpha"], ["previous", "alpha"]]);
  assert.equal(api.text(), "alpha\nbeta");
  terminal.buffer.active.length = 0;
  assert.equal(api.text(), "");
  assert.deepEqual(Array.from(api.size()), [80, 24]);
});
