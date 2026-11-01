; Modbus RTU server (v1.6): a device on an RS-485 bus, answering a Modbus client from a
; table of holding registers in the tape. Functions 03 (Read Holding Registers), 06 (Write
; Single Register) and 16 (Write Multiple Registers); any other function gets exception
; 01, a bad quantity or length 03, registers past the table 02 (Modbus Application
; Protocol V1.1b3, sec. 6.3, 6.6, 6.12 and 7). Broadcasts (address 0) of 06 and 16 are
; carried out without a reply.
;
; Framing (Modbus over Serial Line V1.02, sec. 2.5): 11-bit characters, 8 data bits LSB
; first with even parity by default (PARITY 1 odd, 2 none with two stop bits); a frame
; ends after 3.5 character times of silence (at least 1.75 ms above 19200 baud), and its
; CRC-16 (polynomial 0xA001 reflected, from 0xFFFF, low byte first) must check. A frame
; with a parity or stop-bit error, a bad CRC, under 4 or over 123 bytes is dropped
; silently, as the standard requires. The 1.5-character limit between the characters of
; a frame is not enforced. At power-up the server waits for 3.5 characters of silence.
; A frame for another server is recognised by its first byte and the server listens
; again at once, so a frame that follows it 3.5 characters later is received; a frame
; for this server is checked and answered before it listens again.
;
; Pins: TXP to the transceiver's DI, RXP from its RO, DEP to DE (and /RE): high while the
; reply is on the wire, from one bit time before its first start bit to the end of its
; last stop bit.
;
; Tape: register k (0 <= k < NREG) in word REGS + k, bits 15:0. It holds whatever the
; host put there (WRITE_TAPE before START), and the host can read it at any time
; (READ_TAPE). Host (POP): k << 16 | value writes register k from the device side (a
; sensor reading, say), between frames; k >= NREG raises IRQ 4. Host (PUSH): after a
; write from the bus, first << 0 | count << 16; IRQ 2 if the mailbox is full (the write
; is done, the notice lost).

.default CTX, 0
.default TXP, 0
.default RXP, 1
.default DEP, 2
.default ADDR, 1                            ; this server's address, 1..247
.default NREG, 64
.default REGS, 0x00
.default PARITY, 0                          ; 0 even, 1 odd, 2 none (two stop bits)
.default CLK_KHZ, 25000
.default BAUD, 19200
.assert 1 <= ADDR <= 247, a server address is 1 to 247
.assert 1 <= NREG <= 0x80 - REGS, the registers sit below the frame buffer
.equ BIT, (CLK_KHZ * 1000 + BAUD // 2) // BAUD
.assert BIT >= 100, the receive path between a stop bit and the next start bit needs half a bit of 50 ticks
.equ FIRST, (3 * BIT) // 2 - 8              ; data bit 0's centre from the RDTIME after WAITP (uart_rx.asm)
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
.equ MAXLEN, 124                            ; 123 bytes at most (16 with 57 registers)
.equ CRCW, 0xFE                             ; the running CRC
.equ NOTE, 0xFD                             ; the host notice of a write

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
    CMPI  r2, NREG
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
    MOVI  r0, 0
fn:
    LDT   r2, r0, BUF + 1    ; the function, and the frame's length for it
    CMPI  r2, 16
    JNZ   fn1
    LDT   r2, r0, BUF + 6    ; 16: 9 + the byte count
    ADDI  r2, 9
    CMP   r2, r1
    JNZ   exc3
    JMP   fields
fn1:
    CMPI  r2, 3
    JZ    fn2
    CMPI  r2, 6
    JZ    fn2
    MOVI  r1, 1              ; illegal function
    JMP   exc
fn2:
    CMPI  r1, 8              ; 3, 6: 8
    JNZ   exc3
fields:                      ; r2: bytes 2-3 (the first register), r1: 4-5 (count or value)
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
    MOV   r1, r1             ; a count of 0
    JZ    exc3
    CMPI  r3, 3
    JNZ   f16

f03:                         ; Read Holding Registers: count 1..125
    CMPI  r1, 126
    JNC   exc3
    MOV   r0, r2             ; r0: the first register
    ADD   r1, r0             ; r1: past the last
    CMPI  r1, NREG + 1
    JNC   exc2
    CALL  begin              ; (the address)
    MOVI  r3, 3
    CALL  put
    MOV   r3, r1             ; byte count
    SUB   r3, r0
    SHL   r3, 1
    CALL  put
rd:
    LDT   r3, r0, REGS
    SHR   r3, 8
    CALL  put
    LDT   r3, r0, REGS
    CALL  put
    ADDI  r0, 1
    CMP   r0, r1
    JNZ   rd
    JMP   fin

f06:                         ; Write Single Register
    CMPI  r2, NREG
    JNC   exc2
    STT   r1, r2, REGS
    MOVI  r3, 0
    MOVHI r3, 1
    OR    r3, r2             ; first | 1 << 16
    JMP   note

f16:                         ; Write Multiple Registers: count 1..123, byte count twice it
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
    MOV   r3, r1             ; the notice, first | count << 16
    SHL   r3, 16
    OR    r3, r2
    STT   r3, r0, NOTE
    MOV   r0, r2             ; r0: the register, r2: its value's place, sh: the end
    SHL   r1, 1
    ADDI  r1, 7
    LDSH  r1
    MOVI  r2, 7
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
    CALL  begin              ; the reply: the request's first six bytes (the address)
    MOVI  r0, 1
echo:
    LDT   r3, r0, BUF
    CALL  put
    ADDI  r0, 1
    CMPI  r0, 6
    JNZ   echo
    JMP   fin

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
