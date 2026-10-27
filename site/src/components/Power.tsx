import { useEffect, useRef, useState } from "react";
import { css, fit, useResize, useThemeTick } from "../lib/util";

// docs/15-POWER.md: the first v1.1 netlist, and v1.4 as built
const ROWS: [string, number, number][] = [["Sending UART", 8.61, 3.13], ["Logic analyser", 8.45, 2.82], ["Stopped", 7.99, 2.48]];
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
      <figcaption>Grey: the first design. Brass: the design as built.</figcaption>
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
];

/** Cells of each adopted layout on a labelled axis (the changes are a few thousand cells
 *  on about 26,000, which bars from zero hide), with the five versions in matching columns
 *  below; on a phone the columns become a vertical timeline. */
export function History() {
  const ref = useRef<HTMLCanvasElement>(null);
  const [sel, setSel] = useState(VERSIONS.length - 1);
  const theme = useThemeTick();
  const LO = 22000, HI = 30000;
  const draw = () => {
    const c = ref.current;
    if (!c) return;
    const { g, w, h } = fit(c, 170);
    g.clearRect(0, 0, w, h);
    const n = VERSIONS.length, top = 26, bot = h - 18;
    // on a phone the columns stack, so the points move clear of the axis labels
    const pad = w < 520 ? 30 : 0;
    const X = (i: number) => pad + ((i + 0.5) / n) * (w - pad);
    const Y = (v: number) => bot - ((v - LO) / (HI - LO)) * (bot - top);
    const mono = css("--font-mono");
    g.font = "500 10px " + mono; g.strokeStyle = css("--line"); g.fillStyle = css("--fg-3");
    g.textAlign = "left"; g.textBaseline = "bottom";
    for (let v = LO; v <= HI; v += 2000) {
      const y = Math.round(Y(v)) + 0.5;
      g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke();
      g.fillText((v / 1000).toFixed(0) + "k", 2, y - 2);
    }
    const brass = css("--brass");
    g.strokeStyle = brass; g.lineWidth = 2; g.beginPath();
    VERSIONS.forEach((x, i) => (i ? g.lineTo(X(i), Y(x.cells)) : g.moveTo(X(i), Y(x.cells))));
    g.stroke(); g.lineWidth = 1;
    g.textAlign = "center"; g.textBaseline = "bottom";
    VERSIONS.forEach((x, i) => {
      const on = i === sel;
      g.fillStyle = on ? brass : css("--bg");
      g.strokeStyle = brass; g.lineWidth = 2;
      g.beginPath(); g.arc(X(i), Y(x.cells), on ? 6 : 4.5, 0, Math.PI * 2); g.fill(); g.stroke();
      g.lineWidth = 1;
      g.fillStyle = on ? css("--fg") : css("--fg-2");
      g.font = (on ? "600 " : "500 ") + "11px " + mono;
      g.fillText(w < 520 ? (x.cells / 1000).toFixed(1) + "k" : x.cells.toLocaleString(), X(i), Y(x.cells) - 10);
    });
  };
  useEffect(draw, [sel, theme]);
  useResize(draw, [sel]);
  return (
    <figure className="panel history">
      <canvas ref={ref} className="hist-chart" role="img"
        aria-label={"Standard cells per layout: " + VERSIONS.map((x) => `${x.v} ${x.cells.toLocaleString()}`).join(", ")} />
      <ol className="hist">
        {VERSIONS.map((x, i) => {
          const d = i ? x.cells - VERSIONS[i - 1].cells : 0;
          return (
            <li key={x.v} className={"hist-col" + (i === sel ? " on" : "")} onMouseEnter={() => setSel(i)}
              onFocus={() => setSel(i)} tabIndex={0}>
              <div className="hist-head"><b>{x.v}</b><span className="dim small">{x.date}</span></div>
              <div className="hist-nums mono">
                <span>{x.cells.toLocaleString()} cells</span>
                {i > 0 && <span className={"delta " + (d < 0 ? "down" : "up")}>{d < 0 ? "−" : "+"}{Math.abs(d).toLocaleString()}</span>}
                <span className="dim">{x.util}% used</span>
              </div>
              <p className="small">{x.what}</p>
            </li>
          );
        })}
      </ol>
      <figcaption>Standard cells and utilisation of each adopted layout; the axis starts at 22,000 cells. Every
        run, adopted or rejected, is logged with its reason in <code>asic/reports/area.csv</code>.</figcaption>
    </figure>
  );
}
