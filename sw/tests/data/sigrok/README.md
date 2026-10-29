# Recordings from sigrok-dumps

Logic analyser recordings of real transmitters, from
[sigrok-dumps](https://github.com/sigrokproject/sigrok-dumps), whose README says: "Unless
noted otherwise, all dump files are hereby released into the public domain by their
authors." test_esclearn.py learns each one, decodes it and checks the decode against
sigrok's own decoder where sigrok-cli has one that reads the file.

| File | sigrok-dumps path | What it is |
| --- | --- | --- |
| nec_enter.sr | ir/nec/joy-it_sbc-irc01/joy_it_sbc_irc01_enter_no_repeat.sr | NEC remote, one button, 5 presses; probe 0 demodulated, probe 1 raw (38 kHz carrier) |
| nec_all.sr | ir/nec/joy-it_sbc-irc01/joy_it_sbc_irc01_all.sr | the same remote, every button, with repeat codes |
| rc5_button1.sr | ir/rc-5/hama_8in1/vcr_philips_0082_button1_hold.sr | Philips RC-5 (VCR, button 1 held); probe 0 raw carrier, probe 1 demodulated |
| sirc1.sr, sirc2.sr | ir/sirc/sirc-1.sr, ir/sirc/sirc-2.sr | Sony SIRC, 20-bit frames |
| pt2262_three_a.sr | rc/pt2262/maplin_l95ar/maplin_l95ar_three_a.sr | PT2262 433 MHz socket remote via an RR3 receiver, button A three times |
| pt2262_abcd.sr | rc/pt2262/maplin_l95ar/maplin_l95ar_a_b_c_d_on.sr | the same, buttons A to D |

The NEC remote is outside the 5% timing tolerance of sigrok's ir_nec decoder (its
README says so), and sigrok-cli 0.7.2 misreads it; the test checks NEC's own redundancy
instead (address 0x00 then 0xFF, every command byte then its complement).
