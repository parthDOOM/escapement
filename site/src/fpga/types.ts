/** public/data/fpga.json, written by tools/fpga_bench.py from fpga/tangprimer25k/results/<date>/. */
export type Wire = {
  sig: string; gpio: number | null; pico: number; dock: "J3" | "J4" | "J5" | "J6";
  row: "top" | "bottom" | "inner" | "outer"; col: number; pin: number; ball: string;
};
export type Stage = { n: number; name: string; pass: boolean; detail: string };
export type Firmware = {
  name: string; kind: "steady" | "halt" | "none"; ok: boolean; detail: string;
  digest?: string; model?: string; tick?: number; leads: [number, number | null, number][];
};
export type Manifest = {
  text: string; commit?: string; branch?: string; dirty?: boolean; built?: string; tools?: string;
  bitstream_sha256?: string; skew_ns?: number;
};
export type Block = {
  inst: string; type: string; parent: string; lut4?: number; mux?: number; alu?: number; ff?: number; bram?: number;
};
export type Edge = { from: string; to: string; bits: number; ports: string[] };
export type Fpga = {
  date: string; files: string;
  pinid: { pass: boolean; map: [number, string][] };
  stages: Stage[]; verdict: string | null; firmware: Firmware[];
  chip: Manifest; bundles: Manifest;
  pnr: { util: Record<string, [number, number]>; fmax_mhz: number | null; hold_flags: number };
  hierarchy: { blocks: Block[]; total: Record<string, number>; edges_top: Edge[]; edges_core: Edge[] };
  wires: Wire[]; pico: string[]; j3: string[]; pmod: Record<string, Record<string, string>>;
  clock_mhz: number | null;
};

/** Suggested wire colours: bit k of every bus gets the same colour, GND black, clk white. */
export const BIT_COLOURS = ["brown", "red", "orange", "yellow", "green", "blue", "purple", "grey"];
export function wireColour(w: Wire): string {
  if (w.sig === "GND") return "black";
  if (w.sig === "clk") return "white";
  if (w.sig === "rst_n") return "grey";
  const m = /\[(\d)\]/.exec(w.sig);
  return m ? BIT_COLOURS[+m[1]] : "grey";
}
export const cssColour = (name: string) => `var(--w-${name})`;
export const REPO = "https://github.com/parthDOOM/escapement";
