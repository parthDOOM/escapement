#!/usr/bin/env bash
# TT precheck on a local harden (the check the CI precheck job runs), inside WSL/Linux:
#
#   scripts/local-precheck.sh runs/TAG
#
# Uses tt-support-tools' precheck (the ./tt checkout local-harden.sh pins) with a Python
# venv for its klayout/gdstk modules and the pinned LibreLane container's KLayout binary
# for the DRC decks (the CI job gets KLayout from nix instead).
set -euo pipefail
RUN=$(realpath "$1")
EDA="${EDA:-$HOME/eda}"
export PDK=ihp-sg13cmos5l
# FLOW as for scripts/local-harden.sh (default ttihp27a): the precheck of the release the harden used.
#   pre       tt-support-tools d66cf17's precheck, IHP-Open-PDK 2bbec755, KLayout from LibreLane 3.1.0.dev3
#   ttihp27a  the shuttle's (tools 9d13ff3, the ./tt checkout local-harden.sh makes for it): its PDK
#             89d42ee from ciel and KLayout + Magic from LibreLane 3.1.0.dev4 (KLayout 0.30.12, Magic
#             8.3.677; its tool-versions.json pins 0.30.4 / 8.3.568: the CI precheck job is the authority)
FLOW="${FLOW:-ttihp27a}"
case "$FLOW" in
  pre)      export PDK_ROOT="${PDK_ROOT:-$EDA/pdk-ihp-2bbec755dc67ca3db0261c3d6163e15735d66710}"
            IMG=ghcr.io/librelane/librelane:3.1.0.dev3; PVENV="$EDA/precheck-venv" ;;
  ttihp27a) export PDK_ROOT="${PDK_ROOT:-$EDA/pdk-ciel/ciel/ihp-sg13/versions/89d42ee6d2fd9b50b86a1aada6fee6673c3e6008}"
            IMG=ghcr.io/librelane/librelane:3.1.0.dev4; PVENV="$EDA/precheck-venv-ttihp27a" ;;
  *) echo "FLOW must be pre or ttihp27a" >&2; exit 2 ;;
esac
cd "$(dirname "$0")/.."
[[ -f tt/precheck/precheck.py ]] || { echo "no tt/precheck (run local-harden.sh with the same FLOW first)"; exit 1; }

if [[ ! -x "$PVENV/bin/python" ]]; then
  "$EDA/venv/bin/python3.11" -m venv "$PVENV"
  "$PVENV/bin/pip" install -q -r tt/precheck/requirements.txt
fi
TOOLS="$EDA/precheck-docker-$FLOW"; mkdir -p "$TOOLS"
for t in klayout magic; do
  cat > "$TOOLS/$t" <<EOF
#!/usr/bin/env bash
exec docker run --rm -i -v "\$HOME:\$HOME" -v /tmp:/tmp -w "\$PWD" -e PDK_ROOT -e PDK $IMG $t "\$@"
EOF
  chmod +x "$TOOLS/$t"
done

GDS="$RUN/final/gds/tt_um_escapement.gds"
[[ -f "$GDS" ]] || { echo "no GDS at $GDS"; exit 1; }
# Lay the files out as tt_tool does for submission: GDS, LEF and the gate-level netlist
# (IHP: the unpowered .nl.v) side by side, and info.yaml found by walking up.
WORK=$(mktemp -d)
cp "$GDS" "$WORK/tt_um_escapement.gds"
cp "$RUN/final/lef/tt_um_escapement.lef" "$WORK/tt_um_escapement.lef"
cp "$RUN/final/nl/tt_um_escapement.nl.v" "$WORK/tt_um_escapement.v"
cp info.yaml "$WORK/info.yaml"
cd tt/precheck
# the venv's bin too: the Verilog syntax check runs yowasp-yosys by name (11-13: with a bare PATH
# it failed "No such file or directory"; earlier runs passed only from a shell with a venv on PATH)
PATH="$TOOLS:$PVENV/bin:$PATH" "$PVENV/bin/python" precheck.py --gds "$WORK/tt_um_escapement.gds"
