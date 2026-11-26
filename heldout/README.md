# Held-out protocols

A test of the claim that Escapement takes protocols it was not designed for: eight
protocols chosen before any of them is written, and sealed so the choice cannot change
afterwards.

- [`pool.txt`](pool.txt) lists 24 protocols, each with a public specification and none in
  [`firmware/`](../firmware) when the pool was fixed (2026-11-04, v1.6).
- Eight were drawn from the pool at random (Python's `random.Random(seed).sample(range(1, 25), 8)`
  with a fresh 128-bit seed), not picked.
- [`sealed.txt`](sealed.txt) holds a 256-bit random salt and the eight drawn lines. Its
  SHA-256 is in [`SEAL.sha256`](SEAL.sha256), committed before any of the eight was
  written. [`draw.json`](draw.json) records the draw: its date, the seed and both hashes.

Each drawn protocol was then written from its specification, certified with `escert`,
co-simulated against an independent model, and run on the FPGA bench. The list was opened
on 24 November 2026; every outcome is in [`RESULTS.md`](RESULTS.md).

To check the seal and repeat the draw:

    sha256sum -c SEAL.sha256
    python3 -c "import random; print(random.Random('e3839cc985016dea828246d4a44981f5').sample(range(1, 25), 8))"

The second line prints `[19, 9, 14, 24, 1, 21, 16, 6]`, the pool numbers in `sealed.txt`.

| File | SHA-256 |
| --- | --- |
| `pool.txt` | `624a3f4ca04f7f05ed692a565981550b5aa3613630c65bc76c5474feb11d513d` |
| `sealed.txt` | `c8599fffda4d8a9ea7393c1617fb6320c0168cc1614577d74fc177895cd9b6ab` |
| `draw.json` | `bcc601f0c2f239e6e683bc8eac5500688d6c9bf915a521dd080d1000e95119e6` |
