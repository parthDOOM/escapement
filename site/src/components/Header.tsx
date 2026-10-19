import { useEffect, useState } from "react";
import { isDark, setTheme, useThemeTick } from "../lib/util";

export const SECTIONS = [
  ["time", "Promise"],
  ["inside", "Inside"],
  ["play", "Playground"],
  ["isa", "Instructions"],
  ["proof", "Proof"],
  ["silicon", "Silicon"],
  ["use", "Use it"],
] as const;

export default function Header() {
  useThemeTick();
  const [cur, setCur] = useState("");
  useEffect(() => {
    if (!("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver(
      (es) => es.forEach((e) => e.isIntersecting && setCur(e.target.id)),
      { rootMargin: "-45% 0px -50% 0px" },
    );
    SECTIONS.forEach(([id]) => {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    });
    return () => io.disconnect();
  }, []);
  const dark = isDark();
  return (
    <header className="bar">
      <div className="wrap">
        <a className="mark" href="#top">
          Escapement
        </a>
        <nav aria-label="Sections">
          {SECTIONS.map(([id, label]) => (
            <a key={id} href={"#" + id} aria-current={cur === id ? "true" : undefined}>
              {label}
            </a>
          ))}
        </nav>
        <a className="icon-btn gh" href="https://github.com/parthDOOM/escapement" aria-label="Source on GitHub">
          GitHub
        </a>
        <button className="icon-btn" type="button" onClick={() => setTheme(dark ? "light" : "dark")}
          aria-label="Switch colour theme">
          {dark ? "light" : "dark"}
        </button>
      </div>
    </header>
  );
}
