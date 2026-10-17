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
export PDK_ROOT="${PDK_ROOT:-$EDA/pdk-ihp-2bbec755dc67ca3db0261c3d6163e15735d66710}"
export PDK=ihp-sg13cmos5l
cd "$(dirname "$0")/.."

if [[ ! -x "$EDA/precheck-venv/bin/python" ]]; then
  "$EDA/venv/bin/python3.11" -m venv "$EDA/precheck-venv"
  "$EDA/precheck-venv/bin/pip" install -q -r tt/precheck/requirements.txt
fi
mkdir -p "$EDA/klayout-docker"
cat > "$EDA/klayout-docker/klayout" <<'EOF'
#!/usr/bin/env bash
exec docker run --rm -i -v "$HOME:$HOME" -v /tmp:/tmp -w "$PWD" -e PDK_ROOT -e PDK \
  ghcr.io/librelane/librelane:3.1.0.dev3 klayout "$@"
EOF
chmod +x "$EDA/klayout-docker/klayout"

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
PATH="$EDA/klayout-docker:$PATH" "$EDA/precheck-venv/bin/python" precheck.py --gds "$WORK/tt_um_escapement.gds"
