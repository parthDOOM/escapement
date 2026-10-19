import { useEffect, useRef, useState } from "react";
import { css, fit, useResize, useThemeTick } from "../lib/util";

const T0 = 94, T1 = 132, COMMIT = 100, TAIL = 112;

/** Drag a new event's deadline against the commit tick and the event already queued. */
export default function Deadline() {
  const ref = useRef<HTMLCanvasElement>(null);
  const [d, setD] = useState(120);
  const theme = useThemeTick();
  const verdict = d <= COMMIT ? "PAST" : d <= TAIL ? "ORDER" : "ok";

  const draw = () => {
    const c = ref.current;
    if (!c) return;
    const { g, w, h } = fit(c, 220);
    const x = (t: number) => 24 + ((t - T0) / (T1 - T0)) * (w - 48);
    g.clearRect(0, 0, w, h);
    const fg3 = css("--fg-3"), line = css("--line"), fg = css("--fg"), mono = css("--font-mono");
    const col = verdict === "ok" ? css("--ok") : css("--bad");
    g.strokeStyle = line; g.fillStyle = fg3; g.font = "500 10px " + mono; g.textAlign = "center";
    g.beginPath();
    for (let t = T0; t <= T1; t++) {
      const X = Math.round(x(t)) + 0.5;
      g.moveTo(X, 40); g.lineTo(X, t % 4 === 0 ? 30 : 35);
      if (t % 4 === 0) g.fillText(String(t), X, 22);
    }
    g.moveTo(24, 40.5); g.lineTo(w - 24, 40.5);
    g.stroke();
    // the slots of this context: it commits only every fourth tick
    const xc = x(COMMIT);
    g.strokeStyle = fg3; g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(xc, 44); g.lineTo(xc, 168); g.stroke(); g.setLineDash([]);
    g.fillStyle = fg3; g.textAlign = "left"; g.fillText("EMIT commits", xc + 5, 58);
    const xt = x(TAIL);
    g.fillStyle = css("--bg-3"); g.strokeStyle = fg3;
    g.fillRect(xt - 6, 66, 12, 18); g.strokeRect(xt - 5.5, 66.5, 11, 17);
    g.fillStyle = fg3; g.fillText("queued", xt + 9, 79);
    const xd = x(d);
    if (verdict === "ok") {
      g.fillStyle = css("--ok") + "22";
      g.fillRect(xc, 88, xd - xc, 10);
      g.fillStyle = fg3; g.textAlign = "center";
      g.fillText("lead " + (d - COMMIT), (xc + xd) / 2, 86);
      g.textAlign = "left";
    }
    g.strokeStyle = col; g.lineWidth = 2;
    g.beginPath(); g.moveTo(xd, 92); g.lineTo(xd, 168); g.stroke(); g.lineWidth = 1;
    g.fillStyle = col; g.beginPath(); g.arc(xd, 104, 7, 0, Math.PI * 2); g.fill();
    g.fillStyle = fg; g.fillText("deadline " + d, xd + 11, 108);
    const y0 = 196, y1 = 172;
    g.strokeStyle = css("--wave"); g.lineWidth = 2;
    g.beginPath();
    g.moveTo(24, y0); g.lineTo(xt, y0); g.lineTo(xt, y1);
    if (verdict === "ok") { g.lineTo(xd, y1); g.lineTo(xd, y0); g.lineTo(w - 24, y0); }
    else g.lineTo(w - 24, y1);
    g.stroke(); g.lineWidth = 1;
    g.fillStyle = fg3; g.fillText("pin", 2, y0 - 8);
  };
  useEffect(draw, [d, theme]);
  useResize(draw, [d]);

  const drag = useRef(false);
  const setFrom = (ev: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    const t = T0 + ((ev.clientX - r.left - 24) / (r.width - 48)) * (T1 - T0);
    setD(Math.max(T0 + 1, Math.min(T1 - 1, Math.round(t))));
  };
  return (
    <figure className="panel deadline">
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
      <div className="verdict" aria-live="polite">
        {verdict === "ok" ? (
          <>
            <span className="pill ok">admitted</span>
            <span>lead {d - COMMIT} ticks</span>
            <span>the edge lands at tick {d}, {(d * 40).toLocaleString()} ns, exactly</span>
          </>
        ) : (
          <>
            <span className="pill bad">refused · ADMIT {verdict}</span>
            <span>
              {verdict === "PAST" ? "the deadline is not after the commit tick" : "the deadline is not after the event already queued"}
            </span>
            <span>the context faults and releases its pins; the others run on</span>
          </>
        )}
      </div>
      <figcaption>
        Ticks are 40 ns. The instruction commits at tick 100 and one event is already queued for tick
        112. Drag, or use the arrow keys when the figure has focus.
      </figcaption>
    </figure>
  );
}
