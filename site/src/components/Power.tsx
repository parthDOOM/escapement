import { useEffect, useRef, useState } from "react";
import { css, fit, useResize, useThemeTick } from "../lib/util";

// docs/15-POWER.md: the first v1.1 netlist, and version 3.2 as built (at 25 MHz)
const ROWS: [string, number, number][] = [["Sending UART", 8.61, 1.85], ["Logic analyser", 8.45, 1.54], ["Stopped", 7.99, 1.27]];
const MAX = 9;

export function Power() {
  return (
    <figure className="panel">
      <div className="bars">
        {ROWS.map(([n, a, b]) => (
          <div key={n} className="bar-pair">
            {[a, b].map((v, i) => (
              <div className="row" key={i}>
                <span>{i ? "" : n}</span>
                <div className="track"><div className={"fill " + (i ? "after" : "before")} style={{ width: (v / MAX * 100).toFixed(1) + "%" }} /></div>
                <span className="val">{v.toFixed(2)} mW</span>
              </div>
            ))}
          </div>
        ))}
      </div>
      <figcaption>Grey: the first netlist, version 1.1 before clock gating. Brass: version 3.2 as built.</figcaption>
    </figure>
  );
}

// Each tapeout candidate, from asic/reports/area.csv (the adopted runs only).
const VERSIONS = [
  { v: "v1.0", date: "11 Oct", cells: 28749, util: 55.6,
    what: "The first full chip: host link, four-context core, program memory." },
  { v: "v1.1", date: "15 Oct", cells: 23978, util: 55.2,
    what: "Adds the tape SRAM, capture and replay, and still ends 4,771 cells below v1.0: clock gating and a split hold margin. Metal1 carries signals." },
  { v: "v1.2", date: "19 Oct", cells: 25864, util: 58.9,
    what: "Pin bank B: five spare inputs and outputs become split pins, so four protocols run at once. Hold is repaired again after routing." },
  { v: "v1.3", date: "19 Oct", cells: 25931, util: 59.7,
    what: "Streaming capture: the host reads the tape during a run, so a capture lasts as long as the host keeps up, and PulseView records through the demo board." },
  { v: "v1.4", date: "21 Oct", cells: 27972, util: 58.2,
    what: "The line unit: USB and CAN on the pins, coded in hardware. Registers became latches, so the larger design takes less area than v1.3." },
  { v: "v1.5", date: "31 Oct", cells: 28961, util: 59.5,
    what: "Deadline tests decided a cycle early: the slow-corner limit rises from 28.6 to 38.6 MHz, so the chip is rated up to 33 MHz." },
  { v: "v1.6", date: "2 Nov", cells: 32239, util: 65.9,
    what: "Decode, operands and pin writes a stage earlier: signed off at 20 ns, so the chip is rated up to 50 MHz. 10BASE-T transmit runs inside the rating." },
  { v: "v3", date: "14 Nov", cells: 37230, util: 61.9,
    what: "Eight by four tiles, with what versions 1.7 and 2 built: pair mode and live reload, a 1,024-word store in four pages and proof tripwires. Version 3 adds line-unit receive, a half-tick input sampler and a bus timing monitor. Still signed off at 20 ns." },
  { v: "v3.1", date: "22 Nov", cells: 38514, util: 62.6,
    what: "Span: a program can run on into its idle partner's page. A reset synchroniser, and a layout that meets every electrical rule at every corner. Signed off at 20 ns; the slow corner passes down to 16.6 ns." },
  { v: "v3.2", date: "26 Nov", cells: 39588, util: 64.4,
    what: "Power: almost every register sees the clock only when it is written, and a waiting context reissues its held instruction instead of reading the program store. A stopped chip goes from 3.36 to 1.27 mW. Signed off at 20 ns; the slow corner passes down to 17.0 ns." },
];

/** Cells of each adopted layout on a labelled axis (the changes are a few thousand cells on
 *  24,000 to 39,600, which bars from zero hide), with each version's tag under its point. One
 *  card below tells the selected version's story: the latest by default, any other while its
 *  point or tag is hovered, focused or tapped, until another is. Every card is laid out in the same grid cell, so
 *  the card area is as tall as the longest story and does not move when the selection changes. */
