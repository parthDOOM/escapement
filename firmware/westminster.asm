; Westminster Quarters: the hour chime of the Great Clock of Westminster ("Big Ben"),
; played as square waves on a piezo buzzer, followed by one strike of the hour bell.
; Context 0 on pin bank B, buzzer on B0 (uo_out[3]): push-pull, released high.
;
; The quarter bells are G#4, F#4, E4 and B3 (the chime is in E major), and the hour
; bell is E3. The chime has five changes of four notes:
;   1  G# F# E  B      2  E  G# F# B      3  E  F# G# E
;   4  G# E  F# B      5  B  F# G# E
; The full hour plays changes 2, 3, 4 and 5, then strikes the hour.
;
; Pitches are equal-tempered (A4 = 440 Hz). Every half period is a DRIVE event at an
; exact tick, so each note is exact to one tick (40 ns at 25 MHz): G#4 is 30,099 ticks
; per half period, 415.30 Hz. A note sounds for half a second (its last half period
; is high, the silent level) and the next one starts 0.15 s later; a change ends with
; a further 0.65 s of silence. The whole
; chime takes about 14 s. Then the program halts, which leaves B0 driven high.
;
; All timings derive from CLK_KHZ, so the tune can be played at another clock (the
; tests play it at 250 kHz, a hundredth of the real time).

.default CTX, 0
.default PIN, 0                             ; B0 = uo_out[3]
.default CLK_KHZ, 25000                     ; core clock in kHz
.equ BZ, 1 << PIN

; half periods in ticks, CLK / (2 f), for f in centihertz: 100 * CLK_KHZ * 1000 / (2 f)
.equ HGS4, (CLK_KHZ * 50000 + 20765) // 41530    ; G#4 415.30 Hz
.equ HFS4, (CLK_KHZ * 50000 + 18500) // 36999    ; F#4 369.99 Hz
.equ HE4,  (CLK_KHZ * 50000 + 16481) // 32963    ; E4  329.63 Hz
.equ HB3,  (CLK_KHZ * 50000 + 12347) // 24694    ; B3  246.94 Hz
.equ HE3,  (CLK_KHZ * 50000 + 8241) // 16481     ; E3  164.81 Hz
; periods per note: f * 0.5 s; the hour bell rings for 2.5 s
.equ NGS4, 208
.equ NFS4, 185
.equ NE4, 165
.equ NB3, 123
.equ NE3, 412
.equ GAP, CLK_KHZ * 150                     ; 0.15 s between notes
.equ REST, CLK_KHZ * 650                    ; a further 0.65 s after each change

.bank  CTX
.entry CTX, main
.own   PIN, CTX

main:
    ADDT  64
    EMIT  BZ, BZ, BZ                        ; B0 high, the idle level
; change 2: E G# F# B
    LDLC  NE4
    CALL  e4
    LDLC  NGS4
    CALL  gs4
    LDLC  NFS4
    CALL  fs4
    LDLC  NB3
    CALL  b3
    ADDT  REST
; change 3: E F# G# E
    LDLC  NE4
    CALL  e4
    LDLC  NFS4
    CALL  fs4
    LDLC  NGS4
    CALL  gs4
    LDLC  NE4
    CALL  e4
    ADDT  REST
; change 4: G# E F# B
    LDLC  NGS4
    CALL  gs4
    LDLC  NE4
    CALL  e4
    LDLC  NFS4
    CALL  fs4
    LDLC  NB3
    CALL  b3
    ADDT  REST
; change 5: B F# G# E
    LDLC  NB3
    CALL  b3
    LDLC  NFS4
    CALL  fs4
    LDLC  NGS4
    CALL  gs4
    LDLC  NE4
    CALL  e4
    ADDT  REST
; the hour bell
    LDLC  NE3
    CALL  e3
    WAITT                                   ; every edge has been applied
    HALT

; ---- one routine per bell: lc periods from tmr, ending high. Each note starts on
; an empty queue (WAITT drains the previous one) and GAP after it, so no deadline
; is queued far ahead of the one before it. The half period is an immediate, so
; escert bounds every lead.
gs4:
    WAITT
    ADDT  GAP
gs4_p:
    EMIT  BZ, 0, BZ
    ADDT  HGS4
    EMIT  BZ, BZ, BZ
    ADDT  HGS4
    DJNZ  gs4_p
    RET
fs4:
    WAITT
    ADDT  GAP
fs4_p:
    EMIT  BZ, 0, BZ
    ADDT  HFS4
    EMIT  BZ, BZ, BZ
    ADDT  HFS4
    DJNZ  fs4_p
    RET
e4:
    WAITT
    ADDT  GAP
e4_p:
    EMIT  BZ, 0, BZ
    ADDT  HE4
    EMIT  BZ, BZ, BZ
    ADDT  HE4
    DJNZ  e4_p
    RET
b3:
    WAITT
    ADDT  GAP
b3_p:
    EMIT  BZ, 0, BZ
    ADDT  HB3
    EMIT  BZ, BZ, BZ
    ADDT  HB3
    DJNZ  b3_p
    RET
e3:
    WAITT
    ADDT  GAP
e3_p:
    EMIT  BZ, 0, BZ
    ADDT  HE3
    EMIT  BZ, BZ, BZ
    ADDT  HE3
    DJNZ  e3_p
    RET
