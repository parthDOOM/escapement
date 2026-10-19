# Escapement site

The explainer site: what the chip does, how it works, and its firmware running live in the
browser. React, TypeScript and three.js, built with Vite into a static folder.

```sh
cd site
npm install
npm run dev        # http://localhost:5173
npm run build      # site/dist, ready for any static host
```

Both commands first run `npm run data`, which needs Python 3 with PyYAML:

- `tools/pybundle.py` packs `sw/` (assembler, simulator, certificate checker, bus models),
  `firmware/` and the ISA table for the browser, copies the Pyodide runtime next to it, and
  builds the instruction table from `isa/isa.yaml` and `docs/02-ISA.md`.
- `tools/runs.py` runs every stock firmware once, so the page has its waveforms before
  Python has loaded.

The playground (`py/escplay.py`) runs in a web worker under Pyodide. It wires the project's
own tools together and adds nothing to them, so what the page shows is what the tests check.

## Generated from the layout

| File | Tool | Source |
| --- | --- | --- |
| `public/die/` (not committed) | `tools/die_assets.py` | final GDS, rendered by CI |
| `public/data/slice.json` | `tools/slice_model.py` | final GDS |
| `public/data/floorplan.json` | `tools/floorplan.py` | final DEF and the standard-cell LEF |

The die images need KLayout's Python package. Without them the two die views show a note
instead; everything else works. To render them locally:

```sh
pip install klayout==0.29.12 numpy pillow
python tools/die_assets.py path/to/tt_um_escapement.gds public/die
```

## Deploying

`.github/workflows/site.yaml` builds the site with the die imagery from the latest
successful `gds` run on master and publishes `site/dist` to GitHub Pages. It also deploys to
Netlify (secrets `NETLIFY_AUTH_TOKEN`, `NETLIFY_SITE_ID`) or Vercel (`VERCEL_TOKEN`,
`VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`) when those secrets are set. Cache headers for both
are in `public/_headers` and `public/vercel.json`.
