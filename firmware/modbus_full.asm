; Modbus RTU server with the whole Modbus data model, a 3.1 program that spans: it is
; longer than one 256-word page, so its context takes its partner's page too (.span; the
; partner, context CTX ^ 2, stays off). modbus_rtu.asm is the same server with holding
; registers only, as it fits one page.
;
; Functions (Modbus Application Protocol V1.1b3, sec. 6): 01 Read Coils, 02 Read Discrete
; Inputs, 03 Read Holding Registers, 04 Read Input Registers, 05 Write Single Coil,
; 06 Write Single Register, 15 Write Multiple Coils, 16 Write Multiple Registers and 23
; Read/Write Multiple Registers (the write done before the read). Any other function gets
; exception 01; a bad quantity, byte count, length or coil value 03; an address past its
; table 02 (sec. 7), the quantity checks coming first. Broadcasts (address 0) of 05, 06,
; 15, 16 and 23 are carried out without a reply (23's write; its read is not sent).
;
; Framing (Modbus over Serial Line V1.02, sec. 2.5), as modbus_rtu.asm: 11-bit characters,
; 8 data bits LSB first with even parity by default (PARITY 1 odd, 2 none with two stop
; bits); a frame ends after 3.5 character times of silence (at least 1.75 ms above 19200
; baud), and its CRC-16 must check. A frame with a parity or stop-bit error, a bad CRC,
; under 4 or over 123 bytes is dropped silently. So a write is at most 57 registers (16),
; 55 (23) or 912 coils (15).
;
; Pins: TXP to the transceiver's DI, RXP from its RO, DEP to DE (and /RE).
;
; Tape: four tables one after another from REGS, numbered together (table word k at tape
; word REGS + k): NREG holding registers (k from 0), NINP input registers (from NREG),
; NCOIL coils, then NDISC discrete inputs. A register holds its value in bits 15:0; a coil
; or discrete input its state in bit 0 (a write from the bus stores 0 or 1). The host
; writes them before START (WRITE_TAPE) and reads them at any time (READ_TAPE). Host
; (POP): k << 16 | value writes table word k from the device side (a sensor reading, an
; input's level), between frames; k past the last table raises IRQ 4. Host (PUSH): after
; a write from the bus, first << 0 | count << 16, first numbered the same way (a coil
; write's first is NREG + NINP + its coil); IRQ 2 if the mailbox is full (the write is
; done, the notice lost).

.default CTX, 0
.default TXP, 0
.default RXP, 1
.default DEP, 2
.default ADDR, 1                            ; this server's address, 1..247
.default NREG, 32
.default NINP, 32
.default NCOIL, 32
.default NDISC, 32
.default REGS, 0x00
.default PARITY, 0                          ; 0 even, 1 odd, 2 none (two stop bits)
.default CLK_KHZ, 25000
.default BAUD, 19200
.assert 1 <= ADDR <= 247, a server address is 1 to 247
.assert NREG >= 1, every table has a word
.assert NINP >= 1, every table has a word
.assert NCOIL >= 1, every table has a word
.assert NDISC >= 1, every table has a word
.assert REGS + NREG + NINP + NCOIL + NDISC <= 0x80, the tables sit below the frame buffer
.equ INP, REGS + NREG                       ; tape words
.equ COIL, INP + NINP
.equ DISC, COIL + NCOIL
.equ NTAB, DISC + NDISC - REGS              ; table words, all four
.equ BIT, (CLK_KHZ * 1000 + BAUD // 2) // BAUD
.assert BIT >= 100, the receive path between a stop bit and the next start bit needs half a bit of 50 ticks
.equ FIRST, (3 * BIT - 23) // 2             ; data bit 0's centre from the RDTIME two instructions
                                            ; after WAITP: edge + 3.5 + 8 on average (docs/14 idiom 15)
.equ CHAR35, 77 * BIT // 2                  ; 3.5 characters of 11 bits
.equ MIN35, CLK_KHZ * 7 // 4                ; 1.75 ms
.equ T35, BIT // 2 + CHAR35 + (MIN35 - CHAR35) * (MIN35 > CHAR35)   ; from the last stop bit's centre
.equ POLL, CLK_KHZ                          ; host mailbox checked every 1 ms while idle
.equ LEAD, 64
.equ TX, 1 << TXP
.equ RX, 1 << RXP
.equ DE, 1 << DEP
.equ TXDE, TX | DE
.equ PINIT, PARITY != 0                     ; the parity bit's start (odd, or a stop bit)
.equ PINC, PARITY != 2                      ; added per 1 bit
.equ PMASK, 0x1FF - 0xFF * (PARITY == 2)    ; the received bits the parity covers
.equ BUF, 0x80                              ; the frame, one byte per word
.equ MAXLEN, 124                            ; 123 bytes at most
.equ CRCW, 0xFE                             ; the running CRC
.equ NOTE, 0xFD                             ; the host notice of a write

.span  CTX
.entry CTX, main
.own   TXP, CTX
.own   DEP, CTX

main:
    ADDT  16
    EMIT  TXDE, TX, TXDE     ; driver off, TX idle high
sync:
    RDTIME r3                ; 3.5 characters of silence before the first frame
    SETT  r3
    ADDT  T35
    WAITP RX, 0
    JZ    sync

idle:
    MOVI  r1, 0              ; bytes received
    RDTIME r3
    SETT  r3
    ADDT  POLL
    WAITP RX, 0
    JZ    rx_byte
    RXREADY
    JZ    idle
    POP   r0                 ; k << 16 | value, from the device side
    MOV   r2, r0
    SHR   r2, 16
    CMPI  r2, NTAB
    JC    hostw
    IRQ   4
    JMP   idle
hostw:
    SHL   r0, 16
    SHR   r0, 16
    STT   r0, r2, REGS
    JMP   idle

; A character: 8 data bits and the parity bit sampled at their centres from the start
; edge, the stop bit read at its centre; stored raw (checked once the frame has ended).
rx_byte:
    RDTIME r3
    ADDI  r3, FIRST
    SETT  r3
    LDLC  9
rbit:
    SAMPLE RXP, LSB
    ADDT  BIT
    DJNZ  rbit
    WAITT                    ; the stop bit's centre
    GETP  r2
    GETI  r0                 ; the 9 samples in [31:23]
    OR    r0, r2             ; the stop bit's level in [7:0]
    STT   r0, r1, BUF
    CMPI  r1, MAXLEN
    JZ    rfull
    ADDI  r1, 1
rfull:
    ADDT  T35
    WAITP RX, 0
    JZ    rx_byte

; The frame has ended: r1 bytes (MAXLEN: too long).
    CMPI  r1, MAXLEN
    JZ    idle
    CMPI  r1, 4
    JC    idle
    MOVI  r0, 0              ; not this server's, nor a broadcast: listening again at once
    LDT   r2, r0, BUF        ; (the raw first byte; a frame for another server may follow
    SHL   r2, 1              ; this one 3.5 characters after it ends)
    SHR   r2, 24
    CMPI  r2, ADDR
    JZ    check
    MOV   r2, r2
    JNZ   idle
check:                       ; stop bits and parity; each word becomes its byte
    LDT   r2, r0, BUF
    TSTI  r2, RX
    JZ    idle
    SHR   r2, 23
    MOVI  r3, 0xFF
    AND   r3, r2
    STT   r3, r0, BUF
    MOVI  r3, PMASK
    AND   r2, r3
    MOVI  r3, PINIT          ; PINIT + the 1 bits the parity covers: even
    LDLC  9
par:
    SHR   r2, 1
    JNC   par1
    ADDI  r3, 1
par1:
    DJNZ  par
    TSTI  r3, 1
    JNZ   idle
    ADDI  r0, 1
    CMP   r0, r1
    JNZ   check
    MOVI  r0, 0              ; the CRC over the whole frame, its own CRC included: 0
    MOVI  r2, 0xFFFF
    STT   r2, r0, CRCW
crc:
    LDT   r3, r0, BUF
    CALL  crcb
    ADDI  r0, 1
    CMP   r0, r1
    JNZ   crc
    MOV   r2, r2
    JNZ   idle
    MOVI  r0, 0              ; (r0 stays 0 until the function's own code)

; The function, and the frame's length for it: 15, 16 9 + the byte count (byte 6), 23 13 +
; the byte count (byte 10), 1 to 6 8.
fn:
    LDT   r2, r0, BUF + 1
    CMPI  r2, 15
    JZ    fnw
    CMPI  r2, 16
    JZ    fnw
    CMPI  r2, 23
    JNZ   fn1
    LDT   r2, r0, BUF + 10
    ADDI  r2, 13
    JMP   fnl
fnw:
    LDT   r2, r0, BUF + 6
    ADDI  r2, 9
fnl:
    CMP   r2, r1
    JNZ   exc3
    JMP   fields
fn1:
    CMPI  r2, 7
    JNC   fnx
    MOV   r2, r2
    JNZ   fn2
fnx:
    MOVI  r1, 1              ; illegal function
    JMP   exc
fn2:
    CMPI  r1, 8
    JNZ   exc3
fields:                      ; r2: bytes 2-3 (the first address), r1: 4-5 (quantity or value)
    LDT   r2, r0, BUF + 2
    SHL   r2, 8
    LDT   r3, r0, BUF + 3
    OR    r2, r3
    LDT   r1, r0, BUF + 4
    SHL   r1, 8
    LDT   r3, r0, BUF + 5
    OR    r1, r3
    LDT   r3, r0, BUF + 1
    CMPI  r3, 6
    JZ    f06
    CMPI  r3, 5
    JZ    f05
    MOV   r1, r1             ; a quantity of 0
    JZ    exc3
    CMPI  r3, 16
    JZ    f16
    CMPI  r3, 15
    JZ    f15
    CMPI  r3, 23
    JZ    f23
    CMPI  r3, 3
    JC    fbits

; 03, 04: Read Holding / Input Registers, quantity 1..125
    CMPI  r1, 126
    JNC   exc3
    MOV   r0, r2             ; r0: the first, r1: past the last
    ADD   r1, r0
    CMPI  r3, 4
    JZ    f04
    CMPI  r1, NREG + 1
    JNC   exc2
    ADDI  r0, REGS
    ADDI  r1, REGS
    JMP   rdregs
f04:
    CMPI  r1, NINP + 1
    JNC   exc2
    ADDI  r0, INP
    ADDI  r1, INP
rdregs:                      ; tape words r0 up to r1, two bytes each, after the function
    CALL  begin              ; and the byte count (the address)
    MOVI  r3, 0
    LDT   r3, r3, BUF + 1
    CALL  put
    MOV   r3, r1
    SUB   r3, r0
    SHL   r3, 1
    CALL  put
rd:
    LDT   r3, r0, 0
    SHR   r3, 8
    CALL  put
    LDT   r3, r0, 0
    CALL  put
    ADDI  r0, 1
    CMP   r0, r1
    JNZ   rd
    JMP   fin

; 01, 02: Read Coils / Discrete Inputs, quantity 1..2000, 8 to a byte from bit 0, the last
; byte's high bits 0
fbits:
    CMPI  r1, 2001
    JNC   exc3
    MOV   r0, r2
    ADD   r1, r0
    CMPI  r3, 2
    JZ    f02
    CMPI  r1, NCOIL + 1
    JNC   exc2
    ADDI  r0, COIL
    ADDI  r1, COIL
    JMP   rdbits
f02:
    CMPI  r1, NDISC + 1
    JNC   exc2
    ADDI  r0, DISC
    ADDI  r1, DISC
rdbits:                      ; tape words r0 up to r1, bit 0 of each
    CALL  begin
    MOVI  r3, 0
    LDT   r3, r3, BUF + 1
    CALL  put
    MOV   r3, r1             ; byte count: the bits, 8 to a byte, rounded up
    SUB   r3, r0
    ADDI  r3, 7
    SHR   r3, 3
    CALL  put
rbyte:
    MOVI  r2, 0              ; the byte: each bit in at bit 7, shifted down 7 times after,
    LDLC  8                  ; so the i-th lands in bit i; past r1, 0s
bits:
    SHR   r2, 1
    CMP   r0, r1
    JZ    bits1
    LDT   r3, r0, 0
    SHL   r3, 31
    SHR   r3, 24
    OR    r2, r3
    ADDI  r0, 1
bits1:
    DJNZ  bits
    MOV   r3, r2
    CALL  put
    CMP   r0, r1
    JNZ   rbyte
    JMP   fin

f05:                         ; Write Single Coil: 0xFF00 on, 0x0000 off
    CMPI  r1, 0xFF00
    JNZ   f05a
    MOVI  r1, 1
    JMP   f05b
f05a:
    MOV   r1, r1
    JNZ   exc3
f05b:
    CMPI  r2, NCOIL
    JNC   exc2
    STT   r1, r2, COIL
    MOVI  r3, 0
    MOVHI r3, 1
    ADDI  r3, COIL - REGS
    ADD   r3, r2             ; (COIL - REGS + coil) | 1 << 16
    JMP   note

f06:                         ; Write Single Register
    CMPI  r2, NREG
    JNC   exc2
    STT   r1, r2, REGS
    MOVI  r3, 0
    MOVHI r3, 1
    OR    r3, r2             ; first | 1 << 16
    JMP   note

f15:                         ; Write Multiple Coils: quantity 1..1968, byte count to match
    CMPI  r1, 1969
    JNC   exc3
    MOV   r3, r1
    ADDI  r3, 7
    SHR   r3, 3
    LDT   r0, r0, BUF + 6    ; (r0 was 0)
    CMP   r0, r3
    JNZ   exc3
    MOV   r3, r2
    ADD   r3, r1
    CMPI  r3, NCOIL + 1
    JNC   exc2
    MOV   r3, r1             ; the notice, (COIL - REGS + first) | count << 16
    SHL   r3, 16
    OR    r3, r2
    ADDI  r3, COIL - REGS
    MOVI  r0, 0
    STT   r3, r0, NOTE
    MOV   r0, r2             ; r0: the coil, r1: past the last, r2: the data byte's place,
    ADD   r1, r0             ; sh: its bits not yet stored
    MOVI  r2, 7
wbyte:
    LDT   r3, r2, BUF
    LDSH  r3
    LDLC  8
wbit:
    CMP   r0, r1
    JZ    wrd
    STSH  r3
    SHR   r3, 1              ; C: this coil's bit
    LDSH  r3
    MOVI  r3, 0              ; (MOVI leaves C)
    JNC   wbit0
    MOVI  r3, 1
wbit0:
    STT   r3, r0, COIL
    ADDI  r0, 1
    DJNZ  wbit
    ADDI  r2, 1
    JMP   wbyte

f16:                         ; Write Multiple Registers: quantity 1..123, byte count twice it
    CMPI  r1, 124
    JNC   exc3
    LDT   r3, r0, BUF + 6
    SHR   r3, 1
    JC    exc3
    CMP   r3, r1
    JNZ   exc3
    MOV   r3, r2
    ADD   r3, r1
    CMPI  r3, NREG + 1
    JNC   exc2
    MOVI  r3, 7              ; the data from byte 7
    JMP   wregs

f23:                         ; Read/Write Multiple Registers: read 1..125, write 1..121
    CMPI  r1, 126            ; (r2, r1: the read's first and quantity)
    JNC   exc3
    ADD   r2, r1             ; r2: past the read's last
    LDT   r1, r0, BUF + 8    ; r1: the write's quantity
    SHL   r1, 8
    LDT   r3, r0, BUF + 9
    OR    r1, r3
    JZ    exc3
    CMPI  r1, 122
    JNC   exc3
    LDT   r3, r0, BUF + 10
    SHR   r3, 1
    JC    exc3
    CMP   r3, r1
    JNZ   exc3
    CMPI  r2, NREG + 1
    JNC   exc2
    LDT   r2, r0, BUF + 6    ; r2: the write's first
    SHL   r2, 8
    LDT   r3, r0, BUF + 7
    OR    r2, r3
    MOV   r3, r2
    ADD   r3, r1
    CMPI  r3, NREG + 1
    JNC   exc2
    MOVI  r3, 11             ; the data from byte 11

wregs:                       ; registers r2 on, r1 of them, the data from byte r3
    LDSH  r3
    MOV   r3, r1             ; the notice, first | count << 16
    SHL   r3, 16
    OR    r3, r2
    MOVI  r0, 0
    STT   r3, r0, NOTE
    STSH  r3
    MOV   r0, r2             ; r0: the register, r2: its value's place, sh: the end
    MOV   r2, r3
    SHL   r1, 1
    ADD   r1, r3
    LDSH  r1
wr:
    STSH  r1
    CMP   r2, r1
    JZ    wrd
    LDT   r3, r2, BUF
    SHL   r3, 8
    LDT   r1, r2, BUF + 1
    OR    r3, r1
    STT   r3, r0, REGS
    ADDI  r0, 1
    ADDI  r2, 2
    JMP   wr
wrd:
    MOVI  r3, 0
    LDT   r3, r3, NOTE
note:
    TXREADY                  ; Z: the mailbox is full
    JNZ   note1
    IRQ   2
    JMP   note2
note1:
    PUSH  r3
note2:
    MOVI  r0, 0
    LDT   r3, r0, BUF + 1
    CMPI  r3, 23
    JZ    r23
    CALL  begin              ; the reply: the request's first six bytes (the address)
    MOVI  r0, 1
echo:
    LDT   r3, r0, BUF
    CALL  put
    ADDI  r0, 1
    CMPI  r0, 6
    JNZ   echo
    JMP   fin
r23:                         ; 23's read, once its write is done: bytes 2-5 again
    LDT   r1, r0, BUF + 4
    SHL   r1, 8
    LDT   r3, r0, BUF + 5
    OR    r1, r3
    LDT   r3, r0, BUF + 3
    LDT   r0, r0, BUF + 2
    SHL   r0, 8
    OR    r0, r3
    ADD   r1, r0
    ADDI  r0, REGS
    ADDI  r1, REGS
    JMP   rdregs

exc2:
    MOVI  r1, 2
    JMP   exc
exc3:
    MOVI  r1, 3
exc:                         ; exception r1: address, function | 0x80, code
    CALL  begin              ; (the address)
    MOVI  r0, 0
    LDT   r3, r0, BUF + 1
    MOVI  r2, 0x80
    OR    r3, r2
    CALL  put
    MOV   r3, r1
    CALL  put

fin:                         ; the CRC, low byte first, then the driver off
    MOVI  r0, 0
    LDT   r1, r0, CRCW
    MOV   r3, r1
    CALL  put
    MOV   r3, r1
    SHR   r3, 8
    CALL  put
    EMIT  DE, 0, DE
    WAITT
    JMP   idle

begin:                       ; a broadcast gets no reply (back to idle); else the driver
    MOVI  r3, 0              ; on, a bit time of idle line, the CRC from 0xFFFF, and the
    LDT   r3, r3, BUF
    MOV   r3, r3
    JZ    idle
    RDTIME r2
    SETT  r2
    ADDT  LEAD
    EMIT  TXDE, TXDE, TXDE
    ADDT  BIT
    MOVI  r2, 0xFFFF         ; address byte (put, falling through)
    MOVI  r3, 0
    STT   r2, r3, CRCW
    MOVI  r3, ADDR

put:                         ; r3[7:0] on the wire from tmr, and into the CRC (r2, r3)
    SHL   r3, 24
    SHR   r3, 24
    LDSH  r3
    EMIT  TX, 0, TX          ; start bit
    ADDT  BIT
    MOVI  r2, PINIT
    LDLC  8
put1:
    EMITX TX, LSB, TX        ; C: the bit sent
    JNC   put2
    ADDI  r2, PINC
put2:
    ADDT  BIT
    DJNZ  put1
    LDSH  r2
    EMITX TX, LSB, TX        ; parity (PARITY 2: the first stop bit)
    ADDT  BIT
    EMIT  TX, TX, TX         ; stop bit
    ADDT  BIT
crcb:                        ; CRCW = CRC-16 of CRCW and the byte r3 (r2, r3)
    MOVI  r2, 0
    LDT   r2, r2, CRCW
    XOR   r2, r3
    MOVI  r3, 0xA001
    LDLC  8
put3:
    SHR   r2, 1
    JNC   put4
    XOR   r2, r3
put4:
    DJNZ  put3
    MOVI  r3, 0
    STT   r2, r3, CRCW
    RET
