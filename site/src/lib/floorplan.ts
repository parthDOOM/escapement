// The block map from site/tools/floorplan.py: which logic sits where on the die.
import { json } from "./util";

export type Block = { key: string; label: string; color: string; about: string; cells: number; area_um2: number };
export type Floorplan = { die_um: [number, number]; bin_um: number; w: number; h: number; blocks: Block[];
  grid: Uint8Array; dens: Record<string, Uint8Array> };

const b64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

let fp: Promise<Floorplan> | null = null;
export function floorplan(): Promise<Floorplan> {
  if (!fp) {
    fp = json<any>("data/floorplan.json").then((d) => ({
      ...d,
      grid: b64(d.grid),
      dens: Object.fromEntries(Object.entries(d.dens as Record<string, string>).map(([k, v]) => [k, b64(v)])),
    }));
  }
  return fp;
}

/** Paint the map into a canvas, one pixel per bin, row 0 at the top (north up).
 *  `only`: one block's density as a glow; otherwise every bin in its block's colour. */
export function paint(f: Floorplan, only: string | null, alpha = 1): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = f.w; c.height = f.h;
  const g = c.getContext("2d")!;
  const img = g.createImageData(f.w, f.h);
  const rgb = f.blocks.map((b) => [1, 3, 5].map((i) => parseInt(b.color.slice(i, i + 2), 16)));
  const ki = only ? f.blocks.findIndex((b) => b.key === only) : -1;
  for (let y = 0; y < f.h; y++) {
    for (let x = 0; x < f.w; x++) {
      const src = (f.h - 1 - y) * f.w + x, dst = (y * f.w + x) * 4;
      let col: number[] | null = null, a = 0;
      if (ki >= 0) {
        const d = f.dens[only!][src];
        if (d) { col = rgb[ki]; a = Math.min(1, 0.25 + d / 160); }
      } else {
        const v = f.grid[src];
        if (v) { col = rgb[v - 1]; a = 1; }
      }
      if (col) {
        img.data[dst] = col[0]; img.data[dst + 1] = col[1]; img.data[dst + 2] = col[2];
        img.data[dst + 3] = Math.round(255 * a * alpha);
      }
    }
  }
  g.putImageData(img, 0, 0);
  return c;
}

/** The block at a die position in µm (origin bottom left), or null. */
export function blockAt(f: Floorplan, x: number, y: number): Block | null {
  const gx = Math.floor(x / f.bin_um), gy = Math.floor(y / f.bin_um);
  if (gx < 0 || gy < 0 || gx >= f.w || gy >= f.h) return null;
  const v = f.grid[gy * f.w + gx];
  return v ? f.blocks[v - 1] : null;
}
