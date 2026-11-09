; 1-Wire device (Maxim DS18B20 datasheet; standard speed, AN126 timing): the chip answers a
; bus master on DQ, open-drain on pin PIN, as a DS18B20 temperature sensor would.
;
; Every time slot starts with the master's falling edge. In a slot where the device sends
; a 0 it pulls DQ low at once (about 1.5 us after the edge at 25 MHz) and releases it 45 us
; after the edge; otherwise it leaves DQ alone. Either way it samples DQ 30 us after the
; edge: the master's bit in a write slot. DQ low for 240 us or more is a reset: 30 us after
; DQ rises the device pulls it low for 120 us (presence).
;
; ROM commands: 0x33 Read ROM, 0x55 Match ROM, 0xCC Skip ROM, 0xF0 Search ROM (per ROM bit,
; least significant first: the bit, its complement, then the master's direction; a device
; whose bit differs drops out until the next reset). Then function commands: 0x44 Convert T
; (done at once: read slots return 1), 0xBE Read Scratchpad (9 bytes), 0x4E Write Scratchpad
; (TH, TL, configuration). Anything else: wait for the next reset.
;
; The ROM is the family code 0x28, a 48-bit serial number and its CRC-8; the scratchpad is
; the temperature (LSB, MSB), TH, TL, configuration, 0xFF, 0x0C, 0x10 and its CRC-8. The
; chip computes both CRCs (Maxim CRC-8: x^8 + x^5 + x^4 + 1, least significant bit first).
; Host (POP), between transactions: [31:30] 01: serial bytes 1-3 in [23:0] (byte 1 in
; [7:0]); 10: serial bytes 4-6; 11: the temperature, [15:0] in 1/16 degree C.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000
.default TB, 0xD0                           ; tape: ROM (8 words), scratchpad (9), scratch
.equ US, CLK_KHZ // 1000
.equ D, 1 << PIN
.equ ROM, TB
.equ SP, TB + 8
.equ NEXT, TB + 17                          ; where the byte loops go on to
.equ PTR, TB + 18
.equ CNT, TB + 19
.equ BIDX, TB + 20                          ; Search ROM: the bit index
.assert TB + 21 <= 256, "the tape area does not fit"
.assert US >= 4, "the clock is too slow for 1-Wire slots"

.tape ROM, 0x28, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0
.tape SP, 0x50, 0x05, 0x4B, 0x46, 0x7F, 0xFF, 0x0C, 0x10, 0

.entry CTX, main
.own   PIN, CTX
.od    PIN

main:
    ADDT  20
    EMIT  D, D, D            ; released
    CALL  romcrc
    CALL  spcrc
idle:                        ; between transactions: a slot (perhaps a reset), or the host
    RDTIME r3
    SETT  r3
    ADDT  CLK_KHZ            ; 1 ms
    WAITP D, 0
    JZ    iedge
    RXREADY
    JZ    idle
    POP   r0                 ; (RXREADY saw it: no wait)
    MOV   r1, r0
    SHR   r1, 30
    CMPI  r1, 3
    JZ    htemp
    MOVI  r2, ROM + 1        ; 01: serial bytes 1-3; 10: 4-6
    CMPI  r1, 1
    JZ    hser
    MOVI  r2, ROM + 4
hser:
    LDLC  3
hs:
    MOV   r1, r0
    SHL   r1, 24
    SHR   r1, 24
    STT   r1, r2, 0
    ADDI  r2, 1
    SHR   r0, 8
    DJNZ  hs
    CALL  romcrc
    JMP   idle
htemp:
    MOV   r1, r0
    SHL   r1, 24
    SHR   r1, 24
    MOVI  r3, 0
    STT   r1, r3, SP
    SHL   r0, 16
    SHR   r0, 24
    STT   r0, r3, SP + 1
    CALL  spcrc
    JMP   idle
iedge:                       ; an edge between transactions: an ignored slot, or a reset
    MOVI  r0, 1
    CALL  bedge
    JMP   idle

; ---- after a reset and presence: the ROM command
romcmd:
    MOVI  r2, 0xFF
    MOVI  r1, 1
    JMP   rx1
n_rom:                       ; r1 = the ROM command
    CMPI  r1, 0xCC
    JZ    func
    CMPI  r1, 0x33
    JZ    readrom
    CMPI  r1, 0x55
    JZ    matchrom
    CMPI  r1, 0xF0
    JZ    search
    JMP   idle
readrom:
    MOVI  r1, ROM
    MOVI  r2, 8
    MOVI  r0, 2              ; then the function command
    JMP   sendn
matchrom:                    ; 8 bytes from the master, each compared
    MOVI  r3, 0
    STT   r3, r3, PTR
mloop:
    MOVI  r2, 0xFF
    MOVI  r1, 3
    JMP   rx1
n_match:
    MOVI  r3, 0
    LDT   r2, r3, PTR
    LDT   r0, r2, ROM
    CMP   r0, r1
    JNZ   idle               ; not this device
    ADDI  r2, 1
    STT   r2, r3, PTR
    CMPI  r2, 8
    JNZ   mloop
    JMP   func
search:                      ; 64 ROM bits: the bit, its complement, the master's choice
    MOVI  r2, 0
    MOVI  r3, 0
    STT   r2, r3, BIDX
sloop:
    MOVI  r3, 0
    LDT   r2, r3, BIDX
    MOV   r0, r2
    SHR   r0, 3
    LDT   r1, r0, ROM
    MOV   r0, r2
    SHL   r0, 29
    SHR   r0, 29             ; bit index within the byte
sb:
    CMPI  r0, 0
    JZ    sgot
    SHR   r1, 1
    MOVI  r3, 1
    SUB   r0, r3
    JMP   sb
