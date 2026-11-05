import { useState } from "react";

const P: [string, string, string, string, string][] = [
  ["P2", "Never late", "No event queue head is ever past its deadline, in any reachable state, including the 32-bit counter wrap.", "unbounded, assume-guarantee", "time"],
  ["P6", "Admitted means ahead", "Every admitted event is strictly after its commit tick and after the last event already queued.", "unbounded, PDR", "time"],
  ["B1–B9 · D1–D3", "Early values are exact", "The deadline tests (v1.5), and the decoded instruction, its operands and each queue's next head (v1.6), computed a cycle before the commit that uses them, equal the same values on that commit's own state.", "unbounded, k-induction", "time"],
  ["P7", "Fixed slots", "A commit always belongs to the context that owns that slot, so no context can steal another's time.", "unbounded, PDR", "time"],
  ["P1", "Open drain stays open", "An open-drain pin is never driven high, whatever the firmware does.", "unbounded, PDR", "pins"],
  ["P3 · P5", "Pins stay owned", "A pin no context owns is never driven, and a faulted context drives none of its pins, in either bank.", "unbounded, PDR", "pins"],
  ["P4 · H1–H4", "Queues stay sane", "Queue occupancy stays in bounds; queued deadlines are ahead of now and strictly increasing.", "unbounded, PDR and k-induction", "time"],
  ["C1–C3", "Capture stays in bounds", "The capture unit never writes outside its region, never stages more than two records, and records only during a run.", "unbounded, PDR", "memory"],
  ["C4", "Streaming never overwrites", "While the host streams a capture, the capture unit never writes over a record the host has not yet acknowledged.", "unbounded, PDR", "memory"],
  ["L1", "The line unit stays in its lane", "The line unit (v1.4) claims only pins its owner owns, and is idle and unconfigured while the chip is stopped or once its owner has faulted.", "unbounded, PDR", "pins"],
  ["L2", "Early pin writes are exact", "The line unit's pin writes and transmit bit, decided a cycle before each boundary (v1.6), equal what it would compute at the boundary itself.", "unbounded, k-induction", "pins"],
  ["G1–G3", "Clock gates are safe", "A gated register bank equals an enable flip-flop at every instant, and its clock never glitches.", "unbounded, k-induction", "power"],
  ["G4–G5", "Latches hold what was written", "Each latch register bank (v1.4) equals a flip-flop with an enable at every instant, because its input is always the value registered at the write edge.", "unbounded, k-induction and PDR", "power"],
  ["S1", "Configuration is frozen", "Enabled contexts, entry points, pin ownership and pin banks never change while the chip runs.", "unbounded, on the host interface", "pins"],
  ["≡", "Netlist equals RTL", "The routed netlist is sequentially equivalent to the RTL for every input sequence after reset.", "ABC dprove, about 4 minutes", "silicon"],
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
          <tr><td>Random programs against the model, four campaigns</td><td className="num">3,264</td><td>0 mismatches</td></tr>
          <tr><td>Commits compared tick by tick in those campaigns</td><td className="num">268,582</td><td>0 mismatches</td></tr>
          <tr><td>Random programs with the host reading the tape during the run</td><td className="num">60</td><td>7,598 words, 0 mismatches</td></tr>
          <tr><td>Firmware cases with independent bus models, RTL against the model</td><td className="num">43</td><td>0 mismatches</td></tr>
          <tr><td>Line unit against its model, every output every cycle, two builds</td><td className="num">8 million edges each</td><td>0 mismatches</td></tr>
          <tr><td>Planted RTL faults (mutants), v1.4 campaign on the standard-cell models</td><td className="num">710</td><td>653 caught, 19 proved equivalent, 38 excluded, 0 surviving</td></tr>
          <tr><td>Planted faults at every site of v1.5's new logic</td><td className="num">103</td><td>97 caught, 5 proved equivalent, 1 excluded, 0 surviving</td></tr>
          <tr><td>Planted faults at every site of the lines v1.6 changed, standard-cell models</td><td className="num">626</td><td>556 caught, 22 proved equivalent, 48 excluded, 0 surviving</td></tr>
          <tr><td>Timing certificates against measured leads (43 cases, 46 contexts)</td><td className="num">46</td><td>39 exact, 0 below</td></tr>
          <tr><td>Software tests: assembler, simulator, certificates, models, bring-up, firmware at 33 to 50 MHz</td><td className="num">980</td><td>pass</td></tr>
          <tr><td>Chip tests on the final gate-level netlist</td><td className="num">7</td><td>pass</td></tr>
          <tr><td>Firmware through the pins on the final netlist, NEC at 33 and 50 MHz included</td><td className="num">9</td><td>pass</td></tr>
          <tr><td>Netlist against RTL, sequential equivalence (two steps since v1.6)</td><td className="num">332 bits</td><td>proved</td></tr>
        </tbody>
      </table>
    </div>
  );
}
