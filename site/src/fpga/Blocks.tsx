import type { Block, Edge, Fpga } from "./types";
import { useJson } from "../lib/util";

// The chip as the FPGA synthesis sees it with its hierarchy kept at the silicon floorplan's blocks
// (fpga/tangprimer25k/hierarchy.sh): each block's size and the buses between blocks, with their
// widths counted bit by bit from the netlist.
type Node = { id: string; label: string; sub: string; x: number; y: number; w: number; h: number };

const NODES: Node[] = [
  { id: "(pins)", label: "Pins", sub: "24 + clk, rst_n", x: 10, y: 214, w: 112, h: 92 },
  { id: "(tt_um_escapement logic)", label: "Port glue", sub: "memory port muxes", x: 196, y: 214, w: 134, h: 92 },
  { id: "u_host", label: "Host link", sub: "escapement_host", x: 410, y: 44, w: 172, h: 96 },
  { id: "u_sram", label: "Program store", sub: "1024 × 32, four pages", x: 410, y: 236, w: 172, h: 76 },
  { id: "u_tape", label: "Tape SRAM", sub: "256 × 32", x: 410, y: 400, w: 172, h: 76 },
  { id: "(esc_core logic)", label: "Shared pipeline", sub: "esc_core", x: 670, y: 44, w: 172, h: 466 },
  { id: "g_ctx[0].u_ctx", label: "Context 0", sub: "esc_ctx", x: 932, y: 44, w: 168, h: 58 },
  { id: "g_ctx[1].u_ctx", label: "Context 1", sub: "esc_ctx", x: 932, y: 112, w: 168, h: 58 },
  { id: "g_ctx[2].u_ctx", label: "Context 2", sub: "esc_ctx", x: 932, y: 180, w: 168, h: 58 },
  { id: "g_ctx[3].u_ctx", label: "Context 3", sub: "esc_ctx", x: 932, y: 248, w: 168, h: 58 },
  { id: "u_cap", label: "Capture unit", sub: "esc_cap", x: 932, y: 316, w: 168, h: 58 },
  { id: "u_line", label: "Line unit", sub: "esc_line", x: 932, y: 384, w: 168, h: 58 },
  { id: "u_mon", label: "Bus monitor", sub: "esc_mon", x: 932, y: 452, w: 168, h: 58 },
];
const ALIAS: Record<string, string> = { u_core: "(esc_core logic)" };

function counts(b: Block | undefined) {
  if (!b) return "";
  const parts = [`${(b.lut4 ?? 0).toLocaleString("en")} LUT4`, `${(b.ff ?? 0).toLocaleString("en")} FF`];
  if (b.bram) parts.splice(0, 2, `${b.bram} block RAM${b.bram > 1 ? "s" : ""}`);
  return parts.join(" · ");
}

