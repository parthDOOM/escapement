// Every instruction, with its encoding drawn bit by bit. Click bits to change the word and
// watch it decode; type an instruction to assemble it with the real assembler.
import { Fragment, useMemo, useState } from "react";
import { hex, useJson } from "../lib/util";
import { loadPython, py, usePyStatus } from "../lib/py";

type Op = { name: string; op: number; format: string; wait: boolean; fields: Record<string, [number, number]>;
  group: string; syntax: string; operands: string[]; effect: string; flags: string };
type IsaData = { version: string; opcodes: Op[] };

const REG = new Set(["rd", "rs", "ra", "rb"]);
const FIELD_COL: Record<string, string> = {
  rd: "--c0", rs: "--c1", ra: "--c0", rb: "--c1", imm: "--c2", code: "--c2", n: "--c2", target: "--c3",
  mask: "--c3", value: "--c1", oe: "--c2", level: "--c1", mode: "--c0", window: "--c2", pin: "--c3", dir: "--c0",
};
const GROUP_ORDER = ["Data", "Control", "Time and input", "Events", "Tape and capture", "Host mailboxes"];

function reservedMask(o: Op) {
  let used = 0;
  for (const [m, l] of Object.values(o.fields)) used |= ((2 ** (m - l + 1) - 1) * 2 ** l);
  return (0x00ffffff & ~used) >>> 0;
}
function field(w: number, [m, l]: [number, number]) {
  return Math.floor(w / 2 ** l) % 2 ** (m - l + 1);
}
function disasm(w: number, byOp: Map<number, Op>): [string, boolean] {
  const o = byOp.get(w >>> 24);
  if (!o || (w & reservedMask(o)) >>> 0) return [`.word 0x${hex(w)}  ; ILLEGAL`, false];
  const parts = o.operands.map((n) => {
    const v = field(w, o.fields[n]);
    if (REG.has(n)) return "r" + v;
    if (n === "dir") return v ? "MSB" : "LSB";
    if (n === "mode") return v ? "ARRIVE" : "HOLD";
    if (["mask", "value", "oe", "level"].includes(n)) return "0x" + v.toString(16).padStart(2, "0");
    return String(v);
  });
  return [(o.name + " " + parts.join(", ")).trim(), true];
}
const code = (s: string) =>
  s.split(/(`[^`]+`)/).map((x, i) => (x.startsWith("`") ? <code key={i}>{x.slice(1, -1)}</code> : <Fragment key={i}>{x}</Fragment>));

export default function Isa() {
  const [isa] = useJson<IsaData>("data/isa.json");
  const [name, setName] = useState("EMIT");
  const [word, setWord] = useState<number>(0x28010101);
  const [line, setLine] = useState("EMIT 0x01, 0x01, 0x01");
  const [msg, setMsg] = useState("");
  const status = usePyStatus();
  const byOp = useMemo(() => new Map((isa?.opcodes || []).map((o) => [o.op, o])), [isa]);
  const groups = useMemo(() => {
    const g = new Map<string, Op[]>();
    GROUP_ORDER.forEach((k) => g.set(k, []));
    (isa?.opcodes || []).forEach((o) => { if (!g.has(o.group)) g.set(o.group, []); g.get(o.group)!.push(o); });
    return [...g.entries()].filter(([, v]) => v.length);
  }, [isa]);
  if (!isa) return <div className="panel">Loading the instruction table…</div>;

  const cur = byOp.get(word >>> 24);
  const shown = isa.opcodes.find((o) => o.name === name) || isa.opcodes[0];
  const [dis, legal] = disasm(word, byOp);
  const res = cur ? reservedMask(cur) : 0x00ffffff;
  const owner = (b: number): [string, string] | null => {
    if (b >= 24) return ["opcode", "--brass"];
    if (!cur) return null;
    for (const [n, [m, l]] of Object.entries(cur.fields)) if (b <= m && b >= l) return [n, FIELD_COL[n] || "--m1"];
    return null;
  };
  const pick = (o: Op) => {
    setName(o.name);
    setWord((o.op * 2 ** 24) >>> 0);
    setLine(o.syntax);
    setMsg("");
  };
  const assemble = async () => {
    setMsg(status === "ready" ? "" : "Starting the assembler…");
    try {
      await loadPython();
      const r = await py.encode(line);
      if (r.ok) {
        const w = parseInt(r.word, 16) >>> 0;
        setWord(w);
        const o = byOp.get(w >>> 24);
        if (o) setName(o.name);
        setMsg("");
      } else setMsg(r.error);
    } catch (e) {
      setMsg(String(e));
    }
  };

  return (
    <div className="isa">
      <div className="isa-list panel">
        {groups.map(([g, ops]) => (
          <div key={g} className="isa-group">
            <h4>{g}</h4>
            <div className="chips">
              {ops.map((o) => (
                <button key={o.name} type="button" className={"chip" + (o.name === name ? " on" : "") + (o.wait ? " wait" : "")}
                  onClick={() => pick(o)} title={o.syntax}>
                  {o.name}
                </button>
              ))}
            </div>
          </div>
        ))}
        <p className="dim small">Underlined: wait-class. The instruction keeps its PC and is tried again in the
          context's next slot until it completes.</p>
      </div>
      <div className="isa-detail panel">
        <div className="isa-head">
          <h3 className="mono">{shown.syntax}</h3>
          <span className="pill">op 0x{shown.op.toString(16).toUpperCase().padStart(2, "0")}</span>
          <span className="pill">format {shown.format}</span>
          {shown.wait && <span className="pill warn">waits</span>}
        </div>
        <p>{code(shown.effect)}</p>
        {shown.flags && <p className="dim small">Flags: {shown.flags}</p>}

        <div className="bits" role="group" aria-label="Instruction word, bit 31 to bit 0">
          {Array.from({ length: 32 }, (_, i) => 31 - i).map((b) => {
            const v = field(word, [b, b]);
            const own = owner(b);
            const reserved = b < 24 && ((res >>> b) & 1) === 1;
            return (
              <button key={b} type="button" className={"bit" + (reserved ? " res" : "") + (b % 8 === 0 ? " byte" : "")}
                style={own ? { ["--f" as string]: `var(${own[1]})` } : undefined}
                aria-label={`bit ${b}${own ? ", " + own[0] : reserved ? ", reserved" : ""}: ${v}`}
                onClick={() => setWord((word ^ 2 ** b) >>> 0)}>
                <span className="bn">{b}</span>
                <span className="bv">{v}</span>
              </button>
            );
          })}
        </div>
        <div className="bit-key">
          <span><i style={{ background: "var(--brass)" }} />opcode [31:24]</span>
          {cur && Object.entries(cur.fields).map(([n, [m, l]]) => (
            <span key={n}><i style={{ background: `var(${FIELD_COL[n] || "--m1"})` }} />{n} [{m}{m !== l ? ":" + l : ""}] = {field(word, [m, l])}</span>
          ))}
          <span><i className="hatch" />reserved, must be 0</span>
        </div>
        <div className="decode">
          <label>
            <span>word</span>
            <input className="mono" value={hex(word)} spellCheck={false} aria-label="Instruction word in hexadecimal"
              onChange={(e) => {
                const v = parseInt(e.target.value.replace(/[^0-9a-f]/gi, "").slice(-8) || "0", 16);
                setWord(v >>> 0);
                const o = byOp.get(v >>> 24);
                if (o) setName(o.name);
              }} />
          </label>
          <p className={"mono decoded " + (legal ? "" : "bad")}>{dis}</p>
        </div>
        <form className="encode" onSubmit={(e) => { e.preventDefault(); assemble(); }}>
          <label>
            <span>assemble</span>
            <input className="mono" value={line} onChange={(e) => setLine(e.target.value)} spellCheck={false}
              aria-label="One instruction to assemble" />
          </label>
          <button className="icon-btn" type="submit">assemble</button>
          <span className={"small " + (msg ? "bad" : "dim")}>{msg || "with sw/escasm, running in your browser"}</span>
        </form>
      </div>
    </div>
  );
}
