; Pong on a VGA monitor, 640x480 at 60 Hz, from bank B (v1.2 pins, no new hardware).
; With the Tiny VGA Pmod on uo_out, bank B is VSYNC (B0), R0 (B1), G0 (B2), B0 (B3) and
; HSYNC (B4). The pixel clock is the core clock: 25 MHz against the standard 25.175 MHz,
; so the frame rate is 59.5 Hz, which monitors accept.
;
;   ctx 0  sync   HSYNC and VSYNC: two events per line, 800 ticks per line, 525 lines
;   ctx 1  video  64 x 48 cells of 10 x 10 pixels from a framebuffer on the tape, one bit
;                 per cell; in the vertical blank it runs the game and redraws the buffer
;
; Every edge has a deadline that is a constant offset from the start of the run, so the
; two contexts stay locked to each other by arithmetic alone, and escert proves that no
; edge of the picture is ever late. The picture's content is data: EMITX drives the
; three colour pins from the bit shifted out of sh, at a fixed time per cell.
;
; Game: two paddles, a ball, a dashed net, borders and the score; at 10 points a new
; game starts. A paddle whose buttons (ui_in[4..7], active high) are idle is played by
; the chip: it watches only its own half and steers a randomly picked row of the paddle
; to meet the ball, so it misses when the ball comes too steeply. Hitting the ball with
; a paddle's end sends it off at an angle.

.default SYNC, 0
.default VIDEO, 1

; 640x480 timing, in ticks (= pixels)
.equ H_VIS, 640
.equ H_FP, 16
.equ H_SYNC, 96
.equ H_TOT, 800
.equ V_VIS, 480
.equ V_FP, 10
.equ V_SYNC, 2
.equ V_TOT, 525
.equ CELL, 10                     ; pixels per cell, both ways
.equ T0, (V_TOT - V_VIS) * H_TOT  ; the first frame starts after a whole vertical blank
.equ DRAW, 2000                   ; the redraw starts this long before the frame

; bank B logical pins
.equ VS, 1 << 0
.equ RGB, (1 << 1) | (1 << 2) | (1 << 3)
.equ HS, 1 << 4
.equ L_UP, 1 << 1                 ; inputs: left up and down on ui_in[4], ui_in[5]; the
.equ L_DN, 1 << 2                 ; right side's, ui_in[6] and ui_in[7], are shifted here

; tape: the framebuffer, row r in words 2r (columns 0-31, bit c = column c) and 2r+1
.equ FB, 0
.equ MASK, 96                     ; MASK + i = 1 << i
.equ FONT, 128                    ; 3x5 digits, row k in bits 31-3k..29-3k, bit 0 = left
.equ BX, 140                      ; ball column 0..63
.equ BY, 141                      ; ball row, doubled (rows are word pairs)
.equ DX, 142                      ; +1 or -1
.equ DY, 143                      ; +2 or -2
.equ PL, 144                      ; paddle top rows, doubled
.equ PR, 145
.equ SL, 146                      ; scores
.equ SR, 147
.equ FRAME, 148
.equ GL, 149                      ; the left score's glyph, shifted up one row per row drawn
.equ RNG, 150
.equ K_RP, 151                    ; right paddle, column 61
.equ K_DXS, 152                   ; +1, -1: away from the left side, from the right
.equ K_M2, 154
.equ BTN, 155                     ; buttons, left and right, in the left side's bits
.equ AIM, 157                     ; per side: 100 + the paddle row picked to meet the ball
.equ FLIP, 159                    ; per side: XOR that counts columns from that side
.equ K_TAPS, 161
.equ AIMS, 162                    ; eight choices of paddle row (doubled), plus 100

