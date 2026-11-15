import { useMemo } from "react";
import type { Fpga, Wire } from "./types";
import { cssColour, wireColour } from "./types";

// Every jumper of the bench, one line each, in its own colour. The Dock is seen from above with
// J3 on its far edge; its three Pmod sockets are drawn as seen looking into the openings (column 1,
// the 3V3 pair, on the left; odd pins on the top row), with the Pico W in front of them, USB to the
// right. Holes and pins are 2.54 mm apart on both boards (P units here); the wires are routed, not
// photographed: each leaves its pin, runs in a lane of its own and enters its hole, and wires
// from the Pico's far row go round the board's ends as they do on the bench.
const P = 26;
const OY = 34;                                            // room for the title
const DOCK = { x: 122, y: OY, w: 641, h: 182 };
const J3 = { x1: 200, out: OY + 26, inn: OY + 52 };        // column 1 x; outer (even) and inner (odd) row y
const SOCK: Record<string, number> = { J6: 150, J5: 384, J4: 618 }; // column 1 x (9 pitches apart)
const ST = OY + 152, SB = ST + P;                          // socket top and bottom rows
const BODY = OY + 92;                                      // first lane across the Dock (clk, rst_n, GND)
const CH0 = SB + 40, LANE = 8;                             // the lanes between the boards
const X0 = 180;                                            // Pico pins 20 and 21
const NEVER = new Set([30, 35, 36, 37, 39, 40]);           // power and RUN: never wired
const TOP_ROW = (p: number) => p <= 20;
const pinX = (p: number) => (TOP_ROW(p) ? X0 + (20 - p) * P : X0 + (p - 21) * P);
const j3x = (c: number) => J3.x1 + (c - 1) * P;
const sockX = (s: string, c: number) => SOCK[s] + (c - 1) * P;
const PICO_L = X0 - 22, PICO_R = X0 + 19 * P + 22;

type Pt = [number, number];
type Routed = { w: Wire; kind: "up" | "left" | "j3"; px: number; jx: number; hx: number; hy: number; vx: number; k: number };

/** Signal names the host link gives some pins. */
export const ROLE: Record<string, string> = {
  "ui_in[0]": "CS_n", "ui_in[1]": "SCK", "ui_in[2]": "MOSI", "uo_out[0]": "MISO", "uo_out[1]": "IRQ", "uo_out[2]": "RUN",
};

