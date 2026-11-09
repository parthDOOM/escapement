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

## Real devices against the bus models (test_real_devices.py)

| File | sigrok-dumps path | What it is |
| --- | --- | --- |
| qi_nexus5.sr | qi/gpmq8005a-nexus5/gpmq8005a-nexus5.sr | Qi: a Nexus 5 on a GPMQ8005A transmitter, demodulated data, 1 MHz |
| qi_noname_samsung5.sr | qi/noname-samsung5/noname-samsung5.sr | Qi: a no-name receiver for a Samsung phone, 250 kHz (it keys its bits at 545 us, outside the specification) |
| pd_apple_av_hdmi.sr, pd_apple_power_brick.sr | usb_power_delivery/macbook_2015/ | USB PD: a 2015 MacBook with Apple's AV adapter and power brick (Twinkie, CC1/CC2) |
| pd_hdmi_dongle.sr, pd_power_supply_20V.sr | usb_power_delivery/pixel_2015/ | USB PD: a 2015 Pixel with an HDMI dongle and a 20 V supply |
| pd_thinkpad_*.sr | usb_power_delivery/thinkpad_yoga_370/ | USB PD: a ThinkPad Yoga 370 with an Anker power bank (the plug turned during the capture), an Aukey 45 W supply and a pass-through dongle |
| pd_zy12pds_*.sr | usb_power_delivery/zy12pds_sink_module/ | USB PD: a ZY12PDS trigger module with a no-name 65 W supply and an Anker power bank |
| wiegand36_8a640910.sr | wiegand/36bit/wiegand-8a640910.sr | Wiegand: a 13.56 MHz reader's D0/D1, 20 kHz (its notes say 36-bit; the frames are 34-bit) |
| em4100_*_card.sr, _keyfob.sr, _glass.sr | rfid/em4100/rdm630/ | EM4100: ten tags read by an RDM630 module: its RFID pin and its 9600 8N1 output |
| em4100_0200f5ed8d_coil.sr | rfid/em4100/rfid_copier/em4100_0200f5ed8d.sr | EM4100: one tag on a reader coil |
| dcf77_pollin_120s.sr, dcf77_pollin_1800s.sr | dcf77/pollin_dcf1_module/dcf77_120s.sr, dcf77_1800s.sr | DCF77: a Pollin DCF1 receiver module off the air, 2 and 30 minutes (January 2012) |
