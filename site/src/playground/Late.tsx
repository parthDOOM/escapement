// Make it late: a dare. Any program, assembled, certified and run on the simulator, then
// audited: the pins against the events the program queued, on every tick. The tricks'
// results load from JSON; editing one starts the real tools in the Python worker.
import { useEffect, useMemo, useState } from "react";
import { fmtTime, json, useJson } from "../lib/util";
import { loadPython, py, usePyStatus } from "../lib/py";
import Editor from "./Editor";
import WaveView, { View } from "./WaveView";
import { FAULTS, autoView } from "./Playground";
import type { Run, RunError } from "./types";

type Attempt = { id: string; title: string; blurb: string; expect: string };
type Audit = { ticks: number; edges: number; late: number; withdrawn: number; queued: number;
  mismatches: { t: number; pins: number }[]; unaudited_pins: number; min_lead: number | null };
type LateRun = Run & { audit: Audit };

const NONE: number[] = [];

export default function Late() {
  const [attempts] = useJson<Attempt[]>("data/late/index.json");
  const [aid, setAid] = useState("wait");
  const a = attempts?.find((x) => x.id === aid);
  const [stock, setStock] = useState<Record<string, LateRun>>({});
  const [sources, setSources] = useState<Record<string, string>>({});
  const [run, setRun] = useState<LateRun | null>(null);
  const [err, setErr] = useState<RunError | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View>([0, 1]);
  const [selected, setSelected] = useState<number | null>(null);
  const [tally, setTally] = useState({ runs: 0, edges: 0, late: 0 });
  const status = usePyStatus();
  const ready = status === "ready";

  useEffect(() => {
    if (stock[aid]) return;
    json<LateRun>(`data/late/${aid}.json`).then((r) => setStock((s) => ({ ...s, [aid]: r })));
  }, [aid, stock]);
  useEffect(() => {
    const r = stock[aid];
    if (!r) return;
    setRun(r); setErr(null); setSelected(null); setView(autoView(r));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aid, stock[aid]]);

  const source = sources[aid] ?? stock[aid]?.source ?? "";
  const go = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await loadPython();
      const r = await py.late(source);
      if (r.ok) {
        setRun(r); setErr(null); setSelected(null); setView(autoView(r));
        setTally((t) => ({ runs: t.runs + 1, edges: t.edges + r.audit.edges, late: t.late + r.audit.late }));
      } else setErr(r);
    } catch (e) {
      setErr({ ok: false, error: String(e), line: null });
    } finally {
      setBusy(false);
    }
  };

  const text = useMemo(() => {
    const m = new Map<number, string>();
    run?.listing.forEach(([pc, , dis]) => m.set(pc, dis));
    return m;
  }, [run]);
  const au = run?.audit;
  const f = run?.fault;
  const key = f ? f.cause + (f.subcode ? " " + f.subcode : "") : "";
  const late = !!au && (au.late > 0 || au.mismatches.length > 0);
  const proved = run?.certs.length && run.certs.every((c) => c.status === "CHECKED");

  return (
    <div className="learn late">
      <div className="tabs" role="tablist" aria-label="Attempt">
        {(attempts || []).map((x) => (
          <button key={x.id} type="button" role="tab" className="tab" aria-selected={x.id === aid}
            onClick={() => setAid(x.id)}>{x.title}</button>
        ))}
      </div>

      <div className="learn-grid">
        <div className="panel pg-editor">
          <div className="pg-bar">
            <span className="mono pg-file">{a?.title}</span>
            <span className="grow" />
            {sources[aid] != null && sources[aid] !== stock[aid]?.source && (
              <button type="button" className="icon-btn" onClick={() => {
                setSources((s) => { const n = { ...s }; delete n[aid]; return n; });
                const r = stock[aid];
                if (r) { setRun(r); setErr(null); setView(autoView(r)); }
              }}>reset</button>
            )}
            <button type="button" className="run-btn" onClick={go} disabled={busy}>
              {busy ? (ready ? "running…" : "loading…") : "Run"}
              <kbd>⌘↵</kbd>
            </button>
          </div>
          {stock[aid] ? <Editor value={source}
            onChange={(v) => setSources((s) => ({ ...s, [aid]: v }))}
            onRun={go}
            onCursorLine={() => {}}
            onFocus={() => { if (status === "idle") loadPython().catch(() => {}); }}
            errorLine={err?.line ?? null}
            focusLine={null}
            hitLines={NONE} /> : <div className="editor loading">loading…</div>}
          <div className="pg-status mono" aria-live="polite">
            {err ? <span className="bad">{err.error}</span>
              : busy ? <span>{ready ? "Assembling, certifying, simulating, auditing…" : status}</span>
              : <span className="dim">{a?.blurb} Edit it and press Run.</span>}
          </div>
        </div>

        <div className="panel learn-side">
          {run && au && (
            <>
              <div className="learn-head">
                <h4>Verdict</h4>
                <span className={"pill " + (late ? "bad" : f ? "warn" : "ok")}>
                  {late ? "LATE" : f ? "refused" : "on time"}</span>
              </div>
              {late ? (
                <div className="fault">
                  <b>An edge came late</b>
                  <span>tick {au.mismatches[0]?.t}</span>
                  <p>The pins differ from what the queued events asked for. This should be impossible: please open an
                    issue with this program.</p>
                </div>
              ) : f ? (
                <div className="fault">
                  <b>Context {f.ctx} refused: {key}</b>
                  <span>at tick {f.tick}, pc {f.pc}{f.line && f.line[0] === run.name ? `, line ${f.line[1]}` : ""}</span>
                  <p>{FAULTS[key] || ""} Nothing was driven late: the chip refused the edge when it was queued,
                    released the context's pins, and the other contexts ran on.</p>
                </div>
              ) : (
                <p>Every edge on its tick.{proved ? " escert proved it before the run, for every input." : ""}</p>
              )}
              <ul className="found mono">
                <li>{au.edges.toLocaleString()} edge{au.edges === 1 ? "" : "s"} came due; the pins matched the queued events
                  on all {au.ticks.toLocaleString()} ticks ({fmtTime(au.ticks)})</li>
                {au.edges > 0 && <li>closest call: queued {au.min_lead} tick{au.min_lead === 1 ? "" : "s"} before its deadline</li>}
                {au.withdrawn > 0 && <li>{au.withdrawn} queued edge{au.withdrawn === 1 ? "" : "s"} withdrawn by the fault, never driven</li>}
                {au.queued > 0 && <li>{au.queued} still queued when the run ended</li>}
                {run.certs.map((c) => (
                  <li key={c.ctx}>context {c.ctx}: {c.status === "CHECKED"
                    ? `proved never refused, lead ≥ ${c.min_lead ?? "–"}`
                    : c.status === "POSSIBLE" || c.status === "REFUTED" || c.status === "NOT_PROVEN"
                      ? `escert warned before the run: may fault ${c.findings?.[0]?.[1] ?? ""}` : c.status.toLowerCase()}</li>
                ))}
              </ul>
              {tally.runs > 0 && (
                <p className="dim small">Your runs on this page: {tally.runs}, {tally.edges.toLocaleString()} edges audited,{" "}
                  {tally.late} late.</p>
              )}
            </>
          )}
          {!run && <div className="loading" style={{ minHeight: 200 }}>loading…</div>}
        </div>
      </div>

      {run && (
        <div className="panel pg-wave">
          <WaveView run={run} view={view} setView={setView} selected={selected} onSelect={setSelected}
            hitPcs={new Set<number>()} text={text} onStep={() => {}} />
        </div>
      )}
    </div>
  );
}
