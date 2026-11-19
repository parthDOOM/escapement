import { useState } from "react";

const P: [string, string, string, string, string][] = [
  ["P2", "Never late", "No event queue head is ever past its deadline, in any reachable state, including the 32-bit counter wrap.", "unbounded, assume-guarantee", "time"],
  ["P6", "Admitted means ahead", "Every admitted event is strictly after its commit tick and after the last event already queued.", "unbounded, PDR", "time"],
  ["B1–B9 · D1–D3", "Early values are exact", "The deadline tests, the decoded instruction, its operands and each queue's next head, computed a cycle before the commit that uses them, equal the same values on that commit's own state.", "unbounded, k-induction", "time"],
  ["P7", "Fixed slots", "A commit always belongs to the context that owns that slot, so no context can steal another's time.", "unbounded, PDR", "time"],
  ["P1", "Open drain stays open", "An open-drain pin is never driven high, whatever the firmware does.", "unbounded, PDR", "pins"],
  ["P3 · P5", "Pins stay owned", "A pin no context owns is never driven, and a faulted context drives none of its pins, in either bank.", "unbounded, PDR", "pins"],
  ["P4 · H1–H4", "Queues stay sane", "Queue occupancy stays in bounds; queued deadlines are ahead of now and strictly increasing.", "unbounded, PDR and k-induction", "time"],
  ["C1–C3", "Capture stays in bounds", "The capture unit never writes outside its region, never stages more than two records, and records only during a run.", "unbounded, PDR", "memory"],
  ["C4", "Streaming never overwrites", "While the host streams a capture, the capture unit never writes over a record the host has not yet acknowledged.", "unbounded, PDR", "memory"],
  ["L1", "The line unit stays in its lane", "The line unit claims only pins its owner owns, and is idle and unconfigured while the chip is stopped or once its owner has faulted.", "unbounded, PDR", "pins"],
  ["L2", "Early pin writes are exact", "The line unit's pin writes and transmit bit, decided a cycle before each boundary, equal what it would compute at the boundary itself.", "unbounded, k-induction", "pins"],
  ["G1–G3", "Clock gates are safe", "A gated register bank equals an enable flip-flop at every instant, and its clock never glitches.", "unbounded, k-induction", "power"],
  ["G4–G5", "Latches hold what was written", "Each latch register bank equals a flip-flop with an enable at every instant, because its input is always the value registered at the write edge.", "unbounded, k-induction and PDR", "power"],
  ["S1", "Configuration is frozen", "Enabled contexts, entry points, pin ownership and pin banks never change while the chip runs.", "unbounded, on the host interface", "pins"],
  ["C6", "Live reload is clean", "A program word the host writes during a run reaches the store intact and never meets a running context's fetch, and a free slot comes within four cycles.", "unbounded, induction and PDR", "memory"],
  ["NI", "Contexts cannot disturb each other", "Two chips that differ in every command and program word aimed at the other contexts give context j the same commits, state and pins, for a context that uses no shared unit.", "unbounded, k-induction with an invariant", "time"],
  ["M1–M7", "The monitor keeps true counts", "The bus monitor's counts never wrap and move by at most one, a rule write that keeps the rules changes none, its first-violation record holds until a start or a clear, and its latch banks hold what was written.", "unbounded, k-induction", "pins"],
  ["TW", "Tripwires never fire", "Twelve proved properties are also built into the chip as sticky flags; the bank of flags stays zero in every reachable state.", "unbounded, k-induction", "silicon"],
  ["≡", "Netlist equals RTL", "The routed netlist is sequentially equivalent to the RTL for every input sequence after reset.", "ABC dprove in two steps, about 6 minutes", "silicon"],
];
const KINDS = ["all", "time", "pins", "memory", "power", "silicon"];

export default function Proof() {
  const [k, setK] = useState("all");
  return (
    <>
      <div className="tabs small-tabs" role="tablist" aria-label="Filter properties">
        {KINDS.map((x) => (
          <button key={x} type="button" role="tab" className="tab" aria-selected={k === x} onClick={() => setK(x)}>{x}</button>
        ))}
      </div>
      <div className="cards">
        {P.filter((p) => k === "all" || p[4] === k).map(([id, t, d, how]) => (
          <div className="card" key={id}>
            <b>{id}</b>
            <h3>{t}</h3>
            <p>{d}</p>
            <span className="how">{how}</span>
          </div>
        ))}
      </div>
    </>
  );
}

export function ProofTable() {
  return (
    <div className="table-box panel">
      <table>
        <thead><tr><th>Check</th><th className="num">Size</th><th>Result</th></tr></thead>
        <tbody>
          <tr><td>Proof tasks: unbounded core proofs, other proofs and covers, bounded from reset to depth 20</td><td className="num">100</td><td>pass; 15 planted bugs each make their proof fail</td></tr>
          <tr><td>Random programs against the model, behavioural and standard-cell builds</td><td className="num">191 each</td><td>19,850 commits each, 0 mismatches</td></tr>
          <tr><td>Firmware cases with independent bus models, RTL against the model</td><td className="num">74</td><td>0 mismatches</td></tr>
          <tr><td>Line unit against its model, every output every cycle</td><td className="num">3.8 million edges</td><td>0 mismatches</td></tr>
          <tr><td>Planted RTL faults (mutants) across every module, standard-cell models</td><td className="num">1,010</td><td>892 caught, 57 proved equivalent, 61 excluded, 0 surviving</td></tr>
          <tr><td>Timing certificates against measured leads (74 cases, 74 contexts with a bound)</td><td className="num">74</td><td>59 exact, 0 below</td></tr>
          <tr><td>Planted firmware timing bugs: escert must refute every one that can fault, and what it certifies is attacked on the simulator</td><td className="num">719</td><td>172 refuted, 29 not proven, 518 certified, 0 certified that fault</td></tr>
          <tr><td>Software tests: assembler, simulator, certificates, models, bring-up, firmware at 33 to 50 MHz</td><td className="num">1,507</td><td>pass</td></tr>
          <tr><td>Outside judges: decoders written by others (sigrok, IRMP, Wireshark, rtl_433, rc-switch) read the pins, each with a control that must fail</td><td className="num">73 of 80 programs</td><td>agree; every control fails</td></tr>
          <tr><td>Firmware cases with every pin a tick slower each way (pads and board)</td><td className="num">74</td><td>pass</td></tr>
          <tr><td>Chip tests on the final gate-level netlist</td><td className="num">12</td><td>pass</td></tr>
          <tr><td>Firmware through the pins on the final netlist; SDF-annotated chip tests at three corners</td><td className="num">9 · 11 × 3</td><td>pass</td></tr>
          <tr><td>Netlist against RTL, sequential equivalence (two steps)</td><td className="num">336 bits</td><td>proved</td></tr>
        </tbody>
      </table>
    </div>
  );
}