function route(d: Fpga) {
  const rs: Routed[] = d.wires.map((w) => {
    let hx: number, hy: number, jx: number;
    if (w.dock === "J3") {
      // reached from the board side: the inner row directly, the outer row between two inner pins
      const inner = w.pin % 2 === 1;
      hx = j3x((w.pin + 1) >> 1); hy = inner ? J3.inn : J3.out; jx = inner ? hx : hx + P / 2;
    } else {
      // reached from below: the bottom row directly, the top row between two bottom holes
      hx = sockX(w.dock, w.col); hy = w.row === "top" ? ST : SB; jx = w.row === "top" ? hx - P / 2 : hx;
    }
    const kind = w.dock === "J3" ? "j3" : TOP_ROW(w.pico) ? "up" : "left";
    return { w, kind, px: pinX(w.pico), jx, hx, hy, vx: 0, k: 0 };
  });
  // Round the ends, nested: the innermost track belongs to the wire whose hole is furthest along,
  // so the bundle opens out in order at the top.
  const left = rs.filter((r) => r.kind === "left").sort((a, b) => b.jx - a.jx);
  left.forEach((r, i) => { r.vx = PICO_L - 14 - i * 8; r.k = i; });
  const j3 = rs.filter((r) => r.kind === "j3").sort((a, b) => b.jx - a.jx);
  j3.forEach((r, i) => { r.vx = DOCK.x + DOCK.w + 18 + i * 8; r.k = i; });
  const chan = rs.filter((r) => !(r.kind === "j3" && !TOP_ROW(r.w.pico)));
  const PT = (lanes: number) => CH0 + lanes * LANE + 22;

  const path = (r: Routed, lane: Map<Routed, number>, pt: number): Pt[] => {
    const pb = pt + 7 * P, by0 = pb + 26, yl = CH0 + (lane.get(r) ?? 0) * LANE;
    const pts: Pt[] = [];
    if (r.kind === "up") pts.push([r.px, pt], [r.px, yl], [r.jx, yl], [r.jx, r.hy]);
    else if (r.kind === "left") {
      const by = by0 + r.k * 8;
      pts.push([r.px, pb], [r.px, by], [r.vx, by], [r.vx, yl], [r.jx, yl], [r.jx, r.hy]);
    } else {
      if (TOP_ROW(r.w.pico)) pts.push([r.px, pt], [r.px, yl], [r.vx, yl]);
      else { const by = by0 + (left.length + 1) * 8; pts.push([r.px, pb], [r.px, by], [r.vx, by]); }
      const oy = BODY + r.k * 8;
      pts.push([r.vx, oy], [r.jx, oy], [r.jx, r.hy]);
    }
    if (r.jx !== r.hx) pts.push([r.hx, r.hy]);
    return pts;
  };
  const crossings = (order: Routed[]) => {
    const lane = new Map(order.map((r, i) => [r, i]));
    const segs: [Routed, Pt, Pt][] = [];
    for (const r of rs) { const p = path(r, lane, PT(order.length)); for (let i = 0; i + 1 < p.length; i++) segs.push([r, p[i], p[i + 1]]); }
    let n = 0;
    for (let i = 0; i < segs.length; i++) for (let j = i + 1; j < segs.length; j++) {
      if (segs[i][0] === segs[j][0]) continue;
      let [, a0, a1] = segs[i], [, b0, b1] = segs[j];
      if (a0[1] !== a1[1]) [a0, a1, b0, b1] = [b0, b1, a0, a1];          // a horizontal, b vertical
      if (a0[1] !== a1[1] || b0[0] !== b1[0]) continue;
      const x = b0[0], y = a0[1];
      if (x > Math.min(a0[0], a1[0]) && x < Math.max(a0[0], a1[0]) && y > Math.min(b0[1], b1[1]) && y < Math.max(b0[1], b1[1])) n++;
    }
    return n;
  };
  // Lanes in bands from the sockets down, J6, J5, J4, then clk and GND; inside a band the wire that
  // ends furthest right runs nearest the Pico, so wires going the same way do not cross. Then swap
  // neighbours while that removes crossings.
  const band = (r: Routed) => ["J6", "J5", "J4", "J3"].indexOf(r.w.dock);
  let order = [...chan].sort((a, b) => band(a) - band(b) || a.jx - b.jx || a.px - b.px);
  let best = crossings(order);
  for (let improved = true; improved; ) {
    improved = false;
    for (let i = 0; i + 1 < order.length; i++) {
      const o = [...order]; [o[i], o[i + 1]] = [o[i + 1], o[i]];
      const c = crossings(o);
      if (c < best) { best = c; order = o; improved = true; }
    }
  }
  // Pack: a wire takes the first lane above every earlier wire whose run overlaps its own. Wires
  // whose runs are apart can share a lane without changing any crossing.
  const span = (r: Routed) => {
    const s = r.kind === "left" ? r.vx : r.px, e = r.kind === "j3" ? r.vx : r.jx;
    return [Math.min(s, e) - 10, Math.max(s, e) + 10];
  };
  const lane = new Map<Routed, number>();
  order.forEach((r, i) => {
    const [a, b] = span(r);
    let l = 0;
    for (const q of order.slice(0, i)) { const [c, e] = span(q); if (c < b && a < e) l = Math.max(l, lane.get(q)! + 1); }
    lane.set(r, l);
  });
  const lanes = Math.max(...lane.values()) + 1;
  const pt = PT(lanes);
  return { paths: rs.map((r) => ({ r, pts: path(r, lane, pt) })), pt, pb: pt + 7 * P, left: left.length, rightX: j3[j3.length - 1].vx };
}

