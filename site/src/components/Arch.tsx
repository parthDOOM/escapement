import { useState } from "react";

// 5 columns x 3 rows; every wire runs in the channels between blocks
const BW = 160, BH = 60, C = [10, 220, 430, 640, 850], R = [20, 165, 310];
const KIND: Record<string, [string, string]> = {
  host: ["--m2", "host side"], mem: ["--m1", "memory"], time: ["--brass", "time and core"], io: ["--m4", "pins and inputs"],
};
type Block = { c: number; r: number; k: string; t: string; s: string; d: string; f: [string, string][] };
const B: Record<string, Block> = {
  host: { c: 0, r: 1, k: "host", t: "Host link", s: "SPI, 4 wires",
    d: "The host talks to the chip over SPI mode 0 on four pins. The link is oversampled in the core clock, so the whole chip has one clock domain.",
    f: [["Commands", "load and verify memories, configure, start, stop, mailboxes, debug reads"], ["Clock", "SCK up to f_clk / 8"], ["Also", "an interrupt line and a running flag"]] },
  hi: { c: 1, r: 1, k: "host", t: "Host interface", s: "commands, mailboxes",
    d: "Decodes commands and gives each context two 32-bit mailbox entries in each direction, so firmware and host exchange data while the chip runs. Every debug register, the telemetry and the digest are read here.",
    f: [["Mailboxes", "2 words each way, per context"], ["Debug reads", "registers, telemetry, checkers, faults, digest"]] },
  imem: { c: 2, r: 0, k: "mem", t: "Program memory", s: "SRAM 256 × 32",
    d: "One IHP single-port SRAM holds the firmware for all four contexts. It is read only for running contexts; on idle cycles it is deselected and spends no read energy.",
    f: [["Macro", "RM_IHPSG13_1P_256x32_c2_bm_bist"], ["Written", "by the host, only while stopped"]] },
  core: { c: 2, r: 1, k: "time", t: "Barrel core", s: "4 fixed slots",
    d: "Four hardware contexts share one pipeline in fixed slots: context k fetches when t mod 4 = k and commits three ticks later. No stalls, no forwarding, and no context can delay another.",
    f: [["Per context", "4 registers, shift register, loop counter, timer, PC, link, flags"], ["Issue rate", "one instruction every 4 ticks"]] },
  tape: { c: 2, r: 2, k: "mem", t: "Tape", s: "SRAM 256 × 32",
    d: "A second SRAM shared by firmware loads and stores, the capture unit and the host. One memory serves as device storage, logic-analyser buffer and replay source. The host reads it during a run, in the cycles the core leaves free, so a capture can stream out for as long as the host keeps up.",
    f: [["Users", "firmware LDT / STT, capture, host"], ["Priority", "the core first; the host never delays an instruction"]] },
  time: { c: 3, r: 0, k: "time", t: "Tick counter", s: "32 bit, 40 ns",
    d: "One free-running counter is the only notion of time on the chip. It wraps after 171.8 s; deadlines may be up to half that range ahead.",
    f: [["Resolution", "40 ns at 25 MHz"], ["Horizon", "85.9 s"]] },
  fifo: { c: 3, r: 1, k: "time", t: "Event queues", s: "4 per context",
    d: "DRIVE, EXPECT and SAMPLE events wait here with absolute deadlines. Admission checks each one when it is queued; retirement applies the head on its exact tick.",
    f: [["Refusals", "MASK, OWNER, PAST, ORDER, OVERLAP"], ["Proved", "no head is ever past its deadline"]] },
  tel: { c: 4, r: 0, k: "time", t: "Telemetry", s: "lead, depth, digest",
    d: "Each context records its minimum scheduling lead and maximum queue depth. A 32-bit digest folds every commit, register write and pin change into one word that the simulator predicts exactly.",
    f: [["Checks", "the firmware's timing certificate"], ["Read by", "the host, at any time"]] },
  pins: { c: 4, r: 1, k: "io", t: "Pin banks", s: "A 8 pins, B 5 split",
    d: "Bank A is eight bidirectional pins, push-pull or open-drain. Bank B turns five spare dedicated inputs and outputs into split pins. Each context sees one bank as its eight logical pins.",
    f: [["Signals", "up to 18: 8 bidirectional, 5 out and 5 in"], ["Open drain", "can never drive high, by construction"]] },
  sync: { c: 4, r: 2, k: "io", t: "Input side", s: "sync, checkers",
    d: "Inputs pass two-flop synchronisers. Checkers run EXPECT windows and count passes and failures; SAMPLE shifts bits into a per-context register.",
    f: [["Latency", "2 ticks from pad to logic"], ["Checker window", "up to 127 ticks"]] },
  cap: { c: 3, r: 2, k: "io", t: "Capture unit", s: "timestamped edges",
    d: "Watches a pin bank and writes every change as {levels, tick} into a region of the tape. Overflow stops capture and records when; nothing is dropped silently.",
    f: [["Record", "8 levels + 24-bit tick"], ["Firmware fetches", "none needed while capturing"]] },
};
// [from, to, points, label, label x, label y, both ways]
const W: [string, string, number[][], string, number, number, boolean?][] = [
  ["host", "hi", [[170, 195], [220, 195]], "SPI", 195, 186, true],
  ["hi", "imem", [[380, 180], [405, 180], [405, 50], [430, 50]], "load", 411, 116],
  ["hi", "core", [[380, 195], [430, 195]], "", 0, 0, true],
  ["hi", "tape", [[380, 210], [405, 210], [405, 340], [430, 340]], "", 0, 0, true],
  ["imem", "core", [[510, 80], [510, 165]], "fetch", 516, 126],
  ["core", "fifo", [[590, 190], [640, 190]], "push", 615, 182],
  ["time", "fifo", [[720, 80], [720, 165]], "now", 726, 126],
  ["fifo", "tel", [[800, 180], [825, 180], [825, 50], [850, 50]], "lead", 831, 116],
  ["fifo", "pins", [[800, 205], [850, 205]], "drive", 825, 222],
  ["pins", "sync", [[930, 225], [930, 310]], "pads", 936, 270],
  ["sync", "cap", [[850, 350], [800, 350]], "edges", 825, 366],
  ["cap", "tape", [[640, 340], [590, 340]], "records", 615, 332],
  ["core", "tape", [[510, 225], [510, 310]], "LDT / STT", 516, 270, true],
  ["sync", "core", [[850, 325], [825, 325], [825, 255], [615, 255], [615, 215], [590, 215]], "inputs", 720, 249],
];
const MID = new Set(["SPI", "push", "drive", "edges", "records", "inputs"]);

