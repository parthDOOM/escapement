import { useState } from "react";
import Header from "../components/Header";
import Ruler from "../components/Ruler";
import { useJson } from "../lib/util";
import BenchLayout from "./BenchLayout";
import SignalPath from "./SignalPath";
import { BlockDiagram, FpgaVsAsic } from "./Blocks";
import type { Firmware, Fpga, Wire } from "./types";
import { cssColour, REPO, wireColour } from "./types";

const SECTIONS = [
  ["bench", "Bench"],
  ["path", "Signal path"],
  ["design", "On the FPGA"],
  ["results", "Results"],
  ["trace", "Traceability"],
  ["limits", "Limits"],
] as const;

// What each bring-up stage proves (docs/16-BRING-UP.md); the result and its detail come from the log.
const PROVES: Record<number, string> = {
  0: "The host runs the repository's bring-up script and finds the chip it expects.",
  1: "The clock reaches the chip, reset works, and the outputs sit at their reset values in and after reset.",
  2: "The four-wire SPI link reads STATUS and writes registers, at a fast and a slow SCK.",
  3: "Both memories store and return every pattern at every address, and the tape can be read while the core uses it.",
  4: "Every pin of every bank drives and reads as it should, and the chip's own tick counter measures its clock.",
  5: "Every firmware in the repository runs on the chip and ends in the state the reference model predicts.",
};
const KIND: Record<Firmware["kind"], string> = {
  steady: "digest at a predicted tick",
  halt: "digest when the program halts",
  none: "no fault, as the model predicts",
};
const n = (v: number) => v.toLocaleString("en");

export default function FpgaApp() {
  const [d, err] = useJson<Fpga>("data/fpga.json");
  return (
    <>
      <Header sections={SECTIONS} other={{ href: "../", label: "The chip" }} home="../" />
      <main id="top">
        {err && <div className="wrap"><p className="loading">The bench data could not be loaded ({err}).</p></div>}
        {!d && !err && <div className="wrap"><p className="loading" style={{ minHeight: 400 }}>loading…</p></div>}
        {d && <Page d={d} />}
      </main>
      <footer>
        <div className="wrap">
          <span>Escapement · Apache-2.0 · an entry to the Jane Street protocol emulator competition</span>
          <span><a href={`${REPO}/tree/master/fpga/tangprimer25k`}>Bench sources and results</a></span>
        </div>
      </footer>
    </>
  );
}