export function History() {
  const ref = useRef<HTMLCanvasElement>(null);
  const LAST = VERSIONS.length - 1;
  const [sel, setSel] = useState(LAST);
  const theme = useThemeTick();
  const LO = 22000, HI = 40000, H = 180, PAD = 34;
  const n = VERSIONS.length;
  const X = (i: number, w: number) => PAD + ((i + 0.5) / n) * (w - PAD);
  const draw = () => {
    const c = ref.current;
    if (!c) return;
    const { g, w, h } = fit(c, H);
    g.clearRect(0, 0, w, h);
    const top = 28, bot = h - 10;
    const Y = (v: number) => bot - ((v - LO) / (HI - LO)) * (bot - top);
    const mono = css("--font-mono");
    g.font = "500 10px " + mono; g.strokeStyle = css("--line"); g.fillStyle = css("--fg-3");
    g.textAlign = "left"; g.textBaseline = "middle";
    for (let v = LO; v <= HI; v += 2000) {
      const y = Math.round(Y(v)) + 0.5;
      g.beginPath(); g.moveTo(PAD, y); g.lineTo(w, y); g.stroke();
      if ((v - LO) % 4000 === 0) g.fillText((v / 1000).toFixed(0) + "k", 0, y);
    }
    const brass = css("--brass");
    // the selected version: a guide from its point down to its tag
    const sx = Math.round(X(sel, w)) + 0.5, sy = Y(VERSIONS[sel].cells);
    g.strokeStyle = brass; g.globalAlpha = 0.45; g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(sx, sy); g.lineTo(sx, h); g.stroke();
    g.setLineDash([]); g.globalAlpha = 1;
    g.lineWidth = 2; g.beginPath();
    VERSIONS.forEach((x, i) => (i ? g.lineTo(X(i, w), Y(x.cells)) : g.moveTo(X(i, w), Y(x.cells))));
    g.stroke();
    VERSIONS.forEach((x, i) => {
      const on = i === sel;
      g.fillStyle = on ? brass : css("--bg"); g.strokeStyle = brass; g.lineWidth = 2;
      g.beginPath(); g.arc(X(i, w), Y(x.cells), on ? 6 : 4.5, 0, Math.PI * 2); g.fill(); g.stroke();
    });
    g.lineWidth = 1;
    // the selected point's cell count, kept inside the canvas at either end
    const label = VERSIONS[sel].cells.toLocaleString();
    g.font = "600 11px " + mono; g.fillStyle = css("--fg"); g.textBaseline = "bottom";
    const tw = g.measureText(label).width;
    g.textAlign = "left";
    g.fillText(label, Math.min(Math.max(sx - tw / 2, PAD), w - tw), sy - 10);
  };
  useEffect(draw, [sel, theme]);
  useResize(draw, [sel]);
  const pick = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.floor(((e.clientX - r.left - PAD) / (r.width - PAD)) * n);
    setSel(Math.min(n - 1, Math.max(0, i)));
  };
  return (
    <figure className="panel history">
      <div className="hist-plot">
        <canvas ref={ref} className="hist-chart" role="img" onPointerMove={pick} onPointerDown={pick}
          aria-label={"Standard cells per layout: " + VERSIONS.map((x) => `${x.v} ${x.cells.toLocaleString()}`).join(", ")} />
        <div className="hist-axis" style={{ "--n": n, paddingLeft: PAD } as React.CSSProperties}>
          {VERSIONS.map((x, i) => (
            <button key={x.v} type="button" className={"hist-tag" + (i === sel ? " on" : "")}
              aria-pressed={i === sel} onMouseEnter={() => setSel(i)} onFocus={() => setSel(i)}
              onClick={() => setSel(i)}>{x.v}</button>
          ))}
        </div>
      </div>
      <div className="hist-cards" aria-live="polite">
        {VERSIONS.map((x, i) => {
          const d = i ? x.cells - VERSIONS[i - 1].cells : 0;
          return (
            <div key={x.v} className={"hist-card" + (i === sel ? " on" : "")} aria-hidden={i !== sel}>
              <div className="hist-head">
                <b>{x.v}</b>
                <span className="dim small">{x.date}{i === LAST ? " · the layout going to silicon" : ""}</span>
              </div>
              <div className="hist-nums mono">
                <span>{x.cells.toLocaleString()} cells</span>
                {i > 0 && <span className={"delta " + (d < 0 ? "down" : "up")}>{d < 0 ? "−" : "+"}{Math.abs(d).toLocaleString()} on {VERSIONS[i - 1].v}</span>}
                <span className="dim">{x.util}% used</span>
              </div>
              <p>{x.what}</p>
            </div>
          );
        })}
      </div>
      <figcaption>Standard cells and utilisation of each adopted layout; the axis starts at 22,000 cells. Point
        at a version to read its story. Every 6 × 4 run, adopted or rejected, is logged with its reason in{" "}
        <code>asic/reports/area.csv</code>, and version 3.2's adopted layout is its last row.</figcaption>
    </figure>
  );
}
