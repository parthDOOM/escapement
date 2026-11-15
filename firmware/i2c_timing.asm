; I2C bus timing checker (the bus timing monitor): watches SCL and SDA between other
; devices, drives nothing, and reports every clock pulse or data change that breaks the
; bus's timing. Four interval rules on the synchronised pins, checked by the monitor at
; every tick (the firmware only reads the results):
;
;   rule 0  SCL high time       SCL rise -> SCL fall, at least THIGH
;   rule 1  SCL low time        SCL fall -> SCL rise, at least TLOW, and at most TOUT
;                               (a clock held low that long: a stuck bus)
;   rule 2  data setup          SDA edge -> SCL rise, at least TSU
;   rule 3  clock period        SCL rise -> SCL rise, at least TPER
;
; The defaults are standard mode (100 kHz) at 50 MHz; every limit is in whole ticks,
; rounded down, so a bus within its limits never reports (a violation smaller than a tick
; may go unseen). START and STOP move SDA while SCL is high, which rule 2 measures to the
; next rise; that interval is never short on a working bus.
;
; Host (PUSH): per violation, three words: the monitor's record ([31] valid, [30] late,
; [17:16] rule, [15:0] the interval in ticks, or TOUT for a late one), its tick, and the
; four violation counts since the last report (rule k in [8k+7:8k]; a violation the
; record could not hold still counts). The record and the counts are then cleared.

.default CTX, 0
.default SCL, 0
.default SDA, 1
.default CLK_KHZ, 50000
.default THIGH_NS, 4000
.default TLOW_NS, 4700
.default TSU_NS, 250
.default TPER_NS, 10000
.default TOUT_US, 1000
.default TB, 0xE0                           ; tape: the four rules (8 words)
.equ T_H, THIGH_NS * CLK_KHZ // 1000000
.equ T_L, TLOW_NS * CLK_KHZ // 1000000
.equ T_SU, TSU_NS * CLK_KHZ // 1000000
.equ T_P, TPER_NS * CLK_KHZ // 1000000
.equ T_OUT, TOUT_US * CLK_KHZ // 1000
.assert T_OUT < 65536, the stuck-clock limit must fit the monitor's 16-bit counter
.assert T_P < 65536, the period limit must fit the monitor's 16-bit counter
.equ EN, 1 << 31
.equ R0, EN | (0 << 29) | (SCL << 25) | (1 << 23) | (SCL << 19) | (2 << 17) | T_H
.equ R1, EN | (1 << 29) | (SCL << 25) | (2 << 23) | (SCL << 19) | (1 << 17) | T_L
.equ R2, EN | (2 << 29) | (SDA << 25) | (3 << 23) | (SCL << 19) | (1 << 17) | T_SU
.equ R3, EN | (3 << 29) | (SCL << 25) | (1 << 23) | (SCL << 19) | (1 << 17) | T_P
.equ KEEP, 1 << 16                          ; MON: leave the rule as it is (clear only)
.equ CLR, 1 << 31

.entry CTX, main
.tape  TB, R0, 0, R1, T_OUT, R2, 0, R3, 0

main:
    MOVI  r2, TB
    LDLC  4
rules:
    LDT   r0, r2, 0
    LDT   r1, r2, 1
    MON   r0, r1             ; rule k, its MIN and MAX
    ADDI  r2, 2
    DJNZ  rules
    MOVI  r2, 0
    MOVHI r2, KEEP >> 16
    MOVI  r3, 0
    MOVHI r3, CLR >> 16      ; r2, r3: a MON that clears and changes no rule
poll:
    GETM  r0, 1              ; the record
    MOV   r1, r0
    SHL   r1, 1              ; C = a violation recorded
    JNC   poll
    PUSH  r0
    GETM  r1, 2              ; its tick
    PUSH  r1
    GETM  r1, 0              ; the counts
    PUSH  r1
    MON   r2, r3             ; clear the record and the counts
    JMP   poll
