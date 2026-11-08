import { useEffect, useState } from "react";
import { isDark, scrollToSection, setTheme, useThemeTick } from "../lib/util";

export const SECTIONS = [
  ["time", "Promise"],
  ["inside", "Inside"],
  ["play", "Playground"],
  ["isa", "Instructions"],
  ["proof", "Proof"],
  ["silicon", "Silicon"],
  ["use", "Use it"],
] as const;

type Sections = readonly (readonly [string, string])[];

/** The sticky bar. `sections` are this page's own sections (tracked while scrolling); `other`
 *  links to the other page of the site, and `home` is where the wordmark goes. */
export default function Header({ sections = SECTIONS, other = { href: "fpga/", label: "On hardware" }, home = "#top" }:
  { sections?: Sections; other?: { href: string; label: string }; home?: string } = {}) {
  useThemeTick();
  const [cur, setCur] = useState("");
  // The current section is the last one whose top has passed a line a third of the way
  // down the screen (below the sticky bar); at the very bottom it is the last section.
  useEffect(() => {
    let raf = 0;
    const update = () => {
      raf = 0;
      const line = Math.min(window.innerHeight * 0.33, 260);
      let id = "";
      for (const [sid] of sections) {
        const el = document.getElementById(sid);
        if (el && el.getBoundingClientRect().top <= line) id = sid;
      }
      const atEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      if (atEnd) id = sections[sections.length - 1][0];
      setCur(id);
    };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(update); };
    update();
    addEventListener("scroll", onScroll, { passive: true });
    addEventListener("resize", onScroll);
    // sections below load lazily and change height; re-check when they do
    const ro = new ResizeObserver(onScroll);
    ro.observe(document.body);
    return () => {
      removeEventListener("scroll", onScroll);
      removeEventListener("resize", onScroll);
      ro.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [sections]);
  // keep the current link visible in the scrolling nav on narrow screens. Only the nav
  // strip moves: scrollIntoView would also cancel a smooth scroll of the page under way.
  useEffect(() => {
    const nav = document.querySelector<HTMLElement>(".bar nav");
    const a = nav?.querySelector<HTMLElement>(`a[href="#${cur}"]`);
    if (!nav || !a) return;
    const l = a.offsetLeft - nav.offsetLeft, r = l + a.offsetWidth;
    if (l < nav.scrollLeft) nav.scrollLeft = l - 8;
    else if (r > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = r - nav.clientWidth + 8;
  }, [cur]);
  const dark = isDark();
  return (
    <header className="bar">
      <div className="wrap">
        <a className="mark" href={home}>
          Escapement
        </a>
        <nav aria-label="Sections">
          {sections.map(([id, label]) => (
            <a key={id} href={"#" + id} aria-current={cur === id ? "true" : undefined}
              onClick={(e) => { e.preventDefault(); scrollToSection(id); }}>
              {label}
            </a>
          ))}
          <a className="other-page" href={other.href}>{other.label}</a>
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
