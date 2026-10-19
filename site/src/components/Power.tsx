// docs/15-POWER.md: the first v1.1 netlist, and v1.3 as built
const ROWS: [string, number, number][] = [["Sending UART", 8.61, 3.11], ["Logic analyser", 8.45, 2.77], ["Stopped", 7.99, 2.39]];
const MAX = 9;

export function Power() {
  return (
    <figure className="panel">
      <div className="bars">
        {ROWS.map(([n, a, b]) => (
          <div key={n} className="bar-pair">
            {[a, b].map((v, i) => (
              <div className="row" key={i}>
                <span>{i ? "" : n}</span>
                <div className="track"><div className={"fill " + (i ? "after" : "before")} style={{ width: (v / MAX * 100).toFixed(1) + "%" }} /></div>
                <span className="val">{v.toFixed(2)} mW</span>
              </div>
            ))}
          </div>
        ))}
      </div>
      <figcaption>Grey: the first design. Brass: the design as built.</figcaption>
    </figure>
  );
}

// Each tapeout candidate, from asic/reports/area.csv (the adopted runs only).
const VERSIONS = [
  { v: "v1.0", date: "11 Oct", cells: 28749, util: 55.6,
    what: "The first full chip: host link, four-context core, program memory." },
  { v: "v1.1", date: "15 Oct", cells: 23978, util: 55.2,
    what: "Adds the tape SRAM, capture and replay, and still ends 4,771 cells below v1.0: clock gating and a split hold margin. Metal1 carries signals." },
  { v: "v1.2", date: "19 Oct", cells: 25864, util: 58.9,
    what: "Pin bank B: five spare inputs and outputs become split pins, so four protocols run at once. Hold is repaired again after routing." },
  { v: "v1.3", date: "19 Oct", cells: 25931, util: 59.7,
    what: "Streaming capture: the host reads the tape during a run, so a capture lasts as long as the host keeps up, and PulseView records through the demo board." },
];

export function History() {
  const max = 30000;
  return (
    <figure className="panel history">
      <div className="hist">
        {VERSIONS.map((x) => (
          <div key={x.v} className="hist-col">
            <div className="hist-track">
              <div className="hist-bar" style={{ height: (x.cells / max * 100) + "%" }}>
                <span className="mono">{x.cells.toLocaleString()} cells</span>
              </div>
            </div>
            <b>{x.v}</b>
            <span className="dim small">{x.date} · {x.util}%</span>
            <p className="small">{x.what}</p>
          </div>
        ))}
      </div>
      <figcaption>Standard cells and utilisation of each adopted layout. Every run, adopted or rejected, is
        logged with its reason in <code>asic/reports/area.csv</code>.</figcaption>
    </figure>
  );
}
