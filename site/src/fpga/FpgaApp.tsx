import { useState } from "react";
import Header from "../components/Header";
import Ruler from "../components/Ruler";
import EthCircuit from "./EthCircuit";
import SwdCircuit from "./SwdCircuit";
import Pinout from "../components/Pinout";
import { useJson } from "../lib/util";
import WiringDiagram, { ROLE } from "./WiringDiagram";
import SignalPath from "./SignalPath";
import { BlockDiagram, FpgaVsAsic } from "./Blocks";
import type { Firmware, Fpga, Wire } from "./types";
import { BIT_COLOURS, cssColour, REPO, wireColour } from "./types";

const SECTIONS = [
  ["bench", "Bench"],
  ["pins", "Pins"],
  ["path", "Signal path"],
  ["design", "On the FPGA"],
  ["results", "Results"],
  ["trace", "Traceability"],
  ["ethernet", "Ethernet"],
  ["swd", "SWD"],
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
      <Header sections={SECTIONS} page="fpga" />
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
  const unc = d.firmware.filter((f) => f.uncertified);
  const withDigest = d.firmware.filter((f) => f.digest).length;
  const passed = d.stages.filter((s) => s.pass).length;
  const lut = d.pnr.util.LUT4, ff = d.pnr.util.DFF, bram = d.pnr.util.BSRAM;
  const commit = d.chip.commit ?? "";
  const [sel, setSel] = useState<number | null>(null);
  // a bitstream whose build checks failed and was packed by hand for the run (v3: chip.MANIFEST)
  const packed = !!d.chip.checks_failed;
  return (
    <>
      <section className="hero fpga-hero">
        <div className="wrap hero-grid">
          <div>
            <p className="eyebrow">Hardware bench · v3 · {d.date}</p>
            <h1>On the <em>bench</em></h1>
            <p className="claim">The chip's RTL on an FPGA, driven pin for pin by the bring-up script it was written
              for. {d.verdict === "PASSED" ? "Every stage passes." : unc.length && ok + unc.length === d.firmware.length
                ? `Every program matches the model; ${unc.map((f) => f.name.replace(/\.(asm|json)$/, "")).join(", ")}, ` +
                  "an unfinished bench program with no timing certificate, is the one stage 5 refuses to pass."
                : "See the results below."}</p>
            <p className="lede">A Raspberry Pi Pico W plays the Tiny Tapeout demo board and a Sipeed Tang Primer 25K
              plays the chip. Version 3 of the RTL, the same Verilog that was hardened for silicon, runs through
              six bring-up stages and all {d.firmware.length} firmware runs, each compared with the reference
              model.</p>
            <div className="facts">
              <div><b>{d.pinid.map.length}</b><span>signals wired, {d.pinid.pass ? "all identified" : "see wiring"}</span></div>
              <div><b>{passed} of {d.stages.length}</b><span>bring-up stages pass</span></div>
              <div><b>{ok} of {d.firmware.length}</b><span>firmware match the model and their certificates</span></div>
              <div><b>{d.clock_mhz?.toFixed(4)}</b><span>MHz, the clock the chip measured</span></div>
              <div><b>{d.pnr.fmax_mhz?.toFixed(1)}</b><span>MHz limit of this FPGA build</span></div>
              {packed
                ? <div><b>{d.chip.checks_failed}</b><span>build checks failed: <a href="#path">the clock</a></span></div>
                : <div><b>{d.chip.skew_ns?.toFixed(3)} ns</b><span>clock skew across every clock sink</span></div>}
            </div>
          </div>
          <figure className="panel photo">
            <img src="../img/bench-setup.jpg" width={1400} height={963}
              alt="The bench: a Raspberry Pi Pico W on the left, its pins wired by a bundle of coloured jumpers to the three Pmod sockets of a Sipeed Tang Primer 25K Dock lying on a breadboard on the right, with a USB-C cable in the Dock and a USB cable in the Pico." />
            <figcaption>The bench as it ran: the Pico W (left) and the Tang Primer 25K Dock (right), each powered
              over its own USB cable, joined by 32 jumpers and no power wire. Every wire is drawn and listed
              under <a href="#bench">The bench</a>.</figcaption>
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
                own number down its wire and the Pico identifies every GPIO without driving any. It last ran
                on {d.pinid.date}{d.pinid.date !== d.date ? ", and the wiring has not changed since" : ""}:
                <span className={d.pinid.pass ? "ok-text" : "bad-text"}> {d.pinid.pass ? "WIRING: PASS" : "failed"}</span>,
                all {d.pinid.map.length} signals on the expected GPIOs.</p>
            </div>
            <WireTable d={d} sel={sel} setSel={setSel} />
          </div>
          <figure className="panel wiring-fig" style={{ marginTop: "2rem" }}>
            <div className="scroll-x"><WiringDiagram d={d} sel={sel} setSel={setSel} /></div>
            <WireNote d={d} sel={sel} />
            <figcaption>Every jumper, routed to be followed rather than drawn to scale. Colours follow the
              bit, {BIT_COLOURS.map((c, i) => <Key key={c} c={c} t={String(i)} />)} with <Key c="black" t="ground," />{" "}
              <Key c="white" t="clock" /> and <Key c="grey" t="reset." /> Each socket hole shows its bit, G for ground.
              Red rings are never wired: the sockets' 3V3 column, J3's +5V pin, and the Pico's power pins and
              RUN.</figcaption>
          </figure>
          <div className="sub-head" id="pins" style={{ marginTop: "3.5rem" }}>
            <p className="eyebrow">The chip's pins</p>
            <h3>Twenty-four pins, two banks</h3>
            <p className="dim">The Tiny Tapeout pins as the chip uses them. On the bench each one is a wire from the
              Pico W to a Dock Pmod (the table above); on the demo board they are the same pins of the same chip.</p>
          </div>
          <Pinout />
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
            hold violations, and was never loaded. From L3 the clock reaches every flip-flop and block RAM
            through one global buffer{packed ? "." : <> with {d.chip.skew_ns?.toFixed(3)} ns of measured skew.</>}</p>
          {packed && <p className="note" style={{ marginTop: "1rem" }}>Version 3 adds one more clock sink. It samples
            the 13 protocol pins on the falling clock edge as well, so that the line unit sees each pin twice per tick,
            and the FPGA synthesis made that edge with an inverter in a LUT instead of a falling-edge flip-flop. The
            inverter's input is reached through ordinary routing, {d.chip.skew_ns?.toFixed(3)} ns after every flip-flop
            and block RAM clock pin, and the falling-edge bank is a clock domain of its own, which nextpnr does not
            time into the core: by design the core reads it half a cycle later, 20 ns at 25 MHz. Two of the build's
            own checks fail on this, a clock wholly on the global network and at most 0.05 ns of skew, so this
            bitstream was packed by hand for the bench run. Every stage and every firmware passed on it.</p>}
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
            <div><b>{d.chip.skew_ns?.toFixed(3)} ns</b><span>clock skew{packed ? ", the falling-edge sink" : ""}</span></div>
          </div>
          <div className="cols" style={{ marginTop: "3rem" }}>
            <div className="prose">
              <h3>One design, two forms</h3>
              <p>The same RTL as a GW5A bitstream and as the IHP SG13CMOS5L layout. The FPGA column counts each block
                with its boundaries kept ({n(d.hierarchy.total.lut4)} LUT4 in all); the bitstream itself, flattened
                and optimised across blocks, uses {n(lut[0])}. The silicon column is the signed-off version 3 layout.</p>
              <p>A full gate-level drawing of either is a wall of boxes: tens of thousands of cells. The die views on
                the <a href="../silicon/">layout page</a> show the real layout down to the transistors.</p>
              <p className="note">nextpnr reports {d.pnr.hold_flags} hold flags, all at block RAM address pins. A lone
                counter feeding a block RAM gets such flags too, also with the clock on the Dock's own oscillator pin,
                which the tool routes entirely on global wires: they come from its block RAM timing model, not from this
                design. The build fails unless the clock is global, the skew is at most 0.05 ns, setup passes at 25 MHz
                and no other hold flag appears{packed ? " (this run's bitstream fails the first two: see the signal path)" : ""};
                bring-up stage 3 then exercises every block RAM on the board.</p>
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
              <dt>Commit</dt><dd><a href={`${REPO}/commit/${commit}`}>{commit}</a> ({d.chip.branch}{/clean/.test(d.chip.branch ?? "") ? "" : d.chip.dirty ? ", modified" : ", clean"})</dd>
              <dt>Bitstream</dt><dd>sha256 {d.chip.bitstream_sha256}</dd>
              {d.chip.built && <><dt>Built</dt><dd>{d.chip.built}</dd></>}
              <dt>Tools</dt><dd>{d.chip.tools}</dd>
              <dt>Firmware</dt><dd>bundles from {d.bundles.commit?.slice(0, 7)} ({d.bundles.dirty ? "modified" : "clean"}){d.bundles.built ? `, built ${d.bundles.built}` : ""}</dd>
              <dt>Evidence</dt><dd><a href={`${REPO}/tree/master/${d.files}`}>{d.files}</a></dd>
            </dl>
            <div className="prose">
              <p>Each build writes a manifest with the commit and the sha256 of every input. The bring-up log, the
                stage 5 telemetry of every program and the place-and-route log of this run, and the wiring check of
                {" "}{d.pinid.date}, are kept next to the bench sources, and this page is generated from them.</p>
              <p>{packed
                ? "This run's bitstream was packed by hand after two of the build's own checks failed (the signal path, above), so the published build script stops before it; its sha256 is the one in the manifest."
                : "Rebuilding from the published scripts gives a bit-identical bitstream: the same sha256."} To repeat the
                run, follow <a href={`${REPO}/tree/master/fpga/tangprimer25k`}>fpga/tangprimer25k</a>: two boards,
                32 jumper wires and the OSS CAD Suite.</p>
            </div>
          </div>
        </div>
      </section>

      <Ruler n={16} />

      <section id="ethernet" className="band">
        <div className="wrap">
          <div className="sec-head">
            <p className="eyebrow">Ethernet · 2026-11-16</p>
            <h2>10BASE-T with a laptop</h2>
            <p className="lede">The same bitstream at 40 MHz, with only resistors, capacitors and a patch cable cut
              open at one end between the Dock and a laptop's Ethernet port. Each direction is its own program
              load.</p>
          </div>
          <div className="facts" style={{ marginBottom: "2rem" }}>
            <div><b>20 of 20</b><span>test frames from <code>eth_tx</code> arrived intact</span></div>
            <div><b>20 of 20</b><span>of the laptop's own frames into <code>eth_rx</code>, each with a good FCS</span></div>
            <div><b>0.9 ms</b><span>to push a frame over the bit-banged link</span></div>
          </div>
          <figure className="panel">
            <div className="scroll-x"><EthCircuit /></div>
            <figcaption>Both circuits as built on the breadboard. The wires on the left come from the Dock's Pmod
              sockets, and the conductors on the right are a patch cable cut open at this end, with its plug in the
              laptop. Transmit drives the pair a laptop receives on; receive takes the pair it sends on. On the
              receive bench both are wired, because the chip's link pulses keep the laptop's link up.</figcaption>
          </figure>
          <div className="cols" style={{ marginTop: "2rem" }}>
            <figure className="panel photo">
              <img src="../img/eth-tx-bench.jpg" width={1400} height={883} loading="lazy"
                alt="The transmit bench: the Pico W and the Tang Primer 25K Dock, with two jumpers from the Dock's J6 socket to a breadboard, where resistors and capacitors join them to the green pair of a cut-open Ethernet cable." />
              <figcaption>The transmit bench.</figcaption>
            </figure>
            <figure className="panel photo">
              <img src="../img/eth-rx-bench.jpg" width={1400} height={847} loading="lazy"
                alt="The receive bench: the same two boards, with the breadboard now also holding the orange pair's coupling capacitors, termination and bias resistors, wired to the Dock's receive pin, 3V3 and ground." />
              <figcaption>The receive bench: the transmit circuit stays for the link pulses.</figcaption>
            </figure>
          </div>
          <figure className="panel photo" style={{ marginTop: "2rem" }}>
            <img src="../img/eth-board.jpg" width={1400} height={654} loading="lazy"
              alt="The breadboard close up: jumpers from the Dock and the cut-open cable's conductors, joined only by resistors and small ceramic capacitors, the transmit network and the receive network side by side." />
            <figcaption>The breadboard close up: the transmit and receive networks, nothing but resistors and
              capacitors between the chip's pins and the cable.</figcaption>
          </figure>
          <div className="cols" style={{ marginTop: "2rem" }}>
            <div className="prose">
              <p><strong>Transmit.</strong> <code>eth_tx</code> keeps the laptop's link up with a pulse every 16 ms
                and sends each frame the Pico pushes into its mailbox, 0.9 ms of register writes a frame. Windows'
                own packet capture showed all 20 test frames, <code>escapement eth_tx 1</code> to <code>20</code>,
                and the port counted no error. With the cable unplugged the same run counts none. The line unit's
                report that a frame was sent says only that it left the pins, so the laptop's counters are the
                end-to-end check.</p>
              <p><strong>Two programs in one load.</strong> <code>eth_rx</code> needs the chip's one line unit, so
                the receive bench makes its link pulses in a second context from timed pin events. While the laptop
                sends, that context's timer keeps exact time, so the pulses never stop.</p>
            </div>
            <div className="prose">
              <p><strong>Receive.</strong> <code>eth_rx</code> was written for a line receiver behind a
                transformer. Here an FPGA pin's input threshold stands in for one, so the bias network had to be
                found by experiment (below). The capture unit recorded the line's edges at the pin after each change,
                and with the last one the chip received the laptop's DHCP, ARP and multicast traffic, every frame
                with a good FCS.</p>
              <p><strong>Not explained yet.</strong> On the receive bench the laptop's link comes and goes, about
                0.6 s up and 2.3 s down. Each up interval is long enough for several frames.</p>
              <p><strong>Frames, not yet a network.</strong> The chip does the link layer: whole frames in and out,
                checked or sealed with their CRC-32, and the laptop's IP packets arrived byte for byte. Answering a ping
                needs a protocol stack on the host and one program that switches the line unit between receiving and
                sending, which is allowed because 10BASE-T is half duplex. It needs no chip change.</p>
            </div>
          </div>
          <div className="sub-head">
            <h3>How the receive circuit got there</h3>
            <p>Four versions, each changed for the reason given and judged by the line check: the capture unit's
              record of the high and low times at the pin. A clean 10BASE-T line at 40 MHz shows only 50 and 100 ns
              runs, as many low as high.</p>
          </div>
          <div className="table-box panel">
            <table>
              <thead><tr><th>Circuit</th><th>Why</th><th>What the pin showed</th></tr></thead>
              <tbody>
                {ETH_STEPS.map(([c, why, got, ok], i) => (
                  <tr key={i}>
                    <td className="mono" style={{ whiteSpace: "nowrap" }}>{c}</td>
                    <td>{why}</td>
                    <td><span className={"pill " + (ok ? "ok" : "bad")}>{ok ? "clean" : "no"}</span> {got}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="note" style={{ marginTop: "1.5rem" }}>The circuits, the logs, the captures and the commands
            are in <a href={`${REPO}/tree/master/fpga/tangprimer25k#ethernet-10base-t-2026-11-16`}>fpga/tangprimer25k</a>.</p>
        </div>
      </section>

      <Ruler n={17} />

      <section id="swd">
        <div className="wrap">
          <div className="sec-head">
            <p className="eyebrow">SWD · 2026-11-17</p>
            <h2>The chip debugs an RP2040</h2>
            <p className="lede">The firmware <code>swd_idcode</code> is an Arm Serial Wire Debug host. Here it debugs a
              second Raspberry Pi Pico through that Pico's 3-pin debug header, as a debug probe would, while a logic
              analyser that is not ours records the two wires and sigrok's decoder reads them.</p>
          </div>
          <div className="facts" style={{ marginBottom: "2rem" }}>
            <div><b>0x0BC12477</b><span>the RP2040's debug port ID, read by the chip</span></div>
            <div><b>15 of 15</b><span>accesses after the wake-up decoded off the wire as the chip reported them</span></div>
            <div><b>640 ns</b><span>every SWCLK phase as measured: 16 ticks at 25 MHz</span></div>
            <div><b>2 of 2</b><span>cores selected by TARGETSEL; none answers a TARGETSEL that names no port</span></div>
          </div>
          <figure className="panel">
            <div className="scroll-x"><SwdCircuit /></div>
            <figcaption>The wiring. Each line from the Dock goes to a breadboard row, and from it to the spare Pico
              twice: to its debug header, which is the target, and to a GPIO that the same board samples. The debug
              port is separate hardware from the cores, so one Pico can be the target and run the analyser firmware
              at once. The Pico W's own wires to those two Dock holes are unplugged for this run.</figcaption>
          </figure>
          <figure className="panel photo" style={{ marginTop: "2rem", maxWidth: "760px", marginInline: "auto" }}>
            <img src="../img/swd-bench.jpg" width={1400} height={1404} loading="lazy"
              alt="The SWD bench: the Tang Primer 25K Dock and the bench Pico W on the right, the target Pico on the left on its own USB cable, and a small breadboard where the debug lines meet the target's analyser inputs." />
            <figcaption>The SWD bench: the Dock and the bench Pico W (right), the target Pico (left), and the
              breadboard where SWCLK and SWDIO reach both its debug header and the GPIOs it samples them on.</figcaption>
          </figure>
          <div className="cols" style={{ marginTop: "2rem" }}>
            <div className="prose">
              <p><strong>The host only asks.</strong> The bench Pico W pushes one command at a time into the
                chip's mailbox and reads the reply. Every SWCLK and SWDIO edge comes from the chip, at 25 MHz with
                SWCLK at 781 kHz. Nothing is written to the target's memory and no core is halted: the session reads
                the debug port, powers up the debug domain, and reads one word of the boot ROM through the AP.</p>
              <p><strong>A multi-drop port.</strong> The RP2040 puts core 0, core 1 and a rescue port on the same two
                wires (SWD protocol version 2). They start dormant, and a probe must wake them and select one before
                any of them answers. <code>swd_idcode</code> gained both for this bench: one command bit sends the
                wake-up sequence and a line reset, and a debug-port write to address 0xC goes out as TARGETSEL, which
                no target acknowledges. The sequences are the ones OpenOCD sends, and the program keeps its timing
                certificate.</p>
            </div>
            <div className="prose">
              <p><strong>Judged from outside.</strong> The spare Pico runs gusmanb's LogicAnalyzer firmware (v6.0)
                and samples both lines at 25 MS/s during each step. sigrok's <code>swd</code> decoder reads each
                recording. In every access after the wake-up, it took off the wire exactly the data the chip reported
                to its host, or the data the host asked it to write, each with ACK OK.</p>
              <p><strong>The control fails.</strong> The DPIDR recording with one SWDIO pulse inverted no longer
                decodes to the chip's value. The wake-up step is judged on its clock only: sigrok's decoder (0.7.2)
                has no dormant state or TARGETSEL, so it reads the selection alert as packets. The next step shows
                that the wake-up worked, because the RP2040 answers only once it is woken and selected.</p>
            </div>
          </div>
          <div className="sub-head">
            <h3>The session, step by step</h3>
            <p>Each row is one host command, recorded on its own. The chip column is what the firmware reported to the
              host; the decoder column is what sigrok read from the analyser's recording of the wires.</p>
          </div>
          <div className="table-box panel">
            <table>
              <thead><tr><th>Step</th><th>Access</th><th>The chip reported</th><th>sigrok read off the wire</th></tr></thead>
              <tbody>
                {SWD_STEPS.map(([what, chip, dec], i) => (
                  <tr key={i}>
                    <td className="num mono">{i}</td>
                    <td>{what}</td>
                    <td className="mono">{chip}</td>
                    <td className="mono">{dec}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="note" style={{ marginTop: "1.5rem" }}>The boot ROM word, 0x0301754D, is the ROM's
            magic <code>Mu</code>, 0x01 and its version, 3: an RP2040-B2. Selecting core 1 instead reads the same values
            with instance 1 in DLPIDR. sigrok names debug-port address 0x4 by its bank-0 register, CTRL/STAT, also
            when bank 3 makes it DLPIDR (step 3). The log, the recordings and the commands are
            in <a href={`${REPO}/tree/master/fpga/tangprimer25k#swd-the-chip-debugs-an-rp2040-2026-11-17`}>fpga/tangprimer25k</a>.</p>
        </div>
      </section>

      <Ruler n={18} />

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
              {packed && <li>This run's falling-edge input sampler, which the FPGA clocks through an inverter off the
                global network (the signal path, above).</li>}
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

/** The receive circuit's versions (fpga/tangprimer25k/README.md, Ethernet): circuit, reason, result. */
const ETH_STEPS: [string, string, string, boolean][] = [
  ["1 kΩ series · bias 2.0 V",
    "A swing of about ±0.9 V around 2 V stays inside the pin's supply and idles high, as eth_rx expects, assuming the pin switches near half its supply.",
    "Never low in 25 s while the laptop sent about 40 frames: the swing did not reach the real switching point.", false],
  ["100 Ω series · bias 2.0 V",
    "0.86 of the laptop's swing reaches the pin instead of 0.375, to cross the switching point wherever it is; the pin's input diodes clip the excess.",
    "Frames arrived and eth_rx locked on (the first bytes were the broadcast address), then lost sync. Lows 25 ns against 75 ns highs: the bias sits far above the switching point.", false],
  ["bias 1.32 V (1.5 kΩ up, 1 kΩ down)",
    "Move the idle level toward the switching point. The divider's resistance stays 600 Ω, so the swing does not change.",
    "Lows 25 to 50 ns, and some missed altogether (175 ns highs): every frame check sequence bad.", false],
  ["bias 0.94 V (1 kΩ || 1.5 kΩ down)",
    "One step further down, with the swing about the same (430 Ω).",
    "Only 50 and 100 ns runs, as many low as high; 20 of 20 frames with a good FCS. The pin switches near 1 V.", true],
];

/** The judged SWD session (fpga/tangprimer25k/results/2026-11-17/swd-judge/swdjudge.log):
 *  access, what the chip reported, what sigrok's swd decoder read from the recording. */
const SWD_STEPS: [string, string, string][] = [
  ["Wake from dormant, TARGETSEL core 0 (0x01002927)", "no ACK, as none comes", "not judged: no dormant state or TARGETSEL in the decoder"],
  ["DP read: DPIDR", "OK 0x0BC12477", "IDCODE OK 0x0bc12477"],
  ["DP write: SELECT, DP bank 3", "OK", "W SELECT OK 0x00000003"],
  ["DP read: DLPIDR (version 2, instance 0)", "OK 0x00000001", "R CTRL/STAT OK 0x00000001"],
  ["DP write: SELECT 0", "OK", "W SELECT OK 0x00000000"],
  ["DP write: ABORT, clear the sticky flags", "OK", "W ABORT OK 0x0000001e"],
  ["DP write: CTRL/STAT, power-up request", "OK", "W CTRL/STAT OK 0x50000000"],
  ["DP read: CTRL/STAT, both power-up ACKs", "OK 0xF0000040", "R CTRL/STAT OK 0xf0000040"],
  ["DP write: SELECT AP 0, bank 0xF", "OK", "W SELECT OK 0x000000f0"],
  ["AP read: IDR (posted)", "OK", "R APc OK"],
  ["DP read: RDBUFF, the AP's IDR", "OK 0x04770031", "RDBUFF OK 0x04770031"],
  ["DP write: SELECT AP 0, bank 0", "OK", "W SELECT OK 0x00000000"],
  ["AP write: CSW, 32-bit transfers", "OK", "W AP0 OK 0xa2000002"],
  ["AP write: TAR, address 0x10", "OK", "W AP4 OK 0x00000010"],
  ["AP read: DRW (posted)", "OK", "R APc OK"],
  ["DP read: RDBUFF, the ROM word at 0x10", "OK 0x0301754D", "RDBUFF OK 0x0301754d"],
];

const Key = ({ c, t }: { c: string; t: string }) => (
  <span className="key"><i className="sw" style={{ background: cssColour(c) }} />{t}</span>
);

/** The picked wire, in words. */
function WireNote({ d, sel }: { d: Fpga; sel: number | null }) {
  const w = d.wires.find((x) => x.pico === sel);
  if (!w) return <p className="wire-note dim">Point at a wire, a pin, a hole or a row of the table to follow one wire.</p>;
  const hole = w.dock === "J3" ? `pin ${w.pin}, ${w.row} row` : `${w.row} row, column ${w.col}`;
  const role = ROLE[w.sig] ? ` (${ROLE[w.sig]})` : "";
  return (
    <p className="wire-note">
      <i className="sw" style={{ background: cssColour(wireColour(w)) }} />
      Pico pin {w.pico} · {d.pico[w.pico - 1]} → {w.dock}, {hole} · <b>{w.sig}{role}</b>
      {w.ball !== "GND" && <> · ball {w.ball}</>}
    </p>
  );
}

function WireTable({ d, sel, setSel }: { d: Fpga; sel: number | null; setSel: (p: number | null) => void }) {
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
              <tr key={w.dock + w.pin + w.sig} className={sel === w.pico ? "picked" : undefined}
                onMouseEnter={() => setSel(w.pico)} onMouseLeave={() => setSel(null)}>
                <td className="nowrap"><i className="sw" style={{ background: cssColour(wireColour(w)) }} />{wireColour(w)}</td>
                <td className="num mono">{w.pico} · {d.pico[w.pico - 1]}</td>
                <td className="mono">{w.sig}{ROLE[w.sig] ? ` ${ROLE[w.sig]}` : ""}</td>
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
                <td><span className={"pill " + (f.ok ? "ok" : f.uncertified ? "warn" : "bad")} title={f.note}>
                  {f.ok ? "match" : f.uncertified ? "no certificate" : "differs"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