.tape MASK, 1<<0, 1<<1, 1<<2, 1<<3, 1<<4, 1<<5, 1<<6, 1<<7, 1<<8, 1<<9, 1<<10, 1<<11
.tape MASK + 12, 1<<12, 1<<13, 1<<14, 1<<15, 1<<16, 1<<17, 1<<18, 1<<19, 1<<20, 1<<21
.tape MASK + 22, 1<<22, 1<<23, 1<<24, 1<<25, 1<<26, 1<<27, 1<<28, 1<<29, 1<<30, 1<<31
.tape FONT, 0xF6DE0000, 0x4D2E0000, 0xF39E0000, 0xF3CE0000, 0xB7C80000
.tape FONT + 5, 0xE7CE0000, 0xE7DE0000, 0xF2480000, 0xF7DE0000, 0xF7CE0000
.tape BX, 32, 40, 1, 2, 40, 40, 0, 0, 0, 0, 0xACE1
.tape K_RP, 1 << 29, 1, 0xFFFFFFFF, 0xFFFFFFFE
.tape AIM, 104, 104, 0, 63, 0xB400
.tape AIMS, 100, 102, 104, 106, 108, 110, 102, 108

.bank  SYNC
.bank  VIDEO
.own   0, SYNC
.own   4, SYNC
.own   1, VIDEO
.own   2, VIDEO
.own   3, VIDEO
.entry SYNC, sync
.entry VIDEO, video

; ---------------------------------------------------------------- sync
sync:
    ADDT  T0 + H_VIS + H_FP       ; line 0's HSYNC
s_frame:
    LDLC  V_VIS + V_FP
s_vis:
    EMIT  HS | VS, VS, HS | VS
    ADDT  H_SYNC
    EMIT  HS, HS, HS
    ADDT  H_TOT - H_SYNC
    DJNZ  s_vis
    EMIT  HS | VS, 0, HS | VS     ; VSYNC for two lines
    ADDT  H_SYNC
    EMIT  HS, HS, HS
    ADDT  H_TOT - H_SYNC
    EMIT  HS, 0, HS
    ADDT  H_SYNC
    EMIT  HS, HS, HS
    ADDT  H_TOT - H_SYNC
    LDLC  V_TOT - V_VIS - V_FP - V_SYNC
s_back:
    EMIT  HS | VS, VS, HS | VS
    ADDT  H_SYNC
    EMIT  HS, HS, HS
    ADDT  H_TOT - H_SYNC
    DJNZ  s_back
    JMP   s_frame

; ---------------------------------------------------------------- video
video:
    ADDT  16
    EMIT  RGB, 0, RGB             ; black until the first frame
    MOVI  r3, 0                   ; r3 = 0: the base of every state address
    ADDT  T0 - 16                 ; tmr = the first frame's first pixel
; ---------------------------------------------------------------- game (vertical blank)
game:
    LDT   r0, r3, FRAME
    ADDI  r0, 1
    STT   r0, r3, FRAME
    TSTI  r0, 1
    JZ    ball                    ; even frames move the ball, odd ones the paddles

; paddles, left then right (r1 = side), one row per move: the buttons, or else the chip,
; which watches only its own half and steers the row it has picked to meet the ball
    GETP  r2
    STT   r2, r3, BTN
    SHR   r2, 2
    STT   r2, r3, BTN + 1         ; the right side's buttons, in the left side's bits
    MOVI  r1, 0
p_side:
    LDT   r2, r1, BTN
    LDT   r0, r1, PL
    TSTI  r2, L_UP | L_DN
    JZ    p_auto
    TSTI  r2, L_UP
    JNZ   p_up
    JMP   p_dn
p_auto:
    LDT   r2, r3, BX
    LDT   r0, r1, FLIP
    XOR   r2, r0                  ; the ball's column counted from this side
    LDT   r0, r1, PL
    TSTI  r2, 32
    JNZ   p_done                  ; in the other half
    LDT   r2, r3, BY
    SUB   r2, r0
    ADDI  r2, 100                 ; 100 + ball - paddle top
    LDT   r0, r1, AIM             ; 100 + the paddle row picked to meet it
    CMP   r2, r0
    LDT   r0, r1, PL
    JZ    p_done
    JNC   p_dn
