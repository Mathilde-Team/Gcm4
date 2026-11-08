// SPDX-License-Identifier: GPL-3.0-or-later
"use strict";
// Ressources figées locales. Le contenu du PTY ne passe jamais par innerHTML.
(() => {
  const post = (type, value) =>
    window.webkit.messageHandlers.terminal.postMessage(JSON.stringify({ type, value }));
  const term = new Terminal({
    cols: 80, rows: 24, fontFamily: "monospace", fontSize: 14,
    scrollback: 10000, screenReaderMode: true, allowProposedApi: false,
    cursorBlink: false, theme: { background: "#000000", foreground: "#ffffff" },
  });
  const fit = new FitAddon.FitAddon();
  const search = new SearchAddon.SearchAddon();
  term.loadAddon(fit);
  term.loadAddon(search);
  term.open(document.getElementById("terminal"));
  let backspace = 0;
  let del = 0;
  const binding = (n, fallback) => ["", "\x08", "\x7f", "\x1b[3~", "\x7f"][n] || fallback;
  term.attachCustomKeyEventHandler(event => {
    if (event.type !== "keydown") return true;
    if (event.ctrlKey && event.shiftKey && ["C", "V", "F"].includes(event.key.toUpperCase())) {
      // Seul un vrai geste clavier demande un accès au presse-papiers natif.
      if (event.isTrusted) post({ C: "copy", V: "paste", F: "search" }[event.key.toUpperCase()]);
      return false;
    }
    if (!event.altKey && !event.ctrlKey && !event.metaKey) {
      if (event.key === "Backspace") {
        post(backspace === 4 ? "erase" : "input", backspace === 4 ? null : binding(backspace, "\x7f"));
        return false;
      }
      if (event.key === "Delete") {
        post(del === 4 ? "erase" : "input", del === 4 ? null : binding(del, "\x1b[3~"));
        return false;
      }
    }
    return true;
  });
  term.onData(text => {
    // Ne coupe jamais une paire UTF-16 ; conserve aussi les collages longs.
    const points = Array.from(text);
    for (let start = 0; start < points.length; start += 16384) {
      post("input", points.slice(start, start + 16384).join(""));
    }
  });
  // Les réponses binaires de souris sont des octets, pas du texte à transcoder.
  term.onBinary(text => post("binary", text));
  term.onResize(size => post("resize", [size.cols, size.rows]));
  new ResizeObserver(() => fit.fit()).observe(document.getElementById("terminal"));
  window.gcmTerminal = Object.freeze({
    write: text => term.write(text, () => post("ack")),
    configure: options => {
      backspace = options.backspace;
      del = options.delete;
      term.options.theme = options.theme;
      document.body.style.background = options.theme.background;
      fit.fit();
    },
    focus: () => term.focus(),
    paste: text => term.paste(text),
    selection: () => term.getSelection(),
    search: (text, previous) => previous ? search.findPrevious(text) : search.findNext(text),
    text: () => {
      const buffer = term.buffer.active;
      const lines = [];
      for (let row = 0; row < buffer.length; row++) lines.push(buffer.getLine(row).translateToString(true));
      return lines.join("\n");
    },
    size: () => [term.cols, term.rows],
  });
  post("ready");
})();
