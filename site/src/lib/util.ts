import { useEffect, useRef, useState, useSyncExternalStore } from "react";

export const css = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
export const DPR = () => Math.min(window.devicePixelRatio || 1, 2);
export const asset = (p: string) => import.meta.env.BASE_URL + p;

export const TICK_NS = 40;
export const CTX_VARS = ["--c0", "--c1", "--c2", "--c3"];

/** Size a canvas to its CSS box (or a given height) at device resolution. */
export function fit(canvas: HTMLCanvasElement, h?: number) {
  const w = canvas.clientWidth || canvas.parentElement!.clientWidth;
  const d = DPR();
  if (h) canvas.style.height = h + "px";
  const H = h || canvas.clientHeight;
  canvas.width = Math.round(w * d);
  canvas.height = Math.round(H * d);
  const g = canvas.getContext("2d")!;
  g.setTransform(d, 0, 0, d, 0, 0);
  return { g, w, h: H };
}

const cache = new Map<string, Promise<any>>();
export function json<T = any>(path: string): Promise<T> {
  let p = cache.get(path);
  if (!p) {
    p = fetch(asset(path)).then((r) => {
      if (!r.ok) throw new Error(path + ": " + r.status);
      return r.json();
    });
    cache.set(path, p);
  }
  return p;
}

export function useJson<T = any>(path: string | null): [T | null, string | null] {
  const [v, setV] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let live = true;
    json<T>(path).then((d) => live && setV(d), (e) => live && setErr(String(e)));
    return () => {
      live = false;
    };
  }, [path]);
  return [v, err];
}

/** True once the element has come near the viewport (and while it stays there, if `track`). */
export function useVisible<T extends Element>(track = false, margin = "200px") {
  const ref = useRef<T>(null);
  const [vis, setVis] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!("IntersectionObserver" in window)) {
      setVis(true);
      return;
    }
    const io = new IntersectionObserver(
      (es) =>
        es.forEach((e) => {
          if (e.isIntersecting) setVis(true);
          else if (track) setVis(false);
        }),
      { rootMargin: margin },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [track, margin]);
  return [ref, vis] as const;
}

// ---- theme: the viewer's choice wins; "system" sets nothing
const themeSubs = new Set<() => void>();
let themeTick = 0;
export function storedTheme(): string | null {
  try {
    return localStorage.getItem("esc-theme");
  } catch {
    return null;
  }
}
export function isDark(): boolean {
  const t = document.documentElement.getAttribute("data-theme");
  return t ? t === "dark" : !matchMedia("(prefers-color-scheme: light)").matches;
}
export function setTheme(t: "light" | "dark") {
  document.documentElement.setAttribute("data-theme", t);
  try {
    localStorage.setItem("esc-theme", t);
  } catch {
    /* private window */
  }
  themeTick++;
  requestAnimationFrame(() => themeSubs.forEach((f) => f()));
}
if (typeof window !== "undefined") {
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    themeTick++;
    themeSubs.forEach((f) => f());
  });
}
/** Re-renders (returns a new number) whenever the colour theme changes. */
export function useThemeTick(): number {
  return useSyncExternalStore(
    (f) => {
      themeSubs.add(f);
      return () => themeSubs.delete(f);
    },
    () => themeTick,
  );
}

export function useResize(cb: () => void, deps: unknown[] = []) {
  useEffect(() => {
    let raf = 0;
    const f = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(cb);
    };
    addEventListener("resize", f);
    return () => {
      removeEventListener("resize", f);
      cancelAnimationFrame(raf);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

/** A figure that zooms on the wheel must not catch the page as it scrolls past: a plain
 *  wheel over it scrolls the page, and Ctrl (⌘ on a Mac) plus the wheel, or a trackpad
 *  pinch, which browsers send as a Ctrl wheel, zooms. Listens in the capture phase on the
 *  figure's box, so it stops the event before the canvas's own handler sees it. Returns
 *  true for a moment after a blocked wheel, to show a hint. */
export function useWheelGuard(ref: React.RefObject<HTMLElement>, deps: unknown[] = []) {
  const [hint, setHint] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let t = 0;
    const f = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) return;
      e.stopPropagation();
      setHint(true);
      clearTimeout(t);
      t = window.setTimeout(() => setHint(false), 1400);
    };
    el.addEventListener("wheel", f, { capture: true });
    return () => { el.removeEventListener("wheel", f, { capture: true }); clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return hint;
}
export const ZOOM_KEY = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl";

export function niceStep(span: number, n = 8) {
  const raw = span / n,
    p = Math.pow(10, Math.floor(Math.log10(raw)));
  return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw)!;
}

export function fmtTime(ticks: number): string {
  const ns = ticks * TICK_NS;
  if (ns < 1000) return ns.toFixed(0) + " ns";
  if (ns < 1e6) return (ns / 1000).toFixed(ns < 1e4 ? 2 : 1) + " µs";
  return (ns / 1e6).toFixed(2) + " ms";
}

export function webgl(): boolean {
  try {
    const cv = document.createElement("canvas");
    return !!(cv.getContext("webgl2") || cv.getContext("webgl"));
  } catch {
    return false;
  }
}

export const hex = (v: number, n = 8) => (v >>> 0).toString(16).toUpperCase().padStart(n, "0");

/** Scroll to a section, and keep aiming at it for a moment while sections above it load
 *  and change height (a plain smooth scroll would stop short or overshoot). */
export function scrollToSection(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  try { history.replaceState(null, "", "#" + id); } catch { /* sandboxed */ }
  el.scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "start" });
  let last = document.documentElement.scrollHeight;
  const ro = new ResizeObserver(() => {
    const h = document.documentElement.scrollHeight;
    if (h !== last) { last = h; el.scrollIntoView({ behavior: "auto", block: "start" }); }
  });
  ro.observe(document.body);
  setTimeout(() => ro.disconnect(), 2500);
}
