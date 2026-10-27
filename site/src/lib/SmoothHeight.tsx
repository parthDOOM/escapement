import { useEffect, useRef } from "react";
import { reduced } from "./util";

/** A box whose height follows its content with a short transition instead of a jump, so
 *  that switching what a figure shows slides the page below it rather than snapping it.
 *  The first measurement, and every one with reduced motion, applies at once. */
export default function SmoothHeight({ children, className = "", innerClassName = "" }:
  { children: React.ReactNode; className?: string; innerClassName?: string }) {
  const outer = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const o = outer.current!, i = inner.current!;
    let first = true;
    const ro = new ResizeObserver(() => {
      const h = i.offsetHeight + "px";
      if (first || reduced()) {
        o.style.transition = "none";
        o.style.height = h;
        void o.offsetHeight;               // apply before the transition comes back
        o.style.transition = "";
        first = false;
      } else o.style.height = h;
    });
    ro.observe(i);
    return () => ro.disconnect();
  }, []);
  return (
    <div ref={outer} className={("smooth-h " + className).trim()}>
      <div ref={inner} className={innerClassName || undefined}>{children}</div>
    </div>
  );
}
