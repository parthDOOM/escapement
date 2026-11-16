// The Ethernet bench's breadboard circuits as built (fpga/tangprimer25k/README.md, Ethernet):
// the wires coming in from the Dock on the left, the patch cable's conductors to the laptop on
// the right. Transmit: TD+ and TD- through a series resistor and a coupling capacitor each to
// the laptop's receive pair. Receive: the laptop's transmit pair, terminated, through a coupling
// capacitor and a series resistor to RXP, biased to 0.94 V by 1.5 kΩ to 3V3 and 600 Ω to GND.

const STROKE = "var(--fg-2)";
const PART = "var(--brass)";
const GREEN = "var(--ok)";
const ORANGE = "var(--m3)";

/** A resistor box between (x1, y) and (x2, y), or between (x, y1) and (x, y2) when vertical. */
function Res({ x, y, len = 56, vertical = false, label, sub, side = "above" }:
  { x: number; y: number; len?: number; vertical?: boolean; label: string; sub?: string; side?: "above" | "below" | "left" | "right" }) {
  const w = 12;
  const box = vertical
    ? <rect x={x - w / 2} y={y} width={w} height={len} rx={2} fill="var(--bg)" stroke={PART} strokeWidth={1.8} />
    : <rect x={x} y={y - w / 2} width={len} height={w} rx={2} fill="var(--bg)" stroke={PART} strokeWidth={1.8} />;
  const lx = vertical ? (side === "left" ? x - 14 : x + 14) : x + len / 2;
  const ly = vertical ? y + len / 2 + 4 : side === "below" ? y + 24 : y - 14;
  const anchor = vertical ? (side === "left" ? "end" : "start") : "middle";
  return (
    <g>
      {box}
      <text x={lx} y={ly} fontSize={13} fontWeight={600} fill="var(--fg)" textAnchor={anchor}>{label}</text>
      {sub && <text x={lx} y={vertical ? ly + 15 : y + 22} fontSize={11} fill="var(--fg-3)" textAnchor={anchor}>{sub}</text>}
    </g>
  );
}

/** A capacitor: two plates across a horizontal wire at x (gap 10), or a vertical one. */
function Cap({ x, y, vertical = false, label, side = "above" }:
  { x: number; y: number; vertical?: boolean; label: string; side?: "above" | "below" | "right" }) {
  const p = 13;
  return (
    <g stroke={PART} strokeWidth={2.4}>
      {vertical
        ? <><line x1={x - p} y1={y} x2={x + p} y2={y} /><line x1={x - p} y1={y + 10} x2={x + p} y2={y + 10} /></>
        : <><line x1={x} y1={y - p} x2={x} y2={y + p} /><line x1={x + 10} y1={y - p} x2={x + 10} y2={y + p} /></>}
      <text x={vertical ? x + 20 : x + 5} y={vertical ? y + 9 : side === "below" ? y + 32 : y - 20} stroke="none"
        fontSize={13} fontWeight={600} fill="var(--fg)" textAnchor={vertical ? "start" : "middle"}>{label}</text>
    </g>
  );
}

const Dot = ({ x, y }: { x: number; y: number }) => <circle cx={x} cy={y} r={3.6} fill={STROKE} />;
const W = ({ d, c = STROKE, dash }: { d: string; c?: string; dash?: string }) =>
  <path d={d} fill="none" stroke={c} strokeWidth={1.8} strokeDasharray={dash} strokeLinejoin="round" />;

/** An incoming wire's end on the Dock side: a pin ring and its two-line label. */
function DockPin({ y, sig, where }: { y: number; sig: string; where: string }) {
  return (
    <g>
      <circle cx={232} cy={y} r={5} fill="var(--bg)" stroke={STROKE} strokeWidth={1.8} />
      <text x={218} y={y - 2} fontSize={13} fontWeight={600} fill="var(--fg)" textAnchor="end">{sig}</text>
      <text x={218} y={y + 14} fontSize={11} fill="var(--fg-3)" textAnchor="end">{where}</text>
    </g>
  );
}

/** A cable conductor's end on the laptop side. */
function CablePin({ y, c, stripe, name, role }: { y: number; c: string; stripe: boolean; name: string; role: string }) {
  return (
    <g>
      <line x1={800} y1={y} x2={836} y2={y} stroke={c} strokeWidth={5} strokeLinecap="round" />
      {stripe && <line x1={800} y1={y} x2={836} y2={y} stroke="var(--bg)" strokeWidth={5} strokeDasharray="4 5" />}
      <text x={848} y={y - 2} fontSize={13} fontWeight={600} fill="var(--fg)">{name}</text>
      <text x={848} y={y + 14} fontSize={11} fill="var(--fg-3)">{role}</text>
    </g>
  );
}

