/// <reference lib="webworker" />
// Runs the real toolchain (sw/escasm, esciss, escert, escmodels, escgen, esclearn) in
// Pyodide, off the main thread. The runtime and the bundle are served from the site itself.

type Req = { id: number; fn: string; args: unknown[] };

let py: any = null;
let booting: Promise<void> | null = null;

let root = "";

async function boot(): Promise<void> {
  post({ status: "Loading Python…" });
  const mod = await import(/* @vite-ignore */ root + "pyodide/pyodide.mjs");
  py = await mod.loadPyodide({ indexURL: root + "pyodide/", stdLibURL: root + "pyodide/python_stdlib.zip" });
  post({ status: "Loading the toolchain…" });
  const files: Record<string, string> = await (await fetch(root + "py/bundle.json")).json();
  for (const [path, text] of Object.entries(files)) {
    const full = "/esc/" + path;
    py.FS.mkdirTree(full.slice(0, full.lastIndexOf("/")));
    py.FS.writeFile(full, text);
  }
  py.runPython(`
import sys
sys.path[:0] = ["/esc/shim", "/esc/sw", "/esc/site/py"]
import escplay, learnplay, lateplay
escplay.set_firmware_dir("/esc/firmware")
learnplay.set_data_dir("/esc/sw/tests/data/sigrok")
`);
  post({ status: "ready" });
}

function post(msg: object) {
  (self as unknown as Worker).postMessage(msg);
}

self.onmessage = async (e: MessageEvent) => {
  const msg = e.data as Req & { base?: string };
  if (msg.base) root = msg.base;
  if (msg.fn === "init") {
    booting = booting || boot();
    try {
      await booting;
      post({ id: msg.id, ok: true });
    } catch (err) {
      booting = null;
      post({ id: msg.id, ok: false, error: String(err) });
    }
    return;
  }
  try {
    await (booting || (booting = boot()));
    const [mod, name] = msg.fn.includes(".") ? msg.fn.split(".") : ["escplay", msg.fn];
    const fn = py.pyimport(mod)[name];
    const t0 = performance.now();
    const out = fn(...msg.args);
    post({ id: msg.id, ok: true, value: out, ms: performance.now() - t0 });
  } catch (err) {
    post({ id: msg.id, ok: false, error: String(err) });
  }
};
