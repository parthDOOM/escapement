; SPI controller, modes 0-3 selected at run time, MSB first, 500 kHz at 25 MHz.
; Context 2: SCK = pin 4, MOSI = pin 5, MISO = pin 6 (input), CS_n = pin 7.
;
; Host protocol (mailboxes):
;   1st word  config: [0] CPHA, [1] CPOL
;   then per byte: [7:0] byte to send, [8] keep CS_n low afterwards (multi-byte frame)
;   result per byte: [7:0] byte received on MISO
; Every SCK edge, MOSI change and MISO sample is an event at an absolute tick:
; half-period H = 25 ticks (1 us). Same-context events need distinct ticks, so MOSI
; changes 1 tick after the shift edge and MISO is sampled 1 tick after the sample edge.
;
; Registers: r0 byte+flags, r1 SCK active level [7:0] + CPHA flag [8],
;            r2 SCK idle level (CPOL), r3 scratch.

.default CTX,      2        ; module parameters
.default SCK_PIN,  4
.default MOSI_PIN, 5
.default MISO_PIN, 6
.default CS_PIN,   7
.equ SCK,      1 << SCK_PIN
.equ MOSI,     1 << MOSI_PIN
.equ CS,       1 << CS_PIN
.equ H,        25

.entry CTX, main
.own   SCK_PIN, CTX
.own   MOSI_PIN, CTX
.own   CS_PIN, CTX

main:
    ADDT  40
    EMIT  SCK|MOSI|CS, CS, SCK|MOSI|CS   ; CS_n high, SCK and MOSI low
    POP   r3                 ; config word
    MOVI  r2, 0
    TSTI  r3, 2
    JZ    cpol0
    MOVI  r2, SCK            ; CPOL = 1: SCK idles high
cpol0:
    MOVI  r1, SCK
    XOR   r1, r2             ; active level = SCK ^ idle
    TSTI  r3, 1
    JZ    cpha_set
    ADDI  r1, 0x100          ; remember CPHA in bit 8 (EMITR only uses bits 7:0)
cpha_set:
    RDTIME r3
    ADDI  r3, 20
    SETT  r3
    EMITR SCK, r2, SCK       ; SCK to its idle level before the first frame
next:
    POP   r0                 ; byte + flags
    MOV   r3, r0             ; set up the shift register BEFORE anchoring the
    SHL   r3, 24             ; timeline, so the first bit's deadline is reachable
    LDSH  r3                 ; (the ISS caught the late-edge version: ADMIT/PAST)
    LDLC  8
    RDTIME r3
    ADDI  r3, 20
    SETT  r3
    EMIT  CS, 0, CS          ; CS_n low (no change if already low)
    ADDT  H                  ; CS_n setup before the first edge
    TSTI  r1, 0x100
    JNZ   cpha1
cpha0:                       ; T = bit start
    ADDT  1
    EMITX MOSI, MSB, MOSI    ; T+1    data out
    ADDT  H-1
    EMITR SCK, r1, SCK       ; T+H    leading edge
    ADDT  1
    SAMPLE MISO_PIN, MSB     ; T+H+1  sample MISO
    ADDT  H-1
    EMITR SCK, r2, SCK       ; T+2H   trailing edge
    DJNZ  cpha0
    JMP   byte_done
cpha1:
    ADDT  1
    EMITR SCK, r1, SCK       ; T+1    leading edge
    ADDT  1
    EMITX MOSI, MSB, MOSI    ; T+2    data out
    ADDT  H-1
    EMITR SCK, r2, SCK       ; T+H+1  trailing edge
    ADDT  1
    SAMPLE MISO_PIN, MSB     ; T+H+2  sample MISO
    ADDT  H-2                ; next bit at T+2H
    DJNZ  cpha1
byte_done:
    ADDT  H                  ; CS_n hold after the last edge
    TSTI  r0, 0x100
    JNZ   keep_cs
    EMIT  CS, CS, CS         ; CS_n high
    ADDT  H
keep_cs:
    WAITT                    ; let the last SAMPLE retire before reading ish:
    GETI  r3                 ; the bit loop runs ahead of real time (queued events)
    PUSH  r3
    JMP   next
