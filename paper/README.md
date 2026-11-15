# The paper

`escapement.tex` is the project's paper: IEEE conference format, two columns, US letter.
Its text follows [the write-up](../docs/00-WRITEUP.md) and [the history](../docs/07-STORY.md).

```sh
python paper/figures.py <final GDS>   # fig/die.jpg, fig/die_zoom.jpg, fig/blocks.pdf
cd paper && latexmk -pdf escapement.tex
```

The block diagram, the admission timeline and the barrel schedule are drawn in TikZ in the
source. The die figures come from the final GDS through KLayout, with the colours of
`site/tools/die_assets.py`. The block map comes from `site/public/data/floorplan.json`. The
photographs are `docs/img/bench-setup.jpg`, a crop of `docs/img/bench-oled.jpg`
(`fig/oled-crop.jpg`) and `docs/img/vga-pong.png`.

Numbers that come from the final CI runs are written `\NUM{key}`, which prints
`{{NUM:key}}` in red. The keys are the ones the docs use:

- `formal_writeup`
- `formal_status`
- `cosim_writeup`
- `cert_writeup`
- `mutation_writeup`
- `test_status`

Before a release, `grep -n '\\NUM{' paper/escapement.tex` must print only the macro's
definition.
