import { Component, lazy, Suspense, useEffect, useState } from "react";
import Header from "./components/Header";
import Ruler from "./components/Ruler";
import Deadline from "./components/Deadline";
import Arch from "./components/Arch";
import Barrel from "./components/Barrel";
import Isa from "./components/Isa";
import Proof, { ProofTable } from "./components/Proof";
import CiResults from "./components/CiResults";
import { History, Power } from "./components/Power";
import Pinout from "./components/Pinout";
import { scrollToSection, useVisible } from "./lib/util";

// the heavy parts load when they come near the screen
const Die3D = lazy(() => import("./components/Die3D"));
const DieZoom = lazy(() => import("./components/DieZoom"));
const Slice3D = lazy(() => import("./components/Slice3D"));
const Playground = lazy(() => import("./playground/Playground"));
const Learn = lazy(() => import("./playground/Learn"));

// one figure failing must not take the page down with it
class Guard extends Component<{ children: React.ReactNode }, { err: string | null }> {
  state = { err: null as string | null };
  static getDerivedStateFromError(e: Error) { return { err: e.message }; }
  render() {
    return this.state.err
      ? <div className="loading" style={{ minHeight: 200 }}>This figure could not be drawn ({this.state.err}).</div>
      : this.props.children;
  }
}

// Mounted when near the screen, or anyway once the page has settled, so that sections
// below keep their real heights and links into the page land where they should.
let idle = false;
const idleSubs = new Set<() => void>();
if (typeof window !== "undefined") {
  const go = () => { idle = true; idleSubs.forEach((f) => f()); };
  addEventListener("load", () => setTimeout(go, 1500), { once: true });
}

function Later({ children, h = 360 }: { children: React.ReactNode; h?: number }) {
  const [ref, near] = useVisible<HTMLDivElement>(false, "600px");
  const [settled, setSettled] = useState(idle);
  useEffect(() => {
    if (idle) return;
    const f = () => setSettled(true);
    idleSubs.add(f);
    return () => { idleSubs.delete(f); };
  }, []);
  const vis = near || settled;
  return (
    <div ref={ref} style={vis ? undefined : { minHeight: h }}>
      {vis && <Guard><Suspense fallback={<div className="loading" style={{ minHeight: h }}>loading…</div>}>{children}</Suspense></Guard>}
    </div>
  );
}

export function tryInPlayground(sid: string, edit?: [string, string]) {
  // the playground may not be mounted yet: it picks the request up when it is
  (window as any).__escPlay = { sid, edit };
  window.dispatchEvent(new CustomEvent("esc:play"));
  scrollToSection("play");
}

