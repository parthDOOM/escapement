// The die in 3D: each layer's mask from the final GDS, drawn at its height in the process
// (stretched about 40 times), plus the block map as a layer of its own.
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { asset, DPR, reduced, useJson, useWheelGuard, webgl, ZOOM_KEY } from "../lib/util";
import { floorplan, paint } from "../lib/floorplan";

type Layer = { key: string; label: string; color: string; z: number; t: number };
export type DieInfo = { die_um: [number, number]; tile: number; levels: { z: number; w: number; h: number; nx: number; ny: number }[];
  macros: { name: string; box: [number, number, number, number] }[]; layers: Layer[] };

export default function Die3D() {
  const [info, err] = useJson<DieInfo>("die/die.json");
  const stage = useRef<HTMLDivElement>(null);
  const [explode, setExplode] = useState(60);
  const [vis, setVis] = useState<Record<string, boolean>>({ blocks: false });
  const [hint, setHint] = useState(true);
  const api = useRef<{ place: (k: number) => void; show: (key: string, v: boolean) => void } | null>(null);
  const ok = webgl();
  const wheelHint = useWheelGuard(stage);

  useEffect(() => {
    if (!info || !ok || !stage.current) return;
    const el = stage.current;
    const [W, H] = info.die_um, U = 0.01; // 1 unit = 100 µm
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(DPR());
    el.prepend(renderer.domElement);
    const scene = new THREE.Scene();
    const cam = new THREE.PerspectiveCamera(32, 1.6, 0.1, 200);
    cam.up.set(0, 0, 1);
    cam.position.set(3.2, -13.2, 5.6);
    const ctl = new OrbitControls(cam, renderer.domElement);
    renderer.domElement.style.touchAction = "pan-y"; // a vertical swipe scrolls the page
    ctl.enableDamping = true; ctl.target.set(0, 0.4, 1.3);
    ctl.minDistance = 4; ctl.maxDistance = 40; ctl.enablePan = false;
    ctl.autoRotate = !reduced(); ctl.autoRotateSpeed = 0.35;
    ctl.addEventListener("start", () => { ctl.autoRotate = false; setHint(false); });
    const base = new THREE.Mesh(new THREE.BoxGeometry(W * U, H * U, 0.12), new THREE.MeshBasicMaterial({ color: 0x141a28 }));
    base.position.z = -0.065; scene.add(base);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(base.geometry), new THREE.LineBasicMaterial({ color: 0x3a4560 }));
    edges.position.copy(base.position); scene.add(edges);
    const loader = new THREE.TextureLoader();
    const planes: { p: THREE.Mesh; z: number; i: number; key: string }[] = [];
    info.layers.forEach((L, i) => {
      const tex = loader.load(asset(`die/layer_${L.key}.png`), () => need());
      tex.anisotropy = 4;
      const m = new THREE.MeshBasicMaterial({ color: new THREE.Color(L.color), alphaMap: tex, transparent: true,
        depthWrite: false, side: THREE.DoubleSide, opacity: i < 2 ? 0.75 : 0.9 });
      const p = new THREE.Mesh(new THREE.PlaneGeometry(W * U, H * U), m);
      p.renderOrder = i + 1; scene.add(p);
      planes.push({ p, z: L.z, i: i + 1, key: L.key });
    });
    // the block map: the floor of the stack, seen through the faded layout when shown
    floorplan().then((f) => {
      const tex = new THREE.CanvasTexture(paint(f, null, 0.85));
      tex.magFilter = THREE.NearestFilter;
      tex.colorSpace = THREE.SRGBColorSpace;
      const m = new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, side: THREE.DoubleSide });
      const p = new THREE.Mesh(new THREE.PlaneGeometry(f.w * f.bin_um * U, f.h * f.bin_um * U), m);
      p.position.x = (f.w * f.bin_um - W) * U / 2;
      p.position.y = (f.h * f.bin_um - H) * U / 2;
      p.renderOrder = 0; p.visible = false; scene.add(p);
      planes.push({ p, z: 0, i: 0, key: "blocks" });          // on the floor of the stack
      place(cur);
    });
    let cur = 0.6;
    const place = (k: number) => {
      cur = k;
      planes.forEach(({ p, z, i }) => { p.position.z = z * 40 * U + k * i * 0.5 + 0.01 * i; });
      need();
    };
    let on = false, frames = 0, looping = false;
    const need = () => { frames = 90; if (on) loop(); };
    function loop() {
      if (looping) return;
      looping = true;
      const step = () => {
        ctl.update();
        renderer.render(scene, cam);
        if (on && (ctl.autoRotate || frames-- > 0)) requestAnimationFrame(step);
        else looping = false;
      };
      requestAnimationFrame(step);
    }
    const size = () => {
      const w = el.clientWidth, h = el.clientHeight;
      renderer.setSize(w, h, false);
      cam.aspect = w / h; cam.updateProjectionMatrix(); need();
    };
    ctl.addEventListener("change", need);
    const io = new IntersectionObserver((es) => es.forEach((e) => { on = e.isIntersecting; if (on) loop(); }));
    io.observe(el);
    const ro = new ResizeObserver(size);
    ro.observe(el);
    size(); place(0.6);
    api.current = {
      place,
      show: (key, v) => {
        planes.filter((q) => q.key === key).forEach((q) => (q.p.visible = v));
        if (key === "blocks") {                     // the layout recedes behind the map
          planes.filter((q) => q.key !== "blocks").forEach((q) => {
            (q.p.material as THREE.MeshBasicMaterial).opacity = v ? 0.12 : q.i < 3 ? 0.75 : 0.9;
          });
        }
        need();
      },
    };
    return () => {
      io.disconnect(); ro.disconnect(); ctl.dispose(); renderer.dispose();
      renderer.domElement.remove();
      api.current = null;
    };
  }, [info, ok]);

  const fallback = !ok || !!err;
  return (
    <figure>
      <div className="stage" ref={stage}>
        {fallback && !err && (
          <img className="fallback" src={asset("die/die_0_0_0.jpg")}
            alt="The Escapement die: two SRAM macros at opposite corners and about 28,000 standard cells between them" />
        )}
        {err && <span className="stage-msg">The die images are rendered from the GDS when the site is built.</span>}
        {!fallback && hint && <span className="hint">drag to turn · {ZOOM_KEY} + scroll to zoom</span>}
        {!fallback && <span className="wheel-hint" data-on={wheelHint || undefined}>{ZOOM_KEY} + scroll to zoom</span>}
        {!fallback && info && (
          <div className="stage-ui">
            <label>Separate layers
              <input type="range" min={0} max={100} value={explode}
                onChange={(e) => { const v = +e.target.value; setExplode(v); api.current?.place(v / 100); }} />
            </label>
            <div className="legend" aria-label="Layers">
              <button type="button" aria-pressed={!!vis.blocks} className="blocks-btn"
                onClick={() => { const v = !vis.blocks; setVis((s) => ({ ...s, blocks: v })); api.current?.show("blocks", v); }}>
                <i className="rainbow" />Logic blocks
              </button>
              {info.layers.map((L) => {
                const v = vis[L.key] !== false;
                return (
                  <button key={L.key} type="button" aria-pressed={v}
                    onClick={() => { setVis((s) => ({ ...s, [L.key]: !v })); api.current?.show(L.key, !v); }}>
                    <i style={{ background: L.color }} />{L.label}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
      <figcaption>
        The real layout, 1289 × 711 µm, built from the final GDS. Each layer is drawn at its height in the
        process, stretched about 40 times so the stack is visible. Toggle layers in the legend; Logic blocks
        shows which part of the design each region holds.
      </figcaption>
    </figure>
  );
}
