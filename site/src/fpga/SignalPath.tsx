import type { Fpga, Wire } from "./types";

// Every signal from the Pico's GPIO through its wire and Dock connector to the FPGA ball and the
// chip's port. Rows are built from the wire list (tangprimer25k.cst + barepico.py).
type Row = { label: string; sigs: (w: Wire) => boolean; dir: "in" | "out" | "both"; use: string };
const ROWS: Row[] = [
  { label: "clk", sigs: (w) => w.sig === "clk", dir: "in", use: "25 MHz from the Pico's PWM, on the global clock network" },
  { label: "rst_n", sigs: (w) => w.sig === "rst_n", dir: "in", use: "reset, held low until the clock runs" },
  { label: "ui_in[7:0]", sigs: (w) => w.sig.startsWith("ui_in"), dir: "in", use: "[0] CS_n, [1] SCK, [2] MOSI: the host link; [7:3] bank B inputs" },
  { label: "uo_out[7:0]", sigs: (w) => w.sig.startsWith("uo_out"), dir: "out", use: "[0] MISO, [1] IRQ, [2] RUN; [7:3] bank B outputs" },
  { label: "uio[7:0]", sigs: (w) => w.sig.startsWith("uio"), dir: "both", use: "bank A: three-state, each pin driven only while its uio_oe bit is set" },
];
const bit = (s: string) => { const m = /\[(\d)\]/.exec(s); return m ? +m[1] : 0; };
/** GPIO numbers as ranges: 1,2,3,4 -> "GP1–GP4"; 9,18,19,20 -> "GP9, GP18–GP20". */
function ranges(g: number[]) {
  const s = [...g].sort((a, b) => a - b), out: string[] = [];
  for (let i = 0; i < s.length; ) {
    let j = i;
    while (j + 1 < s.length && s[j + 1] === s[j] + 1) j++;
    out.push(j - i >= 2 ? `GP${s[i]}–GP${s[j]}` : s.slice(i, j + 1).map((v) => "GP" + v).join(", "));
    i = j + 1;
  }
  return out.join(", ");
}

export default function SignalPath({ d }: { d: Fpga }) {
  const H = 78, top = 70, W = 980;
  const h = top + ROWS.length * H + 20;
  const x = { pico: 20, picoW: 200, wire0: 236, wire1: 420, fpga: 456, fpgaW: 504 };
  return (
    <svg className="fig-svg" viewBox={`0 0 ${W} ${h}`} role="img"
      aria-label="Signal path: each Pico W GPIO group, its wires and Dock connector, the FPGA balls it lands on, and the chip port it drives or reads.">
      <defs>
        <marker id="sp-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" fill="var(--brass)" />
        </marker>
      </defs>
      <rect x={x.pico} y={top - 40} width={x.picoW} height={h - top + 30} rx={10} fill="var(--bg)" stroke="var(--line)" />
      <text x={x.pico + 14} y={top - 16} fontSize={14} fontWeight={600} fill="var(--fg)">Pico W · host</text>
      <rect x={x.fpga} y={top - 40} width={x.fpgaW} height={h - top + 30} rx={10} fill="var(--bg)" stroke="var(--line)" />
      <text x={x.fpga + 14} y={top - 16} fontSize={14} fontWeight={600} fill="var(--fg)">GW5A-25 · escapement_board → tt_um_escapement</text>
      <text x={x.wire0 + 6} y={top - 16} fontSize={12} fill="var(--fg-3)">wires · Dock connector</text>
      {ROWS.map((r, i) => {
        const ws = d.wires.filter(r.sigs).sort((a, b) => bit(a.sig) - bit(b.sig));
        const y = top + i * H + 22;
        const gpios = ranges(ws.map((w) => w.gpio ?? 0));
        const balls = ws.map((w) => w.ball).join(" ");
        const dock = ws[0]?.dock;
        const where = dock === "J3" ? `J3 pin ${ws[0].pin}` : `${dock} Pmod`;
        const n = ws.length;
        return (
          <g key={r.label}>
            <text x={x.pico + 14} y={y} fontSize={13} fontWeight={600} fill="var(--brass)">{r.label}</text>
            <text x={x.pico + 14} y={y + 20} fontSize={11} fill="var(--fg-2)">{gpios}</text>
            <line x1={x.pico + x.picoW} y1={y + 4} x2={x.fpga} y2={y + 4} stroke="var(--brass)" strokeWidth={n > 1 ? 3 : 1.6}
              markerEnd={r.dir !== "out" ? "url(#sp-arr)" : undefined} markerStart={r.dir !== "in" ? "url(#sp-arr)" : undefined} />
            {n > 1 && <g><line x1={(x.wire0 + x.wire1) / 2 - 6} y1={y + 12} x2={(x.wire0 + x.wire1) / 2 + 6} y2={y - 4} stroke="var(--brass)" strokeWidth={1.4} />
              <text x={(x.wire0 + x.wire1) / 2 + 9} y={y - 4} fontSize={11} fill="var(--fg-2)">{n}</text></g>}
            <text x={x.wire0 + 6} y={y + 24} fontSize={11} fill="var(--fg-3)">{where}</text>
            <text x={x.fpga + 14} y={y} fontSize={11} fill="var(--fg-2)">balls {balls}</text>
            <text x={x.fpga + 14} y={y + 20} fontSize={12} fontWeight={600} fill="var(--fg)">{r.label}</text>
            <text x={x.fpga + 14} y={y + 36} fontSize={11} fill="var(--fg-3)">{r.use}</text>
          </g>
        );
      })}
    </svg>
  );
}