function Page({ d }: { d: Fpga }) {
  const ok = d.firmware.filter((f) => f.ok).length;
  const withDigest = d.firmware.filter((f) => f.digest).length;
  const passed = d.stages.filter((s) => s.pass).length;
  const lut = d.pnr.util.LUT4, ff = d.pnr.util.DFF, bram = d.pnr.util.BSRAM;
  const commit = d.chip.commit ?? "";
  return (
    <>
      <section className="hero fpga-hero">
        <div className="wrap hero-grid">
          <div>
            <p className="eyebrow">Hardware bench · v1.6 · {d.date}</p>
            <h1>On the <em>bench</em></h1>
            <p className="claim">The chip's RTL on an FPGA, driven pin for pin by the bring-up script it was written
              for. {d.verdict === "PASSED" ? "Every stage passes." : "See the results below."}</p>
            <p className="lede">A Raspberry Pi Pico W plays the Tiny Tapeout demo board and a Sipeed Tang Primer 25K
              plays the chip. Version 1.6 of the RTL, the same commit that was hardened for silicon, runs through
              its own wiring check, six bring-up stages and all {d.firmware.length} firmware programs, each compared
              with the reference model.</p>
            <div className="facts">
              <div><b>{d.pinid.map.length}</b><span>signals wired, {d.pinid.pass ? "all identified" : "see wiring"}</span></div>
              <div><b>{passed} of {d.stages.length}</b><span>bring-up stages pass</span></div>
              <div><b>{ok} of {d.firmware.length}</b><span>firmware match the model</span></div>
              <div><b>{d.clock_mhz?.toFixed(4)}</b><span>MHz, the clock the chip measured</span></div>
              <div><b>{d.pnr.fmax_mhz?.toFixed(1)}</b><span>MHz limit of this FPGA build</span></div>
              <div><b>{d.chip.skew_ns?.toFixed(3)} ns</b><span>clock skew across every clock sink</span></div>
            </div>
          </div>
          <figure className="panel">
            <BenchLayout d={d} />
            <figcaption>The bench from above, to scale. Wire colours follow the bit: bit 0 brown to bit 7 grey,
              ground black, clock white. Red rings mark what must stay empty: the sockets' 3V3 column, the
              header's 5 V pin and the Pico's power pins.</figcaption>
          </figure>
        </div>
      </section>

      <Ruler n={11} />

      <section id="bench">
        <div className="wrap">
          <div className="sec-head">
            <p className="eyebrow">The bench</p>
            <h2>Two boards, 32 wires</h2>
          </div>
          <div className="cols">
            <div className="prose">
              <p><strong>The chip.</strong> A Sipeed Tang Primer 25K Dock carries a Gowin GW5A-25 FPGA. It runs
                the chip's RTL with two substitutions, both with the chip's behaviour: plain flip-flops for the
                clock gates and latch banks, and a block RAM with the SRAM macro's timing contract for each memory.
                The 24 Tiny Tapeout pins sit on its three Pmod sockets, the clock and reset on its 40-pin header.</p>
              <p><strong>The host.</strong> A Raspberry Pi Pico W runs MicroPython and the repository's bring-up
                script, unchanged. A small shim, <code>barepico.py</code>, gives the script the demo board's pin
                interface on plain GPIO: the clock from a PWM output, the SPI link and every bank pin on GPIOs, a
                byte per register access as on the demo board.</p>
              <p><strong>The wiring check.</strong> Before the chip goes on, a test bitstream sends each signal's
                own number down its wire and the Pico identifies every GPIO without driving any. This run:
                <span className={d.pinid.pass ? "ok-text" : "bad-text"}> {d.pinid.pass ? "WIRING: PASS" : "failed"}</span>,
                all {d.pinid.map.length} signals on the expected GPIOs.</p>
            </div>
            <WireTable d={d} />
          </div>
        </div>
      </section>

      <Ruler n={12} />

      <section id="path" className="band">
        <div className="wrap">
          <div className="sec-head">
            <p className="eyebrow">Schematic</p>
            <h2>From GPIO to chip port</h2>
            <p className="lede">Every signal's path: the Pico's GPIOs, the wires and the Dock connector, the FPGA balls
              they land on, and the port of the chip they drive or read.</p>
          </div>
          <figure className="panel scroll-x"><SignalPath d={d} /></figure>
          <p className="note" style={{ marginTop: "1.5rem" }}>The clock enters on ball L3. The neighbouring L4, also
            clock-capable, cannot be put on the FPGA's global clock network by the open-source place-and-route tool
            used here: a first build with the clock there fell back to ordinary routing, with 5.5 ns of skew and 191
            hold violations, and was never loaded. From L3 the clock reaches every flip-flop and both block RAMs
            through one global buffer with {d.chip.skew_ns?.toFixed(3)} ns of measured skew.</p>
        </div>
      </section>

      <Ruler n={13} />

      <section id="design">
        <div className="wrap">
          <div className="sec-head">
            <p className="eyebrow">Synthesis</p>
            <h2>The design on the FPGA</h2>
            <p className="lede">The same blocks as the silicon floorplan, synthesised for the GW5A with their boundaries
              kept. Line widths and labels give each bus's width in bits, both directions.</p>
          </div>
          <figure className="panel scroll-x"><BlockDiagram d={d} /></figure>
          <div className="stats" style={{ marginTop: "2rem" }} aria-label="FPGA build">
            <div><b>{n(lut[0])}</b><span>LUT4 of {n(lut[1])} ({Math.round((100 * lut[0]) / lut[1])}%)</span></div>
            <div><b>{n(ff[0])}</b><span>flip-flops of {n(ff[1])} ({Math.round((100 * ff[0]) / ff[1])}%)</span></div>
            <div><b>{bram[0]}</b><span>block RAMs of {bram[1]}</span></div>
            <div><b>{d.pnr.fmax_mhz?.toFixed(1)} MHz</b><span>clock limit, after routing (run at 25)</span></div>
            <div><b>{d.chip.skew_ns?.toFixed(3)} ns</b><span>clock skew</span></div>
          </div>
          <div className="cols" style={{ marginTop: "3rem" }}>
            <div className="prose">
              <h3>One design, two forms</h3>
              <p>The same RTL as a GW5A bitstream and as the IHP SG13G2 layout. The FPGA column counts each block
                with its boundaries kept ({n(d.hierarchy.total.lut4)} LUT4 in all); the bitstream itself, flattened
                and optimised across blocks, uses {n(lut[0])}. The silicon column is the signed-off v1.6 layout.</p>
              <p>A full gate-level drawing of either is a wall of boxes: tens of thousands of cells. The die views on
                the <a href="../#silicon">main page</a> show the real layout down to the transistors.</p>
              <p className="note">nextpnr reports 40 hold flags at the block RAM address pins. A lone counter feeding a
                block RAM gets the same 40, also with the clock on the Dock's own oscillator pin, which the tool routes
                entirely on global wires: they come from its block RAM timing model, not from this design. The build
                fails unless the clock is global, the skew is at most 0.05 ns, setup passes at 25 MHz and no other hold
                flag appears; bring-up stage 3 then exercises both block RAMs on the board.</p>
            </div>
            <FpgaVsAsic d={d} />
          </div>
        </div>
      </section>

      <Ruler n={14} />

      <section id="results" className="band">
        <div className="wrap">
          <div className="sec-head">
            <p className="eyebrow">The run, {d.date}</p>
            <h2>Bring-up {d.verdict === "PASSED" ? "passed" : "result"}</h2>
            <p className="lede">The bring-up script's six stages, in order, each with what it proves and what it
              measured on this run.</p>
          </div>
          <div className="table-box panel">
            <table>
              <thead><tr><th>Stage</th><th>Proves</th><th>Result on the board</th></tr></thead>
              <tbody>
                {d.stages.map((s) => (
                  <tr key={s.n}>
                    <td style={{ whiteSpace: "nowrap" }}><b>{s.n}</b> {s.name}</td>
                    <td>{PROVES[s.n]}</td>
                    <td><span className={"pill " + (s.pass ? "ok" : "bad")}>{s.pass ? "pass" : "fail"}</span>{" "}
                      <span className="mono" style={{ color: "var(--fg-2)" }}>{s.detail}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="sub-head">
            <h3>Every firmware, against the model</h3>
            <p>Each program is assembled from the repository, loaded over SPI and run on the chip. {withDigest} end
              in a 32-bit execution digest, which folds every commit, register write and pin change of the run into
              one word, compared with the model's prediction, together with every context's state, registers and pin
              levels. The other {d.firmware.length - withDigest} run open-ended loops whose state never settles: for them
              the chip must raise exactly the fault the model predicts, which for all of them is none. Where a program has
              a timing certificate, the chip's own minimum lead, in ticks between queueing an event and its deadline, is
              shown against the bound <code>escert</code> proved for it; a dash means no queued event updated the
              chip's minimum.</p>
          </div>
          <FirmwareTable fw={d.firmware} />
        </div>
      </section>

      <Ruler n={15} />

      <section id="trace">
        <div className="wrap">
          <div className="sec-head">
            <p className="eyebrow">Traceability</p>
            <h2>Exactly what ran</h2>
          </div>
          <div className="cols">
            <dl className="trace">
              <dt>Commit</dt><dd><a href={`${REPO}/commit/${commit}`}>{commit}</a> ({d.chip.branch}{d.chip.dirty ? ", modified" : ", clean"})</dd>
              <dt>Bitstream</dt><dd>sha256 {d.chip.bitstream_sha256}</dd>
              <dt>Built</dt><dd>{d.chip.built}</dd>
              <dt>Tools</dt><dd>{d.chip.tools}</dd>
              <dt>Firmware</dt><dd>bundles from {d.bundles.commit?.slice(0, 7)} ({d.bundles.dirty ? "modified" : "clean"}), built {d.bundles.built}</dd>
              <dt>Evidence</dt><dd><a href={`${REPO}/tree/master/${d.files}`}>{d.files}</a></dd>
            </dl>
            <div className="prose">
              <p>Each build writes a manifest with the commit and the sha256 of every input. The bring-up log, the
                stage 5 telemetry of every program, the wiring check and the place-and-route log of this run are
                committed next to the bench sources, and this page is generated from them.</p>
              <p>Rebuilding from the published scripts gives a bit-identical bitstream: the same sha256. To repeat the
                run, follow <a href={`${REPO}/tree/master/fpga/tangprimer25k`}>fpga/tangprimer25k</a>: two boards,
                32 jumper wires and the OSS CAD Suite.</p>
            </div>
          </div>
        </div>
      </section>

      <Ruler n={16} />

      <section id="limits" className="band">
        <div className="wrap">
          <div className="sec-head">
            <p className="eyebrow">Limits</p>
            <h2>What an FPGA run cannot show</h2>
            <p className="lede">This bench tests the design: its RTL, its firmware and its host interface on real
              pins at the real clock. It does not test the silicon.</p>
          </div>
          <div className="cols">
            <ul className="limits">
              <li>The hardened netlist: the FPGA build uses a different synthesis tool and cell library.</li>
              <li>The clock gates and latch banks: the FPGA build makes them plain flip-flops.</li>
              <li>The IHP SRAM macros: replaced by block RAM with the same timing contract.</li>
            </ul>
            <div className="prose">
              <p>Those stay covered by the gate-level test suites on the standard-cell models, the RTL-to-netlist
                equivalence checks and timing signoff at every corner, all on the <a href="../#proof">main page</a>.
                What the bench adds is the part simulation cannot: real wires, a real clock from another chip, and
                the repository's own host software talking to the design.</p>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}

function WireTable({ d }: { d: Fpga }) {
  const order = ["J4", "J5", "J6", "J3"];
  const label: Record<string, string> = { J4: "J4 · ui_in", J5: "J5 · uo_out", J6: "J6 · uio", J3: "J3 · clock and reset" };
  const ws = [...d.wires].sort((a, b) => order.indexOf(a.dock) - order.indexOf(b.dock) || a.pico - b.pico);
  const pos = (w: Wire) => (w.dock === "J3" ? `pin ${w.pin}, ${w.row} row` : `${w.row} row, column ${w.col}`);
  let last = "";
  return (
    <div className="table-box panel" style={{ maxHeight: 560, overflowY: "auto" }}>
      <table>
        <thead><tr><th>Wire</th><th className="num">Pico</th><th>Signal</th><th>Hole</th><th>Ball</th></tr></thead>
        <tbody>
          {ws.flatMap((w) => {
            const rows = [];
            if (w.dock !== last) { last = w.dock; rows.push(<tr key={"g" + w.dock} className="group"><td colSpan={5}>{label[w.dock]}</td></tr>); }
            rows.push(
              <tr key={w.dock + w.pin + w.sig}>
                <td><i className="sw" style={{ background: cssColour(wireColour(w)) }} />{wireColour(w)}</td>
                <td className="num mono">{w.pico} · {d.pico[w.pico - 1]}</td>
                <td className="mono">{w.sig}</td>
                <td className="mono">{pos(w)}</td>
                <td className="mono">{w.ball}</td>
              </tr>,
            );
            return rows;
          })}
        </tbody>
      </table>
    </div>
  );
}

function FirmwareTable({ fw }: { fw: Firmware[] }) {
  const [kind, setKind] = useState<"all" | Firmware["kind"]>("all");
  const shown = fw.filter((f) => kind === "all" || f.kind === kind);
  const kinds: ("all" | Firmware["kind"])[] = ["all", "steady", "halt", "none"];
  const count = (k: string) => (k === "all" ? fw.length : fw.filter((f) => f.kind === k).length);
  return (
    <>
      <div className="fw-filter" role="tablist" aria-label="Filter firmware by check">
        {kinds.map((k) => (
          <button key={k} type="button" role="tab" className="tab" aria-selected={kind === k} onClick={() => setKind(k)}>
            {k === "all" ? "all" : KIND[k]} ({count(k)})
          </button>
        ))}
      </div>
      <div className="table-box panel">
        <table>
          <thead>
            <tr><th>Firmware</th><th>Check</th><th>On the chip</th><th>Model</th><th className="num">Tick</th>
              <th>Minimum lead (ticks) against certificate</th><th>Result</th></tr>
          </thead>
          <tbody>
            {shown.map((f) => (
              <tr key={f.name}>
                <td className="mono">{f.name.replace(/\.(asm|json)$/, "")}</td>
                <td><span className="kind">{f.kind}</span></td>
                <td className="mono">{f.digest ?? "no fault"}</td>
                <td className="mono">{f.model ?? "no fault"}</td>
                <td className="num mono">{f.tick ? n(f.tick) : ""}</td>
                <td className="mono">{f.leads.map(([c, l, k]) => (l === null ? `ctx ${c}: — (cert ${k})` : `ctx ${c}: ${l} ≥ ${k}`)).join(" · ")}</td>
                <td><span className={"pill " + (f.ok ? "ok" : "bad")}>{f.ok ? "match" : "differs"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
