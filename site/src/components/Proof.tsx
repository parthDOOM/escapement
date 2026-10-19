import { useState } from "react";

const P: [string, string, string, string, string][] = [
  ["P2", "Never late", "No event queue head is ever past its deadline, in any reachable state, including the 32-bit counter wrap.", "unbounded, assume-guarantee", "time"],
  ["P6", "Admitted means ahead", "Every admitted event is strictly after its commit tick and after the last event already queued.", "unbounded, PDR", "time"],
  ["P7", "Fixed slots", "A commit always belongs to the context that owns that slot, so no context can steal another's time.", "unbounded, PDR", "time"],
  ["P1", "Open drain stays open", "An open-drain pin is never driven high, whatever the firmware does.", "unbounded, PDR", "pins"],
  ["P3 · P5", "Pins stay owned", "A pin no context owns is never driven, and a faulted context drives none of its pins, in either bank.", "unbounded, PDR", "pins"],
  ["P4 · H1–H4", "Queues stay sane", "Queue occupancy stays in bounds; queued deadlines are ahead of now and strictly increasing.", "unbounded, PDR and k-induction", "time"],
  ["C1–C3", "Capture stays in bounds", "The capture unit never writes outside its region, never stages more than two records, and records only during a run.", "unbounded, PDR", "memory"],
  ["C4", "Streaming never overwrites", "While the host streams a capture, the capture unit never writes over a record the host has not yet acknowledged.", "unbounded, PDR", "memory"],
  ["G1–G3", "Clock gates are safe", "A gated register bank equals an enable flip-flop at every instant, and its clock never glitches.", "unbounded, k-induction", "power"],
  ["S1", "Configuration is frozen", "Enabled contexts, entry points, pin ownership and pin banks never change while the chip runs.", "unbounded, on the host interface", "pins"],
  ["≡", "Netlist equals RTL", "The routed netlist is sequentially equivalent to the RTL for every input sequence after reset.", "ABC dprove, 3 minutes", "silicon"],
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
          <tr><td>Random programs against the model, three campaigns</td><td className="num">2,300</td><td>0 mismatches</td></tr>
          <tr><td>Random programs with the host reading the tape during the run</td><td className="num">60</td><td>7,598 words, 0 mismatches</td></tr>
          <tr><td>Commits compared tick by tick</td><td className="num">155,416</td><td>0 mismatches</td></tr>
          <tr><td>Firmware with independent bus models</td><td className="num">15</td><td>all decoded</td></tr>
          <tr><td>Planted RTL faults (mutants)</td><td className="num">35</td><td>35 caught</td></tr>
          <tr><td>Software tests: assembler, simulator, certificates, models</td><td className="num">293</td><td>pass</td></tr>
          <tr><td>Chip tests on the final gate-level netlist</td><td className="num">11</td><td>pass</td></tr>
          <tr><td>Netlist against RTL, sequential equivalence</td><td className="num">332 bits</td><td>proved</td></tr>
        </tbody>
      </table>
    </div>
  );
}
