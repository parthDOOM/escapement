; Microwire controller for a 93xx46 serial EEPROM (93AA46, 93LC46, 93C46: 1 Kbit, x16 or x8):
; read, write, erase, erase all, write all, erase/write enable and disable, with the
; Ready/Busy poll after every programming instruction. Held-out round, written 2026-11-24 on
; v3.1 (cb47436), from Microchip DS21749H (2010), 93AA46A/B/C, 93LC46A/B/C, 93C46A/B/C.
;
;   pin CS_PIN (0)    out: CS (chip select, active high)
;   pin CLK_PIN (1)   out: CLK (serial clock, idle low)
;   pin DI_PIN (2)    out: DI (the EEPROM's data in)
;   pin DO_PIN (3)    in:  DO (the EEPROM's data out; high-Z, so a pull-up or pull-down, when
;                     it is not reading or showing Ready/Busy)
; Context CTX (0). Clock CLK_KHZ (25 MHz by default; at least 32 x SK_KHZ, so 16 MHz for the
; default 500 kHz CLK). ORG16 = 1 for x16 (a 'B' part, or a 'C'
; part with ORG high: 64 words, 6 address bits), 0 for x8 (an 'A' part, or ORG low: 128 bytes,
; 7 address bits).
;
; Timing (Table 1-2, the rows for 2.5 V <= VCC <= 5.5 V, which also hold from 4.5 V): CLK at
; SK_KHZ (A1, at most 2 MHz) with high and low times of H ticks each (A2 >= 250 ns, A3 >= 200
; ns); CS rises H ticks before the first DI change and 2H - 1 before the first rising edge
; (A4 >= 100 ns); DI changes 1 tick after CLK falls, so it is set up H - 1 ticks before the
; rising edge and held H + 1 after it (A7, A8 >= 100 ns); CS falls after the last falling
; edge (A5 >= 0) and stays low at least 2H ticks (A6 >= 250 ns). DO is sampled 2H - 2 ticks
; after the rising edge that puts the bit out (A9 <= 250 ns), and the Ready/Busy level 2H
; ticks after CS rises (A11 <= 300 ns).
;
; Instructions (Tables 1-3 and 1-4; section 2): a start bit, two opcode bits, the address
; (6 or 7 bits), then for WRITE and WRAL the data (16 or 8 bits). READ: the dummy zero that
; precedes the data (2.7) is sampled and checked. ERASE, ERAL, WRITE and WRAL: CS low ends the
; instruction (and starts the self-timed cycle on AA and LC parts, 2.4); after CS has been low
; for 2H, CS rises and DO is watched until it shows ready (high) or TOUT_MS passes (A12-A15:
; at most 15 ms, WRAL), then CS falls again. The part powers up write-disabled (2.3): the
; host sends EWEN before programming.
; Sequential read (2.7) is not used: each READ is one instruction.
;
; Host (mailbox, context CTX), one reply word per command:
;   [31:28] 1 READ  [23:16] address           -> data, [31] = 1 if the dummy bit was not 0
;           2 WRITE [23:16] address [15:0] data -> 1 ready, 0 timed out
;           3 ERASE [23:16] address           -> 1 / 0
;           4 ERAL                            -> 1 / 0
;           5 WRAL  [15:0] data               -> 1 / 0
;           6 EWEN                            -> 1
;           7 EWDS                            -> 1
;   any other value is ignored (no reply)

.default CTX, 0
.default CLK_KHZ, 25000
.default SK_KHZ, 500
.default ORG16, 1
.default TOUT_MS, 20
.default CS_PIN, 0
.default CLK_PIN, 1
.default DI_PIN, 2
.default DO_PIN, 3
.equ CS, 1 << CS_PIN
.equ SK, 1 << CLK_PIN
.equ DI, 1 << DI_PIN
.equ DOM, 1 << DO_PIN
.equ H, (CLK_KHZ + 2 * SK_KHZ - 1) // (2 * SK_KHZ)    ; rounded up: CLK never above SK_KHZ
.assert SK_KHZ <= 2000, A1: 2 MHz at most
.assert H * 1000000 >= 250 * CLK_KHZ, A2: a CLK high (and low) time of 250 ns or more
; a bit takes 7 instructions (28 ticks), and CS and the read need more
.assert H >= 16, a half period of 16 ticks or more
.equ AB, 7 - ORG16                  ; address bits
.equ DB, 8 + 8 * ORG16              ; data bits
.equ TOUT, TOUT_MS * CLK_KHZ         ; the Ready/Busy wait, in ticks
.assert TOUT < 1 << 24, TOUT_MS too long for one ADDT

.entry CTX, main
.own CS_PIN, CTX
.own CLK_PIN, CTX
.own DI_PIN, CTX

main:
    ADDT  40
    EMIT  CS | SK | DI, 0, CS | SK | DI    ; CS, CLK and DI low: standby
loop:
    POP   r0
    MOV   r1, r0
    SHR   r1, 28
    MOV   r2, r0                ; the address field, AB bits
    SHL   r2, 16 - AB
    SHR   r2, 32 - AB
    MOV   r3, r0                ; the data field, DB bits
    SHL   r3, 32 - DB
    SHR   r3, 32 - DB
    CMPI  r1, 1
    JZ    o_read
    CMPI  r1, 2
    JZ    o_write
    CMPI  r1, 3
    JZ    o_erase
    CMPI  r1, 4
    JZ    o_eral
    CMPI  r1, 5
    JZ    o_wral
    CMPI  r1, 6
    JZ    o_ewen
    CMPI  r1, 7
    JZ    o_ewds
    JMP   loop

o_read:                         ; 1 10 A
    ADDI  r2, 0b110 << AB
    SHL   r2, 32 - (3 + AB)
    LDSH  r2
    LDLC  3 + AB
    CALL  send
    LDLC  DB
    CALL  rbits                 ; the dummy zero and DB data bits
    CALL  csoff                 ; (queued after the samples, before reading them)
    GETI  r0
    SHL   r0, 31 - DB
    SHR   r0, 31 - DB           ; [DB] the dummy, [DB-1:0] the data
    MOV   r1, r0
    SHR   r1, DB
    SHL   r1, 31                ; [31]: the dummy bit was 1
    SHL   r0, 32 - DB
    SHR   r0, 32 - DB
    OR    r0, r1
    PUSH  r0
    JMP   loop

o_write:                        ; 1 01 A D
    ADDI  r2, 0b101 << AB
    SHL   r2, DB
    OR    r2, r3
    SHL   r2, 32 - (3 + AB + DB)
    LDSH  r2
    LDLC  3 + AB + DB
    JMP   program
o_erase:                        ; 1 11 A
    ADDI  r2, 0b111 << AB
    SHL   r2, 32 - (3 + AB)
    LDSH  r2
    LDLC  3 + AB
    JMP   program
o_eral:                         ; 1 00 10x...
    MOVI  r2, (0b100 << AB) | (0b10 << (AB - 2))
    SHL   r2, 32 - (3 + AB)
    LDSH  r2
    LDLC  3 + AB
    JMP   program
o_wral:                         ; 1 00 01x... D
    MOVI  r2, (0b100 << AB) | (0b01 << (AB - 2))
    SHL   r2, DB
    OR    r2, r3
    SHL   r2, 32 - (3 + AB + DB)
    LDSH  r2
    LDLC  3 + AB + DB
    JMP   program
o_ewen:                         ; 1 00 11x...
    MOVI  r2, (0b100 << AB) | (0b11 << (AB - 2))
    JMP   o_en
o_ewds:                         ; 1 00 00x...
    MOVI  r2, 0b100 << AB
o_en:
    SHL   r2, 32 - (3 + AB)
    LDSH  r2
    LDLC  3 + AB
    CALL  send
    CALL  csoff
    MOVI  r0, 1
    PUSH  r0
    JMP   loop

program:                        ; send, CS low, then the Ready/Busy poll
    CALL  send
    CALL  csoff
    RDTIME r3
    ADDI  r3, 40
    SETT  r3
    EMIT  CS, CS, CS            ; CS high: DO shows Ready/Busy (2.4)
    ADDT  2 * H                 ; status valid (A11)
    WAITT
    ADDT  TOUT
    WAITP DOM, DOM              ; ready: DO high
    JZ    p_ready
    MOVI  r0, 0                 ; timed out
    JMP   p_end
p_ready:
    MOVI  r0, 1
p_end:
    RDTIME r3
    ADDI  r3, 40
    SETT  r3
    EMIT  CS, 0, CS             ; CS low: DO high-Z again
    ADDT  2 * H                 ; A6 before the next instruction
    WAITT
    PUSH  r0
    JMP   loop

; send: sh = the instruction, MSB first, lc = its bits. CS, CLK and DI are low on entry.
; Leaves tmr at the last falling CLK edge (T of the next bit).
send:
    RDTIME r3
    ADDI  r3, 40
    SETT  r3
    EMIT  CS, CS, CS            ; T0: CS high
    ADDT  H
sb:                             ; tmr = T, CLK low since T
    ADDT  1
    EMITX DI, MSB, DI           ; T + 1: DI
    ADDT  H - 1
    EMIT  SK, SK, SK            ; T + H: CLK rises: the EEPROM takes DI
    ADDT  H
    EMIT  SK, 0, SK             ; T + 2H: CLK falls
    DJNZ  sb
    RET

; rbits: after a READ's address, the dummy zero then lc data bits. Leaves tmr at the last
; falling edge plus H.
rbits:
    ADDT  1
    EMIT  DI, 0, DI             ; T + 1: DI low while reading
    ADDT  H - 3
    SAMPLE DO_PIN, MSB          ; T + H - 2: the dummy zero (out since A0's rising edge)
    ADDT  2
rb:
    EMIT  SK, SK, SK            ; T + H: CLK rises: the next data bit comes out
    ADDT  H - 2
    SAMPLE DO_PIN, MSB          ; T + 2H - 2
    ADDT  2
    EMIT  SK, 0, SK             ; T + 2H
    ADDT  H
    DJNZ  rb
    RET

; csoff: CS low after the last falling edge, and held low 2H ticks (A6); DI low.
csoff:
    ADDT  1
    EMIT  CS | DI, 0, CS | DI
    ADDT  2 * H
    WAITT
    RET