sgot:                        ; r1 bit 0: this ROM bit
    MOV   r0, r1
    CALL  bit                ; the bit
    MOVI  r0, 1
    XOR   r0, r1
    CALL  bit                ; its complement
    MOVI  r0, 1
    CALL  bit                ; the master's direction
    XOR   r0, r1
    TSTI  r0, 1
    JNZ   idle               ; another device's branch: out until the next reset
    MOVI  r3, 0
    LDT   r2, r3, BIDX
    ADDI  r2, 1
    STT   r2, r3, BIDX
    CMPI  r2, 64
    JNZ   sloop
; ---- the function command
func:
    MOVI  r2, 0xFF
    MOVI  r1, 4
    JMP   rx1
n_func:
    CMPI  r1, 0x44
    JZ    idle               ; Convert T: done at once (read slots read 1)
    CMPI  r1, 0xBE
    JZ    readsp
    CMPI  r1, 0x4E
    JNZ   idle
    MOVI  r3, 0              ; Write Scratchpad: TH, TL, configuration into bytes 2-4
    MOVI  r2, 2
    STT   r2, r3, PTR
wsp:
    MOVI  r2, 0xFF
    MOVI  r1, 5
    JMP   rx1
n_wsp:
    MOVI  r3, 0
    LDT   r2, r3, PTR
    STT   r1, r2, SP
    ADDI  r2, 1
    STT   r2, r3, PTR
    CMPI  r2, 5
    JNZ   wsp
    CALL  spcrc
    JMP   idle
readsp:
    MOVI  r1, SP
    MOVI  r2, 9
    MOVI  r0, 6              ; then wait for the next reset
    JMP   sendn

; ---- send r2 bytes from tape address r1, then on to step r0 (2: func, 6: idle)
sendn:
    MOVI  r3, 0
    STT   r0, r3, NEXT
    STT   r1, r3, PTR
    STT   r2, r3, CNT
sn:
    MOVI  r3, 0
    LDT   r1, r3, PTR
    LDT   r2, r1, 0          ; the byte, sent least significant bit first
    LDLC  8
sbit:
    MOV   r0, r2
    CALL  bit
    SHR   r2, 1
    DJNZ  sbit
    MOVI  r3, 0
    LDT   r1, r3, PTR
    ADDI  r1, 1
    STT   r1, r3, PTR
    LDT   r2, r3, CNT
    MOVI  r0, 1
    SUB   r2, r0
    STT   r2, r3, CNT
    JNZ   sn
    LDT   r0, r3, NEXT
    CMPI  r0, 2
    JZ    func
    JMP   idle

; ---- receive one byte (least significant bit first) into r1, then on to step r1 (1 ROM
; command, 3 Match ROM, 4 function command, 5 Write Scratchpad)
rx1:
    MOVI  r3, 0
    STT   r1, r3, NEXT
    MOVI  r1, 0
    LDLC  8
rbit:
    MOVI  r0, 1
    CALL  bit
    SHR   r1, 1
    SHL   r0, 7
    OR    r1, r0
    DJNZ  rbit
    MOVI  r3, 0
    LDT   r0, r3, NEXT
    CMPI  r0, 1
    JZ    n_rom
    CMPI  r0, 3
    JZ    n_match
    CMPI  r0, 4
    JZ    n_func
    JMP   n_wsp

; ---- one time slot. r0 bit 0: what the device sends (1: leave DQ alone, 0: pull low);
; returns r0 = DQ 30 us after the edge. A reset goes to `reset` instead. Uses r0, r3.
bit:
    RDTIME r3
    SETT  r3
    ADDT  0xFFFFFF
    WAITP D, 0
    JNZ   bit
bedge:                       ; the edge was about 12 ticks before the next RDTIME
    RDTIME r3
    SETT  r3
    TSTI  r0, 1
    JNZ   b1
    ADDT  24
    EMIT  D, 0, D            ; a 0: pull low at once
    ADDT  30 * US - 36
    SAMPLE PIN, LSB          ; 30 us after the edge
    ADDT  15 * US
    EMIT  D, D, D            ; release 45 us after the edge
    JMP   b2
b1:
    ADDT  30 * US - 12
    SAMPLE PIN, LSB
b2:
    WAITT
    GETI  r0
    SHR   r0, 31             ; the sample
    SETT  r3                 ; a reset if DQ is still low 240 us after the edge
    ADDT  240 * US - 12
    WAITP D, D
    JNZ   reset
    RET

reset:                       ; DQ low for 240 us: wait for it to rise, then presence
    ADDT  0xFFFFFF
    WAITP D, D
    JNZ   reset
    RDTIME r3
    SETT  r3
    ADDT  30 * US
    EMIT  D, 0, D            ; presence: 30 us after the rise, 120 us low
    ADDT  120 * US
    EMIT  D, D, D
    WAITT
    JMP   romcmd

; ---- CRC-8 of the ROM's first 7 bytes into byte 7, and of the scratchpad's 8 into byte 8
romcrc:
    MOVI  r2, ROM
    MOVI  r1, 7
    JMP   crc8
spcrc:
    MOVI  r2, SP
    MOVI  r1, 8
crc8:                        ; r1 bytes from tape address r2; the CRC goes after them
    MOVI  r0, 0
cb:
    LDT   r3, r2, 0
    XOR   r0, r3
    LDLC  8
cbit:
    SHR   r0, 1
    JNC   cn
    MOVI  r3, 0x8C
    XOR   r0, r3
cn:
    DJNZ  cbit
    ADDI  r2, 1
    MOVI  r3, 1
    SUB   r1, r3
    JNZ   cb
    STT   r0, r2, 0
    RET
