// What escplay.run() returns (site/py/escplay.py), and the scenario table.

export type Ev = [kind: number, deadline: number, mask: number, value: number, oe: number,
  level: number, window: number, lead: number, pin: number];
/** [tick, ctx, pc, outcome, event | [cause, subcode]] */
export type Commit = [number, number, number, "C" | "W" | "F", (Ev | [string, string])?];

export type Signal = { name: string; pin: string; w: [number, number][] };
export type Cert = { ctx: number; status: string; events?: number; min_lead: number | null;
  findings?: [number, string, string][]; note?: string };
export type CtxStat = { ctx: number; state: string; pc: number; min_lead: number; max_occ: number;
  wait_full: number; C: number; W: number; F: number; bank: string };
export type Fault = { ctx: number; pc: number; tick: number; cause: string; subcode: string;
  line: [string, number] | null };

export type Run = {
  ok: true;
  scenario: string;
  name: string;
  result: string;
  span: number;
  signals: Signal[];
  commits: Commit[];
  certs: Cert[];
  fault: Fault | null;
  contexts: CtxStat[];
  digest: string;
  words: number;
  listing: [number, string, string, [string, number] | null][];
  errors: string[];
  leds?: number[];
  source?: string;
};
export type RunError = { ok: false; error: string; line: number | null };

export type Param = { key: string; label: string; type: "text" | "hex" | "int" | "select";
  default: string; options?: string[] };
export type Scenario = { id: string; title: string; firmware: string | null; blurb: string;
  params: Param[]; signals: { name: string; pin: number; bank: string }[] };

export const EVENT_KIND = ["", "DRIVE", "EXPECT", "SAMPLE"]; // isa.yaml event_kinds
