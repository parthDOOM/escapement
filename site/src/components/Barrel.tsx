import { useEffect, useMemo, useRef, useState } from "react";
import { css, CTX_VARS, fit, reduced, useJson, useResize, useThemeTick } from "../lib/util";
import type { Run } from "../playground/types";

const N = 160;
const OUT: Record<string, string> = { C: "completes", W: "waits, and is tried again in its next slot", F: "faults" };

/** The first 160 ticks of four protocols at once: who commits in which slot. */
export default function Barrel() {
  const [run] = useJson<Run>("data/runs/combo.json");
  const ref = useRef<HTMLCanvasElement>(null);
  const [at, setAt] = useState<number | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const theme = useThemeTick();
  const geo = useRef({ L: 58, cw: 1 });

  const text = useMemo(() => {
    const m = new Map<number, string>();
    run?.listing.forEach(([pc, , dis]) => m.set(pc, dis.split(";")[0].trim()));
    return m;
  }, [run]);
  const commits = useMemo(() => (run ? run.commits.filter((c) => c[0] < N) : []), [run]);
  const byT = useMemo(() => new Map(commits.map((c) => [c[0], c])), [commits]);

  const draw = () => {
    const c = ref.current;
    if (!c || !run) return;
    const width = Math.max(c.parentElement!.clientWidth, 880);
    c.style.width = width + "px";
    const { g, w, h } = fit(c, 230);
    const L = 58, T = 26, rows = 4, cw = (w - L - 8) / N, rh = (h - T - 30) / rows;
    geo.current = { L, cw };
    g.clearRect(0, 0, w, h);
    const mono = css("--font-mono"), fg3 = css("--fg-3"), line = css("--line");
    g.font = "500 10px " + mono; g.textAlign = "center"; g.fillStyle = fg3; g.strokeStyle = line;
    g.beginPath();
    for (let t = 0; t <= N; t += 4) {
      const X = Math.round(L + t * cw) + 0.5;
      g.moveTo(X, T - 4); g.lineTo(X, T + rows * rh);
      if (t % 20 === 0) g.fillText(String(t), X, 14);
    }
    g.stroke();
    for (let k = 0; k < rows; k++) {
      g.fillStyle = css(CTX_VARS[k]); g.textAlign = "right";
      g.fillText("ctx " + k, L - 10, T + k * rh + rh / 2 + 4);
    }
    const limit = at ?? Infinity;
    commits.forEach(([t, k, , o]) => {
      if (t > limit) return;
      const col = css(CTX_VARS[k]);
      const X = L + t * cw, Y = T + k * rh + 5;
      if (o === "C") { g.fillStyle = col; g.fillRect(X + 0.5, Y, Math.max(cw - 1, 1.5), rh - 10); }
      else { g.strokeStyle = col; g.strokeRect(X + 1, Y + 0.5, Math.max(cw - 2, 1), rh - 11); }
    });
    const mark = at ?? hover;
    if (mark != null) {
      const X = Math.round(L + (mark + 0.5) * cw) + 0.5;
      g.strokeStyle = css("--fg"); g.beginPath(); g.moveTo(X, T - 6); g.lineTo(X, T + rows * rh + 2); g.stroke();
    }
    g.fillStyle = fg3; g.textAlign = "left";
    g.fillText("tick (commit)", L, h - 8);
  };
  useEffect(draw, [run, at, hover, theme]);
  useResize(draw, [run, at, hover]);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setAt((a) => {
        const n = a == null || a >= N - 1 ? 0 : a + 1;
        if (n >= N - 1) setPlaying(false);
        return n;
      });
    }, reduced() ? 1 : 70);
    return () => clearInterval(id);
  }, [playing]);

  const t = at ?? hover ?? (commits[0]?.[0] ?? 0);
  const x = byT.get(t);
  const readout = x ? `tick ${t} · ctx ${x[1]} · ${text.get(x[2]) ?? "pc " + x[2]} · ${OUT[x[3]]}` : `tick ${t} · no commit`;

  return (
    <figure className="panel">
      <div className="barrel">
        <canvas ref={ref} aria-label="Commits per context over the first 160 ticks"
          onPointerMove={(e) => {
            if (playing) return;
            const r = e.currentTarget.getBoundingClientRect();
            const tt = Math.floor((e.clientX - r.left - geo.current.L) / geo.current.cw);
            setHover(tt >= 0 && tt < N ? tt : null);
          }}
          onPointerLeave={() => setHover(null)} />
      </div>
      <div className="controls">
        <button className="icon-btn" type="button" onClick={() => {
          if (playing) { setPlaying(false); return; }
          setAt((a) => (a == null || a >= N - 1 ? 0 : a));
          setPlaying(true);
        }}>
          {playing ? "pause" : at != null && at >= N - 1 ? "replay" : "play"}
        </button>
        {at != null && !playing && (
          <button className="icon-btn" type="button" onClick={() => setAt(null)}>show all</button>
        )}
        <span className="mono" aria-live="polite">{run ? readout : "loading…"}</span>
      </div>
      <figcaption>
        From the instruction-set simulator running <code>combo_two_banks.asm</code>. A filled cell is an
        instruction that completes; a hollow one waits, for example on a full queue, and is tried again in
        that context's next slot. No other context ever uses the slot.
      </figcaption>
    </figure>
  );
}
