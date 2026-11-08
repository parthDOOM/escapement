import { useEffect, useState } from "react";
import { asset, isDark, scrollToSection, setTheme, useThemeTick } from "../lib/util";

export const SECTIONS = [
  ["time", "Promise"],
  ["inside", "Inside"],
  ["play", "Playground"],
  ["proof", "Proof"],
  ["silicon", "Silicon"],
  ["use", "Use it"],
] as const;

type Sections = readonly (readonly [string, string])[];
export type Page = "chip" | "isa" | "silicon" | "fpga";

// the site's pages, relative to its root (each page's esc-root meta says where that is)
const PAGES: readonly (readonly [Page, string, string])[] = [
  ["chip", "", "The chip"],
  ["isa", "isa/", "Instructions"],
  ["silicon", "silicon/", "Layout"],
  ["fpga", "fpga/", "On hardware"],
];

/** The sticky bar. `sections` are this page's own sections (tracked while scrolling); the
 *  other pages of the site follow them, and the wordmark goes to the chip's page. */
export default function Header({ sections = SECTIONS, page = "chip" }: { sections?: Sections; page?: Page } = {}) {
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
        <a className="mark" href={page === "chip" ? "#top" : asset("")}>
          Escapement
        </a>
        <nav aria-label="Sections">
          {sections.map(([id, label]) => (
            <a key={id} href={"#" + id} aria-current={cur === id ? "true" : undefined}
              onClick={(e) => { e.preventDefault(); scrollToSection(id); }}>
              {label}
            </a>
          ))}
          {PAGES.filter(([p]) => p !== page).map(([p, href, label], i) => (
            <a key={p} className={i === 0 ? "other-page first" : "other-page"} href={asset(href) || "./"}>{label}</a>
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
