// The page's handle on the Python worker. Nothing loads until something asks for it.
import { useSyncExternalStore } from "react";

let worker: Worker | null = null;
let seq = 0;
const pending = new Map<number, { res: (v: any) => void; rej: (e: Error) => void }>();
let status = "idle";
const subs = new Set<() => void>();

function setStatus(s: string) {
  status = s;
  subs.forEach((f) => f());
}

function start(): Worker {
  if (worker) return worker;
  worker = new Worker(new URL("../worker/py.worker.ts", import.meta.url), { type: "module" });
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.status) setStatus(m.status);
    if (m.id == null) return;
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    if (m.ok) p.res(m.value);
    else p.rej(new Error(m.error));
  };
  worker.onerror = (e) => setStatus("error: " + (e.message || "the worker failed"));
  return worker;
}

function call<T = string>(fn: string, ...args: unknown[]): Promise<T> {
  const w = start();
  const id = ++seq;
  return new Promise<T>((res, rej) => {
    pending.set(id, { res, rej });
    w.postMessage({ id, fn, args, base: new URL(import.meta.env.BASE_URL, location.href).href });
  });
}

export function loadPython(): Promise<void> {
  if (status === "ready") return Promise.resolve();
  if (status === "idle") setStatus("Loading Python…");
  return call<void>("init").catch((e) => {
    setStatus("error: " + e.message);
    throw e;
  });
}

export const py = {
  run: (source: string, scenario: string, params: Record<string, string>) =>
    call<string>("run", source, scenario, JSON.stringify(params)).then((s) => JSON.parse(s)),
  regsAt: (i: number) => call<string>("regs_at", i).then((s) => JSON.parse(s)),
  encode: (line: string) => call<string>("encode", line).then((s) => JSON.parse(s)),
};

export function usePyStatus(): string {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => status,
  );
}
