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
| nec_extended.sr | ir/nec/extended/unknown_ceiling_light.sr | extended NEC (16-bit address), a ceiling light remote |
| nec_matsui.sr | ir/nec/hama_8in1/tv_matsui_0001/tv_matsui_0001_button_power_hold_3x.sr | Matsui TV standby, held 3 times; probe 6 demodulated, probe 7 raw |
| rc5_button1.sr | ir/rc-5/hama_8in1/vcr_philips_0082_button1_hold.sr | Philips RC-5 (VCR, button 1 held); probe 0 raw carrier, probe 1 demodulated |
| rc5_button2.sr, rc5_standby.sr | ir/rc-5/hama_8in1/vcr_philips_0082_button2_hold.sr, ..._button_standby_hold.sr | the same remote, button 2 and standby |
| rc5_bogus.sr | ir/rc-5/hama_8in1/vcr_philips_0082_button1_hold_one_bogus_packet.sr | button 1 with one bad packet on the receiver's probe (probes swapped: 0 demodulated, 1 raw) |
| sirc1.sr, sirc2.sr | ir/sirc/sirc-1.sr, ir/sirc/sirc-2.sr | Sony SIRC, 20-bit frames |
| pt2262_three_a.sr | rc/pt2262/maplin_l95ar/maplin_l95ar_three_a.sr | PT2262 433 MHz socket remote via an RR3 receiver, button A three times |
| pt2262_abcd.sr | rc/pt2262/maplin_l95ar/maplin_l95ar_a_b_c_d_on.sr | the same, buttons A to D |

The NEC remote is outside the 5% timing tolerance of sigrok's ir_nec decoder (its
README says so), and sigrok-cli 0.7.2 misreads it; the test checks NEC's own redundancy
instead (address 0x00 then 0xFF, every command byte then its complement).