export default function App() {
  return (
    <>
      <Header />
      <main id="top">
        <section className="hero">
          <div className="wrap hero-grid">
            <div>
              <p className="eyebrow">Protocol emulator ASIC · IHP SG13CMOS5L 130 nm · Tiny Tapeout 6×4</p>
              <h1>Escape<em>ment</em></h1>
              <p className="claim">No pin edge is ever late, whatever program runs.
                <a href="#proof">Proved for every program, on the netlist that goes to silicon.</a></p>
              <p className="lede">A chip that speaks UART, SPI, I2C, CAN, JTAG, ARM SWD, low-speed USB and more, with
                none of them built into the hardware. Firmware schedules every pin change for an exact 40 ns tick. The chip makes
                that tick, or refuses the change when it is queued. The guarantee is in the silicon, not in a tool
                that vets programs: one nobody has checked still cannot make a late edge.</p>
              <div className="hero-cta">
                <a className="run-btn" href="#play">Run the firmware</a>
                <a className="icon-btn" href="#inside">How it works</a>
              </div>
              <div className="facts">
                <div><b>40 ns</b><span>one tick at 25 MHz</span></div>
                <div><b>4</b><span>programs running at once</span></div>
                <div><b>13</b><span>protocol pins, two banks</span></div>
                <div><b>31</b><span>firmware programs</span></div>
                <div><b>3.1 mW</b><span>sending UART</span></div>
                <div><b>0</b><span>late edges, for any program, for all time</span></div>
              </div>
            </div>
            <Later h={420}><Die3D /></Later>
          </div>
        </section>

        <Ruler n={1} />

        <section id="time">
          <div className="wrap">
            <div className="sec-head">
              <p className="eyebrow">The one promise</p>
              <h2>Early or refused, never late</h2>
            </div>
            <div className="cols">
              <div className="prose">
                <p>A microcontroller that bit-bangs a protocol is always a little late: an interrupt, a cache miss or
                  a busy loop pushes an edge out, and nobody is told. Escapement separates <strong>deciding</strong> an
                  edge from <strong>making</strong> it.</p>
                <p>An instruction such as <code>EMIT</code> does not touch the pin. It puts an event into its
                  context's four-entry queue with an absolute deadline taken from the context's timer. A timing unit
                  applies the event on the clock edge whose tick equals that deadline, to the tick.</p>
                <p>The check happens when the event is queued. The deadline must be strictly after the commit tick and
                  strictly after the last event already queued. If it is not, the event is refused, the context stops
                  with a fault that names the reason, its pins are released, and the other contexts carry on. A late
                  edge cannot happen, and that is proved formally.</p>
                <p>Drag the deadline in the figure to try it, or{" "}
                  <button type="button" className="link" onClick={() => tryInPlayground("blank", [".equ DELAY, 10", ".equ DELAY, 9"])}>
                    make a real program fall behind</button>.</p>
              </div>
              <Deadline />
            </div>
            <div className="cols" style={{ marginTop: "2.5rem" }}>
              <div className="prose">
                <h3>Time is the only thing in hardware</h3>
                <p>Nothing on the chip knows what UART or I2C is. The silicon offers three kinds of timed event:
                  <strong> DRIVE</strong> sets pins, <strong>EXPECT</strong> checks that an input holds a level or
                  arrives at it within a window of up to 127 ticks, and <strong>SAMPLE</strong> captures one input bit.
                  Everything else is a program in the 256-word program memory. Version 1.4 adds one more timed
                  event, a packet, for protocols whose bits come too fast for a program to code by hand.</p>
              </div>
              <div className="prose">
                <h3>Proved before it runs</h3>
                <p>Because the deadline is absolute, a program can compute it, reuse it and check it. A static
                  analyser, <code>escert</code>, walks every path of a firmware and proves a lower bound on how early
                  each event is queued. The chip measures the same quantity while it runs, so the certificate and the
                  silicon check each other. The playground below shows both, side by side.</p>
              </div>
            </div>
          </div>
        </section>

        <Ruler n={2} />

        <section id="inside" className="band">
          <div className="wrap">
            <div className="sec-head">
              <p className="eyebrow">Architecture</p>
              <h2>One timebase, four programs</h2>
              <p className="lede">Select a block to see what it does.</p>
            </div>
            <Arch />
            <div className="sec-head" style={{ marginTop: "4rem" }}>
              <h3>The barrel</h3>
              <p className="dim">Four contexts share one pipeline in fixed slots. Context <i>k</i> fetches in every
                cycle where <i>t</i> mod 4 = <i>k</i> and commits three cycles later. There are no stalls between
                contexts, so a slow protocol in one context never moves another context's edges. Below are the first
                160 ticks of a real run with four protocols at once.</p>
            </div>
            <Barrel />
            <div className="cols" style={{ marginTop: "3.5rem" }}>
              <div className="prose">
                <h3>The line unit: packets as events</h3>
                <p>Low-speed USB at 24 MHz leaves a program four instructions per bit, too few for NRZI coding, bit
                  stuffing and a running CRC-16. Version 1.4 gives one context a line unit that does that bit
                  work. <code>SEND</code> queues a packet like any other event, checked when it is queued and sent on its
                  exact tick; a receive records the tick the packet ended, so a reply can leave a fixed number of
                  ticks after the host's packet, whatever ran in between.</p>
                <p>The unit knows no protocol. Three words in the tape set the pins, the coding, the stuffing rule,
                  the bit period and any CRC up to 16 bits, so USB and CAN frames are configurations of it. While it
                  is idle its clocks are gated off.</p>
              </div>
              <div className="prose">
                <h3>A USB keyboard in 187 instructions</h3>
                <p>The keyboard firmware enumerates on a real Linux kernel, through USB/IP, with the simulated chip
                  on the other end. The timing checker proves that every reply is scheduled exactly 72 ticks after the
                  end of the host's packet; at the pins that is 75 ticks, 4.69 bit times, inside USB's 2 to 6.5.</p>
                <p><button type="button" className="link" onClick={() => tryInPlayground("usb")}>
                  Type through it in the playground</button>.</p>
              </div>
            </div>
          </div>
        </section>

        <Ruler n={3} />

        <section id="play">
          <div className="wrap">
            <div className="sec-head">
              <p className="eyebrow">Firmware, not hardware</p>
              <h2>Protocols are programs</h2>
              <p className="lede">Each protocol below is real firmware on the chip's instruction-set simulator, wired to
                an independent model of the device on the other end. Edit the source, change what the device sends, and
                run it: the assembler, the timing certificate and the simulator are the project's own, running in your
                browser.</p>
            </div>
            <Later h={900}><Playground /></Later>

            <div className="sub-head learn-intro" id="learn">
              <p className="eyebrow">Record it, speak it</p>
              <h3>A protocol from a recording</h3>
              <p>Point the chip at a transmitter it has never seen. It records the waveform, the
                host works out the protocol's timing table, and generates firmware that escert certifies. The chip
                then sends new messages in that protocol, such as a command the remote never sent. The recordings below
                are real remotes, from sigrok-dumps; the same loop runs on the chip's RTL in CI.</p>
            </div>
            <Later h={700}><Learn /></Later>
          </div>
        </section>

        <Ruler n={4} />

        <section id="isa" className="band">
          <div className="wrap">
            <div className="sec-head">
              <p className="eyebrow">Instruction set</p>
              <h2>Fifty-three instructions, one word each</h2>
              <p className="lede">Every instruction is 32 bits: an 8-bit opcode and a 24-bit payload whose unused bits
                must be zero, so erased or uninitialised memory traps instead of running. Pick an instruction, then
                click bits to see how the word decodes.</p>
            </div>
            <Isa />
          </div>
        </section>

        <Ruler n={5} />

        <section id="proof">
          <div className="wrap">
            <div className="sec-head">
              <p className="eyebrow">Verification</p>
              <h2>Every claim is checked twice</h2>
              <p className="lede">Tests show that something works on the cases tried. The properties that matter most
                are proved for every reachable state instead.</p>
            </div>
            <Proof />
            <div className="sub-head" id="results">
              <h3>Checked on every change</h3>
              <p>Each proof and test runs in GitHub Actions on every change it depends on. These are the latest runs on
                master, with their commits and logs.</p>
            </div>
            <CiResults />
            <div className="cols" style={{ marginTop: "3rem" }}>
              <div className="prose">
                <h3>Two models in lockstep</h3>
                <p>The RTL and an independent Python model of the machine run side by side and are compared on every
                  tick: each commit, every register, every pin, the queues and the memories. Thousands of random
                  programs and every firmware agree with zero mismatches. That Python model is the simulator in the
                  playground above.</p>
                <p>A 32-bit <strong>execution digest</strong> folds every commit, register write and pin change of a
                  run into one word. After a run on silicon, one SPI read compares the chip with the model's
                  prediction.</p>
                <p>To check the tests themselves, 710 faults are planted in the RTL one at a time, across every
                  module, the line unit included, and simulated on the PDK's standard-cell models. The tests catch 653. Of
                  the rest, 19 are proved to change no output of the chip, and 38 are excluded with a written reason,
                  such as logic a proof shows can never run. None survives. Every fault that slipped through an
                  earlier run was read against the RTL, and each hole it showed was closed with a new test.</p>
              </div>
              <ProofTable />
            </div>
          </div>
        </section>

        <Ruler n={6} />

        <section id="silicon" className="band">
          <div className="wrap">
            <div className="sec-head">
              <p className="eyebrow">Physical design</p>
              <h2>The die</h2>
              <p className="lede">Six by four Tiny Tapeout tiles, laid out with LibreLane and OpenROAD on IHP's open
                130 nm process. Zoom in until the wires become separate, or switch on the blocks to see where the four
                contexts ended up.</p>
            </div>
            <Later h={520}><DieZoom /></Later>

            <div className="cols" style={{ marginTop: "3.5rem" }}>
              <Later h={380}><Slice3D /></Later>
              <div className="prose">
                <h3>Down to the transistors</h3>
                <p>The close-up is cut straight from the final layout. Green active area and red gate poly form the
                  transistors. Thousands of contacts and vias climb through Metal1 to Metal4, which carry the signals.
                  A standard cell is one row of that structure, 3.78 µm tall.</p>
                <p>Only four metal layers can carry signals in a Tiny Tapeout block on this process, and each SRAM
                  blocks three of them over its whole footprint. Routing Metal1 as a signal layer cut global-route
                  overflow from 5,276 to 76. Version 1.4 adds the line unit in the same area: most registers became
                  latches, 44% smaller than flip-flops, and the design ended smaller than version 1.3.</p>
                <p>To save power, register banks that are not being written do not see the clock: 80 clock gates cover
                  about 3,150 of the 4,374 storage cells, and each memory is selected only on the cycles that read
                  it.</p>
              </div>
            </div>

            <div className="stats" style={{ marginTop: "3rem" }} aria-label="Sign-off results">
              <div><b>27,972</b><span>standard cells</span></div>
              <div><b>58.2%</b><span>utilisation</span></div>
              <div><b>2.22 m</b><span>of wire</span></div>
              <div><b>243,088</b><span>vias</span></div>
              <div><b>+0.138 ns</b><span>worst hold slack, fast corner</span></div>
              <div><b>0</b><span>DRC, LVS and antenna errors</span></div>
            </div>

            <div className="cols" style={{ marginTop: "3rem" }}>
              <div className="prose">
                <h3>Power: measure, then cut what dominates</h3>
                <p>The first netlist used 93% of its busy power while doing nothing, all of it clocking flip-flops and
                  reading memories every cycle. Clock gating, on-demand memory selects and a split hold margin cut every
                  workload by about two thirds.</p>
                <p>Figures are from gate-level simulation at the typical corner, with the memory energy counted edge by
                  edge.</p>
              </div>
              <Power />
            </div>
            <div style={{ marginTop: "3rem" }}>
              <h3 style={{ marginBottom: "1rem" }}>Five layouts, one story</h3>
              <History />
            </div>
          </div>
        </section>

        <Ruler n={7} />

        <section id="use">
          <div className="wrap">
            <div className="sec-head">
              <p className="eyebrow">Using the chip</p>
              <h2>Bring it up</h2>
            </div>
            <div className="cols">
              <ol className="steps">
                <li><p><strong>Load.</strong> Over a four-wire SPI link, write the program memory, the tape and the
                  configuration: which contexts run, where each starts, which pins each owns.</p></li>
                <li><p><strong>Run.</strong> START begins all contexts on the same tick. Each talks to the host through
                  its own two-word mailboxes, for example the bytes a UART should send.</p></li>
                <li><p><strong>Watch.</strong> While it runs, read any context's registers, queue telemetry and checker
                  results, or let the capture unit record every edge into the tape.</p></li>
                <li><p><strong>Check.</strong> After STOP, read the execution digest and compare it with the simulator's
                  prediction for the same program and inputs.</p></li>
              </ol>
              <div className="prose">
                <p>A staged bring-up script for the Tiny Tapeout demo board's RP2350 tests the link, both memories,
                  every pin and every firmware in order, and says what each failure most likely means.</p>
                <p>The chip is also an instrument. Its capture unit timestamps every change of the watched pins to the
                  40 ns tick, and the host reads the records during the run, so a capture lasts as long as the host
                  keeps up and a record is never overwritten before it has been read. On the demo board, PulseView and
                  sigrok-cli record from the chip through their existing Openbench Logic Sniffer driver.</p>
                <p>Everything is open: RTL, firmware, assembler, simulator, certificate checker, proofs and the flow
                  configuration.</p>
                <ul className="links">
                  <li><a href="https://github.com/parthDOOM/escapement">Source: github.com/parthDOOM/escapement</a></li>
                  <li><a href="https://github.com/parthDOOM/escapement/blob/master/docs/02-ISA.md">ISA reference</a></li>
                  <li><a href="https://github.com/parthDOOM/escapement/blob/master/docs/14-FIRMWARE-GUIDE.md">Firmware guide</a></li>
                  <li><a href="https://github.com/parthDOOM/escapement/blob/master/docs/16-BRING-UP.md">Bring-up</a></li>
                  <li><a href="https://github.com/parthDOOM/escapement/blob/master/docs/17-INSTRUMENT.md">Capture, streaming and PulseView</a></li>
                  <li><a href="https://github.com/parthDOOM/escapement/blob/master/docs/19-LINE-UNIT.md">Line unit and the USB keyboard</a></li>
                </ul>
              </div>
            </div>
            <div style={{ marginTop: "3rem" }}>
              <h3 style={{ marginBottom: "1rem" }}>The pins</h3>
              <Pinout />
            </div>
          </div>
        </section>
      </main>
      <footer>
        <div className="wrap">
          <span>Escapement · Apache-2.0 · an entry to the Jane Street protocol emulator competition</span>
          <span>SRAM macros from IHP-Open-PDK · built with Tiny Tapeout, LibreLane and OpenROAD</span>
        </div>
      </footer>
    </>
  );
}
