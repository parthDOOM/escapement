import { useState } from "react";

// From info.yaml. Each Tiny Tapeout design gets 8 inputs, 8 outputs and 8 bidirectional pins.
type Pin = { id: string; name: string; kind: "host" | "a" | "b" | "status"; about: string };
const IN: Pin[] = [
  { id: "ui[0]", name: "HOST_CS_N", kind: "host", about: "Host SPI chip select, active low. A command is one CS_N-low frame." },
  { id: "ui[1]", name: "HOST_SCK", kind: "host", about: "Host SPI clock, mode 0, up to f_clk / 8. Oversampled in the core clock: one clock domain." },
  { id: "ui[2]", name: "HOST_MOSI", kind: "host", about: "Host SPI data in: commands, memory images, mailbox words." },
  ...[0, 1, 2, 3, 4].map((k): Pin => ({ id: `ui[${k + 3}]`, name: `B${k}_IN`, kind: "b",
    about: `Bank B pin ${k}, input half. A bank B context reads it as its logical pin ${k}.` })),
];
const OUT: Pin[] = [
  { id: "uo[0]", name: "HOST_MISO", kind: "host", about: "Host SPI data out: status, read-backs, mailbox words, debug registers." },
  { id: "uo[1]", name: "IRQ", kind: "status", about: "High while any context has an interrupt pending (the IRQ instruction; the host clears it) or a fault has been recorded." },
  { id: "uo[2]", name: "RUN", kind: "status", about: "High while the chip runs: from START until STOP." },
  ...[0, 1, 2, 3, 4].map((k): Pin => ({ id: `uo[${k + 3}]`, name: `B${k}_OUT`, kind: "b",
    about: `Bank B pin ${k}, output half: push-pull, and high when no context drives it.` })),
];
const IO: Pin[] = [0, 1, 2, 3, 4, 5, 6, 7].map((k) => ({ id: `uio[${k}]`, name: `P${k}`, kind: "a",
  about: `Bank A pin ${k}: bidirectional, push-pull or open-drain per pin. Owned by at most one context, which drives it only through timed events.` }));
const KIND = { host: ["--m2", "host link"], status: ["--brass", "status"], a: ["--c1", "bank A protocol pin"], b: ["--c2", "bank B protocol pin"] };

export default function Pinout() {
  const [sel, setSel] = useState<Pin>(IO[0]);
  const col = (title: string, pins: Pin[]) => (
    <div className="pin-col">
      <h4>{title}</h4>
      {pins.map((p) => (
        <button key={p.id} type="button" className="pin" aria-pressed={sel.id === p.id}
          style={{ ["--k" as string]: `var(${KIND[p.kind][0]})` }}
          onMouseEnter={() => setSel(p)} onFocus={() => setSel(p)} onClick={() => setSel(p)}>
          <span className="mono dim">{p.id}</span><b className="mono">{p.name}</b>
        </button>
      ))}
    </div>
  );
  return (
    <figure className="panel pinout">
      <div className="pin-grid">
        {col("Inputs", IN)}
        {col("Outputs", OUT)}
        {col("Bidirectional", IO)}
      </div>
      <div className="pin-about" aria-live="polite">
        <b className="mono">{sel.id} · {sel.name}</b>
        <p>{sel.about}</p>
      </div>
      <div className="arch-key">
        {Object.values(KIND).map(([v, n]) => <span key={n}><i style={{ background: `var(${v})` }} />{n}</span>)}
      </div>
      <figcaption>Plus the clock (25 MHz), reset and enable that every Tiny Tapeout design gets. Four wires carry
        the host link and two report status; the other 18 are protocol pins.</figcaption>
    </figure>
  );
}