/** A rounded orthogonal path through the points. */
function smooth(pts: Pt[]) {
  let s = `M${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length; i++) {
    const [x, y] = pts[i];
    if (i + 1 === pts.length) { s += ` L${x} ${y}`; break; }
    const [px, py] = pts[i - 1], [nx, ny] = pts[i + 1];
    const r = Math.min(5, Math.hypot(x - px, y - py) / 2, Math.hypot(nx - x, ny - y) / 2);
    s += ` L${x - Math.sign(x - px) * r} ${y - Math.sign(y - py) * r} Q${x} ${y} ${x + Math.sign(nx - x) * r} ${y + Math.sign(ny - y) * r}`;
  }
  return s;
}

const LIGHT = new Set(["yellow", "orange", "white", "grey"]);
const bitOf = (sig: string) => /\[(\d)\]/.exec(sig)?.[1];

export default function WiringDiagram({ d, sel, setSel }: { d: Fpga; sel: number | null; setSel: (p: number | null) => void }) {
  const R = useMemo(() => route(d), [d]);
  const W = R.rightX + 30, H = R.pb + 26 + (R.left + 1) * 8 + 14;
  const byPico = new Map(d.wires.map((w) => [w.pico, w]));
  const hole = (s: string, row: string, c: number) => d.wires.find((w) => w.dock === s && w.row === row && w.col === c);
  const onJ3 = (n: number) => d.wires.find((w) => w.dock === "J3" && w.pin === n);
  const dim = (w?: Wire) => (sel !== null && (!w || w.pico !== sel) ? 0.18 : 1);
  const hover = (w?: Wire) => (w ? {
    onMouseEnter: () => setSel(w.pico), onMouseLeave: () => setSel(null),
    onClick: () => setSel(sel === w.pico ? null : w.pico), style: { cursor: "pointer" },
  } : {});
  const ink = "var(--pcb-ink)";
  const label = (w: Wire, x: number, y: number) => {
    const b = bitOf(w.sig), t = w.sig === "GND" ? "G" : b ?? "";
    return t ? <text x={x} y={y + 3.5} fontSize={10} fontWeight={700} textAnchor="middle" pointerEvents="none"
      fill={LIGHT.has(wireColour(w)) ? "#1b1b1b" : "#f4f4f4"}>{t}</text> : null;
  };
  return (
    <svg className="fig-svg wiring" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label="Wiring of the bench: 32 jumpers from the Pico W's pins to the Tang Primer 25K Dock, 24 of them to the Pmod sockets J4 (ui_in), J5 (uo_out) and J6 (uio) with their grounds, and clock, reset and ground to the header J3. The table below lists every wire.">
      <text x={DOCK.x} y={OY - 14} fontSize={14} fontWeight={600} fill="var(--fg)">Tang Primer 25K Dock</text>
      <text x={DOCK.x + 196} y={OY - 14} fontSize={12} fill="var(--fg-3)">from above; the sockets as seen looking into them</text>

      {/* the Dock: J3 on the far edge, the sockets on the near edge */}
      <rect x={DOCK.x} y={DOCK.y} width={DOCK.w} height={DOCK.h} rx={10} fill="var(--pcb)" />
      <rect x={j3x(1) - 14} y={J3.out - 14} width={19 * P + 28} height={P + 28} rx={3} fill="var(--hole)" />
      <text x={j3x(1) - 22} y={J3.inn + 4} fontSize={13} fontWeight={600} fill={ink} textAnchor="end">J3</text>
      <text x={j3x(1)} y={J3.inn + 26} fontSize={10} fill={ink} textAnchor="middle">1</text>
      <text x={j3x(7) + 4} y={J3.inn + 26} fontSize={11} fill={ink}>pins 9 clk · 10 rst_n · 12 GND · 11 is +5V, left bare</text>
      {(["J6", "J5", "J4"] as const).map((s) => (
        <g key={s}>
          <rect x={sockX(s, 1) - 15} y={ST - 15} width={5 * P + 30} height={P + 30} rx={3} fill="var(--hole)" stroke="var(--line)" />
          <text x={sockX(s, 1) - 14} y={ST - 24} fontSize={13} fontWeight={600} fill={ink}>{s}</text>
          <text x={sockX(s, 1) + 12} y={ST - 24} fontSize={11} fill={ink}>{{ J6: "uio", J5: "uo_out", J4: "ui_in" }[s]}</text>
        </g>
      ))}

      {/* the Pico W */}
      <rect x={PICO_L} y={R.pt - 16} width={PICO_R - PICO_L} height={R.pb - R.pt + 32} rx={6} fill="var(--pcb)" />
      <rect x={PICO_R - 4} y={(R.pt + R.pb) / 2 - 22} width={26} height={44} rx={3} fill="#9aa3a0" />
      <text x={(PICO_L + PICO_R) / 2} y={(R.pt + R.pb) / 2 + 5} fontSize={15} fontWeight={600} fill={ink} textAnchor="middle">Raspberry Pi Pico W</text>
      <text x={PICO_R + 28} y={(R.pt + R.pb) / 2 + 4} fontSize={11} fill="var(--fg-3)">USB</text>

      {/* the wires: a background halo, an edge so black and white read on either theme, the colour */}
      {R.paths.map(({ r, pts }) => {
        const s = smooth(pts);
        return (
          <g key={r.w.pico} opacity={dim(r.w)} {...hover(r.w)}>
            <path d={s} fill="none" stroke="var(--bg)" strokeWidth={7.5} strokeLinejoin="round" />
            <path d={s} fill="none" stroke="var(--w-edge)" strokeWidth={4.8} strokeLinejoin="round" />
            <path d={s} fill="none" stroke={cssColour(wireColour(r.w))} strokeWidth={3.2} strokeLinejoin="round" />
            <path d={s} fill="none" stroke="transparent" strokeWidth={12} />
          </g>
        );
      })}

      {/* J3 pins, socket holes (the bit number, G for ground), Pico pins */}
      {Array.from({ length: 40 }, (_, i) => i + 1).map((n) => {
        const w = onJ3(n), x = j3x((n + 1) >> 1), y = n % 2 ? J3.inn : J3.out;
        return <rect key={n} x={x - 6} y={y - 6} width={12} height={12} opacity={w ? dim(w) : 1} {...hover(w)}
          fill={w ? cssColour(wireColour(w)) : n === 11 ? "var(--hole)" : "var(--pin)"}
          stroke={n === 11 ? "var(--bad)" : w ? "#fff" : "none"} strokeWidth={n === 11 ? 2.5 : 1.2} />;
      })}
      {(["J6", "J5", "J4"] as const).map((s) => [1, 2, 3, 4, 5, 6].flatMap((c) => (["top", "bottom"] as const).map((row) => {
        const w = hole(s, row, c), x = sockX(s, c), y = row === "top" ? ST : SB;
        return (
          <g key={s + c + row} opacity={w ? dim(w) : 1} {...hover(w)}>
            <circle cx={x} cy={y} r={8.5} fill={w ? cssColour(wireColour(w)) : "#2a2a2a"}
              stroke={c === 1 ? "var(--bad)" : "#888"} strokeWidth={c === 1 ? 2.5 : 1} />
            {w && label(w, x, y)}
          </g>
        );
      })))}
      {Array.from({ length: 40 }, (_, i) => i + 1).map((p) => {
        const w = byPico.get(p), x = pinX(p), y = TOP_ROW(p) ? R.pt : R.pb, ty = TOP_ROW(p) ? R.pt + 15 : R.pb - 15;
        return (
          <g key={p} opacity={w ? dim(w) : sel !== null ? 0.5 : 1} {...hover(w)}>
            <circle cx={x} cy={y} r={7.5} fill={w ? cssColour(wireColour(w)) : "var(--pin)"}
              stroke={NEVER.has(p) ? "var(--bad)" : "#333"} strokeWidth={NEVER.has(p) ? 2.5 : 1} />
            <text x={x} y={ty} fontSize={9} fill={ink} textAnchor={TOP_ROW(p) ? "start" : "end"} dominantBaseline="middle"
              transform={`rotate(90 ${x} ${ty})`} pointerEvents="none">{d.pico[p - 1]}</text>
          </g>
        );
      })}
    </svg>
  );
}
