; NEC infrared remote transmitter, with the 38 kHz carrier generated on the pin.
; Context 0, IR LED driver on pin 0 (push-pull, active high, idle low).
;
; Timing (Vishay, "Data Formats for IR Remote Control", doc. 80071 rev. 2.3, and
; SB-Projects, "NEC Protocol", www.sbprojects.net/knowledge/ir/nec.php):
;   carrier  38 kHz, 1/3 duty: pulses 8.77 us wide every 26.3 us (Vishay)
;   frame    9 ms leader burst, 4.5 ms space, 32 bits LSB first: address, ~address,
;            command, ~command; then one 562.5 us stop burst. A bit is a 562.5 us burst
;            followed by a space; burst-to-burst distance 1.125 ms for 0, 2.25 ms for 1.
;   repeat   9 ms burst, 2.25 ms space, 562.5 us burst (sent while a key is held)
;   slot     one transmission per 108 ms time slot, leader to leader (Vishay Fig. 5)
;
; At 25 MHz: carrier period 658 ticks (37.99 kHz, -0.02%), pulse 219 ticks (33.3%).
; A burst is n whole carrier pulses: 22 for a bit (envelope 21 * 658 + 219 ticks =
; 561.5 us; Vishay: "22 pulses"), 342 for the leader (8.984 ms). Burst starts are exact
; multiples of the nominal distances from the leader start (13.5 ms leader to first
; bit, 1.125 / 2.25 ms per bit, 11.25 ms leader to repeat stop burst), so carrier
; quantisation never accumulates. Every edge is a DRIVE event at an absolute deadline;
; the carrier loop is 5 instructions (20 ticks) per 658-tick period, paced by the FIFO.
;
; Host: POP one word per transmission.
;   bit 31 = 1             repeat code
;   bit 30 = 1             raw frame: POP a second word, sent LSB first as is (extended
;                          NEC: [15:0] 16-bit address, [23:16] command, [31:24] ~command)
;   otherwise              [7:0] address, [15:8] command; the inverses are added here
; Transmissions start at least 108 ms apart, leader to leader (the next one waits for
; its slot); after each one the chip PUSHes 1. All timings derive from CLK_KHZ, so
; the frame can be played at another clock (the co-simulation runs it at 2.5 MHz).
;
; Limits: the carrier is quantised to whole ticks (37.99 kHz at 25 MHz); escert proves
; CLK_KHZ >= 2500 (at 1.25 MHz the 33-tick carrier runs without a fault but is beyond
; the checker's precision). The host must drain the acks (two-word mailbox), or the
; transmitter waits. SB-Projects gives 110 ms for the repeat period; Vishay's 108 ms is
; used here.

.default CTX, 0
.default PIN, 0
.default CLK_KHZ, 25000                     ; core clock in kHz
.equ IR, 1 << PIN

.equ PERIOD, (CLK_KHZ * 10 + 190) // 380    ; carrier period, ticks (38 kHz)
.equ ON, (PERIOD + 1) // 3                  ; carrier pulse, ticks (1/3 duty)
.equ NBIT, 22                               ; carrier pulses per bit / stop burst
.equ NLEAD, 342                             ; carrier pulses per leader burst
.equ HDR, CLK_KHZ * 27 // 2                 ; 13.5 ms: leader start to first bit
.equ RPT, CLK_KHZ * 45 // 4                 ; 11.25 ms: leader start to repeat stop burst
.equ BIT0, CLK_KHZ * 9 // 8                 ; 1.125 ms burst to burst: 0
.equ BIT1, CLK_KHZ * 9 // 4                 ; 2.25 ms burst to burst: 1
.equ SLOT, CLK_KHZ * 108                    ; 108 ms leader to leader
.equ LEAD, 200                              ; first deadline after re-anchoring
.equ SLOTW, SLOT - LEAD - 4                 ; slot wait, so RDTIME + LEAD lands on SLOT
.equ NEG_SLOT, (1 << 32) - SLOT             ; "previous leader" before the first frame

.entry CTX, main
.own   PIN, CTX

main:
    ADDT  16
    EMIT  IR, 0, IR                         ; LED off
    MOVI  r2, NEG_SLOT & 0xFFFF             ; r2 = leader start of the last transmission
    MOVHI r2, NEG_SLOT >> 16
next:
    POP   r1
    MOVI  r3, 0                             ; r3 = bits to send (0: repeat code)
    MOV   r0, r1
    SHL   r0, 1                             ; C = bit 31
    JC    slot
    MOVI  r3, 32
    SHL   r0, 1                             ; C = bit 30
    JNC   std
    POP   r1                                ; raw 32-bit frame
    JMP   slot
std:
    MOV   r0, r1
    SHR   r0, 8
    SHL   r0, 24
    SHR   r0, 8                             ; command << 16
    SHL   r1, 24
    SHR   r1, 24                            ; address
    OR    r1, r0
    MOV   r0, r1
    SHL   r0, 8
    OR    r1, r0                            ; cmd, cmd, addr, addr
    MOVI  r0, 0xFF00
    MOVHI r0, 0xFF00
    XOR   r1, r0                            ; ~cmd, cmd, ~addr, addr (sent LSB first)
slot:
    RDTIME r0                               ; time since the last leader, in 2^16 ticks
    SUB   r0, r2
    SHR   r0, 16
    CMPI  r0, (SLOTW >> 16) + 1
    JNC   go                                ; slot long past (also across counter wrap)
    SETT  r2
    ADDT  SLOTW
    WAITT                                   ; wait for this transmission's slot
go:
    RDTIME r2
    ADDI  r2, LEAD
    SETT  r2                                ; tmr = r2 = leader start
    LDLC  NLEAD
    CALL  burst
    TSTI  r3, 0xFF
    JZ    repeat
    ADDT  HDR - NLEAD * PERIOD - LEAD       ; 4.5 ms space: let the leader drain, so
    WAITT                                   ; no queued deadline is ever more than
    ADDT  LEAD                              ; 2^16 ticks ahead of the next one
bit:
    LDLC  NBIT
    CALL  burst
    SHR   r1, 1                             ; C = next bit, LSB first
    JC    one
    ADDT  BIT0 - NBIT * PERIOD
    JMP   counted
one:
    ADDT  BIT1 - NBIT * PERIOD
counted:
    MOVI  r0, 1
    SUB   r3, r0
    JNZ   bit
    JMP   stop
repeat:
    ADDT  RPT - NLEAD * PERIOD
stop:
    LDLC  NBIT
    CALL  burst                             ; stop burst
    WAITT                                   ; every edge has been applied
    MOVI  r0, 1
    PUSH  r0
    JMP   next

; ---- burst: lc carrier pulses from tmr; returns with tmr = start + lc * PERIOD.
burst:
    EMIT  IR, IR, IR
    ADDT  ON
    EMIT  IR, 0, IR
    ADDT  PERIOD - ON
    DJNZ  burst
    RET
