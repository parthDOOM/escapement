/* Escapement site: every figure on the page. No build step; three.js comes from the CDN. */
(function () {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const DPR = () => Math.min(window.devicePixelRatio || 1, 2);
  const redraws = [];
  const onTheme = (f) => redraws.push(f);

  function fit(canvas, h) {
    const w = canvas.clientWidth || canvas.parentElement.clientWidth;
    const d = DPR();
    if (h) canvas.style.height = h + "px";
    const H = h || canvas.clientHeight;
    canvas.width = Math.round(w * d);
    canvas.height = Math.round(H * d);
    const g = canvas.getContext("2d");
    g.setTransform(d, 0, 0, d, 0, 0);
    return { g, w, h: H };
  }

  async function json(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(url + ": " + r.status);
    return r.json();
  }

  function visible(el, cb) {
    if (!("IntersectionObserver" in window)) { cb(true); return; }
    new IntersectionObserver((es) => es.forEach((e) => cb(e.isIntersecting)), { rootMargin: "120px" }).observe(el);
  }

  /* ------------------------------------------------------------------ theme */
  function storedTheme() { try { return localStorage.getItem("esc-theme"); } catch (e) { return null; } }
  function setTheme(t) {
    if (t) document.documentElement.setAttribute("data-theme", t);
    else document.documentElement.removeAttribute("data-theme");
    try { if (t) localStorage.setItem("esc-theme", t); } catch (e) { /* private window */ }
    requestAnimationFrame(() => redraws.forEach((f) => f()));
  }
  const saved = storedTheme();
  if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);
  const isDark = () => {
    const t = document.documentElement.getAttribute("data-theme");
    return t ? t === "dark" : !matchMedia("(prefers-color-scheme: light)").matches;
  };
  const themeBtn = $("#theme");
  const label = () => { themeBtn.textContent = isDark() ? "light" : "dark"; };
  label();
  themeBtn.addEventListener("click", () => { setTheme(isDark() ? "light" : "dark"); label(); });
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => { label(); redraws.forEach((f) => f()); });

  /* ------------------------------------------------------------------ nav */
  const links = [...document.querySelectorAll(".bar nav a")];
  const secs = links.map((a) => $(a.getAttribute("href")));
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((es) => {
      es.forEach((e) => {
        if (!e.isIntersecting) return;
        links.forEach((a) => a.setAttribute("aria-current", String(a.getAttribute("href") === "#" + e.target.id)));
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    secs.forEach((s) => s && io.observe(s));
  }

  /* ------------------------------------------------------------------ rulers: 40 ns ticks */
  const rulers = [...document.querySelectorAll(".ruler canvas")];
  function drawRulers() {
    rulers.forEach((c, i) => {
      const { g, w } = fit(c, 34);
      g.clearRect(0, 0, w, 34);
      g.strokeStyle = css("--line");
      g.fillStyle = css("--fg-3");
      g.font = "500 10px " + css("--font-mono");
      const step = 6, base = (i + 1) * 1000;            // tick numbers continue down the page
      g.beginPath();
      for (let k = 0, x = 0.5; x < w; k++, x += step) {
        const t = base + k, major = t % 25 === 0;       // 25 ticks = 1 us
        g.moveTo(x, 34);
        g.lineTo(x, major ? 14 : (t % 5 === 0 ? 24 : 28));
        if (major && x + 40 < w) g.fillText((t * 0.04).toFixed(0) + " µs", x + 3, 11);
      }
      g.stroke();
    });
  }
  drawRulers();
  onTheme(drawRulers);
  addEventListener("resize", drawRulers);

  /* ------------------------------------------------------------------ deadline demo */
  (function deadline() {
    const c = $("#deadline"), out = $("#deadline-verdict");
    if (!c) return;
    c.tabIndex = 0;
    const T0 = 94, T1 = 132, COMMIT = 100, TAIL = 112;
    let d = 120;
    function x(t, w) { return 24 + (t - T0) / (T1 - T0) * (w - 48); }
    function draw() {
      const { g, w, h } = fit(c, 220);
      g.clearRect(0, 0, w, h);
      const fg3 = css("--fg-3"), line = css("--line"), brass = css("--brass"), ok = css("--ok"), bad = css("--bad"), fg = css("--fg");
      const mono = css("--font-mono");
      // tick axis
      g.strokeStyle = line; g.fillStyle = fg3; g.font = "500 10px " + mono; g.textAlign = "center";
      g.beginPath();
      for (let t = T0; t <= T1; t++) {
        const X = Math.round(x(t, w)) + 0.5;
        g.moveTo(X, 40); g.lineTo(X, t % 4 === 0 ? 30 : 35);
        if (t % 4 === 0) g.fillText(String(t), X, 22);
      }
      g.moveTo(24, 40.5); g.lineTo(w - 24, 40.5);
      g.stroke();
      // commit tick
      const xc = x(COMMIT, w);
      g.strokeStyle = fg3; g.setLineDash([3, 3]);
      g.beginPath(); g.moveTo(xc, 44); g.lineTo(xc, 168); g.stroke(); g.setLineDash([]);
      g.fillStyle = fg3; g.textAlign = "left"; g.fillText("EMIT commits", xc + 5, 58);
      // queued event
      const xt = x(TAIL, w);
      g.fillStyle = css("--bg-3"); g.strokeStyle = fg3;
      g.fillRect(xt - 6, 66, 12, 18); g.strokeRect(xt - 5.5, 66.5, 11, 17);
      g.fillStyle = fg3; g.fillText("queued", xt + 9, 79);
      // verdict
      let verdict, cls;
      if (d <= COMMIT) { verdict = "PAST"; cls = bad; }
      else if (d <= TAIL) { verdict = "ORDER"; cls = bad; }
      else { verdict = "ok"; cls = ok; }
      // new event marker
      const xd = x(d, w);
      g.strokeStyle = cls; g.lineWidth = 2;
      g.beginPath(); g.moveTo(xd, 92); g.lineTo(xd, 168); g.stroke(); g.lineWidth = 1;
      g.fillStyle = cls; g.beginPath(); g.arc(xd, 100, 7, 0, Math.PI * 2); g.fill();
      g.fillStyle = fg; g.fillText("deadline " + d, xd + 11, 104);
      // the pin
      const y0 = 196, y1 = 172;
      g.strokeStyle = css("--wave"); g.lineWidth = 2;
      g.beginPath();
      g.moveTo(24, y0); g.lineTo(xt, y0); g.lineTo(xt, y1);
      if (verdict === "ok") { g.lineTo(xd, y1); g.lineTo(xd, y0); g.lineTo(w - 24, y0); }
      else { g.lineTo(w - 24, y1); }
      g.stroke(); g.lineWidth = 1;
      g.fillStyle = fg3; g.textAlign = "left"; g.fillText("pin", 2, y0 - 8);
      if (verdict === "ok") {
        out.innerHTML = '<span class="pill ok">admitted</span><span>lead ' + (d - COMMIT) +
          " ticks</span><span>the edge lands at tick " + d + ", " + (d * 40).toLocaleString() + " ns, exactly</span>";
      } else {
        out.innerHTML = '<span class="pill bad">refused · ADMIT ' + verdict + "</span><span>" +
          (verdict === "PAST" ? "the deadline is not after the commit tick"
            : "the deadline is not after the event already queued") +
          "</span><span>the context faults and releases its pins; the others run on</span>";
      }
    }
    function setFrom(ev) {
      const r = c.getBoundingClientRect();
      const t = T0 + (ev.clientX - r.left - 24) / (r.width - 48) * (T1 - T0);
      d = Math.max(T0 + 1, Math.min(T1 - 1, Math.round(t)));
      draw();
    }
    let drag = false;
    c.addEventListener("pointerdown", (e) => { drag = true; c.setPointerCapture(e.pointerId); setFrom(e); });
    c.addEventListener("pointermove", (e) => { if (drag) setFrom(e); });
    c.addEventListener("pointerup", () => { drag = false; });
    c.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        d = Math.max(T0 + 1, Math.min(T1 - 1, d + (e.key === "ArrowLeft" ? -1 : 1)));
        draw(); e.preventDefault();
      }
    });
    draw();
    onTheme(draw);
    addEventListener("resize", draw);
  })();

  /* ------------------------------------------------------------------ architecture */
  (function arch() {
    const svg = $("#arch"), detail = $("#arch-detail");
    if (!svg) return;
    const NS = "http://www.w3.org/2000/svg";
    const B = {
      host: { x: 10, y: 40, w: 120, h: 56, t: "Host link", s: "SPI, 4 wires",
        d: "The host talks to the chip over SPI mode 0 on four pins. The link is oversampled in the core clock, so the whole chip has one clock domain.",
        f: [["Commands", "load and verify memories, configure, start, stop, mailboxes, debug reads"], ["Clock", "SCK up to f_clk / 8"], ["Also", "an interrupt line and a running flag"]] },
      hi: { x: 170, y: 40, w: 140, h: 56, t: "Host interface", s: "decoder, mailboxes",
        d: "Decodes commands and gives each context two 32-bit mailbox entries in each direction, so firmware and host can exchange data while running.",
        f: [["Mailboxes", "2 words each way, per context"], ["Debug reads", "registers, telemetry, checkers, faults, digest"]] },
      imem: { x: 350, y: 20, w: 150, h: 56, t: "Program memory", s: "SRAM 256 × 32",
        d: "One IHP single-port SRAM holds the firmware for all four contexts. It is read only for running contexts; on idle cycles it is deselected and spends no read energy.",
        f: [["Macro", "RM_IHPSG13_1P_256x32_c2_bm_bist"], ["Written", "by the host, only while stopped"]] },
      core: { x: 350, y: 112, w: 150, h: 70, t: "Barrel core", s: "4 contexts, fixed slots",
        d: "Four hardware contexts share one pipeline in fixed slots: context k fetches when t mod 4 = k and commits three ticks later. No stalls, no forwarding, no context can delay another.",
        f: [["Per context", "4 registers, shift register, loop counter, timer, PC, link, flags"], ["Issue rate", "one instruction every 4 ticks"]] },
      time: { x: 560, y: 20, w: 150, h: 56, t: "Tick counter", s: "32 bit, 40 ns",
        d: "One free-running counter is the only notion of time. It wraps after 171.8 s; deadlines may be up to half that range ahead.",
        f: [["Resolution", "40 ns at 25 MHz"], ["Horizon", "85.9 s"]] },
      fifo: { x: 560, y: 112, w: 150, h: 70, t: "Event queues", s: "4 entries per context",
        d: "DRIVE, EXPECT and SAMPLE events wait here with absolute deadlines. Admission checks each one when it is queued; retirement applies the head on its exact tick.",
        f: [["Refusals", "MASK, OWNER, PAST, ORDER, OVERLAP"], ["Proved", "no head is ever past its deadline"]] },
      pins: { x: 560, y: 222, w: 150, h: 70, t: "Pin banks", s: "A: 8 pins · B: 5 split",
        d: "Bank A is eight bidirectional pins, push-pull or open-drain. Bank B turns five spare dedicated inputs and outputs into split pins. Each context sees one bank as its eight logical pins.",
        f: [["Signals", "up to 18 protocol signals"], ["Open drain", "can never drive high, by construction"]] },
      sync: { x: 350, y: 222, w: 150, h: 70, t: "Input side", s: "synchronisers, checkers",
        d: "Inputs pass two-flop synchronisers. Checkers run EXPECT windows and count passes and failures; SAMPLE shifts bits into a per-context register.",
        f: [["Latency", "2 ticks from pad to logic"], ["Checker window", "up to 127 ticks"]] },
      cap: { x: 350, y: 330, w: 150, h: 56, t: "Capture unit", s: "timestamped edges",
        d: "Watches a pin bank and writes every change as {levels, tick} into a region of the tape. Overflow stops capture and records when; nothing is dropped silently.",
        f: [["Record", "8 levels + 24-bit tick"], ["Firmware fetches", "none needed while capturing"]] },
      tape: { x: 170, y: 330, w: 140, h: 56, t: "Tape", s: "SRAM 256 × 32",
        d: "A second SRAM shared by firmware loads and stores, the capture unit and the host. One memory serves as device storage, logic-analyser buffer and replay source.",
        f: [["Users", "firmware LDT/STT, capture, host"], ["Conflicts", "none: one context commits per cycle"]] },
      tel: { x: 560, y: 330, w: 150, h: 56, t: "Telemetry, digest", s: "self-measurement",
        d: "Each context records its minimum scheduling lead and maximum queue depth. A 32-bit digest folds every commit, register write and pin change into one word.",
        f: [["Checks", "the firmware's timing certificate"], ["Digest", "predicted exactly by the simulator"]] },
    };
    const W = [["host", "hi"], ["hi", "imem"], ["imem", "core"], ["core", "fifo"], ["time", "fifo"], ["fifo", "pins"],
      ["pins", "sync"], ["sync", "core"], ["sync", "cap"], ["cap", "tape"], ["core", "tape"], ["hi", "tape"], ["fifo", "tel"], ["tel", "hi"]];
    const ctr = (b) => [b.x + b.w / 2, b.y + b.h / 2];
    function edge(a, b) {
      const [ax, ay] = ctr(a), [bx, by] = ctr(b);
      const pa = clip(a, bx - ax, by - ay), pb = clip(b, ax - bx, ay - by);
      return "M" + pa.join(",") + " L" + pb.join(",");
    }
    function clip(b, dx, dy) {
      const [cx, cy] = ctr(b);
      const sx = dx ? (b.w / 2) / Math.abs(dx) : Infinity, sy = dy ? (b.h / 2) / Math.abs(dy) : Infinity;
      const s = Math.min(sx, sy);
      return [cx + dx * s, cy + dy * s];
    }
    const wires = {};
    W.forEach(([a, b]) => {
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", edge(B[a], B[b]));
      p.setAttribute("class", "wire");
      svg.appendChild(p);
      (wires[a] = wires[a] || []).push(p);
      (wires[b] = wires[b] || []).push(p);
    });
    const els = {};
    Object.entries(B).forEach(([k, b]) => {
      const gEl = document.createElementNS(NS, "g");
      gEl.setAttribute("class", "blk");
      gEl.setAttribute("tabindex", "0");
      gEl.setAttribute("role", "button");
      gEl.setAttribute("aria-pressed", "false");
      gEl.setAttribute("aria-label", b.t);
      const r = document.createElementNS(NS, "rect");
      Object.entries({ x: b.x, y: b.y, width: b.w, height: b.h, rx: 7 }).forEach(([a, v]) => r.setAttribute(a, v));
      const t1 = document.createElementNS(NS, "text");
      t1.setAttribute("x", b.x + 12); t1.setAttribute("y", b.y + 24); t1.textContent = b.t;
      const t2 = document.createElementNS(NS, "text");
      t2.setAttribute("x", b.x + 12); t2.setAttribute("y", b.y + 42); t2.setAttribute("class", "sub"); t2.textContent = b.s;
      gEl.append(r, t1, t2);
      svg.appendChild(gEl);
      els[k] = gEl;
      const pick = () => select(k);
      gEl.addEventListener("click", pick);
      gEl.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    });
    function select(k) {
      Object.entries(els).forEach(([kk, el]) => el.setAttribute("aria-pressed", String(kk === k)));
      document.querySelectorAll("#arch .wire").forEach((p) => p.classList.remove("hot"));
      (wires[k] || []).forEach((p) => p.classList.add("hot"));
      const b = B[k];
      detail.innerHTML = "<h3></h3><div class=\"prose\"><p></p></div><dl></dl>";
      detail.querySelector("h3").textContent = b.t;
      detail.querySelector("p").textContent = b.d;
      const dl = detail.querySelector("dl");
      b.f.forEach(([a, v]) => {
        const dt = document.createElement("dt"), dd = document.createElement("dd");
        dt.textContent = a; dd.textContent = v; dl.append(dt, dd);
      });
    }
    select("fifo");
  })();

  /* ------------------------------------------------------------------ waves + barrel */
  const CTX = ["--m1", "--m3", "--m4", "--brass"];
  json("assets/waves.json").then((data) => { waves(data); barrel(data); }).catch((e) => console.error(e));

  function niceStep(span) {
    const raw = span / 8, p = Math.pow(10, Math.floor(Math.log10(raw)));
    return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw);
  }

  function waves(data) {
    const tabs = $("#wave-tabs"), c = $("#wave"), meta = $("#wave-meta"), foot = $("#wave-foot");
    let cur = data.protocols[0], hoverT = null;
    data.protocols.forEach((p, i) => {
      const b = document.createElement("button");
      b.className = "tab"; b.type = "button"; b.setAttribute("role", "tab");
      b.textContent = p.title; b.id = "tab-" + p.id;
      b.setAttribute("aria-selected", String(i === 0));
      b.addEventListener("click", () => {
        tabs.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", "false"));
        b.setAttribute("aria-selected", "true");
        cur = p; hoverT = null; show();
      });
      tabs.appendChild(b);
    });
    function show() {
      const p = cur;
      meta.innerHTML = "";
      const h = document.createElement("h3"); h.textContent = p.title;
      const f = document.createElement("span"); f.className = "mono";
      f.textContent = "firmware/" + p.firmware + " · " + p.words + " of 256 words";
      const pills = document.createElement("span");
      pills.style.display = "flex"; pills.style.gap = "0.4rem"; pills.style.flexWrap = "wrap";
      p.cert.forEach((ct) => {
        const s = document.createElement("span");
        s.className = "pill " + (ct.status === "CHECKED" ? "ok" : "bad");
        s.textContent = "ctx " + ct.ctx + " " + ct.status + (ct.min_lead != null ? " · lead ≥ " + ct.min_lead : "");
        s.title = "Static timing certificate: every event of this context is queued at least this many ticks before its deadline, on every path";
        pills.appendChild(s);
      });
      meta.append(h, f, pills);
      foot.innerHTML = "<p></p><p class=\"result\"></p>";
      foot.children[0].textContent = p.caption;
      foot.children[1].textContent = p.result;
      draw();
    }
    function draw() {
      const p = cur, rows = p.signals.length, RH = 38, TOP = 26, LEFT = 92;
      const width = Math.max(c.parentElement.clientWidth, 560);
      c.style.width = width + "px";
      const { g, w, h } = fit(c, TOP + rows * RH + 8);
      g.clearRect(0, 0, w, h);
      const sx = (t) => LEFT + (t / p.span) * (w - LEFT - 10);
      const mono = css("--font-mono"), fg3 = css("--fg-3"), line = css("--line"), fg2 = css("--fg-2");
      // time axis in microseconds
      const us = p.span * data.tick_ns / 1000, step = niceStep(us);
      g.font = "500 10px " + mono; g.fillStyle = fg3; g.strokeStyle = line; g.textAlign = "center";
      g.beginPath();
      for (let u = 0; u <= us + 1e-9; u += step) {
        const X = Math.round(sx(u * 1000 / data.tick_ns)) + 0.5;
        g.moveTo(X, TOP - 6); g.lineTo(X, h - 4);
        g.fillText((step < 1 ? u.toFixed(1) : u.toFixed(0)) + " µs", X, 12);
      }
      g.stroke();
      p.signals.forEach((s, i) => {
        const yHi = TOP + i * RH + 8, yLo = yHi + RH - 18;
        g.fillStyle = fg2; g.textAlign = "right"; g.font = "600 11px " + mono;
        g.fillText(s.name, LEFT - 12, yLo - 2);
        g.fillStyle = fg3; g.font = "500 9px " + mono;
        g.fillText(s.pin, LEFT - 12, yLo + 9);
        g.strokeStyle = css("--wave"); g.lineWidth = 1.6;
        g.beginPath();
        let v = s.w.length ? s.w[0][1] : 1;
        g.moveTo(sx(0), v ? yHi : yLo);
        for (let k = 1; k < s.w.length; k++) {
          const X = sx(s.w[k][0]);
          g.lineTo(X, v ? yHi : yLo);
          v = s.w[k][1];
          g.lineTo(X, v ? yHi : yLo);
        }
        g.lineTo(sx(p.span), v ? yHi : yLo);
        g.stroke(); g.lineWidth = 1;
      });
      if (hoverT != null) {
        const X = Math.round(sx(hoverT)) + 0.5;
        g.strokeStyle = css("--brass"); g.beginPath(); g.moveTo(X, TOP - 6); g.lineTo(X, h - 4); g.stroke();
        const lab = "tick " + hoverT + " · " + (hoverT * data.tick_ns / 1000).toFixed(2) + " µs";
        g.font = "600 10px " + mono; const tw = g.measureText(lab).width + 10;
        const lx = Math.min(X + 6, w - tw - 2);
        g.fillStyle = css("--bg-3"); g.fillRect(lx, TOP - 4, tw, 16);
        g.fillStyle = css("--fg"); g.textAlign = "left"; g.fillText(lab, lx + 5, TOP + 8);
      }
      c._sx = { LEFT, w };
    }
    c.addEventListener("pointermove", (e) => {
      const r = c.getBoundingClientRect(), { LEFT, w } = c._sx || { LEFT: 92, w: r.width };
      const x = e.clientX - r.left;
      if (x < LEFT) { hoverT = null; draw(); return; }
      hoverT = Math.max(0, Math.min(cur.span, Math.round((x - LEFT) / (w - LEFT - 10) * cur.span)));
      draw();
    });
    c.addEventListener("pointerleave", () => { hoverT = null; draw(); });
    show();
    onTheme(draw);
    addEventListener("resize", draw);
  }

  function barrel(data) {
    const c = $("#barrel"), out = $("#barrel-readout"), btn = $("#barrel-play");
    const N = 160, rows = 4, commits = data.barrel;
    const byT = new Map(commits.map((x) => [x[0], x]));
    let play = null, at = null;
    const OUT = ["completes", "waits", "faults"];
    function draw() {
      const width = Math.max(c.parentElement.clientWidth, 880);
      c.style.width = width + "px";
      const { g, w, h } = fit(c, 230);
      const L = 58, T = 26, cw = (w - L - 8) / N, rh = (h - T - 30) / rows;
      g.clearRect(0, 0, w, h);
      const mono = css("--font-mono"), fg3 = css("--fg-3"), line = css("--line");
      g.font = "500 10px " + mono; g.textAlign = "center"; g.fillStyle = fg3; g.strokeStyle = line;
      g.beginPath();
      for (let t = 0; t <= N; t += 4) {
        const X = Math.round(L + t * cw) + 0.5;
        g.moveTo(X, T - 4); g.lineTo(X, T + rows * rh);
        if (t % 20 === 0) g.fillText(String(t), X, 14);
      }
      g.stroke();
      for (let k = 0; k < rows; k++) {
        g.fillStyle = fg3; g.textAlign = "right"; g.fillText("ctx " + k, L - 10, T + k * rh + rh / 2 + 4);
      }
      commits.forEach(([t, k, , o]) => {
        if (t >= N || (at != null && t > at)) return;
        const col = css(CTX[k]);
        const X = L + t * cw, Y = T + k * rh + 5;
        if (o === 0) { g.fillStyle = col; g.fillRect(X + 0.5, Y, Math.max(cw - 1, 1.5), rh - 10); }
        else { g.strokeStyle = col; g.strokeRect(X + 1, Y + 0.5, Math.max(cw - 2, 1), rh - 11); }
      });
      if (at != null) {
        const X = Math.round(L + (at + 0.5) * cw) + 0.5;
        g.strokeStyle = css("--fg"); g.beginPath(); g.moveTo(X, T - 6); g.lineTo(X, T + rows * rh + 2); g.stroke();
      }
      g.fillStyle = fg3; g.textAlign = "left";
      g.fillText("tick (commit)", L, h - 8);
      c._geo = { L, cw };
    }
    function read(t) {
      const x = byT.get(t);
      out.textContent = x ? "tick " + t + " · ctx " + x[1] + " · " + x[2] + " · " + OUT[x[3]]
        : "tick " + t + " · no commit";
    }
    c.addEventListener("pointermove", (e) => {
      if (play) return;
      const r = c.getBoundingClientRect(), { L, cw } = c._geo;
      const t = Math.floor((e.clientX - r.left - L) / cw);
      if (t >= 0 && t < N) read(t);
    });
    btn.addEventListener("click", () => {
      if (play) { clearInterval(play); play = null; btn.textContent = "play"; return; }
      btn.textContent = "pause"; at = at == null || at >= N - 1 ? 0 : at;
      play = setInterval(() => {
        at++; read(at); draw();
        if (at >= N - 1) { clearInterval(play); play = null; btn.textContent = "replay"; }
      }, reduced ? 0 : 70);
    });
    read(commits[0][0]);
    draw();
    onTheme(draw);
    addEventListener("resize", draw);
  }

  /* ------------------------------------------------------------------ proof cards */
  (function proofs() {
    const P = [
      ["P2", "Never late", "No event queue head is ever past its deadline, in any reachable state, including the 32-bit counter wrap.", "unbounded, assume-guarantee"],
      ["P6", "Admitted means ahead", "Every admitted event is strictly after its commit tick and after the last event already queued.", "unbounded, PDR"],
      ["P7", "Fixed slots", "A commit always belongs to the context that owns that slot, so no context can steal another's time.", "unbounded, PDR"],
      ["P1", "Open drain stays open", "An open-drain pin is never driven high, whatever the firmware does.", "unbounded, PDR"],
      ["P3 · P5", "Pins stay owned", "A pin no context owns is never driven, and a faulted context drives none of its pins, in either bank.", "unbounded, PDR"],
      ["P4 · H1–H4", "Queues stay sane", "Queue occupancy stays in bounds; queued deadlines are ahead of now and strictly increasing.", "unbounded, PDR and k-induction"],
      ["C1–C3", "Capture stays in bounds", "The capture unit never writes outside its region, never stages more than two records, and records only during a run.", "unbounded, PDR"],
      ["G1–G3", "Clock gates are safe", "A gated register bank equals an enable flip-flop at every instant, and its clock never glitches.", "unbounded, k-induction"],
      ["S1", "Configuration is frozen", "Enabled contexts, entry points, pin ownership and pin banks never change while the chip runs.", "unbounded, on the host interface"],
      ["≡", "Netlist equals RTL", "The routed netlist is sequentially equivalent to the RTL for every input sequence after reset.", "ABC dprove, 3 minutes"],
    ];
    const box = $("#proofs");
    P.forEach(([id, t, d, how]) => {
      const el = document.createElement("div");
      el.className = "card";
      el.innerHTML = "<b></b><h3></h3><p></p><span class=\"how\"></span>";
      el.children[0].textContent = id; el.children[1].textContent = t;
      el.children[2].textContent = d; el.children[3].textContent = how;
      box.appendChild(el);
    });
  })();

  /* ------------------------------------------------------------------ power */
  (function power() {
    const rows = [["Sending UART", 8.61, 3.01], ["Logic analyser", 8.45, 2.67], ["Stopped", 7.99, 2.30]];
    const box = $("#power"), max = 9;
    rows.forEach(([n, a, b]) => {
      ["before", "after"].forEach((k, i) => {
        const v = i ? b : a;
        const r = document.createElement("div");
        r.className = "row";
        r.innerHTML = "<span></span><div class=\"track\"><div class=\"fill " + k + "\"></div></div><span class=\"val\"></span>";
        r.children[0].textContent = i ? "" : n;
        r.querySelector(".fill").style.width = (v / max * 100).toFixed(1) + "%";
        r.children[2].textContent = v.toFixed(2) + " mW";
        box.appendChild(r);
      });
    });
  })();

  /* ------------------------------------------------------------------ zoomable die */
  json("assets/die/die.json").then((info) => { zoom(info); die3d(info); }).catch((e) => {
    console.error(e);
    const f = $("#die3d-fallback"); if (f) f.hidden = false;
  });

  function macroName(m, info) {
    return m.box[1] > info.die_um[1] / 2 ? "Tape · SRAM 256 × 32" : "Program memory · SRAM 256 × 32";
  }

  function zoom(info) {
    const box = $("#zoom"), c = box.querySelector("canvas"), scaleEl = $("#zoom-scale");
    const [W, H] = info.die_um, levels = info.levels, cache = new Map();
    let s = 1, ox = 0, oy = 0, labels = true, fitS = 1;     // s: css px per um; o: um at canvas left/top
    function tile(z, x, y) {
      const k = z + "/" + x + "/" + y;
      let im = cache.get(k);
      if (!im) {
        im = new Image();
        im.onload = () => schedule();
        im.src = "assets/die/die_" + z + "_" + x + "_" + y + ".jpg";
        cache.set(k, im);
      }
      return im;
    }
    let raf = 0;
    const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); };
    function reset() {
      const w = c.clientWidth, h = c.clientHeight;
      fitS = Math.min(w / W, h / H); s = fitS;
      ox = -(w / s - W) / 2; oy = -(h / s - H) / 2;
    }
    function drawLevel(g, L, w, h) {
      const T = info.tile, upx = W / L.w;              // um per level px
      for (let ty = 0; ty < L.ny; ty++) for (let tx = 0; tx < L.nx; tx++) {
        const x0 = tx * T * upx, y0 = ty * T * upx;
        const tw = Math.min(T, L.w - tx * T) * upx, th = Math.min(T, L.h - ty * T) * upx;
        const X = (x0 - ox) * s, Y = (y0 - oy) * s;
        if (X > w || Y > h || X + tw * s < 0 || Y + th * s < 0) continue;
        const im = tile(L.z, tx, ty);
        if (im.complete && im.naturalWidth) g.drawImage(im, X, Y, tw * s + 0.6, th * s + 0.6);
      }
    }
    function draw() {
      const { g, w, h } = fit(c);
      g.fillStyle = "#0a0c14"; g.fillRect(0, 0, w, h);
      const need = s * DPR() * W;                       // device px across the die
      let best = levels[levels.length - 1];
      for (const L of levels) { if (L.w >= need) { best = L; break; } }
      drawLevel(g, levels[0], w, h);                    // always-present backdrop
      if (best !== levels[0]) {
        const prev = levels[Math.max(0, best.z - 1)];
        if (prev !== levels[0]) drawLevel(g, prev, w, h);
        drawLevel(g, best, w, h);
      }
      if (labels) {
        g.font = "600 12px " + css("--font-body"); g.textBaseline = "top";
        const lab = (x0, y0, x1, y1, t) => {
          const X = (x0 - ox) * s, Y = (H - y1 - oy) * s, Wd = (x1 - x0) * s, Hd = (y1 - y0) * s;
          g.strokeStyle = "rgba(240,198,110,0.9)"; g.setLineDash([5, 4]); g.lineWidth = 1.2;
          g.strokeRect(X, Y, Wd, Hd); g.setLineDash([]);
          const tw = g.measureText(t).width + 10;
          g.fillStyle = "rgba(10,12,20,0.78)"; g.fillRect(X + 6, Y + 6, tw, 19);
          g.fillStyle = "#f0c66e"; g.fillText(t, X + 11, Y + 10);
        };
        info.macros.forEach((m) => lab(m.box[0], m.box[1], m.box[2], m.box[3], macroName(m, info)));
        lab(30, H - 12, 191, H, "Tiny Tapeout pins");
      }
      // scale bar
      const um = [1, 2, 5, 10, 20, 50, 100, 200, 500].find((u) => u * s >= 70) || 500;
      scaleEl.textContent = "▏" + "—".repeat(3) + "▕ " + um + " µm";
    }
    function zoomAt(f, px, py) {
      const ns = Math.max(fitS * 0.9, Math.min(s * f, (levels[levels.length - 1].w / W) * 3));
      ox += px / s - px / ns; oy += py / s - py / ns; s = ns; schedule();
    }
    const pts = new Map();
    let pinch = null;
    c.addEventListener("pointerdown", (e) => { c.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]); });
    c.addEventListener("pointermove", (e) => {
      if (!pts.has(e.pointerId)) return;
      const [px, py] = pts.get(e.pointerId);
      pts.set(e.pointerId, [e.clientX, e.clientY]);
      if (pts.size === 2) {
        const [a, b] = [...pts.values()], r = c.getBoundingClientRect();
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch) zoomAt(d / pinch, (a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top);
        pinch = d; return;
      }
      ox -= (e.clientX - px) / s; oy -= (e.clientY - py) / s; schedule();
    });
    const up = (e) => { pts.delete(e.pointerId); if (pts.size < 2) pinch = null; };
    c.addEventListener("pointerup", up); c.addEventListener("pointercancel", up);
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    c.addEventListener("dblclick", (e) => { const r = c.getBoundingClientRect(); zoomAt(2, e.clientX - r.left, e.clientY - r.top); });
    $("#zoom-reset").addEventListener("click", () => { reset(); schedule(); });
    const lb = $("#zoom-labels");
    lb.addEventListener("click", () => { labels = !labels; lb.setAttribute("aria-pressed", String(labels)); schedule(); });
    reset(); draw();
    addEventListener("resize", () => { reset(); schedule(); });
  }

  /* ------------------------------------------------------------------ 3D die */
  function webgl() {
    if (!window.THREE) return false;
    try { const cv = document.createElement("canvas"); return !!(cv.getContext("webgl2") || cv.getContext("webgl")); }
    catch (e) { return false; }
  }

  function die3d(info) {
    const stage = $("#die3d");
    if (!webgl()) { $("#die3d-fallback").hidden = false; $("#die3d-hint").hidden = true; return; }
    const T = THREE, [W, H] = info.die_um, U = 0.01;     // 1 unit = 100 um
    const renderer = new T.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(DPR());
    stage.prepend(renderer.domElement);
    const scene = new T.Scene();
    const cam = new T.PerspectiveCamera(32, 1.6, 0.1, 200);
    cam.up.set(0, 0, 1);
    cam.position.set(1.5, -15.5, 10.5);
    const ctl = new T.OrbitControls(cam, renderer.domElement);
    ctl.enableDamping = true; ctl.target.set(0, 0, 0.6);
    ctl.minDistance = 4; ctl.maxDistance = 40; ctl.enablePan = false;
    ctl.autoRotate = !reduced; ctl.autoRotateSpeed = 0.35;
    ctl.addEventListener("start", () => { ctl.autoRotate = false; $("#die3d-hint").hidden = true; });
    const base = new T.Mesh(new T.BoxGeometry(W * U, H * U, 0.12), new T.MeshBasicMaterial({ color: 0x141a28 }));
    base.position.z = -0.065; scene.add(base);
    const edges = new T.LineSegments(new T.EdgesGeometry(base.geometry), new T.LineBasicMaterial({ color: 0x3a4560 }));
    edges.position.copy(base.position); scene.add(edges);
    const loader = new T.TextureLoader(), planes = [];
    info.layers.forEach((L, i) => {
      const tex = loader.load("assets/die/layer_" + L.key + ".png", () => need());
      tex.anisotropy = 4;
      const m = new T.MeshBasicMaterial({ color: new T.Color(L.color), alphaMap: tex, transparent: true,
        depthWrite: false, side: T.DoubleSide, opacity: i < 2 ? 0.75 : 0.9 });
      const p = new T.Mesh(new T.PlaneGeometry(W * U, H * U), m);
      p.renderOrder = i; scene.add(p); planes.push({ p, L, i });
    });
    const slider = $("#explode");
    function place() {
      const k = slider.value / 100;
      // heights 40x true, so the 6 um stack shows against a 1289 um die; the slider adds spacing
      planes.forEach(({ p, L, i }) => { p.position.z = L.z * 40 * U + k * i * 0.55 + 0.01 * i; });
      need();
    }
    slider.addEventListener("input", place);
    const leg = $("#die3d-legend");
    planes.forEach(({ p, L }) => {
      const b = document.createElement("button");
      b.type = "button"; b.setAttribute("aria-pressed", "true");
      b.innerHTML = "<i></i>"; b.append(L.label);
      b.querySelector("i").style.background = L.color;
      b.addEventListener("click", () => { p.visible = !p.visible; b.setAttribute("aria-pressed", String(p.visible)); need(); });
      leg.appendChild(b);
    });
    let on = false, frames = 0;
    const need = () => { frames = 90; if (on) loop(); };
    let looping = false;
    function loop() {
      if (looping) return;
      looping = true;
      const step = () => {
        const moving = ctl.update();
        renderer.render(scene, cam);
        if (on && (ctl.autoRotate || moving || frames-- > 0)) requestAnimationFrame(step);
        else looping = false;
      };
      requestAnimationFrame(step);
    }
    function size() {
      const w = stage.clientWidth, h = stage.clientHeight;
      renderer.setSize(w, h, false);
      cam.aspect = w / h; cam.updateProjectionMatrix(); need();
    }
    ctl.addEventListener("change", need);
    visible(stage, (v) => { on = v; if (v) loop(); });
    addEventListener("resize", size);
    size(); place();
  }

  /* ------------------------------------------------------------------ 3D slice */
  const CELLS = [["dfrbp", "D flip-flop with reset"], ["dfrbpq", "D flip-flop with reset"], ["dlhq", "latch"], ["dllr", "latch"],
    ["sdfbbp", "scan flip-flop"], ["nand", "NAND gate"], ["nor", "NOR gate"], ["xnor", "XNOR gate"], ["xor", "XOR gate"],
    ["and", "AND gate"], ["or", "OR gate"], ["inv", "inverter"], ["buf", "buffer"], ["dlygate", "hold delay cell"],
    ["mux", "multiplexer"], ["a21oi", "AND-OR-invert gate"], ["a221oi", "AND-OR-invert gate"], ["a22oi", "AND-OR-invert gate"],
    ["o21ai", "OR-AND-invert gate"], ["tie", "tie cell"], ["antenna", "antenna diode"], ["ebuf", "tristate buffer"]];
  const cellDesc = (n) => {
    const base = n.replace(/_\d+$/, "");
    const hit = CELLS.filter(([k]) => base.startsWith(k)).sort((a, b) => b[0].length - a[0].length)[0];
    return (hit ? hit[1] : "standard cell") + " (" + n + ")";
  };

  json("assets/slice.json").then(slice3d).catch((e) => console.error(e));

  function slice3d(S) {
    const stage = $("#slice3d");
    if (!webgl()) { stage.querySelector(".hint").textContent = "3D view needs WebGL"; return; }
    const T = THREE, [SW, SH] = S.size_um, cx = SW / 2, cy = SH / 2;
    const renderer = new T.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(DPR());
    stage.prepend(renderer.domElement);
    const scene = new T.Scene();
    scene.add(new T.HemisphereLight(0xdfe6ff, 0x1a1f2c, 0.85));
    const sun = new T.DirectionalLight(0xffffff, 0.9); sun.position.set(-20, -30, 40); scene.add(sun);
    const cam = new T.PerspectiveCamera(35, 1.6, 0.1, 500);
    cam.up.set(0, 0, 1); cam.position.set(-4, -38, 26);
    const ctl = new T.OrbitControls(cam, renderer.domElement);
    ctl.enableDamping = true; ctl.target.set(0, 0, 1.5); ctl.minDistance = 8; ctl.maxDistance = 120;
    const COL = { activ: css("--activ"), poly: css("--poly"), metal1: css("--m1"), metal2: css("--m2"), metal3: css("--m3"),
      metal4: css("--m4"), topmetal1: css("--tm1") };
    const GROUP = { activ: "Transistors", poly: "Transistors", cont: "Contacts and vias", via1: "Contacts and vias",
      via2: "Contacts and vias", via3: "Contacts and vias", topvia1: "Contacts and vias", metal1: "Metal1", metal2: "Metal2",
      metal3: "Metal3", metal4: "Metal4", topmetal1: "TopMetal1" };
    const groups = {};
    const sub = new T.Mesh(new T.BoxGeometry(SW, SH, 0.6), new T.MeshStandardMaterial({ color: 0x3a3f4f, roughness: 0.9 }));
    sub.position.set(0, 0, -0.3); scene.add(sub);
    S.layers.forEach((L) => {
      const geos = [];
      L.polys.forEach((P) => {
        const sh = new T.Shape(P.o.map(([x, y]) => new T.Vector2(x - cx, y - cy)));
        P.h.forEach((hole) => sh.holes.push(new T.Path(hole.map(([x, y]) => new T.Vector2(x - cx, y - cy)))));
        const g = new T.ExtrudeGeometry(sh, { depth: L.t, bevelEnabled: false, curveSegments: 1 });
        g.translate(0, 0, L.z);
        geos.push(g);
      });
      if (!geos.length) return;
      const merged = T.BufferGeometryUtils.mergeBufferGeometries(geos);
      geos.forEach((g) => g.dispose());
      const col = COL[L.key] || "#b9bccb";
      const m = new T.MeshStandardMaterial({ color: new T.Color(col), roughness: L.key.startsWith("metal") ? 0.35 : 0.6,
        metalness: L.key.startsWith("metal") || L.key.startsWith("top") ? 0.55 : 0.1 });
      const mesh = new T.Mesh(merged, m);
      scene.add(mesh);
      const gname = GROUP[L.key] || L.key;
      (groups[gname] = groups[gname] || { meshes: [], color: L.key.includes("via") || L.key === "cont" ? "#b9bccb" : col }).meshes.push(mesh);
    });
    // cell outlines on the substrate, for hover
    const outline = new T.LineBasicMaterial({ color: 0x6b7590, transparent: true, opacity: 0.6 });
    const hot = new T.LineBasicMaterial({ color: 0xf0c66e });
    const rects = S.cells.map((cl) => {
      const [x0, y0, x1, y1] = cl.box.map((v, i) => v - (i % 2 ? cy : cx));
      const g = new T.BufferGeometry().setFromPoints([new T.Vector3(x0, y0, 0.02), new T.Vector3(x1, y0, 0.02),
        new T.Vector3(x1, y1, 0.02), new T.Vector3(x0, y1, 0.02), new T.Vector3(x0, y0, 0.02)]);
      const ln = new T.Line(g, outline); scene.add(ln);
      return { cl, ln, x0, y0, x1, y1 };
    });
    const leg = $("#slice-legend");
    Object.entries(groups).forEach(([n, gr]) => {
      const b = document.createElement("button");
      b.type = "button"; b.setAttribute("aria-pressed", "true");
      b.innerHTML = "<i></i>"; b.append(n);
      b.querySelector("i").style.background = gr.color;
      b.addEventListener("click", () => {
        const v = !gr.meshes[0].visible;
        gr.meshes.forEach((m) => { m.visible = v; });
        b.setAttribute("aria-pressed", String(v)); need();
      });
      leg.appendChild(b);
    });
    const cap = $("#slice-caption"), capText = cap.textContent;
    const ray = new T.Raycaster(), plane = new T.Plane(new T.Vector3(0, 0, 1), 0), hit = new T.Vector3();
    let cur = null;
    renderer.domElement.addEventListener("pointermove", (e) => {
      const r = renderer.domElement.getBoundingClientRect();
      ray.setFromCamera({ x: ((e.clientX - r.left) / r.width) * 2 - 1, y: -((e.clientY - r.top) / r.height) * 2 + 1 }, cam);
      let found = null;
      if (ray.ray.intersectPlane(plane, hit)) found = rects.find((q) => hit.x >= q.x0 && hit.x <= q.x1 && hit.y >= q.y0 && hit.y <= q.y1) || null;
      if (found !== cur) {
        if (cur) cur.ln.material = outline;
        cur = found;
        if (cur) { cur.ln.material = hot; cap.textContent = cellDesc(cur.cl.cell) + ", " + (cur.x1 - cur.x0).toFixed(2) + " µm wide"; }
        else cap.textContent = capText;
        need();
      }
    });
    let on = false, frames = 0, looping = false;
    const need = () => { frames = 60; if (on) loop(); };
    function loop() {
      if (looping) return;
      looping = true;
      const step = () => {
        const moving = ctl.update();
        renderer.render(scene, cam);
        if (on && (moving || frames-- > 0)) requestAnimationFrame(step); else looping = false;
      };
      requestAnimationFrame(step);
    }
    function size() {
      const w = stage.clientWidth, h = stage.clientHeight;
      renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); need();
    }
    ctl.addEventListener("change", need);
    visible(stage, (v) => { on = v; if (v) need(); });
    addEventListener("resize", size);
    size();
  }
})();
