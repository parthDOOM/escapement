#!/bin/bash
# Bitstreams and firmware bundles for the hardware bench: a Raspberry Pi Pico W driving the chip's
# RTL on a Sipeed Tang Primer 25K Dock (GW5A-LV25MG121NC1/I0). See README.md.
#
#   fpga/tangprimer25k/build.sh pinid     # wiring-check bitstream (pinid.v), light
#   fpga/tangprimer25k/build.sh chip      # the whole chip: fpga/escapement_board.v with ESC_NO_CG and
#                                         # fpga/escapement_sram_fpga.v, clk from the Pico at 25 MHz
#   fpga/tangprimer25k/build.sh bundles   # firmware bundles for the Pico (sw/rp2350/make_bundle.py)
#
# Tools: yosys, nextpnr-himbaechel (Gowin) and gowin_pack on PATH, or OSS_CAD_SUITE pointing at an
# OSS CAD Suite folder. Work files go to $WORK (default /tmp/esc-tp25k); outputs, each with a
# MANIFEST (git commit and sha256 of every input), to fpga/tangprimer25k/build/<target>/.
set -euo pipefail
target=${1:?target: pinid, chip or bundles}
H=$(cd "$(dirname "$0")" && pwd)
R=$(cd "$H/../.." && pwd)
W=${WORK:-/tmp/esc-tp25k}/$target
O=$H/build/$target
if [ -n "${OSS_CAD_SUITE:-}" ]; then source "$OSS_CAD_SUITE/environment" >/dev/null 2>&1; fi
rm -rf "$W"; mkdir -p "$W" "$O"
DEV="--device GW5A-LV25MG121NC1/I0 --vopt family=GW5A-25A"
case $target in
  bundles)
    cd "$R"
    PYTHONPATH=$R/sw PYTHONUTF8=1 ${PYTHON:-python3} sw/rp2350/make_bundle.py "$O" > "$W/bundles.log" 2>&1
    {
      echo "repo: $(git rev-parse HEAD) ($(git rev-parse --abbrev-ref HEAD))$(git diff --quiet HEAD -- sw firmware || echo ', sw/firmware modified')"
      echo "built: $(date -Is)"
    } > "$O/MANIFEST"
    echo "bundles: $(ls "$O"/*.json | wc -l) files -> $O"
    exit 0
    ;;
  pinid)
    SRC="$H/pinid.v"
    { cat "$H/tangprimer25k.cst"
      echo 'IO_LOC "clk50" E2;'
      echo 'IO_PORT "clk50" IO_TYPE=LVCMOS33 PULL_MODE=NONE;'
      # open drain: a lower drive limits the current if a Pico pin is left driving
      sed -n 's/^IO_PORT "\([^"]*\)".*/IO_PORT "\1" IO_TYPE=LVCMOS33 PULL_MODE=NONE DRIVE=4;/p' "$H/tangprimer25k.cst"
    } > "$W/pinid.cst"
    yosys -q -l "$W/synth.log" -p "read_verilog $SRC; synth_gowin -family gw5a -top pinid -json $W/synth.json"
    nextpnr-himbaechel -q -l "$W/pnr.log" --json "$W/synth.json" --write "$W/pnr.json" \
      $DEV --vopt cst="$W/pinid.cst" --vopt sspi_as_gpio --freq 50
    # E2 (the 50 MHz oscillator) is also SSPI_WPN and a CPU-mode configuration pin (D07);
    # nextpnr takes only sspi_as_gpio, gowin_pack both
    gowin_pack --cpu_as_gpio --sspi_as_gpio -d GW5A-25A -o "$W/pinid.fs" "$W/pnr.json"
    ;;
  chip)
    cd "$R"
    SRC="src/project.v src/escapement_host.v src/esc_core.v src/esc_ctx.v src/esc_cap.v src/esc_line.v
         src/esc_greg.v src/escapement_art.v fpga/escapement_sram_fpga.v fpga/escapement_board.v"
    SRC=$(echo $SRC)
    DROP_ART="hierarchy -top escapement_board; delete tt_um_escapement/t:escapement_art"
    cp "$H/tangprimer25k.cst" "$W/chip.cst"
    yosys -q -l "$W/synth.log" \
      -p "read_verilog -DESC_NO_CG $SRC; $DROP_ART; synth_gowin -family gw5a -top escapement_board -json $W/synth.json"
    # --timing-allow-fail only because nextpnr flags hold at every block RAM address pin, even
    # for a lone register feeding a RAM on a fully global clock (nextpnr-0.11.1-47 on GW5A-25A:
    # 40 flags with the clock on L3 and on E2). The checks below replace it: clock routed by the
    # global router, measured skew, setup at 25 MHz, and no other hold flag.
    CLKSKEW_OUT="$W/clkskew.txt" nextpnr-himbaechel -q -l "$W/pnr.log" --json "$W/synth.json" --write "$W/pnr.json" \
      $DEV --vopt cst="$W/chip.cst" --freq 25 --timing-allow-fail --post-route "$H/clkskew.py"
    P="$W/pnr.log"; bad=0
    grep -q "'chip.clk' net was routed" "$P" || { echo "CHECK FAIL: chip.clk not routed by the global router"; bad=1; }
    if grep -q "Failed to route net 'chip.clk'" "$P"; then echo "CHECK FAIL: chip.clk fell back to general routing"; bad=1; fi
    skew=$(awk '{ if (NR == 1 || $3 < lo) lo = $3; if ($4 > hi) hi = $4 } END { printf "%.3f", hi - lo }' "$W/clkskew.txt")
    echo "clock sinks (type.port count min max ns):"; sed 's/^/  /' "$W/clkskew.txt"
    awk -v s="$skew" 'BEGIN { exit !(s <= 0.05) }' || { echo "CHECK FAIL: clock skew $skew ns > 0.05 ns"; bad=1; }
    grep "Max frequency for clock 'chip.clk'" "$P" | tail -1 | grep -q "PASS at 25.00 MHz" || { echo "CHECK FAIL: setup at 25 MHz"; bad=1; }
    nhold=$(grep -o "^Info: [0-9]* Hold/min time violations" "$P" | grep -o "[0-9]\+" || echo 0)
    other=$(grep -E "^Info: +hold " "$P" | grep -v -E "\.mem\.0\.0\.AD[AB]?[0-9]+$" || true)
    [ -z "$other" ] || { echo "CHECK FAIL: hold flag outside block RAM address pins:"; echo "$other"; bad=1; }
    [ $bad = 0 ] || exit 1
    echo "checks: clock global, skew $skew ns, setup PASS at 25 MHz, $nhold hold flags (block RAM address pins only)"
    gowin_pack -d GW5A-25A -o "$W/chip.fs" "$W/pnr.json"
    cp "$W/clkskew.txt" "$O/"
    ;;
  *) echo "unknown target: $target" >&2; exit 2 ;;
