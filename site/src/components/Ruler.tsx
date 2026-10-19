import { useEffect, useRef } from "react";
import { css, fit, useResize, useThemeTick } from "../lib/util";

/** A strip of 40 ns ticks between sections; tick numbers continue down the page. */
export default function Ruler({ n }: { n: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const theme = useThemeTick();
  const draw = () => {
    const c = ref.current;
    if (!c) return;
    const { g, w } = fit(c, 34);
    g.clearRect(0, 0, w, 34);
    g.strokeStyle = css("--line");
    g.fillStyle = css("--fg-3");
    g.font = "500 10px " + css("--font-mono");
    const step = 6,
      base = n * 1000;
    g.beginPath();
    for (let k = 0, x = 0.5; x < w; k++, x += step) {
      const t = base + k,
        major = t % 25 === 0; // 25 ticks = 1 µs
      g.moveTo(x, 34);
      g.lineTo(x, major ? 14 : t % 5 === 0 ? 24 : 28);
      if (major && x + 40 < w) g.fillText((t * 0.04).toFixed(0) + " µs", x + 3, 11);
    }
    g.stroke();
  };
  useEffect(draw, [theme]);
  useResize(draw);
  return (
    <div className="ruler" aria-hidden="true">
      <canvas ref={ref} />
    </div>
  );
}
