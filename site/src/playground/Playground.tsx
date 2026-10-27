// The firmware playground: the stock runs load instantly from JSON; editing or changing a
// parameter starts the real assembler and simulator in a Python worker.
import { useEffect, useMemo, useRef, useState } from "react";
import { json, useJson, fmtTime, hex } from "../lib/util";
import { loadPython, py, usePyStatus } from "../lib/py";
import Editor from "./Editor";
import WaveView, { View } from "./WaveView";
import type { Commit, Ev, Run, RunError, Scenario } from "./types";
import { EVENT_KIND } from "./types";
import SmoothHeight from "../lib/SmoothHeight";

const FAULTS: Record<string, string> = {
  ILLEGAL: "The word at this address is not an instruction, or has reserved bits set.",
  PC_RANGE: "The program ran past the last word of program memory.",
  "ADMIT MASK": "An event names no pins.",
  "ADMIT OWNER": "The event drives a pin this context does not own (see .own).",
  "ADMIT PAST": "The deadline is not after the commit tick: the program fell behind its own schedule. The chip refuses the edge rather than make it late.",
  "ADMIT ORDER": "The deadline is not after the event already at the tail of the queue.",
  "ADMIT OVERLAP": "This EXPECT window starts before the previous one has ended.",
  LATE_HEAD: "A queue head passed its deadline. This is proved impossible.",
};

function autoView(run: Run): View {
  let a = Infinity, b = 0;
  for (const s of run.signals) {
    if (s.w.length > 1) { a = Math.min(a, s.w[1][0]); b = Math.max(b, s.w[s.w.length - 1][0]); }
  }
  if (run.fault) { a = Math.min(a, run.fault.tick); b = Math.max(b, run.fault.tick); }
  if (!isFinite(a)) return [0, run.span];
  const pad = Math.max(40, (b - a) * 0.04);
  return [Math.max(0, a - pad), Math.min(run.span, b + pad)];
}

const NONE: number[] = [];

type Regs = { t: number; ctx: number; pc: number; insn: string; text: string; outcome: string;
  post: Record<string, number> };

