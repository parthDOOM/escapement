import type { Fpga, Wire } from "./types";
import { cssColour, wireColour } from "./types";

// Top view of the bench, to scale (10 units per mm) from Sipeed's board file for the Dock and the
// Pico W datasheet: the Dock component side up, J3 on the far edge, the Pmod sockets facing a
// breadboard that holds the Pico W, USB to the right. The socket faces under the Dock's front edge
// are drawn as seen looking into the openings.
const DX = 100, DY = 60;                 // Dock origin; the board is 64.1 x 40.1 mm
const PX = 300, PT = 611, PB = 789;      // Pico: x of index 0, near row y, far row y
const PMOD_X: Record<string, number> = { J6: 28.4, J5: 257.0, J4: 485.6 };
const FT = DY + 426, FB = DY + 448;      // socket-face rows
const NEVER = new Set([30, 35, 36, 37, 39, 40]);
const colX = (s: string, c: number) => DX + PMOD_X[s] + (c - 1) * 25.4;
const picoX = (p: number) => (p <= 20 ? PX + (20 - p) * 25.4 : PX + (p - 21) * 25.4);
const picoY = (p: number) => (p <= 20 ? PT : PB);
const j3X = (n: number) => DX + 79 + Math.floor((n - 1) / 2) * 25.4;
const j3Y = (n: number) => (n % 2 ? DY + 46 : DY + 21);

