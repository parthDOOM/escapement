import { useEffect, useRef, useState } from "react";
import { css, fit, reduced, useResize, useThemeTick, useVisible } from "../lib/util";

const T0 = 94, T1 = 132, COMMIT = 100, TAIL = 112;
const SWEEP_MS = 4200, HOLD_MS = 1100;

/** Drag a new event's deadline against the commit tick and the event already queued. A
 *  cursor plays the ticks: an admitted event retires on its exact tick; a refused one
 *  faults the context at the commit, which flushes its queue and releases its pins
 *  (docs/11-SEMANTICS.md §8), so the edge queued for tick 112 never happens either. */
export default function Deadline() {
  const ref = useRef<HTMLCanvasElement>(null);
  const [d, setD] = useState(120);
  const theme = useThemeTick();
  const [box, visible] = useVisible<HTMLElement>(true, "0px");
  const verdict = d <= COMMIT ? "PAST" : d <= TAIL ? "ORDER" : "ok";
  const now = useRef(T1);

  const draw = () => {
    const c = ref.current;
    if (!c) return;
    const { g, w, h } = fit(c, 220);
    const x = (t: number) => 24 + ((t - T0) / (T1 - T0)) * (w - 48);
    const t = now.current, ok = verdict === "ok";
    g.clearRect(0, 0, w, h);
    const fg3 = css("--fg-3"), line = css("--line"), fg = css("--fg"), mono = css("--font-mono");
    const okc = css("--ok"), bad = css("--bad");
    g.strokeStyle = line; g.fillStyle = fg3; g.font = "500 10px " + mono; g.textAlign = "center";
    g.beginPath();
    for (let k = T0; k <= T1; k++) {
      const X = Math.round(x(k)) + 0.5;
      g.moveTo(X, 40); g.lineTo(X, k % 4 === 0 ? 30 : 35);
      if (k % 4 === 0) g.fillText(String(k), X, 22);
    }
    g.moveTo(24, 40.5); g.lineTo(w - 24, 40.5);
    g.stroke();

    // the commit: this context commits only every fourth tick
    const xc = x(COMMIT), committed = t >= COMMIT;
    g.strokeStyle = fg3; g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(xc, 44); g.lineTo(xc, 168); g.stroke(); g.setLineDash([]);
    g.textAlign = "left";
    if (committed && !ok) {
      g.fillStyle = bad; g.font = "600 10px " + mono; g.fillText("FAULT " + verdict, xc + 5, 58);
      g.font = "500 10px " + mono;
    } else { g.fillStyle = fg3; g.fillText("EMIT commits", xc + 5, 58); }

    // the event already queued: flushed by a fault, else it retires at 112
    const xt = x(TAIL), flushed = !ok && committed, fired = ok && t >= TAIL;
    g.fillStyle = fired ? okc : css("--bg-3"); g.strokeStyle = flushed ? bad : fg3;
    g.fillRect(xt - 6, 66, 12, 18); g.strokeRect(xt - 5.5, 66.5, 11, 17);
    if (flushed) { g.beginPath(); g.moveTo(xt - 8, 64); g.lineTo(xt + 8, 86); g.stroke(); }
    g.fillStyle = flushed ? bad : fg3;
    g.fillText(flushed ? "flushed" : fired ? "retired" : "queued", xt + 9, 79);

    // the new event and its lead
    const xd = x(d);
    if (ok) {
      g.fillStyle = okc + "22";
      g.fillRect(xc, 88, xd - xc, 10);
      g.fillStyle = fg3; g.textAlign = "center";
      const xm = (xc + xd) / 2;                    // above the bar, unless the queued box is there
      g.fillText("lead " + (d - COMMIT), xm, Math.abs(xm - xt) < 34 ? 111 : 86);
      g.textAlign = "left";
    }
    const col = ok ? okc : bad;
    g.strokeStyle = col; g.lineWidth = 2;
    g.beginPath(); g.moveTo(xd, 92); g.lineTo(xd, 168); g.stroke(); g.lineWidth = 1;
    g.fillStyle = col; g.beginPath(); g.arc(xd, 104, 7, 0, Math.PI * 2);
    if (ok && t >= d) g.fill(); else { g.lineWidth = 2; g.stroke(); g.lineWidth = 1; }
    g.fillStyle = fg; g.fillText("deadline " + d, xd + 11, 108);

    // the pin: the plan faint, what has happened so far solid
    const y0 = 196, y1 = 172, xe = w - 24;
    const xn = Math.min(Math.max(x(t), 24), xe);
    const path = () => {
      g.beginPath(); g.moveTo(24, y0);
      if (ok) { g.lineTo(xt, y0); g.lineTo(xt, y1); g.lineTo(xd, y1); g.lineTo(xd, y0); g.lineTo(xe, y0); }
      else g.lineTo(xc, y0);
    };
    const wave = css("--wave");
    g.save(); g.globalAlpha = 0.28; g.strokeStyle = wave; g.lineWidth = 2; path(); g.stroke(); g.restore();
    g.save(); g.beginPath(); g.rect(0, 150, xn, 70); g.clip();
    g.strokeStyle = wave; g.lineWidth = 2; path(); g.stroke();
    if (!ok) {                                   // released: the pin floats to its pull-up
      g.setLineDash([5, 4]); g.beginPath(); g.moveTo(xc, y0); g.lineTo(xc, y1); g.lineTo(xe, y1); g.stroke();
      g.setLineDash([]);
    }
    g.restore();
    if (!ok && t >= COMMIT) { g.fillStyle = fg3; g.fillText("released", xc + 6, y1 - 6); }
    g.fillStyle = fg3; g.fillText("pin", 2, y0 - 8);

    // the cursor
    if (t < T1) {
      g.strokeStyle = fg; g.globalAlpha = 0.55;
      g.beginPath(); g.moveTo(xn + 0.5, 42); g.lineTo(xn + 0.5, 206); g.stroke();
      g.globalAlpha = 1; g.fillStyle = fg; g.textAlign = "center"; g.fillText("now", xn, 216);
    }
  };
  useEffect(draw, [d, theme]);
  useResize(draw, [d]);

  // play the ticks while the figure is on screen
  const drawRef = useRef(draw);
  drawRef.current = draw;
  useEffect(() => {
    if (!visible || reduced()) { now.current = T1; drawRef.current(); return; }
    let raf = 0, t0 = performance.now();
    const step = (ms: number) => {
      const e = (ms - t0) % (SWEEP_MS + HOLD_MS);
      now.current = T0 + Math.min(1, e / SWEEP_MS) * (T1 - T0);
      drawRef.current();
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => { cancelAnimationFrame(raf); now.current = T1; };
  }, [visible]);

  const drag = useRef(false);
  const setFrom = (ev: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    const t = T0 + ((ev.clientX - r.left - 24) / (r.width - 48)) * (T1 - T0);
    setD(Math.max(T0 + 1, Math.min(T1 - 1, Math.round(t))));
  };
  return (
    <figure className="panel deadline" ref={box}>
      <canvas
        ref={ref}
        tabIndex={0}
        role="slider"
        aria-valuemin={T0 + 1}
        aria-valuemax={T1 - 1}
        aria-valuenow={d}
        aria-label="Deadline of the new event, in ticks"
        onPointerDown={(e) => { drag.current = true; e.currentTarget.setPointerCapture(e.pointerId); setFrom(e); }}
        onPointerMove={(e) => drag.current && setFrom(e)}
        onPointerUp={() => (drag.current = false)}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
            setD((v) => Math.max(T0 + 1, Math.min(T1 - 1, v + (e.key === "ArrowLeft" ? -1 : 1))));
            e.preventDefault();
          }
        }}
      />
      {/* both verdicts share one cell, so dragging between them never moves the page */}
      <div className="verdict-stack" aria-live="polite">
        <div className="verdict" hidden={verdict !== "ok"}>
          <span className="pill ok">admitted</span>
          <span>lead {Math.max(d - COMMIT, 1)} ticks</span>
          <span>the edge lands at tick {d}, {(d * 40).toLocaleString()} ns, exactly</span>
        </div>
        <div className="verdict" hidden={verdict === "ok"}>
          <span className="pill bad">refused · ADMIT {verdict === "ok" ? "ORDER" : verdict}</span>
          <span>
            {verdict === "PAST" ? "the deadline is not after the commit tick" : "the deadline is not after the event already queued"}
          </span>
          <span>the context faults at tick {COMMIT}: its queue is flushed, so the edge queued for {TAIL} never
            happens, and its pins are released; the others run on</span>
        </div>
      </div>
      <figcaption>
        Ticks are 40 ns. The instruction commits at tick 100 and one event is already queued for tick
        112. Drag, or use the arrow keys when the figure has focus.
      </figcaption>
    </figure>
  );
}
