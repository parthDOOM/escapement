// The SWD bench (fpga/tangprimer25k/README.md, SWD): the chip's SWCLK and SWDIO from J6's bottom
// row to two breadboard rows, and from each row to the spare Pico twice: its debug header (the
// target) and a GPIO (the analyser's channel, the same board running LogicAnalyzer firmware).
// Ground: the target's debug GND to the bench Pico W's pin 38.
import { DockPin, Dot, W } from "./EthCircuit";

const PART = "var(--brass)";
const CLK = "var(--ok)";
const DIO = "var(--m3)";

/** A breadboard row: a strip with its five joined holes. */
function Row({ y, name }: { y: number; name: string }) {
  return (
    <g>
      <rect x={430} y={y - 9} width={150} height={18} rx={4} fill="var(--bg)" stroke={PART} strokeWidth={1.6} />
      {[0, 1, 2, 3, 4].map((i) => <circle key={i} cx={449 + 28 * i} cy={y} r={3} fill={PART} />)}
      <text x={505} y={y - 16} fontSize={11} fill="var(--fg-3)" textAnchor="middle">{name}</text>
    </g>
  );
}

/** A pin of the spare Pico. */
function PicoPin({ y, name, role, c }: { y: number; name: string; role: string; c?: string }) {
  return (
    <g>
      <circle cx={812} cy={y} r={5} fill="var(--bg)" stroke={c ?? "var(--fg-2)"} strokeWidth={1.8} />
      <text x={826} y={y - 2} fontSize={13} fontWeight={600} fill="var(--fg)">{name}</text>
      <text x={826} y={y + 14} fontSize={11} fill="var(--fg-3)">{role}</text>
    </g>
  );
}

export default function SwdCircuit() {
  const clk = 120, dio = 230, gnd = 330;
  return (
    <svg className="fig-svg" viewBox="0 0 1110 380" role="img"
      aria-label="The SWD bench. SWCLK, the chip's uio[4] on J6 bottom row column 6, goes to a breadboard row, and from it to the spare Pico's debug-header SWCLK and to its GP2, the analyser's channel 1. SWDIO, uio[5] on J6 bottom row column 5, goes to a second row, and from it to the debug-header SWDIO and to GP3, channel 2. The spare Pico's debug GND goes to the bench Pico W's pin 38.">
      <text x={20} y={30} fontSize={12} fill="var(--fg-3)" letterSpacing="0.08em">FROM THE DOCK</text>
      <text x={505} y={30} fontSize={12} fill="var(--brass)" letterSpacing="0.08em" textAnchor="middle">BREADBOARD</text>
      <text x={800} y={30} fontSize={12} fill="var(--fg-3)" letterSpacing="0.08em">SPARE PICO · RP2040</text>
      <rect x={792} y={48} width={300} height={310} rx={10} fill="none" stroke="var(--line)" strokeWidth={1.4} />

      {/* SWCLK */}
      <DockPin y={clk} sig="SWCLK · uio[4]" where="J6 bottom row, col 6" />
      <W d={`M237 ${clk} H430`} c={CLK} /><Row y={clk} name="row A" />
      <W d={`M580 ${clk} H660 V${clk - 30} H807`} c={CLK} />
      <W d={`M660 ${clk} V${clk + 30} H807`} c={CLK} /><Dot x={660} y={clk} />
      <PicoPin y={clk - 30} name="SWCLK" role="debug header: the target" c={CLK} />
      <PicoPin y={clk + 30} name="GP2 · pin 4" role="analyser channel 1" c={CLK} />

      {/* SWDIO */}
      <DockPin y={dio} sig="SWDIO · uio[5]" where="J6 bottom row, col 5" />
      <W d={`M237 ${dio} H430`} c={DIO} /><Row y={dio} name="row B" />
      <W d={`M580 ${dio} H660 V${dio - 30} H807`} c={DIO} />
      <W d={`M660 ${dio} V${dio + 30} H807`} c={DIO} /><Dot x={660} y={dio} />
      <PicoPin y={dio - 30} name="SWDIO" role="debug header: its own pull-up" c={DIO} />
      <PicoPin y={dio + 30} name="GP3 · pin 5" role="analyser channel 2" c={DIO} />

      {/* ground */}
      <DockPin y={gnd} sig="GND" where="the Pico W's pin 38" />
      <W d={`M237 ${gnd} H807`} />
      <PicoPin y={gnd} name="GND" role="debug header" />
    </svg>
  );
}
