# The line unit

The line unit (v1.4) moves packets between the tape and the pins and does the line
coding on the way: NRZI, bit stuffing, a sync prefix, a CRC of any width up to 16 bits,
an end-of-packet, and clock recovery on receive. Firmware handles packets; the line
unit handles bits. It keeps the chip's timing model: a packet starts as an event at an
absolute deadline, and a reception reports the tick its packet ended, so a reply can
be scheduled a fixed number of ticks after the host's packet, exact to the tick.

The normative definition is [11-SEMANTICS §6.6](11-SEMANTICS.md#66-line-unit-v14-one-global).
Status: v1.4 specification, simulator, firmware and timing certificates. The RTL comes
next.

```
python -m escert firmware/usb_hid.asm      # the reply-timing certificate (below)
sudo python -m escusbip "some text"        # from sw/, on Linux: a real kernel types through the chip
```

## Why a line unit

At 24 MHz a low-speed USB bit is 16 ticks, and a context commits one instruction every
4 ticks: four instructions per bit. NRZI, stuffing and a running CRC-16 cost more than
that per bit, so low-speed USB cannot be done in software on this chip (CAN
transmission can, at modest bit rates: `firmware/can_tx.asm`).
A small datapath does the bit work, and one block serves several roles:

- **line coder** for transmission (`SEND`), with a monitor that stops a packet at the
  first bit that reads back wrong (CAN arbitration and acknowledgement);
- **receiver** with clock recovery: a hard sync at the start of a packet, a resync on
  every transition, a sample point inside each bit, destuffing, and a hunt for the
  sync pattern (`LINE` RECV);
- **CRC engine** on tape data (`LINE` CRC), which the USB firmware uses to compute its
  own token CRC-5 when the host gives it an address.

Two design rules come from the rest of the chip:

- **A reply is on time or not at all.** A `SEND` whose deadline has already passed is
  dropped (Z = 0) instead of faulting. Its deadline usually comes from the line, through
  `GETL`'s `tmr = e`, so a late one means the line did something early, not that the
  program is wrong; the protocol's own retry recovers. Every other event still faults
  when late.
- **One owner.** The first context whose `LINE` CONFIG commits owns the line unit for
  the run, and only the line unit drives the pins it claims. The other contexts keep
  their own pins and timing.

## Programming model

| Instruction | Effect |
| --- | --- |
| `LINE rs` **W** | `rs[31:28]` = 0 OFF, 1 RECV (buffer word `rs[7:0]`, limit `rs[12:8]` bytes), 2 CRC (word `rs[7:0]`, `rs[15:8]` bits, `rs[16]` result in sent order), 3 CONFIG (three words at `rs[7:0]`). Waits while a job is running, except OFF. |
| `SEND rs` **W** | a packet at deadline `tmr`: header `rs[23:16]`, append CRC `rs[15]`, `rs[14:8]` stream bits from word `rs[7:0]`. Z = 1 if queued, 0 if dropped as late. |
| `GETL rd` **W** | waits for the job to end, then `rd` = status and `tmr` = its end tick, Z = 1; or at `tmr` first, `rd` = status, Z = 0 (a timeout). |

The status word holds the last job (RECV, CRC, CONFIG, SEND), its cause (OK, stuffing
violation, SE0, buffer full, no sync, monitor mismatch), a bit count and the CRC
register. A good USB DATA packet, for instance, ends with cause OK, 8 bits per byte and
the CRC-16 residual 0x800D.

A configuration is three tape words: pins (`d`, `n` for a differential pair, `r` for
receive), NRZI, bit order, stuffing (N equal bits, then a 0 or their complement),
end-of-packet SE0 length, idle drive, a sync byte, a bit period in ticks and 1/256 tick,
a sample point, and a CRC polynomial with its start value and inversion. The USB
low-speed profile in `firmware/usb_hid.asm`:

| Word | Fields |
| --- | --- |
| W0 | D− data, D+ complement, differential, NRZI, LSB first, stuff after 6 ones, 2 bit times of SE0, sync skip on receive, claim the pins, prefix 0x80 (the SYNC field) |
| W1 | 16 ticks per bit (1.5 Mb/s at 24 MHz), sample 8 ticks into each bit |
| W2 | CRC-16 0x8005, all-ones start, inverted, header byte (the PID) outside the CRC; a second block with CRC-5 0x2800 for the token computation |

The CRC is general. Tests check it against the catalogue check values for CRC-5/USB,
CRC-15/CAN, CRC-16/USB, CRC-16/IBM-SDLC, CRC-16/XMODEM, CRC-16/IBM-3740, CRC-8/SMBUS
and CRC-8/MAXIM-DOW.

**Clock.** USB low speed allows 25 ns of jitter per data transition (USB 2.0, TDDJ1). At
25 MHz a bit is 16⅔ ticks, and the 1/256-tick period makes edges land up to 40 ns early,
so the USB firmware runs at 24 MHz, where a bit is exactly 16 ticks. The chip itself is
rated for 25 MHz.

## A USB keyboard

`firmware/usb_hid.asm` is a self-contained low-speed HID boot keyboard in 187
instructions:

- **Context 0** is the device: enumeration and the standard requests (descriptors,
  strings, status, configuration, SET_ADDRESS, SET_IDLE, SET_REPORT for the LEDs),
  STALL for requests it does not support, data toggles, the address change after the
  status stage, and bus reset.
- **Context 1** feeds key reports from its host mailbox into the tape, two words per
  report.

Every reply leaves 72 ticks after the end of the host's packet, 75 at the pins: 4.69 bit
times, inside USB's 2 to 6.5. The firmware arms the receiver again as soon as a packet
ends, answers whatever arrives instead of an expected ACK, and restarts endpoint 1's
data toggle at SET_CONFIGURATION and CLEAR_FEATURE(ENDPOINT_HALT) (USB 2.0 9.4.5). Its
token CRC-5 comes from the line unit's CRC engine, so no host has to patch the program
with an address.

VID:PID 1209:0001 is the pid.codes test ID. It is not unique and must not be used
outside test environments.

## Reply timing, certified

`escert` proves the firmware free of admission faults for any input, and reports when
each reply leaves:

```
ctx 0: CHECKED, min_lead >= 1 (20 event sites, 2229 states)
  SEND pc 46: deadline = ge + 72, lead >= 16: on time; may be dropped if the line was busy unwaited
  SEND pc 63: deadline = ge + 72, lead >= 4: on time; may be dropped if the line was busy unwaited
  SEND pc 90: deadline = ge + 72, lead >= 28: on time; may be dropped if the line was busy unwaited
  GETL pc 23: waits within 376 ticks of the arm or its last timeout
  GETL pc 78: waits within 16 ticks of the arm or its last timeout
  GETL pc 96: waits within 44 ticks of the arm or its last timeout
  GETL pc 168: waits within 52 ticks of the arm or its last timeout
ctx 1: CHECKED, min_lead >= inf (0 event sites, 16 states)
```

- **The three replies** (data or NAK on endpoint 0, a report or NAK on endpoint 1, and
  the handshake after a SETUP or OUT) have their deadline exactly 72 ticks after `ge`,
  the end tick `GETL` returned, on every path. Each is admitted with at least 4 ticks
  to spare whenever the packet it answers ended after its `GETL` began waiting.
- **Every `GETL` that waits on a reception** begins waiting within 376 ticks of arming
  the receiver (or of its previous timeout). The shortest packet the firmware answers
  is a token: SYNC, 24 bits and an end-of-packet, 34 bit times, about 536 ticks even
  from a host running 1.5% fast. So with a host that keeps to USB timing, every reply
  is on time; a host that does not costs a dropped reply and a retry, never a fault.

How it models the line unit: the zone also tracks the job's end tick, the tick the
receiver was armed and the end tick `GETL` returned. The unit is idle, armed (a
reception may be under way, its end an input), running a job the program started
(CONFIG ends at c + 9, CRC at c + nb, a packet after its deadline) or unknown. `GETL`
completes in the first slot after the job ends, one slot later after a reception, whose
last tape word is written after its end. The owner's tape accesses wait on the unit's
own. Tape words that no `STT` can write and no receive buffer covers are read as
constants, so the analysis knows which `LINE` operation runs. Waits are rounded to the
context's 4-tick slots. These bounds are tested against the simulator at the exact
boundary: one tick less and the simulator faults.

## Verification

| Suite | Tests | What |
| --- | ---: | --- |
| `sw/tests/test_line.py` | 117 | Every rule of §6.6 on the simulator: configuration timing and faults, waveforms against an independent USB model written from the USB 2.0 text, packets of 0 to 8 bytes, stuffing including after the CRC, fractional bit periods, reception at ±5% clock and with jitter, truncated SYNC, dribble bits, stuffing errors, keep-alives, resets, CAN frames through the CAN bus model, late `SEND`s dropped |
| `sw/tests/test_usb_hid.py` | 72 | The keyboard against a reactive USB host model that checks every device packet (EOP, stuffing, CRC, turnaround) and flags a device that drives the bus unasked: enumeration with exact descriptors, host clock ±1.5% and ±5%, random host gaps down to 2 bit times, keep-alives, `wLength` from 0 to 65535 around every packet boundary, standard and class requests, lost and corrupted handshakes, corrupted tokens and data, foreign addresses, bus reset at every stage, 10 ms resets with keep-alives every millisecond, long report sequences, glitches, hostile traffic |
| `sw/tests/test_escert.py` | 10 more | The certificate above, checked against measured leads; exact boundaries for CONFIG, CRC and reception waits; the path after a dropped `SEND`; random programs on the line unit (wherever escert says CHECKED, the simulator never faults and measures no lead below the bound) |
| `sw/tests/test_usbip.py` | 4 | The USB/IP bridge, with the test playing the kernel's side |

Planted bugs check that these tests can fail: 18 in the line unit model, 28 in the
firmware and 9 in escert's line unit analysis, all caught. The tests and the
certificate found real problems that earlier tests had passed: the firmware missed a
token sent two bit times after the host's ACK on a transfer's last packet, could send a
report with a stale data toggle after a reset, and went back to wait without re-arming
the receiver after a corrupt DATA stage. The comparison with measured leads found that
escert assumed a reception's `GETL` woke as early as any other job's, and a directed test
found that it read a negative constant added to a deadline as a large positive one.

## On a real Linux kernel

`python -m escusbip` (as root) plugs the simulated keyboard into the running kernel.
`vhci_hcd`, the kernel's USB/IP host controller, takes one end of a socket pair through
its sysfs `attach` file and sends every URB over it; the bridge answers each one by
running the USB transactions, packet by packet, on the pins of the simulator running
`usb_hid.asm`, decoding the chip's line states, and returning what the firmware sent.
Nothing is stubbed: every descriptor and key report the kernel sees was produced by the
firmware through the line unit. Simulated time stands still while the kernel has
nothing outstanding. It runs in WSL2 too.

```
attached to vhci_hcd port 0 as a low-speed device
enumerated in 1.6 s as 1-1; input device /dev/input/event0
Bus 001 Device 002: ID 1209:0001 Generic pid.codes Test PID
typed 'The quick brown fox: 0123456789, jumps; over the lazy dog?!\n'; the kernel reported 'The quick brown fox: 0123456789, jumps; over the lazy dog?!\n'
usb 1-1: new low-speed USB device number 2 using vhci_hcd
input: Escapement as /devices/platform/vhci_hcd.0/usb1/1-1/1-1:1.0/0003:1209:0001.0001/input/input0
hid-generic 0003:1209:0001.0001: input,hidraw0: USB HID v1.11 Keyboard [Escapement] on usb-vhci_hcd.0-1/input0
PASS
```

The real kernel also found bugs in the bridge that its first stand-in for the kernel
had hidden: USB/IP returns no data for OUT transfers, and the bridge could stall when
keys arrived while it was idle. Both are fixed, and the test now reads replies exactly
as `vhci_hcd` does.
