// The USB bench (fpga/tangprimer25k/README.md, USB keyboard): the chip's D+ and D- from J6's top
// row through 27 ohms each to a USB cable cut open at its far end, its plug in the laptop; the
// 1.5 kohm low-speed pull-up from D- (cable side) to the Pico's GP9, driven high once usb_hid
// runs; the cable's ground to the Pico W's pin 38; VBUS taped off.
import { CablePin, DockPin, Dot, Res, W } from "./EthCircuit";

const DP = "var(--ok)";
const DM = "var(--fg-3)";
const GND = "var(--fg-2)";

export default function UsbCircuit() {
  const dp = 96, dm = 176, pu = 276, gnd = 346, vbus = 406;
  const N = 560;
  return (
    <svg className="fig-svg" viewBox="0 0 1110 440" role="img"
      aria-label="The USB bench. D+, the chip's uio[0] on J6 top row column 6, goes through 27 ohms to the USB cable's green wire. D-, uio[1] on J6 top row column 5, goes through 27 ohms to the cable's white wire, and on the cable side of that resistor 1.5 kilohms go to the Pico W's GP9, which drives it high once usb_hid runs. The cable's black ground wire goes to the Pico W's pin 38. The red VBUS wire is taped off.">
      <text x={20} y={30} fontSize={12} fill="var(--fg-3)" letterSpacing="0.08em">FROM THE DOCK AND THE PICO</text>
      <text x={470} y={30} fontSize={12} fill="var(--brass)" letterSpacing="0.08em" textAnchor="middle">BREADBOARD</text>
      <text x={800} y={30} fontSize={12} fill="var(--fg-3)" letterSpacing="0.08em">USB CABLE TO THE LAPTOP</text>
      <line x1={800} y1={44} x2={800} y2={420} stroke="var(--line)" strokeWidth={1.4} strokeDasharray="3 5" />

      {/* D+ */}
      <DockPin y={dp} sig="D+ · uio[0]" where="J6 top row, col 6" />
      <W d={`M237 ${dp} H330`} /><Res x={330} y={dp} label="27 Ω" sub="series" />
      <W d={`M386 ${dp} H800`} c={DP} />
      <CablePin y={dp} c={DP} stripe={false} name="green · D+" role="the laptop's port" />

      {/* D- and the pull-up on its cable side */}
      <DockPin y={dm} sig="D− · uio[1]" where="J6 top row, col 5" />
      <W d={`M237 ${dm} H330`} /><Res x={330} y={dm} label="27 Ω" side="below" />
      <W d={`M386 ${dm} H${N}`} /><W d={`M${N} ${dm} H800`} c={DM} /><Dot x={N} y={dm} />
      <CablePin y={dm} c={DM} stripe={false} name="white · D−" role="the laptop's port" />
      <W d={`M${N} ${dm} V${dm + 22}`} />
      <Res x={N} y={dm + 22} len={48} vertical label="1.5 kΩ" sub="low-speed pull-up" side="right" />
      <W d={`M${N} ${dm + 70} V${pu}`} /><Dot x={N} y={pu} />

      {/* the pull-up's switch */}
      <DockPin y={pu} sig="GP9" where="the Pico W (wire 22)" />
      <W d={`M237 ${pu} H${N}`} />
      <text x={N - 14} y={pu + 22} fontSize={12} fontWeight={600} fill="var(--brass)" textAnchor="end">
        3.3 V once usb_hid runs: the keyboard plugs in</text>

      {/* ground */}
      <DockPin y={gnd} sig="GND" where="the Pico W's pin 38" />
      <W d={`M237 ${gnd} H800`} c={GND} />
      <CablePin y={gnd} c={GND} stripe={false} name="black · GND" role="" />

      {/* VBUS, not used */}
      <W d={`M760 ${vbus} H800`} c="var(--bad)" dash="3 4" />
      <CablePin y={vbus} c="var(--bad)" stripe={false} name="red · VBUS" role="taped off: both boards have their own power" />
    </svg>
  );
}