esac
{
  echo "target: $target"
  echo "built: $(date -Is)"
  echo "tools: $(yosys -V | head -1); $(nextpnr-himbaechel --version 2>&1 | head -1)"
  if [ "$target" = chip ]; then
    echo "repo: $(git -C "$R" rev-parse HEAD) ($(git -C "$R" rev-parse --abbrev-ref HEAD))$(git -C "$R" diff --quiet HEAD -- src fpga/escapement_board.v fpga/escapement_sram_fpga.v || echo ', sources modified')"
    (cd "$R" && sha256sum $SRC)
  else
    sha256sum "$H/pinid.v"
  fi
  if [ "$target" = chip ]; then
    echo "timing: clock skew ${skew} ns; ${nhold} hold flags, all on block RAM address pins; $(grep "Max frequency for clock 'chip.clk'" "$W/pnr.log" | tail -1 | sed 's/^Info: *//')"
  fi
  (cd "$W" && sha256sum "$target.cst" "$target.fs")
} > "$W/MANIFEST"
cp "$W/$target.fs" "$W/pnr.log" "$W/synth.log" "$W/$target.cst" "$W/MANIFEST" "$O/"
echo "== $target -> $O/$target.fs"
grep -E "^Info:\s+\w+:\s+[0-9]+/\s*[0-9]+\s+[0-9]+%|Max frequency for clock" "$W/pnr.log" | grep -v " 0%$" || true
