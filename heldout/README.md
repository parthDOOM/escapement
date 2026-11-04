# Held-out protocols

A test of the claim that Escapement takes protocols it was not designed for: eight
protocols chosen before any of them is written, and sealed so the choice cannot change
afterwards.

- [`pool.txt`](pool.txt) lists 24 protocols, each with a public specification and none in
  [`firmware/`](../firmware) when the pool was fixed (2026-11-04, v1.6).
- Eight were drawn from the pool at random (Python's `random.Random(seed).sample(range(1, 25), 8)`
  with a fresh 128-bit seed), not picked.
- `sealed.txt`, kept back for now, holds a 256-bit random salt and the eight drawn lines.
  Its SHA-256 is in [`SEAL.sha256`](SEAL.sha256), committed before any of the eight is
  written. When the list is opened, `sealed.txt` and the seed are published, so anyone can
  check the hash and repeat the draw.

Each drawn protocol is then written from its specification, certified with `escert`,
co-simulated against an independent model, and run on the FPGA bench. Every outcome is
published here when the list is opened, including the ones that fail or cannot be done on
this chip, with the reason.

| File | SHA-256 |
| --- | --- |
| `pool.txt` | `624a3f4ca04f7f05ed692a565981550b5aa3613630c65bc76c5474feb11d513d` |
| `sealed.txt` | `c8599fffda4d8a9ea7393c1617fb6320c0168cc1614577d74fc177895cd9b6ab` |
