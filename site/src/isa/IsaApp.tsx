import Header from "../components/Header";
import Ruler from "../components/Ruler";
import Isa from "../components/Isa";
import { asset } from "../lib/util";

const REPO = "https://github.com/parthDOOM/escapement";
const SECTIONS = [["isa", "Instructions"]] as const;

// The instruction set explorer, moved here from the chip's page (it is reference material).
export default function IsaApp() {
  return (
    <>
      <Header sections={SECTIONS} page="isa" />
      <main id="top">
        <section className="hero page-hero">
          <div className="wrap">
            <p className="eyebrow">Instruction set · v1.6</p>
            <h1>Fifty-three <em>words</em></h1>
            <p className="lede">Every instruction is 32 bits: an 8-bit opcode and a 24-bit payload whose unused bits
              must be zero, so erased or uninitialised memory traps instead of running. The decoder below is the
              project's own Python, running in your browser. Programs written with these instructions run in
              the <a href={asset("#play")}>playground</a> on the chip's page.</p>
          </div>
        </section>
        <Ruler n={21} />
        <section id="isa" className="band">
          <div className="wrap">
            <div className="sec-head">
              <p className="eyebrow">Explorer</p>
              <h2>One word each</h2>
              <p className="lede">Pick an instruction, then click bits to see how the word decodes.</p>
            </div>
            <Isa />
            <p className="more"><a href={`${REPO}/blob/master/docs/02-ISA.md`}>The full reference, with every
              field, timing rule and fault →</a></p>
          </div>
        </section>
      </main>
      <footer>
        <div className="wrap">
          <span>Escapement · Apache-2.0 · an entry to the Jane Street protocol emulator competition</span>
          <span><a href={`${REPO}/tree/master/isa`}>The ISA source (isa/)</a></span>
        </div>
      </footer>
    </>
  );
}