p_up:
    CMPI  r0, 2
    JZ    p_done
    MOVI  r2, 2
    SUB   r0, r2
    JMP   p_done
p_dn:
    CMPI  r0, 82
    JZ    p_done
    ADDI  r0, 2
p_done:
    STT   r0, r1, PL
    ADDI  r1, 1
    CMPI  r1, 2
    JNZ   p_side
    JMP   draw

; the ball: bounce off the borders, then off a paddle, or score
ball:
    LDT   r0, r3, RNG             ; 16-bit Galois LFSR, one step per ball move
    SHR   r0, 1
    JNC   b_move
    LDT   r2, r3, K_TAPS
    XOR   r0, r2
b_move:
    STT   r0, r3, RNG
    LDT   r0, r3, BY
    LDT   r1, r3, DY
    ADD   r0, r1
    CMPI  r0, 2
    JC    flip_y                  ; above row 1 (or wrapped)
    CMPI  r0, 93
    JC    y_ok                    ; at or above row 46
flip_y:
    MOVI  r2, 0
    SUB   r2, r1
    STT   r2, r3, DY
    ADD   r0, r2
    ADD   r0, r2
y_ok:
    STT   r0, r3, BY
    LDT   r0, r3, BX
    LDT   r1, r3, DX
    ADD   r0, r1
    STT   r0, r3, BX
    MOVI  r1, 0                   ; side: left
    CMPI  r0, 3
    JZ    hit
    CMPI  r0, 63
    JZ    point
    MOVI  r1, 1                   ; right
    CMPI  r0, 60
    JZ    hit
    CMPI  r0, 0
    JNZ   draw
point:                            ; r1 scores and the ball is served towards the other
    LDT   r0, r1, SL
    ADDI  r0, 1
    STT   r0, r1, SL
    LDT   r2, r1, K_DXS
    STT   r2, r3, DX
    CMPI  r0, 10
    JNZ   serve
    MOVI  r0, 0                   ; 10 points: a new game
    STT   r0, r3, SL
    STT   r0, r3, SR
serve:
    MOVI  r0, 32
    STT   r0, r3, BX
    LDT   r0, r3, RNG
    SHL   r0, 27
    SHR   r0, 26                  ; a random row: 2 * (rng mod 32)
    ADDI  r0, 16
    STT   r0, r3, BY
    JMP   aim
hit:                              ; r1's paddle: ball row - paddle top 0..10 is a hit
    LDT   r2, r1, PL
    LDT   r0, r3, BY
    SUB   r0, r2
    CMPI  r0, 11
    JNC   draw
    LDT   r2, r1, K_DXS           ; back the other way
    STT   r2, r3, DX
    CMPI  r0, 4
    JC    go_up                   ; the top two rows send it up
    CMPI  r0, 8
    JC    aim                     ; the middle two keep its angle
    MOVI  r2, 2                   ; the bottom two send it down
    JMP   set_dy
go_up:
    LDT   r2, r3, K_M2
set_dy:
    STT   r2, r3, DY
aim:                              ; the side the ball now heads for picks a paddle row
    LDT   r0, r3, RNG
    SHL   r0, 29
    SHR   r0, 29
    LDT   r2, r0, AIMS
    MOVI  r0, 1
    XOR   r0, r1
    STT   r2, r0, AIM

; redraw: the background, then the paddles, the score and the ball on top. No branches,
; so the redraw takes the same time every frame.
draw:
    MOVI  r0, (-DRAW) & 0xFFFF
    MOVHI r0, ((-DRAW) >> 16) & 0xFFFF
    ADDTR r0
    WAITT                         ; every game path reaches here by DRAW before the frame
    MOVI  r0, DRAW
    ADDTR r0                      ; tmr = the frame's first pixel again
    MOVI  r0, 1                   ; the net: column 32 is bit 0 of word 1
    MOVI  r1, FB
    LDLC  12
