// A 40 × 24 µm window of the routed chip, every layer extruded at its true height.
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { css, DPR, useJson, webgl } from "../lib/util";

type Poly = { o: [number, number][]; h: [number, number][][] };
type Slice = { size_um: [number, number]; layers: { key: string; z: number; t: number; polys: Poly[] }[];
  cells: { cell: string; box: [number, number, number, number] }[] };

const CELLS: [string, string][] = [["dfrbp", "D flip-flop with reset"], ["dfrbpq", "D flip-flop with reset"], ["dlhq", "latch"],
  ["dllr", "latch"], ["sdfbbp", "scan flip-flop"], ["nand", "NAND gate"], ["nor", "NOR gate"], ["xnor", "XNOR gate"],
  ["xor", "XOR gate"], ["and", "AND gate"], ["or", "OR gate"], ["inv", "inverter"], ["buf", "buffer"],
  ["dlygate", "hold delay cell"], ["mux", "multiplexer"], ["a21oi", "AND-OR-invert gate"], ["a221oi", "AND-OR-invert gate"],
  ["a22oi", "AND-OR-invert gate"], ["o21ai", "OR-AND-invert gate"], ["tie", "tie cell"], ["antenna", "antenna diode"],
  ["ebuf", "tristate buffer"], ["fill", "filler"], ["decap", "decoupling capacitor"]];
const cellDesc = (n: string) => {
  const base = n.replace(/^sg13cmos5l_/, "").replace(/_\d+$/, "");
  const hit = CELLS.filter(([k]) => base.startsWith(k)).sort((a, b) => b[0].length - a[0].length)[0];
  return (hit ? hit[1] : "standard cell") + " (" + n.replace(/^sg13cmos5l_/, "") + ")";
};
const GROUP: Record<string, string> = { activ: "Transistors", poly: "Transistors", cont: "Contacts and vias",
  via1: "Contacts and vias", via2: "Contacts and vias", via3: "Contacts and vias", topvia1: "Contacts and vias",
  metal1: "Metal1", metal2: "Metal2", metal3: "Metal3", metal4: "Metal4", topmetal1: "TopMetal1" };

