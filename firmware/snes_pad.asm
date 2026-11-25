; SNES controller reader: the chip is the console side of one controller port. On each host
; request it latches the controller's buttons and shifts its 16 bits in, and returns them.
; Held-out round, written 2026-11-24 on v3.1 (cb47436), from:
; - Nintendo US patent 5,207,426 (1993), "Controller for a game machine": the controller's
;   signal processing circuit is a parallel-in, serial-out shift register, loaded by a
;   parallel write signal from the console and read serially with a clock;
; - TI (Harris) CD4021B data sheet SCHS024C (rev. Oct 2003), the shift register in it:
;   asynchronous parallel load while PARALLEL/SERIAL CONTROL (P/S) is high, a shift on each
;   rising CLOCK edge while P/S is low; the limits at VDD = 5 V used here (Dynamic Electrical
;   Characteristics): P/S pulse >= 160 ns, P/S removal before a clock edge >= 280 ns, clock
;   pulse >= 180 ns, clock <= 3 MHz, propagation <= 320 ns, transition <= 200 ns;
;   (Fig. 2 and the truth table: Q8 is the last stage; SERIAL INPUT shifts in behind);
; - the SNES Development Manual, Book I (Nintendo, 1993; archive.org item snes_manual1),
;   2-24-8, 2-24-12 and 2-28-8/9: the 16-bit key data with the device's signature in its
;   last four bits (0000 for the standard controller), and the 17th bit telling whether a
;   controller is connected. Only the bit layout is taken from it.
;
;   pin LATCH_PIN (0)  out: latch (to P/S), idle low
;   pin CLK_PIN (1)    out: clock, idle high
;   pin DATA_PIN (2)   in:  serial data (Q8), a button pressed reads low
; Context CTX (0). Clock CLK_KHZ (25 MHz by default). LATCH_NS (12 us by default) high, then
; 17 clocks of period 2 x HALF_NS (6 us by default: 12 us a bit, as slow as a console reads, so
; that cables and clones have room); HALF_NS can be as short as 320 ns at 50 MHz, 640 ns at
; 25 MHz (a bit is 3 events and 4 instructions: 16 ticks a half period). DATA is sampled a
; whole period after the rising edge that shifted it out, and the first bit LATCH_NS + a
; period after the load: both well past the 520 ns it takes to settle.
; Per bit: DATA shows the bit while CLK is low; it is sampled just before CLK rises (the rising
; edge shifts the next bit out). The first bit is on DATA after the latch's parallel load.
;
; Host (mailbox, context CTX): any word asks for one read. Reply: [15:0] the 16 bits, the
; first bit read at [15], a 1 for a line read low (pressed); [3:0] is the signature, 0000 for a
; standard controller. For the standard controller ([15] first): B, Y, Select, Start, Up,
; Down, Left, Right, A, X, L, R, then the signature. [16]: the 17th bit, 1 (read low) when a
; controller is connected (2-28-9; DATA needs a pull-up on the board so that no controller
; reads high).

.default CTX, 0
.default LATCH_PIN, 0
.default CLK_PIN, 1
.default DATA_PIN, 2
.default CLK_KHZ, 25000
.default LATCH_NS, 12000
.default HALF_NS, 6000
.equ LATM, 1 << LATCH_PIN
.equ CLKM, 1 << CLK_PIN
.equ LT, (LATCH_NS * CLK_KHZ + 999999) // 1000000      ; ticks, rounded up
.equ HT, (HALF_NS * CLK_KHZ + 999999) // 1000000
.equ SETTLE, (520 * CLK_KHZ + 999999) // 1000000        ; 320 ns propagation + 200 ns transition
.assert LATCH_NS >= 160, P/S pulse width >= 160 ns
.assert HALF_NS >= 180, clock pulse width >= 180 ns (so also <= 3 MHz: 360 ns a period)
.assert 2 * HALF_NS >= 280, P/S removal time: the first rising edge >= 280 ns after the latch falls
.assert HT >= 16, a half period is a SAMPLE and its rising edge: 16 ticks at least
; DATA settles 520 ns after a rising edge, and the SAMPLE reads the pin 2 ticks early
.assert 2 * HT >= SETTLE + 4, DATA settled when it is sampled

.entry CTX, main
.own LATCH_PIN, CTX
.own CLK_PIN, CTX

main:
    ADDT  40
    EMIT  LATM | CLKM, CLKM, LATM | CLKM     ; latch low, clock high
loop:
    POP   r0
    RDTIME r1
    ADDI  r1, 40
    SETT  r1
    EMIT  LATM, LATM, LATM     ; the parallel load: the buttons, and the first bit on DATA
    ADDT  LT
    EMIT  LATM, 0, LATM        ; shift mode
    LDLC  17
bit:                           ; tmr: the latch's fall, or the last rising edge
    ADDT  HT
    EMIT  CLKM, 0, CLKM
    ADDT  HT - 2
    SAMPLE DATA_PIN, MSB       ; the bit, just before the rising edge
    ADDT  2
    EMIT  CLKM, CLKM, CLKM     ; shift: the next bit out
    DJNZ  bit
    GETI  r1                   ; [16] the first bit, [0] the 17th
    MOVI  r2, 0xFFFF
    MOVHI r2, 0x0001
    XOR   r1, r2               ; a line read low is a 1
    MOV   r2, r1
    SHL   r2, 31
    SHR   r2, 15               ; the 17th bit to [16]
    SHR   r1, 1                ; the 16 bits to [15:0]
    OR    r1, r2
    PUSH  r1
    JMP   loop