export default function Arch() {
  const [sel, setSel] = useState("fifo");
  const b = B[sel];
  return (
    <div className="arch">
      <div className="arch-svg panel">
        <svg viewBox="0 0 1020 390" role="group" aria-label="Block diagram">
          <defs>
            <marker id="ah" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path className="ahead" d="M0,0 L8,4 L0,8 z" />
            </marker>
          </defs>
          {W.map(([a, z, pts, lab, lx, ly, both], i) => {
            const hot = a === sel || z === sel;
            return (
              <g key={i}>
                <polyline points={pts.map((q) => q.join(",")).join(" ")} className={"wire" + (hot ? " hot" : "")}
                  markerEnd="url(#ah)" markerStart={both ? "url(#ah)" : undefined} />
                {lab && (
                  <text x={lx} y={ly} className={"wlab" + (hot ? " hot" : "")} textAnchor={MID.has(lab) ? "middle" : undefined}>
                    {lab}
                  </text>
                )}
              </g>
            );
          })}
          {Object.entries(B).map(([k, v]) => {
            const x = C[v.c], y = R[v.r];
            return (
              <g key={k} className="blk" tabIndex={0} role="button" aria-pressed={k === sel} aria-label={v.t}
                onClick={() => setSel(k)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSel(k); } }}>
                <rect x={x} y={y} width={BW} height={BH} rx={8} className="box" />
                <circle cx={x + BW - 14} cy={y + 14} r={4} style={{ fill: `var(${KIND[v.k][0]})` }} />
                <text x={x + 14} y={y + 26}>{v.t}</text>
                <text x={x + 14} y={y + 45} className="sub">{v.s}</text>
              </g>
            );
          })}
        </svg>
        <div className="arch-key">
          {Object.values(KIND).map(([v, n]) => (
            <span key={n}><i style={{ background: `var(${v})` }} />{n}</span>
          ))}
        </div>
      </div>
      <div className="arch-detail panel" aria-live="polite">
        <h3>{b.t}</h3>
        <div className="prose"><p>{b.d}</p></div>
        <dl>
          {b.f.map(([a, v]) => (
            <div key={a} style={{ display: "contents" }}>
              <dt>{a}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
