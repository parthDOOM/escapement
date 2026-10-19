// Pan and zoom the die from a tile pyramid rendered from the GDS; overlay the block map
// and read off which block is under the pointer.
import { useEffect, useRef, useState } from "react";
import { asset, css, DPR, fit, useJson } from "../lib/util";
import { Block, blockAt, Floorplan, floorplan, paint } from "../lib/floorplan";
import type { DieInfo } from "./Die3D";

const macroName = (box: number[], H: number) =>
  box[1] > H / 2 ? "Tape · SRAM 256 × 32" : "Program memory · SRAM 256 × 32";

export default function DieZoom() {
  const [info, err] = useJson<DieInfo>("die/die.json");
  const [fp, setFp] = useState<Floorplan | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const cv = useRef<HTMLCanvasElement>(null);
  const [labels, setLabels] = useState(true);
  const [overlay, setOverlay] = useState<string | null>(null); // null off, "all", or a block key
  const [under, setUnder] = useState<Block | null>(null);
  const [scale, setScale] = useState("");
  const state = useRef({ labels: true, overlay: null as string | null, schedule: () => {}, reset: () => {} });
  state.current.labels = labels;
  state.current.overlay = overlay;

  useEffect(() => { floorplan().then(setFp); }, []);

  useEffect(() => {
    if (!info || !cv.current) return;
    const c = cv.current;
    const [W, H] = info.die_um, levels = info.levels, cache = new Map<string, HTMLImageElement>();
    let s = 1, ox = 0, oy = 0, fitS = 1; // s: css px per µm; o: µm at the canvas left/top
    const overlays = new Map<string, HTMLCanvasElement>();
    const tile = (z: number, x: number, y: number) => {
      const k = z + "/" + x + "/" + y;
      let im = cache.get(k);
      if (!im) {
        im = new Image();
        im.onload = () => schedule();
        im.src = asset(`die/die_${z}_${x}_${y}.jpg`);
        cache.set(k, im);
      }
      return im;
    };
    let raf = 0;
    const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); };
    const reset = () => {
      const w = c.clientWidth, h = c.clientHeight;
      fitS = Math.min(w / W, h / H); s = fitS;
      ox = -(w / s - W) / 2; oy = -(h / s - H) / 2;
    };
    const drawLevel = (g: CanvasRenderingContext2D, L: DieInfo["levels"][0], w: number, h: number) => {
      const T = info.tile, upx = W / L.w;
      for (let ty = 0; ty < L.ny; ty++) for (let tx = 0; tx < L.nx; tx++) {
        const x0 = tx * T * upx, y0 = ty * T * upx;
        const tw = Math.min(T, L.w - tx * T) * upx, th = Math.min(T, L.h - ty * T) * upx;
        const X = (x0 - ox) * s, Y = (y0 - oy) * s;
        if (X > w || Y > h || X + tw * s < 0 || Y + th * s < 0) continue;
        const im = tile(L.z, tx, ty);
        if (im.complete && im.naturalWidth) g.drawImage(im, X, Y, tw * s + 0.6, th * s + 0.6);
      }
    };
    const draw = () => {
      const { g, w, h } = fit(c);
      g.fillStyle = "#0a0c14"; g.fillRect(0, 0, w, h);
      const need = s * DPR() * W;
      let best = levels[levels.length - 1];
      for (const L of levels) if (L.w >= need) { best = L; break; }
      drawLevel(g, levels[0], w, h);
      if (best !== levels[0]) {
        const prev = levels[Math.max(0, best.z - 1)];
        if (prev !== levels[0]) drawLevel(g, prev, w, h);
        drawLevel(g, best, w, h);
      }
      const ov = state.current.overlay;
      const f = fpRef.current;
      if (ov && f) {
        g.fillStyle = ov === "all" ? "rgba(8,10,16,0.5)" : "rgba(8,10,16,0.72)"; // the layout recedes
        g.fillRect(0, 0, w, h);
        const key = ov === "all" ? "all" : ov;
        let o = overlays.get(key);
        if (!o) { o = paint(f, ov === "all" ? null : ov, ov === "all" ? 0.7 : 1); overlays.set(key, o); }
        g.imageSmoothingEnabled = s * f.bin_um < 6;
        const X = (0 - ox) * s, Y = (H - f.h * f.bin_um - oy) * s;
        g.drawImage(o, X, Y, f.w * f.bin_um * s, f.h * f.bin_um * s);
        g.imageSmoothingEnabled = true;
      }
      if (state.current.labels) {
        g.font = "600 12px " + css("--font-body"); g.textBaseline = "top";
        const lab = (x0: number, y0: number, x1: number, y1: number, t: string) => {
          const X = (x0 - ox) * s, Y = (H - y1 - oy) * s, Wd = (x1 - x0) * s, Hd = (y1 - y0) * s;
          g.strokeStyle = "rgba(240,198,110,0.9)"; g.setLineDash([5, 4]); g.lineWidth = 1.2;
          g.strokeRect(X, Y, Wd, Hd); g.setLineDash([]);
          const tw = g.measureText(t).width + 10;
          g.fillStyle = "rgba(10,12,20,0.78)"; g.fillRect(X + 6, Y + 6, tw, 19);
          g.fillStyle = "#f0c66e"; g.fillText(t, X + 11, Y + 10);
        };
        info.macros.forEach((m) => lab(m.box[0], m.box[1], m.box[2], m.box[3], macroName(m.box, H)));
        lab(30, H - 12, 191, H, "Tiny Tapeout pins");
      }
      const um = [1, 2, 5, 10, 20, 50, 100, 200, 500].find((u) => u * s >= 70) || 500;
      setScale(um + " µm");
      bar.current = um * s;
    };
    const zoomAt = (f: number, px: number, py: number) => {
      const ns = Math.max(fitS * 0.9, Math.min(s * f, (levels[levels.length - 1].w / W) * 3));
      ox += px / s - px / ns; oy += py / s - py / ns; s = ns; schedule();
    };
    const pts = new Map<number, [number, number]>();
    let pinch: number | null = null;
    const down = (e: PointerEvent) => { c.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]); };
    const move = (e: PointerEvent) => {
      const r = c.getBoundingClientRect();
      if (!pts.has(e.pointerId)) {
        const f = fpRef.current;
        if (f && state.current.overlay) {
          const x = (e.clientX - r.left) / s + ox, y = H - ((e.clientY - r.top) / s + oy);
          setUnder(blockAt(f, x, y));
        }
        return;
      }
      const [px, py] = pts.get(e.pointerId)!;
      pts.set(e.pointerId, [e.clientX, e.clientY]);
      if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch) zoomAt(d / pinch, (a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top);
        pinch = d; return;
      }
      ox -= (e.clientX - px) / s; oy -= (e.clientY - py) / s; schedule();
    };
    const up = (e: PointerEvent) => { pts.delete(e.pointerId); if (pts.size < 2) pinch = null; };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    };
    const dbl = (e: MouseEvent) => { const r = c.getBoundingClientRect(); zoomAt(2, e.clientX - r.left, e.clientY - r.top); };
    const leave = () => setUnder(null);
    c.addEventListener("pointerdown", down); c.addEventListener("pointermove", move);
    c.addEventListener("pointerup", up); c.addEventListener("pointercancel", up);
    c.addEventListener("wheel", wheel, { passive: false }); c.addEventListener("dblclick", dbl);
    c.addEventListener("pointerleave", leave);
    const ro = new ResizeObserver(() => { reset(); schedule(); });
    ro.observe(c);
    state.current.schedule = schedule;
    state.current.reset = () => { reset(); schedule(); };
    reset(); draw();
    return () => {
      ro.disconnect();
      c.removeEventListener("pointerdown", down); c.removeEventListener("pointermove", move);
      c.removeEventListener("pointerup", up); c.removeEventListener("pointercancel", up);
      c.removeEventListener("wheel", wheel); c.removeEventListener("dblclick", dbl);
      c.removeEventListener("pointerleave", leave);
    };
  }, [info]);

  const fpRef = useRef<Floorplan | null>(null);
  fpRef.current = fp;
  const bar = useRef(70);
  useEffect(() => { state.current.schedule(); }, [labels, overlay, fp]);

  const total = fp ? fp.blocks.reduce((a, b) => a + b.cells, 0) : 0;
  return (
    <figure>
      <div className="zoom" ref={box}>
        <canvas ref={cv} aria-label="Zoomable die layout" />
        {err && <span className="stage-msg">The die images are rendered from the GDS when the site is built.</span>}
        <div className="zoom-ui">
          <button className="icon-btn" type="button" aria-pressed={labels} onClick={() => setLabels(!labels)}>labels</button>
          <button className="icon-btn" type="button" aria-pressed={!!overlay} onClick={() => setOverlay(overlay ? null : "all")}>blocks</button>
          <button className="icon-btn" type="button" onClick={() => state.current.reset()}>fit</button>
        </div>
        {overlay && under && <span className="cell-tag">{under.label} · {under.about}</span>}
        <span className="scale"><i style={{ width: bar.current }} />{scale}</span>
      </div>
      {fp && overlay && (
        <div className="block-legend" role="group" aria-label="Logic blocks">
          <button type="button" aria-pressed={overlay === "all"} onClick={() => setOverlay("all")}>
            <i className="rainbow" /> all blocks
          </button>
          {fp.blocks.map((b) => (
            <button key={b.key} type="button" aria-pressed={overlay === b.key} onClick={() => setOverlay(b.key)}
              title={b.about}>
              <i style={{ background: b.color }} />{b.label}
              <span className="dim">{(100 * b.cells / total).toFixed(0)}%</span>
            </button>
          ))}
        </div>
      )}
      <figcaption>
        Drag to pan, scroll or pinch to zoom. Program memory bottom left, tape top right, Tiny Tapeout's pins
        along the top left. <b>Blocks</b> colours each 5 µm square by the part of the design with the most cell
        area in it; pick one block to see where its cells went. Synthesis flattens the design, so the map is
        reconstructed from the net names the cells drive: a close approximation, not a placement constraint.
      </figcaption>
    </figure>
  );
}
