// Record it, speak it: a recording in, its protocol learned (sw/esclearn), firmware out
// that escert certifies and that sends a new message. The samples' results load from
// JSON; a dropped file or new words start the real tools in the Python worker.
import { useEffect, useMemo, useState } from "react";
import { fmtTime, json, useJson } from "../lib/util";
import { loadPython, py, usePyStatus } from "../lib/py";

type Sample = { id: string; title: string; blurb: string; pin: number; send: string };
type Pair = [number, number | null];
type Result =
  | { ok: true; summary: string[]; channels: string[]; pin: number; coding: string; frames: number;
      other: number; words: string[]; filled: boolean; carrier: boolean; table: string; asm: string;
      image_words: number; cert: { status: string; min_lead: number | null }; rec: Pair[];
      gen: Pair[] | null; decoded: string[]; sent_ok: boolean; clk_khz: number;
      check?: { ok: boolean; worst_rel: number; fails: string[] } }
  | { ok: false; error: string; channels?: string[]; pin?: number };

const MINE = "file";

function b64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

// One frame as the model sees it: marks (away from idle; with a carrier, bursts) and spaces.
function Lane({ frame, span, carrier, label }: { frame: Pair[]; span: number; carrier: boolean; label: string }) {
  const W = 1000, H = 34, hi = 6, lo = 28;
  let t = 0, d = `M0 ${lo}`;
  const marks: [number, number][] = [];
  for (const [m, s] of frame) {
    const a = (t / span) * W, b = ((t + m) / span) * W;
    d += ` L${a.toFixed(1)} ${lo} L${a.toFixed(1)} ${hi} L${b.toFixed(1)} ${hi} L${b.toFixed(1)} ${lo}`;
    marks.push([a, b]);
    t += m + (s ?? 0);
  }
  d += ` L${W} ${lo}`;
  return (
    <div className="lane">
      <span className="lane-label mono">{label}</span>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
        {marks.map(([a, b], i) => (
          <rect key={i} x={a} y={hi} width={Math.max(0.5, b - a)} height={lo - hi}
            className={carrier ? "burst carrier" : "burst"} />
        ))}
        <path d={d} className="trace" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

const total = (f: Pair[]) => f.reduce((a, [m, s]) => a + m + (s ?? 0), 0);

export default function Learn() {
  const [samples] = useJson<Sample[]>("data/learn/index.json");
  const [sid, setSid] = useState("nec");
  const sample = samples?.find((s) => s.id === sid);
  const [stock, setStock] = useState<Record<string, Result>>({});
  const [res, setRes] = useState<Result | null>(null);
  const [send, setSend] = useState<Record<string, string>>({});
  const [file, setFile] = useState<{ name: string; data: string } | null>(null);
  const [pin, setPin] = useState(0);
  const [busy, setBusy] = useState(false);
  const status = usePyStatus();
  const ready = status === "ready";

  useEffect(() => {
    if (sid === MINE || stock[sid]) return;
    json<Result>(`data/learn/${sid}.json`).then((r) => setStock((s) => ({ ...s, [sid]: r })));
  }, [sid, stock]);
  useEffect(() => {
    if (sid !== MINE) setRes(stock[sid] ?? null);
  }, [sid, stock]);

  const words = send[sid] ?? (sample?.send || "");

  const go = async (source: string, p: number, w: string, data = "") => {
    setBusy(true);
    try {
      await loadPython();
      const r: Result = await py.learn(source, p, w, data);
      setRes(r);
      if (r.pin != null) setPin(r.pin);
    } catch (e) {
      setRes({ ok: false, error: String(e) });
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (sid === MINE) { if (file) go(file.name, pin, words, file.data); }
    else go(sid, sample?.pin ?? 0, words);
  };
  const pick = async (f: File | undefined) => {
    if (!f) return;
    const data = b64(await f.arrayBuffer());
    setFile({ name: f.name, data });
    go(f.name, -1, "", data);                 // the channel with the most edges
  };

  const ok = res && res.ok ? res : null;
  const span = useMemo(() => (ok ? Math.max(total(ok.rec), ok.gen ? total(ok.gen) : 0) : 1), [ok]);
  const frames = ok ? ok.summary.filter((l) => l.startsWith("frame ")) : [];
  const found = ok ? ok.summary.filter((l) => !l.startsWith("frame ")) : [];

  return (
    <div className="learn">
      <div className="tabs" role="tablist" aria-label="Recording">
        {(samples || []).map((s) => (
          <button key={s.id} type="button" role="tab" className="tab" aria-selected={s.id === sid}
            onClick={() => setSid(s.id)}>{s.title}</button>
        ))}
        <button type="button" role="tab" className="tab" aria-selected={sid === MINE}
          onClick={() => { setSid(MINE); setRes(null); setFile(null); }}>Your recording</button>
      </div>

      <div className="learn-grid">
        <div className="panel learn-side">
          {sid === MINE ? (
            <>
              <h3>Your recording</h3>
              <p className="dim small">A sigrok session (.sr, saved from PulseView), a VCD, or a capture from the
                chip. It stays in your browser: the tools run here.</p>
              <label className="drop">
                <input type="file" accept=".sr,.vcd,.json" onChange={(e) => pick(e.target.files?.[0])} />
                <span>{file ? file.name : "Choose a file"}</span>
              </label>
              {res?.channels && res.channels.length > 1 && (
                <label className="chan small">
                  <span className="dim">channel</span>
                  <select value={pin} onChange={(e) => { const p = +e.target.value; setPin(p); if (file) go(file.name, p, "", file.data); }}>
                    {res.channels.map((c, i) => <option key={i} value={i}>{i}: {c}</option>)}
                  </select>
                </label>
              )}
            </>
          ) : (
            <>
              <h3>{sample?.title}</h3>
              <p className="dim small">{sample?.blurb}</p>
            </>
          )}

          {ok && (
            <>
              <h4>What it found</h4>
              <ul className="found mono">
                {found.map((l, i) => <li key={i}>{l}</li>)}
                {frames.slice(0, 4).map((l, i) => <li key={"f" + i}>{l}</li>)}
                {frames.length > 4 && <li className="dim">… {ok.frames} frames read{ok.other ? `, ${ok.other} of another shape` : ""}</li>}
              </ul>
            </>
          )}

          {(sid !== MINE || file) && (
            <form className="send" onSubmit={submit}>
              <label>
                <span>send (hex words, as printed)</span>
                <input value={words} spellCheck={false} placeholder="the first recorded frame"
                  onChange={(e) => { setSend((s) => ({ ...s, [sid]: e.target.value })); if (status === "idle") loadPython().catch(() => {}); }} />
              </label>
              <button type="submit" className="run-btn" disabled={busy}>
                {busy ? (ready ? "working…" : "loading…") : "Build and send"}
              </button>
            </form>
          )}

          <div className="pg-status mono" aria-live="polite">
            {res && !res.ok ? <span className="bad">{res.error}</span>
              : busy ? <span>{ready ? "Learning, building, certifying, simulating…" : status}</span>
              : ok ? <span className={ok.sent_ok ? "ok" : "bad"}>
                  Sent {ok.words.map((w) => "0x" + w).join(" ")}{ok.filled ? " (check byte filled in)" : ""}:{" "}
                  {ok.sent_ok ? "the simulator's output decodes back to it." : "the output did not decode back."}
                </span>
              : <span className="dim">Pick a recording.</span>}
          </div>
        </div>

        <div className="panel learn-table">
          <div className="learn-head">
            <h4>Timing table</h4>
            {ok && <span className={"pill " + (ok.cert.status === "CHECKED" ? "ok" : "bad")}
              title="escert's static certificate for the generated firmware, for every input">
              {ok.cert.status === "CHECKED" ? `CHECKED · lead ≥ ${ok.cert.min_lead}` : ok.cert.status}</span>}
          </div>
          {ok ? <pre className="yaml mono">{ok.table}</pre> : <div className="loading" style={{ minHeight: 240 }}>{res ? "" : "loading…"}</div>}
          {ok && (
            <details>
              <summary className="small">Generated firmware · {ok.image_words} of 256 words</summary>
              <pre className="yaml mono">{ok.asm}</pre>
            </details>
          )}
        </div>
      </div>

      {ok && (
        <div className="panel learn-wave">
          <Lane frame={ok.rec} span={span} carrier={ok.carrier} label="recorded" />
          {ok.gen && <Lane frame={ok.gen} span={span} carrier={ok.carrier} label="sent" />}
          <p className="dim small">
            One frame, {fmtTime(span)}: the first the model reads in the recording, and the frame the generated
            firmware sends on the simulator ({ok.clk_khz === 25000 ? "at 25 MHz" : `at ${ok.clk_khz / 1000} MHz, rescaled`}).
            {ok.carrier ? " Each mark is a burst of carrier pulses." : ""}
            {ok.check ? (ok.check.ok ? " Every mark and space it sends lies within the lengths the recording shows for that symbol."
              : " " + ok.check.fails.join("; ")) : ""}
          </p>
        </div>
      )}
    </div>
  );
}