export default function Playground() {
  const [scenarios] = useJson<Scenario[]>("data/runs/index.json");
  const [sid, setSid] = useState("uart");
  const sc = scenarios?.find((s) => s.id === sid);
  const [stock, setStock] = useState<Record<string, Run>>({});
  const [sources, setSources] = useState<Record<string, string>>({});
  const [params, setParams] = useState<Record<string, Record<string, string>>>({});
  const [run, setRun] = useState<Run | null>(null);
  const [live, setLive] = useState(false);
  const [err, setErr] = useState<RunError | null>(null);
  const [busy, setBusy] = useState(false);
  const [ms, setMs] = useState<number | null>(null);
  const [view, setView] = useState<View>([0, 1]);
  const [selected, setSelected] = useState<number | null>(null);
  const [cursorLine, setCursorLine] = useState<number | null>(null);
  const [regs, setRegs] = useState<Regs | null>(null);
  const status = usePyStatus();
  const ready = status === "ready";
  const [request, setRequest] = useState<{ sid: string; edit?: [string, string] } | null>(null);
  const [autoRun, setAutoRun] = useState(false);

  // "try it" links elsewhere on the page: open a scenario, apply an edit, run it
  useEffect(() => {
    const take = () => {
      const r = (window as any).__escPlay;
      if (!r) return;
      (window as any).__escPlay = null;
      setSid(r.sid);
      setRequest(r);
    };
    take();
    addEventListener("esc:play", take);
    return () => removeEventListener("esc:play", take);
  }, []);

  // the stock run of each scenario, fetched once
  useEffect(() => {
    if (stock[sid]) return;
    json<Run>(`data/runs/${sid}.json`).then((r) => setStock((s) => ({ ...s, [sid]: r })));
  }, [sid, stock]);
  const source = sources[sid] ?? stock[sid]?.source ?? "";
  const p = useMemo(() => {
    const d: Record<string, string> = {};
    sc?.params.forEach((q) => (d[q.key] = q.default));
    return { ...d, ...(params[sid] || {}) };
  }, [sc, params, sid]);
  const edited = !!stock[sid] && (source !== stock[sid].source ||
    (sc?.params || []).some((q) => p[q.key] !== q.default));

  // show the stock run when switching scenario (unless an edited run is wanted)
  useEffect(() => {
    const r = stock[sid];
    if (!r) return;
    setRun(r); setLive(false); setErr(null); setSelected(null); setRegs(null); setMs(null);
    setView(autoView(r));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, stock[sid]]);

  const doRun = async (keep = false) => {
    if (!sc || busy) return;
    setBusy(true);
    try {
      await loadPython();
      const t0 = performance.now();
      const r = await py.run(source, sid, p);
      setMs(performance.now() - t0);
      if (r.ok) {
        setRun(r); setLive(true); setErr(null); setRegs(null);
        if (!keep || (selected != null && selected >= r.commits.length)) { setSelected(null); setView(autoView(r)); }
      } else setErr(r);
    } catch (e) {
      setErr({ ok: false, error: String(e), line: null });
    } finally {
      setBusy(false);
    }
  };
  const runRef = useRef(doRun);
  runRef.current = doRun;

  useEffect(() => {
    if (!request || !stock[request.sid] || request.sid !== sid) return;
    if (request.edit) {
      const [a, b] = request.edit;
      setSources((s) => ({ ...s, [sid]: stock[sid].source!.replace(a, b) }));
      setAutoRun(true);
    }
    setRequest(null);
  }, [request, stock, sid]);
  useEffect(() => {
    if (!autoRun) return;
    setAutoRun(false);
    doRun();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun]);

  // registers after the selected commit come from the live simulator
  useEffect(() => {
    setRegs(null);
    if (selected == null || !live || !ready) return;
    let ok = true;
    py.regsAt(selected).then((r) => ok && setRegs(r));
    return () => { ok = false; };
  }, [selected, live, ready]);

  const text = useMemo(() => {
    const m = new Map<number, string>();
    run?.listing.forEach(([pc, , dis]) => m.set(pc, dis));
    return m;
  }, [run]);
  const lineOf = useMemo(() => {
    const m = new Map<number, number>();
    run?.listing.forEach(([pc, , , where]) => where && where[0] === run.name && m.set(pc, where[1]));
    return m;
  }, [run]);
  const hitPcs = useMemo(() => {
    const s = new Set<number>();
    if (cursorLine == null) return s;
    lineOf.forEach((ln, pc) => ln === cursorLine && s.add(pc));
    return s;
  }, [cursorLine, lineOf]);
  const sel: Commit | null = selected != null && run ? run.commits[selected] ?? null : null;
  const focusLine = sel ? lineOf.get(sel[2]) ?? null : null;

  // step through one context's commits, keeping the selection on screen
  const step = (dir: 1 | -1) => {
    if (!run || !run.commits.length) return;
    let i = selected;
    if (i == null) {
      i = run.commits.findIndex((c) => c[0] >= view[0]);
      if (i < 0) i = 0;
      setSelected(i);
    } else {
      const k = run.commits[i][1];
      let j = i + dir;
      while (j >= 0 && j < run.commits.length && run.commits[j][1] !== k) j += dir;
      if (j < 0 || j >= run.commits.length) return;
      i = j;
      setSelected(j);
    }
    const t = run.commits[i][0], span = view[1] - view[0];
    if (t < view[0] + span * 0.05 || t > view[1] - span * 0.05) {
      const a = Math.max(0, Math.min(run.span - span, t - span / 2));
      setView([a, a + span]);
    }
  };
  const certFor = (k: number) => run?.certs.find((c) => c.ctx === k);
  const faultKey = run?.fault ? (run.fault.cause + (run.fault.subcode ? " " + run.fault.subcode : "")) : "";

  return (
    <div className="pg">
      <div className="tabs" role="tablist" aria-label="Firmware">
        {(scenarios || []).map((s) => (
          <button key={s.id} type="button" role="tab" className="tab" aria-selected={s.id === sid}
            onClick={() => { setSid(s.id); setCursorLine(null); }}>
            {s.title}
          </button>
        ))}
      </div>

      {/* a different firmware has a different height: the page below slides, not jumps */}
      <SmoothHeight innerClassName="pg-body">
      <div className="pg-main">
        <div className="panel pg-editor">
          <div className="pg-bar">
            <span className="mono pg-file">{sc?.firmware ? "firmware/" + sc.firmware : "main.asm"}</span>
            {run && <span className="mono dim">{run.words} of 256 words</span>}
            <span className="grow" />
            {edited && (
              <button type="button" className="icon-btn" onClick={() => {
                setSources((s) => { const n = { ...s }; delete n[sid]; return n; });
                setParams((q) => { const n = { ...q }; delete n[sid]; return n; });
                const r = stock[sid];
                if (r) { setRun(r); setLive(false); setErr(null); setSelected(null); setView(autoView(r)); }
              }}>reset</button>
            )}
            <button type="button" className="run-btn" onClick={() => doRun()} disabled={busy || !sc}>
              {busy ? (ready ? "running…" : "loading…") : "Run"}
              <kbd>⌘↵</kbd>
            </button>
          </div>
          {stock[sid] ? <Editor value={source}
            onChange={(v) => setSources((s) => ({ ...s, [sid]: v }))}
            onRun={() => runRef.current()}
            onCursorLine={setCursorLine}
            onFocus={() => { if (status === "idle") loadPython().catch(() => {}); }}
            errorLine={err?.line ?? null}
            focusLine={focusLine}
            hitLines={NONE} /> : <div className="editor loading">loading the firmware…</div>}
          <div className="pg-status mono" aria-live="polite">
            {err ? <span className="bad">{err.error}</span>
              : busy ? <span>{ready ? "Simulating…" : status}</span>
              : live && ms != null ? <span className="ok">Assembled, certified and simulated {run?.span.toLocaleString()} ticks ({fmtTime(run?.span || 0)}) in {(ms / 1000).toFixed(2)} s, in your browser</span>
              : status.startsWith("error") ? <span className="bad">Python could not start: {status.slice(7)}</span>
              : status !== "idle" && !ready ? <span>{status}</span>
              : <span className="dim">{ready ? "Python is ready. Edit and run." : "The stock run, precomputed. Edit anything and press Run: the assembler and simulator start in your browser."}</span>}
          </div>
        </div>

        <div className="pg-side">
          <div className="panel">
            <h3>{sc?.title}</h3>
            <p className="dim small">{sc?.blurb}</p>
            {sc && sc.params.length > 0 && (
              <form className="params" onSubmit={(e) => { e.preventDefault(); doRun(); }}>
                {sc.params.map((q) => (
                  <label key={q.key}>
                    <span>{q.label}</span>
                    {q.type === "select" ? (
                      <select value={p[q.key]} onChange={(e) => {
                        setParams((s) => ({ ...s, [sid]: { ...(s[sid] || {}), [q.key]: e.target.value } }));
                        if (status === "idle") loadPython().catch(() => {});
                      }}>
                        {q.options!.map((o) => <option key={o}>{o}</option>)}
                      </select>
                    ) : (
                      <input value={p[q.key]} spellCheck={false} inputMode={q.type === "int" ? "numeric" : "text"}
                        onChange={(e) => {
                          setParams((s) => ({ ...s, [sid]: { ...(s[sid] || {}), [q.key]: e.target.value } }));
                          if (status === "idle") loadPython().catch(() => {});
                        }} />
                    )}
                  </label>
                ))}
                <button type="submit" className="icon-btn">run with these</button>
              </form>
            )}
          </div>

          {run && (
            <div className="panel">
              <p className="result">{run.result}</p>
              {run.leds && run.leds.length > 0 && (
                <div className="leds" aria-label="LED colours the decoder model received">
                  {run.leds.map((grb, i) => {
                    const g = (grb >> 16) & 255, r = (grb >> 8) & 255, b = grb & 255;
                    const k = 255 / Math.max(1, r, g, b);
                    return <i key={i} title={"GRB " + hex(grb, 6)}
                      style={{ background: `rgb(${Math.round(r * k)},${Math.round(g * k)},${Math.round(b * k)})` }} />;
                  })}
                </div>
              )}
              {run.errors.length > 0 && <p className="bad small mono">{run.errors.join("; ")}</p>}
              {run.fault && (
                <div className="fault">
                  <b>Context {run.fault.ctx} faulted: {faultKey}</b>
                  <span>at tick {run.fault.tick}, pc {run.fault.pc}{run.fault.line && run.fault.line[0] === run.name ? `, line ${run.fault.line[1]}` : ""}</span>
                  <p>{FAULTS[faultKey] || ""} Its pins are released; the other contexts run on.</p>
                </div>
              )}
              <div className="table-box"><table className="ctx-table">
                <thead>
                  <tr><th>ctx</th><th>state</th><th className="num" title="instructions completed / waited / faulted">C · W · F</th>
                    <th className="num" title="smallest lead the chip measured, in ticks">lead</th>
                    <th title="the static certificate from escert">certificate</th></tr>
                </thead>
                <tbody>
                  {run.contexts.map((c) => {
                    const cert = certFor(c.ctx);
                    return (
                      <tr key={c.ctx}>
                        <td><i className="dot" style={{ background: `var(--c${c.ctx})` }} />{c.ctx}{c.bank === "B" ? " B" : ""}</td>
                        <td className={c.state === "FAULTED" ? "bad" : ""}>{c.state.toLowerCase()}</td>
                        <td className="num">{c.C} · {c.W} · {c.F}</td>
                        <td className="num">{c.min_lead === 0xffff ? "–" : c.min_lead}</td>
                        <td>{cert ? <span className={"pill " + (cert.status === "CHECKED" ? "ok" : "bad")}
                          title={cert.status === "CHECKED" ? "Proved for every input: no admission fault, and every event at least this many ticks ahead" : (cert.findings || []).map((f) => `pc ${f[0]}: ${f[1]} ${f[2]}`).join("; ")}>
                          {cert.status === "CHECKED" ? "lead ≥ " + cert.min_lead : cert.status === "POSSIBLE" ? "may fault" : cert.status.toLowerCase()}</span> : "–"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table></div>
              {run.certs.filter((c) => c.status !== "CHECKED").map((c) => (
                <ul className="findings" key={c.ctx}>
                  {(c.findings || []).map(([pc, kind, note], i) => (
                    <li key={i}>escert, before running: context {c.ctx} <b>may fault {kind}</b> at pc {pc}
                      {lineOf.get(pc) ? `, line ${lineOf.get(pc)}` : ""} ({note})</li>
                  ))}
                  {c.note && <li>{c.note}</li>}
                </ul>
              ))}
              <p className="dim small">The certificate is proved before the run, for every input; the lead is what the
                chip measured during it. The measurement can never be below the bound.</p>
            </div>
          )}
        </div>
      </div>

      {run && (
        <div className="panel pg-wave">
          <WaveView run={run} view={view} setView={setView} selected={selected} onSelect={setSelected}
            hitPcs={hitPcs} text={text} onStep={step} />
        </div>
      )}

      {run && (
        <div className="pg-foot">
          <div className="panel">
            <div className="step-head">
              <h4>Selected commit</h4>
              <span className="grow" />
              <button type="button" className="icon-btn" onClick={() => step(-1)} aria-label="Previous commit of this context">◀</button>
              <button type="button" className="icon-btn" onClick={() => step(1)} aria-label="Next commit of this context">▶</button>
            </div>
            {sel ? (
              <div className="commit">
                <p className="mono">{text.get(sel[2])}</p>
                <dl>
                  <dt>tick</dt><dd>{sel[0]} · {fmtTime(sel[0])}</dd>
                  <dt>context</dt><dd>{sel[1]} (slot {sel[0] % 4})</dd>
                  <dt>pc</dt><dd>{sel[2]}{focusLine ? ` · line ${focusLine}` : ""}</dd>
                  <dt>outcome</dt><dd>{sel[3] === "C" ? "completed" : sel[3] === "W" ? "waited; tried again next slot" : "faulted"}</dd>
                  {sel[3] === "C" && sel[4] && typeof sel[4][0] === "number" && (() => {
                    const ev = sel[4] as Ev;
                    return (<>
                      <dt>event</dt><dd>{EVENT_KIND[ev[0]]} at the clock edge ending tick {ev[1]}, lead {ev[7]} ticks ({fmtTime(ev[7])}){ev[0] === 2 ? `, window ${ev[6]} ticks` : ""}{ev[0] === 3 ? `, pin ${ev[8]}` : ""}</dd>
                      {ev[0] !== 3 && <><dt>pins</dt><dd>mask {hex(ev[2], 2)} · value {hex(ev[0] === 2 ? ev[5] : ev[3], 2)}{ev[0] === 1 ? " · oe " + hex(ev[4], 2) : ""}</dd></>}
                    </>);
                  })()}
                </dl>
                {regs ? (
                  <div className="regs mono">
                    {["r0", "r1", "r2", "r3", "sh", "tmr"].map((k) => (
                      <span key={k}><b>{k}</b>{hex(regs.post[k])}</span>
                    ))}
                    <span><b>lc</b>{regs.post.lc}</span>
                    <span><b>Z C</b>{regs.post.z} {regs.post.c}</span>
                  </div>
                ) : live && ready ? <p className="dim small">Reading registers…</p> : (
                  <button type="button" className="icon-btn" onClick={() => doRun(true)} disabled={busy}>
                    {busy ? "starting Python…" : "show registers (runs it live)"}
                  </button>
                )}
              </div>
            ) : <p className="dim small">Click a commit in a lane, or an edge on a pin, to see which instruction
              queued it and when. Put the cursor on a line of the source to mark every commit of that line. With the waveform focused,
              the arrow keys step through one context's commits.</p>}
          </div>
          <div className="panel listing-box">
            <h4>Program memory <span className="dim">· {run.words} words</span></h4>
            <div className="listing mono">
              {run.listing.map(([pc, word, dis, where]) => (
                <button type="button" key={pc} className={(sel && sel[2] === pc ? "on " : "") + (hitPcs.has(pc) ? "hit" : "")}
                  onClick={() => where && where[0] === run.name && setCursorLine(where[1])}>
                  <span className="dim">{String(pc).padStart(3, " ")}</span>
                  <span className="hexw">{word}</span>
                  <span>{dis}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="panel">
            <h4>Execution digest</h4>
            <p className="digest mono">{run.digest}</p>
            <p className="dim small">Every commit, register write and pin change of the run folded into one
              32-bit word. After a run on silicon, one SPI read compares the chip with this prediction.</p>
          </div>
        </div>
      )}
      </SmoothHeight>
    </div>
  );
}
