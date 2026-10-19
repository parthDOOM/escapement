// Waveforms with the commits that made them. Each context has a lane of commits under
// the pins; select a commit to see the event it queued and where it landed, or click an
// edge to find the commit that scheduled it.
import { useEffect, useMemo, useRef, useState } from "react";
import { css, CTX_VARS, fit, fmtTime, niceStep, useResize, useThemeTick } from "../lib/util";
import type { Commit, Ev, Run } from "./types";
import { EVENT_KIND } from "./types";

const LEFT = 104, TOP = 26, RH = 34, LH = 17, GAP = 22, OV = 22, QGAP = 18, QH = 13;

export type View = [number, number];

type Props = {
  run: Run;
  view: View;
  setView: (v: View) => void;
  selected: number | null;
  onSelect: (i: number | null) => void;
  hitPcs: Set<number>;
  text: Map<number, string>;
};

function lowerBound(a: ArrayLike<number>, x: number) {
  let lo = 0, hi = a.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (a[m] < x) lo = m + 1;
    else hi = m;
  }
  return lo;
}

const isEv = (x: Commit[4]): x is Ev => !!x && typeof x[0] === "number";

export default function WaveView({ run, view, setView, selected, onSelect, hitPcs, text }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const theme = useThemeTick();
  const [hover, setHover] = useState<{ t: number; lane: number | null; i: number | null } | null>(null);

  const idx = useMemo(() => {
    const ticks = Int32Array.from(run.commits.map((c) => c[0]));
    const lanes = run.contexts.map((c) => c.ctx);
    const bank: Record<number, string> = {};
    run.contexts.forEach((c) => (bank[c.ctx] = c.bank));
    const byDeadline = new Map<number, number[]>();
    run.commits.forEach((c, i) => {
      if (c[3] === "C" && isEv(c[4])) {
        const d = c[4][1];
        const l = byDeadline.get(d);
        if (l) l.push(i);
        else byDeadline.set(d, [i]);
      }
    });
    const edges = run.signals.map((s) => Int32Array.from(s.w.map((p) => p[0])));
    // queue occupancy per context: an event is queued from its commit until its deadline
    const queue = lanes.map((k) => {
      const c: number[] = [], d: number[] = [];
      run.commits.forEach((x) => { if (x[1] === k && x[3] === "C" && isEv(x[4])) { c.push(x[0]); d.push(x[4][1]); } });
      return { c: Int32Array.from(c), d: Int32Array.from(d).sort() };
    });
    return { ticks, lanes, bank, byDeadline, edges, queue };
  }, [run]);

  const rows = run.signals.length;
  const height = TOP + rows * RH + GAP + idx.lanes.length * (LH + QH) + QGAP + 12 + OV + 6;

  /** the signal rows an event acts on */
  const rowsOf = (ctx: number, ev: Ev) => {
    const b = idx.bank[ctx];
    const out: number[] = [];
    const want = ev[0] === 3 ? [ev[8]] : [0, 1, 2, 3, 4, 5, 6, 7].filter((p) => (ev[2] >> p) & 1);
    for (const p of want) {
      const name = b === "B" ? "B" + p : "uio" + p;
      const r = run.signals.findIndex((s) => s.pin === name);
      if (r >= 0) out.push(r);
    }
    return out;
  };

  const geom = () => {
    const c = ref.current!;
    const w = c.clientWidth;
    const [t0, t1] = view;
    const sx = (t: number) => LEFT + ((t - t0) / (t1 - t0)) * (w - LEFT - 10);
    const tx = (x: number) => t0 + ((x - LEFT) / (w - LEFT - 10)) * (t1 - t0);
    const laneY = (k: number) => TOP + rows * RH + GAP + k * LH;
    const queueY = (k: number) => laneY(idx.lanes.length) + QGAP + k * QH;
    return { w, sx, tx, laneY, queueY };
  };

  const draw = () => {
    const c = ref.current;
    if (!c) return;
    c.style.width = c.parentElement!.clientWidth + "px";
    const { g, w, h } = fit(c, height);
    const { sx, tx, laneY, queueY } = geom();
    const [t0, t1] = view;
    const span = t1 - t0;
    const ppt = (w - LEFT - 10) / span; // px per tick
    const mono = css("--font-mono"), fg = css("--fg"), fg2 = css("--fg-2"), fg3 = css("--fg-3"),
      line = css("--line"), wave = css("--wave"), brass = css("--brass"), bad = css("--bad");
    const ctxCol = CTX_VARS.map((v) => css(v));
    const laneEnd = laneY(idx.lanes.length);
    g.clearRect(0, 0, w, h);

    // time axis
    const step = niceStep(span, Math.max(3, Math.floor((w - LEFT) / 110)));
    g.font = "500 10px " + mono; g.fillStyle = fg3; g.strokeStyle = line; g.textAlign = "center";
    g.beginPath();
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
      const X = Math.round(sx(t)) + 0.5;
      g.moveTo(X, TOP - 6); g.lineTo(X, laneEnd + 2);
      g.fillText(fmtTime(t), X, 12);
    }
    g.stroke();

    // single ticks, once they are wide enough to see
    if (ppt >= 7) {
      g.strokeStyle = line; g.globalAlpha = 0.45; g.beginPath();
      for (let t = Math.ceil(t0); t <= t1; t++) {
        const X = Math.round(sx(t)) + 0.5;
        g.moveTo(X, TOP + rows * RH + GAP - 4); g.lineTo(X, laneEnd);
      }
      g.stroke(); g.globalAlpha = 1;
    }

    // signals
    run.signals.forEach((s, r) => {
      const yHi = TOP + r * RH + 6, yLo = yHi + RH - 16;
      g.fillStyle = fg2; g.textAlign = "right"; g.font = "600 11px " + mono;
      g.fillText(s.name.length > 13 ? s.name.slice(0, 12) + "…" : s.name, LEFT - 12, yLo - 4);
      g.fillStyle = fg3; g.font = "500 9px " + mono;
      g.fillText(s.pin, LEFT - 12, yLo + 7);
      if (!s.w.length) return;
      let k = Math.max(0, lowerBound(idx.edges[r], t0) - 1);
      let v = s.w[k][1];
      g.strokeStyle = wave; g.lineWidth = 1.6;
      g.beginPath();
      g.moveTo(sx(Math.max(t0, s.w[k][0])), v ? yHi : yLo);
      for (k = k + 1; k < s.w.length && s.w[k][0] <= t1; k++) {
        const X = sx(s.w[k][0]);
        g.lineTo(X, v ? yHi : yLo);
        v = s.w[k][1];
        g.lineTo(X, v ? yHi : yLo);
      }
      g.lineTo(sx(Math.min(t1, run.span)), v ? yHi : yLo);
      g.stroke(); g.lineWidth = 1;
    });

    // commit lanes
    const top = TOP + rows * RH + GAP;
    g.fillStyle = fg3; g.font = "600 9.5px " + mono; g.textAlign = "left";
    g.fillText("COMMITS", 8, top - 7);
    const i0 = lowerBound(idx.ticks, t0 - 4), i1 = lowerBound(idx.ticks, t1 + 1);
    idx.lanes.forEach((k, li) => {
      g.fillStyle = ctxCol[k]; g.textAlign = "right"; g.font = "600 10px " + mono;
      g.fillText("ctx " + k, LEFT - 12, laneY(li) + LH - 5);
    });
    const laneOf = new Map(idx.lanes.map((k, i) => [k, i]));
    const showText = ppt * 4 >= 54;
    const dim = hitPcs.size > 0;
    for (let i = i0; i < i1; i++) {
      const [t, k, pc, o] = run.commits[i];
      const li = laneOf.get(k);
      if (li == null) continue;
      const X = sx(t), Y = laneY(li) + 2, wd = Math.max(1, ppt * 4 - 2), hh = LH - 4;
      const hit = hitPcs.has(pc);
      if (o === "F") { g.fillStyle = bad; g.fillRect(X, Y - 1, Math.max(wd, 3), hh + 2); continue; }
      g.globalAlpha = dim && !hit ? 0.25 : 0.9;
      if (o === "C") { g.fillStyle = ctxCol[k]; g.fillRect(X, Y, wd, hh); }
      else { g.strokeStyle = ctxCol[k]; g.strokeRect(X + 0.5, Y + 0.5, Math.max(wd - 1, 1), hh - 1); }
      g.globalAlpha = 1;
      if (hit) { g.fillStyle = fg; g.fillRect(X, Y + hh + 1, Math.max(wd, 2), 1.5); }
      if (showText) {
        g.save(); g.beginPath(); g.rect(X, Y, wd, hh); g.clip();
        g.fillStyle = o === "C" ? css("--bg") : ctxCol[k]; g.font = "600 9px " + mono; g.textAlign = "left";
        g.fillText((text.get(pc) || "").split(" ")[0], X + 3, Y + hh - 4);
        g.restore();
      }
    }

    // queue depth, 0..4, per context
    const qTop = queueY(0);
    g.fillStyle = fg3; g.font = "600 9.5px " + mono; g.textAlign = "left";
    g.fillText("QUEUE", 8, qTop - 5);
    const lb = (a: Int32Array, x: number) => lowerBound(a, x + 1); // count of values <= x
    idx.lanes.forEach((k, li) => {
      const y = queueY(li), q = idx.queue[li];
      g.fillStyle = ctxCol[k]; g.textAlign = "right"; g.font = "500 9px " + mono;
      g.fillText("ctx " + k, LEFT - 12, y + QH - 3);
      g.strokeStyle = line; g.beginPath(); g.moveTo(LEFT, y + QH - 0.5); g.lineTo(w - 10, y + QH - 0.5); g.stroke();
      g.fillStyle = ctxCol[k]; g.globalAlpha = 0.55;
      for (let x = LEFT; x < w - 10; x += 2) {
        const t = tx(x);
        const n = lb(q.c, t) - lb(q.d, t);
        if (n > 0) g.fillRect(x, y + QH - 1 - (n / 4) * (QH - 2), 2, (n / 4) * (QH - 2));
      }
      g.globalAlpha = 1;
    });

    // event arrows: every visible one when zoomed in, the selected one always
    const arrow = (i: number, strong: boolean) => {
      const cm = run.commits[i];
      const ev = cm[4];
      if (!isEv(ev)) return;
      const li = laneOf.get(cm[1]);
      if (li == null) return;
      const x0 = sx(cm[0] + 1.5), y0 = laneY(li) + 2;
      const xd = sx(ev[1]);
      const targets = rowsOf(cm[1], ev);
      const col = strong ? brass : ctxCol[cm[1]];
      g.strokeStyle = col; g.fillStyle = col;
      g.globalAlpha = strong ? 1 : 0.35; g.lineWidth = strong ? 1.6 : 1;
      for (const r of targets.length ? targets : [Math.max(0, rows - 1)]) {
        const y1 = TOP + r * RH + RH / 2;
        g.beginPath(); g.moveTo(x0, y0);
        g.bezierCurveTo(x0, (y0 + y1) / 2, xd, (y0 + y1) / 2, xd, y1 + 6);
        g.stroke();
        g.beginPath(); g.arc(xd, y1 + 6, strong ? 3.5 : 2, 0, Math.PI * 2); g.fill();
      }
      g.globalAlpha = 1; g.lineWidth = 1;
      if (strong) {
        g.setLineDash([3, 3]); g.beginPath(); g.moveTo(xd + 0.5, TOP - 4); g.lineTo(xd + 0.5, laneEnd); g.stroke(); g.setLineDash([]);
      }
    };
    if (span <= 700) for (let i = i0; i < i1; i++) if (i !== selected) arrow(i, false);
    if (selected != null && selected < run.commits.length) arrow(selected, true);

    // the first fault
    if (run.fault) {
      const X = Math.round(sx(run.fault.tick)) + 0.5;
      if (X >= LEFT && X <= w) {
        g.strokeStyle = bad; g.lineWidth = 2; g.beginPath(); g.moveTo(X, TOP - 6); g.lineTo(X, laneEnd); g.stroke(); g.lineWidth = 1;
        g.fillStyle = bad; g.font = "700 10px " + mono; g.textAlign = "left";
        g.fillText("FAULT " + run.fault.cause + (run.fault.subcode ? " " + run.fault.subcode : ""), Math.min(X + 4, w - 130), TOP + 4);
      }
    }

    if (hover) {
      const X = Math.round(sx(hover.t)) + 0.5;
      g.strokeStyle = fg3; g.beginPath(); g.moveTo(X, TOP - 6); g.lineTo(X, laneEnd); g.stroke();
    }

    // overview of the whole run, with the visible window
    const oy = queueY(idx.lanes.length) + 12;
    const ox = (t: number) => LEFT + (t / run.span) * (w - LEFT - 10);
    g.fillStyle = css("--bg-3"); g.fillRect(LEFT, oy, w - LEFT - 10, OV);
    const s0 = run.signals[0];
    if (s0 && s0.w.length) {
      g.strokeStyle = fg3; g.beginPath();
      let v = s0.w[0][1];
      g.moveTo(ox(0), oy + (v ? 5 : OV - 5));
      for (let k = 1; k < s0.w.length; k++) {
        const X = ox(s0.w[k][0]);
        g.lineTo(X, oy + (v ? 5 : OV - 5)); v = s0.w[k][1]; g.lineTo(X, oy + (v ? 5 : OV - 5));
      }
      g.lineTo(ox(run.span), oy + (v ? 5 : OV - 5)); g.stroke();
    }
    const wx = ox(t0), ww = Math.max(3, ox(t1) - ox(t0));
    g.fillStyle = brass; g.globalAlpha = 0.22; g.fillRect(wx, oy, ww, OV);
    g.globalAlpha = 1; g.strokeStyle = brass; g.strokeRect(wx + 0.5, oy + 0.5, ww - 1, OV - 1);
    g.fillStyle = fg3; g.textAlign = "right"; g.font = "600 9.5px " + mono;
    g.fillText("WHOLE RUN", LEFT - 12, oy + 15);
  };
  useEffect(draw);
  useResize(draw, [run, view, selected, hover, hitPcs, theme]);

  // ---- interaction
  const clampView = (a: number, b: number): View => {
    const max = run.span;
    const span = Math.max(Math.min(24, max), Math.min(max, b - a));
    a = Math.max(0, Math.min(max - span, a));
    return [a, a + span];
  };
  const zoom = (f: number, at?: number) => {
    const [t0, t1] = view;
    const c = at ?? (t0 + t1) / 2;
    setView(clampView(c - (c - t0) * f, c + (t1 - c) * f));
  };
  const drag = useRef<{ x: number; v: View; moved: boolean; overview: boolean } | null>(null);
  const pick = (x: number, y: number) => {
    const { tx, laneY } = geom();
    const t = Math.round(tx(x));
    const top = laneY(0);
    let lane: number | null = null, i: number | null = null;
    if (y >= top && y < laneY(idx.lanes.length)) {
      lane = Math.floor((y - top) / LH);
      const k = idx.lanes[lane];
      // that context's commit whose 4-tick slot contains t
      for (let j = lowerBound(idx.ticks, t - 3); j < run.commits.length && run.commits[j][0] <= t; j++) {
        if (run.commits[j][1] === k) { i = j; break; }
      }
    }
    return { t, lane, i };
  };
  const ovGeom = () => {
    const { w, queueY } = geom();
    return { oy: queueY(idx.lanes.length) + 12, toT: (x: number) => ((x - LEFT) / (w - LEFT - 10)) * run.span };
  };

  // wheel: Ctrl/⌘ zooms, Shift or a sideways swipe pans; a plain wheel scrolls the page
  const wheel = useRef<(e: WheelEvent) => void>(() => {});
  wheel.current = (e: WheelEvent) => {
    const c = ref.current!;
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      zoom(Math.exp(e.deltaY * 0.002), geom().tx(e.clientX - r.left));
    } else if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
      e.preventDefault();
      const s = view[1] - view[0];
      const dt = ((e.deltaX || e.deltaY) / 600) * s;
      setView(clampView(view[0] + dt, view[1] + dt));
    }
  };
  useEffect(() => {
    const c = ref.current!;
    const f = (e: WheelEvent) => wheel.current(e);
    c.addEventListener("wheel", f, { passive: false });
    return () => c.removeEventListener("wheel", f);
  }, []);

  const describe = (c: Commit | null) => {
    if (!c) return "";
    const [t, k, pc, o, x] = c;
    let s = `tick ${t} · ctx ${k} · pc ${pc} · ${text.get(pc) || ""}`;
    if (o === "W") s += " · waits";
    if (o === "F" && x) s += ` · FAULT ${x[0]} ${x[1]}`;
    if (o === "C" && isEv(x)) s += ` → ${EVENT_KIND[x[0]]} at tick ${x[1]}, lead ${x[7]}`;
    return s;
  };
  const hv = hover?.i != null ? run.commits[hover.i] : null;
  const sel = selected != null ? run.commits[selected] : null;
  const info = hv ? describe(hv) : hover ? `tick ${hover.t} · ${fmtTime(hover.t)}` : sel ? describe(sel)
    : "Drag to pan · Ctrl/⌘ + wheel or the buttons to zoom · click a commit, or an edge to find its commit";

  return (
    <div className="wave-view">
      <div className="wave-tools">
        <button className="icon-btn" type="button" onClick={() => zoom(0.5)} aria-label="Zoom in">+</button>
        <button className="icon-btn" type="button" onClick={() => zoom(2)} aria-label="Zoom out">−</button>
        <button className="icon-btn" type="button" onClick={() => setView([0, run.span])}>whole run</button>
        {sel && isEv(sel[4]) && (
          <button className="icon-btn" type="button" onClick={() => {
            const d = (sel[4] as Ev)[1];
            const a = Math.min(sel[0], d), b = Math.max(sel[0], d);
            const pad = Math.max(16, (b - a) * 0.4);
            setView(clampView(a - pad, b + pad));
          }}>zoom to commit and edge</button>
        )}
        <span className="mono wave-span">{fmtTime(view[1] - view[0])} shown · {Math.round(view[1] - view[0])} ticks</span>
      </div>
      <div className="wave-canvas">
        <canvas ref={ref} aria-label="Waveforms and commits"
          onPointerDown={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            e.currentTarget.setPointerCapture(e.pointerId);
            const ov = ovGeom();
            const overview = e.clientY - r.top >= ov.oy;
            drag.current = { x: e.clientX, v: view, moved: false, overview };
            if (overview) {
              const t = ov.toT(e.clientX - r.left), s = view[1] - view[0];
              setView(clampView(t - s / 2, t + s / 2));
            }
          }}
          onPointerMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            const x = e.clientX - r.left, y = e.clientY - r.top;
            const d = drag.current;
            if (d) {
              if (Math.abs(e.clientX - d.x) > 3) d.moved = true;
              if (d.overview) {
                const t = ovGeom().toT(x), s = view[1] - view[0];
                setView(clampView(t - s / 2, t + s / 2));
              } else if (d.moved) {
                const dt = ((e.clientX - d.x) / (geom().w - LEFT - 10)) * (d.v[1] - d.v[0]);
                setView(clampView(d.v[0] - dt, d.v[1] - dt));
              }
              return;
            }
            setHover(x < LEFT ? null : pick(x, y));
          }}
          onPointerUp={(e) => {
            const d = drag.current;
            drag.current = null;
            if (!d || d.moved || d.overview) return;
            const r = e.currentTarget.getBoundingClientRect();
            const x = e.clientX - r.left, y = e.clientY - r.top;
            if (x < LEFT) return;
            const p = pick(x, y);
            if (p.i != null) { onSelect(p.i === selected ? null : p.i); return; }
            // on a signal row: the commit that scheduled the nearest edge
            const row = Math.floor((y - TOP) / RH);
            if (y >= TOP && row < rows) {
              const { sx } = geom();
              let best: number | null = null, bd = 8;
              for (const [t] of run.signals[row].w) {
                const dx = Math.abs(sx(t) - x);
                if (dx < bd) { bd = dx; best = t; }
              }
              if (best != null) {
                const cands = idx.byDeadline.get(best) || [];
                const own = cands.find((i) => rowsOf(run.commits[i][1], run.commits[i][4] as Ev).includes(row));
                onSelect(own ?? cands[0] ?? null);
                return;
              }
            }
            onSelect(null);
          }}
          onPointerLeave={() => setHover(null)}
        />
      </div>
      <div className="wave-info mono" aria-live="polite">{info}</div>
    </div>
  );
}