export default function EthCircuit() {
  // rows
  const tdp = 84, tdn = 148, rail = 268, sig = 330, ow = 330, or_ = 446, gnd = 506;
  const D = 420, A = 760;
  return (
    <svg className="fig-svg" viewBox="0 0 1110 560" role="img"
      aria-label="The Ethernet bench's breadboard circuits. Transmit: TD+ from J6 top column 6 through 47 ohms and 100 nanofarads to the cable's green-white wire, the laptop's RX+; TD- from J6 top column 5 the same way to the green wire, RX-. Receive: the cable's orange-white (laptop TX+) and orange (TX-) wires with 100 ohms across them; orange-white through 100 nanofarads and 100 ohms to RXP on J6 top column 4, which is biased by 1.5 kilohms to 3V3 from J6 column 1 and by 1 kilohm in parallel with 1.5 kilohms to ground from J4 bottom column 2; orange through 100 nanofarads to ground.">
      {/* the two sides */}
      <text x={20} y={30} fontSize={12} fill="var(--fg-3)" letterSpacing="0.08em">FROM THE DOCK</text>
      <text x={800} y={30} fontSize={12} fill="var(--fg-3)" letterSpacing="0.08em">PATCH CABLE TO THE LAPTOP</text>
      <line x1={800} y1={44} x2={800} y2={470} stroke="var(--line)" strokeWidth={1.4} strokeDasharray="3 5" />

      {/* transmit */}
      <text x={420} y={30} fontSize={12} fill="var(--brass)" letterSpacing="0.08em" textAnchor="middle">TRANSMIT · eth_tx</text>
      <DockPin y={tdp} sig="TD+ · uio[0]" where="J6 top row, col 6" />
      <DockPin y={tdn} sig="TD− · uio[1]" where="J6 top row, col 5" />
      <W d={`M237 ${tdp} H330`} /><Res x={330} y={tdp} label="47 Ω" />
      <W d={`M386 ${tdp} H520`} /><Cap x={520} y={tdp} label="100 nF" />
      <W d={`M530 ${tdp} H800`} c={GREEN} />
      <W d={`M237 ${tdn} H330`} /><Res x={330} y={tdn} label="47 Ω" side="below" />
      <W d={`M386 ${tdn} H520`} /><Cap x={520} y={tdn} label="100 nF" side="below" />
      <W d={`M530 ${tdn} H800`} c={GREEN} />
      <text x={848} y={(tdp + tdn) / 2 + 4} fontSize={11} fill="var(--fg-3)">twisted pair</text>
      <CablePin y={tdp} c={GREEN} stripe name="green-white · pin 3" role="laptop RX+" />
      <CablePin y={tdn} c={GREEN} stripe={false} name="green · pin 6" role="laptop RX−" />

      <line x1={20} y1={200} x2={1090} y2={200} stroke="var(--line)" strokeWidth={1} />

      {/* receive */}
      <text x={420} y={226} fontSize={12} fill="var(--brass)" letterSpacing="0.08em" textAnchor="middle">RECEIVE · eth_rx</text>
      <DockPin y={rail} sig="3V3" where="J6 col 1 (this resistor only)" />
      <DockPin y={sig} sig="RXP · uio[2]" where="J6 top row, col 4" />
      <DockPin y={gnd} sig="GND" where="J4 bottom row, col 2" />
      {/* 3V3 rail and the pull-up */}
      <W d={`M237 ${rail} H${D}`} />
      <W d={`M${D} ${rail} V${rail + 6}`} /><Res x={D} y={rail + 6} len={40} vertical label="1.5 kΩ" side="right" />
      <W d={`M${D} ${rail + 46} V${sig}`} />
      {/* RXP node D */}
      <W d={`M237 ${sig} H${D}`} /><Dot x={D} y={sig} />
      <text x={D - 12} y={sig + 22} fontSize={12} fontWeight={600} fill="var(--brass)" textAnchor="end">≈ 0.94 V idle</text>
      {/* pull-down: 1 kΩ ∥ 1.5 kΩ */}
      <W d={`M${D} ${sig} V378`} /><Dot x={D} y={378} />
      <W d={`M${D - 30} 378 H${D + 30}`} />
      <W d={`M${D - 30} 378 V388`} /><Res x={D - 30} y={388} len={48} vertical label="1 kΩ" side="left" />
      <W d={`M${D + 30} 378 V388`} /><Res x={D + 30} y={388} len={48} vertical label="1.5 kΩ" side="right" />
      <W d={`M${D - 30} 436 V${gnd}`} /><W d={`M${D + 30} 436 V${gnd}`} />
      <Dot x={D - 30} y={gnd} /><Dot x={D + 30} y={gnd} />
      {/* signal: D - 100 Ω - C - C3 - A */}
      <W d={`M${D} ${sig} H480`} /><Res x={480} y={sig} len={56} label="100 Ω" sub="series" />
      <W d={`M536 ${sig} H660`} /><Cap x={660} y={sig} label="100 nF" />
      <W d={`M670 ${sig} H${A}`} /><Dot x={A} y={sig} />
      <W d={`M${A} ${ow} H800`} c={ORANGE} />
      {/* termination across the pair */}
      <W d={`M${A} ${ow} V362`} /><Res x={A} y={362} len={52} vertical label="100 Ω" sub="termination" side="left" />
      <W d={`M${A} 414 V${or_}`} /><Dot x={A} y={or_} />
      <W d={`M${A} ${or_} H800`} c={ORANGE} />
      {/* orange to GND through C4 */}
      <W d={`M${A} ${or_} H680 V462`} /><Cap x={680} y={462} vertical label="100 nF" />
      <W d={`M680 472 V${gnd}`} /><Dot x={680} y={gnd} />
      {/* ground rail */}
      <W d={`M237 ${gnd} H680`} />
      <CablePin y={ow} c={ORANGE} stripe name="orange-white · pin 1" role="laptop TX+" />
      <CablePin y={or_} c={ORANGE} stripe={false} name="orange · pin 2" role="laptop TX−" />
      <text x={848} y={(ow + or_) / 2 + 4} fontSize={11} fill="var(--fg-3)">twisted pair</text>
    </svg>
  );
}
