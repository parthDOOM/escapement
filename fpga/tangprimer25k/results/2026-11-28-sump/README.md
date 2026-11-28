# SUMP bridge on the FPGA bench, 2026-11-28

`sw/rp2350/sump.py` lets PulseView and sigrok-cli capture from the chip through libsigrok's
Openbench Logic Sniffer driver (`ols`). Until this run it had been tested only against the
simulated demo board (`sw/tests/test_sump.py`). Here it runs on the bench: the version 3.2
bitstream of 26 November (`wt-ci` build, the one bring-up passed with 94 of 94), the Pico W
as the board, and sigrok-cli 0.7.2 with the `ols` driver PulseView uses.

## The chain

| Where | What |
| --- | --- |
| Pico W | `olsstart.py`: two square waves from PIO into the chip's inputs, uio[2] (GP19) at 10 kHz and uio[3] (GP20) at 1 kHz, then `sump.serve(io=barepico.open_io(...))`. The bridge sets every uio pin to an input with a pull-up before each capture, as it must on the demo board, so the waves are restarted right after that step |
| chip | `logic_analyzer_stream`, the streaming capture: change records of uio[7:0] over SPI during the run |
| laptop | `olsrelay_loop.py` passes the bytes of the Pico's USB serial port, unchanged, to `socat` in WSL, which cannot open a Windows COM port; `olscap.sh` runs sigrok-cli on socat's pseudo-terminal, with `sw/tests/pty_serial_shim.c` preloaded as in `test_sump.py` (libserialport cannot open a pty by itself) and `serialcomm=300/8n1`, which a pty ignores but which gives libsigrok's per-byte timeouts the relay's round trip |

Run: start `olscap.sh` in WSL, `mpremote exec --no-follow "import olsstart"` on the Pico, then
`python olsrelay_loop.py COM8 9555 <the same out folder>` on Windows.

## Results

| Capture | Result |
| --- | --- |
| `triggered_2ms.sr` (and `.vcd`): `--config samplerate=1m --samples 2000 -t 3=1` | sigrok-cli identified the bridge (8 channels) on the first attempt. uio[2]: 40 edges, every interval exactly 50 samples (50 µs, 10 kHz); uio[3]: an edge every 500 samples (1 kHz), at samples 500, 1000 and 1500; the other six channels high, their pull-ups |
| `untriggered_20ms_overflow.sr`: `--samples 20000`, no trigger | The edges are right while they last, 50 and 500 µs, but stop after 2.9 ms and the last level holds to the end. The two waves make 22,000 edges a second, and the bench's host link, bit-banged by MicroPython (one pin write about 36 µs, SCK half period about 68 µs), reads the chip's record stream far more slowly than that, so the ring fills, the chip stops the capture and records the overflow tick, as it is designed to. SUMP has no way to report the overflow to PulseView: the bridge keeps it in its log, and the samples after it repeat the last level |

So the path works end to end on the chip's RTL, with its timing exact to the sample. A
capture lasts as long as the host link keeps up with the line's edges: on this bench a few
milliseconds of a fast line, and longer for slower ones. PulseView's window itself was not
opened; sigrok-cli drives the same driver.
