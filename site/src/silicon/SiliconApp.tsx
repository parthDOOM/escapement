import { Component, lazy, Suspense } from "react";
import Header from "../components/Header";
import Ruler from "../components/Ruler";
import { History } from "../components/Power";
import { asset } from "../lib/util";

const Slice3D = lazy(() => import("../components/Slice3D"));
const REPO = "https://github.com/parthDOOM/escapement";
const SECTIONS = [["transistors", "Transistors"], ["history", "Six layouts"]] as const;

class Guard extends Component<{ children: React.ReactNode }, { err: string | null }> {
  state = { err: null as string | null };
  static getDerivedStateFromError(e: Error) { return { err: e.message }; }
  render() {
    return this.state.err
      ? <div className="loading" style={{ minHeight: 200 }}>This figure could not be drawn ({this.state.err}).</div>
      : this.props.children;
  }
}

// The layout in depth, moved here from the chip's page: the transistor close-up and the six
// layouts from v1.1 to v1.6. The chip's page keeps the die, its sign-off numbers and power.
export default function SiliconApp() {
  return (
    <>
      <Header sections={SECTIONS} page="silicon" />
      <main id="top">
        <section className="hero page-hero">
          <div className="wrap">
            <p className="eyebrow">Physical design · v1.6 · IHP SG13CMOS5L 130 nm · 6×4 tiles</p>
            <h1>The <em>layout</em></h1>
            <p className="lede">The die as it goes to silicon, cut down to its transistors, and the six layouts that
              led to it. The whole die, its sign-off numbers and its power are on <a href={asset("#silicon")}>the
              chip's page</a>.</p>
          </div>
        </section>
        <Ruler n={31} />
        <section id="transistors">
          <div className="wrap">
            <div className="cols">
              <Guard><Suspense fallback={<div className="loading" style={{ minHeight: 380 }}>loading…</div>}>
                <Slice3D />
              </Suspense></Guard>
              <div className="prose">
                <h3>Down to the transistors</h3>
                <p>The close-up is cut straight from the final layout. Green active area and red gate poly form the
                  transistors. Thousands of contacts and vias climb through Metal1 to Metal4, which carry the signals.
                  A standard cell is one row of that structure, 3.78 µm tall.</p>
                <p>Only four metal layers can carry signals in a Tiny Tapeout block on this process, and each SRAM
                  blocks three of them over its whole footprint. Routing Metal1 as a signal layer cut global-route
                  overflow from 5,276 to 76. Version 1.4 adds the line unit in the same area: most registers became
                  latches, 44% smaller than flip-flops, and the design ended smaller than version 1.3. Version 1.5
                  decides each deadline test a cycle before the commit that needs it, which moves the slow-corner
                  limit from 28.6 to 38.6 MHz. Version 1.6 decodes each instruction, reads its operands and decides
                  its pin writes a stage earlier: it is laid out for a 20 ns clock and rated up to 50 MHz.</p>
                <p>To save power, register banks that are not being written do not see the clock: 82 clock gates cover
                  3,292 of the 4,756 storage cells, and each memory is selected only on the cycles that read
                  it.</p>
              </div>
            </div>
          </div>
        </section>
        <Ruler n={32} />
        <section id="history" className="band">
          <div className="wrap">
            <div className="sec-head">
              <p className="eyebrow">History</p>
              <h2>Six layouts, one story</h2>
            </div>
            <History />
            <p className="more"><a href={`${REPO}/blob/master/docs/03-PHYSICAL-DESIGN.md`}>Physical design in full:
              floorplan, sign-off at every corner, power →</a></p>
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