d_back:                           ; four rows at a time: the net on the last two
    STT   r3, r1, 0
    STT   r3, r1, 1
    STT   r3, r1, 2
    STT   r3, r1, 3
    STT   r3, r1, 4
    STT   r0, r1, 5
    STT   r3, r1, 6
    STT   r0, r1, 7
    ADDI  r1, 8
    DJNZ  d_back
    MOVI  r0, 0xFFFF
    MOVHI r0, 0xFFFF
    STT   r0, r3, FB              ; top and bottom borders
    STT   r0, r3, FB + 1
    STT   r0, r3, FB + 94
    STT   r0, r3, FB + 95
    LDT   r1, r3, PL              ; left paddle: column 2, six rows
    LDLC  6
d_left:
    LDT   r0, r1, FB
    ADDI  r0, 1 << 2              ; (the bit is clear)
    STT   r0, r1, FB
    ADDI  r1, 2
    DJNZ  d_left
    LDT   r1, r3, PR              ; right paddle: column 61
    LDT   r2, r3, K_RP
    LDLC  6
d_right:
    LDT   r0, r1, FB + 1
    OR    r0, r2
    STT   r0, r1, FB + 1
    ADDI  r1, 2
    DJNZ  d_right
    LDT   r2, r3, SR              ; the score, rows 2-6: one glyph row per row
    LDT   r2, r2, FONT
    LDSH  r2                      ; sh = the right digit's glyph
    LDT   r2, r3, SL
    LDT   r2, r2, FONT
    STT   r2, r3, GL
    MOVI  r1, FB + 4
    LDLC  5
d_score:
    LDT   r2, r3, GL
    MOV   r0, r2
    SHL   r2, 3
    STT   r2, r3, GL
    SHR   r0, 29
    SHL   r0, 26                  ; columns 26-28
    LDT   r2, r1, FB
    OR    r0, r2
    STT   r0, r1, FB
    STSH  r2
    MOV   r0, r2
    SHL   r2, 3
    LDSH  r2
    SHR   r0, 29
    SHL   r0, 4                   ; columns 36-38
    LDT   r2, r1, FB + 1
    OR    r0, r2
    STT   r0, r1, FB + 1
    ADDI  r1, 2
    DJNZ  d_score
; the ball on top: word BY (+1 right of column 31), bit BX mod 32
    LDT   r0, r3, BX
    LDT   r1, r3, BY
    LDT   r2, r0, MASK
    TSTI  r0, 32
    JZ    d_ball
    ADDI  r1, 1
    LDT   r2, r0, MASK - 32
d_ball:
    LDT   r0, r1, FB
    OR    r0, r2
    STT   r0, r1, FB

; the 480 lines: two words per row, ten lines per row, 64 cells per line
v_frame:
    MOVI  r1, FB
    MOVI  r2, 0
v_line:
    LDT   r0, r1, 0
    LDSH  r0
    LDLC  16
v_left:
    EMITX RGB, 0, RGB
    ADDT  CELL
    EMITX RGB, 0, RGB
    ADDT  CELL
    DJNZ  v_left
    LDT   r0, r1, 1
    LDSH  r0
    LDLC  16
v_right:
    EMITX RGB, 0, RGB
    ADDT  CELL
    EMITX RGB, 0, RGB
    ADDT  CELL
    DJNZ  v_right
    EMIT  RGB, 0, RGB             ; black for the blanking
    WAITT                         ; the line is queued: start the next from a known time
    ADDT  H_TOT - H_VIS
    ADDI  r2, 1
    CMPI  r2, CELL
    JNZ   v_line
    MOVI  r2, 0
    ADDI  r1, 2
    CMPI  r1, FB + 96
    JNZ   v_line
    ADDT  (V_TOT - V_VIS) * H_TOT
    JMP   game