export function BlockDiagram({ d }: { d: Fpga }) {
  const blockOf = new Map(d.hierarchy.blocks.map((b) => [b.inst === "u_core" ? "(esc_core logic)" : b.inst === "(top glue)" ? "(tt_um_escapement logic)" : b.inst, b]));
  const node = new Map(NODES.map((n) => [n.id, n]));
  // fold both directions of each pair into one line; the core's own ports are drawn from the top level
  const pairs = new Map<string, { a: string; b: string; ab: number; ba: number }>();
  const add = (e: Edge) => {
    const f = ALIAS[e.from] ?? e.from, t = ALIAS[e.to] ?? e.to;
    if (!node.has(f) || !node.has(t) || f === t) return;
    const [a, b] = f < t ? [f, t] : [t, f];
    const k = a + "|" + b;
    const p = pairs.get(k) ?? { a, b, ab: 0, ba: 0 };
    if (f === a) p.ab += e.bits; else p.ba += e.bits;
    pairs.set(k, p);
  };
  d.hierarchy.edges_top.forEach(add);
  d.hierarchy.edges_core.filter((e) => e.from !== "(pins)" && e.to !== "(pins)").forEach(add);
  const anchor = (n: Node, other: Node) => {
    const cx = n.x + n.w / 2, cy = n.y + n.h / 2, ox = other.x + other.w / 2;
    if (Math.abs(ox - cx) < 10) return { x: cx, y: other.y > n.y ? n.y + n.h : n.y };
    const y = Math.max(n.y + 14, Math.min(n.y + n.h - 14, other.y + other.h / 2));
    return { x: ox > cx ? n.x + n.w : n.x, y: n.h > 200 ? y : cy };
  };
  return (
    <svg className="fig-svg" viewBox="0 0 1146 530" role="img"
      aria-label="Block diagram of the chip as synthesised for the FPGA: pins, port glue, host link, program store and tape, the shared pipeline, four contexts, the capture unit, the line unit and the bus monitor, with the width of every bus between them.">
      {[...pairs.values()].map((p) => {
        const A = node.get(p.a)!, B = node.get(p.b)!;
        const label = [p.ab && `${p.ab}`, p.ba && `${p.ba}`].filter(Boolean).join(" ⇄ ");
        const total = p.ab + p.ba;
        // two blocks stacked in one column with no room between them: a bracket on their right
        const stacked = A.x === B.x && Math.abs(A.y - B.y) < Math.max(A.h, B.h) + 30;
        const s = stacked ? { x: A.x + A.w, y: A.y + A.h / 2 } : anchor(A, B);
        const t = stacked ? { x: B.x + B.w, y: B.y + B.h / 2 } : anchor(B, A);
        const bx = A.x + A.w + 24;
        const mx = stacked ? bx : (s.x + t.x) / 2, my = (s.y + t.y) / 2;
        const d = stacked ? `M${s.x} ${s.y} C${bx} ${s.y} ${bx} ${t.y} ${t.x} ${t.y}`
          : `M${s.x} ${s.y} C${mx} ${s.y} ${mx} ${t.y} ${t.x} ${t.y}`;
        return (
          <g key={p.a + p.b}>
            <path d={d} fill="none" stroke="var(--brass)"
              strokeWidth={Math.max(1.2, Math.min(7, Math.sqrt(total) / 4))} opacity={0.75} />
            <rect x={mx - (label.length * 6.4 + 10) / 2} y={my - 10} width={label.length * 6.4 + 10} height={18} rx={4} fill="var(--bg-2)" opacity={0.92} />
            <text x={mx} y={my + 3} fontSize={10.5} textAnchor="middle" fill="var(--fg-2)">{label}</text>
          </g>
        );
      })}
      {NODES.map((n) => (
        <g key={n.id}>
          <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={8} fill="var(--bg)" stroke="var(--line)" />
          <text x={n.x + 10} y={n.y + 20} fontSize={12.5} fontWeight={600} fill="var(--fg)" style={{ fontFamily: "var(--font-body)" }}>{n.label}</text>
          <text x={n.x + 10} y={n.y + 36} fontSize={10.5} fill="var(--fg-3)">{n.sub}</text>
          <text x={n.x + 10} y={n.y + 52} fontSize={10.5} fill="var(--brass)">{counts(blockOf.get(n.id))}</text>
        </g>
      ))}
    </svg>
  );
}

type Floorplan = { blocks: { key: string; label: string; cells: number; area_um2: number }[] };

export function FpgaVsAsic({ d }: { d: Fpga }) {
  const [fp] = useJson<Floorplan>("data/floorplan.json");
  const b = (inst: string) => d.hierarchy.blocks.find((x) => x.inst === inst);
  const asic = (key: string) => fp?.blocks.find((x) => x.key === key);
  const rows: [string, string[], string | null][] = [
    ["Host link", ["u_host"], "host"],
    ["Shared pipeline", ["u_core"], "core"],
    ["Context 0", ["g_ctx[0].u_ctx"], "ctx0"],
    ["Context 1", ["g_ctx[1].u_ctx"], "ctx1"],
    ["Context 2", ["g_ctx[2].u_ctx"], "ctx2"],
    ["Context 3", ["g_ctx[3].u_ctx"], "ctx3"],
    ["Capture unit", ["u_cap"], "cap"],
    ["Line unit", ["u_line"], "line"],
    ["Bus monitor", ["u_mon"], "mon"],
    ["Program and tape SRAM", ["u_sram", "u_tape"], null],
    ["Port glue", ["(top glue)"], null],
    ["Clock tree", [], "clock"],
  ];
  const sum = (ids: string[], k: keyof Block) => ids.reduce((s, i) => s + ((b(i)?.[k] as number) ?? 0), 0);
  const n = (v: number) => v.toLocaleString("en");
  return (
    <div className="table-box panel">
      <table>
        <thead>
          <tr><th>Block</th><th className="num">LUT4</th><th className="num">Flip-flops</th><th className="num">Block RAM</th>
            <th className="num">Std cells</th><th className="num">Area <span style={{ textTransform: "none" }}>µm²</span></th></tr>
        </thead>
        <tbody>
          <tr className="group"><td colSpan={4}>GW5A-25 FPGA (this bench)</td><td colSpan={2} className="num">IHP SG13CMOS5L silicon (v3.2)</td></tr>
          {rows.map(([label, ids, key]) => {
            const a = key ? asic(key) : undefined;
            const sram = label.startsWith("Program");
            const clock = label === "Clock tree";
            return (
              <tr key={label}>
                <td>{label}</td>
                <td className="num">{clock ? "global network" : n(sum(ids, "lut4"))}</td>
                <td className="num">{clock ? "—" : n(sum(ids, "ff"))}</td>
                <td className="num">{clock ? "—" : n(sum(ids, "bram"))}</td>
                <td className="num">{sram ? "2 macros" : a ? n(a.cells) : key ? "…" : "—"}</td>
                <td className="num">{sram ? "1024×32 and 256×32" : a ? n(a.area_um2) : key ? "…" : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