export default function Slice3D() {
  const [S] = useJson<Slice>("data/slice.json");
  const stage = useRef<HTMLDivElement>(null);
  const [tag, setTag] = useState<string | null>(null);
  const [groups, setGroups] = useState<{ name: string; color: string; on: boolean }[]>([]);
  const toggle = useRef<(name: string) => void>(() => {});
  const ok = webgl();

  useEffect(() => {
    if (!S || !ok || !stage.current) return;
    const el = stage.current;
    const [SW, SH] = S.size_um, cx = SW / 2, cy = SH / 2;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(DPR());
    el.prepend(renderer.domElement);
    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xdfe6ff, 0x1a1f2c, 2.6));
    const sun = new THREE.DirectionalLight(0xffffff, 2.6); sun.position.set(-20, -30, 40); scene.add(sun);
    const cam = new THREE.PerspectiveCamera(35, 1.6, 0.1, 500);
    cam.up.set(0, 0, 1); cam.position.set(-4, -38, 26);
    const ctl = new OrbitControls(cam, renderer.domElement);
    ctl.enableDamping = true; ctl.target.set(0, 0, 1.5); ctl.minDistance = 8; ctl.maxDistance = 120;
    const COL: Record<string, string> = { activ: css("--activ"), poly: css("--poly"), metal1: css("--m1"), metal2: css("--m2"),
      metal3: css("--m3"), metal4: css("--m4"), topmetal1: css("--tm1") };
    const gmap: Record<string, { meshes: THREE.Mesh[]; color: string }> = {};
    const sub = new THREE.Mesh(new THREE.BoxGeometry(SW, SH, 0.6), new THREE.MeshStandardMaterial({ color: 0x3a3f4f, roughness: 0.9 }));
    sub.position.set(0, 0, -0.3); scene.add(sub);
    S.layers.forEach((L) => {
      const geos: THREE.BufferGeometry[] = [];
      L.polys.forEach((P) => {
        const sh = new THREE.Shape(P.o.map(([x, y]) => new THREE.Vector2(x - cx, y - cy)));
        P.h.forEach((hole) => sh.holes.push(new THREE.Path(hole.map(([x, y]) => new THREE.Vector2(x - cx, y - cy)))));
        const g = new THREE.ExtrudeGeometry(sh, { depth: L.t, bevelEnabled: false, curveSegments: 1 });
        g.translate(0, 0, L.z);
        geos.push(g);
      });
      if (!geos.length) return;
      const merged = mergeGeometries(geos);
      geos.forEach((g) => g.dispose());
      const col = COL[L.key] || "#b9bccb";
      const metal = L.key.startsWith("metal") || L.key.startsWith("top");
      const mesh = new THREE.Mesh(merged, new THREE.MeshStandardMaterial({ color: new THREE.Color(col),
        roughness: metal ? 0.35 : 0.6, metalness: metal ? 0.55 : 0.1 }));
      scene.add(mesh);
      const gname = GROUP[L.key] || L.key;
      (gmap[gname] = gmap[gname] || { meshes: [], color: L.key.includes("via") || L.key === "cont" ? "#b9bccb" : col }).meshes.push(mesh);
    });
    setGroups(Object.entries(gmap).map(([name, g]) => ({ name, color: g.color, on: true })));
    toggle.current = (name) => {
      const g = gmap[name];
      const v = !g.meshes[0].visible;
      g.meshes.forEach((m) => (m.visible = v));
      setGroups((gs) => gs.map((x) => (x.name === name ? { ...x, on: v } : x)));
      need();
    };
    const outline = new THREE.LineBasicMaterial({ color: 0x6b7590, transparent: true, opacity: 0.6 });
    const hot = new THREE.LineBasicMaterial({ color: 0xf0c66e });
    const rects = S.cells.map((cl) => {
      const [x0, y0, x1, y1] = cl.box.map((v, i) => v - (i % 2 ? cy : cx));
      const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(x0, y0, 0.02), new THREE.Vector3(x1, y0, 0.02),
        new THREE.Vector3(x1, y1, 0.02), new THREE.Vector3(x0, y1, 0.02), new THREE.Vector3(x0, y0, 0.02)]);
      const ln = new THREE.Line(g, outline); scene.add(ln);
      return { cl, ln, x0, y0, x1, y1 };
    });
    const ray = new THREE.Raycaster(), plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0), hit = new THREE.Vector3();
    let cur: (typeof rects)[0] | null = null;
    const show = (q: typeof cur) => {
      if (q === cur) return;
      if (cur) cur.ln.material = outline;
      cur = q;
      if (cur) cur.ln.material = hot;
      setTag(cur ? cellDesc(cur.cl.cell) + " · " + (cur.x1 - cur.x0).toFixed(2) + " µm wide" : null);
      need();
    };
    const leave = () => show(null);
    const move = (e: PointerEvent) => {
      const r = renderer.domElement.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), cam);
      let found: typeof cur = null;
      if (ray.ray.intersectPlane(plane, hit)) found = rects.find((q) => hit.x >= q.x0 && hit.x <= q.x1 && hit.y >= q.y0 && hit.y <= q.y1) || null;
      show(found);
    };
    renderer.domElement.addEventListener("pointerleave", leave);
    renderer.domElement.addEventListener("pointermove", move);
    let on = false, frames = 0, looping = false;
    const need = () => { frames = 60; if (on) loop(); };
    function loop() {
      if (looping) return;
      looping = true;
      const step = () => {
        const moving = ctl.update();
        renderer.render(scene, cam);
        if (on && (moving || frames-- > 0)) requestAnimationFrame(step); else looping = false;
      };
      requestAnimationFrame(step);
    }
    const size = () => {
      const w = el.clientWidth, h = el.clientHeight;
      renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); need();
    };
    ctl.addEventListener("change", need);
    const io = new IntersectionObserver((es) => es.forEach((e) => { on = e.isIntersecting; if (on) need(); }));
    io.observe(el);
    const ro = new ResizeObserver(size);
    ro.observe(el);
    size();
    return () => {
      io.disconnect(); ro.disconnect(); ctl.dispose(); renderer.dispose(); renderer.domElement.remove();
    };
  }, [S, ok]);

  return (
    <figure>
      <div className="stage" ref={stage}>
        {ok ? <span className="hint">drag to turn · scroll to zoom · hover a cell</span>
          : <span className="hint">The 3D view needs WebGL</span>}
        <span className="cell-tag" aria-live="polite" hidden={!tag}>{tag}</span>
        <div className="stage-ui">
          <div className="legend" aria-label="Layers">
            {groups.map((g) => (
              <button key={g.name} type="button" aria-pressed={g.on} onClick={() => toggle.current(g.name)}>
                <i style={{ background: g.color }} />{g.name}
              </button>
            ))}
          </div>
        </div>
      </div>
      <figcaption>A 40 × 24 µm window of the routed chip, every layer from the transistors up, at true relative
        heights.</figcaption>
    </figure>
  );
}
