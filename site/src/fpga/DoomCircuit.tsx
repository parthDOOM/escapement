// The DOOM console's sound (docs/25-DOOM-CONSOLE.md): pwm_audio's pin, uio[6], taken from the
// breadboard row of the Pico's GP26 (wire 28), through 1 kohm to a 33 nF low-pass (three 100 nF
// in series) and a 10 kohm / 1.5 kohm divider, DC-coupled into a laptop's headset jack: the
// divider's middle to the plug's sleeve (the mic contact), ground to ring 2; tip and ring 1, the
// laptop's headphone outputs, taped off.
import { CablePin, Cap, DockPin, Dot, Res, W } from "./EthCircuit";

const SIG = "var(--ok)";
const GND = "var(--fg-2)";

export default function DoomCircuit() {
  const sig = 100, gnd = 300, lr = 350;
  const A = 450, M = 660;
  return (
    <svg className="fig-svg" viewBox="0 0 1110 390" role="img"
      aria-label="The DOOM console's sound circuit. pwm_audio's pin, uio[6], from the breadboard row of the Pico's GP26, goes through 1 kilohm to node A; from A, 33 nanofarads (three 100 nanofarad capacitors in series) go to ground, a low-pass filter at 4.8 kilohertz. From A, 10 kilohms go to node M, and from M 1.5 kilohms go to ground. M goes to the sleeve of a headset plug, the laptop's microphone contact, and ground to its second ring. The plug's tip and first ring, the laptop's headphone outputs, are taped off.">
      <text x={20} y={30} fontSize={12} fill="var(--fg-3)" letterSpacing="0.08em">FROM THE DOCK AND THE PICO</text>
      <text x={555} y={30} fontSize={12} fill="var(--brass)" letterSpacing="0.08em" textAnchor="middle">BREADBOARD</text>
      <text x={800} y={30} fontSize={12} fill="var(--fg-3)" letterSpacing="0.08em">HEADSET PLUG TO THE LAPTOP</text>
      <line x1={800} y1={44} x2={800} y2={372} stroke="var(--line)" strokeWidth={1.4} strokeDasharray="3 5" />

      {/* the pin, the series resistor and the low-pass */}
      <DockPin y={sig} sig="uio[6] · pwm_audio" where="the row of the Pico's GP26 (wire 28)" />
      <W d={`M237 ${sig} H330`} /><Res x={330} y={sig} label="1 kΩ" sub="limits the pin's current" />
      <W d={`M386 ${sig} H${A}`} /><Dot x={A} y={sig} />
      <W d={`M${A} ${sig} V170`} /><Cap x={A} y={170} vertical label="33 nF" />
      <text x={A + 20} y={196} fontSize={11} fill="var(--fg-3)">three 100 nF in series</text>
      <W d={`M${A} 180 V${gnd}`} /><Dot x={A} y={gnd} />
      <text x={A - 14} y={232} fontSize={12} fontWeight={600} fill="var(--brass)" textAnchor="end">low-pass</text>
      <text x={A - 14} y={248} fontSize={12} fontWeight={600} fill="var(--brass)" textAnchor="end">at 4.8 kHz</text>

      {/* the divider to a microphone's level */}
      <W d={`M${A} ${sig} H520`} /><Res x={520} y={sig} label="10 kΩ" />
      <W d={`M576 ${sig} H${M}`} /><W d={`M${M} ${sig} H800`} c={SIG} /><Dot x={M} y={sig} />
      <W d={`M${M} ${sig} V150`} />
      <Res x={M} y={150} len={56} vertical label="1.5 kΩ" sub="looks like a microphone" side="right" />
      <W d={`M${M} 206 V${gnd}`} /><Dot x={M} y={gnd} />
      <CablePin y={sig} c={SIG} stripe={false} name="sleeve · microphone" role="about 270 mV peak to peak at full scale" />

      {/* ground */}
      <DockPin y={gnd} sig="GND" where="the Pico W's pin 38" />
      <W d={`M237 ${gnd} H800`} c={GND} />
      <CablePin y={gnd} c={GND} stripe={false} name="ring 2 · ground" role="" />

      {/* the laptop's headphone outputs, not used */}
      <W d={`M760 ${lr} H800`} c="var(--bad)" dash="3 4" />
      <CablePin y={lr} c="var(--bad)" stripe={false} name="tip, ring 1 · left, right" role="taped off: the laptop's headphone outputs" />
    </svg>
  );
}
