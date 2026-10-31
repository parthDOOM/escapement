; Frequency, period and duty meter (v1.6), by reciprocal counting: the capture unit stamps
; every edge of one input with its tick, so a measurement over N whole periods is exact to
; one tick (the synchroniser delays every edge alike), at any frequency. Gates follow one
; another with no dead time: the rise that ends one starts the next.
;
; Per gate the host gets two words (POP order), each meaningful in its low 24 bits:
;   ticks of N periods        frequency = N * f_clk / it, mean period = it / N
;   ticks high in them        duty = it / ticks of N periods
; so a gate must be shorter than 2^24 ticks (0.42 s at 40 MHz): choose N for it.
;
; The capture unit keeps a ring of records over the whole tape; the program reads it
; behind the writer. It checks the distance to the writer at every poll and never lets it
; reach 128 records: from one poll to the next it runs at most 27 instructions (108 cycles,
; so at most 108 new records), so a record is never overwritten before it is read. A
; signal faster than the program keeps up with (a record every ~52 cycles, ~385 kHz at
; 40 MHz) can come in bursts of up to 128 edges. Beyond that: IRQ 1, and measuring
; restarts. A gate whose result finds the TX mailbox full is dropped: IRQ 2 (the host
; should take each pair within N periods). No pins are owned: observation only.

.default CTX, 0
.default PIN, 0                   ; the input: a pin of bank A, or of bank B with BANKB
.default BANKB, 0
.default N, 1000                  ; periods per measurement
.assert (PIN >= 0) & (PIN <= 7), PIN is a bank pin
.assert (BANKB == 0) | (PIN <= 4), bank B has pins 0..4
.assert (N >= 1) & (N <= 65535), N is 1..65535
.equ CFG, 0x8100 | (BANKB << 9) | (1 << PIN)     ; enable, ring, bank, watched pin

.entry CTX, main

main:
restart:
    MOVI  r0, 0                   ; base = limit = 0: the ring is the whole tape
    MOVHI r0, CFG
    CAPT  r0                      ; its first record (in slot 0) is a snapshot
snap:
    GETC  r1
    SHL   r1, 24
    JZ    snap
    MOVI  r0, 1                   ; r0: the next slot to read (the snapshot is skipped)

; Each poll: r1 = (writer - r0) << 25: C = 128 records or more behind, Z = none to read.
; A record's level bit leaves last in SHL by 8 - PIN, into C.
seek:                             ; the first rise
    GETC  r1
    SUB   r1, r0
    SHL   r1, 25
    JC    over
    JZ    seek_e
    LDT   r1, r0, 0
    ADDI  r0, 1
    MOV   r2, r1
    SHL   r2, 8 - PIN
    JNC   seek                    ; a fall, or an epoch marker
    LDSH  r1                      ; sh = the gate's first rise
    JMP   next

; In a gate, r3 accumulates the high time: + each fall, - each rise. Ticks are differenced
; as whole records: the low 24 bits of a difference depend only on the tick fields.
wf:                               ; the next fall
    GETC  r1
    SUB   r1, r0
    SHL   r1, 25
    JC    over
    JZ    wf_e
    LDT   r1, r0, 0
    ADDI  r0, 1
    MOV   r2, r1
    SHL   r2, 8 - PIN
    JC    wf                      ; level 1: an epoch marker
    ADD   r3, r1
wr:                               ; the next rise
    GETC  r1
    SUB   r1, r0
    SHL   r1, 25
    JC    over
    JZ    wr_e
    LDT   r1, r0, 0
    ADDI  r0, 1
    MOV   r2, r1
    SHL   r2, 8 - PIN
    JNC   wr                      ; level 0: an epoch marker
    SUB   r3, r1
    DJNZ  wf                      ; N rises after the first end the gate
    ADD   r3, r1                  ; (the last one ends the high time count)
    STSH  r2                      ; r2 = the gate's first rise
    LDSH  r1                      ; the last starts the next gate
    SUB   r1, r2                  ; r1 = N periods
    TXREADY                       ; Z = full
    JZ    drop
    PUSH  r1
tx2:
    TXREADY
    JZ    tx_w
    PUSH  r3
next:
    STSH  r2
    MOVI  r3, 0
    SUB   r3, r2                  ; - the first rise
    LDLC  N
    JMP   wf

tx_w:                             ; the high time waits for room; the ring keeps filling
    GETC  r2
    SUB   r2, r0
    SHL   r2, 25
    JC    over
    JMP   tx2
drop:
    IRQ   2
    JMP   next
; Nothing to read: has the capture unit stopped (staging overflow, GETC bit 30)?
seek_e:
    GETC  r2
    SHL   r2, 2
    JC    over
    JMP   seek
wf_e:
    GETC  r2
    SHL   r2, 2
    JC    over
    JMP   wf
wr_e:
    GETC  r2
    SHL   r2, 2
    JC    over
    JMP   wr
over:
    IRQ   1
    JMP   restart
