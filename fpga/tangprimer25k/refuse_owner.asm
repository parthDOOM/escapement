; The bench's second refusal (latebench.py): context 0 owns uio[0] only and asks for a
; DRIVE on uio[1], which no context owns. Admission refuses it (ADMIT, OWNER): the chip
; drives nothing on uio[1], releases uio[0], stops the context and records why.

.entry 0, start
.own   0, 0

start:
    ADDT  16
    EMIT  1, 1, 1             ; uio[0] high: owned, admitted
    ADDT  100
    EMIT  2, 2, 2             ; uio[1] high: not owned, refused
    ADDT  100
    EMIT  1, 0, 1             ; never reached
    HALT
