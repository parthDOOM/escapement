# `westminster` on the FPGA bench, 2026-11-28

`westminster` plays the Westminster chime and halts: 391,262,212 ticks, 15.65 s at 25 MHz
(the 14-second tune, its 0.65 s rest and the run-out after the last note). It is the one
library program `make_bundle.py` skips: its prediction keeps a digest history for the
steady-pattern search, and the ISS keeps every commit and pin change, and over this many
ticks both run out of memory. `westminster` only halts, so it needs only the halt branch of
that prediction.

[`make_west_bundle.py`](make_west_bundle.py) builds the bundle with `make_bundle.py`'s own
`bundle()` and that halt branch alone, on the ISS with `record=False`: the same image,
configuration, certificate and snapshot, and the final digest a barrel round after every
context has stopped. On uart_tx the same halt branch gives the digest and every snapshot
field `make_bundle.py`'s prediction gives. The run took 7,969 s on one laptop core, and
[`westminster.json`](westminster.json) is its output.

The bench is the version 3.2 bitstream of 26 November (the `chip.MANIFEST` of
[`results/2026-11-26/`](../2026-11-26/), the same one bring-up passed with 94 of 94) and the
Pico W, with the bundle in its own folder:

```
mpremote fs cp westminster.json :bundles_w/westminster.json
mpremote exec "import bringup, barepico; bringup.run(io=barepico.open_io(sys_hz=125000000), bundles='bundles_w', stages=[0,1,2,3,4,5])"
```

| Stage | Result ([`westminster.log`](westminster.log)) |
| --- | --- |
| 0 to 4 | PASS: board, 25 MHz clock and reset, host link, memories, pins; chip clock 25.0031 MHz |
| 5 firmware | PASS: `westminster` halts with digest `0x6531100e`, the model's; context 0's smallest lead on the chip is 57, exactly the lower bound its certificate proves |

With the 89 programs of 26 November, every one of the library's 90 programs has now run on the
chip's RTL and matched the model and its certificate.