export default function BenchLayout({ d }: { d: Fpga }) {
  const byPico = new Map<number, Wire>(d.wires.map((w) => [w.pico, w]));
  const hole = (s: string, row: "top" | "bottom", c: number) =>
    d.wires.find((w) => w.dock === s && w.row === row && w.col === c);
  const onJ3 = (n: number) => d.wires.find((w) => w.dock === "J3" && w.pin === n);
  const ink = "var(--pcb-ink)";
  const band = (dd: string, colour: string, width: number, dash?: string) => (
    <path d={dd} fill="none" stroke={colour} strokeWidth={width} strokeLinecap="round" strokeLinejoin="round"
      strokeDasharray={dash} opacity={0.28} />
  );
  const j4a = (picoX(11) + picoX(2)) / 2, j4b = (colX("J4", 2) + colX("J4", 6)) / 2;
  const j5a = (picoX(20) + picoX(13)) / 2, j5b = (colX("J5", 2) + colX("J5", 6)) / 2;
  const j6a = (picoX(23) + picoX(32)) / 2;
  return (
    <svg className="fig-svg" viewBox="0 0 880 900" role="img"
      aria-label="Top view of the bench: the Tang Primer 25K Dock with its 40-pin header J3 on the far edge and the Pmod sockets J6, J5 and J4 facing a breadboard with the Pico W; wire bundles run from the Pico to each socket.">
      {/* breadboard and Pico W */}
      <rect x={250} y={566} width={610} height={268} rx={8} fill="var(--bb)" stroke="var(--bb-line)" />
      <rect x={250} y={694} width={610} height={12} fill="var(--bb-line)" opacity={0.6} />
      <rect x={274} y={595} width={534} height={210} rx={6} fill="var(--pcb)" />
      <rect x={806} y={676} width={30} height={48} rx={3} fill="#9aa3a0" stroke="#666" />
      {/* the Dock */}
      <rect x={DX} y={DY} width={641} height={401} rx={10} fill="var(--pcb)" />
      <rect x={DX + 205} y={DY + 73} width={230} height={180} rx={4} fill="#26302b" stroke="#3c4a43" />
      <rect x={DX - 4} y={DY + 128} width={148} height={120} rx={3} fill="#9aa3a0" opacity={0.55} />
      <rect x={DX + 600} y={DY + 162} width={46} height={88} rx={4} fill="#9aa3a0" stroke="#666" />
      <rect x={DX + 578} y={DY + 72} width={40} height={30} rx={3} fill="#2f3a35" />
      <rect x={DX + 578} y={DY + 117} width={40} height={30} rx={3} fill="#2f3a35" />
      <rect x={DX + 65} y={DY + 8} width={510} height={52} rx={3} fill="var(--hole)" />
      {(["J6", "J5", "J4"] as const).map((s) => {
        const x0 = colX(s, 1) - 14, x1 = colX(s, 6) + 14;
        return (
          <g key={s}>
            <rect x={x0} y={DY + 262} width={x1 - x0} height={139} rx={2} fill="var(--hole)" />
            <rect x={x0} y={FT - 16} width={x1 - x0} height={46} rx={3} fill="var(--hole)" stroke="var(--line)" />
          </g>
        );
      })}

      {/* bundles */}
      {band(`M${j4a} ${PT - 8} C${j4a} ${PT - 60} ${j4b} ${FB + 70} ${j4b} ${FB + 8}`, "var(--m1)", 34)}
      {band(`M${j5a} ${PT - 8} C${j5a} ${PT - 60} ${j5b} ${FB + 70} ${j5b} ${FB + 8}`, "var(--ok)", 34)}
      {band(`M${picoX(21) + 12} ${PB} V${PB + 34} H232 V${PT - 34} C232 ${FB + 60} ${colX("J5", 3)} ${FB + 60} ${colX("J5", 3)} ${FB + 8}`, "var(--ok)", 12)}
      {band(`M${j6a} ${PB} V${PB + 62} H70 V${FB + 64} C70 ${FB + 30} ${colX("J6", 4)} ${FB + 60} ${colX("J6", 4)} ${FB + 8}`, "var(--code-l)", 34)}
      {band(`M${picoX(34)} ${PB} C${picoX(34)} ${PT - 10} ${picoX(2) - 20} ${PT - 20} ${picoX(1) - 20} ${PT - 30}`, "var(--brass)", 6, "10 7")}
      {band(`M${(picoX(1) + picoX(3)) / 2} ${PT} C${picoX(1) + 10} ${DY + 300} ${DX + 330} ${DY + 190} ${DX + 194} ${DY + 34}`, "var(--brass)", 16, "14 9")}
      <path d={`M${picoX(12)} ${PT} C${picoX(12)} ${PT - 70} ${colX("J6", 6)} ${FB + 80} ${colX("J6", 6)} ${FT}`}
        fill="none" stroke={cssColour("brown")} strokeWidth={3.5} />

      {/* Pico pins, J3 pins, socket holes */}
      {Array.from({ length: 40 }, (_, i) => i + 1).map((p) => {
        const w = byPico.get(p);
        return <circle key={p} cx={picoX(p)} cy={picoY(p)} r={7.5}
          fill={w ? cssColour(wireColour(w)) : "var(--pin)"}
          stroke={NEVER.has(p) ? "var(--bad)" : w && w.sig === "GND" ? "#cfcfcf" : "#333"} strokeWidth={NEVER.has(p) ? 2.5 : 1} />;
      })}
      {Array.from({ length: 40 }, (_, i) => i + 1).map((n) => {
        const w = onJ3(n);
        return <rect key={n} x={j3X(n) - 6} y={j3Y(n) - 6} width={12} height={12}
          fill={w ? cssColour(wireColour(w)) : n === 11 ? "var(--bad)" : "var(--pin)"}
          stroke={w || n === 11 ? "#fff" : "none"} strokeWidth={1.2} />;
      })}
      {(["J6", "J5", "J4"] as const).map((s) => (
        <g key={s}>
          {[1, 2, 3, 4, 5, 6].flatMap((c) => (["top", "bottom"] as const).map((row) => {
            const w = hole(s, row, c);
            return <circle key={c + row} cx={colX(s, c)} cy={row === "top" ? FT : FB} r={7}
              fill={w ? cssColour(wireColour(w)) : "#333"}
              stroke={c === 1 ? "var(--bad)" : w && w.sig === "GND" ? "#e0e0e0" : "#777"} strokeWidth={c === 1 ? 2.5 : 1} />;
          }))}
          <text x={colX(s, 1) - 14} y={DY + 292} fontSize={15} fontWeight={600} fill={ink}>{s}</text>
        </g>
      ))}

      {/* labels */}
      <text x={picoX(1) - 4} y={PT + 30} fontSize={12} fill={ink}>1</text>
      <text x={picoX(20) - 8} y={PT + 30} fontSize={12} fill={ink}>20</text>
      <text x={picoX(21) - 8} y={PB - 18} fontSize={12} fill={ink}>21</text>
      <text x={picoX(40) - 8} y={PB - 18} fontSize={12} fill={ink}>40</text>
      <text x={470} y={705} fontSize={18} fontWeight={600} fill={ink}>Pico W</text>
      <text x={842} y={705} fontSize={12} fill="var(--fg-3)">USB</text>
      <text x={258} y={826} fontSize={12} fill="var(--fg-3)">breadboard</text>
      <text x={DX + 4} y={22} fontSize={14} fontWeight={600} fill="var(--fg)">Tang Primer 25K Dock</text>
      <text x={DX + 4} y={44} fontSize={12} fill="var(--fg-3)">J3: pin 9 clk · 10 rst_n · 12 GND · 11 is +5V</text>
      <text x={DX + 52} y={DY + 51} fontSize={12} fill={ink}>1</text>
      <text x={DX + 52} y={DY + 26} fontSize={12} fill={ink}>2</text>
      <text x={DX + 582} y={DY + 26} fontSize={12} fill={ink}>40</text>
      <text x={DX + 222} y={DY + 168} fontSize={13} fill="#cfd8d2">GW5A-25 core module</text>
      <text x={DX + 8} y={DY + 196} fontSize={12} fill={ink}>USB-A (unused)</text>
      <text x={DX + 448} y={DY + 186} fontSize={12} fill={ink}>USB-C debugger →</text>
      <text x={DX + 448} y={DY + 228} fontSize={12} fontWeight={600} fill={ink}>clk · GND · rst_n</text>
      <text x={DX + 448} y={DY + 244} fontSize={12} fill={ink}>over the board → J3</text>
      <text x={j4a + 34} y={PT - 64} fontSize={13} fontWeight={600} fill="var(--fg)">J4 · ui_in</text>
      <text x={j5a - 40} y={PT - 64} fontSize={13} fontWeight={600} fill="var(--fg)">J5 · uo_out</text>
      <text x={20} y={PB + 92} fontSize={13} fontWeight={600} fill="var(--fg)">J6 · uio, round the left end</text>
      <text x={picoX(12) + 12} y={PT - 22} fontSize={12} fontWeight={600} fill={cssColour("brown")}>GP9 → J6</text>
    </svg>
  );
}
