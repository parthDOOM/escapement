#!/usr/bin/env bash
# Decoders for the outside judges (test/cosim/judges.py, tree="upstream") that sigrok-cli 0.7.2
# does not ship, each at a pinned commit, into $OUTSIDE_PD (default build/outside-decoders):
#   decoders/  libsigrokdecode's decoder tree at UPSTREAM (ir_sirc, ir_irmp and sbus_futaba are
#              newer than 0.7.2), with the community decoders below linked in beside it
#   lib/       libirmp.so, built from the same libsigrokdecode tree (ir_irmp loads it)
#   bin/       rcswitch_rx: rc-switch's receiver (RCSwitch.cpp) run off its microcontroller,
#              by test/cosim/rcswitch_rx.cpp
# Needs git, gcc, g++, glib-2.0 and the headers of the Python that libsigrokdecode links against.
set -euo pipefail
UPSTREAM_URL=https://github.com/sigrokproject/libsigrokdecode
UPSTREAM=71f451443029322d57376214c330b518efd84f88      # 2024-10-01
# name repository commit folder
COMMUNITY=(
  "dcc https://github.com/FrightRisk/sigrok-DCC-Protocoll 315fedd1d3c5a3588fe92091557623ffafcc00a2 dcc"
)
RCSWITCH_URL=https://github.com/sui77/rc-switch
RCSWITCH=436a74b03f3dc17a29ee327af29d5a05d77f94b9      # 2022-05-17, v2.6.4
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${OUTSIDE_PD:-$ROOT/build/outside-decoders}
mkdir -p "$OUT/src" "$OUT/lib" "$OUT/bin"

fetch() {   # url commit dir
  if [ ! -d "$3/.git" ]; then git init -q "$3"; git -C "$3" remote add origin "$1"; fi
  if [ "$(git -C "$3" rev-parse -q --verify HEAD || true)" != "$2" ]; then
    git -C "$3" fetch -q --depth 1 origin "$2"
    git -C "$3" checkout -q --detach FETCH_HEAD
  fi
  test "$(git -C "$3" rev-parse HEAD)" = "$2"
}

fetch "$UPSTREAM_URL" "$UPSTREAM" "$OUT/src/libsigrokdecode"
rm -rf "$OUT/decoders"; mkdir -p "$OUT/decoders"
for d in "$OUT"/src/libsigrokdecode/decoders/*/; do ln -s "$d" "$OUT/decoders/"; done
for c in "${COMMUNITY[@]}"; do
  read -r name url commit folder <<<"$c"
  fetch "$url" "$commit" "$OUT/src/$name"
  ln -sfn "$OUT/src/$name/$folder" "$OUT/decoders/$name"
done

# libirmp against the Python that libsigrokdecode embeds
PY=$(ldd "$(ldconfig -p | awk '/libsigrokdecode\.so/{print $NF; exit}')" \
     | sed -n 's/.*libpython\([0-9.]*\)\.so.*/\1/p' | head -1)
gcc -O2 -shared -fPIC -o "$OUT/lib/libirmp.so" \
  "$OUT/src/libsigrokdecode/irmp/irmp-main-sharedlib.c" \
  $(pkg-config --cflags --libs glib-2.0 "python-$PY-embed")
fetch "$RCSWITCH_URL" "$RCSWITCH" "$OUT/src/rc-switch"
g++ -O2 -DRPI -I"$ROOT/test/cosim" -I"$OUT/src/rc-switch" -o "$OUT/bin/rcswitch_rx" \
  "$ROOT/test/cosim/rcswitch_rx.cpp" "$OUT/src/rc-switch/RCSwitch.cpp"
echo "outside decoders in $OUT (libsigrokdecode $UPSTREAM, python $PY)"
